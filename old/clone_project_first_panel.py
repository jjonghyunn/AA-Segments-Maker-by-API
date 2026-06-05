# clone_project_first_panel.py
# 2026-05-12  Jonghyun Park w/ Claude
"""
Adobe Workspace project 의 첫 번째 panel 만 다른 (빈) target project 로 복제하면서
panel 안의 segment ID 들을 다른 키워드 패턴의 segment 로 자동 swap 하는 도구.

용도:
  · source 프로젝트(예: 2026 CAMPAIGN NAME, 69d4...) 의 panel[0] 구조를 그대로 복사하되,
    그 안에 박혀있는 "[CAMPAIGN NAME]" 계열 segment ID 들을 "[NEW CAMPAIGN NAME]" 계열 segment ID 들로 swap.
  · target 프로젝트(미리 UI 에서 본인 계정으로 만든 빈 프로젝트, 6a02...) 의 definition 을
    수정해서 PUT.

매칭 룰 (이름 정규화):
  · "[CAMPAIGN NAME] ALL SITES_Internal_GNB"  ─┐
    "[NEW CAMPAIGN NAME] Internal_GNB"             │ → 정규화 후 둘 다 "internal_gnb"
                                       ┘
  · NAME_NORMALIZATION_PATTERNS 의 regex 순서대로 적용해서 비교 키 도출.
  · 매칭 안 되는 segment 는 별도 표시 (CSV 의 MatchStatus 컬럼).

실행:
  python clone_project_first_panel.py                # dry-run (default)
  python clone_project_first_panel.py --apply        # 실제 PUT
  python clone_project_first_panel.py --debug        # source panel JSON dump

옵션:
  · RENAME_PANEL             — panel.name 안 OLD 캠페인 표식을 NEW 로 치환
  · COLLAPSE_ALL_SUBPANELS   — panel.subPanels[*].collapsed = True 강제 (모든 테이블 접힌 상태)

dry-run 결과:
  · 콘솔: 매핑 표 + 매칭 안된 segment 목록
  · CSV : clone_first_panel_mapping_{ts}.csv  (RequestedAt / SourceSegId / SourceSegName /
          NormalizedName / TargetSegId / TargetSegName / MatchStatus)
"""
from __future__ import annotations

import argparse
import copy
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
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
COMPANY_ID = "company_id"

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
# source = 복제 원본. Workspace URL 의 /workspace/edit/{이부분}
SOURCE_PROJECT_ID = "YOUR_PROJECT_ID"   # CAMPAIGN NAME 캠페인 프로젝트
# target = 미리 UI 에서 빈 프로젝트로 생성해둔 곳 (user1_login owner)
TARGET_PROJECT_ID = "YOUR_PROJECT_ID"   # NEW CAMPAIGN NAME 캠페인 프로젝트

# source 의 어느 panel 을 가져올지 (0-based index, 0 = 첫 번째)
SOURCE_PANEL_INDEX = 0

# ─── segment 검색 키워드 ───────────────────────────────────────────
# source panel 에 박혀있는 segment 들이 매칭될 OLD 키워드 (사실 사용 안 함 — 검증용)
OLD_KEYWORDS = ["[CAMPAIGN NAME]", "CAMPAIGN NAME"]
# target segment 들이 매칭될 NEW 키워드 (회사 전체 /segments paginate 후 클라 필터)
NEW_KEYWORDS = ["[NEW CAMPAIGN NAME]", "NEW CAMPAIGN NAME"]

# ─── 이름 정규화 패턴 (logical match 용) ─────────────────────────────
# segment ID 는 다르지만 "같은 논리적 컨셉" 인 경우 매칭하려고 이름을 정규화해서 비교.
# 예) "[CAMPAIGN NAME] ALL SITES_Internal_GNB"  ─┐
#     "[NEW CAMPAIGN NAME] Internal_GNB"             │ → 정규화 후 둘 다 "internal_gnb" → 매칭
#                                        ┘
# 각 항목은 (regex, replacement) 튜플. 순서대로 re.sub 적용됨 (대소문자 무시).
# ⚠️ 의미를 바꾸는 단어(예: "Order") 는 제거하지 말고 표준화만 할 것.
NAME_NORMALIZATION_PATTERNS = [
    (r"^\[\d{2}\s+[A-Z]+\]\s+ALL\s+SITES[_\s]+", ""),  # "[CAMPAIGN NAME] ALL SITES_X" → "X"
    (r"^\[\d{2}\s+[A-Z]+\]\s+ALL\s+SITES\s*", ""),     # "[CAMPAIGN NAME] ALL SITES X" → "X" (variant)
    (r"^\[\d{2}\s+[A-Z]+\]\s+",                ""),    # "[CAMPAIGN NAME] X" / "[NEW CAMPAIGN NAME] X" → "X"
    (r"\s+&\s+",                               " "),   # "X & Y" → "X Y"
]

