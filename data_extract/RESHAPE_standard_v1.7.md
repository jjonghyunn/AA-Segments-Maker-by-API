# RESHAPE_standard_v1.7.py  
<sub>2026-07-31  Jonghyun Park w/ Claude</sub>  

`extract_data_v*.py` 가 site 별로 떨군 추출 CSV(`stack_data_extract_*`, 구버전 `extract_data_*`) 들을 **하나로 합치고(union) 보기 좋게 정리**해주는 범용 정제 스크립트.
특정 디멘션(`campaign`, `evar26` 등)에 묶이지 않는다 — 디멘션 컬럼을 **자동 감지**하므로, 어떤 추출본이든 거의 설정 없이 그대로 돌릴 수 있다.

> 입력은 `stack_data_extract_*.csv` (디멘션 **항목별** 값이 들어있는 long 형식, 구버전 `extract_data_*` 호환) 이다.
> `table_data_extract_*.csv` (가로형, 기존 `column_mapping_*` 대체)가 아니다 — RESHAPE 가 쓰는 long 형식은 stack 쪽이다.

---

## 한눈에 — 무엇을 해주나

`output/` 폴더의 `stack_data_extract_{site}_{날짜}.csv` (구버전 `extract_data_*` 포함) 들을 모아:

1. **site 별 최신 파일 1개씩만** 골라서 세로로 union
2. 분석에 바로 쓰기 좋은 컬럼들로 재배치 + 아래 가공 추가
3. `output/_union_standard_{날짜시간}.csv` (long) + `_union_standard_wide_*.csv` (wide) 저장

### 입력 → 출력 (한 행 예시)

입력 `stack_data_extract_*.csv` 의 한 행:

| site_code | … | campaign | value_n | metric | segments | value1 |
|---|---|---|---|---|---|---|
| us | … | `tracking-code-example` | value1 | Entries | `Landing Page; Email` | 1234 |

출력 `_union_standard_*.csv` 의 한 행:

| TIER | SUBS | COUNTRY | SITE CODE | ITEM | VALUE | … | campaign | segments |
|---|---|---|---|---|---|---|---|---|
| | | United States | us | **Email** | 1234 | … | `tracking-code-example` | `Landing Page; Email` |

- **ITEM** = `segments` 의 `;` 로 나눈 **맨 오른쪽 값**(양끝 공백 제거) → `Landing Page; Email` 에서 `Email`
- **COUNTRY** = `site_registry` 로 site_code 를 국가명으로
- **SITE CODE** = `SITE_CODE_RENAME` 표기 치환 + **`_old` 접미사 일괄 제거** (v1.1, `uk_old`→`uk` 등 — `SITE_CODE_STRIP_OLD`)
- 디멘션 컬럼명(`campaign`)은 **추출한 디멘션 그대로** 따라온다 (evar26 이면 `evar26` 으로)

### metric_origin / 정제 metric · value_origin · wide union (v1.5)

- **metric_origin / metric** — 입력 `metric_origin`(원본 metric 명) 그대로 + `metric` 은 **정제본**. RESHAPE 가 `metric_origin` 에서 직접 정제(`_normalize_metric`)하므로 미정제 stack 이 섞여도 일관:
  - 끝 괄호 제거(이벤트/맥락): `Order (purchase event)` → `Order`, `Visits (for External Order)` → `Visits`
  - 단위 괄호 유지: `Time Spent per Visit (seconds)` → 그대로
  - 별칭: `AppBounce` → `Bounces` (`METRIC_ALIASES`, extract_data 와 동기화)
- **value_origin** — 환율 적용 시 원본값 컬럼 (이전 `VALUE (원본)` 에서 이름 통일).
- **wide union** — `_union_standard_wide_{날짜시간}.csv` 추가 출력. **정제 metric 값들을 열 헤더로 승격**(가로 나열, fx `VALUE` 채움). 행 식별(index) = metric/value 계열 제외 전부(디멘션값·segments·device·panel·variable 등 포함). long(`_union_standard`)과 함께 2종 출력.
  - **(v1.6) revenue 분리** — revenue 계열 metric(이름에 `revenue` 포함, value_origin 있을 때)은 wide 에서 **`<metric>_org`(원본) + `<metric>`(fx)** 두 열로 분리(`Revenue` → `Revenue_org` + `Revenue`). 비-revenue metric 은 단일 열 유지.

