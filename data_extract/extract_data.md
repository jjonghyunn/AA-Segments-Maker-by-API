# data_extract/extract_data.md  
<sub>2026-07-31  Jonghyun Park w/ Claude</sub>  

Adobe Workspace project 의 모든 panel × reportlet 에서 세그먼트/메트릭 이름 + 실제 데이터 값을 동시다발적으로 추출.

## 파일 목록

| 파일 | 용도 |
|---|---|
| `extract_data_v4.3.py` | 메인 추출 스크립트. `sites_input.csv` 의 row 별로 RSID + dateRange override + EXTRA_SEGMENTS globalFilter 추가 + **SKIP_PANEL_SEGMENTS 옵션** (panel segmentGroups 무시) + **EXTRA_SEGMENTS `enabled` 토글** (항목별 끄기) + **`OUTPUT_PREFIX`** (출력 파일명 prefix) + **`REQUIRED_TABLE_KEYWORDS`** (reportlet/테이블 단위 필터) + **name_keywords 패널-우선 해석** + **`SKIP_PANEL_SEGMENT_KEYWORDS`** (특정 패널 세그만 제거) + **EXTRA↔SKIP 충돌검사** + **N단계 dimension breakdown** + **device 컬럼 자동 추출** + **레벨별 limit cap** (`LIMIT_LV1`/`LIMIT_BD`) + **stack/table 출력 2종** + **device 케이스별 반복 추출** (`DEVICE_CASES`, 기본 비활성) + **(v3.9) stack metric → metric_origin** + **(v4.0) breakdown 단계별 cap `LIMIT_BD`~`LIMIT_BD4` + 출력 무결성 자가검증 + breakdown 진행률·남은예상시간 + `--estimate` 사전추정** + **(v4.1) breakdown 깊이/부모행 출력 제어 `BREAKDOWN_MAX_DEPTH`(깊이 캡)·`INCLUDE_PARENT_ROWS`(총계행 포함 여부) — 총계만/총계+bd1/bd1만 조합** + **(v4.2) 기간 분할·연도 shift `MONTHLY`(총기간을 달력 월로 쪼개 월별 추출, period 컬럼)·`YEAR_OFFSETS`(sites_input 연도 ±N shift, 동기간 YoY 를 한 실행으로)** |
| `RESHAPE_standard_v1.7.py` | extract_data 출력 → `_union_standard_*.csv` union 정제 (범용). **v1.7: `period` 컬럼(v4.2 MONTHLY 의 월 라벨) passthrough** (`PASSTHROUGH_COLUMNS`) — YEAR_OFFSETS 의 `_y{연도}` 파일은 v1.6 에서도 이미 연도별로 union 됨. v1.6: wide 의 revenue 계열을 `<metric>_org`(원본)+`<metric>`(fx) 두 열로 분리 + `variable` 컬럼(dimension 뒤 토큰, 예 `variables/evar26`→`evar26`) 추가. v1.5: metric_origin + 정제 metric + value_origin + wide union(`_union_standard_wide_*`). v1.4: panel/table/reportlet 의 product 키워드(`Multi Purchase`/`Multi Order`/`Best Selling Product`) 행에 `product_category.yaml` 로 `category` 컬럼 분류 추가 (`ADD_CATEGORY_COLUMN`). v1.3: `stack_data_extract_*` 입력 패턴 대응 (구버전 `extract_data_*` 호환). v1.2: metric / Panel name 출력 컬럼 추가 + `EXCLUDE_OUTPUT_COLUMNS` 컬럼 제외 옵션. v1.1: breakdown 행 모드(`BREAKDOWN_ROWS_MODE`) + device/bd 컬럼 passthrough + `_old` 접미사 SITE CODE 정규화 |
| `site_registry.py` | `site_code → (subsidiary, country, rsid)` 매핑. `lookup_site()` 함수 제공 |
| `table_data_extract_example.csv` / `stack_data_extract_example.csv` | 출력 2종(가로형 table / 세로형 stack) 형식 예시 (placeholder 값) |
| `app_O_X_example.csv` / `currency_example.csv` / `product_category_example.yaml` | **입력 참조 파일 형식 예시.** 실제 파일(`app_O_X.csv` / `currency.csv` / `product_category.yaml`)은 운영 데이터라 repo 미포함 — `_example` 을 뗀 이름으로 본인 데이터를 채워 같은 폴더에 저장할 것 |
| `_contents/` (하위폴더) | **캠페인 콘텐츠(콘텐츠 배너·시나리오) 분석 전용** 변형. contents 프로젝트의 site × device 5종(pc/mobile/app/android/ios) payload 분기 + `RESHAPE_contents` 후처리(환율·Delayed 합산·SITE CODE 정규화)가 묶인 도구 세트. generic `extract_data_v4.3.py` 와 별개 — 콘텐츠 캠페인 추출은 여기 사용. 추출: `_contents/extract_data_v3.2_contents.py`, 정제 상세: `_contents/RESHAPE_contents_v1.1.md` |

## v4.3 신규 기능 (2026-07-29)

site ↔ 패널 매칭 2종. **둘 다 기본 미사용** — 상수가 전부 비어 있어, 설정하기 전에는 v4.2 와 100% 동일하게 모든 패널을 추출한다. 입력 상수(`PROJECT_ID`·`LIMIT`·`MONTHLY`·`YEAR_OFFSETS` 등)는 그대로고 신규 상수만 추가됐다.

