# input_csv_maker.py
# 2026-05-15  Jonghyun Park w/ Claude
"""
seg_make_ref_*.csv → aa_create_segment_v2_1.py 가 받는 input CSV 자동 변환.

처리 대상 — eVar25/26/35/48 의 *_event-exists 컬럼 중 **1개라도 TRUE** 인 row.

변환 룰:
  · Segment Name 의 [CAMPAIGN NAME] → [CAMPAIGN NAME] 변환 (NAME_CAMPAIGN_BEFORE/AFTER 로 조정)
  · 공통 컨테이너 = @<COMMON_SEGMENT_REF>  (segment-ref 한 줄)
  · 각 eVar 의 _event-exists=TRUE 블록 빌드:
      - 값(줄바꿈 split) 의 공통 substring (LCS, ≥ MIN_LCS_LENGTH 자) → 'vN'!hit(eventN event-exists AND evarN contains '<lcs>')
      - 공통 없음 → '특이사항'!hit(eventN event-exists AND evarN contains-any-of ['v1','v2',...])
      - 값이 비어있고 event-exists 만 있음 → 'vN'!hit(eventN event-exists)
  · customlink → 'Component'!hit(customlink starts-with '<customlink>')

출력 CSV — v2_1 형식: segment_id, name, description, rsid, tags, structure
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

SEG_MAKE_REF_CSV = ""   # 빈 값이면 폴더 내 seg_make_ref_*.csv 파일명 사전순 최신 1개 자동 선택. 특정 파일 강제 지정 시 파일명 박기.

# 공통 컨테이너 segment ID (segment-ref 로 참조될 ID)
COMMON_SEGMENT_REF = "segment_id_placeholder"

# 이름 변환 룰 (캠페인 시즌 변경 시)
NAME_CAMPAIGN_BEFORE = "[CAMPAIGN NAME]"
NAME_CAMPAIGN_AFTER  = "[CAMPAIGN NAME]"

# 공통 substring 최소 길이 — 이 길이 미만은 generic 으로 간주, 특이사항 컨테이너로 강제
MIN_LCS_LENGTH = 4

# 한 seg_make_ref row 를 어떻게 segment 로 만들지:
#   "both"  : visit 버전 + hit 버전 두 segment 자동 생성 (default, 사용자 운영 룰)
#               · visit 버전 — visit( hit( @공통ref AND ... ) ),  이름 뒤 ' (Visit)' suffix
#               · hit  버전 — hit( ... ),  공통 ref 없음,  이름 suffix 없음
#   "visit" : visit 버전만 (한 segment)
#   "hit"   : hit 버전만 (한 segment, 공통 ref 없음)
SCOPE_MODE = "both"
# SCOPE_MODE = "hit"
# SCOPE_MODE = "visit"

DEFAULT_RSID = "rsid_placeholder"
DEFAULT_TAGS = ""

# 출력 파일명 (timestamp 자동) — csv + dsl 두 파일 같이 생성
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_NAME_TEMPLATE     = "segments_input_{ts}.csv"
OUTPUT_DSL_NAME_TEMPLATE = "segments_input_{ts}.dsl"
OUTPUT_WARN_NAME_TEMPLATE = "segments_input_{ts}_WARN.csv"   # 검수 경고 결과

# ─── 검수 옵션 ──────────────────────────────────────────────────
# customlink 중복 감지 — 같은 customlink 를 여러 row 가 쓰면 segment 들이 의도치 않게 겹칠 수 있음.
# True 면 input csv 생성 후 콘솔 경고 + 별도 WARN.csv 출력.
WARN_DUPLICATE_CUSTOMLINK = True

# eVar 값 중복 감지 — 같은 customlink + 같은 eVar 값 조합인 row 들도 경고 (더 엄격)
WARN_DUPLICATE_CUSTOMLINK_AND_EVAR = True

# eVar<N>_event-exists 컬럼이 csv 헤더에 있는 evar 번호 → 'event-exists + value' 한 묶음으로 메인 evar 블록 생성.
# (예: eVar26_event-exists 컬럼이 있으면 eVar26 도 같이 본 후 evar 블록 빌드)
# 그 외 prop<N> / evar<N> (event-exists 컬럼 없음) → site 컨테이너 (별도 hit-scope) 에 추가됨.
# EVAR_COLUMNS / SITE_VARS / EVAR_INLINE_NUMS 같은 hardcoded 리스트는 csv 헤더 스캔으로 동적 결정.
EVAR_EVENT_EXISTS_REGEX = r"^eVar(\d+)_event-exists$"
EVAR_VALUE_COLUMN_TEMPLATE = "eVar{num}"           # value 컬럼 이름 (case-insensitive 매칭)
ALLOWED_VAR_NUM_RANGE = range(1, 201)              # evar/prop 인식 번호 상한 (1~200, 넉넉히)

SITE_CONTAINER_NAME = "site"   # site 양수/음수 컨테이너 (prop/evar 의 hit-scope 묶음) 이름

# crystallize 컬럼 — 자동 LCS 가 못 잡거나 짧을 때 사용자가 직접 keyword 박을 수 있게 함.
# 컬럼명 패턴: <condition>_crystallize_<varname>  (예: starts_crystallize_evar26)
#   · condition: starts → starts-with, contains → contains, equals → equals
#   · varname  : evar25 / evar26 / evar35 / evar48 (prop1/evar1 는 별도 site 컬럼 사용)
# 값이 있으면 그 row 의 자동 LCS 무시하고 사용자가 박은 키워드로 해당 evar 블록 강제.
CRYSTALLIZE_CONDITION_TO_OPERATOR: dict[str, str] = {
    "starts": "starts-with",
    "contains": "contains",
    "equals": "equals",
}
CRYSTALLIZE_COLUMN_REGEX = r"^(starts|contains|equals)_crystallize_(evar\d+)$"

# Generic site / evar 필터 컬럼 — {not_있으면제외 없으면 포함}{조건}_{prop/evar}{#}
# 예: starts_prop1, not_starts_evar1, contains_evar26, not_contains_evar26
# default operator (조건 없는 옛 컬럼 prop<N>/evar<N>/not_prop<N>/not_evar<N>) → starts-with
# default 줄바꿈 multi 값 → <operator>-any-of [...] (contains 만 표준 지원, 다른 op multi 는 OR 로 묶음)
GENERIC_FILTER_REGEX = r"^(?P<neg>not_)?(?P<cond>starts|contains|equals)_(?P<var>prop|evar)(?P<num>\d+)$"
LEGACY_FILTER_REGEX  = r"^(?P<neg>not_)?(?P<var>prop|evar)(?P<num>\d+)$"   # prop1, not_prop1, evar1, not_evar1 등 (default starts-with)

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════


def find_longest_common_substring(strings: list[str]) -> str:
    """모든 string 에 공통으로 나타나는 가장 긴 substring (대소문자 그대로)."""
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0].strip()
    shortest = min(strings, key=len)
    n = len(shortest)
    for length in range(n, 0, -1):
        for start in range(n - length + 1):
            candidate = shortest[start:start + length]
            if all(candidate in s for s in strings):
                return candidate
    return ""


def split_evar_values(raw: str) -> list[str]:
    """eVar 컬럼의 줄바꿈 값들을 list 로 split. 공란 제거."""
    if not raw:
        return []
    return [v.strip() for v in re.split(r"[\r\n]+", raw) if v.strip()]


def classify_filter_column(header: str) -> tuple[bool, str, str] | None:
    """헤더 한 개를 (is_negative, operator, var_name) 로 분류.
    매치 안 되면 None — site/evar 필터 컬럼 아님 (eVar<N>_event-exists / eVar<N> value / 기타 unrelated 헤더).

    규칙 (순서대로 시도):
      1) `<not_?><cond>_<prop|evar><#>` (새 명시 형식) — 예: starts_prop1, not_contains_evar26
      2) `<not_?><prop|evar><#>` (옛 default 형식) — 예: prop1, not_evar1 → operator=starts-with
      3) eVar<N> / eVar<N>_event-exists / 그 외 → None (이 함수가 다루지 않음)
    """
    h = header.strip()
    # eVar<N>_event-exists / eVar<N>(값 컬럼) 같은 메인 evar 블록 헤더는 필터 컬럼 아님 — 우선 제외
    if re.match(EVAR_EVENT_EXISTS_REGEX, h, flags=re.IGNORECASE):
        return None
    if re.match(r"^eVar\d+$", h, flags=re.IGNORECASE):
        return None
    # 1) 새 명시 형식
    m = re.match(GENERIC_FILTER_REGEX, h, flags=re.IGNORECASE)
    if m:
        neg = bool(m.group("neg"))
        op = CRYSTALLIZE_CONDITION_TO_OPERATOR[m.group("cond").lower()]
        var = m.group("var").lower() + m.group("num")
        try:
            if int(m.group("num")) not in ALLOWED_VAR_NUM_RANGE:
                return None
        except ValueError:
            return None
        return (neg, op, var)
    # 2) 옛 default 형식 — operator 없음, default starts-with
    m = re.match(LEGACY_FILTER_REGEX, h, flags=re.IGNORECASE)
    if m:
        neg = bool(m.group("neg"))
        var = m.group("var").lower() + m.group("num")
        try:
            if int(m.group("num")) not in ALLOWED_VAR_NUM_RANGE:
                return None
        except ValueError:
            return None
        return (neg, "starts-with", var)
    return None


def _format_filter_condition(var_name: str, op: str, vals: list[str]) -> str:
    """단일 또는 multi 값 → DSL 조건 한 줄.
    · 1 개  → `<var> <op> '<v>'`
    · 2+ 개 → contains 면 `<var> contains-any-of ['v1', ...]`, 그 외 op 는 OR 로 묶음.
    """
    if len(vals) == 1:
        return f"{var_name} {op} '{vals[0]}'"
    values_str = ", ".join(f"'{v}'" for v in vals)
    if op == "contains":
        return f"{var_name} contains-any-of [{values_str}]"
    # 다른 op (starts-with, equals) 의 multi → OR 묶음
    sub = " OR ".join(f"{var_name} {op} '{v}'" for v in vals)
    return f"({sub})"


def detect_customlink_collisions(raw_rows: list[dict]) -> tuple[
    dict[str, list[str]],
    dict[tuple, list[str]],
    dict[tuple[str, str], str],
]:
    """seg_make_ref 의 원본 row 들 사이에서 customlink 중복 감지.

    같은 Segment Name 안에서 customlink 가 여러 개로 나뉘어 있는 것은 의도된 그룹(OR 로 묶일 예정) 이라
    충돌이 아님. 따라서 **다른 Segment Name 사이에서** 동일 customlink (또는 customlink+eVar+site 조합) 가
    겹치는 경우만 진짜 충돌로 본다.

    raw_rows 에 박힌 main pass 산출물 (`_customlink`, `_seg_name`, `_evar_lcs`, `_site_pos`, `_site_neg`,
    `_evar_extras`) 만 활용 — 헤더 이름 hardcoded 안 함 (1~200 어느 prop/evar 든 generic 처리).

    returns:
      cl_dup      : {customlink: [Segment Name, ...]}   2 개 이상의 distinct name 이 동일 cl 사용
      cl_evar_dup : {(customlink, eN=<lcs>, +var/op/vals, ...): [Segment Name, ...]}  조합 정확 일치 (더 엄격)
      site_map    : {(customlink, Segment Name): "prop1 starts-with au; NOT evar1 starts-with au" 등}
                    cl_dup 검수 시 같은 customlink 라도 site 필터로 실질 분리된 케이스 표시용.
    """
    from collections import defaultdict
    by_cl: dict[str, set[str]] = defaultdict(set)
    by_combo: dict[tuple, set[str]] = defaultdict(set)
    site_labels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in raw_rows:
        cl = (r.get("_customlink") or r.get("customlink") or "").strip()
        name = (r.get("_seg_name") or r.get("Segment Name") or "").strip()
        if not cl or not name:
            continue
        by_cl[cl].add(name)
        combo_parts: list = [cl]
        # evar LCS (메인 블록 핵심 keyword)
        evar_lcs = r.get("_evar_lcs") or {}
        for num in sorted(evar_lcs.keys()):
            combo_parts.append(f"e{num}={evar_lcs[num]}")
        # site filter 양수 / 음수
        site_pos = r.get("_site_pos") or []
        site_neg = r.get("_site_neg") or []
        for op, var, vals in site_pos:
            combo_parts.append(f"+{var}/{op}/{','.join(vals)}")
        for op, var, vals in site_neg:
            combo_parts.append(f"-{var}/{op}/{','.join(vals)}")
        # evar 블록 inline 추가 조건
        evar_extras = r.get("_evar_extras") or {}
        for num in sorted(evar_extras.keys()):
            for is_neg, op, vals in evar_extras[num]:
                tag = "-" if is_neg else "+"
                combo_parts.append(f"{tag}evar{num}/{op}/{','.join(vals)}")
        by_combo[tuple(combo_parts)].add(name)
        # site label (WARN.csv site_filter 컬럼 — DUP_CUSTOMLINK 검수에 표시)
        label_parts: list[str] = []
        for op, var, vals in site_pos:
            for v in vals:
                label_parts.append(f"{var} {op} {v}")
        for op, var, vals in site_neg:
            for v in vals:
                label_parts.append(f"NOT {var} {op} {v}")
        site_labels[(cl, name)].add("; ".join(label_parts))

    cl_dup = {cl: sorted(names) for cl, names in by_cl.items() if len(names) >= 2}
    cl_evar_dup = {combo: sorted(names) for combo, names in by_combo.items() if len(names) >= 2}
    site_map: dict[tuple[str, str], str] = {
        key: ", ".join(sorted(labels)) for key, labels in site_labels.items()
    }
    return cl_dup, cl_evar_dup, site_map


def transform_name(name: str) -> str:
    """[CAMPAIGN NAME] → [CAMPAIGN NAME] 변환."""
    return name.replace(NAME_CAMPAIGN_BEFORE, NAME_CAMPAIGN_AFTER)


def derive_name_with_suffix(base_name: str, structure_oneline: str) -> str:
    """structure 의 root scope (visit/hit/visitor) 에 따라 이름 suffix 자동 결정.
    · visit(   → name + ' (Visit)'
    · visitor( → name + ' (Visitor)'
    · hit(     → name 그대로 (suffix 없음)
    이미 같은 suffix 가 있으면 중복 추가 안 함."""
    first = structure_oneline.split(" | ", 1)[0].strip()
    if first.startswith("visitor("):
        suffix = " (Visitor)"
    elif first.startswith("visit("):
        suffix = " (Visit)"
    else:
        return base_name
    if base_name.endswith(suffix):
        return base_name
    return base_name + suffix


def build_evar_block(evar_num: int, values: list[str],
                     crystallize_override: tuple[str, str] | None = None,
                     extra_conditions: list[tuple[bool, str, list[str]]] | None = None) -> str:
    """한 eVarN 의 DSL 블록 (한 줄, ' | ' 구분).
    · crystallize_override = (operator, value) → 자동 LCS 무시, 사용자 키워드 강제
    · 값 없음 → 'vN'!hit( eventN event-exists )
    · 공통 substring 있음 (≥ MIN_LCS_LENGTH) → 'vN'!hit( eventN event-exists AND evarN contains '<lcs>' )
    · 공통 없음 → '특이사항'!hit( eventN event-exists AND evarN contains-any-of ['v1','v2',...] )

    extra_conditions: list of (is_negative, operator, values) — 메인 블록 안에 inline AND / AND NOT 추가.
       (못 잡힌 keyword 보정·제외 케이스. 예: not_contains_evar26 = ':tab' → AND NOT (evar26 contains ':tab'))
    """
    tokens: list[str]
    if crystallize_override:
        op, val = crystallize_override
        tokens = [
            f"'v{evar_num}'!hit(",
            f"event{evar_num} event-exists",
            "AND",
            f"evar{evar_num} {op} '{val}'",
        ]
    elif not values:
        tokens = [
            f"'v{evar_num}'!hit(",
            f"event{evar_num} event-exists",
        ]
    else:
        lcs = find_longest_common_substring(values).strip()
        if len(lcs) >= MIN_LCS_LENGTH:
            tokens = [
                f"'v{evar_num}'!hit(",
                f"event{evar_num} event-exists",
                "AND",
                f"evar{evar_num} contains '{lcs}'",
            ]
        else:
            # 공통 없음 → 특이사항 컨테이너
            values_str = ", ".join(f"'{v}'" for v in values)
            tokens = [
                "'특이사항'!hit(",
                f"event{evar_num} event-exists",
                "AND",
                f"evar{evar_num} contains-any-of [{values_str}]",
            ]

    # inline extra_conditions — 메인 블록 안에 AND / AND NOT 추가
    if extra_conditions:
        var_name = f"evar{evar_num}"
        for is_neg, op, vals in extra_conditions:
            vals_clean = [v for v in (vals or []) if v]
            if not vals_clean:
                continue
            cond_str = _format_filter_condition(var_name, op, vals_clean)
            tokens.append("AND")
            tokens.append(f"NOT ({cond_str})" if is_neg else cond_str)

    tokens.append(")")
    return " | ".join(tokens)


def structure_oneline_to_multiline(s: str) -> str:
    """한 줄 ' | ' → 멀티라인 + 들여쓰기. dsl 출력용."""
    parts = [p.strip() for p in s.split(" | ") if p.strip()]
    indent = 0
    out: list[str] = []
    for p in parts:
        # 닫는 괄호로 시작하면 indent 먼저 감소
        if p.startswith(")"):
            indent = max(0, indent - 1)
        out.append("  " * indent + p)
        # 여는 괄호로 끝나면 indent 증가
        if p.endswith("("):
            indent += 1
    return "\n".join(out)


def build_dsl_block(name: str, description: str, rsid: str, tags: str,
                    structure_oneline: str) -> str:
    """segment_lookup 의 .dsl 형식과 동일한 한 segment 블록."""
    parts = ["--- segment"]
    parts.append(f"name: {name}")
    if description:
        parts.append(f"description: {description}")
    parts.append(f"rsid: {rsid}")
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            parts.append(f"tags: [{', '.join(tag_list)}]")
    parts.append("")
    parts.append(structure_oneline_to_multiline(structure_oneline))
    return "\n".join(parts)


def _build_site_container_inner(filters: list[tuple[str, str, list[str]]]) -> str:
    """site 컨테이너 내부 — 여러 (operator, var_name, values) 를 OR 로 묶음.
    각 항목은 _format_filter_condition 으로 한 줄 만들고, 여러 항목이면 그 사이에 OR 토큰."""
    lines: list[str] = []
    for op, var, vals in (filters or []):
        vals_clean = [v for v in vals if v]
        if not vals_clean:
            continue
        cond_str = _format_filter_condition(var, op, vals_clean)
        if lines:
            lines.append("OR")
        lines.append(cond_str)
    return " | ".join(lines)


def build_customlink_block(customlink: str, evar_blocks: list[str],
                           site_pos: list[tuple[str, str, list[str]]] | None = None,
                           site_neg: list[tuple[str, str, list[str]]] | None = None) -> str:
    """한 customlink + eVar 조건 (+ 선택 site 양수/음수 필터) 의 inner hit 블록.

    구조:
      hit(
        'Component'!hit( customlink starts-with '<cl>' )
        AND  <eVar 블록들 ...>
        AND  '<SITE>'!hit( <site_pos OR 묶음> )      # 양수
        AND  not '<SITE>'!hit( <site_neg OR 묶음> )  # 음수 (de Morgan 으로 둘 다 starts-with 아님 의미)
      )

    site_pos / site_neg 형식: list of (operator, var_name, values_list)
      예: [("starts-with", "prop1", ["au"]), ("starts-with", "evar1", ["au"])]
    여러 항목 OR 로 묶임. 빈 list 면 해당 컨테이너 추가 안 함.
    """
    parts: list[str] = ["hit("]
    has_first = False
    if customlink:
        parts.extend([
            "'Component'!hit(",
            f"customlink starts-with '{customlink}'",
            ")",
        ])
        has_first = True
    # eVar 블록들 — 첫 토큰 앞엔 AND 없음, 이후엔 AND
    for block in evar_blocks:
        if has_first:
            parts.append("AND")
        parts.append(block)
        has_first = True
    # 양수 site 필터
    pos_inner = _build_site_container_inner(site_pos or [])
    if pos_inner:
        site_block = f"'{SITE_CONTAINER_NAME}'!hit( | {pos_inner} | )"
        if has_first:
            parts.append("AND")
        parts.append(site_block)
        has_first = True
    # 음수 site 필터 — 양수 컨테이너를 'not' 으로 감쌈
    neg_inner = _build_site_container_inner(site_neg or [])
    if neg_inner:
        not_site_block = f"not '{SITE_CONTAINER_NAME}'!hit( | {neg_inner} | )"
        if has_first:
            parts.append("AND")
        parts.append(not_site_block)
        has_first = True
    parts.append(")")
    return " | ".join(parts)


def build_structure(name: str, customlink_blocks: list[str],
                    root_scope: str = "visit") -> str:
    """전체 structure 한 줄 ' | ' 구분.

    customlink_blocks 가 1 개여도 (안전성·시각 일관성 위해) 항상 paren grouping 으로 감쌈.
    2 개 이상이면 OR 로 엮음.

    root_scope:
      · "visit" / "visitor" → 공통 ref AND ( '<name>'!hit( <blocks 또는 OR> ) ) 로 묶음
                              → visit( hit( @공통ref AND ( '<name>'!hit( ... ) ) ) )
      · "hit"               → 공통 ref 없이 단독 → hit( '<name>'!hit( <blocks 또는 OR> ) )"""
    parts: list[str] = []
    closing: list[str] = []
    if root_scope == "hit":
        parts.append("hit(")
        parts.append(f"'{name}'!hit(")
        closing = [")", ")"]
    else:
        parts.append(f"{root_scope}(")
        parts.append("hit(")
        parts.append(f"@{COMMON_SEGMENT_REF}")
        parts.append("AND")
        parts.append("(")                       # ← 추가 paren grouping 열기 (수동편집예시 .dsl 형식)
        parts.append(f"'{name}'!hit(")
        closing = [")", ")", ")", ")"]          # ← name-container 닫기 + paren grouping 닫기 + hit 닫기 + root 닫기

    # customlink 블록들 — 1 개면 그대로, 2+ 면 OR 로 엮음
    for i, block in enumerate(customlink_blocks):
        if i > 0:
            parts.append("OR")
        parts.append(block)

    parts.extend(closing)
    return " | ".join(parts)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="seg_make_ref CSV → v2_1 input CSV 자동 변환")
    parser.add_argument("--input", default=SEG_MAKE_REF_CSV,
                        help="input CSV. 빈 값이면 폴더에서 seg_make_ref_*.csv 사전순 최신 1개 자동 선택.")
    args = parser.parse_args()

    ts = datetime.now().strftime("%y%m%d_%H%M")
    out_path     = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(ts=ts)
    out_dsl_path  = OUTPUT_DIR / OUTPUT_DSL_NAME_TEMPLATE.format(ts=ts)
    out_warn_path = OUTPUT_DIR / OUTPUT_WARN_NAME_TEMPLATE.format(ts=ts)

    # ─── input CSV 결정 ─────────────────────────────────────
    # SEG_MAKE_REF_CSV (또는 --input) 가 비어 있으면 OUTPUT_DIR 의 seg_make_ref_*.csv 중
    # 파일명 사전순 최신 1 개 자동 선택. 파일명 timestamp 는 _YYMMDD_HHMM 이므로
    # 사전순 정렬 = 시간순 정렬 (mtime 보다 안정적 — OneDrive 동기화·복사 시 mtime 이 어긋날 수 있어서).
    input_arg = (args.input or "").strip()
    if not input_arg:
        candidates = sorted(OUTPUT_DIR.glob("seg_make_ref_*.csv"), reverse=True)
        if not candidates:
            print(f"ERROR: seg_make_ref_*.csv 못 찾음 — {OUTPUT_DIR}")
            return 1
        src_path = candidates[0]
        print(f"  [auto-latest] {src_path.name}  (총 {len(candidates)} 개 후보 중 최신)")
    else:
        src_path = Path(input_arg)
        if not src_path.is_absolute():
            src_path = OUTPUT_DIR / src_path
        if not src_path.exists():
            print(f"ERROR: input CSV 못 찾음 — {src_path}")
            return 1

    rows_out: list[dict] = []
    raw_rows: list[dict] = []     # 검수용 — seg_make_ref 의 원본 row 들 보관 (skip 제외 후)
    skipped: list[tuple[str, str]] = []
    n_with_common: dict[int, int] = {}    # evar_num → 공통 추출 성공 count
    n_with_quirk:  dict[int, int] = {}    # evar_num → 특이사항 컨테이너 count

    with open(src_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        fieldnames_lower = {h.strip().lower(): h for h in fieldnames}

        # eVar<N>_event-exists 컬럼 동적 인식 → evar_event_cols = [(exists_col, value_col, num), ...]
        # 같은 N 의 value 컬럼 (eVar<N>) 도 case-insensitive 로 매칭. 둘 다 있어야 처리 대상.
        evar_event_cols: list[tuple[str, str, int]] = []
        inline_evar_nums: set[int] = set()
        for hdr in fieldnames:
            m = re.match(EVAR_EVENT_EXISTS_REGEX, hdr.strip(), flags=re.IGNORECASE)
            if not m:
                continue
            num = int(m.group(1))
            if num not in ALLOWED_VAR_NUM_RANGE:
                continue
            value_col_key = EVAR_VALUE_COLUMN_TEMPLATE.format(num=num).lower()
            value_col = fieldnames_lower.get(value_col_key)
            if value_col is None:
                continue
            evar_event_cols.append((hdr, value_col, num))
            inline_evar_nums.add(num)
        if evar_event_cols:
            print(f"  [evar-event] 인식된 evar 블록 대상: " +
                  ", ".join(f"eVar{n}" for _, _, n in evar_event_cols))

        # crystallize 컬럼 매핑 — {evarN: (operator, column_name)} (값 있으면 자동 LCS override)
        crystallize_map: dict[str, tuple[str, str]] = {}
        for hdr in fieldnames:
            m = re.match(CRYSTALLIZE_COLUMN_REGEX, hdr.strip(), flags=re.IGNORECASE)
            if m:
                cond, varname = m.group(1).lower(), m.group(2).lower()
                op = CRYSTALLIZE_CONDITION_TO_OPERATOR[cond]
                crystallize_map[varname] = (op, hdr)
        if crystallize_map:
            print(f"  [crystallize] 인식된 override 컬럼: " +
                  ", ".join(f"{v}→{op}({col})" for v, (op, col) in crystallize_map.items()))

        # site/evar 필터 컬럼 분류 — {header: (is_neg, operator, var_name)}
        # 옛 (prop<N>/evar<N>/not_prop<N>/not_evar<N>, default starts-with) + 새 ({not_}<cond>_(prop|evar)<#>)
        filter_columns: dict[str, tuple[bool, str, str]] = {}
        for hdr in fieldnames:
            cls = classify_filter_column(hdr)
            if cls:
                filter_columns[hdr] = cls
        if filter_columns:
            descs = ", ".join(
                f"{h}→{'NOT ' if n else ''}{v} {o}" for h, (n, o, v) in filter_columns.items()
            )
            print(f"  [filter] 인식된 site/evar 필터 컬럼: {descs}")

        from collections import defaultdict as _dd
        for row in reader:
            seg_name = (row.get("Segment Name") or "").strip()
            customlink = (row.get("customlink") or "").strip()
            if not seg_name:
                skipped.append((seg_name, "name 없음"))
                continue

            # 각 row 의 filter 컬럼 값 분류 → site_pos / site_neg / evar_extras
            #   site_pos / site_neg : list of (operator, var_name, values_list)
            #   evar_extras         : dict[evar_num → list of (is_neg, operator, values_list)] (inline 추가 조건)
            site_pos: list[tuple[str, str, list[str]]] = []
            site_neg: list[tuple[str, str, list[str]]] = []
            evar_extras: dict[int, list[tuple[bool, str, list[str]]]] = _dd(list)
            for hdr, (is_neg, op, var) in filter_columns.items():
                raw_val = (row.get(hdr) or "").strip()
                if not raw_val:
                    continue
                vals = split_evar_values(raw_val)
                if var.startswith("prop"):
                    # prop<N> — 모두 site 컨테이너 (별도 hit-scope) 로
                    target = site_neg if is_neg else site_pos
                    target.append((op, var, vals))
                elif var.startswith("evar"):
                    try:
                        num = int(var[4:])
                    except ValueError:
                        continue
                    if num in inline_evar_nums:
                        # evar<N> with event-exists 컬럼 → 메인 evar 블록 안에 inline AND/AND NOT
                        evar_extras[num].append((is_neg, op, vals))
                    else:
                        # evar<N> without event-exists 컬럼 → site 컨테이너로
                        target = site_neg if is_neg else site_pos
                        target.append((op, var, vals))

            # _event-exists TRUE 인 eVar 수집 (crystallize + evar_extras 같이 전달)
            evar_blocks: list[str] = []
            evar_lcs_per_row: dict[int, str] = {}   # 검수 combo_key 용 — 각 evar 의 LCS 또는 override 표시
            for exists_col, val_col, num in evar_event_cols:
                flag = (row.get(exists_col) or "").strip().upper()
                if flag != "TRUE":
                    continue
                values = split_evar_values(row.get(val_col) or "")
                cry_override: tuple[str, str] | None = None
                cry_entry = crystallize_map.get(f"evar{num}")
                if cry_entry:
                    op, col_name = cry_entry
                    cry_val = (row.get(col_name) or "").strip()
                    if cry_val:
                        cry_override = (op, cry_val)
                # 통계 — 공통/특이/override 추적 + LCS 저장 (검수 combo_key 용)
                if cry_override:
                    n_with_common[num] = n_with_common.get(num, 0) + 1
                    evar_lcs_per_row[num] = f"override:{cry_override[1]}"
                elif values:
                    lcs = find_longest_common_substring(values).strip()
                    if len(lcs) >= MIN_LCS_LENGTH:
                        n_with_common[num] = n_with_common.get(num, 0) + 1
                        evar_lcs_per_row[num] = lcs
                    else:
                        n_with_quirk[num] = n_with_quirk.get(num, 0) + 1
                        evar_lcs_per_row[num] = "(no-lcs)"
                else:
                    evar_lcs_per_row[num] = "(no-vals)"
                evar_blocks.append(build_evar_block(num, values, cry_override, evar_extras.get(num)))

            if not evar_blocks:
                skipped.append((seg_name, "_event-exists TRUE 컬럼 0 개"))
                continue

            # 검수용 raw row 저장 (skip 안 된 것만)
            raw_rows.append(row)

            # raw row 와 build 재료 임시 보관 (warning 은 검수 단계 후 채움)
            raw_rows[-1]["_seg_name"] = seg_name
            raw_rows[-1]["_customlink"] = customlink
            raw_rows[-1]["_evar_blocks"] = evar_blocks
            raw_rows[-1]["_site_pos"] = site_pos
            raw_rows[-1]["_site_neg"] = site_neg
            raw_rows[-1]["_evar_extras"] = dict(evar_extras)
            raw_rows[-1]["_evar_lcs"] = evar_lcs_per_row

    # ─── 검수 — customlink 중복 감지 (rows_out 빌드 전에 먼저) ──
    cl_dup: dict[str, list[str]] = {}
    cl_evar_dup: dict[tuple, list[str]] = {}
    site_map: dict[tuple[str, str], str] = {}
    if WARN_DUPLICATE_CUSTOMLINK or WARN_DUPLICATE_CUSTOMLINK_AND_EVAR:
        cl_dup, cl_evar_dup, site_map = detect_customlink_collisions(raw_rows)

    # Segment Name 기준 warning 텍스트 (결과 csv 의 warning 컬럼에 박힘 — 수동 편집용 마커)
    warnings_by_name: dict[str, list[str]] = {}
    if WARN_DUPLICATE_CUSTOMLINK and cl_dup:
        for cl, names in cl_dup.items():
            for nm in names:
                others = [n for n in names if n != nm]
                warnings_by_name.setdefault(nm, []).append(
                    f"DUP_CUSTOMLINK with: {', '.join(others)}"
                )
    if WARN_DUPLICATE_CUSTOMLINK_AND_EVAR and cl_evar_dup:
        for combo, names in cl_evar_dup.items():
            for nm in names:
                others = [n for n in names if n != nm]
                combo_str = " | ".join(combo)
                warnings_by_name.setdefault(nm, []).append(
                    f"DUP_CL_EVAR ({combo_str}) with: {', '.join(others)}"
                )

    # ─── 2차 패스 — Segment Name 기준 그룹화 후 rows_out 빌드 ─────
    # 같은 Segment Name 의 customlink 블록들을 OR 로 엮어 1 개 segment (또는 SCOPE_MODE=both 면 visit+hit 2 개).
    # raw csv 가 customlink 별로 row 를 쪼개 놓은 케이스 ([CAMPAIGN NAME] CC_03. ... 처럼 2 customlink 인데 4 row) 라도
    # 출력은 customlink 블록 OR 로 합쳐서 segment name 당 1~2 개로 통합됨.
    from collections import OrderedDict
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in raw_rows:
        raw_seg_name = (r.get("Segment Name") or "").strip()
        groups.setdefault(raw_seg_name, []).append(r)

    for raw_seg_name, members in groups.items():
        # 각 member row → 단일 hit 블록 (customlink + 해당 row 의 evar 조건들 + prop1/evar1 site 필터)
        customlink_blocks: list[str] = []
        for m in members:
            customlink_blocks.append(build_customlink_block(
                m["_customlink"], m["_evar_blocks"],
                site_pos=m.get("_site_pos") or [],
                site_neg=m.get("_site_neg") or [],
            ))

        new_name_base = transform_name(raw_seg_name)
        warning_text  = " ; ".join(warnings_by_name.get(raw_seg_name, []))

        if SCOPE_MODE in ("visit", "both"):
            visit_name = (new_name_base + " (Visit)"
                          if not new_name_base.endswith(" (Visit)") else new_name_base)
            visit_structure = build_structure(visit_name, customlink_blocks, root_scope="visit")
            rows_out.append({
                "segment_id": "",
                "name": visit_name,
                "description": "",
                "rsid": DEFAULT_RSID,
                "tags": DEFAULT_TAGS,
                "structure": visit_structure,
                "warning": warning_text,
            })
        if SCOPE_MODE in ("hit", "both"):
            hit_name = new_name_base
            hit_structure = build_structure(hit_name, customlink_blocks, root_scope="hit")
            rows_out.append({
                "segment_id": "",
                "name": hit_name,
                "description": "",
                "rsid": DEFAULT_RSID,
                "tags": DEFAULT_TAGS,
                "structure": hit_structure,
                "warning": warning_text,
            })

    # 출력 — csv (warning 컬럼 포함. aa_create_segment_v2_1.py 는 warning 무시)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["segment_id", "name", "description", "rsid", "tags", "structure", "warning"])
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

    # 출력 — dsl (segment_lookup .dsl 와 동일 형식. 시각 확인용. warning 은 dsl 에 안 박음)
    with open(out_dsl_path, "w", encoding="utf-8") as f:
        blocks: list[str] = []
        for r in rows_out:
            blocks.append(build_dsl_block(
                name=r["name"],
                description=r["description"],
                rsid=r["rsid"],
                tags=r["tags"],
                structure_oneline=r["structure"],
            ))
        f.write("\n\n".join(blocks) + "\n")

    print(f"[{ts}] input_csv_maker.py")
    print(f"  입력: {src_path.name}")
    print(f"  raw rows: {len(raw_rows)}  →  Segment Names: {len(groups)} (그룹화)  →  output: {len(rows_out)} row  (SCOPE_MODE={SCOPE_MODE!r})")
    print(f"  출력 csv: {out_path.name}  (warning 컬럼 포함)")
    print(f"  출력 dsl: {out_dsl_path.name}  (시각 확인용)")

    # ─── 별도 WARN.csv (상세 검수 결과 — inline warning 컬럼과 동일 정보) ─
    if WARN_DUPLICATE_CUSTOMLINK or WARN_DUPLICATE_CUSTOMLINK_AND_EVAR:
        warning_rows: list[dict] = []

        if WARN_DUPLICATE_CUSTOMLINK and cl_dup:
            print(f"\n⚠️ customlink 중복 감지 ({len(cl_dup)} 개의 customlink 가 2 개 이상 segment 에 사용):")
            for cl, names in sorted(cl_dup.items(), key=lambda x: -len(x[1])):
                # site 필터 비교 — distinct site_label set 이 2 개 이상이면 실질 분리 가능 표시
                site_set = {site_map.get((cl, n), "") for n in names}
                separated = len(site_set) >= 2 and "" not in site_set
                sep_note = "  (site 필터로 실질 분리됨 — 충돌 아님)" if separated else ""
                print(f"   customlink={cl!r}  → {len(names)} segments{sep_note}:")
                for nm in names:
                    sf = site_map.get((cl, nm), "")
                    print(f"     - {nm}   site_filter=[{sf}]" if sf else f"     - {nm}")
                    warning_rows.append({
                        "warning_type": "DUP_CUSTOMLINK",
                        "customlink": cl,
                        "site_filter": sf,
                        "evar_combo": "",
                        "segment_name": nm,
                        "duplicates_with": "; ".join(n for n in names if n != nm),
                        "note": "site 필터로 실질 분리됨" if separated else "",
                    })

        if WARN_DUPLICATE_CUSTOMLINK_AND_EVAR and cl_evar_dup:
            print(f"\n⚠️ customlink + eVar (+site) 조합 정확 일치 감지 ({len(cl_evar_dup)} 조합 — 더 엄격한 충돌):")
            for combo, names in sorted(cl_evar_dup.items(), key=lambda x: -len(x[1])):
                combo_str = " | ".join(combo)
                cl = combo[0] if combo else ""
                print(f"   조합={combo_str}")
                for nm in names:
                    sf = site_map.get((cl, nm), "")
                    print(f"     - {nm}")
                    warning_rows.append({
                        "warning_type": "DUP_CL_EVAR",
                        "customlink": cl,
                        "site_filter": sf,
                        "evar_combo": " | ".join(combo[1:]) if len(combo) > 1 else "",
                        "segment_name": nm,
                        "duplicates_with": "; ".join(n for n in names if n != nm),
                        "note": "",
                    })

        if warning_rows:
            with open(out_warn_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=["warning_type", "customlink", "site_filter", "evar_combo", "segment_name", "duplicates_with", "note"])
                w.writeheader()
                for r in warning_rows:
                    w.writerow(r)
            print(f"\n  ⚠️ 별도 WARN.csv: {out_warn_path.name}  ({len(warning_rows)} 행)")
            print(f"  ※ site_filter 컬럼이 비어있지 않고 row 마다 다르면 → 실질 분리됨 (note 컬럼에 표시).")
            print(f"  ※ 결과 csv 의 warning 컬럼에도 같은 정보가 row 별로 표시됨 — 수동 편집 후 v2_1 에 input.")
        else:
            print(f"\n  ✓ customlink 중복 없음 (검수 통과)")
    print(f"  skip: {len(skipped)} row")
    if skipped[:5]:
        for name, reason in skipped[:5]:
            print(f"     - {name[:60]}  ({reason})")
        if len(skipped) > 5:
            print(f"     ... +{len(skipped) - 5}건 더")
    print()
    print(f"  공통 substring 추출 성공 (≥{MIN_LCS_LENGTH}자):")
    for num in sorted(set(n_with_common) | set(n_with_quirk)):
        ok = n_with_common.get(num, 0)
        quirk = n_with_quirk.get(num, 0)
        print(f"     eVar{num}: 공통 {ok} / 특이사항 {quirk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
