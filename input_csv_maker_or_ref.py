# input_csv_maker_or_ref.py
# 2026-05-18  Jonghyun Park w/ Claude
# segment id 들을 OR 로 묶는 단순 maker — `segment name, or_seg-id` 형식 raw csv 처리.
"""
raw csv 예:
    segment name, or_seg-id
    temp.product recommendation 1~14, "s200001591_69f17c11..\ns200001591_69f17ca0..\n..."

처리:
    각 row 의 or_seg-id cell 안 — 줄바꿈 / 콤마 / 세미콜론 / 공백 으로 segment id 분리 →
    hit container 의 OR pred 에 segment-ref 들 묶음 → v2_2 input csv 빌드.

DSL 출력 예:
    hit(
      @s200001591_69f17c11..
      OR
      @s200001591_69f17ca0..
      OR
      ...
    )

사용법:
    python input_csv_maker_or_ref.py                                # 폴더 내 seg_make_ref_*.csv 사전순 최신 자동
    python input_csv_maker_or_ref.py --input <file>                # 명시
    python input_csv_maker_or_ref.py --input <file> --output-ts <ts>
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

SEG_MAKE_REF_CSV = ""   # 빈 값이면 폴더의 seg_make_ref_*.csv 사전순 최신 1개 자동

DEFAULT_RSID = "rsid_placeholder"
# DEFAULT_RSID = "rsid_placeholder"
DEFAULT_TAGS = ""

# scope 단일 — hit container 안 OR 묶음. (필요하면 "visit" 으로 변경)
SCOPE = "hit"

# 입력 컬럼 (case-insensitive 매칭) — header 변형 허용
NAME_COLUMN_CANDIDATES   = ["segment name", "name"]
OR_REF_COLUMN_CANDIDATES = ["or_seg-id", "or_seg_id", "or_segid", "or-seg-id"]

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE     = "segments_input_{ts}.csv"
OUTPUT_DSL_NAME_TEMPLATE = "segments_input_{ts}.dsl"

SCOPE_TO_CONTAINER = {"hit": "hit", "visit": "visit", "visitor": "visitor"}

# segment id 패턴 — 'sXXXXXXXXX_<24hex>' 또는 24hex 짧은 형태 둘 다 허용
SEG_ID_REGEX = re.compile(r"s\d{9}_[0-9a-f]{24}|[0-9a-f]{24}", re.IGNORECASE)


def _split_seg_ids(cell: str) -> list[str]:
    """or_seg-id cell 안 — 줄바꿈/콤마/세미콜론/공백/탭 으로 split 후 segment id 만 추출."""
    if not cell:
        return []
    # 정규식으로 직접 매칭 — 구분자 신경 안 씀
    return SEG_ID_REGEX.findall(cell)


def _find_column(fieldnames: list[str], candidates: list[str]) -> str | None:
    fn_lower = {h.strip().lower(): h for h in fieldnames}
    for c in candidates:
        h = fn_lower.get(c.lower())
        if h:
            return h
    return None


def _build_dsl(seg_ids: list[str], scope: str) -> str:
    """DSL 시각 확인용 — 줄바꿈 포함 multi-line."""
    container = SCOPE_TO_CONTAINER.get(scope, "hit")
    lines = [f"{container}("]
    for i, sid in enumerate(seg_ids):
        if i > 0:
            lines.append("  OR")
        lines.append(f"  @{sid}")
    lines.append(")")
    return "\n".join(lines)


def _build_structure(seg_ids: list[str], scope: str) -> str:
    """v2_2 structure 컬럼 — DSL 한 줄 ('|' 로 줄바꿈 치환)."""
    container = SCOPE_TO_CONTAINER.get(scope, "hit")
    tokens = [f"{container}("]
    for i, sid in enumerate(seg_ids):
        if i > 0:
            tokens.append("OR")
        tokens.append(f"@{sid}")
    tokens.append(")")
    return " | ".join(tokens)


def _pick_latest_input() -> Path | None:
    """폴더 내 seg_make_ref_<YYMMDD>_<HHMM>.csv 사전순 최신 (tmp 제외)."""
    all_files = sorted(OUTPUT_DIR.glob("seg_make_ref_*.csv"), reverse=True)
    candidates = [p for p in all_files
                  if re.match(r"^seg_make_ref_\d", p.name) and "_tmp." not in p.name]
    return candidates[0] if candidates else None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="or_seg-id 패턴 raw csv → v2_2 input csv 빌드")
    parser.add_argument("--input", default=SEG_MAKE_REF_CSV,
                        help="input csv (빈 값 → 폴더 사전순 최신).")
    parser.add_argument("--output-ts", dest="output_ts", default="",
                        help="출력 파일 ts override. 빈 값이면 datetime.now() 사용.")
    args = parser.parse_args()

    # 입력 csv 결정
    input_arg = (args.input or "").strip()
    if input_arg:
        src_path = Path(input_arg)
        if not src_path.is_absolute():
            src_path = OUTPUT_DIR / src_path
    else:
        picked = _pick_latest_input()
        if picked is None:
            print(f"ERROR: seg_make_ref_<YYMMDD>_<HHMM>.csv 못 찾음 — {OUTPUT_DIR}")
            return 1
        src_path = picked
        print(f"  [auto-latest] {src_path.name}")

    if not src_path.exists():
        print(f"ERROR: input csv 없음 — {src_path}")
        return 1

    # 출력 ts
    ts = (args.output_ts or "").strip() or datetime.now().strftime("%y%m%d_%H%M")
    out_path     = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(ts=ts)
    out_dsl_path = OUTPUT_DIR / OUTPUT_DSL_NAME_TEMPLATE.format(ts=ts)

    # 입력 csv 읽기 — 컬럼 자동 매칭
    with open(src_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        name_col   = _find_column(fieldnames, NAME_COLUMN_CANDIDATES)
        or_ref_col = _find_column(fieldnames, OR_REF_COLUMN_CANDIDATES)
        if not name_col or not or_ref_col:
            print(f"ERROR: 필수 컬럼 못 찾음 — name={name_col!r}, or_seg-id={or_ref_col!r}")
            print(f"  헤더 후보: name={NAME_COLUMN_CANDIDATES}, or_ref={OR_REF_COLUMN_CANDIDATES}")
            print(f"  실제 헤더: {fieldnames}")
            return 1
        print(f"  [columns] name={name_col!r}, or_seg-id={or_ref_col!r}")
        rows = list(reader)

    rows_out: list[dict] = []
    dsl_blocks: list[str] = []
    skipped: list[tuple[str, str]] = []

    for r in rows:
        seg_name = (r.get(name_col) or "").strip()
        cell = r.get(or_ref_col) or ""
        seg_ids = _split_seg_ids(cell)
        if not seg_name:
            skipped.append(("(no name)", "name 없음"))
            continue
        if not seg_ids:
            skipped.append((seg_name, "or_seg-id 추출 0개"))
            continue

        dsl = _build_dsl(seg_ids, SCOPE)
        structure = _build_structure(seg_ids, SCOPE)
        rows_out.append({
            "segment_id": "",
            "name": seg_name,
            "description": "",
            "rsid": DEFAULT_RSID,
            "tags": DEFAULT_TAGS,
            "structure": structure,
            "warning": "",
        })

        dsl_blocks.append(
            f"--- segment\nname: {seg_name}\ndescription: \nrsid: {DEFAULT_RSID}\ntags: []\n\n{dsl}"
        )

    # 출력 csv
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["segment_id", "name", "description", "rsid", "tags", "structure", "warning"])
        w.writeheader()
        w.writerows(rows_out)

    with open(out_dsl_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(dsl_blocks))

    ts_disp = datetime.now().strftime("%y%m%d_%H%M")
    print(f"[{ts_disp}] input_csv_maker_or_ref.py")
    print(f"  입력: {src_path.name}")
    print(f"  raw rows: {len(rows)}  →  output: {len(rows_out)} row  (SCOPE={SCOPE!r})")
    print(f"  출력 csv: {out_path.name}")
    print(f"  출력 dsl: {out_dsl_path.name}")
    for row in rows_out:
        ids_count = row["structure"].count("@s") + row["structure"].count("@" + "0123456789"[0])  # rough
        n_or = row["structure"].count("OR") + 1
        print(f"     - {row['name']}  ({n_or}개 seg-id OR)")
    if skipped:
        print(f"  skip: {len(skipped)} row")
        for name, reason in skipped:
            print(f"     - {name[:60]}  ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
