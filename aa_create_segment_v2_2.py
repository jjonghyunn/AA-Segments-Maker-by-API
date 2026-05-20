# aa_create_segment_v2_2.py
# 2026-05-15  Jonghyun Park w/ Claude
# updated: 2026-05-15  — v2.1 기반. --input 상대경로일 때 스크립트 폴더 기준 fallback 추가
#                       (cwd 가 어디든 segment_maker 폴더의 segments.csv 자동 발견)
# updated: 2026-05-18  — segment-ref cache patch (sequence-prefix 변환) +
#                       --update-or-create (mixed PUT/POST) +
#                       --lookup-by-name (segment_id 빈 row 의 자동 채움 via 폴더의 최신 segment_lookup csv) +
#                       result csv 의 Action 컬럼 (PUT/POST 구분)
"""
CSV 입력 → AA 세그먼트 일괄 생성 또는 업데이트.

segment_lookup.py / input_csv_maker.py 결과 CSV(structure 칼럼 포함)를 입력으로 바로 사용 가능.
structure 칼럼의 " | " 구분 한 줄 구조를 파싱하여 AA JSON으로 변환.

v2.2 변경점 (vs v2.1):
  · --input 상대경로면 우선 cwd 기준 → 없으면 스크립트 폴더(segment_maker/) 기준으로 fallback.
    어디서 실행하든 segments.csv 가 segment_maker/ 안에 있으면 자동 인식.

사용법 (--input 비우면 폴더의 최신 segments_input_*.csv 자동 pick):
  python aa_create_segment_v2_2.py --input segments.csv                     # dry-run (CREATE)
  python aa_create_segment_v2_2.py --input segments.csv --apply             # 실제 POST (CREATE)
  python aa_create_segment_v2_2.py --update --apply                         # 실제 PUT (모두 update, segment_id 컬럼 모두 박혀야)
  python aa_create_segment_v2_2.py --update-or-create --apply               # mixed: id 있으면 PUT, 없으면 POST
       # ↑ --lookup-by-name 이 default True 라 segment_id 빈 row 는
       #   폴더의 segment_lookup_*.csv 에서 name 매칭으로 자동 채움.
       #   매칭되면 PUT (update), 없으면 POST (create). 가장 일반 운영 흐름.
  python aa_create_segment_v2_2.py --update-or-create --no-lookup-by-name --apply
       # ↑ lookup csv 무시하고 강제 POST. lookup csv 에 동일 name 있으면 경고만 출력.

CSV 필수 칼럼:
  · CREATE (POST)               — name, structure
  · UPDATE (PUT)                — segment_id, structure  (csv 마다 박혀 있어야)
  · MIXED (--update-or-create)  — name, structure  (segment_id 있으면 PUT, 없으면 POST 자동 분기)
CSV 선택 칼럼: description, rsid, tags

segment_id 모르거나 많아서 수동 박기 부담:
  · 미리 segment_lookup 으로 csv 받아두기:
      python aa_segment_lookup.py --search "[CAMPAIGN NAME]" --limit 500
      → segment_lookup_<ts>_<keyword>.csv 생성 (segment_id + name 박힘)
  · 그 후 v2.2 에 --lookup-by-name 옵션 → 자동 매칭 (수동 박기 불필요)
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

# INPUT_CSV = "segments_input_260519_1558_global.csv"
INPUT_CSV = "segments_input_260520_1610_scenario.csv"
# INPUT_CSV = "segments_from_ref_260519_1945_recomm15.csv"
# INPUT_CSV = "segments_from_ref_batch_us_hit_260520_1103.csv"
# INPUT_CSV = "segments_from_ref_batch_us_hit_260520_1325_hit_only_plus15.csv"
# INPUT_CSV = "segments_from_ref_batch_260520_1114.csv"
# INPUT_CSV = "segments_from_ref_batch_260520_1340.csv"

# segment-ref 캐시 파일명 suffix — 캠페인 / 환경 별로 분리 가능.
#   ""       → segment_ref_cache.json         (기본)
#   "global" → segment_ref_cache_global.json
#   "us"     → segment_ref_cache_us.json
#   콤마 구분 multi-cache 지원 → 모든 파일 merge load (앞 파일 우선, 첫 파일이 save target).
# --cache <name> argparse 로도 override 가능 (CLI 우선).
# 시나리오 csv (글로벌+US 섞임) 처리 시 → 글로벌·US evar 캐시 + ATC 캐시 모두 박아야
# delayed_purchase 의 ATC visit segment-ref 까지 inline 처리됨.
CACHE_NAME = "26sw_evar_global,26sw_evar_us,add_to_cart_global,add_to_cart_us"

# --lookup-by-name 시 활용할 lookup csv 파일명 (default).
# 빈 값이면 폴더의 모든 segment_lookup_*.csv 자동 merge (사전순 reverse — 새 거 우선).
# 특정 파일 명시하면 그것만 사용. --lookup-csv argparse 로도 override 가능.
LOOKUP_CSV = ""   # 예: "segment_lookup_260518_1327_CAMPAIGN NAME.csv"

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_CSV_PREFIX = "segment_v2_2_result_"


def _resolve_cache_paths(name: str) -> list[Path]:
    """CACHE_NAME 또는 --cache 값 (콤마 분리 가능) → cache 파일 경로 list.
    예: "us,add_to_cart_us" → [segment_ref_cache_us.json, segment_ref_cache_add_to_cart_us.json].
    list 첫 파일이 save target, 모든 파일 load + merge (앞 파일 우선 — 충돌 시 뒤 파일이 덮어쓰지 않음)."""
    raw = (name or "").strip()
    if not raw:
        return [OUTPUT_DIR / "segment_ref_cache.json"]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return [OUTPUT_DIR / "segment_ref_cache.json"]
    return [OUTPUT_DIR / f"segment_ref_cache_{p}.json" for p in parts]


def _resolve_cache_path(name: str) -> Path:
    """첫 cache 파일 — save target (신규 fetch 결과 저장될 곳)."""
    return _resolve_cache_paths(name)[0]

import requests


def _load_seg_ref_cache(cache_path) -> dict[str, dict]:
    """캐시 파일 load. cache_path 가 Path 면 한 파일, list[Path] 면 여러 파일 merge.
    Merge 순서 — 앞 파일 우선 (뒤 파일 매칭 시 덮어쓰지 않음). 첫 파일이 save target."""
    if isinstance(cache_path, list):
        merged: dict[str, dict] = {}
        for p in cache_path:
            if not p.exists():
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    one = json.load(f)
                for k, v in one.items():
                    if k not in merged:
                        merged[k] = v
            except Exception:
                continue
        return merged
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_seg_ref_cache(cache_path: Path, cache: dict[str, dict]) -> None:
    """캐시 파일 저장 (사용자 OneDrive 폴더, 로컬 전용)."""
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
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


def _patch_root_sequence_for_hit_scope(definition: dict) -> dict:
    """root container.context = 'hits' + root.pred.func = 'sequence' 케이스 → 'sequence-prefix' + context='visitors' 변환.

    AA reference 의 정확한 패턴 (Delayed Purchase 같은 hit-scope sequence):
      container { context: "hits", pred: { func: "sequence-prefix", context: "visitors", stream: [...] } }

    AA validator 룰:
      · `sequence` (full)  → implicit context 가 visits/visitors 여야 함, hit-scope 거부
      · `sequence-prefix`  → hit-scope 컨테이너 안 허용. 단 자체 `context` 필수 ("visitors" 권장)
    """
    if not isinstance(definition, dict):
        return definition
    container = definition.get("container")
    if not isinstance(container, dict):
        return definition
    if container.get("context") != "hits":
        return definition
    pred = container.get("pred")
    if isinstance(pred, dict) and pred.get("func") == "sequence":
        pred["func"] = "sequence-prefix"
        # sequence-prefix 자체 context 필수 — reference 패턴 따라 "visitors"
        if "context" not in pred:
            pred["context"] = "visitors"
    return definition


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
        # 1a) event metric 의 exists (v2 가 이미 event type 으로 컴파일한 경우)
        if (node.get("func") == "exists"
                and isinstance(node.get("val"), dict)
                and node["val"].get("func") == "event"):
            return {"func": "event-exists", "evt": node["val"]}
        # 1b) `<varname>instances` 의 exists — v2 는 attr (variables/...) 로 컴파일하지만
        #     AA 는 metric (metrics/...) + event-exists func + evt 키로 받음 (US 패턴)
        if (node.get("func") == "exists"
                and isinstance(node.get("val"), dict)
                and node["val"].get("func") == "attr"
                and isinstance(node["val"].get("name"), str)
                and node["val"]["name"].endswith("instances")):
            attr_name = node["val"]["name"]
            metric_name = attr_name.replace("variables/", "metrics/", 1)
            return {"func": "event-exists",
                    "evt": {"func": "event", "name": metric_name}}
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
    # 1) `<varname> event-exists` → `<varname> exists`
    #    글로벌: `event<N> event-exists`, US: `evar<N>instances event-exists` 둘 다 매칭.
    #    v2 parser 는 `exists` operator 만 받음 — `event-exists` 는 토큰 자체로 파싱 실패.
    #    JSON 컴파일 후 _patch_definition_for_aa 에서 AA 호환 `event-exists` func 으로 다시 변환.
    tokens = [
        re.sub(r"\b(\w+) event-exists\b", r"\1 exists", t)
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


def _fetch_existing_segment(headers: dict, base_endpoint: str, seg_id: str) -> dict | None:
    """기존 segment GET — noop 비교용. 실패하면 None (PUT 진행 fallback)."""
    try:
        r = requests.get(
            f"{base_endpoint}/{seg_id}",
            headers=headers,
            params={"expansion": "definition,name,description,reportSuiteName,tags"},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _payload_equals_existing(payload: dict, existing: dict) -> bool:
    """새 payload 와 기존 segment 가 5 필드 (definition, name, description, rsid, tags) 모두 같은지.
    tags — payload 는 list[str], existing 은 list[dict] (각 dict 의 'name' 키) → set 비교.
    그 외 — 직접 ==. definition 은 nested dict 동등성.
    """
    if (payload.get("name") or "").strip() != (existing.get("name") or "").strip():
        return False
    if (payload.get("description") or "").strip() != (existing.get("description") or "").strip():
        return False
    if (payload.get("rsid") or "") != (existing.get("rsid") or ""):
        return False
    p_tags = set((payload.get("tags") or []))
    e_tags_raw = existing.get("tags") or []
    e_tags = set(
        (t.get("name", "") if isinstance(t, dict) else str(t)) for t in e_tags_raw
    )
    if p_tags != e_tags:
        return False
    if payload.get("definition") != existing.get("definition"):
        return False
    return True


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
                        help="기존 세그먼트 업데이트 (PUT). segment_id 칼럼 필수 — 빈 row 있으면 ERROR. "
                             "segment_id 모르면: python segment_lookup.py --search \"이름\"")
    parser.add_argument("--update-or-create", action="store_true",
                        help="mixed mode: row 별 segment_id 있으면 PUT (update), 없으면 POST (create). "
                             "재실행 시 — 새 segment 는 만들고 기존 segment 는 갱신. --update 와 동시 사용 불가.")
    parser.add_argument("--lookup-by-name", action=argparse.BooleanOptionalAction, default=True,
                        help="csv 의 segment_id 빈 row 는 폴더의 segment_lookup_*.csv 에서 name 매칭으로 자동 채움 (default True). "
                             "--no-lookup-by-name 으로 비활성 — 강제 POST (lookup csv 안 봄). "
                             "그래도 POST 시점에 lookup csv 에 동일 name 있으면 중복 생성 경고 출력.")
    parser.add_argument("--lookup-csv", default=LOOKUP_CSV,
                        help=f"--lookup-by-name 의 lookup csv 경로 명시. 빈 값이면 폴더의 모든 segment_lookup_*.csv merge "
                             f"(사전순 reverse, 새 거 우선). 코드 상단 LOOKUP_CSV 로 default 박을 수 있음 (현재 {LOOKUP_CSV!r}).")
    parser.add_argument("--input", default=INPUT_CSV,
                        help=f"입력 CSV 경로 (default: {INPUT_CSV})")
    parser.add_argument("--cache", default=CACHE_NAME,
                        help="segment-ref 캐시 파일 suffix (예: --cache us → segment_ref_cache_us.json). "
                             f"빈 값이면 기본 segment_ref_cache.json. 코드 상단 CACHE_NAME 으로 default 지정 가능 (현재 {CACHE_NAME!r}).")
    args = parser.parse_args()
    seg_ref_cache_paths = _resolve_cache_paths(args.cache)
    seg_ref_cache_path = seg_ref_cache_paths[0]   # save target = 첫 파일
    if len(seg_ref_cache_paths) == 1:
        print(f"[seg-ref cache] {seg_ref_cache_path.name}")
    else:
        print(f"[seg-ref cache] save: {seg_ref_cache_path.name} / load merge: " +
              ", ".join(p.name for p in seg_ref_cache_paths))

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    # ── CSV 읽기 ──
    # --input 절대경로 → 그대로 사용
    # --input 빈 값이면 폴더 내 segments_input_*.csv 파일명 사전순 최신 1개 자동 pick.
    # 박혀 있으면 그대로 사용 (상대경로면 cwd → script 폴더 fallback).
    input_arg = (args.input or "").strip()
    if not input_arg:
        cands = sorted(OUTPUT_DIR.glob("segments_input_*.csv"), reverse=True)
        if not cands:
            print(f"ERROR: segments_input_*.csv 못 찾음 — {OUTPUT_DIR}")
            return 1
        input_path = cands[0]
        print(f"[input auto-latest] {input_path.name}  (총 {len(cands)} 개 후보 중 최신)")
    else:
        input_path = Path(input_arg)
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

    # lookup csv 로딩 — 옵션 무관 항상 로드 (name 매핑 dict 빌드).
    # lookup-by-name=True → segment_id 자동 채움. False → POST 시 동일 name 매칭 경고만.
    lookup_csv_arg = (getattr(args, "lookup_csv", "") or "").strip()
    if lookup_csv_arg:
        lookup_path = Path(lookup_csv_arg)
        if not lookup_path.is_absolute() and not lookup_path.exists():
            fb = OUTPUT_DIR / lookup_path
            if fb.exists():
                lookup_path = fb
        lookup_paths = [lookup_path] if lookup_path else []
    else:
        lookup_paths = sorted(OUTPUT_DIR.glob("segment_lookup_*.csv"), reverse=True)

    name_to_id: dict[str, str] = {}
    used_files: list[str] = []
    for p in lookup_paths:
        try:
            added = 0
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    nm = (r.get("name") or "").strip()
                    sid = (r.get("segment_id") or "").strip()
                    if nm and sid and nm not in name_to_id:
                        name_to_id[nm] = sid
                        added += 1
            if added > 0:
                used_files.append(f"{p.name}(+{added})")
        except Exception:
            continue

    if getattr(args, "lookup_by_name", True):
        # segment_id 빈 row 의 segment_id 를 lookup csv name 매칭으로 자동 채움
        if not lookup_paths:
            print(f"  [lookup-by-name] ⚠️ lookup csv 없음 — segment_id 빈 row 그대로 (POST 처리됨)")
        else:
            n_filled = 0
            for r in rows:
                if not (r.get("segment_id") or "").strip():
                    sid = name_to_id.get((r.get("name") or "").strip())
                    if sid:
                        r["segment_id"] = sid
                        n_filled += 1
            print(f"  [lookup-by-name] {len(lookup_paths)} lookup csv merge → {n_filled}/{len(rows)} row 의 segment_id 채움")
            if used_files:
                print(f"    매핑 추가 — {', '.join(used_files[:5])}{'...' if len(used_files)>5 else ''}")
    else:
        # --no-lookup-by-name — segment_id 채우지 않음. POST 시 동일 name 매칭되면 경고.
        dup_warns: list[tuple[str, str]] = []
        for r in rows:
            if not (r.get("segment_id") or "").strip():
                nm = (r.get("name") or "").strip()
                if nm in name_to_id:
                    dup_warns.append((nm, name_to_id[nm]))
        if dup_warns:
            print(f"  [warn] --no-lookup-by-name (강제 POST) — lookup csv 에 동일 name {len(dup_warns)} 건 존재:")
            for nm, sid in dup_warns[:5]:
                print(f"     - {nm}  (existing id={sid})")
            if len(dup_warns) > 5:
                print(f"     ... +{len(dup_warns) - 5} 건")
            print(f"  → 중복 생성됩니다. 의도가 아니면 명령에서 --no-lookup-by-name 제거.")

    update_mode = args.update
    mixed_mode  = getattr(args, "update_or_create", False)
    if update_mode and mixed_mode:
        print(f"ERROR: --update 와 --update-or-create 동시 사용 불가. 하나만 선택.")
        return 1
    if mixed_mode:
        mode_label = "MIXED"   # row 별 segment_id 있음/없음 자동 분기
    elif update_mode:
        mode_label = "UPDATE"
    else:
        mode_label = "CREATE"

    # update 모드: segment_id 필수 검증 (mixed 모드는 row 별 자동 분기라 검증 안 함)
    if update_mode:
        missing_ids = [i+1 for i, r in enumerate(rows) if not r["segment_id"]]
        if missing_ids:
            print(f"ERROR: --update 모드인데 segment_id가 없는 행: {missing_ids}")
            print(f"  → mixed mode 원하면 --update-or-create 사용 (segment_id 빈 row 는 POST, 있는 row 는 PUT)")
            return 1

    action_label = f"{mode_label} / {'APPLY' if args.apply else 'DRY-RUN'}"
    print(f"[{requested_at}] AA segment maker v2.2 (CSV) — {action_label}")
    print(f"  Company : {COMPANY_ID}")
    print(f"  Input   : {input_path}")
    print(f"  Segments: {len(rows)}개")
    if mixed_mode:
        n_put  = sum(1 for r in rows if (r.get("segment_id") or "").strip())
        n_post = len(rows) - n_put
        print(f"  Mode    : MIXED (PUT {n_put} update + POST {n_post} create — segment_id 유무로 row 별 분기)")
    elif update_mode:
        print(f"  Mode    : UPDATE (기존 세그먼트 덮어쓰기)")
    print()

    # ── segment-ref 처리 준비 — cache + lazy auth load ──
    seg_ref_cache: dict[str, dict] = _load_seg_ref_cache(seg_ref_cache_paths)   # 여러 파일 merge load
    _auth_state: dict = {"headers": None, "gcid": None, "tried": False}

    def _extract_container(entry):
        """cache entry → container dict. 두 형식 호환:
        · 새 형식 {"container": {...}, "name": ..., ...} → entry["container"]
        · 옛 형식 container 자체 → entry 그대로"""
        if isinstance(entry, dict) and "container" in entry and isinstance(entry.get("container"), dict):
            return entry["container"]
        return entry

    def fetch_seg_pred(seg_id: str) -> dict | None:
        """segment-ref 의 sub-segment container 가져오기. cache 우선, miss 면 AA GET 시도."""
        if seg_id in seg_ref_cache:
            return _extract_container(seg_ref_cache[seg_id])
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
            # 자동 fetch 는 metadata 없이 container 만 받음 — 새 형식으로 저장 (lookup name 은 빈 값)
            seg_ref_cache[seg_id] = {"container": container, "name": "", "description": "", "rsid": ""}
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
            definition = _patch_root_sequence_for_hit_scope(definition)   # Delayed Purchase: root sequence → sequence-prefix
            print(f"  [{i+1}] '{row['name']}' — 파싱 OK")
            specs.append({**row, "definition": definition, "dsl": dsl_text})
        except DSLParseError as e:
            errors.append((i + 1, str(e)))
            print(f"  [{i+1}] '{row['name']}' — ERROR: {e}")
            specs.append({**row, "definition": None, "dsl": dsl_text})

    # segment-ref cache 저장 (loop 중 fetch 한 게 있으면 디스크에 영구 저장)
    _save_seg_ref_cache(seg_ref_cache_path, seg_ref_cache)

    if errors:
        print(f"\n파싱 에러 {len(errors)}건:")
        for idx, msg in errors:
            print(f"  row {idx}: {msg}")
        if args.apply:
            print("\n에러가 있어 --apply 중단합니다. 수정 후 재실행하세요.")
            return 1

    print()

    # Payload 미리보기 — 콘솔 출력 안 함 (너무 김). dryrun csv 에 ParseStatus / Mode 박힘.

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
                "action": "skip",
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

        # PUT (segment_id 있음, update_mode 또는 mixed 모드 + 그 row 가 id 있음) vs POST (없음)
        row_seg_id = (spec.get("segment_id") or "").strip()
        if update_mode or (mixed_mode and row_seg_id):
            # PUT 전에 GET 해서 기존과 동일한지 비교 — 5 필드 모두 같으면 noop (PUT 안 보냄)
            seg_id = row_seg_id
            url = f"{base_endpoint}/{seg_id}"
            existing = _fetch_existing_segment(headers, base_endpoint, seg_id)
            if existing is not None and _payload_equals_existing(payload, existing):
                ui_url = UI_URL_TEMPLATE.format(seg_id=seg_id)
                print(f"  [{i+1}/{len(specs)}] noop '{spec['name']}' ({seg_id}) — 동일 조건")
                results.append({
                    "name": spec["name"], "seg_id": seg_id,
                    "action": "noop",
                    "status": "NOOP",
                    "url": ui_url, "error": "",
                })
                continue
            action_label = "update"
            print(f"  [{i+1}/{len(specs)}] update '{spec['name']}' ({seg_id}) ...", end=" ")
            r = requests.put(url, headers=headers, json=payload, timeout=60)
        else:
            # POST — 새 세그먼트 생성 (CREATE 또는 mixed 의 segment_id 빈 row)
            action_label = "create"
            print(f"  [{i+1}/{len(specs)}] create '{spec['name']}' ...", end=" ")
            r = requests.post(base_endpoint, headers=headers, json=payload, timeout=60)

        if r.status_code in (200, 201):
            data = r.json()
            seg_id = data.get("id", "")
            ui_url = UI_URL_TEMPLATE.format(seg_id=seg_id) if seg_id else ""
            print(f"OK — {seg_id}")
            results.append({
                "name": spec["name"], "seg_id": seg_id,
                "action": action_label,
                "status": f"{r.status_code} {r.reason}",
                "url": ui_url, "error": "",
            })
        else:
            error = r.text[:3000]   # validator 의 errors[] 전체 보려면 충분히 길게
            print(f"FAIL — {r.status_code} {r.reason}")
            results.append({
                "name": spec["name"], "seg_id": spec.get("segment_id", ""),
                "action": action_label,
                "status": f"{r.status_code} {r.reason}",
                "url": "", "error": error,
            })

    # ── Result CSV ──
    csv_path = OUTPUT_DIR / f"{RESULT_CSV_PREFIX}{timestamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["RequestedAt", "Name", "SegmentId", "RSID", "Action", "Status", "Url", "Error"])
        for res in results:
            w.writerow([
                requested_at, res["name"], res["seg_id"],
                "", res.get("action", ""), res["status"], res["url"], res["error"],
            ])
    print(f"\nresult CSV: {csv_path}")

    def _is_ok(r):
        s = r.get("status", "")
        return s.startswith("200") or s.startswith("201") or s == "NOOP"
    ok = sum(1 for r in results if _is_ok(r))
    skip = sum(1 for r in results if r.get("status") == "SKIP")
    fail = len(results) - ok - skip
    n_update = sum(1 for r in results if r.get("action") == "update")
    n_create = sum(1 for r in results if r.get("action") == "create")
    n_noop = sum(1 for r in results if r.get("action") == "noop")
    print(f"[summary] 성공: {ok}, 실패: {fail}, skip: {skip}  (update: {n_update} / create: {n_create} / noop: {n_noop})")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
