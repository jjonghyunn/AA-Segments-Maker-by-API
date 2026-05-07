# extract_panel_tables_json_v2.0.py
# 2026-05-07  Jonghyun Park w/ Claude
"""
Adobe Workspace project의 모든 panel × 모든 reportlet(테이블)을 walk해서
/reports API payload 형식의 JSON으로 자동 저장.

v1 (extract_panel_last_table_json_v1.0.py) 와의 차이:
  · v1: 단일 panel × 마지막 reportlet 1개 → main/last/prior 3개 동일 복사
  · v2: 모든 panel × 모든 reportlet → 각 reportlet 마다 카테고리별 변형 룰 적용

panel context 자동 감지 (panel.name 키워드로):
  · "[ALL SITES] 2026 ..." → prefix ""           → main/
  · "[US] 2026 ..."        → prefix "us_"        → us_main/
  · "[ALL SITES] 2025 ..." → prefix "last_"      → last_main/
  · "[US] 2025 ..."        → prefix "us_last_"   → last_us_main/

reportlet 이름 → tb 이름 결정 (CSV 참조 X — 패널이 source of truth):
  · 패널의 SectionHeaderReportlet 으로부터 #_# 번호 결정 (range/fixed/sequential 패턴 인식)
  · reportlet 자체가 own #-# 가지면 그게 우선
  · slug 화 (Campaign→cmp, S.com→scom, Conversion→cvr 등 abbreviation, noise 패턴 stripping)
  · best_selling / next_page / multi_purchase 는 카테고리별 명시 매핑 사용
  · 결과 파일명 = panel_prefix + (#_# prefix +) slug
  · 매 실행마다 ref/tb_column_name_mapping_MD_{YYYYMMDD_HHMM}.csv 자동 생성
    (열: tb / value_n / column. value_n 은 metricContainer.metrics 개수만큼 value1..valueN)

카테고리별 변형(prior 자동 복제) 룰:
  · numbered (#_#_*)   : 2026 panel → main + main_prior 2개. 2025 panel → last 1개
  · best_selling       : 2026 → main 1개. 2025 → last 1개. (prior 없음)
  · next_page          : 2026 → main 1개. 2025 panel은 스킵 (last/prior 없음)
  · multi_purchase     : 2026 → main + main_prior 2개. 2025 panel은 스킵

prior 파일은 main reportlet의 payload를 그대로 복사한 템플릿. 실제 prior period 날짜·세그먼트
교체는 이후 copy_prior_json.py 등 후속 유틸로 처리하는 흐름.

사용:
  python extract_panel_tables_json_v2.0.py             # 전체 추출 + 저장 (debug dump 기본 ON)
  python extract_panel_tables_json_v2.0.py --dry-run   # 저장 안 하고 어떤 파일이 생길지만 표시
  python extract_panel_tables_json_v2.0.py --no-debug  # debug dump 생략 (안정화 후)
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
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
COMPANY_ID = "company_id"

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
# Workspace URL의 `/workspace/edit/{이부분}`
PROJECT_ID = "YOUR_PROJECT_ID"   # MD 프로젝트

# ─── 출력 폴더 / 출력 CSV ──────────────────────────────────────────
# JSON 출력 root: 하위에 main/, last_main/, last_us_main/, main_prior/, us_main/, us_main_prior/ 자동 사용
JSON_ROOT = Path(__file__).resolve().parent.parent / "json"

# 추출 결과로 자동 생성되는 매핑 CSV. tb / value_n / column 열을 가지며,
# tb = 출력된 JSON 파일명(.json 제거), value_n = value1..valueN (N = metricContainer.metrics 개수), column = 빈 값
# 파일명: tb_column_name_mapping_MD_{YYYYMMDD_HHMM}.csv  (실행 시각 timestamp)
# — 결과 안정화되면 timestamp 부분 제거하고 파일명 고정으로 바꿀 예정.
OUTPUT_CSV_DIR = Path(__file__).resolve().parent.parent / "ref"
OUTPUT_CSV_NAME_TEMPLATE = "tb_column_name_mapping_MD_{ts}.csv"

# ─── panel 컨텍스트 감지 룰 ────────────────────────────────────────
CURRENT_YEAR = "2026"   # 패널 이름에 이 문자열 있으면 "이번 시즌"
LAST_YEAR    = "2025"   # 있으면 "작년 시즌" (last_ prefix)
US_TAGS      = ["[US]"] # 있으면 us_ prefix

# 처리 대상 패널 필터 — 패널 이름에 이 키워드들 중 하나가 있어야 처리.
# (그 외 'Panel' 같은 빈/draft/daily 패널은 자동 스킵)
REQUIRED_PANEL_KEYWORDS = ["[ALL SITES]", "[US]"]

# ─── 카테고리별 변형 생성 룰 ───────────────────────────────────────
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
    ("current", "us",  "next_page",      ""):       "us_nextpage",
    ("current", "all", "multi_purchase", ""):       "multi_purchase",
    ("current", "us",  "multi_purchase", ""):       "us_multi_purchase",
}

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
        # 알파벳만 남긴 flat 으로 sub_kind 검사
        flat_alpha = re.sub(r"[^a-z]+", "", n)
        if "ttlmx" in flat_alpha or "totalmx" in flat_alpha:
            return ("next_page", "ttlmx")
        if "vdda" in flat_alpha:
            return ("next_page", "vdda")
        return ("next_page", "")

    if any(kw in n for kw in ("multi purchase", "multi-purchase", "multipurchase",
                              "multi order", "multi-order", "multiorder")):
        return ("multi_purchase", "")

    return ("numbered", "")


def _resolve_tb_name(reportlet_name: str, assigned_num: tuple[int, int] | None,
                     year_kind: str, region_kind: str) -> tuple[str | None, str]:
    """reportlet → (출력 파일명 base, match_kind).
    match_kind ∈ {'special','slug','none'}.

    매칭 흐름:
      1) own #-# 도 없고 assigned_num 도 없는데 special(best/next/multi) 키워드 잡힘
         → SPECIAL_TABLE_NAMES 명시 매핑
      2) 그 외 → panel_prefix + (assigned_num 있으면 #_#_ prefix) + slug
    CSV 참조 없음. 출력 후 별도 CSV로 (tb, value_n, column) 자동 생성.
    """
    category, sub_kind = _detect_category(reportlet_name, has_assigned_num=(assigned_num is not None))

    # special 카테고리는 명시 매핑 우선
    if category != "numbered":
        for sub in (sub_kind, ""):
            key = (year_kind, region_kind, category, sub)
            if key in SPECIAL_TABLE_NAMES:
                return SPECIAL_TABLE_NAMES[key], "special"
        return None, "none"

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


def _walk_column_tree(root_nodes: list) -> list[dict]:
    leaves: list[dict] = []
    counter = [-1]
    def walk(node, segments, metric):
        counter[0] += 1
        my_pos = counter[0]
        comp = node.get("component") or {}
        ctype = comp.get("type")
        cid = comp.get("id")
        new_segments = list(segments)
        new_metric = metric
        if ctype == "Segment" and cid:
            new_segments.append(cid)
        elif ctype in ("Metric", "CalculatedMetric") and cid:
            new_metric = cid
        children = node.get("nodes") or []
        if not children:
            if new_metric is not None:
                leaves.append({"metric_id": new_metric, "segments": new_segments,
                               "leaf_id": node.get("id"), "position": my_pos})
            return
        for child in children:
            walk(child, new_segments, new_metric)
    for n in root_nodes or []:
        walk(n, [], None)
    return leaves


def _build_metric_container(reportlet: dict) -> dict:
    column_tree = reportlet.get("columnTree") or {}
    leaves = _walk_column_tree(column_tree.get("nodes") or [])
    sort_cfg = (reportlet.get("freeformTable") or {}).get("sort") or {}
    sort_target_leaf_id = sort_cfg.get("columnId")
    sort_asc = sort_cfg.get("asc")

    metric_filters: list[dict] = []
    metrics: list[dict] = []
    next_filter_id = 0
    for leaf in leaves:
        # leaf→root 순서 (Adobe 출력 관례)
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
    return {"metrics": metrics, "metricFilters": metric_filters}


def _build_report_payload(project: dict, panel: dict, reportlet: dict) -> dict:
    rsid = ((panel.get("reportSuite") or {}).get("id")
            or project.get("rsid")
            or project.get("definition", {}).get("rsid")
            or "")
    global_filters = _build_global_filters(panel)
    metric_container = _build_metric_container(reportlet)

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

    return {
        "rsid": rsid,
        "globalFilters": global_filters,
        "metricContainer": metric_container,
        "dimension": dimension,
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
    parser = argparse.ArgumentParser(
        description="Adobe Workspace project의 모든 panel × reportlet → /reports JSON")
    parser.add_argument("--no-debug", dest="debug", action="store_false",
                        help="raw panel/table dump 생략 (기본 ON)")
    parser.set_defaults(debug=True)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="실제 저장 없이 어떤 파일이 생길지만 표시")
    args = parser.parse_args()

    ts = datetime.now().strftime("%y%m%d_%H%M")
    headers, gcid = _load_auth_headers()

    print(f"[{ts}] extract_panel_tables_json_v2.0")
    print(f"  project    : {PROJECT_ID}")
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
    # 출력 CSV용 (tb_name, num_metrics, panel_label, period)
    # panel_label: 'all_2026', 'us_2026', 'all_2025', 'us_2025' 같은 region_year 약어
    # period:      'campaign' (current year main) / 'prior' (current year _prior) / 'last' (last year)
    csv_rows: list[tuple[str, int, str, str]] = []

    for p_idx, panel in enumerate(panels):
        p_name = panel.get("name", f"(panel-{p_idx})")

        # 필수 키워드 없는 패널은 스킵 (예: 'Panel' 같은 빈/draft/daily)
        if not any(kw in p_name for kw in REQUIRED_PANEL_KEYWORDS):
            print(f"\n[panel skip] panel[{p_idx}] '{p_name}' — 필수 키워드({REQUIRED_PANEL_KEYWORDS}) 없음")
            continue

        year_kind, region_kind = _detect_panel_context(p_name)
        prefix = _panel_prefix(year_kind, region_kind)
        # CSV col4 'panel': region_year 약어 ('all_2026', 'us_2025' 등)
        year_str = CURRENT_YEAR if year_kind == "current" else LAST_YEAR
        panel_label = f"{region_kind}_{year_str}"

        rep_iter = list(_iter_panel_reportlets(panel))
        print(f"\n▶ panel[{p_idx}] '{p_name}'")
        print(f"  year={year_kind}  region={region_kind}  prefix='{prefix}'  label='{panel_label}'")
        print(f"  reportlets: {len(rep_iter)}개 (subPanels 순서)")

        for r_idx, (assigned_num, r) in enumerate(rep_iter):
            r_name = r.get("name", f"(reportlet-{r_idx})")
            category, sub_kind = _detect_category(r_name, has_assigned_num=(assigned_num is not None))

            # 작년 panel에서 special 카테고리 스킵 룰
            if year_kind == "last" and SKIP_LAST_BY_CATEGORY.get(category, False):
                skipped.append((p_name, r_name, f"last+{category} 스킵"))
                print(f"    [skip] '{r_name}' ({category}) — last year 스킵 룰")
                continue

            tb_name, match_kind = _resolve_tb_name(r_name, assigned_num, year_kind, region_kind)
            if not tb_name:
                unmatched.append((p_name, r_name))
                print(f"    [unmatched] '{r_name}' — 카테고리/이름 결정 실패")
                continue

            payload = _build_report_payload(project, panel, r)
            num_metrics = len(payload.get("metricContainer", {}).get("metrics", []))

            num_str = f"{assigned_num[0]}-{assigned_num[1]}" if assigned_num else "—"
            print(f"    [{match_kind}] ({num_str}) '{r_name}' → {tb_name}  ({num_metrics} metrics)")

            # period: 'campaign' (current year main) / 'last' (last year main)
            main_period = "campaign" if year_kind == "current" else "last"
            saved.append(_save_json(payload, f"{tb_name}.json", dry_run=args.dry_run))
            csv_rows.append((tb_name, num_metrics, panel_label, main_period))

            # _prior 변형 (2026 panel + 카테고리 룰 만족 시) — period: 'prior'
            if year_kind == "current" and NEEDS_PRIOR_BY_CATEGORY.get(category, False):
                saved.append(_save_json(payload, f"{tb_name}_prior.json", dry_run=args.dry_run))
                csv_rows.append((f"{tb_name}_prior", num_metrics, panel_label, "prior"))

    # ── 매핑 CSV 출력 ───────────────────────────────────────────────
    # 각 tb 마다 metric 개수(N)만큼 행 생성 → tb 1개 = N개 row (value1..valueN)
    # 칼럼: tb / value_n / column / panel / period
    #   column = 빈 값 (사용자가 채울 영역)
    #   panel  = 'all_2026' / 'us_2026' / 'all_2025' / 'us_2025'
    #   period = 'campaign' / 'prior' / 'last'
    csv_out_path = OUTPUT_CSV_DIR / OUTPUT_CSV_NAME_TEMPLATE.format(ts=ts)
    total_csv_rows = sum(n for _, n, _, _ in csv_rows)
    if not args.dry_run:
        csv_out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["tb", "value_n", "column", "panel", "period"])
            for tb_name, n, panel_label, period in csv_rows:
                for i in range(1, n + 1):
                    w.writerow([tb_name, f"value{i}", "", panel_label, period])
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