**① `SITE_PANEL_SITES` — 패널명의 site_code 토큰으로 전용/공용 판정** (기존 US·Global 접두 룰 대체)

- 패널명에 **등록된 site_code 가 토큰으로** 들어 있으면 그 site 전용 패널 → 그 site 에서만 추출
- 어느 등록 site_code 도 없는 패널 = 공용 → **미등록 site 에서만** 추출 (등록 site 는 전용 패널만 봄. `--include-global-for-us` 면 공용도 함께)
- 매칭은 **토큰 경계** 기준·대소문자 무시 — 영숫자/언더바가 아닌 문자(또는 문자열 끝)로 둘러싸여야 한다. `in` → `IN B2B` ✓ / `[IN]` ✓ / `inside`·`main`·`point` ✗. 경계 정규식은 `SITE_PANEL_BOUNDARY` 로 조정
- 패널명이 site_code 와 다르게 적혀 있으면 `SITE_PANEL_ALIAS` 로 키워드 지정 (예: `{"us_old": ["US_old", "US old"]}`)
- **비어 있으면(`[]`) 기존 접두 룰로 폴백**

**② `PANEL_GROUP_*` — 패널 분류(group) × site 매칭**

패널을 이름 키워드로 몇 개 분류로 나누고, `sites_input.csv` 의 분류 컬럼으로 site 마다 어느 분류의 패널에서 뽑을지 정한다. 분류가 안 맞는 패널은 그 site 에서 skip.

```python
# 한 캠페인을 B2B / B2C 로 나눈 사례 — 분류값은 자유 (("Mobile","MO") 처럼 디바이스로 나눠도 됨)
PANEL_GROUP_COLUMN        = "B2B_B2C"
PANEL_GROUP_RULES         = [("B2B", "B2B"), ("B2C", "B2C")]
PANEL_GROUP_SITE_DEFAULT  = "B2B"
PANEL_GROUP_PANEL_DEFAULT = "B2B"
# → sites_input 의 us 행이 B2C 면 'Global B2C' 패널만 돌고 'Global B2B' 는 skip
```

- ⚠ `PANEL_GROUP_RULES` 는 **위에서부터 첫 매칭**이라 순서가 중요하다. 한 패널명이 여러 키워드를 가지면 더 구체적인 쪽을 위에. (`② Global - B2B (B2C RS)` 는 B2B·B2C 를 다 가지므로 B2B 를 먼저 둬야 B2B 로 잡힌다)
- **안전장치 3중** — 다른 폴더에 그대로 복사해도 조용히 데이터가 빠지지 않는다. 아래 어느 경우든 분류 필터를 적용하지 않고 그 site 의 모든 패널을 추출한다:
  - `PANEL_GROUP_COLUMN` 이 비어 있을 때 (기본값 = 기능 미사용)
  - 컬럼명을 넣었는데 `sites_input` 헤더에 그 컬럼이 없을 때
  - 컬럼이 있어도 그 프로젝트에 해당 분류의 패널이 하나도 없을 때 (그 site 만, 경고 출력)

## v4.2 신규 기능 (2026-07-24)

> 전제: `sites_input.csv` 에는 site 별 **총기간**(start~end)만 넣는다. 아래 두 상수가 "그 총기간을 어떻게 뽑을지"를 정한다. 기본값(`False` / `[0]`) 이면 v4.1 과 100% 동일 출력.

1. **`MONTHLY` — 총기간을 달력 월로 쪼개 월별 추출** (bd 안 쓰고 monthly)
   - 월마다 `dateRange` override 로 각각 호출. 양 끝 부분월은 총기간에 맞춰 잘림 (`2025-07-06~07-21` → `Jul 2025` 한 조각).
   - 출력에 **`period` 컬럼** 추가 (`Jul 2025` — AA `daterangemonth` 표기와 동일) + `start_date`/`end_date` 가 그 달 범위로 기록. `MONTHLY=False` 면 `period` 컬럼 자체가 생기지 않음.
   - **AA 프로젝트에 daterangemonth breakdown 을 미리 만들 필요가 없다** — 어떤 프로젝트든 월별로 뽑힌다. bd 슬롯을 안 쓰므로 **기존 breakdown(예: 채널 detail)과 병행** 가능.
   - task 수 = 패널 × 테이블 × device케이스 × 월수.
   - 검증(2026-07-24, 월별 트래픽 프로젝트): 단일월 site 는 기존 bd1(daterangemonth) 추출과 **값 완전일치**. 다월 site 는 월 경계 visit 귀속·UV dedup 차이로 셀 3.5% 가 ±1~2 (합계 기준 -0.002% 이하) — AA 집계 특성이며 방향성 없는 미세 편차. bd 방식에만 있던 셀은 전부 0값(그 달 데이터 없는 store).
2. **`YEAR_OFFSETS` — sites_input 날짜의 연도를 ±N shift** (동기간 YoY)
   - `[0, -1]` → 올해 + 작년 동기간을 **한 실행으로** (site 당 2 run). `[-2,-1,0]` 처럼 개수·부호 자유. 2/29 는 shift 후 없는 날이면 2/28 로 clamp.
   - offset≠0 인 run 의 출력 파일명에 **`_y{연도}` 태그** (`stack_data_extract_us_y2025_260724_1130.csv`) — 같은 output 폴더에서 연도가 안 섞이고, RESHAPE 의 "site 별 최신 1개" 선택도 연도별로 분리된다. offset 0 은 태그 없음(v4.1 과 동일 파일명).
   - → **연도별 폴더 사본(y25/y26)을 만들 필요가 없다.** 환율은 RESHAPE 가 각 행 `end_date` 연도로 고르므로 폴더 1개로 두 연도를 같이 정제 가능.
   - 검증(2026-07-24, 동기간 비교 프로젝트): offset 0 / -1 결과가 기존 연도별 폴더 수기 추출본과 일치.

