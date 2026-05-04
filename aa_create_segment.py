# test_create_segment.py
# 2026-05-04  Jonghyun Park w/ Claude
"""
MST Global RSID에 테스트 세그먼트 1개 생성.
결과를 test_result_YYMMDD_HHMM.csv로 저장.

본격 AA_segment_maker_260504.py 작성 전 사전 검증용 (소규모 1건 테스트).
실행 후 어도비 UI에서 확인 → test_delete_segment.py로 정리.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분 — 다른 사람이 쓸 때 여기만 수정
# ════════════════════════════════════════════════════════════════════

# ─── 1) 인증 (한 번 셋업 후 거의 안 바뀜) ──────────────────────────
# Adobe Developer Console에서 받은 OAuth Server-to-Server 자격증명 json 경로.
# 본인 OneDrive 또는 로컬 안전한 위치에 저장.
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"

# AA 회사(login company) ID. 어도비 UI URL의 `so:xxxxx` 부분과 동일.
# 예: https://experience.adobe.com/#/@company_name/so:company_id/...
COMPANY_ID = "company_id"

# ─── 2) 본인 식별 (사람마다 다름 — segment owner를 본인으로 잡기 위함) ──
# 우선순위: OWNER_ID > OWNER_IMS_USER_ID > OWNER_LOGIN
#
#  · OWNER_ID          : AA 내부 numeric loginId 직접 지정 (예: 000000001).
#                        한 번 알아낸 후 박아두면 매 실행마다 lookup 안 해도 빨라짐.
#                        find_user_id.py 실행해서 알아낼 수 있음.
#
#  · OWNER_IMS_USER_ID : Admin Console URL의 IMS user ID. 가장 안전 (정확 매칭).
#                        본인 클릭 → URL의 `/users/{이부분}` 복사
#                        형식: B22...@aeab47c7....e
#
#  · OWNER_LOGIN       : login/email/fullName에 substring 매칭 (동명이인 위험).
#                        다른 두 개 비웠을 때 fallback으로만 사용 권장.
#
# 셋 다 비우면 owner 미지정 → technical account 소유로 저장됨 (본인 owner X).
OWNER_ID: int | None = None
OWNER_IMS_USER_ID: str = "YOUR_IMS_USER_ID"
OWNER_LOGIN: str = "user1_login"

# ─── 3) 만들 segment의 메타정보 ────────────────────────────────────
# RSID = 어떤 report suite에 segment를 등록할지.
# site_code → RSID 매핑은 site_registry.py 참고.
# 예시: rsid_placeholder (MST Global), rsid_placeholder (한국), rsid_placeholder (미국)
RSID = "rsid_placeholder"

SEGMENT_NAME = "_test_aa_segment_maker_260504"     # AA UI에 보일 이름
SEGMENT_DESCRIPTION = "API 테스트용"                # AA UI 설명 필드
SEGMENT_TAGS = ["_test", "generated"]              # AA UI 태그 (폴더 분류용)

# ─── 4) Segment 조건 정의 (가장 자주 바뀌는 부분) ───────────────────
#
# 트리 구조:
#   definition
#   ├── func: "segment"          ◀ 고정 (빠지면 Adobe가 ImproperFunctionCall 거부)
#   ├── version: [1,0,0]
#   └── container
#       ├── func: "container"    ◀ 고정
#       ├── context: ◀◀◀ ① 스코프 (Hit/Visit/Visitor 중 선택)
#       └── pred
#           ├── func:    ◀◀◀ ② 조건 종류 (equals/contains/exists/...)
#           ├── str/num/list:  ◀◀◀ ④ 비교 값 (조건 종류에 따라 키 다름)
#           └── val
#               ├── func: "attr" or "event"  (차원이면 attr, 메트릭이면 event)
#               └── name: ◀◀◀ ③ 변수 이름 ("variables/page" 등)
#
# ─────────────────────────────────────────────────────────────
# ① 스코프 (container.context):
#   "hits"     — Hit 단위 (한 페이지뷰/이벤트)
#   "visits"   — Visit 단위 (세션 전체)
#   "visitors" — Visitor 단위 (방문자 전체)
#
# ─────────────────────────────────────────────────────────────
# ② 조건 종류 (pred.func) — 어도비 UI 드롭다운 라벨 ↔ API func 매핑:
#
#   ┌─ 어도비 UI 라벨 ──────────────┬─ API func 직렬화 형태 ────────────────────┬─ 보조 필드 ──────┐
#   │  equals                       │ streq                                       │ "str": "값"        │
#   │  does not equal               │ streq                + without wrapper      │ "str": "값"        │
#   │  contains                     │ contains                                    │ "str": "값"        │
#   │  does not contain             │ contains             + without wrapper      │ "str": "값"        │
#   │  contains all of              │ contains-all-of                             │ "list": ["값",...] │
#   │  does not contain all of      │ contains-all-of      + without wrapper      │ "list": ["값",...] │
#   │  contains any of              │ contains-any-of                             │ "list": ["값",...] │
#   │  does not contain any of      │ contains-any-of      + without wrapper      │ "list": ["값",...] │
#   │  equals any of                │ streq-in                                    │ "list": ["값",...] │
#   │  does not equal any of        │ streq-in             + without wrapper      │ "list": ["값",...] │
#   │  starts with                  │ starts-with                                 │ "str": "값"        │
#   │  does not start with          │ starts-with          + without wrapper      │ "str": "값"        │
#   │  ends with                    │ ends-with                                   │ "str": "값"        │
#   │  does not end with            │ ends-with            + without wrapper      │ "str": "값"        │
#   │  matches (정규식)             │ matches-regex                               │ "regex": "패턴"    │
#   │  does not match               │ matches-regex        + without wrapper      │ "regex": "패턴"    │
#   │  exists                       │ exists                                      │ (없음)             │
#   │  does not exist               │ exists               + without wrapper      │ (없음)             │
#   └───────────────────────────────┴─────────────────────────────────────────────┴────────────────────┘
#
#   ※ "does not ~" 계열은 Adobe UI에서 직렬화 시 `without` wrapper로 부정 처리:
#        {"func": "without", "pred": { (positive 조건 그대로) }}
#
#   숫자 비교 (메트릭): eq, ne, gt, ge, lt, le → "num": 숫자
#   메트릭 발생 여부:   event-exists           → "evt": {...} (val 아닌 evt 키)
#   조합:              and / or               → "preds": [{...}, {...}]
#                      not                    → "pred": {...} (단수, 단순 부정 — UI에서는 잘 안 씀)
#
#   ※ 잘 안 알려진 함수명은 _probe_segment.py 또는 GET /segments/{id}로 기존 segment 정의 보고 학습.
#   ※ example_segment_campaign_main_page.py에 실제 segment 예시 있음 (or/and/without/contains-any-of 모두 포함).
#
# ─────────────────────────────────────────────────────────────
# ③ 변수 이름 (val.name):
#   차원:   "variables/page", "variables/evar1"~"variables/evar250",
#           "variables/prop1"~"variables/prop75",
#           "variables/sitesection", "variables/country",
#           "variables/marketingchannel", "variables/referrer", ...
#   메트릭: "metrics/revenue", "metrics/orders", "metrics/event25", ...
#
# ─────────────────────────────────────────────────────────────
# ④ 비교 값 (보조 필드) — pred.func 종류에 따라 키와 타입이 다름:
#   "str": "값"           — streq, contains, starts-with, ends-with, matches-regex
#   "list": ["값1","값2"] — streq-in, contains-any, contains-all 계열
#   "num": 숫자           — eq, gt, lt 등 숫자 비교
#   "regex": "패턴"       — matches-regex (정규식)
#   (없음)                — exists, not-exists
#
# ─────────────────────────────────────────────────────────────
# 예시: Page contains "campaign_name" (스크린샷의 segment 재현)
#   "context": "hits",
#   "pred": {
#       "func": "contains",
#       "val": {"func": "attr", "name": "variables/page"},
#       "str": "campaign_name",
#       "description": "Page contains campaign_name"
#   }
#
# 더 많은 예시는 README.md "Segment 조건 빠른 참조" 섹션 참고.
SEGMENT_DEFINITION = {
    "func": "segment",
    "container": {
        "func": "container",
        "context": "hits",                                          # ← ① 여기서 스코프 변경
        "pred": {
            "func": "streq",                                        # ← ② 여기서 조건 종류 변경
            "val": {"func": "attr", "name": "variables/page"},      # ← ③ 여기서 변수 이름 변경
            "str": "home",                                          # ← ④ 여기서 비교값 변경 (str/list/num 등)
            "description": "Page",
        },
    },
    "version": [1, 0, 0],
}

# ════════════════════════════════════════════════════════════════════
# 내부 사용 — 보통 수정 불필요
# ════════════════════════════════════════════════════════════════════
OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_CSV_PREFIX = "test_result_"

# AA UI 편집 화면 URL 템플릿. tenant(@company_name)와 company(so:company_id)는
# auth json의 org_id에 묶여있어서 보통 안 바뀜.
UI_URL_TEMPLATE = (
    "https://experience.adobe.com/#/@company_name/so:company_id/"
    "analytics/spa/#/components/segments/edit/{seg_id}"
)


# ─────────────────────────────────────────────────────────────
def _load_auth_headers() -> tuple[dict, str]:
    """aanalytics2로 토큰 발급 → POST 헤더 + global company id 반환."""
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)

    h = dict(ags.header) if isinstance(getattr(ags, "header", None), dict) else {}
    h_lower = {k.lower(): v for k, v in h.items()}

    api_key = h_lower.get("x-api-key")
    auth = h_lower.get("authorization")
    gcid = h_lower.get("x-proxy-global-company-id")

    if not (api_key and auth and gcid):
        raise RuntimeError(
            f"필수 헤더 누락: api_key={bool(api_key)}, "
            f"auth={bool(auth)}, gcid={bool(gcid)}"
        )

    headers = {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return headers, gcid


def _lookup_owner_id(
    headers: dict,
    gcid: str,
    *,
    ims_user_id: str = "",
    login_sub: str = "",
) -> int:
    """
    AA /users 페이지 순회 → 사용자 매칭 후 numeric loginId 반환.
      - ims_user_id 주어지면 imsUserId 필드와 정확 일치 매칭 (대소문자 무시)
      - 아니면 login_sub로 login/email/fullName substring 매칭
      - 매칭 0명/2명+ → 에러 (직접 OWNER_ID 박도록 유도)
    """
    if not (ims_user_id or login_sub):
        raise ValueError("ims_user_id 또는 login_sub 중 하나는 필요합니다.")

    url = f"https://analytics.adobe.io/api/{gcid}/users"
    target_ims = ims_user_id.lower().strip()
    sub = login_sub.lower().strip()
    matches: list[dict] = []
    page = 0
    while page < 50:  # limit=400 × 50page = 20,000명 상한
        r = requests.get(
            url, headers=headers, params={"limit": 400, "page": page}, timeout=120
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"GET /users 실패: {r.status_code} {r.reason} — {r.text[:300]}\n"
                f"  → 권한 없으면 OWNER_ID에 numeric loginId를 직접 지정하세요."
            )
        body = r.json()
        items = body.get("content") if isinstance(body, dict) else body
        if not items:
            break
        for u in items:
            ims_field = (u.get("imsUserId") or "").lower()
            login_str = u.get("login") or ""
            email = u.get("email") or ""
            full_name = u.get("fullName") or ""

            hit = False
            if target_ims:
                if ims_field == target_ims:
                    hit = True
            elif sub:
                haystack = f"{login_str} {email} {full_name}".lower()
                if sub in haystack:
                    hit = True

            if hit:
                matches.append(
                    {
                        "loginId": u.get("loginId"),
                        "login": login_str,
                        "email": email,
                        "fullName": full_name,
                        "imsUserId": u.get("imsUserId") or "",
                    }
                )
        if isinstance(body, dict) and body.get("lastPage", True):
            break
        page += 1

    mode = f"imsUserId='{ims_user_id}'" if target_ims else f"login substring='{login_sub}'"
    if not matches:
        raise RuntimeError(f"{mode} 매칭 사용자 0명")
    if len(matches) > 1:
        msg = f"{mode} 매칭 {len(matches)}명 — OWNER_ID 직접 지정 필요:\n"
        for m in matches[:10]:
            msg += f"  - loginId={m['loginId']:>10}  {m['email']}  ({m['fullName']})\n"
        if len(matches) > 10:
            msg += f"  ... +{len(matches) - 10}명 더\n"
        raise RuntimeError(msg)

    m = matches[0]
    print(
        f"  matched owner: loginId={m['loginId']}  {m['email']}  ({m['fullName']})"
        f"  imsUserId={m['imsUserId']}"
    )
    return int(m["loginId"])


def _build_payload(owner_id: int | None) -> dict:
    p: dict = {
        "name": SEGMENT_NAME,
        "description": SEGMENT_DESCRIPTION,
        "rsid": RSID,
        "definition": SEGMENT_DEFINITION,
        "tags": SEGMENT_TAGS,
    }
    if owner_id is not None:
        p["owner"] = {"id": owner_id}
    return p


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MST Global RSID에 테스트 세그먼트 1개 생성 (기본 dry-run)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 POST 수행. 없으면 lookup + payload 출력만 (dry-run)",
    )
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{requested_at}] AA segment maker — test  ({'APPLY' if args.apply else 'DRY-RUN'})")
    print(f"  Company : {COMPANY_ID}")
    print(f"  RSID    : {RSID}")
    print(f"  Name    : {SEGMENT_NAME}")
    print()

    headers, gcid = _load_auth_headers()

    # Owner 확정 — 우선순위: OWNER_ID > OWNER_IMS_USER_ID > OWNER_LOGIN
    if OWNER_ID is not None:
        owner_id: int | None = OWNER_ID
        print(f"  Owner    : {owner_id} (config 직접 지정)")
    elif OWNER_IMS_USER_ID:
        print(f"  resolving owner by imsUserId='{OWNER_IMS_USER_ID}' ...")
        owner_id = _lookup_owner_id(headers, gcid, ims_user_id=OWNER_IMS_USER_ID)
    elif OWNER_LOGIN:
        print(f"  resolving owner by login substring '{OWNER_LOGIN}' ...")
        owner_id = _lookup_owner_id(headers, gcid, login_sub=OWNER_LOGIN)
    else:
        owner_id = None
        print("  Owner    : (미지정 → technical account 소유로 저장됨)")
    print()

    endpoint = f"https://analytics.adobe.io/api/{gcid}/segments"
    payload = _build_payload(owner_id)

    print(f"POST {endpoint}")
    print("Payload:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print()

    if not args.apply:
        print("DRY-RUN — 실제 POST 안 함. 위 내용(특히 owner) 확인 후 --apply 추가해서 다시 실행.")
        print(f"  python {Path(__file__).name} --apply")
        return 0

    r = requests.post(endpoint, headers=headers, json=payload, timeout=60)

    seg_id = ""
    error = ""
    ui_url = ""
    if r.status_code in (200, 201):
        data = r.json()
        seg_id = data.get("id", "")
        ui_url = UI_URL_TEMPLATE.format(seg_id=seg_id) if seg_id else ""
        owner = data.get("owner", {})
        owner_id = owner.get("id") if isinstance(owner, dict) else owner

        print(f"OK status   : {r.status_code} {r.reason}")
        print(f"   segment id: {seg_id}")
        print(f"   owner     : {owner_id if owner_id is not None else '(N/A)'}")
        print(f"   rsid      : {data.get('rsid')}")
        print(f"   tags      : {SEGMENT_TAGS}")
        print(f"   UI URL    : {ui_url}")
    else:
        error = r.text[:500]
        print(f"FAIL status : {r.status_code} {r.reason}")
        print(f"   error    : {error}")

    out_csv = OUTPUT_DIR / f"{RESULT_CSV_PREFIX}{timestamp}.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(
            ["RequestedAt", "Name", "SegmentId", "RSID", "Status", "Url", "Error"]
        )
        w.writerow(
            [
                requested_at,
                SEGMENT_NAME,
                seg_id,
                RSID,
                f"{r.status_code} {r.reason}",
                ui_url,
                error,
            ]
        )
    print(f"\nresult CSV : {out_csv}")

    return 0 if seg_id else 1


if __name__ == "__main__":
    sys.exit(main())
