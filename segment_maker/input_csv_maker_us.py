# input_csv_maker_us.py
# 2026-05-29  Jonghyun Park w/ Claude
# input_csv_maker.py 의 US 캠페인 파생 — RSID, flat 구조, event<N> event-exists 패턴
# updated: 2026-05-26       — crystallize: regex 에 hyphen 변형 (starts-with / contains-any-of) 매칭 추가, map → list 구조 + 우선순위 (starts-with > equals > contains-any-of > contains), row 마다 값 있는 첫 컬럼 사용
# updated: 2026-05-29  v1.1 — event metric 명 통일: 기존 `evar<N>instances event-exists` → `event<N> event-exists` (Adobe Analytics commerce event <N> 사용 — US 캠페인 click total/구체 컨텐츠 click 측정에 instances metric 보다 더 정확). 영향: 모든 evar block 의 main event-exists 토큰 변경 (build_evar_block line 318).
# updated: 2026-05-29  v1.2 — _build_delayed_purchase_structure 재작성: mixed-AND 패턴 + 'Order (All Products)' named container + [sequence-after]/[sequence-all] 라벨. CAMPAIGN NAME US_CC_xx DP 컨벤션 따름.
# updated: 2026-05-29  v1.3 — build_structure (visit) 의 inner hit 에 'page+content' description 박음. v2.3 _lift_inner_hit_into_visit_root 후처리 우회 — visit(hit(AND)) 구조 보존.
"""
seg_make_ref_us_*.csv → aa_create_segment_v2_1.py 가 받는 input CSV 자동 변환.

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

seg_make_ref_us_CSV = "seg_make_ref_us_260526_1308.csv"   # 빈 값이면 폴더 내 seg_make_ref_us_*.csv 파일명 사전순 최신 1개 자동 선택. 특정 파일 강제 지정 시 파일명 박기.

# US 버전 — visit/visitor 모드에서 AND 로 묶을 US 캠페인 segment-ref ID.
# 두 가지 방법 (둘 다 동작, 둘 다 박혀 있으면 COMMON_SEGMENT_REF 가 우선):
#   1) 직접 박기: COMMON_SEGMENT_REF = "sXXXXXXXXX_<id>" (가장 단순, ID 알면)
#   2) cache lookup: REF_SEGMENT_NAME + CACHE_NAME 박으면 segment_ref_cache_<CACHE_NAME>.json 에서
#      name partial 매칭으로 자동 ID 결정 (prewarm_seg_ref_cache.py 로 미리 cache 만들어 둘 것)
# 둘 다 빈 값이면 visit/visitor 모드에서도 AND 묶음 없이 단순 visit(hit(...)) 만 (hit 모드는 어차피 영향 없음).
COMMON_SEGMENT_REF      = ""   # 예: "sXXXXXXXXX_<US_캠페인_Main_Page_segment_id>"
COMMON_SEGMENT_REF_NAME = ""   # 예: "[CAMPAIGN NAME] US_Campaign Main Page_Evar" — 박혀 있으면 dsl 에 named container wrap (`'<name>'!hit(@<id>)`)
REF_SEGMENT_NAME   = "US_Campaign Main Page_Evar"   # cache 에서 name partial 매칭 → id + name 자동 결정
CACHE_NAME         = "26sw_evar_us,add_to_cart_us"   # 콤마 분리 — 두 cache 다 lookup (Campaign Main + ATC)

# 이름 변환 룰 (캠페인 시즌 변경 시)
NAME_CAMPAIGN_BEFORE = "[CAMPAIGN NAME]"
NAME_CAMPAIGN_AFTER  = "[CAMPAIGN NAME]"

# 공통 substring 최소 길이 — 이 길이 미만은 generic 으로 간주, 특이사항 컨테이너로 강제
MIN_LCS_LENGTH = 4

# 한 seg_make_ref row 를 어떻게 segment 로 만들지 — 콤마 구분으로 여러 옵션 동시 활성화 가능.
# 옵션:
#   "visit"            → visit 버전 (이름 뒤 ' (Visit)' suffix)
#   "hit"              → hit 버전 (이름 suffix 없음)
#   "delayed_purchase" → Delayed Purchase 버전 (이름 뒤 ' (Delayed Purchase)' suffix)
#                        → visit 안에 [본 segment content + ATC visit + NOT orders] THEN visit(orders) wrap.
#                          ATC_VISIT_SEGMENT_REF / ATC_VISIT_SEGMENT_NAME 또는 ATC_REF_SEGMENT_NAME 박혀 있어야 동작.
# backward compat: "both" → "visit,hit"
SCOPE_MODE = "visit,hit,delayed_purchase"
# SCOPE_MODE = "hit"
# SCOPE_MODE = "visit"
# SCOPE_MODE = "delayed_purchase"
# 예: SCOPE_MODE = "visit,hit"
# 예: SCOPE_MODE = "hit"   (단일)

# delayed_purchase 옵션의 ATC (Add to Cart Visit) segment-ref — visit/hit 의 COMMON_SEGMENT_REF 와 별개
ATC_VISIT_SEGMENT_REF      = ""   # 예: "sXXXXXXXXX_<US_ATC_Visit_id>"  (직접 박기)
ATC_VISIT_SEGMENT_NAME     = ""   # 예: "[US] Add to Cart Visit"        (named container wrap 박힘)
ATC_REF_SEGMENT_NAME       = "[US] Add to Cart Visit"   # cache 에서 partial 매칭 → id+name 자동 (CACHE_NAME 활용). 글로벌과 안 섞이게 [US] prefix 박을 것

DEFAULT_RSID = "sscompany_namenewus"   # US 캠페인 RSID
DEFAULT_TAGS = ""

# ─── evar 블록 묶음 방식 (row 별 override) ──────────────────────
# raw csv 에 'evar_join' 컬럼 있고 값이 "OR"/"AND" 면 그 값 우선.
# 컬럼 없거나 값 빈 채면 → multi-evar TRUE (>=2) 자동 OR (default), 단일이면 AND.
# named container wrap 으로 OR 묶음 — raw paren 은 v2_2 의 paren strip 에 잡혀 사라지므로.
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
EVAR_EVENT_EXISTS_REGEX = r"^(?:or_|and_)?eVar(\d+)_event-exists$"   # or_/and_ prefix 허용 (row 묶음 의도 명시용 — 인식만, evar_join 매커니즘과 별개)
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
    "starts-with": "starts-with",   # hyphen 형식 (예: starts-with_evar105) 도 매칭
    "contains": "contains",
    "contains-any-of": "contains-any-of",  # hyphen 형식 (multi-value)
    "equals": "equals",
}
CRYSTALLIZE_COLUMN_REGEX = r"^(starts-with|starts|contains-any-of|contains|equals)_crystallize_(evar\d+)$"
# US 패턴 — cond prefix 없는 'crystallize_evar<N>' 형식. default operator = starts-with (US 기본).
CRYSTALLIZE_COLUMN_REGEX_NO_COND = r"^crystallize_(evar\d+)$"

# customlink 컬럼 — 헤더에 부가 설명 (한글 등) 붙어 있어도 prefix 매칭으로 찾음
# 예: "customlink", "customLink", "customLink (한글, ...)"
CUSTOMLINK_COLUMN_REGEX = r"^custom[Ll]ink\b"

# Generic site / evar 필터 컬럼 — {not_있으면제외 없으면 포함}{조건}_{prop/evar}{#}
# 예: starts_prop1, not_starts_evar1, contains_evar26, not_contains_evar26
# default operator (조건 없는 옛 컬럼 prop<N>/evar<N>/not_prop<N>/not_evar<N>) → starts-with
# default 줄바꿈 multi 값 → <operator>-any-of [...] (contains 만 표준 지원, 다른 op multi 는 OR 로 묶음)
GENERIC_FILTER_REGEX = r"^(?:or_|and_)?(?P<neg>not_)?(?P<cond>starts-with|contains|equals|starts)_(?P<var>prop|evar)(?P<num>\d+)$"
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
    """단일 또는 multi 값 → DSL 조건. paren grouping 없이 ' | OR | ' 토큰 분리 (parser 가 한 줄 한 cond 룰)."""
    if len(vals) == 1:
        return f"{var_name} {op} '{vals[0]}'"
    if op == "contains":
        values_str = ", ".join(f"'{v}'" for v in vals)
        return f"{var_name} contains-any-of [{values_str}]"
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
    """[US] 한 eVarN 의 DSL 블록 (한 줄, ' | ' 구분). 컨테이너 없는 flat 구조.

    US reference 패턴 (큰따옴표 + event metric + starts-with):
      · 값 없음 → event<N> event-exists
      · crystallize override = (op, val) → event<N> event-exists AND evar<N> <op> "<val>"
      · 공통 substring ≥ MIN_LCS_LENGTH → ... AND evar<N> starts-with "<lcs>"
      · 공통 없음 → ... AND evar<N> starts-with "<v1>" OR evar<N> starts-with "<v2>" ...

    extra_conditions: list of (is_negative, operator, values) — 메인 블록 끝에 inline AND / AND NOT 추가.
    """
    var = f"evar{evar_num}"
    tokens: list[str] = [f"event{evar_num} event-exists"]

    if crystallize_override:
        op, val = crystallize_override
        # crystallize val 도 줄바꿈으로 multi 가능 → '<op>-any-of' named container wrap 안에 OR
        # (AA reference: paren 안 씀 — paren 은 AA validator 가 attribute 일부로 잘못 인식)
        cry_vals = split_evar_values(val)
        if len(cry_vals) <= 1:
            tokens.append("AND")
            tokens.append(f'{var} {op} "{cry_vals[0] if cry_vals else val}"')
        else:
            tokens.append("AND")
            tokens.append(f"'{op}-any-of'!hit(")
            for i, v in enumerate(cry_vals):
                if i > 0:
                    tokens.append("OR")
                tokens.append(f'{var} {op} "{v}"')
            tokens.append(")")
    elif values:
        lcs = find_longest_common_substring(values).strip()
        if len(lcs) >= MIN_LCS_LENGTH:
            tokens.append("AND")
            tokens.append(f'{var} starts-with "{lcs}"')
        else:
            # 공통 없음 → 'starts-with-any-of' named container wrap 안에 OR (AA reference: paren 안 씀)
            tokens.append("AND")
            tokens.append("'starts-with-any-of'!hit(")
            for i, v in enumerate(values):
                if i > 0:
                    tokens.append("OR")
                tokens.append(f'{var} starts-with "{v}"')
            tokens.append(")")

    # inline extra_conditions — 큰따옴표 사용 (US 패턴)
    if extra_conditions:
        for is_neg, op, vals in extra_conditions:
            vals_clean = [v for v in (vals or []) if v]
            if not vals_clean:
                continue
            # US 는 큰따옴표라 _format_filter_condition (작은따옴표) 대신 직접 빌드
            if len(vals_clean) == 1:
                cond_str = f'{var} {op} "{vals_clean[0]}"'
            else:
                # multi values OR 묶음 — 각 줄 별 op
                sub = " OR ".join(f'{var} {op} "{v}"' for v in vals_clean)
                cond_str = f"({sub})"
            tokens.append("AND")
            tokens.append(f"NOT ({cond_str})" if is_neg else cond_str)

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


def _build_us_filter_inner(filters: list[tuple[str, str, list[str]]]) -> str:
    """[US] 여러 (op, var, vals) 를 OR 로 묶음. 큰따옴표 사용. multi vals 는 paren 으로 묶어 OR."""
    lines: list[str] = []
    for op, var, vals in (filters or []):
        vals_clean = [v for v in vals if v]
        if not vals_clean:
            continue
        if len(vals_clean) == 1:
            cond_str = f'{var} {op} "{vals_clean[0]}"'
        else:
            sub = " OR ".join(f'{var} {op} "{v}"' for v in vals_clean)
            cond_str = f"({sub})"
        if lines:
            lines.append("OR")
        lines.append(cond_str)
    return " | ".join(lines)


def build_customlink_block(customlink: str, evar_blocks: list[str],
                           site_pos: list[tuple[str, str, list[str]]] | None = None,
                           site_neg: list[tuple[str, str, list[str]]] | None = None,
                           evar_join: str = "AND") -> str:
    """[US] flat hit(...) 구조 — Component / site 컨테이너 없이 inline AND.

    구조:
      hit(
        customlink starts-with "<cl>"
        AND
        <eVar 블록들 inline ...>      # build_evar_block 결과 (컨테이너 없음)
        AND
        <site 양수 inline OR 묶음>
        AND
        NOT (<site 음수 inline OR 묶음>)
      )

    site_pos / site_neg : list of (operator, var_name, values_list).  큰따옴표 사용.

    evar_join: "AND" (default) | "OR" — evar_blocks 가 2 개 이상일 때 묶음 방식.
       OR 일 때 named container wrap (EVAR_JOIN_WRAP_NAME) 안 OR 토큰으로 묶음.
       (raw paren `(...)` 은 v2_2 의 paren strip 에 잡혀 사라지므로 컨테이너 형태로 보존.)
    """
    parts: list[str] = ["hit("]
    has_first = False
    if customlink:
        parts.append(f'customlink starts-with "{customlink}"')
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
    # 양수 site 필터 — paren 으로 OR 묶음
    pos_inner = _build_us_filter_inner(site_pos or [])
    if pos_inner:
        if has_first:
            parts.append("AND")
        # 여러 항목 또는 multi vals 면 이미 paren 포함, 단일 항목+단일 val 이면 그대로
        parts.append(f"({pos_inner})" if " OR " in pos_inner else pos_inner)
        has_first = True
    # 음수 site 필터 — NOT (...) 으로 감쌈
    neg_inner = _build_us_filter_inner(site_neg or [])
    if neg_inner:
        if has_first:
            parts.append("AND")
        parts.append(f"NOT ({neg_inner})")
        has_first = True
    parts.append(")")
    return " | ".join(parts)


def build_structure(name: str, customlink_blocks: list[str],
                    root_scope: str = "hit") -> str:
    """[US] flat 구조 — name container 없음. customlink_blocks 1 개면 그대로,
    여러 개면 hit( <block1> OR <block2> ... ) 로 nested OR 묶음.

    root_scope:
      · "hit"     → customlink_blocks[0] 그대로 (이미 `hit( ... )`) 또는 nested hit 안 OR
      · "visit"   → `visit( <위 결과> )` 로 한 단 더 감쌈 (COMMON_SEGMENT_REF 빈 문자열이라 AND 묶음 없음)
      · "visitor" → 위와 동일하게 visitor() 로 감쌈
    """
    if not customlink_blocks:
        return ""
    # hit-level 묶음
    if len(customlink_blocks) == 1:
        hit_part = customlink_blocks[0]   # already 'hit( ... )'
    else:
        # 여러 개 — nested hit() 안에 OR 로 묶음
        inner_tokens: list[str] = []
        for i, block in enumerate(customlink_blocks):
            if i > 0:
                inner_tokens.append("OR")
            inner_tokens.append(block)
        hit_part = "hit( | " + " | ".join(inner_tokens) + " | )"

    if root_scope == "hit":
        return hit_part
    # visit / visitor — COMMON_SEGMENT_REF 값 있으면 글로벌처럼 AND 묶음, 없으면 단순 wrap.
    # US 캠페인 visit segment 만들 때 COMMON_SEGMENT_REF 에 US 용 segment-ref ID 박아둘 것
    # (글로벌과 다른 값. 비어있으면 segment-ref 묶음 없이 visit(hit(...)) 만 — hit 모드와 동등 의미).
    if COMMON_SEGMENT_REF:
        # named container wrap — COMMON_SEGMENT_REF_NAME 있으면 '<name>'!hit(@<id>) 로 박음 (reference dsl 스타일)
        if COMMON_SEGMENT_REF_NAME:
            ref_token = f"'{COMMON_SEGMENT_REF_NAME}'!hit( | @{COMMON_SEGMENT_REF} | )"
        else:
            ref_token = f"@{COMMON_SEGMENT_REF}"
        # [v1.3] inner hit 에 'page+content' description 박음 — v2.3 의 _lift_inner_hit_into_visit_root 가
        # description 없는 단일 inner hit wrap 을 벗기는 동작 우회 (visit(hit(AND)) → visit(AND) 깨짐 방지).
        return f"{root_scope}( | 'page+content'!hit( | {ref_token} | AND | {hit_part} | ) | )"
    return f"{root_scope}( | {hit_part} | )"


def _lookup_visit_seg_from_result_csv(base_name: str) -> tuple[str, str]:
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


def _build_delayed_purchase_structure(dp_name: str, base_name: str, customlink_blocks: list[str]) -> str:
    """[US] Delayed Purchase wrap — mixed-AND 패턴 + 'Order (All Products)' named container.

    구조:
      hit(
        [sequence-after] visitor(
          visit(                                       ← outer visit (AND wrapping)
            [sequence-all] visit(                       ← inner visit (sequence container)
              '<visit_name>'!hit( @<visit_id> )         ← Step A — Visit segment @-ref (있으면)
                또는 '<base_name> (Visit)'!hit(<inline>)  ← Visit segment 없으면 inline
              THEN
              '<ATC Visit name>'!hit( @<ATC id> )       ← Step B
            )
            AND
            'Order (All Products)'!hit(                 ← NOT orders (visit-level AND child)
              NOT orders event-exists
            )
          )
          THEN
          visit(                                        ← Stream 2
            'Order (All Products)'!hit(
              orders event-exists
            )
          )
        )
      )

    변경 이력 (v1.2 2026-05-29):
      · 기존 unified 구조 → mixed-AND 패턴 (다른 사람 / CAMPAIGN NAME 컨벤션 따름)
      · `'Order (All Products)'!hit(...)` named container 로 NOT orders / Stream 2 orders 둘 다 wrap
      · `[sequence-after] visitor(...)` + `[sequence-all] visit(...)` 라벨 명시 (AA validator 호환)
      · fallback inline 시 wrapper 를 `'<base>'` → `'<base> (Visit)'` 로 변경
    """
    # visit segment 의 segment-ref 활용 — 가장 최신 result csv 에서 '<base> (Visit)' 의 id lookup
    visit_id, visit_name = _lookup_visit_seg_from_result_csv(base_name)

    parts: list[str] = [
        "hit(",
        "[sequence-after] visitor(",
        "visit(",                        # outer visit (AND wrapping)
        "visit(",                         # inner visit (sequence container) — [sequence-all] 라벨 안 박음 (parser 미인식)
    ]

    if visit_id:
        # B: '<visit name>'!hit( @<visit_id> ) — named container + segment-ref
        parts.append(f"'{visit_name}'!hit(")
        parts.append(f"@{visit_id}")
        parts.append(")")
    else:
        # fallback (A): visit content inline — '<base_name> (Visit)' wrapper 안에 inline
        inner_parts: list[str] = []
        for i, block in enumerate(customlink_blocks):
            toks = block.split(" | ")
            if len(toks) >= 3 and toks[0].strip() == "hit(" and toks[-1].strip() == ")":
                inner = toks[1:-1]
            else:
                inner = toks
            if i > 0:
                inner_parts.append("OR")
            inner_parts.extend(inner)
        parts.append(f"'{base_name} (Visit)'!hit(")
        parts.extend(inner_parts)
        parts.append(")")

    parts.append("THEN")
    # Step B — ATC Visit segment-ref (NOT orders 는 여기 안 들어감)
    if ATC_VISIT_SEGMENT_REF:
        if ATC_VISIT_SEGMENT_NAME:
            parts.append(f"'{ATC_VISIT_SEGMENT_NAME}'!hit(")
            parts.append(f"@{ATC_VISIT_SEGMENT_REF}")
            parts.append(")")
        else:
            parts.append(f"@{ATC_VISIT_SEGMENT_REF}")
    parts.extend([
        ")",                              # close [sequence-all] visit (sequence)
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
    cache_name 예: "us" / "us,add_to_cart_us" — 여러 cache 파일 순서대로 시도, 첫 매칭 반환."""
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
    parser.add_argument("--input", default=seg_make_ref_us_CSV,
                        help="input CSV. 빈 값이면 폴더에서 seg_make_ref_us_*.csv 사전순 최신 1개 자동 선택.")
    parser.add_argument("--output-ts", dest="output_ts", default="",
                        help="출력 파일 ts override (scenario 에서 _global / _us suffix 박을 때 사용). "
                             "빈 값이면 datetime.now() 사용.")
    args = parser.parse_args()

    # SCOPE_MODE 파싱 — 콤마 구분 list. backward compat "both" → ["visit","hit"]
    if SCOPE_MODE.strip().lower() == "both":
        modes = ["visit", "hit"]
    else:
        modes = [m.strip().lower() for m in SCOPE_MODE.split(",") if m.strip()]
    print(f"  [scope] modes: {modes}")

    # COMMON_SEGMENT_REF 결정 — 직접 박은 값 우선, 없으면 REF_SEGMENT_NAME 으로 cache lookup
    global COMMON_SEGMENT_REF, COMMON_SEGMENT_REF_NAME
    if not COMMON_SEGMENT_REF and REF_SEGMENT_NAME:
        looked_id, looked_name = _lookup_seg_ref_by_name(REF_SEGMENT_NAME, CACHE_NAME)
        if looked_id:
            COMMON_SEGMENT_REF = looked_id
            if not COMMON_SEGMENT_REF_NAME:
                COMMON_SEGMENT_REF_NAME = looked_name
    if COMMON_SEGMENT_REF:
        wrap_note = f" (named container wrap: {COMMON_SEGMENT_REF_NAME!r})" if COMMON_SEGMENT_REF_NAME else ""
        print(f"  [common-ref] visit/visitor 모드에서 AND 묶일 segment id: {COMMON_SEGMENT_REF}{wrap_note}")
    elif "visit" in modes or "visitor" in modes:
        print(f"  [common-ref] (없음) — visit/visitor 모드도 segment-ref AND 묶음 없이 단순 visit(hit(...)) 만 생성됨")

    # ATC segment-ref (delayed_purchase 옵션 용) — 직접 박은 값 우선, 없으면 ATC_REF_SEGMENT_NAME 으로 lookup
    global ATC_VISIT_SEGMENT_REF, ATC_VISIT_SEGMENT_NAME
    if "delayed_purchase" in modes:
        if not ATC_VISIT_SEGMENT_REF and ATC_REF_SEGMENT_NAME:
            looked_id, looked_name = _lookup_seg_ref_by_name(ATC_REF_SEGMENT_NAME, CACHE_NAME)
            if looked_id:
                ATC_VISIT_SEGMENT_REF = looked_id
                if not ATC_VISIT_SEGMENT_NAME:
                    ATC_VISIT_SEGMENT_NAME = looked_name
        if ATC_VISIT_SEGMENT_REF:
            print(f"  [atc-ref] delayed_purchase 용 ATC visit segment id: {ATC_VISIT_SEGMENT_REF}  (name: {ATC_VISIT_SEGMENT_NAME!r})")
        else:
            print(f"  [atc-ref] ⚠️ delayed_purchase 활성인데 ATC segment-ref 없음 — prewarm 으로 cache 만들거나 ATC_VISIT_SEGMENT_REF 직접 박기")

    ts = (args.output_ts or "").strip() or datetime.now().strftime("%y%m%d_%H%M")
    out_path     = OUTPUT_DIR / OUTPUT_NAME_TEMPLATE.format(ts=ts)
    out_dsl_path  = OUTPUT_DIR / OUTPUT_DSL_NAME_TEMPLATE.format(ts=ts)
    out_warn_path = OUTPUT_DIR / OUTPUT_WARN_NAME_TEMPLATE.format(ts=ts)

    # ─── input CSV 결정 ─────────────────────────────────────
    # seg_make_ref_us_CSV (또는 --input) 가 비어 있으면 OUTPUT_DIR 의 seg_make_ref_us_*.csv 중
    # 파일명 사전순 최신 1 개 자동 선택. 파일명 timestamp 는 _YYMMDD_HHMM 이므로
    # 사전순 정렬 = 시간순 정렬 (mtime 보다 안정적 — OneDrive 동기화·복사 시 mtime 이 어긋날 수 있어서).
    input_arg = (args.input or "").strip()
    if not input_arg:
        candidates = [p for p in sorted(OUTPUT_DIR.glob("seg_make_ref_us_*.csv"), reverse=True)
                      if "_tmp." not in p.name]   # scenario 임시 csv 제외
        if not candidates:
            print(f"ERROR: seg_make_ref_us_*.csv 못 찾음 — {OUTPUT_DIR}")
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
            # value_col 없어도 OK — crystallize_evar<N> 같은 override 있으면 main evar 블록 빌드 가능
            evar_event_cols.append((hdr, value_col or "", num))
            inline_evar_nums.add(num)
        if evar_event_cols:
            print(f"  [evar-event] 인식된 evar 블록 대상: " +
                  ", ".join(f"eVar{n}" for _, _, n in evar_event_cols))

        # customlink 컬럼 동적 인식 — 헤더 prefix 매칭 ("customlink", "customLink (...)" 등)
        customlink_header = None
        for hdr in fieldnames:
            if re.match(CUSTOMLINK_COLUMN_REGEX, hdr.strip(), flags=re.IGNORECASE):
                customlink_header = hdr
                break
        if customlink_header:
            print(f"  [customlink] 인식된 컬럼: {customlink_header!r}")

        # crystallize 컬럼 매핑 — {evarN: (operator, column_name)} (값 있으면 자동 LCS override)
        # 두 형식 인식:
        #   1) <cond>_crystallize_evar<N>   (예: starts-with_crystallize_evar26) — cond 명시
        #   2) crystallize_evar<N>          (예: crystallize_evar96)             — cond 없음, default starts-with (US 기본)
        # 같은 evar 에 여러 컬럼 가능 → 우선순위 (starts-with > equals > contains-any-of > contains) 로 값 있는 첫 컬럼 사용
        CRYSTALLIZE_OP_PRIORITY = {"starts-with": 0, "equals": 1, "contains-any-of": 2, "contains": 3}
        crystallize_map: dict[str, list[tuple[str, str]]] = {}
        for hdr in fieldnames:
            h = hdr.strip()
            m = re.match(CRYSTALLIZE_COLUMN_REGEX, h, flags=re.IGNORECASE)
            if m:
                cond, varname = m.group(1).lower(), m.group(2).lower()
                op = CRYSTALLIZE_CONDITION_TO_OPERATOR[cond]
                crystallize_map.setdefault(varname, []).append((op, hdr))
                continue
            m2 = re.match(CRYSTALLIZE_COLUMN_REGEX_NO_COND, h, flags=re.IGNORECASE)
            if m2:
                varname = m2.group(1).lower()
                crystallize_map.setdefault(varname, []).append(("starts-with", hdr))   # default starts-with (US)
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
        if filter_columns:
            descs = ", ".join(
                f"{h}→{'NOT ' if n else ''}{v} {o}" for h, (n, o, v) in filter_columns.items()
            )
            print(f"  [filter] 인식된 site/evar 필터 컬럼: {descs}")

        from collections import defaultdict as _dd
        for row in reader:
            seg_name = (row.get("Segment Name") or "").strip()
            customlink = (row.get(customlink_header) or "").strip() if customlink_header else ""
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

            if not evar_blocks:
                skipped.append((seg_name, "_event-exists TRUE 컬럼 0 개"))
                continue

            # 검수용 raw row 저장 (skip 안 된 것만)
            raw_rows.append(row)

            # evar_join 결정 — 우선순위:
            #   1) evar_join 컬럼 명시값 ("OR" / "AND")
            #   2) 컬럼 없거나 값 빈 채 → 자동 default: multi-evar TRUE (>=2) 면 OR, 아니면 AND
            evar_join_col = fieldnames_lower.get(EVAR_JOIN_COLUMN.lower())
            explicit_val = (row.get(evar_join_col) or "").strip().upper() if evar_join_col else ""
            if explicit_val in ("OR", "AND"):
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
                evar_join=m.get("_evar_join") or "AND",
                site_pos=m.get("_site_pos") or [],
                site_neg=m.get("_site_neg") or [],
            ))

        new_name_base = transform_name(raw_seg_name)
        warning_text  = " ; ".join(warnings_by_name.get(raw_seg_name, []))

        if "visit" in modes:
            visit_name = (new_name_base + " (Visit)"
                          if not new_name_base.endswith(" (Visit)") else new_name_base)
            visit_structure = build_structure(visit_name, customlink_blocks, root_scope="visit")
            rows_out.append({
                "segment_id": "", "name": visit_name, "description": "",
                "rsid": DEFAULT_RSID, "tags": DEFAULT_TAGS,
                "structure": visit_structure, "warning": warning_text,
            })
        if "hit" in modes:
            hit_name = new_name_base
            hit_structure = build_structure(hit_name, customlink_blocks, root_scope="hit")
            rows_out.append({
                "segment_id": "", "name": hit_name, "description": "",
                "rsid": DEFAULT_RSID, "tags": DEFAULT_TAGS,
                "structure": hit_structure, "warning": warning_text,
            })
        if "delayed_purchase" in modes:
            dp_name = (new_name_base + " (Delayed Purchase)"
                       if not new_name_base.endswith(" (Delayed Purchase)") else new_name_base)
            dp_structure = _build_delayed_purchase_structure(dp_name, new_name_base, customlink_blocks)
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