### variable 컬럼 (v1.6)

extract 의 `dimension`(예: `variables/evar26`)에서 `variables/` 앞부분을 떼고 **뒤 토큰**(`evar26`·`product`·`marketingchannel` 등)만 `variable` 컬럼으로 출력한다(**long·wide 둘 다**). 어떤 변수(evar/prop/marketingchannel)에서 나온 dim 값인지 식별용. `INCLUDE_VARIABLE_COLUMN`(기본 True)로 on/off, extract 에 `dimension` 컬럼이 없으면(구버전) 자동 skip.

### product category 분류 (v1.4)

panel/table/reportlet 이름에 **product 키워드**(`Multi Purchase` / `Multi Order` / `Best Selling Product`, 대소문자·언더바 무시)가 있으면, 그 행의 **제품코드(자동 감지된 디멘션 값)** 를 `product_category.yaml`(divisions → categories → include/exclude regex)로 분류해 `category` 컬럼을 추가한다.

- **multi 모드** (`Multi Purchase` / `Multi Order`): 디멘션 값이 콤마로 묶인 다제품 (예: `AAA-000,BBB-000`) →
  - `category` = 각 제품 카테고리를 **알파벳 오름차순**으로 콤마 조인 (ACC·Unknown 포함, 중복 유지)
  - `category_non_acc_unknown_excl` = 같은 리스트에서 **ACC·Unknown 제외**
- **single 모드** (`Best Selling Product`): 단일 제품 → `category` 만 (`category_non_acc_unknown_excl` 은 빈칸)
- 어느 카테고리에도 안 걸리는 제품코드 → **`Unknown`**.
- 분류 룰은 `product_category.yaml` 만 사용 (파일 순서대로 첫 매칭, division 은 매칭된 카테고리와 함께 결정).
- `ADD_CATEGORY_COLUMN = True` 로 on/off. **yaml 이 없을 때**: 키워드 매칭 행이 있으면 경고만 하고 분류 skip, 매칭 행이 없으면 조용히 pass (정제는 정상 진행).
- 키워드가 없는 행(예: Cross-Sell / Total Order)은 두 컬럼이 빈칸.

> 모드 판정은 **table+reportlet(구체 필드) 우선**, 거기서 신호 없으면 panel fallback. 한 패널에 두 키워드가 다 있어도(예: `Multi Purchase & Best Selling Products`) table/reportlet 으로 정확히 구분된다.

### 입력 파일명 개편 대응 (v1.3)

- extract_data_v3.7 부터 출력이 `stack_data_extract_*`(long unpivot) / `table_data_extract_*`(가로형) 2종으로 분리 — RESHAPE 입력은 **stack** 쪽.
- 구버전 `extract_data_*` 파일명도 계속 인식 (같은 site 면 ts 최신 파일 승리).

### extract_data_v3.5 출력 대응 (v1.1)

- **breakdown 행 모드** — v3.5 출력은 dim1 총계 행 + breakdown 행(`bd{k}_itemId` 채워짐)이 같이 있음. `BREAKDOWN_ROWS_MODE` 로 선택:
  - `"include"` (기본): 둘 다 union — Workspace 테이블 그대로. ⚠ 단순 합산 시 이중집계 주의 (`bd{k}_itemId` 빈칸 여부로 총계/breakdown 구분해서 합산)
  - `"exclude"`: dim1 총계만 (v1.0 semantics)
  - `"only"`: breakdown 행만
- **passthrough 컬럼** — 입력에 `device` / `period` / `bd{k}_dimension/itemId/value` 컬럼이 있으면 출력에 그대로 따라옴 (`PASSTHROUGH_COLUMNS` 상수 + `bd{k}_*` 정규식). v3.4 이하 출력(bd 컬럼 없음)은 모드 무관 전체 처리.
  - **(v1.7) `period`** — `extract_data_v4.3` 를 `MONTHLY=True` 로 뽑으면 월 라벨(`Jul 2026`)이 `period` 컬럼에 들어온다. v1.6 은 이걸 안 넘겨서 월 라벨이 유실됐다(행은 `start_date`/`end_date` 로 분리 유지). long·wide 양쪽에 실린다.

### 출력 컬럼 추가 (v1.2)

