# aa_create_segment_v2.3.py
# 2026-05-15  Jonghyun Park w/ Claude
# updated: 2026-05-15  — v2.1 기반. --input 상대경로일 때 스크립트 폴더 기준 fallback 추가
#                       (cwd 가 어디든 segment_maker 폴더의 segments.csv 자동 발견)
# updated: 2026-05-18  — segment-ref cache patch (sequence-prefix 변환) +
#                       --update-or-create (mixed PUT/POST) +
#                       --lookup-by-name (segment_id 빈 row 의 자동 채움 via 폴더의 최신 segment_lookup csv) +
#                       result csv 의 Action 컬럼 (PUT/POST 구분)
# updated: 2026-05-22  — --lookup-by-name 의 source 위치를 같은 폴더의 lookup/ 하위로 변경 (segment_lookup_*.csv 가 lookup/ 로 이동됨)
# updated: 2026-05-26  — v2.3: (1) DSL preprocess 추가 — [sequence-after] visitor( 같은 label+scope-keyword 토큰을 visit( 으로 strip → Delayed Purchase ParseError 해결. (2) v2.py 의존성 제거 — v2 의 parser/compiler/auth 코드 inline (self-contained). (3) OWNER_IMS_USER_ID + OWNER_LOGIN 빈 값 default (다른 사람 fork 시 자기 정보로 채움). OWNER_ID 는 유지.
# updated: 2026-05-26  — _lift_inner_hit_into_visit_root 후처리 추가. visit/visitor scope segment 가 AA server-side simplification (outer-visit + 단일 inner-hit no_desc wrap → hit-scope 로 합침) 에 의해 hit scope 로 떨어지는 문제 fix. inner hit wrap 제거하고 outer.pred = inner.pred 직접 박아서 server 가 단일 wrap 패턴 simplify 못 하도록.
"""
CSV 입력 → AA 세그먼트 일괄 생성 또는 업데이트.

segment_lookup.py / input_csv_maker.py 결과 CSV(structure 칼럼 포함)를 입력으로 바로 사용 가능.
structure 칼럼의 " | " 구분 한 줄 구조를 파싱하여 AA JSON으로 변환.

v2.2 변경점 (vs v2.1):
  · --input 상대경로면 우선 cwd 기준 → 없으면 스크립트 폴더(segment_maker/) 기준으로 fallback.
    어디서 실행하든 segments.csv 가 segment_maker/ 안에 있으면 자동 인식.

사용법 (--input 비우면 폴더의 최신 segments_input_*.csv 자동 pick):
  python aa_create_segment_v2.2.py --input segments.csv                     # dry-run (CREATE)
  python aa_create_segment_v2.2.py --input segments.csv --apply             # 실제 POST (CREATE)
  python aa_create_segment_v2.2.py --update --apply                         # 실제 PUT (모두 update, segment_id 컬럼 모두 박혀야)
  python aa_create_segment_v2.2.py --update-or-create --apply               # mixed: id 있으면 PUT, 없으면 POST
       # ↑ --lookup-by-name 이 default True 라 segment_id 빈 row 는
       #   폴더의 segment_lookup_*.csv 에서 name 매칭으로 자동 채움.
       #   매칭되면 PUT (update), 없으면 POST (create). 가장 일반 운영 흐름.
  python aa_create_segment_v2.2.py --update-or-create --no-lookup-by-name --apply
       # ↑ lookup csv 무시하고 강제 POST. lookup csv 에 동일 name 있으면 경고만 출력.

CSV 필수 칼럼:
  · CREATE (POST)               — name, structure
  · UPDATE (PUT)                — segment_id, structure  (csv 마다 박혀 있어야)
  · MIXED (--update-or-create)  — name, structure  (segment_id 있으면 PUT, 없으면 POST 자동 분기)
CSV 선택 칼럼: description, rsid, tags

segment_id 모르거나 많아서 수동 박기 부담:
  · 미리 segment_lookup 으로 csv 받아두기:
      python aa_segment_lookup.py --search "[CAMPAIGN NAME]" --limit 500
      → segment_lookup_<ts>_<keyword>.csv 생성 (segment_id + name 박힘)
  · 그 후 v2.2 에 --lookup-by-name 옵션 → 자동 매칭 (수동 박기 불필요)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# v2에서 parser/compiler/auth 재사용
# ─── v2 (parser/compiler/auth) inline — self-contained ───
import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"

# ─── 본인 식별 (segment owner) ────────────────────────────────────
# OWNER_ID 에 numeric loginId 직접 지정하면 API lookup 생략.
# 팀원 loginId 목록 (add_segment_shares.py 기준):
#   YOUR_LOGIN_ID  user1   (User 1)
#   YOUR_LOGIN_ID  user2     (User 2)
#   YOUR_LOGIN_ID  user3  (User 3)
#   YOUR_LOGIN_ID  user4         (User 4)
#   YOUR_LOGIN_ID  user5     (User 5)
#   YOUR_LOGIN_ID  user6      (User 6)
#   YOUR_LOGIN_ID  user7   (User 7)
OWNER_ID: int | None = YOUR_LOGIN_ID   # 자기 numeric loginId (위 팀원 목록 참조). None 이면 IMS/LOGIN 으로 lookup
OWNER_IMS_USER_ID: str = ""        # 다른 사람 fork 시 자기 IMS user ID. 빈 값이면 OWNER_ID 사용
OWNER_LOGIN: str = ""              # 다른 사람 fork 시 자기 login email substring. 빈 값이면 OWNER_ID 사용
                                   # 셋 다 빈 값이면 owner 필드 빠짐 — AA 가 인증 user 자동 사용

# ─── 기본 RSID (segment별 rsid 미지정 시 사용) ───────────────────
DEFAULT_RSID = "sscompany_name4mstglobal"

# ─── 입력 파일 ─────────────────────────────────────────────────────
INPUT_FILE = "segments.dsl"


# ─── 입력 / 캐시 / lookup (v2.2 사용자 설정) ───

INPUT_CSV = "segments_input_260526_1653.csv" #global
# INPUT_CSV = "segments_input_260526_1313_us.csv"
# INPUT_CSV = "segments_input_260526_1657_scenario.csv"
# INPUT_CSV = "segments_from_ref_260519_1945_recomm15.csv"
# INPUT_CSV = "segments_from_ref_batch_us_hit_260520_1103.csv"
# INPUT_CSV = "segments_from_ref_batch_us_hit_260520_1325_hit_only_plus15.csv"
# INPUT_CSV = "segments_from_ref_batch_260520_1114.csv"
# INPUT_CSV = "segments_from_ref_batch_260520_1340.csv"

# segment-ref 캐시 파일명 suffix — 캠페인 / 환경 별로 분리 가능.
#   ""       → segment_ref_cache.json         (기본)
#   "global" → segment_ref_cache_global.json
#   "us"     → segment_ref_cache_us.json
#   콤마 구분 multi-cache 지원 → 모든 파일 merge load (앞 파일 우선, 첫 파일이 save target).
# --cache <name> argparse 로도 override 가능 (CLI 우선).
# 시나리오 csv (글로벌+US 섞임) 처리 시 → 글로벌·US evar 캐시 + ATC 캐시 모두 박아야
# delayed_purchase 의 ATC visit segment-ref 까지 inline 처리됨.
CACHE_NAME = "evar_global,evar_us,add_to_cart_global,add_to_cart_us"

# --lookup-by-name 시 활용할 lookup csv 파일명 (default).
# 빈 값이면 같은 폴더의 lookup/ 하위의 모든 segment_lookup_*.csv 자동 merge (사전순 reverse — 새 거 우선).
# 특정 파일 명시하면 그것만 사용. --lookup-csv argparse 로도 override 가능.
LOOKUP_CSV = ""   # 예: "lookup/segment_lookup_260518_1327_CAMPAIGN NAME.csv" 또는 "segment_lookup_260518_1327_CAMPAIGN NAME.csv" (LOOKUP_DIR 기준)


# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_CSV_PREFIX = "segment_v2_result_"

UI_URL_TEMPLATE = (
    "https://experience.adobe.com/#/@company_name/so:your_aa_company_id/"
    "analytics/spa/#/components/segments/edit/{seg_id}"
)

# ─── 변수 단축어 ──────────────────────────────────────────────────
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

# evar(\d+), prop(\d+), event(\d+) 패턴은 _resolve_variable()에서 처리

# ─── 연산자 매핑 ──────────────────────────────────────────────────
OPERATOR_MAP: dict[str, str] = {
    "=": "streq",
    "equals": "streq",
    "!=": "streq",              # + without
    "contains": "contains",
    "contains-any-of": "contains-any-of",
    "contains-all-of": "contains-all-of",
    "equals-any-of": "streq-in",
    "in": "streq-in",
    "starts-with": "starts-with",
    "ends-with": "ends-with",
    "matches": "matches-regex",
    "exists": "exists",
    ">": "gt",
    ">=": "ge",
    "<": "lt",
    "<=": "le",
}

# 항상 without wrapper가 붙는 연산자
ALWAYS_NEGATE = {"!="}

# 연산자 역매핑 (decompile용)
FUNC_TO_DSL: dict[str, str] = {
    "streq": "=",
    "contains": "contains",
    "contains-any-of": "contains-any-of",
    "contains-all-of": "contains-all-of",
    "streq-in": "in",
    "starts-with": "starts-with",
    "ends-with": "ends-with",
    "matches-regex": "matches",
    "exists": "exists",
    "gt": ">",
    "ge": ">=",
    "lt": "<",
    "le": "<=",
}

# context 매핑
CONTEXT_TO_SCOPE = {"hits": "hit", "visits": "visit", "visitors": "visitor"}
SCOPE_TO_CONTEXT = {"hit": "hits", "visit": "visits", "visitor": "visitors"}

# 리스트 값을 받는 연산자
LIST_OPERATORS = {"contains-any-of", "contains-all-of", "streq-in", "in", "equals-any-of"}

# 값이 없는 연산자
NO_VALUE_OPERATORS = {"exists"}

# 숫자 비교 연산자
NUMERIC_OPERATORS = {">", ">=", "<", "<=", "gt", "ge", "lt", "le"}

# 모든 유효 연산자 (파서 에러 메시지용)
ALL_OPERATORS = sorted(OPERATOR_MAP.keys())

# ═══════════════════════════════════════════════════════════════════
# 변수 해석
# ═══════════════════════════════════════════════════════════════════

_RE_EVAR = re.compile(r"^evar(\d+)$", re.IGNORECASE)
_RE_PROP = re.compile(r"^prop(\d+)$", re.IGNORECASE)
_RE_EVENT = re.compile(r"^event(\d+)$", re.IGNORECASE)


def _resolve_variable(name: str) -> tuple[str, str]:
    """변수 단축어 → (풀네임, val_func).
    val_func: "attr" (dimensions) or "event" (metrics)
    """
    if "/" in name:
        val_func = "event" if name.startswith("metrics/") else "attr"
        return name, val_func

    if name in VARIABLE_ALIASES:
        full = VARIABLE_ALIASES[name]
        val_func = "event" if full.startswith("metrics/") else "attr"
        return full, val_func

    m = _RE_EVAR.match(name)
    if m:
        return f"variables/evar{m.group(1)}", "attr"
    m = _RE_PROP.match(name)
    if m:
        return f"variables/prop{m.group(1)}", "attr"
    m = _RE_EVENT.match(name)
    if m:
        return f"metrics/event{m.group(1)}", "event"

    # 알 수 없으면 variables/ 접두사 붙여서 통과
    return f"variables/{name}", "attr"


def _reverse_variable(full_name: str) -> str:
    """풀네임 → 단축어 (decompile용)."""
    # 역매핑 dict 구축
    rev = {v: k for k, v in VARIABLE_ALIASES.items()}
    if full_name in rev:
        return rev[full_name]

    # evar/prop/event 패턴
    m = re.match(r"^variables/evar(\d+)$", full_name)
    if m:
        return f"evar{m.group(1)}"
    m = re.match(r"^variables/prop(\d+)$", full_name)
    if m:
        return f"prop{m.group(1)}"
    m = re.match(r"^metrics/event(\d+)$", full_name)
    if m:
        return f"event{m.group(1)}"

    # variables/ 접두사 제거
    if full_name.startswith("variables/"):
        return full_name[len("variables/"):]
    if full_name.startswith("metrics/"):
        return full_name[len("metrics/"):]
    return full_name


# ═══════════════════════════════════════════════════════════════════
# AST 노드
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ConditionNode:
    variable: str           # 풀네임: "variables/page"
    val_func: str           # "attr" or "event"
    operator: str           # AA func: "contains", "streq", ...
    value: Any              # str, list, int, float, None
    negated: bool = False   # True → without wrapper
    description: str = ""
    line: int = 0


@dataclass
class LogicalNode:
    op: str                 # "and" or "or"
    children: list = field(default_factory=list)  # ConditionNode | LogicalNode | ContainerNode


@dataclass
class SegmentRefNode:
    segment_id: str         # "sXXXXXXXXX_abc123"
    negated: bool = False
    line: int = 0


@dataclass
class SequenceNode:
    steps: list = field(default_factory=list)  # 각 step: ConditionNode | LogicalNode | ContainerNode


@dataclass
class RestrictionNode:
    # sequence THEN-step "WITHIN N <dim>" restriction (AA dimension-restriction node).
    # lookup renders 'WITHIN 1 page' -> rebuilt here into dimension-restriction.
    count: int
    limit: str
    attribute_name: str
    line: int = 0


@dataclass
class ContainerNode:
    context: str            # "hits", "visits", "visitors"
    description: str = ""   # 이름 지정 컨테이너
    pred: Any = None        # ConditionNode | LogicalNode | ContainerNode | SegmentRefNode | SequenceNode


# ═══════════════════════════════════════════════════════════════════
# DSL Parse Error
# ═══════════════════════════════════════════════════════════════════

class DSLParseError(Exception):
    def __init__(self, message: str, line: int = 0):
        self.line = line
        super().__init__(f"ParseError at line {line}: {message}" if line else f"ParseError: {message}")


# ═══════════════════════════════════════════════════════════════════
# Tokenizer
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Token:
    type: str       # SCOPE_OPEN, NAMED_SCOPE_OPEN, PAREN_CLOSE, AND, OR, NOT, THEN, CONDITION, SEGMENT_REF, NOT_OPEN
    value: str      # scope name for SCOPE/NAMED, condition text for CONDITION, segment_id for SEGMENT_REF
    scope: str = "" # "hit"/"visit"/"visitor" for scope tokens
    name: str = ""  # container name for NAMED_SCOPE_OPEN
    line: int = 0

_RE_NAMED_SCOPE = re.compile(r"^'([^']+)'!(hit|visit|visitor)\(\s*$")
_RE_SCOPE = re.compile(r"^(hit|visit|visitor)\(\s*$")
_RE_RESTRICTION = re.compile(r"^(WITHIN|AFTER)\s+(\d+)\s+(\S.*)$", re.IGNORECASE)


def _tokenize(text: str) -> list[Token]:
    """DSL 텍스트 → 토큰 리스트."""
    tokens: list[Token] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        lineno = i + 1
        raw = lines[i].strip()

        # 빈 줄, 주석 건너뛰기
        if not raw or raw.startswith("#"):
            i += 1
            continue

        # 이름 지정 컨테이너: 'Name'!hit(
        m = _RE_NAMED_SCOPE.match(raw)
        if m:
            tokens.append(Token("NAMED_SCOPE_OPEN", raw, scope=m.group(2), name=m.group(1), line=lineno))
            i += 1
            continue

        # 스코프 열기: hit(
        m = _RE_SCOPE.match(raw)
        if m:
            tokens.append(Token("SCOPE_OPEN", raw, scope=m.group(1), line=lineno))
            i += 1
            continue

        # 닫기: )
        if raw == ")":
            tokens.append(Token("PAREN_CLOSE", raw, line=lineno))
            i += 1
            continue

        # 독립 AND / OR / THEN
        if raw == "AND":
            tokens.append(Token("AND", raw, line=lineno))
            i += 1
            continue
        if raw == "OR":
            tokens.append(Token("OR", raw, line=lineno))
            i += 1
            continue
        if raw == "THEN":
            tokens.append(Token("THEN", raw, line=lineno))
            i += 1
            continue

        # WITHIN N <dim> / AFTER N <dim> -> sequence dimension-restriction step
        if _RE_RESTRICTION.match(raw):
            tokens.append(Token("RESTRICTION", raw, line=lineno))
            i += 1
            continue

        # NOT ( → NOT 그룹 시작
        if raw == "NOT (":
            tokens.append(Token("NOT_OPEN", raw, line=lineno))
            i += 1
            continue

        # AND ... / OR ... 으로 시작하는 줄
        condition_text = raw
        if raw.startswith("AND "):
            tokens.append(Token("AND", "AND", line=lineno))
            condition_text = raw[4:].strip()
        elif raw.startswith("OR "):
            tokens.append(Token("OR", "OR", line=lineno))
            condition_text = raw[3:].strip()

        if not condition_text:
            i += 1
            continue

        # AND/OR 뒤에 scope가 오는 경우: AND 'P29'!hit( 또는 OR hit(
        m2 = _RE_NAMED_SCOPE.match(condition_text)
        if m2:
            tokens.append(Token("NAMED_SCOPE_OPEN", condition_text, scope=m2.group(2), name=m2.group(1), line=lineno))
            i += 1
            continue
        m2 = _RE_SCOPE.match(condition_text)
        if m2:
            tokens.append(Token("SCOPE_OPEN", condition_text, scope=m2.group(1), line=lineno))
            i += 1
            continue

        # @segment_id 참조
        seg_ref_text = condition_text
        not_prefix_ref = False
        if seg_ref_text.startswith("NOT "):
            not_prefix_ref = True
            seg_ref_text = seg_ref_text[4:].strip()
        if seg_ref_text.startswith("@"):
            seg_id = seg_ref_text[1:].strip()
            # NOT 정보는 value에 prefix로 전달
            ref_val = f"NOT@{seg_id}" if not_prefix_ref else seg_id
            tokens.append(Token("SEGMENT_REF", ref_val, line=lineno))
            i += 1
            continue

        # 멀티라인 리스트 처리: [ 열렸는데 ] 안 닫혔으면 계속 읽기
        if "[" in condition_text and "]" not in condition_text:
            while i + 1 < len(lines):
                i += 1
                next_line = lines[i].strip()
                condition_text += " " + next_line
                if "]" in next_line:
                    break

        if condition_text:
            tokens.append(Token("CONDITION", condition_text, line=lineno))
        i += 1

    return tokens


# ═══════════════════════════════════════════════════════════════════
# 조건 파싱 (한 줄 → ConditionNode)
# ═══════════════════════════════════════════════════════════════════

def _parse_value(val_str: str) -> str | list | int | float | None:
    """값 문자열 → Python 객체."""
    val_str = val_str.strip()
    if not val_str:
        return None

    # 리스트: ["a", "b", "c"]
    if val_str.startswith("["):
        # JSON 파싱
        try:
            return json.loads(val_str)
        except json.JSONDecodeError:
            # 작은따옴표 → 큰따옴표로 변환 후 재시도
            converted = val_str.replace("'", '"')
            try:
                return json.loads(converted)
            except json.JSONDecodeError as e:
                raise DSLParseError(f"리스트 값 파싱 실패: {val_str} — {e}")

    # 따옴표 문자열
    if (val_str.startswith('"') and val_str.endswith('"')) or \
       (val_str.startswith("'") and val_str.endswith("'")):
        return val_str[1:-1]

    # 숫자
    try:
        if "." in val_str:
            return float(val_str)
        return int(val_str)
    except ValueError:
        pass

    # 따옴표 없는 문자열 (허용)
    return val_str


# 조건 줄 파싱 패턴
_OPERATORS_PATTERN = "|".join(
    re.escape(op) for op in sorted(OPERATOR_MAP.keys(), key=len, reverse=True)
)
_RE_CONDITION = re.compile(
    rf"^(NOT\s+)?(\S+)\s+({_OPERATORS_PATTERN})(?:\s+(.+))?$",
    re.IGNORECASE
)


def _parse_condition(text: str, line: int) -> ConditionNode:
    """조건 텍스트 → ConditionNode."""
    m = _RE_CONDITION.match(text.strip())
    if not m:
        raise DSLParseError(
            f"조건 파싱 실패: '{text}'\n"
            f"  형식: [NOT] 변수 연산자 값\n"
            f"  유효 연산자: {', '.join(ALL_OPERATORS)}",
            line=line,
        )

    not_prefix = bool(m.group(1))
    var_name = m.group(2)
    op_text = m.group(3).lower() if m.group(3) not in (">", ">=", "<", "<=", "=", "!=") else m.group(3)
    val_text = m.group(4)

    # 연산자 해석
    if op_text not in OPERATOR_MAP:
        raise DSLParseError(
            f"알 수 없는 연산자: '{op_text}' — 유효: {', '.join(ALL_OPERATORS)}",
            line=line,
        )
    aa_func = OPERATOR_MAP[op_text]

    # NOT 처리
    negated = not_prefix or (op_text in ALWAYS_NEGATE)

    # 변수 해석
    full_var, val_func = _resolve_variable(var_name)

    # 값 파싱
    if op_text in NO_VALUE_OPERATORS:
        value = None
    elif val_text is None:
        if op_text not in NO_VALUE_OPERATORS:
            raise DSLParseError(f"연산자 '{op_text}'에 값이 필요합니다", line=line)
        value = None
    else:
        value = _parse_value(val_text)

    return ConditionNode(
        variable=full_var,
        val_func=val_func,
        operator=aa_func,
        value=value,
        negated=negated,
        line=line,
    )


# ═══════════════════════════════════════════════════════════════════
# Parser (재귀 하강)
# ═══════════════════════════════════════════════════════════════════

class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def _expect(self, ttype: str) -> Token:
        t = self._peek()
        if t is None or t.type != ttype:
            line = t.line if t else 0
            raise DSLParseError(f"'{ttype}' 기대했지만 '{t.type if t else 'EOF'}' 발견", line=line)
        return self._advance()

    def parse(self) -> ContainerNode | LogicalNode | ConditionNode:
        result = self._parse_or()
        if self.pos < len(self.tokens):
            t = self.tokens[self.pos]
            raise DSLParseError(f"예상치 못한 토큰: '{t.value}'", line=t.line)
        return result

    def _parse_or(self):
        left = self._parse_and()
        while self._peek() and self._peek().type == "OR":
            self._advance()
            right = self._parse_and()
            if isinstance(left, LogicalNode) and left.op == "or":
                left.children.append(right)
            else:
                left = LogicalNode(op="or", children=[left, right])
        return left

    def _parse_and(self):
        left = self._parse_then()
        while self._peek() and self._peek().type == "AND":
            self._advance()
            right = self._parse_then()
            if isinstance(left, LogicalNode) and left.op == "and":
                left.children.append(right)
            else:
                left = LogicalNode(op="and", children=[left, right])
        return left

    def _parse_then(self):
        left = self._parse_unary()
        steps = [left]
        while self._peek() and self._peek().type == "THEN":
            self._advance()
            right = self._parse_unary()
            steps.append(right)
        if len(steps) == 1:
            return steps[0]
        return SequenceNode(steps=steps)

    def _parse_unary(self):
        t = self._peek()
        # NOT ( ... ) → 복합 NOT 그룹
        if t and t.type == "NOT_OPEN":
            self._advance()
            inner = self._parse_or()
            self._expect("PAREN_CLOSE")
            # LogicalNode를 without으로 감싸기 위해 negated 컨테이너로 반환
            return LogicalNode(op="not", children=[inner])
        if t and t.type == "NOT":
            self._advance()
            child = self._parse_unary()
            if isinstance(child, ConditionNode):
                child.negated = True
                return child
            if isinstance(child, SegmentRefNode):
                child.negated = True
                return child
            raise DSLParseError("NOT은 조건 앞에만 사용 가능합니다 (복합 조건은 NOT ( ... ) 사용)", line=t.line)
        return self._parse_primary()

    def _parse_primary(self):
        t = self._peek()
        if t is None:
            raise DSLParseError("예상치 못한 입력 종료")

        # 스코프 컨테이너
        if t.type in ("SCOPE_OPEN", "NAMED_SCOPE_OPEN"):
            self._advance()
            context = SCOPE_TO_CONTEXT[t.scope]
            desc = t.name if t.type == "NAMED_SCOPE_OPEN" else ""
            inner = self._parse_or()
            self._expect("PAREN_CLOSE")
            return ContainerNode(context=context, description=desc, pred=inner)

        # 세그먼트 참조: @segment_id
        if t.type == "SEGMENT_REF":
            self._advance()
            val = t.value
            negated = False
            if val.startswith("NOT@"):
                negated = True
                val = val[4:]
            return SegmentRefNode(segment_id=val, negated=negated, line=t.line)

        # 조건
        if t.type == "CONDITION":
            self._advance()
            return _parse_condition(t.value, t.line)

        # WITHIN N <dim> -> sequence dimension-restriction step
        if t.type == "RESTRICTION":
            self._advance()
            mr = _RE_RESTRICTION.match(t.value.strip())
            limit = mr.group(1).lower()
            count = int(mr.group(2))
            attr_raw = mr.group(3).strip()
            full_var, _ = _resolve_variable(attr_raw)
            return RestrictionNode(count=count, limit=limit, attribute_name=full_var, line=t.line)

        raise DSLParseError(f"예상치 못한 토큰: '{t.value}'", line=t.line)


def parse_dsl(text: str) -> ContainerNode | LogicalNode | ConditionNode:
    """DSL 텍스트 → AST."""
    tokens = _tokenize(text)
    if not tokens:
        raise DSLParseError("빈 DSL 입력")
    return _Parser(tokens).parse()


# ═══════════════════════════════════════════════════════════════════
# Compiler (AST → AA JSON)
# ═══════════════════════════════════════════════════════════════════

def _compile_condition(node: ConditionNode) -> dict:
    """ConditionNode → AA JSON pred dict."""
    pred: dict[str, Any] = {"func": node.operator}

    # val / evt
    if node.operator == "event-exists":
        pred["evt"] = {"func": node.val_func, "name": node.variable}
    else:
        pred["val"] = {"func": node.val_func, "name": node.variable}

    # 값 필드
    if node.value is not None:
        if isinstance(node.value, list):
            pred["list"] = node.value
        elif node.operator == "matches-regex":
            pred["regex"] = node.value
        elif isinstance(node.value, (int, float)):
            pred["num"] = node.value
        else:
            pred["str"] = str(node.value)

    if node.description:
        pred["description"] = node.description

    # negation
    if node.negated:
        return {"func": "without", "pred": pred}
    return pred


def _wrap_in_container(node: Any, parent_context: str) -> dict:
    """노드를 container로 감싸기 (AA 패턴 재현)."""
    if isinstance(node, RestrictionNode):
        return {
            "count": node.count,
            "limit": node.limit,
            "attribute": {"func": "attr", "name": node.attribute_name},
            "func": "dimension-restriction",
        }
    if isinstance(node, ContainerNode):
        return _compile_node(node)
    # segment-ref는 container로 감싸지 않음 (AA API 패턴)
    if isinstance(node, SegmentRefNode):
        return _compile_segment_ref(node)
    # 조건이나 논리 노드는 부모 context 상속 container로 감싸기
    return {
        "func": "container",
        "context": parent_context,
        "pred": _compile_pred(node, parent_context),
    }


def _compile_segment_ref(node: SegmentRefNode) -> dict:
    """SegmentRefNode → AA JSON segment-ref dict."""
    pred = {"func": "segment-ref", "segmentId": node.segment_id}
    if node.negated:
        return {"func": "without", "pred": pred}
    return pred


def _compile_pred(node: Any, parent_context: str) -> dict:
    if isinstance(node, SegmentRefNode):
        return _compile_segment_ref(node)
    elif isinstance(node, ConditionNode):
        return _compile_condition(node)
    elif isinstance(node, SequenceNode):
        return {
            "func": "sequence",
            "stream": [_wrap_in_container(step, parent_context) for step in node.steps],
        }
    elif isinstance(node, LogicalNode):
        # NOT 그룹: LogicalNode(op="not", children=[inner])
        if node.op == "not":
            inner = _compile_pred(node.children[0], parent_context)
            return {"func": "without", "pred": inner}
        return {
            "func": node.op,
            "preds": [_wrap_in_container(child, parent_context) for child in node.children],
        }
    elif isinstance(node, ContainerNode):
        return _compile_node(node)
    raise TypeError(f"Unknown node type: {type(node)}")


def _compile_node(node: Any) -> dict:
    if isinstance(node, ContainerNode):
        result: dict[str, Any] = {
            "func": "container",
            "context": node.context,
            "pred": _compile_pred(node.pred, node.context),
        }
        if node.description:
            result["description"] = node.description
        return result
    return _compile_pred(node, "hits")


def compile_to_definition(ast: Any) -> dict:
    """AST → AA SEGMENT_DEFINITION dict."""
    # 최상위가 ContainerNode가 아니면 기본 hit container로 감싸기
    if not isinstance(ast, ContainerNode):
        ast = ContainerNode(context="hits", pred=ast)

    return {
        "func": "segment",
        "version": [1, 0, 0],
        "container": _compile_node(ast),
    }


# ═══════════════════════════════════════════════════════════════════
# Decompiler (AA JSON → DSL 텍스트)
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
    """AA JSON pred → DSL 텍스트 줄 리스트."""
    func = pred.get("func", "")
    pad = "  " * indent

    # without → NOT
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

    # and / or
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

    # container (중첩)
    if func == "container":
        ctx = pred.get("context", parent_context)
        desc = pred.get("description", "")
        inner_pred = pred.get("pred", {})

        # 부모와 같은 context이고 description 없으면 container 생략
        if ctx == parent_context and not desc:
            return _decompile_pred(inner_pred, indent, ctx)

        scope = CONTEXT_TO_SCOPE.get(ctx, ctx)
        prefix = f"'{desc}'!" if desc else ""
        inner_lines = _decompile_pred(inner_pred, indent + 1, ctx)

        lines = [f"{pad}{prefix}{scope}("]
        lines.extend(inner_lines)
        lines.append(f"{pad})")
        return lines

    # sequence (then 로직)
    if func in ("sequence", "sequence-prefix", "sequence-suffix"):
        stream = pred.get("stream", pred.get("preds", []))
        lines: list[str] = []
        for i, step in enumerate(stream):
            step_lines = _decompile_pred(step, indent, parent_context)
            if i > 0:
                lines.append(f"{pad}THEN")
            lines.extend(step_lines)
        return lines

    # segment-ref
    if func == "segment-ref":
        seg_id = pred.get("segmentId", "?")
        return [f"{pad}@{seg_id}"]

    # leaf 조건
    leaf = _decompile_leaf(pred, parent_context)
    return [f"{pad}{l}" for l in leaf]


def _decompile_leaf(pred: dict, parent_context: str) -> list[str]:
    """leaf 조건 → DSL 한 줄."""
    func = pred.get("func", "")

    # without wrapper
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

    # 변수명
    val = pred.get("val") or pred.get("evt") or {}
    var_name = val.get("name", "")
    short_var = _reverse_variable(var_name) if var_name else "?"

    # 연산자
    dsl_op = FUNC_TO_DSL.get(func, func)

    # 값
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
    """AA SEGMENT_DEFINITION → DSL 텍스트."""
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
    """세그먼트 메타 + DSL 본문 → 하나의 --- segment 블록."""
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
# 입력 파일 파서 (--- segment 블록 분리)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SegmentSpec:
    name: str
    description: str
    rsid: str
    tags: list[str]
    dsl_body: str
    definition: dict | None = None   # compile 후 채워짐
    block_line: int = 0              # 파일 내 시작 줄번호


def _parse_input_file(text: str) -> list[SegmentSpec]:
    """입력 파일 → SegmentSpec 리스트."""
    blocks = re.split(r"^---\s*segment\s*$", text, flags=re.MULTILINE)
    specs: list[SegmentSpec] = []

    # 첫 블록은 보통 빈칸 또는 주석
    line_offset = 0
    for block in blocks:
        if not block.strip():
            line_offset += block.count("\n") + 1
            continue

        lines = block.split("\n")
        # 메타데이터 파싱 (빈 줄 전까지)
        meta: dict[str, str] = {}
        body_start = 0
        for j, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                body_start = j + 1
                break
            if ":" in stripped:
                key, val = stripped.split(":", 1)
                meta[key.strip().lower()] = val.strip()
            body_start = j + 1

        # tags 파싱
        tags_str = meta.get("tags", "")
        if tags_str.startswith("[") and tags_str.endswith("]"):
            tags_str = tags_str[1:-1]
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        dsl_body = "\n".join(lines[body_start:]).strip()

        specs.append(SegmentSpec(
            name=meta.get("name", ""),
            description=meta.get("description", ""),
            rsid=meta.get("rsid", ""),
            tags=tags,
            dsl_body=dsl_body,
            block_line=line_offset + 1,
        ))
        line_offset += block.count("\n") + 1

    return specs


# ═══════════════════════════════════════════════════════════════════
# 인증 (v1에서 재사용)
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


def _lookup_owner_id(headers: dict, gcid: str, *,
                     ims_user_id: str = "", login_sub: str = "") -> int:
    if not (ims_user_id or login_sub):
        raise ValueError("ims_user_id 또는 login_sub 중 하나는 필요합니다.")

    url = f"https://analytics.adobe.io/api/{gcid}/users"
    target_ims = ims_user_id.lower().strip()
    sub = login_sub.lower().strip()
    matches: list[dict] = []
    page = 0
    while page < 50:
        r = requests.get(url, headers=headers, params={"limit": 400, "page": page}, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(
                f"GET /users 실패: {r.status_code} {r.reason} — {r.text[:300]}\n"
                f"  → 권한 없으면 OWNER_ID에 numeric loginId를 직접 지정하세요."
            )
        body = r.json()
        items = body.get("content") if isinstance(body, dict) else body
        if not items:
            break
        for u in items:
            ims_field = (u.get("imsUserId") or "").lower()
            login_str = u.get("login") or ""
            email = u.get("email") or ""
            full_name = u.get("fullName") or ""
            hit = False
            if target_ims:
                if ims_field == target_ims:
                    hit = True
            elif sub:
                haystack = f"{login_str} {email} {full_name}".lower()
                if sub in haystack:
                    hit = True
            if hit:
                matches.append({
                    "loginId": u.get("loginId"),
                    "login": login_str,
                    "email": email,
                    "fullName": full_name,
                    "imsUserId": u.get("imsUserId") or "",
                })
        if isinstance(body, dict) and body.get("lastPage", True):
            break
        page += 1

    mode = f"imsUserId='{ims_user_id}'" if target_ims else f"login substring='{login_sub}'"
    if not matches:
        raise RuntimeError(f"{mode} 매칭 사용자 0명")
    if len(matches) > 1:
        msg = f"{mode} 매칭 {len(matches)}명 — OWNER_ID 직접 지정 필요:\n"
        for m in matches[:10]:
            msg += f"  - loginId={m['loginId']:>10}  {m['email']}  ({m['fullName']})\n"
        raise RuntimeError(msg)

    m = matches[0]
    print(f"  matched owner: loginId={m['loginId']}  {m['email']}  ({m['fullName']})")
    return int(m["loginId"])


# ═══════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="DSL 기반 AA 세그먼트 다중 생성 (기본 dry-run)"
    )
    parser.add_argument("--apply", action="store_true",
                        help="실제 POST 수행. 없으면 JSON 출력만 (dry-run)")
    parser.add_argument("--input", default=INPUT_FILE,
                        help=f"DSL 입력 파일 경로 (default: {INPUT_FILE})")
    parser.add_argument("--decompile", metavar="JSON_FILE",
                        help="AA JSON 파일을 DSL로 역변환 (입력 파일 대신)")
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    # ── decompile 모드 ──
    if args.decompile:
        json_path = Path(args.decompile)
        if not json_path.exists():
            print(f"ERROR: 파일 없음: {json_path}")
            return 1
        data = json.loads(json_path.read_text(encoding="utf-8"))
        definition = data.get("definition", data)
        dsl_text = decompile_definition(definition)
        print(dsl_text)
        return 0

    # ── 입력 파일 읽기 ──
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: 입력 파일 없음: {input_path}")
        print(f"  '{INPUT_FILE}'에 DSL을 작성하세요.")
        return 1

    file_text = input_path.read_text(encoding="utf-8")
    specs = _parse_input_file(file_text)

    if not specs:
        print("ERROR: 세그먼트 블록이 없습니다 (--- segment 로 시작)")
        return 1

    print(f"[{requested_at}] AA segment maker v2 — {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"  Company : {COMPANY_ID}")
    print(f"  Input   : {input_path}")
    print(f"  Segments: {len(specs)}개")
    print()

    # ── DSL 파싱 + 컴파일 ──
    errors = []
    for i, spec in enumerate(specs):
        if not spec.name:
            print(f"  [segment {i+1}] WARNING: name 미지정")
        if not spec.rsid:
            spec.rsid = DEFAULT_RSID
        if not spec.dsl_body:
            errors.append((i + 1, "DSL 본문이 비어있습니다"))
            continue
        try:
            ast = parse_dsl(spec.dsl_body)
            spec.definition = compile_to_definition(ast)
            print(f"  [segment {i+1}] '{spec.name}' — 파싱 OK")
        except DSLParseError as e:
            errors.append((i + 1, str(e)))
            print(f"  [segment {i+1}] '{spec.name}' — ERROR: {e}")

    if errors:
        print(f"\n파싱 에러 {len(errors)}건:")
        for idx, msg in errors:
            print(f"  segment {idx}: {msg}")
        if args.apply:
            print("\n에러가 있어 --apply 중단합니다. 수정 후 재실행하세요.")
            return 1

    print()

    # ── Payload 출력 ──
    for i, spec in enumerate(specs):
        if spec.definition is None:
            continue
        print(f"{'─' * 60}")
        print(f"Segment {i+1}: {spec.name}")
        print(f"  RSID: {spec.rsid}")
        print(f"  Tags: {spec.tags}")
        print(f"  Description: {spec.description}")
        print()
        print("  Definition JSON:")
        print(json.dumps(spec.definition, ensure_ascii=False, indent=2))
        print()

    if not args.apply:
        print("DRY-RUN — 실제 POST 안 함. 위 JSON 확인 후 --apply 추가해서 다시 실행.")
        print(f"  python {Path(__file__).name} --apply --input {args.input}")
        return 0

    # ── 인증 ──
    print("Authenticating ...")
    headers, gcid = _load_auth_headers()

    # Owner 확정
    if OWNER_ID is not None:
        owner_id: int | None = OWNER_ID
        print(f"  Owner: {owner_id} (config 직접 지정)")
    elif OWNER_IMS_USER_ID:
        print(f"  resolving owner by imsUserId ...")
        owner_id = _lookup_owner_id(headers, gcid, ims_user_id=OWNER_IMS_USER_ID)
    elif OWNER_LOGIN:
        print(f"  resolving owner by login ...")
        owner_id = _lookup_owner_id(headers, gcid, login_sub=OWNER_LOGIN)
    else:
        owner_id = None
        print("  Owner: (미지정)")
    print()

    # ── API POST ──
    endpoint = f"https://analytics.adobe.io/api/{gcid}/segments"
    results: list[dict] = []

    for i, spec in enumerate(specs):
        if spec.definition is None:
            results.append({
                "name": spec.name, "seg_id": "", "status": "SKIP",
                "url": "", "error": "파싱 실패",
            })
            continue

        payload: dict[str, Any] = {
            "name": spec.name,
            "description": spec.description,
            "rsid": spec.rsid,
            "definition": spec.definition,
            "tags": spec.tags,
        }
        if owner_id is not None:
            payload["owner"] = {"id": owner_id}

        print(f"  [{i+1}/{len(specs)}] POST '{spec.name}' ...", end=" ")
        r = requests.post(endpoint, headers=headers, json=payload, timeout=60)

        if r.status_code in (200, 201):
            data = r.json()
            seg_id = data.get("id", "")
            ui_url = UI_URL_TEMPLATE.format(seg_id=seg_id) if seg_id else ""
            print(f"OK — {seg_id}")
            results.append({
                "name": spec.name, "seg_id": seg_id,
                "status": f"{r.status_code} {r.reason}",
                "url": ui_url, "error": "",
            })
        else:
            error = r.text[:300]
            print(f"FAIL — {r.status_code} {r.reason}")
            results.append({
                "name": spec.name, "seg_id": "",
                "status": f"{r.status_code} {r.reason}",
                "url": "", "error": error,
            })

    # ── Result CSV ──
    csv_path = OUTPUT_DIR / f"{RESULT_CSV_PREFIX}{timestamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["RequestedAt", "Name", "SegmentId", "RSID", "Status", "Url", "Error"])
        for res in results:
            spec_rsid = next((s.rsid for s in specs if s.name == res["name"]), DEFAULT_RSID)
            w.writerow([
                requested_at, res["name"], res["seg_id"],
                spec_rsid, res["status"], res["url"], res["error"],
            ])
    print(f"\nresult CSV: {csv_path}")

    ok = sum(1 for r in results if r["seg_id"])
    fail = sum(1 for r in results if not r["seg_id"])
    print(f"[summary] 성공: {ok}, 실패: {fail}")

    return 0 if fail == 0 else 1




OUTPUT_DIR = Path(__file__).resolve().parent
LOOKUP_DIR = OUTPUT_DIR / "lookup"          # aa_segment_lookup* 가 결과 csv/dsl 떨어뜨리는 폴더 — --lookup-by-name source
RESULT_CSV_PREFIX = "segment_v2.2_result_"


def _resolve_cache_paths(name: str) -> list[Path]:
    """CACHE_NAME 또는 --cache 값 (콤마 분리 가능) → cache 파일 경로 list.
    예: "us,add_to_cart_us" → [segment_ref_cache_us.json, segment_ref_cache_add_to_cart_us.json].
    list 첫 파일이 save target, 모든 파일 load + merge (앞 파일 우선 — 충돌 시 뒤 파일이 덮어쓰지 않음)."""
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

import requests


def _load_seg_ref_cache(cache_path) -> dict[str, dict]:
    """캐시 파일 load. cache_path 가 Path 면 한 파일, list[Path] 면 여러 파일 merge.
    Merge 순서 — 앞 파일 우선 (뒤 파일 매칭 시 덮어쓰지 않음). 첫 파일이 save target."""
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


def _fetch_segment_container(seg_id: str, headers: dict, gcid: str) -> dict | None:
    """AA GET /segments/{id}?expansion=definition → definition.container 통째 반환."""
    url = f"https://analytics.adobe.io/api/{gcid}/segments/{seg_id}?expansion=definition"
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code != 200:
        print(f"  [seg-ref fetch] {seg_id} GET 실패: {r.status_code} {r.reason}")
        return None
    data = r.json()
    container = (data.get("definition") or {}).get("container")
    if not container:
        print(f"  [seg-ref fetch] {seg_id} → definition.container 없음")
        return None
    return container


# v2.3 preprocess — [sequence-after] visitor(...) wrap 전체 제거 (paren depth tracking).
# 제거 후 sequence (THEN) 가 outer hit 의 직접 pred 가 되어 _patch_root_sequence_for_hit_scope 가
# sequence → sequence-prefix + context=visitors 자동 변환. AA reference 룰 충족:
#   container.context=hits → pred.func=sequence-prefix(context=visitors) → stream[i].context=visits
_SEQUENCE_LABEL_SCOPE_RE = re.compile(r'\[sequence-(?:after|before|all)\]\s*(?:hit|visit|visitor)\(')


def _strip_sequence_label_tokens(dsl_text):
    while True:
        m = _SEQUENCE_LABEL_SCOPE_RE.search(dsl_text)
        if not m:
            break
        start = m.start()
        open_pos = m.end() - 1   # '(' 위치
        depth = 1
        i = open_pos + 1
        while i < len(dsl_text) and depth > 0:
            if dsl_text[i] == '(':
                depth += 1
            elif dsl_text[i] == ')':
                depth -= 1
            i += 1
        if depth == 0:
            inner = dsl_text[open_pos+1:i-1]   # visitor( 와 ) 사이 content 만 유지
            dsl_text = dsl_text[:start] + inner + dsl_text[i:]
        else:
            break
    return dsl_text


def _patch_root_sequence_for_hit_scope(definition: dict) -> dict:
    """root container.context = 'hits' + root.pred.func = 'sequence' 케이스 → 'sequence-prefix' + context='visitors' 변환.

    AA reference 의 정확한 패턴 (Delayed Purchase 같은 hit-scope sequence):
      container { context: "hits", pred: { func: "sequence-prefix", context: "visitors", stream: [...] } }

    AA validator 룰:
      · `sequence` (full)  → implicit context 가 visits/visitors 여야 함, hit-scope 거부
      · `sequence-prefix`  → hit-scope 컨테이너 안 허용. 단 자체 `context` 필수 ("visitors" 권장)
    """
    if not isinstance(definition, dict):
        return definition
    container = definition.get("container")
    if not isinstance(container, dict):
        return definition
    if container.get("context") != "hits":
        return definition
    pred = container.get("pred")
    if isinstance(pred, dict) and pred.get("func") == "sequence":
        pred["func"] = "sequence-prefix"
        # sequence-prefix 자체 context 필수 — reference 패턴 따라 "visitors"
        if "context" not in pred:
            pred["context"] = "visitors"
    return definition


def _lift_inner_hit_into_visit_root(definition: dict) -> dict:
    """root container.context='visits'(or 'visitors') + pred 가 container(hits, no_desc) 단일 wrap 인 경우
    inner hit wrap 제거하고 outer.pred = inner.pred 로 직접 박음.

    AA POST 시 server-side simplification 이 outer-visit + 단일 inner-hit(no_desc) 패턴을
    hit-scope 로 합쳐버리는 동작 우회. visit/visitor scope 보존.
    """
    if not isinstance(definition, dict):
        return definition
    container = definition.get("container")
    if not isinstance(container, dict):
        return definition
    if container.get("context") not in ("visits", "visitors"):
        return definition
    pred = container.get("pred")
    if not (isinstance(pred, dict) and pred.get("func") == "container"
            and pred.get("context") == "hits" and not pred.get("description")):
        return definition
    inner_pred = pred.get("pred")
    if inner_pred is None:
        return definition
    container["pred"] = inner_pred
    return definition


def _patch_definition_for_aa(node, *, fetch_seg_pred=None):
    """v2 컴파일 결과 JSON → AA validator 호환 형식 후처리.

    AA segment definition 의 정확한 형식 (v1 주석 + 실제 validator 응답 단서):
      1) metric event 의 발생 여부 — `event-exists` func + `evt` 키 (val 아님)
         변환 전: {"func": "exists", "val": {"func": "event", "name": "metrics/event<N>"}}
         변환 후: {"func": "event-exists", "evt": {"func": "event", "name": "metrics/event<N>"}}

      2) segment-ref — AA 가 `segment-ref` 라는 func 이름 모름. fetch_seg_pred 가 주어지면
         AA GET /segments/{id} 으로 sub-segment 의 container 를 가져와 그 자리에 inline 박음.
         fetch_seg_pred 가 None 이거나 결과 None 이면 노드 그대로 유지 (apply 시 validator 거부 예상).

    재귀로 dict / list 안 모든 노드 순회.
    """
    if isinstance(node, dict):
        # 1a) event metric 의 exists (v2 가 이미 event type 으로 컴파일한 경우)
        if (node.get("func") == "exists"
                and isinstance(node.get("val"), dict)
                and node["val"].get("func") == "event"):
            return {"func": "event-exists", "evt": node["val"]}
        # 1b) `<varname>instances` 의 exists — v2 는 attr (variables/...) 로 컴파일하지만
        #     AA 는 metric (metrics/...) + event-exists func + evt 키로 받음 (US 패턴)
        if (node.get("func") == "exists"
                and isinstance(node.get("val"), dict)
                and node["val"].get("func") == "attr"
                and isinstance(node["val"].get("name"), str)
                and node["val"]["name"].endswith("instances")):
            attr_name = node["val"]["name"]
            metric_name = attr_name.replace("variables/", "metrics/", 1)
            return {"func": "event-exists",
                    "evt": {"func": "event", "name": metric_name}}
        # 2) segment-ref → sub-segment 의 container inline
        if node.get("func") == "segment-ref" and fetch_seg_pred is not None:
            seg_id = node.get("segmentId")
            if seg_id:
                inline = fetch_seg_pred(seg_id)
                if inline is not None:
                    return _patch_definition_for_aa(inline, fetch_seg_pred=fetch_seg_pred)
            return node
        return {k: _patch_definition_for_aa(v, fetch_seg_pred=fetch_seg_pred)
                for k, v in node.items()}
    if isinstance(node, list):
        return [_patch_definition_for_aa(v, fetch_seg_pred=fetch_seg_pred) for v in node]
    return node


def _structure_to_dsl(structure: str) -> str:
    """structure 칼럼 한 줄 → 멀티라인 DSL 텍스트 (parser 호환 변환 포함).

    " | " 구분을 줄바꿈으로 변환 + 아래 두 가지 input_csv_maker DSL → parser 호환 변환:

    1) `event<N> event-exists` → `event<N> exists`
       (parser 는 operator `exists` 만 받음 — `event-exists` 라는 변형은 미지원)

    2) 단독 grouping paren `( ... )` 제거
       (input_csv_maker 가 visit/visitor 모드에서 추가하는 `'Name'!hit(...)` 외부 paren grouping.
        parser 는 컨테이너 기반 grouping 만 받고 raw paren grouping 은 미지원.
        단독 `(` 토큰과 매칭되는 단독 `)` 토큰만 제거 — 컨테이너 open `'name'!hit(`, `visit(`, `hit(`,
        `'Component'!hit(` 같은 토큰은 보존.)
    """
    raw_tokens = structure.split(" | ")
    # 1) `<varname> event-exists` → `<varname> exists`
    #    글로벌: `event<N> event-exists`, US: `evar<N>instances event-exists` 둘 다 매칭.
    #    v2 parser 는 `exists` operator 만 받음 — `event-exists` 는 토큰 자체로 파싱 실패.
    #    JSON 컴파일 후 _patch_definition_for_aa 에서 AA 호환 `event-exists` func 으로 다시 변환.
    tokens = [
        re.sub(r"\b(\w+) event-exists\b", r"\1 exists", t)
        for t in raw_tokens
    ]
    # 2) `not '<container>'!hit(` → `NOT (`  (parser 는 NOT named container 미지원, NOT (...) grouping 만 받음)
    #    매칭 `)` 는 그대로 유지 — 아래 paren stack 에서 'cont' (NOT ( 가 endswith "(") 로 처리되어 보존
    tokens = [
        re.sub(r"^not '[^']*'!hit\(\s*$", "NOT (", t.strip())
        for t in tokens
    ]
    # 2.5) `NOT (cond)` 한 토큰 → 멀티라인 `NOT (` / `cond` / `)` 로 펼침
    #      한 줄 inline 형태로 들어가면 v2 parser 가 paren 안 변수명을 잘못 읽음 (예: `(evar26` 을 attr name 으로 인식).
    #      build_evar_block 의 `f"NOT ({cond_str})"` 같은 inline NOT 케이스 대응.
    expanded: list[str] = []
    for t in tokens:
        m = re.match(r"^NOT\s*\((.+)\)$", t.strip())
        if m:
            expanded.append("NOT (")
            expanded.append(m.group(1).strip())
            expanded.append(")")
        else:
            expanded.append(t)
    tokens = expanded
    # 3) grouping paren 제거 — paren stack 으로 단독 '(' / ')' 매칭 추적
    stack: list[str] = []          # 'group' (제거) or 'cont' (보존)
    to_remove: set[int] = set()
    for i, tok in enumerate(tokens):
        t = tok.strip()
        if t == "(":
            stack.append("group")
            to_remove.add(i)
        elif t == ")":
            if stack:
                opened = stack.pop()
                if opened == "group":
                    to_remove.add(i)
                # 'cont' 면 토큰 그대로 유지
        elif t.endswith("("):
            # 컨테이너 open: visit(, hit(, 'name'!hit(, 'Component'!hit( 등
            stack.append("cont")
    filtered = [t for i, t in enumerate(tokens) if i not in to_remove]
    return "\n".join(filtered)


def _fetch_existing_segment(headers: dict, base_endpoint: str, seg_id: str) -> dict | None:
    """기존 segment GET — noop 비교용. 실패하면 None (PUT 진행 fallback)."""
    try:
        r = requests.get(
            f"{base_endpoint}/{seg_id}",
            headers=headers,
            params={"expansion": "definition,name,description,reportSuiteName,tags"},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _payload_equals_existing(payload: dict, existing: dict) -> bool:
    """새 payload 와 기존 segment 가 5 필드 (definition, name, description, rsid, tags) 모두 같은지.
    tags — payload 는 list[str], existing 은 list[dict] (각 dict 의 'name' 키) → set 비교.
    그 외 — 직접 ==. definition 은 nested dict 동등성.
    """
    if (payload.get("name") or "").strip() != (existing.get("name") or "").strip():
        return False
    if (payload.get("description") or "").strip() != (existing.get("description") or "").strip():
        return False
    if (payload.get("rsid") or "") != (existing.get("rsid") or ""):
        return False
    p_tags = set((payload.get("tags") or []))
    e_tags_raw = existing.get("tags") or []
    e_tags = set(
        (t.get("name", "") if isinstance(t, dict) else str(t)) for t in e_tags_raw
    )
    if p_tags != e_tags:
        return False
    if payload.get("definition") != existing.get("definition"):
        return False
    return True


def _parse_csv(csv_path: Path) -> list[dict]:
    """CSV → [{name, description, rsid, tags, structure}, ...]"""
    rows: list[dict] = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        if "structure" not in headers:
            print(f"ERROR: CSV에 'structure' 칼럼이 없습니다.")
            print(f"  칼럼: {headers}")
            return []
        if "name" not in headers:
            print(f"ERROR: CSV에 'name' 칼럼이 없습니다.")
            print(f"  칼럼: {headers}")
            return []

        for row in reader:
            structure = (row.get("structure") or "").strip()
            if not structure:
                continue
            rows.append({
                "segment_id": (row.get("segment_id") or "").strip(),
                "name": (row.get("name") or "").strip(),
                "description": (row.get("description") or "").strip(),
                "rsid": (row.get("rsid") or "").strip(),
                "tags": (row.get("tags") or "").strip(),
                "structure": structure,
            })
    return rows


def main() -> int:
    # Windows cp949 콘솔에서도 em dash / 한글 깨지지 않도록 utf-8 reconfigure
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="CSV 기반 AA 세그먼트 생성/업데이트 (기본 dry-run)"
    )
    parser.add_argument("--apply", action="store_true",
                        help="실제 POST/PUT 수행. 없으면 JSON 출력만 (dry-run)")
    parser.add_argument("--update", action="store_true",
                        help="기존 세그먼트 업데이트 (PUT). segment_id 칼럼 필수 — 빈 row 있으면 ERROR. "
                             "segment_id 모르면: python segment_lookup.py --search \"이름\"")
    parser.add_argument("--update-or-create", action="store_true",
                        help="mixed mode: row 별 segment_id 있으면 PUT (update), 없으면 POST (create). "
                             "재실행 시 — 새 segment 는 만들고 기존 segment 는 갱신. --update 와 동시 사용 불가.")
    parser.add_argument("--lookup-by-name", action=argparse.BooleanOptionalAction, default=True,
                        help="csv 의 segment_id 빈 row 는 같은 폴더의 lookup/ 하위 segment_lookup_*.csv 에서 name 매칭으로 자동 채움 (default True). "
                             "--no-lookup-by-name 으로 비활성 — 강제 POST (lookup csv 안 봄). "
                             "그래도 POST 시점에 lookup csv 에 동일 name 있으면 중복 생성 경고 출력.")
    parser.add_argument("--lookup-csv", default=LOOKUP_CSV,
                        help=f"--lookup-by-name 의 lookup csv 경로 명시. 빈 값이면 같은 폴더의 lookup/ 하위 모든 segment_lookup_*.csv merge "
                             f"(사전순 reverse, 새 거 우선). 코드 상단 LOOKUP_CSV 로 default 박을 수 있음 (현재 {LOOKUP_CSV!r}). "
                             f"상대경로면 LOOKUP_DIR → OUTPUT_DIR 순으로 fallback.")
    parser.add_argument("--input", default=INPUT_CSV,
                        help=f"입력 CSV 경로 (default: {INPUT_CSV})")
    parser.add_argument("--cache", default=CACHE_NAME,
                        help="segment-ref 캐시 파일 suffix (예: --cache us → segment_ref_cache_us.json). "
                             f"빈 값이면 기본 segment_ref_cache.json. 코드 상단 CACHE_NAME 으로 default 지정 가능 (현재 {CACHE_NAME!r}).")
    args = parser.parse_args()
    seg_ref_cache_paths = _resolve_cache_paths(args.cache)
    seg_ref_cache_path = seg_ref_cache_paths[0]   # save target = 첫 파일
    if len(seg_ref_cache_paths) == 1:
        print(f"[seg-ref cache] {seg_ref_cache_path.name}")
    else:
        print(f"[seg-ref cache] save: {seg_ref_cache_path.name} / load merge: " +
              ", ".join(p.name for p in seg_ref_cache_paths))

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    # ── CSV 읽기 ──
    # --input 절대경로 → 그대로 사용
    # --input 빈 값이면 폴더 내 segments_input_*.csv 파일명 사전순 최신 1개 자동 pick.
    # 박혀 있으면 그대로 사용 (상대경로면 cwd → script 폴더 fallback).
    input_arg = (args.input or "").strip()
    if not input_arg:
        cands = sorted(OUTPUT_DIR.glob("segments_input_*.csv"), reverse=True)
        if not cands:
            print(f"ERROR: segments_input_*.csv 못 찾음 — {OUTPUT_DIR}")
            return 1
        input_path = cands[0]
        print(f"[input auto-latest] {input_path.name}  (총 {len(cands)} 개 후보 중 최신)")
    else:
        input_path = Path(input_arg)
        if not input_path.is_absolute() and not input_path.exists():
            fallback = OUTPUT_DIR / input_path
            if fallback.exists():
                input_path = fallback
        if not input_path.exists():
            print(f"ERROR: 입력 파일 없음: {input_path}")
            print(f"       cwd: {Path.cwd()}")
            print(f"       script dir: {OUTPUT_DIR}")
            return 1

    rows = _parse_csv(input_path)
    if not rows:
        print("ERROR: structure가 있는 행이 없습니다.")
        return 1

    # lookup csv 로딩 — 옵션 무관 항상 로드 (name 매핑 dict 빌드).
    # lookup-by-name=True → segment_id 자동 채움. False → POST 시 동일 name 매칭 경고만.
    # source: 같은 폴더의 lookup/ 하위 segment_lookup_*.csv (aa_segment_lookup* 가 떨어뜨림)
    lookup_csv_arg = (getattr(args, "lookup_csv", "") or "").strip()
    if lookup_csv_arg:
        lookup_path = Path(lookup_csv_arg)
        if not lookup_path.is_absolute() and not lookup_path.exists():
            # 1) LOOKUP_DIR 기준 fallback (예: "segment_lookup_xxx.csv")
            fb_lookup = LOOKUP_DIR / lookup_path
            if fb_lookup.exists():
                lookup_path = fb_lookup
            else:
                # 2) OUTPUT_DIR 기준 fallback (구버전 호환 — 같은 폴더에 직접 있던 케이스)
                fb_out = OUTPUT_DIR / lookup_path
                if fb_out.exists():
                    lookup_path = fb_out
        lookup_paths = [lookup_path] if lookup_path else []
    else:
        lookup_paths = sorted(LOOKUP_DIR.glob("segment_lookup_*.csv"), reverse=True)

    name_to_id: dict[str, str] = {}
    used_files: list[str] = []
    for p in lookup_paths:
        try:
            added = 0
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    nm = (r.get("name") or "").strip()
                    sid = (r.get("segment_id") or "").strip()
                    if nm and sid and nm not in name_to_id:
                        name_to_id[nm] = sid
                        added += 1
            if added > 0:
                used_files.append(f"{p.name}(+{added})")
        except Exception:
            continue

    if getattr(args, "lookup_by_name", True):
        # segment_id 빈 row 의 segment_id 를 lookup csv name 매칭으로 자동 채움
        if not lookup_paths:
            print(f"  [lookup-by-name] ⚠️ lookup csv 없음 — segment_id 빈 row 그대로 (POST 처리됨)")
        else:
            n_filled = 0
            for r in rows:
                if not (r.get("segment_id") or "").strip():
                    sid = name_to_id.get((r.get("name") or "").strip())
                    if sid:
                        r["segment_id"] = sid
                        n_filled += 1
            print(f"  [lookup-by-name] {len(lookup_paths)} lookup csv merge → {n_filled}/{len(rows)} row 의 segment_id 채움")
            if used_files:
                print(f"    매핑 추가 — {', '.join(used_files[:5])}{'...' if len(used_files)>5 else ''}")
    else:
        # --no-lookup-by-name — segment_id 채우지 않음. POST 시 동일 name 매칭되면 경고.
        dup_warns: list[tuple[str, str]] = []
        for r in rows:
            if not (r.get("segment_id") or "").strip():
                nm = (r.get("name") or "").strip()
                if nm in name_to_id:
                    dup_warns.append((nm, name_to_id[nm]))
        if dup_warns:
            print(f"  [warn] --no-lookup-by-name (강제 POST) — lookup csv 에 동일 name {len(dup_warns)} 건 존재:")
            for nm, sid in dup_warns[:5]:
                print(f"     - {nm}  (existing id={sid})")
            if len(dup_warns) > 5:
                print(f"     ... +{len(dup_warns) - 5} 건")
            print(f"  → 중복 생성됩니다. 의도가 아니면 명령에서 --no-lookup-by-name 제거.")

    update_mode = args.update
    mixed_mode  = getattr(args, "update_or_create", False)
    if update_mode and mixed_mode:
        print(f"ERROR: --update 와 --update-or-create 동시 사용 불가. 하나만 선택.")
        return 1
    if mixed_mode:
        mode_label = "MIXED"   # row 별 segment_id 있음/없음 자동 분기
    elif update_mode:
        mode_label = "UPDATE"
    else:
        mode_label = "CREATE"

    # update 모드: segment_id 필수 검증 (mixed 모드는 row 별 자동 분기라 검증 안 함)
    if update_mode:
        missing_ids = [i+1 for i, r in enumerate(rows) if not r["segment_id"]]
        if missing_ids:
            print(f"ERROR: --update 모드인데 segment_id가 없는 행: {missing_ids}")
            print(f"  → mixed mode 원하면 --update-or-create 사용 (segment_id 빈 row 는 POST, 있는 row 는 PUT)")
            return 1

    action_label = f"{mode_label} / {'APPLY' if args.apply else 'DRY-RUN'}"
    print(f"[{requested_at}] AA segment maker v2.2 (CSV) — {action_label}")
    print(f"  Company : {COMPANY_ID}")
    print(f"  Input   : {input_path}")
    print(f"  Segments: {len(rows)}개")
    if mixed_mode:
        n_put  = sum(1 for r in rows if (r.get("segment_id") or "").strip())
        n_post = len(rows) - n_put
        print(f"  Mode    : MIXED (PUT {n_put} update + POST {n_post} create — segment_id 유무로 row 별 분기)")
    elif update_mode:
        print(f"  Mode    : UPDATE (기존 세그먼트 덮어쓰기)")
    print()

    # ── segment-ref 처리 준비 — cache + lazy auth load ──
    seg_ref_cache: dict[str, dict] = _load_seg_ref_cache(seg_ref_cache_paths)   # 여러 파일 merge load
    _auth_state: dict = {"headers": None, "gcid": None, "tried": False}

    def _extract_container(entry):
        """cache entry → container dict. 두 형식 호환:
        · 새 형식 {"container": {...}, "name": ..., ...} → entry["container"]
        · 옛 형식 container 자체 → entry 그대로"""
        if isinstance(entry, dict) and "container" in entry and isinstance(entry.get("container"), dict):
            return entry["container"]
        return entry

    def fetch_seg_pred(seg_id: str) -> dict | None:
        """segment-ref 의 sub-segment container 가져오기. cache 우선, miss 면 AA GET 시도."""
        if seg_id in seg_ref_cache:
            return _extract_container(seg_ref_cache[seg_id])
        if not _auth_state["tried"]:
            _auth_state["tried"] = True
            try:
                h, g = _load_auth_headers()
                _auth_state["headers"] = h
                _auth_state["gcid"] = g
                print(f"  [seg-ref] auth load OK — sub-segment GET 가능")
            except Exception as e:
                print(f"  [seg-ref] auth load 실패: {e}")
                return None
        if _auth_state["headers"] is None:
            return None
        container = _fetch_segment_container(seg_id, _auth_state["headers"], _auth_state["gcid"])
        if container is not None:
            # 자동 fetch 는 metadata 없이 container 만 받음 — 새 형식으로 저장 (lookup name 은 빈 값)
            seg_ref_cache[seg_id] = {"container": container, "name": "", "description": "", "rsid": ""}
            print(f"  [seg-ref] cache 추가 — {seg_id}")
        return container

    # ── structure → DSL → JSON 변환 ──
    specs: list[dict] = []
    errors: list[tuple[int, str]] = []

    for i, row in enumerate(rows):
        dsl_text = _structure_to_dsl(row["structure"])
        dsl_text = _strip_sequence_label_tokens(dsl_text)
        try:
            ast = parse_dsl(dsl_text)
            definition = compile_to_definition(ast)
            definition = _patch_definition_for_aa(definition, fetch_seg_pred=fetch_seg_pred)
            definition = _lift_inner_hit_into_visit_root(definition)      # visit/visitor scope 보존 (server-side simplify 우회)
            definition = _patch_root_sequence_for_hit_scope(definition)   # Delayed Purchase: root sequence → sequence-prefix
            print(f"  [{i+1}] '{row['name']}' — 파싱 OK")
            specs.append({**row, "definition": definition, "dsl": dsl_text})
        except DSLParseError as e:
            errors.append((i + 1, str(e)))
            print(f"  [{i+1}] '{row['name']}' — ERROR: {e}")
            specs.append({**row, "definition": None, "dsl": dsl_text})

    # segment-ref cache 저장 (loop 중 fetch 한 게 있으면 디스크에 영구 저장)
    _save_seg_ref_cache(seg_ref_cache_path, seg_ref_cache)

    if errors:
        print(f"\n파싱 에러 {len(errors)}건:")
        for idx, msg in errors:
            print(f"  row {idx}: {msg}")
        if args.apply:
            print("\n에러가 있어 --apply 중단합니다. 수정 후 재실행하세요.")
            return 1

    print()

    # Payload 미리보기 — 콘솔 출력 안 함 (너무 김). dryrun csv 에 ParseStatus / Mode 박힘.

    if not args.apply:
        # Dry-run 결과 CSV — 파싱 성공/실패 한 눈에 확인용 (apply 안 해도 진단 결과 csv 로 남김)
        errors_by_idx = {idx - 1: msg for idx, msg in errors}
        dryrun_csv_path = OUTPUT_DIR / f"{RESULT_CSV_PREFIX}{timestamp}_dryrun.csv"
        with open(dryrun_csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["RequestedAt", "Name", "SegmentId", "RSID", "Mode", "ParseStatus", "Error"])
            for i, spec in enumerate(specs):
                ok = spec["definition"] is not None
                w.writerow([
                    requested_at, spec["name"], spec.get("segment_id", ""),
                    spec["rsid"] or DEFAULT_RSID,
                    f"{mode_label}/DRY-RUN",
                    "OK" if ok else "PARSE_ERROR",
                    errors_by_idx.get(i, ""),
                ])
        n_ok = sum(1 for s in specs if s["definition"] is not None)
        n_fail = len(specs) - n_ok
        print(f"\nDry-run 결과 CSV: {dryrun_csv_path.name}  (파싱 OK {n_ok} / FAIL {n_fail})")
        update_flag = " --update" if update_mode else ""
        print(f"DRY-RUN — 실제 {'PUT' if update_mode else 'POST'} 안 함. 위 JSON 확인 후 --apply 추가.")
        print(f"  python {Path(__file__).name} --apply{update_flag} --input {args.input}")
        return 0

    # ── 인증 ──
    print("Authenticating ...")
    headers, gcid = _load_auth_headers()

    # Owner 확정
    if OWNER_ID is not None:
        owner_id: int | None = OWNER_ID
        print(f"  Owner: {owner_id} (config 직접 지정)")
    elif OWNER_IMS_USER_ID:
        print(f"  resolving owner by imsUserId ...")
        owner_id = _lookup_owner_id(headers, gcid, ims_user_id=OWNER_IMS_USER_ID)
    elif OWNER_LOGIN:
        print(f"  resolving owner by login ...")
        owner_id = _lookup_owner_id(headers, gcid, login_sub=OWNER_LOGIN)
    else:
        owner_id = None
        print("  Owner: (미지정)")
    print()

    # ── API POST / PUT ──
    base_endpoint = f"https://analytics.adobe.io/api/{gcid}/segments"
    results: list[dict] = []

    for i, spec in enumerate(specs):
        if spec["definition"] is None:
            results.append({
                "name": spec["name"], "seg_id": spec.get("segment_id", ""),
                "action": "skip",
                "status": "SKIP", "url": "", "error": "파싱 실패",
            })
            continue

        rsid = spec["rsid"] or DEFAULT_RSID

        # tags 파싱: "tag1, tag2" → ["tag1", "tag2"]
        tags_str = spec["tags"]
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        payload: dict[str, Any] = {
            "name": spec["name"],
            "description": spec["description"],
            "rsid": rsid,
            "definition": spec["definition"],
            "tags": tags,
        }
        if owner_id is not None:
            payload["owner"] = {"id": owner_id}

        # PUT (segment_id 있음, update_mode 또는 mixed 모드 + 그 row 가 id 있음) vs POST (없음)
        row_seg_id = (spec.get("segment_id") or "").strip()
        if update_mode or (mixed_mode and row_seg_id):
            # PUT 전에 GET 해서 기존과 동일한지 비교 — 5 필드 모두 같으면 noop (PUT 안 보냄)
            seg_id = row_seg_id
            url = f"{base_endpoint}/{seg_id}"
            existing = _fetch_existing_segment(headers, base_endpoint, seg_id)
            if existing is not None and _payload_equals_existing(payload, existing):
                ui_url = UI_URL_TEMPLATE.format(seg_id=seg_id)
                print(f"  [{i+1}/{len(specs)}] noop '{spec['name']}' ({seg_id}) — 동일 조건")
                results.append({
                    "name": spec["name"], "seg_id": seg_id,
                    "action": "noop",
                    "status": "NOOP",
                    "url": ui_url, "error": "",
                })
                continue
            action_label = "update"
            print(f"  [{i+1}/{len(specs)}] update '{spec['name']}' ({seg_id}) ...", end=" ")
            r = requests.put(url, headers=headers, json=payload, timeout=60)
        else:
            # POST — 새 세그먼트 생성 (CREATE 또는 mixed 의 segment_id 빈 row)
            action_label = "create"
            print(f"  [{i+1}/{len(specs)}] create '{spec['name']}' ...", end=" ")
            r = requests.post(base_endpoint, headers=headers, json=payload, timeout=60)

        if r.status_code in (200, 201):
            data = r.json()
            seg_id = data.get("id", "")
            ui_url = UI_URL_TEMPLATE.format(seg_id=seg_id) if seg_id else ""
            print(f"OK — {seg_id}")
            results.append({
                "name": spec["name"], "seg_id": seg_id,
                "action": action_label,
                "status": f"{r.status_code} {r.reason}",
                "url": ui_url, "error": "",
            })
        else:
            error = r.text[:3000]   # validator 의 errors[] 전체 보려면 충분히 길게
            print(f"FAIL — {r.status_code} {r.reason}")
            results.append({
                "name": spec["name"], "seg_id": spec.get("segment_id", ""),
                "action": action_label,
                "status": f"{r.status_code} {r.reason}",
                "url": "", "error": error,
            })

    # ── Result CSV ──
    csv_path = OUTPUT_DIR / f"{RESULT_CSV_PREFIX}{timestamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["RequestedAt", "Name", "SegmentId", "RSID", "Action", "Status", "Url", "Error"])
        for res in results:
            w.writerow([
                requested_at, res["name"], res["seg_id"],
                "", res.get("action", ""), res["status"], res["url"], res["error"],
            ])
    print(f"\nresult CSV: {csv_path}")

    def _is_ok(r):
        s = r.get("status", "")
        return s.startswith("200") or s.startswith("201") or s == "NOOP"
    ok = sum(1 for r in results if _is_ok(r))
    skip = sum(1 for r in results if r.get("status") == "SKIP")
    fail = len(results) - ok - skip
    n_update = sum(1 for r in results if r.get("action") == "update")
    n_create = sum(1 for r in results if r.get("action") == "create")
    n_noop = sum(1 for r in results if r.get("action") == "noop")
    print(f"[summary] 성공: {ok}, 실패: {fail}, skip: {skip}  (update: {n_update} / create: {n_create} / noop: {n_noop})")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())