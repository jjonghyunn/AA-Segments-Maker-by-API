# add_segment_shares.py
# 2026-05-13  Jonghyun Park w/ Claude
"""
Adobe Analytics segment 중 이름에 특정 키워드가 들어간 것들에 대해 shares 일괄 추가.

수행 작업
  1) `/segments?expansion=...` 로 본인 owner 의 segment 전체 GET
  2) name 또는 description 에 KEYWORDS 의 모든 substring 다 포함 (AND) 인 segment 매칭
  3) 매칭 목록을 사용자에게 보여주고 확인 받음 (안전장치)
  4) 확인 후 각 segment 의 shares 필드에 SHARE_USER_IDS 추가 (기존 shares 보존)
  5) `--apply` 면 실제 PUT, 아니면 dry-run

설계 원칙
  · 매칭된 segment 목록을 항상 먼저 출력 → 사용자가 "이 세그들이 맞는지" 눈으로 확인
  · `--apply` 모드에서도 input() confirm 한 번 더 받음 (두 번째 안전장치)
  · 본인이 owner 인 segment 만 처리 (다른 사람 owner 면 PUT 시 403)
  · 기존 shares 는 그대로 두고 신규 ID 만 append (중복 방지)

사용 예
  python add_segment_shares.py             # dry-run — 매칭 목록만 출력
  python add_segment_shares.py --apply     # 매칭 목록 출력 → y/N confirm → 실제 PUT
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
COMPANY_ID     = "company_id"

# RSID 필터 — 빈 문자열 ""이면 모든 RSID. segment 가 어느 RSID 인지 모를 때 전체 검색 권장.
RSID = ""

# 본인 numeric loginId — `/segments` GET 후 owner.id 가 이 값과 일치하는 segment 만 처리.
# (AA API 의 ownerId 쿼리 파라미터는 지원 안 함 → 클라이언트 사이드 필터링)
OWN_LOGIN_ID = 000000001

# 이 키워드가 name 또는 description 에 substring 으로 포함된 segment 매칭 (case-insensitive)
# 같은 값을 AA API 의 `name` 쿼리 파라미터로도 보내서 server-side 사전 필터링 (회사 전체 22만 → 수십개로)
# 이 키워드들 모두 (AND) name 또는 description 에 substring 으로 포함된 segment 만 매칭.
# server-side `name` 필터는 KEYWORDS[0] 만 사용 (가장 specific 한 키워드를 앞에 둘 것).
# client-side 에서 나머지 키워드들도 다 매칭하는 segment 만 통과 (AND).
KEYWORDS: list[str] = [
    "[CAMPAIGN NAME]",
    # "& order",
    # "[part_name] US_",
    # "visit",
]

# owner.id 화이트리스트 (numeric loginId). 비어있으면 미사용.
# 예: [000000001, YOUR_LOGIN_ID] → 두 사람 owner segment 만
OWNER_ID_FILTER: list[int] = [
    # 000000001,   # user1_login  (Jonghyun Park)
    # YOUR_LOGIN_ID,   # user2_login    (User2 Name)
    # YOUR_LOGIN_ID,   # user3_login (User3 Name)
    # YOUR_LOGIN_ID,   # user4_login        (User4 Name)
    # YOUR_LOGIN_ID,   # user5_login    (User5 Name)
    # YOUR_LOGIN_ID,   # user6_login     (User6 Name)
    # YOUR_LOGIN_ID,   # user7_login  (User7 Name)
]

# owner.name (fullName) substring 매칭 (case-insensitive). 빈 리스트면 미사용.
# AA API 의 owner.login 은 안정적으로 안 채워져서 이메일 substring 은 못 씀.
# 대신 fullName 으로 매칭 — CSV (segments_matched_*.csv) 의 OwnerName 컬럼 보고 박기.
# 예: ["user1", "User 2"] → 두 fullName substring 매칭되는 owner 의 segment 만
OWNER_FULLNAME_INCLUDES: list[str] = [
    # "user1",
    # "User 2",
]

# 매칭 segment 중 이 id 들만 PUT 대상으로 좁힘. 빈 리스트면 매칭 전체 대상.
# 권장 워크플로우:
#   1) 첫 실행 (dry-run) → segments_matched_YYMMDD_HHMM.csv 생성
#   2) CSV 열어 share 추가할 segment 의 SegmentId 컬럼 값 복사 → 아래 박기
#   3) --apply 로 다시 실행 → 그것만 share 추가
# 한 줄에 하나씩 segment id 박기. 빈 줄 무시. # 으로 시작하면 주석 처리 (해당 id 제외).
# 큰따옴표·콤마 안 써도 됨 — 아래 자동 parse.
TARGET_SEGMENT_IDS_RAW = """
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder
segment_id_placeholder

