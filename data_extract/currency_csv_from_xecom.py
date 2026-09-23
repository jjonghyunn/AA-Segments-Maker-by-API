# currency_csv_from_xecom.py
# 2026-09-23  Jonghyun Park w/ Claude
#
# extract 폴더의 sites_input.csv 에서 end_date 최댓값을 기준일로 잡아,
# xe.com 환율표(https://www.xe.com/currencytables/)의 기준일 + 1년 전 USD 환율로
# 그 폴더에 currency.csv (site_code, currency_code, <기준일>, <1년전>) 를 만든다.
# 값 = USD per unit (현지통화 1단위의 USD 가치) — RESHAPE_* 의 load_currency_map() 이 읽는 형식.
#
# 두 가지로 쓴다.
#   1) RESHAPE_* 가 자동 호출 — revenue metric 이 있을 때 ensure_currency_csv() 를 부른다.
#      RESHAPE 는 이 파일을 **자기 폴더 → 상위 폴더** 순으로 찾으므로, data_extract 폴더(또는
#      cutoff 폴더) 최상단에 1개만 두면 하위 폴더 RESHAPE 들이 같이 쓴다. 폴더째 복사해도 따라간다.
#   2) 직접 실행:
#        python currency_csv_from_xecom.py <extract 폴더>            # 이미 최신이면 skip
#        python currency_csv_from_xecom.py <extract 폴더> --force    # 무조건 다시 받음
#      (인자 없으면 폴더 경로 입력 프롬프트)
#
# currency.csv 헤더의 두 날짜가 이미 [기준일, 1년전] 이면 다시 받지 않는다 (네트워크 안 씀).
# 받기에 실패하면(미래 날짜 404, 네트워크 등) SystemExit — RESHAPE 쪽은 이를 잡아 기존 파일로 진행한다.

import csv
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# ════ 사용자가 바꿔야 하는 부분 ════
# ─── 입력 / 출력 ───
SITE_MAP_CSV = SCRIPT_DIR / "currency_code_by_site.csv"   # site_code, currency_code (이 스크립트와 같은 폴더)
SITES_INPUT_NAME = "sites_input.csv"                      # extract 폴더 안 입력 파일명
END_DATE_COLUMN = "end_date"
OUTPUT_NAME = "currency.csv"                              # extract 폴더에 저장 (덮어쓰기)

# ─── xe.com ───
XE_URL = "https://www.xe.com/currencytables/"
BASE_CURRENCY = "USD"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 30

# ─── 계산 / 포맷 ───
YEARS_BACK = 1          # 비교 날짜 = 기준일 - N년
SIG_DIGITS = 10         # 값 유효숫자 자리수 (지수표기 없이 소수로 출력)
MISSING_VALUE = "#N/A"  # xe.com 표에 없는 통화

# ════ 내부 사용 ════
DATE_FORMATS = ("%Y-%m-%d", "%y%m%d")
COMMENT_PREFIX = "#"
LOG_PREFIX = "[currency-auto]"


def parse_date(text):
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def years_before(d, n):
    try:
        return d.replace(year=d.year - n)
    except ValueError:  # 2/29 → 2/28
        return d.replace(year=d.year - n, day=28)


def fmt_num(v):
    out = format(Decimal(format(float(v), f".{SIG_DIGITS}g")), "f")
    return out.rstrip("0").rstrip(".") if "." in out else out


def max_end_date(sites_input):
    """sites_input.csv 의 end_date 최댓값. '#' 주석행·빈 end_date(reuse 행)는 건너뜀."""
    if not sites_input.exists():
        raise SystemExit(f"{LOG_PREFIX} {sites_input} 없음")
    with open(sites_input, encoding="utf-8-sig", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith(COMMENT_PREFIX)]
    reader = csv.DictReader(lines)
    if END_DATE_COLUMN not in (reader.fieldnames or []):
        raise SystemExit(f"{LOG_PREFIX} {sites_input.name} 에 '{END_DATE_COLUMN}' 컬럼 없음")
    dates, bad = [], []
    for row in reader:
        raw = (row.get(END_DATE_COLUMN) or "").strip()
        if not raw:
            continue
        d = parse_date(raw)
        if d is None:
            bad.append(f"{row.get('site_code', '')}={raw}")
        else:
            dates.append(d)
    if bad:
        print(f"{LOG_PREFIX} ⚠ 날짜 형식을 못 읽어 제외한 end_date: {', '.join(bad)}")
    if not dates:
        raise SystemExit(f"{LOG_PREFIX} {sites_input.name} 에 유효한 {END_DATE_COLUMN} 가 없음")
    return max(dates)


