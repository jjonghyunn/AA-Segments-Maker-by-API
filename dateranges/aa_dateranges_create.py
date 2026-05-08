# aa_dateranges_create.py
# 2026-05-08  Jonghyun Park w/ Claude
"""
사이트별 6값 입력으로 N×8 daterange 일괄 생성 (POST).

update 도구와 같은 입력 구조 + 같은 산출 공식. 차이점:
  · fetch + 매칭 단계 없음 → POST 만 수행 (빠름)
  · 본인이 owner 가 됨 → admin 권한 의존 X
  · 결과 CSV 에 새로 생성된 ID 들이 기록됨

⚠️ 같은 이름의 daterange 가 이미 있어도 POST 는 새로 생성 (Adobe API 동작) →
   동일 이름 중복 가능. 실수로 두 번 돌리지 않도록 dry-run 으로 먼저 확인.

입력 소스 (자동 우선순위):
  1순위) 같은 폴더의 SITES_INPUT_CSV (기본: 'dateranges_sites_input_create.csv')
         — update 도구의 'dateranges_sites_input.csv' 와 별도 파일.
  2순위) 1순위 파일 없으면 코드 안 SITES_INLINE dict (fallback)

입력 (사이트 1개당 6값) — CSV 컬럼 또는 dict 항목:
  SITE, THIS_START, THIS_END, LAST_START, LAST_END, BEFORE_BASE
글로벌 (모든 사이트 공통, 코드 상단 상수):
  THIS_YEAR_YY, LAST_YEAR_YY, CAMPAIGN_TAG

8개 daterange 자동 산출 공식 — update 도구와 동일.

사용:
  python aa_dateranges_create.py             # dry-run
  python aa_dateranges_create.py --apply     # 실제 POST
"""
from __future__ import annotations

import argparse
import csv
import sys
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
THIS_YEAR_YY  = 26       # 올해 두 자리 (예: 26 = 2026)
LAST_YEAR_YY  = 25       # 보통 THIS_YEAR_YY - 1
CAMPAIGN_TAG  = "SW"     # 캠페인 코드 (짧은 약어)

# ─── owner 처리 ─────────────────────────────────────────────────
# Adobe API 는 POST 시 owner.id 를 명시해도 token holder 로 덮어씀 (실측 확인).
# 그래서 다른 사람 owner 로 만들고 싶으면 POST(=token holder owner) 후 PUT 으로 owner 이전.
#
# POST_TRANSFER_TO_OWNER_ID:
#   0          : 본인(token holder) owner 그대로 유지
#   numeric ID : POST 직후 자동 PUT 으로 owner 이전 (admin 권한 필요)
POST_TRANSFER_TO_OWNER_ID = YOUR_LOGIN_ID   # user2_login

# 새 daterange 의 description, tags (모든 사이트 공통)
NEW_DESCRIPTION = ""
NEW_TAGS: list[str] = []

# ─── 사이트별 입력 — 1순위: CSV 파일 ────────────────────────────
# update 도구의 'dateranges_sites_input.csv' 와 별도. create 는 새 캠페인용 (보통 다른 값).
SITES_INPUT_CSV = Path(__file__).resolve().parent / "dateranges_sites_input_create.csv"

# ─── 사이트별 입력 — 2순위: inline dict (CSV 없을 때 fallback) ─
# (this_start, this_end, last_start, last_end, before_base) — 모두 ISO YYYY-MM-DD
SITES_INLINE: dict[str, tuple[str, str, str, str, str]] = {
    # 예시 — 본인 캠페인 값으로 채우거나 CSV 사용
    "US": ("2026-05-11", "2026-05-17", "2025-05-11", "2025-05-17", "2024-05-01"),
}

# 결과 CSV 저장 위치
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE = "dateranges_create_{ts}_{mode}.csv"


# ════════════════════════════════════════════════════════════════════
# 내부 사용 (update 도구와 같은 산출 함수들)
# ════════════════════════════════════════════════════════════════════
def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _fmt_kr(d: datetime) -> str:
    return f"{d.year}. {d.month}. {d.day}"


def _iso_range(start: datetime, end: datetime) -> str:
    return (f"{start:%Y-%m-%dT}00:00:00/"
            f"{end:%Y-%m-%dT}23:59:59")


def _load_sites() -> tuple[dict[str, tuple[str, str, str, str, str]], str]:
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


def _build_mapping_from_sites(sites: dict[str, tuple[str, str, str, str, str]]) -> list[tuple[str, str, str]]:
    """sites dict + 글로벌 → list of (prefix, full_name, definition).
    update 와 달리 dict 가 아닌 list 반환 (POST 순서·중복 보존).
    """
    out: list[tuple[str, str, str]] = []
    yy = THIS_YEAR_YY
    ly = LAST_YEAR_YY
    tag = CAMPAIGN_TAG

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
            out.append((prefix, full_name, definition))
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


def _lookup_my_user_id(headers: dict, gcid: str) -> int | None:
    url = f"https://analytics.adobe.io/api/{gcid}/users/me"
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None
    return r.json().get("loginId")