## v3.8 신규 기능 (2026-06-12)

1. **device 케이스별 반복 추출 (`DEVICE_CASES`)** — 대상 프로젝트 패널에 device 세그가 전혀 없을 때, 패널마다 device 세그 stack 을 globalFilter 로 끼워 케이스별로 각각 추출.
   - 케이스 1개 = `{"device": 라벨, "segment_ids": [세그ID...], ("requires_app": True)}` — 리스트 추가/삭제로 케이스 수 자유 증감. **기본은 전부 주석(비활성)** = v3.7 과 100% 동일 동작. 코드 상단 주석의 5케이스(PC/Mobile/App/Android/iOS) 예시를 주석 해제해서 사용.
   - task 수 = 패널 × 테이블 × 케이스 수 — API 호출 그만큼 증가 (429 빈발 시 `--site-workers` 축소).
   - 출력: stack CSV `device` 컬럼 = 케이스 라벨 + `segments` 에 케이스 세그 name append, **table CSV 에 `device` 컬럼 신설**.
2. **`app_O_X.csv` 룰** — site 별 App 론치 O/X. **X site 는 `requires_app` 케이스 제외** (PC/Mobile 류만). lookup: ① site_code 그대로 → ② `_old` 접미사 제거 → ③ 미매칭이면 경고 후 X 간주. 파일 없으면 전 site O 간주.
3. **`DEVICE_CASE_SITE_OVERRIDES`** — site 별 세그 치환 (`{site: {원본id: 대체id}}`). 구/별도 suite 라서 [Global] 세그가 0행을 만드는 site 용. 검증: `us_old`(구 US suite)는 `[Global] Excluded APP`/`[Global] App Only` 가 0행 (PC User/Mobile User 는 정상) → Excluded APP 을 `[US] Excluded APP` 으로 치환하면 정상 추출.

## v3.7 신규 기능 (2026-06-12)

1. **레벨별 limit 분리 + 실제 행수 cap** — `LIMIT` 1개 → `LIMIT_LV1`(dim1/1st level, reportlet 당) / `LIMIT_BD`(breakdown/2nd level~, 부모 item 1개당) 분리. CLI `--limit` / `--limit-bd`, `0`=무제한.
   - v3.6 까지 limit 은 API page 크기로만 쓰여 페이지네이션(MAX_PAGES)이 계속 돌아 **행수 제한이 실제로 안 걸렸음** → `_fetch_all_pages(max_rows=N)` cap (도달 시 중단 + truncate) 으로 수정.
2. **출력 CSV 2종 개편** — `stack_data_extract_*`(기존 `extract_data_*`, long unpivot 유지·RESHAPE 입력용) + `table_data_extract_*`(기존 `column_mapping_*` 대체, **AA 테이블 모양 가로형**: 1행=item, `value1..N` + `seg_value1..N`).
   - `seg_value{i}` = `"metric;; segments"` — metric 맨앞, `;;` 구분 (segments 내부 구분자가 `; ` 라 세미콜론 2개. `SEG_VALUE_SEP` 상수).
   - 테이블 블록 순서: `(summary)` 총계 행 → dim1 item 행들 → breakdown 행들 (`bd{k}_*` 컬럼).

## 진행률 + ETA 콘솔 출력 (2026-06-15)

`VERBOSE_PROGRESS = True`(기본) 면 site 1개 추출이 끝날 때마다 한 줄로 진행상황 + 남은시간 + 전체 예상시간을 출력. 추출 로직은 그대로 (출력만 추가).

```
  [ 3/20] site=fr         ✓   12.0s  rows    6,540  | 누적 36s | 평균 12.0s/site | 남은 ~3m 24s | 전체 ~4m 0s (17 left)
```

- `[i/N]` 완료 site / 전체 site, `✓ 소요s` 그 site 소요시간, `rows` 그 site 추출 행수(dim1 + breakdown)
- `누적` 시작부터 벽시계 경과(지난시간), `평균` 완료 site 평균 소요
- **남은 = 평균 site당 소요 × 남은 site 수** (running average — 진행될수록 정확해짐). `SITE_WORKERS>1`(병렬) 이면 `÷ 워커수` 한 근사치라 `~` 표기
- **전체 = 누적 + 남은** (이 작업 통째로 걸리는 총 예상시간)
- 시간 표기는 `몇H 몇M 몇S` 형식 (`36s` / `3m 24s` / `1h 5m 30s`)
- `VERBOSE_PROGRESS = False` 면 끔 (기존 마지막 site별 summary 출력은 항상 유지)
- 주의: site 마다 크기(breakdown 항목 수)가 달라 남은 site 가 유독 크/작으면 예상이 빗나갈 수 있음. 병렬 모드는 site별 내부 로그가 섞이지만 이 진행률 한 줄은 main 스레드에서만 찍혀 깔끔.

## v3.6 신규 기능 (2026-06-10)

