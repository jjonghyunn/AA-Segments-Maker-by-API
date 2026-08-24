# aa_segment_lookup.py
# 2026-05-15  Jonghyun Park w/ Claude
# ── 변경 이력 (git 히스토리 스크럽됨 — 아래 changelog 가 변경 기록) ──
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
세그먼트 ID 리스트 → 기본 정보 CSV + DSL 구조 파일(.dsl) 출력.

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
"""
from __future__ import annotations

import argparse
import csv
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

# ─── 콘솔 출력 설정 ────────────────────────────────────────────────
PROGRESS_EVERY = 100                # 진행률을 몇 건마다 찍을지 (CSV/DSL 작성, 세그 GET)
DETAIL_PRINT_MAX = 20               # 결과가 이 건수 이하일 때만 건별 상세(구조/이름) 출력
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

RESULT_PREFIX = "segment_lookup_"

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


def _search_segments(headers: dict, gcid: str, keywords: list[str] | str,
                     rsid: str = "", limit: int = SEARCH_RESULT_LIMIT,
                     date_filters: dict[str, tuple[str, str]] | None = None) -> list[dict]:
    """GET /segments — 이름 키워드 검색. 결과를 _lookup_segment 포맷으로 반환.

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
    if not kw_list:
        return []
    match_kws = [k.lower() for k in kw_list]          # 첫 키워드 포함 전부 substring 매칭
    url = f"https://analytics.adobe.io/api/{gcid}/segments"
    base_params: dict[str, Any] = {
        "expansion": "definition,name,description,owner,tags,reportSuiteName,modified,"
                     "createdDate,definitionLastModified,recentRecordedAccess",
        "includeType": "all",
    }
    prefilter = _pick_prefilter_word(kw_list)         # 서버 volume 축소용 (매칭 수단 아님)
    if prefilter:
        base_params["name"] = prefilter
    if rsid:
        base_params["rsids"] = rsid

    # paging — 매칭 후보를 전부 받음(중간에 안 자름). AA API max page size = 1000.
    PAGE_SIZE = 1000
    MAX_PAGES = 50
    items: list[dict] = []
    for page in range(MAX_PAGES):
        params = {**base_params, "limit": PAGE_SIZE, "page": page}
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            print(f"ERROR: 검색 실패 page {page} — {r.status_code} {r.reason}: {r.text[:200]}")
            break
        data = r.json()
        rows = data.get("content", [])
        if not rows:
            break
        items.extend(rows)
        if bool(data.get("lastPage")) or len(rows) < PAGE_SIZE:
            break

    # client-side AND — 모든 키워드를 substring 으로 (첫 키워드 포함)
    def _hay(it: dict) -> str:
        s = it.get("name", "") or ""
        if SEARCH_MATCH_IN_DESCRIPTION:
            s = s + " " + (it.get("description") or "")
        return s.lower()
    matched = [it for it in items if all(kw in _hay(it) for kw in match_kws)]

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
# Runner
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# 진행률 출력 (대량 조회 시 GET/CSV/DSL 이 오래 걸려 체감용)
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


def main() -> int:
    # Windows cp949 콘솔에서도 em dash / 한글 안 깨지게
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="세그먼트 ID 조회 또는 이름 키워드 검색 → CSV + DSL 구조 파일 출력"
    )
    parser.add_argument("ids", nargs="*", help="segment ID(s)")
    parser.add_argument("--from-file", help="segment ID 목록 파일 (한 줄에 하나)")
    parser.add_argument("--search", nargs="+", default=None,
                        help="세그먼트 이름 키워드 검색 (여러 개 박으면 모두 AND). "
                             "공백 구분 (콤마 X). 각 키워드는 이름(+설명)에 **연속 substring** 으로 매칭 "
                             "(공백 포함). 예: --search \"[us] p\" \"visit\"  /  --search \"[CAMPAIGN NAME]\" \"US_CC\"")
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
    args = parser.parse_args()

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

    search_mode = bool(args.search)

    if not seg_ids and not search_mode:
        print("ERROR: 세그먼트 ID 또는 --search 키워드가 필요합니다.")
        print("  python segment_lookup.py sXXXXXXXXX_abc123")
        print("  python segment_lookup.py --from-file ids.txt")
        print("  python segment_lookup.py --search \"campaign\" --rsid sscompany_name4mstglobal")
        return 1

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    if search_mode:
        kw_disp = " AND ".join(repr(k) for k in args.search) if isinstance(args.search, list) else repr(args.search)
        print(f"[{requested_at}] segment search — {kw_disp}")
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
                                   date_filters=date_filters)
        print(f"  검색 결과: {len(results)}건")
        if LIST_RESULT_NAMES:
            for r in results:
                print(f"    {r['segment_id']}  {r['name']}")
    else:
        n_ids = len(seg_ids)
        verbose_ids = n_ids <= DETAIL_PRINT_MAX      # 소량일 때만 건별 한 줄
        t_get = time.time()
        for i, seg_id in enumerate(seg_ids, 1):
            if verbose_ids:
                print(f"  [{i}/{n_ids}] {seg_id} ...", end=" ")
            info = _lookup_segment(headers, gcid, seg_id)
            results.append(info)
            if verbose_ids:
                print(f"FAIL — {info['error'][:60]}" if info["error"] else f"OK — {info['name']}")
            else:
                if info["error"]:                     # 실패는 건수 무관 항상 노출
                    print(f"  FAIL {seg_id} — {info['error'][:60]}")
                if i % PROGRESS_EVERY == 0 or i == n_ids:
                    _progress("GET", i, n_ids, t_get)

    print()

    # owner 이름/이메일 보강 — GET /users 직접 조회 (외부 CSV 의존 없음)
    user_map = _load_user_map(headers, gcid)
    if user_map:
        _enrich_owner_info(results, user_map)
        print(f"  owner 보강(/users): {len(user_map)}명")
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
                     "modified_by_id", "description", "tags", "structure", "error"])
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
            w.writerow([
                r["segment_id"], r["name"], r["owner_id"], r["owner_name"],
                r["owner_email"], r["rsid"], r.get("created", ""), r.get("modified", ""),
                r.get("definition_last_modified", ""), r.get("recent_access", ""),
                r.get("modified_by_id", ""), r["description"],
                r["tags"], structure, r["error"],
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