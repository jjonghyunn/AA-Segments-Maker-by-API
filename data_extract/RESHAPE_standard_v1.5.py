# RESHAPE_standard_v1.5.py
# 2026-06-18  Jonghyun Park w/ Claude
# v1.5 (2026-06-18): stack metric_origin + normalized metric (add metric_origin col),
#                    VALUE (원본)->value_origin rename, wide union (_union_standard_wide_*)
#                    with normalized metric as columns. RESHAPE also normalizes from
#                    metric_origin (_normalize_metric) so mixed v3.8 stacks stay consistent.
# v1.4 (2026-06-15): panel/table/reportlet 이름에 키워드(Multi Purchase·Multi Order·
#                    Best Selling Product, 대소문자/언더바 무시) 가 있으면 product_category.yaml
#                    (include/exclude regex) 로 제품코드를 분류해 'category' 컬럼 추가.
#                    · multi(콤마 다제품): category(ACC·Unknown 포함, 알파벳 오름차순, 중복유지)
#                                         + category_non_acc_unknown_excl(ACC·Unknown 제외)
#                    · single(best selling, 단일 제품): category 만 (non_acc 컬럼 빈칸)
#                    미분류 = Unknown. ADD_CATEGORY_COLUMN 으로 on/off. (pyyaml 필요)
# v1.3 (2026-06-12): extract_data_v3.7 파일명 개편 대응 — 입력 패턴을
#                    stack_data_extract_* (신규) + extract_data_* (구버전 호환) 둘 다 인식.
#                    (v3.7 의 table_data_extract_* 가로형은 RESHAPE 입력 아님 — stack 만 사용)
# v1.2 (2026-06-11): 출력 컬럼 추가 — metric (value_n 다음), Panel name (reportlet 왼쪽)
#                    + EXCLUDE_OUTPUT_COLUMNS — 출력에서 뺄 컬럼 선택 옵션
# v1.1 (2026-06-10): extract_data_v3.5 출력 대응 —
#   · breakdown 행(bd{k}_itemId 채워진 행) 처리 모드 BREAKDOWN_ROWS_MODE 추가
#     ("exclude" 기본 = dim1 총계만 union, 이중집계 방지 / "only" / "include")
#   · device / bd{k}_* 컬럼이 입력에 있으면 출력에 passthrough
#
# ※ [LOCAL ONLY / 마이너] 단발성 union 정제용 로컬 도구.
"""
extract_data 추출본을 union 으로 합치는 범용(standard) 정제 — v1.4.

특정 디멘션에 묶이지 않음. cid(campaign), evar26 등 어떤 디멘션이든
extract_data 헤더에서 디멘션 값 컬럼을 자동 감지해서 그대로 처리.

무엇을 하나:
  · 입력 : <폴더>/output/stack_data_extract_{site}_YYMMDD_HHMM.csv
           (구버전 extract_data_{site}_*.csv 도 호환 인식.
            table_data_extract_*.csv 가로형 아님 — 디멘션 항목별 값은 stack(long) 에 들어있음)
  · site 별 최신 ts 파일 1개씩만 골라 세로로 union
  · ITEM 컬럼 = segments 의 ';' split 제일 우측 토큰 (양끝 공백 trim)
        예) 'Landing Page; Email' → 'Email'
  · VALUE = value1 값. revenue metric 이면 currency.csv 환율 적용, 그 외 원본 그대로
        (환율 적용된 batch 면 VALUE=환산값 + 'VALUE (원본)' 컬럼 추가)
  · (v1.4) category : panel/table/reportlet 에 product 키워드 있으면 dim_value 를
        product_category.yaml 로 분류한 카테고리 컬럼 추가 (ADD_CATEGORY_COLUMN)
  · DIM_EXCLUDE_VALUES 일치하는 디멘션값 행 제외 (Unspecified/null/(summary) 등, 대소문자 무시)
  · COUNTRY = site_registry 로 site_code → 국가명
  · 출력 : <폴더>/output/_union_standard_{ts}.csv (long)
           + _union_standard_wide_{ts}.csv (normalized metric as columns)

환율(currency) 처리:
  · revenue metric 행이 하나도 없으면 currency.csv 불필요 → 그냥 진행 (Entries/Visits 등)
  · revenue 행이 있는데 currency.csv 가 없으면 → 정제를 일시정지하고
    "currency.csv 넣고 Enter (q=중단)" 프롬프트로 파일 요청 (조용히 미환산 진행 방지)

product category 분류 (v1.4):
  · panel/table/reportlet 이름에 'Multi Purchase'/'Multi Order'/'Best Selling Product'
    (대소문자·언더바 무시) 가 있으면 제품코드를 product_category.yaml 로 분류.
  · multi (Multi Purchase/Multi Order): dim_value 가 콤마구분 다제품 →
      category = 전체(ACC·Unknown 포함, 알파벳 오름차순, 중복유지)
      category_non_acc_unknown_excl = ACC·Unknown 제외 (알파벳 오름차순, 중복유지)
  · single (Best Selling Product): dim_value 단일 제품 → category 만 (non_acc 빈칸)
  · 어느 카테고리에도 안 걸리면 Unknown.
  · 분류 룰은 product_category.yaml(include/exclude regex) 만 사용 — 파일 순서대로 첫 매칭.

처리 순서:
  1) output 스캔 → site 별 최신 extract_data_*.csv 로드
  2) 헤더에서 디멘션 값 컬럼 자동 감지 (DIM_COLUMN 으로 수동 지정도 가능)
  3) revenue metric 있으면 currency.csv 확인 (없으면 일시정지 후 요청)
  4) SITES_FILTER 있으면 그 site 만
  5) ITEM = segments 우측 토큰 / SITE CODE = SITE_CODE_RENAME 치환 (us_old→us)
     revenue 면 환율 적용 / (v1.4) product 키워드 행이면 category 분류
  6) DIM_EXCLUDE_VALUES 에 일치하는 디멘션값 행 제외 (Unspecified/null/(summary) 등)
  7) (옵션) DROP_ZERO_VALUE 면 VALUE==0 행 제외
  8) _union_standard_{ts}.csv 저장

출력 컬럼:
  TIER, SUBS, COUNTRY, SITE CODE, ITEM, VALUE, [VALUE (원본)],
  rsid, start_date, end_date, value_n, metric, <디멘션>,
  [category, category_non_acc_unknown_excl,] segments,
  Panel name [, reportlet]
  (EXCLUDE_OUTPUT_COLUMNS 에 지정한 컬럼은 출력에서 제외)
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

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
# segments 컬럼이 'A; B; push' 처럼 구분자로 묶여 있을 때, 제일 우측 값을 ITEM 으로.
# 양끝 공백은 strip 으로 제거.
#   예: 'Landing Page; Email'       → 'Email'
#       'Landing Page; Paid Search' → 'Paid Search'
SEG_SPLIT_CHAR = ";"

# ─── 디멘션 컬럼 ────────────────────────────────────────────────────
# extract_data 안에서 디멘션 항목 값을 담은 컬럼명.
#   "" (기본) → 자동 감지: 헤더에서 'itemId' 다음 컬럼 (cid→campaign, contents→evar26 등)
#   수동 지정도 가능: 예) "campaign", "evar26"
DIM_COLUMN = ""
# 출력 CSV 에 쓸 디멘션 컬럼 헤더명. "" 면 위에서 감지/지정한 소스 컬럼명 그대로 사용.
#   예) "CID" 로 바꾸고 싶으면 여기 지정.
DIM_OUTPUT_HEADER = ""

# ─── site_code 치환 (출력 SITE CODE 마지막 정리) ───────────────────
# 출력 SITE CODE 값에서 아래 매핑대로 치환.
# COUNTRY lookup 은 원본 site_code 로 수행하고, SITE CODE 표기만 치환.
SITE_CODE_RENAME: dict[str, str] = {}
# v1.1: `_old` 접미사 일괄 제거 (uk_old→uk, es_old→es, us_old→us …).
# 구 RSID suite 표기용 접미사라 출력 SITE CODE 에선 항상 정규화. False 면 끔.
SITE_CODE_STRIP_OLD = True

# ─── 환율 적용 (revenue metric 한정) ────────────────────────────────
# True 면 metric 에 CURRENCY_METRIC_KEYWORD 포함된 행에 currency.csv 환율 적용.
#   · revenue 행이 하나도 없으면 currency.csv 불필요 (자동 skip)
#   · revenue 행이 있는데 currency.csv 없으면 → 정제 일시정지 후 파일 요청 (q=중단)
# Entries/Visits 등 비-금액 metric 은 환율 무관 (rate=1.0).
APPLY_CURRENCY = True
CURRENCY_CSV = SCRIPT_DIR / "currency.csv"
# ★ metric 에 이 키워드가 '포함'되기만 하면(부분일치, 대소문자 무시) 환율 적용.
#   예: 'Revenue', 'Revenue (KRW)', 'Total Revenue', 'revenue per visit' → 전부 매칭
#       (정확히 'revenue' 일 필요 없음 — revenue 글자가 들어가면 됨)
CURRENCY_METRIC_KEYWORD = "revenue"   # 부분일치(substring), 대소문자 무시

# ─── product category 분류 (v1.4) ──────────────────────────────────
# panel/table/reportlet 이름에 아래 키워드가 있으면 제품코드를
# product_category.yaml 로 분류해 category 컬럼을 출력에 추가.
# (yaml 이 없을 때: 키워드 매칭 행이 있으면 경고만 하고 분류 skip,
#  키워드 매칭 행이 없으면 조용히 pass — 둘 다 정제는 정상 진행)
ADD_CATEGORY_COLUMN = True
# 분류 룰 yaml (divisions → categories → include/exclude regex). 같은 폴더 기본값.
CATEGORY_YAML = SCRIPT_DIR / "product_category.yaml"
# 어느 카테고리 include 에도 안 걸리는 제품코드 라벨.
CATEGORY_UNKNOWN_LABEL = "Unknown"
# 멀티 모드 dim_value 안에서 여러 제품코드를 나누는 구분자.
CATEGORY_MULTI_SPLIT = ","
# 분류 결과(여러 카테고리)를 한 셀에 조인할 구분자 (알파벳 오름차순 정렬 후 조인).
CATEGORY_JOIN = ","
# 키워드(소문자·언더바→공백 정규화 후 substring 매칭) → 모드. 리스트 순서 = 우선순위.
#   "multi"  : dim_value 가 콤마구분 다제품 → category(ACC·Unknown 포함)
#              + category_non_acc_unknown_excl(ACC·Unknown 제외)
#   "single" : dim_value 단일 제품 → category 만 (non_acc 컬럼 빈칸)
CATEGORY_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["multi purchase", "multi order"], "multi"),
    (["best selling product"],          "single"),
]
# 멀티 category_non_acc_unknown_excl 에서 빼는 카테고리 (ACC + 미분류 Unknown).
CATEGORY_NON_ACC_EXCLUDE = {"ACC", CATEGORY_UNKNOWN_LABEL}

# ─── 디멘션 값 제외 (값 전체 일치, 대소문자 무시) ───────────────────
# 디멘션 값이 아래 목록 중 하나와 (양끝 공백 제거 후 대소문자 무시) 정확히 일치하면 그 행 제외.
# 디멘션 종류에 따라 추가/제거하는 영역. 빈 리스트면 제외 없음.
#   '(summary)' 는 괄호 포함. 'Unspecified'/'null' 은 디멘션 미지정/빈값 라벨.
DIM_EXCLUDE_VALUES: list[str] = ["Unspecified", "null"]

# ─── VALUE==0 행 제외 (옵션) ────────────────────────────────────────
# True 면 VALUE 가 0 인 행을 출력에서 제외. False(기본) 면 0 행도 전부 유지.
DROP_ZERO_VALUE = False

# ─── 검수용 reportlet 컬럼 ──────────────────────────────────────────
# True 면 출력 CSV 제일 우측에 reportlet 컬럼 추가 (어떤 reportlet 에서 왔는지 검수용).
INCLUDE_REPORTLET = True

# ─── 출력 컬럼 제외 (옵션) ──────────────────────────────────────────
# 출력 CSV 에서 빼고 싶은 컬럼명 나열 (대소문자 무시). 빈 리스트면 전부 유지.
#   예) ["Panel name", "value_n", "reportlet"]
EXCLUDE_OUTPUT_COLUMNS: list[str] = []

# ─── breakdown 행 처리 (extract_data_v3.5+ 출력 대응) ───────────────
# v3.5 extract_data 는 dim1 총계 행 + breakdown 행(bd{k}_itemId 채워짐)이 한 파일에 같이 있음.
#   "include" (기본) : 둘 다 union — Workspace 테이블 그대로(총계행 + 하위 breakdown행).
#                      bd{k}_itemId 빈칸 여부로 총계/breakdown 구분 가능.
#                      ⚠ 단순 합산 시 이중집계 주의 — 합산은 bd{k}_itemId 빈칸(총계)만 또는
#                        채워진 행(breakdown)만 골라서 할 것.
#   "exclude"        : breakdown 행 제외 — dim1 총계만 (v1.0 semantics)
#   "only"           : breakdown 행만 (총계 행 제외)
# v3.4 이하 출력(bd 컬럼 없음)이면 모드 무관 전체 처리.
BREAKDOWN_ROWS_MODE = "include"

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
# v1.3: 신규 stack_data_extract_* + 구버전 extract_data_* 둘 다 인식
RE_TS_FILE = re.compile(r"^(?:stack_data_extract|extract_data)_(.+)_(\d{6}_\d{4})\.csv$")
_DIM_EXCLUDE_LOWER = {v.strip().lower() for v in DIM_EXCLUDE_VALUES if v.strip()}
# 키워드 정규화: 소문자 + 언더바→공백 (table 'multi_order' ↔ 키워드 'multi order' 매칭용)
_RE_NORMALIZE_KW = re.compile(r"[_]+")


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


# ─── product category 분류 (v1.4) ──────────────────────────────────
def load_category_rules(path: Path):
    """product_category.yaml → [(category_name, [include_re], [exclude_re]), …].
    divisions·categories 의 파일 순서를 보존 (예: Smartthings 가 ACC 보다 먼저 매칭돼야 함).
    division 명(ETC 등)은 출력 라벨이 아니라 그룹일 뿐 — leaf category 명만 사용."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rules = []
    for div in data.get("divisions", []) or []:
        for cat in div.get("categories", []) or []:
            name = cat.get("category", "")
            inc = [re.compile(p) for p in (cat.get("include") or [])]
            exc = [re.compile(p) for p in (cat.get("exclude") or [])]
            rules.append((name, inc, exc))
    return rules


