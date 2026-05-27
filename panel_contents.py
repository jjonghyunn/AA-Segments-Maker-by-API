# panel_contents.py
# 2026-05-18  Jonghyun Park w/ Claude
"""
Adobe Workspace project 의 지정한 panel 들 (기본: 전체) 을 다른 (빈) target project 로
복제하면서 panel 안의 segment ID 들을 다른 키워드 패턴의 segment 로 자동 swap 하는 도구.

clone_project_first_panel.py 의 변형 버전.
주요 차이:
  · NEW_KEYWORDS = [CAMPAIGN NAME] (CAMPAIGN NAME 캠페인용)
  · 매칭 방식: 이름 정규화 대신 "CC_<번호>" / "US_CC_<번호>" 패턴 + 끝 suffix
    ((Visit) / (Delayed Purchase) / 없음) 를 분류 키(type, number, suffix) 로
    추출해서 매칭. 같은 번호 + 같은 suffix 변형끼리 짝지음.
  · normalize_name 매칭은 fallback 으로만 사용 (CC 패턴이 없는 segment 들).
  · [CAMPAIGN NAME] 에 없는 번호/변형(예: CC_07 없음, CC_03 (Delayed Purchase) 없음)은
    "No Data" segment 로 메꿔서 칼럼 수 유지.
    No Data segment 는 source/target 양쪽 프로젝트에 이미 박혀 있는 공용 placeholder.
  · "[CAMPAIGN NAME]" prefix 가 없는 system / 공용 segment (No Data, PC User, [part_name],
    [Global] 계열 등) 는 swap 없이 그대로 둠 (target 에서 같은 ID 그대로 참조).

용도:
  · source 프로젝트(YOUR_PROJECT_ID, [CAMPAIGN NAME] 계열) 의 panel[0] 구조를
    그대로 복사하되, 그 안에 박혀있는 "[CAMPAIGN NAME]" 계열 segment ID 들을
    "[CAMPAIGN NAME]" 계열 segment 중 같은 (type, 번호, suffix) 를 갖는 것으로 swap.
  · target 프로젝트(YOUR_PROJECT_ID, 미리 UI 에서 본인 계정으로
    만든 빈 프로젝트) 의 definition 을 수정해서 PUT.

매칭 룰:
  · 1차 키 (type, primary_num, suffix) — 이름 prefix `CC_##.` / `US_CC_##.` + 끝 suffix.
  · 2차 키 (type, sub_num, suffix)     — 이름 안의 ` - ##.` 패턴 (있는 경우만).
  · source 에 sub_num 있으면 → 2차 키 매칭. 실패 시 같은 (type, primary_num, suffix)
    의 SW 컨테이너 (sub_num 없는 것) 로 fallback. 그것도 없으면 No Data.
  · source 에 sub_num 없으면 → 1차 키로 매칭 (target 도 sub_num 없는 것끼리).
  · 매칭 안 되면 No Data fallback (칼럼 자리수 유지).
  · 2개 이상 매칭되면 (AMBIGUOUS) PREFERRED_OWNER_ID (예: user2_login) 가 만든 것
    1개로 좁히는 tie-breaker. 그래도 안 좁혀지면 AMBIGUOUS 표시.
  · CC / US_CC 패턴 없는 segment 는 _normalize_name 으로 fallback.
  · "[CAMPAIGN NAME]" prefix 없는 segment 는 swap 후보에서 제외 — keep as-is.
  · SKIP_KEYWORDS (예: "recomm") + sub_num 둘 다 있는 segment 는 자동 매칭 제외 → No Data
    (하위 breakdown 케이스가 많고 매칭 규칙이 복잡한 경우용. 컨테이너 segment 는 영향 X).
  · 우선순위: MANUAL_OVERRIDES > keep(no [CAMPAIGN NAME]) > skip(SKIP_KEYWORDS + sub_num)
              > sub_num > primary CC (sub_num 매칭 실패 시 fallback 포함) > No Data
              > normalize. 각 단계 AMBIGUOUS 시 owner_pref tie-breaker.

매칭 예시:
  · "[CAMPAIGN NAME] CC_01. Rewards Benefit"     ─ ("CC","01","")    ↔ "[CAMPAIGN NAME] CC_01. Rewards Benefit"
  · "[CAMPAIGN NAME] CC_01. ... (Visit)"          ─ ("CC","01","visit") ↔ SW 같은 (Visit) 변형
  · "[CAMPAIGN NAME] CC_03. ... - 01. Trip Recall"  ─ sub_num="01"
       ↔ "[CAMPAIGN NAME] CC_XX. ... - 01. ..."  (CC 번호 달라도 sub_num 같으면 매칭)
  · "[CAMPAIGN NAME] CC_08. Product Recommendation"  → sub_num 없음, recomm 포함 → 정상 매칭 시도
  · "[CAMPAIGN NAME] CC_08. Product Recommendation - 01. Foo"  → sub_num 있고 recomm 포함 → No Data

실행:
  python panel_contents.py                # dry-run (default)
  python panel_contents.py --apply        # 실제 PUT
  python panel_contents.py --debug        # source panel JSON dump
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
SOURCE_PROJECT_ID = "YOUR_PROJECT_ID"   # CAMPAIGN NAME 캠페인 프로젝트 (구조 참고용)
# target = 미리 UI 에서 빈 프로젝트로 생성해둔 곳 (user1_login owner)
# TARGET_PROJECT_ID = "YOUR_PROJECT_ID" # CAMPAIGN NAME (user_id)플젝
TARGET_PROJECT_ID = "YOUR_PROJECT_ID"   # team 공유용. CAMPAIGN NAME 캠페인 프로젝트

# source 의 어느 panel(들) 을 가져올지.
#   · "all"            → 모든 panel (기본)
#   · [0]              → 첫 panel 만
#   · [0, 1]           → 첫·두번째 panel
#   · [0, 2, 3]        → 특정 인덱스만
# 0-based. 결과는 지정한 순서대로 target 에 박힘.
SOURCE_PANEL_INDICES: "list[int] | str" = "all"

# ─── segment 검색 키워드 ───────────────────────────────────────────
# source panel 에 박혀있는 segment 들이 매칭될 OLD 키워드 (검증용)
OLD_KEYWORDS = ["[CAMPAIGN NAME]", "CAMPAIGN NAME"]
# target segment 들이 매칭될 NEW 키워드 (회사 전체 /segments paginate 후 클라 필터)
NEW_KEYWORDS = ["[CAMPAIGN NAME]", "CAMPAIGN NAME"]

# ─── target segment 이름 추가 필터 ─────────────────────────────────
# NEW_KEYWORDS 로 잡힌 SW 후보들 중에서 이 키워드 매칭하는 것만 매핑 후보로 사용.
# 빈 list 면 추가 필터 안 함 (기본).
#   · TARGET_SEG_NAME_KEYWORDS=["CC_03.", "Visit"], MODE="AND"  → 두 키워드 다 포함
#   · TARGET_SEG_NAME_KEYWORDS=["CC_03.", "CC_04."], MODE="OR"  → 한 개라도 포함
# 대소문자 무시 substring 매칭.
TARGET_SEG_NAME_KEYWORDS: list[str] = []
TARGET_SEG_NAME_MODE: str = "AND"   # "AND" 또는 "OR"

# ─── (type, number) 추출 패턴 ───────────────────────────────────────
# segment 이름에서 분류 키 (type, number) 를 뽑아내는 정규식.
# US_CC 패턴이 더 specific 하니까 먼저 평가 → 매칭되면 ("US_CC", "01") 리턴.
# 그 다음 CC_ 패턴 → ("CC", "01").
# 둘 다 매칭 안 되면 None 리턴 → fallback (normalize_name) 매칭.
#
# 매칭 형태: "CC_##.<내용>" / "US_CC_##.<내용>" (prefix 위치 단일 번호 + 마침표).
# 뒷부분 <내용> 안에 또 다른 "##." (예: "Top10.") 가 와도 _extract_cc_key 가
# pat.search 의 첫 매치만 쓰므로 prefix 번호만 잡힘 — 오인 매핑 위험 없음.
# 매칭 키는 raw 문자열 ("01") 로 보관: zero-pad 구분되므로 source/target 표기 통일 필요.
CC_TYPE_PATTERNS = [
    ("US_CC", re.compile(r"\bUS_CC_(\d+)\.", re.IGNORECASE)),
    ("CC",    re.compile(r"\bCC_(\d+)\.",    re.IGNORECASE)),
]

# ─── Suffix 패턴 (이름 끝 (Visit) / (Delayed Purchase) 식별) ──────────
# 같은 CC_## 라도 (Visit) / (Delayed Purchase) / 없음 3가지 변형으로 분리해서
# 매칭. SW 쪽에 같은 변형이 없으면 No Data fallback.
SUFFIX_PATTERNS = [
    ("visit",   re.compile(r"\(\s*Visit\s*\)\s*$",            re.IGNORECASE)),
    ("delayed", re.compile(r"\(\s*Delayed\s+Purchase\s*\)\s*$", re.IGNORECASE)),
]

# ─── Sub-number 패턴 (CC_## 내용 뒤의 " - ##." 식별) ─────────────────
# 예: "CC_03. Gift Curation by Scenario - 01. Trip Recall" → sub_num="01"
# 같은 sub_num 끼리 매칭 (primary CC 번호가 달라도 OK).
# 매칭 룰: source 에 sub_num 있으면 (type, sub_num, suffix) 로만 매칭.
# target 측에도 sub_num 있는 것끼리만 매칭됨 — primary 번호는 무시.
SUB_NUM_PATTERN = re.compile(r"\s-\s(\d+)\.", re.IGNORECASE)

# ─── Old (source) 캠페인 prefix 식별 키워드 ────────────────────────
# 이 키워드 중 하나라도 이름에 포함되어 있어야 swap 대상으로 본다.
# 없으면 system / 공용 segment 로 간주하고 target 에서도 같은 ID 그대로 둠
# (예: "No Data", "PC User (Visit)", "[part_name] Excluded EPP", "[Global] Excluded APP").
SWAP_REQUIRED_KEYWORDS = ["[CAMPAIGN NAME]", "CAMPAIGN NAME"]

# ─── Ambiguous tie-breaker (소유자 우선순위) ──────────────────────
# 같은 키 ((type, num/sub_num, suffix) 등) 에 SW segment 가 2개 이상 매칭될 때,
# 이 owner.id 가 만든 segment 1개를 우선 선택. 그래도 정확히 1개로 좁혀지지
# 않으면 기존대로 AMBIGUOUS 표시.
PREFERRED_OWNER_ID = "YOUR_LOGIN_ID"  # user2_login

# ─── 자동 매칭 제외 키워드 (sub_num 있는 segment 한정) ─────────────
# 이 단어가 이름에 포함되고 sub_num (' - ##.') 도 있는 segment 는 자동 매칭에서
# 제외하고 No Data fallback 으로 메꿈. 하위 breakdown 케이스가 많아 따로 매핑할
# segment 들 (예: "recomm" → Product Recommendation - 01. Foo 같은 sub 변형).
# sub_num 없는 컨테이너 segment (예: "CC_08. Product Recommendation") 는 영향 없이
# 정상 매칭. 추후 MANUAL_OVERRIDES 또는 별도 도구로 처리.
SKIP_KEYWORDS = ["recomm"]

# ─── 이름 정규화 패턴 (CC/US_CC 패턴 없는 segment 용 fallback) ─────────
# segment ID 는 다르지만 "같은 논리적 컨셉" 인 경우 매칭하려고 이름을 정규화해서 비교.
NAME_NORMALIZATION_PATTERNS = [
    (r"^\[\d{2}\s+[A-Z]+\]\s+ALL\s+SITES[_\s]+", ""),  # "[CAMPAIGN NAME] ALL SITES_X" → "X"
    (r"^\[\d{2}\s+[A-Z]+\]\s+ALL\s+SITES\s*", ""),     # "[CAMPAIGN NAME] ALL SITES X" → "X"
    (r"^\[\d{2}\s+[A-Z]+\]\s+",                ""),    # "[CAMPAIGN NAME] X" / "[CAMPAIGN NAME] X" → "X"
    (r"\s+&\s+",                               " "),   # "X & Y" → "X Y"
]

# ─── 수동 매핑 오버라이드 ──────────────────────────────────────────
# 자동 (type,num) / normalize 매칭으로도 잡히지 않거나 의도적으로 다른 segment 에
# 연결하고 싶을 때 직접 박아둠. 자동 매칭보다 우선.
# dry-run 결과 NO_MATCH / AMBIGUOUS 잡힌 것 보고 추가.
MANUAL_OVERRIDES: dict[str, str] = {
    # "s200001591_xxxxxxxxxxxxxxxxxxxxxxxx": "s200001591_yyyyyyyyyyyyyyyyyyyyyyyy",
}

# ─── No Data fallback ───────────────────────────────────────────────
# [CAMPAIGN NAME] 에 CC_N / US_CC_N 매칭이 없는 번호를 만났을 때 칼럼 수 유지용 placeholder.
# source panel 의 referenced segments 중 이름에 NO_DATA_NAME_PATTERN 매칭되는 것을
# 매 실행마다 자동 탐지해서 그 segment ID 를 fallback 으로 사용.
# (No Data segment 는 source / target 양쪽 프로젝트에 이미 박혀있는 공용 placeholder
#  라는 가정 — 따로 swap mapping 안 들어감, 같은 ID 그대로 유지.)
# 다음 실행 시 [CAMPAIGN NAME] CC_N 이 새로 생겼다면 자동으로 정상 매핑으로 복귀 — idempotent.
NO_DATA_NAME_PATTERN = re.compile(r"\bNo\s*Data\b", re.IGNORECASE)
USE_NO_DATA_FALLBACK = True

# ─── 끝쪽 sub_num 미매칭 시 column 제거 ────────────────────────────
# True 면: source 의 sub_num 이 SW (type, primary_num) 의 max sub_num 보다 클 때
# (즉 SW 끝쪽에 없는 경우) → mapping 안 만들고 panel JSON 에서 그 source ID 를
# 참조하는 list/segment object entry 를 통째로 제거 (한 칸 당기는 효과).
# False 면: 기존 동작 (sub→cc_key fallback 또는 No Data).
# 시작/중간 빔 (예: SW 가 02,03,04 만 있고 MD 가 01) 은 영향 X — No Data 유지.
TRIM_TAIL_UNMATCHED_SUB_NUM = True

# ─── SW 기준 순서 매핑 (sub_num 매칭 무시) ──────────────────────────
# True 면: sub_num / primary CC 정확 매칭 무시. 같은 type (CC vs US_CC) 안에서
# SW segment list 와 MD source list 를 (primary_num, sub_num, suffix) 정렬 후
# 위치 기반 zip 매핑. SW 가 정의하는 column 순서대로 MD panel column 자리에 채움.
#   · MD 가 더 많은 type → 끝 source 들 tail 처리 (panel 에서 제거 시도).
#   · SW 가 더 많은 type → 초과 SW 는 panel 에 안 들어감 (panel column 동적 추가
#     미구현 — panel JSON dump 받은 후 확장 예정).
# 우선순위: MANUAL > keep > skip > [SW_ORDER 매핑] > normalize fallback (CC 패턴 없는
# segment 용). sub_num/primary CC 정확 매칭 단계는 SW_ORDER_MAPPING=True 시 스킵.
SW_ORDER_MAPPING = False

# ─── Shift Tail to Next Primary (cursor 기반 cascade) ───────────────
# True 면: (type, suffix) 그룹 안에서 SW cursor 진행하면서 매핑.
#   · MD primary 가 SW 에 있으면 → SW cursor 의 현재 segment 와 매핑
#   · MD primary 가 SW 에 없으면 → No Data, SW cursor 유지 (CC_01 케이스)
#   · sub_num breakdown 의 tail (예: SW 에 CC_03-04 없음) → SW cursor 의 다음
#     segment 로 매핑 → cascade shift (MD CC_04 → SW CC_05 식)
#   · SW cursor 가 끝나면 No Data
# sub_num/primary CC 매칭 단계는 SHIFT_TAIL_TO_NEXT_PRIMARY=True 시 스킵.
# SW_ORDER_MAPPING 과 동시 활성 비추천 — SHIFT 가 우선.
SHIFT_TAIL_TO_NEXT_PRIMARY = False

# ─── 테이블(subPanel) 접힘 상태 강제 ───────────────────────────────
COLLAPSE_ALL_SUBPANELS = True

# ─── Panel 이름 변환 패턴 (panel 헤더 텍스트) ────────────────────────
RENAME_PANEL = True
PANEL_NAME_REPLACEMENTS = [
    (r"\[ALL\s+SITES\]\s*",         ""),                  # "[ALL SITES] " 제거
    (r"\[26\s+MD\]",                "[CAMPAIGN NAME]"),           # "[CAMPAIGN NAME]" → "[CAMPAIGN NAME]"
    (r"26\s+campaign_name'?s\s+Day",       "26 CAMPAIGN NAME"),   # campaign 표식
    (r"campaign_name'?s\s+Day",            "CAMPAIGN NAME"),
    (r"\bMD\b",                     "SW"),
]

# ─── 출력 ──────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).resolve().parent
CSV_OUTPUT_TEMPLATE = "panel_contents_mapping_{ts}.csv"

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


SEG_ID_PATTERN = re.compile(r"s\d+_[0-9a-f]+")   # search 용 (anchor 없음)


def _swap_segment_ids(node, mapping: dict[str, str]):
    """JSON 트리 안의 segment ID 들을 mapping 대로 in-place 치환.
    잡는 위치:
      · dict key (예: {"s200001591_xxx": {...}}) — key 자체 치환
      · dict value 의 string (segment ID 단독 또는 substring 포함)
      · list element 의 string
      · 더 깊은 nested 위치도 재귀 처리
    string 안의 모든 segment ID 발생을 mapping 대로 일괄 치환 (substring 매칭)."""

    def repl(s: str) -> str:
        return SEG_ID_PATTERN.sub(lambda m: mapping.get(m.group(0), m.group(0)), s)

    def walk(obj):
        if isinstance(obj, dict):
            # dict key 치환 (segment ID 가 key 위치에 박힌 경우)
            for k in list(obj.keys()):
                if isinstance(k, str):
                    new_k = repl(k)
                    if new_k != k:
                        obj[new_k] = obj.pop(k)
            # value 치환 + 재귀
            for k, v in list(obj.items()):
                if isinstance(v, str):
                    new_v = repl(v)
                    if new_v != v:
                        obj[k] = new_v
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, str):
                    new_v = repl(v)
                    if new_v != v:
                        obj[i] = new_v
                elif isinstance(v, (dict, list)):
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
    """server-side `name` 필터로 keyword 별 segment 만 fetch (회사 전체 page 안 함)."""
    seen: dict[str, dict] = {}

    if not keywords:
        keywords = [""]

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

    kw_lower = [k.lower() for k in keywords if k]
    if not kw_lower:
        return list(seen.values())
    matched = []
    for it in seen.values():
        n_lower = (it.get("name") or "").lower()
        if any(k in n_lower for k in kw_lower):
            matched.append(it)
    return matched


def _extract_suffix(name: str) -> str:
    """이름 끝에서 (Visit) / (Delayed Purchase) 식별. 매칭 안 되면 ''."""
    n = (name or "").strip()
    for label, pat in SUFFIX_PATTERNS:
        if pat.search(n):
            return label
    return ""


def _extract_cc_key(name: str) -> tuple[str, str, str] | None:
    """이름에서 (type, number_raw, suffix) 키 추출. US_CC 먼저, 그 다음 CC.
    suffix 는 'visit' / 'delayed' / '' 중 하나.
    type/number 매칭 안 되면 None.
    number_raw 는 zero-pad 그대로 ("01" vs "1" 구분) — source/target 표기 통일 가정."""
    n = name or ""
    for type_label, pat in CC_TYPE_PATTERNS:
        m = pat.search(n)
        if m:
            return (type_label, m.group(1), _extract_suffix(n))
    return None


def _extract_sub_num(name: str) -> str | None:
    """이름 안의 ' - ##.' 패턴에서 sub_num 추출. 첫 매치 사용 (zero-pad 그대로).
    매칭 안 되면 None.
    예: 'CC_03. Gift Curation - 01. Trip Recall' → '01'"""
    m = SUB_NUM_PATTERN.search(name or "")
    return m.group(1) if m else None


def _cc_sort_key(cc_key: tuple[str, str, str] | None, sub_num: str | None = None) -> tuple:
    """natural number 정렬용 sort key 생성.
    (type, primary_num, sub_num, suffix_order, raw_num) — 같은 번호 내에서 sub_num 오름차순,
    같은 sub_num 내에서 no→visit→delayed 순."""
    suffix_order = {"": 0, "visit": 1, "delayed": 2}
    if cc_key is None:
        return ("ZZ", 99999, 99999, 9, "")
    type_label, num_str, suffix = cc_key
    sfx = suffix_order.get(suffix, 9)
    try:
        primary_int = int(num_str)
    except ValueError:
        primary_int = 99999
    try:
        sub_int = int(sub_num) if sub_num is not None else -1
    except ValueError:
        sub_int = 99999
    return (type_label, primary_int, sub_int, sfx, num_str)


def _format_match_key(cc_key: tuple[str, str, str] | None, sub_num: str | None = None) -> str:
    """CSV / 콘솔 표시용 — 'CC_01', 'CC_01 - 02', 'CC_01 - 02 (visit)' 형태."""
    if cc_key is None:
        return ""
    type_label, num_str, suffix = cc_key
    base = f"{type_label}_{num_str}"
    if sub_num is not None:
        base = f"{base} - {sub_num}"
    return f"{base} ({suffix})" if suffix else base


def _has_swap_keyword(name: str) -> bool:
    """source 이름에 SWAP_REQUIRED_KEYWORDS 중 하나라도 포함되는지."""
    n = (name or "").lower()
    return any(k.lower() in n for k in SWAP_REQUIRED_KEYWORDS)


def _has_skip_keyword(name: str) -> bool:
    """source 이름에 SKIP_KEYWORDS 중 하나라도 포함되는지 (자동 매칭 제외 표시)."""
    n = (name or "").lower()
    return any(k.lower() in n for k in SKIP_KEYWORDS)


def _filter_by_name_keywords(items: list[dict], keywords: list[str], mode: str) -> list[dict]:
    """이름 기반 추가 필터 — TARGET_SEG_NAME_KEYWORDS 적용용.
    keywords 빈 list 면 그대로 통과. 대소문자 무시 substring 매칭.
    mode='AND' → 모든 키워드 포함, mode='OR' → 한 개라도 포함."""
    if not keywords:
        return items
    kw_lower = [k.lower() for k in keywords if k]
    if not kw_lower:
        return items
    mode_up = (mode or "").strip().upper()
    out = []
    for it in items:
        n_lower = (it.get("name") or "").lower()
        if mode_up == "AND":
            if all(k in n_lower for k in kw_lower):
                out.append(it)
        else:  # OR
            if any(k in n_lower for k in kw_lower):
                out.append(it)
    return out


def _pick_by_owner(cand: list[dict], owner_id: str) -> dict | None:
    """AMBIGUOUS tie-breaker — 2개 이상 후보를 1개로 좁히기.
    적용 순서:
      1단계 (prefix 필터): 이름이 NEW_KEYWORDS 의 '[' 로 시작하는 prefix 로 정확히
            시작하는 것만 남김 (예: "UK_추가_[CAMPAIGN NAME] ..." 같은 wrap prefix segment 거름).
            이 단계에서 1개로 좁혀지면 그걸로 채택 (owner 무관).
      2단계 (owner 매칭): 1단계 후에도 여러 개면 owner.id 가 owner_id 와 일치하는
            것 1개로 좁힘.
    그래도 1개로 안 좁혀지면 None (호출자가 AMBIGUOUS 처리)."""
    # 1단계: prefix 필터 — UK_추가_ 같은 wrap prefix 거름 (owner 무관)
    bracketed_prefixes = [k for k in NEW_KEYWORDS if k.startswith("[")]
    filtered = list(cand)
    if bracketed_prefixes:
        filtered = [c for c in filtered
                    if any((c.get("name") or "").lstrip().startswith(kw) for kw in bracketed_prefixes)]
    if len(filtered) == 1:
        return filtered[0]
    if len(filtered) == 0:
        return None
    # 2단계: owner.id 매칭
    matched = [c for c in filtered if str(((c.get("owner") or {}).get("id") or "")) == str(owner_id)]
    return matched[0] if len(matched) == 1 else None


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


def _remove_source_ids_from_panel(panel: dict, ids_to_remove: set[str]) -> tuple[int, list[str]]:
    """panel JSON 트리에서 ids_to_remove 의 segment ID 를 참조하는 list element 들을
    list 에서 제거 (in-place). 적용 규칙:
      · list 의 element 가 string 이고 ids_to_remove 에 있으면 → list 에서 제거
      · list 의 element 가 dict 이고 그 dict 의 'id' / 'componentId' / 'segmentId'
        value 가 ids_to_remove 에 있으면 → dict 통째로 list 에서 제거
      · dict value 위치 ({"segmentId": "..."}) 는 안 건드림 — 구조 보존
    Returns: (n_removed, sample_paths) — 제거된 entry 개수 + 위치 sample 디버그 용도."""
    n_removed = 0
    samples: list[str] = []

    def walk(node, path: str):
        nonlocal n_removed
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, (dict, list)):
                    walk(item, f"{path}[{i}]")
            kept = []
            for i, item in enumerate(node):
                if isinstance(item, str) and item in ids_to_remove:
                    n_removed += 1
                    if len(samples) < 20:
                        samples.append(f"{path}[{i}] str={item}")
                    continue
                if isinstance(item, dict):
                    sid = item.get("id") or item.get("componentId") or item.get("segmentId")
                    if isinstance(sid, str) and sid in ids_to_remove:
                        n_removed += 1
                        if len(samples) < 20:
                            samples.append(f"{path}[{i}] dict.id={sid}")
                        continue
                kept.append(item)
            node[:] = kept

    walk(panel, "panel")
    return n_removed, samples


ISO_INTERVAL_RE_BUILD = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T[\d:.+\-Z]*)?/\d{4}-\d{2}-\d{2}(?:T[\d:.+\-Z]*)?$"
)


def _collect_date_intervals(node) -> list[str]:
    """panel 트리에서 ISO interval format '<ISO>/<ISO>' 값들을 path 순으로 모음.
    같은 path 의 같은 값을 target/source 비교에 쓰는 게 아니라, 단순 occurrence list 로
    수집해서 첫 N 개를 target 의 첫 N 개로 복원하는 방식. panel 구조가 동일하니까
    occurrence 순서가 동일하다는 가정."""
    out: list[str] = []

    def walk(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, str) and ISO_INTERVAL_RE_BUILD.match(v):
                    out.append(v)
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(obj, list):
            for v in obj:
                if isinstance(v, str) and ISO_INTERVAL_RE_BUILD.match(v):
                    out.append(v)
                elif isinstance(v, (dict, list)):
                    walk(v)

    walk(node)
    return out


def _restore_date_intervals(node, values: list[str]) -> int:
    """panel 트리의 ISO interval format 값들을 values list 순으로 복원 (in-place).
    occurrence 순서대로 매칭. values 길이보다 발견 위치가 많으면 초과분은 그대로 둠.
    Returns: 복원된 개수."""
    n_restored = 0
    idx = [0]   # closure 카운터

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, str) and ISO_INTERVAL_RE_BUILD.match(v):
                    if idx[0] < len(values):
                        if obj[k] != values[idx[0]]:
                            obj[k] = values[idx[0]]
                        idx[0] += 1
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, str) and ISO_INTERVAL_RE_BUILD.match(v):
                    if idx[0] < len(values):
                        if obj[i] != values[idx[0]]:
                            obj[i] = values[idx[0]]
                        idx[0] += 1
                elif isinstance(v, (dict, list)):
                    walk(v)

    walk(node)
    return idx[0]


def _collapse_all_subpanels(panel: dict) -> int:
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

    parser = argparse.ArgumentParser(description="source 프로젝트의 지정 panel 들을 target 프로젝트로 복제 + segment swap (CC/US_CC + suffix 매칭)")
    parser.add_argument("--apply", action="store_true", help="실제 PUT 실행 (기본은 dry-run)")
    parser.add_argument("--debug", action="store_true", help="panel JSON dump 등 디버그 출력")
    args = parser.parse_args()

    ts = datetime.now().strftime("%y%m%d_%H%M")
    requested_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] panel_contents.py  ({'APPLY' if args.apply else 'DRY-RUN'})")
    print(f"  AUTH      : {AUTH_JSON_PATH}")
    print(f"  COMPANY   : {COMPANY_ID}")
    print(f"  SOURCE    : {SOURCE_PROJECT_ID} (panels: {SOURCE_PANEL_INDICES})")
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

    # SOURCE_PANEL_INDICES 해석
    if isinstance(SOURCE_PANEL_INDICES, str) and SOURCE_PANEL_INDICES.lower() == "all":
        panel_indices = list(range(len(src_panels)))
    elif isinstance(SOURCE_PANEL_INDICES, (list, tuple)):
        panel_indices = list(SOURCE_PANEL_INDICES)
    else:
        print(f"  ❌ SOURCE_PANEL_INDICES 형식 오류: {SOURCE_PANEL_INDICES!r}  ('all' 또는 list[int])")
        return 2

    bad = [i for i in panel_indices if not (0 <= i < len(src_panels))]
    if bad:
        print(f"  ❌ SOURCE_PANEL_INDICES 에 범위 초과 인덱스: {bad} (panel 수: {len(src_panels)})")
        return 2
    if not panel_indices:
        print(f"  ❌ SOURCE_PANEL_INDICES 가 비어있습니다.")
        return 2

    selected_panels = [src_panels[i] for i in panel_indices]
    print(f"\n  → 사용할 panels ({len(selected_panels)} 개):")
    for i, p in zip(panel_indices, selected_panels):
        print(f"      [{i}] {p.get('name', '(unnamed)')}")

    if args.debug:
        dbg_path = OUTPUT_DIR / f"_debug_src_panels_{ts}.json"
        dbg_path.write_text(json.dumps(selected_panels, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [debug] selected source panels dump → {dbg_path.name}")

    # 2) source panel(들) 안 segment ID 추출 + 이름 resolve
    print(f"\n[2] Extracting segment IDs from {len(selected_panels)} source panel(s)...")
    src_seg_ids: set[str] = set()
    for p in selected_panels:
        src_seg_ids |= _extract_segment_ids(p)
    print(f"  panel 들 안 unique segment ID: {len(src_seg_ids)} 개")
    src_seg_info: dict[str, dict] = {}
    for sid in sorted(src_seg_ids):
        d = _fetch_segment(headers, gcid, sid)
        src_seg_info[sid] = {
            "name": d.get("name", ""),
            "rsid": d.get("rsid", ""),
            "owner": (d.get("owner") or {}).get("id", ""),
        }
    print("  source segments (CC/US_CC + sub_num + suffix 키 표시):")
    for sid in sorted(src_seg_ids, key=lambda s: (
        _cc_sort_key(_extract_cc_key(src_seg_info[s]["name"]), _extract_sub_num(src_seg_info[s]["name"])),
        src_seg_info[s]["name"],
    )):
        info = src_seg_info[sid]
        key = _extract_cc_key(info["name"])
        sub = _extract_sub_num(info["name"])
        key_str = _format_match_key(key, sub) or "—"
        print(f"    [{key_str:<22}] {sid}  {info['name']}")

    # No Data fallback segment 자동 탐지 (source panel referenced segments 중에서)
    # USE_NO_DATA_FALLBACK=True 면 CC_N 매칭 0건일 때 칼럼 자리 메꿈용으로 사용.
    no_data_sid = None
    no_data_name = ""
    if USE_NO_DATA_FALLBACK:
        for sid, info in src_seg_info.items():
            if NO_DATA_NAME_PATTERN.search(info.get("name", "")):
                no_data_sid = sid
                no_data_name = info["name"]
                break
        if no_data_sid:
            print(f"\n  [No Data fallback] {no_data_sid}  {no_data_name}")
        else:
            print(f"\n  ⚠️  USE_NO_DATA_FALLBACK=True 인데 source panel referenced segments 에서 'No Data' 못 찾음 — fallback 비활성화됨.")

    # 3) 회사 전체에서 NEW_KEYWORDS segment 들 fetch
    print(f"\n[3] Fetching all segments matching {NEW_KEYWORDS}...")
    new_segs = _list_segments_by_keyword(headers, gcid, NEW_KEYWORDS)
    print(f"  → 매칭된 {NEW_KEYWORDS[0]} 계열 segment: {len(new_segs)} 개")
    if TARGET_SEG_NAME_KEYWORDS:
        before = len(new_segs)
        new_segs = _filter_by_name_keywords(new_segs, TARGET_SEG_NAME_KEYWORDS, TARGET_SEG_NAME_MODE)
        print(f"  추가 필터 ({TARGET_SEG_NAME_MODE} {TARGET_SEG_NAME_KEYWORDS}): {before} → {len(new_segs)} 개")

    # CC/US_CC 키별 인덱스 분리:
    #   · new_by_sub_key: sub_num 있는 target — (type, sub_num, suffix)
    #   · new_by_cc_key : sub_num 없는 target — (type, primary_num, suffix)
    # + normalize 인덱스 (fallback)
    # + sw_max_sub_by_primary: (type, primary_num) → max int(sub_num) — TRIM 판정용
    new_by_cc_key: dict[tuple[str, str, str], list[dict]] = {}
    new_by_sub_key: dict[tuple[str, str, str], list[dict]] = {}
    new_by_norm: dict[str, list[dict]] = {}
    sw_max_sub_by_primary: dict[tuple[str, str], int] = {}
    for it in new_segs:
        name = it.get("name", "")
        key = _extract_cc_key(name)
        sub = _extract_sub_num(name)
        if key:
            if sub is not None:
                new_by_sub_key.setdefault((key[0], sub, key[2]), []).append(it)
                try:
                    sub_int = int(sub)
                    primary_key = (key[0], key[1])
                    prev = sw_max_sub_by_primary.get(primary_key)
                    if prev is None or sub_int > prev:
                        sw_max_sub_by_primary[primary_key] = sub_int
                except ValueError:
                    pass
            else:
                new_by_cc_key.setdefault(key, []).append(it)
        norm = _normalize_name(name)
        new_by_norm.setdefault(norm, []).append(it)

    # 4) 매핑 빌드
    print("\n[4] Building old → new mapping...")
    print("    우선순위: MANUAL_OVERRIDES > keep(no [CAMPAIGN NAME]) > skip(SKIP_KEYWORDS + sub_num→no_data)")
    print("              > sub_num((type,sub_num,suffix)) > tail trim (끝쪽 sub_num) > primary CC")
    print("              > No Data fallback > normalize_name")
    if TRIM_TAIL_UNMATCHED_SUB_NUM:
        print(f"  (TRIM_TAIL_UNMATCHED_SUB_NUM=True — SW max sub_num 초과 source 는 panel 에서 제거)")
    if MANUAL_OVERRIDES:
        print(f"  (수동 오버라이드 {len(MANUAL_OVERRIDES)}건 우선 적용)")
    if SKIP_KEYWORDS:
        print(f"  (자동 매칭 제외 키워드: {SKIP_KEYWORDS} — sub_num 있는 segment 한정 → No Data)")
    if PREFERRED_OWNER_ID:
        print(f"  (AMBIGUOUS tie-breaker: owner.id={PREFERRED_OWNER_ID} (user2_login) 우선)")
    new_by_id: dict[str, dict] = {it["id"]: it for it in new_segs}
    mapping: dict[str, str] = {}
    rows: list[dict] = []
    unmapped_src: list[str] = []
    ambiguous: list[tuple[str, list[dict]]] = []
    tail_source_ids: set[str] = set()   # TRIM_TAIL_UNMATCHED_SUB_NUM / SW_ORDER 대상 (panel 에서 제거)

    # SW_ORDER_MAPPING 모드 — 같은 type (CC vs US_CC) 안에서 SW list 와 MD source list 를
    # (primary_num int, sub_num int, suffix_order) 정렬 후 위치 기반 zip.
    sw_order_mapping: dict[str, dict] = {}   # sid → matched SW item dict
    sw_order_tail: set[str] = set()          # MD 가 더 많은 경우 끝 source 들 (panel 에서 제거 대상)
    sw_order_excess: list[dict] = []         # SW 가 더 많은 경우 안 들어간 SW 들 (콘솔 디버그용)
    if SW_ORDER_MAPPING:
        def _sort_seg(name: str):
            k = _extract_cc_key(name)
            s = _extract_sub_num(name)
            return _cc_sort_key(k, s)

        md_by_type: dict[str, list[str]] = {}
        sw_by_type: dict[str, list[dict]] = {}
        for sid in src_seg_ids:
            nm = src_seg_info[sid]["name"]
            if not _has_swap_keyword(nm):
                continue
            if SKIP_KEYWORDS and _extract_sub_num(nm) is not None and _has_skip_keyword(nm):
                continue
            k = _extract_cc_key(nm)
            if not k:
                continue
            md_by_type.setdefault(k[0], []).append(sid)
        for it in new_segs:
            k = _extract_cc_key(it.get("name", ""))
            if not k:
                continue
            sw_by_type.setdefault(k[0], []).append(it)
        for t in md_by_type:
            md_by_type[t].sort(key=lambda s: (_sort_seg(src_seg_info[s]["name"]), src_seg_info[s]["name"]))
        for t in sw_by_type:
            sw_by_type[t].sort(key=lambda it: (_sort_seg(it.get("name", "")), it.get("name", "")))
        for t, md_list in md_by_type.items():
            sw_list = sw_by_type.get(t, [])
            for i, sid in enumerate(md_list):
                if i < len(sw_list):
                    sw_order_mapping[sid] = sw_list[i]
                else:
                    sw_order_tail.add(sid)
            if len(sw_list) > len(md_list):
                sw_order_excess.extend(sw_list[len(md_list):])
        print(f"  [SW_ORDER_MAPPING] type 별 zip 결과:")
        for t in sorted(set(list(md_by_type.keys()) + list(sw_by_type.keys()))):
            n_md = len(md_by_type.get(t, []))
            n_sw = len(sw_by_type.get(t, []))
            print(f"    {t:<7}: MD={n_md}  SW={n_sw}  → 매핑 {min(n_md, n_sw)} / tail {max(0, n_md - n_sw)} / excess {max(0, n_sw - n_md)}")
        if sw_order_excess:
            print(f"  ⚠️ SW 초과 {len(sw_order_excess)} 개 — panel column 동적 추가 미구현. UI 에서 수동 추가 필요.")
            for it in sw_order_excess[:10]:
                print(f"      · {it.get('id')}  {it.get('name', '')}")

    # SHIFT_TAIL_TO_NEXT_PRIMARY 모드 — cursor 기반 cascade shift.
    # (type, suffix) 그룹 별로 MD source 와 SW segment 를 (primary, sub_num) 정렬 후
    # SW pointer 진행하면서 매핑. MD primary 가 SW 에 없으면 No Data + cursor 유지.
    shift_mapping: dict[str, dict] = {}     # sid → matched SW item
    shift_no_data: set[str] = set()         # SW 에 same primary 없거나 cursor 끝남 → No Data
    if SHIFT_TAIL_TO_NEXT_PRIMARY:
        def _sort_seg2(name: str):
            k = _extract_cc_key(name)
            s = _extract_sub_num(name)
            return _cc_sort_key(k, s)

        # SW 의 (type) 별 primary set 구함 — primary 존재 여부 판정용
        sw_primaries_by_type: dict[str, set[str]] = {}
        for it in new_segs:
            k = _extract_cc_key(it.get("name", ""))
            if k:
                sw_primaries_by_type.setdefault(k[0], set()).add(k[1])

        # (type, suffix) 별로 grouping
        md_by_ts: dict[tuple[str, str], list[str]] = {}
        sw_by_ts: dict[tuple[str, str], list[dict]] = {}
        for sid in src_seg_ids:
            nm = src_seg_info[sid]["name"]
            if not _has_swap_keyword(nm):
                continue
            if SKIP_KEYWORDS and _extract_sub_num(nm) is not None and _has_skip_keyword(nm):
                continue
            k = _extract_cc_key(nm)
            if not k:
                continue
            md_by_ts.setdefault((k[0], k[2]), []).append(sid)
        for it in new_segs:
            k = _extract_cc_key(it.get("name", ""))
            if not k:
                continue
            sw_by_ts.setdefault((k[0], k[2]), []).append(it)

        for ts_key in md_by_ts:
            md_by_ts[ts_key].sort(key=lambda s: (_sort_seg2(src_seg_info[s]["name"]), src_seg_info[s]["name"]))
        for ts_key in sw_by_ts:
            sw_by_ts[ts_key].sort(key=lambda it: (_sort_seg2(it.get("name", "")), it.get("name", "")))

        # cursor 알고리즘
        for ts_key, md_list in md_by_ts.items():
            sw_list = sw_by_ts.get(ts_key, [])
            sw_primaries_in_type = sw_primaries_by_type.get(ts_key[0], set())
            sw_cursor = 0
            for sid in md_list:
                nm = src_seg_info[sid]["name"]
                k = _extract_cc_key(nm)
                md_primary = k[1]
                # SW 에 same primary 자체 없으면 → No Data, cursor 유지
                if md_primary not in sw_primaries_in_type:
                    shift_no_data.add(sid)
                    continue
                # cursor 끝났으면 → No Data
                if sw_cursor >= len(sw_list):
                    shift_no_data.add(sid)
                    continue
                shift_mapping[sid] = sw_list[sw_cursor]
                sw_cursor += 1

        print(f"  [SHIFT_TAIL_TO_NEXT_PRIMARY] (type, suffix) 별 cursor 매핑 결과:")
        for ts_key in sorted(set(list(md_by_ts.keys()) + list(sw_by_ts.keys()))):
            n_md = len(md_by_ts.get(ts_key, []))
            n_sw = len(sw_by_ts.get(ts_key, []))
            n_mapped = sum(1 for sid in md_by_ts.get(ts_key, []) if sid in shift_mapping)
            n_nodata = sum(1 for sid in md_by_ts.get(ts_key, []) if sid in shift_no_data)
            print(f"    ({ts_key[0]:<6}, {ts_key[1]:<8}): MD={n_md}  SW={n_sw}  → 매핑 {n_mapped} / no_data {n_nodata}")

    for sid in sorted(src_seg_ids, key=lambda s: (
        _cc_sort_key(_extract_cc_key(src_seg_info[s]["name"]), _extract_sub_num(src_seg_info[s]["name"])),
        src_seg_info[s]["name"],
    )):
        info = src_seg_info[sid]
        name = info["name"]
        key = _extract_cc_key(name)
        sub = _extract_sub_num(name)
        key_str = _format_match_key(key, sub)
        norm = _normalize_name(name)

        # 1) 수동 오버라이드 우선
        if sid in MANUAL_OVERRIDES:
            new_id = MANUAL_OVERRIDES[sid]
            new_name = (new_by_id.get(new_id) or {}).get("name") or ""
            if not new_name:
                d = _fetch_segment(headers, gcid, new_id)
                new_name = d.get("name", "")
            mapping[sid] = new_id
            rows.append({
                "SourceSegId":   sid,
                "SourceSegName": name,
                "MatchKey":      key_str,
                "NormalizedName": norm,
                "TargetSegId":   new_id,
                "TargetSegName": new_name,
                "MatchStatus":   "OK (manual)",
            })
            continue

        # 2) SW_ORDER_MAPPING 모드면 미리 빌드된 dict 우선 적용
        if SW_ORDER_MAPPING and _has_swap_keyword(name):
            if sid in sw_order_mapping:
                it = sw_order_mapping[sid]
                mapping[sid] = it["id"]
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   it["id"],
                    "TargetSegName": it.get("name", ""),
                    "MatchStatus":   "OK (sw_order)",
                })
                continue
            if sid in sw_order_tail:
                tail_source_ids.add(sid)
                fallback_status = "REMOVE (sw_order tail)"
                target_id_disp = ""
                target_name_disp = "(REMOVE — SW list 끝남)"
                if no_data_sid and USE_NO_DATA_FALLBACK:
                    mapping[sid] = no_data_sid
                    fallback_status = "REMOVE (sw_order tail) → no_data fallback"
                    target_id_disp = no_data_sid
                    target_name_disp = "(TRIM 시도 → 실패 시 No Data 보험; SW list 끝남)"
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   target_id_disp,
                    "TargetSegName": target_name_disp,
                    "MatchStatus":   fallback_status,
                })
                continue
            # SW_ORDER 에서 안 잡혔으면 CC key 없는 source 일 가능성 → normalize 로 fall through

        # 2.5) SHIFT_TAIL_TO_NEXT_PRIMARY 모드 — 미리 빌드된 cursor 매핑 적용
        if SHIFT_TAIL_TO_NEXT_PRIMARY and _has_swap_keyword(name):
            if sid in shift_mapping:
                it = shift_mapping[sid]
                mapping[sid] = it["id"]
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   it["id"],
                    "TargetSegName": it.get("name", ""),
                    "MatchStatus":   "OK (shift)",
                })
                continue
            if sid in shift_no_data:
                if no_data_sid and USE_NO_DATA_FALLBACK:
                    mapping[sid] = no_data_sid
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   no_data_sid or "",
                    "TargetSegName": no_data_name or "",
                    "MatchStatus":   "OK (shift→no_data)",
                })
                continue
            # SHIFT 에서 안 잡혔으면 CC key 없는 source 일 가능성 → normalize 로 fall through

        # 3) [CAMPAIGN NAME] prefix 없는 system/공용 segment → swap 없이 keep
        #    (target 에도 같은 ID 그대로 참조 — No Data, PC User, [part_name], [Global] 등)
        if not _has_swap_keyword(name):
            rows.append({
                "SourceSegId":   sid,
                "SourceSegName": name,
                "MatchKey":      key_str,
                "NormalizedName": norm,
                "TargetSegId":   sid,
                "TargetSegName": name,
                "MatchStatus":   "OK (keep)",
            })
            continue

        # 3) SKIP_KEYWORDS (예: recomm) 포함 + sub_num 있음 → 자동 매칭 제외, No Data fallback
        #    sub_num 없는 컨테이너 segment 는 영향 없이 정상 매칭 단계로 진입.
        if sub is not None and _has_skip_keyword(name):
            if no_data_sid and USE_NO_DATA_FALLBACK:
                mapping[sid] = no_data_sid
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   no_data_sid,
                    "TargetSegName": no_data_name,
                    "MatchStatus":   "OK (skip→no_data)",
                })
            else:
                unmapped_src.append(sid)
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   "",
                    "TargetSegName": "",
                    "MatchStatus":   "SKIP (no_data unavailable)",
                })
            continue

        # 4) sub_num 매칭 — source 가 ' - ##.' 가지면 (type, sub_num, suffix) 로 1차 매칭
        #    (primary CC 번호 다른 target 도 OK — sub_num 이 핵심 키)
        #    매칭 실패시 즉시 No Data 로 빠지지 않고 5) primary CC fallback 으로 흘러감.
        #    SW_ORDER_MAPPING 모드에서는 사전 처리되니까 스킵.
        if not SW_ORDER_MAPPING and not SHIFT_TAIL_TO_NEXT_PRIMARY and key is not None and sub is not None:
            cand_key = (key[0], sub, key[2])
            cand = new_by_sub_key.get(cand_key) or []
            if len(cand) == 1:
                new_id = cand[0]["id"]
                mapping[sid] = new_id
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   new_id,
                    "TargetSegName": cand[0].get("name", ""),
                    "MatchStatus":   "OK (sub_num)",
                })
                continue
            elif len(cand) > 1:
                # tie-breaker: PREFERRED_OWNER_ID 가 만든 것 1개로 좁혀지면 선택
                picked = _pick_by_owner(cand, PREFERRED_OWNER_ID)
                if picked:
                    mapping[sid] = picked["id"]
                    rows.append({
                        "SourceSegId":   sid,
                        "SourceSegName": name,
                        "MatchKey":      key_str,
                        "NormalizedName": norm,
                        "TargetSegId":   picked["id"],
                        "TargetSegName": picked.get("name", ""),
                        "MatchStatus":   "OK (sub_num + owner_pref)",
                    })
                    continue
                ambiguous.append((sid, cand))
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   "|".join(c["id"] for c in cand),
                    "TargetSegName": "|".join(c.get("name", "") for c in cand),
                    "MatchStatus":   f"AMBIGUOUS_sub_num({len(cand)})",
                })
                continue
            # cand == 0 → 끝쪽 sub_num 빔 (SW max 보다 큰 source) 이면 panel 에서 제거.
            #             그 외 (시작/중간 빔) 는 5) primary CC fallback 으로 흘러감.
            if TRIM_TAIL_UNMATCHED_SUB_NUM:
                primary_key = (key[0], key[1])
                sw_max_sub = sw_max_sub_by_primary.get(primary_key)
                try:
                    src_sub_int = int(sub)
                except ValueError:
                    src_sub_int = -1
                if sw_max_sub is not None and src_sub_int > sw_max_sub:
                    tail_source_ids.add(sid)
                    # 보험 — panel JSON 제거가 실패할 가능성 대비 mapping 에도 No Data 박음.
                    # panel JSON 제거 성공 시: source ID 가 panel 에서 사라졌으니 mapping 영향 X.
                    # 실패 시: _swap_segment_ids 가 source → No Data 로 swap → "No Data 가 그
                    # 자리에 박힘" graceful fallback (panel column 수는 그대로 유지).
                    fallback_status = "REMOVE (tail)"
                    target_id_disp = ""
                    target_name_disp = f"(REMOVE — SW max sub_num={sw_max_sub} < {src_sub_int})"
                    if no_data_sid and USE_NO_DATA_FALLBACK:
                        mapping[sid] = no_data_sid
                        fallback_status = "REMOVE (tail) → no_data fallback"
                        target_id_disp = no_data_sid
                        target_name_disp = f"(TRIM 시도 → 실패 시 No Data 보험; SW max sub_num={sw_max_sub} < {src_sub_int})"
                    rows.append({
                        "SourceSegId":   sid,
                        "SourceSegName": name,
                        "MatchKey":      key_str,
                        "NormalizedName": norm,
                        "TargetSegId":   target_id_disp,
                        "TargetSegName": target_name_disp,
                        "MatchStatus":   fallback_status,
                    })
                    continue
            # 끝쪽 아니거나 TRIM 꺼져있으면 5) primary CC 매칭으로 fall through

        # 5) primary CC 매칭 — sub_num 없는 컨테이너 segment, 또는 sub_num 매칭 실패 fallback.
        #    SW 의 sub_num 없는 컨테이너 (예: "CC_03. Scenario: Your Daily Sync") 와 매칭.
        #    여기서도 안 잡히면 No Data fallback.
        #    sub→cc_key fallback 시 중복 방지: 이미 다른 source 에 쓰인 target 은 후보에서
        #    제외 → No Data 로. (sort 순서상 컨테이너 source 가 먼저 처리되어 mapping 에
        #    들어가니까 그 다음 sub_num 변형들이 fallback 들어올 때 cand 에서 자동 제거됨.)
        #    SW_ORDER_MAPPING 모드에서는 사전 처리되니까 스킵.
        if not SW_ORDER_MAPPING and not SHIFT_TAIL_TO_NEXT_PRIMARY and key is not None:
            cand = new_by_cc_key.get(key) or []
            if sub is not None and cand:
                used = set(mapping.values()) - ({no_data_sid} if no_data_sid else set())
                cand = [c for c in cand if c["id"] not in used]
            if len(cand) == 1:
                new_id = cand[0]["id"]
                mapping[sid] = new_id
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   new_id,
                    "TargetSegName": cand[0].get("name", ""),
                    "MatchStatus":   "OK (sub→cc_key)" if sub is not None else "OK (cc_key)",
                })
                continue
            elif len(cand) > 1:
                # tie-breaker: PREFERRED_OWNER_ID 가 만든 것 1개로 좁혀지면 선택
                picked = _pick_by_owner(cand, PREFERRED_OWNER_ID)
                if picked:
                    mapping[sid] = picked["id"]
                    rows.append({
                        "SourceSegId":   sid,
                        "SourceSegName": name,
                        "MatchKey":      key_str,
                        "NormalizedName": norm,
                        "TargetSegId":   picked["id"],
                        "TargetSegName": picked.get("name", ""),
                        "MatchStatus":   "OK (cc_key + owner_pref)",
                    })
                    continue
                ambiguous.append((sid, cand))
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   "|".join(c["id"] for c in cand),
                    "TargetSegName": "|".join(c.get("name", "") for c in cand),
                    "MatchStatus":   f"AMBIGUOUS_cc_key({len(cand)})",
                })
                continue
            # cand == 0 → No Data fallback (SW 측에 해당 (type,num,suffix) 변형 없음)
            if no_data_sid and USE_NO_DATA_FALLBACK:
                mapping[sid] = no_data_sid
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   no_data_sid,
                    "TargetSegName": no_data_name,
                    "MatchStatus":   "OK (no_data)",
                })
                continue
            # No Data fallback 도 못 쓰면 normalize_name fallback 으로 떨어짐

        # 6) normalize_name fallback
        cand = new_by_norm.get(norm) or []
        if len(cand) == 1:
            new_id = cand[0]["id"]
            mapping[sid] = new_id
            rows.append({
                "SourceSegId":   sid,
                "SourceSegName": name,
                "MatchKey":      key_str,
                "NormalizedName": norm,
                "TargetSegId":   new_id,
                "TargetSegName": cand[0].get("name", ""),
                "MatchStatus":   "OK (normalize)",
            })
        elif len(cand) == 0:
            unmapped_src.append(sid)
            rows.append({
                "SourceSegId":   sid,
                "SourceSegName": name,
                "MatchKey":      key_str,
                "NormalizedName": norm,
                "TargetSegId":   "",
                "TargetSegName": "",
                "MatchStatus":   "NO_MATCH",
            })
        else:
            # tie-breaker: PREFERRED_OWNER_ID 가 만든 것 1개로 좁혀지면 선택
            picked = _pick_by_owner(cand, PREFERRED_OWNER_ID)
            if picked:
                mapping[sid] = picked["id"]
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   picked["id"],
                    "TargetSegName": picked.get("name", ""),
                    "MatchStatus":   "OK (normalize + owner_pref)",
                })
            else:
                ambiguous.append((sid, cand))
                rows.append({
                    "SourceSegId":   sid,
                    "SourceSegName": name,
                    "MatchKey":      key_str,
                    "NormalizedName": norm,
                    "TargetSegId":   "|".join(c["id"] for c in cand),
                    "TargetSegName": "|".join(c.get("name", "") for c in cand),
                    "MatchStatus":   f"AMBIGUOUS_norm({len(cand)})",
                })

    used_new_ids = {v for v in mapping.values()}
    leftover_new = [it for it in new_segs if it["id"] not in used_new_ids]

    # 5) 콘솔 요약 + CSV
    print(f"\n  매핑 결과:")
    print(f"    OK (sw_order)             : {sum(1 for r in rows if r['MatchStatus'] == 'OK (sw_order)')}")
    print(f"    REMOVE (sw_order tail)    : {sum(1 for r in rows if r['MatchStatus'] == 'REMOVE (sw_order tail)')}")
    print(f"    OK (shift)                : {sum(1 for r in rows if r['MatchStatus'] == 'OK (shift)')}")
    print(f"    OK (shift→no_data)        : {sum(1 for r in rows if r['MatchStatus'] == 'OK (shift→no_data)')}")
    print(f"    OK (sub_num)              : {sum(1 for r in rows if r['MatchStatus'] == 'OK (sub_num)')}")
    print(f"    OK (sub_num + owner_pref) : {sum(1 for r in rows if r['MatchStatus'] == 'OK (sub_num + owner_pref)')}")
    print(f"    OK (sub→cc_key)           : {sum(1 for r in rows if r['MatchStatus'] == 'OK (sub→cc_key)')}")
    print(f"    OK (cc_key)               : {sum(1 for r in rows if r['MatchStatus'] == 'OK (cc_key)')}")
    print(f"    OK (cc_key + owner_pref)  : {sum(1 for r in rows if r['MatchStatus'] == 'OK (cc_key + owner_pref)')}")
    print(f"    OK (no_data)              : {sum(1 for r in rows if r['MatchStatus'] == 'OK (no_data)')}")
    print(f"    OK (skip→no_data)         : {sum(1 for r in rows if r['MatchStatus'] == 'OK (skip→no_data)')}")
    print(f"    OK (normalize)            : {sum(1 for r in rows if r['MatchStatus'] == 'OK (normalize)')}")
    print(f"    OK (normalize + owner_pref): {sum(1 for r in rows if r['MatchStatus'] == 'OK (normalize + owner_pref)')}")
    print(f"    OK (manual)               : {sum(1 for r in rows if r['MatchStatus'] == 'OK (manual)')}")
    print(f"    OK (keep)                 : {sum(1 for r in rows if r['MatchStatus'] == 'OK (keep)')}")
    print(f"    REMOVE (tail)             : {sum(1 for r in rows if r['MatchStatus'] == 'REMOVE (tail)')}")
    print(f"    NO_MATCH                  : {len(unmapped_src)}")
    print(f"    AMBIGUOUS                 : {len(ambiguous)}")
    print(f"    leftover {NEW_KEYWORDS[0]} segments not used: {len(leftover_new)}")

    print("\n  ── 매핑 표 ─────────────────────────────────────────────────")
    for r in rows:
        flag = "✓" if r["MatchStatus"].startswith("OK") else "✗"
        src_name = r["SourceSegName"][:48]
        tgt_name = r["TargetSegName"][:48]
        key_disp = f"[{r['MatchKey']:<20}]" if r['MatchKey'] else "[—                  ]"
        print(f"    {flag} {key_disp} {r['SourceSegId']}  {src_name:<48}  →  {r['TargetSegId']:<40}  {tgt_name}")
        if not r["MatchStatus"].startswith("OK"):
            print(f"        status: {r['MatchStatus']}  normalized: '{r['NormalizedName']}'")

    if leftover_new:
        print(f"\n  ── target {NEW_KEYWORDS[0]} segments NOT used (참고) ─────────────────")
        for it in sorted(leftover_new, key=lambda it: _cc_sort_key(_extract_cc_key(it.get("name", "")), _extract_sub_num(it.get("name", ""))))[:50]:
            nm = it.get("name", "")
            k = _extract_cc_key(nm)
            s = _extract_sub_num(nm)
            key_disp = f"[{_format_match_key(k, s)}]" if k else "[—]"
            print(f"    · {key_disp:<24} {it['id']}  {nm}")
        if len(leftover_new) > 50:
            print(f"    ... +{len(leftover_new) - 50}")

    csv_out = OUTPUT_DIR / CSV_OUTPUT_TEMPLATE.format(ts=ts)
    with open(csv_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[
            "RequestedAt", "SourceSegId", "SourceSegName", "MatchKey", "NormalizedName",
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

    # 7) APPLY
    if not mapping:
        print("\n❌ 매핑된 segment 가 0 개 — apply 중단.")
        return 3
    if unmapped_src or ambiguous:
        print(f"\n⚠️  NO_MATCH {len(unmapped_src)}건, AMBIGUOUS {len(ambiguous)}건 남아있음.")
        ans = input("그래도 진행하시려면 'yes' 입력: ").strip().lower()
        if ans != "yes":
            print("취소.")
            return 0

    print(f"\n[6] Building modified panels ({len(selected_panels)} 개)...")
    if tail_source_ids:
        print(f"  TRIM 대상 (끝쪽 sub_num) source ID: {len(tail_source_ids)} 개 — panel 에서 제거 시도")
    # 날짜 유지 — target panel 들의 ISO interval (dateRange) 값을 미리 수집해서
    # 새 panel 빌드 후 같은 occurrence 순서대로 복원. source 날짜 덮어쓰는 거 방지.
    tgt_panels_existing = (tgt_ws[0].get("panels") if tgt_ws else None) or []
    target_date_intervals_per_panel: list[list[str]] = []
    for tgt_panel in tgt_panels_existing:
        target_date_intervals_per_panel.append(_collect_date_intervals(tgt_panel))
    new_panels: list[dict] = []
    for build_i, (idx, src_panel) in enumerate(zip(panel_indices, selected_panels)):
        np = copy.deepcopy(src_panel)
        # 1) tail (끝쪽 sub_num 미매칭) source ID 들을 panel JSON 에서 제거
        if tail_source_ids:
            n_trim, trim_samples = _remove_source_ids_from_panel(np, tail_source_ids)
            print(f"  [{idx}] TRIM       : {n_trim} 개 entry 제거")
            for s in trim_samples[:10]:
                print(f"      · {s}")
            if len(trim_samples) > 10:
                print(f"      · ... ({len(trim_samples) - 10}개 더)")
        # 2) 나머지 source → target swap
        _swap_segment_ids(np, mapping)
        if RENAME_PANEL:
            old_name = np.get("name", "")
            renamed = _rename_panel(old_name)
            if renamed and renamed != old_name:
                print(f"  [{idx}] panel 이름 : '{old_name}'  →  '{renamed}'")
                np["name"] = renamed
        if COLLAPSE_ALL_SUBPANELS:
            n_collapsed = _collapse_all_subpanels(np)
            total = len(np.get("subPanels") or [])
            print(f"  [{idx}] subPanels  : {total} 개 중 {n_collapsed} 개 collapsed=True 로 변경")
        # 3) 날짜 유지 — target 의 기존 dateRange 들로 occurrence 순서대로 복원
        if build_i < len(target_date_intervals_per_panel):
            tgt_intervals = target_date_intervals_per_panel[build_i]
            if tgt_intervals:
                n_restored = _restore_date_intervals(np, tgt_intervals)
                print(f"  [{idx}] 날짜 복원 : {n_restored} 개 dateRange — target 의 기존 값으로 유지")
        new_panels.append(np)

    new_definition = copy.deepcopy(src_def)
    new_definition["workspaces"] = [copy.deepcopy(src_workspaces[0])]
    new_definition["workspaces"][0]["panels"] = new_panels

    new_target = copy.deepcopy(tgt)
    new_target["definition"] = new_definition

    if args.debug:
        dbg_path = OUTPUT_DIR / f"_debug_put_body_{ts}.json"
        dbg_path.write_text(json.dumps(new_target, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [debug] PUT body dump → {dbg_path.name}")

    print("\n[7] PUT to target project...")
    try:
        resp = _put_project(headers, gcid, TARGET_PROJECT_ID, new_target)
        print(f"  ✓ PUT 성공.")
        print(f"  target 새 이름  : {resp.get('name', '?')}")
        resp_ws = resp.get('definition', {}).get('workspaces', []) or []
        print(f"  workspaces      : {len(resp_ws)}")
        resp_panels = (resp_ws[0].get('panels') if resp_ws else None) or []
        print(f"  panels ({len(resp_panels)} 개):")
        for i, p in enumerate(resp_panels):
            print(f"      [{i}] {(p or {}).get('name', '?')}")
        print(f"  UI 링크         : https://experience.adobe.com/@company_name/analytics/spa/#/workspace/edit/{TARGET_PROJECT_ID}")
    except Exception as e:
        print(f"  ❌ PUT 실패: {e}")
        return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
