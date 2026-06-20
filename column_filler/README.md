# column_filler/ — tb_column_name_mapping CSV 의 빈 column 자동 채움  
<sub>2026-06-09  Jonghyun Park w/ Claude</sub>  

캠페인 시즌이 바뀌어서 새 매핑 CSV (`tb_column_name_mapping_{ts}.csv`) 를 뽑았을 때, **이전 시즌의 column 컬럼 정리본** 을 참조해서 새 CSV 의 column 컬럼을 유사도 기반으로 자동 채워주는 유틸.

## 도구

| 파일 | 입력 | 출력 |
|---|---|---|
| `fill_column_by_similarity.py` | OLD_CSV (column 채워진 reference) + NEW_CSV (column 비어있는 신버전, segments/metric 컬럼 포함) | `<NEW>_filled.csv` (column 자동 채움) |

## 사용 시나리오

1. 새 캠페인 / 새 시즌마다 `tb_column_name_mapping_{ts}.csv` 가 새로 생성됨 (column 컬럼은 빈 값).
2. 이전 시즌엔 비슷한 panel 구조에 column 명을 손으로 정리해서 reference 가 있음.
3. 두 CSV 를 이 도구에 넣으면 신버전 column 컬럼이 유사도 기반으로 자동 채워짐.
4. 결과 CSV (`*_filled.csv`) 를 검토하고 prior 변형이 없는 일부 행만 수동 보정.

## 입력 CSV 컬럼 요구

**구버전 (OLD_CSV):**
- 최소 `tb` / `value_n` / `column` 3열 필요
- column 은 채워져있어야 함 (참조 reference)

**신버전 (NEW_CSV):** — 매핑 CSV 출력 형식
- `tb` / `value_n` / `column` / `segments` / `metric` / `panel` / `panel_slug` / `period`
- column 은 비어있음 (이 도구가 채움)
- segments / metric 가 매칭 점수에 활용됨

## 매칭 점수 (높을수록 우수)

```
total = tb_score + period_bonus + seg_bonus + metric_bonus + seq_tie
```

| 요소 | 계산 |
|---|---|
| `tb_score` | period/panel marker 제거 후 core tb 이름의 token multiset Jaccard (0~1) |
| `period_bonus` | period 정합 보너스 — last/campaign 은 strict 필터로 처리, prior 만 soft 보너스 (-0.02 ~ +0.05) |
| `seg_bonus` | new.segments 토큰 ∩ old.column 토큰 (0~0.15 가중) |
| `metric_bonus` | new.metric compact 이 old.column 의 metric suffix 와 정합 (0/+0.05/+0.08) |
| `seq_tie` | SequenceMatcher × 0.001 — Jaccard 동률 미세 tiebreak |

## 매칭 룰

1. **같은 value_n 안에서만** 후보 선택
2. **period/panel strict 필터:**
   - `period=last`     → 구 tb 가 `last_` 로 시작 (strict)
   - `period=campaign` → 구 tb 가 base (last_ / _prior 둘 다 아님, strict)
   - `period=prior`    → 별도 period 필터 없음 (soft — score 보너스로 처리). 구버전에 `_prior` 변형이 없는 tb 가 있어서 strict 면 망함
   - `panel=us_*`      → 구 tb 가 `us_` 시작
   - `panel=all_*`     → 구 tb 가 `us_` 시작 X
3. 후보 중 점수 합이 최대인 행의 column 값 차용
4. 매칭 후보 0개면 row.column 은 비워둠

## metric 매칭 처리

new metric 표시 이름 vs old column metric suffix — 표기가 다름. compact (lowercase + 영숫자만) 으로 normalize 후 양방향 substring 검사로 단/복수 차이까지 흡수.

| new metric | compact | old column suffix | 매칭? |
|---|---|---|---|
| `Visits` | `visits` | `_visit` | ✓ (substring) |
| `Unique Visitors` | `uniquevisitors` | `_uniquevisitor` | ✓ (substring) |
| `Non bounced visit` | `nonbouncedvisit` | `_nonbouncedvisit` | ✓ (직접) |
| `Orders` | `orders` | `_order` | ✓ (substring) |

## 사용자가 바꿔야 할 상수 (코드 상단)

```python
OLD_CSV = Path(r"C:\path\to\your\old\tb_column_name_mapping.csv")
NEW_CSV = Path(r"C:\path\to\your\new\tb_column_name_mapping_YYMMDD_HHMM.csv")
```

`OUT_CSV` 는 NEW_CSV 의 stem + `_filled.csv` 로 자동 결정 (예: `tb_column_name_mapping_260512_1455_filled.csv`).

## 실행

```bash
python fill_column_by_similarity.py
```

dry-run 옵션 없음 — 항상 새 파일 (`_filled.csv`) 생성, 원본은 안 건드림. 결과 검토 후 OLD_CSV 자리에 옮겨서 활용.

## 콘솔 출력 예시

```
OLD: 1414 rows  (tb_column_name_mapping_OLD.csv)
NEW: 1729 rows  (tb_column_name_mapping_NEW.csv)

채움    : 1729
후보없음: 0
출력    : .../tb_column_name_mapping_NEW_filled.csv

[저신뢰 매칭 예시 (score < 0.5)]
  0.49  [campaign]  short_tb_name                                        → other_short_tb_name
  ...
```

저신뢰 (`score < 0.5`) 매칭은 tb 이름이 짧아서 jaccard 분모가 작아 점수가 낮을 뿐, 실제 매칭은 의미상 옳은 경우가 많음. metric / segments / column 값을 직접 확인해서 검증할 것.

## 흔한 함정

| 증상 | 원인 / 해결 |
|---|---|
| `KeyError: 'segments'` / `'metric'` | NEW_CSV 가 v1 추출 결과 (segments/metric 컬럼 없음). v2.0 으로 재추출 필요 |
| prior 행이 base year 값으로 채워짐 | old CSV 에 그 tb 의 `_prior` 변형이 없어서 base 로 fallback. column 결과에 `_prior` marker 가 빠져있을 수 있음 — prior 행만 따로 수동 보정 |
| `period=campaign` 행이 last year 컬럼으로 채워짐 | strict 필터 동작 정상이라면 발생 X — NEW_CSV 의 period 컬럼이 비어있거나 잘못 채워졌는지 확인 |
| 모든 행이 `채움 0` | OLD_CSV 와 NEW_CSV 가 다른 회사 / 다른 RSID 구조 → tb 이름이 전혀 안 겹쳐 jaccard 0. NAME_NORMALIZATION 룰 (token 기반) 검토 |
