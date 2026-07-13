# data_extract  
<sub>2026-07-13  Jonghyun Park w/ Claude</sub>  

Adobe Workspace project 의 panel × reportlet 에서 세그먼트/메트릭 이름 + 실제 데이터 값을 동시다발적으로 추출하고, 그 결과를 정제하는 도구 모음.

## 문서 인덱스

| 도구 | 문서 | 용도 |
|---|---|---|
| `extract_data_v4.1.py` | [`extract_data.md`](extract_data.md) | **현행 추출기** — N단계 breakdown + device 컬럼 + 사이트별 RSID/dateRange override + site 병렬(`SITE_WORKERS`) |
| `RESHAPE_standard_v1.6.py` | [`RESHAPE_standard_v1.6.md`](RESHAPE_standard_v1.6.md) | 추출본 union 정제 — breakdown 행 모드 + `_old` SITE CODE 정규화 + metric/Panel name 컬럼 + product 카테고리 분류 |
| `_contents/extract_data_v3.2_contents.py` | [`_contents/RESHAPE_contents_v1.1.md`](_contents/RESHAPE_contents_v1.1.md) | contents 계열 추출 + 정제 (별도 시리즈) |

## 보조 파일

| 파일 | 역할 |
|---|---|
| `site_registry.py` | `site_code → rsid` 매핑 (`lookup_site()`). `_contents/` 에 사본 |
| `aa_segment_lookup.py` | segment 이름 검색 + DSL decompile 헬퍼 (원본은 `segment_maker/`, 갱신 시 동기화) |
| `sites_input.csv` | 입력 템플릿 — `site_code, start_date, end_date` |
| `stack_data_extract_example.csv` / `table_data_extract_example.csv` | 출력 형식 예시 |

> 구버전 `extract_data_v1~v3.x` 및 그 문서는 삭제됨(2026-06-17). 이력은 git history 참조. 현행은 `extract_data_v4.1.py` 단일 계열.
