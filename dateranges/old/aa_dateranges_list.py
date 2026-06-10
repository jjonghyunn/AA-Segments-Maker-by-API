# aa_dateranges_list.py
# 2026-05-08  Jonghyun Park w/ Claude
"""
Adobe Analytics Date Range 컴포넌트 일괄 조회·필터 도구.

NAME_INCLUDES 의 키워드 중 하나라도 name 에 부분 일치(대소문자 무시) 하는 Date Range 들을
모두 fetch 해서 콘솔 표 + CSV 로 출력. NAME_INCLUDES 비우면 전체 출력.

수정·삭제는 별도 도구 (aa_daterange.py / 향후 aa_delete_daterange.py 등) 으로 진행.
이 스크립트는 read-only.

사용:
  python aa_dateranges_list.py            # 콘솔 + CSV 출력
  python aa_dateranges_list.py --no-csv   # CSV 비활성
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
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
COMPANY_ID = "company_id"

# ─── 필터 키워드 ───────────────────────────────────────────────────
# Date Range 의 name 에 부분 일치(대소문자 무시) 하는 것들만 남김.
# 빈 리스트면 모든 Date Range 출력.
NAME_INCLUDES = [
    "CAMPAIGN NAME",
    "last year campaign",
]

# ─── 페이징 ────────────────────────────────────────────────────────
PAGE_LIMIT = 1000   # 한 페이지당 fetch 수 (Adobe API max 1000)
MAX_PAGES = 200     # 최대 페이지 (안전장치 — 회사 전체 23k+ 면 25페이지 정도 필요)
INCLUDE_TYPE = "all"  # "all" 이면 본인+남이 만든 것 모두. "shared" 만 / 빈 값 = 본인 소유만

# ─── 출력 ──────────────────────────────────────────────────────────
CSV_OUTPUT_DIR = Path(__file__).resolve().parent
CSV_OUTPUT_NAME_TEMPLATE = "dateranges_filtered_{ts}.csv"


# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
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


def _list_all_dateranges(headers: dict, gcid: str) -> list[dict]:
    """페이지 0 부터 순회하며 모든 Date Range fetch. 빈 페이지 / lastPage true 만나면 중단."""
    all_items: list[dict] = []
    for page in range(MAX_PAGES):
        url = f"https://analytics.adobe.io/api/{gcid}/dateranges"
        params = {"limit": PAGE_LIMIT, "page": page,
                  "expansion": "definition,ownerFullName,modified,tags"}
        if INCLUDE_TYPE:
            params["includeType"] = INCLUDE_TYPE
        r = requests.get(url, headers=headers, params=params, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"GET /dateranges page {page} failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        # 응답 구조: dict 면 content 키 + 페이징 메타 / 직접 list 면 그대로
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
        all_items.extend(items)
        if total is not None and page == 0:
            print(f"  totalElements: {total} (필터 전 회사 전체)")
        print(f"  page {page}: +{len(items)} (누적 {len(all_items)})")
        if is_last or len(items) < PAGE_LIMIT:
            break  # 마지막 페이지
    return all_items


def _match_keyword(name: str, includes: list[str]) -> str | None:
    """name 이 includes 중 하나라도 부분 일치(대소문자 무시) 하면 매칭된 키워드 반환.
    필터 없으면 빈 문자열 반환 (모두 통과). 매칭 안 되면 None.
    """
    if not includes:
        return ""
    n_lower = (name or "").lower()
    for kw in includes:
        if kw.lower() in n_lower:
            return kw
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Adobe Analytics Date Range 일괄 조회/필터")
    parser.add_argument("--no-csv", dest="no_csv", action="store_true",
                        help="CSV 출력 비활성 (콘솔만)")
    args = parser.parse_args()

    headers, gcid = _auth()
    ts = datetime.now().strftime("%y%m%d_%H%M")
    print(f"[{ts}] aa_dateranges_list.py")
    print(f"  AUTH    : {AUTH_JSON_PATH}")
    print(f"  COMPANY : {COMPANY_ID}")
    print(f"  filters : {NAME_INCLUDES if NAME_INCLUDES else '(없음 — 전체 출력)'}")
    print()

    print("Fetching all Date Ranges (paginated)...")
    all_items = _list_all_dateranges(headers, gcid)
    print(f"  총 {len(all_items)} 개 fetch")
    print()

    # 필터링
    matched: list[tuple[dict, str]] = []
    for item in all_items:
        m = _match_keyword(item.get("name", ""), NAME_INCLUDES)
        if m is not None:
            matched.append((item, m))

    print(f"필터 매칭: {len(matched)} 개")
    print()
    if matched:
        print(f"{'id':<26}  {'name':<60}  {'definition':<48}  {'owner':<28}")
        print("-" * 168)
        for item, kw in matched:
            own = item.get("owner") or {}
            own_str = f"{own.get('login', '?')}({own.get('id', '?')})"
            print(f"{item.get('id', ''):<26}  {item.get('name', '')[:58]:<60}  {item.get('definition', '')[:46]:<48}  {own_str[:26]:<28}")

    # CSV 출력
    if not args.no_csv and matched:
        csv_out = CSV_OUTPUT_DIR / CSV_OUTPUT_NAME_TEMPLATE.format(ts=ts)
        with open(csv_out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["MatchedKeyword", "Id", "Name", "Definition",
                        "OwnerId", "OwnerLogin", "OwnerName",
                        "Modified", "Description", "Tags"])
            for item, kw in matched:
                own = item.get("owner") or {}
                w.writerow([
                    kw,
                    item.get("id", ""),
                    item.get("name", ""),
                    item.get("definition", ""),
                    own.get("id", ""),
                    own.get("login", ""),
                    own.get("name", ""),
                    item.get("modified", ""),
                    item.get("description", ""),
                    "|".join(item.get("tags") or []),
                ])
        print(f"\n[CSV] {csv_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
