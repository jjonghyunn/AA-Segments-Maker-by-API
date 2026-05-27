# _move_stale_to_old.py
# 2026-05-19  Jonghyun Park w/ Claude
"""
output/ 폴더의 파일들을 old/ 로 정리. 두 모드 지원.

모드 1 (default — 그룹별 최신만 유지):
  같은 prefix 그룹 (파일명에서 _YYMMDD_HHMM.<ext> 부분 떼고 남은 base) 안에서
  최신 ts 가 아닌 것들을 old/ 로 이동.

모드 2 (--before <YYMMDD_HHMM>):
  그룹 무시. ts < BEFORE 인 모든 파일을 old/ 로 이동.

대상 파일명 패턴: <prefix>_<YYMMDD>_<HHMM>.<ext>
  예: column_mapping_sscompany_name4br_260518_2147.csv

패턴 매칭 안 되는 파일 (예: 3_26_4_table_real_json.json, *.py 등) 은 그대로 둠.

사용:
  python _move_stale_to_old.py                              # dry-run, 그룹별 최신 유지
  python _move_stale_to_old.py --apply                      # 실제 이동, 그룹별 최신 유지
  python _move_stale_to_old.py --before 260519_0000         # dry-run, 그 ts 이전 모두
  python _move_stale_to_old.py --before 260519_0000 --apply # 실제 이동, 그 ts 이전 모두
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# 정리 대상 폴더 — 이 스크립트가 있는 폴더 그대로
OUTPUT_DIR = Path(__file__).resolve().parent
OLD_DIR    = OUTPUT_DIR / "old"

# ts 패턴 — 파일명 끝의 _YYMMDD_HHMM.<ext>
# 예: column_mapping_sscompany_name4br_260518_2147.csv
#     → prefix=column_mapping_sscompany_name4br, ts=260518_2147, ext=csv
TS_REGEX = re.compile(r"^(?P<prefix>.+?)_(?P<ts>\d{6}_\d{4})\.(?P<ext>[A-Za-z0-9]+)$")

# 동명 파일 충돌 회피 suffix
DUP_SUFFIX = "__dup"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="output/ 의 stale 파일 → output/old/ 이동 (그룹별 최신만 유지 또는 ts 컷오프 기반)"
    )
    parser.add_argument("--apply", action="store_true",
                        help="실제 이동 실행. 미지정 시 dry-run.")
    parser.add_argument("--before", default="",
                        help="YYMMDD_HHMM 컷오프 — 그 ts 이전 (strict <) 모든 파일을 old/ 로 이동. "
                             "지정 시 그룹 모드 무시. 예: --before 260519_0000")
    args = parser.parse_args()

    before_ts = (args.before or "").strip()
    if before_ts and not re.match(r"^\d{6}_\d{4}$", before_ts):
        print(f"ERROR: --before 형식 잘못됨 (YYMMDD_HHMM 필요): {before_ts!r}")
        return 1

    # prefix+ext 별로 (ts, path) 리스트 모음
    groups: dict[tuple[str, str], list[tuple[str, Path]]] = defaultdict(list)
    untracked: list[Path] = []
    all_tracked: list[tuple[str, Path]] = []   # --before 모드용 평탄 list

    for p in OUTPUT_DIR.iterdir():
        if not p.is_file():
            continue
        m = TS_REGEX.match(p.name)
        if not m:
            untracked.append(p)
            continue
        ts = m.group("ts")
        key = (m.group("prefix"), m.group("ext"))
        groups[key].append((ts, p))
        all_tracked.append((ts, p))

    to_move: list[Path] = []
    keepers: list[tuple[str, Path]] = []

    if before_ts:
        # 모드 2 — ts < before_ts 인 모든 파일 이동
        for ts, path in all_tracked:
            if ts < before_ts:
                to_move.append(path)
            else:
                keepers.append((path.name, path))
        print(f"[scan] {OUTPUT_DIR}  (모드: --before {before_ts})")
        print(f"  추적 가능 파일: {len(all_tracked)} 개")
        print(f"  유지 (ts >= {before_ts}): {len(keepers)} 개")
        print(f"  이동 대상 (ts < {before_ts}): {len(to_move)} 개")
    else:
        # 모드 1 (default) — 그룹별 최신 1개만 유지
        for (prefix, ext), entries in groups.items():
            entries.sort(key=lambda x: x[0])
            latest_ts, latest_path = entries[-1]
            keepers.append((f"{prefix}.{ext}", latest_path))
            for ts, path in entries[:-1]:
                to_move.append(path)
        print(f"[scan] {OUTPUT_DIR}  (모드: 그룹별 최신 유지)")
        print(f"  추적 가능 그룹: {len(groups)} 개  (prefix+ext 단위)")
        print(f"  유지(최신): {len(keepers)} 개")
        print(f"  이동 대상(stale): {len(to_move)} 개")
    print(f"  패턴 매칭 안 됨 (그대로 둠): {len(untracked)} 개")
    print()

    if keepers:
        print("유지(각 그룹의 최신):")
        for label, p in sorted(keepers):
            print(f"  ✓ {p.name}")
        print()

    if to_move:
        print("이동 대상 (stale):")
        for p in sorted(to_move):
            print(f"  → {p.name}")
        print()

    if untracked:
        print("패턴 매칭 안 됨 (건드리지 않음):")
        for p in sorted(untracked):
            print(f"  · {p.name}")
        print()

    if not args.apply:
        print(f"dry-run — 실제 이동 안 함. --apply 박으면 {OLD_DIR.name}/ 로 이동.")
        return 0

    if not to_move:
        print("이동 대상 없음 — 종료.")
        return 0

    # 이동
    OLD_DIR.mkdir(exist_ok=True)
    moved = 0
    failed = 0
    for p in to_move:
        dest = OLD_DIR / p.name
        # 동명 파일 충돌 — suffix 붙여 회피
        if dest.exists():
            dest = OLD_DIR / f"{p.stem}{DUP_SUFFIX}{p.suffix}"
            i = 1
            while dest.exists():
                dest = OLD_DIR / f"{p.stem}{DUP_SUFFIX}{i}{p.suffix}"
                i += 1
        try:
            shutil.move(str(p), str(dest))
            moved += 1
        except Exception as e:
            print(f"  ⚠️ 이동 실패: {p.name} — {e}")
            failed += 1

    print(f"\n[done] 이동: {moved} 개, 실패: {failed} 개  → {OLD_DIR}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
