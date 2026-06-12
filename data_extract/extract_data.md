# extract_data.py / v2 / v3 / v3.1 / v3.2 / v3.3 / v3.4 / v3.5 / extract_data_v3.7.py  
<sub>2026-06-12  Jonghyun Park w/ Claude</sub>  

Adobe Workspace project 의 모든 panel × reportlet 에서 세그먼트/메트릭 이름 + 실제 데이터 값을 동시다발적으로 추출.
## 파일 목록

| 파일 | 용도 |
|---|---|
| `extract_data_v3.7.py` | 메인 추출 스크립트. `sites_input.csv` 의 row 별로 RSID + dateRange override + EXTRA_SEGMENTS globalFilter 추가 + **SKIP_PANEL_SEGMENTS 옵션** (panel segmentGroups 무시) + **EXTRA_SEGMENTS `enabled` 토글** (항목별 끄기) + **`OUTPUT_PREFIX`** (출력 파일명 prefix) + **`REQUIRED_TABLE_KEYWORDS`** (reportlet/테이블 단위 필터) + **name_keywords 패널-우선 해석** + **`SKIP_PANEL_SEGMENT_KEYWORDS`** (특정 패널 세그만 제거) + **EXTRA↔SKIP 충돌검사** + **N단계 dimension breakdown** + **device 컬럼 자동 추출** + **레벨별 limit cap** (`LIMIT_LV1`/`LIMIT_BD`) + **stack/table 출력 2종** |
| `RESHAPE_standard_v1.3.py` | extract_data 출력 → `_union_standard_*.csv` union 정제 (범용). v1.3: `stack_data_extract_*` 입력 패턴 대응 (구버전 `extract_data_*` 호환). v1.2: metric / Panel name 출력 컬럼 추가 + `EXCLUDE_OUTPUT_COLUMNS` 컬럼 제외 옵션. v1.1: breakdown 행 모드(`BREAKDOWN_ROWS_MODE`) + device/bd 컬럼 passthrough + `_old` 접미사 SITE CODE 정규화 |
| `site_registry.py` | `site_code → (subsidiary, country, rsid)` 매핑. `lookup_site()` 함수 제공 |

## v3.7 신규 기능 (2026-06-12)

1. **레벨별 limit 분리 + 실제 행수 cap** — `LIMIT` 1개 → `LIMIT_LV1`(dim1/1st level, reportlet 당) / `LIMIT_BD`(breakdown/2nd level~, 부모 item 1개당) 분리. CLI `--limit` / `--limit-bd`, `0`=무제한.
   - v3.6 까지 limit 은 API page 크기로만 쓰여 페이지네이션(MAX_PAGES)이 계속 돌아 **행수 제한이 실제로 안 걸렸음** → `_fetch_all_pages(max_rows=N)` cap (도달 시 중단 + truncate) 으로 수정.
2. **출력 CSV 2종 개편** — `stack_data_extract_*`(기존 `extract_data_*`, long unpivot 유지·RESHAPE 입력용) + `table_data_extract_*`(기존 `column_mapping_*` 대체, **AA 테이블 모양 가로형**: 1행=item, `value1..N` + `seg_value1..N`).
   - `seg_value{i}` = `"metric;; segments"` — metric 맨앞, `;;` 구분 (segments 내부 구분자가 `; ` 라 세미콜론 2개. `SEG_VALUE_SEP` 상수).
   - 테이블 블록 순서: `(summary)` 총계 행 → dim1 item 행들 → breakdown 행들 (`bd{k}_*` 컬럼).

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
2. **`SKIP_PANEL_SEGMENT_KEYWORDS`** — 패널 세그 중 이름에 키워드를 모두(AND) 포함하는 세그만 globalFilter 에서 제거(EPP 등 나머지는 유지). `[]` 면 미적용.
   - cf. `SKIP_PANEL_SEGMENTS=True`(패널 세그 전부 제거), `enabled:False`(EXTRA 추가만 차단 — 패널 세그는 못 뺌).
