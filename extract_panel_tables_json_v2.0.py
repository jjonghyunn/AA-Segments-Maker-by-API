# extract_panel_tables_json_v2.0.py
# 2026-05-08  Jonghyun Park w/ Claude
"""
Adobe Workspace project의 모든 panel × 모든 reportlet(테이블)을 walk해서
/reports API payload 형식의 JSON으로 자동 저장.

v1 (extract_panel_last_table_json_v1.0.py) 와의 차이:
  · v1: 단일 panel × 마지막 reportlet 1개 → main/last/prior 3개 동일 복사
  · v2: 모든 panel × 모든 reportlet → 각 reportlet 마다 카테고리별 변형 룰 적용

panel context 자동 감지 (panel.name 키워드로):
  · "[ALL SITES] {BASE_YEAR} ..." → prefix ""           → main/
  · "[US] {BASE_YEAR} ..."        → prefix "us_"        → us_main/
  · "[ALL SITES] {LAST_YEAR} ..." → prefix "last_"      → last_main/
  · "[US] {LAST_YEAR} ..."        → prefix "us_last_"   → last_us_main/
  · 토큰 어느 것도 안 매칭되면 fallback: ("current", "all") → main/
  · REQUIRED_PANEL_KEYWORDS = [] 로 두면 모든 패널 통과 (토픽 단위 패널 구조 대응)

reportlet 이름 → tb 이름 결정 (CSV 참조 X — 패널이 source of truth):
  · 패널의 SectionHeaderReportlet 으로부터 #_# 번호 결정 (range/fixed/sequential 패턴 인식)
  · reportlet 자체가 own #-# 가지면 그게 우선
  · slug 화 (Campaign→cmp, S.com→scom, Conversion→cvr 등 abbreviation, noise 패턴 stripping)
  · best_selling / next_page / multi_purchase 는 카테고리별 명시 매핑 사용
  · 결과 파일명 = panel_prefix + (#_# prefix +) slug
  · 매 실행마다 ref/tb_column_name_mapping_{YYYYMMDD_HHMM}.csv 자동 생성
    (열: tb / value_n / column / panel / panel_slug / period)

카테고리별 변형(prior 자동 복제) 룰 — 프로젝트별로 다르므로 NEEDS_PRIOR_BY_CATEGORY /
SKIP_LAST_BY_CATEGORY dict 로 조정. 대표 패턴:
  · numbered (#_#_*)   : current panel → main + main_prior 2개. last panel → last 1개
  · best_selling       : current → main 1개. last → last 1개. (prior 없음)
  · next_page          : current → main 1개. last panel은 스킵
  · multi_purchase     : current → main + main_prior 2개. last panel은 스킵

prior 파일은 main reportlet의 payload를 그대로 복사한 템플릿. 실제 prior period 날짜·세그먼트
교체는 이후 copy_prior_json.py 등 후속 유틸로 처리하는 흐름.

사용:
  python extract_panel_tables_json_v2.0.py                # 전체 추출 + 저장 (debug dump 기본 ON)
  python extract_panel_tables_json_v2.0.py --dry-run      # 저장 안 하고 어떤 파일이 생길지만 표시
  python extract_panel_tables_json_v2.0.py --no-debug     # debug dump 생략 (안정화 후)
  python extract_panel_tables_json_v2.0.py --year 2025    # 기준년도 일시 오버라이드 (last 자동 = year-1)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timedelta
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

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
# Workspace URL의 `/workspace/edit/{이부분}`
PROJECT_ID = "YOUR_PROJECT_ID"   # MD 프로젝트

# ─── 출력 폴더 / 출력 CSV ──────────────────────────────────────────
# JSON 출력 root: 하위에 main/, last_main/, last_us_main/, main_prior/, us_main/, us_main_prior/ 자동 사용
JSON_ROOT = Path(__file__).resolve().parent.parent / "json"

# 추출 결과로 자동 생성되는 매핑 CSV. tb / value_n / column / panel / panel_slug / period 열을 가지며,
# tb = 출력된 JSON 파일명(.json 제거), value_n = value1..valueN (N = metricContainer.metrics 개수), column = 빈 값
# 파일명: tb_column_name_mapping_{YYYYMMDD_HHMM}.csv  (실행 시각 timestamp)
# — 결과 안정화되면 timestamp 부분 제거하고 파일명 고정으로 바꿀 예정.
OUTPUT_CSV_DIR = Path(__file__).resolve().parent.parent / "ref"
OUTPUT_CSV_NAME_TEMPLATE = "tb_column_name_mapping_{ts}.csv"

# ─── panel 컨텍스트 감지 룰 ────────────────────────────────────────
# 기준년도 — 패널 이름에 이 연도 문자열 있으면 "이번 시즌"으로 처리.
# 25년 데이터 뽑으면 25로, 26년이면 26으로. 매년 1번 또는 시즌 단위로 수정.
# CLI에서 --year 2025 로 일시 오버라이드 가능.
BASE_YEAR    = 2026
CURRENT_YEAR = str(BASE_YEAR)            # 호환 — 기존 코드 참조용
LAST_YEAR    = str(BASE_YEAR - 1)        # 자동 도출 (있으면 last_ prefix)
US_TAGS      = ["[US]"]                  # 있으면 us_ prefix

# 처리 대상 패널 필터 — 패널 이름에 이 키워드들 중 하나가 있어야 처리.
# 빈 리스트([])로 두면 모든 패널 통과 (토픽 단위 패널 구조 대응. 예: 'Basic Traffic', 'Cross-Sell').
# 비어있지 않으면 필터 적용 — 'Panel' 같은 무명/draft/daily 패널 자동 스킵.
REQUIRED_PANEL_KEYWORDS: list[str] = []

# ─── 카테고리별 변형 생성 룰 ───────────────────────────────────────
# 프로젝트마다 다름! 예:
#  · prior 기간이 없는 캠페인 → 모두 False
#  · last 시즌 데이터 따로 안 뽑는 캠페인 → SKIP_LAST_BY_CATEGORY 모두 True
#  · US 패널이 main만 있고 prior/last 없는 케이스 → 워크스페이스 패널 자체에 [US] 작년 패널을
#    안 만든 거라 자동으로 처리 안 됨 (코드에서 추가 설정 불필요)

# 현재 연도 panel reportlet에 대해 _prior 변형 자동 복제할지
NEEDS_PRIOR_BY_CATEGORY = {
    "numbered":       True,
    "best_selling":   False,
    "next_page":      False,
    "multi_purchase": True,
}

# 작년(LAST_YEAR) panel에서 어떤 카테고리는 저장 안 함 (CSV에 last_* 가 아예 없음)
SKIP_LAST_BY_CATEGORY = {
    "numbered":       False,
    "best_selling":   False,
    "next_page":      True,
    "multi_purchase": True,
}

# ─── 특수 테이블 명시 매핑 (best_selling / next_page / multi_purchase) ──
# (year_kind, region_kind, category, sub_kind) → output basename
# year_kind: "current" or "last", region_kind: "all" or "us", sub_kind는 next_page에서만 사용 (ttlmx/vdda)
SPECIAL_TABLE_NAMES = {
    ("current", "all", "best_selling",   ""):       "best_selling_products",
    ("last",    "all", "best_selling",   ""):       "last_best_selling_products",
    ("current", "us",  "best_selling",   ""):       "us_bestselling",
    ("last",    "us",  "best_selling",   ""):       "us_last_best_selling_products",
    ("current", "all", "next_page",      "ttlmx"):  "next_page_ttlmx",
    ("current", "all", "next_page",      "vdda"):   "next_page_vdda",
    ("current", "all", "next_page",      "total"):  "next_page_total",
    ("current", "all", "next_page",      "mx"):     "next_page_mx",
    ("current", "all", "next_page",      "vd"):     "next_page_vd",
    ("current", "all", "next_page",      "da"):     "next_page_da",
    ("current", "us",  "next_page",      ""):       "us_nextpage",
    ("current", "all", "multi_purchase", ""):       "multi_purchase",
    ("current", "us",  "multi_purchase", ""):       "us_multi_purchase",
}

# special 카테고리(best_selling/next_page/multi_purchase) 처리 방식.
# True : SPECIAL_TABLE_NAMES 의 base 이름 그대로 (MD 운영 사본 호환 — 다운스트림 RESHAPE 가
#        'best_selling_products.json' 같은 고정 파일명 의존 시)
# False: panel_slug + reportlet_slug 결합 (panel 안에 같은 카테고리 reportlet 여러 개일 때 충돌 방지.
#        토픽 단위 패널 구조 프로젝트 기본 — 한 패널에 best_selling + multi_purchase + cross_sell 섞여
#        있고 각각 여러 reportlet이라 base 이름만으론 식별 못 함)
SPECIAL_USE_BASE_NAME = False

# ─── reportlet 이름 슬러그화 룰 ────────────────────────────────────
# reportlet 제목 안의 단어 → tb 표기 변환 (CSV의 짧은 이름과 매칭률 높이기 위해)
SLUG_ABBREVIATIONS = [
    (r"\bcampaign\b",                      "cmp"),
    (r"\bs\s*\.\s*com\b",                  "scom"),
    (r"\bs\.com\b",                        "scom"),
    (r"\bconversion\b",                    "cvr"),
    (r"\blogin\s*&\s*non[\s\-]*login\b",   "loginout"),
    (r"\blogin\s*&\s*logout\b",            "loginout"),
]

# 슬러그화 시 제거할 noise 패턴 (Adobe panel 제목에 흔히 붙는 메타 텍스트)
# 주의: (Web), (App), (Web & App) 같은 platform 표시는 NOISE 가 아니라 의미 있는 구분자!
#      (예: "S.com MX Traffic (Web)" vs "S.com MX Traffic (App)" — Web/App 떨어뜨리면 같은 파일로 충돌)
#      그래서 platform 표시는 stripping 대상에서 제외하고 토큰으로 살림.
SLUG_NOISE_PATTERNS = [
    r"\[\s*vs\.?\s*prior\s*period\s*\]",
    r"\(\s*vs\.?\s*prior\s*period\s*\)",
    r"\[\s*w\.\s*prior\s*period\s*\]",
    r"\(\s*w\.\s*prior\s*period\s*\)",
    r"\(\s*values?\s*\d+(\s*[\+=]\s*\d+)*\s*\)",
    r"\(\s*total\s*\)",
    r"\(\s*w\.\s*cid\s*\)",
    # (Web), (App), (Web & App), (Total & App) 등은 의도적으로 stripping 안 함
]

# ─── /reports payload fallback ─────────────────────────────────────
SETTINGS_FALLBACK = {
    "countRepeatInstances": True,
    "includeAnnotations": True,
    "nonesBehavior": "return-nones",
    "limit": 400,
    "page": 0,
}
STATISTICS_FALLBACK = {"functions": ["col-max", "col-min"]}

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
SEG_ID_RE = re.compile(r"^s\d+_[0-9a-f]+$")


# ─── auth ────────────────────────────────────────────────────────
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
        raise RuntimeError("필수 헤더 누락")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def _fetch_project(headers: dict, gcid: str, project_id: str) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{project_id}"
    r = requests.get(url, headers=headers,
                     params={"expansion": "definition,ownerFullName,modifiedDate,sharesFullName"},
                     timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"GET project {project_id} 실패: {r.status_code} {r.text[:300]}")
    return r.json()


# ─── panels ──────────────────────────────────────────────────────
def _list_panels(project: dict) -> list[dict]:
    out = []
    for ws in project.get("definition", {}).get("workspaces", []) or []:
        for p in ws.get("panels", []) or []:
            out.append(p)
    return out


def _detect_panel_context(panel_name: str) -> tuple[str, str]:
    """패널 이름 → (year_kind, region_kind).
    year_kind: 'current' | 'last'  (LAST_YEAR 문자열 있으면 last, 그 외 current)
    region_kind: 'all' | 'us'     (US_TAGS 중 하나라도 있으면 us)
    """
    is_us = any(tag in panel_name for tag in US_TAGS)
    year_kind = "last" if (LAST_YEAR in panel_name) else "current"
    return year_kind, ("us" if is_us else "all")


def _panel_prefix(year_kind: str, region_kind: str) -> str:
    if region_kind == "us" and year_kind == "last":
        return "us_last_"
    if region_kind == "us":
        return "us_"
    if year_kind == "last":
        return "last_"
    return ""


# ─── 섹션 헤더 파싱 ─────────────────────────────────────────────
# 섹션 헤더 이름은 보통 다음 4가지 패턴 중 하나:
#   "X-Y ~ X-Z. ..."  → range. Z-Y+1 개의 reportlet에 (X,Y), (X,Y+1), ..., (X,Z) 순차 부여
#   "X-Y. ..."        → fixed. 그 아래 모든 reportlet이 (X,Y) 사용
#   "X. ..."          → sequential from X-1. (X,1), (X,2), ... 순차 부여
#   그 외             → 섹션 무시 (None)
def _parse_section_num(name: str) -> tuple[tuple[int, int], tuple[int, int] | None, str] | None:
    """반환: ((start_x, start_y), (end_x, end_y) | None, mode) 또는 None.
    숫자와 dash, period 사이의 공백을 모두 허용 (예: '1-1 . CAMPAIGN', '1-1~1-2.', '1 - 1.').
    """
    n = (name or "").strip()
    # range: "X-Y ~ X-Z." (공백 포함 가능)
    m = re.match(r"^(\d+)\s*-\s*(\d+)\s*~\s*(\d+)\s*-\s*(\d+)\s*\.", n)
    if m:
        x1, y1, x2, y2 = map(int, m.groups())
        return ((x1, y1), (x2, y2), "range")
    # fixed: "X-Y." (공백 포함 가능)
    m = re.match(r"^(\d+)\s*-\s*(\d+)\s*\.", n)
    if m:
        x, y = map(int, m.groups())
        return ((x, y), (x, y), "fixed")
    # sequential: "X." (공백 포함 가능)
    m = re.match(r"^(\d+)\s*\.", n)
    if m:
        x = int(m.group(1))
        return ((x, 1), None, "sequential")
    return None


# reportlet 자체 이름에 #-#. 가 있는지 검사
def _parse_own_num(reportlet_name: str) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d+)\s*[-_.]\s*(\d+)\s*[.]\s", (reportlet_name or "") + " ")
    if not m:
        m = re.match(r"^\s*(\d+)\s*[-_.]\s*(\d+)", (reportlet_name or ""))
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


# ─── panel.subPanels walk: 섹션 컨텍스트 + reportlet 순회 ─────────
def _iter_panel_reportlets(panel: dict):
    """panel.subPanels 를 순서대로 walk. FreeformReportlet 만 yield.
    각 reportlet에 대해 섹션 컨텍스트로부터 결정된 assigned_num (X, Y) 도 함께 yield.
    own #-# 이 reportlet 이름에 있으면 그게 우선, 그 다음 reportlet이 nullable counter 동기화.
    """
    current_sec: tuple[tuple[int, int], tuple[int, int] | None, str] | None = None
    sec_offset = 0   # 섹션 시작 (start_y) 으로부터 몇 번째 reportlet인지

    sub_panels = panel.get("subPanels") or []
    for sub in sub_panels:
        rep = sub.get("reportlet") or {}
        rep_type = rep.get("type")

        if rep_type == "SectionHeaderReportlet":
            current_sec = _parse_section_num(rep.get("name", ""))
            sec_offset = 0
            continue

        if rep_type != "FreeformReportlet":
            continue

        own = _parse_own_num(rep.get("name", ""))
        assigned: tuple[int, int] | None = None

        if own is not None:
            assigned = own
            if current_sec is not None and current_sec[2] in ("range", "sequential"):
                # counter를 own 다음 위치로 동기화
                start_y = current_sec[0][1]
                sec_offset = max(sec_offset, own[1] - start_y + 1)
        elif current_sec is not None:
            mode = current_sec[2]
            start_x, start_y = current_sec[0]
            if mode == "fixed":
                assigned = (start_x, start_y)
            else:  # range or sequential
                assigned = (start_x, start_y + sec_offset)
                sec_offset += 1

        yield assigned, rep


# legacy 호환용 — section 정보 없이 단순 reportlet 리스트만 필요할 때
def _collect_tables(panel: dict) -> list[dict]:
    return [r for _, r in _iter_panel_reportlets(panel)]


# ─── 슬러그화 ───────────────────────────────────────────────────
def _slugify(name: str) -> str:
    """'1.1 Basic Traffic (Campaign) [vs. prior period]' → '1_1_basic_traffic_cmp'."""
    s = name.lower()
    for pat in SLUG_NOISE_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)
    for pat, repl in SLUG_ABBREVIATIONS:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _dedupe_panel_rep(panel_slug: str, rep_slug: str) -> str:
    """panel_slug 와 rep_slug 합칠 때 token 단위 prefix-suffix overlap 한 번만 사용.
    예) panel='multi_purchase_best_selling_products', rep='best_selling_products_cmp'
        → 'multi_purchase_best_selling_products_cmp'  (best/selling/products 3토큰 중복 제거)
    overlap 없으면 'panel_rep' 단순 결합. 단복수 차이 등 형태 다르면 매칭 안 됨 (의도).
    """
    if not panel_slug:
        return rep_slug
    if not rep_slug:
        return panel_slug
    panel_tok = panel_slug.split("_")
    rep_tok = rep_slug.split("_")
    max_k = min(len(panel_tok), len(rep_tok))
    overlap = 0
    for k in range(max_k, 0, -1):
        if panel_tok[-k:] == rep_tok[:k]:
            overlap = k
            break
    return "_".join(panel_tok + rep_tok[overlap:])


# ─── 카테고리 감지 ─────────────────────────────────────────────
def _detect_category(reportlet_name: str, has_assigned_num: bool = False) -> tuple[str, str]:
    """(category, sub_kind) 반환. category∈{'best_selling','next_page','multi_purchase','numbered'}.

    중요 — 우선순위:
      1) 리딩 #-# 이 있거나 (own_num 추출됨) section 으로 assigned_num 부여됨 → "numbered"
      2) 그 외 키워드(best_selling/next_page/multi_purchase) 검사
      3) 둘 다 아니면 "numbered" (slug-only fallback)
    next_page sub_kind: ttlmx/vdda. 'Total/MX', 'VD/DA' 같은 표현도 인식.
    """
    n = (reportlet_name or "").lower()

    # 1) 번호가 있으면 무조건 numbered (special 키워드 무시)
    if has_assigned_num or _parse_own_num(reportlet_name) is not None:
        return ("numbered", "")

    # 2) special 카테고리 키워드
    if any(kw in n for kw in ("best selling", "best-selling", "bestselling")):
        return ("best_selling", "")

    if any(kw in n for kw in ("next page", "next-page", "nextpage")):
        # sub_kind 검사: 묶음(ttlmx/vdda) 우선, 단독(total/mx/vd/da) 차순.
        # flat_alpha = 알파벳만 남긴 문자열로 묶음 검사
        flat_alpha = re.sub(r"[^a-z]+", "", n)
        if "ttlmx" in flat_alpha or "totalmx" in flat_alpha:
            return ("next_page", "ttlmx")
        if "vdda" in flat_alpha:
            return ("next_page", "vdda")
        # 단독 단서 — 단어 단위 검사 (예: 'Next Page - Total' / 'Next Page - MX')
        words = set(re.findall(r"[a-z]+", n))
        if "total" in words:
            return ("next_page", "total")
        if "mx" in words:
            return ("next_page", "mx")
        if "vd" in words:
            return ("next_page", "vd")
        if "da" in words:
            return ("next_page", "da")
        return ("next_page", "")

    if any(kw in n for kw in ("multi purchase", "multi-purchase", "multipurchase",
                              "multi order", "multi-order", "multiorder")):
        return ("multi_purchase", "")

    return ("numbered", "")


def _resolve_tb_name(reportlet_name: str, assigned_num: tuple[int, int] | None,
                     year_kind: str, region_kind: str,
                     panel_slug: str = "") -> tuple[str | None, str]:
    """reportlet → (출력 파일명 base, match_kind).
    match_kind ∈ {'special','special-slug','slug','none'}.

    매칭 흐름:
      1) own #-# 도 없고 assigned_num 도 없는데 special(best/next/multi) 키워드 잡힘
         · SPECIAL_USE_BASE_NAME=True  → SPECIAL_TABLE_NAMES 명시 매핑 base 이름
         · SPECIAL_USE_BASE_NAME=False → panel_prefix + panel_slug + reportlet_slug (충돌 방지)
      2) 그 외 → panel_prefix + (assigned_num 있으면 #_#_ prefix) + slug
    CSV 참조 없음. 출력 후 별도 CSV로 (tb, value_n, column, panel, panel_slug, period) 자동 생성.
    """
    category, sub_kind = _detect_category(reportlet_name, has_assigned_num=(assigned_num is not None))

    # special 카테고리 처리
    if category != "numbered":
        if SPECIAL_USE_BASE_NAME:
            # 기존 동작 — 명시 매핑 base 이름 사용 (MD 운영 사본 호환)
            for sub in (sub_kind, ""):
                key = (year_kind, region_kind, category, sub)
                if key in SPECIAL_TABLE_NAMES:
                    return SPECIAL_TABLE_NAMES[key], "special"
            return None, "none"
        # panel_slug + reportlet_slug 결합 — 충돌 방지 + 토큰 중복 제거
        rep_slug = _slugify(reportlet_name)
        if not rep_slug and not panel_slug:
            return None, "none"
        panel_pref = _panel_prefix(year_kind, region_kind)
        return panel_pref + _dedupe_panel_rep(panel_slug, rep_slug), "special-slug"

    # numbered 처리
    panel_pref = _panel_prefix(year_kind, region_kind)
    slug = _slugify(reportlet_name)

    # assigned_num 이 있으면 slug 앞에 #_#_ 강제 적용 (이미 그 prefix 면 그대로, 다른 #_# 면 교체)
    if assigned_num is not None:
        x, y = assigned_num
        num_pref = f"{x}_{y}_"
        m = re.match(r"^(\d+_\d+_)", slug)
        if m:
            if m.group(1) != num_pref:
                slug = num_pref + slug[len(m.group(1)):]
        else:
            slug = num_pref + slug

    if not slug:
        return None, "none"

    return panel_pref + slug, "slug"


# ─── /reports payload 빌드 (v1과 동일 로직) ─────────────────────
def _convert_date_range(definition: str) -> str:
    if not definition or "/" not in definition:
        return definition
    start, end = definition.split("/", 1)
    def add_ms(s: str) -> str:
        return s if "." in s else (s + ".000")
    def normalize_end(s: str) -> str:
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return add_ms(s)
        if dt.hour == 23 and dt.minute == 59 and dt.second == 59:
            dt = dt + timedelta(seconds=1)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000")
    return f"{add_ms(start)}/{normalize_end(end)}"


def _build_global_filters(panel: dict) -> list[dict]:
    filters: list[dict] = []
    for grp in panel.get("segmentGroups") or []:
        for opt in grp.get("componentOptions") or []:
            if not opt.get("isActive", True):
                continue
            comp = opt.get("component") or {}
            sid = comp.get("id")
            if isinstance(sid, str) and SEG_ID_RE.match(sid):
                filters.append({"type": "segment", "segmentId": sid})

    dr = panel.get("dateRange") or {}
    definition = ""
    if isinstance(dr, dict):
        definition = (dr.get("__metaData__") or {}).get("definition", "")
    elif isinstance(dr, str):
        definition = dr
    if definition:
        filters.append({"type": "dateRange", "dateRange": _convert_date_range(definition)})

    return filters


def _comp_name(comp: dict) -> str:
    """component dict 에서 표시 이름 추출. workspace JSON 은 component.__metaData__.name 에 박혀있음.
    fallback 순서: __metaData__.name → name → '' (id 는 안 씀 — segments 컬럼은 사람이 읽는 용도)."""
    if not isinstance(comp, dict):
        return ""
    meta_name = (comp.get("__metaData__") or {}).get("name")
    if isinstance(meta_name, str) and meta_name:
        return meta_name
    n = comp.get("name")
    return n if isinstance(n, str) else ""


def _walk_column_tree(root_nodes: list) -> list[dict]:
    """columnTree 를 leaf 까지 walk. leaf 마다 그 path 의 segment id 리스트 + 이름 리스트 모음.

    leaf entry:
        metric_id     : leaf 에 매달린 metric id (없으면 leaf emit 안 함)
        metric_name   : 그 metric 의 표시 이름 (component.__metaData__.name)
        segments      : root→leaf 경로의 segment ID 리스트 (Adobe 등록 순서)
        segment_names : 위와 1:1 대응되는 표시 이름 리스트 (CSV segments 컬럼용)
        leaf_id       : leaf node id
        position      : walk 순서 (전역 counter)
    """
    leaves: list[dict] = []
    counter = [-1]
    def walk(node, segments, segment_names, metric, metric_name):
        counter[0] += 1
        my_pos = counter[0]
        comp = node.get("component") or {}
        ctype = comp.get("type")
        cid = comp.get("id")
        new_segments = list(segments)
        new_segment_names = list(segment_names)
        new_metric = metric
        new_metric_name = metric_name
        if ctype == "Segment" and cid:
            new_segments.append(cid)
            new_segment_names.append(_comp_name(comp))
        elif ctype in ("Metric", "CalculatedMetric") and cid:
            new_metric = cid
            new_metric_name = _comp_name(comp)
        children = node.get("nodes") or []
        if not children:
            if new_metric is not None:
                leaves.append({
                    "metric_id":     new_metric,
                    "metric_name":   new_metric_name or "",
                    "segments":      new_segments,
                    "segment_names": new_segment_names,
                    "leaf_id":       node.get("id"),
                    "position":      my_pos,
                })
            return
        for child in children:
            walk(child, new_segments, new_segment_names, new_metric, new_metric_name)
    for n in root_nodes or []:
        walk(n, [], [], None, None)
    return leaves


def _build_metric_container(reportlet: dict) -> tuple[dict, list[list[str]], list[str]]:
    """columnTree (열) + freeformTable.staticRows (행) 펼쳐서 metricContainer 빌드.

    반환: (metric_container_dict, segment_names_per_metric, metric_names_per_metric)
        · metric_container_dict        — payload['metricContainer'] 에 그대로 들어갈 dict
        · segment_names_per_metric     — metrics 와 같은 길이의 list.
                                         각 entry = 해당 metric 셀의 segment 이름 리스트
                                         (panel-level baseline 은 제외, 순수 cell-specific 만)
        · metric_names_per_metric      — metrics 와 같은 길이의 list. 각 entry = metric 표시 이름

    두 가지 모드 자동 분기:

      · column-only 모드 — staticRows 없음 (대부분의 reportlet)
        세그먼트가 열 방향으로만 줄세움. value_n 별 segments = 그 column path 의 세그 1개~N개.

        예) 단순 줄세움 (3열 × 2행):
                   |  seg_A  |  seg_B  |  seg_C  |
            -------|---------|---------|---------|
            Visit  |  val1   |  val2   |  val3   |
            Vstr   |  val4   |  val5   |  val6   |
            → value1 segments=[seg_A], value2=[seg_B], value3=[seg_C], …

      · row × column cross-tab 모드 (햄버거) — staticRows 가 있거나 totalsType="allVisits"
        왼쪽에 row 세그, 위쪽에 col 세그 — 격자형. 각 셀 = row_seg ∩ col_seg (AND 의미).
        value_n 별 segments = [row_seg, col_seg_outer, col_seg_inner, ...] (row 먼저, 그 다음 col path).

        예) 격자 (3 row 세그 × 2 col 세그 = 6 cells, metric 당 6 values):
                       |  col_seg_X  |  col_seg_Y  |
            -----------|-------------|-------------|
            row_seg_A  |   value1    |   value2    |
            row_seg_B  |   value3    |   value4    |
            row_seg_C  |   value5    |   value6    |
            → value3 = "row_seg_B AND col_seg_X" 셀.
              segments 컬럼 = "row_seg_B; col_seg_X"  (row 먼저, ; 로 AND)

        column tree depth > 1 이면 col path 의 모든 segment 를 outermost→deepest 순으로 추가.
        (코드에서는 path 안의 reversed(segments) deepest→outermost 로 emit 하지만,
         segments 컬럼은 사람 가독성 위해 outermost→deepest 정상 순서로 박음.)

    filter id 패턴:
      · row × col: columnId="metric_id:::position", filters=[STATIC_ROW_COMPONENT_X, col_id_1, ...]
      · column-only: columnId="position", filters=["<col_id>", "<col_id_parent>", ...]
    """
    column_tree = reportlet.get("columnTree") or {}
    leaves = _walk_column_tree(column_tree.get("nodes") or [])

    ff = reportlet.get("freeformTable") or {}
    static_rows_raw = ff.get("staticRows") or []
    totals_type = (ff.get("settings") or {}).get("totalsType")

    # row segment (ID + 이름) 추출 — Segment 타입만; Dimension item 등은 일단 무시
    row_segs: list[str] = []
    row_seg_names: list[str] = []
    for r in static_rows_raw:
        comp = r.get("component") or {}
        if comp.get("type") == "Segment" and comp.get("id"):
            row_segs.append(comp["id"])
            row_seg_names.append(_comp_name(comp))
    # totalsType="allVisits" + staticRows 가 있으면 → 가상 'All_Visits' row 1개 append
    # (staticRows 비어있을 땐 단순 totals 설정일 뿐 row 차원 아님 → column-only 처리)
    if row_segs and totals_type == "allVisits":
        row_segs.append("All_Visits")
        row_seg_names.append("All_Visits")

    sort_cfg = ff.get("sort") or {}
    sort_target_leaf_id = sort_cfg.get("columnId")
    sort_asc = sort_cfg.get("asc")

    metrics: list[dict] = []
    metric_filters: list[dict] = []
    seg_names_per_metric: list[list[str]] = []
    metric_names_per_metric: list[str] = []

    if row_segs:
        # ── row × column cross-tab 모드 (격자, 햄버거) ─────────────
        # 각 cell = row_seg ∩ col_path_segs (AND). filter id 패턴:
        #   row 측: STATIC_ROW_COMPONENT_{2*global_idx + 1}  (홀수)
        #   col 측: 전역 정수 카운터 (0, 1, 2, ... 누적)
        #   columnId position: 2 * global_idx
        # column tree depth > 1 이면 path 모든 segment 를 deepest→outermost 순서로 emit.
        global_idx = 0
        next_col_id = 0
        for row_seg, row_seg_name in zip(row_segs, row_seg_names):
            for leaf in leaves:
                row_filter_id = f"STATIC_ROW_COMPONENT_{2 * global_idx + 1}"
                col_position  = 2 * global_idx

                # column path 의 모든 segment 를 별도 filter로 (deepest→outermost)
                col_filter_ids: list[str] = []
                for _sid in reversed(leaf["segments"]):
                    col_filter_ids.append(str(next_col_id))
                    next_col_id += 1

                entry = {
                    "columnId": f"{leaf['metric_id']}:::{col_position}",
                    "id": leaf["metric_id"],
                    "filters": [row_filter_id] + col_filter_ids,
                }
                metrics.append(entry)

                # metricFilters: row first, then cols in same order
                metric_filters.append({"id": row_filter_id, "type": "segment", "segmentId": row_seg})
                for fid, sid in zip(col_filter_ids, reversed(leaf["segments"])):
                    metric_filters.append({"id": fid, "type": "segment", "segmentId": sid})

                # segments 컬럼용: row 먼저, 그 다음 column path (outermost→deepest 정상 순서)
                cell_names = [row_seg_name] + list(leaf["segment_names"])
                seg_names_per_metric.append([n for n in cell_names if n])
                metric_names_per_metric.append(leaf.get("metric_name", "") or "")

                global_idx += 1
        return {"metrics": metrics, "metricFilters": metric_filters}, seg_names_per_metric, metric_names_per_metric

    # ── column-only 모드 ────────────────────────────────────────
    next_filter_id = 0
    for leaf in leaves:
        # leaf→root 순서 (Adobe 출력 관례) — metricFilters
        filter_ids: list[str] = []
        for sid in reversed(leaf["segments"]):
            fid = str(next_filter_id)
            next_filter_id += 1
            metric_filters.append({"id": fid, "type": "segment", "segmentId": sid})
            filter_ids.append(fid)
        entry = {"columnId": str(leaf["position"]), "id": leaf["metric_id"], "filters": filter_ids}
        if sort_target_leaf_id and leaf["leaf_id"] == sort_target_leaf_id:
            entry["sort"] = "desc" if sort_asc is False else "asc"
        metrics.append(entry)

        # segments 컬럼용: outermost→deepest 정상 순서 (가독성)
        seg_names_per_metric.append([n for n in leaf["segment_names"] if n])
        metric_names_per_metric.append(leaf.get("metric_name", "") or "")

    return {"metrics": metrics, "metricFilters": metric_filters}, seg_names_per_metric, metric_names_per_metric


def _build_report_payload(project: dict, panel: dict, reportlet: dict) -> tuple[dict, list[list[str]], list[str]]:
    """payload dict 와 함께 metric 별 segments(이름 리스트) + metric 이름도 반환.
    매핑 CSV 의 segments / metric 컬럼용 (panel-level baseline 은 제외, cell-specific 만)."""
    rsid = ((panel.get("reportSuite") or {}).get("id")
            or project.get("rsid")
            or project.get("definition", {}).get("rsid")
            or "")
    global_filters = _build_global_filters(panel)
    metric_container, seg_names_per_metric, metric_names_per_metric = _build_metric_container(reportlet)

    ff = reportlet.get("freeformTable") or {}
    dim_settings = ff.get("dimensionSettings") or []
    dimension = ""
    if dim_settings and isinstance(dim_settings[0], dict):
        dimension = (dim_settings[0].get("dimension") or {}).get("id", "")

    pagination = ff.get("pagination") or {}
    settings = {
        "countRepeatInstances": SETTINGS_FALLBACK.get("countRepeatInstances", True),
        "includeAnnotations":   SETTINGS_FALLBACK.get("includeAnnotations", True),
        "nonesBehavior":        SETTINGS_FALLBACK.get("nonesBehavior", "return-nones"),
        "limit":                pagination.get("viewBy",      SETTINGS_FALLBACK.get("limit")),
        "page":                 pagination.get("currentPage", SETTINGS_FALLBACK.get("page", 0)),
    }

    rep_stats = ff.get("statistics") or {}
    funcs = rep_stats.get("functions") or []
    statistics = {"functions": funcs if funcs else STATISTICS_FALLBACK["functions"]}

    payload = {
        "rsid": rsid,
        "globalFilters": global_filters,
        "metricContainer": metric_container,
        "settings": settings,
        "statistics": statistics,
        "capacityMetadata": {
            "associations": [
                {"name": "applicationName", "value": "Analysis Workspace UI"},
                {"name": "projectId", "value": project.get("id", "")},
                {"name": "projectName", "value": project.get("name", "")},
                {"name": "panelName",   "value": reportlet.get("name", "")},
            ]
        },
    }
    if dimension:
        # dimension 비어있으면 키 자체 omit (test_real 패턴 따라감)
        payload["dimension"] = dimension
    return payload, seg_names_per_metric, metric_names_per_metric


# ─── 출력 위치 ──────────────────────────────────────────────────
def _get_subfolder(filename: str) -> str:
    n = filename[:-5] if filename.endswith(".json") else filename
    if n.startswith("us_last_") or n.startswith("last_us_"):
        return "last_us_main"
    if n.startswith("last_"):
        return "last_main"
    if n.startswith("us_") and n.endswith("_prior"):
        return "us_main_prior"
    if n.startswith("us_"):
        return "us_main"
    if n.endswith("_prior"):
        return "main_prior"
    return "main"


def _save_json(payload: dict, filename: str, *, dry_run: bool) -> Path:
    sub = _get_subfolder(filename)
    out = JSON_ROOT / sub / filename
    if dry_run:
        print(f"  [dry-run] would save → {out}")
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"  saved → {out}")
    return out


# ─── 메인 ───────────────────────────────────────────────────────
def main() -> int:
    # Windows cp949 콘솔에서 print 시 unicode (→, —, ▶) 깨지지 않도록
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Adobe Workspace project의 모든 panel × reportlet → /reports JSON")
    parser.add_argument("--no-debug", dest="debug", action="store_false",
                        help="raw panel/table dump 생략 (기본 ON)")
    parser.set_defaults(debug=True)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="실제 저장 없이 어떤 파일이 생길지만 표시")
    parser.add_argument("--year", type=int, default=BASE_YEAR,
                        help=f"기준년도 (default {BASE_YEAR}). last 자동 = year-1")
    args = parser.parse_args()

    # --year 오버라이드 — 전역 갱신
    global CURRENT_YEAR, LAST_YEAR
    CURRENT_YEAR = str(args.year)
    LAST_YEAR    = str(args.year - 1)

    ts = datetime.now().strftime("%y%m%d_%H%M")
    headers, gcid = _load_auth_headers()

    print(f"[{ts}] extract_panel_tables_json_v2.0")
    print(f"  project    : {PROJECT_ID}")
    print(f"  base year  : {CURRENT_YEAR}  (last = {LAST_YEAR})")
    print(f"  json root  : {JSON_ROOT}")
    print(f"  output CSV : {OUTPUT_CSV_DIR / OUTPUT_CSV_NAME_TEMPLATE.format(ts=ts)}")
    print()

    print("Fetching project ...")
    project = _fetch_project(headers, gcid, PROJECT_ID)
    panels = _list_panels(project)
    print(f"  panel 발견: {len(panels)}개")

    if args.debug:
        for i, p in enumerate(panels):
            dump = SCRIPT_DIR / f"_debug_panel_{i}_{ts}.json"
            dump.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  debug dump panel[{i}] → {dump.name}")

    saved: list[Path] = []
    skipped: list[tuple[str, str, str]] = []      # (panel_name, reportlet_name, reason)
    unmatched: list[tuple[str, str]] = []         # 카테고리 자체가 결정 안 됨
    # 출력 CSV용 (tb_name, seg_names_per_metric, metric_names_per_metric, panel_label, panel_slug, period)
    # seg_names_per_metric:    길이 N(metric 수). 각 entry = 해당 value_n 셀의 segment 이름 리스트
    #                          (panel-level baseline 은 제외, cell-specific cross-tab 만).
    #                          cross-tab row × col 인 경우 row 먼저 → col path 순서.
    #                          CSV 쓸 때 ';' 로 join — 한 셀에 여러 segment 가 AND 의미로 적용됨.
    # metric_names_per_metric: 길이 N. 각 entry = 그 value_n 의 metric 표시 이름.
    # panel_label: 'all_2026', 'us_2026', 'all_2025', 'us_2025' 같은 region_year 약어
    # panel_slug:  panel 이름 자체 슬러그 ('basic_traffic', 'cross_sell', 'all_sites_2026_campaign' 등)
    # period:      'campaign' (current year main) / 'prior' (current year _prior) / 'last' (last year)
    csv_rows: list[tuple[str, list[list[str]], list[str], str, str, str]] = []
    # segments 컬럼 join 구분자 — segment 이름에 ':' 가 등장 가능하므로 ';' 사용
    SEG_JOIN = "; "

    for p_idx, panel in enumerate(panels):
        p_name = panel.get("name", f"(panel-{p_idx})")

        # 필수 키워드 없는 패널은 스킵 (예: 'Panel' 같은 빈/draft/daily).
        # REQUIRED_PANEL_KEYWORDS = [] 면 모든 패널 통과 (토픽 단위 패널 구조 대응).
        if REQUIRED_PANEL_KEYWORDS and not any(kw in p_name for kw in REQUIRED_PANEL_KEYWORDS):
            print(f"\n[panel skip] panel[{p_idx}] '{p_name}' — 필수 키워드({REQUIRED_PANEL_KEYWORDS}) 없음")
            continue

        year_kind, region_kind = _detect_panel_context(p_name)
        prefix = _panel_prefix(year_kind, region_kind)
        # CSV 'panel' 컬럼: region_year 약어 ('all_2026', 'us_2025' 등)
        year_str = CURRENT_YEAR if year_kind == "current" else LAST_YEAR
        panel_label = f"{region_kind}_{year_str}"
        # CSV 'panel_slug' 컬럼: panel 이름 슬러그 ('basic_traffic' 등)
        panel_slug = _slugify(p_name)

        rep_iter = list(_iter_panel_reportlets(panel))
        print(f"\n▶ panel[{p_idx}] '{p_name}'")
        print(f"  year={year_kind}  region={region_kind}  prefix='{prefix}'  label='{panel_label}'  slug='{panel_slug}'")
        print(f"  reportlets: {len(rep_iter)}개 (subPanels 순서)")

        for r_idx, (assigned_num, r) in enumerate(rep_iter):
            r_name = r.get("name", f"(reportlet-{r_idx})")
            category, sub_kind = _detect_category(r_name, has_assigned_num=(assigned_num is not None))

            # 작년 panel에서 special 카테고리 스킵 룰
            if year_kind == "last" and SKIP_LAST_BY_CATEGORY.get(category, False):
                skipped.append((p_name, r_name, f"last+{category} 스킵"))
                print(f"    [skip] '{r_name}' ({category}) — last year 스킵 룰")
                continue

            tb_name, match_kind = _resolve_tb_name(r_name, assigned_num, year_kind, region_kind, panel_slug)
            if not tb_name:
                unmatched.append((p_name, r_name))
                print(f"    [unmatched] '{r_name}' — 카테고리/이름 결정 실패")
                continue

            payload, seg_names_per_metric, metric_names_per_metric = _build_report_payload(project, panel, r)
            num_metrics = len(payload.get("metricContainer", {}).get("metrics", []))

            num_str = f"{assigned_num[0]}-{assigned_num[1]}" if assigned_num else "—"
            print(f"    [{match_kind}] ({num_str}) '{r_name}' → {tb_name}  ({num_metrics} metrics)")

            # period: 'campaign' (current year main) / 'last' (last year main)
            main_period = "campaign" if year_kind == "current" else "last"
            saved.append(_save_json(payload, f"{tb_name}.json", dry_run=args.dry_run))
            csv_rows.append((tb_name, seg_names_per_metric, metric_names_per_metric, panel_label, panel_slug, main_period))

            # _prior 변형 (current year panel + 카테고리 룰 만족 시) — period: 'prior'
            # segments / metric 은 main 과 동일 (prior 는 daterange 만 다른 변형)
            if year_kind == "current" and NEEDS_PRIOR_BY_CATEGORY.get(category, False):
                saved.append(_save_json(payload, f"{tb_name}_prior.json", dry_run=args.dry_run))
                csv_rows.append((f"{tb_name}_prior", seg_names_per_metric, metric_names_per_metric, panel_label, panel_slug, "prior"))

    # ── 매핑 CSV 출력 ───────────────────────────────────────────────
    # 각 tb 마다 metric 개수(N)만큼 행 생성 → tb 1개 = N개 row (value1..valueN)
    # 칼럼: tb / value_n / column / segments / metric / panel / panel_slug / period
    #   column     = 빈 값 (사용자가 채울 영역)
    #   segments   = 그 value_n 셀의 segment 이름들을 ';' 로 join.
    #                column-only 모드면 보통 1개, cross-tab(햄버거)이면 row_seg; col_seg (AND).
    #                panel-level baseline (LATIN CR 제거, [Global] Excluded EPP 등) 은 제외.
    #   metric     = 그 value_n 의 metric 표시 이름 ('Visits', 'Orders' 등). 보통 1개.
    #   panel      = 'all_2026' / 'us_2026' / 'all_2025' / 'us_2025'  (region_year 약어)
    #   panel_slug = panel 이름 슬러그 ('basic_traffic', 'cross_sell' 등)
    #   period     = 'campaign' / 'prior' / 'last'
    csv_out_path = OUTPUT_CSV_DIR / OUTPUT_CSV_NAME_TEMPLATE.format(ts=ts)
    total_csv_rows = sum(len(seg_list) for _, seg_list, _, _, _, _ in csv_rows)
    if not args.dry_run:
        csv_out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["tb", "value_n", "column", "segments", "metric", "panel", "panel_slug", "period"])
            for tb_name, seg_names_per_metric, metric_names_per_metric, panel_label, panel_slug, period in csv_rows:
                for i, (names, m_name) in enumerate(zip(seg_names_per_metric, metric_names_per_metric), start=1):
                    seg_str = SEG_JOIN.join(names) if names else ""
                    w.writerow([tb_name, f"value{i}", "", seg_str, m_name or "", panel_label, panel_slug, period])
        print(f"\n[CSV] 매핑 자동생성: {csv_out_path}")
        print(f"      tb {len(csv_rows)}개 × 각 N(metric)개 = 총 {total_csv_rows}행")
    else:
        print(f"\n[CSV] [dry-run] would save → {csv_out_path}")
        print(f"      tb {len(csv_rows)}개 × 각 N(metric)개 = 총 {total_csv_rows}행")

    print(f"\n[summary]")
    print(f"  saved JSON     : {len(saved)}개 (main + _prior 변형 합산)")
    print(f"  unique tb      : {len(csv_rows)}개")
    print(f"  CSV total rows : {total_csv_rows}개 (각 tb의 metric 수만큼 value1..N 펼침)")
    print(f"  skipped        : {len(skipped)}개 (last year + special 스킵 룰)")
    print(f"  unmatched      : {len(unmatched)}개")
    if unmatched:
        print(f"\n  unmatched reportlets:")
        for p, r in unmatched:
            print(f"    [{p}] {r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
