# RESHAPE_standard_v1.0.py
# 2026-06-02  Jonghyun Park w/ Claude
#
# 범용(standard) union 정제 도구 — 특정 디멘션/캠페인에 비종속.
"""
extract_data 추출본을 union 으로 합치는 범용(standard) 정제 — v1.0.

특정 디멘션에 묶이지 않음. campaign, evar26 등 어떤 디멘션이든
extract_data 헤더에서 디멘션 값 컬럼을 자동 감지해서 그대로 처리.

무엇을 하나:
  · 입력 : <폴더>/output/extract_data_{site}_YYMMDD_HHMM.csv
           (column_mapping_*.csv 아님 — 디멘션 항목별 값은 extract_data 에 들어있음)
  · site 별 최신 ts 파일 1개씩만 골라 세로로 union
  · ITEM 컬럼 = segments 의 ';' split 제일 우측 토큰 (양끝 공백 trim)
        예) 'Landing Page; Email' → 'Email'
  · VALUE = value1 값. revenue metric 이면 currency.csv 환율 적용, 그 외 원본 그대로
        (환율 적용된 batch 면 VALUE=환산값 + 'VALUE (원본)' 컬럼 추가)
  · COUNTRY = site_registry 로 site_code → 국가명
  · 출력 : <폴더>/output/_union_standard_{ts}.csv

환율(currency) 처리:
  · revenue metric 행이 하나도 없으면 currency.csv 불필요 → 그냥 진행 (Entries/Visits 등)
  · revenue 행이 있는데 currency.csv 가 없으면 → 정제를 일시정지하고
    "currency.csv 넣고 Enter (q=중단)" 프롬프트로 파일 요청 (조용히 미환산 진행 방지)

처리 순서:
  1) output 스캔 → site 별 최신 extract_data_*.csv 로드
  2) 헤더에서 디멘션 값 컬럼 자동 감지 (DIM_COLUMN 으로 수동 지정도 가능)
  3) revenue metric 있으면 currency.csv 확인 (없으면 일시정지 후 요청)
  4) SITES_FILTER 있으면 그 site 만
  5) ITEM = segments 우측 토큰 / SITE CODE = SITE_CODE_RENAME 치환 (xx_old→xx)
     revenue 면 환율 적용
  6) (옵션) DROP_ZERO_VALUE 면 VALUE==0 행 제외
  7) _union_standard_{ts}.csv 저장

출력 컬럼:
  TIER, SUBS, COUNTRY, SITE CODE, ITEM, VALUE, [VALUE (원본)],
  rsid, start_date, end_date, value_n, <디멘션>, segments [, reportlet]
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from site_registry import lookup_site

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_DIR  = SCRIPT_DIR / "output"
OUTPUT_DIR = SCRIPT_DIR / "output"

# 처리 site 제한 (CSV 안의 site_code 기준). 빈 리스트면 최신 batch 의 모든 site.
SITES_FILTER: list[str] = []

# 출력 파일명 → _union_standard_{yymmdd_hhmm}.csv
OUTPUT_BASENAME = "_union_standard"

# ─── ITEM = segments 의 ';' split 제일 우측 토큰 (trim) ──────────────
# segments 컬럼이 'A; B; Email' 처럼 구분자로 묶여 있을 때, 제일 우측 값을 ITEM 으로.
# 양끝 공백은 strip 으로 제거.
#   예: 'Landing Page; Email'       → 'Email'
#       'Landing Page; Paid Search' → 'Paid Search'
SEG_SPLIT_CHAR = ";"

# ─── 디멘션 컬럼 ────────────────────────────────────────────────────
# extract_data 안에서 디멘션 항목 값을 담은 컬럼명.
#   "" (기본) → 자동 감지: 헤더에서 'itemId' 다음 컬럼 (campaign, evar26 등)
#   수동 지정도 가능: 예) "campaign", "evar26"
DIM_COLUMN = ""
# 출력 CSV 에 쓸 디멘션 컬럼 헤더명. "" 면 위에서 감지/지정한 소스 컬럼명 그대로 사용.
#   예) "CID" 로 바꾸고 싶으면 여기 지정.
DIM_OUTPUT_HEADER = ""

# ─── site_code 치환 (출력 SITE CODE 마지막 정리) ───────────────────
# 출력 SITE CODE 값에서 아래 매핑대로 치환. 예: xx_old → xx.
# COUNTRY lookup 은 원본 site_code 로 수행하고, SITE CODE 표기만 치환.
SITE_CODE_RENAME: dict[str, str] = {"xx_old": "xx"}

# ─── 환율 적용 (revenue metric 한정) ────────────────────────────────
# True 면 metric 에 CURRENCY_METRIC_KEYWORD 포함된 행에 currency.csv 환율 적용.
#   · revenue 행이 하나도 없으면 currency.csv 불필요 (자동 skip)
#   · revenue 행이 있는데 currency.csv 없으면 → 정제 일시정지 후 파일 요청 (q=중단)
# Entries/Visits 등 비-금액 metric 은 환율 무관 (rate=1.0).
APPLY_CURRENCY = True
CURRENCY_CSV = SCRIPT_DIR / "currency.csv"
CURRENCY_METRIC_KEYWORD = "revenue"   # 소문자 substring 매칭

# ─── VALUE==0 행 제외 (옵션) ────────────────────────────────────────
# True 면 VALUE 가 0 인 행을 출력에서 제외. False(기본) 면 0 행도 전부 유지.
DROP_ZERO_VALUE = False

# ─── 검수용 reportlet 컬럼 ──────────────────────────────────────────
# True 면 출력 CSV 제일 우측에 reportlet 컬럼 추가 (어떤 reportlet 에서 왔는지 검수용).
INCLUDE_REPORTLET = True

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
RE_TS_FILE = re.compile(r"^extract_data_(.+)_(\d{6}_\d{4})\.csv$")


def _ts_now() -> str:
    return datetime.now().strftime("%y%m%d_%H%M")


def detect_dim_column(fieldnames) -> str:
    """extract_data 헤더에서 디멘션 값 컬럼 자동 감지.
    구조: ...,itemId,<DIM>,value_n,... → 'itemId' 다음 컬럼. DIM_COLUMN 지정 시 그걸 우선."""
    if DIM_COLUMN:
        return DIM_COLUMN
    fn = list(fieldnames or [])
    if "itemId" in fn:
        i = fn.index("itemId")
        if i + 1 < len(fn) and fn[i + 1] != "value_n":
            return fn[i + 1]
    if "value_n" in fn:  # fallback: value_n 직전
        j = fn.index("value_n")
        if j - 1 >= 0 and fn[j - 1] != "itemId":
            return fn[j - 1]
    return ""


def load_currency_map(path: Path) -> dict[tuple[str, str], float]:
    """(site_code, year_str) → rate. header 의 'YYYY-MM-DD' 컬럼 → YYYY 매칭.
    파일 없거나 비면 빈 dict (조용히)."""
    out: dict[tuple[str, str], float] = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        try:
            header = next(r)
        except StopIteration:
            return out
        date_cols = []
        for idx, h in enumerate(header):
            m = re.match(r"^(\d{4})-\d{2}-\d{2}$", h.strip())
            if m:
                date_cols.append((idx, m.group(1)))
        for row in r:
            if not row or not row[0].strip():
                continue
            site = row[0].strip()
            for col_idx, year in date_cols:
                try:
                    val = row[col_idx].strip()
                except IndexError:
                    continue
                if not val:
                    continue
                try:
                    out[(site, year)] = float(val)
                except ValueError:
                    pass
    return out


def item_from_segments(segments: str) -> str:
    """segments 의 SEG_SPLIT_CHAR 마지막 토큰을 strip 해서 반환. 비면 ''."""
    if not segments:
        return ""
    return segments.split(SEG_SPLIT_CHAR)[-1].strip()


def find_latest_per_site(input_dir: Path) -> tuple[list[Path], dict[str, str]]:
    """site 별로 각자 최신 ts 의 extract_data csv 1개만 반환.
    return: (paths_list, {site: ts})"""
    by_site: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for p in input_dir.glob("extract_data_*.csv"):
        m = RE_TS_FILE.match(p.name)
        if not m:
            continue
        by_site[m.group(1)].append((m.group(2), p))
    if not by_site:
        raise FileNotFoundError(f"{input_dir} 에 extract_data_*_YYMMDD_HHMM.csv 없음")
    paths: list[Path] = []
    site_to_ts: dict[str, str] = {}
    for site, entries in by_site.items():
        entries.sort(key=lambda x: x[0])
        latest_ts, latest_path = entries[-1]
        paths.append(latest_path)
        site_to_ts[site] = latest_ts
    return sorted(paths), site_to_ts


# ────────────────────────────────────────────────────────────────
def process() -> int:
    # 1) site 별 최신 extract_data_*.csv 1개씩 pick
    files, site_to_ts = find_latest_per_site(INPUT_DIR)
    print(f"[per-site latest] {len(files)} sites:")
    for p in files:
        m = RE_TS_FILE.match(p.name)
        site = m.group(1) if m else "?"
        print(f"   - {p.name}  (ts={site_to_ts.get(site, '?')})")

    # 2) 모든 row load + 디멘션 컬럼 감지 (첫 파일 헤더 기준)
    rows: list[dict] = []
    dim_col: str | None = None
    for p in files:
        with open(p, encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            if dim_col is None:
                dim_col = detect_dim_column(r.fieldnames)
            for row in r:
                rows.append(row)
    if not dim_col:
        print("[ERROR] 디멘션 값 컬럼을 못 찾음 (헤더에 itemId/value_n 확인). DIM_COLUMN 수동 지정 필요.")
        return 1
    dim_header = DIM_OUTPUT_HEADER or dim_col
    print(f"[dim] 디멘션 값 컬럼 = {dim_col!r} → 출력 헤더 {dim_header!r}")
    print(f"[load] total rows: {len(rows)}")

    # 환율 — revenue metric 행이 있을 때만 currency.csv 필요. 없으면 일시정지 후 요청.
    has_revenue = any(CURRENCY_METRIC_KEYWORD in (r.get("metric") or "").lower() for r in rows)
    currency: dict[tuple[str, str], float] = {}
    apply_fx = APPLY_CURRENCY and has_revenue
    if apply_fx:
        currency = load_currency_map(CURRENCY_CSV)
        while not currency:
            print(f"\n[일시정지] revenue metric 이 있는데 환율 파일이 없습니다:")
            print(f"           {CURRENCY_CSV}")
            ans = input("  → currency.csv 를 같은 폴더에 넣고 Enter (q=중단): ").strip().lower()
            if ans == "q":
                print("  중단됨 (currency.csv 미제공).")
                return 1
            currency = load_currency_map(CURRENCY_CSV)
        print(f"[currency] {len(currency)} (site×year) entries — revenue 행에 환율 적용")
    elif APPLY_CURRENCY:
        print("[currency] revenue metric 없음 → 환율 적용 skip")

    # 출력 컬럼 순서 (dim_header 확정 후 구성). 환율 적용 batch 면 'VALUE (원본)' 추가.
    base_headers = ["TIER", "SUBS", "COUNTRY", "SITE CODE", "ITEM", "VALUE"]
    if apply_fx:
        base_headers.append("VALUE (원본)")
    base_headers += ["rsid", "start_date", "end_date", "value_n", dim_header, "segments"]
    out_headers = base_headers + (["reportlet"] if INCLUDE_REPORTLET else [])

    # 3) site filter
    if SITES_FILTER:
        wanted = {s.strip().lower() for s in SITES_FILTER}
        rows = [r for r in rows if (r.get("site_code") or "").strip().lower() in wanted]
        print(f"[filter] SITES_FILTER={SITES_FILTER} -> {len(rows)} rows")

    # 4) 출력 row 생성
    country_cache: dict[str, str] = {}
    def country_of(site_code: str) -> str:
        if site_code not in country_cache:
            try:
                country_cache[site_code] = lookup_site(site_code).country or ""
            except Exception:
                country_cache[site_code] = ""
        return country_cache[site_code]

    out_rows: list[dict] = []
    n_zero_dropped = 0

    for r in rows:
        site       = (r.get("site_code")  or "").strip()
        segments   = (r.get("segments")   or "").strip()
        start_date = (r.get("start_date") or "").strip()
        end_date   = (r.get("end_date")   or "").strip()
        rsid       = (r.get("rsid")       or "").strip()
        value_n    = (r.get("value_n")    or "").strip()
        metric     = (r.get("metric")     or "").strip()
        dim_val    = (r.get(dim_col)      or "").strip()
        reportlet  = (r.get("reportlet")  or "").strip()
        raw_val    = (r.get("value1")     or "").strip()   # extract_data 의 값 컬럼은 항상 'value1'

        # 숫자 변환 (원본값)
        try:
            origin = float(raw_val) if raw_val else 0.0
        except ValueError:
            origin = 0.0

        # 환율 — revenue metric 에만 적용 (end_date 연도로 site×year rate lookup)
        rate = 1.0
        if apply_fx and CURRENCY_METRIC_KEYWORD in metric.lower():
            year = end_date[:4] if end_date else ""
            rate = (currency.get((site.lower(), year))
                    or currency.get((site, year))
                    or 1.0)
        value = origin * rate

        # VALUE==0 행 제외 (옵션)
        if DROP_ZERO_VALUE and value == 0.0:
            n_zero_dropped += 1
            continue

        # ITEM = segments 의 ';' 마지막 토큰 (trim)
        item = item_from_segments(segments)

        # 출력 SITE CODE 치환 (xx_old → xx 등). COUNTRY 는 원본 site 로 lookup.
        site_disp = SITE_CODE_RENAME.get(site, site)

        row_out = {
            "TIER": "", "SUBS": "",
            "COUNTRY": country_of(site),
            "SITE CODE": site_disp,
            "ITEM": item,
            "VALUE": value,
            "rsid": rsid,
            "start_date": start_date,
            "end_date": end_date,
            "value_n": value_n,
            dim_header: dim_val,
            "segments": segments,
        }
        if apply_fx:
            row_out["VALUE (원본)"] = origin
        if INCLUDE_REPORTLET:
            row_out["reportlet"] = reportlet
        out_rows.append(row_out)

    # 5) 저장
    def _fmt_num(v):
        if v == "" or v is None:
            return ""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return v
        return int(fv) if fv == int(fv) else round(fv, 2)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{OUTPUT_BASENAME}_{_ts_now()}.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_headers, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in out_rows:
            r["VALUE"] = _fmt_num(r.get("VALUE", ""))
            if "VALUE (원본)" in r:
                r["VALUE (원본)"] = _fmt_num(r.get("VALUE (원본)", ""))
            w.writerow(r)
    print(f"\n[save] {out_path}")
    print(f"  output rows : {len(out_rows)}")
    if DROP_ZERO_VALUE:
        print(f"  VALUE==0 제외: {n_zero_dropped} rows")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(process())
