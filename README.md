# AA-Segments-Maker-by-API  
<sub>2026-05-27  Jonghyun Park w/ Claude</sub>  

Adobe Analytics 세그먼트 및 Workspace 데이터 자동화 도구 모음.

---

## 핵심 도구

### 세그먼트 생성 — `aa_create_segment_v2.3.py` ★

CSV 입력 → AA 세그먼트 일괄 생성/업데이트. DSL preprocess, self-contained.

```bash
python aa_create_segment_v2.3.py --input segments.csv                  # dry-run
python aa_create_segment_v2.3.py --input segments.csv --apply          # 실제 생성
python aa_create_segment_v2.3.py --update-or-create --apply            # id 있으면 PUT, 없으면 POST
```

주요 기능: `--update-or-create`, `--lookup-by-name`, segment-ref cache, event-exists patch, NOT container 자동 처리.

### 세그먼트 조회·삭제

| 도구 | 설명 |
|---|---|
| `aa_segment_lookup.py` | ID/이름 키워드 검색 → CSV + `.dsl` 역변환 |
| `aa_segment_lookup_from_pjt.py` | project 내 panel segment 일괄 lookup |
| `segment_lookup.py` | segment 검색 유틸 |
| `aa_delete_segment.py` | result CSV 기반 안전 삭제 (3중 안전장치) |
| `prewarm_seg_ref_cache.py` | segment-ref cache 사전 갱신 |
| `input_csv_maker(_*).py` | raw ref CSV → v2.3 input CSV 변환 (us/cc00/or_ref/scenario/replace/from_ref 등) |

### Workspace 데이터 추출 — `data_extract/`

| 버전 | 파일 | 설명 |
|---|---|---|
| **v3 ★** | `extract_data_v3.py` | site 단위 병렬 처리 (SITE_WORKERS) |
| v2 | `extract_data_v2.py` | sites_input.csv 기반 site별 RSID + dateRange override |
| | `_contents/` | contents 특화 추출 + RESHAPE v1.1 |
| | `_contents_tier2_cc_03/` | tier2 CC03 특화 |
| | `1st_cutoff17may_*/` | cutoff 기준 before/after 분리 추출 (recomm) |

### Panel 도구

| 도구 | 설명 |
|---|---|
| `extract_panel_tables_json_v2.0.py` | panel × reportlet → /reports JSON 추출 + 매핑 CSV |
| `panel_maker/clone_project_first_panel.py` | panel 복제 + segment swap |
| `panel_maker/panel_contents_recomm_v1.2.py` | recomm panel 복제 ★최신 |
| `panel_collapse/collapse_panel_tables.py` | subPanel collapse=True 강제 |
| `panel_date_update/update_panel_date.py` | panel 시작/종료일 일괄 치환 |
| `segment_share/add_segment_shares.py` | 본인 owner segment → 일괄 share 추가 |

### Date Range 도구

| 도구 | 설명 |
|---|---|
| `aa_daterange.py` | 단건 CRUD (ID 비우면 CREATE, 채우면 UPDATE) |
| `dateranges/` | 일괄 도구 (list / update / create / upsert) + 입력 CSV |

### 유틸리티

| 도구 | 설명 |
|---|---|
| `find_user_id.py` | AA 사용자 numeric loginId 검색 |
| `_probe_segment.py` | 세그먼트 GET → definition 구조 확인 |
| `_inspect_panel.py` | panel 구조 검사 |
| `compare_panel_segments.py` | 두 panel 세그먼트 차집합 비교 |
| `cleanup_recent_json.py` | extract 재실행 전 최근 JSON 일괄 삭제 |
| `site_registry.py` | site_code ↔ rsid 매핑 |
| `column_filler/fill_column_by_similarity.py` | tb_column_name_mapping 빈 칼럼 자동 채움 |

---

## 인증

```python
AUTH_JSON_PATH = "path/to/aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"
```

## 의존성

```
pip install aanalytics2 pandas requests
```

---

## 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-05-27 | 구버전 코드 `old/` 이동, README 최신화 |
| 2026-05-26 | v2.3: DSL preprocess, v2 의존성 제거 (self-contained) |
| 2026-05-22 | extract_data v3: site 단위 병렬 처리 |
| 2026-05-20 | panel_contents_recomm v1.2: recomm 개선 |
| 2026-05-18 | input_csv_maker 시리즈 추가 (scenario/or_ref/from_ref 등) |
| 2026-05-15 | v2.2: CSV 입력, AA validator patch |
| 2026-05-13 | segment_share, panel_collapse 추가 |
| 2026-05-08 | dateranges 일괄 도구, extract_panel_tables v2.0 |
| 2026-05-04 | 초기 생성 — v2, _probe_segment, compare_panel_segments, find_user_id |

> 구버전 코드는 `old/`, `panel_maker/old/`, `data_extract/old/` 등에 보존.

---

## License

MIT
