# RESHAPE_contents_recomm_v1.1.py
# 2026-05-20  Jonghyun Park w/ Claude
# updated: 2026-05-20 19:50  — v1.1: Delayed 합산 수정, expand 제거, US 필터
"""
RESHAPE_contents_v1.0.py 사본 — recomm 전용 정제.

v1.0 대비 v1.1 수정:
  · Delayed 합산 정규식: 하이픈 필수(- Delayed) → 하이픈 옵션(Delayed) 으로 변경
    reportlet "Contents Clicked Delayed Order" 등 매칭 가능
  · Recomm expand (value1 → 15 row 복제) 제거 — value1 은 Campaign Main Visit 그대로 출력
  · non-US site 의 US panel row / US site 의 Global panel row 필터 적용
  · 모든 row 에 extra 컬럼 박음 (최우측).

extract_data_v2_contents.py 가 떨군 column_mapping_*.csv 들을 union 형태로 정제.

흐름:
  1) output 폴더 스캔 → 최신 YYMMDD_HHMM batch 의 column_mapping_*.csv 만 로드
  2) SITES_FILTER 있으면 그 site 만 (CSV 안의 site_code 컬럼 기준)
  3) No Data row 제외
  4) App 없는 site (app_O_X.csv 의 'X') → app/android/ios device row 제외
  5) Order ↔ Delayed Order, Revenue ↔ Delayed Revenue 합산 → origin_only_delayed_value 따로 보존
  6) Revenue 만 환율 적용 (currency.csv, end_date 년도 기준)
  7) ITEM 추출 (segments 마지막 토큰)
  8) 출력 컬럼 재배치 → _union_contents_{ts}.csv

TYPE 5종 모두 보존: Visits / Order / Revenue / Order+Delayed Order / Revenue+Delayed Revenue
SUBS, TIER 컬럼은 빈 값 (나중에 수기 채움)
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

SCRIPT_DIR  = Path(__file__).resolve().parent
INPUT_DIR   = SCRIPT_DIR / "output"
OUTPUT_DIR  = SCRIPT_DIR / "output"

CURRENCY_CSV = SCRIPT_DIR / "currency.csv"
APP_OX_CSV   = SCRIPT_DIR / "app_O_X.csv"

# 처리 site 제한 (CSV 안의 site_code 기준). 빈 리스트면 최신 batch 의 모든 site.
SITES_FILTER: list[str] = []

# 출력 파일명
OUTPUT_BASENAME = "_union_contents"  # → _union_contents_{yymmdd_hhmm}.csv

# ─── 라벨 / 매핑 ────────────────────────────────────────────────────
DEVICE_LABEL: dict[str, str] = {
    "pc":      "PC",
    "mobile":  "Mobile",
    "app":     "App",
    "android": "Android",
    "ios":     "iOS",
}

# metric → REPORT NO.
REPORT_NO_BY_METRIC = {
    "visits":  "Engagement by Contents",
    # 나머지 (order / revenue) → 아래 DEFAULT
}
REPORT_NO_DEFAULT = "Order by Contents"

# 출력 컬럼 순서
OUTPUT_HEADERS = [
    "TIER", "SUBS",
    "COUNTRY", "SITE CODE", "REPORT NO.", "DEVICE TYPE", "TYPE", "ITEM",
    "VALUE", "VALUE (원본)",
    "origin_only_delayed_value",
    "rsid", "start_date", "end_date", "value_n",
    "extra",
]

# ─── Recomm expand 룰 ─────────────────────────────────────────────
# Campaign Main Visit (fallback) row → 15 row 로 expand. 각 row 의 ITEM 형식:
#   "<EXPAND_ITEM_PREFIX><NN>. <RECOMM_NAME>"
# 예: "08. - 01. Top Selling", "08. - 02. Interested Category", ...
EXPAND_ITEM_PREFIX = "08. - "
RECOMM_NAMES: list[str] = [
    "01. Top Selling",
    "02. Interested Category",
    "03. Interested Product",
    "04. Bought After View",
    "05. Demographic Popular",
    "06. Frequently Bought Together",
    "07. Frequently Replaced By Owner",
    "08. Frequently Viewed Together",
    "09. Product Lifetime",
    "10. Sas",
    "11. Search After View",
    "12. Searched By Owner",
    "13. Similar Owner",
    "14. Similar Pageview",
    "15. Theme Category Popular",
]

# 모든 output row 의 extra 컬럼 값
EXTRA_VALUE = "Your Extra Label"  # e.g. "After 14 May", "Before 14 May"

# fallback ITEM 값 set — 이 값 매칭되는 row 가 expand 대상
FALLBACK_ITEMS = {"Campaign Main Visit", "Campaign Main Visit > Order (Visit)", "Campaign Main Visit > Order (Visitor)"}

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
RE_TS_FILE  = re.compile(r"^column_mapping_(.+)_(\d{6}_\d{4})\.csv$")
RE_CAMPAIGN_PREFIX = re.compile(r"\[[^\]]+\]\s*")            # [CAMPAIGN NAME]  [25 YM] 등
RE_CC_PREFIX       = re.compile(r"^CC_\d+\.\s*")             # CC_03.
RE_TRAILING_PAREN  = re.compile(r"\s*\([^)]*\)\s*$")         # 마지막 토큰 끝 괄호 내용 모두 제거

# segments 마지막 토큰에서 "CC_" 검출 후 prefix [XX YY] 와 CC_ 까지 제거하면
# "03. Scenario: Your Daily Sync - 03. New movers" 만 남음
# value1 (총합 row) 의 metric+delayed 매칭용
ITEM_PROP_FALLBACK    = "Campaign Main Visit"
ITEM_ORDER_NON_DELAY  = "Campaign Main Visit > Order (Visit)"
ITEM_ORDER_DELAY      = "Campaign Main Visit > Order (Visitor)"

# Delayed 합산 페어 (reportlet 의 ` - ` 뒤 토큰 기준)
DELAYED_PAIRS = {
    "Delayed Order":   "Order",
    "Delayed Revenue": "Revenue",
}

# ────────────────────────────────────────────────────────────────
def _ts_now() -> str:
    return datetime.now().strftime("%y%m%d_%H%M")


def find_latest_per_site(input_dir: Path) -> tuple[list[Path], dict[str, str]]:
    """site (RE_TS_FILE group 1) 별로 각자 최신 ts column_mapping csv 1 개만 반환.
    한 시점 batch 강제 안 함 — site 별 ts 가 달라도 각자의 최신 사용.

    return: (paths_list, site_to_ts_dict)
    """
    by_site: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for p in input_dir.glob("column_mapping_*.csv"):
        m = RE_TS_FILE.match(p.name)
        if not m:
            continue
        site = m.group(1)
        ts = m.group(2)
        by_site[site].append((ts, p))
    if not by_site:
        raise FileNotFoundError(f"{input_dir} 에 column_mapping_*_YYMMDD_HHMM.csv 없음")
    paths: list[Path] = []
    site_to_ts: dict[str, str] = {}
    for site, entries in by_site.items():
        entries.sort(key=lambda x: x[0])
        latest_ts, latest_path = entries[-1]
        paths.append(latest_path)
        site_to_ts[site] = latest_ts
    return sorted(paths), site_to_ts


def load_currency_map(path: Path) -> dict[tuple[str, str], float]:
    """(site_code, year_str) → rate. year 컬럼은 CSV header 의 'YYYY-MM-DD' 중 YYYY 매칭."""
    out: dict[tuple[str, str], float] = {}
    if not path.exists():
        print(f"[WARN] currency.csv 없음: {path}")
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        header = next(r)
        # header 형식: site_code, currency_code, 2026-05-17, 2025-05-17
        date_cols = []  # [(col_idx, year_str)]
        for idx, h in enumerate(header):
            m = re.match(r"^(\d{4})-\d{2}-\d{2}$", h.strip())
            if m:
                date_cols.append((idx, m.group(1)))
        site_idx = 0
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


def load_app_x_sites(path: Path) -> set[str]:
    """App 'X' (론치 안 된) site code set."""
    out: set[str] = set()
    if not path.exists():
        print(f"[WARN] app_O_X.csv 없음: {path}")
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            site = (row.get("site_code") or "").strip()
            flag = (row.get("App 론치 (O/X)") or "").strip().upper()
            if site and flag == "X":
                out.add(site)
    return out


def extract_item(segments: str, metric: str, reportlet: str) -> str | None:
    """ITEM 룰. No Data → None (=row 제외 신호)."""
    if not segments:
        return None
    last = segments.split(";")[-1].strip()
    # No Data row 제외
    if re.search(r"\bno\s*data\b", last, re.IGNORECASE):
        return None

    has_cc = "CC_" in last
    if has_cc:
        # 1) 캠페인 prefix [XX YY] 제거: "[CAMPAIGN NAME] CC_03. ..." → "CC_03. ..."
        #    [US] panel 은 "[CAMPAIGN NAME] US_CC_00. ..." 형식 → 다음 단계에서 US_ 까지 같이 제거
        s = RE_CAMPAIGN_PREFIX.sub("", last, count=1).strip()
        # 2) CC_ 앞의 모든 문자(US_, 공백 등) + "CC_" 자체 제거 → "NN. ..." 만 남김
        #    예: "CC_03. Scenario..."        → "03. Scenario..."
        #    예: "US_CC_00. Content Click..." → "00. Content Click..."
        s = re.sub(r"^.*?CC_", "", s)
        # 3) 끝 괄호 ( ... ) 내용 제거 — (Delayed Purchase), (Visitor), (Web) 등 모두
        while RE_TRAILING_PAREN.search(s):
            s = RE_TRAILING_PAREN.sub("", s).strip()
        return s

    # v1.1: CC_ 없지만 "Product Recommendation -" 패턴 → 그대로 ITEM 으로 사용
    #   Visit 테이블의 개별 recomm 세그먼트에 CC_ prefix 가 없는 구조 대응
    if "Product Recommendation" in last:
        s = RE_CAMPAIGN_PREFIX.sub("", last, count=1).strip()
        # [part_name] US_ 등 prefix 제거
        s = re.sub(r"^(US_)?", "", s).strip()
        while RE_TRAILING_PAREN.search(s):
            s = RE_TRAILING_PAREN.sub("", s).strip()
        return s

    # value1 (총합) 룰 — metric + reportlet 의 Delayed 유무
    metric_l = (metric or "").lower()
    is_visit = "visit" in metric_l
    is_delayed_reportlet = "delayed" in (reportlet or "").lower()
    if is_visit:
        return ITEM_PROP_FALLBACK
    if is_delayed_reportlet:
        return ITEM_ORDER_DELAY
    return ITEM_ORDER_NON_DELAY


def device_label(device: str) -> str:
    return DEVICE_LABEL.get((device or "").strip().lower(), device or "")


def report_no(metric: str) -> str:
    key = (metric or "").lower()
    # "Visits" 안에 "visit" 들어가지만, "Order (purchase event)" 와 명확히 구분
    if "visit" in key:
        return REPORT_NO_BY_METRIC["visits"]
    return REPORT_NO_DEFAULT


def type_label(metric: str, has_delayed_pair: bool) -> str:
    """단일 row 의 TYPE 결정. has_delayed_pair=True 면 합산된 row → 'Order+Delayed Order' 형식."""
    metric_l = (metric or "").lower()
    if "visit" in metric_l:
        return "Visits"
    if "order" in metric_l:
        return "Order+Delayed Order" if has_delayed_pair else "Order"
    if "revenue" in metric_l:
        return "Revenue+Delayed Revenue" if has_delayed_pair else "Revenue"
    return metric or ""


def normalize_reportlet_base(reportlet: str) -> str:
    """reportlet 의 'Delayed ' 만 제거 → metric 토큰은 유지.
    합산 페어 찾기용 join key. Order ↔ Delayed Order, Revenue ↔ Delayed Revenue 만 매칭됨
    (Order ↔ Delayed Revenue 같은 cross-매칭 방지).
    v1.1: 하이픈 없는 "Contents Clicked Delayed Order" 패턴도 매칭."""
    return re.sub(r"[-\s]*Delayed\s+", " ", reportlet or "", flags=re.IGNORECASE).strip()


def normalize_segments_for_join(segments: str) -> str:
    """Order vs Delayed Order 의 segments 가 토큰 끝 괄호 ( ... ) 만 다른 케이스 많음.
    예) '> Order (All Products)' vs '> Order (All Products) (Visitor)'
    예) 'CC_00. xxx (Visit)' vs 'CC_00. xxx (Delayed Purchase)'
    각 토큰의 끝 괄호 모두 반복 제거 → 동일 normalize key 로 join 가능."""
    s = segments or ""
    parts = [p.strip() for p in s.split(";")]
    cleaned = []
    for p in parts:
        while True:
            new = re.sub(r"\s*\([^)]*\)\s*$", "", p).strip()
            if new == p:
                break
            p = new
        cleaned.append(p)
    return "; ".join(cleaned).strip()


# ────────────────────────────────────────────────────────────────
def process() -> int:
    # 1) site 별 최신 column_mapping_*.csv 1 개씩 pick
    files, site_to_ts = find_latest_per_site(INPUT_DIR)
    print(f"[per-site latest] {len(files)} sites — site 별 ts 다르게 잡힘:")
    for p in files:
        m = RE_TS_FILE.match(p.name)
        site = m.group(1) if m else "?"
        print(f"   - {p.name}  (ts={site_to_ts.get(site, '?')})")

    # 2) currency / app_X
    currency = load_currency_map(CURRENCY_CSV)
    app_x_sites = load_app_x_sites(APP_OX_CSV)
    print(f"[currency] {len(currency)} (site×year) entries")
    print(f"[app X] {len(app_x_sites)} sites: {sorted(app_x_sites)[:8]}...")

    # 3) 모든 row load
    rows: list[dict] = []
    for p in files:
        with open(p, encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append(row)
    print(f"[load] total rows: {len(rows)}")

    # 4) site filter
    if SITES_FILTER:
        wanted = {s.strip().lower() for s in SITES_FILTER}
        rows = [r for r in rows if (r.get("site_code") or "").strip().lower() in wanted]
        print(f"[filter] SITES_FILTER={SITES_FILTER} → {len(rows)} rows")

    # 4.5) v1.1: US panel ↔ site 매칭 필터
    #   us site → US panel 만, non-us site → Global panel 만
    US_SITES = {"us"}
    before_panel = len(rows)
    rows = [
        r for r in rows
        if not (
            ((r.get("panel") or "").strip() == "US" and (r.get("site_code") or "").strip().lower() not in US_SITES)
            or ((r.get("panel") or "").strip() == "Global" and (r.get("site_code") or "").strip().lower() in US_SITES)
        )
    ]
    print(f"[panel filter] US/Global 매칭: {before_panel} → {len(rows)} rows")

    # 5) App X site 의 app/android/ios row → value 0 으로 (row 자체는 유지)
    zero_cnt = 0
    for r in rows:
        if ((r.get("site_code") or "").strip().lower() in app_x_sites
                and (r.get("device") or "").strip().lower() in {"app", "android", "ios"}):
            r["data_value"] = "0"
            zero_cnt += 1
    print(f"[app X zero] {zero_cnt} rows 의 data_value=0 처리 (X site + app/android/ios)")

    # 6) Delayed 합산용 lookup 구성
    #    key = (site, device, panel, reportlet_base, value_n, segments_norm)
    delayed_index: dict[tuple, dict] = {}
    for r in rows:
        rep = r.get("reportlet") or ""
        # reportlet 'Delayed Order' / 'Delayed Revenue' 인덱싱 (하이픈 유무 무관)
        m = re.search(r"(Delayed\s+(Order|Revenue))\s*$", rep, re.IGNORECASE)
        if not m:
            continue
        key = (
            (r.get("site_code") or "").strip().lower(),
            (r.get("device")    or "").strip().lower(),
            (r.get("panel")     or "").strip(),
            normalize_reportlet_base(rep),
            (r.get("value_n")   or "").strip(),
            normalize_segments_for_join(r.get("segments") or ""),
        )
        delayed_index[key] = r

    # 7) 출력 row 생성
    out_rows: list[dict] = []
    delayed_used: set[tuple] = set()

    country_cache: dict[str, str] = {}
    def country_of(site_code: str) -> str:
        if site_code not in country_cache:
            try:
                country_cache[site_code] = lookup_site(site_code).country or ""
            except Exception:
                country_cache[site_code] = ""
        return country_cache[site_code]

    for r in rows:
        site = (r.get("site_code") or "").strip()
        device = (r.get("device") or "").strip()
        metric = r.get("metric") or ""
        reportlet = r.get("reportlet") or ""
        segments = r.get("segments") or ""
        start_date = (r.get("start_date") or "").strip()
        end_date = (r.get("end_date") or "").strip()
        rsid = (r.get("rsid") or "").strip()
        value_n = (r.get("value_n") or "").strip()
        raw_val = (r.get("data_value") or "").strip()

        # Delayed reportlet 은 자기 자신을 row 로 안 만듦 (Order/Revenue 본체 row 에서 합산)
        if re.search(r"Delayed\s+(Order|Revenue)\s*$", reportlet, re.IGNORECASE):
            continue

        # ITEM
        item = extract_item(segments, metric, reportlet)
        if item is None:  # No Data row
            continue

        # 숫자 변환
        try:
            origin_val = float(raw_val) if raw_val else 0.0
        except ValueError:
            origin_val = 0.0

        # Delayed pair 찾기 (Order/Revenue 만)
        pair_key = (
            site.lower(), device.lower(),
            (r.get("panel") or "").strip(),
            normalize_reportlet_base(reportlet),
            value_n,
            normalize_segments_for_join(segments),
        )
        delayed_row = delayed_index.get(pair_key)
        delayed_val_only = None
        if delayed_row is not None and re.search(r"(Order|Revenue)\s*$", reportlet, re.IGNORECASE):
            try:
                delayed_val_only = float(delayed_row.get("data_value") or 0)
            except ValueError:
                delayed_val_only = 0.0
            delayed_used.add(pair_key)

        # 환율 (Revenue 만)
        rate = 1.0
        if "revenue" in metric.lower():
            year = end_date[:4] if end_date else ""
            rate = currency.get((site.lower(), year)) \
                or currency.get((site, year)) \
                or 1.0

        # ───── case A: Delayed pair 있는 합산 row ─────
        if delayed_val_only is not None:
            # 1) 일반 (단독) row
            single_value = origin_val * rate
            out_rows.append({
                "TIER": "", "SUBS": "",
                "COUNTRY": country_of(site),
                "SITE CODE": site,
                "REPORT NO.": report_no(metric),
                "DEVICE TYPE": device_label(device),
                "TYPE": type_label(metric, has_delayed_pair=False),
                "ITEM": item,
                "VALUE": single_value,
                "VALUE (원본)": origin_val,
                "origin_only_delayed_value": "",
                "rsid": rsid, "start_date": start_date, "end_date": end_date,
                "value_n": value_n,
            })
            # 2) 합산 row (Order+Delayed Order 또는 Revenue+Delayed Revenue)
            #    value1 (CC_ 없는 총합) row 의 경우 ITEM 을 (Visit) → (Visitor) 로 교체
            item_summed = item
            last_seg = segments.split(";")[-1].strip() if segments else ""
            if "CC_" not in last_seg and "visit" not in metric.lower():
                item_summed = ITEM_ORDER_DELAY
            summed_origin = origin_val + delayed_val_only
            summed_value = summed_origin * rate
            out_rows.append({
                "TIER": "", "SUBS": "",
                "COUNTRY": country_of(site),
                "SITE CODE": site,
                "REPORT NO.": report_no(metric),
                "DEVICE TYPE": device_label(device),
                "TYPE": type_label(metric, has_delayed_pair=True),
                "ITEM": item_summed,
                "VALUE": summed_value,
                "VALUE (원본)": summed_origin,
                "origin_only_delayed_value": delayed_val_only,
                "rsid": rsid, "start_date": start_date, "end_date": end_date,
                "value_n": value_n,
            })
        else:
            # ───── case B: Delayed pair 없음 (Visits 포함) ─────
            value = origin_val * rate
            out_rows.append({
                "TIER": "", "SUBS": "",
                "COUNTRY": country_of(site),
                "SITE CODE": site,
                "REPORT NO.": report_no(metric),
                "DEVICE TYPE": device_label(device),
                "TYPE": type_label(metric, has_delayed_pair=False),
                "ITEM": item,
                "VALUE": value,
                "VALUE (원본)": origin_val,
                "origin_only_delayed_value": "",
                "rsid": rsid, "start_date": start_date, "end_date": end_date,
                "value_n": value_n,
            })

    # v1.1: recomm expand 제거 — value1 (Campaign Main Visit 등) 은 그대로 출력
    #       개별 recomm 값은 이미 value2~17 에 있으므로 expand 불필요

    # 모든 row 에 extra 박음
    for r in out_rows:
        r["extra"] = EXTRA_VALUE

    # 8) 저장 — numeric 컬럼 일관 포맷 (엑셀 text 인식 방지)
    NUM_COLS = {"VALUE", "VALUE (원본)", "origin_only_delayed_value"}
    def _fmt_num(v):
        if v == "" or v is None:
            return ""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return v
        if fv == int(fv):
            return int(fv)
        return round(fv, 2)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{OUTPUT_BASENAME}_{_ts_now()}.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in out_rows:
            for c in NUM_COLS:
                r[c] = _fmt_num(r.get(c, ""))
            w.writerow(r)
    print(f"\n[save] {out_path}")
    print(f"  output rows : {len(out_rows)}")
    print(f"  delayed pair used : {len(delayed_used)}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(process())
