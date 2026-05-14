# extract_data.py
# 2026-05-14  Jonghyun Park w/ Claude
# updated: 2026-05-14 16:48  — dateRange API 조회 시 expansion=definition 추가
"""
Adobe Workspace project의 모든 panel × reportlet에서
세그먼트/메트릭 이름 + 실제 데이터 값을 동시다발적으로 추출.

기존 extract_panel_tables_json_v2.0.py (구조 파악 + payload 생성) 와
aa_exporter.py (ThreadPoolExecutor 병렬 /reports 호출) 를 결합.

사용:
  python extract_data.py                    # 전체 추출 (콘솔 출력 + CSV 저장)
  python extract_data.py --dry-run          # payload 생성까지만 (API 호출 안 함)
  python extract_data.py --workers 8        # 병렬 워커 수 조정
  python extract_data.py --limit 100        # dimension row 수 제한
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
AUTH_JSON_PATH = str(Path(__file__).resolve().parent.parent.parent / "aa_auth.json")
COMPANY_ID = "company_id"

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
# Workspace URL의 `/workspace/edit/{이부분}`
PROJECT_ID = "YOUR_PROJECT_ID"

# ─── 출력 ──────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# ─── 요청 설정 ─────────────────────────────────────────────────────
MAX_WORKERS = 6          # 병렬 워커 수 (5~8 추천)
REQUEST_TIMEOUT = 600    # 요청 타임아웃 (초)
MAX_RETRIES = 10         # 재시도 횟수
LIMIT = 400              # dimension row 수 제한 (API settings.limit)
MAX_PAGES = 100          # 최대 페이지 수

# ─── panel 컨텍스트 감지 ───────────────────────────────────────────
BASE_YEAR = 2026
CURRENT_YEAR = str(BASE_YEAR)
LAST_YEAR = str(BASE_YEAR - 1)
US_TAGS = ["[US]"]

# 빈 리스트면 모든 패널 통과
REQUIRED_PANEL_KEYWORDS: list[str] = []

# ─── /reports payload fallback ─────────────────────────────────────
SETTINGS_FALLBACK = {
    "countRepeatInstances": True,
    "includeAnnotations": True,
    "nonesBehavior": "return-nones",
    "limit": LIMIT,
    "page": 0,
}

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
SEG_ID_RE = re.compile(r"^s\d+_[0-9a-f]+$")
_DATE_RANGE_CACHE: dict[str, str] = {}  # dateRange ID → definition string


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


# ─── project fetch ──────────────────────────────────────────────
def _fetch_project(headers: dict, gcid: str, project_id: str) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{project_id}"
    r = requests.get(url, headers=headers,
                     params={"expansion": "definition,ownerFullName,modifiedDate,sharesFullName"},
                     timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"GET project {project_id} 실패: {r.status_code} {r.text[:300]}")
    return r.json()


# ─── dateRange definition 조회 ──────────────────────────────────
def _resolve_date_range(headers: dict, gcid: str, dr_id: str) -> str:
    """dateRange ID → definition 문자열 조회 (캐시 사용)."""
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
    """모든 패널의 columnTree + staticRows에서 DateRange ID 수집."""
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
    """프로젝트의 모든 dateRange ID를 미리 조회해서 캐시."""
    dr_ids = _collect_date_range_ids(panels)
    if dr_ids:
        print(f"  dateRange 조회: {len(dr_ids)}개 ...")
    for dr_id in dr_ids:
        _resolve_date_range(headers, gcid, dr_id)


# ─── panels ──────────────────────────────────────────────────────
def _list_panels(project: dict) -> list[dict]:
    out = []
    for ws in project.get("definition", {}).get("workspaces", []) or []:
        for p in ws.get("panels", []) or []:
            out.append(p)
    return out


def _detect_panel_context(panel_name: str) -> tuple[str, str]:
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


# ─── component name helper ──────────────────────────────────────
def _comp_name(comp: dict) -> str:
    if not isinstance(comp, dict):
        return ""
    meta_name = (comp.get("__metaData__") or {}).get("name")
    if isinstance(meta_name, str) and meta_name:
        return meta_name
    n = comp.get("name")
    return n if isinstance(n, str) else ""


# ─── reportlet iteration ────────────────────────────────────────
def _iter_panel_reportlets(panel: dict):
    """FreeformReportlet만 yield. (assigned_num, reportlet_dict)"""
    current_sec = None
    sec_offset = 0
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


# ─── slugify ─────────────────────────────────────────────────────
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


# ─── date range conversion ──────────────────────────────────────
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


# ─── global filters (segment + dateRange) ────────────────────────
def _build_global_filters(panel: dict, *,
                          add_segments: list[str] | None = None,
                          remove_segments: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """반환: (filters_list, segment_names_list)
    add_segments: 전체필터에 추가할 segment ID 목록
    remove_segments: 전체필터에서 제거할 segment ID 목록
    """
    filters: list[dict] = []
    segment_names: list[str] = []
    remove_set = set(remove_segments or [])

    for grp in panel.get("segmentGroups") or []:
        for opt in grp.get("componentOptions") or []:
            if not opt.get("isActive", True):
                continue
            comp = opt.get("component") or {}
            sid = comp.get("id")
            if isinstance(sid, str) and SEG_ID_RE.match(sid):
                if sid in remove_set:
                    continue
                filters.append({"type": "segment", "segmentId": sid})
                segment_names.append(_comp_name(comp))

    # 추가 세그먼트
    for sid in (add_segments or []):
        if isinstance(sid, str) and SEG_ID_RE.match(sid):
            filters.append({"type": "segment", "segmentId": sid})
            segment_names.append(f"(added) {sid}")

    dr = panel.get("dateRange") or {}
    definition = ""
    if isinstance(dr, dict):
        definition = (dr.get("__metaData__") or {}).get("definition", "")
    elif isinstance(dr, str):
        definition = dr
    if definition:
        filters.append({"type": "dateRange", "dateRange": _convert_date_range(definition)})

    return filters, segment_names


def _list_panel_filters(panel: dict) -> list[tuple[str, str, bool]]:
    """패널의 전체필터 세그먼트 목록 반환: (segment_id, name, is_active)"""
    results = []
    for grp in panel.get("segmentGroups") or []:
        for opt in grp.get("componentOptions") or []:
            comp = opt.get("component") or {}
            sid = comp.get("id")
            if isinstance(sid, str) and SEG_ID_RE.match(sid):
                is_active = opt.get("isActive", True)
                results.append((sid, _comp_name(comp), is_active))
    return results


# ─── columnTree walk ─────────────────────────────────────────────
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
            # dateRange: inline definition 또는 ID → 캐시에서 definition 조회
            meta = comp.get("__metaData__") or {}
            definition = meta.get("definition", "")
            if not definition:
                definition = _DATE_RANGE_CACHE.get(cid, "")
            if definition:
                dr_filter = {"type": "dateRange", "dateRange": _convert_date_range(definition)}
                # dateRangeId도 함께 보내야 API가 정확한 값을 반환
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


# ─── metric container build ──────────────────────────────────────
def _build_metric_container(reportlet: dict) -> tuple[dict, list[list[str]], list[str]]:
    column_tree = reportlet.get("columnTree") or {}
    leaves = _walk_column_tree(column_tree.get("nodes") or [])

    ff = reportlet.get("freeformTable") or {}
    static_rows_raw = ff.get("staticRows") or []
    totals_type = (ff.get("settings") or {}).get("totalsType")

    row_segs: list[str] = []
    row_seg_names: list[str] = []
    for r in static_rows_raw:
        comp = r.get("component") or {}
        if comp.get("type") == "Segment" and comp.get("id"):
            row_segs.append(comp["id"])
            row_seg_names.append(_comp_name(comp))
    # totalsType "allVisits"는 UI에서 총합 행 표시용 — API에서는
    # 기존 row segment와 동일 결과를 내므로 추가하지 않음 (중복 방지)

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
                # dateRange filters
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
        # dateRange filters
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


# ─── payload build ───────────────────────────────────────────────
def _build_report_payload(project: dict, panel: dict, reportlet: dict, *,
                          add_segments: list[str] | None = None,
                          remove_segments: list[str] | None = None) -> tuple[dict, list[list[str]], list[str], list[str]]:
    """반환: (payload, seg_names_per_metric, metric_names, panel_segment_names)"""
    rsid = ((panel.get("reportSuite") or {}).get("id")
            or project.get("rsid")
            or project.get("definition", {}).get("rsid")
            or "")
    global_filters, panel_seg_names = _build_global_filters(
        panel, add_segments=add_segments, remove_segments=remove_segments)
    metric_container, seg_names_per_metric, metric_names = _build_metric_container(reportlet)

    ff = reportlet.get("freeformTable") or {}
    dim_settings = ff.get("dimensionSettings") or []
    dimension = ""
    if dim_settings and isinstance(dim_settings[0], dict):
        dimension = (dim_settings[0].get("dimension") or {}).get("id", "")

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
    return payload, seg_names_per_metric, metric_names, panel_seg_names


# ─── /reports API call with retry ────────────────────────────────
def _post_reports(session: requests.Session, headers: dict, gcid: str,
                  payload: dict) -> dict:
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


def _fetch_all_pages(session: requests.Session, headers: dict, gcid: str,
                     payload: dict) -> tuple[list[dict], list[float]]:
    """모든 페이지의 rows를 하나로 합쳐 반환.
    dimension이 없는 summary 테이블은 rows가 비고 summaryData에 값이 들어옴.
    반환: (rows_list, summary_data)
    """
    all_rows = []
    summary_data: list[float] = []
    limit = int(payload.get("settings", {}).get("limit", LIMIT))
    for page in range(MAX_PAGES):
        payload["settings"]["page"] = page
        res = _post_reports(session, headers, gcid, payload)

        # summary 데이터 (dimension 없는 테이블)
        if page == 0:
            sd = res.get("summaryData", {})
            if isinstance(sd, dict):
                totals = sd.get("totals", [])
                if totals:
                    summary_data = totals
                # filteredTotals도 시도
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


# ─── task: 하나의 reportlet 추출 작업 ────────────────────────────
def _extract_one(task: dict, headers: dict, gcid: str) -> dict:
    """ThreadPoolExecutor에서 실행. task dict에 결과를 붙여 반환.
    실패 시(422 등) metric을 1개씩 분리해서 개별 추출 시도 (fallback).
    """
    session = requests.Session()
    try:
        payload = json.loads(json.dumps(task["payload"]))  # deep copy
        rows, summary_data = _fetch_all_pages(session, headers, gcid, payload)
        task["rows"] = rows
        task["summary_data"] = summary_data
        task["ok"] = True
        task["error"] = ""
    except Exception as e:
        # fallback: metric 개별 추출 시도
        err_str = str(e)
        if "422" in err_str or "400" in err_str:
            try:
                summary_data = _extract_metrics_individually(
                    session, headers, gcid, task["payload"])
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


def _extract_metrics_individually(session: requests.Session, headers: dict,
                                  gcid: str, original_payload: dict) -> list[float]:
    """metric을 1개씩 분리해서 개별 요청. summary_data 리스트로 합쳐 반환."""
    metrics = original_payload.get("metricContainer", {}).get("metrics", [])
    metric_filters = original_payload.get("metricContainer", {}).get("metricFilters", [])
    summary_values: list[float] = []

    for m in metrics:
        # 이 metric이 참조하는 filter만 추출
        m_filter_ids = set(m.get("filters", []))
        needed_filters = [mf for mf in metric_filters if mf.get("id") in m_filter_ids]

        single_payload = json.loads(json.dumps(original_payload))
        single_payload["metricContainer"] = {
            "metrics": [m],
            "metricFilters": needed_filters,
        }

        res = _post_reports(session, headers, gcid, single_payload)
        sd = res.get("summaryData", {})
        totals = sd.get("totals", [])
        if totals:
            summary_values.append(totals[0] if len(totals) == 1 else totals[0])
        else:
            ft = sd.get("filteredTotals", [])
            summary_values.append(ft[0] if ft else 0.0)

    return summary_values


# ─── main ────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Adobe Workspace project 데이터 추출 (세그먼트/메트릭 이름 + 값)")
    parser.add_argument("--dry-run", action="store_true",
                        help="payload 생성까지만, API 호출 안 함")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"병렬 워커 수 (default {MAX_WORKERS})")
    parser.add_argument("--limit", type=int, default=LIMIT,
                        help=f"dimension row 수 제한 (default {LIMIT})")
    parser.add_argument("--year", type=int, default=BASE_YEAR,
                        help=f"기준년도 (default {BASE_YEAR})")
    parser.add_argument("--show-filters", action="store_true",
                        help="패널별 전체필터(globalFilters) 세그먼트 목록만 출력하고 종료")
    parser.add_argument("--add-filter", action="append", default=[],
                        metavar="SEGMENT_ID",
                        help="전체필터에 세그먼트 추가 (여러 개 가능: --add-filter s... --add-filter s...)")
    parser.add_argument("--remove-filter", action="append", default=[],
                        metavar="SEGMENT_ID",
                        help="전체필터에서 세그먼트 제거 (여러 개 가능)")
    args = parser.parse_args()

    global CURRENT_YEAR, LAST_YEAR
    CURRENT_YEAR = str(args.year)
    LAST_YEAR = str(args.year - 1)

    ts = datetime.now().strftime("%y%m%d_%H%M")

    print(f"[{ts}] extract_data.py")
    print(f"  project  : {PROJECT_ID}")
    print(f"  workers  : {args.workers}")
    print(f"  limit    : {args.limit}")
    if args.add_filter:
        print(f"  +filter  : {', '.join(args.add_filter)}")
    if args.remove_filter:
        print(f"  -filter  : {', '.join(args.remove_filter)}")
    print()

    # ── 1. 인증 + 프로젝트 가져오기 ──
    print("Authenticating ...")
    headers, gcid = _load_auth_headers()

    print("Fetching project ...")
    project = _fetch_project(headers, gcid, PROJECT_ID)
    panels = _list_panels(project)
    print(f"  panels: {len(panels)}개")
    _prefetch_date_ranges(headers, gcid, panels)
    print()

    # ── show-filters 모드: 전체필터 목록만 출력하고 종료 ──
    if args.show_filters:
        for p_idx, panel in enumerate(panels):
            p_name = panel.get("name", f"(panel-{p_idx})")
            if REQUIRED_PANEL_KEYWORDS and not any(kw in p_name for kw in REQUIRED_PANEL_KEYWORDS):
                continue
            filters = _list_panel_filters(panel)
            print(f"[panel {p_idx}] {p_name}")
            if not filters:
                print("  (전체필터 세그먼트 없음)")
            for sid, name, is_active in filters:
                status = "ON " if is_active else "OFF"
                print(f"  {status}  {sid}  {name}")
            print()
        print("사용 예:")
        print("  --remove-filter <SEGMENT_ID>     # 특정 세그먼트 제거 후 추출")
        print("  --add-filter <SEGMENT_ID>        # 세그먼트 추가 후 추출")
        return 0

    # ── 2. 모든 reportlet에서 task 목록 생성 ──
    tasks: list[dict] = []
    task_order = 0

    for p_idx, panel in enumerate(panels):
        p_name = panel.get("name", f"(panel-{p_idx})")

        if REQUIRED_PANEL_KEYWORDS and not any(kw in p_name for kw in REQUIRED_PANEL_KEYWORDS):
            print(f"[skip] panel[{p_idx}] '{p_name}' — 키워드 불일치")
            continue

        year_kind, region_kind = _detect_panel_context(p_name)
        prefix = _panel_prefix(year_kind, region_kind)

        rep_iter = list(_iter_panel_reportlets(panel))
        print(f"[panel {p_idx}] '{p_name}' ({len(rep_iter)} reportlets)")

        for r_idx, (assigned_num, rep) in enumerate(rep_iter):
            r_name = rep.get("name", f"(reportlet-{r_idx})")
            slug = _slugify(r_name)
            if assigned_num:
                x, y = assigned_num
                slug = f"{x}_{y}_{slug}" if not slug.startswith(f"{x}_{y}_") else slug

            tb_name = prefix + slug if slug else f"{prefix}table_{r_idx}"

            payload, seg_names_per_metric, metric_names, panel_seg_names = \
                _build_report_payload(project, panel, rep,
                                      add_segments=args.add_filter,
                                      remove_segments=args.remove_filter)

            # limit 오버라이드
            payload["settings"]["limit"] = min(args.limit, 50000)

            num_metrics = len(payload.get("metricContainer", {}).get("metrics", []))
            num_str = f"{assigned_num[0]}-{assigned_num[1]}" if assigned_num else "-"
            print(f"  [{num_str}] '{r_name}' -> {tb_name} ({num_metrics} metrics)")

            tasks.append({
                "order": task_order,
                "panel_idx": p_idx,
                "panel_name": p_name,
                "reportlet_name": r_name,
                "tb_name": tb_name,
                "payload": payload,
                "seg_names_per_metric": seg_names_per_metric,
                "metric_names": metric_names,
                "panel_segments": panel_seg_names,
                "year_kind": year_kind,
                "region_kind": region_kind,
                "rows": [],
                "summary_data": [],
                "ok": False,
                "error": "",
            })
            task_order += 1

    print(f"\n총 {len(tasks)}개 reportlet payload 생성 완료")

    if args.dry_run:
        print("\n[dry-run] API 호출 생략. payload만 확인:")
        for t in tasks:
            print(f"  {t['tb_name']}: {len(t['payload']['metricContainer']['metrics'])} metrics")
        return 0

    # ── 3. 동시다발적 /reports API 호출 ──
    print(f"\n/reports API 호출 시작 (workers={args.workers}) ...")
    start_time = datetime.now()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(_extract_one, t, headers, gcid): t for t in tasks}
        done_count = 0
        for fut in as_completed(futures):
            done_count += 1
            result = fut.result()
            status = "OK" if result["ok"] else f"FAIL: {result['error'][:60]}"
            print(f"  [{done_count}/{len(tasks)}] {result['tb_name']}: {len(result['rows'])} rows — {status}")

    elapsed = datetime.now() - start_time
    print(f"\n완료! 소요 시간: {elapsed}")

    # ── 4. 결과 정리 (패널별 → 테이블별 → 칼럼 순서) ──
    tasks.sort(key=lambda t: t["order"])

    # 콘솔 출력
    print("\n" + "=" * 80)
    print("추출 결과")
    print("=" * 80)

    current_panel = None
    for t in tasks:
        if t["panel_name"] != current_panel:
            current_panel = t["panel_name"]
            print(f"\n{'─' * 60}")
            print(f"PANEL: {current_panel}")
            if t["panel_segments"]:
                print(f"  Panel Segments: {', '.join(t['panel_segments'])}")
            print(f"{'─' * 60}")

        print(f"\n  TABLE: {t['tb_name']}")
        print(f"  Reportlet: {t['reportlet_name']}")

        # 칼럼 헤더 (세그먼트 + 메트릭 이름)
        col_headers = []
        for i, (seg_list, m_name) in enumerate(
                zip(t["seg_names_per_metric"], t["metric_names"]), start=1):
            seg_str = " & ".join(seg_list) if seg_list else ""
            label = f"value{i}"
            if m_name:
                label += f" [{m_name}]"
            if seg_str:
                label += f" ({seg_str})"
            col_headers.append(label)

        if col_headers:
            print(f"  Columns ({len(col_headers)}):")
            for ch in col_headers:
                print(f"    - {ch}")

        if not t["ok"]:
            print(f"  ERROR: {t['error']}")
            continue

        summary = t.get("summary_data", [])
        rows = t["rows"]

        if summary:
            print(f"  Summary Data ({len(summary)} values):")
            for i, (val, seg_list, m_name) in enumerate(
                    zip(summary, t["seg_names_per_metric"], t["metric_names"]), start=1):
                seg_str = " & ".join(seg_list) if seg_list else ""
                label = m_name or f"value{i}"
                if seg_str:
                    label += f" ({seg_str})"
                val_str = f"{val:,.2f}" if isinstance(val, (int, float)) else str(val)
                print(f"    value{i} [{label}]: {val_str}")

        if rows:
            print(f"  Rows: {len(rows)}")
            for row in rows[:10]:
                value = row.get("value", "")
                data = row.get("data", [])
                data_str = " | ".join(f"{d:,.2f}" if isinstance(d, (int, float)) else str(d) for d in data)
                print(f"    {value}: {data_str}")
            if len(rows) > 10:
                print(f"    ... ({len(rows) - 10}행 더)")
        elif not summary:
            print(f"  (데이터 없음)")

    # ── 5. CSV 저장 ──
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"extract_data_{ts}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        # 헤더: 최대 metric 수 기준으로 value 칼럼 생성
        max_metrics = max((len(t["metric_names"]) for t in tasks), default=0)
        header = ["panel", "table", "reportlet", "itemId", "value"]
        header += [f"value{i+1}" for i in range(max_metrics)]
        header += ["status", "error"]
        w.writerow(header)

        for t in tasks:
            if not t["ok"]:
                row = [t["panel_name"], t["tb_name"], t["reportlet_name"],
                       "", "", *[""] * max_metrics, "FAIL", t["error"]]
                w.writerow(row)
                continue

            summary = t.get("summary_data", [])
            rows = t["rows"]

            if summary and not rows:
                # dimension 없는 summary 테이블 — 한 행으로 출력
                padded = list(summary) + [None] * (max_metrics - len(summary))
                row = [t["panel_name"], t["tb_name"], t["reportlet_name"],
                       "", "(summary)", *padded, "OK", ""]
                w.writerow(row)
            else:
                for r in rows:
                    data = r.get("data", [])
                    padded = data + [None] * (max_metrics - len(data))
                    row = [t["panel_name"], t["tb_name"], t["reportlet_name"],
                           r.get("itemId", ""), r.get("value", ""),
                           *padded, "OK", ""]
                    w.writerow(row)

    print(f"\nCSV 저장: {csv_path}")

    # 칼럼 매핑 CSV (어떤 value_n이 어떤 세그먼트/메트릭인지 + 실제 값)
    mapping_path = OUTPUT_DIR / f"column_mapping_{ts}.csv"
    with open(mapping_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["panel", "table", "reportlet", "value_n", "metric", "segments", "data_value"])
        for t in tasks:
            summary = t.get("summary_data", [])
            for i, (seg_list, m_name) in enumerate(
                    zip(t["seg_names_per_metric"], t["metric_names"]), start=1):
                seg_str = "; ".join(seg_list) if seg_list else ""
                # 실제 값: summary 테이블이면 해당 인덱스 값, 아니면 빈값
                val = summary[i - 1] if i - 1 < len(summary) else ""
                if isinstance(val, float) and val == int(val):
                    val = int(val)
                w.writerow([t["panel_name"], t["tb_name"], t["reportlet_name"], f"value{i}", m_name, seg_str, val])

    print(f"칼럼 매핑 CSV: {mapping_path}")

    # 요약
    ok_count = sum(1 for t in tasks if t["ok"])
    fail_count = sum(1 for t in tasks if not t["ok"])
    total_rows = sum(len(t["rows"]) for t in tasks if t["ok"])
    summary_count = sum(1 for t in tasks if t["ok"] and t.get("summary_data") and not t["rows"])
    print(f"\n[summary] 성공: {ok_count}, 실패: {fail_count}, 총 데이터 행: {total_rows:,}, summary 테이블: {summary_count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
