# input_csv_maker_from_ref.py
# 2026-05-19  Jonghyun Park w/ Claude
#
# 모드: 단일 segment-ref 1 개 → visit + delayed_purchase 즉석 생성 (csv 안 읽음)
# 같은 폴더 형제 maker 들과의 차이:
#   · input_csv_maker_from_ref.py        ← 본 파일. 단건. 상단 상수 박고 실행.
#   · input_csv_maker_from_ref_batch.py  ← csv batch. 여러 SEG_REF 한 번에. region 자동 분기.
#   · input_csv_maker_replace.py         ← csv batch + 기존 segment GET → [CAMPAIGN NAME] *_Evar swap (inner 보존).
"""
input_csv_maker_cc00.py 변형 — segment-ref 1 개만 기반으로 visit + delayed_purchase 생성.

흐름:
  · SEG_REF (한 개) + SEG_NAME_BASE 박음
  · SCOPE_MODE 에 따라 visit 와 delayed_purchase 둘 다 또는 일부 생성
  · v2.2 input csv (segment_id, name, description, rsid, tags, structure, warning) 형식

DSL 패턴:
  · visit (COMMON_SEGMENT_REF 박혀있을 때):
      visit(
        hit(
          '<COMMON_NAME>'!hit( @<COMMON_REF> )   # COMMON_SEGMENT_REF_NAME 있으면 wrap
          AND
          @<SEG_REF>
        )
      )
  · visit (COMMON_SEGMENT_REF 빈 채):
      visit(
        @<SEG_REF>
      )
  · delayed_purchase (input_csv_maker.py 의 reference 패턴 동일):
      hit(
        visit(
          '<base_name>'!hit(
            '<COMMON_NAME>'!hit( @<COMMON_REF> )
            AND
            @<SEG_REF>
          )
          THEN
          '<ATC_NAME>'!hit( @<ATC_REF> )
          AND
          hit( NOT orders event-exists )
        )
        THEN
        visit( orders event-exists )
      )

사용:
  python input_csv_maker_from_ref.py
  python aa_create_segment_v2.2.py --input segments_from_ref_<ts>.csv --update-or-create --lookup-by-name --apply
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# 베이스 segment-ref (visit / delayed_purchase 양쪽에서 활용)
SEG_REF       = "세그먼트_아이디_넘버"
# SEG_NAME_BASE = "[CAMPAIGN NAME] CC_XX. 세그명"   # ' (Visit)' / ' (Delayed Purchase)' suffix 자동 붙음
SEG_NAME_BASE = "Product Recommendation - 15. Theme Category Popular"

# 이름 prefix — visit / delayed_purchase segment 이름 앞에 자동 추가 (base 가 이미 시작하면 중복 안 함).
# 빈 값이면 prefix 없이 SEG_NAME_BASE 그대로. global / us 케이스에 맞춰 사용자가 토글.
NAME_PREFIX = "[CAMPAIGN NAME] CC_"
# NAME_PREFIX = "[CAMPAIGN NAME] US_CC_"   # US 일 때

# Common evar segment-ref — visit / delayed_purchase 안에서 SEG_REF 와 AND 로 묶일 메인 ref
# (input_csv_maker.py 의 COMMON_SEGMENT_REF 와 동일 역할)
# 글로벌: "[CAMPAIGN NAME] Campaign Main Page_Evar" / US: "[CAMPAIGN NAME] US_Campaign Main Page_Evar"
# 빈 값 → AND 안 묶음 (SEG_REF 단독)
COMMON_SEGMENT_REF      = "세그먼트_아이디_넘버" # [CAMPAIGN NAME] Campaign Main Page_Evar
# COMMON_SEGMENT_REF      = "세그먼트_아이디_넘버"  # US — [CAMPAIGN NAME] US_Campaign Main Page_Evar
COMMON_SEGMENT_REF_NAME = ""   # 박혀 있으면 named container wrap: '<name>'!hit( @<COMMON> )

# Delayed Purchase 용 ATC visit segment-ref (없으면 delayed_purchase mode 비활성)
# 글로벌: [Global] Add to Cart Visit  / US: [US] Add to Cart Visit
ATC_VISIT_SEGMENT_REF  = "YOUR_ID"
ATC_VISIT_SEGMENT_NAME = "[Global] Add to Cart Visit"

# 한 SEG_REF 로 어떤 segment 만들지 — 콤마 구분.
#   "visit"            → visit 1 개
#   "delayed_purchase" → delayed_purchase 1 개
#   "visit,delayed_purchase" → 둘 다 (default)
SCOPE_MODE = "visit,delayed_purchase"

DEFAULT_RSID = "sscompany_name4mstglobal"
# DEFAULT_RSID = "sscompany_namenewus"
DEFAULT_TAGS = ""

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE     = "segments_from_ref_{ts}.csv"
OUTPUT_DSL_NAME_TEMPLATE = "segments_from_ref_{ts}.dsl"


def _common_ref_tokens() -> list[str]:
    """COMMON_SEGMENT_REF 토큰 — name 있으면 named container wrap, 없으면 raw @id."""
    if not COMMON_SEGMENT_REF:
        return []
    if COMMON_SEGMENT_REF_NAME:
        return [
            f"'{COMMON_SEGMENT_REF_NAME}'!hit(",
            f"@{COMMON_SEGMENT_REF}",
            ")",
        ]
    return [f"@{COMMON_SEGMENT_REF}"]


def build_visit_structure() -> str:
    """visit( hit( @<COMMON> AND @<SEG_REF> ) ) — COMMON_SEGMENT_REF 있으면 AND 묶음.
    빈 채면 visit( @<SEG_REF> ) 단순.
    """
    common = _common_ref_tokens()
    if common:
        parts = ["visit(", "hit("]
        parts.extend(common)
        parts.append("AND")
        parts.append(f"@{SEG_REF}")
        parts.extend([")", ")"])
    else:
        parts = ["visit(", f"@{SEG_REF}", ")"]
    return " | ".join(parts)


def build_delayed_purchase_structure(base_name: str) -> str:
    """input_csv_maker.py 의 _build_delayed_purchase_structure 패턴 — COMMON_SEGMENT_REF 있으면 AND 묶음."""
    common = _common_ref_tokens()
    parts: list[str] = [
        "hit(",
        "visit(",
        f"'{base_name}'!hit(",
    ]
    if common:
        parts.extend(common)
        parts.append("AND")
    parts.append(f"@{SEG_REF}")
    parts.extend([
        ")",
        "THEN",
    ])
    if ATC_VISIT_SEGMENT_REF:
        if ATC_VISIT_SEGMENT_NAME:
            parts.append(f"'{ATC_VISIT_SEGMENT_NAME}'!hit(")
            parts.append(f"@{ATC_VISIT_SEGMENT_REF}")
            parts.append(")")
        else:
            parts.append(f"@{ATC_VISIT_SEGMENT_REF}")
    parts.extend([
        "AND",
        "hit(", "NOT orders event-exists", ")",
        ")",
        "THEN",
        "visit(", "orders event-exists", ")",
        ")",
    ])
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

    modes_raw = (SCOPE_MODE or "").lower()
    modes = {m.strip() for m in modes_raw.split(",") if m.strip()}
    if not modes:
        print("ERROR: SCOPE_MODE 비어있음")
        return 1
    invalid = modes - {"visit", "delayed_purchase"}
    if invalid:
        print(f"ERROR: SCOPE_MODE 알 수 없는 값: {invalid} (허용: visit / delayed_purchase)")
        return 1
    if "delayed_purchase" in modes and not ATC_VISIT_SEGMENT_REF:
        print("ERROR: delayed_purchase mode 인데 ATC_VISIT_SEGMENT_REF 비어있음")
        return 1

    # 이름 base — NAME_PREFIX 자동 추가 (중복 방지)
    base = SEG_NAME_BASE
    if NAME_PREFIX and not base.startswith(NAME_PREFIX):
        base = NAME_PREFIX + base

    rows: list[dict] = []
    if "visit" in modes:
        v_name = base + (" (Visit)" if not base.endswith(" (Visit)") else "")
        rows.append({
            "segment_id": "", "name": v_name, "description": "",
            "rsid": DEFAULT_RSID, "tags": DEFAULT_TAGS,
            "structure": build_visit_structure(), "warning": "",
        })
    if "delayed_purchase" in modes:
        dp_name = base + (" (Delayed Purchase)"
                          if not base.endswith(" (Delayed Purchase)") else "")
        rows.append({
            "segment_id": "", "name": dp_name, "description": "",
            "rsid": DEFAULT_RSID, "tags": DEFAULT_TAGS,
            "structure": build_delayed_purchase_structure(base), "warning": "",
        })

    # csv 출력
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f, fieldnames=["segment_id", "name", "description", "rsid", "tags", "structure", "warning"]
        )
        w.writeheader()
        w.writerows(rows)

    # dsl 출력 (시각 확인용)
    blocks: list[str] = []
    for row in rows:
        blocks.append("--- segment")
        blocks.append(f"name: {row['name']}")
        blocks.append(f"rsid: {row['rsid']}")
        blocks.append("")
        blocks.append(structure_to_dsl(row["structure"]))
        blocks.append("")
    with open(out_dsl, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))

    print(f"[{ts}] input_csv_maker_from_ref.py")
    print(f"  SEG_REF       : {SEG_REF}")
    print(f"  SEG_NAME_BASE : {SEG_NAME_BASE}")
    print(f"  SCOPE_MODE    : {SCOPE_MODE}")
    print(f"  output: {len(rows)} row")
    print(f"  출력 csv: {out_csv.name}")
    print(f"  출력 dsl: {out_dsl.name}")
    for row in rows:
        print(f"     - {row['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