1. **site 단위 병렬 처리** — _contents 시리즈의 `SITE_WORKERS` 포팅. `SITE_WORKERS>1` 이면 그 수만큼 site 동시 추출 (`--site-workers N` CLI override, `1`=순차=v3.5 동작).
   - 동시 API 요청 ≈ SITE_WORKERS × workers — 콘솔에 429 자주 보이면 5 → 3 → 2 로 줄이기.
   - site 병렬 시 콘솔 로그는 site 간 섞여 출력 (결과 CSV 는 site 별 파일이라 영향 없음).

## v3.5 신규 기능 (2026-06-10)

1. **N단계 dimension breakdown** — dim1(행 = `dimensionSettings[0]`)의 각 item 을 하위 차원으로 재귀 분해 (Workspace 의 "행 break down" 과 동일).
   - 차원 체인은 테이블 `freeformTable.breakdowns[].breakdowns[]` 에서 **자동감지** (또는 `BREAKDOWN_DIMENSIONS` 로 명시 override).
   - 하위 레벨 호출 = `/reports` with `dimension=<하위차원>` + 조상마다 `{"type":"breakdown","dimension":<조상차원>,"itemId":<조상item>}` metricFilter 를 모든 metric 에 AND.
   - CSV 에 도달 깊이만큼 `bd{k}_dimension/itemId/value` 컬럼 셋(레벨당 3개) 추가. dim1 총계 행은 `bd*` 공백 → v3.4 출력 상위호환.
   - `BREAKDOWN_TOP_N>0` 이면 레벨별 상위 N item 만 분해 (전체 분해는 레벨 깊을수록 호출 곱연산 증가 주의).
   - `BREAKDOWN_ENABLED=False` 면 v3.4 와 100% 동일 동작.
2. **device 컬럼** — 각 컬럼(value_n) stack 세그명에서 device 를 추출해 `device` 컬럼으로 (`Mobile`/`PC`/`Android`/`iOS`/`App`).
   - `[Device] X` 형식 우선, 맨 `Mobile`/`Mobile (Visit)`/`Desktop` 등 토큰도 매칭 (`DEVICE_SEGMENT_RULES` 정규식 순서 리스트).
   - `App Only` 샌드위치: stack 에 Android/iOS 토큰 있으면 그 플랫폼, 없으면(`All Visit` 쪽) `App`.

## v3.4 신규 기능

1. **name_keywords 패널-우선 해석** — `EXTRA_SEGMENTS` 의 `name_keywords` 검색을 회사 전체보다 **프로젝트 패널 segmentGroups 안에서 먼저** 매칭. 패널 내 1건이면 자동 적용, 2건+면 중단(segment_id/세밀 키워드 안내), 0건이면 회사 전체 검색 fallback.
2. **`SKIP_PANEL_SEGMENT_KEYWORDS`** — 패널 세그 중 이름에 키워드를 모두(AND) 포함하는 세그만 globalFilter 에서 제거(B2B 등 나머지는 유지). `[]` 면 미적용.
   - cf. `SKIP_PANEL_SEGMENTS=True`(패널 세그 전부 제거), `enabled:False`(EXTRA 추가만 차단 — 패널 세그는 못 뺌).
3. **EXTRA ↔ SKIP 충돌 검사** — 같은 세그를 EXTRA(추가)+SKIP(제거) 동시 지정 시 경고 후 중단.

> `SKIP_PANEL_SEGMENTS` / `SKIP_PANEL_SEGMENT_KEYWORDS` 는 상단 사용자 설정부(EXTRA_SEGMENTS 뒤)에 위치.

## 기존 도구와의 관계

| 도구 | 역할 | 활용 |
|---|---|---|
| `aa_segment_lookup.py` (같은 폴더 사본) | 세그먼트 검색/lookup + DSL decompile | extract_data 가 **모듈 import 하는 필수 의존** (`_search_segments`/`decompile_definition`/`format_dsl_block`/`_set_daterange_auth`). 코드가 자기 폴더만 sys.path 에 넣으므로 같은 폴더에 사본 필요 — 원본은 `../segment_maker/aa_segment_lookup.py`, 원본 갱신 시 사본도 동기화 |

→ "프로젝트 URL 만 넣으면 구조 파악 + 데이터 추출 + 추가 segment 적용까지 한번에" 되는 단일 스크립트.

## extract_data_v4.3.py 사용법