3. **EXTRA ↔ SKIP 충돌 검사** — 같은 세그를 EXTRA(추가)+SKIP(제거) 동시 지정 시 경고 후 중단.

> `SKIP_PANEL_SEGMENTS` / `SKIP_PANEL_SEGMENT_KEYWORDS` 는 상단 사용자 설정부(EXTRA_SEGMENTS 뒤)에 위치.

## 기존 도구와의 관계

| 도구 | 역할 | 활용 |
|---|---|---|
| `aa_segment_lookup.py` (같은 폴더 사본) | 세그먼트 검색/lookup + DSL decompile | extract_data 가 **모듈 import 하는 필수 의존** (`_search_segments`/`decompile_definition`/`format_dsl_block`/`_set_daterange_auth`). 코드가 자기 폴더만 sys.path 에 넣으므로 같은 폴더에 사본 필요 — 원본은 `../segment_maker/aa_segment_lookup.py`, 원본 갱신 시 사본도 동기화 |

→ "프로젝트 URL 만 넣으면 구조 파악 + 데이터 추출 + 추가 segment 적용까지 한번에" 되는 단일 스크립트.

## extract_data_v3.7.py 사용법

```bash
python extract_data_v3.7.py                       # sites_input.csv 의 모든 site 처리
python extract_data_v3.7.py --site us             # us 하나만
python extract_data_v3.7.py --site us --site uk   # 여러 개 좁히기
python extract_data_v3.7.py --dry-run             # payload 생성까지만 (API 호출 안 함)
python extract_data_v3.7.py --workers 8           # 병렬 워커 수 (기본 6)
python extract_data_v3.7.py --limit 200          # 1st level(dim1) reportlet 당 행 수 cap (0=무제한)
python extract_data_v3.7.py --limit-bd 50        # breakdown 부모 item 당 레벨별 행 수 cap (0=무제한)
python extract_data_v3.7.py --include-global-for-us            # us site 에서도 [Global] panel 추출
python extract_data_v3.7.py --site-workers 3                   # site 3곳 동시 처리 (기본 5, 1=순차)
python extract_data_v3.7.py --breakdown-top-n 5                # breakdown 레벨별 상위 5개만 (검증/성능)
python extract_data_v3.7.py --breakdown-dims "variables/product,variables/evar92"  # 분해 차원 명시
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
| `LIMIT_BD` | breakdown 부모 item 당 레벨별 행 수 cap (`0`=무제한) | 데이터 양 조절 |
| `SEG_VALUE_SEP` | table CSV `seg_value{i}` 의 metric↔segments 구분자 (기본 `';; '`) | 보통 고정 |
| `REQUIRED_PANEL_KEYWORDS` | 빈 리스트면 모든 패널 통과 | 특정 패널만 추출 시 |
| `REQUIRED_TABLE_KEYWORDS` | 빈 리스트면 모든 테이블 통과 | 특정 reportlet/테이블만 추출 시 |
| `BREAKDOWN_ENABLED` | N단계 breakdown on/off (`False` = v3.4 동작) | breakdown 필요 여부 |
| `BREAKDOWN_DIMENSIONS` | 분해 차원 체인 명시 (`[]` = 테이블에서 자동감지) | 자동감지 안 될 때 |
| `BREAKDOWN_TOP_N` | 레벨별 상위 N item 만 분해 (`0` = 전체) | 검증/성능 조절 |
| `DEVICE_FROM_SEGMENT` | 세그명 → `device` 컬럼 추출 on/off | device 분기 테이블 |
| `DEVICE_SEGMENT_RULES` | device 매칭 (정규식, 라벨) 순서 리스트 | 새 device 표기 등장 시 |
| `EXTRA_SEGMENTS` | 추가 segment globalFilter 리스트 (빈 리스트 = v2 동일 동작) | 옵트인 |
| `EXTRA_SEGMENTS[*].enabled` | 항목별 globalFilter 추가 on/off (키 없으면 True) | 적용했던 세그 끄고 재추출 시 |
| `OUTPUT_PREFIX` | 출력 CSV 파일명 앞 prefix (`""` = 기존) | 세그 제외본/전체본 구분 저장 시 |

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
- `start_date` / `end_date` — ISO `YYYY-MM-DD`. v3 가 `YYYY-MM-DDT00:00:00.000/다음날T00:00:00.000` 형식 (AA 컨벤션) 으로 자동 변환
- 빈 줄 / `#` 시작 라인 자동 skip

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
        "name_keywords": ["[Global] Excluded EPP"],
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
1. 인증 (aanalytics2 OAuth S2S)
2. GET /projects/{PROJECT_ID} → 패널/리포틀렛 구조 전체 walk
       - 패널별 컨텍스트 감지 (year_kind, region_kind)
       - 리포틀렛별 세그먼트/메트릭 이름 추출
       - columnTree 의 DateRange 컴포넌트 감지 + API 로 definition 조회
       - /reports API payload 자동 빌드 (site 별 RSID + dateRange override 적용)
