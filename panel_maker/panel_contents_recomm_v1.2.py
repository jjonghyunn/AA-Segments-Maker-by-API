# panel_contents_recomm_v1.2.py
# 2026-05-20  Jonghyun Park w/ Claude
# v1.2 변경: Content C (PR/US_PR) fallback type 추가, SKIP_KEYWORDS 비우기,
#          PREFERRED_SEGMENT_CSV 옵션 (현재 비활성), US_CC_[US] 잔재 제외 룰 추가
#
# panel_contents.py 사본 — recomm (Recommendation) 계열 패널용.
# SOURCE/TARGET PROJECT_ID 가 본 사본 전용 값으로 박혀있음.
# 캠페인 prefix 가 [CAMPAIGN NAME]/[CAMPAIGN NAME] 가 아니면 OLD_KEYWORDS / NEW_KEYWORDS /
# PANEL_NAME_REPLACEMENTS 도 함께 수정 필요 (아래 # TODO 표시).
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
  · source 프로젝트(YOUR_ID, [CAMPAIGN NAME] 계열) 의 panel[0] 구조를
    그대로 복사하되, 그 안에 박혀있는 "[CAMPAIGN NAME]" 계열 segment ID 들을
    "[CAMPAIGN NAME]" 계열 segment 중 같은 (type, 번호, suffix) 를 갖는 것으로 swap.
  · target 프로젝트(YOUR_ID, 미리 UI 에서 본인 계정으로
    만든 빈 프로젝트) 의 definition 을 수정해서 PUT.

매칭 룰:
  · 1차 키 (type, primary_num, suffix) — 이름 prefix `CC_##.` / `US_CC_##.` + 끝 suffix.
  · 2차 키 (type, sub_num, suffix)     — 이름 안의 ` - ##.` 패턴 (있는 경우만).
  · source 에 sub_num 있으면 → 2차 키 매칭. 실패 시 같은 (type, primary_num, suffix)
    의 SW 컨테이너 (sub_num 없는 것) 로 fallback. 그것도 없으면 No Data.
  · source 에 sub_num 없으면 → 1차 키로 매칭 (target 도 sub_num 없는 것끼리).
  · 매칭 안 되면 No Data fallback (칼럼 자리수 유지).
  · 2개 이상 매칭되면 (AMBIGUOUS) PREFERRED_OWNER_ID (예: user2) 가 만든 것
    1개로 좁히는 tie-breaker. 그래도 안 좁혀지면 AMBIGUOUS 표시.
  · CC / US_CC 패턴 없는 segment 는 _normalize_name 으로 fallback.
  · "[CAMPAIGN NAME]" prefix 없는 segment 는 swap 후보에서 제외 — keep as-is.
  · SKIP_KEYWORDS (예: "recomm") + sub_num 둘 다 있는 segment 는 자동 매칭 제외 → No Data
    (하위 breakdown 케이스가 많고 매칭 규칙이 복잡한 경우용. 컨테이너 segment 는 영향 X).
  · 우선순위: MANUAL_OVERRIDES > keep(no [CAMPAIGN NAME]) > skip(SKIP_KEYWORDS + sub_num)
              > sub_num > primary CC (sub_num 매칭 실패 시 fallback 포함) > No Data
              > normalize. 각 단계 AMBIGUOUS 시 owner_pref tie-breaker.

매칭 예시:
  · "[CAMPAIGN NAME] CC_01. Content B"     ─ ("CC","01","")    ↔ "[CAMPAIGN NAME] CC_01. Content B"
  · "[CAMPAIGN NAME] CC_01. ... (Visit)"          ─ ("CC","01","visit") ↔ SW 같은 (Visit) 변형
  · "[CAMPAIGN NAME] CC_03. ... - 01. Trip Recall"  ─ sub_num="01"
       ↔ "[CAMPAIGN NAME] CC_XX. ... - 01. ..."  (CC 번호 달라도 sub_num 같으면 매칭)
  · "[CAMPAIGN NAME] CC_08. Content C"  → sub_num 없음, recomm 포함 → 정상 매칭 시도
  · "[CAMPAIGN NAME] CC_08. Content C - 01. Foo"  → sub_num 있고 recomm 포함 → No Data

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
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
# source = 복제 원본. Workspace URL 의 /workspace/edit/{이부분}
SOURCE_PROJECT_ID = "YOUR_PROJECT_ID"   # 참고 원본 프로젝트
# target = 미리 UI 에서 빈 프로젝트로 생성해둔 곳 (user1 owner)
# TARGET_PROJECT_ID = "YOUR_PROJECT_ID" # team공유용.
TARGET_PROJECT_ID = "YOUR_PROJECT_ID"   # [part_name] 2026 CAMPAIGN NAME | Contents Click Analysis (Content C) | API (user_id)
# https://experience.adobe.com/@company_name/analytics/spa/#/workspace/edit/YOUR_PROJECT_ID
# source 의 어느 panel(들) 을 가져올지.
#   · "all"            → 모든 panel (기본)
#   · [0]              → 첫 panel 만
#   · [0, 1]           → 첫·두번째 panel
#   · [0, 2, 3]        → 특정 인덱스만
# 0-based. 결과는 지정한 순서대로 target 에 박힘.
SOURCE_PANEL_INDICES: "list[int] | str" = "all"

