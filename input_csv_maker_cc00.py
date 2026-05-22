# input_csv_maker_cc00.py
# 2026-05-15  Jonghyun Park w/ Claude
"""
input_csv_maker.py 사본 — 단일 segment 1 개만 생성하는 단순 버전.

두 segment-ref 를 visit scope 안에 AND 로 묶은 segment 한 개만 만듦:
  visit(
    hit(
      @<SEG_REF_PRIMARY>
      AND
      @<SEG_REF_SECONDARY>
    )
  )

출력 — aa_create_segment_v2.2.py 가 받는 형식의 input CSV + 시각 확인용 dsl.

사용:
  python input_csv_maker_cc00.py
  python aa_create_segment_v2.2.py --input segments_cc00_<ts>.csv --apply
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

SEG_NAME = "[CAMPAIGN NAME] CC_00. Contents Click Total (Visit)"

# AND 로 묶일 두 segment-ref ID
SEG_REF_PRIMARY   = "segment_id_placeholder"   # 메인 컨테이너 (input_csv_maker 와 동일)
SEG_REF_SECONDARY = "segment_id_placeholder"   # 추가 AND 대상

DEFAULT_RSID = "rsid_placeholder"
DEFAULT_TAGS = ""

OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE     = "segments_cc00_{ts}.csv"
OUTPUT_DSL_NAME_TEMPLATE = "segments_cc00_{ts}.dsl"

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════


def build_structure() -> str:
    """visit( hit( @<seg1> AND @<seg2> ) ) — structure 한 줄 ' | ' 구분 (v2_1 input 형식)."""
    parts = [
        "visit(",
        "hit(",
        f"@{SEG_REF_PRIMARY}",
        "AND",
        f"@{SEG_REF_SECONDARY}",
        ")",
        ")",
    ]
    return " | ".join(parts)


def structure_to_dsl(structure: str) -> str:
    """structure ' | ' 구분 한 줄 → 멀티라인 dsl (괄호 기준 들여쓰기)."""
    parts = [p.strip() for p in structure.split(" | ") if p.strip()]
    indent = 0
    out: list[str] = []
    for p in parts:
        if p.startswith(")"):
            indent = max(0, indent - 1)
        out.append("  " * indent + p)
        if p.endswith("("):
            indent += 1
    return "\n".join(out)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ts = datetime.now().strftime("%y%m%d_%H%M")
    out_csv = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(ts=ts)
    out_dsl = OUTPUT_DIR / OUTPUT_DSL_NAME_TEMPLATE.format(ts=ts)

    structure = build_structure()

    # csv 출력 — aa_create_segment_v2.2.py 가 받는 형식 (segment_id, name, description, rsid, tags, structure, warning)
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f, fieldnames=["segment_id", "name", "description", "rsid", "tags", "structure", "warning"]
        )
        w.writeheader()
        w.writerow({
            "segment_id": "",
            "name": SEG_NAME,
            "description": "",
            "rsid": DEFAULT_RSID,
            "tags": DEFAULT_TAGS,
            "structure": structure,
            "warning": "",
        })

    # dsl 출력 — 시각 확인용 (input_csv_maker 의 dsl 형식과 동일)
    blocks = [
        "--- segment",
        f"name: {SEG_NAME}",
        f"rsid: {DEFAULT_RSID}",
        "",
        structure_to_dsl(structure),
    ]
    with open(out_dsl, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks) + "\n")

    print(f"[{ts}] input_csv_maker_cc00.py")
    print(f"  세그먼트: {SEG_NAME}")
    print(f"  출력 csv: {out_csv.name}")
    print(f"  출력 dsl: {out_dsl.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
