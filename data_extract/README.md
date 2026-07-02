# [구버전 참고 · v1~v3, 삭제됨] extract_data.py / extract_data_v2.py / extract_data_v3.py  
> ⚠ 본 문서가 다루는 v1~v3.1 파일들은 삭제됨 (2026-06-17). **최신은 `extract_data_v4.0.py` — `extract_data.md` 참조.** 본 문서는 구버전 참고용.  
<sub>2026-06-09  Jonghyun Park w/ Claude</sub>  

Adobe Workspace project의 모든 panel × reportlet에서 세그먼트/메트릭 이름 + 실제 데이터 값을 **동시다발적으로** 추출.

| 파일 | 용도 |
|---|---|
| `extract_data.py` (v1) | 단일 project + project 의 panel reportSuite / dateRange 그대로 |
| `extract_data_v2.py` | **사이트별 RSID + dateRange override** (`sites_input.csv` 의 row 별로) |
| `extract_data_v3.py` | v2 베이스 + **EXTRA_SEGMENTS 옵션** (세그 이름 검색 또는 ID 로 globalFilter 추가) |
| `extract_data_v3_contents*.py` (v3 contents 시리즈) | v2 베이스 + **site 단위 병렬 처리 옵션** (`SITE_WORKERS` 상수) |

> 주의: `extract_data_v3.py` 와 `extract_data_v3_contents*.py` 는 **이름은 비슷하지만 다른 시리즈**. 전자는 segment 추가, 후자는 site 병렬화.

## 사용법

```bash
python extract_data.py                    # 전체 추출 (콘솔 출력 + CSV 저장)
python extract_data.py --dry-run          # payload 생성까지만 (API 호출 안 함)
python extract_data.py --workers 8        # 병렬 워커 수 조정 (기본 6)
python extract_data.py --limit 100        # dimension row 수 제한 (기본 400)
python extract_data.py --year 2025        # 기준년도 오버라이드
python extract_data.py --show-filters     # 패널별 전체필터 세그먼트 목록 확인
python extract_data.py --remove-filter sXXXXXXXXX_xxx  # 특정 세그먼트 빼고 추출
python extract_data.py --add-filter sXXXXXXXXX_xxx     # 세그먼트 추가해서 추출
```

## 동작 흐름

```
1. 인증 (aanalytics2 OAuth S2S)
       │
2. GET /projects/{PROJECT_ID} → 패널/리포틀렛 구조 전체 walk
       │  - 패널별 컨텍스트 감지 (year_kind, region_kind)
       │  - 리포틀렛별 세그먼트/메트릭 이름 추출
       │  - columnTree의 DateRange 컴포넌트 감지 + API로 definition 조회
       │  - /reports API payload 자동 빌드
       │
3. ThreadPoolExecutor로 동시다발적 /reports POST
       │  - dimension 있는 테이블 → rows 추출
       │  - dimension 없는 summary 테이블 → summaryData.totals 추출
       │  - 429/5xx 자동 retry (지수 백오프)
       │  - 422/400 실패 시 metric 개별 분리 추출 (fallback)
       │
4. 결과 정리 (패널 → 테이블 → 칼럼 순서)
       │  - 콘솔 출력: 패널별 구분, 칼럼 매핑 표시, 값 미리보기
       │  - CSV 저장: 데이터 + 칼럼 매핑 각각 별도 파일
```

## 출력 파일

| 파일 | 내용 |
|---|---|
| `output/extract_data_{ts}.csv` | 패널/테이블/리포틀렛별 데이터 값. dimension 테이블은 행 단위, summary 테이블은 한 행 |
| `output/column_mapping_{ts}.csv` | value_n이 어떤 세그먼트/메트릭인지 매핑 + 실제 값 (panel, table, reportlet, value_n, metric, segments, data_value) |

## 사용자 설정 (상단 상수)

