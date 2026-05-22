# input_csv_maker_replace.py
# 2026-05-19  Jonghyun Park w/ Claude
# updated: 2026-05-22  — lookup csv fallback 경로를 같은 폴더의 lookup/ 하위로 변경
#
# 모드: 기존 segment GET → [CAMPAIGN NAME] *_Evar 컨테이너만 [CAMPAIGN NAME] *_Evar 로 swap (inner 보존)
# 같은 폴더 형제 maker 들과의 차이:
#   · input_csv_maker_from_ref.py        ← 단건. csv 안 읽음. 단순 wrap 만 새로 빌드.
#   · input_csv_maker_from_ref_batch.py  ← csv batch. row 당 visit + delayed_purchase 새로 빌드. inner 무시.
#   · input_csv_maker_replace.py         ← 본 파일. csv batch + 기존 segment GET → Evar 컨테이너만 swap. inner 보존.
"""
input csv 의 각 row segment 를 AA GET 으로 받아 inner condition 보존 +
[CAMPAIGN NAME] *_Evar 컨테이너만 region 맞는 [CAMPAIGN NAME] *_Evar 로 swap → 새 segment 빌드.

흐름 (row 별):
  1) AA GET segment definition (aa_segment_lookup._lookup_segment 재활용)
  2) definition tree 재귀 walk
     · container.description 이 [CAMPAIGN NAME] *_Evar 패턴 매칭되면
       → pred 를 {"func": "segment-ref", "segmentId": "<region 별 sw_evar_id>"} 로 교체
       → description 도 [CAMPAIGN NAME] *_Evar (region 별) 로 변경
  3) name swap — "[CAMPAIGN NAME]" → "[CAMPAIGN NAME]" / 없으면 NAME_PREFIX 자동 추가
  4) decompile_definition 으로 DSL → " | " 구분 structure 컬럼
  5) v2.2 input csv 형식 출력 (segment_id, name, description, rsid, tags, structure, warning)

OUTPUT_MODE:
  · "create" (default) → segment_id 빈 채 출력 (v2.2 가 POST 신규 생성, 기존 [CAMPAIGN NAME] 보존)
  · "update"           → segment_id 입력 그대로 박은 채 (v2.2 가 PUT, 기존 segment 갱신)

input csv 형식 (lookup csv 호환):
  segment_id, name, rsid    ← 필수 (다른 컬럼 무시)

사용:
  python input_csv_maker_replace.py
  python aa_create_segment_v2.2.py --input segments_replace_<ts>.csv --update-or-create --apply
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

# 입력 csv — segment_id, name, rsid 컬럼 필수 (lookup csv 호환).
# 빈 값이면 폴더의 replace_input.csv → lookup/segment_lookup_pjt_*_md.csv → segment_lookup_pjt_*_md.csv 순으로 fallback.
INPUT_CSV = "replace_input.csv"

# 출력 모드 — "create" (POST 신규, segment_id 빈 채) / "update" (PUT 기존 갱신, segment_id 박힌 채)
OUTPUT_MODE = "create"

# swap 대상 컨테이너 description 정규식 — [CAMPAIGN NAME] 시작 + _Evar 끝 (안 임의 문자열 — ALL SITES / US 등)
MD_EVAR_DESC_REGEX = re.compile(r"^\[CAMPAIGN NAME\].*_Evar$")

# region 별 매핑 — COMMON_REF (swap 대상 컨테이너의 새 segment-ref), name swap prefix
REGION_CONFIG: dict[str, dict[str, str]] = {
    "global": {
        "evar_ref":    "segment_id_placeholder",
        "evar_name":   "[CAMPAIGN NAME] Campaign Main Page_Evar",
        "name_prefix": "[CAMPAIGN NAME] CC_",
    },
    "us": {
        "evar_ref":    "segment_id_placeholder",
        "evar_name":   "[CAMPAIGN NAME] US_Campaign Main Page_Evar",
        "name_prefix": "[CAMPAIGN NAME] US_CC_",
    },
}


def _detect_region(rsid: str, name: str) -> str:
    """rsid + name 둘 다 체크. 한 쪽이라도 US 면 us. 둘이 안 맞으면 warning 출력."""
    rsid_us = (rsid or "").strip().lower() == "rsid_placeholder"
    name_lower = (name or "").lower()
    name_us = "[us]" in name_lower or "us_" in name_lower
    if rsid_us != name_us:
        print(f"  [warn] region 불일치: rsid_us={rsid_us}, name_us={name_us} — {name[:60]}")
    return "us" if (rsid_us or name_us) else "global"


DEFAULT_TAGS = ""

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
LOOKUP_DIR = OUTPUT_DIR / "lookup"          # aa_segment_lookup_from_pjt 결과 csv 위치
OUTPUT_NAME_TEMPLATE = "segments_replace_{ts}.csv"
OUTPUT_DSL_NAME_TEMPLATE = "segments_replace_{ts}.dsl"


def _resolve_input_csv() -> Path | None:
    if INPUT_CSV.strip():
        p = OUTPUT_DIR / INPUT_CSV.strip()
        return p if p.exists() else None
    explicit = OUTPUT_DIR / "replace_input.csv"
    if explicit.exists():
        return explicit
    # lookup csv fallback — 새 위치 (LOOKUP_DIR) 우선, 구위치 (OUTPUT_DIR) 호환
    cands = sorted(LOOKUP_DIR.glob("segment_lookup_pjt_*_md.csv"), reverse=True)
    if not cands:
        cands = sorted(OUTPUT_DIR.glob("segment_lookup_pjt_*_md.csv"), reverse=True)
    return cands[0] if cands else None


def _swap_evar_in_tree(node, evar_ref: str, evar_name: str, matched: list[str]) -> None:
    """definition tree 재귀 walk → MD_EVAR_DESC_REGEX 매칭 container 의 pred 를 segment-ref 로,
    description 도 [CAMPAIGN NAME] _Evar 로 swap. 매칭된 원본 description 들 matched 리스트에 append.

    중첩 매칭 방지 — 한 container 가 매칭되면 그 안쪽은 더 walk 안 함 (swap 으로 대체됨).
    """
    if isinstance(node, dict):
        if node.get("func") == "container":
            desc = node.get("description", "")
            if MD_EVAR_DESC_REGEX.match(desc):
                matched.append(desc)
                node["description"] = evar_name
                node["pred"] = {"func": "segment-ref", "segmentId": evar_ref}
                return
        for key in ("pred", "preds", "stream"):
            sub = node.get(key)
            if isinstance(sub, dict):
                _swap_evar_in_tree(sub, evar_ref, evar_name, matched)
            elif isinstance(sub, list):
                for item in sub:
                    _swap_evar_in_tree(item, evar_ref, evar_name, matched)
    elif isinstance(node, list):
        for item in node:
            _swap_evar_in_tree(item, evar_ref, evar_name, matched)


def _swap_name(name: str, name_prefix: str) -> str:
    """name swap — '[CAMPAIGN NAME]' → '[CAMPAIGN NAME]' literal swap. 없으면 NAME_PREFIX 자동 추가."""
    if "[CAMPAIGN NAME]" in name:
        return name.replace("[CAMPAIGN NAME]", "[CAMPAIGN NAME]")
    if name.startswith("[CAMPAIGN NAME]"):
        return name
    if name_prefix and not name.startswith(name_prefix):
        return name_prefix + name
    return name


def _structure_to_dsl(structure: str) -> str:
    """structure ' | ' 한 줄 → 멀티라인 (괄호 기준 들여쓰기)."""
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

    src_path = _resolve_input_csv()
    if src_path is None:
        print(f"ERROR: input csv 못 찾음 — INPUT_CSV={INPUT_CSV!r}")
        return 1
    print(f"  [input] {src_path.name}")

    mode = (OUTPUT_MODE or "create").strip().lower()
    if mode not in ("create", "update"):
        print(f"ERROR: OUTPUT_MODE 알 수 없는 값: {mode!r} (허용: create / update)")
        return 1
    print(f"  [output mode] {mode}")
    print()

    print("Authenticating ...")
    headers, gcid = _load_auth_headers()
    _set_daterange_auth(headers, gcid)
    print()

    # input csv 읽기
    rows_in: list[dict] = []
    with open(src_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fn_lower = {h.strip().lower(): h for h in (reader.fieldnames or [])}
        id_col = fn_lower.get("segment_id") or fn_lower.get("id")
        name_col = fn_lower.get("name") or fn_lower.get("name_base")
        rsid_col = fn_lower.get("rsid")
        if not (id_col and name_col and rsid_col):
            print(f"ERROR: 필수 컬럼 못 찾음 — segment_id={id_col!r}, name={name_col!r}, rsid={rsid_col!r}")
            print(f"  헤더: {reader.fieldnames}")
            return 1
        for row in reader:
            sid = (row.get(id_col) or "").strip()
            name = (row.get(name_col) or "").strip()
            rsid = (row.get(rsid_col) or "").strip()
            if sid and name:
                rows_in.append({"segment_id": sid, "name": name, "rsid": rsid})

    out_rows: list[dict] = []
    skipped: list[tuple[str, str]] = []
    match_summary: list[tuple[str, list[str]]] = []   # (new_name, matched container descs)

    for i, row in enumerate(rows_in):
        sid = row["segment_id"]
        name = row["name"]
        rsid = row["rsid"]
        region = _detect_region(rsid, name)
        cfg = REGION_CONFIG.get(region)
        if cfg is None:
            skipped.append((name, f"region '{region}' 매핑 없음"))
            continue

        print(f"  [{i+1}/{len(rows_in)}] GET {sid} ...", end=" ")
        info = _lookup_segment(headers, gcid, sid)
        if info.get("error"):
            skipped.append((name, f"GET 실패: {info['error'][:80]}"))
            print(f"FAIL — {info['error'][:60]}")
            continue
        defn = info.get("definition")
        if not defn:
            skipped.append((name, "definition 없음"))
            print("FAIL — definition 없음")
            continue
        print(f"OK")

        # definition 복사 후 swap
        new_defn = copy.deepcopy(defn)
        matched: list[str] = []
        _swap_evar_in_tree(new_defn, cfg["evar_ref"], cfg["evar_name"], matched)

        # name swap
        new_name = _swap_name(name, cfg["name_prefix"])

        # DSL 변환 → ' | ' 한 줄
        try:
            dsl_text = decompile_definition(new_defn)
            structure = dsl_text.replace('"', "'").replace("\n", " | ")
        except Exception as e:
            skipped.append((name, f"decompile 실패: {e}"))
            continue

        out_rows.append({
            "segment_id": sid if mode == "update" else "",
            "name": new_name,
            "description": info.get("description", ""),
            "rsid": rsid,
            "tags": DEFAULT_TAGS,
            "structure": structure,
            "warning": f"swap matched: {len(matched)}" if matched else "no Evar container matched — inner 그대로, name 만 swap",
        })
        match_summary.append((new_name, matched))

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
        blocks.append(_structure_to_dsl(row["structure"]))
        blocks.append("")
    with open(out_dsl, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))

    # 매칭 요약
    n_matched = sum(1 for _, m in match_summary if m)
    n_unmatched = len(match_summary) - n_matched
    print()
    print(f"[{ts}] input_csv_maker_replace.py")
    print(f"  input  : {src_path.name}")
    print(f"  output : {len(out_rows)} row → {out_csv.name}")
    print(f"  dsl    : {out_dsl.name}")
    print(f"  mode   : {mode}")
    print(f"  swap 매칭: {n_matched}/{len(match_summary)}  (매칭 없음: {n_unmatched} — inner 그대로 + name swap만)")
    print()
    print("매칭 상세:")
    for new_name, matched in match_summary:
        if matched:
            print(f"  ✓ {new_name}  ← swap [{' | '.join(matched)}]")
        else:
            print(f"  · {new_name}  (no Evar match)")
    if skipped:
        print()
        print(f"  skip: {len(skipped)} row")
        for name, reason in skipped:
            print(f"     - {name[:60]}  ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
