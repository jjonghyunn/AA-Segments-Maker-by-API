# update_panel_date.py
# 2026-05-11  Jonghyun Park w/ Claude
"""
Adobe Analytics Workspace 프로젝트의 특정 패널 1개에 대해
종료일을 일괄 치환하는 도구.

수행 작업
  1) 패널 name 안의 OLD_END_DATE 문자열 → NEW_END_DATE 로 치환
  2) 그 패널의 subtree(JSON)를 재귀 탐색해서 dateRange / reportRange /
     endTimestamp / endDate 등 어디에 박혀있든 OLD_END_DATE → NEW_END_DATE 로
     문자열 치환

설계 원칙
  · 1개 패널만 정확히 매칭되어야 진행 (0개 또는 2개 이상이면 abort)
  · 기본은 dry-run (실제 PUT 안 함). 실제 반영하려면 --apply
  · 다른 패널/프로젝트 메타데이터는 손대지 않음 — 매칭된 패널 subtree 안에서만 치환

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
PROJECT_ID = "YOUR_PROJECT_ID"

# 패널 이름이 이 정규식에 매칭되는 패널 1개만 처리한다.
# CAMPAIGN NAME Campaign 패널들이 사이트코드별([DE], [UK] ...)로 여러 개 있을 경우,
# 사이트코드까지 포함시켜 1개만 매칭되게 좁혀야 함.
PANEL_NAME_PATTERN = r"\[AU\]\s*campaign_name'?s\s*Day\s*Campaign"

# 패널 name 및 subtree 안에서 치환할 종료일 문자열
OLD_END_DATE = "2026-05-10"
NEW_END_DATE = "2026-05-11"

# ════════════════════════════════════════════════════════════════════
# 내부 사용 — 보통 수정 불필요
# ════════════════════════════════════════════════════════════════════

API_BASE = "https://analytics.adobe.io/api"
SCRIPT_DIR = Path(__file__).parent


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
    치환된 누적 횟수 반환."""
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
    if len(matches) > 1:
        print(f"\n❌ {len(matches)}개 매칭 — 1개만 매칭되도록 PANEL_NAME_PATTERN 좁혀주세요:")
        for ws_idx, p_idx, p in matches:
            print(f"     [ws{ws_idx} p{p_idx}] {p.get('name')}")
        return 1

    ws_idx, p_idx, panel = matches[0]
    print(f"\n✅ 매칭 패널: [ws{ws_idx} p{p_idx}] {panel.get('name')}")

    if args.dump:
        dump_path = SCRIPT_DIR / f"panel_dump_{PROJECT_ID[:8]}_ws{ws_idx}_p{p_idx}_{timestamp}.json"
        dump_path.write_text(
            json.dumps(panel, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"💾 패널 JSON 덤프 저장: {dump_path}")

    before_dates: list[str] = []
    collect_date_strings(panel, before_dates)
    print(f"\n매칭 패널 subtree 안 ISO 날짜 문자열 (중복 포함, 최대 30개):")
    for s in before_dates[:30]:
        print(f"  - {s}")
    if len(before_dates) > 30:
        print(f"  ... ({len(before_dates) - 30}개 더 있음)")

    panel_after = copy.deepcopy(panel)
    n_replaced = replace_in_subtree(panel_after, OLD_END_DATE, NEW_END_DATE)

    print(f"\n--- 치환 결과 ---")
    print(f"  OLD → NEW : {OLD_END_DATE} → {NEW_END_DATE}")
    print(f"  치환 횟수 : {n_replaced}")

    if n_replaced == 0:
        print(f"\n⚠️ 패널 subtree 에 '{OLD_END_DATE}' 가 없어 치환할 게 없습니다.")
        print(f"   OLD_END_DATE 값을 위 ISO 날짜 목록과 대조해서 확인하세요.")
        return 1

    print(f"\n--- name 변화 ---")
    print(f"  BEFORE: {panel.get('name')}")
    print(f"  AFTER : {panel_after.get('name')}")

    if not args.apply:
        print("\nℹ️ Dry-run 모드 — 실제 PUT 안 함. 적용하려면 --apply")
        return 0

    # 실제 적용: project.definition.workspaces[ws_idx].panels[p_idx] 를 panel_after 로 교체 후 PUT
    project["definition"]["workspaces"][ws_idx]["panels"][p_idx] = panel_after

    print(f"\nPUT project {PROJECT_ID} ...")
    put_project(headers, gcid, PROJECT_ID, project)
    print(f"\n✅ 완료 — {OLD_END_DATE} → {NEW_END_DATE} ({n_replaced}회 치환)")
    print(f"   AA Workspace 에서 새로고침해서 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