```bash
python extract_data_v4.3.py                       # sites_input.csv 의 모든 site 처리
python extract_data_v4.3.py --site us             # us 하나만
python extract_data_v4.3.py --site us --site uk   # 여러 개 좁히기
python extract_data_v4.3.py --dry-run             # payload 생성까지만 (API 호출 안 함)
python extract_data_v4.3.py --workers 8           # 병렬 워커 수 (기본 6)
python extract_data_v4.3.py --limit 200          # 1st level(dim1) reportlet 당 행 수 cap (0=무제한)
python extract_data_v4.3.py --limit-bd 50        # breakdown 1단계(bd1=level2) 부모 item 당 행 수 cap (0=무제한)
python extract_data_v4.3.py --limit-bd2 15 --limit-bd3 15 --limit-bd4 15   # breakdown 2/3/4단계(bd2~4 = level3~5) 각 cap
python extract_data_v4.3.py --estimate           # 실제 추출 전 총 호출수·예상 소요시간만 출력 (dim1+단계별 1경로 샘플)
python extract_data_v4.3.py --include-global-for-us            # us_old site 에서도 Global panel 추출
python extract_data_v4.3.py --site-workers 3                   # site 3곳 동시 처리 (기본 5, 1=순차)
python extract_data_v4.3.py --breakdown-top-n 5                # breakdown 레벨별 상위 5개만 (검증/성능)
python extract_data_v4.3.py --breakdown-dims "variables/product,variables/evar92"  # 분해 차원 명시
python extract_data_v4.3.py --breakdown-max-depth 1           # (v4.1) breakdown 깊이 캡 (0=총계만, 1=bd1까지, N=bdN까지, -1=무제한)
python extract_data_v4.3.py --breakdown-max-depth 1 --no-parent-rows   # (v4.1) dim1 총계행 빼고 bd1 행만 ("bd만" 모드)
python extract_data_v4.3.py --monthly                          # (v4.2) 총기간을 달력 월로 쪼개 월별 추출 (period 컬럼 추가)
python extract_data_v4.3.py --year-offsets 0,-1                # (v4.2) 올해 + 작년 동기간 한 실행으로 (작년은 파일명 _y{연도})
python extract_data_v4.3.py --monthly --year-offsets 0,-1      # (v4.2) 두 옵션 조합 — 2개 연도 × 월별
```

## 사용자 설정 (상단 상수)

| 상수 | 설명 | 변경 시점 |
|---|---|---|
| `AUTH_JSON_PATH` | Adobe OAuth 인증 JSON 경로 | 환경 변경 시 |
| `COMPANY_ID` | Adobe Analytics company ID | 보통 고정 |
| `PROJECT_ID` | Workspace URL 의 `/workspace/edit/{이부분}` | **프로젝트마다 변경** |
| `MAX_WORKERS` | reportlet 병렬 워커 수 (5~8 추천) | 성능/안정성 조절 |
| `SITE_WORKERS` | site 단위 병렬 워커 수 (1=순차) | 사이트 수 많을 때. 429 뜨면 줄이기 |
| `LIMIT_LV1` | dim1(1st level) reportlet 당 행 수 cap (`0`=무제한) | 데이터 양 조절 |
| `LIMIT_BD`~`LIMIT_BD4` | breakdown 단계별 행 수 cap — bd1(=level2)~bd4(=level5+). bd5+ 는 BD4 (`0`=무제한) | 데이터 양 조절 |
| `SEG_VALUE_SEP` | table CSV `seg_value{i}` 의 metric↔segments 구분자 (기본 `';; '`) | 보통 고정 |
| `REQUIRED_PANEL_KEYWORDS` | 빈 리스트면 모든 패널 통과 | 특정 패널만 추출 시 |
| `REQUIRED_TABLE_KEYWORDS` | 빈 리스트면 모든 테이블 통과 | 특정 reportlet/테이블만 추출 시 |
| `BREAKDOWN_ENABLED` | N단계 breakdown on/off (`False` = v3.4 동작) | breakdown 필요 여부 |
| `BREAKDOWN_DIMENSIONS` | 분해 차원 체인 명시 (`[]` = 테이블에서 자동감지) | 자동감지 안 될 때 |
| `BREAKDOWN_TOP_N` | 레벨별 상위 N item 만 분해 (`0` = 전체) | 검증/성능 조절 |
| `BREAKDOWN_MAX_DEPTH` | breakdown 깊이 캡 (`-1`=무제한, `0`=총계만, `N`=bdN까지) | 필요한 깊이만 뽑을 때 |
| `INCLUDE_PARENT_ROWS` | dim1 총계(부모) 행 출력 포함 (`False`=breakdown 행만) | "bd만" 모드 |
| `MONTHLY` (v4.2) | 총기간을 달력 월로 쪼개 월별 추출 (`False`=총기간 1회). `True` 면 `period` 컬럼 추가 | 월별 추이가 필요할 때 |
| `YEAR_OFFSETS` (v4.2) | sites_input 날짜 연도 shift 리스트 (`[0]`=그대로, `[0,-1]`=올해+작년). offset≠0 은 파일명 `_y{연도}` 태그 | 동기간 YoY 를 한 폴더에서 |
| `SITE_PANEL_SITES` (v4.3) | 패널명에 site_code 가 토큰으로 든 패널을 그 site 전용으로 판정할 site 목록 (`[]`=기존 US·Global 접두 룰) | 패널이 site 별로 나뉜 프로젝트 |
| `SITE_PANEL_ALIAS` (v4.3) | 패널명이 site_code 와 다를 때 쓸 키워드 (`{"us_old": ["US_old","US old"]}`) | 패널명 표기가 다를 때 |
| `SITE_PANEL_BOUNDARY` (v4.3) | 토큰 경계 정규식 (`{kw}` 자리에 키워드 escape 되어 삽입) | 보통 고정 |
| `PANEL_GROUP_COLUMN` (v4.3) | sites_input 의 분류 컬럼명 (`""`=분류 기능 미사용) | 패널을 B2B/B2C 식으로 나눌 때 |
| `PANEL_GROUP_RULES` (v4.3) | `[(패널명 키워드, 분류값), ...]` — **위에서부터 첫 매칭** | 〃 |
| `PANEL_GROUP_SITE_DEFAULT` (v4.3) | 컬럼은 있는데 셀이 빈 site 의 분류 | 〃 |
| `PANEL_GROUP_PANEL_DEFAULT` (v4.3) | 어느 키워드에도 안 걸리는 패널의 분류 | 〃 |
| `PANEL_GROUP_OFF` (v4.3) | 이 값이면 분류 필터를 적용하지 않는 sentinel | 특정 site 만 분류 해제 |
| `PANEL_GROUP_YEAR_OFFSETS` (v4.3) | 분류별 `YEAR_OFFSETS` override (`{}`=전역값). 예 `{"B2C": [0]}` | 특정 분류만 연도 확장 제외 |
| `PANEL_GROUP_SKIP_SITE_PANEL_RULE` (v4.3) | 이 분류는 site↔패널 룰(위 ①·접두 룰) 면제 (`[]`=미사용) | 그 분류에 패널이 하나뿐일 때 |
| `DEVICE_FROM_SEGMENT` | 세그명 → `device` 컬럼 추출 on/off | device 분기 테이블 |
| `DEVICE_SEGMENT_RULES` | device 매칭 (정규식, 라벨) 순서 리스트 | 새 device 표기 등장 시 |
| `EXTRA_SEGMENTS` | 추가 segment globalFilter 리스트 (빈 리스트 = v2 동일 동작) | 옵트인 |
| `EXTRA_SEGMENTS[*].enabled` | 항목별 globalFilter 추가 on/off (키 없으면 True) | 적용했던 세그 끄고 재추출 시 |
| `OUTPUT_PREFIX` | 출력 CSV 파일명 앞 prefix (`""` = 기존) | 세그 제외본/전체본 구분 저장 시 |
| `VERBOSE_PROGRESS` | site별 진행률 + ETA 콘솔 한 줄 출력 on/off (`True` = 기본) | 진행상황·남은시간 보고 싶을 때 |

