# segment_maker/  
<sub>2026-05-22  Jonghyun Park w/ Claude</sub>

Adobe Analytics 세그먼트 생성·조회·삭제 도구 모음.

## 파일 목록

| 파일 | 용도 |
|---|---|
| `aa_create_segment_v2.py` | v2 — 구조(DSL) 텍스트 → AA JSON 변환 + 다중 세그먼트 일괄 생성. `@segment_id` 참조 지원 |
| `aa_create_segment_v2.2.py` | **v2.2 (권장)** — CSV 입력 + AA validator 호환 patch 자동화 (event-exists / segment-ref auto-fetch + cache / NOT container / grouping paren / dry-run CSV) |
| `input_csv_maker.py` | raw `seg_make_ref_*.csv` → v2.2 input CSV + `.dsl` + `_WARN.csv` 자동 변환. LCS 추출 / crystallize override / 양수·음수 site 필터 / 그룹화 |
| `input_csv_maker_us.py` | input_csv_maker 의 US 캠페인 파생 — flat 구조 / `evar<N>instances event-exists` 패턴 / 큰따옴표 / `sscompany_namenewus` RSID |
| `input_csv_maker_cc00.py` | input_csv_maker 사본 — segment-ref 두 개를 visit scope 안 AND 로 묶은 단일 segment 만 생성 (사본 패턴 예시) |
| `input_csv_maker_or_ref.py` | input_csv_maker 변형 — 기존 segment-ref 들을 OR 로 묶는 룰 |
| `input_csv_maker_scenario.py` | input_csv_maker 변형 — 시나리오 단계별 segment 생성 |
| `input_csv_maker_replace.py` | input_csv_maker 변형 — 기존 ref 의 일부 element 를 replace |
| `input_csv_maker_from_ref.py` | 기존 segment-ref CSV 에서 v2.2 input 다시 생성 |
| `input_csv_maker_from_ref_batch.py` | from_ref 의 batch 처리 (여러 ref 묶음 한번에) |
| `input_csv_maker_from_ref_batch_us_hit.py` | from_ref_batch 의 US + hit container 변형 |
| `aa_segment_lookup.py` | 세그먼트 ID 조회 또는 이름 키워드 검색 → CSV (기본 정보 + structure 칼럼) + `.dsl` (구조 역변환) |
| `aa_segment_lookup_from_pjt.py` | project 안 panel·reportlet 이 참조하는 segment 들을 일괄 lookup |
| `aa_delete_segment.py` | result CSV 기반 안전 삭제 (3중 안전장치: CSV 강제, 이름 prefix 확인, `--yes` 필수) |
| `example_segment_campaign_main_page.py` | 복잡 조건 세그먼트 정의 예시 (or/and/without/contains-any-of) |
| `prewarm_seg_ref_cache.py` | `segment_ref_cache.json` 사전 갱신 — v2.2 dry-run 빠르게 (auth 없이) |
| `segment_ref_cache.json` | v2.2 가 자동 생성·갱신하는 segment-ref inline 캐시 |
| `old/aa_create_segment.py` | v1 — CSV 기반 단건 생성 (deprecated, v2.2 사용 권장) |
| `old/aa_create_segment_v2_1.py` | v2.1 — CSV 입력 + 생성/업데이트 (deprecated, v2.2 superset) |
| `lookup/` | **`aa_segment_lookup*` 결과 폴더** — 두 lookup 도구가 떨어뜨리는 `segment_lookup_*.csv` / `.dsl` / `segment_lookup_pjt_*.csv` / `.dsl` 가 여기로 모임. `aa_create_segment_v2.2.py --lookup-by-name` / `input_csv_maker_from_ref_batch.py` / `input_csv_maker_replace.py` 의 fallback 입력 source. |

> variant 들 (`input_csv_maker_*`) 은 base `input_csv_maker.py` 를 캠페인·구조별로 커스터마이즈한 사본. 새 캠페인 추가 시 base 에서 사본 복사 → 룰 차이만 수정. 상세 동작은 각 파일 상단 docstring 참조.

## v2 구조 문법 요약

```
hit(                          # 컨테이너 (hit/visit/visitor)
  page contains "keyword"
  AND NOT prop29 contains-any-of ["a", "b"]
)
OR
'Named'!hit(                  # 이름 지정 컨테이너
  evar22 = "value"
)
AND @s200001591_abc123def456  # 기존 세그먼트 참조
```

