# project_share_owner.py
# 2026-08-31  Jonghyun Park w/ Claude
"""
Adobe Analytics **프로젝트(Workspace)** 를 팀원에게 일괄 share 하거나, owner(주인) 를 이관하는 도구.
MODE 상수 하나로 동작을 전환한다.

  MODE = "share"  → 팀원들에게 공유만 (POST /componentmetadata/shares)
  MODE = "owner"  → owner 이관만      (PUT /projects/{id} 의 owner.id)
  MODE = "both"   → owner 이관 후 공유 (이관 뒤에도 본인·팀이 조회 가능하도록)

세그먼트용 자매도구:
  · segment_share/add_segment_shares.py            — 키워드 매칭 segment 일괄 share
  · panel_copy_swap_owner/change_segment_owner.py  — segment owner 이관

동작:
  1) 대상 프로젝트 선정 — TARGET_PROJECT_IDS 우선, 비어있으면 NAME_KEYWORDS 로 전체 목록에서 AND 매칭
  2) 매칭 결과를 CSV + 콘솔(첫 N개)로 보여줌 (안전장치 1)
  3) 변경 미리보기 출력 → --apply 면 키보드 confirm (안전장치 2)
  4) owner: GET 응답 JSON 을 파일 백업 후 owner 만 갈아끼워 통째로 PUT → read-back 검증
     share: 아직 share 안 된 user id 만 단건 POST
  5) 결과 CSV 출력

주의:
  · owner 변경은 보통 **admin 권한 필요**. 권한 없으면 조용히 무시될 수 있어 read-back 으로 검증한다.
  · share 는 PUT 으로 보내면 silently drop 될 수 있어 /componentmetadata/shares 로만 처리한다.
  · **owner PUT 은 definition(워크스페이스 내용) 을 반드시 함께 실어 보내야 한다.**
    그래서 필드를 새로 조립하지 않고 GET 응답을 그대로 PUT 한다 (PUT_STRIP_KEYS 만 제거).
  · componentType="project" 는 2026-08-31 실측 확인됨 (share POST 200, owner PUT 도 통과).
    그래도 실패하면 응답 본문을 그대로 출력하니 그 메시지를 보고 판단할 것.
  · 기본 dry-run. --apply + confirm 시에만 실제 쓰기.

사용:
  python project_share_owner.py            # dry-run — 대상·미리보기만
  python project_share_owner.py --apply    # 확인 후 실제 적용
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ───
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID     = "your_aa_company_id"

# ─── 동작 선택 ───
# "share" = 팀원들에게 공유만
# "owner" = owner 이관만
# "both"  = owner 이관 후 공유까지
MODE = "share"

# ─── 대상 선정 (TARGET_PROJECT_IDS 가 있으면 그게 우선, 없으면 NAME_KEYWORDS 검색) ───
# 한 줄에 project id 하나. 빈 줄 무시, '#' 로 시작하면 주석 처리(해당 id 제외).
# project id = AA URL 의 /workspace/projects/<id> 부분 (24-hex)
TARGET_PROJECT_IDS_RAW = """

