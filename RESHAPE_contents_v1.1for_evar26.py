# RESHAPE_contents_v1.3for_evar26.py
# 2026-05-20  Jonghyun Park w/ Claude
# 1.1 → 1.2 변경: ITEM 우측에 KV_order 컬럼 추가 (evar26 의 ':' split 2번째 토큰)
# 1.2 → 1.3 변경: ref CSV (RESHAPE_contents_ref_scenario_*.csv) 기반 ITEM 매핑 추가.
#                 evar26 값에 ref CSV B열 키워드 중 하나라도 contains 매칭되면
#                 ref CSV A열 (KF_name) 을 ITEM 으로. 매칭 안 되면 기존 SEGMENT_TO_ITEM_MAPPING fallback.
#
# RESHAPE_contents_v1.0.py 의 사본 — evar26 dimension 전용.
# 정제 대상: data_extract/output/extract_data_{site}_YYMMDD_HHMM.csv
# (column_mapping_*.csv 가 아니라 extract_data_*.csv — 구조가 다름)
"""
extract_data_v2.py 가 떨군 extract_data_{site}_YYMMDD_HHMM.csv 들을 union 형태로 정제.
evar26 dimension 전용 (panel: Content Analysis, metric: Visits).

차이 (v1.0 대비):
  · 입력 파일 패턴이 다름 (extract_data_*.csv)
  · 레포트넘버 (REPORT NO.) 컬럼 없음 — 사용자 요구
  · device 컬럼 없음 (panel 안에 device 구분 없음) — DEVICE TYPE 제거
  · metric 은 Visits 뿐 — Order/Revenue 합산 / 환율 / app_O_X 로직 제거
  · ITEM 매핑은 segments 4 가지 → placeholder (사용자 직접 채움)
    · value1: 'v26 starts with SCENARIO'  (총합)
    · value2: 'SCENARIO_cc09kv01 Click'
    · value3: 'SCENARIO_cc09kv02 Click'
    · value4: 'SCENARIO_cc09kv03 Click'

흐름:
  1) output 폴더 스캔 → 최신 YYMMDD_HHMM batch 의 extract_data_*.csv 만 로드
  2) SITES_FILTER 있으면 그 site 만
  3) ITEM 매핑 — SEGMENT_TO_ITEM_MAPPING 의 placeholder 적용
  4) 출력 컬럼 재배치 → _union_contents_evar26_{ts}.csv
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

# ref CSV — evar26 키워드 매칭으로 ITEM 결정. 같은 폴더의 RESHAPE_contents_ref_scenario_*.csv
# 중 최신 파일 자동 pick. 없으면 ref 매칭 비활성 (SEGMENT_TO_ITEM_MAPPING 만 사용).
REF_CSV_PATTERN = "RESHAPE_contents_ref_scenario_*.csv"

# 처리 site 제한 (CSV 안의 site_code 기준). 빈 리스트면 최신 batch 의 모든 site.
SITES_FILTER: list[str] = []

# 출력 파일명
OUTPUT_BASENAME = "_union_contents_evar26"  # → _union_contents_evar26_{yymmdd_hhmm}.csv

# ─── ITEM 매핑 (segments → ITEM placeholder) ────────────────────────
# 데이터 안의 segments 4 가지에 따라 ITEM 값을 매핑.
# 1번 (총합 segment) 만 의미 있는 default 이름으로 채우고, 2~4 는 placeholder.
# 사용자가 실제 ITEM 명으로 직접 교체.
SEGMENT_TO_ITEM_MAPPING: dict[str, str] = {
    # value1 (총합) 만 segments 매핑. evar26 dimension 값이 (summary) 라 ref 매칭 불가능 →
    # 여기서 fallback.
    "v26 starts with SCENARIO": "0.Total SCENARIO Click",
    # value2/3/4 (detail) 은 ref CSV (evar26 키워드 → KF_name) 매칭으로 처리.
    # 위치 기반 매핑 (SCENARIO_cc09kv01/02/03 Click → 1/2/3) 은 부정확해서 제거.
}

# 매핑 안 된 segments 에 대한 fallback (보통 안 나옴 — 위 4개 외 segments 등장 시 디버그용)
ITEM_FALLBACK = "(unmapped)"

# ─── 환율 적용 (Visit metric 이면 자동 skip) ─────────────────────────
# True 면 currency.csv 로드해서 Revenue / Order metric 에만 환율 적용.
# 현재 데이터는 metric=Visits 만 → 환율 영향 없음.
# currency.csv 없거나 비어있으면 경고만 출력 (Visits 만이면 무시 가능).
APPLY_CURRENCY = True

# ─── 검수용 reportlet 컬럼 ──────────────────────────────────────────
# True 면 출력 CSV 의 제일 우측 컬럼에 reportlet 추가 (어떤 reportlet 에서 왔는지 검수용).
INCLUDE_REPORTLET = True

# 출력 컬럼 순서 (REPORT NO. / DEVICE TYPE / TYPE 제거 — Visits 만이라 불필요)
# KV_order 컬럼: evar26 의 ':' split 2번째 토큰 — Excel 의 =INDEX(TEXTSPLIT(L2,":"),2) 와 동일.
_BASE_HEADERS = [
    "TIER", "SUBS",
    "COUNTRY", "SITE CODE", "ITEM", "KV_order",
    "VALUE", "VALUE (원본)",
    "rsid", "start_date", "end_date", "value_n", "evar26", "segments",
]
OUTPUT_HEADERS = _BASE_HEADERS + (["reportlet"] if INCLUDE_REPORTLET else [])

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
RE_TS_FILE = re.compile(r"^extract_data_(.+)_(\d{6}_\d{4})\.csv$")


def _ts_now() -> str:
    return datetime.now().strftime("%y%m%d_%H%M")


def load_currency_map(path: Path) -> dict[tuple[str, str], float]:
    """(site_code, year_str) → rate. header 의 'YYYY-MM-DD' 컬럼 → YYYY 매칭.
    파일 없으면 빈 dict + 경고."""
    out: dict[tuple[str, str], float] = {}
    if not path.exists():
        print(f"[WARN] currency.csv 없음: {path}")
        print(f"       Visits 만 처리하면 무시 가능. Revenue/Order metric 발생 시 환율 미적용.")
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        header = next(r)
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


def load_ref_keyword_map(script_dir: Path, pattern: str) -> tuple[Path | None, dict[str, list[str]]]:
    """RESHAPE_contents_ref_scenario_*.csv 중 최신 파일 로드.
    구조: A열 KF_name, B열 evar26 (줄바꿈으로 구분된 키워드 list)
    Returns: (선택된 파일 경로, {KF_name: [keyword list]}) — 파일 없으면 (None, {})."""
    candidates = sorted(script_dir.glob(pattern))
    if not candidates:
        print(f"[WARN] ref CSV 없음 (pattern: {pattern}) — ref 매칭 비활성, segments 매핑만 사용")
        return None, {}
    ref_path = candidates[-1]  # 사전순 마지막 (timestamp 포함이라 최신)
    out: dict[str, list[str]] = {}
    with open(ref_path, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        try:
            next(r)  # header skip
        except StopIteration:
            return ref_path, out
        for row in r:
            if not row or not row[0].strip():
                continue
            kf_name = row[0].strip()
            evar_raw = row[1] if len(row) > 1 else ""
            # 줄바꿈 (\n, \r\n 둘 다) 으로 split 후 strip
            keywords = [k.strip() for k in re.split(r"\r?\n", evar_raw) if k.strip()]
            if keywords:
                out[kf_name] = keywords
    return ref_path, out


def lookup_evar26_kf(evar26_val: str, keyword_map: dict[str, list[str]]) -> str | None:
    """evar26 값에 keyword 중 하나라도 contains 매칭되면 KF_name 리턴. 없으면 None.
    대소문자 무시 substring 매칭. dict 순서대로 첫 매치 채택."""
    if not evar26_val or not keyword_map:
        return None
    v_lower = evar26_val.lower()
    for kf_name, keywords in keyword_map.items():
        for kw in keywords:
            if kw.lower() in v_lower:
                return kf_name
    return None


def find_latest_batch(input_dir: Path) -> tuple[str, list[Path]]:
    """output/ 안의 extract_data_*_YYMMDD_HHMM.csv 중 최신 timestamp batch 반환."""
    by_ts: dict[str, list[Path]] = defaultdict(list)
    for p in input_dir.glob("extract_data_*.csv"):
        m = RE_TS_FILE.match(p.name)
        if not m:
            continue
        ts = m.group(2)
        by_ts[ts].append(p)
    if not by_ts:
        raise FileNotFoundError(f"{input_dir} 에 extract_data_*_YYMMDD_HHMM.csv 없음")
    latest_ts = max(by_ts.keys())
    return latest_ts, sorted(by_ts[latest_ts])


def lookup_item(segments: str) -> str:
    """segments → ITEM 매핑. SEGMENT_TO_ITEM_MAPPING 에 정의 없으면 fallback."""
    s = (segments or "").strip()
    return SEGMENT_TO_ITEM_MAPPING.get(s, ITEM_FALLBACK)


# ────────────────────────────────────────────────────────────────
def process() -> int:
    # 1) 최신 batch 파일들
    latest_ts, files = find_latest_batch(INPUT_DIR)
    print(f"[batch] 최신 timestamp: {latest_ts}  (파일 {len(files)}개)")
    for p in files[:5]:
        print(f"   - {p.name}")
    if len(files) > 5:
        print(f"   ... +{len(files) - 5}")

    # 2) currency 로드 (Visits 만이면 무시 가능)
    currency = load_currency_map(CURRENCY_CSV) if APPLY_CURRENCY else {}
    if APPLY_CURRENCY and currency:
        print(f"[currency] {len(currency)} (site×year) entries")

    # 2.5) ref CSV (evar26 keyword → KF_name) 로드
    ref_path, ref_map = load_ref_keyword_map(SCRIPT_DIR, REF_CSV_PATTERN)
    if ref_path:
        total_kw = sum(len(v) for v in ref_map.values())
        print(f"[ref] {ref_path.name} — {len(ref_map)} KF_name / {total_kw} 키워드")
        for kf, kws in ref_map.items():
            print(f"      {kf}: {kws}")

    # 3) 모든 row load
    rows: list[dict] = []
    for p in files:
        with open(p, encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append(row)
    print(f"[load] total rows: {len(rows)}")

    # 3) site filter
    if SITES_FILTER:
        wanted = {s.strip().lower() for s in SITES_FILTER}
        rows = [r for r in rows if (r.get("site_code") or "").strip().lower() in wanted]
        print(f"[filter] SITES_FILTER={SITES_FILTER} → {len(rows)} rows")

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
    unmapped_segs: set[str] = set()

    for r in rows:
        site       = (r.get("site_code")  or "").strip()
        segments   = (r.get("segments")   or "").strip()
        start_date = (r.get("start_date") or "").strip()
        end_date   = (r.get("end_date")   or "").strip()
        rsid       = (r.get("rsid")       or "").strip()
        value_n    = (r.get("value_n")    or "").strip()
        evar26     = (r.get("evar26")     or "").strip()
        metric     = (r.get("metric")     or "").strip()
        reportlet  = (r.get("reportlet")  or "").strip()
        raw_val    = (r.get("value1")     or "").strip()

        # 숫자 변환
        try:
            origin_val = float(raw_val) if raw_val else 0.0
        except ValueError:
            origin_val = 0.0

        # 환율 — Revenue / Order metric 에만 적용. Visits 면 1.0.
        rate = 1.0
        if APPLY_CURRENCY and "revenue" in metric.lower():
            year = end_date[:4] if end_date else ""
            rate = (currency.get((site.lower(), year))
                    or currency.get((site, year))
                    or 1.0)

        # ITEM 결정 — (summary) row 는 공란, 그 외 ref CSV 매칭 우선, 마지막 segments fallback
        item = None
        if evar26.strip().lower() == "(summary)":
            item = ""   # AA 의 summary row (evar26 데이터 없음) — ITEM 공란
        else:
            ref_hit = lookup_evar26_kf(evar26, ref_map) if ref_map else None
            if ref_hit:
                item = ref_hit
            else:
                item = lookup_item(segments)
                if item == ITEM_FALLBACK:
                    unmapped_segs.add(segments)

        # KV_order — evar26 의 ':' split 2번째 토큰. Excel =INDEX(TEXTSPLIT(L2,":"),2) 와 동일.
        # split 결과가 2개 미만이면 빈 값.
        ev_parts = evar26.split(":") if evar26 else []
        kv_order = ev_parts[1].strip() if len(ev_parts) >= 2 else ""

        row_out = {
            "TIER": "", "SUBS": "",
            "COUNTRY": country_of(site),
            "SITE CODE": site,
            "ITEM": item,
            "KV_order": kv_order,
            "VALUE": origin_val * rate,
            "VALUE (원본)": origin_val,
            "rsid": rsid,
            "start_date": start_date,
            "end_date": end_date,
            "value_n": value_n,
            "evar26": evar26,
            "segments": segments,
        }
        if INCLUDE_REPORTLET:
            row_out["reportlet"] = reportlet
        out_rows.append(row_out)

    # 5) 저장
    NUM_COLS = {"VALUE", "VALUE (원본)"}
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
    if unmapped_segs:
        print(f"  ⚠️ unmapped segments ({len(unmapped_segs)} 종) — SEGMENT_TO_ITEM_MAPPING 에 추가 필요:")
        for s in sorted(unmapped_segs):
            print(f"    - {s!r}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(process())
