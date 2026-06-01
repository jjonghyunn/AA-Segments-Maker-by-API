# compare_panel_segments.py
# 2026-05-04  Jonghyun Park w/ Claude
"""
두 Adobe Workspace project의 특정 panel에 사용된 segment들을 비교.

용도:
  · A 프로젝트 패널의 segment ⊇ B 프로젝트 패널의 segment 가 가정인데,
    실제로는 서로 겹치는 것 + 각자만 가진 것이 있다 → 정확한 차집합을 알아내기.
  · ID 비교만 아니라 이름 정규화로 logical match도 잡음 (예: "[CAMPAIGN NAME] ALL SITES_X" ↔ "[template] X").

실행:
  기본 (콘솔 + CSV 자동 저장):
    python compare_panel_segments.py
  CSV 비활성:
    python compare_panel_segments.py --no-csv
  panel 매칭 없거나 segment 못 찾을 때:
    python compare_panel_segments.py --debug

─────────────────────────────────────────────────────────────────────
결과 분류 (CSV의 `Group` 컬럼):
─────────────────────────────────────────────────────────────────────
  · Same ID         : 양쪽 panel 다 같은 segment 사용 (ID 일치).        → Side = "A&B" (1 row)
  · Logical match   : ID 다른데 normalized name 같음.                   → Side = "A" + "B" (각 1 row, 짝)
                       (예: A쪽 "[CAMPAIGN NAME] ALL SITES_Internal_GNB" ↔
                            B쪽 "[template] Internal_GNB" → 둘 다 normalized "internal_gnb")
  · A only          : A에만 있고 logical 짝도 B에 없음 → 진짜 추가 후보. → Side = "A"
  · B only          : B에만 있고 logical 짝도 A에 없음 → 진짜 추가 후보. → Side = "B"

CSV 컬럼:
  RequestedAt | Group | Side | NormalizedName | SegmentId | Name | RSID | OwnerId

  · Group : 위 4개 카테고리 (어떤 매칭 분류인지)
  · Side  : 그 row가 A panel 출신인지 / B panel 출신인지 / 양쪽 공통인지
            ("Logical match" 안에서 같은 NormalizedName 공유하는 A/B row 짝 구별용)
  · NormalizedName : NAME_NORMALIZATION_PATTERNS 적용 후 비교 키
  · 나머지(SegmentId/Name/RSID/OwnerId) : segment 본연 정보
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"

# ─── 비교할 두 프로젝트 + 패널 ─────────────────────────────────────
# project_id = Workspace URL의 `/workspace/edit/{이부분}`
# panel_name = panel 헤더 텍스트 substring (대소문자 무시)
PROJECT_A_ID = "YOUR_ID"
PANEL_A_NAME = "[ALL SITES] 2026 CAMPAIGN NAME Campaign Traffic & CVR"

PROJECT_B_ID = "YOUR_ID"
PANEL_B_NAME = "MAIN KPI"

# ─── 결과 CSV ──────────────────────────────────────────────────────
# 결과는 항상 CSV로도 저장됨 (콘솔 출력은 그대로). 비활성화하려면 --no-csv 플래그.
# 파일명: <CSV_OUTPUT_NAME>_YYMMDD_HHMM.csv (timestamp 자동 suffix)
CSV_OUTPUT_NAME = "compare_panel_segments.csv"

# ─── 이름 정규화 패턴 (logical match 용) ─────────────────────────────
# segment ID는 다르지만 "같은 논리적 컨셉"인 경우 매칭하려면 이름을 정규화해서 비교.
# 예) "[CAMPAIGN NAME] ALL SITES_Internal_GNB"  ─┐
#     "[template] Internal_GNB"          │ → 정규화 후 둘 다 "internal_gnb" → logical match
#                                        ┘
# 각 항목은 (regex, replacement) 튜플. 순서대로 re.sub 적용됨 (대소문자 무시).
# 빈 리스트로 두면 정규화 비활성 (ID 비교만).
#
# ⚠️ 주의: 의미를 바꾸는 단어(예: "Order")는 제거하지 말고 표준화만 할 것.
#   안 그러면 "Internal_GNB"와 "Internal_GNB Order"가 같은 이름으로 합쳐져 잘못된 매칭.
NAME_NORMALIZATION_PATTERNS = [
    (r"^\[template\]\s+",                  ""),     # "[template] X"          → "X"
    (r"^\[\d{2}\s+[A-Z]+\]\s+ALL SITES_",  ""),     # "[CAMPAIGN NAME] ALL SITES_X"   → "X"
    (r"^\[\d{2}\s+[A-Z]+\]\s+",            ""),     # "[CAMPAIGN NAME] X"             → "X"
    (r"\s+&\s+",                           " "),    # "X & Y"                 → "X Y"
    #   → A쪽 "X & Order"와 B쪽 "X Order"가 둘 다 "x order"로 정규화돼 매칭됨
    #   → "X" 단독과 "X Order"는 다르게 유지 (Order는 의미 있는 단어이므로 제거 X)
]

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
OUTPUT_DIR = Path(__file__).resolve().parent

# Adobe Analytics segment ID 패턴 — `s<digits>_<hex>` 형태
SEG_ID_RE = re.compile(r"^s\d+_[0-9a-f]+$")


# ─────────────────────────────────────────────────────────────
def _load_auth_headers() -> tuple[dict, str]:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h = dict(ags.header) if isinstance(getattr(ags, "header", None), dict) else {}
    h_lower = {k.lower(): v for k, v in h.items()}
    api_key = h_lower.get("x-api-key")
    auth = h_lower.get("authorization")
    gcid = h_lower.get("x-proxy-global-company-id")
    if not (api_key and auth and gcid):
        raise RuntimeError("필수 헤더 누락")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def _fetch_project(headers: dict, gcid: str, project_id: str) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{project_id}"
    r = requests.get(
        url,
        headers=headers,
        params={"expansion": "definition,ownerFullName,modifiedDate,sharesFullName"},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GET project {project_id} 실패: {r.status_code} {r.text[:300]}")
    return r.json()


def _find_panels_by_name(project: dict, name_substring: str) -> list[dict]:
    """project.definition.workspaces[].panels[]에서 name이 substring 매칭되는 panel들 반환."""
    sub = name_substring.lower().strip()
    workspaces = project.get("definition", {}).get("workspaces", []) or []
    matched = []
    for ws in workspaces:
        for p in ws.get("panels", []) or []:
            name = (p.get("name") or "").strip()
            if sub in name.lower():
                matched.append(p)
    return matched


def _all_panel_names(project: dict) -> list[str]:
    """디버그용 — 프로젝트의 모든 panel 이름 리스트."""
    names = []
    for ws in project.get("definition", {}).get("workspaces", []) or []:
        for p in ws.get("panels", []) or []:
            names.append(p.get("name", "(unnamed)"))
    return names


def _extract_segment_ids(node) -> set[str]:
    """주어진 node(panel/sub-tree) 안에서 segment ID 패턴(s\\d+_<hex>) 모두 추출 (재귀 walk)."""
    found: set[str] = set()

    def walk(obj):
        if isinstance(obj, dict):
            # 흔한 위치: {"segmentId": "s..."} 또는 {"id": "s...", "type": "Segment"}
            for k, v in obj.items():
                if isinstance(v, str) and SEG_ID_RE.match(v):
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, str):
            if SEG_ID_RE.match(obj):
                found.add(obj)

    walk(node)
    return found


def _normalize_name(name: str) -> str:
    """NAME_NORMALIZATION_PATTERNS를 순서대로 적용해 logical name 도출."""
    n = (name or "").strip()
    for pat, repl in NAME_NORMALIZATION_PATTERNS:
        n = re.sub(pat, repl, n, flags=re.IGNORECASE)
    # 연속 공백 → 단일 공백 (replacement 후 정리)
    n = re.sub(r"\s+", " ", n)
    return n.strip().lower()


def _build_norm_map(seg_ids: set[str], names: dict[str, dict]) -> dict[str, list[tuple[str, str]]]:
    """normalized_name → [(id, original_name), ...]."""
    out: dict[str, list[tuple[str, str]]] = {}
    for sid in seg_ids:
        original = names.get(sid, {}).get("name", "")
        norm = _normalize_name(original)
        out.setdefault(norm, []).append((sid, original))
    return out


def _resolve_segment_names(headers: dict, gcid: str, seg_ids: set[str]) -> dict[str, dict]:
    """각 segment ID → {name, rsid, owner} 딕셔너리 매핑."""
    out: dict[str, dict] = {}
    for sid in sorted(seg_ids):
        url = f"https://analytics.adobe.io/api/{gcid}/segments/{sid}"
        try:
            r = requests.get(
                url,
                headers=headers,
                params={"expansion": "name,rsid,owner"},
                timeout=60,
            )
            if r.status_code == 200:
                d = r.json()
                out[sid] = {
                    "name": d.get("name", ""),
                    "rsid": d.get("rsid", ""),
                    "owner": d.get("owner", {}).get("id", ""),
                }
            else:
                out[sid] = {
                    "name": f"(GET 실패: {r.status_code})",
                    "rsid": "",
                    "owner": "",
                }
        except Exception as e:
            out[sid] = {"name": f"(error: {e})", "rsid": "", "owner": ""}
    return out


def _print_table(title: str, sids: list[str], names: dict[str, dict]) -> None:
    print(f"\n[{title}] {len(sids)}개")
    if not sids:
        print("  (없음)")
        return
    for sid in sorted(sids, key=lambda s: names.get(s, {}).get("name", "")):
        info = names.get(sid, {})
        print(f"  {sid}  {info.get('name', '')}  (rsid={info.get('rsid', '')})")


def _dump_csv(
    path: Path,
    *,
    same_id: list[str],
    logical_pairs: list[tuple[str, list[tuple[str, str]], list[tuple[str, str]]]],
    a_only: list[str],
    b_only: list[str],
    names: dict[str, dict],
    timestamp: str,
    requested_at: str,
) -> None:
    out = path.with_name(f"{path.stem}_{timestamp}{path.suffix}")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "RequestedAt",
                "Group",
                "Side",
                "NormalizedName",
                "SegmentId",
                "Name",
                "RSID",
                "OwnerId",
            ]
        )

        # Same ID (양쪽 다 있는 동일 segment)
        for sid in sorted(same_id, key=lambda s: names.get(s, {}).get("name", "")):
            info = names.get(sid, {})
            w.writerow(
                [
                    requested_at,
                    "Same ID",
                    "A&B",
                    _normalize_name(info.get("name", "")),
                    sid,
                    info.get("name", ""),
                    info.get("rsid", ""),
                    info.get("owner", ""),
                ]
            )

        # Logical match (다른 ID, 같은 normalized name)
        for norm, a_entries, b_entries in sorted(logical_pairs, key=lambda x: x[0]):
            for sid, _orig in a_entries:
                info = names.get(sid, {})
                w.writerow(
                    [
                        requested_at,
                        "Logical match",
                        "A",
                        norm,
                        sid,
                        info.get("name", ""),
                        info.get("rsid", ""),
                        info.get("owner", ""),
                    ]
                )
            for sid, _orig in b_entries:
                info = names.get(sid, {})
                w.writerow(
                    [
                        requested_at,
                        "Logical match",
                        "B",
                        norm,
                        sid,
                        info.get("name", ""),
                        info.get("rsid", ""),
                        info.get("owner", ""),
                    ]
                )

        # A only (B에도 없음 — 진짜 추가 후보)
        for sid in sorted(a_only, key=lambda s: names.get(s, {}).get("name", "")):
            info = names.get(sid, {})
            w.writerow(
                [
                    requested_at,
                    "A only",
                    "A",
                    _normalize_name(info.get("name", "")),
                    sid,
                    info.get("name", ""),
                    info.get("rsid", ""),
                    info.get("owner", ""),
                ]
            )

        # B only (A에도 없음 — 진짜 추가 후보)
        for sid in sorted(b_only, key=lambda s: names.get(s, {}).get("name", "")):
            info = names.get(sid, {})
            w.writerow(
                [
                    requested_at,
                    "B only",
                    "B",
                    _normalize_name(info.get("name", "")),
                    sid,
                    info.get("name", ""),
                    info.get("rsid", ""),
                    info.get("owner", ""),
                ]
            )
    print(f"\nCSV dump : {out}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="두 Workspace project의 특정 panel에 사용된 segment들을 비교"
    )
    parser.add_argument(
        "--no-csv",
        dest="no_csv",
        action="store_true",
        help="기본은 CSV 자동 저장. 이 플래그 주면 콘솔 출력만",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="panel JSON 구조 dump (panel 매칭 안 되거나 segment 추출 실패 시)",
    )
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    headers, gcid = _load_auth_headers()

    print(f"[{requested_at}] compare_panel_segments")
    print(f"  A: project={PROJECT_A_ID}  panel~='{PANEL_A_NAME}'")
    print(f"  B: project={PROJECT_B_ID}  panel~='{PANEL_B_NAME}'")
    print()

    # ── A 그룹 ───────────────────────────────────────────────
    print("Fetching project A ...")
    proj_a = _fetch_project(headers, gcid, PROJECT_A_ID)
    panels_a = _find_panels_by_name(proj_a, PANEL_A_NAME)
    print(f"  matched panels in A: {len(panels_a)}")
    if not panels_a:
        print(f"  (참고) A의 모든 panel 이름: {_all_panel_names(proj_a)}")
        return 1
    if args.debug:
        for i, p in enumerate(panels_a, 1):
            dump = OUTPUT_DIR / f"_debug_panel_a_{i}_{timestamp}.json"
            dump.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  debug dump: {dump}")
    seg_a: set[str] = set()
    for p in panels_a:
        seg_a |= _extract_segment_ids(p)
    print(f"  segments in A: {len(seg_a)}")

    # ── B 그룹 ───────────────────────────────────────────────
    print("\nFetching project B ...")
    proj_b = _fetch_project(headers, gcid, PROJECT_B_ID)
    panels_b = _find_panels_by_name(proj_b, PANEL_B_NAME)
    print(f"  matched panels in B: {len(panels_b)}")
    if not panels_b:
        print(f"  (참고) B의 모든 panel 이름: {_all_panel_names(proj_b)}")
        return 1
    if args.debug:
        for i, p in enumerate(panels_b, 1):
            dump = OUTPUT_DIR / f"_debug_panel_b_{i}_{timestamp}.json"
            dump.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  debug dump: {dump}")
    seg_b: set[str] = set()
    for p in panels_b:
        seg_b |= _extract_segment_ids(p)
    print(f"  segments in B: {len(seg_b)}")

    # ── ID 비교 ───────────────────────────────────────────────
    same_id = sorted(seg_a & seg_b)
    a_only_ids = seg_a - seg_b
    b_only_ids = seg_b - seg_a

    # ── 이름 해석 ─────────────────────────────────────────────
    print(f"\nResolving segment names ...")
    all_ids = seg_a | seg_b
    names = _resolve_segment_names(headers, gcid, all_ids)

    # ── 이름 정규화 + logical match ─────────────────────────────
    a_norm_map = _build_norm_map(seg_a, names)
    b_norm_map = _build_norm_map(seg_b, names)

    logical_pairs: list[tuple[str, list[tuple[str, str]], list[tuple[str, str]]]] = []
    common_norms = set(a_norm_map) & set(b_norm_map)
    same_id_set = set(same_id)
    for norm in common_norms:
        a_entries = a_norm_map[norm]
        b_entries = b_norm_map[norm]
        a_ids = {e[0] for e in a_entries}
        b_ids = {e[0] for e in b_entries}
        # ID가 완전히 일치하는 건 이미 same_id에 잡힘 → logical pair는
        # ID는 다른데 normalized name이 같은 경우만
        if a_ids != b_ids or (a_ids & b_ids) != a_ids:
            # A쪽 entries 중 same_id 아닌 것, B쪽도 same_id 아닌 것
            a_log = [e for e in a_entries if e[0] not in same_id_set]
            b_log = [e for e in b_entries if e[0] not in same_id_set]
            if a_log and b_log:
                logical_pairs.append((norm, a_log, b_log))

    # logical match에 들어간 ID는 a_only/b_only에서 제외
    logical_a_ids = {e[0] for _, ae, _ in logical_pairs for e in ae}
    logical_b_ids = {e[0] for _, _, be in logical_pairs for e in be}

    true_a_only = sorted(a_only_ids - logical_a_ids)
    true_b_only = sorted(b_only_ids - logical_b_ids)

    print(f"\n[비교 결과]")
    print(f"  A 전체           : {len(seg_a)}")
    print(f"  B 전체           : {len(seg_b)}")
    print(f"  Same ID (양쪽 다) : {len(same_id)}")
    print(f"  Logical match    : {len(logical_pairs)}  쌍 (normalized name 같음, ID 다름)")
    print(f"  A only           : {len(true_a_only)}  (B에 logical 짝도 없음 → 진짜 추가 후보)")
    print(f"  B only           : {len(true_b_only)}  (A에 logical 짝도 없음 → 진짜 추가 후보)")

    # ── 출력 ───────────────────────────────────────────────
    _print_table("Same ID  (양쪽 다 동일 segment)", same_id, names)

    print(f"\n[Logical match  (다른 ID, 같은 normalized name)] {len(logical_pairs)} 쌍")
    if not logical_pairs:
        print("  (없음)")
    else:
        for norm, a_entries, b_entries in sorted(logical_pairs, key=lambda x: x[0]):
            print(f"  → '{norm}'")
            for sid, orig in a_entries:
                rsid = names.get(sid, {}).get("rsid", "")
                print(f"     [A]  {sid}  {orig}  (rsid={rsid})")
            for sid, orig in b_entries:
                rsid = names.get(sid, {}).get("rsid", "")
                print(f"     [B]  {sid}  {orig}  (rsid={rsid})")

    _print_table("A only (B에 logical 짝도 없음 → 진짜 추가 후보)", true_a_only, names)
    _print_table("B only (A에 logical 짝도 없음 → 진짜 추가 후보)", true_b_only, names)

    if not args.no_csv:
        _dump_csv(
            (OUTPUT_DIR / CSV_OUTPUT_NAME).resolve(),
            same_id=same_id,
            logical_pairs=logical_pairs,
            a_only=true_a_only,
            b_only=true_b_only,
            names=names,
            timestamp=timestamp,
            requested_at=requested_at,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