def classify_product(code: str, rules) -> str:
    """제품코드 1개 → yaml 순서대로 첫 매칭 카테고리. 없으면 CATEGORY_UNKNOWN_LABEL.
    (yaml include/exclude 패턴은 대문자·^…$ 앵커 기준 — 코드도 upper 로 맞춤)"""
    c = (code or "").strip().upper()
    if not c:
        return CATEGORY_UNKNOWN_LABEL
    for name, inc, exc in rules:
        if any(r.search(c) for r in inc) and not any(r.search(c) for r in exc):
            return name
    return CATEGORY_UNKNOWN_LABEL


def _normalize_kw_text(*parts: str) -> str:
    """키워드 매칭용 정규화: 소문자 + 언더바→공백 (여러 필드 합침)."""
    return _RE_NORMALIZE_KW.sub(" ", " ".join(p or "" for p in parts).lower())


def detect_category_mode(panel: str, table: str, reportlet: str) -> str | None:
    """panel/table/reportlet 이름으로 분류 모드 결정.
    table+reportlet(구체 필드) 우선, 없으면 panel fallback (panel 은 두 키워드를 다 포함할 수 있어
    모드 구분이 안 되므로 후순위). 매칭 없으면 None."""
    spec = _normalize_kw_text(table, reportlet)
    for keywords, mode in CATEGORY_KEYWORD_RULES:
        if any(kw in spec for kw in keywords):
            return mode
    pan = _normalize_kw_text(panel)
    for keywords, mode in CATEGORY_KEYWORD_RULES:
        if any(kw in pan for kw in keywords):
            return mode
    return None