# ─── 수동 매핑 오버라이드 ──────────────────────────────────────────
# 자동 normalize 로 매칭 안 되는 source → target segment id 직접 지정.
# 자동 매핑보다 우선 적용. dry-run 결과로 NO_MATCH / AMBIGUOUS 잡힌 것 중
# 이름이 살짝 달라 매칭 안 됐지만 의미상 같은 것을 여기 박아두면 됨.
MANUAL_OVERRIDES = {
    # [CAMPAIGN NAME] ALL SITES_Internal_Home GNB (Shop)  →  [NEW CAMPAIGN NAME] Internal_Home GNB
    "segment_id_placeholder": "segment_id_placeholder",
    # [CAMPAIGN NAME] ALL SITES_logged In (p10) - Visitor  →  [NEW CAMPAIGN NAME] Logged In Visitor
    "segment_id_placeholder": "segment_id_placeholder",
    # [CAMPAIGN NAME] ALL SITES_logged Out (p10) - Visitor →  [NEW CAMPAIGN NAME] Logged Out Visitor
    "segment_id_placeholder": "segment_id_placeholder",
}

# ─── 테이블(subPanel) 접힘 상태 강제 ───────────────────────────────
# True 면 apply 시 panel 안 모든 subPanel(테이블/visualization) 의 collapsed 를 True 로 강제.
# 신규 캠페인 프로젝트는 보통 모두 접힌 상태로 시작하는 게 보기 편함.
# False 면 source 의 collapsed 상태 그대로 복제.
COLLAPSE_ALL_SUBPANELS = True

# ─── Panel 이름 변환 패턴 (panel 헤더 텍스트) ────────────────────────
# True 면 panel.name 안의 source 캠페인 표식을 target 표식으로 치환.
# False 면 panel.name 손대지 않음.
RENAME_PANEL = True
PANEL_NAME_REPLACEMENTS = [
    (r"\[ALL\s+SITES\]\s*",   ""),                              # "[ALL SITES] " 제거
    (r"\[CAMPAIGN NAME\]",     "[NEW CAMPAIGN NAME]"),            # source 캠페인 표식 → new 표식
]

# ─── 출력 ──────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).resolve().parent
CSV_OUTPUT_TEMPLATE = "clone_first_panel_mapping_{ts}.csv"

# ─── 페이징 ────────────────────────────────────────────────────────
PAGE_LIMIT = 1000
MAX_PAGES = 200
INCLUDE_TYPE = "all"   # 본인+남이 만든 것 모두


# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

SEG_ID_RE = re.compile(r"^s\d+_[0-9a-f]+$")


def _auth() -> tuple[dict, str]:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h_lower = {k.lower(): v for k, v in dict(ags.header).items()}
    api_key = h_lower.get("x-api-key")
    auth = h_lower.get("authorization")
    gcid = h_lower.get("x-proxy-global-company-id")
    if not (api_key and auth and gcid):
        raise RuntimeError("필수 헤더 누락 — auth json / company id 확인")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def _fetch_project(headers: dict, gcid: str, pid: str) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{pid}"
    r = requests.get(
        url,
        headers=headers,
        params={"expansion": "definition,ownerFullName,modifiedDate,sharesFullName,tags,name"},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GET project {pid} failed: {r.status_code} {r.text[:400]}")
    return r.json()


def _put_project(headers: dict, gcid: str, pid: str, body: dict) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{pid}"
    r = requests.put(
        url,
        headers=headers,
        params={"expansion": "definition,ownerFullName,modifiedDate,name"},
        data=json.dumps(body),
        timeout=120,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"PUT project {pid} failed: {r.status_code} {r.text[:600]}")
    return r.json()


def _extract_segment_ids(node) -> set[str]:
    """JSON 트리 안의 모든 segment ID 패턴(s\\d+_<hex>) 수집."""
    found: set[str] = set()

    def walk(obj):
        if isinstance(obj, dict):
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


def _swap_segment_ids(node, mapping: dict[str, str]):
    """JSON 트리 안의 segment ID 들을 mapping 대로 in-place 치환."""

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, str) and SEG_ID_RE.match(v) and v in mapping:
                    obj[k] = mapping[v]
                else:
                    walk(v)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, str) and SEG_ID_RE.match(v) and v in mapping:
                    obj[i] = mapping[v]
                else:
                    walk(v)

    walk(node)


