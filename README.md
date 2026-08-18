# AA-Segments-Maker-by-API  
<sub>2026-08-05  Jonghyun Park w/ Claude</sub>  

Adobe Analytics 세그먼트 및 Workspace 데이터 자동화 도구 모음.

---

## 폴더 구조

```
AA-Segments-Maker-by-API/
├── segment_maker/          # 세그먼트 생성·조회·삭제
│   ├── aa_create_segment_v*.py     (CSV 입력, 생성+업데이트, AA validator patch. **현재 권장**)
│   ├── aa_segment_lookup.py        (ID/이름 검색 → CSV + .dsl 역변환)
│   ├── aa_segment_lookup_from_pjt.py (project 안 panel 의 segment 들 일괄 lookup)
│   ├── aa_delete_segment.py        (안전 삭제, 3중 안전장치)
│   ├── prewarm_seg_ref_cache.py    (참조 세그 캐시 사전 채움 — 캐시별 id 그룹 / --all / --refresh)
│   ├── input_csv_maker.py          (raw seg_make_ref → input CSV 자동 변환)
│   └── input_csv_maker_us.py / _from_ref_batch.py  (us / from_ref 일괄 variant)
├── utils/                  # 유틸리티
│   └── find_user_id.py             (AA 사용자 numeric loginId 검색)
├── data_extract/           # Workspace 리포트 데이터 추출 (extract_data_v*.py 권장 + _contents_tier1_2_uni 등 캠페인 variant)
│   ├── extract_data_v*.py          (사이트별 RSID + dateRange override + N단계 breakdown + device 컬럼 + site 병렬)
│   ├── RESHAPE_standard_v*.py      (추출본 union 정제 — breakdown 행 모드 + device/bd passthrough + metric/Panel name 컬럼·컬럼 제외 옵션 + product category 분류)
│   ├── site_registry.py            (site_code ↔ rsid 매핑)
│   ├── aa_segment_lookup.py        (extract_data 가 import 하는 필수 의존 — 원본은 segment_maker/, 갱신 시 동기화)
│   ├── sites_input.csv             (추출 대상 site/기간 입력 템플릿)
│   ├── *_example.csv / *_example.yaml  (입출력 형식 예시 — 실제 입력 파일은 repo 미포함, README 참조)
│   └── _contents_tier1_2_uni/ …    (콘텐츠 CC_xx Tier1+Tier2 통합 추출·정제)
├── dateranges/             # Date Range 도구 (aa_daterange 단건 + list/update/create/upsert 일괄)
├── panel_collapse/         # panel 안 subPanel 일괄 collapse=True
├── panel_date_update/      # panel 시작/종료일 일괄 치환
├── panel_maker/            # source project panel 복제 + segment swap (기본 전체 panel, recomm variant 포함)
├── segment_share/          # 본인 owner segment 키워드 매칭 → 일괄 share 추가
├── column_filler/          # tb_column_name_mapping 빈 컬럼 유사도 자동 채움
└── (루트 유지)             cleanup_recent_json.py, requirements.txt, LICENSE
```

> 구버전은 정리 완료 (2026-06-17). 활성 경로엔 항상 최신 1개만.

---

## 빠른 시작 (end-to-end 예시)

세그먼트 생성 → 조회 → 데이터 추출까지의 기본 흐름. 각 단계 입력/출력 형식은 각 폴더의 `*_example.*` 파일 참고.

```bash
# 1) raw 명세(seg_make_ref CSV) → v2.4 입력 CSV/DSL 자동 변환
cd segment_maker
python input_csv_maker.py                  # → segments_input_<ts>.csv  (형식: segments_input_example.csv)

# 2) 참조 세그 캐시 준비 (선택 — v2.4 dry-run 이 자동으로 채우기도 함)
python prewarm_seg_ref_cache.py --all      # → segment_ref_cache_<name>.json

# 3) dry-run 으로 파싱 검증 → 문제 없으면 실제 생성
python aa_create_segment_v2.4.py --input segments_input_<ts>.csv                     # dry-run
python aa_create_segment_v2.4.py --input segments_input_<ts>.csv --update-or-create --apply
#   → segment_result_<ts>.csv  (형식: segments_result_example.csv)

# 4) 만든 세그먼트 이름으로 재조회 (id/structure 확인)
python aa_segment_lookup.py --search "[CAMPAIGN NAME]"     # → lookup/segment_lookup_<ts>.csv

# 5) Workspace 프로젝트 패널 데이터 추출
cd ../data_extract
python extract_data_v4.4.py                # sites_input.csv 의 site 별로 추출
#   → stack_data_extract_<site>_<ts>.csv  (형식: stack_data_extract_example.csv)
#   → table_data_extract_<site>_<ts>.csv  (형식: table_data_extract_example.csv)
python RESHAPE_standard_v1.8.py            # union 정제
```

