# collapse_panel_tables.py
# 2026-05-13  Jonghyun Park w/ Claude
"""
Adobe Analytics Workspace 프로젝트의 모든 panel 안의 subPanel(테이블/visualization)을
collapsed=True 로 일괄 강제 — 워크스페이스 열었을 때 모든 테이블이 접힌 상태로 보이게.

수행 작업
  1) project GET (definition 포함)
  2) 모든 panel(또는 PANEL_NAME_PATTERN 매칭 panel)의 subPanels[*].collapsed = True
  3) 변경 요약 출력 후 dry-run/apply

설계 원칙
  · panel 자체의 최상위 collapsed (panel 헤더 접힘) 은 건드리지 않음
    — subPanel(테이블) 만 접음. panel 헤더 접으면 워크스페이스 진입 시 panel 자체가 가려져 UX 나쁨.
  · 기본은 dry-run (PUT 안 함). --apply 로 실제 반영.
  · PANEL_NAME_PATTERN 비워두면 모든 panel 처리. 정규식 채우면 매칭 panel 만.

참고
  · panel_date_update/update_panel_date.py — GET/PUT 흐름, dry-run 구조 동일
  · panel_maker/clone_project_first_panel.py — _collapse_all_subpanels 동일 헬퍼

사용 예
  python collapse_panel_tables.py             # dry-run (안전 확인용)
  python collapse_panel_tables.py --apply     # 실제 PUT
  python collapse_panel_tables.py --dump      # 변경 전/후 panel JSON 덤프 (디버깅)
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
# 사용자가 바꿔야 하는 부분 — 다른 프로젝트에 재사용할 때 여기만 수정
# ════════════════════════════════════════════════════════════════════

# Adobe Developer Console OAuth Server-to-Server 자격증명 json 경로
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"

# AA 회사 ID
COMPANY_ID = "company_id"

# 대상 Workspace project ID (URL 의 /workspace/edit/<여기> 부분)
PROJECT_ID = "YOUR_PROJECT_ID"

# 처리 대상 panel 필터 — 빈 문자열이면 모든 panel 처리
# 특정 panel 만 좁히려면 정규식 (예: r"\[AU\]\s*campaign_name'?s\s*Day")
PANEL_NAME_PATTERN = ""

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
        raise RuntimeError(f"GET project 실패: {r.status_code} {r.reason} — {r.text[:500]}")
    return r.json()


def put_project(headers: dict, gcid: str, project_id: str, body: dict) -> dict:
    url = f"{API_BASE}/{gcid}/projects/{project_id}"
    r = requests.put(url, headers=headers, json=body, timeout=120)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"PUT project 실패: {r.status_code} {r.reason} — {r.text[:500]}")
    return r.json()


def iter_panels(project: dict):
    """project.definition.workspaces[].panels[] 를 (ws_idx, p_idx, panel) tuple 로 yield."""
    workspaces = (project.get("definition") or {}).get("workspaces") or []
    for ws_idx, ws in enumerate(workspaces):
        for p_idx, panel in enumerate(ws.get("panels") or []):
            yield ws_idx, p_idx, panel


def collapse_all_subpanels(panel: dict) -> tuple[int, int]:
    """panel.subPanels[*].collapsed = True 로 강제.
    returns (변경된 subPanel 수, 전체 subPanel 수). panel 자체의 collapsed 는 건드리지 않음."""
    sub_panels = panel.get("subPanels") or []
    total = len(sub_panels)
    changed = 0
    for sp in sub_panels:
        if isinstance(sp, dict) and sp.get("collapsed") is not True:
            sp["collapsed"] = True
            changed += 1
    return changed, total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AA Workspace project 내 모든 panel 의 subPanel(테이블) 일괄 collapse"
    )
    parser.add_argument("--apply", action="store_true",
                        help="실제 PUT 실행 (기본은 dry-run)")
    parser.add_argument("--dump", action="store_true",
                        help="변경 전/후 project JSON 을 SCRIPT_DIR 에 저장 (디버그)")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
        ws_count = len((project.get("definition") or {}).get("workspaces") or [])
        print(f"\n❌ definition.workspaces[].panels[] 비어있음 (workspaces={ws_count}) — abort")
        return 1

    # panel 필터링
    if PANEL_NAME_PATTERN:
        rx = re.compile(PANEL_NAME_PATTERN)
        targets = [(w, p, pn) for w, p, pn in all_panels if rx.search(pn.get("name", ""))]
        print(f"\n전체 패널 {len(all_panels)}개 중 패턴 r\"{PANEL_NAME_PATTERN}\" 매칭 {len(targets)}개 처리")
    else:
        targets = all_panels
        print(f"\n전체 패널 {len(all_panels)}개 모두 처리 (PANEL_NAME_PATTERN 비어있음)")

    if not targets:
        print("❌ 처리 대상 panel 없음 — abort")
        return 1

    if args.dump:
        before_path = SCRIPT_DIR / f"project_before_{PROJECT_ID[:8]}_{timestamp}.json"
        before_path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 변경 전 project JSON 덤프: {before_path}")

    # 작업 — deepcopy 후 collapse
    project_after = copy.deepcopy(project)
    total_changed = 0
    total_subs    = 0
    print(f"\n--- panel 별 처리 ---")
    for ws_idx, p_idx, _ in targets:
        panel_after = project_after["definition"]["workspaces"][ws_idx]["panels"][p_idx]
        changed, total = collapse_all_subpanels(panel_after)
        total_changed += changed
        total_subs    += total
        name = panel_after.get("name", "(no name)")
        flag = "✓" if changed else "·"
        print(f"  {flag} [ws{ws_idx} p{p_idx:2d}] {name[:60]:60}  subPanels={total:>3}  changed={changed:>3}")

    print(f"\n--- 합계 ---")
    print(f"  처리 panel    : {len(targets)} 개")
    print(f"  전체 subPanel : {total_subs} 개")
    print(f"  collapse 변경 : {total_changed} 개 (나머지는 이미 collapsed=True)")

    if total_changed == 0:
        print("\nℹ️ 변경할 subPanel 없음 — 이미 모두 collapsed 상태. PUT 생략.")
        return 0

    if args.dump:
        after_path = SCRIPT_DIR / f"project_after_{PROJECT_ID[:8]}_{timestamp}.json"
        after_path.write_text(json.dumps(project_after, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 변경 후 project JSON 덤프: {after_path}")

    if not args.apply:
        print("\nℹ️ Dry-run 모드 — 실제 PUT 안 함. 적용하려면 --apply")
        return 0

    print(f"\nPUT project {PROJECT_ID} ...")
    put_project(headers, gcid, PROJECT_ID, project_after)
    print(f"\n✅ 완료 — {total_changed}개 subPanel collapsed=True 로 변경")
    print(f"   AA Workspace 에서 새로고침해서 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
