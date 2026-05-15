# aa_create_segment_v2_2.py
# 2026-05-15  Jonghyun Park w/ Claude
# updated: 2026-05-15  — v2.1 기반. --input 상대경로일 때 스크립트 폴더 기준 fallback 추가
#                       (cwd 가 어디든 segment_maker 폴더의 segments.csv 자동 발견)
"""
CSV 입력 → AA 세그먼트 일괄 생성 또는 업데이트.

segment_lookup.py / input_csv_maker.py 결과 CSV(structure 칼럼 포함)를 입력으로 바로 사용 가능.
structure 칼럼의 " | " 구분 한 줄 구조를 파싱하여 AA JSON으로 변환.

v2.2 변경점 (vs v2.1):
  · --input 상대경로면 우선 cwd 기준 → 없으면 스크립트 폴더(segment_maker/) 기준으로 fallback.
    어디서 실행하든 segments.csv 가 segment_maker/ 안에 있으면 자동 인식.

사용법:
  python aa_create_segment_v2_2.py --input segments.csv              # dry-run (생성)
  python aa_create_segment_v2_2.py --input segments.csv --apply      # 실제 POST (생성)
  python aa_create_segment_v2_2.py --input segments.csv --update     # dry-run (업데이트)
  python aa_create_segment_v2_2.py --input segments.csv --update --apply  # 실제 PUT (업데이트)

생성(POST): CSV 필수 칼럼 — name, structure
업데이트(PUT): CSV 필수 칼럼 — segment_id, structure
  → segment_id 모르면: python segment_lookup.py --search "세그먼트이름"
CSV 선택 칼럼: description, rsid, tags
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# v2에서 parser/compiler/auth 재사용
from aa_create_segment_v2 import (
    parse_dsl,
    compile_to_definition,
    DSLParseError,
    _load_auth_headers,
    _lookup_owner_id,
    COMPANY_ID,
    DEFAULT_RSID,
    OWNER_ID,
    OWNER_IMS_USER_ID,
    OWNER_LOGIN,
    UI_URL_TEMPLATE,
)

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

INPUT_CSV = "segments.csv"

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_CSV_PREFIX = "segment_v2_2_result_"
SEG_REF_CACHE_PATH = OUTPUT_DIR / "segment_ref_cache.json"   # @<seg_id> → AA container 인라인 캐시

import requests


def _load_seg_ref_cache() -> dict[str, dict]:
    """캐시 파일 load. 없으면 빈 dict 반환."""
    if SEG_REF_CACHE_PATH.exists():
        try:
            with open(SEG_REF_CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_seg_ref_cache(cache: dict[str, dict]) -> None:
    """캐시 파일 저장 (사용자 OneDrive 폴더, 로컬 전용)."""
    try:
        with open(SEG_REF_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [seg-ref cache] 저장 실패 (무시): {e}")


def _fetch_segment_container(seg_id: str, headers: dict, gcid: str) -> dict | None:
    """AA GET /segments/{id}?expansion=definition → definition.container 통째 반환."""
    url = f"https://analytics.adobe.io/api/{gcid}/segments/{seg_id}?expansion=definition"
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code != 200:
        print(f"  [seg-ref fetch] {seg_id} GET 실패: {r.status_code} {r.reason}")
        return None
    data = r.json()
    container = (data.get("definition") or {}).get("container")
    if not container:
        print(f"  [seg-ref fetch] {seg_id} → definition.container 없음")
        return None
    return container


def _patch_definition_for_aa(node, *, fetch_seg_pred=None):
    """v2 컴파일 결과 JSON → AA validator 호환 형식 후처리.

    AA segment definition 의 정확한 형식 (v1 주석 + 실제 validator 응답 단서):
      1) metric event 의 발생 여부 — `event-exists` func + `evt` 키 (val 아님)
         변환 전: {"func": "exists", "val": {"func": "event", "name": "metrics/event<N>"}}
         변환 후: {"func": "event-exists", "evt": {"func": "event", "name": "metrics/event<N>"}}

      2) segment-ref — AA 가 `segment-ref` 라는 func 이름 모름. fetch_seg_pred 가 주어지면
         AA GET /segments/{id} 으로 sub-segment 의 container 를 가져와 그 자리에 inline 박음.
         fetch_seg_pred 가 None 이거나 결과 None 이면 노드 그대로 유지 (apply 시 validator 거부 예상).

    재귀로 dict / list 안 모든 노드 순회.
    """
    if isinstance(node, dict):
        # 1) event metric 의 exists → event-exists + evt 키
        if (node.get("func") == "exists"
                and isinstance(node.get("val"), dict)
                and node["val"].get("func") == "event"):
            return {"func": "event-exists", "evt": node["val"]}
        # 2) segment-ref → sub-segment 의 container inline
        if node.get("func") == "segment-ref" and fetch_seg_pred is not None:
            seg_id = node.get("segmentId")
            if seg_id:
                inline = fetch_seg_pred(seg_id)
                if inline is not None:
                    return _patch_definition_for_aa(inline, fetch_seg_pred=fetch_seg_pred)
            return node
        return {k: _patch_definition_for_aa(v, fetch_seg_pred=fetch_seg_pred)
                for k, v in node.items()}
    if isinstance(node, list):
        return [_patch_definition_for_aa(v, fetch_seg_pred=fetch_seg_pred) for v in node]
    return node


def _structure_to_dsl(structure: str) -> str:
    """structure 칼럼 한 줄 → 멀티라인 DSL 텍스트 (parser 호환 변환 포함).

    " | " 구분을 줄바꿈으로 변환 + 아래 두 가지 input_csv_maker DSL → parser 호환 변환:

    1) `event<N> event-exists` → `event<N> exists`
       (parser 는 operator `exists` 만 받음 — `event-exists` 라는 변형은 미지원)

    2) 단독 grouping paren `( ... )` 제거
       (input_csv_maker 가 visit/visitor 모드에서 추가하는 `'Name'!hit(...)` 외부 paren grouping.
        parser 는 컨테이너 기반 grouping 만 받고 raw paren grouping 은 미지원.
        단독 `(` 토큰과 매칭되는 단독 `)` 토큰만 제거 — 컨테이너 open `'name'!hit(`, `visit(`, `hit(`,
        `'Component'!hit(` 같은 토큰은 보존.)
    """
    raw_tokens = structure.split(" | ")
    # 1) event<N> event-exists → event<N> exists
    tokens = [
        re.sub(r"\bevent(\d+) event-exists\b", r"event\1 exists", t)
        for t in raw_tokens
    ]
    # 2) `not '<container>'!hit(` → `NOT (`  (parser 는 NOT named container 미지원, NOT (...) grouping 만 받음)
    #    매칭 `)` 는 그대로 유지 — 아래 paren stack 에서 'cont' (NOT ( 가 endswith "(") 로 처리되어 보존
    tokens = [
        re.sub(r"^not '[^']*'!hit\(\s*$", "NOT (", t.strip())
        for t in tokens
    ]
    # 2.5) `NOT (cond)` 한 토큰 → 멀티라인 `NOT (` / `cond` / `)` 로 펼침
    #      한 줄 inline 형태로 들어가면 v2 parser 가 paren 안 변수명을 잘못 읽음 (예: `(evar26` 을 attr name 으로 인식).
    #      build_evar_block 의 `f"NOT ({cond_str})"` 같은 inline NOT 케이스 대응.
    expanded: list[str] = []
    for t in tokens:
        m = re.match(r"^NOT\s*\((.+)\)$", t.strip())
        if m:
            expanded.append("NOT (")
            expanded.append(m.group(1).strip())
            expanded.append(")")
        else:
            expanded.append(t)
    tokens = expanded
    # 3) grouping paren 제거 — paren stack 으로 단독 '(' / ')' 매칭 추적
    stack: list[str] = []          # 'group' (제거) or 'cont' (보존)
    to_remove: set[int] = set()
    for i, tok in enumerate(tokens):
        t = tok.strip()
        if t == "(":
            stack.append("group")
            to_remove.add(i)
        elif t == ")":
            if stack:
                opened = stack.pop()
                if opened == "group":
                    to_remove.add(i)
                # 'cont' 면 토큰 그대로 유지
        elif t.endswith("("):
            # 컨테이너 open: visit(, hit(, 'name'!hit(, 'Component'!hit( 등
            stack.append("cont")
    filtered = [t for i, t in enumerate(tokens) if i not in to_remove]
    return "\n".join(filtered)


def _parse_csv(csv_path: Path) -> list[dict]:
    """CSV → [{name, description, rsid, tags, structure}, ...]"""
    rows: list[dict] = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        if "structure" not in headers:
            print(f"ERROR: CSV에 'structure' 칼럼이 없습니다.")
            print(f"  칼럼: {headers}")
            return []
        if "name" not in headers:
            print(f"ERROR: CSV에 'name' 칼럼이 없습니다.")
            print(f"  칼럼: {headers}")
            return []

        for row in reader:
            structure = (row.get("structure") or "").strip()
            if not structure:
                continue
            rows.append({
                "segment_id": (row.get("segment_id") or "").strip(),
                "name": (row.get("name") or "").strip(),
                "description": (row.get("description") or "").strip(),
                "rsid": (row.get("rsid") or "").strip(),
                "tags": (row.get("tags") or "").strip(),
                "structure": structure,
            })
    return rows


def main() -> int:
    # Windows cp949 콘솔에서도 em dash / 한글 깨지지 않도록 utf-8 reconfigure
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="CSV 기반 AA 세그먼트 생성/업데이트 (기본 dry-run)"
    )
    parser.add_argument("--apply", action="store_true",
                        help="실제 POST/PUT 수행. 없으면 JSON 출력만 (dry-run)")
    parser.add_argument("--update", action="store_true",
                        help="기존 세그먼트 업데이트 (PUT). segment_id 칼럼 필수. "
                             "segment_id 모르면: python segment_lookup.py --search \"이름\"")
    parser.add_argument("--input", default=INPUT_CSV,
                        help=f"입력 CSV 경로 (default: {INPUT_CSV})")
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    # ── CSV 읽기 ──
    # --input 절대경로 → 그대로 사용
    # --input 상대경로 → 1) cwd 기준 시도, 2) 없으면 스크립트 폴더(OUTPUT_DIR) 기준 fallback
    input_path = Path(args.input)
    if not input_path.is_absolute() and not input_path.exists():
        fallback = OUTPUT_DIR / input_path
        if fallback.exists():
            input_path = fallback
    if not input_path.exists():
        print(f"ERROR: 입력 파일 없음: {input_path}")
        print(f"       cwd: {Path.cwd()}")
        print(f"       script dir: {OUTPUT_DIR}")
        return 1

    rows = _parse_csv(input_path)
    if not rows:
        print("ERROR: structure가 있는 행이 없습니다.")
        return 1

    update_mode = args.update
    mode_label = "UPDATE" if update_mode else "CREATE"

    # update 모드: segment_id 필수 검증
    if update_mode:
        missing_ids = [i+1 for i, r in enumerate(rows) if not r["segment_id"]]
        if missing_ids:
            print(f"ERROR: --update 모드인데 segment_id가 없는 행: {missing_ids}")
            print(f"  → segment_id 조회: python segment_lookup.py --search \"세그먼트이름\"")
            return 1

    action_label = f"{mode_label} / {'APPLY' if args.apply else 'DRY-RUN'}"
    print(f"[{requested_at}] AA segment maker v2.2 (CSV) — {action_label}")
    print(f"  Company : {COMPANY_ID}")
    print(f"  Input   : {input_path}")
    print(f"  Segments: {len(rows)}개")
    if update_mode:
        print(f"  Mode    : UPDATE (기존 세그먼트 덮어쓰기)")
    print()

    # ── segment-ref 처리 준비 — cache + lazy auth load ──
    seg_ref_cache: dict[str, dict] = _load_seg_ref_cache()
    _auth_state: dict = {"headers": None, "gcid": None, "tried": False}

    def fetch_seg_pred(seg_id: str) -> dict | None:
        """segment-ref 의 sub-segment container 가져오기. cache 우선, miss 면 AA GET 시도."""
        if seg_id in seg_ref_cache:
            return seg_ref_cache[seg_id]
        if not _auth_state["tried"]:
            _auth_state["tried"] = True
            try:
                h, g = _load_auth_headers()
                _auth_state["headers"] = h
                _auth_state["gcid"] = g
                print(f"  [seg-ref] auth load OK — sub-segment GET 가능")
            except Exception as e:
                print(f"  [seg-ref] auth load 실패: {e}")
                return None
        if _auth_state["headers"] is None:
            return None
        container = _fetch_segment_container(seg_id, _auth_state["headers"], _auth_state["gcid"])
        if container is not None:
            seg_ref_cache[seg_id] = container
            print(f"  [seg-ref] cache 추가 — {seg_id}")
        return container

    # ── structure → DSL → JSON 변환 ──
    specs: list[dict] = []
    errors: list[tuple[int, str]] = []

    for i, row in enumerate(rows):
        dsl_text = _structure_to_dsl(row["structure"])
        try:
            ast = parse_dsl(dsl_text)
            definition = compile_to_definition(ast)
            definition = _patch_definition_for_aa(definition, fetch_seg_pred=fetch_seg_pred)
            print(f"  [{i+1}] '{row['name']}' — 파싱 OK")
            specs.append({**row, "definition": definition, "dsl": dsl_text})
        except DSLParseError as e:
            errors.append((i + 1, str(e)))
            print(f"  [{i+1}] '{row['name']}' — ERROR: {e}")
            specs.append({**row, "definition": None, "dsl": dsl_text})

    # segment-ref cache 저장 (loop 중 fetch 한 게 있으면 디스크에 영구 저장)
    _save_seg_ref_cache(seg_ref_cache)

    if errors:
        print(f"\n파싱 에러 {len(errors)}건:")
        for idx, msg in errors:
            print(f"  row {idx}: {msg}")
        if args.apply:
            print("\n에러가 있어 --apply 중단합니다. 수정 후 재실행하세요.")
            return 1

    print()

    # ── Payload 출력 ──
    for i, spec in enumerate(specs):
        if spec["definition"] is None:
            continue
        print(f"{'─' * 60}")
        print(f"Row {i+1}: {spec['name']}")
        if update_mode:
            print(f"  Segment ID: {spec['segment_id']}  (PUT 대상)")
        print(f"  RSID: {spec['rsid'] or DEFAULT_RSID}")
        print(f"  Tags: {spec['tags']}")
        print(f"  Description: {spec['description']}")
        print()
        print("  Definition JSON:")
        print(json.dumps(spec["definition"], ensure_ascii=False, indent=2))
        print()

    if not args.apply:
        # Dry-run 결과 CSV — 파싱 성공/실패 한 눈에 확인용 (apply 안 해도 진단 결과 csv 로 남김)
        errors_by_idx = {idx - 1: msg for idx, msg in errors}
        dryrun_csv_path = OUTPUT_DIR / f"{RESULT_CSV_PREFIX}{timestamp}_dryrun.csv"
        with open(dryrun_csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["RequestedAt", "Name", "SegmentId", "RSID", "Mode", "ParseStatus", "Error"])
            for i, spec in enumerate(specs):
                ok = spec["definition"] is not None
                w.writerow([
                    requested_at, spec["name"], spec.get("segment_id", ""),
                    spec["rsid"] or DEFAULT_RSID,
                    f"{mode_label}/DRY-RUN",
                    "OK" if ok else "PARSE_ERROR",
                    errors_by_idx.get(i, ""),
                ])
        n_ok = sum(1 for s in specs if s["definition"] is not None)
        n_fail = len(specs) - n_ok
        print(f"\nDry-run 결과 CSV: {dryrun_csv_path.name}  (파싱 OK {n_ok} / FAIL {n_fail})")
        update_flag = " --update" if update_mode else ""
        print(f"DRY-RUN — 실제 {'PUT' if update_mode else 'POST'} 안 함. 위 JSON 확인 후 --apply 추가.")
        print(f"  python {Path(__file__).name} --apply{update_flag} --input {args.input}")
        return 0

    # ── 인증 ──
    print("Authenticating ...")
    headers, gcid = _load_auth_headers()

    # Owner 확정
    if OWNER_ID is not None:
        owner_id: int | None = OWNER_ID
        print(f"  Owner: {owner_id} (config 직접 지정)")
    elif OWNER_IMS_USER_ID:
        print(f"  resolving owner by imsUserId ...")
        owner_id = _lookup_owner_id(headers, gcid, ims_user_id=OWNER_IMS_USER_ID)
    elif OWNER_LOGIN:
        print(f"  resolving owner by login ...")
        owner_id = _lookup_owner_id(headers, gcid, login_sub=OWNER_LOGIN)
    else:
        owner_id = None
        print("  Owner: (미지정)")
    print()

    # ── API POST / PUT ──
    base_endpoint = f"https://analytics.adobe.io/api/{gcid}/segments"
    results: list[dict] = []

    for i, spec in enumerate(specs):
        if spec["definition"] is None:
            results.append({
                "name": spec["name"], "seg_id": spec.get("segment_id", ""),
                "status": "SKIP", "url": "", "error": "파싱 실패",
            })
            continue

        rsid = spec["rsid"] or DEFAULT_RSID

        # tags 파싱: "tag1, tag2" → ["tag1", "tag2"]
        tags_str = spec["tags"]
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        payload: dict[str, Any] = {
            "name": spec["name"],
            "description": spec["description"],
            "rsid": rsid,
            "definition": spec["definition"],
            "tags": tags,
        }
        if owner_id is not None:
            payload["owner"] = {"id": owner_id}

        if update_mode:
            # PUT — 기존 세그먼트 업데이트
            seg_id = spec["segment_id"]
            url = f"{base_endpoint}/{seg_id}"
            print(f"  [{i+1}/{len(specs)}] PUT '{spec['name']}' ({seg_id}) ...", end=" ")
            r = requests.put(url, headers=headers, json=payload, timeout=60)
        else:
            # POST — 새 세그먼트 생성
            print(f"  [{i+1}/{len(specs)}] POST '{spec['name']}' ...", end=" ")
            r = requests.post(base_endpoint, headers=headers, json=payload, timeout=60)

        if r.status_code in (200, 201):
            data = r.json()
            seg_id = data.get("id", "")
            ui_url = UI_URL_TEMPLATE.format(seg_id=seg_id) if seg_id else ""
            print(f"OK — {seg_id}")
            results.append({
                "name": spec["name"], "seg_id": seg_id,
                "status": f"{r.status_code} {r.reason}",
                "url": ui_url, "error": "",
            })
        else:
            error = r.text[:3000]   # validator 의 errors[] 전체 보려면 충분히 길게
            print(f"FAIL — {r.status_code} {r.reason}")
            results.append({
                "name": spec["name"], "seg_id": spec.get("segment_id", ""),
                "status": f"{r.status_code} {r.reason}",
                "url": "", "error": error,
            })

    # ── Result CSV ──
    csv_path = OUTPUT_DIR / f"{RESULT_CSV_PREFIX}{timestamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["RequestedAt", "Name", "SegmentId", "RSID", "Status", "Url", "Error"])
        for res in results:
            w.writerow([
                requested_at, res["name"], res["seg_id"],
                "", res["status"], res["url"], res["error"],
            ])
    print(f"\nresult CSV: {csv_path}")

    ok = sum(1 for r in results if r["seg_id"])
    fail = sum(1 for r in results if not r["seg_id"])
    print(f"[summary] 성공: {ok}, 실패: {fail}")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