def current_header_dates(path):
    """기존 currency.csv 헤더의 날짜 컬럼들 (없거나 못 읽으면 [])."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f), [])
    except OSError:
        return []
    return [h.strip() for h in header if parse_date(h) and len(h.strip()) == 10]


def fetch_rates(d):
    """{currency_code: usd_per_unit}"""
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(
        XE_URL,
        params={"from": BASE_CURRENCY, "date": d.isoformat()},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    if resp.status_code == 404:
        raise SystemExit(f"{LOG_PREFIX} {d} 환율표 없음(404) — 아직 확정 전인 날짜인지 확인 ({resp.url})")
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    section = soup.find(id="table-section") or soup
    rates = {}
    for tr in section.select("tbody tr"):
        th = tr.find("th")
        tds = tr.find_all("td")
        if th and len(tds) >= 3:
            rates[th.get_text(strip=True)] = tds[2].get_text(strip=True)
    if not rates:
        raise SystemExit(f"{LOG_PREFIX} {d} 환율표 0행 — xe.com 페이지 구조가 바뀌었는지 확인 ({resp.url})")
    return rates


def load_site_map():
    if not SITE_MAP_CSV.exists():
        raise SystemExit(f"{LOG_PREFIX} {SITE_MAP_CSV} 없음 — 이 스크립트와 같은 폴더에 두어야 함")
    with open(SITE_MAP_CSV, encoding="utf-8-sig", newline="") as f:
        return [(r["site_code"].strip(), r["currency_code"].strip().upper())
                for r in csv.DictReader(f) if (r.get("site_code") or "").strip()]


def ensure_currency_csv(extract_dir, out_path=None, force=False):
    """extract_dir 의 sites_input 기준으로 currency.csv 를 만든다. 반환: 'skip' / 'written'.
    실패하면 SystemExit (호출하는 RESHAPE 가 잡아서 기존 파일로 진행)."""
    extract_dir = Path(extract_dir)
    out_path = Path(out_path) if out_path else extract_dir / OUTPUT_NAME

    cur_date = max_end_date(extract_dir / SITES_INPUT_NAME)
    prev_date = years_before(cur_date, YEARS_BACK)
    dates = [cur_date, prev_date]
    want = [d.isoformat() for d in dates]

    if not force and current_header_dates(out_path) == want:
        print(f"{LOG_PREFIX} {out_path.name} 이미 {want[0]} / {want[1]} 기준 → 그대로 사용")
        return "skip"

    print(f"{LOG_PREFIX} 기준일 = {SITES_INPUT_NAME} max {END_DATE_COLUMN}: {want[0]} / 비교일: {want[1]} → xe.com 조회")
    rates = {d: fetch_rates(d) for d in dates}   # 둘 다 받은 뒤에만 쓴다 (반쯤 쓴 파일 방지)

    missing = set()
    out_rows = []
    for site, code in load_site_map():
        row = [site, code]
        for d in dates:
            v = rates[d].get(code)
            if v is None:
                missing.add((code, d.isoformat()))
                row.append(MISSING_VALUE)
            else:
                row.append(fmt_num(v))
        out_rows.append(row)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerow(["site_code", "currency_code"] + want)
        w.writerows(out_rows)
    print(f"{LOG_PREFIX} 저장: {out_path} ({len(out_rows)}행)")

    if missing:
        print(f"{LOG_PREFIX} ⚠ xe.com 표에 없는 통화 — '{MISSING_VALUE}' 로 채움: "
              + ", ".join(f"{c}@{d}" for c, d in sorted(missing)))
    return "written"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    raw = args[0] if args else input("extract 폴더 경로: ")
    extract_dir = Path(raw.strip().strip('"'))
    if not extract_dir.is_dir():
        raise SystemExit(f"ERROR: 폴더 아님 — {extract_dir}")
    ensure_currency_csv(extract_dir, force=force)


if __name__ == "__main__":
    main()
