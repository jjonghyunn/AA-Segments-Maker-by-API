# extract_data_v3.1_contents_recomm_after14may.py
# 2026-05-28  Jonghyun Park w/ Claude — v3.1: SKIP_PANEL_SEGMENTS 추가 / v3: site 단위 병렬 처리 옵션 (SITE_WORKERS)
"""
extract_data_v2.py 베이스 + device 분기 추가.

v2 차이:
  · PROJECT_ID = NYNY contents 후속 프로젝트 (YOUR_ID)
  · 각 site × device 5종(pc/mobile/app/android/ios) 으로 payload 분기
  · device 치환 = `json pc to mo_app_android_ios replacer_260127.py` 룰 그대로
      - PC_ID 를 device 별 visit segment 로 swap
      - app/android/ios 는 [Global] Excluded APP → [Global] App Only / All Visit 로 swap
  · 결과 CSV 는 site 별 1개 + device 컬럼 long format
      output/extract_data_<site_code>_<ts>.csv     (device 컬럼 포함)
      output/column_mapping_<site_code>_<ts>.csv   (device 컬럼 포함)

흐름:
  1) sites_input.csv 읽음 → [(site_code, start, end), ...]
  2) 각 site 마다:
     - PROJECT_ID 의 모든 panel × reportlet 으로 base payload 생성
     - device 5종 마다 base payload deepcopy + segment swap
     - /reports 호출 (site × device × reportlet 만큼)
     - site 단위로 CSV 2개 저장 (device 컬럼으로 구분)

site × panel 룰 (--include-global-for-us 로 us 의 [Global] 추출 토글):
  · us site            → [US] panel 추출, [Global] panel skip (기본)
  · non-us site        → [Global] panel 추출, [US] panel skip
  · --include-global-for-us 주면 us 도 [Global] 같이 추출

사용:
  python extract_data_v2_contents.py                              # 전체 site × 5 device
  python extract_data_v2_contents.py --site us                    # us 만, [US] panel 만
  python extract_data_v2_contents.py --site us --include-global-for-us
                                                                  # us 에 [Global] 도 같이
  python extract_data_v2_contents.py --device pc mobile           # 일부 device 만
  python extract_data_v2_contents.py --dry-run
  python extract_data_v2_contents.py --workers 8

v3.1 (2026-05-28) 추가:
  · SKIP_PANEL_SEGMENTS 옵션 — panel.segmentGroups (Workspace 패널 상단 박힌 기존 세그) globalFilter 포함 여부
      False (default)      : 기존 동작
      True                 : 모든 panel 에서 기존 세그 무시
      ["키워드1", ...]      : panel.name 키워드 매칭된 panel 만 기존 세그 무시 (OR, case-insensitive)
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
import aanalytics2 as api2

from site_registry import lookup_site, SiteInfo

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
COMPANY_ID = "company_id"

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
PROJECT_ID = "YOUR_PROJECT_ID" # [part_name] 2026 CAMPAIGN NAME | Contents Click Analysis (Product Recommendation) | API (user_id)
# https://experience.adobe.com/#/@company_name/so:company_id/analytics/spa/#/workspace/edit/YOUR_PROJECT_ID

# ─── input / 출력 ──────────────────────────────────────────────────
SITES_INPUT_CSV = Path(__file__).resolve().parent / "sites_input.csv"
OUTPUT_DIR      = Path(__file__).resolve().parent / "output"

# ─── 요청 설정 ─────────────────────────────────────────────────────
MAX_WORKERS = 6
# site 단위 병렬 워커 수 (1 = 순차, v2 동일). 동시 API 요청 = SITE_WORKERS × MAX_WORKERS — AA throttling 주의.
SITE_WORKERS = 5
REQUEST_TIMEOUT = 600
MAX_RETRIES = 10
LIMIT = 400
MAX_PAGES = 100

# ─── panel 필터 ────────────────────────────────────────────────────
REQUIRED_PANEL_KEYWORDS: list[str] = []

# ─── site × panel prefix 룰 ─────────────────────────────────────────
# [US] panel 은 us site 에서만 추출 (다른 site 일 땐 자동 skip).
# [Global] panel 은 기본 모든 site 에서 추출. 단 us 에서는 [US] 와 중복되니
# 기본 skip — 같이 뽑고 싶으면 --include-global-for-us flag.
US_SITE_CODE         = "us"
US_PANEL_PREFIX      = "[US]"
GLOBAL_PANEL_PREFIX  = "[Global]"
INCLUDE_GLOBAL_FOR_US = False  # CLI --include-global-for-us 로 override

SETTINGS_FALLBACK = {
    "countRepeatInstances": True,
    "includeAnnotations": True,
    "nonesBehavior": "return-nones",
    "limit": LIMIT,
    "page": 0,
}

# ─── Device 치환 룰 (reference: json pc to mo_app_android_ios replacer_260127.py) ───
# base = PC (치환 없음). 나머지 4개 device 는 PC segment 를 자기 visit segment 로 swap,
# app/android/ios 는 추가로 [Global] Excluded APP segment 도 swap.
PC_NAME           = "PC User (visit)"
PC_ID             = "세그먼트_아이디_넘버"
EXCLUDED_APP_NAME = "[Global] Excluded APP"
EXCLUDED_APP_ID   = "세그먼트_아이디_넘버"
ALL_VISIT_NAME    = "All Visit"
ALL_VISIT_ID      = "세그먼트_아이디_넘버"
APP_ONLY_NAME     = "[Global] App Only"
APP_ONLY_ID       = "세그먼트_아이디_넘버"

DEVICES: dict[str, dict] = {
    "pc":      {"pc_replace": None,
                "excluded_replace": None},
    "mobile":  {"pc_replace": ("Mobile User (Visit)", "세그먼트_아이디_넘버"),
                "excluded_replace": None},
    "app":     {"pc_replace": (APP_ONLY_NAME, APP_ONLY_ID),
                "excluded_replace": (ALL_VISIT_NAME, ALL_VISIT_ID)},
    "android": {"pc_replace": ("Android - Visit", "세그먼트_아이디_넘버"),
                "excluded_replace": (APP_ONLY_NAME, APP_ONLY_ID)},
    "ios":     {"pc_replace": ("iOS - Visit", "세그먼트_아이디_넘버"),
                "excluded_replace": (APP_ONLY_NAME, APP_ONLY_ID)},
}

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
SEG_ID_RE = re.compile(r"^s\d+_[0-9a-f]+$")
_DATE_RANGE_CACHE: dict[str, str] = {}
_SEG_NAME_CACHE: dict[str, str] = {}    # segment_id → fresh name (via /segments/{id} GET)


# ─── auth / project / panel / column tree — v2 동일 ─────────────
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


def _resolve_date_range(headers: dict, gcid: str, dr_id: str) -> str:
    if dr_id in _DATE_RANGE_CACHE:
        return _DATE_RANGE_CACHE[dr_id]
    url = f"https://analytics.adobe.io/api/{gcid}/dateranges/{dr_id}"
    r = requests.get(url, headers=headers, params={"expansion": "definition"}, timeout=30)
    if r.status_code == 200:
        definition = r.json().get("definition", "")
        _DATE_RANGE_CACHE[dr_id] = definition
        return definition
    _DATE_RANGE_CACHE[dr_id] = ""
    return ""


def _collect_date_range_ids(panels: list[dict]) -> set[str]:
    ids: set[str] = set()
    def _scan_nodes(nodes):
        for n in (nodes or []):
            comp = n.get("component") or {}
            if comp.get("type") == "DateRange" and comp.get("id"):
                ids.add(comp["id"])
            _scan_nodes(n.get("nodes"))
    for panel in panels:
        for sub in panel.get("subPanels") or []:
            rep = sub.get("reportlet") or {}
            ct = rep.get("columnTree") or {}
            _scan_nodes(ct.get("nodes"))
    return ids


def _prefetch_date_ranges(headers: dict, gcid: str, panels: list[dict]):
    dr_ids = _collect_date_range_ids(panels)
    if dr_ids:
        print(f"  dateRange 조회: {len(dr_ids)}개 ...")
    for dr_id in dr_ids:
        _resolve_date_range(headers, gcid, dr_id)


def _fetch_segment_name(headers: dict, gcid: str, seg_id: str) -> str:
    """`/segments/{seg_id}` GET → fresh name. 캐시 적용."""
    if seg_id in _SEG_NAME_CACHE:
        return _SEG_NAME_CACHE[seg_id]
    url = f"https://analytics.adobe.io/api/{gcid}/segments/{seg_id}"
    name = ""
    try:
        r = requests.get(url, headers=headers, params={"expansion": "name"}, timeout=30)
        if r.status_code == 200:
            name = r.json().get("name", "") or ""
    except Exception:
        pass
    _SEG_NAME_CACHE[seg_id] = name
    return name


def _collect_segment_ids(panels: list[dict]) -> set[str]:
    """panel.segmentGroups + columnTree + freeformTable.staticRows 에서 segment id 모두 수집."""
    ids: set[str] = set()
    def _scan_nodes(nodes):
        for n in (nodes or []):
            comp = n.get("component") or {}
            cid = comp.get("id")
            if comp.get("type") == "Segment" and isinstance(cid, str) and SEG_ID_RE.match(cid):
                ids.add(cid)
            _scan_nodes(n.get("nodes"))
    for panel in panels:
        # panel-level segments
        for grp in panel.get("segmentGroups") or []:
            for opt in grp.get("componentOptions") or []:
                comp = opt.get("component") or {}
                cid = comp.get("id")
                if isinstance(cid, str) and SEG_ID_RE.match(cid):
                    ids.add(cid)
        # subPanels reportlet columnTree + staticRows
        for sub in panel.get("subPanels") or []:
            rep = sub.get("reportlet") or {}
            ct = rep.get("columnTree") or {}
            _scan_nodes(ct.get("nodes"))
            ff = rep.get("freeformTable") or {}
            for sr in ff.get("staticRows") or []:
                comp = sr.get("component") or {}
                cid = comp.get("id")
                if comp.get("type") == "Segment" and isinstance(cid, str) and SEG_ID_RE.match(cid):
                    ids.add(cid)
    return ids


def _prefetch_segment_names(headers: dict, gcid: str, panels: list[dict]):
    """project 안 모든 segment id 들 fresh name 한 번에 조회 → _SEG_NAME_CACHE 채움.
    panel 정의 snapshot 의 __metaData__.name 이 stale 일 수 있어 /segments/{id} 직접 조회."""
    seg_ids = _collect_segment_ids(panels)
    if not seg_ids:
        return
    print(f"  segment name 조회: {len(seg_ids)}개 ...")
    for sid in seg_ids:
        _fetch_segment_name(headers, gcid, sid)


def _list_panels(project: dict) -> list[dict]:
    out = []
    for ws in project.get("definition", {}).get("workspaces", []) or []:
        for p in ws.get("panels", []) or []:
            out.append(p)
    return out


def _comp_name(comp: dict) -> str:
    if not isinstance(comp, dict):
        return ""
    # segment id 가 cache 에 fresh name 으로 있으면 그걸 우선 (panel snapshot 의 stale name 회피)
    cid = comp.get("id")
    if isinstance(cid, str) and SEG_ID_RE.match(cid):
        cached = _SEG_NAME_CACHE.get(cid)
        if cached:
            return cached
    meta_name = (comp.get("__metaData__") or {}).get("name")
    if isinstance(meta_name, str) and meta_name:
        return meta_name
    n = comp.get("name")
    return n if isinstance(n, str) else ""


def _iter_panel_reportlets(panel: dict):
    current_sec = None
    sec_offset = 0
    for sub in panel.get("subPanels") or []:
        rep = sub.get("reportlet") or {}
        rep_type = rep.get("type")
        if rep_type == "SectionHeaderReportlet":
            current_sec = _parse_section_num(rep.get("name", ""))
            sec_offset = 0
            continue
        if rep_type != "FreeformReportlet":
            continue

        own = _parse_own_num(rep.get("name", ""))
        assigned = None
        if own is not None:
            assigned = own
            if current_sec is not None and current_sec[2] in ("range", "sequential"):
                start_y = current_sec[0][1]
                sec_offset = max(sec_offset, own[1] - start_y + 1)
        elif current_sec is not None:
            mode = current_sec[2]
            start_x, start_y = current_sec[0]
            if mode == "fixed":
                assigned = (start_x, start_y)
            else:
                assigned = (start_x, start_y + sec_offset)
                sec_offset += 1
        yield assigned, rep


def _parse_section_num(name: str):
    n = (name or "").strip()
    m = re.match(r"^(\d+)\s*-\s*(\d+)\s*~\s*(\d+)\s*-\s*(\d+)\s*\.", n)
    if m:
        x1, y1, x2, y2 = map(int, m.groups())
        return ((x1, y1), (x2, y2), "range")
    m = re.match(r"^(\d+)\s*-\s*(\d+)\s*\.", n)
    if m:
        x, y = map(int, m.groups())
        return ((x, y), (x, y), "fixed")
    m = re.match(r"^(\d+)\s*\.", n)
    if m:
        x = int(m.group(1))
        return ((x, 1), None, "sequential")
    return None


def _parse_own_num(reportlet_name: str):
    m = re.match(r"^\s*(\d+)\s*[-_.]\s*(\d+)\s*[.]\s", (reportlet_name or "") + " ")
    if not m:
        m = re.match(r"^\s*(\d+)\s*[-_.]\s*(\d+)", (reportlet_name or ""))
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


SLUG_ABBREVIATIONS = [
    (r"\bcampaign\b", "cmp"),
    (r"\bs\s*\.\s*com\b", "scom"),
    (r"\bs\.com\b", "scom"),
    (r"\bconversion\b", "cvr"),
    (r"\blogin\s*&\s*non[\s\-]*login\b", "loginout"),
    (r"\blogin\s*&\s*logout\b", "loginout"),
]
SLUG_NOISE_PATTERNS = [
    r"\[\s*vs\.?\s*prior\s*period\s*\]",
    r"\(\s*vs\.?\s*prior\s*period\s*\)",
    r"\[\s*w\.\s*prior\s*period\s*\]",
    r"\(\s*w\.\s*prior\s*period\s*\)",
    r"\(\s*values?\s*\d+(\s*[\+=]\s*\d+)*\s*\)",
    r"\(\s*total\s*\)",
    r"\(\s*w\.\s*cid\s*\)",
]


def _slugify(name: str) -> str:
    s = name.lower()
    for pat in SLUG_NOISE_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)
    for pat, repl in SLUG_ABBREVIATIONS:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _build_date_range_definition(start_date: str, end_date: str) -> str:
    from datetime import datetime as _dt, timedelta
    start_dt = _dt.strptime(start_date, "%Y-%m-%d")
    end_dt = _dt.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    return f"{start_dt:%Y-%m-%dT}00:00:00.000/{end_dt:%Y-%m-%dT}00:00:00.000"


# ─── 기존 패널 segmentGroups 무시 설정 ─────────────────────────────
# panel.segmentGroups (AA Workspace 패널 상단에 박힌 기존 세그) 를
# globalFilter 에 추가할지 결정. "기존 패널 세그 빼고 새 세그로 갈아끼우기" 용.
#
#   False              → 기존 동작 (panel.segmentGroups + EXTRA_SEGMENTS 누적, default)
#   True               → 모든 panel 에서 기존 세그 무시 (EXTRA_SEGMENTS + dateRange 만)
#   ["키워드1", ...]    → panel.name 에 키워드 포함된 panel 만 기존 세그 무시
#                         (OR 매칭, case-insensitive). 나머지 panel 은 기존 동작 유지.
# 예: True               → 전체 panel 적용
#     ["[US]"]           → [US] panel 만 적용
#     ["Revisit", "EPP"] → Revisit OR EPP 포함된 panel 만 적용
SKIP_PANEL_SEGMENTS: bool | list[str] = False


# ─── SKIP_PANEL_SEGMENTS 적용 여부 결정 ────────────────────────────
def _should_skip_panel_segments(panel_name: str) -> bool:
    """SKIP_PANEL_SEGMENTS 설정 → 해당 panel 에서 기존 segmentGroups 를 무시할지."""
    if SKIP_PANEL_SEGMENTS is True:
        return True
    if isinstance(SKIP_PANEL_SEGMENTS, list) and SKIP_PANEL_SEGMENTS:
        pname_lower = (panel_name or "").lower()
        return any(str(kw).lower() in pname_lower for kw in SKIP_PANEL_SEGMENTS)
    return False


def _build_global_filters(panel: dict, *, override_date_range: str | None = None) -> tuple[list[dict], list[str]]:
    filters: list[dict] = []
    segment_names: list[str] = []
    skip_existing = _should_skip_panel_segments(panel.get("name", ""))
    if not skip_existing:
        for grp in panel.get("segmentGroups") or []:
            for opt in grp.get("componentOptions") or []:
                if not opt.get("isActive", True):
                    continue
                comp = opt.get("component") or {}
                sid = comp.get("id")
                if isinstance(sid, str) and SEG_ID_RE.match(sid):
                    filters.append({"type": "segment", "segmentId": sid})
                    segment_names.append(_comp_name(comp))
    if override_date_range:
        filters.append({"type": "dateRange", "dateRange": override_date_range})
    else:
        dr = panel.get("dateRange") or {}
        definition = ""
        if isinstance(dr, dict):
            definition = (dr.get("__metaData__") or {}).get("definition", "")
        elif isinstance(dr, str):
            definition = dr
        if definition:
            filters.append({"type": "dateRange", "dateRange": definition})
    return filters, segment_names


def _walk_column_tree(root_nodes: list) -> list[dict]:
    leaves: list[dict] = []
    counter = [-1]
    def walk(node, segments, segment_names, date_ranges, date_range_names, metric, metric_name):
        counter[0] += 1
        my_pos = counter[0]
        comp = node.get("component") or {}
        ctype = comp.get("type")
        cid = comp.get("id")
        new_segments = list(segments)
        new_segment_names = list(segment_names)
        new_date_ranges = list(date_ranges)
        new_date_range_names = list(date_range_names)
        new_metric = metric
        new_metric_name = metric_name
        if ctype == "Segment" and cid:
            new_segments.append(cid)
            new_segment_names.append(_comp_name(comp))
        elif ctype == "DateRange" and cid:
            meta = comp.get("__metaData__") or {}
            definition = meta.get("definition", "")
            if not definition:
                definition = _DATE_RANGE_CACHE.get(cid, "")
            if definition:
                dr_filter = {"type": "dateRange", "dateRange": definition}
                if not SEG_ID_RE.match(cid):
                    dr_filter["dateRangeId"] = cid
                new_date_ranges.append(dr_filter)
            new_date_range_names.append(_comp_name(comp) or cid)
        elif ctype in ("Metric", "CalculatedMetric") and cid:
            new_metric = cid
            new_metric_name = _comp_name(comp)
        children = node.get("nodes") or []
        if not children:
            if new_metric is not None:
                leaves.append({
                    "metric_id": new_metric,
                    "metric_name": new_metric_name or "",
                    "segments": new_segments,
                    "segment_names": new_segment_names,
                    "date_ranges": new_date_ranges,
                    "date_range_names": new_date_range_names,
                    "leaf_id": node.get("id"),
                    "position": my_pos,
                })
            return
        for child in children:
            walk(child, new_segments, new_segment_names, new_date_ranges, new_date_range_names, new_metric, new_metric_name)
    for n in root_nodes or []:
        walk(n, [], [], [], [], None, None)
    return leaves


def _build_metric_container(reportlet: dict) -> tuple[dict, list[list[str]], list[str]]:
    column_tree = reportlet.get("columnTree") or {}
    leaves = _walk_column_tree(column_tree.get("nodes") or [])
    ff = reportlet.get("freeformTable") or {}
    static_rows_raw = ff.get("staticRows") or []
    row_segs: list[str] = []
    row_seg_names: list[str] = []
    for r in static_rows_raw:
        comp = r.get("component") or {}
        if comp.get("type") == "Segment" and comp.get("id"):
            row_segs.append(comp["id"])
            row_seg_names.append(_comp_name(comp))
    sort_cfg = ff.get("sort") or {}
    sort_target_leaf_id = sort_cfg.get("columnId")
    sort_asc = sort_cfg.get("asc")
    metrics: list[dict] = []
    metric_filters: list[dict] = []
    seg_names_per_metric: list[list[str]] = []
    metric_names_per_metric: list[str] = []
    if row_segs:
        global_idx = 0
        next_col_id = 0
        for row_seg, row_seg_name in zip(row_segs, row_seg_names):
            for leaf in leaves:
                row_filter_id = f"STATIC_ROW_COMPONENT_{2 * global_idx + 1}"
                col_position = 2 * global_idx
                col_filter_ids: list[str] = []
                for _sid in reversed(leaf["segments"]):
                    col_filter_ids.append(str(next_col_id))
                    next_col_id += 1
                dr_filter_ids: list[str] = []
                for _dr in leaf.get("date_ranges", []):
                    dr_filter_ids.append(str(next_col_id))
                    next_col_id += 1
                entry = {
                    "columnId": f"{leaf['metric_id']}:::{col_position}",
                    "id": leaf["metric_id"],
                    "filters": [row_filter_id] + col_filter_ids + dr_filter_ids,
                }
                metrics.append(entry)
                metric_filters.append({"id": row_filter_id, "type": "segment", "segmentId": row_seg})
                for fid, sid in zip(col_filter_ids, reversed(leaf["segments"])):
                    metric_filters.append({"id": fid, "type": "segment", "segmentId": sid})
                for fid, dr_filter in zip(dr_filter_ids, leaf.get("date_ranges", [])):
                    metric_filters.append({"id": fid, **dr_filter})
                cell_names = [row_seg_name] + list(leaf["segment_names"]) + list(leaf.get("date_range_names", []))
                seg_names_per_metric.append([n for n in cell_names if n])
                metric_names_per_metric.append(leaf.get("metric_name", "") or "")
                global_idx += 1
        return {"metrics": metrics, "metricFilters": metric_filters}, seg_names_per_metric, metric_names_per_metric
    next_filter_id = 0
    for leaf in leaves:
        filter_ids: list[str] = []
        for sid in reversed(leaf["segments"]):
            fid = str(next_filter_id)
            next_filter_id += 1
            metric_filters.append({"id": fid, "type": "segment", "segmentId": sid})
            filter_ids.append(fid)
        for dr_filter in leaf.get("date_ranges", []):
            fid = str(next_filter_id)
            next_filter_id += 1
            metric_filters.append({"id": fid, **dr_filter})
            filter_ids.append(fid)
        entry = {"columnId": str(leaf["position"]), "id": leaf["metric_id"], "filters": filter_ids}
        if sort_target_leaf_id and leaf["leaf_id"] == sort_target_leaf_id:
            entry["sort"] = "desc" if sort_asc is False else "asc"
        metrics.append(entry)
        all_names = list(leaf["segment_names"]) + list(leaf.get("date_range_names", []))
        seg_names_per_metric.append([n for n in all_names if n])
        metric_names_per_metric.append(leaf.get("metric_name", "") or "")
    return {"metrics": metrics, "metricFilters": metric_filters}, seg_names_per_metric, metric_names_per_metric


def _build_report_payload(project: dict, panel: dict, reportlet: dict, *,
                          override_rsid: str, override_date_range: str
                          ) -> tuple[dict, list[list[str]], list[str], list[str], str, str]:
    rsid = override_rsid
    global_filters, panel_seg_names = _build_global_filters(panel, override_date_range=override_date_range)
    metric_container, seg_names_per_metric, metric_names = _build_metric_container(reportlet)
    ff = reportlet.get("freeformTable") or {}
    dim_settings = ff.get("dimensionSettings") or []
    dimension = ""
    dimension_name = ""
    if dim_settings and isinstance(dim_settings[0], dict):
        dim_obj = dim_settings[0].get("dimension") or {}
        dimension = dim_obj.get("id", "")
        dimension_name = _comp_name(dim_settings[0]) or dim_obj.get("description", "") or dimension
    pagination = ff.get("pagination") or {}
    settings = {
        "countRepeatInstances": SETTINGS_FALLBACK.get("countRepeatInstances", True),
        "includeAnnotations": SETTINGS_FALLBACK.get("includeAnnotations", True),
        "nonesBehavior": SETTINGS_FALLBACK.get("nonesBehavior", "return-nones"),
        "limit": pagination.get("viewBy", SETTINGS_FALLBACK.get("limit")),
        "page": pagination.get("currentPage", SETTINGS_FALLBACK.get("page", 0)),
    }
    payload = {
        "rsid": rsid,
        "globalFilters": global_filters,
        "metricContainer": metric_container,
        "settings": settings,
        "capacityMetadata": {
            "associations": [
                {"name": "applicationName", "value": "Analysis Workspace UI"},
                {"name": "projectId", "value": project.get("id", "")},
                {"name": "projectName", "value": project.get("name", "")},
                {"name": "panelName", "value": reportlet.get("name", "")},
            ]
        },
    }
    if dimension:
        payload["dimension"] = dimension
    return payload, seg_names_per_metric, metric_names, panel_seg_names, dimension, dimension_name


# ─── device swap: payload 의 segmentId 치환 + segment 이름도 같이 ────
def _apply_device_swap(payload: dict, seg_names_per_metric: list[list[str]],
                       panel_seg_names: list[str], conf: dict) -> None:
    """payload 안의 segmentId 를 device conf 룰대로 in-place 치환.
    seg_names_per_metric / panel_seg_names 도 함께 치환해서 column_mapping CSV 에 반영."""
    pc_new = conf["pc_replace"]
    excl_new = conf["excluded_replace"]
    if pc_new is None and excl_new is None:
        return

    pc_new_id = pc_new[1] if pc_new else None
    pc_new_name = pc_new[0] if pc_new else None
    excl_new_id = excl_new[1] if excl_new else None
    excl_new_name = excl_new[0] if excl_new else None

    def _swap_seg_filter(lst):
        for f in lst:
            if f.get("type") != "segment":
                continue
            sid = f.get("segmentId")
            if sid == PC_ID and pc_new_id:
                f["segmentId"] = pc_new_id
            elif sid == EXCLUDED_APP_ID and excl_new_id:
                f["segmentId"] = excl_new_id

    _swap_seg_filter(payload.get("globalFilters", []))
    _swap_seg_filter(payload.get("metricContainer", {}).get("metricFilters", []))

    pc_name_lower = PC_NAME.lower()
    excl_name_lower = EXCLUDED_APP_NAME.lower()

    def _swap_name(name: str) -> str:
        nl = (name or "").lower()
        if nl == pc_name_lower and pc_new_name:
            return pc_new_name
        if nl == excl_name_lower and excl_new_name:
            return excl_new_name
        return name

    for i, names in enumerate(seg_names_per_metric):
        seg_names_per_metric[i] = [_swap_name(n) for n in names]
    for i, n in enumerate(panel_seg_names):
        panel_seg_names[i] = _swap_name(n)


# ─── /reports API + 페이징 (v2 동일) ───────────────────────────────
def _post_reports(session: requests.Session, headers: dict, gcid: str, payload: dict) -> dict:
    endpoint = f"https://analytics.adobe.io/api/{gcid}/reports"
    for attempt in range(MAX_RETRIES + 1):
        r = session.post(endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code < 400:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            ra = r.headers.get("Retry-After")
            sleep_sec = float(ra) if ra else min(2 ** attempt, 30)
            if attempt == MAX_RETRIES:
                r.raise_for_status()
            time.sleep(sleep_sec)
            continue
        r.raise_for_status()
    raise RuntimeError("post_reports: unexpected fall-through")


def _fetch_all_pages(session: requests.Session, headers: dict, gcid: str, payload: dict) -> tuple[list[dict], list[float]]:
    all_rows = []
    summary_data: list[float] = []
    limit = int(payload.get("settings", {}).get("limit", LIMIT))
    for page in range(MAX_PAGES):
        payload["settings"]["page"] = page
        res = _post_reports(session, headers, gcid, payload)
        if page == 0:
            sd = res.get("summaryData", {})
            if isinstance(sd, dict):
                totals = sd.get("totals", [])
                if totals:
                    summary_data = totals
                if not summary_data:
                    ft = sd.get("filteredTotals", [])
                    if ft:
                        summary_data = ft
        rows = res.get("rows", [])
        if not rows:
            break
        all_rows.extend(rows)
        if res.get("lastPage") is True:
            break
        total_pages = res.get("totalPages")
        if isinstance(total_pages, int) and page >= total_pages - 1:
            break
        if limit and len(rows) < limit:
            break
    return all_rows, summary_data


def _extract_metrics_individually(session: requests.Session, headers: dict, gcid: str, original_payload: dict) -> list[float]:
    metrics = original_payload.get("metricContainer", {}).get("metrics", [])
    metric_filters = original_payload.get("metricContainer", {}).get("metricFilters", [])
    summary_values: list[float] = []
    for m in metrics:
        m_filter_ids = set(m.get("filters", []))
        needed_filters = [mf for mf in metric_filters if mf.get("id") in m_filter_ids]
        single_payload = json.loads(json.dumps(original_payload))
        single_payload["metricContainer"] = {"metrics": [m], "metricFilters": needed_filters}
        res = _post_reports(session, headers, gcid, single_payload)
        sd = res.get("summaryData", {})
        totals = sd.get("totals", [])
        if totals:
            summary_values.append(totals[0] if len(totals) == 1 else totals[0])
        else:
            ft = sd.get("filteredTotals", [])
            summary_values.append(ft[0] if ft else 0.0)
    return summary_values


def _extract_one(task: dict, headers: dict, gcid: str) -> dict:
    session = requests.Session()
    try:
        payload = json.loads(json.dumps(task["payload"]))
        rows, summary_data = _fetch_all_pages(session, headers, gcid, payload)
        task["rows"] = rows
        task["summary_data"] = summary_data
        task["ok"] = True
        task["error"] = ""
    except Exception as e:
        err_str = str(e)
        if "422" in err_str or "400" in err_str:
            try:
                summary_data = _extract_metrics_individually(session, headers, gcid, task["payload"])
                task["rows"] = []
                task["summary_data"] = summary_data
                task["ok"] = True
                task["error"] = "(fallback: individual metrics)"
            except Exception as e2:
                task["rows"] = []
                task["summary_data"] = []
                task["ok"] = False
                task["error"] = f"original: {err_str} | fallback: {str(e2)}"
        else:
            task["rows"] = []
            task["summary_data"] = []
            task["ok"] = False
            task["error"] = err_str
    finally:
        session.close()
    return task


# ─── sites_input.csv 로드 ──────────────────────────────────────────
def _load_sites_input(path: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8-sig") as f:
        lines = [ln for ln in f if ln.strip() and not ln.strip().startswith("#")]
    reader = csv.DictReader(lines)
    for r in reader:
        site = (r.get("site_code") or "").strip()
        s = (r.get("start_date") or "").strip()
        e = (r.get("end_date") or "").strip()
        if site and s and e:
            rows.append((site, s, e))
    return rows


# ─── site 단위 처리 (device 5종 모두 포함) ─────────────────────────
def _should_skip_panel(panel_name: str, site_code: str, include_global_for_us: bool) -> tuple[bool, str]:
    """site × panel prefix 룰 적용. (skip, reason) 반환."""
    is_us = site_code.lower() == US_SITE_CODE
    if panel_name.startswith(US_PANEL_PREFIX) and not is_us:
        return True, f"non-us site → {US_PANEL_PREFIX} panel skip"
    if panel_name.startswith(GLOBAL_PANEL_PREFIX) and is_us and not include_global_for_us:
        return True, f"us site → {GLOBAL_PANEL_PREFIX} panel skip (use --include-global-for-us to keep)"
    return False, ""


def _process_site(headers: dict, gcid: str, project: dict, panels: list[dict],
                  site: SiteInfo, start_date: str, end_date: str,
                  devices: list[str],
                  *, workers: int, limit: int, dry_run: bool, ts: str,
                  include_global_for_us: bool) -> dict:
    date_range_def = _build_date_range_definition(start_date, end_date)
    print(f"\n{'═'*78}\nSITE: {site.site_code}  →  rsid={site.rsid}  "
          f"({start_date} ~ {end_date})  device: {devices}\n{'═'*78}")

    tasks: list[dict] = []
    task_order = 0
    for p_idx, panel in enumerate(panels):
        p_name = panel.get("name", f"(panel-{p_idx})")
        if REQUIRED_PANEL_KEYWORDS and not any(kw in p_name for kw in REQUIRED_PANEL_KEYWORDS):
            continue
        skip, reason = _should_skip_panel(p_name, site.site_code, include_global_for_us)
        if skip:
            print(f"  ⊘ panel skip: {p_name}  ({reason})")
            continue
        rep_iter = list(_iter_panel_reportlets(panel))
        for r_idx, (assigned_num, rep) in enumerate(rep_iter):
            r_name = rep.get("name", f"(reportlet-{r_idx})")
            slug = _slugify(r_name)
            if assigned_num:
                x, y = assigned_num
                slug = f"{x}_{y}_{slug}" if not slug.startswith(f"{x}_{y}_") else slug
            tb_name = slug if slug else f"table_{r_idx}"

            base_payload, base_seg_names, metric_names, base_panel_seg_names, dim_id, dim_name = \
                _build_report_payload(project, panel, rep,
                                      override_rsid=site.rsid,
                                      override_date_range=date_range_def)
            base_payload["settings"]["limit"] = min(limit, 50000)

            for device in devices:
                conf = DEVICES[device]
                dev_payload = copy.deepcopy(base_payload)
                dev_seg_names = copy.deepcopy(base_seg_names)
                dev_panel_seg_names = list(base_panel_seg_names)
                _apply_device_swap(dev_payload, dev_seg_names, dev_panel_seg_names, conf)

                tasks.append({
                    "order": task_order,
                    "panel_idx": p_idx,
                    "panel_name": p_name,
                    "reportlet_name": r_name,
                    "tb_name": tb_name,
                    "device": device,
                    "payload": dev_payload,
                    "seg_names_per_metric": dev_seg_names,
                    "metric_names": metric_names,
                    "panel_segments": dev_panel_seg_names,
                    "dimension_id": dim_id,
                    "dimension_name": dim_name,
                    "rows": [],
                    "summary_data": [],
                    "ok": False,
                    "error": "",
                })
                task_order += 1
    print(f"  payload {len(tasks)}개 생성 (= {len(tasks)//max(len(devices),1)} reportlet × {len(devices)} device)")

    if dry_run:
        return {"site": site, "tasks": tasks, "n_ok": 0, "n_fail": 0}

    print(f"  /reports 호출 (workers={workers}) ...")
    start_time = datetime.now()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_extract_one, t, headers, gcid): t for t in tasks}
        done_count = 0
        for fut in as_completed(futures):
            done_count += 1
            result = fut.result()
            status = "OK" if result["ok"] else f"FAIL: {result['error'][:50]}"
            print(f"    [{done_count}/{len(tasks)}] {result['device']:<7} {result['tb_name']}: "
                  f"{len(result['rows'])} rows — {status}")
    elapsed = datetime.now() - start_time
    print(f"  소요: {elapsed}")

    tasks.sort(key=lambda t: t["order"])
    n_ok = sum(1 for t in tasks if t["ok"])
    n_fail = sum(1 for t in tasks if not t["ok"])

    # CSV 저장 — 파일명 prefix = site_code, 안에 device 컬럼
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    extract_path = OUTPUT_DIR / f"extract_data_{site.site_code}_{ts}.csv"
    mapping_path = OUTPUT_DIR / f"column_mapping_{site.site_code}_{ts}.csv"

    max_metrics = max((len(t["metric_names"]) for t in tasks), default=0)
    with open(extract_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        header = ["site_code", "rsid", "start_date", "end_date", "device",
                  "panel", "table", "reportlet", "dimension", "dimension_name", "itemId", "value"]
        header += [f"value{i+1}" for i in range(max_metrics)]
        header += ["status", "error"]
        w.writerow(header)
        for t in tasks:
            dim_id = t.get("dimension_id", "")
            dim_name = t.get("dimension_name", "")
            base_cols = [site.site_code, site.rsid, start_date, end_date, t["device"],
                         t["panel_name"], t["tb_name"], t["reportlet_name"], dim_id, dim_name]
            if not t["ok"]:
                w.writerow(base_cols + ["", "", *[""] * max_metrics, "FAIL", t["error"]])
                continue
            summary = t.get("summary_data", [])
            rows = t["rows"]
            if summary and not rows:
                padded = list(summary) + [None] * (max_metrics - len(summary))
                w.writerow(base_cols + ["", "(summary)", *padded, "OK", ""])
            else:
                for r in rows:
                    data = r.get("data", [])
                    padded = data + [None] * (max_metrics - len(data))
                    w.writerow(base_cols + [r.get("itemId", ""), r.get("value", ""), *padded, "OK", ""])
    print(f"  CSV: {extract_path.name}")

    with open(mapping_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["site_code", "rsid", "start_date", "end_date", "device",
                    "panel", "table", "reportlet", "dimension", "dimension_name",
                    "value_n", "metric", "segments", "data_value"])
        for t in tasks:
            summary = t.get("summary_data", [])
            dim_id = t.get("dimension_id", "")
            dim_name = t.get("dimension_name", "")
            for i, (seg_list, m_name) in enumerate(zip(t["seg_names_per_metric"], t["metric_names"]), start=1):
                seg_str = "; ".join(seg_list) if seg_list else ""
                val = summary[i - 1] if i - 1 < len(summary) else ""
                if isinstance(val, float) and val == int(val):
                    val = int(val)
                w.writerow([site.site_code, site.rsid, start_date, end_date, t["device"],
                            t["panel_name"], t["tb_name"], t["reportlet_name"], dim_id, dim_name,
                            f"value{i}", m_name, seg_str, val])
    print(f"  mapping CSV: {mapping_path.name}")
    print(f"  결과: 성공 {n_ok} / 실패 {n_fail}")
    return {"site": site, "tasks": tasks, "n_ok": n_ok, "n_fail": n_fail}


# ─── main ────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="사이트 × device 5종 데이터 추출 (NYNY contents 후속)")
    parser.add_argument("--dry-run", action="store_true", help="payload 생성까지만")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"병렬 워커 수 (default {MAX_WORKERS})")
    parser.add_argument("--limit", type=int, default=LIMIT, help=f"dimension row 제한 (default {LIMIT})")
    parser.add_argument("--site", action="append", default=[], metavar="SITE_CODE",
                        help="특정 site 만 처리 (여러 개 가능). 없으면 sites_input.csv 전체")
    parser.add_argument("--device", action="append", default=[], metavar="DEVICE",
                        choices=list(DEVICES.keys()),
                        help=f"특정 device 만 처리. 없으면 전체 {list(DEVICES.keys())}")
    parser.add_argument("--include-global-for-us", action="store_true",
                        default=INCLUDE_GLOBAL_FOR_US,
                        help=f"us site 일 때도 {GLOBAL_PANEL_PREFIX} panel 추출 "
                             f"(기본 skip, [US] panel 과 중복 방지)")
    args = parser.parse_args()

    devices = args.device if args.device else list(DEVICES.keys())

    ts = datetime.now().strftime("%y%m%d_%H%M")
    print(f"[{ts}] extract_data_v2_contents.py")
    print(f"  project : {PROJECT_ID}")
    print(f"  input   : {SITES_INPUT_CSV.name}")
    print(f"  workers : {args.workers}")
    print(f"  limit   : {args.limit}")
    print(f"  devices : {devices}")

    sites_rows = _load_sites_input(SITES_INPUT_CSV)
    if not sites_rows:
        print(f"\n❌ {SITES_INPUT_CSV} 에 site 정보 없음")
        return 1

    if args.site:
        site_filter = set(args.site)
        sites_rows = [r for r in sites_rows if r[0] in site_filter]
        if not sites_rows:
            print(f"\n❌ --site {args.site} 매칭되는 row 없음")
            return 1

    print(f"  처리 site: {len(sites_rows)}개 → {[r[0] for r in sites_rows]}")
    print()

    headers, gcid = _load_auth_headers()
    project = _fetch_project(headers, gcid, PROJECT_ID)
    panels = _list_panels(project)
    print(f"Project name : {project.get('name', '?')}")
    print(f"Project owner: {(project.get('ownerFullName') or project.get('owner') or {}).get('fullName', '?') if isinstance(project.get('ownerFullName') or project.get('owner'), dict) else (project.get('ownerFullName') or '?')}")
    print(f"Project panels: {len(panels)}개")
    for i, p in enumerate(panels):
        print(f"  panel[{i}]: {p.get('name', '?')}")
    _prefetch_date_ranges(headers, gcid, panels)
    _prefetch_segment_names(headers, gcid, panels)

    def _run_one(item):
        site_code, start_date, end_date = item
        site_info = lookup_site(site_code)
        return _process_site(headers, gcid, project, panels,
                              site_info, start_date, end_date, devices,
                              workers=args.workers, limit=args.limit,
                              dry_run=args.dry_run, ts=ts,
                              include_global_for_us=args.include_global_for_us)

    results = []
    if SITE_WORKERS <= 1:
        for item in sites_rows:
            results.append(_run_one(item))
    else:
        print(f"  [site-parallel] {SITE_WORKERS} sites 동시 처리 (총 동시 API 요청 ≈ {SITE_WORKERS * args.workers})")
        with ThreadPoolExecutor(max_workers=SITE_WORKERS) as ex:
            futures = {ex.submit(_run_one, item): item[0] for item in sites_rows}
            for fut in as_completed(futures):
                sc = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    print(f"  [site {sc}] ERROR: {e}")

    print(f"\n{'═'*78}\n[전체 summary]\n{'═'*78}")
    total_ok = sum(r["n_ok"] for r in results)
    total_fail = sum(r["n_fail"] for r in results)
    print(f"  처리 site : {len(results)}")
    print(f"  device    : {devices}")
    print(f"  성공 task : {total_ok}")
    print(f"  실패 task : {total_fail}")
    print(f"\n사이트별:")
    for r in results:
        s = r["site"]
        print(f"  {s.site_code:10}  ({s.rsid:35})  성공={r['n_ok']:3}  실패={r['n_fail']:3}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
