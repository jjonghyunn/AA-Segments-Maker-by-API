# aa_segment_lookup_by_owner.py
# 2026-08-21  Jonghyun Park w/ Claude
# aa_segment_lookup.py 의 사본 — **owner 기준 검색(--owner)** 추가 버전.
#   · --owner 로 loginId / 이메일 / 이름(부분일치) 지정 → 그 사람이 소유한 세그만 필터
#   · --owner 단독 사용 가능 (키워드 없이 owner 만으로 검색). --search 와 같이 주면 AND
#   · AA /segments 는 ownerId 서버 필터를 안 받음 → 후보를 받아온 뒤 **클라이언트측 필터**
#     (--modified-after 와 같은 구조). owner 단독 스캔은 전량 페이징이라 느림 → --rsid 병행 권장
#   · owner 단독 스캔은 page 0 으로 totalPages 확인 후 나머지를 ThreadPoolExecutor 로 병렬 fetch
#   · 출력 파일 prefix 를 segment_lookup_owner_ 로 분리 (원본 결과와 안 섞이게)
# updated: 2026-08-25       — --with-projects: 세그별 **사용 프로젝트** 컬럼 3개 추가
#                            (project_count / project_ids / project_names, ';' 구분).
#                            AA 2.0 에는 segment → projects 역방향 조회 API 가 **없다**
#                            (componentmetadata/* 는 share 전용, usage/usedIn 류는 미존재)
#                            → /projects 를 훑어 project→segment 를 모은 뒤 뒤집는 수밖에 없다.
#                            스캔 대상 프로젝트는 **세그 owner 와 무관** — 상단 PROJECTS_OWNER_DEFAULT
#                            (본인 loginId 를 넣으면 본인 소유 프로젝트만), --projects-owner 로 지정,
#                            --all-projects 로 전체, --refresh-projects 로 캐시 무시.
#                            플래그 안 켜면 기존 동작 그대로.
#                            ⚠ '본인'을 GET /users/me 로 자동 판별하면 안 된다 — OAuth S2S 는
#                            테크니컬 계정(@techacct.adobe.com)을 돌려줘서 프로젝트 0건을 스캔하고
#                            전부 project_count=0 으로 조용히 틀린 답이 나온다.
#                            ⚠ /projects 응답은 /segments 와 달리 {content,lastPage} envelope 가
#                            아니라 **bare 배열**로 온다 — 두 모양 다 받도록 정규화해 뒀다.
#                            ⚠ expansion=definition 은 **목록 호출에선 안 먹는다** → 프로젝트마다
#                            개별 GET 이 필요해 대상이 많으면 오래 걸린다(확인 프롬프트 있음).
# ── 아래는 원본 aa_segment_lookup.py 에서 이어받은 변경 이력 ──
# updated: 2026-06-15       — --search 결과에 날짜 필터 추가: --modified-after / --modified-before (YYYY-MM-DD).
#                            (2026-08-21 정정: 생성일은 expansion=createdDate 로 제공된다. 아래 참조)
#                            after 만=이후, before 만=이전, 둘 다=두 날짜 사이(both inclusive). CSV 에 modified 컬럼 추가.
# updated: 2026-06-05  v1.2 — --search 전 키워드를 연속 substring AND (첫 키워드 토큰화 버그 수정) + SEARCH_RESULT_LIMIT 등 상단 상수화 + 초과 시 경고
# updated: 2026-06-05  v1.1 — owner_email 컬럼 추가 + owner 이름/이메일을 GET /users 직접 조회로 보강 (외부 user-id CSV 의존 제거)
# updated: 2026-05-26       — sequence 처리: wrap 분기 제거, 모든 sequence/prefix/suffix 에 [sequence-after/before/all] 라벨 + scope 감쌈
# updated: 2026-05-22       — 결과 CSV/DSL 출력 위치를 같은 폴더의 lookup/ 하위로 분리 (LOOKUP_DIR)
# updated: 2026-05-18       — --search 키워드 nargs='+' 로 AND 매칭 (공백 구분), 사용법 주석 보완
# updated: 2026-05-15 13:00  — owner_name 을 외부 user-id CSV 에서 보강 (v1.1 에서 GET /users 로 대체)
"""
세그먼트 ID 리스트 / 이름 키워드 / **owner** → 기본 정보 CSV + DSL 구조 파일(.dsl) 출력.

.dsl 파일은 aa_create_segment_v2.py의 입력으로 바로 재사용 가능.

사용법:
  # ID 직접 지정 (여러 개 공백 구분)
  python segment_lookup.py sXXXXXXXXX_abc123 sXXXXXXXXX_def456

  # ID list 파일에서 읽기 (한 줄에 하나)
  python segment_lookup.py --from-file segment_ids.txt

  # 이름 키워드 검색 (1 개)
  python segment_lookup.py --search "campaign"
  python segment_lookup.py --search "campaign" --rsid sscompany_name4mstglobal

  # 이름 키워드 AND 검색 (여러 개 — 공백 구분, 각 quote 로 감쌈)
  python segment_lookup.py --search "[us] p" "visit"      # 이름에 "[us] p" 와 "visit" 둘 다
  python segment_lookup.py --search "[CAMPAIGN NAME]" "US_CC" --limit 2000

  # owner 기준 검색 (loginId / 이메일 / 이름 부분일치 — 여러 명이면 OR)
  python aa_segment_lookup_by_owner.py --owner YOUR_LOGIN_ID
  python aa_segment_lookup_by_owner.py --owner someone@example.com
  python aa_segment_lookup_by_owner.py --owner "jane" "john"          # 두 명 중 아무나 (OR)
  python aa_segment_lookup_by_owner.py --owner YOUR_LOGIN_ID --rsid your_rsid      # 권장(속도)
  python aa_segment_lookup_by_owner.py --owner YOUR_LOGIN_ID --search "campaign"   # owner AND 키워드

  # 날짜 필터 (YYYY-MM-DD, both inclusive) — 4종 지원. 서로 다른 필드끼리는 AND
  #   --created-*             생성일 (createDate)
  #   --modified-*            최종 수정일 (이름/설명 변경 포함)
  #   --definition-modified-* 정의(로직) 마지막 변경 — 이름만 고친 건 안 잡힘
  #   --accessed-*            최근 사용 시각 — 미사용 세그 정리용
  python segment_lookup.py --search "campaign" --modified-after 2025-01-01      # 이후
  python segment_lookup.py --search "campaign" --modified-before 2025-07-01     # 이전
  python segment_lookup.py --search "campaign" --modified-after 2025-01-01 --modified-before 2025-07-01  # 사이

  # 예) 이번 달 생성 + 최근 3개월간 안 쓴 세그
  #   python aa_segment_lookup.py --search "campaign" --created-after 2026-08-01 --accessed-before 2026-05-21

  주의: --search 키워드 list 는 **공백 구분** (콤마 박지 말 것).
        PowerShell 에서 콤마 (`,`) 는 array operator 라 native exe 전달 시 처리 불일치 가능.
        ❌ --search "[CAMPAIGN NAME]","recomm"     (잘못 — single string 으로 들어갈 수 있음)
        ✅ --search "[CAMPAIGN NAME]" "recomm"     (정확 — argparse nargs='+' 가 공백 구분)

  검색 동작:
    · 각 키워드(첫 키워드 포함)를 이름(+설명)에 **연속 substring** 으로 AND 매칭 (대소문자 무시).
      → "[us] p" 는 공백까지 통째로 한 substring (단어로 안 쪼갬).
    · 서버 `name` 파라미터는 매칭 수단이 아니라 가장 긴 완전단어 1개로 속도용 prefetch 만.
    · --limit (기본 SEARCH_RESULT_LIMIT) — 결과 상한. 초과하면 경고 후 상위 N 만 출력.

  owner 검색 동작:
    · --owner 값은 ① numeric loginId ② 이메일 ③ 이름 부분일치 순으로 해석.
      ②③ 은 GET /users 로 받은 회사 사용자 목록에서 case-insensitive substring 매칭.
      여러 명 매칭되면 전부 대상(OR) — 실행 시 해석된 사람 목록을 콘솔에 찍는다.
    · AA /segments 는 ownerId 서버 파라미터를 안 받아 **클라이언트측 필터**.
      --search 없이 --owner 만 쓰면 회사 전체 세그를 페이징으로 훑으므로 느리다
      (병렬 fetch 로 완화 — OWNER_SCAN_WORKERS). --rsid 로 좁히는 걸 권장.
    · 페이지 상한(OWNER_SCAN_MAX_PAGES) 에 걸리면 조용히 자르지 않고 경고를 찍는다.
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"

# ─── 검색(--search) 설정 ───────────────────────────────────────────
SEARCH_RESULT_LIMIT = 1000          # --search 결과 최대 건수 (--limit 로 덮어쓰기 가능)
SEARCH_MATCH_IN_DESCRIPTION = True  # 키워드를 이름+description 에서 찾을지 (False=이름만)
SEARCH_PREFILTER_MIN_WORD = 2       # server-side name prefetch 에 쓸 "완전단어" 최소 길이

# ─── 검색(--owner) 설정 ────────────────────────────────────────────
SEARCH_MAX_PAGES = 50               # --search 키워드 검색 시 페이지 순회 상한 (1page=1000건)
OWNER_SCAN_MAX_PAGES = 300          # --owner 단독(키워드 없음) 스캔 시 상한 — 회사 전체라 크게
OWNER_SCAN_WORKERS = 6              # owner 단독 스캔 병렬 페이지 fetch 워커 수 (1 = 순차)

# ─── 프로젝트 사용처 인덱스(--with-projects) 설정 ───────────────────
# 스캔할 프로젝트 owner **기본값** — 각자 환경에 맞게 변경 (numeric loginId / 이메일 / 이름 부분일치).
#   ""(빈 문자열) = 조회된 세그들의 owner 기준.
#   본인 loginId 를 넣어두면 **본인 소유 프로젝트만** 스캔해 훨씬 빠르다.
#   `--projects-owner` 로 실행 시 덮어쓰고, `--all-projects` 면 무시된다.
PROJECTS_OWNER_DEFAULT = ""         # 예: "YOUR_LOGIN_ID"
PROJECT_LIST_PAGE_SIZE = 1000       # /projects 목록 페이지 크기 (AA max)
PROJECT_LIST_MAX_PAGES = 100        # 목록 페이지 순회 상한 (도달 시 조용한 절단 대신 경고)
PROJECT_DEF_WORKERS    = 12         # project definition 병렬 GET 워커
PROJECT_IDS_MAX        = 0          # 셀당 project id 상한 (0 = 무제한). Excel 셀 한도 32,767자 ≈ id 1,300개
PROJECT_CACHE_HOURS    = 24         # 인덱스 캐시 유효시간(h) — 넘으면 재수집
PROJECT_SCAN_CONFIRM_OVER = 3000    # 대상이 이보다 많으면 예상시간 보여주고 확인 입력 (--yes 로 생략)

# 처리량 참고(실측 경향): /projects **목록** 자체는 금방 받지만 expansion=definition 이 목록
#   호출에서 안 먹어 프로젝트마다 개별 GET 이 필요하다. 워커 6 ≈ 2.6건/s, 워커 12 ≈ 3.9건/s.
#   → 프로젝트 수백 건이면 수 분, 수만 건이면 수 시간. 그래서 owner 한정 사용을 권장.
PROJECT_DEF_RATE_PER_SEC = 3.9      # 위 실측치 (워커 12) — 예상시간 안내용

# ─── 콘솔 출력 설정 ────────────────────────────────────────────────
PROGRESS_EVERY = 100                # CSV/DSL 작성 진행률을 몇 건마다 찍을지
DETAIL_PRINT_MAX = 20               # 결과가 이 건수 이하일 때만 마지막에 구조(DSL) 상세 덤프
LIST_RESULT_NAMES = False           # 검색 직후 'id  name' 나열 여부 (대량 조회 시 수만 줄 → 기본 off)

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
LOOKUP_DIR = OUTPUT_DIR / "lookup"          # 결과 CSV/DSL 출력 위치 — 코드 폴더 어지럽지 않게 분리
# AA 응답의 날짜 키 ↔ CSV/필터에서 쓰는 이름.
# ⚠ 생성일은 제공된다 — 요청 expansion 이름은 createdDate, 응답 키는 createDate 로 다르다.
#    (segment id 24-hex 는 Mongo ObjectId 라 앞 8자리 = 생성 유닉스 시각. createDate 와 초 단위 일치)
DATE_FIELDS: dict[str, str] = {
    "created": "createDate",                        # 생성일
    "modified": "modified",                         # 최종 수정일(이름/설명 변경 포함)
    "definition_modified": "definitionLastModified",  # 정의(로직) 마지막 변경 — 없으면 빈값
    "accessed": "recentRecordedAccess",             # 최근 사용 시각 (미사용 세그 정리용)
}

RESULT_PREFIX = "segment_lookup_owner_"   # 원본(aa_segment_lookup.py) 결과와 안 섞이게 분리

# ─── 프로젝트 사용처 인덱스 — 내부 상수 ───────────────────────────
PROJECT_INDEX_CACHE = LOOKUP_DIR / "_project_index_cache.json"
# ⚠ segment id 정규식은 숫자 접두 길이를 고정하지 말 것 (`s\d{9}_` 로 박으면 9자리가 아닌 id 를
#    조용히 놓친다). length-agnostic `\d+` 로 둔다.
SEG_ID_IN_JSON_RE = re.compile(r"s\d+_[0-9a-f]{24}")

# ─── 변수 단축어 (decompile용) ────────────────────────────────────
VARIABLE_ALIASES: dict[str, str] = {
    "page": "variables/page",
    "sitesection": "variables/sitesection",
    "country": "variables/country",
    "mktchannel": "variables/marketingchannel",
    "marketingchannel": "variables/marketingchannel",
    "referrer": "variables/referrer",
    "devicetype": "variables/devicetype",
    "revenue": "metrics/revenue",
    "orders": "metrics/orders",
    "visits": "metrics/visits",
    "visitors": "metrics/visitors",
    "pageviews": "metrics/pageviews",
}

# 연산자 역매핑
FUNC_TO_DSL: dict[str, str] = {
    "streq": "=",
    "contains": "contains",
    "contains-any-of": "contains-any-of",
    "contains-all-of": "contains-all-of",
    "streq-in": "in",
    "not-streq-in": "not-equal-any-of",   # AA UI "does not equal any of"
    "starts-with": "starts-with",
    "ends-with": "ends-with",
    "matches-regex": "matches",
    "exists": "exists",
    "not-streq": "not-equals",                     # does not equal
    "not-contains": "not-contains",                # does not contain
    "not-contains-any-of": "not-contains-any-of",  # does not contain any of
    "not-starts-with": "not-starts-with",
    "not-ends-with": "not-ends-with",
    "not-exists": "not-exists",                    # does not exist (no value)
    "gt": ">",
    "ge": ">=",
    "lt": "<",
    "le": "<=",
    # ── 260824 라운드트립 보강 (ROUNDTRIP_PATCH_260824) ──
    # AA 의 eq / not-eq 는 **숫자 등가**로, streq(문자 등가, "=") 와 다른 연산자다.
    # "=" 로 합치면 재컴파일 때 streq 가 되어 의미가 바뀌므로 별도 토큰으로 유지하고,
    # aa_create_segment 의 OPERATOR_MAP 이 같은 토큰을 받아 eq 로 되돌린다.
    "eq": "eq",
    "not-eq": "not-eq",
    "not-contains-all-of": "not-contains-all-of",
    # event-exists / not-event-exists 는 **의도적으로 identity** — DSL 에 AA func 이름
    # 그대로 나가고, aa_create_segment 의 _normalize_dsl_line_tokens 가 파싱 직전
    # exists / not-exists 로 바꿔 받은 뒤 _patch_definition_for_aa 가 되돌린다.
    # 표에 없으면 아래 UNMAPPED_FUNCS 경고가 "왕복 불가"로 오탐을 낸다.
    "event-exists": "event-exists",
    "not-event-exists": "not-event-exists",
}

CONTEXT_TO_SCOPE = {"hits": "hit", "visits": "visit", "visitors": "visitor"}


# ─── Date Range 컴포넌트 GET (datetime-interval-ref 처리용) ────────
# segment definition 안 'datetime-within' / 'datetime-interval-ref' 토큰은
# AA Date Range 컴포넌트 id (예: YOUR_ID) reference.
# decompile 시 name + definition (ISO interval) 까지 같이 보여주려면 별도 GET 필요.
# module-level cache 로 같은 세션 중복 GET 방지. main() 에서 _set_daterange_auth() 호출.

_DATERANGE_CACHE: dict[str, dict] = {}
_DATERANGE_HEADERS: dict | None = None
_DATERANGE_GCID: str = ""


def _set_daterange_auth(headers: dict, gcid: str) -> None:
    """decompile 가 daterange fetch 할 수 있도록 module 변수 셋업."""
    global _DATERANGE_HEADERS, _DATERANGE_GCID
    _DATERANGE_HEADERS = headers
    _DATERANGE_GCID = gcid


def _fetch_daterange(dr_id: str) -> dict:
    """Date Range GET — name + definition (ISO interval string) 반환. 캐시.
    실패해도 dict 반환 — error 키에 사유 박힘.
    """
    if dr_id in _DATERANGE_CACHE:
        return _DATERANGE_CACHE[dr_id]
    if _DATERANGE_HEADERS is None or not _DATERANGE_GCID:
        return {"id": dr_id, "name": "", "definition": "", "error": "auth 미설정"}
    try:
        url = f"https://analytics.adobe.io/api/{_DATERANGE_GCID}/dateranges/{dr_id}"
        r = requests.get(url, headers=_DATERANGE_HEADERS,
                         params={"expansion": "definition,modified,tags"}, timeout=30)
        if r.status_code != 200:
            entry = {"id": dr_id, "name": "", "definition": "",
                     "error": f"{r.status_code} {r.reason}"}
        else:
            data = r.json()
            entry = {
                "id": dr_id,
                "name": data.get("name", ""),
                "definition": data.get("definition", ""),
                "modified": data.get("modified", ""),
                "error": "",
            }
    except Exception as e:
        entry = {"id": dr_id, "name": "", "definition": "", "error": str(e)}
    _DATERANGE_CACHE[dr_id] = entry
    return entry


# ═══════════════════════════════════════════════════════════════════
# 변수 역매핑
# ═══════════════════════════════════════════════════════════════════

def _reverse_variable(full_name: str) -> str:
    rev = {v: k for k, v in VARIABLE_ALIASES.items()}
    if full_name in rev:
        return rev[full_name]
    m = re.match(r"^variables/evar(\d+)$", full_name)
    if m:
        return f"evar{m.group(1)}"
    m = re.match(r"^variables/prop(\d+)$", full_name)
    if m:
        return f"prop{m.group(1)}"
    m = re.match(r"^metrics/event(\d+)$", full_name)
    if m:
        return f"event{m.group(1)}"
    if full_name.startswith("variables/"):
        return full_name[len("variables/"):]
    if full_name.startswith("metrics/"):
        return full_name[len("metrics/"):]
    return full_name


# ═══════════════════════════════════════════════════════════════════
# Decompiler (AA JSON → DSL)
# ═══════════════════════════════════════════════════════════════════

def _effective_logical_func(pred: dict, parent_context: str) -> str:
    """collapse 되는(빈 description + parent 와 동일 context) 컨테이너를 뚫고
    그 안의 실제 논리 연산자('and'/'or')를 반환. desc 있는 컨테이너나 context 다른
    컨테이너(= scope 블록으로 이미 괄호 처리됨) / 리프 / sequence 는 '' 반환."""
    while isinstance(pred, dict):
        f = pred.get("func", "")
        if f == "container":
            ctx = pred.get("context", parent_context)
            if not pred.get("description", "") and ctx == parent_context:
                pred = pred.get("pred", {})
                continue
            return ""
        return f if f in ("and", "or") else ""
    return ""


def _decompile_pred(pred: dict, indent: int, parent_context: str) -> list[str]:
    func = pred.get("func", "")
    pad = "  " * indent

    if func == "without":
        inner = pred.get("pred", {})
        inner_func = inner.get("func", "")
        # 복합 조건 (and/or/sequence 등) → NOT ( ... ) 블록
        if inner_func in ("and", "or", "sequence", "sequence-prefix", "sequence-suffix", "container"):
            inner_lines = _decompile_pred(inner, indent + 1, parent_context)
            return [f"{pad}NOT ("] + inner_lines + [f"{pad})"]
        # 단순 leaf → NOT 접두사 한 줄
        inner_lines = _decompile_leaf(inner, parent_context)
        if inner_lines:
            return [f"{pad}NOT {inner_lines[0].strip()}"]
        return [f"{pad}NOT ..."]

    if func in ("and", "or"):
        preds = pred.get("preds", [])
        lines: list[str] = []
        for i, p in enumerate(preds):
            if i > 0:
                lines.append(f"{pad}{func.upper()}")
            # 자식이 (collapse 되는 빈-desc 컨테이너를 뚫고) 반대 연산자의 and/or 그룹이면
            # 평평하게 풀면 우선순위가 깨진다(A AND (B OR C) ≠ A AND B OR C) → scope 블록으로 괄호 보존.
            child_func = _effective_logical_func(p, parent_context)
            if child_func and child_func != func:
                scope = CONTEXT_TO_SCOPE.get(parent_context, parent_context)
                inner_lines = _decompile_pred(p, indent + 1, parent_context)
                lines.append(f"{pad}{scope}(")
                lines.extend(inner_lines)
                lines.append(f"{pad})")
            else:
                lines.extend(_decompile_pred(p, indent, parent_context))
        return lines

    if func == "container":
        ctx = pred.get("context", parent_context)
        desc = pred.get("description", "")
        inner_pred = pred.get("pred", {})
        if ctx == parent_context and not desc:
            return _decompile_pred(inner_pred, indent, ctx)
        scope = CONTEXT_TO_SCOPE.get(ctx, ctx)
        prefix = f"'{desc}'!" if desc else ""
        inner_lines = _decompile_pred(inner_pred, indent + 1, ctx)
        lines = [f"{pad}{prefix}{scope}("]
        lines.extend(inner_lines)
        lines.append(f"{pad})")
        return lines

    # sequence (then 로직) — sequence/prefix/suffix 항상 [sequence-after/before/all] 라벨 + scope 감쌈
    # 명명 변환 — raw AA func → AA UI 라벨 (검증된 매핑):
    #   sequence-prefix → sequence-after   (UI "After Sequence")
    #   sequence-suffix → sequence-before  (UI "Before Sequence")
    #   sequence        → sequence-all     (UI "Anywhere Sequence")
    # 주의: Adobe 의 prefix/suffix 가 UI 의 Before/After 와 교차 매핑 — 직관과 반대.
    if func in ("sequence", "sequence-prefix", "sequence-suffix"):
        seq_label = {
            "sequence-prefix": "sequence-after",
            "sequence-suffix": "sequence-before",
            "sequence":        "sequence-all",
        }[func]
        stream = pred.get("stream", pred.get("preds", []))
        seq_ctx = pred.get("context", parent_context) or parent_context
        scope = CONTEXT_TO_SCOPE.get(seq_ctx, seq_ctx)
        inner_pad = "  " * (indent + 1)
        inner_lines: list[str] = []
        for i, step in enumerate(stream):
            step_lines = _decompile_pred(step, indent + 1, seq_ctx)
            if i > 0:
                inner_lines.append(f"{inner_pad}THEN")
            inner_lines.extend(step_lines)
        return [f"{pad}[{seq_label}] {scope}("] + inner_lines + [f"{pad})"]

    # segment-ref
    if func == "segment-ref":
        seg_id = pred.get("segmentId", "?")
        return [f"{pad}@{seg_id}"]

    # datetime-within — sequence step 사이 시간 제약. interval-value 안에 datetime-interval-ref 또는 inline.
    if func == "datetime-within":
        iv = pred.get("interval-value") or {}
        return [f"{pad}WITHIN {_format_datetime_interval(iv)}"]

    # datetime-interval-ref — 단독 등장 시 Date Range 컴포넌트 reference
    if func == "datetime-interval-ref":
        return [f"{pad}{_format_datetime_interval(pred)}"]

    # dimension-restriction -- sequence THEN-step "within N <dim>" restriction.
    # AA: {"func":"dimension-restriction","count":1,"limit":"within",
    #      "attribute":{"func":"attr","name":"variables/page","description":"Pages"}}
    # -> "WITHIN 1 page" (short var; round-trips via aa_create_segment _resolve_variable).
    if func == "dimension-restriction":
        n = pred.get("count", "?")
        lim = str(pred.get("limit", "within")).upper()
        attr = pred.get("attribute") or {}
        attr_name = attr.get("name", "")
        attr_tok = (_reverse_variable(attr_name) if attr_name else "") or attr.get("description") or "?"
        return [f"{pad}{lim} {n} {attr_tok}"]

    # ── 260824 라운드트립 보강 (ROUNDTRIP_PATCH_260824) ──
    # 위 어느 분기에도 안 걸린 func 인데 자식을 품고 있으면, 자식을 버리지 말고 재귀 렌더한다.
    # 예전엔 그대로 _decompile_leaf 로 떨어져 '? exclude-next-checkpoint' 한 줄만 남고
    # 자식 트리가 통째로 사라졌다 (원본을 눈으로도 복원할 수 없는 유일한 유실 지점).
    # '??' 접두 = 파서가 못 받는 미지원 노드 = raw-JSON 수술 대상 표시.
    if func:
        for _key in _CHILD_KEYS:
            _child = pred.get(_key)
            if isinstance(_child, list) and _child:
                UNSUPPORTED_FUNCS.add(func)
                _inner: list[str] = []
                for _step in _child:
                    _inner.extend(_decompile_pred(_step, indent + 1, parent_context))
                return [f"{pad}?? {func}("] + _inner + [f"{pad})"]
            if isinstance(_child, dict) and _child:
                UNSUPPORTED_FUNCS.add(func)
                _inner = _decompile_pred(_child, indent + 1, parent_context)
                return [f"{pad}?? {func}("] + _inner + [f"{pad})"]

    leaf = _decompile_leaf(pred, parent_context)
    return [f"{pad}{l}" for l in leaf]


def _format_datetime_interval(iv: dict) -> str:
    """datetime-interval-ref 또는 inline interval → 사람이 읽을 수 있는 표현.

    AA 구조:
      ref:    {"func": "datetime-interval-ref", "id": "<daterange_id>"}
      inline: {"func": "rolling-days", "num": 30} 같은 형태 (옵션)
    """
    if not isinstance(iv, dict):
        return f"@datetime:({iv!r})"
    iv_func = iv.get("func", "")
    if iv_func == "datetime-interval-ref":
        dr_id = iv.get("id", "?")
        info = _fetch_daterange(dr_id)
        name = info.get("name") or ""
        defn = info.get("definition") or ""
        err = info.get("error") or ""
        if err and not name:
            return f"@daterange:{dr_id} ({err})"
        # 'name'!datetime( <defn> )  형식 — 사람이 보기 쉽게
        parts = [f"@daterange:{dr_id}"]
        if name:
            parts.append(f"'{name}'")
        if defn:
            parts.append(f"({defn})")
        return " ".join(parts)
    # inline (rolling-days, calendar-day 등) — 키들 정렬해서 한 줄로
    items = ", ".join(f"{k}={v!r}" for k, v in iv.items() if k != "func")
    return f"{iv_func}({items})" if items else iv_func


# ─── 260824 라운드트립 보강 (ROUNDTRIP_PATCH_260824) ──────────────
# 이 렌더러가 내는 .dsl 은 aa_create_segment 의 input 으로 되돌아갈 수 있어야 한다.
# 아래 두 set 은 "되돌릴 수 없는 자리"를 실행 끝에 드러내기 위한 것 —
# 예전엔 아무 신호 없이 통과해서 재입력 때 파서가 죽는 이유를 알 방법이 없었다.
UNMAPPED_FUNCS: set[str] = set()      # FUNC_TO_DSL 에 없어 raw 이름이 새어나간 연산자
UNSUPPORTED_FUNCS: set[str] = set()   # 구조 노드인데 렌더 분기가 없는 func ('??' 로 표시)

# _decompile_pred 의 어느 분기에도 안 걸린 func 이 자식을 품고 있는지 볼 키 후보.
_CHILD_KEYS = ("preds", "stream", "pred")


def _extract_operand_name(pred: dict) -> str:
    """predicate 좌변(변수/메트릭) 풀네임을 중첩까지 파고들어 찾는다.

    metric 집계는 val 안에 한 겹 더 들어가 있다:
      {"func":"gt","val":{"func":"total","evt":{"func":"event","name":"metrics/orders"}},"num":1}
    예전엔 val["name"] 만 봐서 이런 노드가 전부 '?' 로 뭉개졌고, 그 '?' 가 재입력 때
    variables/? 로 컴파일돼 AA 가 400 (Unknown Attribute) 으로 거부했다.
    """
    for holder in (pred.get("val"), pred.get("evt")):
        if not isinstance(holder, dict):
            continue
        name = holder.get("name")
        if isinstance(name, str) and name:
            return name
        inner = holder.get("evt") or holder.get("val")
        if isinstance(inner, dict):
            name = inner.get("name")
            if isinstance(name, str) and name:
                return name
        desc = holder.get("description")
        if isinstance(desc, str) and desc:
            return desc
    return ""


# ── 260824 2차 (ROUNDTRIP_PATCH2_260824) ──
# aa_create_segment._resolve_variable 은 짧은 이름을 VARIABLE_ALIASES 에 있거나 event<N>
# 일 때만 metrics/* 로 되돌리고, 나머지는 variables/* 를 붙인다. 그래서 'units' /
# 'cartadditions' 처럼 alias 에 없는 metric 을 축약해서 내보내면 재컴파일 때 **dimension
# 으로 오컴파일**된다 (CLAUDE.md 의 "Delayed Purchase 가 metric 을 variables/* 로
# 오컴파일해 AA 가 400 Unknown Attribute 로 거부" 가 바로 이 지점이다).
# → 축약이 왕복되지 않는 metric 은 풀네임 'metrics/<name>' 을 그대로 낸다.
#   파서 _resolve_variable 은 '/' 가 든 이름을 그대로 받아 metrics/ 접두면 event 로 본다.
_ROUNDTRIP_METRIC_ALIASES = {v for v in VARIABLE_ALIASES.values() if v.startswith("metrics/")}
_RE_EVENT_SHORT = re.compile(r"^event\d+$")


def _roundtrip_safe_var(full_name: str) -> str:
    """풀네임 -> DSL 토큰. 축약이 왕복 안 되는 metric 은 풀네임 그대로 낸다."""
    short = _reverse_variable(full_name)
    if (full_name.startswith("metrics/")
            and full_name not in _ROUNDTRIP_METRIC_ALIASES
            and not _RE_EVENT_SHORT.match(short)):
        return full_name
    return short


def report_roundtrip_warnings() -> None:
    """실행 끝에 미치환/미지원 func 요약. 새 AA func 이 등장했음을 드러낸다."""
    if UNMAPPED_FUNCS:
        print()
        print(f"  WARN: FUNC_TO_DSL 에 없는 AA 연산자 {len(UNMAPPED_FUNCS)}종 — "
              f"raw 이름으로 DSL 에 나갔고 재입력 시 파싱 실패한다:")
        for f in sorted(UNMAPPED_FUNCS):
            print(f"    - {f}")
        print("  → FUNC_TO_DSL + aa_create_segment 의 OPERATOR_MAP 양쪽에 추가할 것.")
    if UNSUPPORTED_FUNCS:
        print()
        print(f"  WARN: 렌더 분기가 없는 구조 func {len(UNSUPPORTED_FUNCS)}종 — "
              f"'?? <func>(' 형태로 자식까지 출력했다. 파서는 못 받으니 "
              f"해당 세그는 raw-JSON 으로 다뤄야 한다:")
        for f in sorted(UNSUPPORTED_FUNCS):
            print(f"    - {f}")


def _decompile_leaf(pred: dict, parent_context: str) -> list[str]:
    func = pred.get("func", "")
    negated = False
    if func == "without":
        negated = True
        pred = pred.get("pred", {})
        func = pred.get("func", "")

    # segment-ref
    if func == "segment-ref":
        seg_id = pred.get("segmentId", "?")
        prefix = "NOT " if negated else ""
        return [f"{prefix}@{seg_id}"]

    # 260824: 중첩(metric 집계)까지 파고들어 좌변 이름을 찾는다 — ROUNDTRIP_PATCH_260824
    var_name = _extract_operand_name(pred)
    # 260824 2차: 축약이 왕복 안 되는 metric 은 풀네임 유지 — ROUNDTRIP_PATCH2_260824
    short_var = _roundtrip_safe_var(var_name) if var_name else "?"

    if func and func not in FUNC_TO_DSL:
        UNMAPPED_FUNCS.add(func)
    dsl_op = FUNC_TO_DSL.get(func, func)

    if "list" in pred:
        val_str = json.dumps(pred["list"], ensure_ascii=False)
    elif "str" in pred:
        val_str = f'"{pred["str"]}"'
    elif "regex" in pred:
        val_str = f'"{pred["regex"]}"'
    elif "num" in pred:
        val_str = str(pred["num"])
    elif func == "exists":
        val_str = ""
    else:
        val_str = ""

    prefix = "NOT " if negated else ""
    condition = f"{prefix}{short_var} {dsl_op}"
    if val_str:
        condition += f" {val_str}"
    return [condition]


def decompile_definition(definition: dict) -> str:
    container = definition.get("container", {})
    ctx = container.get("context", "hits")
    desc = container.get("description", "")
    pred = container.get("pred", {})

    scope = CONTEXT_TO_SCOPE.get(ctx, ctx)
    prefix = f"'{desc}'!" if desc else ""

    inner_lines = _decompile_pred(pred, 1, ctx)

    lines = [f"{prefix}{scope}("]
    lines.extend(inner_lines)
    lines.append(")")
    return "\n".join(lines)


def format_dsl_block(name: str, description: str, rsid: str,
                     tags: list[str], definition: dict) -> str:
    parts = ["--- segment"]
    parts.append(f"name: {name}")
    if description:
        parts.append(f"description: {description}")
    parts.append(f"rsid: {rsid}")
    if tags:
        parts.append(f"tags: [{', '.join(tags)}]")
    parts.append("")
    parts.append(decompile_definition(definition))
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# AA /users 직접 조회 → loginId → {name, email} (owner 보강)
#   외부 CSV 의존 제거 — find_user_id.py 와 동일하게 GET /users 페이지 순회로
#   회사 사용자 전체를 받아 owner_id(numeric loginId) → 이름/이메일 매핑.
# ═══════════════════════════════════════════════════════════════════

_USER_PAGE_SIZE = 400          # /users 한 페이지 최대 (Adobe API max=1000)
_USER_MAX_PAGES = 100          # 페이지 순회 상한
_USER_MAP_CACHE: dict[str, dict] | None = None   # 실행 1회만 조회


def _iter_users(headers: dict, gcid: str):
    """AA /users 엔드포인트 페이지 순회 generator (find_user_id.py 와 동일)."""
    url = f"https://analytics.adobe.io/api/{gcid}/users"
    page = 0
    while page < _USER_MAX_PAGES:
        r = requests.get(url, headers=headers,
                         params={"limit": _USER_PAGE_SIZE, "page": page}, timeout=120)
        if r.status_code != 200:
            print(f"  WARN: GET /users 실패 page {page} — {r.status_code} {r.reason}")
            break
        body = r.json()
        items = body.get("content") if isinstance(body, dict) else body
        if not items:
            break
        for u in items:
            yield u
        if isinstance(body, dict) and body.get("lastPage", True):
            break
        page += 1


def _load_user_map(headers: dict, gcid: str) -> dict[str, dict]:
    """GET /users → {loginId(str): {"name": fullName, "email": email}}. 실행 1회 캐시.
    실패 시 빈 dict (owner_name/owner_email 빈값 유지)."""
    global _USER_MAP_CACHE
    if _USER_MAP_CACHE is not None:
        return _USER_MAP_CACHE
    user_map: dict[str, dict] = {}
    try:
        for u in _iter_users(headers, gcid):
            lid = str(u.get("loginId") or "").strip()
            if not lid:
                continue
            user_map[lid] = {
                "name": (u.get("fullName") or "").strip(),
                "email": (u.get("email") or "").strip(),
            }
    except Exception as e:
        print(f"  WARN: user map 로드 실패 — {e}")
    _USER_MAP_CACHE = user_map
    return user_map


def _enrich_owner_info(results: list[dict], user_map: dict[str, dict]) -> None:
    """owner_id 로 user_map 조회 → owner_name(빈 경우만) + owner_email 보강."""
    for r in results:
        oid = str(r.get("owner_id") or "")
        if not oid:
            continue
        info = user_map.get(oid)
        if not info:
            continue
        if not r.get("owner_name"):
            r["owner_name"] = info.get("name", "")
        if not r.get("owner_email"):
            r["owner_email"] = info.get("email", "")


def _resolve_owner_ids(user_map: dict[str, dict],
                       specs: list[str]) -> tuple[set[str], list[str]]:
    """--owner 값 리스트 → (loginId set, 해석 로그 라인들).

    각 spec 해석 순서:
      ① 전부 숫자  → loginId 그대로 (user_map 에 없어도 그대로 사용 — /users 미로드 대비)
      ② 이메일/이름 → user_map 에서 email·name 에 case-insensitive substring 매칭 (여러 명 = OR)
    """
    ids: set[str] = set()
    log: list[str] = []
    for spec in specs:
        sp = (spec or "").strip()
        if not sp:
            continue
        if sp.isdigit():
            ids.add(sp)
            info = user_map.get(sp, {})
            who = f"{info.get('name','')} <{info.get('email','')}>".strip()
            log.append(f"    {sp}  {who or '(이름 미확인 — /users 에 없음)'}")
            continue
        needle = sp.lower()
        hits = [(lid, i) for lid, i in user_map.items()
                if needle in (i.get("email", "") or "").lower()
                or needle in (i.get("name", "") or "").lower()]
        if not hits:
            log.append(f"    ⚠ {sp!r} — 매칭되는 사용자 없음 (무시)")
            continue
        for lid, i in sorted(hits, key=lambda x: x[1].get("name", "")):
            ids.add(lid)
            log.append(f"    {lid}  {i.get('name','')} <{i.get('email','')}>  (from {sp!r})")
    return ids, log


# ═══════════════════════════════════════════════════════════════════
# 인증
# ═══════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════
# 세그먼트 조회
# ═══════════════════════════════════════════════════════════════════

def _lookup_segment(headers: dict, gcid: str, seg_id: str) -> dict:
    """GET /segments/{id} → 세그먼트 상세."""
    url = f"https://analytics.adobe.io/api/{gcid}/segments/{seg_id}"
    r = requests.get(
        url, headers=headers,
        params={"expansion": "definition,name,description,owner,tags,reportSuiteName,modified,"
                     "createdDate,definitionLastModified,recentRecordedAccess"},
        timeout=60,
    )
    if r.status_code != 200:
        return {
            "segment_id": seg_id,
            "name": "",
            "owner_id": "",
            "owner_name": "",
            "owner_email": "",
            "rsid": "",
            "created": "",
            "modified": "",
            "definition_last_modified": "",
            "recent_access": "",
            "modified_by_id": "",
            "description": "",
            "tags": "",
            "definition": None,
            "error": f"{r.status_code} {r.reason}: {r.text[:200]}",
        }

    data = r.json()
    owner = data.get("owner", {})
    tag_names = ", ".join(
        t.get("name", "") for t in data.get("tags", [])
    ) if data.get("tags") else ""

    return {
        "segment_id": seg_id,
        "name": data.get("name", ""),
        "owner_id": owner.get("id", "") if isinstance(owner, dict) else "",
        "owner_name": owner.get("name", "") if isinstance(owner, dict) else "",
        "owner_email": "",
        "rsid": data.get("rsid", ""),
        "created": data.get("createDate", ""),
        "modified": data.get("modified", ""),
        "definition_last_modified": data.get("definitionLastModified", "") or "",
        "recent_access": data.get("recentRecordedAccess", "") or "",
        "modified_by_id": data.get("modifiedById", "") or "",
        "description": data.get("description", ""),
        "tags": tag_names,
        "definition": data.get("definition"),
        "error": "",
    }


def _pick_prefilter_word(kw_list: list[str]) -> str:
    """모든 키워드를 공백 split → `[a-z0-9]+` 이고 길이 ≥ SEARCH_PREFILTER_MIN_WORD 인
    토큰 중 가장 긴 것 1개 반환. 서버 `name` prefetch(volume 축소)용. 없으면 ''."""
    cand: list[str] = []
    for kw in kw_list:
        for tok in re.findall(r"[a-z0-9]+", kw.lower()):
            if len(tok) >= SEARCH_PREFILTER_MIN_WORD:
                cand.append(tok)
    return max(cand, key=len) if cand else ""


def _search_segments(headers: dict, gcid: str, keywords: list[str] | str | None,
                     rsid: str = "", limit: int = SEARCH_RESULT_LIMIT,
                     date_filters: dict[str, tuple[str, str]] | None = None,
                     owner_ids: set[str] | None = None) -> list[dict]:
    """GET /segments — 이름 키워드 / owner 검색. 결과를 _lookup_segment 포맷으로 반환.

    owner_ids 를 주면 결과를 그 owner(loginId) 소유분으로 **클라이언트측 필터**
    (AA /segments 가 ownerId 서버 파라미터를 미지원). keywords 없이 owner_ids 만 줘도 동작.

    매칭: keywords 의 **모든** 키워드(첫 키워드 포함)를 case-insensitive **연속 substring** 으로
          이름(+ SEARCH_MATCH_IN_DESCRIPTION 시 description)에 AND 매칭.
          예) --search "[us] p" "visit" → 이름에 "[us] p" 와 "visit" 가 둘 다 들어있는 세그.

    Adobe 서버 `name` 필터는 **단어(토큰) 매칭**이라 부분단어("[us] p" 의 "p")를 못 잡으므로
    매칭 수단으로 쓰지 않고, 가장 긴 완전단어 1개만 **속도용 prefetch** 로 사용(없으면 미사용).
    """
    if isinstance(keywords, str):
        kw_list = [keywords]
    else:
        kw_list = [k for k in (keywords or []) if k]
    owner_ids = {str(o) for o in (owner_ids or set()) if str(o)}
    if not kw_list and not owner_ids:
        return []
    match_kws = [k.lower() for k in kw_list]          # 첫 키워드 포함 전부 substring 매칭
    url = f"https://analytics.adobe.io/api/{gcid}/segments"
    base_params: dict[str, Any] = {
        "expansion": "definition,name,description,owner,tags,reportSuiteName,modified,"
                     "createdDate,definitionLastModified,recentRecordedAccess",
        "includeType": "all",
    }
    prefilter = _pick_prefilter_word(kw_list) if kw_list else ""   # 서버 volume 축소용 (매칭 수단 아님)
    if prefilter:
        base_params["name"] = prefilter
    if rsid:
        base_params["rsids"] = rsid

    # paging — 매칭 후보를 전부 받음(중간에 안 자름). AA API max page size = 1000.
    PAGE_SIZE = 1000
    # 키워드 없이 owner 만 쓰면 회사 전체를 훑어야 하므로 상한을 크게
    MAX_PAGES = SEARCH_MAX_PAGES if kw_list else OWNER_SCAN_MAX_PAGES
    if not kw_list:
        print(f"  ⏳ owner 단독 스캔 — 키워드 prefilter 없이 전체 페이징 (최대 {MAX_PAGES:,} page "
              f"× {PAGE_SIZE:,}건). --rsid / --search 로 좁히면 훨씬 빠릅니다.")
    items: list[dict] = []
    hit_page_cap = True                    # 정상 종료(break) 하면 False 로 내려감

    if not kw_list and OWNER_SCAN_WORKERS > 1:
        # ── owner 단독 스캔: page 0 으로 totalPages 확인 후 나머지를 병렬 fetch ──
        def _get_page(pg: int) -> list[dict]:
            rr = requests.get(url, headers=headers,
                              params={**base_params, "limit": PAGE_SIZE, "page": pg}, timeout=120)
            if rr.status_code != 200:
                print(f"  WARN: page {pg} 실패 — {rr.status_code} {rr.reason}")
                return []
            return rr.json().get("content", []) or []

        r0 = requests.get(url, headers=headers,
                          params={**base_params, "limit": PAGE_SIZE, "page": 0}, timeout=120)
        if r0.status_code != 200:
            print(f"ERROR: 검색 실패 page 0 — {r0.status_code} {r0.reason}: {r0.text[:200]}")
            return []
        d0 = r0.json()
        items.extend(d0.get("content", []) or [])
        total_pages = int(d0.get("totalPages") or 1)
        total_elems = int(d0.get("totalElements") or len(items))
        print(f"    전체 {total_elems:,}건 / {total_pages:,} page — {OWNER_SCAN_WORKERS} 워커 병렬 fetch")
        if total_pages > MAX_PAGES:
            print(f"  ⚠️ 페이지 상한 {MAX_PAGES:,} < 전체 {total_pages:,} page — 뒤쪽은 못 봅니다. "
                  f"OWNER_SCAN_MAX_PAGES 를 올리거나 --rsid 로 좁히세요.")
            total_pages = MAX_PAGES
        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=OWNER_SCAN_WORKERS) as ex:
                for i, rows in enumerate(ex.map(_get_page, range(1, total_pages)), start=1):
                    items.extend(rows)
                    if i % 40 == 0:
                        print(f"    … {i:,}/{total_pages - 1:,} page / 누적 {len(items):,}건")
        print(f"    수집 완료: {len(items):,}건")
        hit_page_cap = False
        MAX_PAGES = 0                      # 아래 순차 루프 skip

    for page in range(MAX_PAGES):
        params = {**base_params, "limit": PAGE_SIZE, "page": page}
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            print(f"ERROR: 검색 실패 page {page} — {r.status_code} {r.reason}: {r.text[:200]}")
            hit_page_cap = False
            break
        data = r.json()
        rows = data.get("content", [])
        if not rows:
            hit_page_cap = False
            break
        items.extend(rows)
        if bool(data.get("lastPage")) or len(rows) < PAGE_SIZE:
            hit_page_cap = False
            break
    # 조용한 절단 방지 — 페이지 상한에 걸려 끝난 경우 경고
    if hit_page_cap:
        print(f"  ⚠️ 페이지 상한 {MAX_PAGES:,} 도달 — 뒤쪽 세그는 못 봤을 수 있습니다 "
              f"(누적 {len(items):,}건). OWNER_SCAN_MAX_PAGES/SEARCH_MAX_PAGES 를 올리거나 "
              f"--rsid 로 범위를 좁히세요.")

    # client-side AND — 모든 키워드를 substring 으로 (첫 키워드 포함)
    def _hay(it: dict) -> str:
        s = it.get("name", "") or ""
        if SEARCH_MATCH_IN_DESCRIPTION:
            s = s + " " + (it.get("description") or "")
        return s.lower()
    matched = [it for it in items if all(kw in _hay(it) for kw in match_kws)] if match_kws else list(items)

    # owner 필터 (클라이언트측 — AA 가 ownerId 서버 파라미터 미지원)
    if owner_ids:
        before_n = len(matched)

        def _owner_ok(it: dict) -> bool:
            o = it.get("owner") or {}
            return str(o.get("id", "")) in owner_ids if isinstance(o, dict) else False

        matched = [it for it in matched if _owner_ok(it)]
        print(f"  👤 owner 필터({', '.join(sorted(owner_ids))}): {before_n:,} → {len(matched):,}건")

    # 날짜 필터 — DATE_FIELDS 의 4종(created/modified/definition_modified/accessed) 전부 지원.
    # date 부분(YYYY-MM-DD)만 비교, both inclusive. 여러 필드를 같이 주면 AND.
    # ⚠ 속도 최적화 아님: AA 는 날짜를 서버측 필터 파라미터로 안 받는다(모르는 파라미터는 조용히 무시).
    #    일단 후보를 전부 받아온 뒤(=병목: 서버 페이징 + /users 유저맵 로드) 클라이언트에서 거르는 구조다.
    #    → 날짜 범위를 좁혀도 전체 속도는 거의 그대로(후처리 decompile/CSV 만 약간 절약).
    #    실제 속도는 --search 키워드를 더 구체적으로 / --rsid 로 줄일 것.
    for _fld, (_aft, _bef) in (date_filters or {}).items():
        if not (_aft or _bef):
            continue
        raw_key = DATE_FIELDS.get(_fld, _fld)
        before_n = len(matched)

        def _date_ok(it: dict, _k=raw_key, _a=_aft, _b=_bef) -> bool:
            d = (it.get(_k) or "")[:10]              # 'YYYY-MM-DD'
            if not d:
                return False                          # 값 없으면 필터 시 제외
            if _a and d < _a:
                return False
            if _b and d > _b:
                return False
            return True

        matched = [it for it in matched if _date_ok(it)]
        rng = f"{_aft or '…'} ~ {_bef or '…'}"
        print(f"  📅 {_fld} 필터({rng}): {before_n:,} → {len(matched):,}건")

    # 조용한 절단 방지 — 초과 시 경고 후 자름
    total = len(matched)
    if limit and total > limit:
        print(f"  ⚠️ {total}건 매칭 — 상위 {limit}건만 출력. --limit 또는 SEARCH_RESULT_LIMIT 올리기.")
        matched = matched[:limit]
    items = matched

    results: list[dict] = []
    for item in items:
        owner = item.get("owner", {})
        tag_names = ", ".join(
            t.get("name", "") for t in item.get("tags", [])
        ) if item.get("tags") else ""
        results.append({
            "segment_id": item.get("id", ""),
            "name": item.get("name", ""),
            "owner_id": owner.get("id", "") if isinstance(owner, dict) else "",
            "owner_name": owner.get("name", "") if isinstance(owner, dict) else "",
            "owner_email": "",
            "rsid": item.get("rsid", ""),
            "created": item.get("createDate", ""),
            "modified": item.get("modified", ""),
            "definition_last_modified": item.get("definitionLastModified", "") or "",
            "recent_access": item.get("recentRecordedAccess", "") or "",
            "modified_by_id": item.get("modifiedById", "") or "",
            "description": item.get("description", ""),
            "tags": tag_names,
            "definition": item.get("definition"),
            "error": "",
        })
    return results


# ═══════════════════════════════════════════════════════════════════
# 프로젝트 사용처 인덱스 (--with-projects)
#
# AA 2.0 API 에는 segment → projects 역방향 조회 엔드포인트가 **없다**
# (componentmetadata/* 는 share 전용, usage/usedIn 류는 존재하지 않음).
# → /projects 를 훑어 project → segment id 를 모은 뒤 뒤집는 수밖에 없다.
#
# 비용은 세그 건수가 아니라 **definition 을 받는 프로젝트 수** 가 결정한다
# (인덱스는 dict 조회라 세그가 수만 건이어도 추가 비용 0).
# ═══════════════════════════════════════════════════════════════════


def _project_owner_id(p: dict) -> str:
    o = p.get("owner") or {}
    return str(o.get("id", "")) if isinstance(o, dict) else ""


def _list_projects(headers: dict, gcid: str) -> tuple[list[dict], bool]:
    """GET /projects 페이징 → (프로젝트 목록, definition 이 목록에 같이 왔는지).

    목록 호출에서 expansion=definition 이 먹으면 프로젝트별 GET 을 통째로 건너뛸 수 있다.
    Adobe 문서에 명시가 없어 **page 0 응답을 보고 런타임에 판별**하고,
    안 먹으면 definition 을 뺀 가벼운 expansion 으로 다시 받는다.
    """
    url = f"https://analytics.adobe.io/api/{gcid}/projects"
    base_exp = "ownerFullName,modified"

    def _fetch(page: int, exp: str):
        """→ (parsed_body, err). body 는 list(=envelope 없음) 또는 dict 둘 다 올 수 있다."""
        try:
            r = requests.get(url, headers=headers,
                             params={"includeType": "all", "expansion": exp,
                                     "limit": PROJECT_LIST_PAGE_SIZE, "page": page},
                             timeout=120)
        except Exception as e:
            return None, str(e)
        if r.status_code != 200:
            return None, f"{r.status_code} {r.reason}: {r.text[:200]}"
        try:
            return r.json(), ""
        except Exception as e:
            return None, f"json parse: {e}"

    # ⚠ /projects 는 /segments 와 응답 모양이 다르다 — {content, lastPage} envelope 가 아니라
    #   **bare JSON 배열**로 오는 경우가 있다. 둘 다 받도록 정규화한다.
    def _rows_of(body) -> list[dict]:
        if isinstance(body, list):
            return [x for x in body if isinstance(x, dict)]
        if isinstance(body, dict):
            return [x for x in (body.get("content") or []) if isinstance(x, dict)]
        return []

    def _is_last(body, rows: list[dict]) -> bool:
        if isinstance(body, dict) and "lastPage" in body:
            return bool(body["lastPage"])
        return len(rows) < PROJECT_LIST_PAGE_SIZE   # envelope 없으면 짧은 페이지 = 끝

    # page 0 — definition 포함으로 먼저 시도
    exp = base_exp + ",definition"
    data, err = _fetch(0, exp)
    if data is None:
        print(f"  WARN: /projects (definition 포함) 실패 — {err}")
        exp = base_exp
        data, err = _fetch(0, exp)
    if data is None:
        print(f"ERROR: /projects 목록 실패 — {err}")
        return [], False

    rows0 = _rows_of(data)
    inline = any(isinstance(r0.get("definition"), dict) for r0 in rows0)
    if not inline and exp != base_exp:
        # definition 이 안 왔다 → 불필요한 expansion 빼고 다시 (트래픽 낭비 방지)
        exp = base_exp
        data, err = _fetch(0, exp)
        if data is None:
            print(f"ERROR: /projects 목록 실패 — {err}")
            return [], False
        rows0 = _rows_of(data)

    items: list[dict] = list(rows0)
    hit_cap = True
    if _is_last(data, rows0):
        hit_cap = False
    else:
        for page in range(1, PROJECT_LIST_MAX_PAGES):
            d, err = _fetch(page, exp)
            if d is None:
                print(f"  WARN: /projects page {page} 실패 — {err}")
                hit_cap = False
                break
            rows = _rows_of(d)
            if not rows:
                hit_cap = False
                break
            items.extend(rows)
            if _is_last(d, rows):
                hit_cap = False
                break
    # 조용한 절단 방지 — 페이지 상한에 걸려 끝난 경우 경고
    if hit_cap:
        print(f"  ⚠️ 프로젝트 목록 페이지 상한 {PROJECT_LIST_MAX_PAGES:,} 도달 — 뒤쪽 프로젝트는 "
              f"못 봤을 수 있습니다 (누적 {len(items):,}건). PROJECT_LIST_MAX_PAGES 를 올리세요.")
    return items, inline


def _fetch_project_definition(headers: dict, gcid: str, pid: str) -> dict:
    """GET /projects/{id} — definition 포함. 실패해도 raise 안 하고 error 키로 돌려준다."""
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{pid}"
    try:
        r = requests.get(url, headers=headers,
                         params={"expansion": "definition,ownerFullName,modifiedDate,name"},
                         timeout=120)
    except Exception as e:
        return {"id": pid, "error": str(e)}
    if r.status_code != 200:
        return {"id": pid, "error": f"{r.status_code} {r.reason}"}
    try:
        return r.json()
    except Exception as e:
        return {"id": pid, "error": f"json parse: {e}"}


def _extract_segment_ids_from_project(project: dict) -> set[str]:
    """프로젝트 JSON 을 통째로 직렬화해 segment id 패턴을 쓸어담는다.

    구조 walk(segmentGroups / columnTree / staticRows) 보다 **넓게** 잡는다 —
    패널 필터·드롭다운·breakdown 등 구조 walk 가 놓치는 자리까지 포함되기 때문.
    여기서는 "쓰이는가" 만 알면 되고 어느 테이블인지는 불필요하다.
    (구조 walk 와 대조 검증 완료 — 두 방식 결과 동일)
    """
    try:
        body = json.dumps(project, ensure_ascii=False)
    except Exception:
        return set()
    return set(SEG_ID_IN_JSON_RE.findall(body))


def _cache_scope_key(owner_ids: set[str], all_projects: bool) -> str:
    """캐시는 **스캔 범위가 정확히 같을 때만** 재사용 — 범위가 다르면 의미가 달라진다."""
    return "ALL" if all_projects else "OWNER:" + ",".join(sorted(owner_ids))


def _load_project_index_cache(scope: str) -> dict[str, list[dict]] | None:
    if not PROJECT_INDEX_CACHE.exists():
        return None
    try:
        blob = json.loads(PROJECT_INDEX_CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  WARN: 캐시 읽기 실패 — {e} (새로 수집)")
        return None
    if blob.get("scope") != scope:
        return None
    try:
        built = datetime.strptime(blob.get("built_at", ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    age_h = (datetime.now() - built).total_seconds() / 3600
    if age_h > PROJECT_CACHE_HOURS:
        print(f"  캐시 만료 ({age_h:.1f}h > {PROJECT_CACHE_HOURS}h) — 새로 수집합니다.")
        return None
    idx = blob.get("index")
    if not isinstance(idx, dict):
        return None
    print(f"  ♻ 캐시 재사용 — 프로젝트 {blob.get('project_count', '?')}개 / "
          f"세그 {len(idx):,}개, {age_h:.1f}h 전 수집 ({PROJECT_INDEX_CACHE.name}). "
          f"새로 받으려면 --refresh-projects")
    return idx


def build_project_usage_index(headers: dict, gcid: str, owner_ids: set[str],
                              all_projects: bool = False,
                              refresh: bool = False,
                              assume_yes: bool = False) -> dict[str, list[dict]]:
    """segment_id → [{id, name, owner_id}, ...] 역인덱스."""
    scope = _cache_scope_key(owner_ids, all_projects)
    if not refresh:
        cached = _load_project_index_cache(scope)
        if cached is not None:
            return cached

    print("  프로젝트 목록 수집 중 (GET /projects) ...")
    projects, inline = _list_projects(headers, gcid)
    if not projects:
        print("  ⚠️ 프로젝트를 하나도 못 받았습니다 — 사용처 컬럼은 빈값으로 남습니다.")
        return {}
    print(f"    전체 {len(projects):,}건 "
          f"(API 계정이 볼 수 있는 프로젝트 = 본인 소유 + 공유받은 것)")

    if all_projects:
        targets = list(projects)
    else:
        targets = [p for p in projects if _project_owner_id(p) in owner_ids]
        print(f"    대상 {len(targets):,}건 (owner 일치분만 — 전체를 보려면 --all-projects)")
    if not targets:
        print("    → 대상 프로젝트 0건. 인덱스 빈 채로 진행합니다.")
        return {}

    # 큰 스캔은 몇 시간짜리라 조용히 시작하지 않는다 (--all-projects 오타 방어)
    if not inline and len(targets) > PROJECT_SCAN_CONFIRM_OVER and not assume_yes:
        eta_min = len(targets) / PROJECT_DEF_RATE_PER_SEC / 60
        print(f"    ⚠️ definition 을 {len(targets):,}건 개별 GET 해야 합니다 — "
              f"예상 {eta_min:,.0f}분 (실측 {PROJECT_DEF_RATE_PER_SEC}건/s @ 워커 {PROJECT_DEF_WORKERS}).")
        try:
            ans = input("    계속할까요? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "y":
            print("    → 중단. 사용처 컬럼은 빈값으로 남습니다. (--yes 로 확인 생략 가능)")
            return {}

    index: dict[str, list[dict]] = {}

    def _add(proj: dict, meta: dict) -> None:
        for sid in _extract_segment_ids_from_project(proj):
            index.setdefault(sid, []).append(meta)

    metas = [{"id": p.get("id", ""), "name": p.get("name", ""),
              "owner_id": _project_owner_id(p)} for p in targets]

    if inline:
        print("    ✓ /projects 목록이 definition 을 같이 줌 — 프로젝트별 GET 생략")
        for p, meta in zip(targets, metas):
            _add(p, meta)
    else:
        print(f"    definition 개별 조회 — {len(metas):,}건 (워커 {PROJECT_DEF_WORKERS})")
        t0 = time.time()
        step = max(1, min(PROGRESS_EVERY, max(1, len(metas) // 10)))
        fail = 0
        with ThreadPoolExecutor(max_workers=PROJECT_DEF_WORKERS) as ex:
            fetched = ex.map(lambda m: _fetch_project_definition(headers, gcid, m["id"]), metas)
            for i, (meta, full) in enumerate(zip(metas, fetched), 1):
                if full.get("error"):
                    fail += 1
                else:
                    _add(full, meta)
                if i % step == 0 or i == len(metas):
                    _progress("PROJECT", i, len(metas), t0)
        if fail:
            print(f"    ⚠️ definition 조회 실패 {fail:,}건 — 그만큼 사용처를 못 봤습니다.")

    try:
        PROJECT_INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PROJECT_INDEX_CACHE.write_text(json.dumps({
            "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scope": scope,
            "project_count": len(targets),
            "index": index,
        }, ensure_ascii=False), encoding="utf-8")
        print(f"    캐시 저장: {PROJECT_INDEX_CACHE}")
    except Exception as e:
        print(f"    WARN: 캐시 저장 실패 — {e}")

    print(f"    인덱스 완성: 세그 {len(index):,}개가 어딘가에서 쓰이고 있음")
    return index


def _format_project_cells(hits: list[dict], seg_owner_id: str) -> tuple[str, str, str]:
    """(project_count, project_ids, project_names) — 같은 owner 프로젝트를 앞으로 정렬."""
    if not hits:
        return "0", "", ""
    so = str(seg_owner_id or "")
    ordered = sorted(hits, key=lambda h: (0 if h.get("owner_id") == so else 1,
                                          h.get("name", "") or ""))
    total = len(ordered)
    shown = ordered[:PROJECT_IDS_MAX] if PROJECT_IDS_MAX > 0 else ordered
    ids = ";".join(h.get("id", "") or "" for h in shown)
    # 이름 안 ';' 는 ',' 로 — 구분자 모호성 제거
    names = ";".join((h.get("name", "") or "").replace(";", ",") for h in shown)
    if PROJECT_IDS_MAX > 0 and total > len(shown):
        more = f"(+{total - len(shown)} more)"
        ids += f";{more}"
        names += f";{more}"
    return str(total), ids, names


# ═══════════════════════════════════════════════════════════════════
# 진행률 출력 (대량 조회 시 CSV/DSL 작성이 오래 걸려 체감용)
# ═══════════════════════════════════════════════════════════════════

def _fmt_dur(sec: float) -> str:
    """초 → '1h02m' / '3m20s' / '12s'"""
    sec = int(max(sec, 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _progress(label: str, done: int, total: int, t0: float) -> None:
    """'CSV 1,200/50,000 (2.4%) — 경과 12s / 남은 예상 8m20s' 형태 한 줄."""
    if total <= 0:
        return
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed > 0 else 0.0
    eta = (total - done) / rate if rate > 0 else 0.0
    print(f"    {label} {done:,}/{total:,} ({done / total * 100:.1f}%) — "
          f"경과 {_fmt_dur(elapsed)} / 남은 예상 {_fmt_dur(eta)}", flush=True)


# ═══════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    # Windows cp949 콘솔에서도 em dash / 한글 안 깨지게
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="세그먼트 ID 조회 / 이름 키워드 / owner 검색 → CSV + DSL 구조 파일 출력"
    )
    parser.add_argument("ids", nargs="*", help="segment ID(s)")
    parser.add_argument("--from-file", help="segment ID 목록 파일 (한 줄에 하나)")
    parser.add_argument("--search", nargs="+", default=None,
                        help="세그먼트 이름 키워드 검색 (여러 개 박으면 모두 AND). "
                             "공백 구분 (콤마 X). 각 키워드는 이름(+설명)에 **연속 substring** 으로 매칭 "
                             "(공백 포함). 예: --search \"[us] p\" \"visit\"  /  --search \"[CAMPAIGN NAME]\" \"US_CC\"")
    parser.add_argument("--owner", nargs="+", default=None, metavar="OWNER",
                        help="owner 기준 검색 — numeric loginId / 이메일 / 이름 부분일치. "
                             "여러 개면 OR. --search 와 같이 주면 AND. "
                             "단독 사용 시 회사 전체 페이징이라 느림 → --rsid 병행 권장")
    parser.add_argument("--rsid", default="", help="검색 시 RSID 필터 (선택)")
    parser.add_argument("--limit", type=int, default=SEARCH_RESULT_LIMIT,
                        help=f"검색 결과 최대 건수 (기본 SEARCH_RESULT_LIMIT={SEARCH_RESULT_LIMIT})")
    # ─── 날짜 필터 (검색 결과에만 적용 — ID 직접 지정 모드는 필터 안 함) ───
    #     after 만=이후(>=), before 만=이전(<=), 둘 다=두 날짜 사이. 서로 다른 필드끼리는 AND.
    parser.add_argument("--created-after", default="", metavar="YYYY-MM-DD",
                        help="이 날짜 이후(>=) **생성**된 세그만 (createDate 기준)")
    parser.add_argument("--created-before", default="", metavar="YYYY-MM-DD",
                        help="이 날짜 이전(<=) 생성된 세그만")
    parser.add_argument("--modified-after", default="", metavar="YYYY-MM-DD",
                        help="이 날짜 이후(>=) 수정된 세그만 (modified — 이름/설명 변경도 포함)")
    parser.add_argument("--modified-before", default="", metavar="YYYY-MM-DD",
                        help="이 날짜 이전(<=) 수정된 세그만")
    parser.add_argument("--definition-modified-after", default="", metavar="YYYY-MM-DD",
                        help="이 날짜 이후(>=) **정의(로직)** 가 바뀐 세그만 (definitionLastModified). "
                             "이름/설명만 고친 건 안 잡힘. 값 없는 옛 세그는 제외됨")
    parser.add_argument("--definition-modified-before", default="", metavar="YYYY-MM-DD",
                        help="이 날짜 이전(<=) 정의가 바뀐 세그만")
    parser.add_argument("--accessed-after", default="", metavar="YYYY-MM-DD",
                        help="이 날짜 이후(>=) **사용**된 세그만 (recentRecordedAccess)")
    parser.add_argument("--accessed-before", default="", metavar="YYYY-MM-DD",
                        help="이 날짜 이전(<=) 사용된 세그만 — 미사용 세그 정리에 유용")
    # ─── 프로젝트 사용처 (세그 → 그 세그를 쓰는 project) ───
    parser.add_argument("--with-projects", action="store_true",
                        help="세그별 사용 프로젝트 컬럼 추가 "
                             "(project_count / project_ids / project_names, ';' 구분). "
                             "AA 에 역방향 API 가 없어 /projects 를 훑어 인덱스를 만든다")
    parser.add_argument("--all-projects", action="store_true",
                        help="(--with-projects 와 함께) owner 제한 없이 전 프로젝트 스캔. "
                             "⚠ 프로젝트가 수만 건이면 수 시간 걸린다. "
                             "기본은 PROJECTS_OWNER_DEFAULT / --projects-owner 로 좁힌다")
    parser.add_argument("--projects-owner", nargs="+", default=None, metavar="OWNER",
                        help="(--with-projects 와 함께) 스캔할 **프로젝트 owner** 지정 "
                             "(loginId / 이메일 / 이름 부분일치, 여러 명이면 OR). "
                             "미지정 시 상단 PROJECTS_OWNER_DEFAULT. 세그 owner 와는 무관하게 동작한다")
    parser.add_argument("--refresh-projects", action="store_true",
                        help="(--with-projects 와 함께) 프로젝트 인덱스 캐시를 무시하고 새로 수집")
    parser.add_argument("--yes", action="store_true",
                        help=f"대량 스캔(>{PROJECT_SCAN_CONFIRM_OVER:,}건) 확인 프롬프트 생략")
    args = parser.parse_args()

    if (args.all_projects or args.refresh_projects or args.projects_owner) and not args.with_projects:
        print("  WARN: --all-projects / --refresh-projects / --projects-owner 는 --with-projects 와 "
              "같이 써야 의미가 있습니다 — 무시합니다.")
    if args.all_projects and args.projects_owner:
        print("  WARN: --all-projects 와 --projects-owner 가 같이 왔습니다 "
              "— --all-projects 가 우선(전체 스캔)이라 --projects-owner 는 무시합니다.")

    # 날짜 옵션 형식 검증 (YYYY-MM-DD)
    for label, val in (("--created-after", args.created_after),
                       ("--created-before", args.created_before),
                       ("--modified-after", args.modified_after),
                       ("--modified-before", args.modified_before),
                       ("--definition-modified-after", args.definition_modified_after),
                       ("--definition-modified-before", args.definition_modified_before),
                       ("--accessed-after", args.accessed_after),
                       ("--accessed-before", args.accessed_before)):
        if val:
            try:
                datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                print(f"ERROR: {label} 형식은 YYYY-MM-DD 여야 합니다 (받은 값: {val!r})")
                return 1

    # ID 수집
    seg_ids: list[str] = list(args.ids)
    if args.from_file:
        fp = Path(args.from_file)
        if not fp.exists():
            print(f"ERROR: 파일 없음: {fp}")
            return 1
        for line in fp.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                seg_ids.append(stripped)

    search_mode = bool(args.search or args.owner)

    if not seg_ids and not search_mode:
        print("ERROR: 세그먼트 ID 또는 --search 키워드 / --owner 가 필요합니다.")
        print("  python segment_lookup.py sXXXXXXXXX_abc123")
        print("  python segment_lookup.py --from-file ids.txt")
        print("  python segment_lookup.py --search \"campaign\" --rsid sscompany_name4mstglobal")
        print("  python segment_lookup.py --owner YOUR_LOGIN_ID")
        return 1

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    if search_mode:
        if args.search:
            kw_disp = " AND ".join(repr(k) for k in args.search) if isinstance(args.search, list) else repr(args.search)
        else:
            kw_disp = "(키워드 없음 — owner 만)"
        print(f"[{requested_at}] segment search — {kw_disp}")
        if args.owner:
            print(f"  owner filter: {' OR '.join(repr(o) for o in args.owner)}")
        if args.rsid:
            print(f"  RSID filter: {args.rsid}")
    else:
        print(f"[{requested_at}] segment lookup — {len(seg_ids)}개 조회")
    print(f"  Company: {COMPANY_ID}")
    print()

    # 인증
    print("Authenticating ...")
    headers, gcid = _load_auth_headers()
    _set_daterange_auth(headers, gcid)   # decompile 시 datetime-interval-ref → Date Range name fetch
    print()

    # --owner 해석 — GET /users 로 loginId 확정 (뒤 owner 보강에서도 같은 캐시 재사용)
    owner_ids: set[str] = set()
    if args.owner:
        print("owner 해석 중 (GET /users) ...")
        user_map_pre = _load_user_map(headers, gcid)
        owner_ids, owner_log = _resolve_owner_ids(user_map_pre, args.owner)
        for line in owner_log:
            print(line)
        if not owner_ids:
            print("ERROR: --owner 로 해석된 사용자가 없습니다. loginId / 이메일 / 이름을 확인하세요.")
            return 1
        print(f"  → 대상 owner {len(owner_ids)}명\n")

    # 날짜 필터 묶음 (DATE_FIELDS 키 기준) — 값이 다 비면 필터 미적용
    date_filters = {
        "created": (args.created_after, args.created_before),
        "modified": (args.modified_after, args.modified_before),
        "definition_modified": (args.definition_modified_after, args.definition_modified_before),
        "accessed": (args.accessed_after, args.accessed_before),
    }

    # 조회
    results: list[dict] = []
    if search_mode:
        results = _search_segments(headers, gcid, args.search,
                                   rsid=args.rsid, limit=args.limit,
                                   date_filters=date_filters,
                                   owner_ids=owner_ids)
        print(f"  검색 결과: {len(results):,}건")
        if LIST_RESULT_NAMES:
            for r in results:
                print(f"    {r['segment_id']}  {r['name']}")
    else:
        for i, seg_id in enumerate(seg_ids):
            print(f"  [{i+1}/{len(seg_ids)}] {seg_id} ...", end=" ")
            info = _lookup_segment(headers, gcid, seg_id)
            results.append(info)
            if info["error"]:
                print(f"FAIL — {info['error'][:60]}")
            else:
                print(f"OK — {info['name']}")

    print()

    # owner 이름/이메일 보강 — GET /users 직접 조회 (외부 CSV 의존 없음)
    user_map = _load_user_map(headers, gcid)
    if user_map:
        _enrich_owner_info(results, user_map)
        print(f"  owner 보강(/users): {len(user_map)}명")
    print()

    # 프로젝트 사용처 인덱스 (--with-projects) — CSV 쓰기 직전에 구축
    project_index: dict[str, list[dict]] = {}
    project_scan_label = ""
    if args.with_projects:
        # 스캔 대상 프로젝트 owner 결정 — **세그 owner 와 무관**하다.
        #   ① --all-projects  → 전체
        #   ② --projects-owner → 지정한 사람
        #   ③ PROJECTS_OWNER_DEFAULT → 상수에 박아둔 사람 (보통 본인)
        #   ④ 상수가 빈 값이면 → 결과 세그들의 owner
        scan_owner_ids: set[str] = set()
        if not args.all_projects:
            specs = args.projects_owner or (
                [PROJECTS_OWNER_DEFAULT] if PROJECTS_OWNER_DEFAULT.strip() else [])
            if specs:
                scan_owner_ids, proj_owner_log = _resolve_owner_ids(user_map, specs)
                src = "--projects-owner" if args.projects_owner else "PROJECTS_OWNER_DEFAULT"
                print(f"프로젝트 스캔 대상 owner ({src}):")
                for line in proj_owner_log:
                    print(line)
                if not scan_owner_ids:
                    print(f"ERROR: {src} 로 해석된 사용자가 없습니다. loginId / 이메일 / 이름을 확인하세요.")
                    return 1
            else:
                # 상수도 비고 옵션도 없음 → 결과 세그들의 owner 기준
                scan_owner_ids = {str(r.get("owner_id") or "") for r in results if r.get("owner_id")}
                print(f"프로젝트 스캔 대상 owner (세그 owner 기준): "
                      f"{', '.join(sorted(scan_owner_ids)) or '(없음)'}")
                if not scan_owner_ids:
                    print("  WARN: 세그 owner 를 못 정해 owner 제한을 걸 수 없습니다 "
                          "— 전체 프로젝트를 스캔합니다.")
                    args.all_projects = True
        project_scan_label = ("전체 프로젝트를" if args.all_projects
                              else f"owner {', '.join(sorted(scan_owner_ids))} 의 프로젝트만")
        print("프로젝트 사용처 인덱스 구축 (--with-projects)")
        project_index = build_project_usage_index(
            headers, gcid, scan_owner_ids,
            all_projects=args.all_projects, refresh=args.refresh_projects,
            assume_yes=args.yes)
        print()

    # CSV 출력 — lookup/ 하위
    LOOKUP_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = LOOKUP_DIR / f"{RESULT_PREFIX}{timestamp}.csv"
    total_n = len(results)
    print(f"CSV 작성 — {total_n:,}건 (진행률 {PROGRESS_EVERY}건 단위)")
    t_csv = time.time()
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["segment_id", "name", "owner_id", "owner_name", "owner_email", "rsid",
                     "created", "modified", "definition_last_modified", "recent_access",
                     "modified_by_id", "description", "tags",
                     "project_count", "project_ids", "project_names",
                     "structure", "error"])
        for i, r in enumerate(results, 1):
            if i % PROGRESS_EVERY == 0 or i == total_n:
                _progress("CSV", i, total_n, t_csv)
            # structure: decompiled DSL을 한 줄로
            structure = ""
            if r["definition"]:
                try:
                    dsl_text = decompile_definition(r["definition"])
                    # CSV 안전: 쌍따옴표→작은따옴표, 줄바꿈→ " | "
                    structure = dsl_text.replace('"', "'").replace("\n", " | ")
                except Exception:
                    structure = "(decompile error)"
            # 사용 프로젝트 — --with-projects 안 켜면 세 칸 모두 빈값
            if args.with_projects:
                p_cnt, p_ids, p_names = _format_project_cells(
                    project_index.get(r["segment_id"], []), r.get("owner_id", ""))
            else:
                p_cnt = p_ids = p_names = ""
            w.writerow([
                r["segment_id"], r["name"], r["owner_id"], r["owner_name"],
                r["owner_email"], r["rsid"], r.get("created", ""), r.get("modified", ""),
                r.get("definition_last_modified", ""), r.get("recent_access", ""),
                r.get("modified_by_id", ""), r["description"],
                r["tags"], p_cnt, p_ids, p_names, structure, r["error"],
            ])
    print(f"CSV: {csv_path}  ({_fmt_dur(time.time() - t_csv)})")

    # DSL 출력 — lookup/ 하위
    dsl_path = LOOKUP_DIR / f"{RESULT_PREFIX}{timestamp}.dsl"
    print(f"DSL 작성 — {total_n:,}건 (진행률 {PROGRESS_EVERY}건 단위)")
    t_dsl = time.time()
    dsl_blocks: list[str] = []
    for i, r in enumerate(results, 1):
        if i % PROGRESS_EVERY == 0 or i == total_n:
            _progress("DSL", i, total_n, t_dsl)
        if r["definition"] is None:
            continue
        try:
            tag_list = [t.strip() for t in r["tags"].split(",") if t.strip()] if r["tags"] else []
            block = format_dsl_block(
                name=r["name"],
                description=r["description"],
                rsid=r["rsid"],
                tags=tag_list,
                definition=r["definition"],
            )
            dsl_blocks.append(block)
        except Exception as e:
            print(f"  WARN: {r['segment_id']} decompile 실패 — {e}")

    if dsl_blocks:
        dsl_path.write_text("\n\n".join(dsl_blocks) + "\n", encoding="utf-8")
        print(f"DSL 구조: {dsl_path}  ({_fmt_dur(time.time() - t_dsl)}, {len(dsl_blocks):,} block)")
        print(f"  → aa_create_segment_v2.py --input {dsl_path.name} 으로 재사용 가능")
    else:
        print("DSL 구조: (유효한 definition 없음, 파일 미생성)")

    # 콘솔 요약
    ok = sum(1 for r in results if not r["error"])
    fail = sum(1 for r in results if r["error"])
    print(f"\n[summary] 성공: {ok}, 실패: {fail}")

    # 프로젝트 사용처 — 조용한 오해 방지용 경고를 항상 찍는다
    if args.with_projects:
        used = sum(1 for r in results if project_index.get(r["segment_id"]))
        print(f"[projects] 사용처 발견: {used:,} / {len(results):,}건")
        if not args.all_projects:
            print(f"  ⚠️ project_count=0 은 '미사용'이 아닙니다 — {project_scan_label} "
                  "스캔했습니다. 다른 사람 프로젝트까지 보려면 --all-projects, "
                  "특정인만 보려면 --projects-owner.")
        print("  ⚠️ /projects 는 API 계정이 볼 수 있는 프로젝트만 반환합니다 "
              "(본인 소유 + 공유받은 것). 안 보이는 프로젝트의 사용분은 못 잡습니다.")

    # 성공한 것들 콘솔 출력 — 대량 조회 시 수만 줄이 되므로 소량일 때만
    if ok > DETAIL_PRINT_MAX:
        print(f"  (상세 구조 출력 생략 — {ok:,}건 > DETAIL_PRINT_MAX={DETAIL_PRINT_MAX}. "
              f"위 CSV/DSL 파일을 보세요)")
    else:
        for r in results:
            if r["error"]:
                continue
            print(f"\n{'─' * 50}")
            print(f"  ID: {r['segment_id']}")
            print(f"  Name: {r['name']}")
            print(f"  Owner: {r['owner_name']} <{r['owner_email']}> ({r['owner_id']})")
            print(f"  RSID: {r['rsid']}")
            if r["tags"]:
                print(f"  Tags: {r['tags']}")
            if r["definition"]:
                print(f"  구조:")
                dsl = decompile_definition(r["definition"])
                for line in dsl.splitlines():
                    print(f"    {line}")

    return 0


if __name__ == "__main__":
    sys.exit(main())