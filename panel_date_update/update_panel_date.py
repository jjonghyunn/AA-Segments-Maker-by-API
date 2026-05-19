# update_panel_date.py
# 2026-05-18  Jonghyun Park w/ Claude
"""
Adobe Analytics Workspace 프로젝트의 PANEL_NAME_PATTERN 에 매칭되는 모든 패널에
대해 시작일/종료일을 일괄 치환하는 도구.

수행 작업
  1) 매칭된 각 패널 subtree(JSON)를 재귀 탐색. 기존 날짜 값 몰라도 OK.
     · 값이 ISO interval format "<ISO>/<ISO>" 면 (키 이름 무관) 시작/끝 둘 다 교체.
       예: "dateRange": "2026-04-20T00:00:00/2026-04-20T23:59:59"
            → "<NEW_START>T00:00:00/<NEW_END>T23:59:59"
     · 그 외 단일 ISO 날짜 값이고 key 이름이 start* 계열이면 → NEW_START_DATE 로,
       end* 계열이면 → NEW_END_DATE 로 교체.
  2) OLD_*_DATE 가 설정돼 있으면 추가로 패널 name 안의 OLD → NEW substring 치환.
  3) 모든 패널 변경을 합쳐 project 1회 PUT.

설계 원칙
  · 매칭 패널 0개면 abort. 1개 이상이면 모두 처리.
  · 기본은 dry-run (실제 PUT 안 함). 실제 반영하려면 --apply
  · 매칭 안 된 패널/프로젝트 메타데이터는 손대지 않음 — 매칭 패널 subtree 안에서만 치환

사용 예
  python update_panel_date.py             # dry-run (안전 확인용)
  python update_panel_date.py --apply     # 실제 PUT
  python update_panel_date.py --dump      # 매칭 패널 JSON 전체를 파일로 덤프 (디버깅)
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분 — 다른 캠페인/패널에 재사용할 때 여기만 수정
# ════════════════════════════════════════════════════════════════════

# Adobe Developer Console에서 받은 OAuth Server-to-Server 자격증명 json 경로
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"

# AA 회사(login company) ID
COMPANY_ID = "company_id"

# 대상 Workspace project ID (URL 의 /workspace/edit/<여기> 부분)
PROJECT_ID = "YOUR_PROJECT_ID" # CAMPAIGN NAME Scenario cc09 component only
# PROJECT_ID = "YOUR_PROJECT_ID" # CAMPAIGN NAME (user_id)플젝
# PROJECT_ID = "YOUR_PROJECT_ID"   # CAMPAIGN NAME 캠페인 프로젝트

# 패널 이름이 이 정규식에 매칭되는 모든 패널을 일괄 치환한다 (0개면 abort).
# 1개만 좁히고 싶으면 사이트코드 등으로 좁힐 것.
# 주의: 정규식에서 `[` `]` 는 character class 이므로 literal 매칭하려면 escape 필요.
#   ✗ r"[Global] Content Analysis"   → "G/l/o/b/a 중 한 글자" 로 해석됨
#   ✓ r"\[Global\] Content Analysis" → literal "[Global]" 매칭
# 예시:
#   r"\bContent Analysis\b"                — 모든 사이트의 Content Analysis 패널 일괄
#   r"\[AU\]\s*campaign_name'?s\s*Day\s*Campaign" — 특정 사이트 1개만
#   r"\[Global\]\s*Content Analysis"       — Global 사이트의 Content Analysis 만
PANEL_NAME_PATTERN = r"\bContent Analysis\b"

# 시작/종료일 — 트리 안 start*/end* 키의 값을 이 값으로 교체 (기존 값 무관).
NEW_START_DATE = "2026-05-11"
NEW_END_DATE   = "2026-05-17"

# (선택) 패널 name 텍스트 안의 옛 날짜 substring 치환용. 비워두면 name 안 건드림.
# 트리 안 dateRange / reportRange 등은 위 NEW_*_DATE 로 키 기반 교체되므로 무관.
OLD_START_DATE = ""   # 예: "2026-05-01"
OLD_END_DATE   = ""   # 예: "2026-05-11"

# ════════════════════════════════════════════════════════════════════
# 내부 사용 — 보통 수정 불필요
# ════════════════════════════════════════════════════════════════════

API_BASE = "https://analytics.adobe.io/api"
SCRIPT_DIR = Path(__file__).parent

# 날짜 key 패턴 — 트리 워크 중 이 패턴에 매칭되는 key 의 값을 교체.
# 매칭 예: start, startDate, startDateTime, startTimestamp, end, endDate, ...
# startMs / endMs (millis) 도 키 매칭은 되지만 값에 ISO 날짜 없으니 자동 패스.
START_KEY_RE = re.compile(r"^start(?:Date|DateTime|Timestamp|Ms)?$", re.IGNORECASE)
END_KEY_RE   = re.compile(r"^end(?:Date|DateTime|Timestamp|Ms)?$",   re.IGNORECASE)
ISO_DATE_RE  = re.compile(r"\d{4}-\d{2}-\d{2}")

# ISO 8601 Interval 형식 — "<시작ISO>/<끝ISO>" 단일 문자열 (Adobe Workspace 의
# dateRange 값이 보통 이 형태로 박힘. 예: "2026-04-20T00:00:00/2026-04-20T23:59:59").
# 키 이름과 무관하게 이 패턴이 매칭되면 시작/끝 날짜 부분만 NEW_*_DATE 로 교체.
ISO_INTERVAL_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}(?:T[\d:.+\-Z]*)?)/(\d{4}-\d{2}-\d{2}(?:T[\d:.+\-Z]*)?)$"
)


def load_auth_headers() -> tuple[dict, str]:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h = dict(ags.header) if isinstance(getattr(ags, "header", None), dict) else {}
    h_lower = {k.lower(): v for k, v in h.items()}
    api_key = h_lower.get("x-api-key")
    auth = h_lower.get("authorization")
    gcid = h_lower.get("x-proxy-global-company-id")
    if not (api_key and auth and gcid):
        raise RuntimeError("필수 헤더 누락 (api_key/authorization/x-proxy-global-company-id)")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def get_project(headers: dict, gcid: str, project_id: str) -> dict:
    url = f"{API_BASE}/{gcid}/projects/{project_id}"
    params = {
        "expansion": (
            "definition,ownerFullName,modified,tags,shares,"
            "reportSuiteName,externalReferences,accessLevel"
        )
    }
    r = requests.get(url, headers=headers, params=params, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(
            f"GET project 실패: {r.status_code} {r.reason} — {r.text[:500]}"
        )
    return r.json()


def put_project(headers: dict, gcid: str, project_id: str, body: dict) -> dict:
    url = f"{API_BASE}/{gcid}/projects/{project_id}"
    r = requests.put(url, headers=headers, json=body, timeout=120)
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"PUT project 실패: {r.status_code} {r.reason} — {r.text[:500]}"
        )
    return r.json()


def iter_panels(project: dict):
    """project.definition.workspaces[].panels[] 를 (ws_idx, p_idx, panel) tuple 로 yield."""
    workspaces = (project.get("definition") or {}).get("workspaces") or []
    for ws_idx, ws in enumerate(workspaces):
        for p_idx, panel in enumerate(ws.get("panels") or []):
            yield ws_idx, p_idx, panel


def find_matching_panels(project: dict, pattern: str) -> list[tuple[int, int, dict]]:
    rx = re.compile(pattern)
    return [
        (ws_idx, p_idx, panel)
        for ws_idx, p_idx, panel in iter_panels(project)
        if rx.search(panel.get("name", ""))
    ]


def replace_in_subtree(node, old: str, new: str) -> int:
    """node 트리(dict/list)를 재귀 탐색하면서 문자열 안의 old → new 치환.
    치환된 누적 횟수 반환. (name 안 OLD_*_DATE substring 치환용 등)"""
    count = 0
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, str):
                if old in v:
                    count += v.count(old)
                    node[k] = v.replace(old, new)
            elif isinstance(v, (dict, list)):
                count += replace_in_subtree(v, old, new)
    elif isinstance(node, list):
        for item in node:
            count += replace_in_subtree(item, old, new)
    return count


def replace_date_fields(node, new_start: str, new_end: str) -> tuple[int, int, int, list[str]]:
    """트리 재귀 탐색하면서 날짜 값 교체. 기존 날짜 값 무관.
    교체 대상:
      A) 값이 ISO interval format "<ISO>/<ISO>" 인 경우 (키 이름 무관) — 시작/끝 둘 다.
         예: "dateRange": "2026-04-20T00:00:00/2026-04-20T23:59:59"
      B) 값에 단일 ISO 날짜만 있고 key 이름이 start* 매칭 → new_start 로.
      C) 값에 단일 ISO 날짜만 있고 key 이름이 end* 매칭 → new_end 로.
    Returns (n_interval, n_start, n_end, samples_for_log)."""
    counters = {"interval": 0, "start": 0, "end": 0}
    samples: list[str] = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, str):
                    # A) ISO interval format — 키 이름 무관
                    m = ISO_INTERVAL_RE.match(v)
                    if m:
                        sp = ISO_DATE_RE.sub(new_start, m.group(1), count=1)
                        ep = ISO_DATE_RE.sub(new_end,   m.group(2), count=1)
                        new_v = f"{sp}/{ep}"
                        if new_v != v:
                            obj[k] = new_v
                            counters["interval"] += 1
                            samples.append(f"  [interval] {k}: {v!r} → {new_v!r}")
                        continue
                    # B/C) 단일 ISO 날짜 + start*/end* 키 매칭
                    if ISO_DATE_RE.search(v):
                        if START_KEY_RE.search(k):
                            new_v = ISO_DATE_RE.sub(new_start, v)
                            if new_v != v:
                                obj[k] = new_v
                                counters["start"] += 1
                                samples.append(f"  [start]    {k}: {v!r} → {new_v!r}")
                        elif END_KEY_RE.search(k):
                            new_v = ISO_DATE_RE.sub(new_end, v)
                            if new_v != v:
                                obj[k] = new_v
                                counters["end"] += 1
                                samples.append(f"  [end]      {k}: {v!r} → {new_v!r}")
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(node)
    return counters["interval"], counters["start"], counters["end"], samples


def collect_date_strings(node, found: list[str]) -> None:
    """node 트리에서 ISO 형식 날짜처럼 보이는 문자열을 모두 수집 (디버그용)."""
    iso_re = re.compile(r"\d{4}-\d{2}-\d{2}")
    if isinstance(node, dict):
        for v in node.values():
            if isinstance(v, str) and iso_re.search(v):
                found.append(v)
            elif isinstance(v, (dict, list)):
                collect_date_strings(v, found)
    elif isinstance(node, list):
        for item in node:
            collect_date_strings(item, found)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AA Workspace 패널 1개의 종료일 치환 도구"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="실제 PUT 실행 (기본은 dry-run)"
    )
    parser.add_argument(
        "--dump", action="store_true",
        help="매칭 패널의 JSON 을 SCRIPT_DIR 에 timestamp 붙여 저장 (디버그)"
    )
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")

    headers, gcid = load_auth_headers()

    print(f"[{now:%Y-%m-%d %H:%M:%S}] GET project {PROJECT_ID} ...")
    project = get_project(headers, gcid, PROJECT_ID)

    print(f"\n프로젝트: {project.get('name', '(no name)')}")
    print(f"  owner    : {project.get('ownerFullName', '?')}")
    print(f"  modified : {project.get('modified', '?')}")

    all_panels = list(iter_panels(project))
    if not all_panels:
        print("\n❌ 프로젝트 definition.workspaces[].panels[] 가 비어있습니다 — abort")
        ws_count = len((project.get("definition") or {}).get("workspaces") or [])
        print(f"   (workspaces 수: {ws_count})")
        return 1

    print(f"\n전체 패널 {len(all_panels)}개 (워크스페이스 × 패널):")
    for ws_idx, p_idx, p in all_panels:
        print(f"  [ws{ws_idx} p{p_idx:2d}] {p.get('name', '(no name)')}")

    matches = find_matching_panels(project, PANEL_NAME_PATTERN)
    if not matches:
        print(f"\n❌ 패턴 r\"{PANEL_NAME_PATTERN}\" 에 매칭되는 패널 없음 — abort")
        return 1

    print(f"\n✅ {len(matches)}개 매칭 패널:")
    for ws_idx, p_idx, p in matches:
        print(f"     [ws{ws_idx} p{p_idx}] {p.get('name')}")

    # 각 매칭 패널 처리 — deepcopy + 치환 후 project tree 에 반영, 마지막에 1회 PUT
    total_interval = 0
    total_start = 0
    total_end = 0
    total_name = 0
    for ws_idx, p_idx, panel in matches:
        if args.dump:
            dump_path = SCRIPT_DIR / f"panel_dump_{PROJECT_ID[:8]}_ws{ws_idx}_p{p_idx}_{timestamp}.json"
            dump_path.write_text(
                json.dumps(panel, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"💾 패널 JSON 덤프 저장: {dump_path}")

        before_dates: list[str] = []
        collect_date_strings(panel, before_dates)
        name_before = panel.get("name", "")
        print(f"\n  ── [ws{ws_idx} p{p_idx}] {name_before} ──")
        print(f"  subtree 안 ISO 날짜 문자열 (최대 10개):")
        for s in before_dates[:10]:
            print(f"    - {s}")
        if len(before_dates) > 10:
            print(f"    ... ({len(before_dates) - 10}개 더 있음)")

        panel_after = copy.deepcopy(panel)

        # 1) 트리 교체 — interval 형식 "<ISO>/<ISO>" 값 + start*/end* 키 값
        n_interval, n_start, n_end, samples = replace_date_fields(
            panel_after, NEW_START_DATE, NEW_END_DATE
        )
        if samples:
            print(f"  트리 교체 ({n_interval} interval + {n_start} start + {n_end} end):")
            for s in samples[:20]:
                print(s)
            if len(samples) > 20:
                print(f"    ... ({len(samples) - 20}개 더)")
        else:
            print(f"  트리 교체: 변경 없음 (interval / start* / end* 매칭 없음)")

        # 2) (선택) name 안 OLD substring 치환 — OLD_*_DATE 비어있으면 건너뜀
        n_name_this = 0
        nm = panel_after.get("name", "") or ""
        if OLD_START_DATE and OLD_START_DATE in nm:
            n_name_this += nm.count(OLD_START_DATE)
            nm = nm.replace(OLD_START_DATE, NEW_START_DATE)
        if OLD_END_DATE and OLD_END_DATE in nm:
            n_name_this += nm.count(OLD_END_DATE)
            nm = nm.replace(OLD_END_DATE, NEW_END_DATE)
        panel_after["name"] = nm
        name_after = nm
        if name_before != name_after:
            print(f"  name 변경: {name_before!r} → {name_after!r}")

        total_interval += n_interval
        total_start += n_start
        total_end += n_end
        total_name += n_name_this

        project["definition"]["workspaces"][ws_idx]["panels"][p_idx] = panel_after

    print(f"\n--- 합계 ---")
    print(f"  NEW_START_DATE    : {NEW_START_DATE}")
    print(f"  NEW_END_DATE      : {NEW_END_DATE}")
    print(f"  매칭 패널         : {len(matches)}개")
    print(f"  interval 교체     : {total_interval}건 (<ISO>/<ISO> 형식)")
    print(f"  start 키 교체     : {total_start}건")
    print(f"  end 키 교체       : {total_end}건")
    print(f"  name substring    : {total_name}건 (OLD_*_DATE 비어있으면 0)")

    if total_interval + total_start + total_end + total_name == 0:
        print(f"\n⚠️ 매칭된 어느 패널에도 교체할 날짜 값이 없습니다.")
        print(f"   --dump 로 패널 JSON 떠서 start/end 키 구조 확인해보세요.")
        return 1

    if not args.apply:
        print("\nℹ️ Dry-run 모드 — 실제 PUT 안 함. 적용하려면 --apply")
        return 0

    print(f"\nPUT project {PROJECT_ID} ...")
    put_project(headers, gcid, PROJECT_ID, project)
    print(f"\n✅ 완료 — start={NEW_START_DATE} end={NEW_END_DATE} ({len(matches)}개 패널)")
    print(f"   AA Workspace 에서 새로고침해서 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