> 입력 CSV·structure DSL·결과 CSV 의 구체 형식은 `segment_maker/segments_input_example.csv` · `.dsl` · `segments_result_example.csv`, `data_extract/table_data_extract_example.csv` · `stack_data_extract_example.csv`, `panel_maker/panel_contents_mapping_example.csv` 참고. (모두 placeholder 값)

## 핵심 도구 요약

### segment_maker/ — 세그먼트 생성·관리

| 도구 | 설명 |
|---|---|
| **`aa_create_segment_v*.py` (CSV) — 권장** | CSV 입력 (structure 칼럼) → 생성(POST) / 업데이트(PUT). dry-run CSV 자동, AA validator patch (event-exists / segment-ref auto-fetch + cache / NOT container) |
| `input_csv_maker(_*).py` | raw `seg_make_ref_*.csv` → input CSV + `.dsl` + `_WARN.csv` 자동 변환. variant: us / from_ref_batch 룰 차이 |
| `aa_segment_lookup.py` | ID 또는 이름 키워드로 검색 → CSV (owner 이름/이메일 + structure 포함) + `.dsl` 역변환. 결과는 `lookup/` 하위. `--search` 는 모든 키워드(첫 키워드 포함)를 이름 **연속 substring** 으로 AND (v1.2), owner 보강은 AA `GET /users` (v1.1). `SEARCH_RESULT_LIMIT` 상수로 상한 조정. `--modified-after/before YYYY-MM-DD` 로 수정일 필터(AA 가 생성일 미제공 → `modified` 기준, both inclusive) |
| `aa_segment_lookup_from_pjt.py` | project 의 panel 들이 참조하는 segment 목록 일괄 lookup (출력 포맷 동일, `lookup/` 하위) |
| `aa_delete_segment.py` | result CSV 기반 안전 삭제 (3중 안전장치: CSV 강제 / 이름 prefix / `--yes`) |
| `prewarm_seg_ref_cache.py` | 참조 세그 캐시(`segment_ref_cache_<name>.json`) 사전 채움 — `SEGMENT_IDS_BY_CACHE` 캐시별 id 그룹, `--cache <key>` / `--all` / `--refresh` |

### utils/ — 유틸리티

| 도구 | 설명 |
|---|---|
| `find_user_id.py` | AA 사용자 numeric loginId 검색 |

### data_extract/ — Workspace 리포트 데이터 추출

Workspace 리포트 데이터를 API로 추출 → CSV 출력. dimension 칼럼 포함.

- **추출** (`extract_data_v*.py`, 권장) — `sites_input.csv` 의 row 별로 RSID + dateRange override → 같은 panel 구조를 여러 site 에 적용. `site_registry.py` 로 site_code ↔ RSID 매핑 분리. N단계 dimension breakdown (행 item 을 하위 차원으로 재귀 분해, `bd{k}_*` 컬럼) + device 컬럼 + site 단위 병렬 (`SITE_WORKERS`) + 레벨별 limit cap + device 케이스별 반복 추출 (`DEVICE_CASES` / `app_O_X.csv`). site 별 별도 CSV
- **정제** (`RESHAPE_standard_v*.py`) — 추출본 union 정제. breakdown 행 모드(`include`/`exclude`/`only`) + device/bd passthrough + `_old` SITE CODE 정규화 + metric / Panel name 출력 컬럼·`EXCLUDE_OUTPUT_COLUMNS` 컬럼 제외 옵션 + product 키워드 행 `product_category.yaml` category 분류

> **입력 참조 파일은 repo 미포함** — `app_O_X.csv`(App 론치 O/X) · `currency.csv`(환율) · `product_category.yaml`(제품 분류 룰) 은 운영 데이터라 제외했다. 같은 폴더의 `*_example` 3종으로 형식을 확인하고 `_example` 을 뗀 이름으로 채워 쓸 것. 자세한 건 [`data_extract/README.md`](data_extract/README.md).

