# prewarm_seg_ref_cache.py
# 2026-05-18  Jonghyun Park w/ Claude
"""
segment-ref 캐시 미리 채우기 utility — input csv 없이 segment ID list 만으로
AA GET /segments/{id} 호출 → segment_ref_cache[_<name>].json 채움.

aa_create_segment_v2.2.py 의 dry-run 으로도 자동 채워지지만, input csv 없이도
미리 cache 준비하고 싶을 때 사용 (예: 새 캠페인 시작 전 reference segment 들 한 번에 받아두기).

사용:
  python prewarm_seg_ref_cache.py                        # 아래 SEGMENT_IDS_RAW 사용, 기본 cache 파일
  python prewarm_seg_ref_cache.py --cache us             # segment_ref_cache_us.json 채움
  python prewarm_seg_ref_cache.py --cache global         # segment_ref_cache_global.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# v2.2 의 cache path / load / save helper 재사용
from aa_create_segment_v2.2 import (
    _resolve_cache_path,
    _load_seg_ref_cache,
    _save_seg_ref_cache,
)
# segment GET 은 aa_segment_lookup 의 _lookup_segment 활용 (expansion: definition,name,description,owner,tags,reportSuiteName)
from aa_segment_lookup import _lookup_segment
from aa_create_segment_v2 import _load_auth_headers

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# 한 줄에 하나씩 segment id 박기. 빈 줄 무시. # 으로 시작하면 주석 처리.
SEGMENT_IDS_RAW = """
segment_id_placeholder
"""

# 기본 cache 파일 suffix — "" / "us" / "global" / ... (CLI --cache 로 override 가능)
CACHE_NAME = ""

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

SEGMENT_IDS: list[str] = [
    line.strip()
    for line in SEGMENT_IDS_RAW.splitlines()
    if line.strip() and not line.strip().startswith("#")
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="segment-ref 캐시 prewarm — AA GET 으로 segment container 받아 캐시에 저장")
    parser.add_argument("--cache", default=CACHE_NAME,
                        help=f"캐시 파일 suffix (segment_ref_cache_<name>.json). 빈 값=기본 segment_ref_cache.json. default={CACHE_NAME!r}")
    parser.add_argument("--ids", default="",
                        help="추가 segment id 들 (콤마 구분). 코드 상단 SEGMENT_IDS_RAW 와 합쳐서 처리")
    args = parser.parse_args()

    # CLI --ids + 코드 상단 SEGMENT_IDS_RAW 합치기 (dedup)
    cli_ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    all_ids = list(dict.fromkeys(SEGMENT_IDS + cli_ids))   # 순서 보존 dedup

    if not all_ids:
        print("ERROR: segment id 가 하나도 지정 안 됨. 코드 상단 SEGMENT_IDS_RAW 또는 --ids 로 박을 것.")
        return 1

    cache_path = _resolve_cache_path(args.cache)
    print(f"[cache] {cache_path.name}")
    print(f"[targets] {len(all_ids)} segment id 처리 예정")
    print()

    cache = _load_seg_ref_cache(cache_path)
    print(f"  기존 cache: {len(cache)} 항목")

    # 미캐시된 id + 옛 형식 (container 자체) 인 id 추출 — 둘 다 새 형식으로 fetch
    def _needs_fetch(entry):
        # 새 형식 = {"container": {...}, "name": "..."} 키 있어야 함
        return not (isinstance(entry, dict) and "container" in entry and "name" in entry)
    missing = [sid for sid in all_ids if sid not in cache or _needs_fetch(cache[sid])]
    if not missing:
        print(f"  ✓ 모든 id 이미 캐시됨 — fetch 안 함")
        return 0
    print(f"  미캐시 {len(missing)} 개 → AA GET 진행")
    print()

    print("Authenticating ...")
    headers, gcid = _load_auth_headers()
    print()

    added = 0
    failed: list[str] = []
    for i, sid in enumerate(missing, 1):
        print(f"  [{i}/{len(missing)}] {sid} ...", end=" ")
        try:
            seg_data = _lookup_segment(headers, gcid, sid)
        except Exception as e:
            print(f"FAIL — {e}")
            failed.append(sid)
            continue
        container = (seg_data.get("definition") or {}).get("container") if seg_data else None
        if container is None:
            print("FAIL — container 없음")
            failed.append(sid)
            continue
        # 새 cache 형식 — container + metadata (name lookup 등에 활용)
        cache[sid] = {
            "container": container,
            "name": seg_data.get("name", ""),
            "description": seg_data.get("description", ""),
            "rsid": seg_data.get("rsid", ""),
        }
        added += 1
        name = (seg_data.get("name") or "")[:60]
        print(f"OK  {name}")

    _save_seg_ref_cache(cache_path, cache)
    print()
    print(f"완료 — 추가 {added} / 실패 {len(failed)} / 총 캐시 {len(cache)}")
    print(f"  저장: {cache_path}")
    if failed:
        print(f"  실패 id: {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
