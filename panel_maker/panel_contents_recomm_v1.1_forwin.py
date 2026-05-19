# panel_contents_recomm_v1.1_forwin.py
# 2026-05-18  Jonghyun Park w/ Claude
# updated: 2026-05-19 22:55  — Windows용 (AUTH_JSON_PATH = Windows 경로)
#
# panel_contents_recomm.py 의 후속 도구.
# 이미 R01~R14 까지 반영된 프로젝트에 R15 (Theme Category Popular) 컬럼을
# R14 오른쪽에 삽입한다.
#
# 동작:
#   1. target 프로젝트 GET
#   2. 각 panel(Global/US) → 각 subPanel 의 columnTree 탐색
#   3. 마지막 세그먼트 노드의 이름으로 suffix 유형 판별:
#      - "(Visit)"            → visit
#      - "(Delayed Purchase)" → delayed
#      - 그 외               → none (no suffix)
#   4. R14_TO_R15 매핑에서 해당 (panel_type, suffix) 의 R15 ID 가 있으면
#      R14 노드를 deep-copy → R15 정보로 교체 → R14 바로 뒤에 삽입
#   5. PUT 으로 반영
#
# 사용:
#   python panel_contents_recomm_v1.1.py             # dry-run (기본)
#   python panel_contents_recomm_v1.1.py --apply     # 실제 PUT
"""
Recomm 15 컬럼을 기존 프로젝트의 R14 옆에 삽입하는 도구.

R14 가 있는 subPanel 을 자동 탐색하고, suffix 유형 (Visit / Delayed Purchase / 없음) 에
따라 알맞은 R15 segment ID 를 가진 컬럼 노드를 deep-copy + 삽입.

6가지 경우의수:
  Global × (none, visit, delayed)  =  3
  US     × (none, visit, delayed)  =  3

R15 segment ID 가 비어있는 경우의수는 자동으로 skip.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import uuid
from datetime import datetime

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
COMPANY_ID = "company_id"

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
TARGET_PROJECT_ID = "YOUR_PROJECT_ID"  # team공유용

# ─── R14 → R15 매핑 ────────────────────────────────────────────────
# (panel_type, suffix) → { r14_id, r15_id, r15_name }
# r15_id 가 "" 이면 해당 경우의수는 skip.
# panel_type: "global" = Panel[0], "us" = Panel[1]
# suffix:     "none" / "visit" / "delayed"

R14_TO_R15 = {
    # ── Global ──
    ("global", "none"): {
        "r14_id": "segment_id_placeholder",
        "r15_id": "",  # R15 no-suffix 없음 → skip
        "r15_name": "",
    },
    ("global", "visit"): {
        "r14_id": "segment_id_placeholder",
        "r15_id": "segment_id_placeholder",
        "r15_name": "Product Recommendation - 15. Theme Category Popular (Visit)",
    },
    ("global", "delayed"): {
        "r14_id": "segment_id_placeholder",
        "r15_id": "segment_id_placeholder",
        "r15_name": "Product Recommendation - 15. Theme Category Popular (Delayed Purchase)",
    },
    # ── US ──
    ("us", "none"): {
        "r14_id": "segment_id_placeholder",
        "r15_id": "",  # US R15 no-suffix 없음 → skip
        "r15_name": "",
    },
    ("us", "visit"): {
        "r14_id": "segment_id_placeholder",
        "r15_id": "",  # US R15 Visit 미생성 → skip
        "r15_name": "[US] Product Recommendation - 15. Theme Category Popular (Visit)",
    },
    ("us", "delayed"): {
        "r14_id": "segment_id_placeholder",
        "r15_id": "",  # US R15 Delayed 미생성 → skip
        "r15_name": "[US] Product Recommendation - 15. Theme Category Popular (Delayed Purchase)",
    },
}

# ─── columnTree 탐색 깊이 ──────────────────────────────────────────
# 세그먼트 노드가 위치하는 깊이: nodes[0].nodes[0].nodes[0].nodes[0].nodes
SEGMENT_DEPTH = 4

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

SUFFIX_VISIT_RE = re.compile(r"\(\s*Visit\s*\)\s*$", re.IGNORECASE)
SUFFIX_DELAYED_RE = re.compile(r"\(\s*Delayed\s+Purchase\s*\)\s*$", re.IGNORECASE)

PANEL_TYPE_MAP = {0: "global", 1: "us"}


# ─── Auth / API helpers ───────────────────────────────────────────

def _auth() -> tuple[dict, str]:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h_lower = {k.lower(): v for k, v in dict(ags.header).items()}
    api_key = h_lower.get("x-api-key")
    auth = h_lower.get("authorization")
    gcid = h_lower.get("x-proxy-global-company-id")
    if not (api_key and auth and gcid):
        raise RuntimeError("필수 헤더 누락 — auth json / company id 확인")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def _fetch_project(headers: dict, gcid: str, pid: str) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{pid}"
    r = requests.get(
        url, headers=headers,
        params={"expansion": "definition,ownerFullName,modifiedDate,sharesFullName,tags,name"},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GET project {pid} failed: {r.status_code} {r.text[:400]}")
    return r.json()


def _put_project(headers: dict, gcid: str, pid: str, body: dict) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{pid}"
    r = requests.put(
        url, headers=headers,
        params={"expansion": "definition,ownerFullName,modifiedDate,name"},
        data=json.dumps(body),
        timeout=120,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"PUT project {pid} failed: {r.status_code} {r.text[:600]}")
    return r.json()


# ─── 핵심 로직 ────────────────────────────────────────────────────

def _detect_suffix(name: str) -> str:
    """세그먼트 이름에서 suffix 유형 판별."""
    if SUFFIX_VISIT_RE.search(name):
        return "visit"
    if SUFFIX_DELAYED_RE.search(name):
        return "delayed"
    return "none"


def _get_seg_nodes(column_tree: dict) -> list | None:
    """columnTree 에서 SEGMENT_DEPTH 만큼 내려가 세그먼트 노드 리스트 반환."""
    try:
        node = column_tree["nodes"][0]
        for _ in range(SEGMENT_DEPTH - 1):
            node = node["nodes"][0]
        return node["nodes"]
    except (KeyError, IndexError):
        return None


def _find_r14_index(seg_nodes: list, r14_id: str) -> int | None:
    """seg_nodes 에서 R14 segment ID 를 가진 노드의 인덱스 반환."""
    for i, node in enumerate(seg_nodes):
        if node.get("component", {}).get("id") == r14_id:
            return i
    return None


def _make_r15_node(r14_node: dict, r15_id: str, r15_name: str) -> dict:
    """R14 노드를 deep-copy 해서 R15 정보로 교체."""
    new_node = copy.deepcopy(r14_node)
    new_node["id"] = str(uuid.uuid4())
    new_node["name"] = r15_name
    new_node["component"]["id"] = r15_id
    if "__metaData__" in new_node["component"]:
        new_node["component"]["__metaData__"]["name"] = r15_name
    return new_node


def process_project(defn: dict, dry_run: bool = True) -> int:
    """프로젝트 definition 을 순회하며 R15 노드를 삽입. 삽입 건수 반환."""
    panels = defn.get("workspaces", [{}])[0].get("panels", [])
    inserted = 0

    for pi, panel in enumerate(panels):
        panel_type = PANEL_TYPE_MAP.get(pi)
        if panel_type is None:
            continue
        panel_name = panel.get("name", f"Panel[{pi}]")

        for si, sp in enumerate(panel.get("subPanels", [])):
            ct = sp.get("reportlet", {}).get("columnTree")
            if not ct:
                continue

            seg_nodes = _get_seg_nodes(ct)
            if not seg_nodes:
                continue

            # suffix 판별 (마지막 노드 이름 기준)
            last_name = seg_nodes[-1].get("name", "")
            suffix = _detect_suffix(last_name)

            # 매핑 조회
            key = (panel_type, suffix)
            mapping = R14_TO_R15.get(key)
            if not mapping or not mapping["r15_id"]:
                print(f"  SKIP  {panel_name} subPanel[{si}] suffix={suffix} — R15 ID 없음")
                continue

            # R14 위치 찾기
            r14_idx = _find_r14_index(seg_nodes, mapping["r14_id"])
            if r14_idx is None:
                print(f"  WARN  {panel_name} subPanel[{si}] — R14 ID 못 찾음: {mapping['r14_id']}")
                continue

            # R15 이미 있는지 확인
            already = any(
                n.get("component", {}).get("id") == mapping["r15_id"]
                for n in seg_nodes
            )
            if already:
                print(f"  EXISTS {panel_name} subPanel[{si}] suffix={suffix} — R15 이미 존재, skip")
                continue

            # R15 노드 생성 & 삽입
            r15_node = _make_r15_node(seg_nodes[r14_idx], mapping["r15_id"], mapping["r15_name"])
            insert_pos = r14_idx + 1

            if not dry_run:
                seg_nodes.insert(insert_pos, r15_node)

            inserted += 1
            mode = "DRY" if dry_run else "INSERT"
            print(f"  {mode}  {panel_name} subPanel[{si}] suffix={suffix} "
                  f"— R15 at idx {insert_pos} ({mapping['r15_name'][:50]})")

    return inserted


# ─── main ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="R15 column 삽입 (R14 오른쪽)")
    parser.add_argument("--apply", action="store_true", help="실제 PUT 실행 (기본: dry-run)")
    args = parser.parse_args()
    dry_run = not args.apply

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_label = "DRY-RUN" if dry_run else "APPLY"
    print(f"\n{'='*60}")
    print(f"panel_contents_recomm_v1.1  [{mode_label}]  {ts}")
    print(f"target: {TARGET_PROJECT_ID}")
    print(f"{'='*60}\n")

    # 인증
    print("[1/3] 인증...")
    headers, gcid = _auth()
    print("  OK\n")

    # 프로젝트 GET
    print("[2/3] 프로젝트 GET...")
    proj = _fetch_project(headers, gcid, TARGET_PROJECT_ID)
    defn = proj["definition"]
    print(f"  OK — {proj.get('name','?')}\n")

    # R15 삽입
    print("[3/3] R15 삽입 탐색...")
    count = process_project(defn, dry_run=dry_run)
    print(f"\n  총 {count} 건 {'예정' if dry_run else '삽입'}\n")

    if count == 0:
        print("삽입 대상 없음. 종료.")
        return 0

    if dry_run:
        print("--apply 옵션으로 실행하면 실제 PUT 됩니다.")
        return 0

    # 실제 PUT
    print("[PUT] 프로젝트 저장...")
    # process_project 에서 이미 defn 을 in-place 수정했으므로 바로 PUT
    result = _put_project(headers, gcid, TARGET_PROJECT_ID, proj)
    print(f"  OK — modifiedDate: {result.get('modifiedDate','?')}\n")

    # 검증: 다시 GET 해서 R15 존재 확인
    print("[검증] 프로젝트 재조회...")
    verify = _fetch_project(headers, gcid, TARGET_PROJECT_ID)
    v_defn = verify["definition"]
    found_r15 = []
    panels = v_defn.get("workspaces", [{}])[0].get("panels", [])
    for pi, panel in enumerate(panels):
        for si, sp in enumerate(panel.get("subPanels", [])):
            ct = sp.get("reportlet", {}).get("columnTree")
            if not ct:
                continue
            seg_nodes = _get_seg_nodes(ct)
            if not seg_nodes:
                continue
            for node in seg_nodes:
                cid = node.get("component", {}).get("id", "")
                for key, mapping in R14_TO_R15.items():
                    if mapping["r15_id"] and cid == mapping["r15_id"]:
                        found_r15.append(f"  Panel[{pi}] subPanel[{si}]: {node.get('name','?')}")

    if found_r15:
        print(f"  R15 {len(found_r15)} 건 확인:")
        for line in found_r15:
            print(line)
    else:
        print("  WARNING: R15 를 찾지 못했습니다!")

    print("\n완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
