# input_csv_maker_from_ref_batch_us_hit.py
# 2026-05-20  Jonghyun Park w/ Claude
#
# 모드: hit-only US 변환 maker.
#   · input csv 의 각 segment GET → raw definition 받음
#   · definition 안 'and' 컨테이너의 evar96 starts-with pred 를 두 pred 로 expand:
#       evar96 starts-with "<long CAMPAIGN NAME url>"
#       →
#       evar96 contains ">product recommendation with reason"
#       AND
#       evar96 starts-with "https://www.company_name.com/us/offer/"
#   · 결과 definition → decompile → DSL " | " 한 줄
#   · name: scope suffix 제거 + "[US]" → "US_" swap + NAME_PREFIX "[part_name] " 추가
#   · segment_id 빈 채로 POST (신규 생성)
"""
input csv 형식 (lookup csv 호환):
  segment_id, name, rsid    ← 필수

사용:
  python input_csv_maker_from_ref_batch_us_hit.py
  python aa_create_segment_v2_2.py --input segments_from_ref_batch_us_hit_<ts>.csv --apply
"""
from __future__ import annotations

import copy
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

from aa_segment_lookup import (
    _load_auth_headers,
    _lookup_segment,
    decompile_definition,
    _set_daterange_auth,
)

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

INPUT_CSV = "combined_us_evar105_update_input.csv"

# OUTPUT_MODE — "create" (segment_id 빈 채 → POST 신규) / "update" (segment_id 박은 채 → PUT)
OUTPUT_MODE = "update"

NAME_PREFIX = "[part_name] "

# Swap 룰 — definition tree 안 'and' 컨테이너에서 매칭되면 N pred 로 expand.
#   N=1 면 in-place 변환 (operator/str 만 바뀜), N=2 면 1→2 expand.
SWAP_VAR_NAME = "variables/evar96"
SWAP_OLD_OP   = "contains"
SWAP_NEW_PREDS: list[dict] = [
    {"func": "contains", "val": {"func": "attr", "name": "variables/evar96"},
     "str":  ">recommended picks tailored for you"},
]

DEFAULT_TAGS = ""

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE     = "segments_from_ref_batch_us_hit_{ts}.csv"
OUTPUT_DSL_NAME_TEMPLATE = "segments_from_ref_batch_us_hit_{ts}.dsl"


def _swap_pred_str_in_tree(node, matched: list[int]) -> None:
    """definition tree 재귀 walk → SWAP_OLD_OP / SWAP_VAR_NAME 매칭 pred 의 str 값을 SWAP_NEW_PREDS[0]['str'] 로 in-place 변경.
    (SWAP_NEW_PREDS 가 단일 pred 이고 같은 var/op 일 때 단순 str replace 의미.)"""
    new_str = SWAP_NEW_PREDS[0].get("str", "") if SWAP_NEW_PREDS else ""
    def _walk(n):
        if isinstance(n, dict):
            if n.get("func") == SWAP_OLD_OP:
                val = n.get("val", {})
                if isinstance(val, dict) and val.get("name") == SWAP_VAR_NAME:
                    n["str"] = new_str
                    matched[0] += 1
                    return
            for v in n.values():
                if isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)
    _walk(node)