### sequence (THEN) 지원

```
visit(
  page contains "campaign"
  THEN
  event1 exists
)
```

### NOT 복합조건

```
NOT (
  prop29 contains-any-of ["a", "b"]
  OR
  prop39 contains "prize"
)
```

### 다중 세그먼트 입력

```
--- segment
name: Segment Name
description: 설명
rsid: sscompany_name4mstglobal
tags: [tag1, tag2]

hit( page contains "keyword" )

--- segment
name: Another Segment
...
```

### CLI

```bash
python aa_create_segment_v2.py                    # dry-run (JSON 미리보기)
python aa_create_segment_v2.py --apply            # 실제 POST
python aa_create_segment_v2.py --input my.dsl     # 입력 파일 지정
python aa_create_segment_v2.py --decompile seg.json  # AA JSON → 구조 텍스트
```

## aa_segment_lookup.py 사용법

```bash
# ID로 조회
python aa_segment_lookup.py s200001591_abc123 s200001591_def456
python aa_segment_lookup.py --from-file segment_ids.txt

# 이름 키워드 검색
python aa_segment_lookup.py --search "campaign"
python aa_segment_lookup.py --search "campaign" --rsid sscompany_name4mstglobal
python aa_segment_lookup.py --search "campaign" --limit 100
```

출력 (모두 `lookup/` 폴더로 떨어짐 — 코드 폴더 정리용):
- `lookup/segment_lookup_YYMMDD_HHMM.csv` — segment_id, name, owner, rsid, description, tags, structure
- `lookup/segment_lookup_YYMMDD_HHMM.dsl` — 역변환된 구조 텍스트 (v2 입력으로 재사용 가능)

## aa_segment_lookup_from_pjt.py 사용법

project 의 panel·reportlet 들이 참조하는 segment 들을 walk 해서 일괄 lookup. project ID 만 넣으면 그 project 가 의존하는 모든 segment 의 structure 까지 한 번에 추출.

```bash
python aa_segment_lookup_from_pjt.py --project YOUR_ID
```

출력: `lookup/segment_lookup_pjt_YYMMDD_HHMM[_suffix].csv` / `.dsl`

## v2.2 — CSV 입력 + AA validator patch 자동화 (권장)

v2.1 의 한계를 보완한 production 버전. CSV 한 장이면 생성·업데이트 다 됨.

```bash
python aa_create_segment_v2.2.py --input segments.csv                # dry-run + dryrun CSV
python aa_create_segment_v2.2.py --input segments.csv --apply        # 실제 POST
python aa_create_segment_v2.2.py --input segments.csv --update --apply   # PUT 업데이트
```

생성: CSV 필수 칼럼 `name`, `structure`  
업데이트: CSV 필수 칼럼 `segment_id`, `structure` (segment_id 모르면 `aa_segment_lookup.py --search "이름"`)  
공통 선택 칼럼: `description`, `rsid`, `tags`

`structure` 칼럼은 ` | ` 구분 한 줄 형식 (aa_segment_lookup CSV 출력과 동일).

### v2.2 변경점 (vs v2.1)

| 변경 | 효과 |
|---|---|
| `--input` 경로 fallback | cwd 기준 못 찾으면 스크립트 폴더 (`segment_maker/`) 기준 자동 재시도 |
| stdout utf-8 reconfigure | Windows cp949 콘솔에서도 한글·em dash 안 깨짐 |
| **Dry-run 결과 CSV** 자동 생성 | `--apply` 안 줘도 `segment_v2.2_result_<ts>_dryrun.csv` 떨어짐 (Name / Mode / ParseStatus / Error) |
| **`event<N> exists` → AA `event-exists` patch** | v2 컴파일 결과 `{"func":"exists","val":event}` 를 `{"func":"event-exists","evt":event}` 로 변환 (AA validator 가 metric 에 `exists` 거부) |
| **`<varname> event-exists` → `<varname> exists` 일반화** | regex 가 `event<N>` 만이 아니라 모든 변수명 매칭 (US 의 `evar<N>instances event-exists` 도 처리) |
| **`<X>instances` attr → metric event-exists patch** | v2 가 attr (`variables/<X>instances`) 로 컴파일한 노드 → `event-exists` func + `evt` 키 + `metrics/<X>instances` 로 변환 (US instance metric 케이스) |
| **`@<seg_id>` segment-ref auto-fetch + 캐시** | AA GET `/segments/{id}` 으로 sub-segment container 받아 inline 박음. `segment_ref_cache.json` 에 저장 → 다음 실행부터 auth 없이도 변환 |
| **`not '<container>'!hit(...)` → `NOT (...)` 변환** | parser 가 NOT named container 미지원이라 de Morgan 으로 풀어줌 |
| **`NOT (cond)` 한 줄 인라인 → 멀티라인 펼침** | `(evar26 contains '...)` 형태로 변수명에 paren 섞이는 parser 버그 회피 |
| **단독 paren grouping `( ... )` 제거** | input_csv_maker 가 visit/visitor 모드에 추가하는 외부 grouping paren — parser 가 raw grouping 미지원 |

