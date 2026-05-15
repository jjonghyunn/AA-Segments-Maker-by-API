# extract_data_v2.py
# 2026-05-15  Jonghyun Park w/ Claude
"""
v1 (extract_data.py) 차이:
  · 사이트별 RSID + dateRange 를 sites_input.csv 의 (site_code, start_date, end_date) 로 override
  · site_code → rsid 매핑은 site_registry.py 의 lookup_site() 사용
  · 결과 CSV 는 사이트별 별도 파일 (파일명 prefix = rsid)
      output/extract_data_<rsid>_<ts>.csv
      output/column_mapping_<rsid>_<ts>.csv

흐름:
  1) sites_input.csv 읽음 → [(site_code, start, end), ...]
  2) 각 row 마다:
     - site_registry.lookup_site(site_code) → SiteInfo (rsid 등)
     - PROJECT_ID 의 모든 panel × reportlet 에 대해
       payload 의 rsid + dateRange 를 그 row 값으로 override
     - /reports 호출 → site 별 결과 CSV 두 개 저장 (extract_data / column_mapping)

사용:
  python extract_data_v2.py                    # sites_input.csv 의 모든 site
  python extract_data_v2.py --site us           # 한 site 만
  python extract_data_v2.py --dry-run           # payload 생성까지만
  python extract_data_v2.py --workers 8         # 워커 수
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
AUTH_JSON_PATH = str(Path(__file__).resolve().parent.parent.parent / "aa_auth.json")
COMPANY_ID = "company_id"

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
# v1 과 동일 — 같은 project 의 panel/reportlet 구조를 여러 site (rsid) 로 추출
PROJECT_ID = "YOUR_PROJECT_ID"

# ─── input / 출력 ──────────────────────────────────────────────────
SITES_INPUT_CSV = Path(__file__).resolve().parent / "sites_input.csv"
OUTPUT_DIR      = Path(__file__).resolve().parent / "output"

# ─── 요청 설정 ─────────────────────────────────────────────────────
MAX_WORKERS = 6
REQUEST_TIMEOUT = 600
MAX_RETRIES = 10
LIMIT = 400
MAX_PAGES = 100

# ─── panel 필터 (v1 동일) ──────────────────────────────────────────
REQUIRED_PANEL_KEYWORDS: list[str] = []

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
_DATE_RANGE_CACHE: dict[str, str] = {}


# ─── auth / project / panel / column tree — v1 과 동일 ─────────────
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


def _list_panels(project: dict) -> list[dict]:
    out = []
    for ws in project.get("definition", {}).get("workspaces", []) or []:
        for p in ws.get("panels", []) or []:
            out.append(p)
    return out


def _comp_name(comp: dict) -> str:
    if not isinstance(comp, dict):
        return ""
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


# ─── dateRange override 형식 ───────────────────────────────────────
def _build_date_range_definition(start_date: str, end_date: str) -> str:
    """ISO YYYY-MM-DD 두 개 → AA dateRange definition 형식.
    예: '2026-05-11', '2026-05-17' → '2026-05-11T00:00:00.000/2026-05-18T00:00:00.000'
    (end 는 다음날 00:00:00 — v1 의 _convert_date_range 와 동일 컨벤션)"""
    from datetime import datetime as _dt, timedelta
    start_dt = _dt.strptime(start_date, "%Y-%m-%d")
    end_dt = _dt.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    return f"{start_dt:%Y-%m-%dT}00:00:00.000/{end_dt:%Y-%m-%dT}00:00:00.000"


# ─── panel 의 dateRange + rsid override + global filter 구성 ───────
def _build_global_filters(panel: dict, *, override_date_range: str | None = None) -> tuple[list[dict], list[str]]:
    filters: list[dict] = []
    segment_names: list[str] = []
    for grp in panel.get("segmentGroups") or []:
        for opt in grp.get("componentOptions") or []:
            if not opt.get("isActive", True):
                continue
            comp = opt.get("component") or {}
            sid = comp.get("id")
            if isinstance(sid, str) and SEG_ID_RE.match(sid):
                filters.append({"type": "segment", "segmentId": sid})
                segment_names.append(_comp_name(comp))
    # dateRange — override 우선
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


# ─── columnTree walk — v1 동일 ────────────────────────────────────
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
    """v1 과 달리 rsid + dateRange override 필수."""
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


# ─── /reports API + 페이징 (v1 동일) ───────────────────────────────
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
    """sites_input.csv 읽음 — site_code, start_date, end_date.
    빈 줄 / # 시작 주석 라인 무시."""
    rows: list[tuple[str, str, str]] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8-sig") as f:
        # 주석 라인 skip 후 DictReader
        lines = [ln for ln in f if ln.strip() and not ln.strip().startswith("#")]
    reader = csv.DictReader(lines)
    for r in reader:
        site = (r.get("site_code") or "").strip()
        s = (r.get("start_date") or "").strip()
        e = (r.get("end_date") or "").strip()
        if site and s and e:
            rows.append((site, s, e))
    return rows