def _swap_evar96_in_tree(node, matched: list[int]) -> None:
    """definition tree 재귀 walk → 'and' 컨테이너 안 evar96 <SWAP_OLD_OP> pred 를 두 pred 로 expand."""
    if isinstance(node, dict):
        if node.get("func") == "and":
            preds = node.get("preds", [])
            new_preds = []
            for p in preds:
                if isinstance(p, dict) and p.get("func") == SWAP_OLD_OP:
                    val = p.get("val", {})
                    if isinstance(val, dict) and val.get("name") == SWAP_VAR_NAME:
                        new_preds.extend(copy.deepcopy(SWAP_NEW_PREDS))
                        matched[0] += 1
                        continue
                _swap_evar96_in_tree(p, matched)
                new_preds.append(p)
            node["preds"] = new_preds
            return
        for v in node.values():
            if isinstance(v, (dict, list)):
                _swap_evar96_in_tree(v, matched)
    elif isinstance(node, list):
        for item in node:
            _swap_evar96_in_tree(item, matched)


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
    for suf in (" (Visit)", " (Delayed Purchase)", " (Visitor)", " (Hit)"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


_RE_BRACKET_US = re.compile(r"\[US\]\s*")


def _swap_bracket_us_to_underscore(name: str) -> str:
    """name 안 '[US] ' (또는 '[US]') → 'US_' 변환."""
    return _RE_BRACKET_US.sub("US_", name)


def _resolve_input_csv() -> Path | None:
    if INPUT_CSV.strip():
        p = OUTPUT_DIR / INPUT_CSV.strip()
        return p if p.exists() else None
    return None


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

    print("Authenticating ...")
    headers, gcid = _load_auth_headers()
    _set_daterange_auth(headers, gcid)
    print()

    out_rows: list[dict] = []
    skipped: list[tuple[str, str]] = []
    match_summary: list[tuple[str, int]] = []

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
        rows = list(reader)

    for i, row in enumerate(rows):
        sid = (row.get(id_col) or "").strip()
        raw_name = (row.get(name_col) or "").strip()
        rsid = (row.get(rsid_col) or "").strip()
        if not sid or not raw_name:
            skipped.append((raw_name or "(no name)", "segment_id 또는 name 빈 채"))
            continue

        print(f"  [{i+1}/{len(rows)}] GET {sid} ...", end=" ")
        info = _lookup_segment(headers, gcid, sid)
        if info.get("error"):
            skipped.append((raw_name, f"GET 실패: {info['error'][:80]}"))
            print(f"FAIL")
            continue
        defn = info.get("definition")
        if not defn:
            skipped.append((raw_name, "definition 없음"))
            print("FAIL — definition 없음")
            continue
        print("OK")

        # definition 복사 후 swap — SWAP_NEW_PREDS 단일 pred 면 in-place str 변경,
        # 여러 pred 면 and 컨테이너 expand (evar96 같은 1→2 케이스).
        new_defn = copy.deepcopy(defn)
        matched = [0]
        if len(SWAP_NEW_PREDS) == 1:
            _swap_pred_str_in_tree(new_defn, matched)
        else:
            _swap_evar96_in_tree(new_defn, matched)

        # name 처리 — update 모드면 raw_name 그대로, create 모드면 prefix/swap 적용
        if (OUTPUT_MODE or "create").strip().lower() == "update":
            base = raw_name
            output_seg_id = sid
        else:
            stripped = _strip_scope_suffix(raw_name)
            swapped = _swap_bracket_us_to_underscore(stripped)
            base = swapped
            if NAME_PREFIX and not base.startswith(NAME_PREFIX):
                base = NAME_PREFIX + base
            output_seg_id = ""

        # DSL 변환 → ' | ' 한 줄
        try:
            dsl_text = decompile_definition(new_defn)
            structure = dsl_text.replace('"', "'").replace("\n", " | ")
        except Exception as e:
            skipped.append((raw_name, f"decompile 실패: {e}"))
            continue

        out_rows.append({
            "segment_id": output_seg_id, "name": base, "description": info.get("description", ""),
            "rsid": rsid, "tags": DEFAULT_TAGS,
            "structure": structure,
            "warning": f"swap matched: {matched[0]}" if matched[0] else "no swap match",
        })
        match_summary.append((base, matched[0]))

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

    n_matched = sum(1 for _, m in match_summary if m)
    print()
    print(f"[{ts}] input_csv_maker_from_ref_batch_us_hit.py")
    print(f"  input : {src_path.name}")
    print(f"  output: {len(out_rows)} row → {out_csv.name}")
    print(f"  dsl   : {out_dsl.name}")
    print(f"  swap 매칭: {n_matched}/{len(match_summary)}")
    for base, m in match_summary:
        flag = "✓" if m else "·"
        print(f"  {flag} {base}  (swap={m})")
    if skipped:
        print(f"  skip: {len(skipped)} row")
        for name, reason in skipped:
            print(f"     - {name[:60]}  ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
