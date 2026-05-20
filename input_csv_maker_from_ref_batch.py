# input_csv_maker_from_ref_batch.py
# 2026-05-19  Jonghyun Park w/ Claude
"""
input_csv_maker_from_ref.py 의 batch 버전 — 여러 SEG_REF 를 한 번에 처리.

흐름:
  · INPUT_CSV (segment_id, name, rsid 컬럼 필수 — lookup csv 호환) 의 각 row 별로:
      - SEG_REF = row.segment_id
      - rsid → region 자동 결정 (rsid_placeholder → us, 그 외 → global)
      - region 별 COMMON_REF + COMMON_NAME + ATC ref/name 자동 매핑
      - NAME_PREFIX + name 으로 새 segment name 빌드
      - SCOPE_MODE 따라 visit + delayed_purchase 새 segment 생성 (POST 용 — segment_id 빈 채)

input csv 형식 (lookup csv 호환):
  segment_id, name, rsid    ← 필수 컬럼 (다른 컬럼은 무시)

DSL 패턴 (input_csv_maker_from_ref.py 와 동일):
  visit:
    visit(
      hit(
        '<COMMON_NAME>'!hit( @<COMMON_REF> )
        AND
        @<SEG_REF>
      )
    )
  delayed_purchase:
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
  python input_csv_maker_from_ref_batch.py
  python aa_create_segment_v2_2.py --input segments_from_ref_batch_<ts>.csv --apply
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# 입력 csv — segment_id, name, rsid 컬럼 필수 (lookup csv 호환).
# create 모드: input segment_id = SEG_REF (새 segment 안 박힐 @ref)
# update 모드: input segment_id = update 대상. SEG_REF 는 SEG_REF_SOURCE_CSV 에서 name key 매칭.
INPUT_CSV = "us_recomm_15_input.csv"

# OUTPUT_MODE — "create" (default, segment_id 빈 채 → POST) / "update" (segment_id 박은 채 → PUT)
OUTPUT_MODE = "create"

# update 모드에서 SEG_REF 매핑용 source csv (segment_id + name 컬럼 필요).
# input csv 의 name 의 "Product Recommendation - XX. *" key 와 source 의 name key 매칭으로 SEG_REF 결정.
# 빈 값이면 self-ref (input 의 segment_id 그대로).
SEG_REF_SOURCE_CSV = "segment_v2_2_result_260520_1108.csv"

# 한 SEG_REF 로 어떤 segment 만들지 — 콤마 구분.
# update 모드면 input row 의 name 의 scope suffix (Visit / Delayed Purchase) 자동 감지로 1 개만 빌드.
SCOPE_MODE = "visit,delayed_purchase"

# region 별 매핑 — COMMON_REF (Campaign Main Page_Evar), ATC ref, NAME_PREFIX 모두 region 자동 분기.
REGION_CONFIG: dict[str, dict[str, str]] = {
    "global": {
        "common_ref":  "segment_id_placeholder",
        "common_name": "[CAMPAIGN NAME] Campaign Main Page_Evar",
        "atc_ref":     "YOUR_PROJECT_ID",
        "atc_name":    "[Global] Add to Cart Visit",
        "name_prefix": "[CAMPAIGN NAME] CC_",
    },
    "us": {
        "common_ref":  "segment_id_placeholder",
        "common_name": "[CAMPAIGN NAME] US_Campaign Main Page_Evar",
        "atc_ref":     "segment_id_placeholder",
        "atc_name":    "[US] Add to Cart Visit",
        "name_prefix": "[CAMPAIGN NAME] US_CC_",
    },
}

def _detect_region(rsid: str) -> str:
    """rsid → region (us / global)."""
    return "us" if (rsid or "").strip().lower() == "rsid_placeholder" else "global"

DEFAULT_TAGS = ""

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE     = "segments_from_ref_batch_{ts}.csv"
OUTPUT_DSL_NAME_TEMPLATE = "segments_from_ref_batch_{ts}.dsl"


def _resolve_input_csv() -> Path | None:
    if INPUT_CSV.strip():
        p = OUTPUT_DIR / INPUT_CSV.strip()
        return p if p.exists() else None
    explicit = OUTPUT_DIR / "from_ref_batch_input.csv"
    if explicit.exists():
        return explicit
    cands = sorted(OUTPUT_DIR.glob("segment_lookup_pjt_*_md.csv"), reverse=True)
    return cands[0] if cands else None


def _common_ref_tokens(common_ref: str, common_name: str) -> list[str]:
    if not common_ref:
        return []
    if common_name:
        return [
            f"'{common_name}'!hit(",
            f"@{common_ref}",
            ")",
        ]
    return [f"@{common_ref}"]


def build_visit_structure(seg_ref: str, common_ref: str, common_name: str) -> str:
    common = _common_ref_tokens(common_ref, common_name)
    if common:
        parts = ["visit(", "hit("]
        parts.extend(common)
        parts.append("AND")
        parts.append(f"@{seg_ref}")
        parts.extend([")", ")"])
    else:
        parts = ["visit(", f"@{seg_ref}", ")"]
    return " | ".join(parts)


def build_delayed_purchase_structure(seg_ref: str, base_name: str,
                                     common_ref: str, common_name: str,
                                     atc_ref: str, atc_name: str) -> str:
    common = _common_ref_tokens(common_ref, common_name)
    parts: list[str] = [
        "hit(",
        "visit(",
        f"'{base_name}'!hit(",
    ]
    if common:
        parts.extend(common)
        parts.append("AND")
    parts.append(f"@{seg_ref}")
    parts.extend([")", "THEN"])
    if atc_ref:
        if atc_name:
            parts.append(f"'{atc_name}'!hit(")
            parts.append(f"@{atc_ref}")
            parts.append(")")
        else:
            parts.append(f"@{atc_ref}")
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


def _strip_scope_suffix(name: str) -> str:
    for suf in (" (Visit)", " (Delayed Purchase)", " (Visitor)"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


_RE_BRACKET_US     = re.compile(r"\[US\]\s*")
_RE_US_SOLO        = re.compile(r"\bUS_(?!CC_)")


def _dedupe_us_in_name(name: str) -> str:
    """우선순위: US_CC_ > US_ > [US]. 상위 패턴이 있으면 [US] 제거.
    상위 모두 없고 [US] 만 있으면 그대로 유지.

    예) '[CAMPAIGN NAME] US_CC_[US] Product Recommendation - 01.' → '[CAMPAIGN NAME] US_CC_Product Recommendation - 01.'
    예) '[CAMPAIGN NAME] US_[US] Foo'                            → '[CAMPAIGN NAME] US_Foo'
    예) '[CAMPAIGN NAME] [US] Foo'                               → '[CAMPAIGN NAME] [US] Foo'  (변경 없음)
    """
    has_us_cc = "US_CC_" in name
    name_no_us_cc = name.replace("US_CC_", "")
    has_us_solo = _RE_US_SOLO.search(name_no_us_cc) is not None
    if has_us_cc or has_us_solo:
        return _RE_BRACKET_US.sub("", name).strip()
    return name


def _extract_name_key(name: str) -> str:
    """name 에서 prefix/scope suffix 다 떼서 핵심 key 추출 — 두 csv 매핑용.
    예: '[CAMPAIGN NAME] US_CC_Product Recommendation - 01. Top Selling (Visit)' → 'Product Recommendation - 01. Top Selling'
        '[part_name] US_Product Recommendation - 01. Top Selling'             → 'Product Recommendation - 01. Top Selling'
    """
    s = re.sub(r"^\[[^\]]*\]\s*", "", name)         # 대괄호 prefix 제거
    s = re.sub(r"^(US_CC_|US_|CC_)", "", s)          # 추가 prefix 제거
    for suf in (" (Visit)", " (Delayed Purchase)", " (Visitor)", " (Hit)"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.strip()


def _load_seg_ref_source(path: Path) -> dict[str, str]:
    """SEG_REF_SOURCE_CSV → {name_key: segment_id} 매핑 dict."""
    if not path.exists():
        return {}
    mapping: dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fn_lower = {h.strip().lower(): h for h in (reader.fieldnames or [])}
        id_col   = fn_lower.get("segmentid") or fn_lower.get("segment_id") or fn_lower.get("id")
        name_col = fn_lower.get("name")
        if not (id_col and name_col):
            return {}
        for row in reader:
            sid = (row.get(id_col) or "").strip()
            nm  = (row.get(name_col) or "").strip()
            if sid and nm:
                key = _extract_name_key(nm)
                if key and key not in mapping:
                    mapping[key] = sid
    return mapping


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    src_path = _resolve_input_csv()
    if src_path is None:
        print(f"ERROR: input csv 못 찾음 — INPUT_CSV={INPUT_CSV!r}")
        return 1
    print(f"  [input] {src_path.name}")

    output_mode = (OUTPUT_MODE or "create").strip().lower()
    if output_mode not in ("create", "update"):
        print(f"ERROR: OUTPUT_MODE 알 수 없는 값: {output_mode!r} (허용: create / update)")
        return 1
    print(f"  [output mode] {output_mode}")

    modes_raw = (SCOPE_MODE or "").lower()
    modes = {m.strip() for m in modes_raw.split(",") if m.strip()}
    invalid = modes - {"visit", "delayed_purchase"}
    if invalid:
        print(f"ERROR: SCOPE_MODE 알 수 없는 값: {invalid} (허용: visit / delayed_purchase)")
        return 1
    if not modes:
        print("ERROR: SCOPE_MODE 비어있음")
        return 1

    # update 모드: SEG_REF source csv 로딩 (name_key → seg_ref 매핑)
    seg_ref_map: dict[str, str] = {}
    if output_mode == "update" and SEG_REF_SOURCE_CSV.strip():
        ref_path = OUTPUT_DIR / SEG_REF_SOURCE_CSV.strip()
        seg_ref_map = _load_seg_ref_source(ref_path)
        print(f"  [seg_ref source] {ref_path.name} → {len(seg_ref_map)} mapping")

    out_rows: list[dict] = []
    skipped: list[tuple[str, str]] = []
    with open(src_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fn_lower = {h.strip().lower(): h for h in (reader.fieldnames or [])}
        id_col   = fn_lower.get("segment_id") or fn_lower.get("segmentid") or fn_lower.get("id")
        name_col = fn_lower.get("name") or fn_lower.get("name_base")
        rsid_col = fn_lower.get("rsid")
        if not (id_col and name_col and rsid_col):
            print(f"ERROR: 필수 컬럼 못 찾음 — segment_id={id_col!r}, name={name_col!r}, rsid={rsid_col!r}")
            print(f"  헤더: {reader.fieldnames}")
            return 1

        for row in reader:
            input_id = (row.get(id_col) or "").strip()
            raw_name = (row.get(name_col) or "").strip()
            rsid = (row.get(rsid_col) or "").strip()
            # rsid 빈 채면 name 으로 자동 추론
            if not rsid:
                if "US_" in raw_name or "[US]" in raw_name:
                    rsid = "rsid_placeholder"
                else:
                    rsid = "rsid_placeholder"

            if not input_id or not raw_name:
                skipped.append((raw_name or "(no name)", "segment_id 또는 name 빈 채"))
                continue

            region = _detect_region(rsid)
            cfg = REGION_CONFIG.get(region)
            if cfg is None:
                skipped.append((raw_name, f"region '{region}' 매핑 없음"))
                continue

            # update / create 모드별 SEG_REF + scope 결정
            if output_mode == "update":
                key = _extract_name_key(raw_name)
                seg_ref = seg_ref_map.get(key, input_id)   # 매핑 없으면 self-ref fallback
                # scope 자동 감지 — name suffix 보고 1 개만 빌드
                if raw_name.endswith(" (Visit)") or raw_name.endswith(" (Visitor)"):
                    row_modes = {"visit"} & modes or {"visit"}
                elif raw_name.endswith(" (Delayed Purchase)"):
                    row_modes = {"delayed_purchase"} & modes or {"delayed_purchase"}
                else:
                    row_modes = modes
                if "delayed_purchase" in row_modes and not cfg["atc_ref"]:
                    skipped.append((raw_name, f"region '{region}' atc_ref 없음"))
                    continue
                output_seg_id = input_id   # PUT 대상
            else:   # create
                seg_ref = input_id   # input segment_id = inner @ref
                row_modes = modes
                if "delayed_purchase" in row_modes and not cfg["atc_ref"]:
                    skipped.append((raw_name, f"region '{region}' atc_ref 없음"))
                    continue
                output_seg_id = ""

            # base name — scope suffix 제거 후 region 별 name_prefix 적용
            stripped = _strip_scope_suffix(raw_name)
            name_prefix = cfg["name_prefix"]
            base = stripped
            if name_prefix and not base.startswith(name_prefix):
                base = name_prefix + base
            base = _dedupe_us_in_name(base)

            if "visit" in row_modes:
                v_name = base + " (Visit)"
                out_rows.append({
                    "segment_id": output_seg_id, "name": v_name, "description": "",
                    "rsid": rsid, "tags": DEFAULT_TAGS,
                    "structure": build_visit_structure(seg_ref, cfg["common_ref"], cfg["common_name"]),
                    "warning": "",
                })
            if "delayed_purchase" in row_modes:
                dp_name = base + " (Delayed Purchase)"
                out_rows.append({
                    "segment_id": output_seg_id, "name": dp_name, "description": "",
                    "rsid": rsid, "tags": DEFAULT_TAGS,
                    "structure": build_delayed_purchase_structure(
                        seg_ref, base,
                        cfg["common_ref"], cfg["common_name"],
                        cfg["atc_ref"], cfg["atc_name"],
                    ),
                    "warning": "",
                })

    ts = datetime.now().strftime("%y%m%d_%H%M")
    out_csv = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(ts=ts)
    out_dsl = OUTPUT_DIR / OUTPUT_DSL_NAME_TEMPLATE.format(ts=ts)

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f, fieldnames=["segment_id", "name", "description", "rsid", "tags", "structure", "warning"]
        )
        w.writeheader()
        w.writerows(out_rows)

    blocks: list[str] = []
    for row in out_rows:
        blocks.append("--- segment")
        blocks.append(f"name: {row['name']}")
        blocks.append(f"rsid: {row['rsid']}")
        blocks.append("")
        blocks.append(structure_to_dsl(row["structure"]))
        blocks.append("")
    with open(out_dsl, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))

    print(f"[{ts}] input_csv_maker_from_ref_batch.py")
    print(f"  input : {src_path.name}")
    print(f"  SCOPE_MODE: {SCOPE_MODE}")
    print(f"  output: {len(out_rows)} row → {out_csv.name}")
    print(f"  dsl   : {out_dsl.name}")
    for row in out_rows:
        print(f"     - {row['name']}  (rsid={row['rsid']})")
    if skipped:
        print(f"  skip: {len(skipped)} row")
        for name, reason in skipped:
            print(f"     - {name[:60]}  ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