- **metric** — 입력 `metric` 컬럼 passthrough (`value_n` 다음 위치). 어떤 metric 의 값인지 바로 확인.
- **Panel name** — 입력 `panel` 컬럼 passthrough (`reportlet` 왼쪽). 어느 panel 에서 온 값인지 검수용.
- **`EXCLUDE_OUTPUT_COLUMNS`** — 출력에서 빼고 싶은 컬럼명 나열 (대소문자 무시). 빈 리스트(기본)면 전부 유지. 예) `["Panel name", "value_n"]`

---

## ⚠ 새 추출 구성(한 파일에 여러 패널) 사용 시 주의 (2026-07-31)

`extract_data` 를 **site 당 1행 · 전 패널 한 파일** 구성으로 쓰면 아래를 확인할 것.
(`RESHAPE_standard` 자체는 수정 없이 그대로 동작한다.)

- **분류 구분은 `Panel name` 컬럼으로** — 입력 `panel` 이 출력에 그대로 실린다. 한 파일에 분류가
  섞여도 사후에 이 컬럼으로 나눌 수 있다.
- **dim 컬럼 자동 감지는 이미 대응** — `detect_dim_column()` 이 헤더의 `itemId` 다음 컬럼을 잡으므로
  여러 차원이 섞여 `dim_value` 로 나와도 그대로 읽는다.
- ⚠ **`DIM_EXCLUDE_VALUES` 기본값이 `Unspecified` 를 버린다.** 디멘션 미지정 라벨이라 보통은 맞지만,
  **분류가 섞인 패널에서는 `Unspecified` 가 "그 분류가 아님"을 뜻하는 신호일 수 있다**(예: store 값이
  안 찍힌 행 = 비-전용 트래픽). 그런 raw 를 돌릴 땐 이 목록에서 빼야 조용히 사라지지 않는다.
- ⚠ **`SITES_FILTER` 는 raw `site_code` 기준**이다(`SITE_CODE_STRIP_OLD` 적용 **전**). 출력
  `SITE CODE` 는 `_old` 가 떨어진 값이라 서로 다르다 — `us_old` 를 거르려면 `us_old` 라고 적을 것.
- **(site × year) 조합 제외 수단은 없다** — `SITES_FILTER` 는 site 단위 화이트리스트뿐이다.
  구/신 suite 가 같은 기간을 이중으로 담는 경우처럼 연도까지 봐야 하면, 캠페인 전용 RESHAPE 쪽에
  조합 제외 룰을 두거나 추출 단계에서 걸러야 한다.

## 거르기 / 가공 동작

- **디멘션 값 제외** — `DIM_EXCLUDE_VALUES` 에 (대소문자 무시) **정확히 일치**하는 디멘션값 행은 버린다.
  - 기본: `Unspecified`, `null` ← 미지정/빈값 라벨. 디멘션 종류에 따라 추가·제거.