## sites_input.csv 형식

```csv
site_code,start_date,end_date
ae,2026-05-11,2026-05-17
au,2026-05-14,2026-05-17
br,2026-05-11,2026-05-17
de,2026-05-12,2026-05-17
...
```

- `site_code` — `site_registry._SITE_MASTER` 의 key. 매핑에 없으면 fallback `sscompany_name4{site_code 의 _ 제거}` 사용
- `start_date` / `end_date` — site 별 **총기간**, ISO `YYYY-MM-DD`. v3 가 `YYYY-MM-DDT00:00:00.000/다음날T00:00:00.000` 형식 (AA 컨벤션) 으로 자동 변환
- **(v4.2) 여기엔 총기간만 넣는다** — 월별로 쪼갤지(`MONTHLY`), 다른 연도 동기간도 뽑을지(`YEAR_OFFSETS`)는 상단 상수가 결정. 연도별로 sites_input 을 복사·수정하거나 폴더를 통째로 복제할 필요 없음
- **(v4.3) 패널 분류 컬럼은 선택** — `PANEL_GROUP_COLUMN` 에 이름을 박으면 그 이름의 4번째 컬럼을 site 별 분류값으로 읽는다. 컬럼이 없어도(또는 상수가 `""` 여도) 그대로 동작하며 분류 필터만 적용되지 않는다. 예:
  ```csv
  site_code,start_date,end_date,B2B_B2C
  us,2026-05-19,2026-06-09,B2C
  uk,2026-05-11,2026-06-09,B2B
  ```
- 빈 줄 / `#` 시작 라인 자동 skip
- **예시 파일 동봉** — 같은 폴더 `sites_input.csv` 가 바로 실행 가능한 템플릿 (US 2행 분할 / 언어변형 site 규칙 주석 포함). 실제 캠페인 site·기간으로 행만 교체

## site_registry 매핑 lookup 흐름

```
row 의 site_code (예: "au")
    ↓
lookup_site("au")  ──  _SITE_MASTER 에서 찾기
    ↓
SiteInfo(subsidiary="FRNH", country="Australia", site_code="au", rsid="sscompany_name4au")
    ↓
payload 의 "rsid" → "sscompany_name4au"
payload 의 globalFilters[dateRange] → "2026-05-14T00:00:00.000/2026-05-18T00:00:00.000"
```

- 정식 매핑 외에 `_` 제거 alias 도 시도 (예: `ca_fr` → 매핑 없으면 `cafr` 시도)
- 끝까지 매칭 안 되면 fallback: `sscompany_name4{normalized}`

새 사이트 추가하려면 `site_registry.py` 의 `_SITE_MASTER` dict 에 `"<code>": ("<subsidiary>", "<country>", "<rsid>")` 한 줄 추가.

## EXTRA_SEGMENTS 옵션 — 추가 segment 를 globalFilter 에 끼워넣기

`EXTRA_SEGMENTS = []` 이면 옵트아웃 (panel 의 기존 segmentGroups + dateRange 그대로).

```python
EXTRA_SEGMENTS: list[dict] = [
    # 1) ID 직접 지정 — 가장 빠름
    {"segment_id": "세그먼트_아이디_넘버", "panel_scope": "all"},

    # 2) 풀네임 substring 검색 — Adobe `name` 필터 = case-insensitive contains
    {"name_keywords": "visitor id = d=mid, null (Exclude)"},

    # 3) AND 키워드 리스트
    {
        "name_keywords": ["visitor id", "d=mid", "null", "Exclude"],
        "panel_scope": "all",
    },

    # 4) panel 일부 적용
    {
        "name_keywords": ["[Global] Excluded B2B"],
        "panel_scope": ["[Global]"],
    },
]
```