def make_category_cells(dim_value: str, mode: str | None, rules) -> tuple[str, str]:
    """(category, category_non_acc_unknown_excl) 반환.
      · single : (단일 카테고리, "")  — best selling: category 만
      · multi  : (전체 정렬조인, ACC·Unknown 제외 정렬조인) — 콤마 다제품
      · None   : ("", "")"""
    if mode is None:
        return ("", "")
    if mode == "single":
        return (classify_product(dim_value, rules), "")
    # multi: 콤마 split → 각 분류 (중복 유지) → 알파벳 오름차순 조인
    parts = [p.strip() for p in (dim_value or "").split(CATEGORY_MULTI_SPLIT)]
    cats = [classify_product(p, rules) for p in parts if p]
    category = CATEGORY_JOIN.join(sorted(cats, key=str.lower))
    non_excl = [c for c in cats if c not in CATEGORY_NON_ACC_EXCLUDE]
    category_non_acc = CATEGORY_JOIN.join(sorted(non_excl, key=str.lower))
    return (category, category_non_acc)


def find_latest_per_site(input_dir: Path) -> tuple[list[Path], dict[str, str]]:
    """site 별로 각자 최신 ts 의 extract_data csv 1개만 반환.
    return: (paths_list, {site: ts})"""
    by_site: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for pattern in ("stack_data_extract_*.csv", "extract_data_*.csv"):   # v1.3: 신규 + 구버전
        for p in input_dir.glob(pattern):
            m = RE_TS_FILE.match(p.name)
            if not m:
                continue
            by_site[m.group(1)].append((m.group(2), p))
    if not by_site:
        raise FileNotFoundError(f"{input_dir} 에 stack_data_extract_*(또는 extract_data_*)_YYMMDD_HHMM.csv 없음")
    paths: list[Path] = []
    site_to_ts: dict[str, str] = {}
    for site, entries in by_site.items():
        entries.sort(key=lambda x: x[0])
        latest_ts, latest_path = entries[-1]
        paths.append(latest_path)
        site_to_ts[site] = latest_ts
    return sorted(paths), site_to_ts


