"""panel_contents.py 가 처리하는 source panel 의 JSON 구조에서
끝쪽 sub_num segment ID 가 어디 박혀있는지 검색해서 보여주는 일회성 inspection 도구.

panel_contents.py 의 인증/PROJECT_ID 를 그대로 import 해서 사용."""
from __future__ import annotations
import json
import sys
from pathlib import Path

# panel_contents.py 와 같은 폴더라 import 가능
import panel_contents as pc

# 검색 대상 — MD CC_03 - 04. Running Ritual 변형들 (1956.csv 결과 기반)
TAIL_SEG_IDS = [
    "세그먼트_아이디_넘버",  # CC_03 - 04. Running Ritual
    "세그먼트_아이디_넘버",  # CC_03 - 04. Running Ritual (Visit)
    "세그먼트_아이디_넘버",  # CC_03 - 04. Running Ritual (Delayed Purchase)
    "세그먼트_아이디_넘버",  # US_CC_03 - 04. Running Ritual
    "세그먼트_아이디_넘버",  # US_CC_03 - 04. Running Ritual (Visit)
    "세그먼트_아이디_넘버",  # US_CC_03 - 04. Running Ritual (Delayed Purchase)
]


def find_occurrences(node, target_ids: set[str], path: str = "panel"):
    """트리 walk 하면서 target_id 가 박혀있는 위치를 모두 yield."""
    if isinstance(node, dict):
        for k, v in node.items():
            child_path = f"{path}.{k}"
            if isinstance(v, str) and v in target_ids:
                yield child_path, v, "dict-value", k
            elif isinstance(v, (dict, list)):
                yield from find_occurrences(v, target_ids, child_path)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            child_path = f"{path}[{i}]"
            if isinstance(item, str) and item in target_ids:
                yield child_path, item, "list-string", None
            elif isinstance(item, (dict, list)):
                yield from find_occurrences(item, target_ids, child_path)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    headers, gcid = pc._auth()
    print(f"[inspect] source project: {pc.SOURCE_PROJECT_ID}")
    src = pc._fetch_project(headers, gcid, pc.SOURCE_PROJECT_ID)
    src_def = src.get("definition") or {}
    src_workspaces = src_def.get("workspaces") or []
    if not src_workspaces:
        print("workspaces 없음")
        return 1
    src_panels = src_workspaces[0].get("panels") or []
    print(f"panels: {len(src_panels)} 개")

    target_ids = set(TAIL_SEG_IDS)
    occurrences = []
    for p_idx, panel in enumerate(src_panels):
        for path, sid, kind, key in find_occurrences(panel, target_ids, f"panels[{p_idx}]"):
            occurrences.append((path, sid, kind, key))

    print(f"\n총 {len(occurrences)} 곳 발견:")
    for path, sid, kind, key in occurrences[:80]:
        print(f"  [{kind}] {path}  → {sid}")
    if len(occurrences) > 80:
        print(f"  ... +{len(occurrences) - 80} 곳")

    # 부모 context 까지 dump (위치 sample 5개)
    print(f"\n샘플 위치 5개의 부모 dict 구조 확인:")
    for path, sid, kind, key in occurrences[:5]:
        # path 거슬러 올라가서 parent dict 찾기 — 단순화: 그냥 path 끝 일부 슬라이스로
        parts = path.replace("[", ".[").split(".")
        node = src_panels
        for p in parts[1:]:  # parts[0] = 'panels'
            if p.startswith("[") and p.endswith("]"):
                node = node[int(p[1:-1])]
            else:
                node = node.get(p) if isinstance(node, dict) else None
            if node is None:
                break
        # node 의 부모 (한 단계 위) 못 잡으니, node 자체가 string 인 경우 path 슬라이스로 다시
        # 간단히 그 segment ID 가 들어있는 dict 일부만 json dump
        print(f"\n=== {path} ===")
        # path 끝의 dict/list 부분 출력
        # 부모 dict 다시 찾기
        ancestors_node = src_panels
        last_parent = None
        for p in parts[1:-1]:
            if p.startswith("[") and p.endswith("]"):
                ancestors_node = ancestors_node[int(p[1:-1])]
            else:
                ancestors_node = ancestors_node.get(p) if isinstance(ancestors_node, dict) else None
        if isinstance(ancestors_node, (dict, list)):
            dumped = json.dumps(ancestors_node, ensure_ascii=False, indent=2)
            if len(dumped) > 2000:
                dumped = dumped[:2000] + "\n  ...(truncated)"
            print(dumped)

    return 0


if __name__ == "__main__":
    sys.exit(main())