### panel_scope
- `"all"` (생략 시 기본) → 모든 panel 에 적용
- `["키워드", ...]` → panel.name 에 키워드 포함 시만 (OR, case-insensitive)

### enabled (v3.2 — 항목별 토글)
- `{"segment_id": ..., "enabled": False}` → 그 EXTRA 세그를 globalFilter 에 **안 넣음** (줄 안 지우고 끄기). 키 없으면 `True`.
- include/exclude 스위치가 아니라 "추가할지 말지" 스위치 — Exclude 세그를 끄면 그 세그 없는 전체(full population) 로 재추출.

### REQUIRED_PANEL_KEYWORDS 와의 차이
| 상수 | 역할 |
|---|---|
| `REQUIRED_PANEL_KEYWORDS` | 그 panel **자체를 처리할지 말지** (필터) |
| `REQUIRED_TABLE_KEYWORDS` | panel 통과 후 그 안 **reportlet(테이블) 을 처리할지 말지** (필터) |
| `EXTRA_SEGMENTS[*].panel_scope` | 처리되는 panel 중 **추가 segment 를 적용할지 말지** |

### 매칭 정책 (name_keywords 검색 시)

| 매칭 수 | 동작 |
|---|---|
| 1개 | 진행 (lookup CSV/DSL 같이 저장) |
| 2~5개 | 콘솔에 ID+이름 나열 + 중단 (lookup CSV/DSL 저장). 키워드 좁히고 재실행 |
| 6개 이상 | "lookup CSV 확인" + 중단 |
| 0개 | 에러 + 중단 |

`segment_id` 직접 지정 케이스는 검색 단계 자체를 건너뛰므로 lookup 파일 생성 안 함.

### lookup 출력

```
data_extract/
└── lookup/
    ├── segment_lookup_<query_slug>_<YYMMDD_HHMM>.csv
    └── segment_lookup_<query_slug>_<YYMMDD_HHMM>.dsl
```

`<query_slug>` = name_keywords 의 alphanumeric 만 남기고 `__` 로 join.

## 동작 흐름

```
0. EXTRA_SEGMENTS 가 있으면 _resolve_extra_segment() 로 ID 확정 + lookup 파일 저장
   (v4.2) sites_input row × YEAR_OFFSETS → run 목록 확장 (연도 shift + 파일명 태그)
1. 인증 (aanalytics2 OAuth S2S)
2. GET /projects/{PROJECT_ID} → 패널/리포틀렛 구조 전체 walk
       - 패널별 컨텍스트 감지 (year_kind, region_kind)
       - 리포틀렛별 세그먼트/메트릭 이름 추출
       - columnTree 의 DateRange 컴포넌트 감지 + API 로 definition 조회
       - /reports API payload 자동 빌드 (site 별 RSID + dateRange override 적용)
         · (v4.2) MONTHLY 면 총기간을 달력 월로 쪼개 조각마다 payload 1개씩
3. ThreadPoolExecutor 로 동시다발적 /reports POST
       - dimension 있는 테이블 → rows 추출
       - dimension 없는 summary 테이블 → summaryData.totals 추출
       - 429/5xx 자동 retry (지수 백오프)
       - 422/400 실패 시 metric 개별 분리 추출 (fallback)
4. 결과 정리 (패널 → 테이블 → 칼럼 순서)
       - 사이트별 별도 CSV 저장
```

## ⚠ 한 파일에 여러 패널이 담길 수 있다 (2026-07-31)

`PANEL_GROUP_COLUMN` 을 비우고 **site 당 1행**으로 두면, 그 site 가 돌 수 있는 **모든 패널이 한 run
= 한 파일**에 담긴다. sites_input 에 같은 `site_code` 를 분류(group)별로 여러 행 넣던 구성의 대안이다.

- **왜 이 구성이 나은가** — 같은 site_code 가 여러 행이면 offset 0 출력 경로가 **완전히 같아져
  나중에 끝난 run 이 앞 run 파일을 덮어쓴다.** `SITE_WORKERS` 병렬이라 어느 쪽이 남는지도 run 마다
  달라지는 경합이고, 에러가 안 나 발견이 늦다 (실측: 한 분류의 데이터가 통째로 유실).
  `GROUP_TAG_IN_FILENAME` 이 그 경우 파일명에 `_{group}` 을 붙여 막지만, 애초에 1행으로 두면 안 겪는다.
- **분류 구분은 정제가 한다** — stack CSV 에 `panel` 컬럼이 있으므로 RESHAPE 단계에서 나누면 된다.
  `RESHAPE_standard` 는 `panel` 을 **`Panel name` 컬럼으로 그대로 출력**한다.
- **`SITE_EXTRA_PANELS`** — `SITE_PANEL_SITES` 등록 site 는 전용 패널만 보는 all-or-nothing 이라
  "전용 패널 + 공용 패널" 조합을 표현할 수 없었다. `{site_code: [패널명 키워드]}` 로 공용 패널을 추가 허용한다.
- ⚠ **여러 패널이 섞이면 dim1 컬럼명이 `dim_value` 로 폴백**된다(차원이 2종 이상이라). 단일 패널
  파일은 여전히 차원 id 뒤 토큰(`evar73` 등). 정제 쪽은 헤더에서 자동 판별하므로 그대로 동작한다.