# ────────────────────────────────────────────────────────────────
# ─── metric 정규화 (v1.5) ─ metric_origin → 정제 metric ──────
# extract_data v3.9 와 동일 규칙. stack 의 metric 을 신뢰하지 않고 metric_origin 에서 직접 정제.
METRIC_ALIASES = {
    "appbounce": "Bounces",
}
METRIC_KEEP_PAREN_UNITS = {
    "seconds", "second", "sec", "minutes", "minute", "min",
    "hours", "hour", "days", "day", "%",
}
_METRIC_PAREN_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def _normalize_metric(name):
    """metric_origin -> normalized metric (alias first + trailing paren cleanup)."""
    if not name or not isinstance(name, str):
        return name
    s = name.strip()
    alias = METRIC_ALIASES.get(s.lower().replace(" ", ""))
    if alias:
        return alias
    m = _METRIC_PAREN_RE.match(s)
    if m and m.group(2).strip().lower() not in METRIC_KEEP_PAREN_UNITS:
        return m.group(1).strip()
    return s


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
    first_fields: list[str] = []
    for p in files:
        with open(p, encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            if dim_col is None:
                dim_col = detect_dim_column(r.fieldnames)
                first_fields = list(r.fieldnames or [])
            for row in r:
                rows.append(row)
    if not dim_col:
        print("[ERROR] 디멘션 값 컬럼을 못 찾음 (헤더에 itemId/value_n 확인). DIM_COLUMN 수동 지정 필요.")
        return 1
    dim_header = DIM_OUTPUT_HEADER or dim_col
    print(f"[dim] 디멘션 값 컬럼 = {dim_col!r} → 출력 헤더 {dim_header!r}")
    print(f"[load] total rows: {len(rows)}")

    # v1.4: product category 분류 룰 로드 (ADD_CATEGORY_COLUMN 시)
    #   · yaml 있으면 → 정상 분류 (do_category=True)
    #   · yaml 없는데 product 키워드(table/panel/reportlet) 매칭 행이 있으면 → 경고만 하고 분류 skip
    #   · yaml 없고 키워드 매칭 행도 없으면 → 조용히 pass (category 컬럼 미생성)
    cat_rules = None
    do_category = False
    if ADD_CATEGORY_COLUMN:
        if CATEGORY_YAML.exists():
            cat_rules = load_category_rules(CATEGORY_YAML)
            do_category = True
            print(f"[category] {CATEGORY_YAML.name} 로드 — {len(cat_rules)} categories "
                  f"(미분류={CATEGORY_UNKNOWN_LABEL!r})")
        else:
            n_kw = sum(1 for r in rows
                       if detect_category_mode((r.get("panel") or ""),
                                               (r.get("table") or ""),
                                               (r.get("reportlet") or "")) is not None)
            if n_kw:
                print(f"[category][경고] 분류 yaml 없음: {CATEGORY_YAML}")
                print(f"            → product 키워드 매칭 행 {n_kw}개가 있는데 category 분류를 건너뜁니다 "
                      f"(yaml 넣고 재실행하면 분류됨).")
            else:
                print("[category] 분류 yaml 없음 + product 키워드 매칭 행도 없음 → category 컬럼 skip")

    # v1.1: device / bd{k}_* 컬럼 감지 (v3.5+ 출력) — 있으면 출력에 passthrough
    passthrough_cols = [c for c in first_fields
                        if c == "device" or re.match(r"^bd\d+_", c)]
    bd_item_cols = [c for c in first_fields if re.match(r"^bd\d+_itemId$", c)]

    def _is_bd_row(row: dict) -> bool:
        return any((row.get(c) or "").strip() for c in bd_item_cols)

    # breakdown 행 모드 적용 (bd 컬럼 없으면 무관 — 전체 처리)
    if bd_item_cols and BREAKDOWN_ROWS_MODE in ("exclude", "only"):
        before = len(rows)
        if BREAKDOWN_ROWS_MODE == "exclude":
            rows = [r for r in rows if not _is_bd_row(r)]
        else:
            rows = [r for r in rows if _is_bd_row(r)]
        print(f"[breakdown] mode={BREAKDOWN_ROWS_MODE!r}: {before} → {len(rows)} rows "
              f"(bd 컬럼 {len(bd_item_cols)}레벨 감지)")
    elif bd_item_cols:
        print(f"[breakdown] mode='include': 총계+breakdown 둘 다 포함 (검수용 — 합산 주의)")

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
        base_headers.append("value_origin")
    base_headers += ["rsid", "start_date", "end_date", "value_n", "metric_origin", "metric", dim_header]
    if do_category:   # v1.4: dim 컬럼 다음에 category 컬럼 (yaml 있을 때만)
        base_headers += ["category", "category_non_acc_unknown_excl"]
    base_headers.append("segments")
    base_headers += passthrough_cols   # v1.1: device / bd{k}_* 있으면 그대로 출력
    base_headers.append("Panel name")  # v1.2: 입력 'panel' 컬럼 passthrough
    out_headers = base_headers + (["reportlet"] if INCLUDE_REPORTLET else [])
    if EXCLUDE_OUTPUT_COLUMNS:   # v1.2: 출력 컬럼 제외 옵션
        _excl = {c.strip().lower() for c in EXCLUDE_OUTPUT_COLUMNS}
        dropped = [c for c in out_headers if c.lower() in _excl]
        out_headers = [c for c in out_headers if c.lower() not in _excl]
        if dropped:
            print(f"[columns] 제외: {dropped}")

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

    # v1.4: (panel, table, reportlet) → mode 캐시 (행마다 재계산 방지)
    mode_cache: dict[tuple[str, str, str], str | None] = {}
    n_cat_classified = 0

    out_rows: list[dict] = []
    n_zero_dropped = 0
    n_dim_excluded = 0

    for r in rows:
        site       = (r.get("site_code")  or "").strip()
        segments   = (r.get("segments")   or "").strip()
        start_date = (r.get("start_date") or "").strip()
        end_date   = (r.get("end_date")   or "").strip()
        rsid       = (r.get("rsid")       or "").strip()
        value_n    = (r.get("value_n")    or "").strip()
        metric_origin = (r.get("metric_origin") or r.get("metric") or "").strip()
        metric     = _normalize_metric(metric_origin)
        dim_val    = (r.get(dim_col)      or "").strip()
        reportlet  = (r.get("reportlet")  or "").strip()
        raw_val    = (r.get("value1")     or "").strip()   # extract_data 의 값 컬럼은 항상 'value1'

        # 디멘션 값 제외 (Unspecified/null/(summary) 등 — 값 전체 일치, 대소문자 무시)
        if dim_val.strip().lower() in _DIM_EXCLUDE_LOWER:
            n_dim_excluded += 1
            continue

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

        # 출력 SITE CODE 치환 + `_old` 접미사 제거 (uk_old→uk 등). COUNTRY 는 원본 site 로 lookup.
        site_disp = SITE_CODE_RENAME.get(site, site)
        if SITE_CODE_STRIP_OLD and site_disp.lower().endswith("_old"):
            site_disp = site_disp[:-4]

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
            "metric_origin": metric_origin,
            "metric": metric,
            dim_header: dim_val,
            "segments": segments,
            "Panel name": (r.get("panel") or "").strip(),
        }
        if apply_fx:
            row_out["value_origin"] = origin

        # v1.4: product category 분류 — panel/table/reportlet 키워드 매칭 행만
        if do_category:
            panel = (r.get("panel") or "").strip()
            table = (r.get("table") or "").strip()
            key = (panel, table, reportlet)
            if key not in mode_cache:
                mode_cache[key] = detect_category_mode(panel, table, reportlet)
            mode = mode_cache[key]
            cat, cat_non_acc = make_category_cells(dim_val, mode, cat_rules)
            row_out["category"] = cat
            row_out["category_non_acc_unknown_excl"] = cat_non_acc
            if mode is not None:
                n_cat_classified += 1

        for c in passthrough_cols:   # v1.1: device / bd{k}_* passthrough
            row_out[c] = (r.get(c) or "").strip()
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
    ts = _ts_now()
    out_path = OUTPUT_DIR / f"{OUTPUT_BASENAME}_{ts}.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_headers, quoting=csv.QUOTE_MINIMAL,
                           extrasaction="ignore")  # 제외 컬럼 키 무시
        w.writeheader()
        for r in out_rows:
            r["VALUE"] = _fmt_num(r.get("VALUE", ""))
            if "value_origin" in r:
                r["value_origin"] = _fmt_num(r.get("value_origin", ""))
            w.writerow(r)
    print(f"\n[save] {out_path}")
    print(f"  output rows : {len(out_rows)}")
    if do_category:
        print(f"  category 분류 행: {n_cat_classified} rows (키워드 매칭 panel/table/reportlet)")
    if _DIM_EXCLUDE_LOWER:
        print(f"  디멘션값 제외: {n_dim_excluded} rows")
    if DROP_ZERO_VALUE:
        print(f"  VALUE==0 제외: {n_zero_dropped} rows")

    # v1.5: wide union (normalized metric as columns)
    _write_wide(out_rows, out_headers, OUTPUT_DIR, ts)
    return 0


