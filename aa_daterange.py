# aa_daterange.py
# 2026-05-08  Jonghyun Park w/ Claude
"""
Adobe Analytics Date Range 컴포넌트 생성·갱신 도구.

DATERANGE_ID 가 비어있으면 새로 생성 (POST /dateranges).
DATERANGE_ID 가 채워져 있으면 기존 것 갱신 (PUT /dateranges/{id}).

갱신 모드는 GET 으로 기존 정의를 먼저 조회해서 owner/tags/description 보존하고,
변경 대상(NEW_NAME / NEW_DEFINITION) 만 덮어씀. 서버 생성 메타(modified/createDate 등) 는 자동 제외.

dry-run 기본. --apply 로 실제 실행.

사용:
  python aa_daterange.py            # dry-run (payload 출력만)
  python aa_daterange.py --apply    # 실제 POST 또는 PUT

definition 형식:
  "YYYY-MM-DDTHH:MM:SS/YYYY-MM-DDTHH:MM:SS"  (start/end ISO 한 줄, 슬래시 구분)
  예) "2026-05-01T00:00:00/2026-05-31T23:59:59"

⚠️ owner 가 본인이 아닌 Date Range 를 PUT 하면 403 나올 수 있음 (admin 권한 필요).
   GET 응답의 owner 를 dry-run 에서 출력하니 PUT 전에 본인 ID 인지 확인할 것.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"

# ─── 모드 ──────────────────────────────────────────────────────────
# 비워두면 새로 생성 (POST). 기존 ID 채우면 그 ID 갱신 (PUT).
# Workspace UI URL `.../components/dateRanges/edit/{이부분}` 의 24자리 hex.
DATERANGE_ID = ""

# ─── 새로 적용할 값 ────────────────────────────────────────────────
# Workspace UI 에 보일 라벨
NEW_NAME = "My Date Range"
# 빈 문자열이면 갱신 모드에서 기존 description 유지. 명시 변경하려면 채울 것.
NEW_DESCRIPTION = ""
# 날짜 범위 — ISO 형식 "START/END" 한 줄.
# 시작은 보통 00:00:00, 끝은 23:59:59 (Adobe 관례). 하루 단위로 끝내려면 다음날 00:00:00 도 가능.
NEW_DEFINITION = "2026-05-01T00:00:00/2026-05-31T23:59:59"

# ─── 생성 모드 전용 필드 (DATERANGE_ID 가 비어있을 때만 사용) ──
# numeric loginId. find_user_id.py 로 확인 가능. 0 이면 GET /users/me 자동 lookup 시도.
NEW_OWNER_ID = 0
NEW_TAGS: list[str] = []

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent

# 갱신 시 PUT body 에 보존할 클라이언트 설정 가능 필드 (서버 메타 제외)
_KEEP_FIELDS_ON_UPDATE = {"id", "name", "description", "definition", "owner", "tags"}


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


def _get_existing(headers: dict, gcid: str, dr_id: str) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/dateranges/{dr_id}"
    r = requests.get(url, headers=headers,
                     params={"expansion": "definition,ownerFullName,modified,tags"},
                     timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"GET {dr_id} failed: {r.status_code} {r.text[:500]}")
    return r.json()


def _lookup_my_user_id(headers: dict, gcid: str) -> int | None:
    """GET /users/me 로 본인 loginId 조회. 권한 없으면 None 반환."""
    url = f"https://analytics.adobe.io/api/{gcid}/users/me"
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None
    return r.json().get("loginId")


def _build_create_body(headers: dict, gcid: str) -> dict:
    body = {
        "name": NEW_NAME,
        "description": NEW_DESCRIPTION,
        "definition": NEW_DEFINITION,
        "tags": NEW_TAGS,
    }
    owner_id = NEW_OWNER_ID
    if not owner_id:
        looked = _lookup_my_user_id(headers, gcid)
        if looked:
            owner_id = looked
            print(f"  (auto) owner_id = {owner_id} from /users/me")
        else:
            print("  ⚠ NEW_OWNER_ID 가 0 이고 /users/me 권한도 없음 — owner 생략 (Adobe 가 토큰 owner 로 자동 부여)")
    if owner_id:
        body["owner"] = {"id": owner_id}
    return body


def _build_update_body(existing: dict) -> dict:
    """기존 정의에서 클라이언트 필드만 추려서 NEW_NAME/NEW_DEFINITION 으로 덮어씀."""
    body = {k: v for k, v in existing.items() if k in _KEEP_FIELDS_ON_UPDATE}
    body["name"] = NEW_NAME
    body["definition"] = NEW_DEFINITION
    if NEW_DESCRIPTION:
        body["description"] = NEW_DESCRIPTION
    return body


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Adobe Analytics Date Range 생성/갱신")
    parser.add_argument("--apply", dest="apply", action="store_true",
                        help="실제 POST/PUT 실행 (기본은 dry-run, payload 만 출력)")
    args = parser.parse_args()

    headers, gcid = _auth()

    is_update = bool(DATERANGE_ID)
    mode = "UPDATE" if is_update else "CREATE"
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] aa_daterange.py — {mode} mode")
    print(f"  AUTH    : {AUTH_JSON_PATH}")
    print(f"  COMPANY : {COMPANY_ID}")
    print()

    if is_update:
        print(f"GET existing → {DATERANGE_ID}")
        existing = _get_existing(headers, gcid, DATERANGE_ID)
        print(f"  current name      : {existing.get('name')}")
        print(f"  current definition: {existing.get('definition')}")
        own = existing.get("owner") or {}
        print(f"  owner             : id={own.get('id')}, login={own.get('login')}, name={own.get('name')}")
        print(f"  modified          : {existing.get('modified')}")
        print(f"  ⚠ PUT 시 403 나면 owner 가 다른 사람 → admin 권한 필요")
        print()
        print(f"  → new name      : {NEW_NAME}")
        print(f"  → new definition: {NEW_DEFINITION}")
        print()

        body = _build_update_body(existing)
        print("=== PUT body (preview) ===")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        print()

        if not args.apply:
            print("[dry-run] PUT skipped. --apply 로 실제 실행.")
            return 0

        url = f"https://analytics.adobe.io/api/{gcid}/dateranges/{DATERANGE_ID}"
        r = requests.put(url, headers=headers, json=body, timeout=60)
        print(f"PUT status: {r.status_code}")
        if r.status_code in (200, 201, 204):
            print(f"  ✓ updated.")
            if r.text:
                try:
                    out = r.json()
                    print(f"  response: name={out.get('name')}, definition={out.get('definition')}, modified={out.get('modified')}")
                except Exception:
                    pass
            return 0
        print(f"  error: {r.text[:500]}")
        return 1

    # CREATE
    body = _build_create_body(headers, gcid)
    print("=== POST body (preview) ===")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print()

    if not args.apply:
        print("[dry-run] POST skipped. --apply 로 실제 실행.")
        return 0

    url = f"https://analytics.adobe.io/api/{gcid}/dateranges"
    r = requests.post(url, headers=headers, json=body, timeout=60)
    print(f"POST status: {r.status_code}")
    if r.status_code in (200, 201):
        out = r.json()
        print(f"  ✓ created.")
        print(f"  id   = {out.get('id')}")
        print(f"  name = {out.get('name')}")
        print(f"  definition = {out.get('definition')}")
        return 0
    print(f"  error: {r.text[:500]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