# ─── site 단위 처리 ────────────────────────────────────────────────
def _process_site(headers: dict, gcid: str, project: dict, panels: list[dict],
                  site: SiteInfo, start_date: str, end_date: str,
                  *, workers: int, limit: int, dry_run: bool, ts: str) -> dict:
    """한 site 의 모든 panel × reportlet 추출 + CSV 저장."""
    date_range_def = _build_date_range_definition(start_date, end_date)
    print(f"\n{'═'*78}\nSITE: {site.site_code}  →  rsid={site.rsid}  ({start_date} ~ {end_date})\n{'═'*78}")

    tasks: list[dict] = []
    task_order = 0
    for p_idx, panel in enumerate(panels):
        p_name = panel.get("name", f"(panel-{p_idx})")
        if REQUIRED_PANEL_KEYWORDS and not any(kw in p_name for kw in REQUIRED_PANEL_KEYWORDS):
            continue
        rep_iter = list(_iter_panel_reportlets(panel))
        for r_idx, (assigned_num, rep) in enumerate(rep_iter):
            r_name = rep.get("name", f"(reportlet-{r_idx})")
            slug = _slugify(r_name)
            if assigned_num:
                x, y = assigned_num
                slug = f"{x}_{y}_{slug}" if not slug.startswith(f"{x}_{y}_") else slug
            tb_name = slug if slug else f"table_{r_idx}"
            payload, seg_names_per_metric, metric_names, panel_seg_names, dim_id, dim_name = \
                _build_report_payload(project, panel, rep,
                                      override_rsid=site.rsid,
                                      override_date_range=date_range_def)
            payload["settings"]["limit"] = min(limit, 50000)
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
                "dimension_id": dim_id,
                "dimension_name": dim_name,
                "rows": [],
                "summary_data": [],
                "ok": False,
                "error": "",
            })
            task_order += 1
    print(f"  payload {len(tasks)}개 생성")

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
            print(f"    [{done_count}/{len(tasks)}] {result['tb_name']}: {len(result['rows'])} rows — {status}")
    elapsed = datetime.now() - start_time
    print(f"  소요: {elapsed}")

    tasks.sort(key=lambda t: t["order"])
    n_ok = sum(1 for t in tasks if t["ok"])
    n_fail = sum(1 for t in tasks if not t["ok"])

    # CSV 저장 — 파일명 prefix = rsid
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    extract_path = OUTPUT_DIR / f"extract_data_{site.rsid}_{ts}.csv"
    mapping_path = OUTPUT_DIR / f"column_mapping_{site.rsid}_{ts}.csv"

    max_metrics = max((len(t["metric_names"]) for t in tasks), default=0)
    with open(extract_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        header = ["site_code", "rsid", "start_date", "end_date",
                  "panel", "table", "reportlet", "dimension", "dimension_name", "itemId", "value"]
        header += [f"value{i+1}" for i in range(max_metrics)]
        header += ["status", "error"]
        w.writerow(header)
        for t in tasks:
            dim_id = t.get("dimension_id", "")
            dim_name = t.get("dimension_name", "")
            base_cols = [site.site_code, site.rsid, start_date, end_date,
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
        w.writerow(["site_code", "rsid", "start_date", "end_date",
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
                w.writerow([site.site_code, site.rsid, start_date, end_date,
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

    parser = argparse.ArgumentParser(description="사이트별 RSID + dateRange override 데이터 추출")
    parser.add_argument("--dry-run", action="store_true", help="payload 생성까지만")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"병렬 워커 수 (default {MAX_WORKERS})")
    parser.add_argument("--limit", type=int, default=LIMIT, help=f"dimension row 제한 (default {LIMIT})")
    parser.add_argument("--site", action="append", default=[], metavar="SITE_CODE",
                        help="특정 site 만 처리 (여러 개 가능). 없으면 sites_input.csv 전체")
    args = parser.parse_args()

    ts = datetime.now().strftime("%y%m%d_%H%M")
    print(f"[{ts}] extract_data_v2.py")
    print(f"  project : {PROJECT_ID}")
    print(f"  input   : {SITES_INPUT_CSV.name}")
    print(f"  workers : {args.workers}")
    print(f"  limit   : {args.limit}")

    # sites_input.csv 로드
    sites_rows = _load_sites_input(SITES_INPUT_CSV)
    if not sites_rows:
        print(f"\n❌ {SITES_INPUT_CSV} 에 site 정보 없음 (header 빼고 #-comment 외 데이터 라인 0)")
        print(f"   샘플 형식: site_code,start_date,end_date")
        return 1

    if args.site:
        site_filter = set(args.site)
        sites_rows = [r for r in sites_rows if r[0] in site_filter]
        if not sites_rows:
            print(f"\n❌ --site {args.site} 매칭되는 row 없음")
            return 1

    print(f"  처리 site: {len(sites_rows)}개 → {[r[0] for r in sites_rows]}")
    print()

    # 인증 + project 한 번만
    headers, gcid = _load_auth_headers()
    project = _fetch_project(headers, gcid, PROJECT_ID)
    panels = _list_panels(project)
    print(f"Project panels: {len(panels)}개")
    _prefetch_date_ranges(headers, gcid, panels)

    # 사이트별 처리
    results = []
    for site_code, start_date, end_date in sites_rows:
        site_info = lookup_site(site_code)
        result = _process_site(headers, gcid, project, panels,
                                site_info, start_date, end_date,
                                workers=args.workers, limit=args.limit,
                                dry_run=args.dry_run, ts=ts)
        results.append(result)

    # 전체 요약
    print(f"\n{'═'*78}\n[전체 summary]\n{'═'*78}")
    total_ok = sum(r["n_ok"] for r in results)
    total_fail = sum(r["n_fail"] for r in results)
    print(f"  처리 site : {len(results)}")
    print(f"  성공 task : {total_ok}")
    print(f"  실패 task : {total_fail}")
    print(f"\n사이트별:")
    for r in results:
        s = r["site"]
        print(f"  {s.site_code:10}  ({s.rsid:35})  성공={r['n_ok']:3}  실패={r['n_fail']:3}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