### v2.2 처리 흐름

```
segments.csv (structure 칼럼)
   ↓ _structure_to_dsl  (event-exists, NOT 컨테이너, grouping paren 등 DSL 변환)
멀티라인 DSL
   ↓ parse_dsl + compile_to_definition  (v2 모듈)
v2 JSON
   ↓ _patch_definition_for_aa  (event-exists evt 키, segment-ref auto-fetch + cache)
AA 호환 JSON
   ↓ dry-run 이면 → dryrun csv + JSON 미리보기, --apply 면 → POST/PUT + result csv
```

### segment-ref 캐시

- 위치: `segment_ref_cache.json` (segment_maker 폴더)
- 동작: 첫 실행 시 auth 로 GET → cache 저장. 이후 실행은 cache hit 이면 auth 없이도 변환
- 사전 갱신: `prewarm_seg_ref_cache.py` 로 한 번에 다 채워두면 dry-run 빠름
- 갱신: AA 에서 sub-segment 정의가 바뀌면 cache 파일 삭제 후 재실행

### v2.2 의 dry-run csv

`--apply` 안 줘도 자동 생성. 16 row 입력이면 16 row 결과 (ParseStatus = `OK` 또는 `PARSE_ERROR`). 어떤 row 가 어떤 에러인지 한 눈에 식별.

## input_csv_maker.py — raw → v2.2 input CSV 자동 변환

operations 단계에서 raw `seg_make_ref_*.csv` (사람이 손으로 작성한 segment 명세) → v2.2 가 받는 `segments_input_<ts>.csv` + `.dsl` + `_WARN.csv` 로 변환.

### 사용

```bash
python input_csv_maker.py                                # 폴더의 최신 seg_make_ref_*.csv 자동 pick
python input_csv_maker.py --input seg_make_ref_X.csv     # 특정 파일 강제 지정
```

### 입력 CSV 컬럼 패턴

| 컬럼 | 의미 |
|---|---|
| `Segment Name` | 출력 segment 이름 (변환 룰: `[CAMPAIGN NAME]` → `[CAMPAIGN NAME]` 등) |
| `customlink` | customlink 컨테이너 값. `COMPONENT_LABEL_FROM_CUSTOMLINK=True`(기본) 면 선두코드 기반 라벨 `'pd25 component'!hit(customlink starts-with '<val>')`, `False` 면 `'Component'!hit(...)`. **줄바꿈 multi-value** 면 한 컨테이너에 OR 로 묶고 라벨은 코드 조합 (`'pd25 or ft31 component'`, v1.7) |
| `eVar<N>_event-exists` (TRUE/FALSE) + `eVar<N>` (값들, 줄바꿈 multi) | 메인 evar 블록 빌드. event-exists 컬럼 있는 evar 번호 자동 인식 |
| `starts_crystallize_evar<N>` | 자동 LCS 못 잡거나 짧을 때 사용자가 직접 keyword 박는 override |
| `prop<N>` / `evar<N>` / `not_prop<N>` / `not_evar<N>` | site 필터 (default `starts-with`). prop1, evar1 같이 한쪽 site 식별 변수 |
| `<starts\|contains\|equals>_(prop\|evar)<N>` / `not_<cond>_(prop\|evar)<N>` | 새 명시 형식. `not_contains_evar26 = ':tab'` 같이 inline 제외 |

### 변환 룰