"""

TARGET_SEGMENT_IDS: list[str] = [
    line.strip()
    for line in TARGET_SEGMENT_IDS_RAW.splitlines()
    if line.strip() and not line.strip().startswith("#")
]

# 콘솔에 매칭 결과 첫 N 개만 print (나머지는 CSV 만 — 회사 전체 검색 시 매칭 너무 많을 때)
PRINT_FIRST_N = 5

# shares 에 추가할 numeric loginId 리스트 (aa_user_id_*.csv 에서 lookup)
# 본인 + 추가 인원
SHARE_USER_IDS = [
    000000001,   # user1@company_name.com  (Jonghyun Park)
    YOUR_LOGIN_ID,   # user2@company_name.com    (User2 Name)
    YOUR_LOGIN_ID,   # user3@company_name.com (User3 Name)
    YOUR_LOGIN_ID,   # user4@company_name.com        (User4 Name)
    YOUR_LOGIN_ID,   # user5@company_name.com    (User5 Name)
    YOUR_LOGIN_ID,   # user6@company_name.com     (User6 Name)
    YOUR_LOGIN_ID,   # user7@company_name.com  (User7 Name)
    YOUR_LOGIN_ID, # user8_login
]

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

API_BASE = "https://analytics.adobe.io/api"
SCRIPT_DIR = Path(__file__).parent

# AA user 매핑 CSV — 상위 폴더의 aa_user_id_*.csv 자동 pick (loginId → login/email/fullName)
# AA API 가 segment 의 owner.name/login 을 안 채워서 (id 만) 이 CSV 로 enrich.
_AA_USER_CSV_CANDIDATES = sorted(SCRIPT_DIR.parent.glob("aa_user_id_*.csv"))
AA_USER_CSV = _AA_USER_CSV_CANDIDATES[-1] if _AA_USER_CSV_CANDIDATES else None


def load_auth_headers() -> tuple[dict, str]:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h = dict(ags.header) if isinstance(getattr(ags, "header", None), dict) else {}
    h_lower = {k.lower(): v for k, v in h.items()}
    api_key = h_lower.get("x-api-key")
    auth = h_lower.get("authorization")
    gcid = h_lower.get("x-proxy-global-company-id")
    if not (api_key and auth and gcid):
        raise RuntimeError("필수 헤더 누락 (api_key/authorization/x-proxy-global-company-id)")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


PAGE_LIMIT = 1000   # AA API max (한 페이지당 fetch 수)
MAX_PAGES  = 200    # 안전장치 — 회사 전체 segment 가 페이지로 200 곱하기 1000 = 20만 미만이라면 다 받음


def list_segments(headers: dict, gcid: str, rsid: str = "", name_filter: str = "") -> list[dict]:
    """`/segments` 전체 GET (`includeType=all` — 본인 + shared + templates 모두).
    name_filter 가 있으면 AA API 의 `name` 파라미터로 server-side substring 사전 필터.
    페이징은 dateranges_list 패턴 따라 MAX_PAGES 안전장치 + lastPage / 짧은 응답 break.
    aa_dateranges_list.py 참고."""
    out: list[dict] = []
    for page in range(MAX_PAGES):
        params = {
            "expansion": "definition,description,shares,tags,name,reportSuiteName,owner",
            "includeType": "all",   # default 는 본인 소유만 → all 명시 (남이 만들고 공유된 것도 포함)
            "limit": PAGE_LIMIT,
            "page": page,
        }
        if rsid:
            params["rsids"] = rsid
        if name_filter:
            params["name"] = name_filter   # AA API server-side substring filter on name
        url = f"{API_BASE}/{gcid}/segments"
        r = requests.get(url, headers=headers, params=params, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"GET /segments page {page} 실패: {r.status_code} {r.reason} — {r.text[:500]}")
        data = r.json()
        if isinstance(data, dict):
            rows = data.get("content") or []
            is_last = bool(data.get("lastPage"))
            total = data.get("totalElements")
        else:
            rows = data
            is_last = len(rows) < PAGE_LIMIT
            total = None
        if not rows:
            break
        out.extend(rows)
        if total is not None and page == 0:
            print(f"  totalElements: {total} (필터 전 회사 전체)")
        print(f"  page {page}: +{len(rows)} (누적 {len(out)})")
        if is_last or len(rows) < PAGE_LIMIT:
            break
    return out


def load_user_map(csv_path: Path | None) -> dict[int, dict]:
    """aa_user_id_*.csv 를 loginId(int) → {login, email, name} dict 로 로드.
    파일 없거나 컬럼 형식 다르면 빈 dict 반환."""
    if csv_path is None or not csv_path.exists():
        return {}
    m: dict[int, dict] = {}
    try:
        for r in csv.DictReader(open(csv_path, encoding="utf-8-sig")):
            try:
                lid = int(r["loginId"])
            except (ValueError, KeyError, TypeError):
                continue
            m[lid] = {
                "login": (r.get("login") or "").strip(),
                "email": (r.get("email") or "").strip(),
                "name":  (r.get("fullName") or "").strip(),
            }
    except Exception:
        return {}
    return m


def enrich_owner(segments: list[dict], user_map: dict[int, dict]) -> int:
    """각 segment 의 owner 객체의 빈 name/login 을 user_map 으로 채움.
    returns 보강된 segment 수."""
    if not user_map:
        return 0
    n = 0
    for s in segments:
        own = s.get("owner") or {}
        try:
            oid = int(own.get("id"))
        except (TypeError, ValueError):
            continue
        info = user_map.get(oid)
        if not info:
            continue
        changed = False
        if not own.get("name") and info["name"]:
            own["name"] = info["name"]; changed = True
        if not own.get("login") and info["login"]:
            own["login"] = info["login"]; changed = True
        if changed:
            s["owner"] = own
            n += 1
    return n


def filter_by_owner(segments: list[dict], own_login_id: int) -> list[dict]:
    """segment.owner.id == own_login_id 인 segment 만. 본인 owner 아닌 segment 는 PUT 시 403."""
    out = []
    for s in segments:
        owner = s.get("owner") or {}
        oid = owner.get("id") or owner.get("loginId") or owner.get("userId")
        try:
            if int(oid) == own_login_id:
                out.append(s)
        except (TypeError, ValueError):
            continue
    return out


def filter_by_owner_name_or_id(segments: list[dict],
                                fullname_includes: list[str],
                                id_whitelist: list[int]) -> list[dict]:
    """owner.name 에 fullname_includes 의 어떤 substring 도 매칭되거나, owner.id 가 id_whitelist
    안에 있으면 통과. 두 필터 다 비어있으면 전체 통과 (no filter).
    OR 매칭 — fullName substring 또는 id 화이트리스트 둘 중 하나만 맞으면 통과."""
    if not fullname_includes and not id_whitelist:
        return segments
    name_lower = [s.lower() for s in fullname_includes]
    id_set = set(id_whitelist)
    out = []
    for s in segments:
        owner = s.get("owner") or {}
        name = (owner.get("name") or "").lower()
        oid = owner.get("id") or owner.get("loginId") or owner.get("userId")
        try:
            oid_int = int(oid) if oid is not None else None
        except (TypeError, ValueError):
            oid_int = None
        if id_set and oid_int in id_set:
            out.append(s); continue
        if name_lower and any(kw in name for kw in name_lower):
            out.append(s); continue
    return out


def post_share(headers: dict, gcid: str, component_id: str, share_to_id: int,
               component_type: str = "segment", share_to_type: str = "user") -> dict:
    """`POST /componentmetadata/shares` — 단건 share 추가. 새 shareId 발급해서 응답.
    AA 의 segment shares 갱신은 `/segments/{id}` PUT 으로는 silently drop 되고
    이 endpoint 로만 실제 적용됨 (Adobe Analytics 2.x 공식 component sharing API)."""
    url = f"{API_BASE}/{gcid}/componentmetadata/shares"
    body = {
        "shareToId":     share_to_id,
        "shareToType":   share_to_type,
        "componentType": component_type,
        "componentId":   component_id,
    }
    r = requests.post(url, headers=headers, json=body, timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"POST share {component_id}+{share_to_id} 실패: {r.status_code} {r.reason} — {r.text[:500]}")
    return r.json()


def match_segments(segments: list[dict], keywords) -> list[dict]:
    """name 또는 description 에 keywords 의 모든 substring 이 다 포함된 segment 만 (AND, case-insensitive).
    keywords 가 str 면 단일 키워드, list 면 AND 매칭."""
    if isinstance(keywords, str):
        kws = [keywords.lower()]
    else:
        kws = [k.lower() for k in keywords]
    out = []
    for s in segments:
        text = ((s.get("name") or "") + " " + (s.get("description") or "")).lower()
        if all(kw in text for kw in kws):
            out.append(s)
    return out


def normalize_share_id(share) -> int | None:
    """share dict / int / str 어느 형태라도 numeric loginId 추출.
    AA 실제 응답 형식: {"shareId": "...", "shareToId": 200xxx, "shareToType": "user", ...}
    legacy / 기타 형식도 폴백 (id / loginId / userId)."""
    if isinstance(share, dict):
        sid = (share.get("shareToId")
               or share.get("id")
               or share.get("loginId")
               or share.get("userId"))
    else:
        sid = share
    try:
        return int(sid) if sid is not None else None
    except (ValueError, TypeError):
        return None


def merge_shares(existing: list, new_ids: list[int], component_id: str = "") -> tuple[list, list[int]]:
    """기존 shares 에 new_ids 추가 (중복 제거).
    AA 실제 share entry 형식: {"shareToId": <loginId>, "shareToType": "user",
                              "componentType": "segment", "componentId": "<sid>"}
    returns (merged_shares, actually_added_ids)."""
    existing_ids = {normalize_share_id(s) for s in (existing or [])}
    existing_ids.discard(None)
    to_add = [i for i in new_ids if i not in existing_ids]
    merged = list(existing or [])
    for i in to_add:
        entry = {
            "shareToId":   i,
            "shareToType": "user",
            "componentType": "segment",
        }
        if component_id:
            entry["componentId"] = component_id
        merged.append(entry)
    return merged, to_add


def main() -> int:
    parser = argparse.ArgumentParser(description="키워드 매칭 segment 에 shares 일괄 추가")
    parser.add_argument("--apply", action="store_true", help="실제 PUT 실행 (기본은 dry-run)")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    now = datetime.now()
    print(f"[{now:%Y-%m-%d %H:%M:%S}] segment share 일괄 추가 도구")
    print(f"  KEYWORDS                : {KEYWORDS}  (AND 매칭. server-side name = KEYWORDS[0])")
    print(f"  RSID 필터               : {RSID!r}  (빈 문자열 = 전체 RSID)")
    print(f"  OWNER_ID_FILTER         : {OWNER_ID_FILTER}")
    print(f"  OWNER_FULLNAME_INCLUDES : {OWNER_FULLNAME_INCLUDES}")
    print(f"  TARGET_SEGMENT_IDS      : {len(TARGET_SEGMENT_IDS)}개 (비어있으면 owner 필터 결과 전체)")
    print(f"  추가할 user id          : {SHARE_USER_IDS}")

    headers, gcid = load_auth_headers()

    # server-side name 필터로 22만 → 수십개로 사전 축소 (회사 전체에서 substring 일치 명만)
    server_filter_keyword = KEYWORDS[0] if KEYWORDS else ""
    print(f"\nGET /segments (RSID={RSID!r}, includeType=all, server-side name={server_filter_keyword!r}) ...")
    server_filtered = list_segments(headers, gcid, RSID, name_filter=server_filter_keyword)
    print(f"  server-side name 필터 후 {len(server_filtered)}개")

    # AA API 가 owner.name/login 안 채워서 aa_user_id_*.csv 로 loginId → name/login lookup 보강
    user_map = load_user_map(AA_USER_CSV)
    if user_map:
        n_enriched = enrich_owner(server_filtered, user_map)
        print(f"  owner enrich (lookup: {AA_USER_CSV.name}, {len(user_map)} users): {n_enriched}개 segment 보강")
    else:
        print(f"  ⚠️ aa_user_id CSV 못찾음 — owner.name/login 비어있을 수 있음 (find_user_id.py --all --csv ... 로 생성)")

    # client-side: name 또는 description 에 KEYWORDS 의 모든 substring 다 포함 (AND)
    keyword_matches = match_segments(server_filtered, KEYWORDS)
    print(f"  client-side AND 매칭 ({KEYWORDS}): {len(keyword_matches)}개")

    # owner 필터 — fullName substring 또는 id 화이트리스트 (OR)
    before_owner = list(keyword_matches)   # owner 필터 전 보존 (진단용)
    if OWNER_FULLNAME_INCLUDES or OWNER_ID_FILTER:
        before = len(keyword_matches)
        keyword_matches = filter_by_owner_name_or_id(keyword_matches, OWNER_FULLNAME_INCLUDES, OWNER_ID_FILTER)
        print(f"  owner 필터 (name∈{OWNER_FULLNAME_INCLUDES} OR id∈{OWNER_ID_FILTER}): {before} → {len(keyword_matches)}개")
    else:
        print(f"  owner 필터 없음 (OWNER_FULLNAME_INCLUDES / OWNER_ID_FILTER 둘 다 비어있음)")

    if not keyword_matches:
        print("\n❌ 매칭 segment 없음 — abort.")
        # owner 필터 적용 전엔 있었는데 필터 후 0 → owner 형식 진단 출력
        if before_owner and (OWNER_FULLNAME_INCLUDES or OWNER_ID_FILTER):
            print(f"\n🔍 owner 필터 적용 전 {len(before_owner)}개 — owner 형식 진단 (첫 5개 owner raw):")
            for s in before_owner[:5]:
                own = s.get("owner") or {}
                print(f"   - segment={s.get('id','?')}  name={(s.get('name','') or '')[:40]:40}  owner={own}")
            unique_names = sorted({(s.get("owner") or {}).get("name","") for s in before_owner})
            unique_ids = sorted({(s.get("owner") or {}).get("id") for s in before_owner if (s.get("owner") or {}).get("id") is not None})
            print(f"\n   고유 owner.name 값들 ({len(unique_names)}개): {unique_names[:20]}")
            print(f"   고유 owner.id 값들   ({len(unique_ids)}개): {unique_ids[:20]}")
        print("\n   확인: (1) RSID (현재값: {!r})  /  RSID=\"\" 로 두면 모든 RSID".format(RSID))
        print("        (2) KEYWORDS 의 substring 들이 정확한지 (모두 AND 매칭, case-insensitive)")
        print("        (3) OWNER_FULLNAME_INCLUDES / OWNER_ID_FILTER 너무 좁지 않은지 (위 진단 참고)")
        return 1

    # owner 분류 — 본인 / 다른 사람
    mine = []
    others = []
    for s in keyword_matches:
        owner = s.get("owner") or {}
        oid = owner.get("id") or owner.get("loginId") or owner.get("userId")
        try:
            if int(oid) == OWN_LOGIN_ID:
                mine.append(s)
            else:
                others.append(s)
        except (TypeError, ValueError):
            others.append(s)

    # 매칭 결과 항상 CSV 로 저장 (회사 전체 검색 시 콘솔 잘림 대비)
    ts = now.strftime("%y%m%d_%H%M")
    csv_path = SCRIPT_DIR / f"segments_matched_{ts}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Flag", "SegmentId", "Name", "RSID",
                    "OwnerId", "OwnerLogin", "OwnerName", "Modified",
                    "Description", "Tags", "ShareCount", "ShareIds"])
        for flag, s in [("mine", x) for x in mine] + [("other", x) for x in others]:
            own = s.get("owner") or {}
            shares = s.get("shares") or []
            share_ids = "|".join(str(normalize_share_id(sh)) for sh in shares if normalize_share_id(sh))
            # tags — string list 또는 dict list ({id,name}) 둘 다 대응 (AA API 응답 형식 가변)
            tag_names = [
                (t.get("name") or t.get("id") or "") if isinstance(t, dict) else str(t)
                for t in (s.get("tags") or [])
            ]
            w.writerow([
                flag, s.get("id", ""), s.get("name", ""), s.get("rsid", ""),
                own.get("id", ""), own.get("login", ""), own.get("name", ""),
                s.get("modified", ""), s.get("description", ""),
                "|".join(t for t in tag_names if t),
                len(shares), share_ids,
            ])
    print(f"\n📝 매칭 결과 CSV: {csv_path.name} ({len(keyword_matches)}행)")

    # 콘솔 출력 — first-N 만
    print(f"\n--- 매칭된 segment 목록 (콘솔: 첫 {PRINT_FIRST_N}개 / 전체는 CSV) ---")
    print(f"  본인 owner (id={OWN_LOGIN_ID}): {len(mine)}개  /  다른 사람 owner: {len(others)}개")
    print(f"{'#':>4}  {'flag':6}  {'segment id':28}  {'owner_name':22}  {'owner_login':28}  {'owner_id':10}  {'rsid':14}  name")
    print("-" * 175)
    rows_to_show = [("✓mine", s) for s in mine] + [("·other", s) for s in others]
    for i, (flag, s) in enumerate(rows_to_show[:PRINT_FIRST_N], 1):
        sid = s.get("id", "?")
        rs  = s.get("rsid", "?")
        nm  = s.get("name", "(no name)")[:50]
        own = s.get("owner") or {}
        own_name  = (own.get("name") or "?")[:20]
        own_login = (own.get("login") or "?")[:26]
        own_id    = str(own.get("id") or "?")[:10]
        print(f"{i:>4}  {flag:6}  {sid:28}  {own_name:22}  {own_login:28}  {own_id:10}  {rs:14}  {nm}")
    if len(rows_to_show) > PRINT_FIRST_N:
        print(f"  ... +{len(rows_to_show) - PRINT_FIRST_N}개 (CSV 참고)")

    # TARGET_SEGMENT_IDS 로 좁히기 — 비어있으면 매칭 전체 대상
    all_pool = mine + others
    if TARGET_SEGMENT_IDS:
        target_set = set(TARGET_SEGMENT_IDS)
        matches = [s for s in all_pool if s.get("id") in target_set]
        print(f"\n🎯 TARGET_SEGMENT_IDS 필터 ({len(TARGET_SEGMENT_IDS)}개 지정) → 매칭 안에서 {len(matches)}개 좁혀짐")
        not_found = [sid for sid in TARGET_SEGMENT_IDS if sid not in {s.get('id') for s in all_pool}]
        if not_found:
            print(f"   ⚠️ 매칭 안에서 찾지 못한 id: {not_found}")
    else:
        matches = all_pool
        print(f"\n⚠️ TARGET_SEGMENT_IDS 비어있음 — 매칭 전체 {len(matches)}개 대상")
        print(f"   권장: 위 CSV ({csv_path.name}) 보고 share 추가할 segment id 만 선별 후")
        print(f"        코드 상단 TARGET_SEGMENT_IDS 에 박고 다시 실행하면 안전")

    # 본인 owner 아닌 segment 가 대상에 있으면 안내 (PUT 시 admin 권한 없으면 403)
    n_other_in_matches = sum(1 for s in matches if s in others)
    if n_other_in_matches > 0:
        print(f"\n⚠️ 대상 {len(matches)}개 중 본인 owner 아닌 segment {n_other_in_matches}개 — admin 권한 있으면 PUT 통과, 없으면 403")

    if not matches:
        print("\n❌ 대상 segment 없음 — abort.")
        return 1

    # 변경 미리보기 — 각 segment 에 실제 추가될 ID 출력
    print(f"\n--- 변경 미리보기 (segment 별 추가될 user id) ---")
    plans: list[tuple[dict, list, list[int]]] = []
    for s in matches:
        merged, added = merge_shares(s.get("shares") or [], SHARE_USER_IDS, s.get("id", ""))
        plans.append((s, merged, added))
        sid = s.get("id", "?")
        nm  = s.get("name", "(no name)")[:50]
        own = s.get("owner") or {}
        own_name = (own.get("name") or "?")[:18]
        if added:
            print(f"  + {sid}  [{own_name:18}]  {nm:<52}  추가: {added}")
        else:
            print(f"  · {sid}  [{own_name:18}]  {nm:<52}  이미 모두 share 됨 (skip)")

    n_to_change = sum(1 for _, _, added in plans if added)
    print(f"\n--- 합계 ---")
    print(f"  매칭 segment    : {len(matches)}개")
    print(f"  share 추가 대상 : {n_to_change}개 (나머지는 이미 share 되어있어 skip)")

    if n_to_change == 0:
        print("\nℹ️ 변경 대상 없음 — PUT 생략.")
        return 0

    if not args.apply:
        print("\nℹ️ Dry-run 모드 — 실제 PUT 안 함. 적용하려면 --apply")
        return 0

    # 두 번째 안전장치 — 키보드 confirm
    print(f"\n⚠️ 위 {n_to_change}개 segment 에 share 를 추가합니다. 진행하시겠습니까?")
    ans = input("   진행하려면 'y' 또는 'yes' 입력: ").strip().lower()
    if ans not in ("y", "yes"):
        print("   취소됨.")
        return 1

    # 실행 — segment 별로 신규 user id 마다 POST /componentmetadata/shares 단건 호출.
    # ( /segments/{id} PUT 은 200 OK 반환해도 shares 갱신이 silently drop 됨 — Adobe 의
    #   component sharing 은 별도 endpoint /componentmetadata/shares 에서만 실제 적용 )
    print(f"\nPOST /componentmetadata/shares 시작 ...")
    n_ok = 0
    n_fail = 0
    for s, _, added in plans:
        if not added:
            continue
        sid = s.get("id")
        nm  = s.get("name", "(no name)")[:50]
        ok_n   = 0
        fail_n = 0
        for uid in added:
            try:
                post_share(headers, gcid, sid, uid)
                ok_n += 1
            except Exception as e:
                fail_n += 1
                print(f"  ✗ {sid}  +{uid}  ERROR: {e}")
        if fail_n == 0:
            n_ok += 1
            print(f"  ✓ {sid}  {nm:<52}  +{ok_n}명 share 추가")
        else:
            n_fail += 1
            print(f"  △ {sid}  {nm:<52}  성공 {ok_n}명 / 실패 {fail_n}명")

    print(f"\n--- 결과 ---")
    print(f"  성공 : {n_ok}")
    print(f"  실패 : {n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
