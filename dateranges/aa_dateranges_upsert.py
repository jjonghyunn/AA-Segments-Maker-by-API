# aa_dateranges_upsert.py
# 2026-05-14  Jonghyun Park w/ Claude
"""
사이트별 6값 → 8 daterange 자동 산출 후 회사 전체 fetch 와 매칭:
  · 이름 prefix 가 이미 존재 → UPDATE (PUT, definition/name 갱신)
  · 존재하지 않음 → CREATE (POST, 옵션으로 owner 이전)

aa_dateranges_update.py + aa_dateranges_create.py 합친 단일 도구.

핵심 흐름
  1) sites 입력 로드 (CSV 1순위 / inline 2순위)
  2) 매핑 산출 (사이트 × 8 entry)
  3) GET /dateranges?includeType=all paged — 회사 전체 fetch
  4) 각 매핑 entry 의 prefix 가 fetch 결과에 있는지 확인 → UPDATE / CREATE 분류
  5) dry-run 으로 분류 결과 항상 먼저 출력 (UPDATE N개 / CREATE N개) + 결과 CSV
  6) `--apply` 면 분류 결과 출력 후 input() y/N confirm → PUT(UPDATE) + POST(CREATE)

사용 예
  python aa_dateranges_upsert.py             # dry-run — UPDATE/CREATE 분류만 출력
  python aa_dateranges_upsert.py --apply     # 분류 출력 → y/N confirm → 실제 PUT + POST
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
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
THIS_YEAR_YY  = 26
LAST_YEAR_YY  = 25
CAMPAIGN_TAG  = "SW"

# ─── 사이트별 입력 — 1순위: CSV 파일 ────────────────────────────
# CSV 컬럼: SITE, THIS_START, THIS_END, LAST_START, LAST_END, BEFORE_BASE
# update 도구 (`dateranges_sites_input.csv`) / create 도구 (`dateranges_sites_input_create.csv`)
# 와 별개 파일 — upsert 만의 사이트 셋트 따로 관리 가능.
SITES_INPUT_CSV = Path(__file__).resolve().parent / "dateranges_sites_input_upsert.csv"

# ─── 사이트별 입력 — 2순위: inline dict (CSV 없을 때 fallback) ─
SITES_INLINE: dict[str, tuple[str, str, str, str, str]] = {
    # "US": ("2026-04-20", "2026-05-10", "2025-04-14", "2025-05-04", "2024-04-01"),
}

# ─── CREATE 옵션 ────────────────────────────────────────────────
# CREATE 시 새 daterange 의 description / tags (모든 사이트 공통)
NEW_DESCRIPTION = ""
NEW_TAGS: list[str] = []

# CREATE 시 owner 이전:
#   0          : 본인(token holder) owner 그대로 유지
#   numeric ID : POST 직후 PUT 으로 owner 이전 (admin 권한 필요)
POST_TRANSFER_TO_OWNER_ID = 0

# ─── UPDATE 옵션 ────────────────────────────────────────────────
# UPDATE 시 PUT 대상 owner 필터:
#   "all"  : 매칭된 모든 daterange (admin 권한 있을 때)
#   "self" : 본인 소유 (login id 자동 lookup) 만
#   특정 numeric loginId 문자열: 그 owner 만
APPLY_OWNER_FILTER = "all"

# ─── fetch 옵션 ─────────────────────────────────────────────────
INCLUDE_TYPE = "all"
PAGE_LIMIT = 1000
MAX_PAGES = 200

# ─── 출력 ──────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE = "dateranges_upsert_{ts}_{mode}.csv"


# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
PREFIX_RE = re.compile(r'^(\[[^\]]+\])')


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _fmt_kr(d: datetime) -> str:
    return f"{d.year}. {d.month}. {d.day}"


def _iso_range(start: datetime, end: datetime) -> str:
    return f"{start:%Y-%m-%dT}00:00:00/{end:%Y-%m-%dT}23:59:59"


def _load_sites() -> tuple[dict[str, tuple[str, str, str, str, str]], str]:
    if SITES_INPUT_CSV.exists():
        sites: dict[str, tuple[str, str, str, str, str]] = {}
        with open(SITES_INPUT_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
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


def _build_mapping_from_sites(sites: dict[str, tuple[str, str, str, str, str]]) -> list[tuple[str, str, str, str]]:
    """sites → list of (site, prefix, full_name, definition). 순서 보존."""
    out: list[tuple[str, str, str, str]] = []
    yy, ly, tag = THIS_YEAR_YY, LAST_YEAR_YY, CAMPAIGN_TAG
    for site, (this_s, this_e, last_s, last_e, base) in sites.items():
        ts, te = _parse_date(this_s), _parse_date(this_e)
        ls, le = _parse_date(last_s), _parse_date(last_e)
        bb     = _parse_date(base)
        entries: list[tuple[int, str, datetime, datetime]] = [
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
            out.append((site, prefix, full_name, definition))
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


def _build_post_body(name: str, definition: str) -> dict:
    return {
        "name": name,
        "description": NEW_DESCRIPTION,
        "definition": definition,
        "tags": NEW_TAGS,
    }


def _transfer_owner(headers: dict, gcid: str, dr_id: str, new_owner_id: int) -> tuple[bool, str]:
    """POST 직후 daterange 의 owner 를 new_owner_id 로 이전 (PUT)."""
    url = f"https://analytics.adobe.io/api/{gcid}/dateranges/{dr_id}"
    rg = requests.get(url, headers=headers, params={"expansion": "definition,owner,tags"}, timeout=30)
    if rg.status_code != 200:
        return False, f"GET {dr_id}: {rg.status_code} {rg.text[:200]}"
    d = rg.json()
    body = {k: v for k, v in d.items() if k in {"id", "name", "description", "definition", "tags"}}
    body["owner"] = {"id": new_owner_id}
    rp = requests.put(url, headers=headers, json=body, timeout=30)
    if rp.status_code in (200, 201, 204):
        return True, ""
    return False, f"PUT {dr_id}: {rp.status_code} {rp.text[:200]}"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Date Range 일괄 upsert (UPDATE / CREATE 자동 분류)")
    parser.add_argument("--apply", dest="apply", action="store_true",
                        help="분류 결과 확인 후 실제 PUT + POST. 기본은 dry-run.")
    args = parser.parse_args()

    ts_str = datetime.now().strftime("%y%m%d_%H%M")
    headers, gcid = _auth()
    mode_label = "APPLY" if args.apply else "dry-run"
    print(f"[{ts_str}] aa_dateranges_upsert.py — {mode_label}")
    print(f"  AUTH    : {AUTH_JSON_PATH}")
    print(f"  COMPANY : {COMPANY_ID}")
    print(f"  campaign: {THIS_YEAR_YY} {CAMPAIGN_TAG} (last={LAST_YEAR_YY})")

    # 0. 사이트 입력 로드
    sites, source = _load_sites()
    print(f"  sites src: {source}")
    print(f"  sites    : {list(sites.keys())}")
    if not sites:
        print("\n❌ 사이트 입력이 비어있음 — abort.")
        return 1
    print()

    # 1. 매핑 산출
    print("Building mapping from sites + globals ...")
    mapping = _build_mapping_from_sites(sites)
    print(f"  매핑 entry: {len(mapping)} 개 ({len(sites)} 사이트 × 8)")
    print()

    # 2. fetch
    print("Fetching all Date Ranges (paginated)...")
    all_items = _list_dateranges(headers, gcid)
    print(f"  총 {len(all_items)} 개 fetch")
    # prefix → items (같은 prefix 다중 owner 가능)
    by_prefix: dict[str, list[dict]] = defaultdict(list)
    for item in all_items:
        p = _name_prefix(item.get("name", ""))
        if p:
            by_prefix[p].append(item)
    print()

    # 3. 분류 — entry 별로 UPDATE / CREATE
    update_targets: list[tuple[str, str, str, str, dict]] = []   # (site, prefix, new_name, new_def, existing)
    create_targets: list[tuple[str, str, str, str]] = []         # (site, prefix, new_name, new_def)
    for site, prefix, new_name, new_def in mapping:
        existing_list = by_prefix.get(prefix, [])
        if existing_list:
            for existing in existing_list:
                update_targets.append((site, prefix, new_name, new_def, existing))
        else:
            create_targets.append((site, prefix, new_name, new_def))

    # 사이트별 분포
    upd_by_site = Counter(t[0] for t in update_targets)
    cre_by_site = Counter(t[0] for t in create_targets)
    all_site_set = sorted(set(sites.keys()) | set(upd_by_site) | set(cre_by_site))

    print(f"=== 분류 결과 ===")
    print(f"  UPDATE 대상: {len(update_targets)} 개 entry")
    print(f"  CREATE 대상: {len(create_targets)} 개 entry")
    print()
    print(f"=== 사이트별 분포 ===")
    print(f"  {'SITE':6}  {'UPDATE':>6}  {'CREATE':>6}  classification")
    for site in all_site_set:
        u, c = upd_by_site.get(site, 0), cre_by_site.get(site, 0)
        if u == 0 and c > 0:
            cls = "→ NEW SITE (create only)"
        elif c == 0 and u > 0:
            cls = "→ EXISTING (update all)"
        else:
            cls = f"→ PARTIAL (update {u} + create {c})"
        print(f"  {site:6}  {u:>6}  {c:>6}  {cls}")
    print()

    # 매칭 안 된 케이스 (매핑은 있는데 fetch에서 안 보임) = CREATE 대상이라 정상

    # 4. APPLY 모드 owner 필터 결정 (UPDATE 대상에 한정)
    apply_filter_ids: set[int] | None = None
    if args.apply:
        f = APPLY_OWNER_FILTER
        if f == "all":
            apply_filter_ids = None
        elif f == "self":
            my_id = _lookup_my_user_id(headers, gcid)
            if my_id:
                apply_filter_ids = {my_id}
                print(f"  APPLY filter (UPDATE 한정): self (loginId={my_id})")
            else:
                print(f"  ⚠ /users/me 조회 실패 → abort")
                return 1
        else:
            try:
                apply_filter_ids = {int(f)}
                print(f"  APPLY filter (UPDATE 한정): ownerId={apply_filter_ids}")
            except ValueError:
                print(f"  ⚠ APPLY_OWNER_FILTER='{f}' 잘못된 값")
                return 1

    # 5. 결과 CSV 작성 + (옵션) PUT/POST
    out_csv = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(
        ts=ts_str, mode=("apply" if args.apply else "dryrun"))

    no_change   = 0
    will_update = 0
    will_create = 0
    skipped_by_owner = 0
    put_ok, put_fail = 0, 0
    post_ok, post_fail = 0, 0
    transfer_ok, transfer_fail = 0, 0

    # --apply 면 사용자 confirm 한 번 더
    if args.apply:
        print()
        print(f"⚠️ 위 분류 결과로 실제 PUT (UPDATE {len(update_targets)}건) + POST (CREATE {len(create_targets)}건) 실행합니다.")
        if POST_TRANSFER_TO_OWNER_ID:
            print(f"   CREATE 후 owner → {POST_TRANSFER_TO_OWNER_ID} 자동 이전")
        ans = input("   진행하려면 'y' 또는 'yes' 입력 (그 외 입력 시 중단): ").strip().lower()
        if ans not in ("y", "yes"):
            print("   취소됨.")
            return 1
        print()

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Action", "Site", "MatchKey", "Id",
                    "OwnerLogin", "OwnerId", "OwnerName",
                    "CurrentName", "NewName", "CurrentDefinition", "NewDefinition",
                    "Status", "ResponseCode", "CreatedId",
                    "TransferStatus", "FinalOwnerId", "ErrorMessage"])

        # UPDATE 처리
        for site, prefix, new_name, new_def, existing in update_targets:
            own = existing.get("owner") or {}
            current_def  = existing.get("definition", "")
            current_name = existing.get("name", "")
            same = (current_name == new_name and current_def == new_def)
            status = ""; response_code = ""; err = ""
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
                            status = "OK"; put_ok += 1
                        else:
                            status = "FAIL"; err = r.text[:300]; put_fail += 1
                    except Exception as e:
                        status = "EXCEPTION"; err = str(e); put_fail += 1
                    print(f"  PUT {existing.get('id')} ({prefix})  {own.get('login','?')}  → {status}  {response_code}")
            else:
                if same:
                    status = "NO_CHANGE"; no_change += 1
                else:
                    status = "WOULD_UPDATE"; will_update += 1
            w.writerow(["UPDATE", site, prefix, existing.get("id"),
                        own.get("login",""), own.get("id",""), own.get("name",""),
                        current_name, new_name, current_def, new_def,
                        status, response_code, "", "", "", err])

        # CREATE 처리
        for site, prefix, new_name, new_def in create_targets:
            status = ""; response_code = ""; created_id = ""
            transfer_status = ""; final_owner = ""; err = ""
            if args.apply:
                body = _build_post_body(new_name, new_def)
                url = f"https://analytics.adobe.io/api/{gcid}/dateranges"
                try:
                    r = requests.post(url, headers=headers, json=body, timeout=60)
                    response_code = str(r.status_code)
                    if r.status_code in (200, 201):
                        status = "OK"; post_ok += 1
                        out = r.json()
                        created_id = out.get("id", "")
                        final_owner = (out.get("owner") or {}).get("id", "")
                        if POST_TRANSFER_TO_OWNER_ID and created_id:
                            ok_t, err_t = _transfer_owner(headers, gcid, created_id, POST_TRANSFER_TO_OWNER_ID)
                            if ok_t:
                                transfer_status = "TRANSFERRED"
                                final_owner = POST_TRANSFER_TO_OWNER_ID
                                transfer_ok += 1
                            else:
                                transfer_status = "TRANSFER_FAIL"; err = err_t; transfer_fail += 1
                        else:
                            transfer_status = "SKIP"
                    else:
                        status = "FAIL"; err = r.text[:300]; post_fail += 1
                except Exception as e:
                    status = "EXCEPTION"; err = str(e); post_fail += 1
                t_label = f"  T:{transfer_status}" if POST_TRANSFER_TO_OWNER_ID else ""
                print(f"  POST {prefix}  → {status}  {response_code}  id={created_id}{t_label}")
            else:
                status = "WOULD_CREATE"; will_create += 1
                if POST_TRANSFER_TO_OWNER_ID:
                    transfer_status = "WOULD_TRANSFER"
            w.writerow(["CREATE", site, prefix, "",
                        "", "", "",
                        "", new_name, "", new_def,
                        status, response_code, created_id,
                        transfer_status, final_owner, err])

    print(f"\n[CSV] {out_csv}")
    print(f"\n[summary]")
    if args.apply:
        print(f"  UPDATE  PUT OK   : {put_ok}")
        print(f"  UPDATE  PUT FAIL : {put_fail}")
        print(f"  UPDATE  NO_CHANGE: {no_change}")
        print(f"  UPDATE  SKIP_OWNER_FILTER: {skipped_by_owner}")
        print(f"  CREATE  POST OK  : {post_ok}")
        print(f"  CREATE  POST FAIL: {post_fail}")
        if POST_TRANSFER_TO_OWNER_ID:
            print(f"  TRANSFER OK     : {transfer_ok}")
            print(f"  TRANSFER FAIL   : {transfer_fail}")
    else:
        print(f"  WOULD_UPDATE    : {will_update}")
        print(f"  WOULD_CREATE    : {will_create}")
        print(f"  NO_CHANGE       : {no_change}")
        print()
        print("ℹ️ Dry-run 모드 — 적용하려면 --apply (실행 전 y/N input() 한 번 더 확인)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
