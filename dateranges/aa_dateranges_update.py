# aa_dateranges_update.py
# 2026-05-08  Jonghyun Park w/ Claude
"""
사이트별 6개 입력값으로 8개 daterange 자동 산출 + 회사 전체 fetch 후 매칭 → 일괄 갱신.

입력 소스 (자동 우선순위):
  1순위) 같은 폴더의 SITES_INPUT_CSV (기본: 'dateranges_sites_input.csv')
  2순위) 1순위 파일 없으면 코드 안 SITES_INLINE dict (fallback)

입력 (사이트 1개당 6값) — CSV 컬럼 또는 dict 항목:
  SITE, THIS_START, THIS_END, LAST_START, LAST_END, BEFORE_BASE
글로벌 (모든 사이트 공통, 코드 상단 상수):
  THIS_YEAR_YY, LAST_YEAR_YY, CAMPAIGN_TAG

8개 daterange 자동 산출 공식 (사이트 <S>, 올해 yy, 작년 ly, 캠페인 <C>):
  1. [<S> <yy> <C>]                      = (THIS_START         ~ THIS_END)
  2. [<S> <yy> <C> 이전 전체]              = (BEFORE_BASE        ~ THIS_START - 1d)
  3. [<S> <yy> <C> 직전 4주]              = (THIS_START - 28d   ~ THIS_START - 1d)
  4. [<S> <yy> <C> 직전 4주 이전 전체]      = (BEFORE_BASE        ~ THIS_START - 29d)
  5. [<S> <yy> <C> 포함 이전 전체]         = (BEFORE_BASE        ~ THIS_END)
  6. [<S> <ly> <C>]                      = (LAST_START         ~ LAST_END)
  7. [<S> <ly> <C> 이전 전체]              = (BEFORE_BASE        ~ LAST_START - 1d)
  8. [<S> <ly> <C> 포함 이전 전체]         = (BEFORE_BASE        ~ LAST_END)

이후 GET /dateranges (includeType=all, paginated) 로 회사 전체 fetch,
이름 prefix 가 산출 키와 일치하는 것을 매칭. 같은 이름 다중 owner 면 각각 PUT 시도.

dry-run 기본 — 매칭/변경 예정 결과 CSV 출력.
--apply: PUT 실행. APPLY_OWNER_FILTER 로 owner 제한 가능.

사용:
  python aa_dateranges_update.py             # dry-run
  python aa_dateranges_update.py --apply     # 실제 PUT
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
COMPANY_ID = "company_id"

# ─── 캠페인 글로벌 (사이트 공통) ────────────────────────────────
THIS_YEAR_YY  = 26       # 올해 두 자리 (예: 26 = 2026). 매년 갱신
LAST_YEAR_YY  = 25       # 보통 THIS_YEAR_YY - 1
CAMPAIGN_TAG  = "MD"     # 캠페인 코드 (CAMPAIGN NAME=MD, CAMPAIGN NAME=BF, CAMPAIGN NAME=CAMPAIGN NAME 등)

# ─── 사이트별 입력 — 1순위: CSV 파일 ────────────────────────────
# 같은 폴더에 이 파일이 있으면 거기서 사이트 dict 를 읽음 (Excel 에서 편집 가능).
# CSV 컬럼: SITE, THIS_START, THIS_END, LAST_START, LAST_END, BEFORE_BASE
SITES_INPUT_CSV = Path(__file__).resolve().parent / "dateranges_sites_input.csv"

# ─── 사이트별 입력 — 2순위: inline dict (CSV 없을 때 fallback) ─
# CSV 파일이 없으면 아래 dict 가 사용됨. CSV 가 있으면 이 dict 는 무시.
# (this_start, this_end, last_start, last_end, before_base) — 모두 ISO YYYY-MM-DD
# before_base = 이 사이트의 '이전 전체' daterange 시작점. 보통 모든 사이트 공통이지만
#               캠페인/시점에 따라 매번 바뀌므로 사이트별로 둠 (필요시 같은 값 복사).
SITES_INLINE: dict[str, tuple[str, str, str, str, str]] = {
    "US": ("2026-04-20", "2026-05-10", "2025-04-14", "2025-05-04", "2024-04-01"),
    "MX": ("2026-04-20", "2026-05-10", "2025-04-28", "2025-05-11", "2024-04-01"),
    "BR": ("2026-04-20", "2026-05-10", "2025-04-23", "2025-05-11", "2024-04-01"),
    "AU": ("2026-04-23", "2026-05-10", "2025-04-17", "2025-05-04", "2024-04-01"),
    "CN": ("2026-05-01", "2026-05-10", "2025-05-01", "2025-05-10", "2024-04-01"),
    "DE": ("2026-04-09", "2026-05-10", "2025-04-23", "2025-05-15", "2024-04-01"),
    "IN": ("2026-04-17", "2026-04-25", "2025-04-22", "2025-04-29", "2024-04-01"),
    "TR": ("2026-05-01", "2026-05-10", "2025-05-01", "2025-05-10", "2024-04-01"),
}

# ─── fetch / apply 옵션 ──────────────────────────────────────────
INCLUDE_TYPE = "all"   # "all" 빼면 본인 소유만 = 0건. 회사 전체 보려면 "all".
PAGE_LIMIT = 1000      # Adobe API max 1000
MAX_PAGES = 200        # 안전장치

# --apply 시 PUT 적용 대상 owner 필터:
#   "all": 매칭된 모든 daterange (admin 권한 있을 때)
#   "self": 본인 소유 (login id 자동 lookup) 만
#   특정 numeric loginId: 그 owner 만
APPLY_OWNER_FILTER = "all"

# 결과 CSV 저장 위치
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE = "dateranges_update_{ts}_{mode}.csv"


# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
PREFIX_RE = re.compile(r'^(\[[^\]]+\])')


def _parse_date(s: str) -> datetime:
    """ISO YYYY-MM-DD → datetime (시간 0)."""
    return datetime.strptime(s, "%Y-%m-%d")


def _fmt_kr(d: datetime) -> str:
    """datetime → '2026. 4. 20' (한국식, 기존 csv 와 동일)."""
    return f"{d.year}. {d.month}. {d.day}"


def _iso_range(start: datetime, end: datetime) -> str:
    """START 00:00:00 / END 23:59:59 형식 (Adobe 관례)."""
    return (f"{start:%Y-%m-%dT}00:00:00/"
            f"{end:%Y-%m-%dT}23:59:59")


def _load_sites() -> tuple[dict[str, tuple[str, str, str, str, str]], str]:
    """SITES dict 결정. CSV 파일 1순위, 없으면 inline dict 2순위.
    Return: (sites_dict, source_label)
    """
    if SITES_INPUT_CSV.exists():
        sites: dict[str, tuple[str, str, str, str, str]] = {}
        with open(SITES_INPUT_CSV, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get("SITE"):
                    continue
                site = row["SITE"].strip()
                sites[site] = (
                    row["THIS_START"].strip(),
                    row["THIS_END"].strip(),
                    row["LAST_START"].strip(),
                    row["LAST_END"].strip(),
                    row["BEFORE_BASE"].strip(),
                )
        return sites, f"CSV ({SITES_INPUT_CSV.name})"
    return SITES_INLINE, "inline dict (SITES_INLINE) — CSV 미발견"


def _build_mapping_from_sites(sites: dict[str, tuple[str, str, str, str, str]]) -> dict[str, tuple[str, str]]:
    """sites dict + 글로벌 상수로부터 daterange 매핑 산출.
    Return: {prefix_key: (full_name, definition)}
    """
    out: dict[str, tuple[str, str]] = {}
    yy = THIS_YEAR_YY
    ly = LAST_YEAR_YY
    tag = CAMPAIGN_TAG

    for site, (this_s, this_e, last_s, last_e, base) in sites.items():
        ts, te = _parse_date(this_s), _parse_date(this_e)
        ls, le = _parse_date(last_s), _parse_date(last_e)
        bb     = _parse_date(base)

        # 8개 (suffix, start, end) 산출
        # suffix 가 빈 문자열이면 메인 (예: '[US CAMPAIGN NAME]')
        entries: list[tuple[int, str, datetime, datetime]] = [
            # (year_yy, suffix, start, end)
            (yy, "",                ts,                  te),
            (yy, " 이전 전체",         bb,                  ts - timedelta(days=1)),
            (yy, " 직전 4주",         ts - timedelta(days=28), ts - timedelta(days=1)),
            (yy, " 직전 4주 이전 전체", bb,                  ts - timedelta(days=29)),
            (yy, " 포함 이전 전체",    bb,                  te),
            (ly, "",                ls,                  le),
            (ly, " 이전 전체",         bb,                  ls - timedelta(days=1)),
            (ly, " 포함 이전 전체",    bb,                  le),
        ]
        for year_yy, suffix, start, end in entries:
            prefix = f"[{site} {year_yy} {tag}{suffix}]"
            full_name = f"{prefix} ({_fmt_kr(start)} ~ {_fmt_kr(end)})"
            definition = _iso_range(start, end)
            out[prefix] = (full_name, definition)
    return out


def _auth() -> tuple[dict, str]:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h = {k.lower(): v for k, v in dict(ags.header).items()}
    return {
        "x-api-key": h["x-api-key"],
        "Authorization": h["authorization"],
        "x-proxy-global-company-id": h["x-proxy-global-company-id"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, h["x-proxy-global-company-id"]


def _list_dateranges(headers: dict, gcid: str) -> list[dict]:
    all_items: list[dict] = []
    for page in range(MAX_PAGES):
        url = f"https://analytics.adobe.io/api/{gcid}/dateranges"
        params = {
            "limit": PAGE_LIMIT, "page": page,
            "expansion": "definition,ownerFullName,modified,tags",
        }
        if INCLUDE_TYPE:
            params["includeType"] = INCLUDE_TYPE
        r = requests.get(url, headers=headers, params=params, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"GET /dateranges page {page}: {r.status_code} {r.text[:300]}")
        d = r.json()
        items = d.get("content") if isinstance(d, dict) else d
        if not items:
            break
        all_items.extend(items)
        is_last = isinstance(d, dict) and d.get("lastPage", False)
        print(f"  page {page}: +{len(items)} (누적 {len(all_items)})")
        if is_last or len(items) < PAGE_LIMIT:
            break
    return all_items


def _name_prefix(name: str) -> str | None:
    m = PREFIX_RE.match(name or "")
    return m.group(1).strip() if m else None


def _lookup_my_user_id(headers: dict, gcid: str) -> int | None:
    url = f"https://analytics.adobe.io/api/{gcid}/users/me"
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None
    return r.json().get("loginId")


def _build_put_body(existing: dict, new_name: str, new_definition: str) -> dict:
    KEEP = {"id", "name", "description", "definition", "owner", "tags"}
    body = {k: v for k, v in existing.items() if k in KEEP}
    body["name"] = new_name
    body["definition"] = new_definition
    return body


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Date Range 일괄 갱신 (사이트별 6값 → 8 daterange 자동 산출)")
    parser.add_argument("--apply", dest="apply", action="store_true",
                        help="실제 PUT 실행 (기본은 dry-run)")
    args = parser.parse_args()

    ts = datetime.now().strftime("%y%m%d_%H%M")
    headers, gcid = _auth()
    mode_label = "APPLY" if args.apply else "dry-run"
    print(f"[{ts}] aa_dateranges_update.py — {mode_label}")
    print(f"  AUTH    : {AUTH_JSON_PATH}")
    print(f"  COMPANY : {COMPANY_ID}")
    print(f"  campaign: {THIS_YEAR_YY} {CAMPAIGN_TAG} (last={LAST_YEAR_YY})")

    # 0. 사이트 입력 로드 (CSV 1순위 / inline 2순위)
    sites, source = _load_sites()
    print(f"  sites src: {source}")
    print(f"  sites    : {list(sites.keys())}")
    if args.apply:
        print(f"  filter   : APPLY_OWNER_FILTER={APPLY_OWNER_FILTER}")
    print()

    # 1. 매핑 산출 (입력 dict → 8 사이트 × 8 타입 = 64 daterange)
    print("Building mapping from sites + globals ...")
    mapping = _build_mapping_from_sites(sites)
    print(f"  매핑 키(이름 prefix): {len(mapping)} 개")
    for k in sorted(mapping):
        nm, defn = mapping[k]
        print(f"    {k:<40} → {defn}")
    print()

    # 2. fetch
    print("Fetching all Date Ranges (paginated)...")
    all_items = _list_dateranges(headers, gcid)
    print(f"  총 {len(all_items)} 개 fetch")
    print()

    # 3. 매칭
    matches: list[tuple[dict, str, str]] = []
    for item in all_items:
        prefix = _name_prefix(item.get("name", ""))
        if not prefix:
            continue
        if prefix in mapping:
            new_name, new_def = mapping[prefix]
            matches.append((item, new_name, new_def))

    print(f"매칭 결과: {len(matches)} 개")
    matched_prefixes = {_name_prefix(m[0].get("name", "")) for m in matches}
    unmatched_keys = set(mapping) - matched_prefixes
    if unmatched_keys:
        print(f"  매핑은 있는데 fetch에서 못 찾음: {len(unmatched_keys)}개")
        for k in sorted(unmatched_keys):
            print(f"    - {k}")
    print()

    # 4. APPLY 모드 owner 필터 결정
    apply_filter_ids: set[int] | None = None  # None = no filter
    if args.apply:
        f = APPLY_OWNER_FILTER
        if f == "all":
            apply_filter_ids = None
        elif f == "self":
            my_id = _lookup_my_user_id(headers, gcid)
            if my_id:
                apply_filter_ids = {my_id}
                print(f"  APPLY filter: self (loginId={my_id})")
            else:
                print(f"  ⚠ /users/me 조회 실패 → APPLY filter 'self' 적용 불가, 종료")
                return 1
        else:
            try:
                apply_filter_ids = {int(f)}
                print(f"  APPLY filter: ownerId={apply_filter_ids}")
            except ValueError:
                print(f"  ⚠ APPLY_OWNER_FILTER='{f}' 잘못된 값")
                return 1

    # 5. 결과 CSV 작성 + (옵션) PUT
    out_csv = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(
        ts=ts, mode=("apply" if args.apply else "dryrun"))
    no_change = 0
    will_update = 0
    skipped_by_owner = 0
    put_ok = 0
    put_fail = 0

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["MatchKey", "Id", "OwnerLogin", "OwnerId", "OwnerName",
                    "CurrentName", "NewName", "CurrentDefinition", "NewDefinition",
                    "Status", "ResponseCode", "ErrorMessage"])
        for existing, new_name, new_def in matches:
            prefix = _name_prefix(existing.get("name", ""))
            own = existing.get("owner") or {}
            current_def = existing.get("definition", "")
            current_name = existing.get("name", "")

            same = (current_name == new_name and current_def == new_def)
            status = ""
            response_code = ""
            err = ""

            if args.apply:
                own_id = own.get("id")
                if apply_filter_ids is not None and own_id not in apply_filter_ids:
                    status = "SKIP_OWNER_FILTER"
                    skipped_by_owner += 1
                elif same:
                    status = "NO_CHANGE_SKIP"
                    no_change += 1
                else:
                    body = _build_put_body(existing, new_name, new_def)
                    url = f"https://analytics.adobe.io/api/{gcid}/dateranges/{existing.get('id')}"
                    try:
                        r = requests.put(url, headers=headers, json=body, timeout=60)
                        response_code = str(r.status_code)
                        if r.status_code in (200, 201, 204):
                            status = "OK"
                            put_ok += 1
                        else:
                            status = "FAIL"
                            err = r.text[:300]
                            put_fail += 1
                    except Exception as e:
                        status = "EXCEPTION"
                        err = str(e)
                        put_fail += 1
                    print(f"  PUT {existing.get('id')} ({prefix})  {own.get('login', '?')}  → {status}  {response_code}")
            else:
                if same:
                    status = "NO_CHANGE"
                    no_change += 1
                else:
                    status = "WOULD_UPDATE"
                    will_update += 1

            w.writerow([prefix, existing.get("id"),
                        own.get("login", ""), own.get("id", ""), own.get("name", ""),
                        current_name, new_name, current_def, new_def,
                        status, response_code, err])

    print(f"\n[CSV] {out_csv}")

    # 6. summary
    print(f"\n[summary]")
    print(f"  매칭 daterange : {len(matches)}")
    if args.apply:
        print(f"  PUT OK         : {put_ok}")
        print(f"  PUT FAIL       : {put_fail}")
        print(f"  NO_CHANGE_SKIP : {no_change}")
        print(f"  SKIP_OWNER_FILTER: {skipped_by_owner}")
    else:
        print(f"  WOULD_UPDATE   : {will_update}")
        print(f"  NO_CHANGE      : {no_change}")

    owners = Counter(((m[0].get("owner") or {}).get("login", "") or f"id_{(m[0].get('owner') or {}).get('id', '?')}") for m in matches)
    print(f"  owner 분포     : {dict(owners)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
