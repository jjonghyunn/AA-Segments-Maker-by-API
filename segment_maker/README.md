# segment_maker/  
<sub>2026-06-22  Jonghyun Park w/ Claude</sub>  

Adobe Analytics 세그먼트 생성·조회·삭제 도구 모음.

## 파일 목록

| 파일 | 용도 |
|---|---|
| `aa_create_segment_v2.3.py` | **권장** — CSV 입력 + AA validator 호환 patch 자동화. CREATE / UPDATE / mixed (`--update-or-create`) 지원 |
| `aa_segment_lookup.py` | 세그먼트 ID 또는 이름 키워드 검색 → CSV (기본 정보 + owner 이름/이메일 + structure 칼럼) + `.dsl` (DSL 역변환). v2.3 가 받는 입력 형식과 동일. 결과는 `lookup/` 하위 |
| `aa_segment_lookup_from_pjt.py` | project 안 panel·reportlet 이 참조하는 segment 들을 일괄 lookup → CSV + DSL (`lookup/` 하위) |
| `aa_delete_segment.py` | result CSV 기반 안전 삭제 (3중 안전장치: CSV 강제, 이름 prefix 검증, `--yes` 필수) |
| `input_csv_maker.py` | raw `seg_make_ref_*.csv` → v2.3 input CSV + `.dsl` + `_WARN.csv` 자동 변환. LCS 추출 / crystallize override / 양수·음수 site 필터 / 그룹화 |
| `input_csv_maker_us.py` / `input_csv_maker_from_ref_batch.py` | `input_csv_maker.py` 의 variant — US 룰 / from_ref 일괄 변환 룰 차이 |

## 예시 파일 (형식 참고용)

| 파일 | 보여주는 것 |
|---|---|
| `segments_input_example.csv` | v2.3 가 받는 입력 CSV 형식 (`structure` 칼럼은 ` \| ` 구분 한 줄 DSL) |
| `segments_input_example.dsl` | 같은 내용의 멀티라인 DSL (시각 확인용) |
| `segments_result_example.csv` | `--apply` 실행 결과 CSV 형식 (`Action` = POST/PUT, `Status`, `SegmentId` …) |

> 예시 파일의 캠페인명·ID·RSID 는 placeholder (`[CAMPAIGN NAME]`, `YOUR_ID`, `YOUR_SEGMENT_ID`, `sscompany_name4mstglobal`). 실제 환경 값으로 바꿔 사용.

## aa_create_segment_v2.3.py — CSV 입력 + AA validator patch 자동화 (권장)

CSV 한 장이면 생성·업데이트 다 됨.

```bash
python aa_create_segment_v2.3.py --input segments.csv                # dry-run + dryrun CSV
python aa_create_segment_v2.3.py --input segments.csv --apply        # 실제 POST (CREATE)
python aa_create_segment_v2.3.py --input segments.csv --update --apply              # PUT (모든 row update)
python aa_create_segment_v2.3.py --input segments.csv --update-or-create --apply    # mixed — id 있으면 PUT, 없으면 POST
```

`--input` 비우면 폴더의 최신 `segments_input_*.csv` 자동 pick.
`--lookup-by-name` (default True) — `--update-or-create` 모드에서 segment_id 빈 row 는 폴더의 `segment_lookup_*.csv` 에서 name 매칭으로 자동 채움.

CSV 필수 칼럼:
- CREATE — `name`, `structure`
- UPDATE — `segment_id`, `structure`
- MIXED — `name`, `structure` (segment_id 있으면 PUT)

선택 칼럼: `description`, `rsid`, `tags`.

`structure` 칼럼은 ` | ` 구분 한 줄 DSL 형식 (aa_segment_lookup 출력과 동일). → 형식 예: `segments_input_example.csv`

### AA validator patch (컴파일 결과 → AA 호환)

| 변환 | 효과 |
|---|---|
| `event<N> exists` → `event-exists` | metric 컨테이너의 exists 함수 거부 우회 |
| `<varname> event-exists` 일반화 | `evar<N>instances event-exists` 도 처리 |
| `@<seg_id>` segment-ref auto-fetch + 캐시 | AA GET 으로 sub-segment 받아 inline 박음 → `segment_ref_cache.json` 저장 |
| `not '<container>'!hit(...)` → de Morgan | parser 가 NOT named container 미지원 |
| 단독 paren grouping `( ... )` 제거 | input_csv_maker 가 visit/visitor 모드에 추가하는 외부 grouping paren |
| stdout utf-8 reconfigure | Windows cp949 콘솔에서도 한글·em dash 안 깨짐 |