- **그룹화**: 같은 `Segment Name` row 들의 customlink 블록을 OR 로 묶어 1 개 segment
- **SCOPE_MODE = "both"**: 한 그룹당 `visit(...)` + `hit(...)` 2 개 segment 자동 생성 (이름 suffix `(Visit)` / 없음)
- **공통 ref**: `@<COMMON_SEGMENT_REF>` 를 visit/visitor 모드에서 AND 로 묶음 (hit 모드는 안 묶음)
- **LCS 자동 추출**: evar 값들 (줄바꿈 split) 의 공통 substring ≥ `MIN_LCS_LENGTH(4)` → `evar<N> contains '<lcs>'`. 미만이면 `'특이사항'!hit(... contains-any-of [...])`
- **prop/evar generic 인식**: 1~200 어느 번호든. event-exists 있으면 메인 evar 블록 안 inline, 없으면 site 컨테이너로
- **컨테이너 라벨** (v1.5/v1.6, `COMPONENT_LABEL_FROM_CUSTOMLINK=True` 기본): 브랜치 customlink 컨테이너를 선두코드(`'pd25 component'` / `'co78 component'`), 바깥 segment 컨테이너를 코드 ` or ` 조합(`'pd25 or co78 component'`)으로 명명. `False` 면 기존 `'Component'` + segment 이름
- **Delayed Purchase 그룹핑** (v1.4): 같은 segment 의 2 개+ customlink 브랜치(OR)를 `( hit(A) OR hit(B) )` 괄호 그룹으로 묶어 모든 브랜치가 `@COMMON_REF` 페이지필터 / `THEN` 시퀀스 안에 포함 (이전엔 래퍼 평탄화로 두 번째 브랜치가 빠지던 버그 수정)

### 출력 3 파일

| 파일 | 용도 |
|---|---|
| `segments_input_<ts>.csv` | v2.2 가 받는 형식 (segment_id, name, description, rsid, tags, structure, warning) |
| `segments_input_<ts>.dsl` | 시각 확인용 멀티라인 (들여쓰기 + 괄호 fold) |
| `segments_input_<ts>_WARN.csv` | 같은 customlink 가 여러 segment 에 쓰이거나 cl+eVar 조합 정확 일치 case 경고 (site 필터로 분리되면 `note: "실질 분리됨"`) |

### 전체 흐름

```
raw seg_make_ref_*.csv (사람 작성)
   ↓ input_csv_maker.py (또는 _us / _cc00 / _or_ref / _scenario / _replace / _from_ref(_batch) variant)
segments_input_<ts>.csv  +  .dsl (시각 확인)  +  _WARN.csv (검수)
   ↓ (필요시 수동 편집 → segments.csv 등 이름 변경)
aa_create_segment_v2.2.py --input segments.csv
   ↓ dry-run → segment_v2.2_result_<ts>_dryrun.csv
   ↓ --apply → AA POST + segment_v2.2_result_<ts>.csv
```

## VS Code 에서 `.dsl` fold (접기/펼치기) 활성화

`input_csv_maker.py` / `aa_segment_lookup.py` 가 만드는 `.dsl` 파일은 기본적으로 VS Code 에서 plain text 로 인식되어 fold 마커가 안 보임. file association 한 줄만 추가하면 indent / bracket 단위 fold + 색상 활성화.

### 설정 파일 경로

```
C:\Users\<사용자명>\AppData\Roaming\Code\User\settings.json
```

VS Code 안에서 직접 열기: `Ctrl+Shift+P` → `Preferences: Open User Settings (JSON)`.

### 추가할 키

settings.json 의 최상위 `{ ... }` 객체 안에 (기존 마지막 키 줄 끝 `,` 콤마 주의):

```json
"files.associations": {
    "*.dsl": "lisp"
}
```

저장 즉시 적용 (VS Code 재시작 불필요). 이미 열려 있는 `.dsl` 파일은 한 번 닫았다가 다시 열기.

### 매핑 값 비교 — `"lisp"` 자리 후보

| 값 | fold 방식 | 비고 |
|---|---|---|
| `"lisp"` | paren `()` 단위 + bracket pair color | nested `()` 구조에 가장 자연스러움 |
| `"python"` | indent 기반 | 색상 친숙, 일부 syntax noise |
| `"yaml"` | indent 기반 | syntax noise 거의 없음 |
| `"coffee"` | indent 기반 | 비교적 관대 |
| `"plaintext"` | 매핑 효과 사라짐 | (원래대로 돌리는 셈) |

자세한 셋업·단축키·제거 방법·다른 PC 셋업 → [`00.vscode_dsl_fold.txt`](./00.vscode_dsl_fold.txt)
