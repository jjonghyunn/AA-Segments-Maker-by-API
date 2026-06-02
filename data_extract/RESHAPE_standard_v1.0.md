# RESHAPE_standard_v1.0.py  
<sub>2026-06-02  Jonghyun Park w/ Claude</sub>  

`extract_data_*.py` 가 site 별로 떨군 추출 CSV 들을 **하나로 합치고(union) 보기 좋게 정리**해주는 범용 정제 스크립트.
특정 디멘션(`campaign`, `evar26` 등)에 묶이지 않는다 — 디멘션 컬럼을 **자동 감지**하므로, 어떤 추출본이든 거의 설정 없이 그대로 돌릴 수 있다.

> 입력은 `extract_data_*.csv` (디멘션 **항목별** 값이 들어있는 long 형식) 이다.
> `column_mapping_*.csv` (요약본)가 아니다 — 디멘션 항목 값은 extract_data 에만 있다.

---

## 한눈에 — 무엇을 해주나

`output/` 폴더의 `extract_data_{site}_{날짜}.csv` 들을 모아:

1. **site 별 최신 파일 1개씩만** 골라서 세로로 union
2. 분석에 바로 쓰기 좋은 컬럼들로 재배치 + 아래 가공 추가
3. `output/_union_standard_{날짜시간}.csv` 한 파일로 저장

### 입력 → 출력 (한 행 예시)

입력 `extract_data_*.csv` 의 한 행:

| site_code | … | campaign | value_n | metric | segments | value1 |
|---|---|---|---|---|---|---|
| us | … | `dis-mktg-...-803487` | value1 | Entries | `Landing Page; Email` | 1234 |

출력 `_union_standard_*.csv` 의 한 행:

| TIER | SUBS | COUNTRY | SITE CODE | ITEM | VALUE | … | campaign | segments |
|---|---|---|---|---|---|---|---|---|
| | | United States | us | **Email** | 1234 | … | `dis-mktg-...-803487` | `Landing Page; Email` |

- **ITEM** = `segments` 의 `;` 로 나눈 **맨 오른쪽 값**(양끝 공백 제거) → `Landing Page; Email` 에서 `Email`
- **COUNTRY** = `site_registry` 로 site_code 를 국가명으로
- **SITE CODE** = `SITE_CODE_RENAME` 로 표기 정리 (예: 잘못된/구버전 코드 → 정식 코드)
- 디멘션 컬럼명(`campaign`)은 **추출한 디멘션 그대로** 따라온다 (evar26 이면 `evar26` 으로)

---

## 거르기 / 가공 동작

- **디멘션 값 제외** — `DIM_EXCLUDE_VALUES` 에 (대소문자 무시) **정확히 일치**하는 디멘션값 행은 버린다.
  - 기본: `Unspecified`, `null`, `(summary)` ← 합계/미지정/빈값 라벨. 디멘션 종류에 따라 추가·제거.
- **환율 적용 (revenue 한정)** — `metric` 에 `revenue` 글자가 **포함되면**(부분일치, 대소문자 무시) `currency.csv` 환율을 곱한다.
  - `Revenue`, `Revenue (KRW)`, `Total Revenue` 등 전부 해당. `Entries`/`Visits` 같은 비-금액 metric 은 그대로.
  - revenue 행이 **있는데** `currency.csv` 가 **없으면** → 정제를 **멈추고** 파일을 넣어달라고 물어본다(조용히 미환산 진행 방지).
  - 환율이 적용된 경우 `VALUE` = 환산값 + `VALUE (원본)` 컬럼이 추가된다.
- **0 값 제외 (옵션)** — `DROP_ZERO_VALUE = True` 면 VALUE 가 0 인 행 제거.

---

## 사용자 설정 (파일 상단 상수만 만지면 됨)

| 상수 | 무슨 일 | 언제 바꾸나 |
|---|---|---|
| `SITES_FILTER` | 특정 site 만 처리 (빈 리스트=전체) | 일부 site 만 뽑을 때 |
| `OUTPUT_BASENAME` | 출력 파일명 앞부분 | 결과 이름 바꿀 때 |
| `SEG_SPLIT_CHAR` | ITEM 뽑을 구분자 (기본 `;`) | segments 구분자가 다를 때 |
| `DIM_COLUMN` | 디멘션 값 컬럼. `""` 면 자동 감지 | 자동 감지가 틀릴 때만 수동 지정 |
| `DIM_OUTPUT_HEADER` | 출력 디멘션 컬럼명. `""` 면 원래 이름 | 컬럼명 고정하고 싶을 때(예: `CID`) |
| `DIM_EXCLUDE_VALUES` | 이 값들과 일치하는 디멘션 행 제외 | 디멘션 따라 거를 라벨 추가/제거 |
| `SITE_CODE_RENAME` | 출력 SITE CODE 표기 치환 dict | 구코드→정식코드 정리할 때 |
| `APPLY_CURRENCY` · `CURRENCY_CSV` · `CURRENCY_METRIC_KEYWORD` | revenue 환율 적용 | 금액 metric 환산할 때 |
| `DROP_ZERO_VALUE` | VALUE==0 행 제외 | 0 행이 많아 빼고 싶을 때 |
| `INCLUDE_REPORTLET` | 검수용 reportlet 컬럼 추가 | 출처 확인 필요할 때 |

---

## 출력 컬럼

```
TIER, SUBS, COUNTRY, SITE CODE, ITEM, VALUE, [VALUE (원본)],
rsid, start_date, end_date, value_n, <디멘션>, segments [, reportlet]
```
- `<디멘션>` 자리는 자동 감지된 디멘션 이름(`campaign`, `evar26` …)
- `VALUE (원본)` 은 환율이 적용된 경우에만 추가

## 실행

```bash
python RESHAPE_standard_v1.0.py
```
- 같은 폴더에 `site_registry.py` 필요 (site_code → 국가/rsid)
- 입력: 같은 폴더 `output/extract_data_*.csv`
- 결과: `output/_union_standard_{날짜시간}.csv`
- revenue 행이 있으면 `currency.csv` (1열 site_code + 헤더에 `YYYY-MM-DD` 컬럼들) 도 같은 폴더에 필요

## 의존성

표준 라이브러리(csv, re, pathlib 등)만 사용 + 같은 폴더 `site_registry.py`.