def _fetch_segment(headers: dict, gcid: str, sid: str) -> dict:
    """단건 segment GET — name, rsid, owner."""
    url = f"https://analytics.adobe.io/api/{gcid}/segments/{sid}"
    r = requests.get(
        url,
        headers=headers,
        params={"expansion": "name,rsid,owner"},
        timeout=60,
    )
    if r.status_code != 200:
        return {"id": sid, "name": f"(GET 실패: {r.status_code})", "rsid": "", "owner": {}}
    return r.json()


def _list_segments_by_keyword(headers: dict, gcid: str, keywords: list[str]) -> list[dict]:
    """server-side `name` 필터로 keyword 별 segment 만 fetch (회사 전체 page 안 함).

    AA API /segments 는 `name=...` 쿼리 파라미터로 substring 매칭 지원 (대소문자 무시).
    keyword 마다 별도로 호출 후 ID 기준 dedup.
    keywords 비어있으면 server filter 없이 전체 paginate (안 권장 — 219K).
    """
    seen: dict[str, dict] = {}

    if not keywords:
        keywords = [""]  # 빈 키워드 = 필터 없음

    for kw in keywords:
        print(f"  [server-side filter: name~'{kw}']")
        for page in range(MAX_PAGES):
            url = f"https://analytics.adobe.io/api/{gcid}/segments"
            params = {
                "limit": PAGE_LIMIT,
                "page": page,
                "expansion": "name,rsid,owner,modified,description,tags",
                "includeType": INCLUDE_TYPE,
            }
            if kw:
                params["name"] = kw
            r = requests.get(url, headers=headers, params=params, timeout=180)
            if r.status_code != 200:
                raise RuntimeError(f"GET /segments page {page} failed: {r.status_code} {r.text[:400]}")
            data = r.json()
            if isinstance(data, dict):
                items = data.get("content") or []
                is_last = data.get("lastPage", False)
                total = data.get("totalElements")
            else:
                items = data
                is_last = len(items) < PAGE_LIMIT
                total = None
            if not items:
                break
            for it in items:
                sid = it.get("id")
                if sid and sid not in seen:
                    seen[sid] = it
            if total is not None and page == 0:
                print(f"    totalElements: {total}")
            print(f"    page {page}: +{len(items)} (누적 unique {len(seen)})")
            if is_last or len(items) < PAGE_LIMIT:
                break

    # 클라 사이드 한 번 더 substring 검증 (대소문자 무시) — 안전망
    kw_lower = [k.lower() for k in keywords if k]
    if not kw_lower:
        return list(seen.values())
    matched = []
    for it in seen.values():
        n_lower = (it.get("name") or "").lower()
        if any(k in n_lower for k in kw_lower):
            matched.append(it)
    return matched


def _normalize_name(name: str) -> str:
    n = (name or "").strip()
    for pat, repl in NAME_NORMALIZATION_PATTERNS:
        n = re.sub(pat, repl, n, flags=re.IGNORECASE)
    n = re.sub(r"\s+", " ", n)
    return n.strip().lower()


def _rename_panel(name: str) -> str:
    out = name or ""
    for pat, repl in PANEL_NAME_REPLACEMENTS:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


