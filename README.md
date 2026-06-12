# AA_segment_maker  
<sub>2026-06-11  Jonghyun Park w/ Claude</sub>  

Adobe Analytics 세그먼트 및 Workspace 데이터 자동화 도구 모음.

최종 업데이트: 2026-06-09

---

## 폴더 구조

```
AA_segment_maker/
├── segment_maker/          # 세그먼트 생성·조회·삭제
│   ├── aa_create_segment_v2.3.py   (v2.3 — CSV 입력, 생성+업데이트, AA validator patch. **현재 권장**)
│   ├── aa_segment_lookup.py        (ID/이름 검색 → CSV + .dsl 역변환)
│   ├── aa_segment_lookup_from_pjt.py (project 안 panel 의 segment 들 일괄 lookup)
│   ├── aa_delete_segment.py        (안전 삭제, 3중 안전장치)
│   ├── input_csv_maker.py          (raw seg_make_ref → v2.3 input CSV 자동 변환)
│   ├── input_csv_maker_*.py        (us / cc00 / or_ref / scenario / replace / from_ref(_batch / _us_hit) variant)
│   ├── prewarm_seg_ref_cache.py    (segment-ref inline cache 사전 갱신)
│   └── example_segment_campaign_main_page.py
├── utils/                  # 유틸리티
│   ├── _probe_segment.py           (세그먼트 구조 GET)
│   ├── compare_panel_segments.py   (panel 세그먼트 차집합 비교)
│   ├── extract_panel_tables_json_v2.0.py (+ .md)  (panel→JSON 추출 + 상세 문서)
│   └── find_user_id.py             (AA 사용자 numeric loginId 검색)
├── data_extract/           # Workspace 리포트 데이터 추출 (extract_data_v3.8.py 권장 + _contents 등 캠페인 variant)
│   ├── extract_data_v3.8.py        (사이트별 RSID + dateRange override + N단계 breakdown + device 컬럼 + site 병렬)
│   ├── RESHAPE_standard_v1.3.py    (추출본 union 정제 — breakdown 행 모드 + device/bd passthrough + metric/Panel name 컬럼·컬럼 제외 옵션)
│   ├── site_registry.py            (site_code ↔ rsid 매핑)
│   └── _contents/ …                (캠페인별 추출·정제 variant)
├── dateranges/             # Date Range 도구 (aa_daterange 단건 + list/update/create/upsert 일괄)
├── panel_collapse/         # panel 안 subPanel 일괄 collapse=True
├── panel_date_update/      # panel 시작/종료일 일괄 치환
├── panel_maker/            # 첫 panel/전체 panel 복제 + segment swap (recomm variant 포함)
├── segment_share/          # 본인 owner segment 키워드 매칭 → 일괄 share 추가
├── column_filler/          # tb_column_name_mapping 빈 컬럼 유사도 자동 채움
└── (루트 유지)             cleanup_recent_json.py
```

> 구버전은 **각 도구 폴더의 `old/`** 에 보관 (루트 old/ 해체, 2026-06-10). 활성 경로엔 항상 최신 1개만.

---

## 핵심 도구 요약

### segment_maker/ — 세그먼트 생성·관리

| 도구 | 설명 |
|---|---|
| **v2.3 (CSV) — 권장** | CSV 입력 (structure 칼럼) → 생성(POST) / 업데이트(PUT). dry-run CSV 자동, AA validator patch (event-exists / segment-ref auto-fetch + cache / NOT container) |
| v2 (구조 텍스트, `old/`) | SQL-like DSL → AA JSON 자동 변환. 다중 일괄 생성, `@segment_id` 참조, THEN/NOT 복합 (레거시 — `segment_maker/old/` 보관) |
| `input_csv_maker(_*).py` | raw `seg_make_ref_*.csv` → v2.3 input CSV + `.dsl` + `_WARN.csv` 자동 변환. variant 별로 us / cc00 / or_ref / scenario / replace / from_ref 룰 차이 |
| `aa_segment_lookup.py` | ID 또는 이름 키워드로 검색 → CSV (owner 이름/이메일 + structure 포함) + `.dsl` 역변환. 결과는 `lookup/` 하위. `--search` 는 모든 키워드(첫 키워드 포함)를 이름 **연속 substring** 으로 AND (v1.2), owner 보강은 AA `GET /users` (v1.1). `SEARCH_RESULT_LIMIT` 상수로 상한 조정 |
| `aa_segment_lookup_from_pjt.py` | project 의 panel 들이 참조하는 segment 목록 일괄 lookup (출력 포맷 동일, `lookup/` 하위) |
| `aa_delete_segment.py` | result CSV 기반 안전 삭제 (3중 안전장치: CSV 강제 / 이름 prefix / `--yes`) |
| `prewarm_seg_ref_cache.py` | `segment_ref_cache.json` 사전 갱신 — dry-run 빠르게 |

