# cleanup_recent_json.py
# 2026-05-08  Jonghyun Park w/ Claude
"""
최근 N분 내에 생성/수정된 JSON 파일을 일괄 삭제.

기본 대상: 같은 폴더의 `json/` 하위(모든 서브폴더 재귀).
기본 시간창: 60분.
파일명에 EXCLUDE_KEYWORDS 안의 키워드가 들어 있으면 삭제 대상에서 제외 (대소문자 무시).

extract_panel_tables_json_v2.0.py 로 추출한 JSON 결과물을 재실행 전에 정리할 때 사용.

사용:
  python cleanup_recent_json.py                  # 60분 내 .json 보여주고 확인 후 삭제
  python cleanup_recent_json.py --minutes 30     # 30분 내로
  python cleanup_recent_json.py --dry-run        # 미리보기만
  python cleanup_recent_json.py --yes            # 확인 프롬프트 스킵
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ════════════ 사용자가 바꿔야 하는 부분 ════════════════════════════
# 스캔 대상 폴더 (이 하위 모든 서브폴더 재귀)
TARGET_DIR = Path(__file__).resolve().parent / "json"

# 기본 시간창 (분)
DEFAULT_MINUTES = 60

# 삭제에서 제외할 키워드 — 파일명(서브폴더 경로 제외, basename)에 이 중 하나라도
# 부분 일치하면 그 파일은 스킵 (대소문자 무시).
# 한 줄에 한 키워드씩. 빈 줄·`#` 시작 줄·앞뒤 공백 무시. 추가/삭제 자유롭게.
EXCLUDE_KEYWORDS = """
test_real
"""


# ════════════ 내부 사용 ════════════════════════════════════════════
_EXCLUDE_KEYWORDS = [
    kw.strip().lower()
    for kw in EXCLUDE_KEYWORDS.splitlines()
    if kw.strip() and not kw.strip().startswith("#")
]


def main() -> int:
    parser = argparse.ArgumentParser(description="최근 N분 내 .json 파일 일괄 삭제")
    parser.add_argument("--minutes", type=int, default=DEFAULT_MINUTES,
                        help=f"기준 시간창(분). 기본 {DEFAULT_MINUTES}")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="삭제 안 하고 대상 목록만 출력")
    parser.add_argument("--yes", dest="auto_yes", action="store_true",
                        help="확인 프롬프트 스킵 (자동 yes)")
    args = parser.parse_args()

    if not TARGET_DIR.exists():
        print(f"❌ 대상 폴더 없음: {TARGET_DIR}")
        return 1

    cutoff = datetime.now() - timedelta(minutes=args.minutes)
    cutoff_ts = cutoff.timestamp()

    print(f"스캔 폴더 : {TARGET_DIR}")
    print(f"기준      : {args.minutes}분 이내 수정된 .json (cutoff: {cutoff:%Y-%m-%d %H:%M:%S})")
    if _EXCLUDE_KEYWORDS:
        print(f"제외 키워드: {_EXCLUDE_KEYWORDS} (파일명 부분 일치, 대소문자 무시)")
    print()

    targets: list[Path] = []
    excluded: list[Path] = []
    for p in TARGET_DIR.rglob("*.json"):
        try:
            if p.stat().st_mtime < cutoff_ts:
                continue
            if _EXCLUDE_KEYWORDS:
                name_lower = p.name.lower()
                if any(kw in name_lower for kw in _EXCLUDE_KEYWORDS):
                    excluded.append(p)
                    continue
            targets.append(p)
        except OSError:
            pass

    if excluded:
        print(f"[excluded] 키워드 매치로 보존: {len(excluded)}개")
        for f in sorted(excluded)[:5]:
            try:
                rel = f.relative_to(TARGET_DIR.parent)
            except ValueError:
                rel = f.name
            print(f"    - {rel}")
        if len(excluded) > 5:
            print(f"    ... (그 외 {len(excluded)-5}개)")
        print()

    if not targets:
        print("삭제 대상 없음.")
        return 0

    # 폴더별 그룹화해서 미리보기
    by_dir: dict[Path, list[Path]] = {}
    for t in targets:
        by_dir.setdefault(t.parent, []).append(t)
    for d in sorted(by_dir):
        files = by_dir[d]
        print(f"  [{d.relative_to(TARGET_DIR.parent) if TARGET_DIR.parent in d.parents or d == TARGET_DIR else d}]  {len(files)}개")
        for f in sorted(files)[:3]:
            print(f"    - {f.name}")
        if len(files) > 3:
            print(f"    ... (그 외 {len(files)-3}개)")

    print(f"\n총 {len(targets)}개 파일")

    if args.dry_run:
        print("[dry-run] 실제 삭제 안 함.")
        return 0

    if not args.auto_yes:
        ans = input("\n정말 삭제? (y/N): ").strip().lower()
        if ans != "y":
            print("취소됨.")
            return 0

    deleted = 0
    for t in targets:
        try:
            t.unlink()
            deleted += 1
        except OSError as e:
            print(f"  실패: {t}  ({e})")

    print(f"\n[OK] {deleted}/{len(targets)}개 삭제 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
