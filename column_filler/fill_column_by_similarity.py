# fill_column_by_similarity.py
# 2026-05-12  Jonghyun Park w/ Claude
"""
구버전 tb_column_name_mapping CSV 의 column 값을 사용해서, 신버전 매핑 CSV 의 빈 column 컬럼을
유사도 기반으로 채워넣는 유틸.

신버전 CSV 는 `extract_panel_tables_json_v2.0.py` 에서 segments + metric 컬럼이 추가된
형태를 기대 (8열: tb / value_n / column / segments / metric / panel / panel_slug / period).
구버전 CSV 는 column 값이 이미 채워진 (사람이 손으로 정리한) 형태를 기대 — 최소 tb / value_n / column 3열.

매칭 점수 (높을수록 우수):
  · tb_score    = period/panel marker 제거 후 core tb 이름의 multiset Jaccard
  · period_bonus = period 정합 보너스 (last/campaign 은 strict 필터로 처리, prior 만 soft)
  · seg_bonus   = 신버전 segments 컬럼의 토큰이 구버전 column 값 토큰과 얼마나 겹치는지
                  (segments = 그 cell 의 segment 이름 ';' join — 새 v2.0 추출 결과에 추가됨)
  · metric_bonus = 신버전 metric 컬럼 (compact lowercase) 이 구버전 column 의 metric suffix
                   와 substring/역substring 정합 (단/복수 차이 흡수)
  · seq_tie     = SequenceMatcher.ratio() × 0.001 (Jaccard 동률 미세 tiebreak)

매칭 룰:
  1. 같은 value_n 안에서만 후보 선택
  2. period/panel 따라 strict 필터:
     · period=last     → 구 tb 가 'last_' 로 시작 (strict)
     · period=campaign → 구 tb 가 base (last_ / _prior 둘 다 아님, strict)
     · period=prior    → 구 tb 필터 없음 (soft — prior 변형 없는 tb 도 base 로 fallback)
     · panel=us_*      → 구 tb 가 'us_' 로 시작
     · panel=all_*     → 구 tb 가 'us_' 시작 X
  3. 후보 중 점수 합이 최대인 행의 column 값 차용
  4. 매칭 후보 0개면 row.column 은 비워둠

출력: 입력파일과 같은 폴더에 `<원본이름>_filled.csv` 로 저장. 원본은 안 건드림.
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# 구버전 매핑 CSV — column 값이 채워져있는 reference (최소 tb / value_n / column 3열)
OLD_CSV = Path(r"C:\path\to\your\old\tb_column_name_mapping.csv")
# 신버전 매핑 CSV — extract_panel_tables_json_v2.0.py 로 새로 추출된 결과 (segments / metric 컬럼 포함)
NEW_CSV = Path(r"C:\path\to\your\new\tb_column_name_mapping_YYMMDD_HHMM.csv")


# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
OUT_CSV = NEW_CSV.with_name(NEW_CSV.stem + "_filled.csv")


def _classify_old_tb(tb: str) -> tuple[bool, bool, bool]:
    """(is_us, is_last, is_prior) 반환."""
    is_us    = tb.startswith("us_")
    rest     = tb[3:] if is_us else tb
    is_last  = rest.startswith("last_")
    is_prior = rest.endswith("_prior")
    return is_us, is_last, is_prior


def _filter_old_candidates(old_rows: list[dict], value_n: str, panel: str, period: str) -> list[dict]:
    """value_n + panel scope + period 따라 strict 필터링.

    · period=last     → 구 tb 가 'last_' 시작 (strict)
    · period=campaign → 구 tb 가 'last_' 시작 X 이고 '_prior' 끝 X (strict)
    · period=prior    → 별도 period 필터 없음 (soft — score 보너스로 처리).
                        구버전 set 에 _prior 변형이 없는 tb 가 있어서 strict 면 망함.
    """
    want_us = panel.startswith("us_")
    out = []
    for r in old_rows:
        if r["value_n"] != value_n:
            continue
        is_us, o_is_last, o_is_prior = _classify_old_tb(r["tb"])
        if is_us != want_us:
            continue
        if period == "last" and not o_is_last:
            continue
        if period == "campaign" and (o_is_last or o_is_prior):
            continue
        # period == "prior" 는 강한 필터 안 함
        out.append(r)
    return out


def _strip_period_markers(tb: str) -> str:
    """tb 이름에서 us_/last_ prefix 와 _prior suffix 만 떼서 core 토픽 이름만 반환."""
    s = tb
    if s.startswith("us_"):
        s = s[3:]
    if s.startswith("last_"):
        s = s[5:]
    if s.endswith("_prior"):
        s = s[:-6]
    return s


def _tokens(s: str) -> Counter:
    return Counter(t for t in s.split("_") if t)


def _multiset_jaccard(a: str, b: str) -> float:
    """token multiset Jaccard — short tb 와 long tb 비교에 SequenceMatcher 보다 안정.
    분자 = 토큰 multiset 교집합 (min count). 분모 = 합집합 (max count)."""
    ca = _tokens(a)
    cb = _tokens(b)
    if not ca or not cb:
        return 0.0
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return inter / union if union else 0.0


_TOKEN_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")


def _name_tokens(s: str) -> set[str]:
    """사람 친화적 segment 이름 / column 값을 비교 가능한 토큰 set 으로 분해.
    영숫자 외 문자 (공백, [, ], (, ), &, ;, _, > 등) 모두 split 한 후 lowercase.
    빈 토큰, 매우 짧은 토큰 (length 1) 제거.
    """
    if not s:
        return set()
    parts = _TOKEN_SPLIT_RE.split(s.lower())
    return {p for p in parts if len(p) >= 2}


def _seg_bonus(new_segments: str, old_column: str) -> float:
    """segments 토큰 ∩ old column 토큰 개수 기반 보너스.
    분모는 max(len(seg_tokens), 4) 로 stabilize — 토큰 수 너무 적으면 폭주 방지.
    범위: 0 ~ ~0.15."""
    seg_tokens = _name_tokens(new_segments)
    if not seg_tokens:
        return 0.0
    col_tokens = _name_tokens(old_column)
    if not col_tokens:
        return 0.0
    inter = seg_tokens & col_tokens
    denom = max(len(seg_tokens), 4)
    return (len(inter) / denom) * 0.15


def _metric_bonus(new_metric: str, old_column: str) -> float:
    """metric 이름이 old column 값의 metric suffix 와 정합되는지 검사.

    metric 표시 이름과 old column 의 metric suffix 는 표기가 다름:
      · new "Visits"             → compact "visits"      / old 마지막 토큰 "visit"
      · new "Unique Visitors"    → compact "uniquevisitors" / old "uniquevisitor"
      · new "Non bounced visit"  → compact "nonbouncedvisit" / old "nonbouncedvisit"
      · new "Orders"             → compact "orders"      / old "order"
    bidirectional substring (한쪽이 다른쪽 substring) 검사로 단/복수 차이까지 흡수.
    범위: 0 / +0.05 / +0.08."""
    if not new_metric or not old_column:
        return 0.0
    nm_compact = re.sub(r"[^a-z]", "", new_metric.lower())
    if not nm_compact or len(nm_compact) < 3:
        return 0.0
    oc_lower = old_column.lower()
    # 1) 직접 substring 매치 (compact 전체가 old 안에)
    if nm_compact in oc_lower:
        return +0.08
    # 2) old 의 마지막 토큰 (metric suffix) 과 bidirectional substring
    last_token = oc_lower.rsplit("_", 1)[-1]
    if last_token and len(last_token) >= 3:
        if last_token in nm_compact or nm_compact in last_token:
            return +0.05
    return 0.0


def _score(new_tb: str, old_tb: str, new_period: str,
           new_segments: str, new_metric: str, old_column: str) -> float:
    """매칭 점수.

    tb_score      = period/panel marker 제거 후 core tb 이름의 token multiset Jaccard (0~1)
    period_bonus  = period 정합 시 우대 (prior 만 mixed 후보라 의미 있음)
    seg_bonus     = new.segments 토큰 ∩ old.column 토큰 (0~0.15 가중)
    metric_bonus  = new.metric compact 이 old.column 의 metric suffix 와 정합 (0/+0.05/+0.08)
    seq_tie       = SequenceMatcher × 0.001 — Jaccard 동률 미세 tiebreak
    """
    new_core = _strip_period_markers(new_tb)
    old_core = _strip_period_markers(old_tb)
    tb_score = _multiset_jaccard(new_core, old_core)
    seq_tie  = SequenceMatcher(None, new_core, old_core).ratio() * 0.001
    _, o_is_last, o_is_prior = _classify_old_tb(old_tb)
    period_bonus = 0.0
    if new_period == "prior":
        if   o_is_prior: period_bonus = +0.05
        elif o_is_last:  period_bonus = -0.02
        else:            period_bonus = +0.02
    seg_b = _seg_bonus(new_segments, old_column)
    metric_b = _metric_bonus(new_metric, old_column)
    return tb_score + period_bonus + seg_b + metric_b + seq_tie


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    old_rows = list(csv.DictReader(open(OLD_CSV, encoding="utf-8-sig")))
    new_rows = list(csv.DictReader(open(NEW_CSV, encoding="utf-8-sig")))
    print(f"OLD: {len(old_rows)} rows  ({OLD_CSV.name})")
    print(f"NEW: {len(new_rows)} rows  ({NEW_CSV.name})")
    print()

    out_rows: list[dict] = []
    n_filled        = 0
    n_no_candidate  = 0
    no_cand_examples: list[str] = []
    weak_match_examples: list[tuple[float, str, str, str]] = []

    for n in new_rows:
        cands = _filter_old_candidates(old_rows, n["value_n"], n.get("panel", ""), n.get("period", ""))
        if not cands:
            # us/all scope 필터도 풀고 value_n 만으로 fallback (very loose)
            cands = [r for r in old_rows if r["value_n"] == n["value_n"]]
            if not cands:
                n_no_candidate += 1
                if len(no_cand_examples) < 8:
                    no_cand_examples.append(f"{n['tb']:<55} value_n={n['value_n']:<8} period={n.get('period','')} panel={n.get('panel','')}")
                out_rows.append({**n, "column": ""})
                continue

        # tb 유사도 + period bonus + segments 토큰 + metric 매칭 + tie-break 합산 best pick
        n_segs   = n.get("segments", "")
        n_metric = n.get("metric", "")
        n_period = n.get("period", "")
        best  = max(cands, key=lambda r: _score(n["tb"], r["tb"], n_period, n_segs, n_metric, r["column"]))
        score = _score(n["tb"], best["tb"], n_period, n_segs, n_metric, best["column"])
        if score < 0.5 and len(weak_match_examples) < 15:
            weak_match_examples.append((score, n["tb"], best["tb"], n_period))

        out_rows.append({**n, "column": best["column"]})
        n_filled += 1

    # CSV 출력 — 신버전과 동일 컬럼 순서
    fieldnames = list(new_rows[0].keys())
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    print(f"채움    : {n_filled}")
    print(f"후보없음: {n_no_candidate}")
    print(f"출력    : {OUT_CSV}")
    if no_cand_examples:
        print("\n[후보 없음 예시]")
        for ex in no_cand_examples:
            print(f"  {ex}")
    if weak_match_examples:
        print("\n[저신뢰 매칭 예시 (score < 0.5)]")
        for score, n_tb, o_tb, per in weak_match_examples:
            print(f"  {score:.2f}  [{per:<8}]  {n_tb:<55} → {o_tb}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