### utils/ — 유틸리티

| 도구 | 설명 |
|---|---|
| `_probe_segment.py` | 세그먼트 GET → definition 구조 확인 |
| `compare_panel_segments.py` | 두 panel의 세그먼트 차집합 비교 |
| `extract_panel_tables_json_v2.0.py` | panel × reportlet → `/reports` JSON 추출 + 매핑 CSV |
| `find_user_id.py` | AA 사용자 numeric loginId 검색 |

### data_extract/ — Workspace 리포트 데이터 추출

Workspace 리포트 데이터를 API로 추출 → CSV 출력. dimension 칼럼 포함.

- **v1** (`extract_data.py`) — 단일 project, panel 의 RSID·dateRange 그대로
- **v2** (`extract_data_v2.py`) — `sites_input.csv` 의 row 별로 RSID + dateRange override → 같은 panel 구조를 여러 site 에 적용. site 별 별도 CSV (`extract_data_<rsid>_<ts>.csv`)
- **v3 → v3.8 (권장)** (`extract_data_v3.8.py`) — v2 의 `sites_input` 기반 멀티사이트 구조 유지 + `site_registry.py` 로 site_code ↔ RSID 매핑 분리. v3.5: **N단계 dimension breakdown** (행 item 을 하위 차원으로 재귀 분해, `bd{k}_*` 컬럼) + **device 컬럼** (컬럼 세그명에서 Mobile/PC/Android/iOS/App 추출). v3.6: **site 단위 병렬** (`SITE_WORKERS`, 기본 5곳 동시, `--site-workers`). v3.7: **레벨별 limit cap** (`LIMIT_LV1`/`LIMIT_BD`) + 출력 2종 개편 (`stack_data_extract_*` / `table_data_extract_*`). v3.8: **device 케이스별 반복 추출** (`DEVICE_CASES`, 기본 비활성 — 패널에 device 세그 없는 프로젝트용) + `app_O_X.csv` 케이스 선택 룰 + site 별 세그 치환 (`DEVICE_CASE_SITE_OVERRIDES`)
- **정제** (`RESHAPE_standard_v1.3.py`) — 추출본 union 정제. breakdown 행 모드(`include`/`exclude`/`only`) + device/bd passthrough + `_old` SITE CODE 정규화 + v1.2: metric / Panel name 출력 컬럼·`EXCLUDE_OUTPUT_COLUMNS` 컬럼 제외 옵션

상세: `data_extract/extract_data.md`

### Panel 운영 도구 (운영 사본 모음)

코드 상단에 실제 PROJECT_ID·키워드가 박혀있는 운영 작업본. 캠페인 바꿀 때 상수만 교체. generic 변경은 repo 사본에도 동기화.

| 폴더 | 용도 |
|---|---|
| `panel_collapse/` | panel 안 모든 subPanel `collapsed=True` 강제 (panel 자체 헤더는 유지) |
| `panel_date_update/` | panel 시작/종료일 일괄 치환 — ISO interval / start*·end* 키 자동 탐지 |
| `panel_maker/` | source project 의 panel(들) 을 빈 target project 로 복제 + segment ID 자동 swap. clone_project_first_panel (이름 정규화) / panel_contents (CC 패턴) / panel_contents_recomm (recomm fallback) / panel_contents_target_seg variant |
| `segment_share/` | 본인 owner segment 중 키워드 매칭된 것에 SHARE_USER_IDS 일괄 추가 (PUT) |

---

## 인증

모든 스크립트 공통: `aanalytics2` 라이브러리 + OAuth Server-to-Server auth JSON.

```python
AUTH_JSON_PATH = "path/to/aanalytics_auth.json"
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
| NOT (부정) | `without` wrapper | `"pred": {...}` |
| AND / OR | `and` / `or` | `"preds": [{...}]` |

Container 스코프: `"hits"` / `"visits"` / `"visitors"`

---

## 의존성

```
pip install aanalytics2 pandas requests
```