# ─── segment 검색 키워드 ───────────────────────────────────────────
# TODO: 캠페인이 [CAMPAIGN NAME] → [CAMPAIGN NAME] 가 아니면 아래 값을 새 캠페인 prefix 에 맞게 변경.
#       SWAP_REQUIRED_KEYWORDS (아래) 도 동일하게 맞춰야 함.
# source panel 에 박혀있는 segment 들이 매칭될 OLD 키워드 (검증용)
OLD_KEYWORDS = ["[CAMPAIGN NAME]", "CAMPAIGN NAME"]
# target segment 들이 매칭될 NEW 키워드 (회사 전체 /segments paginate 후 클라 필터)
NEW_KEYWORDS = ["[CAMPAIGN NAME]", "CAMPAIGN NAME"]

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
# TODO: OLD_KEYWORDS 와 동일하게 캠페인 prefix 에 맞게 변경.
SWAP_REQUIRED_KEYWORDS = ["[CAMPAIGN NAME]", "CAMPAIGN NAME", "Content C"]

# ─── Ambiguous tie-breaker (소유자 우선순위) ──────────────────────
# 같은 키 ((type, num/sub_num, suffix) 등) 에 SW segment 가 2개 이상 매칭될 때,
# 이 owner.id 가 만든 segment 1개를 우선 선택. 그래도 정확히 1개로 좁혀지지
# 않으면 기존대로 AMBIGUOUS 표시.
PREFERRED_OWNER_ID = "YOUR_LOGIN_ID"  # user2

# ─── 자동 매칭 제외 키워드 (sub_num 있는 segment 한정) ─────────────
# 이 단어가 이름에 포함되고 sub_num (' - ##.') 도 있는 segment 는 자동 매칭에서
# 제외하고 No Data fallback 으로 메꿈. 하위 breakdown 케이스가 많아 따로 매핑할
# segment 들 (예: "recomm" → Content C - 01. Foo 같은 sub 변형).
# sub_num 없는 컨테이너 segment (예: "CC_08. Content C") 는 영향 없이
# 정상 매칭. 추후 MANUAL_OVERRIDES 또는 별도 도구로 처리.
SKIP_KEYWORDS: list[str] = []  # 새 [CAMPAIGN NAME] CC_Content C 추가됨 → recomm 도 자동 매칭 OK

# ─── candidate 제한 (특정 result csv 의 SegmentId 만 swap 후보) ──────
# 빈 string 이면 NEW_KEYWORDS 매칭 segment 전체 사용.
# 박혀있으면 그 csv 의 SegmentId 컬럼 값에 해당하는 segment 만 candidate.
# (입력 csv 형식: aa_create_segment_v2.2.py 의 result csv — header 에 'SegmentId' 컬럼)
PREFERRED_SEGMENT_CSV = ""  # 비활성화 — 중복 segment 삭제 완료, NEW_KEYWORDS 전체에서 매칭

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
    # "sXXXXXXXXX_xxxxxxxxxxxxxxxxxxxxxxxx": "sXXXXXXXXX_yyyyyyyyyyyyyyyyyyyyyyyy",
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

# ─── 테이블(subPanel) 접힘 상태 강제 ───────────────────────────────
COLLAPSE_ALL_SUBPANELS = True