def _collapse_all_subpanels(panel: dict) -> int:
    """panel.subPanels[*].collapsed = True 로 강제. 변경 건수 반환.
    panel 자체의 최상위 collapsed (panel 헤더 접힘) 은 건드리지 않음.
    """
    changed = 0
    for sp in panel.get("subPanels") or []:
        if isinstance(sp, dict) and sp.get("collapsed") is not True:
            sp["collapsed"] = True
            changed += 1
    return changed


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="source 프로젝트의 첫 panel 만 target 프로젝트로 복제 + segment swap")
    parser.add_argument("--apply", action="store_true", help="실제 PUT 실행 (기본은 dry-run)")
    parser.add_argument("--debug", action="store_true", help="panel JSON dump 등 디버그 출력")
    args = parser.parse_args()

    ts = datetime.now().strftime("%y%m%d_%H%M")
    requested_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] clone_project_first_panel.py  ({'APPLY' if args.apply else 'DRY-RUN'})")
    print(f"  AUTH      : {AUTH_JSON_PATH}")
    print(f"  COMPANY   : {COMPANY_ID}")
    print(f"  SOURCE    : {SOURCE_PROJECT_ID} (panel[{SOURCE_PANEL_INDEX}])")
    print(f"  TARGET    : {TARGET_PROJECT_ID}")
    print(f"  NEW_KEYS  : {NEW_KEYWORDS}")
    print()

    headers, gcid = _auth()

    # 1) source 프로젝트 GET
    print("[1] Fetching source project...")
    src = _fetch_project(headers, gcid, SOURCE_PROJECT_ID)
    src_def = src.get("definition") or {}
    src_workspaces = src_def.get("workspaces") or []
    if not src_workspaces:
        print("  ❌ source 에 workspaces 가 없습니다.")
        return 2
    src_panels = src_workspaces[0].get("panels") or []
    print(f"  source 이름     : {src.get('name', '?')}")
    print(f"  source panel 수 : {len(src_panels)}")
    for i, p in enumerate(src_panels):
        print(f"    [{i}] {p.get('name', '(unnamed)')}")
    if SOURCE_PANEL_INDEX >= len(src_panels):
        print(f"  ❌ SOURCE_PANEL_INDEX={SOURCE_PANEL_INDEX} 이 panel 수({len(src_panels)})를 초과합니다.")
        return 2

    src_panel = src_panels[SOURCE_PANEL_INDEX]
    print(f"\n  → 사용할 panel: [{SOURCE_PANEL_INDEX}] {src_panel.get('name', '(unnamed)')}")

    if args.debug:
        dbg_path = OUTPUT_DIR / f"_debug_src_panel_{ts}.json"
        dbg_path.write_text(json.dumps(src_panel, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [debug] source panel dump → {dbg_path.name}")

    # 2) source panel 안 segment ID 추출 + 이름 resolve
    print("\n[2] Extracting segment IDs from source panel...")
    src_seg_ids = _extract_segment_ids(src_panel)
    print(f"  panel 안 unique segment ID: {len(src_seg_ids)} 개")
    src_seg_info: dict[str, dict] = {}
    for sid in sorted(src_seg_ids):
        d = _fetch_segment(headers, gcid, sid)
        src_seg_info[sid] = {
            "name": d.get("name", ""),
            "rsid": d.get("rsid", ""),
            "owner": (d.get("owner") or {}).get("id", ""),
        }
    print("  source segments:")
    for sid in sorted(src_seg_ids, key=lambda s: src_seg_info[s]["name"]):
        info = src_seg_info[sid]
        print(f"    {sid}  {info['name']}")

    # 3) 회사 전체에서 NEW_KEYWORDS segment 들 fetch
    print(f"\n[3] Fetching all segments matching {NEW_KEYWORDS}...")
    new_segs = _list_segments_by_keyword(headers, gcid, NEW_KEYWORDS)
    print(f"  → 매칭된 [NEW CAMPAIGN NAME] 계열 segment: {len(new_segs)} 개")
    new_by_norm: dict[str, list[dict]] = {}
    for it in new_segs:
        norm = _normalize_name(it.get("name", ""))
        new_by_norm.setdefault(norm, []).append(it)

    # 4) 매핑 빌드 — MANUAL_OVERRIDES 우선, 그 다음 정규화 이름 자동 매칭
    print("\n[4] Building old → new mapping by normalized name...")
    if MANUAL_OVERRIDES:
        print(f"  (수동 오버라이드 {len(MANUAL_OVERRIDES)}건 우선 적용)")
    new_by_id: dict[str, dict] = {it["id"]: it for it in new_segs}
    mapping: dict[str, str] = {}   # source_seg_id → new_seg_id
    rows: list[dict] = []
    unmapped_src: list[str] = []
    ambiguous: list[tuple[str, list[dict]]] = []
    for sid in sorted(src_seg_ids, key=lambda s: src_seg_info[s]["name"]):
        info = src_seg_info[sid]
        norm = _normalize_name(info["name"])
        # 수동 오버라이드 우선
        if sid in MANUAL_OVERRIDES:
            new_id = MANUAL_OVERRIDES[sid]
            new_name = (new_by_id.get(new_id) or {}).get("name") or ""
            if not new_name:
                # 회사 segment 목록에 없으면 별도 GET 시도
                d = _fetch_segment(headers, gcid, new_id)
                new_name = d.get("name", "")
            mapping[sid] = new_id
            rows.append({
                "SourceSegId":   sid,
                "SourceSegName": info["name"],
                "NormalizedName": norm,
                "TargetSegId":   new_id,
                "TargetSegName": new_name,
                "MatchStatus":   "OK (manual)",
            })
            continue
        cand = new_by_norm.get(norm) or []
        if len(cand) == 1:
            new_id = cand[0]["id"]
            mapping[sid] = new_id
            rows.append({
                "SourceSegId":   sid,
                "SourceSegName": info["name"],
                "NormalizedName": norm,
                "TargetSegId":   new_id,
                "TargetSegName": cand[0].get("name", ""),
                "MatchStatus":   "OK",
            })
        elif len(cand) == 0:
            unmapped_src.append(sid)
            rows.append({
                "SourceSegId":   sid,
                "SourceSegName": info["name"],
                "NormalizedName": norm,
                "TargetSegId":   "",
                "TargetSegName": "",
                "MatchStatus":   "NO_MATCH",
            })
        else:
            ambiguous.append((sid, cand))
            rows.append({
                "SourceSegId":   sid,
                "SourceSegName": info["name"],
                "NormalizedName": norm,
                "TargetSegId":   "|".join(c["id"] for c in cand),
                "TargetSegName": "|".join(c.get("name", "") for c in cand),
                "MatchStatus":   f"AMBIGUOUS({len(cand)})",
            })

    # 매핑 안 된 NEW segment (참고 — target 에만 있고 source 에 안 쓰인 NEW CAMPAIGN NAME segment)
    used_new_ids = {v for v in mapping.values()}
    leftover_new = [it for it in new_segs if it["id"] not in used_new_ids]

    # 5) 콘솔 요약 + CSV 출력
    print(f"\n  매핑 결과:")
    print(f"    OK (auto)  : {sum(1 for r in rows if r['MatchStatus'] == 'OK')}")
    print(f"    OK (manual): {sum(1 for r in rows if r['MatchStatus'] == 'OK (manual)')}")
    print(f"    NO_MATCH   : {len(unmapped_src)}")
    print(f"    AMBIGUOUS  : {len(ambiguous)}")
    print(f"    leftover [NEW CAMPAIGN NAME] segments not used: {len(leftover_new)}")

    print("\n  ── 매핑 표 ─────────────────────────────────────────────────")
    for r in rows:
        flag = "✓" if r["MatchStatus"].startswith("OK") else "✗"
        src_name = r["SourceSegName"][:50]
        tgt_name = r["TargetSegName"][:50]
        print(f"    {flag} {r['SourceSegId']}  {src_name:<50}  →  {r['TargetSegId']:<40}  {tgt_name}")
        if not r["MatchStatus"].startswith("OK"):
            print(f"        status: {r['MatchStatus']}  normalized: '{r['NormalizedName']}'")

    if leftover_new:
        print("\n  ── target [NEW CAMPAIGN NAME] segments NOT used (참고) ─────────────────")
        for it in leftover_new[:30]:
            print(f"    · {it['id']}  {it.get('name', '')}")
        if len(leftover_new) > 30:
            print(f"    ... +{len(leftover_new) - 30}")

    # CSV
    csv_out = OUTPUT_DIR / CSV_OUTPUT_TEMPLATE.format(ts=ts)
    with open(csv_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[
            "RequestedAt", "SourceSegId", "SourceSegName", "NormalizedName",
            "TargetSegId", "TargetSegName", "MatchStatus",
        ])
        w.writeheader()
        for r in rows:
            w.writerow({"RequestedAt": requested_at, **r})
    print(f"\n  [CSV] {csv_out}")

    # 6) target 프로젝트 현재 상태
    print("\n[5] Fetching target project (state check)...")
    tgt = _fetch_project(headers, gcid, TARGET_PROJECT_ID)
    tgt_def = tgt.get("definition") or {}
    tgt_ws = tgt_def.get("workspaces") or []
    print(f"  target 이름     : {tgt.get('name', '?')}")
    print(f"  target owner    : {(tgt.get('owner') or {}).get('login', '?')} ({(tgt.get('owner') or {}).get('id', '?')})")
    print(f"  target workspace 수 : {len(tgt_ws)}")
    if tgt_ws:
        print(f"  target panels   : {[p.get('name', '?') for p in (tgt_ws[0].get('panels') or [])]}")

    if not args.apply:
        print("\n[dry-run] --apply 없이는 PUT 안 함. 매핑 OK 면 --apply 로 재실행.")
        if unmapped_src or ambiguous:
            print(f"  ⚠️  NO_MATCH {len(unmapped_src)}건, AMBIGUOUS {len(ambiguous)}건 — apply 전 해결 권장.")
        return 0

    # 7) APPLY — panel 복사 + segment swap + (옵션) panel name rename + target PUT
    if not mapping:
        print("\n❌ 매핑된 segment 가 0 개 — apply 중단.")
        return 3
    if unmapped_src or ambiguous:
        print(f"\n⚠️  NO_MATCH {len(unmapped_src)}건, AMBIGUOUS {len(ambiguous)}건 남아있음.")
        ans = input("그래도 진행하시려면 'yes' 입력: ").strip().lower()
        if ans != "yes":
            print("취소.")
            return 0

    print("\n[6] Building modified panel...")
    new_panel = copy.deepcopy(src_panel)
    _swap_segment_ids(new_panel, mapping)
    if RENAME_PANEL:
        old_name = new_panel.get("name", "")
        new_name = _rename_panel(old_name)
        if new_name and new_name != old_name:
            print(f"  panel 이름  : '{old_name}'  →  '{new_name}'")
            new_panel["name"] = new_name
    if COLLAPSE_ALL_SUBPANELS:
        n_collapsed = _collapse_all_subpanels(new_panel)
        total = len(new_panel.get("subPanels") or [])
        print(f"  subPanels   : {total} 개 중 {n_collapsed} 개 collapsed=True 로 변경 (나머지는 이미 접힘)")

    # workspaces 구조 (1 워크스페이스 1 패널) 로 덮어쓰기 — target 의 workspace 메타는 보존
    new_definition = copy.deepcopy(src_def)
    new_definition["workspaces"] = [copy.deepcopy(src_workspaces[0])]
    new_definition["workspaces"][0]["panels"] = [new_panel]

    new_target = copy.deepcopy(tgt)
    new_target["definition"] = new_definition
    # tags 는 POST/PUT 시 ignored 라 굳이 안 건드림. id/owner/name 등은 target 의 것 그대로 유지.

    if args.debug:
        dbg_path = OUTPUT_DIR / f"_debug_put_body_{ts}.json"
        dbg_path.write_text(json.dumps(new_target, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [debug] PUT body dump → {dbg_path.name}")

    print("\n[7] PUT to target project...")
    try:
        resp = _put_project(headers, gcid, TARGET_PROJECT_ID, new_target)
        print(f"  ✓ PUT 성공.")
        print(f"  target 새 이름  : {resp.get('name', '?')}")
        print(f"  workspaces      : {len(resp.get('definition', {}).get('workspaces', []) or [])}")
        print(f"  panels[0].name  : {(resp.get('definition', {}).get('workspaces', [{}])[0].get('panels', [{}])[0] or {}).get('name', '?')}")
        print(f"  UI 링크         : https://experience.adobe.com/@company_name/analytics/spa/#/workspace/edit/{TARGET_PROJECT_ID}")
    except Exception as e:
        print(f"  ❌ PUT 실패: {e}")
        return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