def _write_wide(out_rows, out_headers, out_dir, ts):
    """정제 metric 을 열 헤더로 올린 wide union (셀 = fx VALUE).
    index = out_headers 에서 metric_origin/metric/value_n/VALUE/value_origin 제외 전부."""
    metric_value_cols = {"metric_origin", "metric", "value_n", "VALUE", "value_origin"}
    index_cols = [c for c in out_headers if c not in metric_value_cols]
    groups = {}
    metric_order = []
    collisions = 0
    for r in out_rows:
        m = (r.get("metric") or "").strip()
        if not m:
            continue
        key = tuple(r.get(c, "") for c in index_cols)
        g = groups.get(key)
        if g is None:
            g = {c: r.get(c, "") for c in index_cols}
            groups[key] = g
        if m not in metric_order:
            metric_order.append(m)
        if m in g:
            try:
                g[m] = float(g[m]) + float(r.get("VALUE") or 0)
            except (TypeError, ValueError):
                pass
            collisions += 1
        else:
            g[m] = r.get("VALUE", "")

    def _fmt(v):
        if v == "" or v is None:
            return ""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return v
        return int(fv) if fv == int(fv) else round(fv, 2)

    wide_headers = index_cols + metric_order
    out_path = out_dir / f"{OUTPUT_BASENAME}_wide_{ts}.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=wide_headers, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore")
        w.writeheader()
        for g in groups.values():
            row = {c: g.get(c, "") for c in index_cols}
            for m in metric_order:
                row[m] = _fmt(g.get(m, ""))
            w.writerow(row)
    print(f"[save] {out_path}")
    print(f"  wide rows : {len(groups)} / metric cols {len(metric_order)}")
    if collisions:
        print(f"  warn: index+metric collisions {collisions} summed")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(process())