def _build_post_body(name: str, definition: str) -> dict:
    return {
        "name": name,
        "description": NEW_DESCRIPTION,
        "definition": definition,
        "tags": NEW_TAGS,
    }


def _transfer_owner(headers: dict, gcid: str, dr_id: str, new_owner_id: int) -> tuple[bool, str]:
    """POST 직후 daterange 의 owner 를 new_owner_id 로 이전 (PUT).
    Return: (success, error_msg)
    """
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

    parser = argparse.ArgumentParser(description="Date Range 일괄 생성 (사이트별 6값 → N×8 POST)")
    parser.add_argument("--apply", dest="apply", action="store_true",
                        help="실제 POST 실행 (기본은 dry-run, body 만 출력)")
    args = parser.parse_args()

    ts = datetime.now().strftime("%y%m%d_%H%M")
    headers, gcid = _auth()
    mode_label = "APPLY" if args.apply else "dry-run"
    print(f"[{ts}] aa_dateranges_create.py — {mode_label}")
    print(f"  AUTH    : {AUTH_JSON_PATH}")
    print(f"  COMPANY : {COMPANY_ID}")
    print(f"  campaign: {THIS_YEAR_YY} {CAMPAIGN_TAG} (last={LAST_YEAR_YY})")

    # 사이트 입력 로드
    sites, source = _load_sites()
    print(f"  sites src: {source}")
    print(f"  sites    : {list(sites.keys())}")
    print()

    # owner 처리 — POST 시 Adobe 가 token holder 로 강제하므로 일단 본인. 필요시 PUT 으로 이전.
    transfer_to = POST_TRANSFER_TO_OWNER_ID
    if transfer_to:
        print(f"  POST owner: token holder (본인). 직후 PUT 으로 owner → {transfer_to} 이전")
    else:
        print(f"  POST owner: token holder (본인). owner 이전 안 함")
    print()

    # 매핑 산출 (list 형태 — 순서 보존)
    print("Building mapping from sites + globals ...")
    mapping = _build_mapping_from_sites(sites)
    print(f"  생성 예정 daterange: {len(mapping)} 개 ({len(sites)} 사이트 × 8)")
    if not args.apply:
        print()
        print("  ⚠ --apply 시 동일 이름 중복 생성 위험. 실수로 두 번 돌리지 않도록 주의.")
    print()

    # 결과 CSV + (옵션) POST
    out_csv = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(
        ts=ts, mode=("apply" if args.apply else "dryrun"))
    post_ok = 0
    post_fail = 0

    transfer_ok = 0
    transfer_fail = 0
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["MatchKey", "NewName", "NewDefinition",
                    "Status", "ResponseCode", "CreatedId",
                    "TransferStatus", "FinalOwnerId", "ErrorMessage"])

        for prefix, new_name, new_def in mapping:
            status = ""
            response_code = ""
            created_id = ""
            transfer_status = ""
            final_owner = ""
            err = ""

            if args.apply:
                body = _build_post_body(new_name, new_def)
                url = f"https://analytics.adobe.io/api/{gcid}/dateranges"
                try:
                    r = requests.post(url, headers=headers, json=body, timeout=60)
                    response_code = str(r.status_code)
                    if r.status_code in (200, 201):
                        status = "OK"
                        post_ok += 1
                        out = r.json()
                        created_id = out.get("id", "")
                        final_owner = (out.get("owner") or {}).get("id", "")
                        # owner 이전 (옵션)
                        if transfer_to and created_id:
                            ok_t, err_t = _transfer_owner(headers, gcid, created_id, transfer_to)
                            if ok_t:
                                transfer_status = "TRANSFERRED"
                                final_owner = transfer_to
                                transfer_ok += 1
                            else:
                                transfer_status = "TRANSFER_FAIL"
                                err = err_t
                                transfer_fail += 1
                        else:
                            transfer_status = "SKIP"
                    else:
                        status = "FAIL"
                        err = r.text[:300]
                        post_fail += 1
                except Exception as e:
                    status = "EXCEPTION"
                    err = str(e)
                    post_fail += 1
                t_label = f"  T:{transfer_status}" if transfer_to else ""
                print(f"  POST {prefix}  → {status}  {response_code}  id={created_id}{t_label}")
            else:
                status = "WOULD_CREATE"
                if transfer_to:
                    transfer_status = "WOULD_TRANSFER"

            w.writerow([prefix, new_name, new_def,
                        status, response_code, created_id,
                        transfer_status, final_owner, err])

    print(f"\n[CSV] {out_csv}")

    # summary
    print(f"\n[summary]")
    print(f"  생성 예정      : {len(mapping)}")
    if args.apply:
        print(f"  POST OK        : {post_ok}")
        print(f"  POST FAIL      : {post_fail}")
        if transfer_to:
            print(f"  TRANSFER OK    : {transfer_ok}")
            print(f"  TRANSFER FAIL  : {transfer_fail}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