- ⚠ **한 파일에 append 하는 방식은 못 쓴다** — 헤더(dim1 컬럼명, `bd{k}_*` 컬럼 수)가 run 단위로
  계산돼 어긋난다. 그래서 파일 분리(`GROUP_TAG_IN_FILENAME`) 또는 1행 구성 둘 중 하나여야 한다.

## 출력 파일

```
output/
├── stack_data_extract_au_260610_1030          ← au site long unpivot (RESHAPE 입력)
├── table_data_extract_au_260610_1030        ← au site AA 테이블 모양 가로형 (1행=item)
├── stack_data_extract_uk_260610_1030.csv
├── table_data_extract_uk_260610_1030.csv
└── ...
```

각 사이트의 extract CSV 첫 4 컬럼은 `site_code, rsid, start_date, end_date` — site 식별자 + 기간 정보가 모든 row 에 같이 박혀서 사후 통합 분석 시 join 키로 활용 가능.

**(v4.2) 기간 분할 / 연도 shift 시 출력**

```
output/
├── stack_data_extract_id_260724_1133.csv          ← YEAR_OFFSETS 의 offset 0 (태그 없음 = v4.1 과 동일)
├── stack_data_extract_id_y2025_260724_1133.csv    ← offset -1 (작년 동기간)
└── ...
```

- `MONTHLY=True` 면 `end_date` 뒤에 **`period` 컬럼**(`Jul 2025`)이 추가되고 `start_date`/`end_date` 는 **그 달의 범위**로 기록된다 (마지막 달은 총기간 end 까지). `MONTHLY=False` 면 컬럼 자체가 없음.
- 월 정보는 `bd{k}_*` 가 아니라 `period` 로만 들어가므로, 테이블에 원래 breakdown(예: 채널 detail)이 있어도 그대로 공존한다.

| 파일 | 내용 |
|---|---|
| `stack_data_extract_<site_code>_{ts}.csv` | (기존 `extract_data_*`) 패널/테이블/리포틀렛별 데이터 값 (long unpivot: 1 row = 디멘션값 × value_n). `device` 컬럼 + breakdown 시 `bd{k}_dimension/itemId/value` 컬럼 포함 |
| `table_data_extract_<site_code>_{ts}.csv` | (기존 `column_mapping_*` 대체) AA 테이블 모양 가로형 — 1행 = item, `value1..N` 값 + `seg_value1..N`(`"metric;; segments"`) + `bd{k}_*` 컬럼. 테이블 블록 = `(summary)` 행 → dim1 행 → breakdown 행 |

### breakdown 행 구분 (v3.5)

- dim1 총계 행: `bd{k}_*` 전부 공백
- breakdown 행: `itemId`/디멘션 컬럼 = **부모(dim1) item**, `bd1_*` = 1단계 하위 item, `bd2_*` = 2단계 …
- 같은 dim1 item 의 총계와 breakdown 이 둘 다 들어있으므로 **단순 합산 시 이중집계 주의** — `bd1_itemId` 빈칸 여부로 필터해서 사용 (RESHAPE_standard_v1.7 의 `BREAKDOWN_ROWS_MODE` 참고)

## Fallback (개별 metric 추출)

API 가 422/400 을 반환하면 (메트릭이 너무 많거나 dateRange 가 긴 경우 등), 자동으로 metric 을 1 개씩 분리해서 개별 요청 후 결과 합침. 콘솔에 `(fallback: individual metrics)` 표시.

## DateRange 처리

columnTree 에 DateRange 컴포넌트가 있으면:
1. `GET /dateranges/{id}?expansion=definition` 으로 실제 날짜 범위 조회 (시작 시 일괄 prefetch + 캐시)
2. metricFilter 에 `dateRange` + `dateRangeId` 둘 다 포함하여 정확한 값 추출
3. segments 칼럼에 dateRange 이름 표시 (예: `[DE CAMPAIGN NAME 직전 4주] (2026. 3. 12 ~ 2026. 4. 8)`)

> 주의: 2년 이상 긴 dateRange 는 API 가 `max network bytes exceeded` (422) 로 거부할 수 있음. Workspace UI 는 내부 캐시로 처리하지만 raw API 에는 제한 있음.

## 지원하는 테이블 유형

- **Summary 테이블** (dimension 없음): `summaryData.totals` 에서 값 추출
- **Dimension 테이블** (dimension 있음): `rows` 에서 행 단위 추출 + 페이지네이션
- **Cross-tab** (row × column): staticRows + columnTree 결합

## 권장 사용 흐름

1. `sites_input.csv` 의 site 들 + 캠페인 시즌의 start/end 채움
2. `python extract_data_v4.3.py --dry-run --site us` 로 한 site payload 확인
3. breakdown 쓰는 경우 `--site <한곳> --breakdown-top-n 5` 로 소규모 검증 (총계 = breakdown 합 확인)
4. OK 면 전체 실행 — `python extract_data_v4.3.py`
5. `output/` 폴더의 사이트별 CSV 검토. 실패 site (FAIL 표시) 만 따로 `--site <code>` 로 재시도
6. union 정제 필요 시 `python RESHAPE_standard_v1.7.py` (breakdown 행 처리 모드는 `BREAKDOWN_ROWS_MODE`)

## 의존성

```
pip install aanalytics2 requests
```

Mac venv 사용 예 (Python 3.13):

```bash
cd data_extract
DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib .venv/bin/python3.13 extract_data_v4.3.py
```