- **환율 적용 (revenue 한정)** — `metric` 에 `revenue` 글자가 **포함되면**(부분일치, 대소문자 무시) `currency.csv` 환율을 곱한다.
  - `Revenue`, `Revenue (KRW)`, `Total Revenue` 등 전부 해당. `Entries`/`Visits` 같은 비-금액 metric 은 그대로.
  - revenue 행이 **있는데** `currency.csv` 가 **없으면** → 정제를 **멈추고** 파일을 넣어달라고 물어본다(조용히 미환산 진행 방지).
  - 환율이 적용된 경우 `VALUE` = 환산값 + `value_origin` 컬럼이 추가된다.
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
| `SITE_CODE_STRIP_OLD` | `_old` 접미사 일괄 제거 (기본 True) | 구 suite 코드 그대로 두고 싶을 때만 False |
| `BREAKDOWN_ROWS_MODE` | v3.5 breakdown 행 처리 (`include`/`exclude`/`only`) | 총계만·breakdown만 필요할 때 |
| `APPLY_CURRENCY` · `CURRENCY_CSV` · `CURRENCY_METRIC_KEYWORD` | revenue 환율 적용 | 금액 metric 환산할 때 |
| `DROP_ZERO_VALUE` | VALUE==0 행 제외 | 0 행이 많아 빼고 싶을 때 |
| `INCLUDE_REPORTLET` | 검수용 reportlet 컬럼 추가 | 출처 확인 필요할 때 |
| `EXCLUDE_OUTPUT_COLUMNS` | 출력에서 뺄 컬럼명 리스트 (대소문자 무시, 빈 리스트=전부 유지) | 안 쓰는 컬럼 빼고 가볍게 뽑을 때 |
| `PASSTHROUGH_COLUMNS` | 입력에 있으면 출력에 그대로 싣는 컬럼명 (기본 `["device", "period"]`, v1.7). `bd{k}_*` 는 정규식으로 자동 | extract 에 새 부가 컬럼이 생겼을 때 |
| `INCLUDE_VARIABLE_COLUMN` · `VARIABLE_SOURCE_COLUMN` · `VARIABLE_OUTPUT_HEADER` | dimension(`variables/evar26`)에서 뒤 토큰만 `variable` 컬럼 추가 (v1.6) | 어떤 변수(evar/prop)에서 온 dim 값인지 표기할 때 |
| `ADD_CATEGORY_COLUMN` | product 키워드 행에 category 컬럼 추가 (v1.4) | 제품코드 카테고리 분류가 필요할 때 |
| `CATEGORY_YAML` | 분류 룰 yaml 경로 (기본 같은 폴더 `product_category.yaml`) | yaml 위치/룰 바꿀 때 |
| `CATEGORY_UNKNOWN_LABEL` | 미분류 제품 라벨 (기본 `Unknown`) | 미분류 표기 바꿀 때 |
| `CATEGORY_KEYWORD_RULES` | 키워드→모드(multi/single) 매핑 (우선순위 순) | 대상 테이블 키워드 추가/변경할 때 |
| `CATEGORY_MULTI_SPLIT` · `CATEGORY_JOIN` | 멀티 제품 split / 결과 조인 구분자 (기본 `,`) | 구분자가 다를 때 |

---

## 출력 컬럼

```
TIER, SUBS, COUNTRY, SITE CODE, ITEM, VALUE, [value_origin],
rsid, start_date, end_date, value_n, metric_origin, metric, [variable], <디멘션>,
[category, category_non_acc_unknown_excl,] segments
[, device, period, bd1_dimension, bd1_itemId, bd1_value, …], Panel name [, reportlet]
```
- `<디멘션>` = 자동 감지된 디멘션 이름(`campaign`, `evar26` …)
- `[ ]` 표기 컬럼은 **조건부**로만 들어감 — `value_origin`(환율 적용 시), `variable`(extract 에 `dimension` 컬럼 있을 때, v1.6), `category` / `category_non_acc_unknown_excl`(`ADD_CATEGORY_COLUMN`+yaml 있을 때), `device` / `period`(v1.7) / `bd{k}_*`(입력에 있을 때). 각 컬럼의 동작·이유는 위 해당 기능 섹션 참고.
- `EXCLUDE_OUTPUT_COLUMNS` 로 위 목록 중 원하는 컬럼을 출력에서 제외 가능.

## 실행

```bash
python RESHAPE_standard_v1.7.py
```
- 같은 폴더에 `site_registry.py` 필요 (site_code → 국가/rsid)
- `ADD_CATEGORY_COLUMN=True` 면 같은 폴더에 `product_category.yaml` 필요 (없고 키워드 매칭 행 있으면 경고 후 분류 skip)
- 입력: 같은 폴더 `output/stack_data_extract_*.csv` (+구버전 `extract_data_*.csv`)
- 결과: `output/_union_standard_{날짜시간}.csv` (long) + `_union_standard_wide_{날짜시간}.csv` (wide)
- revenue 행이 있으면 `currency.csv` (1열 site_code + 헤더에 `YYYY-MM-DD` 컬럼들) 도 같은 폴더에 필요

> ⚠️ **`product_category.yaml` 과 `currency.csv` 는 repo 에 포함돼 있지 않다** (운영 데이터라 제외).
> 같은 폴더의 **`product_category_example.yaml` / `currency_example.csv`** 로 형식을 확인한 뒤,
> `_example` 을 뗀 이름(`product_category.yaml` / `currency.csv`)으로 본인 데이터를 채워 저장할 것.
> 코드가 찾는 파일명은 `_example` 없는 쪽이다.

## 의존성

표준 라이브러리(csv, re, pathlib 등) + `pyyaml`(category 분류용) + 같은 폴더 `site_registry.py`.
