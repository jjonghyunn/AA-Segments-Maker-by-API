# aa_create_segment_v2_1.py
# 2026-05-15  Jonghyun Park w/ Claude
# updated: 2026-05-15 12:30  — CSV 입력 기반 세그먼트 생성 + 업데이트(PUT)
"""
CSV 입력 → AA 세그먼트 일괄 생성 또는 업데이트.

segment_lookup.py 결과 CSV(structure 칼럼 포함)를 입력으로 바로 사용 가능.
structure 칼럼의 " | " 구분 한 줄 구조를 파싱하여 AA JSON으로 변환.

사용법:
  python aa_create_segment_v2_1.py --input segments.csv              # dry-run (생성)
  python aa_create_segment_v2_1.py --input segments.csv --apply      # 실제 POST (생성)
  python aa_create_segment_v2_1.py --input segments.csv --update     # dry-run (업데이트)
  python aa_create_segment_v2_1.py --input segments.csv --update --apply  # 실제 PUT (업데이트)

생성(POST): CSV 필수 칼럼 — name, structure
업데이트(PUT): CSV 필수 칼럼 — segment_id, structure
  → segment_id 모르면: python segment_lookup.py --search "세그먼트이름"
CSV 선택 칼럼: description, rsid, tags
"""
from __future__ import annotations

import argparse
import csv
import json
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
RESULT_CSV_PREFIX = "segment_v2_1_result_"

import requests


def _structure_to_dsl(structure: str) -> str:
    """structure 칼럼 한 줄 → 멀티라인 DSL 텍스트.

    " | " 구분을 줄바꿈으로 변환.
    작은따옴표를 쌍따옴표로 복원 (lookup CSV에서 ' 로 치환했으므로).
    """
    lines = structure.split(" | ")
    # 작은따옴표로 감싸진 값을 쌍따옴표로 복원
    # 단, 이름 지정 컨테이너의 작은따옴표('Name'!hit)는 유지
    restored: list[str] = []
    for line in lines:
        restored.append(line)
    dsl = "\n".join(restored)
    return dsl


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
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: 입력 파일 없음: {input_path}")
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
    print(f"[{requested_at}] AA segment maker v2.1 (CSV) — {action_label}")
    print(f"  Company : {COMPANY_ID}")
    print(f"  Input   : {input_path}")
    print(f"  Segments: {len(rows)}개")
    if update_mode:
        print(f"  Mode    : UPDATE (기존 세그먼트 덮어쓰기)")
    print()

    # ── structure → DSL → JSON 변환 ──
    specs: list[dict] = []
    errors: list[tuple[int, str]] = []

    for i, row in enumerate(rows):
        dsl_text = _structure_to_dsl(row["structure"])
        try:
            ast = parse_dsl(dsl_text)
            definition = compile_to_definition(ast)
            print(f"  [{i+1}] '{row['name']}' — 파싱 OK")
            specs.append({**row, "definition": definition, "dsl": dsl_text})
        except DSLParseError as e:
            errors.append((i + 1, str(e)))
            print(f"  [{i+1}] '{row['name']}' — ERROR: {e}")
            specs.append({**row, "definition": None, "dsl": dsl_text})

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
            error = r.text[:300]
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