구버전 lineage·상세 변경이력: `data_extract/extract_data.md`

### Panel 운영 도구 (운영 사본 모음)

로컬 운영 작업본은 코드 상단에 실제 PROJECT_ID·키워드를 박아 쓰지만, **공개 repo 사본은 placeholder**. 캠페인 바꿀 때 상수만 교체. generic 변경은 repo 사본에도 동기화.

| 폴더 | 용도 |
|---|---|
| `panel_collapse/` | panel 안 모든 subPanel `collapsed=True` 강제 (panel 자체 헤더는 유지) |
| `panel_date_update/` | panel 시작/종료일 일괄 치환 — ISO interval / start*·end* 키 자동 탐지 |
| `panel_maker/` | source project 의 panel(들) 을 빈 target project 로 복제 + segment ID 자동 swap. panel_contents (CC 패턴) / panel_contents_recomm (recomm fallback) |
| `segment_share/` | 본인 owner segment 중 키워드 매칭된 것에 SHARE_USER_IDS 일괄 추가 (PUT) |

### 기타 도구

| 도구 | 설명 |
|---|---|
| `dateranges/` | Date Range 도구 모음 — `aa_daterange.py` 단건 CRUD + `aa_dateranges_list/update/create/upsert.py` 일괄 (입력 CSV: `dateranges_sites_input*.csv`) |
| `column_filler/` | `tb_column_name_mapping` CSV 의 빈 column 을 유사도로 자동 채움 (`fill_column_by_similarity.py`) |
| `cleanup_recent_json.py` (루트) | extract 재실행 전 최근 생성된 JSON 을 일괄 삭제 (키워드 제외 지원) |

---

## 인증

모든 스크립트 공통: `aanalytics2` 라이브러리 + OAuth Server-to-Server auth JSON.

```python
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"
```

---

## Segment 조건 빠른 참조

| 어도비 UI 라벨 | API func | 보조 필드 |
|---|---|---|
| equals | `streq` | `"str": "값"` |
| contains | `contains` | `"str": "값"` |
| contains any of | `contains-any-of` | `"list": [...]` |
| contains all of | `contains-all-of` | `"list": [...]` |
| equals any of | `streq-in` | `"list": [...]` |
| starts with | `starts-with` | `"str": "값"` |
| ends with | `ends-with` | `"str": "값"` |
| matches (정규식) | `matches-regex` | `"regex": "패턴"` |
| exists | `exists` | (없음) |
| event<N> exists | `event-exists` | `"evt": event` (metric 컨테이너) |
| 숫자 비교 | `eq` / `gt` / `lt` / `ge` / `le` | `"num": 숫자` |
| NOT (컨테이너 부정) | `without` wrapper | `"pred": {...}` |
| does not equal | `not-streq` (DSL `not-equals`) | `"str": "값"` |
| does not equal any of | `not-streq-in` (DSL `not-equal-any-of`) | `"list": [...]` |
| does not contain | `not-contains` | `"str": "값"` |
| does not contain any of | `not-contains-any-of` | `"list": [...]` |
| does not start with | `not-starts-with` | `"str": "값"` |
| does not end with | `not-ends-with` | `"str": "값"` |
| does not exist | `not-exists` | (없음) |
| AND / OR | `and` / `or` | `"preds": [{...}]` |

Container 스코프: `"hits"` / `"visits"` / `"visitors"`

> **부정(negation) 2가지**: ① 컨테이너 전체 부정 = `without` wrapper, ② leaf 단일 조건 부정 = 위 native `not-*` func (`not-streq`→DSL `not-equals`, `not-streq-in`→`not-equal-any-of`, `not-contains`/`not-starts-with`/`not-exists` …). lookup 이 native func 을 DSL 로 표기하고 maker 가 그대로 되읽어 왕복(round-trip). (⚠ `event-exists`(metric) / numeric `eq` 의 부정은 아직 미매핑.)

---

## 의존성

```
pip install aanalytics2 requests pyyaml
```

- `pyyaml` 은 `data_extract/RESHAPE_standard_v*.py` 의 product 카테고리 분류(`product_category.yaml`)에만 필요.
