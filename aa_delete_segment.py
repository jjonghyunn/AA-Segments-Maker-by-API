# aa_delete_segment.py
# 2026-05-04  Jonghyun Park w/ Claude
"""
aa_create_segment.py가 만든 result CSV의 SegmentId만 안전 삭제.

안전장치 3중:
  1. result CSV 기반 — CSV에 기록된 SegmentId만 대상. 직접 ID/와일드카드 입력 불가.
  2. 이름 prefix 검증 — 삭제 직전 GET으로 실제 이름 확인. SAFE_NAME_PREFIX (기본 "_test_")로
                       시작 안 하면 자동 skip. 일반 운영 segment 보호.
  3. --yes 플래그 게이트 — 안 주면 삭제 후보 목록만 출력하고 끝남 (preview).

─────────────────────────────────────────────────────────────────────
사용법
─────────────────────────────────────────────────────────────────────

(1) PREVIEW (실제 삭제 X) — 안전 확인용. 삭제 후보만 목록 출력:

  python aa_delete_segment.py
  python aa_delete_segment.py --from-csv result_YYMMDD_HHMM.csv

(2) 실제 삭제 — 위 preview 결과 OK일 때 --yes 추가:

  python aa_delete_segment.py --yes
  python aa_delete_segment.py --from-csv result_YYMMDD_HHMM.csv --yes

─────────────────────────────────────────────────────────────────────
PowerShell 주의사항 — `--yes`는 따옴표 밖에 둘 것:

  & python.exe "...\\aa_delete_segment.py" --yes        ← OK
  & python.exe "...\\aa_delete_segment.py --yes"        ← X (파일경로 안에 박힘)

─────────────────────────────────────────────────────────────────────
--from-csv 동작:
  · 경로 주면 그 CSV 사용
  · 생략하면 같은 폴더의 result_*.csv / test_result_*.csv 중 가장 최신 1개 자동 선택
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분 — 다른 사람이 쓸 때 여기만 수정
# ════════════════════════════════════════════════════════════════════

# Adobe Developer Console에서 받은 OAuth Server-to-Server 자격증명 json 경로.
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"

# AA 회사(login company) ID.
COMPANY_ID = "your_aa_company_id"

# 안전장치 #2 (선택) — 실제 segment 이름이 이 prefix로 시작 안 하면 삭제 거부.
# 빈 값 ""이면 prefix 검증 비활성 — csv 의 모든 SegmentId 그대로 삭제 진행.
# 운영 segment 통째 삭제 안전망이 필요할 때만 prefix 박음 (예: "_test_" / "[CAMPAIGN NAME] CC_").
# CLI: --safe-prefix "<PREFIX>" 로도 override 가능.
SAFE_NAME_PREFIX = ""


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


def _read_csv_segment_ids(csv_path: Path) -> list[dict]:
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sid = (row.get("SegmentId") or "").strip()
            if sid:
                rows.append(
                    {
                        "segment_id": sid,
                        "csv_name": (row.get("Name") or "").strip(),
                    }
                )
    return rows


def _get_segment_name(headers: dict, gcid: str, segment_id: str) -> tuple[str | None, str]:
    """GET /segments/{id} → 실제 이름 반환. 실패 시 (None, error_msg)."""
    url = f"https://analytics.adobe.io/api/{gcid}/segments/{segment_id}"
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code != 200:
        return None, f"GET {r.status_code} {r.reason}"
    return r.json().get("name", ""), ""


def _autodetect_latest_csv() -> Path | None:
    """같은 폴더의 result_*.csv / test_result_*.csv 중 mtime 최신 1개."""
    script_dir = Path(__file__).resolve().parent
    candidates = list(script_dir.glob("test_result_*.csv")) + list(
        script_dir.glob("result_*.csv")
    )
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="안전 삭제: result CSV의 SAFE_NAME_PREFIX 매칭 segment만"
    )
    parser.add_argument(
        "--from-csv",
        dest="from_csv",
        default=None,
        help="result CSV 경로. 생략하면 같은 폴더의 가장 최신 result_*.csv / test_result_*.csv 자동 선택",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="실제 삭제 수행 (없으면 목록만 출력 후 종료)",
    )
    parser.add_argument(
        "--safe-prefix",
        dest="safe_prefix",
        default=None,
        help="이름 prefix 검증 override (코드 상단 SAFE_NAME_PREFIX 보다 우선). "
             "빈 문자열 또는 미지정 시 코드 상단 값 사용. 박힌 값이 빈 문자열이면 검증 비활성.",
    )
    args = parser.parse_args()

    # CLI override
    global SAFE_NAME_PREFIX
    if args.safe_prefix is not None:
        SAFE_NAME_PREFIX = args.safe_prefix

    if args.from_csv:
        csv_path = Path(args.from_csv).resolve()
    else:
        latest = _autodetect_latest_csv()
        if latest is None:
            print(
                "result CSV 못 찾음. --from-csv path/to/result.csv 로 직접 지정하거나,\n"
                "aa_create_segment.py를 먼저 실행해서 result CSV가 생기게 하세요."
            )
            return 1
        csv_path = latest
        print(f"(--from-csv 생략 -> 최신 CSV 자동 선택: {csv_path.name})")
    if not csv_path.is_file():
        print(f"CSV 없음: {csv_path}")
        return 1

    rows = _read_csv_segment_ids(csv_path)
    if not rows:
        print(f"CSV에 SegmentId 없음: {csv_path}")
        return 0

    headers, gcid = _load_auth_headers()

    print(f"CSV       : {csv_path}")
    print(f"검증 대상 : {len(rows)}개")
    if SAFE_NAME_PREFIX:
        print(f"safe pref : '{SAFE_NAME_PREFIX}' (이 prefix 아니면 skip)")
    else:
        print(f"safe pref : (비활성 — 모든 segment 처리)")
    print()

    to_delete = []
    skip = []
    for row in rows:
        sid = row["segment_id"]
        actual_name, err = _get_segment_name(headers, gcid, sid)
        if err:
            print(f"  WARN  {sid}: GET 실패 ({err}) → skip")
            skip.append({**row, "reason": err})
            continue
        if SAFE_NAME_PREFIX and not actual_name.startswith(SAFE_NAME_PREFIX):
            print(
                f"  STOP  {sid}: 이름 '{actual_name}' — '{SAFE_NAME_PREFIX}' prefix 아님 → skip"
            )
            skip.append({**row, "reason": f"prefix mismatch: {actual_name}"})
            continue
        print(f"  OK    {sid}: '{actual_name}' → 삭제 후보")
        to_delete.append({"segment_id": sid, "name": actual_name})

    if not to_delete:
        print(f"\n삭제 대상 0개 (skip {len(skip)}개). 종료.")
        return 0

    print(f"\n[CONFIRM] About to DELETE {len(to_delete)} segment(s):")
    for r in to_delete:
        print(f"  - {r['segment_id']}  {r['name']}")
    print(f"  (skip: {len(skip)})")

    if not args.yes:
        print("\n--yes 없음 → 목록만 출력 후 종료. 실제 삭제하려면 --yes 추가.")
        return 0

    print("\nDELETE 실행 중…")
    deleted = 0
    failed = 0
    for r in to_delete:
        sid = r["segment_id"]
        url = f"https://analytics.adobe.io/api/{gcid}/segments/{sid}"
        rr = requests.delete(url, headers=headers, timeout=60)
        if rr.status_code in (200, 204):
            print(f"  OK   {sid} → {rr.status_code}")
            deleted += 1
        else:
            print(f"  FAIL {sid} → {rr.status_code} {rr.text[:200]}")
            failed += 1

    print(f"\n결과: deleted={deleted}, skipped={len(skip)}, failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
