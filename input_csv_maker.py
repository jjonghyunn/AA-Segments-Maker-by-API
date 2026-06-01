# input_csv_maker.py
# 2026-06-01  Jonghyun Park w/ Claude
# updated: 2026-06-01  v1.7 — build_customlink_block: customlink 멀티값(줄바꿈 split) 지원 — 한 컨테이너에 여러 customlink 를 OR 로 묶고 라벨은 코드들 ' or ' 조합('pd25 or ft31 component'). 바깥 container_label 도 멀티 customlink 코드 반영.
# updated: 2026-06-01  v1.6 — 컨테이너 라벨 정렬: 바깥 segment 컨테이너를 customlink 코드 ' or ' 조합('pd25 or co78 component')으로(build_structure container_label). DP 는 '<base>(Visit)'!visit( hit( @common AND ( '<label>'!hit( hit(A) OR hit(B) ) ) ) ) 구조 — '<label>' 이 모든 브랜치를 감쌈(hit/visit 와 동일 대칭). customlink 서브컨테이너 'pd25 component'/'co78 component' 유지.
# updated: 2026-06-01  v1.5 — build_customlink_block: COMPONENT_LABEL_FROM_CUSTOMLINK(기본 True) 면 브랜치 컨테이너를 customlink 선두코드('pd25 component'/'co78 component')로 명명 + customlink inline (무명 hit() 래퍼 제거). hit/visit/DP 전 변형 공통 적용. False 면 기존 hit('Component'!hit(customlink)...) 구조.
# updated: 2026-06-01  v1.4 — _build_delayed_purchase_structure: customlink_block 2 개+ (OR 브랜치) 일 때 각 hit() 래퍼 유지하고 ( hit(A) OR hit(B) ) 괄호 그룹으로 묶음. 기존엔 래퍼 벗겨 평탄화 → @COMMON_REF AND A OR B 로 그룹 경계가 사라져 B 가 @ref/THEN 시퀀스에서 빠지는 버그 수정. 1 개일 땐 기존 inline 유지.
# updated: 2026-05-29  v1.2 — _build_delayed_purchase_structure 재작성: mixed-AND 패턴 + 'Order (All Products)' named container + [sequence-after]/[sequence-all] 라벨 명시. CAMPAIGN NAME US_CC_xx DP 컨벤션 따름.
# updated: 2026-05-29  v1.3 — build_structure (visit) 의 inner hit 에 'page+content' description 박음. aa_create_segment_v2.3 의 _lift_inner_hit_into_visit_root 후처리가 description 없는 단일 inner hit 을 벗기는 문제 우회 — visit(hit(AND(@page, named_content))) 구조 보존.
# updated: 2026-05-26       — crystallize: regex 에 hyphen 변형 (starts-with / contains-any-of) 매칭 추가, contains-any-of multi-value 처리 (build_evar_block)
# updated: 2026-05-27       — CRYSTALLIZE_COLUMN_REGEX_OR 추가: `or_starts-with_evar105` 같이 `_crystallize_` 키워드 없는 form 도 evar block 의 crystallize_override 로 인식 (OR group evar value condition 명시용)
# updated: 2026-05-27  v1.1 — `set_pair` 컬럼 추가. 회사 특성 (event+evar AND set) 처리.
#                              format: `e<event_num>+v<evar_num>[<operator>]` (operator 생략시 contains, default 가 contains 아닌 starts-with 인 케이스는 명시).
#                              세미콜론 multi: `e45+v33;e44+v55[starts-with]`. 빈 값 = 처리 안 함 (회귀 안전).
#                              지정된 row 에서 v<M> named container wrap 안 event<N> event-exists AND evar<M> <op> '<val>' 자동 생성 + 다른 evar_block 과 AND 강제.
#                              같은 row 의 `eVar<M>` column 값을 set block 의 value 로 재사용 (그 evar<M> 은 site filter 분류에서 제외).
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

SEG_MAKE_REF_CSV = "seg_make_ref_260526_1121.csv"   # 빈 값이면 폴더 내 seg_make_ref_*.csv 파일명 사전순 최신 1개 자동 선택. 특정 파일 강제 지정 시 파일명 박기.

# 공통 컨테이너 segment ID (segment-ref 로 참조될 ID)
# 두 가지 방법 (둘 다 동작, COMMON_SEGMENT_REF 가 우선):
#   1) 직접 박기: COMMON_SEGMENT_REF + COMMON_SEGMENT_REF_NAME 둘 다
#   2) cache lookup: REF_SEGMENT_NAME + CACHE_NAME 박으면 segment_ref_cache_<CACHE_NAME>.json 에서 자동 결정
# COMMON_SEGMENT_REF_NAME 박혀 있으면 dsl 에 named container wrap (`'<name>'!hit(@<id>)`).
COMMON_SEGMENT_REF      = "segment_id_placeholder"
COMMON_SEGMENT_REF_NAME = ""   # 예: "[CAMPAIGN NAME] Campaign Main Page_Evar" — 박혀 있으면 named container wrap
REF_SEGMENT_NAME = ""          # 예: "Campaign Main Page_Evar" — cache 에서 name partial 매칭
CACHE_NAME       = "26sw_evar_global,add_to_cart_global"   # 콤마 분리 — 두 cache 다 lookup (Campaign Main + ATC)

# 이름 변환 룰 (캠페인 시즌 변경 시)
NAME_CAMPAIGN_BEFORE = "[CAMPAIGN NAME]"
NAME_CAMPAIGN_AFTER  = "[CAMPAIGN NAME]"