| 상수 | 설명 | 변경 시점 |
|---|---|---|
| `AUTH_JSON_PATH` | Adobe OAuth 인증 JSON 경로 | 환경 변경 시 |
| `COMPANY_ID` | Adobe Analytics company ID | 보통 고정 (`company_id`) |
| `PROJECT_ID` | Workspace URL의 `/workspace/edit/{이부분}` | **프로젝트마다 변경** |
| `MAX_WORKERS` | 병렬 워커 수 (5~8 추천) | 성능/안정성 조절 |
| `LIMIT` | dimension row 수 제한 | 데이터 양 조절 |
| `BASE_YEAR` | 기준년도 (panel 컨텍스트 감지용) | 매년/시즌 변경 |
| `REQUIRED_PANEL_KEYWORDS` | 빈 리스트면 모든 패널 통과 | 특정 패널만 추출 시 |

## 전체필터 조회/수정

`--show-filters`로 패널에 걸린 세그먼트 목록 확인 후, `--add-filter` / `--remove-filter`로 세그먼트를 추가/제거하여 재추출 가능.

```bash
python extract_data.py --show-filters
# 출력 예:
# [panel 0] [DE] ...
#   ON   sXXXXXXXXX_xxx  [Global] Excluded EPP
#   ON   sXXXXXXXXX_yyy  [part_name] Excluded EPP

python extract_data.py --remove-filter sXXXXXXXXX_yyy   # 하나 빼고 추출
python extract_data.py --add-filter sXXXXXXXXX_zzz      # 하나 추가해서 추출
```

## Fallback (개별 metric 추출)

API가 422/400을 반환하면 (메트릭이 너무 많거나 dateRange가 긴 경우 등), 자동으로 metric을 1개씩 분리해서 개별 요청 후 결과를 합침. 콘솔에 `(fallback: individual metrics)` 표시.

## DateRange 처리

columnTree에 DateRange 컴포넌트가 있으면:
1. `GET /dateranges/{id}?expansion=definition`으로 실제 날짜 범위 조회 (시작 시 일괄 prefetch + 캐시)
2. metricFilter에 `dateRange` + `dateRangeId` 둘 다 포함하여 정확한 값 추출
3. segments 칼럼에 dateRange 이름 표시 (예: `[DE CAMPAIGN NAME 직전 4주] (2026. 3. 12 ~ 2026. 4. 8)`)

> 주의: 2년 이상 긴 dateRange는 API가 `max network bytes exceeded` (422)로 거부할 수 있음. Workspace UI는 내부 캐시로 처리하지만 raw API에는 제한 있음.

## 지원하는 테이블 유형

- **Summary 테이블** (dimension 없음): `summaryData.totals`에서 값 추출. 대부분의 cross-tab/햄버거 구조
- **Dimension 테이블** (dimension 있음): `rows`에서 행 단위 추출 + 페이지네이션 지원
- **Cross-tab (row × column)**: staticRows + columnTree 결합. row 세그먼트 × column 세그먼트 격자

## 의존성

```
pip install aanalytics2 requests
```

## venv 사용 (Mac)

이 Mac에서는 Python 3.13 venv로 실행:

```bash
cd data_extract
DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib .venv/bin/python3.13 extract_data.py
```

## 테스트 결과 (2026-05-14 16:50)

- 프로젝트: `YOUR_ID` (campaign_name Campaign — DE, MX)
- 2개 패널 × 13개 리포틀렛 = 26개 task
- 23/26 성공, 3개 실패 (DE 패널 긴 dateRange 테이블 — API 용량 초과)
- dateRange 반영 값 검증 완료 (DE 재방문 value1: 2,724,568 ≈ Workspace 2,724,567)

---

# extract_data_v2.py — 사이트별 RSID + dateRange override

v1 의 한계 — 같은 PROJECT 의 panel 만 그대로 추출 → site 별 RSID / start·end 가 다른 캠페인엔 적용 불가.

v2 = `sites_input.csv` 의 `(site_code, start_date, end_date)` row 마다 RSID + dateRange 를 자동 override 해서 같은 panel 구조를 여러 site 에 적용.

## 파일 구조

| 파일 | 용도 |
|---|---|
| `extract_data_v2.py` | 메인 스크립트 |
| `sites_input.csv` | 입력 — `site_code, start_date, end_date` 3 컬럼 |
| `site_registry.py` | `site_code → rsid` 매핑 (utils 폴더에서 복사). `lookup_site()` 함수 제공 |

