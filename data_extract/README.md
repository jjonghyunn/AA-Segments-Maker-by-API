# data_extract  
<sub>2026-07-28  Jonghyun Park w/ Claude</sub>  

Adobe Workspace project 의 panel × reportlet 에서 세그먼트/메트릭 이름 + 실제 데이터 값을 동시다발적으로 추출하고, 그 결과를 정제하는 도구 모음.

## 문서 인덱스

| 도구 | 문서 | 용도 |
|---|---|---|
| `extract_data_v4.3.py` | [`extract_data.md`](extract_data.md) | **현행 추출기** — N단계 breakdown + device 컬럼 + 사이트별 RSID/dateRange override + site 병렬(`SITE_WORKERS`) |
| `RESHAPE_standard_v1.7.py` | [`RESHAPE_standard_v1.7.md`](RESHAPE_standard_v1.7.md) | 추출본 union 정제 — breakdown 행 모드 + `_old` SITE CODE 정규화 + metric/Panel name 컬럼 + product 카테고리 분류 |
| `_contents/RESHAPE_contents_v1.1.py` | [`_contents/RESHAPE_contents_v1.1.md`](_contents/RESHAPE_contents_v1.1.md) | contents 계열 **정제기** (별도 시리즈) |
| `_contents/extract_data_v3.2_contents.py` | *(전용 문서 없음 — 정제기 md 참고)* | contents 계열 추출기 (아래 주 참조) |

> **`_contents` 시리즈 주**: contents 계열은 별개 lineage 로, 추출기가 메인(`v4.2`)보다 뒤처진 `v3.2` 이며 입력도 구 명명 `column_mapping_*.csv` 를 사용한다. 메인 계열과 통합되지 않은 채 분기·정체된 상태이며, 추출기 전용 문서는 없다(정제기 md 로 흐름 파악).

## 보조 파일

| 파일 | 역할 |
|---|---|
| `site_registry.py` | `site_code → rsid` 매핑 (`lookup_site()`). `_contents/` 에 사본 |
| `aa_segment_lookup.py` | segment 이름 검색 + DSL decompile 헬퍼 (원본은 `segment_maker/`, 갱신 시 동기화) |
| `sites_input.csv` | 입력 템플릿 — `site_code, start_date, end_date` |
| `stack_data_extract_example.csv` / `table_data_extract_example.csv` | 출력 형식 예시 |
| `app_O_X_example.csv` | 입력 형식 예시 — site 별 App 론치 O/X (`extract_data_v4.3.py` 의 `requires_app` device 케이스 필터용) |
| `currency_example.csv` | 입력 형식 예시 — `site_code` × `YYYY-MM-DD` 헤더 환율표 (revenue metric 환산용) |
| `product_category_example.yaml` | 입력 형식 예시 — 제품코드 분류 룰 (`ADD_CATEGORY_COLUMN=True` 일 때 `category` 컬럼 생성) |

> ⚠️ **`_example` 3종은 형식 샘플이다.** 코드가 실제로 찾는 파일명은 `app_O_X.csv` / `currency.csv` /
> `product_category.yaml` — 운영 데이터라 repo 에 포함하지 않았다. `_example` 을 뗀 이름으로 본인
> 데이터를 채워 저장할 것. (`_contents/` 계열은 그 폴더 안에 각각 두어야 한다 — 경로 기준이 스크립트와 같은 폴더)

> 구버전 `extract_data_v1~v3.x` 및 그 문서는 삭제됨(2026-06-17). 이력은 git history 참조. 현행은 `extract_data_v4.3.py` 단일 계열.