"""

# 이름 또는 설명에 아래 substring 이 **모두**(AND) 들어간 프로젝트 매칭 (case-insensitive).
# TARGET_PROJECT_IDS 가 비어있을 때만 사용. 둘 다 비면 abort.
NAME_KEYWORDS: list[str] = [
    # "[campaign name]",
    # "contents",
]

# ─── owner 이관 (MODE 에 owner 포함일 때만 필요) ───
# 새 owner 의 numeric loginId. `utils/find_user_id.py` 로 조회.
NEW_OWNER_ID: int | None = None

# ─── share (MODE 에 share 포함일 때만 필요) ───
# share 를 추가할 numeric loginId 리스트. `utils/find_user_id.py` 로 조회해 채울 것.
# 본인도 포함 — owner 본인만으론 조회가 막히는 경우가 있어 최소 본인 share 1건은 박아둔다.
# 비어 있으면 (MODE 에 share 포함일 때) 실행 시 안내 후 종료.
SHARE_USER_IDS: list[int] = [
    # 0,   # <numeric loginId>   # 본인
    # 0,   # <numeric loginId>   # 팀원
]

# ─── 출력 / 동작 옵션 ───
PRINT_FIRST_N     = 5      # 콘솔에 보여줄 매칭 프로젝트 수 (전체는 CSV)
BACKUP_BEFORE_PUT = True   # owner PUT 전 GET 응답 JSON 을 파일로 저장

# owner PUT 시 body 에서 제거할 read-only / expansion 전용 키.
# (GET 응답을 그대로 PUT 하되 서버가 거부하는 필드만 덜어낸다)
PUT_STRIP_KEYS = [
    "modified", "ownerFullName", "shares", "externalReferences",
    "accessLevel", "companyId", "lastRecordedAccess", "reportSuiteName",
]

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

API_BASE   = "https://analytics.adobe.io/api"
SCRIPT_DIR = Path(__file__).parent

PAGE_LIMIT = 1000   # AA API 한 페이지당 최대
MAX_PAGES  = 100    # 안전장치

VALID_MODES = ("share", "owner", "both")

# componentmetadata/shares 의 componentType — 프로젝트는 "project" (2026-08-31 실측 확인).
# 그래도 400 이 나면 이 값을 의심할 것 (에러 본문을 그대로 출력한다).
SHARE_COMPONENT_TYPE = "project"

TARGET_PROJECT_IDS: list[str] = [
    line.strip()
    for line in TARGET_PROJECT_IDS_RAW.splitlines()
    if line.strip() and not line.strip().startswith("#")
]


def load_auth_headers() -> tuple[dict, str]:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h = dict(ags.header) if isinstance(getattr(ags, "header", None), dict) else {}
    hl = {k.lower(): v for k, v in h.items()}
    api_key, auth, gcid = hl.get("x-api-key"), hl.get("authorization"), hl.get("x-proxy-global-company-id")
    if not (api_key and auth and gcid):
        raise RuntimeError("필수 헤더 누락 (api_key/authorization/x-proxy-global-company-id)")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def get_project(H: dict, G: str, pid: str, expansion: str) -> dict:
    r = requests.get(f"{API_BASE}/{G}/projects/{pid}", headers=H,
                     params={"expansion": expansion}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"GET project {pid} 실패: {r.status_code} {r.reason} — {r.text[:500]}")
    return r.json()


def list_projects(H: dict, G: str) -> list[dict]:
    """`/projects` 전체 GET (includeType=all — 본인 + 공유받은 것 모두).

    /segments 와 달리 server-side name 필터를 쓰지 않는다
    (프로젝트 수는 세그먼트와 달리 적어 전량 페이징이 부담 없음)."""
    out: list[dict] = []
    for page in range(MAX_PAGES):
        params = {
            "expansion": "shares,ownerFullName,tags,description,modified",
            "includeType": "all",
            "limit": PAGE_LIMIT,
            "page": page,
        }
        r = requests.get(f"{API_BASE}/{G}/projects", headers=H, params=params, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"GET /projects page {page} 실패: {r.status_code} {r.reason} — {r.text[:500]}")
        data = r.json()
        if isinstance(data, dict):
            rows = data.get("content") or []
            is_last = bool(data.get("lastPage"))
            total = data.get("totalElements")
        else:
            rows = data
            is_last = len(rows) < PAGE_LIMIT
            total = None
        if not rows:
            break
        out.extend(rows)
        if total is not None and page == 0:
            print(f"  totalElements: {total} (필터 전 전체)")
        print(f"  page {page}: +{len(rows)} (누적 {len(out)})")
        if is_last or len(rows) < PAGE_LIMIT:
            break
    return out


def match_projects(projects: list[dict], keywords: list[str]) -> list[dict]:
    """name 또는 description 에 keywords 의 모든 substring 이 다 포함된 것만 (AND, case-insensitive)."""
    kws = [k.lower() for k in keywords]
    out = []
    for p in projects:
        text = ((p.get("name") or "") + " " + (p.get("description") or "")).lower()
        if all(kw in text for kw in kws):
            out.append(p)
    return out


def normalize_share_id(share) -> int | None:
    """share dict / int / str 어느 형태라도 numeric loginId 추출."""
    if isinstance(share, dict):
        sid = (share.get("shareToId") or share.get("id")
               or share.get("loginId") or share.get("userId"))
    else:
        sid = share
    try:
        return int(sid) if sid is not None else None
    except (ValueError, TypeError):
        return None


def existing_share_ids(project: dict) -> set:
    ids = {normalize_share_id(sh) for sh in (project.get("shares") or [])}
    ids.discard(None)
    return ids


def post_share(H: dict, G: str, component_id: str, share_to_id: int) -> dict:
    """`POST /componentmetadata/shares` — 단건 share 추가.

    AA 의 component sharing 은 이 endpoint 로만 실제 적용된다 (PUT 은 silently drop)."""
    body = {
        "shareToId":     share_to_id,
        "shareToType":   "user",
        "componentType": SHARE_COMPONENT_TYPE,
        "componentId":   component_id,
    }
    r = requests.post(f"{API_BASE}/{G}/componentmetadata/shares", headers=H, json=body, timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"POST share {component_id}+{share_to_id} 실패: "
                           f"{r.status_code} {r.reason} — {r.text[:500]}")
    return r.json()


def put_project_owner(H: dict, G: str, proj_full: dict, new_owner_id: int) -> dict:
    """GET 응답을 그대로 PUT 하되 owner 만 교체하고 read-only 키만 제거.

    ⚠ definition 을 반드시 실어 보내야 워크스페이스 내용이 보존된다."""
    pid = proj_full["id"]
    body = copy.deepcopy(proj_full)
    for k in PUT_STRIP_KEYS:
        body.pop(k, None)
    body["owner"] = {"id": new_owner_id}
    r = requests.put(f"{API_BASE}/{G}/projects/{pid}", headers=H,
                     data=json.dumps(body), params={"expansion": "name,owner"}, timeout=120)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"PUT project owner {pid} 실패: {r.status_code} {r.reason} — {r.text[:500]}")
    return r.json()


def count_definition_nodes(definition) -> tuple:
    """definition 안의 panel / reportlet 개수 — PUT 전후 워크스페이스 보존 검증용."""
    n_panel = n_reportlet = 0

    def walk(node):
        nonlocal n_panel, n_reportlet
        if isinstance(node, dict):
            t = str(node.get("type") or "")
            if "Panel" in t:
                n_panel += 1
            if "Reportlet" in t:
                n_reportlet += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(definition)
    return n_panel, n_reportlet


def main() -> int:
    ap = argparse.ArgumentParser(description="AA 프로젝트 일괄 share / owner 이관")
    ap.add_argument("--apply", action="store_true", help="실제 적용 (기본은 dry-run)")
    args = ap.parse_args()
    apply = args.apply

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    now = datetime.now()
    ts = now.strftime("%y%m%d_%H%M")
    print(f"[{now:%Y-%m-%d %H:%M:%S}] project_share_owner  ({'APPLY' if apply else 'DRY-RUN'})")

    # ─── 설정 검증 ───
    if MODE not in VALID_MODES:
        print(f"❌ MODE={MODE!r} 는 올바르지 않음 — {VALID_MODES} 중 하나여야 합니다.")
        return 1
    do_owner = MODE in ("owner", "both")
    do_share = MODE in ("share", "both")

    if do_owner and NEW_OWNER_ID is None:
        print("❌ NEW_OWNER_ID 미지정 — 새 owner 의 numeric loginId 를 상단에 채우세요 "
              "(`utils/find_user_id.py` 로 조회).")
        return 1
    if do_share and not SHARE_USER_IDS:
        print("❌ SHARE_USER_IDS 비어있음 — share 대상 loginId 를 상단에 채우세요.")
        return 1
    if not TARGET_PROJECT_IDS and not NAME_KEYWORDS:
        print("❌ TARGET_PROJECT_IDS 와 NAME_KEYWORDS 둘 다 비어있음 — 대상을 지정하세요.")
        return 1

    print(f"  MODE                : {MODE}  (owner={do_owner} / share={do_share})")
    print(f"  TARGET_PROJECT_IDS  : {len(TARGET_PROJECT_IDS)}개")
    print(f"  NAME_KEYWORDS       : {NAME_KEYWORDS}  (IDS 비어있을 때만 사용, AND 매칭)")
    if do_owner:
        print(f"  NEW_OWNER_ID        : {NEW_OWNER_ID}")
    if do_share:
        print(f"  SHARE_USER_IDS      : {SHARE_USER_IDS}")

    H, G = load_auth_headers()

    # ─── 대상 목록 ───
    matches: list[dict] = []
    if TARGET_PROJECT_IDS:
        print(f"\nGET /projects/{{id}} — 지정 id {len(TARGET_PROJECT_IDS)}개 조회 ...")
        for pid in TARGET_PROJECT_IDS:
            try:
                matches.append(get_project(H, G, pid, "shares,ownerFullName,tags,description,modified"))
            except Exception as e:
                print(f"  ✗ {pid}  ERROR: {e}")
    else:
        print("\nGET /projects (includeType=all) ...")
        all_projects = list_projects(H, G)
        matches = match_projects(all_projects, NAME_KEYWORDS)
        print(f"  client-side AND 매칭 ({NAME_KEYWORDS}): {len(matches)}개")

    if not matches:
        print("\n❌ 대상 프로젝트 없음 — abort.")
        return 1

    # ─── 매칭 CSV (콘솔 잘림 대비 항상 저장) ───
    csv_path = SCRIPT_DIR / f"projects_matched_{ts}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ProjectId", "Name", "OwnerId", "OwnerName", "Modified",
                    "ShareCount", "ShareIds", "Description"])
        for p in matches:
            own = p.get("owner") or {}
            share_ids = sorted(existing_share_ids(p))
            w.writerow([
                p.get("id", ""), p.get("name", ""),
                own.get("id", ""), own.get("name", "") or p.get("ownerFullName", ""),
                p.get("modified", ""), len(share_ids),
                "|".join(str(i) for i in share_ids), p.get("description", ""),
            ])
    print(f"\n📝 매칭 결과 CSV: {csv_path.name} ({len(matches)}행)")

    print(f"\n--- 매칭된 프로젝트 (콘솔: 첫 {PRINT_FIRST_N}개 / 전체는 CSV) ---")
    print(f"{'#':>4}  {'project id':26}  {'owner_id':10}  {'shares':>6}  name")
    print("-" * 110)
    for i, p in enumerate(matches[:PRINT_FIRST_N], 1):
        own = p.get("owner") or {}
        print(f"{i:>4}  {p.get('id','?'):26}  {str(own.get('id') or '?'):10}  "
              f"{len(existing_share_ids(p)):>6}  {(p.get('name') or '(no name)')[:50]}")
    if len(matches) > PRINT_FIRST_N:
        print(f"  ... +{len(matches) - PRINT_FIRST_N}개 (CSV 참고)")

    # ─── 변경 미리보기 ───
    print("\n--- 변경 미리보기 ---")
    plans: list = []
    n_owner_change = 0
    for p in matches:
        own_id = (p.get("owner") or {}).get("id")
        have = existing_share_ids(p)
        to_add = [uid for uid in SHARE_USER_IDS if uid not in have] if do_share else []
        plans.append((p, to_add))
        parts = []
        if do_owner:
            same = str(own_id) == str(NEW_OWNER_ID)
            parts.append(f"owner {own_id}→{NEW_OWNER_ID}" + (" (이미 동일)" if same else ""))
            if not same:
                n_owner_change += 1
        if do_share:
            parts.append(f"share 추가 {to_add}" if to_add else "share 이미 모두 됨")
        print(f"  · {p.get('id','?'):26}  {(p.get('name') or '')[:44]:44}  {' / '.join(parts)}")

    n_share_change = sum(1 for _, add in plans if add)
    print("\n--- 합계 ---")
    print(f"  대상 프로젝트   : {len(matches)}개")
    if do_owner:
        print(f"  owner 변경 대상 : {n_owner_change}개")
    if do_share:
        print(f"  share 추가 대상 : {n_share_change}개")

    nothing_to_do = ((not do_owner) or n_owner_change == 0) and ((not do_share) or n_share_change == 0)
    if nothing_to_do:
        print("\nℹ️ 변경 대상 없음 — 쓰기 생략.")
        return 0

    if not apply:
        print(f"\nℹ️ DRY-RUN — 실제 쓰기 안 함. 적용: python {Path(__file__).name} --apply")
        return 0

    # ─── 두 번째 안전장치 ───
    print(f"\n⚠️ 위 {len(matches)}개 프로젝트에 MODE={MODE!r} 를 적용합니다. 진행하시겠습니까?")
    ans = input("   진행하려면 'y' 또는 'yes' 입력: ").strip().lower()
    if ans not in ("y", "yes"):
        print("   취소됨.")
        return 1

    # ─── 실행 ───
    results = []
    n_fail = 0
    for p, to_add in plans:
        pid = p.get("id")
        name = p.get("name") or "(no name)"
        owner_before = (p.get("owner") or {}).get("id")
        owner_after = owner_before
        owner_ok = ""
        n_share = 0
        err = ""

        # 1) owner 이관 — share 보다 먼저 (이관 후에도 조회 가능하도록 share 를 나중에 붙인다)
        if do_owner and str(owner_before) != str(NEW_OWNER_ID):
            try:
                full = get_project(H, G, pid, "definition,tags,description,ownerFullName")
                before_nodes = count_definition_nodes(full.get("definition"))
                if BACKUP_BEFORE_PUT:
                    bpath = SCRIPT_DIR / f"project_backup_{pid}_{ts}.json"
                    bpath.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
                put_project_owner(H, G, full, NEW_OWNER_ID)
                back = get_project(H, G, pid, "owner,definition")
                owner_after = (back.get("owner") or {}).get("id")
                owner_ok = str(owner_after) == str(NEW_OWNER_ID)
                after_nodes = count_definition_nodes(back.get("definition"))
                if after_nodes != before_nodes:
                    err += (f"⚠️definition 노드 변화 panel/reportlet {before_nodes}→{after_nodes} "
                            "(백업 JSON 확인) ")
                    n_fail += 1
                if not owner_ok:
                    err += f"owner 미변경(현재 {owner_after}) "
                    n_fail += 1
            except Exception as e:
                err += f"owner ERROR: {e} "
                owner_ok = False
                n_fail += 1
        elif do_owner:
            owner_ok = True   # 이미 동일

        # 2) share
        if do_share:
            for uid in to_add:
                try:
                    post_share(H, G, pid, uid)
                    n_share += 1
                except Exception as e:
                    err += f"share {uid} ERROR: {e} "
                    n_fail += 1

        flag = "✓" if not err else "△"
        print(f"  {flag} {pid:26}  {name[:44]:44}  owner={owner_after}  +{n_share} share  {err}")
        results.append({
            "project_id": pid, "name": name,
            "owner_before": owner_before, "owner_after": owner_after,
            "owner_ok": owner_ok, "shares_added": n_share, "error": err.strip(),
        })

    out_csv = SCRIPT_DIR / f"project_share_owner_{ts}.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["project_id", "name", "owner_before", "owner_after",
                    "owner_ok", "shares_added", "error"])
        for r in results:
            w.writerow([r["project_id"], r["name"], r["owner_before"], r["owner_after"],
                        r["owner_ok"], r["shares_added"], r["error"]])

    print(f"\n--- 완료 --- 실패 {n_fail}건  CSV: {out_csv.name}")
    if n_fail:
        print("  ⚠️ owner 미변경은 admin 권한 문제일 수 있음. share 가 400 이면 "
              f"componentType={SHARE_COMPONENT_TYPE!r} 를 의심 (에러 본문 확인).")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