## 사용법

```bash
python extract_data_v2.py                       # sites_input.csv 의 모든 site 처리
python extract_data_v2.py --site us             # us 하나만
python extract_data_v2.py --site us --site uk   # 여러 개 좁히기
python extract_data_v2.py --dry-run             # payload 생성까지만 (API X)
python extract_data_v2.py --workers 8           # 병렬 워커
python extract_data_v2.py --limit 200           # dimension row 제한
```

## `sites_input.csv` 형식

```csv
site_code,start_date,end_date
ae,2026-05-11,2026-05-17
au,2026-05-14,2026-05-17
br,2026-05-11,2026-05-17
de,2026-05-12,2026-05-17
...
```

- `site_code` — `site_registry.py` 의 `_SITE_MASTER` 의 key (`ae`, `au`, `br`, `de`, ... `mstglobal`). 매핑에 없으면 fallback `sscompany_name4{site_code 의 _ 제거}` 사용
- `start_date` / `end_date` — ISO `YYYY-MM-DD`. v2 가 `YYYY-MM-DDT00:00:00.000/다음날T00:00:00.000` 형식 (AA 컨벤션) 으로 자동 변환
- 빈 줄 / `#` 시작 라인 자동 skip — 일부 site 만 일시 제외할 때 `#` 붙이기

## v1 대비 동작 차이

| 항목 | v1 | v2 |
|---|---|---|
| 처리 site | PROJECT 의 panel reportSuite | `sites_input.csv` 의 row 별 site (RSID 매핑) |
| panel dateRange | panel 의 dateRange 그대로 | row 의 `start_date ~ end_date` 로 override |
| 결과 CSV | `extract_data_{ts}.csv` 한 개 | **사이트별 별도** — `extract_data_<rsid>_{ts}.csv` |
| mapping CSV | `column_mapping_{ts}.csv` 한 개 | **사이트별 별도** — `column_mapping_<rsid>_{ts}.csv` |
| 결과 CSV 첫 4 컬럼 | panel/table/... 부터 | `site_code, rsid, start_date, end_date` 4 컬럼 추가 |

## site_registry 매핑 lookup 흐름

```
row 의 site_code (예: "au")
    │
    ↓
lookup_site("au")  ── site_registry._SITE_MASTER 에서 찾기
    │
    ↓
SiteInfo(subsidiary="FRNH", country="Australia", site_code="au", rsid="rsid_placeholder")
    │
    ↓
payload 의 "rsid" → "rsid_placeholder"
payload 의 globalFilters[dateRange] → "2026-05-14T00:00:00.000/2026-05-18T00:00:00.000"
```

- 정식 매핑 외에 `_` 제거 alias 도 시도 (예: `ca_fr` → 매핑 없으면 `cafr` 시도)
- 끝까지 매칭 안 되면 fallback: `sscompany_name4{normalized}`

## 출력 파일 예시

```
output/
├── extract_data_rsid_placeholder.csv          ← au site 의 panel × reportlet 데이터
├── column_mapping_rsid_placeholder.csv         ← au site 의 칼럼 매핑
├── extract_data_rsid_placeholder.csv          ← br
├── column_mapping_rsid_placeholder.csv
├── extract_data_rsid_placeholder.csv        ← us (rsid_placeholder 처럼 일반 형식 외도 매핑됨)
├── column_mapping_rsid_placeholder.csv
└── ...
```

각 사이트의 extract CSV 첫 4 컬럼은 `site_code, rsid, start_date, end_date` — site 식별자 + 기간 정보가 모든 row 에 같이 박혀서 사후 통합 분석 시 join 키로 활용 가능.

## 권장 사용 흐름

1. `sites_input.csv` 의 site 들 + 캠페인 시즌의 start/end 채움 (또는 site 별 다른 기간)
2. `python extract_data_v2.py --dry-run --site us` 로 한 site 만 payload 확인
3. payload OK 면 전체 실행 — `python extract_data_v2.py`
4. `output/` 폴더의 사이트별 CSV 검토. 실패 site (FAIL 표시) 만 따로 `--site <code>` 로 재시도