# ─── Panel 이름 변환 패턴 (panel 헤더 텍스트) ────────────────────────
# TODO: 캠페인이 [CAMPAIGN NAME] → [CAMPAIGN NAME] 가 아니면 아래 치환 룰을 새 캠페인 명칭에 맞게 변경.
#       필요 없으면 RENAME_PANEL=False 로 두면 panel 이름은 source 그대로 복사됨.
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
    number_raw 는 zero-pad 그대로 ("01" vs "1" 구분) — source/target 표기 통일 가정.

    fallback: CC 패턴 없어도 'Content C - NN.' 형식이면
    type='PR' + num=sub_num 으로 매칭 키 생성 (source 와 target 둘 다 동일하게 추출됨).
    이는 source 의 [CAMPAIGN NAME] 없는 'Content C - 01. ...' 와
    target 의 '[CAMPAIGN NAME] CC_Content C - 01. ...' 를 같은 키로 묶기 위함."""
    n = name or ""
    # 잔재 segment 제외: 'US_CC_[US]' (dedupe 안 된 옛 이름) → 매칭 후보 제외
    if re.search(r"US_CC_\[US\]", n, re.IGNORECASE):
        return None
    for type_label, pat in CC_TYPE_PATTERNS:
        m = pat.search(n)
        if m:
            return (type_label, m.group(1), _extract_suffix(n))
    # fallback — Content C 패턴 (Global vs US 분리)
    # US 우선순위: US_CC_Content C 또는 [US] Content C
    has_us = bool(re.search(r"\[US\]\s*Content C|US_CC_Content C|US_Content C", n, re.IGNORECASE))
    has_pr = bool(re.search(r"Content C", n, re.IGNORECASE))
    if has_pr:
        sub_num = _extract_sub_num(n)
        if sub_num:
            type_label = "US_PR" if has_us else "PR"
            return (type_label, sub_num, _extract_suffix(n))
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


def _pick_by_owner(cand: list[dict], owner_id: str) -> dict | None:
    """cand 중 owner.id 가 owner_id 인 것 1개 리턴. 0개 또는 2개 이상이면 None.
    AMBIGUOUS tie-breaker 용 (예: user2 의 segment 우선)."""
    matched = [c for c in cand if str(((c.get("owner") or {}).get("id") or "")) == str(owner_id)]
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

    # candidate 제한 — PREFERRED_SEGMENT_CSV 의 SegmentId 만 candidate 로
    if PREFERRED_SEGMENT_CSV:
        preferred_ids: set[str] = set()
        try:
            with open(PREFERRED_SEGMENT_CSV, encoding="utf-8-sig", newline="") as f:
                rdr = csv.DictReader(f)
                for row in rdr:
                    sid = (row.get("SegmentId") or row.get("segment_id") or "").strip()
                    if sid:
                        preferred_ids.add(sid)
            print(f"  → PREFERRED_SEGMENT_CSV: {len(preferred_ids)} ids ({Path(PREFERRED_SEGMENT_CSV).name})")
            new_segs = [s for s in new_segs if s.get("id") in preferred_ids]
            print(f"  → filtered candidates: {len(new_segs)} (csv 안 SegmentId 만)")
        except Exception as e:
            print(f"  ⚠️ PREFERRED_SEGMENT_CSV 로드 실패 — {e}. filter 건너뜀.")

    # CC/US_CC 키별 인덱스 분리:
    #   · new_by_sub_key: sub_num 있는 target — (type, sub_num, suffix)
    #   · new_by_cc_key : sub_num 없는 target — (type, primary_num, suffix)
    # + normalize 인덱스 (fallback)
    new_by_cc_key: dict[tuple[str, str, str], list[dict]] = {}
    new_by_sub_key: dict[tuple[str, str, str], list[dict]] = {}
    new_by_norm: dict[str, list[dict]] = {}
    for it in new_segs:
        name = it.get("name", "")
        key = _extract_cc_key(name)
        sub = _extract_sub_num(name)
        if key:
            if sub is not None:
                new_by_sub_key.setdefault((key[0], sub, key[2]), []).append(it)
            else:
                new_by_cc_key.setdefault(key, []).append(it)
        norm = _normalize_name(name)
        new_by_norm.setdefault(norm, []).append(it)

    # 4) 매핑 빌드
    print("\n[4] Building old → new mapping...")
    print("    우선순위: MANUAL_OVERRIDES > keep(no [CAMPAIGN NAME]) > skip(SKIP_KEYWORDS + sub_num→no_data)")
    print("              > sub_num((type,sub_num,suffix)) > primary CC((type,num,suffix))")
    print("              > No Data fallback > normalize_name")
    if MANUAL_OVERRIDES:
        print(f"  (수동 오버라이드 {len(MANUAL_OVERRIDES)}건 우선 적용)")
    if SKIP_KEYWORDS:
        print(f"  (자동 매칭 제외 키워드: {SKIP_KEYWORDS} — sub_num 있는 segment 한정 → No Data)")
    if PREFERRED_OWNER_ID:
        print(f"  (AMBIGUOUS tie-breaker: owner.id={PREFERRED_OWNER_ID} (user2) 우선)")
    new_by_id: dict[str, dict] = {it["id"]: it for it in new_segs}
    mapping: dict[str, str] = {}
    rows: list[dict] = []
    unmapped_src: list[str] = []
    ambiguous: list[tuple[str, list[dict]]] = []
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

        # 2) [CAMPAIGN NAME] prefix 없는 system/공용 segment → swap 없이 keep
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
        if key is not None and sub is not None:
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
            # cand == 0 → 5) primary CC 매칭으로 fall through

        # 5) primary CC 매칭 — sub_num 없는 컨테이너 segment, 또는 sub_num 매칭 실패 fallback.
        #    SW 의 sub_num 없는 컨테이너 (예: "CC_03. Scenario: Your Daily Sync") 와 매칭.
        #    여기서도 안 잡히면 No Data fallback.
        #    sub→cc_key fallback 시 중복 방지: 이미 다른 source 에 쓰인 target 은 후보에서
        #    제외 → No Data 로. (컨테이너 source 가 먼저 처리되어 mapping 에 들어가니까
        #    그 다음 sub_num 변형들이 fallback 들어올 때 cand 에서 자동 제거됨.)
        if key is not None:
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
    new_panels: list[dict] = []
    for idx, src_panel in zip(panel_indices, selected_panels):
        np = copy.deepcopy(src_panel)
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
