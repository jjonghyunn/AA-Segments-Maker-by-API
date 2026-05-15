# aa_create_segment_v2.py
# 2026-05-15  Jonghyun Park w/ Claude
# updated: 2026-05-15 12:45  — OWNER_ID 기본값 설정 + 팀원 ID 주석 추가
"""
DSL(간결한 텍스트 문법)로 Adobe Analytics 세그먼트를 정의하고
AA API용 JSON으로 자동 변환 + 다중 세그먼트 일괄 생성.

사용법:
  python aa_create_segment_v2.py                    # dry-run (JSON 출력만)
  python aa_create_segment_v2.py --apply            # 실제 POST
  python aa_create_segment_v2.py --input my.dsl     # 입력 파일 지정

DSL 문법 예시:
  --- segment
  name: [CAMPAIGN NAME] Campaign Main Page_Prop
  description: Campaign main page visit
  rsid: rsid_placeholder
  tags: [campaign, md]

  hit(
    page contains "campaign_name"
    AND NOT page contains-any-of ["whatsapp", "explore"]
  )
"""
from __future__ import annotations

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
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
# Mac: AUTH_JSON_PATH = str(Path(__file__).resolve().parent.parent / "aa_auth.json")
COMPANY_ID = "company_id"

# ─── 본인 식별 (segment owner) ────────────────────────────────────
# OWNER_ID 에 numeric loginId 직접 지정하면 API lookup 생략.
# 팀원 loginId 목록 (add_segment_shares.py 기준):
#   000000001  user1_login   (Jonghyun Park)
#   YOUR_LOGIN_ID  user2_login     (User2 Name)
#   YOUR_LOGIN_ID  user3_login  (User3 Name)
#   YOUR_LOGIN_ID  user4_login         (User4 Name)
#   YOUR_LOGIN_ID  user5_login     (User5 Name)
#   YOUR_LOGIN_ID  user6_login      (User6 Name)
#   YOUR_LOGIN_ID  user7_login   (User7 Name)
OWNER_ID: int | None = 000000001  # user1_login
OWNER_IMS_USER_ID: str = "YOUR_IMS_USER_ID"
OWNER_LOGIN: str = "user1_login"

# ─── 기본 RSID (segment별 rsid 미지정 시 사용) ───────────────────
DEFAULT_RSID = "rsid_placeholder"

# ─── 입력 파일 ─────────────────────────────────────────────────────
INPUT_FILE = "segments.dsl"

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_CSV_PREFIX = "segment_v2_result_"

UI_URL_TEMPLATE = (
    "https://experience.adobe.com/#/@company_name/so:company_id/"
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
    segment_id: str         # "s200001591_abc123"
    negated: bool = False
    line: int = 0


@dataclass
class SequenceNode:
    steps: list = field(default_factory=list)  # 각 step: ConditionNode | LogicalNode | ContainerNode


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
            child_lines = _decompile_pred(p, indent, parent_context)
            if i > 0:
                lines.append(f"{pad}{func.upper()}")
                lines.extend(child_lines)
            else:
                lines.extend(child_lines)
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


if __name__ == "__main__":
    sys.exit(main())
