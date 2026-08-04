# prewarm_seg_ref_cache.py
# 2026-05-18  Jonghyun Park w/ Claude
# updated: 2026-08-03 19:20  — 깨진 import 제거, v2.4 헬퍼 4종 복사(self-contained)로 복구 (TODO_next_session 1-1)
# updated: 2026-08-04  — 사용자 설정 상수(AUTH_JSON_PATH / COMPANY_ID)를 헬퍼 블록에서 꺼내 파일 상단 '사용자가 바꿔야 하는 부분' 섹션으로 통합. 내부 상수(OUTPUT_DIR / 파싱 헬퍼)는 '내부 사용' 으로 분리.
# updated: 2026-08-04  — --cache 에 콤마로 여러 key 를 주면 각각 자기 파일로 처리 (기존엔 첫 key 만 쓰고 나머지 무시) + --all 로 정의된 그룹 전부 한 번에. 로그인은 1회만.
# updated: 2026-08-04  — fix: 캐시 항목의 name 이 빈 값이면 재fetch 하도록 _needs_fetch 보정 (v2.4 자동 fetch 가 남긴 빈 name 이 영영 안 채워지던 문제).
# updated: 2026-08-04  — fix: --cache 가 파일명만 바꾸고 id 목록은 안 골라서, 어떤 값을 줘도 전체 id 가 저장되던 문제 (global 캐시에 US 세그 혼입). SEGMENT_IDS_RAW → SEGMENT_IDS_BY_CACHE 그룹 dict + DEFAULT_SEGMENT_IDS_RAW 로 교체, --cache key 의 그룹만 fetch.
"""
segment-ref 캐시 미리 채우기 utility — input csv 없이 segment ID list 만으로
AA GET /segments/{id} 호출 → segment_ref_cache[_<name>].json 채움.

aa_create_segment_v2.4.py 의 dry-run 으로도 자동 채워지지만, input csv 없이도
미리 cache 준비하고 싶을 때 사용 (예: 새 캠페인 시작 전 reference segment 들 한 번에 받아두기).

사용 (--cache 값 = 캐시 파일명 suffix = SEGMENT_IDS_BY_CACHE 의 key):
  python prewarm_seg_ref_cache.py --all                      # 정의된 그룹 전부, 각자 파일로 (권장)
  python prewarm_seg_ref_cache.py --cache evar_global,evar_us     # 캠페인 global+us 2개
  python prewarm_seg_ref_cache.py --cache add_to_cart_global,add_to_cart_us # ATC 2개
  python prewarm_seg_ref_cache.py --cache add_to_cart_us     # 1개만
  python prewarm_seg_ref_cache.py                            # DEFAULT 그룹 → segment_ref_cache.json
  python prewarm_seg_ref_cache.py --all --refresh            # 원본 바뀐 걸 캐시에 반영
  python prewarm_seg_ref_cache.py --ids <id1>,<id2>          # 그룹에 없는 id 추가 (대상 1개일 때만)

콤마로 여러 key 를 주면 **각각 자기 파일**로 처리한다 (로그인은 1회만).

⚠ 기본은 **이미 캐시된 항목을 skip** 한다. 참조 원본 세그의 정의가 AA 에서 바뀌었다면
  skip 때문에 옛 정의가 계속 남는다 (v2.4 는 캐시 정의를 파생 세그에 inline 복사하므로
  조용히 옛 내용으로 세그가 만들어진다). 그때는 `--refresh` 로 다시 받을 것.
  --refresh 는 정의가 실제로 바뀐 항목을 실행 끝에 모아 보여주며, 그 세그를 참조하는
  파생 세그는 `aa_create_segment_v2.4.py --update` 로 다시 밀어야 반영된다.

⚠ 2026-08-04 이전에는 --cache 가 **파일명만** 바꿔서, 어떤 값을 주든 상단 목록의
  id 전체가 그 파일에 저장됐다 (global 캐시에 US 세그가 섞임). 이제 --cache key 에
  해당하는 그룹만 fetch 한다. key 가 SEGMENT_IDS_BY_CACHE 에 없으면 DEFAULT 를 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import aanalytics2 as api2

# segment GET 은 aa_segment_lookup 의 _lookup_segment 활용 (expansion: definition,name,description,owner,tags,reportSuiteName)
from aa_segment_lookup import _lookup_segment

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ───
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"

# ─── 대상 segment (캐시 파일별 그룹) ───
# key   = --cache 값. 그대로 segment_ref_cache_<key>.json 파일명이 된다.
# value = 그 캐시에 담을 segment id (한 줄에 하나, # 뒤는 주석, 빈 줄 무시).
# --cache 로 준 key 의 그룹만 fetch 하므로 global 캐시에 US 세그가 섞이지 않는다.
SEGMENT_IDS_BY_CACHE: dict[str, str] = {
    "evar_global": """
        YOUR_SEGMENT_ID_EVAR_GLOBAL  # [CAMPAIGN NAME] Campaign Main Page_Evar (Global)
    """,
    "evar_us": """
        YOUR_SEGMENT_ID_EVAR_US  # [CAMPAIGN NAME] US_Campaign Main Page_Evar (US)
    """,
    "add_to_cart_global": """
        YOUR_SEGMENT_ID_ATC_GLOBAL             # [Global] Add to Cart Visit
    """,
    "add_to_cart_us": """
        YOUR_SEGMENT_ID_ATC_US  # [US] Add to Cart Visit
    """,
}

# 위 dict 에 없는 --cache 값(또는 빈 값)일 때 쓸 기본 목록.
DEFAULT_SEGMENT_IDS_RAW = """
YOUR_SEGMENT_ID_EVAR_GLOBAL  # [CAMPAIGN NAME] Campaign Main Page_Evar (Global)
YOUR_SEGMENT_ID_EVAR_US  # [CAMPAIGN NAME] US_Campaign Main Page_Evar (US)
YOUR_SEGMENT_ID_ATC_GLOBAL             # [Global] Add to Cart Visit
YOUR_SEGMENT_ID_ATC_US  # [US] Add to Cart Visit
"""

# ─── 출력 ───
# 기본 cache 파일 suffix — SEGMENT_IDS_BY_CACHE 의 key 중 하나 권장. (CLI --cache 로 override)
CACHE_NAME = ""

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent


def _parse_ids(raw: str) -> list[str]:
    """id 목록 문자열 → id list. 빈 줄·`#` 주석 제거, 순서 보존."""
    out: list[str] = []
    for line in (raw or "").splitlines():
        sid = line.split("#")[0].strip()
        if sid:
            out.append(sid)
    return out


def _ids_for_cache(cache_name: str) -> tuple[list[str], str]:
    """캐시 key 하나 → (그 캐시에 담을 id list, 어느 그룹을 썼는지 라벨).

    main() 이 콤마를 이미 쪼개서 key 를 하나씩 넘긴다. 방어적으로 여기서도 첫 토큰만 본다.
    """
    first = (cache_name or "").split(",")[0].strip()
    raw = SEGMENT_IDS_BY_CACHE.get(first)
    if raw is None:
        label = "DEFAULT" if not first else f"DEFAULT (그룹 '{first}' 미정의)"
        return _parse_ids(DEFAULT_SEGMENT_IDS_RAW), label
    return _parse_ids(raw), f"그룹 '{first}'"


# ── self-contained 헬퍼 (2026-08-03) ──────────────────────────────────
# aa_create_segment_v2.4.py 는 파일명에 점이 있어 import 불가 → 필요한 헬퍼 4개를 복사해 둠.
# v2.4 쪽 구현이 바뀌면 여기도 같이 맞출 것 (_load_auth_headers / _resolve_cache_path(s) /
# _load_seg_ref_cache / _save_seg_ref_cache).

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
        raise RuntimeError(
            f"필수 헤더 누락: api_key={bool(api_key)}, "
            f"auth={bool(auth)}, gcid={bool(gcid)}"
        )
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def _resolve_cache_paths(name: str) -> list[Path]:
    """CACHE_NAME 또는 --cache 값 (콤마 분리 가능) → cache 파일 경로 list."""
    raw = (name or "").strip()
    if not raw:
        return [OUTPUT_DIR / "segment_ref_cache.json"]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return [OUTPUT_DIR / "segment_ref_cache.json"]
    return [OUTPUT_DIR / f"segment_ref_cache_{p}.json" for p in parts]


def _resolve_cache_path(name: str) -> Path:
    """첫 cache 파일 — save target (신규 fetch 결과 저장될 곳)."""
    return _resolve_cache_paths(name)[0]


def _load_seg_ref_cache(cache_path) -> dict[str, dict]:
    """캐시 파일 load. Path 면 한 파일, list[Path] 면 merge (앞 파일 우선)."""
    if isinstance(cache_path, list):
        merged: dict[str, dict] = {}
        for p in cache_path:
            if not p.exists():
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    one = json.load(f)
                for k, v in one.items():
                    if k not in merged:
                        merged[k] = v
            except Exception:
                continue
        return merged
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_seg_ref_cache(cache_path: Path, cache: dict[str, dict]) -> None:
    """캐시 파일 저장 (사용자 OneDrive 폴더, 로컬 전용)."""
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [seg-ref cache] 저장 실패 (무시): {e}")
# ── self-contained 헬퍼 끝 ────────────────────────────────────────────


def _needs_fetch(entry) -> bool:
    """이 캐시 항목을 다시 받아야 하나?

    새 형식 = {"container": {...}, "name": "..."} 이고 name 이 **비어있지 않아야** 한다.
    v2.4 자동 fetch 는 name 을 빈 값으로 남기므로, 키 존재만 보면 영영 갱신이 안 된다.
    """
    if not (isinstance(entry, dict) and "container" in entry):
        return True
    return not (entry.get("name") or "").strip()


def _prewarm_one(cache_key: str, extra_ids: list[str], auth: list,
                 refresh: bool = False) -> tuple[int, list[str], list[str]]:
    """캐시 파일 하나를 prewarm. 반환 (fetch 건수, 실패 id list, 정의가 바뀐 id list).

    auth 는 [headers, gcid] 를 담는 2칸 리스트 — 여러 캐시를 돌 때 로그인 1회만 하도록
    호출자가 들고 다니며 lazy 로 채운다.
    refresh=True 면 이미 캐시된 항목도 다시 받아 원본 변경분을 반영한다.
    """
    group_ids, group_label = _ids_for_cache(cache_key)
    all_ids = list(dict.fromkeys(group_ids + extra_ids))   # 순서 보존 dedup
    cache_path = _resolve_cache_path(cache_key)

    print(f"[cache] {cache_path.name}")
    print(f"[ids] {group_label} — {len(group_ids)} 개"
          + (f" (+ --ids {len(extra_ids)} 개)" if extra_ids else ""))

    if not all_ids:
        print("  ⚠ 대상 id 없음 — 건너뜀\n")
        return 0, [], []

    cache = _load_seg_ref_cache(cache_path)
    print(f"  기존 cache: {len(cache)} 항목")

    targets = [sid for sid in all_ids
               if refresh or sid not in cache or _needs_fetch(cache[sid])]
    if not targets:
        print("  ✓ 모든 id 이미 캐시됨 — fetch 안 함 (원본 변경 반영은 --refresh)\n")
        return 0, [], []
    print(f"  {'전체 재조회' if refresh else '미캐시'} {len(targets)} 개 → AA GET 진행")

    if auth[0] is None:
        print("  Authenticating ...")
        auth[0], auth[1] = _load_auth_headers()
    headers, gcid = auth[0], auth[1]

    fetched = 0
    failed: list[str] = []
    changed: list[str] = []
    for i, sid in enumerate(targets, 1):
        print(f"  [{i}/{len(targets)}] {sid} ...", end=" ")
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

        # 기존 항목과 정의가 달라졌는지 — 달라졌으면 파생 세그도 --update 해야 한다
        prev = cache.get(sid)
        prev_container = prev.get("container") if isinstance(prev, dict) else None
        is_changed = prev_container is not None and prev_container != container

        # 새 cache 형식 — container + metadata (name lookup 등에 활용)
        cache[sid] = {
            "container": container,
            "name": seg_data.get("name", ""),
            "description": seg_data.get("description", ""),
            "rsid": seg_data.get("rsid", ""),
        }
        fetched += 1
        name = (seg_data.get("name") or "")[:60]
        if is_changed:
            changed.append(f"{sid}  {name}")
            print(f"OK  {name}   ⚠ 정의 변경됨")
        else:
            print(f"OK  {name}")

    _save_seg_ref_cache(cache_path, cache)
    print(f"  → fetch {fetched} / 변경 {len(changed)} / 실패 {len(failed)} / "
          f"총 {len(cache)} 항목  ({cache_path.name})\n")
    return fetched, failed, changed


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="segment-ref 캐시 prewarm — AA GET 으로 segment container 받아 캐시에 저장")
    parser.add_argument("--cache", default=CACHE_NAME,
                        help=f"캐시 파일 suffix (segment_ref_cache_<name>.json) 이자 SEGMENT_IDS_BY_CACHE 의 key. "
                             f"콤마로 여러 개를 주면 각각 자기 파일로 처리. "
                             f"빈 값=기본 segment_ref_cache.json + DEFAULT 그룹. default={CACHE_NAME!r}")
    parser.add_argument("--all", action="store_true",
                        help="SEGMENT_IDS_BY_CACHE 의 모든 그룹을 각자 파일로 한 번에 prewarm")
    parser.add_argument("--refresh", action="store_true",
                        help="이미 캐시된 항목도 다시 받아 원본 변경분을 반영 (기본은 캐시 있으면 skip). "
                             "정의가 바뀐 항목을 실행 끝에 표시한다")
    parser.add_argument("--ids", default="",
                        help="추가 segment id 들 (콤마 구분). 캐시 대상이 1개일 때만 사용 가능")
    args = parser.parse_args()

    if args.all:
        targets = list(SEGMENT_IDS_BY_CACHE)
    else:
        targets = [p.strip() for p in (args.cache or "").split(",")]
        if not any(targets):
            targets = [""]          # 빈 값 → DEFAULT 그룹 1회

    cli_ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    if cli_ids and len(targets) > 1:
        print(f"ERROR: --ids 는 캐시 대상이 1개일 때만 쓸 수 있습니다 "
              f"(지금 {len(targets)}개: {targets}). 어느 캐시에 넣을지 모호합니다.")
        return 1

    print(f"[대상] {len(targets)} 개 캐시 — {targets}"
          + ("  (--refresh: 캐시된 항목도 재조회)" if args.refresh else "") + "\n")

    total_fetched = 0
    all_failed: list[str] = []
    all_changed: list[str] = []
    auth: list = [None, None]       # [headers, gcid] — 첫 fetch 때 1회만 로그인
    for key in targets:
        fetched, failed, changed = _prewarm_one(key, cli_ids, auth, refresh=args.refresh)
        total_fetched += fetched
        all_failed += failed
        all_changed += changed

    print(f"전체 완료 — 캐시 {len(targets)} 개 / fetch {total_fetched} / "
          f"변경 {len(all_changed)} / 실패 {len(all_failed)}")
    if all_changed:
        print("\n  ⚠ 원본 정의가 바뀐 참조 세그:")
        for c in all_changed:
            print(f"     {c}")
        print("     → 이 세그를 참조하는 파생 세그는 정의가 inline 복사본이라 아직 옛 내용입니다.")
        print("       aa_create_segment_v2.4.py --update 로 다시 밀어줘야 반영됩니다.")
    if all_failed:
        print(f"  실패 id: {all_failed}")
    return 0 if not all_failed else 1


if __name__ == "__main__":
    sys.exit(main())