# 공통 substring 최소 길이 — 이 길이 미만은 generic 으로 간주, 특이사항 컨테이너로 강제
MIN_LCS_LENGTH = 4

# 한 seg_make_ref row 를 어떻게 segment 로 만들지 — 콤마 구분으로 여러 옵션 동시 활성화.
# 옵션:
#   "visit"            → visit 버전 (visit( hit( @공통ref AND ( '<name>'!hit(...) ) ) ), 이름 뒤 ' (Visit)' suffix)
#   "hit"              → hit 버전  (hit( '<name>'!hit(...) ), 공통 ref 없음, suffix 없음)
#   "delayed_purchase" → Delayed Purchase 버전 (visit 안에 [MainPage + 본 segment + ATC + NOT orders] THEN visit(orders))
#                        ATC_VISIT_SEGMENT_REF / NAME 또는 ATC_REF_SEGMENT_NAME 박혀 있어야 동작.
# backward compat: "both" → "visit,hit"
# SCOPE_MODE = "visit,hit"
SCOPE_MODE = "visit,hit,delayed_purchase"
# SCOPE_MODE = "hit"
# SCOPE_MODE = "visit"
# SCOPE_MODE = "delayed_purchase"
# 예: SCOPE_MODE = "visit,hit,delayed_purchase"
# 예: SCOPE_MODE = "hit"

# delayed_purchase 옵션의 ATC (Add to Cart Visit) segment-ref — 글로벌은 [Global] Add to Cart Visit
ATC_VISIT_SEGMENT_REF      = ""   # 예: "YOUR_PROJECT_ID"  (직접 박기)
ATC_VISIT_SEGMENT_NAME     = ""   # 예: "[Global] Add to Cart Visit"
ATC_REF_SEGMENT_NAME       = "[Global] Add to Cart Visit"   # cache 에서 partial 매칭 → id+name 자동

DEFAULT_RSID = "rsid_placeholder"
# DEFAULT_RSID = "rsid_placeholder"
DEFAULT_TAGS = ""

# ─── evar 블록 묶음 방식 (row 별 override) ──────────────────────
# raw csv 에 'evar_join' 컬럼 있고 값이 "OR" (case-insensitive) 이면
# 그 row 의 evar_blocks 들을 named container wrap 안에 OR 로 묶음.
# 빈 값 / "AND" / 컬럼 없음 → 기본 AND 동작 (모든 evar_block 사이 AND).
# OR 그루핑이 raw paren `(...)` 으로는 v2_2 의 paren strip 에 잡혀 사라지므로
# named container wrap 으로 self-contained 형태로 보존.
EVAR_JOIN_COLUMN = "evar_join"
EVAR_JOIN_WRAP_NAME = "evar OR group"

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
EVAR_EVENT_EXISTS_REGEX = r"^(?:or_|and_)?eVar(\d+)_event-exists$"   # or_/and_ prefix 허용 (row 묶음 의도 명시용 — 인식만, evar_join 자동 default 와 별개)
EVAR_VALUE_COLUMN_TEMPLATE = "eVar{num}"           # value 컬럼 이름 (case-insensitive 매칭)
ALLOWED_VAR_NUM_RANGE = range(1, 201)              # evar/prop 인식 번호 상한 (1~200, 넉넉히)

SITE_CONTAINER_NAME = "site"   # site 양수/음수 컨테이너 (prop/evar 의 hit-scope 묶음) 이름

# customlink 컨테이너 라벨 — True 면 customlink 선두코드(pd25, co78 ...) + ' component'
#   (예: 'pd25_product recommendation' → 'pd25 component', 'co78_recommended product' → 'co78 component').
#   코드(영문+숫자) 못 잡으면 'Component' 로 fallback. False 면 항상 'Component'.
COMPONENT_LABEL_FROM_CUSTOMLINK = True

# crystallize 컬럼 — 자동 LCS 가 못 잡거나 짧을 때 사용자가 직접 keyword 박을 수 있게 함.
# 컬럼명 패턴: <condition>_crystallize_<varname>  (예: starts_crystallize_evar26)
#   · condition: starts → starts-with, contains → contains, equals → equals
#   · varname  : evar25 / evar26 / evar35 / evar48 (prop1/evar1 는 별도 site 컬럼 사용)
# 값이 있으면 그 row 의 자동 LCS 무시하고 사용자가 박은 키워드로 해당 evar 블록 강제.
CRYSTALLIZE_CONDITION_TO_OPERATOR: dict[str, str] = {
    "starts": "starts-with",
    "starts-with": "starts-with",       # hyphen 형식도 매칭
    "contains": "contains",
    "contains-any-of": "contains-any-of",  # hyphen 형식 (multi-value)
    "equals": "equals",
}
CRYSTALLIZE_COLUMN_REGEX = r"^(starts-with|starts|contains-any-of|contains|equals)_crystallize_(evar\d+)$"
# or_/and_ prefix + condition + evarN 형식도 crystallize_override 로 처리 (crystallize 키워드 없는 form).
# 예: or_starts-with_evar105 → starts-with 조건으로 evar105 block 에 강제 박음.
# `_crystallize_` 키워드 없이 사용자가 OR group 의 evar value condition 직접 명시할 때 사용.
CRYSTALLIZE_COLUMN_REGEX_OR = r"^(?:or_|and_)?(starts-with|starts|contains-any-of|contains|equals)_(evar\d+)$"