### segment-ref 캐시

- 위치: 같은 폴더 `segment_ref_cache.json` 또는 `segment_ref_cache_<name>.json` (`--cache <name>` 옵션)
- 동작: 첫 실행 시 auth 로 GET → cache 저장. 이후 실행은 cache hit 이면 auth 없이도 DSL 변환
- 강제 갱신: cache 파일 삭제 후 재실행

### dry-run CSV

`--apply` 안 줘도 자동 생성: `segment_v2.3_result_<ts>_dryrun.csv` (Name / Mode / ParseStatus / Error). 어떤 row 가 어떤 에러인지 한 눈에 식별.

## aa_segment_lookup.py 사용법

```bash
# ID 직접 지정 (여러 개 공백 구분)
python aa_segment_lookup.py sXXXXXXXXX_YOUR_ID sXXXXXXXXX_YOUR_ID2
python aa_segment_lookup.py --from-file segment_ids.txt

# 이름 키워드 검색 (1 개)
python aa_segment_lookup.py --search "campaign"
python aa_segment_lookup.py --search "campaign" --rsid sscompany_name4mstglobal

# 이름 키워드 AND 검색 (공백 구분)
python aa_segment_lookup.py --search "[us] p" "visit"      # 이름에 "[us] p" 와 "visit" 둘 다
python aa_segment_lookup.py --search "[campaign name]" "US_CC" --limit 2000

# 날짜 필터 (수정일 modified 기준 — AA 가 생성일 미제공. YYYY-MM-DD)
python aa_segment_lookup.py --search "campaign" --modified-after 2025-01-01 --modified-before 2025-07-01
```

검색 동작:
- 각 키워드(**첫 키워드 포함**)를 이름(+설명)에 **연속 substring** 으로 AND 매칭 (대소문자 무시).
  → `"[us] p"` 는 공백까지 통째로 한 substring (Adobe 토큰 필터처럼 단어로 안 쪼갬).
- 상단 상수 `SEARCH_RESULT_LIMIT`(기본 1000, `--limit` 로 덮어씀). 결과가 limit 초과 시 **경고 출력**(조용한 절단 없음).
- ⚠ AA 세그먼트 API 가 **생성일(created)을 제공하지 않아 마지막 수정일(`modified`) 기준** 으로 날짜 필터.

출력 (코드 폴더의 `lookup/` 하위에 생성):
- `lookup/segment_lookup_<ts>.csv` — `segment_id, name, owner_id, owner_name, owner_email, rsid, modified, description, tags, structure`
- `lookup/segment_lookup_<ts>.dsl` — 역변환된 DSL 멀티라인 (v2.3 입력으로 재사용 가능)

> **sequence dimension-restriction round-trip (2026-07-08)**: sequence THEN 스텝 사이 "within N \<dim\>" 제약(AA `dimension-restriction`)을 DSL `WITHIN N <dim>` 스텝으로 표기 (예: `WITHIN 1 page`). `aa_create_segment_v2.3` 이 되읽어 재생성 → lookup↔maker 왕복. 차원은 조건문과 같은 short var(`page`) 표기. (sequence label strip 도 `hit`/`visit`/`visitor` scope 전부 처리하도록 일반화.)

## aa_segment_lookup_from_pjt.py 사용법

project ID 만 넣으면 그 project 가 의존하는 모든 segment 의 structure 까지 한 번에 추출.

```bash
python aa_segment_lookup_from_pjt.py <project_id>
python aa_segment_lookup_from_pjt.py <project_id> --include-disabled
python aa_segment_lookup_from_pjt.py YOUR_PROJECT_ID --suffix pjt
```

출력 포맷은 `aa_segment_lookup.py` 와 동일 (`lookup/` 하위에 `segment_lookup_pjt_<ts>.csv` / `.dsl`).

## input_csv_maker.py — raw → v2.3 input CSV 자동 변환

raw `seg_make_ref_*.csv` (사람이 손으로 작성한 segment 명세) → v2.3 가 받는 `segments_input_<ts>.csv` + `.dsl` + `_WARN.csv` 로 변환.