---

# extract_data_v3.py — EXTRA_SEGMENTS 옵션 (세그 이름 검색 → globalFilter 추가)

v2 베이스 + **추가 segment 를 panel 의 globalFilter 에 끼워넣는** 옵션. 캠페인마다 panel 마다 동일한 추가 필터(예: `visitor id = d=mid, null (Exclude)`)를 수동으로 박는 부담 해소.

`EXTRA_SEGMENTS = []` 이면 v2 와 100% 동일 동작 (옵트인).

## 파일 의존성

| 파일 | 위치 | 역할 |
|---|---|---|
| `extract_data_v3.py` | 같은 폴더 | 메인 |
| `aa_segment_lookup.py` | **같은 폴더 (사본 필수)** | segment 이름 검색 + DSL decompile 헬퍼 import |
| `site_registry.py` | 같은 폴더 | v2 와 동일 |
| `sites_input.csv` | 같은 폴더 | v2 와 동일 |

> 원본 `aa_segment_lookup.py` 는 `...\260504_AA_segment_maker\segment_maker\` 에 있음. v3 는 `Path(__file__).resolve().parent` 로 같은 폴더 사본을 import 하므로 운영 폴더마다 사본 두는 게 룰.

## 사용법

```bash
python extract_data_v3.py                       # EXTRA_SEGMENTS 적용해서 전체 site
python extract_data_v3.py --site us             # us 만
python extract_data_v3.py --dry-run             # payload 생성까지만
# 나머지 옵션은 v2 와 동일 — --workers, --limit, --include-global-for-us
```

## EXTRA_SEGMENTS 설정 (상단 상수)

항목 하나 = 추가 segment 1개. 각 항목은 **segment_id 직접 지정** 또는 **name_keywords 이름 검색** 둘 중 하나.

```python
EXTRA_SEGMENTS: list[dict] = [
    # 1) ID 직접 지정 — 가장 빠름 (검색·lookup 생략)
    {"segment_id": "segment_id_placeholder", "panel_scope": "all"},

    # 2) 풀네임 substring 검색 — Adobe `name` 필터 = case-insensitive contains
    #    그 문자열을 포함하는 모든 세그 반환 (완전 일치 + 더 긴 이름도)
    {"name_keywords": "visitor id = d=mid, null (Exclude)"},

    # 3) AND 키워드 리스트 — 첫 키워드는 server-side, 나머지는 client-side AND
    {
        "name_keywords": ["visitor id", "d=mid", "null", "Exclude"],
        "panel_scope": "all",
    },

    # 4) panel 일부 적용 — panel.name 에 키워드 포함된 panel 만 (OR 매칭)
    {
        "name_keywords": ["[Global] Excluded EPP"],
        "panel_scope": ["[Global]"],
    },
]
```

### panel_scope
- `"all"` (생략 시 기본) → 모든 panel
- `["키워드", ...]` → panel.name 에 키워드 포함 시만 (OR, case-insensitive)

### REQUIRED_PANEL_KEYWORDS 와의 차이
| 상수 | 역할 |
|---|---|
| `REQUIRED_PANEL_KEYWORDS` | 그 panel **자체를 처리할지 말지** (필터) |
| `EXTRA_SEGMENTS[*].panel_scope` | 처리되는 panel 중 **추가 segment 를 적용할지 말지** |

## 매칭 정책 (name_keywords 검색 시)

| 매칭 수 | 동작 |
|---|---|
| 1개 | 진행 (lookup CSV/DSL 같이 저장) |
| 2~5개 | 콘솔에 ID+이름 나열 + 중단 (lookup CSV/DSL 저장). 키워드를 좁히고 재실행 |
| 6개 이상 | "lookup CSV 확인" 메시지 + 중단 (lookup CSV/DSL 저장). 위 CSV 보고 키워드 좁히기 |
| 0개 | 에러 + 중단 |

`segment_id` 직접 지정 케이스는 검색 단계 자체를 건너뛰므로 lookup 파일 생성 안 함.

## lookup 출력 파일

```
data_extract/
└── lookup/
    ├── segment_lookup_<query_slug>_<YYMMDD_HHMM>.csv
    └── segment_lookup_<query_slug>_<YYMMDD_HHMM>.dsl
