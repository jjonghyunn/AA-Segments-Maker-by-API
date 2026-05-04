# find_user_id.py
# 2026-05-04  Jonghyun Park w/ Claude
"""
AA company의 사용자 검색 → numeric loginId + IMS user ID + 기본 정보 출력.

본격 AA_segment_maker_260504.py / test_create_segment.py에서 owner.id로 쓸
numeric loginId를 찾기 위한 헬퍼.

사용 예:
  python find_user_id.py --ims-user-id "B22...e"             # IMS user ID 정확 매칭 (가장 안전)
  python find_user_id.py --login user1                    # login 필드 substring
  python find_user_id.py --email user1@company_name    # email 필드 substring
  python find_user_id.py --name "user1"                   # fullName substring
  python find_user_id.py --all                               # 전체 목록 (company_id 사용자 전부)
  python find_user_id.py --all --csv users.csv               # 전체 + CSV dump
  python find_user_id.py --email user1 --csv me.csv       # 검색 결과를 CSV로도 저장

검색 옵션은 mutually exclusive (한 번에 한 가지만).
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분 — 다른 사람이 쓸 때 여기만 수정
# ════════════════════════════════════════════════════════════════════

# Adobe Developer Console에서 받은 OAuth Server-to-Server 자격증명 json 경로.
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"

# AA 회사(login company) ID. 어도비 UI URL의 `so:xxxxx` 부분.
COMPANY_ID = "company_id"

# ════════════════════════════════════════════════════════════════════
# 내부 사용 — 보통 수정 불필요
# ════════════════════════════════════════════════════════════════════
# /users 엔드포인트 페이지네이션 설정
PAGE_SIZE = 400          # 한 페이지당 최대 사용자 수 (Adobe API max=1000)
MAX_PAGES = 100          # 페이지 순회 상한 (400 × 100 = 40,000명까지 커버)


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
        raise RuntimeError("필수 헤더 누락 (api_key/authorization/x-proxy-global-company-id)")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def _iter_users(headers: dict, gcid: str):
    """AA /users 엔드포인트 페이지 순회 generator."""
    url = f"https://analytics.adobe.io/api/{gcid}/users"
    page = 0
    while page < MAX_PAGES:
        r = requests.get(
            url,
            headers=headers,
            params={"limit": PAGE_SIZE, "page": page},
            timeout=120,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"GET /users 실패: {r.status_code} {r.reason} — {r.text[:300]}"
            )
        body = r.json()
        items = body.get("content") if isinstance(body, dict) else body
        if not items:
            break
        for u in items:
            yield u
        if isinstance(body, dict) and body.get("lastPage", True):
            break
        page += 1


def _matches(user: dict, args: argparse.Namespace) -> bool:
    if args.ims_user_id:
        return (user.get("imsUserId") or "").lower() == args.ims_user_id.lower().strip()
    if args.login:
        return args.login.lower() in (user.get("login") or "").lower()
    if args.email:
        return args.email.lower() in (user.get("email") or "").lower()
    if args.name:
        return args.name.lower() in (user.get("fullName") or "").lower()
    return False  # --all은 별도 처리


def _print_table(users: list[dict]) -> None:
    if not users:
        print("  (매칭 사용자 없음)")
        return
    rows = []
    for u in users:
        rows.append(
            [
                str(u.get("loginId") or ""),
                (u.get("email") or "")[:60],
                (u.get("fullName") or "")[:30],
                (u.get("imsUserId") or "")[:60],
            ]
        )
    cols = ["loginId", "email", "fullName", "imsUserId"]
    widths = [max(len(cols[i]), max(len(r[i]) for r in rows)) for i in range(len(cols))]
    sep = "  "
    print(sep.join(c.ljust(w) for c, w in zip(cols, widths)))
    print(sep.join("-" * w for w in widths))
    for r in rows:
        print(sep.join(c.ljust(w) for c, w in zip(r, widths)))


def _stamp_path(path: Path, timestamp: str) -> Path:
    """foo.csv → foo_YYMMDD_HHMM.csv (확장자 앞에 timestamp suffix 삽입)."""
    return path.with_name(f"{path.stem}_{timestamp}{path.suffix}")


def _dump_csv(users: list[dict], path: Path, requested_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(
            ["RequestedAt", "loginId", "login", "email", "fullName", "imsUserId", "disabled"]
        )
        for u in users:
            w.writerow(
                [
                    requested_at,
                    u.get("loginId", ""),
                    u.get("login", ""),
                    u.get("email", ""),
                    u.get("fullName", ""),
                    u.get("imsUserId", ""),
                    u.get("disabled", ""),
                ]
            )
    print(f"\nCSV dump : {path} ({len(users)}명)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AA company 사용자 검색 → numeric loginId 출력"
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--ims-user-id", dest="ims_user_id", help="IMS user ID 정확 매칭 (예: 'B22...e')")
    g.add_argument("--login", help="login 필드 substring 매칭")
    g.add_argument("--email", help="email 필드 substring 매칭")
    g.add_argument("--name", help="fullName 필드 substring 매칭")
    g.add_argument("--all", action="store_true", help="전체 사용자 출력 (company_id 모든 사용자)")
    parser.add_argument(
        "--csv",
        help="결과를 CSV로도 저장 (검색/전체 어느 모드든 사용 가능)",
    )
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    headers, gcid = _load_auth_headers()

    print(f"[{requested_at}] GET /users  (company={COMPANY_ID})  ...")
    matched: list[dict] = []
    total = 0
    for u in _iter_users(headers, gcid):
        total += 1
        if args.all or _matches(u, args):
            matched.append(u)

    print(f"총 {total}명 스캔 → {len(matched)}명 매칭\n")
    _print_table(matched)

    if args.csv:
        out_path = _stamp_path(Path(args.csv).resolve(), timestamp)
        _dump_csv(matched, out_path, requested_at)

    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
