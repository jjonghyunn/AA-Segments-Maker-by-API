# aa_segment_lookup_from_pjt.py
# 2026-05-19  Jonghyun Park w/ Claude
# updated: 2026-05-22  — 결과 CSV/DSL 출력 위치를 같은 폴더의 lookup/ 하위로 분리 (LOOKUP_DIR)
# 특정 AA Workspace project 가 사용하는 모든 segment 들을 일괄 lookup.
"""
AA project id → project definition GET → 안에 박힌 모든 segment-ref id 추출 →
각 segment 를 _lookup_segment 로 받아 CSV + DSL 출력.

기존 aa_segment_lookup.py 와 동일한 출력 포맷 (segment_lookup_pjt_<ts>.csv / .dsl).
헬퍼는 aa_segment_lookup 에서 재사용.

사용법:
  python aa_segment_lookup_from_pjt.py <project_id>
  python aa_segment_lookup_from_pjt.py <project_id> --include-disabled
  python aa_segment_lookup_from_pjt.py 5ed7c6e9... --suffix 26sw_pjt

  project id 가 AA URL 의 /workspace/projects/<id> 부분.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

from aa_segment_lookup import (
    _load_auth_headers,
    _lookup_segment,
    _load_user_map,
    _enrich_owner_name,
    decompile_definition,
    format_dsl_block,
    _set_daterange_auth,
    OUTPUT_DIR,
    LOOKUP_DIR,
    COMPANY_ID,
)

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# 빈 값이면 --project 인자 필수. 박혀 있으면 default project_id.
# PROJECT_ID = "YOUR_PROJECT_ID"
PROJECT_ID = "YOUR_PROJECT_ID" # [part_name] 2026 CAMPAIGN NAME | Contents Click Analysis (Product Recommendation) | API (user_id)
# 기존 MD visit, delayed_purchase수정위함.
# https://experience.adobe.com/#/@company_name/so:company_id/analytics/spa/#/workspace/edit/YOUR_PROJECT_ID

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

RESULT_PREFIX = "segment_lookup_pjt_"
SEG_ID_REGEX = re.compile(r"s\d{9}_[0-9a-f]{24}")


def _walk_panels(obj) -> list[dict]:
    """JSON tree 재귀 walk → 'panels' 키 안 dict 들 (= panel 객체) 수집."""
    found: list[dict] = []
    def _rec(o):
        if isinstance(o, dict):
            if isinstance(o.get("panels"), list):
                for p in o["panels"]:
                    if isinstance(p, dict):
                        found.append(p)
            for v in o.values():
                _rec(v)
        elif isinstance(o, list):
            for v in o:
                _rec(v)
    _rec(obj)
    return found


def _fetch_project_segment_ids(headers: dict, gcid: str, project_id: str,
                               panel_names: list[str] | None = None,
                               panel_ids: list[str] | None = None) -> list[str]:
    """project GET → segment id 패턴 unique 추출.

    panel 필터 미지정 → project 전체 JSON 에서 추출.
    panel_names 지정 → panel name substring (case-insensitive) 매칭되는 panel 들만 대상.
    panel_ids 지정 → panel id 정확 매칭. (이름과 id 둘 다 박으면 합집합).

    AA project definition 은 nested 라 정규식 매칭이 안정적. segment id = 's<9digit>_<24hex>'.
    """
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{project_id}"
    r = requests.get(
        url, headers=headers,
        params={"expansion": "definition,name,description,tags,reportSuiteName,ownerFullName"},
        timeout=60,
    )
    if r.status_code != 200:
        print(f"ERROR: project GET 실패 — {r.status_code} {r.reason}: {r.text[:200]}")
        return []
    data = r.json()
    proj_name = data.get("name", "")
    rsid = data.get("rsid", "")
    print(f"  Project name: {proj_name}")
    print(f"  Project rsid: {rsid}")

    import json as _json

    # panel 필터 결정
    panel_names = [p.lower() for p in (panel_names or []) if p.strip()]
    panel_ids = [p for p in (panel_ids or []) if p.strip()]

    if panel_names or panel_ids:
        all_panels = _walk_panels(data)
        print(f"  Project 의 panel 수: {len(all_panels)}")
        matched: list[dict] = []
        for p in all_panels:
            p_name = (p.get("name") or "").lower()
            p_id = p.get("id") or ""
            if panel_ids and p_id in panel_ids:
                matched.append(p)
                continue
            if panel_names and any(kw in p_name for kw in panel_names):
                matched.append(p)
        print(f"  매칭된 panel 수: {len(matched)}")
        for p in matched:
            print(f"     - {p.get('name', '(no name)')}  (id={p.get('id', '')})")
        if not matched:
            print(f"  ⚠️ panel 필터에 매칭되는 게 없음 — 빈 결과 반환")
            return []
        body = _json.dumps(matched, ensure_ascii=False)
    else:
        body = _json.dumps(data, ensure_ascii=False)

    found = SEG_ID_REGEX.findall(body)
    # dedup, preserve order (첫 등장 순)
    seen: set[str] = set()
    uniq: list[str] = []
    for sid in found:
        if sid not in seen:
            seen.add(sid)
            uniq.append(sid)
    return uniq


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="AA project id → 그 project 가 사용하는 모든 segment 들 lookup → CSV + DSL"
    )
    parser.add_argument("project_id", nargs="?", default=PROJECT_ID,
                        help="AA project id. URL 의 /workspace/projects/<id> 부분. "
                             "코드 상단 PROJECT_ID 박으면 인자 생략 가능.")
    parser.add_argument("--panel", nargs="+", default=None,
                        help="panel name 필터 (substring case-insensitive, 여러 개 박으면 OR). "
                             "미지정 시 project 전체에서 segment 추출. 공백 구분.")
    parser.add_argument("--panel-id", dest="panel_id", nargs="+", default=None,
                        help="panel id 정확 매칭 (여러 개 박으면 OR). --panel 과 같이 박으면 합집합.")
    parser.add_argument("--suffix", default="",
                        help="출력 파일명 suffix (예: --suffix 26sw_pjt → segment_lookup_pjt_<ts>_26sw_pjt.csv)")
    args = parser.parse_args()

    project_id = (args.project_id or "").strip()
    if not project_id:
        print("ERROR: project_id 필요. 코드 상단 PROJECT_ID 박거나 인자로 전달.")
        return 1

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{requested_at}] segment lookup from project — {project_id}")
    print(f"  Company: {COMPANY_ID}")
    print()

    print("Authenticating ...")
    headers, gcid = _load_auth_headers()
    _set_daterange_auth(headers, gcid)   # decompile 시 datetime-interval-ref → Date Range name fetch
    print()

    print(f"Fetching project definition ...")
    seg_ids = _fetch_project_segment_ids(
        headers, gcid, project_id,
        panel_names=args.panel, panel_ids=args.panel_id,
    )
    print(f"  추출된 segment id: {len(seg_ids)}개")
    print()

    if not seg_ids:
        print("project 안 segment-ref 없음 — 종료.")
        return 0

    # 일괄 GET
    results: list[dict] = []
    for i, seg_id in enumerate(seg_ids):
        print(f"  [{i+1}/{len(seg_ids)}] {seg_id} ...", end=" ")
        info = _lookup_segment(headers, gcid, seg_id)
        results.append(info)
        if info["error"]:
            print(f"FAIL — {info['error'][:60]}")
        else:
            print(f"OK — {info['name']}")
    print()

    # owner_name 보강
    user_map = _load_user_map()
    if user_map:
        _enrich_owner_name(results, user_map)
        print(f"  owner_name 보강: aa_user_id CSV ({len(user_map)}명)")
    print()

    # 출력 파일명 — lookup/ 하위
    LOOKUP_DIR.mkdir(parents=True, exist_ok=True)
    suffix = (args.suffix or "").strip()
    base_name = f"{RESULT_PREFIX}{timestamp}" + (f"_{suffix}" if suffix else "")
    csv_path = LOOKUP_DIR / f"{base_name}.csv"
    dsl_path = LOOKUP_DIR / f"{base_name}.dsl"

    # CSV
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["segment_id", "name", "owner_id", "owner_name", "rsid",
                     "description", "tags", "structure", "error"])
        for r in results:
            structure = ""
            if r["definition"]:
                try:
                    dsl_text = decompile_definition(r["definition"])
                    structure = dsl_text.replace('"', "'").replace("\n", " | ")
                except Exception:
                    structure = "(decompile error)"
            w.writerow([
                r["segment_id"], r["name"], r["owner_id"], r["owner_name"],
                r["rsid"], r["description"], r["tags"], structure, r["error"],
            ])
    print(f"CSV: {csv_path}")

    # DSL
    dsl_blocks: list[str] = []
    for r in results:
        if r["definition"] is None:
            continue
        try:
            tag_list = [t.strip() for t in r["tags"].split(",") if t.strip()] if r["tags"] else []
            block = format_dsl_block(
                name=r["name"], description=r["description"],
                rsid=r["rsid"], tags=tag_list, definition=r["definition"],
            )
            dsl_blocks.append(block)
        except Exception as e:
            print(f"  WARN: {r['segment_id']} decompile 실패 — {e}")

    if dsl_blocks:
        dsl_path.write_text("\n\n".join(dsl_blocks) + "\n", encoding="utf-8")
        print(f"DSL 구조: {dsl_path}")

    ok = sum(1 for r in results if not r["error"])
    fail = sum(1 for r in results if r["error"])
    print(f"\n[summary] 성공: {ok}, 실패: {fail}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