```

- **CSV columns**: `segment_id, name, owner_id, owner_name, rsid, description, tags, structure, error`
  - `structure` = decompile 된 DSL 을 한 줄(`|` 구분)로 펼친 것
- **DSL**: 모든 매치를 한 파일에 `===` 구분선으로 이어붙임. 들여쓰기 보존 → 그대로 `aa_create_segment_v2.3.py --input` 으로 재사용 가능

`<query_slug>` = name_keywords 의 alphanumeric 만 남기고 `__` 로 join (예: `visitor_id__d_mid__null__Exclude`).

## v2 대비 동작 차이

| 항목 | v2 | v3 |
|---|---|---|
| globalFilters 구성 | panel 의 segmentGroups + dateRange | 위 + **EXTRA_SEGMENTS 의 추가 segment** |
| 추가 의존성 | — | 같은 폴더 `aa_segment_lookup.py` 사본 |
| 검색 단계 | 없음 | EXTRA_SEGMENTS 가 있고 name_keywords 면 `/segments` 호출 후 매칭 검증 |
| 신규 출력 | — | `lookup/segment_lookup_*.csv` + `.dsl` (검색 케이스만) |

## 권장 사용 흐름

1. lookup 부터 — 이름 모르면 키워드로 검색:
   ```python
   EXTRA_SEGMENTS = [{"name_keywords": ["visitor id", "Exclude"]}]
   ```
   → 매칭 결과 콘솔 + `lookup/` 폴더 CSV/DSL 확인
2. 원하는 segment ID 골라 직접 지정 모드로 전환:
   ```python
   EXTRA_SEGMENTS = [{"segment_id": "sXXXXXXXXX_xxx", "panel_scope": "all"}]
   ```
3. `--dry-run --site us` 로 한 site payload 확인
4. 전체 실행 — `python extract_data_v3.py`

---

# extract_data_v3_contents*.py — site 단위 병렬 처리

v2 contents 시리즈 베이스 + main 의 site loop 를 `ThreadPoolExecutor` 로 병렬화. 각 site 가 자기 CSV (`extract_data_<rsid>_{ts}.csv` / `column_mapping_<rsid>_{ts}.csv`) 따로 떨어뜨려 충돌 없음.

## 파일 위치

| 파일 | 폴더 |
|---|---|
| `extract_data_v3.2_contents.py` | `_contents/` |

## v2 contents 대비 차이

- 상단 상수 `SITE_WORKERS` 추가 (`MAX_WORKERS` 옆)
- `SITE_WORKERS = 1` → v2 동일 순차 / `2+` → site 단위 병렬
- 동시 API 요청 = `SITE_WORKERS × MAX_WORKERS` — AA throttling 주의
- 기본값 `5` (사이트 10개 이상 환경 기준)

## 사용 예 (코드 상단 상수만 조정 후 실행)

```python
# 상단
MAX_WORKERS = 6
SITE_WORKERS = 5    # 1=순차, 2-3=site 적을 때, 5-8=site 많을 때
```

```bash
python extract_data_v3.2_contents.py             # SITE_WORKERS 값 그대로 사용
python extract_data_v3.2_contents.py --site us   # 특정 site 만 (병렬 의미 없음)
```

## SITE_WORKERS 권장값

| sites 수 | 권장 `SITE_WORKERS` | 이유 |
|---|---|---|
| 1-3 | `1` | 병렬 오버헤드 > 이득 |
| 4-9 | `2-3` | throttling 위험 적게 |
| 10-29 | `4-5` | 일반적인 캠페인 규모 |
| 30+ | `5-8` | 단 429 발생 시 줄이기 |

## throttling 발생 시

콘솔에 `429` 또는 `Too Many Requests` 자주 보이면:
1. `SITE_WORKERS` 줄이기 (5 → 3 → 2)
2. `MAX_WORKERS` 도 같이 줄이기 (6 → 4)
3. 그래도 안 되면 `--workers <N>` 로 일시 override