3. ThreadPoolExecutor 로 동시다발적 /reports POST
       - dimension 있는 테이블 → rows 추출
       - dimension 없는 summary 테이블 → summaryData.totals 추출
       - 429/5xx 자동 retry (지수 백오프)
       - 422/400 실패 시 metric 개별 분리 추출 (fallback)
4. 결과 정리 (패널 → 테이블 → 칼럼 순서)
       - 사이트별 별도 CSV 저장
```

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

| 파일 | 내용 |
|---|---|
| `stack_data_extract_<site_code>_{ts}.csv` | (기존 `extract_data_*`) 패널/테이블/리포틀렛별 데이터 값 (long unpivot: 1 row = 디멘션값 × value_n). `device` 컬럼 + breakdown 시 `bd{k}_dimension/itemId/value` 컬럼 포함 |
| `table_data_extract_<site_code>_{ts}.csv` | (기존 `column_mapping_*` 대체) AA 테이블 모양 가로형 — 1행 = item, `value1..N` 값 + `seg_value1..N`(`"metric;; segments"`) + `bd{k}_*` 컬럼. 테이블 블록 = `(summary)` 행 → dim1 행 → breakdown 행 |

### breakdown 행 구분 (v3.5)

- dim1 총계 행: `bd{k}_*` 전부 공백
- breakdown 행: `itemId`/디멘션 컬럼 = **부모(dim1) item**, `bd1_*` = 1단계 하위 item, `bd2_*` = 2단계 …
- 같은 dim1 item 의 총계와 breakdown 이 둘 다 들어있으므로 **단순 합산 시 이중집계 주의** — `bd1_itemId` 빈칸 여부로 필터해서 사용 (RESHAPE_standard_v1.3 의 `BREAKDOWN_ROWS_MODE` 참고)

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
2. `python extract_data_v3.7.py --dry-run --site us` 로 한 site payload 확인
3. breakdown 쓰는 경우 `--site <한곳> --breakdown-top-n 5` 로 소규모 검증 (총계 = breakdown 합 확인)
4. OK 면 전체 실행 — `python extract_data_v3.7.py`
5. `output/` 폴더의 사이트별 CSV 검토. 실패 site (FAIL 표시) 만 따로 `--site <code>` 로 재시도
6. union 정제 필요 시 `python RESHAPE_standard_v1.3.py` (breakdown 행 처리 모드는 `BREAKDOWN_ROWS_MODE`)

## 의존성

```
pip install aanalytics2 requests
```

Mac venv 사용 예 (Python 3.13):

```bash
cd data_extract
DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib .venv/bin/python3.13 extract_data_v3.7.py
```