```bash
python input_csv_maker.py                                # 폴더의 최신 seg_make_ref_*.csv 자동 pick
python input_csv_maker.py --input seg_make_ref_X.csv     # 특정 파일 강제 지정
```

raw CSV 컬럼 패턴:

| 컬럼 | 의미 |
|---|---|
| `Segment Name` | 출력 segment 이름 (변환 룰: `NAME_CAMPAIGN_BEFORE` → `NAME_CAMPAIGN_AFTER` 로 캠페인 prefix 교체) |
| `customlink` | customlink 컨테이너 값. `COMPONENT_LABEL_FROM_CUSTOMLINK=True`(기본) 면 선두코드 기반 라벨 `'cc04 component'!hit(customlink starts-with '<val>')`. **줄바꿈 multi-value** 면 한 컨테이너에 OR 로 묶고 라벨은 코드 조합 |
| `eVar<N>_event-exists` (TRUE/FALSE) + `eVar<N>` (값들, 줄바꿈 multi) | 메인 evar 블록 빌드 |
| `<cond>_crystallize_evar<N>` | 자동 LCS override (LCS 못 잡거나 짧을 때 사용자가 직접 keyword). `<cond>` = `starts-with` / `starts` / `equals` / `contains-any-of` / `contains` |
| `prop<N>` / `evar<N>` / `not_prop<N>` / `not_evar<N>` | site 필터 (default `starts-with`) |
| `<starts\|contains\|equals>_(prop\|evar)<N>` / `not_<cond>_(prop\|evar)<N>` | 새 명시 형식 |

변환 룰 요약:
- **그룹화**: 같은 `Segment Name` row 들의 customlink 블록을 OR 로 묶어 1 개 segment
- **SCOPE_MODE = "both"**: 한 그룹당 `visit(...)` + `hit(...)` 2 개 segment 자동 생성 (이름 suffix `(Visit)` / 없음)
- **공통 ref**: `@<COMMON_SEGMENT_REF>` 를 visit/visitor 모드에서 AND 로 묶음 (hit 모드는 안 묶음)
- **LCS 자동 추출**: evar 값들 (줄바꿈 split) 의 공통 substring ≥ `MIN_LCS_LENGTH(4)` → `evar<N> contains '<lcs>'`. 미만이면 `contains-any-of [...]`

출력 3 파일:

| 파일 | 용도 |
|---|---|
| `segments_input_<ts>.csv` | v2.3 가 받는 형식 (형식 예: `segments_input_example.csv`) |
| `segments_input_<ts>.dsl` | 시각 확인용 멀티라인 (형식 예: `segments_input_example.dsl`) |
| `segments_input_<ts>_WARN.csv` | 같은 customlink 가 여러 segment 에 쓰이는 case 경고 |

### 전체 흐름

```
raw seg_make_ref_*.csv (사람 작성)
   ↓ input_csv_maker.py
segments_input_<ts>.csv  +  .dsl (시각 확인)  +  _WARN.csv (검수)
   ↓ (필요시 수동 편집 → segments.csv 등 이름 변경)
aa_create_segment_v2.3.py --input segments.csv
   ↓ dry-run → segment_v2.3_result_<ts>_dryrun.csv
   ↓ --apply → AA POST/PUT + segment_v2.3_result_<ts>.csv (형식 예: segments_result_example.csv)
```

## aa_delete_segment.py — 안전 삭제

```bash
# (1) Preview — 삭제 후보만 목록 출력 (실제 삭제 X)
python aa_delete_segment.py
python aa_delete_segment.py --from-csv result_YYMMDD_HHMM.csv

# (2) 실제 삭제 — preview OK 일 때 --yes 추가
python aa_delete_segment.py --yes
```

3중 안전장치:
1. result CSV 기반 — CSV 에 기록된 SegmentId 만 대상 (직접 ID/와일드카드 입력 불가)
2. 이름 prefix 검증 — 삭제 직전 GET 으로 실제 이름 확인. `SAFE_NAME_PREFIX` (기본 `_test_`) 로 시작 안 하면 자동 skip
3. `--yes` 플래그 게이트 — 없으면 preview 만

`--from-csv` 생략 시 같은 폴더의 `result_*.csv` 중 가장 최신 1 개 자동 선택.

PowerShell 주의: `--yes` 는 따옴표 밖에 둘 것.
