# RESHAPE_standard_v1.0.py  
<sub>2026-06-02  Jonghyun Park w/ Claude</sub>  

`extract_data_*.py` 가 떨군 `extract_data_{site}_YYMMDD_HHMM.csv` 들을 세로로 **union** 해주는 범용(standard) 정제 스크립트. 특정 디멘션/캠페인에 묶이지 않고, 헤더에서 디멘션 값 컬럼을 자동 감지하므로 `campaign`, `evar26` 등 어떤 디멘션 추출본이든 그대로 돌릴 수 있다.

> 입력은 `column_mapping_*.csv`(요약)가 아니라 **`extract_data_*.csv`**(디멘션 항목별 long) 이다. 디멘션 항목 값은 extract_data 에만 들어있다.

## 무엇을 하나

- `output/` 폴더에서 **site 별 최신 ts** 파일 1개씩만 골라 union
- **ITEM** 컬럼 = `segments` 의 `;` split **제일 우측 토큰** (양끝 공백 trim)
  - 예) `Landing Page; Email` → `Email`
- **VALUE** = `value1` 값. `revenue` metric 이면 `currency.csv` 환율 적용, 그 외 원본 그대로
- **COUNTRY** = `site_registry` 로 `site_code` → 국가명
- **SITE CODE** = `SITE_CODE_RENAME` 치환 (예: `xx_old` → `xx`)
- 출력 → `output/_union_standard_{ts}.csv`

## 사용자 설정 (파일 상단 상수)

| 상수 | 설명 |
|---|---|
| `SITES_FILTER` | 처리 site 제한 (빈 리스트면 전체) |
| `OUTPUT_BASENAME` | 출력 파일명 base (`_union_standard`) |
| `SEG_SPLIT_CHAR` | ITEM 추출용 구분자 (기본 `;`) |
| `DIM_COLUMN` | 디멘션 값 컬럼. `""` 면 헤더에서 `itemId` 다음 컬럼 자동 감지 |
| `DIM_OUTPUT_HEADER` | 출력 디멘션 컬럼 헤더명. `""` 면 소스 컬럼명 그대로 |
| `SITE_CODE_RENAME` | 출력 SITE CODE 치환 dict (예: `{"xx_old": "xx"}`) |
| `APPLY_CURRENCY` / `CURRENCY_CSV` / `CURRENCY_METRIC_KEYWORD` | 환율 적용 (revenue metric 한정) |
| `DROP_ZERO_VALUE` | VALUE==0 행 제외 (기본 False) |
| `INCLUDE_REPORTLET` | 출력 우측에 reportlet 검수 컬럼 추가 |

## 환율(currency) 처리

- `revenue` metric 행이 **하나도 없으면** `currency.csv` 불필요 → 그냥 진행 (Entries/Visits 등)
- `revenue` 행이 **있는데 `currency.csv` 가 없으면** → 정제를 **일시정지**하고
  `currency.csv 넣고 Enter (q=중단)` 프롬프트로 파일을 요청 (조용히 미환산 진행 방지)
- 환율이 적용된 batch 면 `VALUE` = 환산값 + **`VALUE (원본)`** 컬럼이 추가된다
- `currency.csv` 형식: 1열 `site_code`, 헤더에 `YYYY-MM-DD` 컬럼들 → 연도(YYYY)별 rate

## 출력 컬럼

```
TIER, SUBS, COUNTRY, SITE CODE, ITEM, VALUE, [VALUE (원본)],
rsid, start_date, end_date, value_n, <디멘션>, segments [, reportlet]
```

## 실행

```bash
python RESHAPE_standard_v1.0.py
```

- 같은 폴더에 `site_registry.py` 필요 (`site_code` → 국가/rsid 매핑)
- 입력은 같은 폴더의 `output/extract_data_*.csv`
- 결과는 `output/_union_standard_{ts}.csv`

## 의존성

```
표준 라이브러리만 사용 (csv, re, pathlib 등) + 같은 폴더 site_registry.py
```