# v1.1 — set_pair 컬럼 (회사 특성 event+evar AND set 명시)
#   format: `e<event_num>+v<evar_num>[<operator>]` (operator 생략시 contains)
#   세미콜론 multi: `e45+v33;e44+v55[starts-with]`
SET_PAIR_COLUMN = "set_pair"
SET_PAIR_REGEX = re.compile(r"e(\d+)\+v(\d+)(?:\[([\w-]+)\])?", re.IGNORECASE)
SET_PAIR_DEFAULT_OP = "contains"

# Generic site / evar 필터 컬럼 — {not_있으면제외 없으면 포함}{조건}_{prop/evar}{#}
# 예: starts_prop1, not_starts_evar1, contains_evar26, not_contains_evar26
# default operator (조건 없는 옛 컬럼 prop<N>/evar<N>/not_prop<N>/not_evar<N>) → starts-with
# default 줄바꿈 multi 값 → <operator>-any-of [...] (contains 만 표준 지원, 다른 op multi 는 OR 로 묶음)
GENERIC_FILTER_REGEX = r"^(?:or_|and_)?(?P<neg>not_)?(?P<cond>starts|contains|equals)_(?P<var>prop|evar)(?P<num>\d+)$"
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
    # eVar<N>_event-exists 헤더는 필터 컬럼 아님 — 메인 evar 블록의 exists flag
    if re.match(EVAR_EVENT_EXISTS_REGEX, h, flags=re.IGNORECASE):
        return None
    # eVar<N> 값 컬럼 (main evar 블록 대상) 도 필터 컬럼 아님 — 단 main 에서 inline_evar_nums 의 N 인 경우에만 제외 (그 외 evar<N> 헤더는 site filter 로 인식)
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
    """단일 또는 multi 값 → DSL 조건. 한 줄 (single) 또는 ' | OR | ' 토큰 분리 (multi).
    · 1 개   → `<var> <op> '<v>'`
    · 2+ 개  → contains 면 `<var> contains-any-of ['v1', ...]`, 그 외 op 는 ' | OR | ' 토큰 분리
              (paren grouping 안 박음 — parser 가 한 줄 한 cond 룰이라 paren 안 박힌 OR 토큰만 정상 처리)
    """
    if len(vals) == 1:
        return f"{var_name} {op} '{vals[0]}'"
    if op == "contains":
        values_str = ", ".join(f"'{v}'" for v in vals)
        return f"{var_name} contains-any-of [{values_str}]"
    # multi vals OR — " | OR | " 로 토큰 분리. paren wrap 없음 (parser 가 paren 안 받음).
    return " | OR | ".join(f"{var_name} {op} '{v}'" for v in vals)


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
        if op == "contains-any-of":
            # multi-value — 줄바꿈/CR 으로 split (split_evar_values 룰 그대로)
            # multi-value 면 'contains-any-of' named container wrap 안에 OR-of-contains
            # (AA reference: hit-scope container 안에 or pred, paren 안 씀 — paren 은 AA validator 가
            # attribute 이름 일부로 잘못 인식)
            vals = split_evar_values(val)
            if not vals:
                tokens = [
                    f"'v{evar_num}'!hit(",
                    f"event{evar_num} event-exists",
                    "AND",
                    f"evar{evar_num} contains-any-of []",
                ]
            elif len(vals) == 1:
                tokens = [
                    f"'v{evar_num}'!hit(",
                    f"event{evar_num} event-exists",
                    "AND",
                    f"evar{evar_num} contains '{vals[0]}'",
                ]
            else:
                or_lines = [f"evar{evar_num} contains '{vals[0]}'"]
                for v in vals[1:]:
                    or_lines.append("OR")
                    or_lines.append(f"evar{evar_num} contains '{v}'")
                tokens = [
                    f"'v{evar_num}'!hit(",
                    f"event{evar_num} event-exists",
                    "AND",
                    "'contains-any-of'!hit(",
                    *or_lines,
                    ")",
                ]
        else:
            tokens = [
                f"'v{evar_num}'!hit(",
                f"event{evar_num} event-exists",
                "AND",
                f"evar{evar_num} {op} '{val}'",
            ]
    elif not values:
        # 값 없고 event-exists 만 TRUE 인 case — reference 패턴 따라 `evar<N> exists AND event<N> event-exists`
        # (예: CC_00. Contents Click Total 의 evar25/26/35 — 값 없이 event-exists 만 TRUE)
        tokens = [
            f"'v{evar_num}'!hit(",
            f"evar{evar_num} exists",
            "AND",
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
            # 공통 LCS 없음 → 이름 컨테이너 'v<N>', multi-value 는 'contains-any-of' named container wrap 안에 OR
            # (AA reference: hit-scope container, paren 안 씀 — paren 은 AA validator 가 attribute 일부로 잘못 인식)
            if len(values) == 1:
                tokens = [
                    f"'v{evar_num}'!hit(",
                    f"event{evar_num} event-exists",
                    "AND",
                    f"evar{evar_num} contains '{values[0]}'",
                ]
            else:
                or_lines = [f"evar{evar_num} contains '{values[0]}'"]
                for v in values[1:]:
                    or_lines.append("OR")
                    or_lines.append(f"evar{evar_num} contains '{v}'")
                tokens = [
                    f"'v{evar_num}'!hit(",
                    f"event{evar_num} event-exists",
                    "AND",
                    "'contains-any-of'!hit(",
                    *or_lines,
                    ")",
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


def parse_set_pair(raw: str) -> list[tuple[int, int, str]]:
    """`set_pair` 컬럼 값 parse → [(event_num, evar_num, operator), ...]
    format: `e<event_num>+v<evar_num>[<operator>]`, 세미콜론 multi.
    operator 생략시 SET_PAIR_DEFAULT_OP (contains).
    """
    if not raw or not raw.strip():
        return []
    results: list[tuple[int, int, str]] = []
    for piece in raw.split(";"):
        piece = piece.strip()
        if not piece:
            continue
        m = SET_PAIR_REGEX.search(piece)
        if m:
            event_num = int(m.group(1))
            evar_num  = int(m.group(2))
            op        = m.group(3) or SET_PAIR_DEFAULT_OP
            results.append((event_num, evar_num, op))
    return results


def build_set_block(event_num: int, evar_num: int, operator: str, value: str) -> str:
    """`'v<M>'!hit( event<N> event-exists AND evar<M> <op> '<val>' )` 토큰 한 라인.
    set_pair (회사 특성 event+evar AND set) 처리용. build_evar_block 의 crystallize_override 패턴 fork.
    """
    tokens = [
        f"'v{evar_num}'!hit(",
        f"event{event_num} event-exists",
        "AND",
        f"evar{evar_num} {operator} '{value}'",
        ")",
    ]
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
                           site_neg: list[tuple[str, str, list[str]]] | None = None,
                           evar_join: str = "AND") -> str:
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

    evar_join: "AND" (default) | "OR" — evar_blocks 가 2 개 이상일 때 묶음 방식.
       OR 일 때 named container wrap (EVAR_JOIN_WRAP_NAME) 안에 OR 토큰으로 묶음.
       (raw paren `(...)` 은 v2_2 의 paren strip 에 잡혀 사라지므로 컨테이너 형태로 보존.)
    """
    parts: list[str] = ["hit("]
    has_first = False
    if customlink:
        # customlink 멀티값(줄바꿈 split) 지원 — 한 컨테이너 안에 여러 customlink 를 OR 로 묶음.
        _cls = [c.strip() for c in re.split(r"[\r\n]+", customlink) if c.strip()]
        # 서브컨테이너 라벨 — toggle True 면 선두코드들(pd25, ft31 ...) ' or ' 조합 + ' component'
        if COMPONENT_LABEL_FROM_CUSTOMLINK:
            _codes: list[str] = []
            for _c in _cls:
                _m = re.match(r"^([A-Za-z]+\d+)", _c)
                if _m and _m.group(1) not in _codes:
                    _codes.append(_m.group(1))
            _label = (" or ".join(_codes) + " component") if _codes else "Component"
        else:
            _label = "Component"
        parts.append(f"'{_label}'!hit(")
        for _i, _c in enumerate(_cls):
            if _i > 0:
                parts.append("OR")
            parts.append(f"customlink starts-with '{_c}'")
        parts.append(")")
        has_first = True
    # eVar 블록들 — join 옵션 따라 AND (default) 또는 OR (named container wrap)
    if evar_blocks:
        join_op = (evar_join or "AND").strip().upper()
        if join_op == "OR" and len(evar_blocks) >= 2:
            if has_first:
                parts.append("AND")
            parts.append(f"'{EVAR_JOIN_WRAP_NAME}'!hit(")
            for i, block in enumerate(evar_blocks):
                if i > 0:
                    parts.append("OR")
                parts.append(block)
            parts.append(")")
            has_first = True
        else:
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
                    root_scope: str = "visit", container_label: str = "") -> str:
    """전체 structure 한 줄 ' | ' 구분.

    customlink_blocks 가 1 개여도 (안전성·시각 일관성 위해) 항상 paren grouping 으로 감쌈.
    2 개 이상이면 OR 로 엮음.

    root_scope:
      · "visit" / "visitor" → 공통 ref AND ( '<name>'!hit( <blocks 또는 OR> ) ) 로 묶음
                              → visit( hit( @공통ref AND ( '<name>'!hit( ... ) ) ) )
      · "hit"               → 공통 ref 없이 단독 → hit( '<name>'!hit( <blocks 또는 OR> ) )"""
    label = container_label or name   # 바깥 named container 라벨 (빈 값이면 segment name)
    parts: list[str] = []
    closing: list[str] = []
    if root_scope == "hit":
        parts.append("hit(")
        parts.append(f"'{label}'!hit(")
        closing = [")", ")"]
    else:
        parts.append(f"{root_scope}(")
        # [v1.3] inner hit 에 'page+content' description 박음 — v2.3 의 _lift_inner_hit_into_visit_root 가
        # description 없는 단일 inner hit wrap 을 벗기는 동작 우회. description 채워두면 그 조건 (no_desc) 실패 →
        # _lift 트리거 안 됨 → visit(hit(AND(...))) 구조 보존.
        parts.append("'page+content'!hit(")
        # COMMON_SEGMENT_REF_NAME 있으면 named container wrap ('<name>'!hit(@<id>)), 없으면 @<id> 단독
        if COMMON_SEGMENT_REF_NAME:
            parts.append(f"'{COMMON_SEGMENT_REF_NAME}'!hit(")
            parts.append(f"@{COMMON_SEGMENT_REF}")
            parts.append(")")
        else:
            parts.append(f"@{COMMON_SEGMENT_REF}")
        parts.append("AND")
        parts.append("(")                       # ← 추가 paren grouping 열기 (수동편집예시 .dsl 형식)
        parts.append(f"'{label}'!hit(")
        closing = [")", ")", ")", ")"]          # ← name-container 닫기 + paren grouping 닫기 + hit 닫기 + root 닫기

    # customlink 블록들 — 1 개면 그대로, 2+ 면 OR 로 엮음
    for i, block in enumerate(customlink_blocks):
        if i > 0:
            parts.append("OR")
        parts.append(block)

    parts.extend(closing)
    return " | ".join(parts)


def _lookup_visit_seg_id(base_name: str) -> tuple[str, str]:
    """(Visit) segment 의 (id, full name) lookup — 가장 최신 segment_v2_2_result_*.csv (방금 POST 한 visit segments) 만 본다.
    lookup csv (segment_lookup_*.csv) 는 의도적으로 안 봄 — 이전 캠페인 같은 name segment 잘못 매칭 방지.
    매칭 없으면 ("", "") → Delayed Purchase 빌더가 fallback (inline content). visit segment 먼저 POST 해야 함."""
    if not base_name:
        return ("", "")
    visit_name = f"{base_name} (Visit)"
    for path in sorted(OUTPUT_DIR.glob("segment_v2_2_result_*.csv"), reverse=True):
        if "dryrun" in path.name:
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if (r.get("Name") or "").strip() == visit_name:
                        sid = (r.get("SegmentId") or "").strip()
                        if sid:
                            print(f"  [visit-ref] '{visit_name}' → {sid}  (from {path.name})")
                            return (sid, visit_name)
        except Exception:
            continue
    print(f"  [visit-ref] '{visit_name}' 매칭 없음 — fallback inline (visit segment 먼저 v2.2 로 POST 해야 함)")
    return ("", "")


def _build_delayed_purchase_structure(dp_name: str, base_name: str, customlink_blocks: list[str],
                                      container_label: str = "") -> str:
    """[Global] Delayed Purchase wrap — mixed-AND 패턴 (다른 사람 / [CAMPAIGN NAME] 컨벤션 따름).

    구조:
      hit(
        [sequence-after] visitor(
          visit(                                    ← outer visit (AND wrapping)
            [sequence-all] visit(                   ← inner visit (sequence container)
              '<base_name> (Visit)'!hit(            ← Step A — named container
                '<COMMON_NAME>'!hit(@COMMON_REF)    ← page block (안쪽)
                AND
                <inner content>
              )
              THEN
              '<ATC_NAME>'!hit(@ATC_REF)            ← Step B
            )
            AND
            'Order (All Products)'!hit(             ← NOT orders (visit-level AND child)
              NOT orders event-exists
            )
          )
          THEN
          visit(                                    ← Stream 2
            'Order (All Products)'!hit(
              orders event-exists
            )
          )
        )
      )

    변경 이력 (v1.2 2026-05-29):
      · 기존 unified 구조 (Step B + NOT orders 가 한 hit) → mixed-AND 패턴 (AND child 분리)
      · `'Order (All Products)'!hit(...)` named container 로 NOT orders / Stream 2 orders 둘 다 wrap
      · Step A wrapper 를 `'<base_name>'` → `'<base_name> (Visit)'` 로 변경 (Visit segment 의 inline content 임을 명시)
      · `[sequence-all] visit(...)` inner wrap 추가 (sequence container 명시)
    """
    # customlink_blocks → Step A inner content.
    #  · 1 개: 기존처럼 hit(...) 래퍼 벗겨 inline (@COMMON_REF AND <inner>)
    #  · 2 개+: 각 블록 hit(...) 유지하고 ( hit(A) OR hit(B) ) 괄호 그룹으로 묶음
    #          → @COMMON_REF AND ( hit(A) OR hit(B) ). 벗겨 평탄화하면
    #            @ref AND A OR B 로 그룹 경계가 사라져 B 가 @ref/THEN 시퀀스에서 빠짐.
    # 바깥 그룹: ( '<label>'!hit( hit(A) OR hit(B) ... ) ) — '<label>' 이 모든 브랜치를 감쌈 (hit/visit 와 동일 대칭).
    dp_label = container_label or base_name
    inner_parts: list[str] = ["(", f"'{dp_label}'!hit("]
    for i, block in enumerate(customlink_blocks):
        if i > 0:
            inner_parts.append("OR")
        inner_parts.extend(block.split(" | "))   # 각 브랜치 full hit() 유지
    inner_parts.append(")")                       # close '<label>'!hit
    inner_parts.append(")")                       # close paren group

    # Step A 의 wrapper 이름 — base_name + " (Visit)" (Visit segment inline)
    step_a_wrapper = f"{base_name} (Visit)"

    parts: list[str] = [
        "hit(",
        "[sequence-after] visitor(",
        "visit(",                        # outer visit (AND wrapping)
        f"'{step_a_wrapper}'!visit(",    # Step A — named visit (sequence container)
        "hit(",                           # inner hit
    ]
    # @COMMON_REF (page 블록) 은 Step A wrapper 안 (named hit 안) 으로 들어감
    if COMMON_SEGMENT_REF:
        if COMMON_SEGMENT_REF_NAME:
            parts.append(f"'{COMMON_SEGMENT_REF_NAME}'!hit(")
            parts.append(f"@{COMMON_SEGMENT_REF}")
            parts.append(")")
        else:
            parts.append(f"@{COMMON_SEGMENT_REF}")
        parts.append("AND")
    parts.extend(inner_parts)
    parts.append(")")                    # close inner hit
    parts.append("THEN")
    # Step B — ATC (NOT orders 는 여기 안 들어감)
    if ATC_VISIT_SEGMENT_REF:
        if ATC_VISIT_SEGMENT_NAME:
            parts.append(f"'{ATC_VISIT_SEGMENT_NAME}'!hit(")
            parts.append(f"@{ATC_VISIT_SEGMENT_REF}")
            parts.append(")")
        else:
            parts.append(f"@{ATC_VISIT_SEGMENT_REF}")
    parts.extend([
        ")",                              # close Step A wrapper (named visit)
        "AND",
        # NOT orders — 'Order (All Products)' named container 으로 wrap
        "'Order (All Products)'!hit(", "NOT orders event-exists", ")",
        ")",                              # close outer visit (AND wrapping)
        "THEN",
        # Stream 2 — visit 안 'Order (All Products)' named container
        "visit(", "'Order (All Products)'!hit(", "orders event-exists", ")", ")",
        ")",                              # close [sequence-after] visitor
        ")",                              # close outermost hit
    ])
    return " | ".join(parts)


def _lookup_seg_ref_by_name(name_pat: str, cache_name: str) -> tuple[str, str]:
    """segment_ref_cache_<cache_name>.json 들 (콤마 분리 가능) 에서 name partial 매칭으로 (segment id, full name) 찾기.
    cache_name 예: "26sw_evar_global" / "26sw_evar_global,add_to_cart_global" — 여러 cache 순서대로 시도."""
    import json
    if not name_pat:
        return ("", "")
    parts = [p.strip() for p in (cache_name or "").split(",") if p.strip()]
    if not parts:
        parts = [""]
    needle = name_pat.lower()
    tried_paths: list[str] = []
    for nm_suffix in parts:
        suffix = f"_{nm_suffix}" if nm_suffix else ""
        cache_path = OUTPUT_DIR / f"segment_ref_cache{suffix}.json"
        tried_paths.append(cache_path.name)
        if not cache_path.exists():
            continue
        try:
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            continue
        for sid, entry in cache.items():
            if isinstance(entry, dict):
                nm = (entry.get("name") or "")
                if nm and needle in nm.lower():
                    print(f"  [ref lookup] '{name_pat}' → {sid}  (name: {nm}, from {cache_path.name})")
                    return (sid, nm)
    print(f"  [ref lookup] '{name_pat}' 매칭 없음 — 시도: {tried_paths}")
    return ("", "")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="seg_make_ref CSV → v2_1 input CSV 자동 변환")
    parser.add_argument("--input", default=SEG_MAKE_REF_CSV,
                        help="input CSV. 빈 값이면 폴더에서 seg_make_ref_*.csv 사전순 최신 1개 자동 선택.")
    parser.add_argument("--output-ts", dest="output_ts", default="",
                        help="출력 파일 ts override (scenario 에서 _global / _us suffix 박을 때 사용). "
                             "빈 값이면 datetime.now() 사용.")
    args = parser.parse_args()

    # REF_SEGMENT_NAME 으로 cache lookup (COMMON_SEGMENT_REF 직접 박혀 있으면 lookup 안 함)
    global COMMON_SEGMENT_REF, COMMON_SEGMENT_REF_NAME
    if REF_SEGMENT_NAME and not COMMON_SEGMENT_REF_NAME:
        looked_id, looked_name = _lookup_seg_ref_by_name(REF_SEGMENT_NAME, CACHE_NAME)
        # 단 직접 박힌 COMMON_SEGMENT_REF 있으면 id 유지 (사용자 의도 존중), name 만 채움
        if looked_id and not COMMON_SEGMENT_REF:
            COMMON_SEGMENT_REF = looked_id
        if looked_name and not COMMON_SEGMENT_REF_NAME:
            COMMON_SEGMENT_REF_NAME = looked_name
    if COMMON_SEGMENT_REF:
        wrap_note = f" (named container wrap: {COMMON_SEGMENT_REF_NAME!r})" if COMMON_SEGMENT_REF_NAME else ""
        print(f"  [common-ref] visit/visitor 모드에서 AND 묶일 segment id: {COMMON_SEGMENT_REF}{wrap_note}")

    # SCOPE_MODE 파싱 — 콤마 구분 list. backward compat "both" → ["visit","hit"]
    if SCOPE_MODE.strip().lower() == "both":
        modes = ["visit", "hit"]
    else:
        modes = [m.strip().lower() for m in SCOPE_MODE.split(",") if m.strip()]
    print(f"  [scope] modes: {modes}")

    # ATC segment-ref (delayed_purchase 옵션 용) — 직접 박은 값 우선, 없으면 ATC_REF_SEGMENT_NAME 으로 lookup
    global ATC_VISIT_SEGMENT_REF, ATC_VISIT_SEGMENT_NAME
    if "delayed_purchase" in modes:
        if not ATC_VISIT_SEGMENT_REF and ATC_REF_SEGMENT_NAME:
            atc_id, atc_nm = _lookup_seg_ref_by_name(ATC_REF_SEGMENT_NAME, CACHE_NAME)
            if atc_id:
                ATC_VISIT_SEGMENT_REF = atc_id
                if not ATC_VISIT_SEGMENT_NAME:
                    ATC_VISIT_SEGMENT_NAME = atc_nm
        if ATC_VISIT_SEGMENT_REF:
            print(f"  [atc-ref] delayed_purchase 용 ATC visit segment id: {ATC_VISIT_SEGMENT_REF}  (name: {ATC_VISIT_SEGMENT_NAME!r})")
        else:
            print(f"  [atc-ref] ⚠️ delayed_purchase 활성인데 ATC segment-ref 없음 — prewarm 으로 cache 만들거나 ATC_VISIT_SEGMENT_REF 직접 박기")

    ts = (args.output_ts or "").strip() or datetime.now().strftime("%y%m%d_%H%M")
    out_path     = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(ts=ts)
    out_dsl_path  = OUTPUT_DIR / OUTPUT_DSL_NAME_TEMPLATE.format(ts=ts)
    out_warn_path = OUTPUT_DIR / OUTPUT_WARN_NAME_TEMPLATE.format(ts=ts)

    # ─── input CSV 결정 ─────────────────────────────────────
    # SEG_MAKE_REF_CSV (또는 --input) 가 비어 있으면 OUTPUT_DIR 의 seg_make_ref_*.csv 중
    # 파일명 사전순 최신 1 개 자동 선택. 파일명 timestamp 는 _YYMMDD_HHMM 이므로
    # 사전순 정렬 = 시간순 정렬 (mtime 보다 안정적 — OneDrive 동기화·복사 시 mtime 이 어긋날 수 있어서).
    input_arg = (args.input or "").strip()
    if not input_arg:
        # 글로벌 — 'seg_make_ref_<YYMMDD>_<HHMM>.csv' 패턴만 (숫자 시작). us / tmp 같은 파생 제외.
        all_files = sorted(OUTPUT_DIR.glob("seg_make_ref_*.csv"), reverse=True)
        candidates = [p for p in all_files
                      if re.match(r"^seg_make_ref_\d", p.name) and "_tmp." not in p.name]
        if not candidates:
            print(f"ERROR: seg_make_ref_<YYMMDD>_<HHMM>.csv 못 찾음 — {OUTPUT_DIR}")
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
            # value_col 없어도 OK — generic filter (starts-with_eVarN 등) 또는 crystallize 가 main 조건 역할
            evar_event_cols.append((hdr, value_col or "", num))
            inline_evar_nums.add(num)
        if evar_event_cols:
            print(f"  [evar-event] 인식된 evar 블록 대상: " +
                  ", ".join(f"eVar{n}" for _, _, n in evar_event_cols))

        # crystallize 컬럼 매핑 — {evarN: [(operator, column_name), ...]} (같은 evar 에 여러 컬럼 가능)
        # 우선순위: starts-with > equals > contains-any-of > contains  (specific > generic)
        # row 마다 우선순위 순으로 값 있는 첫 컬럼 사용 (없으면 자동 LCS 폴백)
        CRYSTALLIZE_OP_PRIORITY = {"starts-with": 0, "equals": 1, "contains-any-of": 2, "contains": 3}
        crystallize_map: dict[str, list[tuple[str, str]]] = {}
        for hdr in fieldnames:
            m = re.match(CRYSTALLIZE_COLUMN_REGEX, hdr.strip(), flags=re.IGNORECASE)
            if not m:
                m = re.match(CRYSTALLIZE_COLUMN_REGEX_OR, hdr.strip(), flags=re.IGNORECASE)
            if m:
                cond, varname = m.group(1).lower(), m.group(2).lower()
                op = CRYSTALLIZE_CONDITION_TO_OPERATOR[cond]
                crystallize_map.setdefault(varname, []).append((op, hdr))
        for var in crystallize_map:
            crystallize_map[var].sort(key=lambda x: CRYSTALLIZE_OP_PRIORITY.get(x[0], 99))
        if crystallize_map:
            summary = ", ".join(
                f"{v}→[{' > '.join(op for op, _ in entries)}]"
                for v, entries in crystallize_map.items()
            )
            print(f"  [crystallize] 인식된 override 컬럼 (우선순위 순): {summary}")

        # site/evar 필터 컬럼 분류 — {header: (is_neg, operator, var_name)}
        # 옛 (prop<N>/evar<N>/not_prop<N>/not_evar<N>, default starts-with) + 새 ({not_}<cond>_(prop|evar)<#>)
        filter_columns: dict[str, tuple[bool, str, str]] = {}
        for hdr in fieldnames:
            cls = classify_filter_column(hdr)
            if cls:
                filter_columns[hdr] = cls
        # eVar<N> 값 컬럼 (메인 evar 블록 대상) 은 filter 에서 제외 — inline_evar_nums 의 N 매칭 evar<N> 헤더만 제외
        # 그 외 evar<N> 헤더 (예: evar1, evar11 같은 site filter 용) 는 그대로 site filter 로 인식
        def _is_main_evar_value_col(hdr: str) -> bool:
            m = re.match(r"^eVar(\d+)$", hdr.strip(), flags=re.IGNORECASE)
            return bool(m) and int(m.group(1)) in inline_evar_nums
        filter_columns = {h: c for h, c in filter_columns.items() if not _is_main_evar_value_col(h)}
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

            # v1.1 — set_pair 컬럼 parse (회사 특성 event+evar AND set)
            set_pairs = parse_set_pair(row.get(SET_PAIR_COLUMN, ""))
            set_pair_evar_nums: set[int] = {evar_num for _, evar_num, _ in set_pairs}

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
                    if num in set_pair_evar_nums:
                        # v1.1 — set_pair 의 evar<M> 은 set block 의 value 로 사용됨 → site filter 분류 skip
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
            # v1.1 — set_pair 의 event_num 과 매칭되는 evar<num> 은 set 의 일부로 처리 → 별도 evar block 빌드 skip
            set_pair_event_nums: set[int] = {event_num for event_num, _, _ in set_pairs}
            for exists_col, val_col, num in evar_event_cols:
                if num in set_pair_event_nums:
                    # set_pair 의 event_num 이 evar<num> 와 매칭 — set block 안 들어가니 별도 block 안 만듬
                    continue
                flag = (row.get(exists_col) or "").strip().upper()
                if flag != "TRUE":
                    continue
                values = split_evar_values(row.get(val_col) or "")
                cry_override: tuple[str, str] | None = None
                # 우선순위 순으로 (starts-with > equals > contains-any-of > contains) 값 있는 첫 컬럼 사용
                for op, col_name in crystallize_map.get(f"evar{num}", []):
                    cry_val = (row.get(col_name) or "").strip()
                    if cry_val:
                        cry_override = (op, cry_val)
                        break
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

            # v1.1 — set_pair block 추가 (회사 특성 event+evar AND set)
            #   같은 row 의 `eVar<evar_num>` column 값을 set block 의 value 로 사용
            for event_num, evar_num, op in set_pairs:
                val_col = EVAR_VALUE_COLUMN_TEMPLATE.format(num=evar_num)
                set_val_col = fieldnames_lower.get(val_col.lower())
                value = (row.get(set_val_col, "") or "").strip() if set_val_col else ""
                if not value:
                    print(f"  [set_pair] WARN — row '{seg_name}' set_pair e{event_num}+v{evar_num} 의 eVar{evar_num} 값 없음 → 이 set 건너뜀")
                    continue
                evar_blocks.append(build_set_block(event_num, evar_num, op, value))

            if not evar_blocks:
                skipped.append((seg_name, "_event-exists TRUE 컬럼 0 개"))
                continue

            # 검수용 raw row 저장 (skip 안 된 것만)
            raw_rows.append(row)

            # evar_join 결정 — 우선순위:
            #   1) evar_join 컬럼 명시값 ("OR" / "AND")
            #   2) 컬럼 없거나 값 빈 채 → 자동 default: multi-evar TRUE (>=2) 면 OR, 아니면 AND
            #      (한 row 에 evar event-exists 2개 이상 = OR 의도로 자동 판단)
            evar_join_col = fieldnames_lower.get(EVAR_JOIN_COLUMN.lower())
            explicit_val = (row.get(evar_join_col) or "").strip().upper() if evar_join_col else ""
            if set_pairs:
                # v1.1 — set_pair 있으면 AND 강제 (회사 특성 set 의도 — explicit OR 도 덮어씀)
                evar_join_val = "AND"
            elif explicit_val in ("OR", "AND"):
                evar_join_val = explicit_val
            else:
                evar_join_val = "OR" if len(evar_blocks) >= 2 else "AND"

            # raw row 와 build 재료 임시 보관 (warning 은 검수 단계 후 채움)
            raw_rows[-1]["_seg_name"] = seg_name
            raw_rows[-1]["_customlink"] = customlink
            raw_rows[-1]["_evar_blocks"] = evar_blocks
            raw_rows[-1]["_site_pos"] = site_pos
            raw_rows[-1]["_site_neg"] = site_neg
            raw_rows[-1]["_evar_extras"] = dict(evar_extras)
            raw_rows[-1]["_evar_lcs"] = evar_lcs_per_row
            raw_rows[-1]["_evar_join"] = evar_join_val

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
                evar_join=m.get("_evar_join") or "AND",
            ))

        new_name_base = transform_name(raw_seg_name)
        warning_text  = " ; ".join(warnings_by_name.get(raw_seg_name, []))

        # 바깥 named container 라벨 — customlink 선두코드 ' or ' 조합 + ' component' (toggle True), 아니면 세그명
        if COMPONENT_LABEL_FROM_CUSTOMLINK:
            _codes: list[str] = []
            for m in members:
                for _c in re.split(r"[\r\n]+", (m["_customlink"] or "")):
                    _cm = re.match(r"^([A-Za-z]+\d+)", _c.strip())
                    if _cm and _cm.group(1) not in _codes:
                        _codes.append(_cm.group(1))
            container_label = (" or ".join(_codes) + " component") if _codes else new_name_base
        else:
            container_label = new_name_base

        if "visit" in modes:
            visit_name = (new_name_base + " (Visit)"
                          if not new_name_base.endswith(" (Visit)") else new_name_base)
            visit_structure = build_structure(visit_name, customlink_blocks, root_scope="visit", container_label=container_label)
            rows_out.append({
                "segment_id": "", "name": visit_name, "description": "",
                "rsid": DEFAULT_RSID, "tags": DEFAULT_TAGS,
                "structure": visit_structure, "warning": warning_text,
            })
        if "hit" in modes:
            hit_name = new_name_base
            hit_structure = build_structure(hit_name, customlink_blocks, root_scope="hit", container_label=container_label)
            rows_out.append({
                "segment_id": "", "name": hit_name, "description": "",
                "rsid": DEFAULT_RSID, "tags": DEFAULT_TAGS,
                "structure": hit_structure, "warning": warning_text,
            })
        if "delayed_purchase" in modes:
            dp_name = (new_name_base + " (Delayed Purchase)"
                       if not new_name_base.endswith(" (Delayed Purchase)") else new_name_base)
            dp_structure = _build_delayed_purchase_structure(dp_name, new_name_base, customlink_blocks, container_label=container_label)
            rows_out.append({
                "segment_id": "", "name": dp_name, "description": "",
                "rsid": DEFAULT_RSID, "tags": DEFAULT_TAGS,
                "structure": dp_structure, "warning": warning_text,
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
                    # site_filter 가 길면 truncate (WARN.csv 에 full 박힘)
                    sf_short = (sf[:60] + "...") if len(sf) > 60 else sf
                    print(f"     - {nm}   site_filter=[{sf_short}]" if sf else f"     - {nm}")
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

        # skip 된 row 도 WARN.csv 에 기록 — 콘솔 못 봐도 csv 만 보고 빠짐 발견 가능
        for name, reason in skipped:
            warning_rows.append({
                "warning_type": "SKIP_ROW",
                "customlink": "",
                "site_filter": "",
                "evar_combo": "",
                "segment_name": name,
                "duplicates_with": "",
                "note": reason,
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
            print(f"  ※ SKIP_ROW = maker 가 skip 한 row (사유는 note 컬럼).")
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
