# segment_maker/  
<sub>2026-08-03  Jonghyun Park w/ Claude</sub>  

Adobe Analytics 세그먼트 생성·조회·삭제 도구 모음.

---

## 0. 전체 흐름

흐름이 **두 층**입니다. 캠페인 시작 때 한 번 하는 **최초 구축(0-2)** 과,
세그를 만들거나 고칠 때마다 반복하는 **세그 제작 3단계(0-1)** 입니다.

### 0-1. 세그 제작 3단계

```bash
# 1) 참조 세그 캐시 준비 — input maker 와 같은 단계
python prewarm_seg_ref_cache.py --cache <name>    # 캠페인 폴더 도구 (본 repo 미포함)

# 2) input maker — 글로벌 / US 각각
python input_csv_maker.py        # → segments_input_<ts>.csv + .dsl + _WARN.csv
python input_csv_maker_us.py     # → US 판 (RSID·ref 세그가 다름)

# 3) 세그 생성 / 갱신
python aa_create_segment_v2.4.py --input segments_input_<ts>.csv --apply            # 1차 = create
python aa_create_segment_v2.4.py --input segments_input_<ts>.csv --update --apply   # 2차~ = update
```

| # | 단계 | 코드 | 산출물 |
|---|---|---|---|
| 1 | 참조 세그 캐시 준비 | `prewarm_seg_ref_cache.py` (repo 미포함) 또는 v2.4 dry-run | `segment_ref_cache_<name>.json` |
| 2 | input maker (글로벌/US) | `input_csv_maker.py` / `input_csv_maker_us.py` | `segments_input_<ts>.csv` + `.dsl` + `_WARN.csv` |
| 3 | 세그 생성/갱신 | `aa_create_segment_v2.4.py` | AA POST/PUT + `segment_result_<ts>.csv` |

#### 1단계 — 캐시 준비는 왜 input maker 와 같은 단계인가

input maker 는 참조 세그를 **id 가 아니라 이름으로** 찾습니다.
`REF_SEGMENT_NAME` / `ATC_REF_SEGMENT_NAME` 에 이름 조각을 박아두면
`_lookup_seg_ref_by_name()` 이 **캐시의 `name` 필드**를 partial 매칭해 segment id 를 자동으로 채웁니다.

그런데 **`aa_create_segment_v2.4.py` 가 자동으로 채운 캐시 항목은 `name` 이 빈 값**입니다
(AA GET 으로 container 만 받아 `{"container": …, "name": ""}` 로 저장). 즉 **이름으로는 못 찾습니다.**
`prewarm_seg_ref_cache.py` 만 `name` / `description` / `rsid` 메타까지 채워 넣습니다.

> 그래서 캐시 준비는 "세그 생성 전 준비"가 아니라 **input maker 를 돌리기 위한 준비**입니다.
> 캐시 없이 input maker 를 돌리면 `[ref lookup]` 매칭 실패로 참조 세그가 통째로 빠진 채 CSV 가 나옵니다.
> 캐시 없이 가려면 `COMMON_SEGMENT_REF` / `ATC_VISIT_SEGMENT_REF` 에 segment id 를 직접 박으면 됩니다.

캐시 파일 형식 — **`name` 이 채워져 있어야 이름 lookup 이 걸립니다**:

```json
{
  "YOUR_SEGMENT_ID": {
    "container": { "func": "container", "context": "hits", "pred": { "…": "…" } },
    "name": "[Global] Add to Cart Visit",
    "description": "",
    "rsid": "YOUR_RSID"
  }
}
```

캠페인 하나에 보통 캐시가 **4개** 필요합니다 — (글로벌 / US) × (캠페인 메인 페이지 참조 / Add-to-Cart 참조).
`delayed_purchase` scope 를 쓸 때만 Add-to-Cart 쪽이 필요합니다.

3단계의 `aa_create_segment_v2.4.py` 는 상단 `CACHE_NAME` 에 **필요한 캐시를 콤마로 모두 박아** merge load 합니다.
**첫 번째 파일이 save target** 이라, 실행 중 새로 fetch 한 항목은 전부 첫 캐시에 쌓입니다.

#### 2단계 — 글로벌 / US 는 코드가 다릅니다

| | 글로벌 | US |
|---|---|---|
| 코드 | `input_csv_maker.py` | `input_csv_maker_us.py` |
| `REF_SEGMENT_NAME` | 캠페인에 따라 (안 쓰면 빈 값) | US 판 캠페인 메인 페이지 세그 |
| `ATC_REF_SEGMENT_NAME` | `[Global] Add to Cart Visit` | `[US] Add to Cart Visit` |
| `CACHE_NAME` | 글로벌 캐시 2개 | US 캐시 2개 |
| RSID | 글로벌 suite | US suite |
| `SCOPE_MODE` | `visit,hit,delayed_purchase` | 동일 |

한 캠페인에서 **두 코드를 각각 돌려 input CSV 를 2개** 만듭니다.
`SCOPE_MODE` 에 3개가 들어 있으면 그룹 1개당 세그가 **3개**(`(Visit)` / 접미사 없는 hit / `(Delayed Purchase)`) 나옵니다.

#### 3단계 — 1차는 create, 2차부터는 update

| 차수 | 명령 | CSV 필수 컬럼 | 동작 |
|---|---|---|---|
| **1차** | `--apply` | `name`, `structure` | POST (신규 생성) |
| **2차~** | `--update --apply` | `segment_id`, `structure` | PUT (덮어쓰기) |
| 섞임 | `--update-or-create --apply` | `name`, `structure` | id 있으면 PUT, 없으면 POST |

> ⚠ 2차 update 전에 `aa_segment_lookup.py --search "…"` 로 `lookup/` csv 를 **최신화**해야 합니다.
> `--lookup-by-name`(기본 켜짐)이 그 csv 에서 이름으로 `segment_id` 를 채우기 때문에,
> csv 가 오래됐으면 엉뚱한 세그를 덮어씁니다.

> ⚠ update 시 **owner 는 건드리지 않는 것이 기본**입니다. `OWNER_ID` 를 새로 박으면
> 남의 소유 세그를 가져오는 셈이 되고, 보통 admin 권한이 없으면 조용히 무시됩니다.

### 0-2. 최초 구축 7단계

캠페인을 처음 세팅할 때의 순서입니다. **이 폴더가 담당하는 건 3~6단계**입니다.

| # | 단계 | 방식 | 산출물 |
|---|---|---|---|
| 1 | 태깅 디버깅 | 거의 자동 (디버깅 툴) | 콘텐츠별 클릭 콜 목록 |
| 2 | **디버깅 검수 + 누락 콜 추가** | **수기** | 보정된 콜 목록 |
| 3 | input maker ref 제작 | 수기 → API 참조용 | `seg_make_ref*.csv` **← 이 폴더** |
| 4 | 디폴트 세그 제작 | API | 기본 콘텐츠(CC_xx) 세그 **← 이 폴더** |
| 5 | Visit / Delayed Order 세그 제작 | API (**eVar 필요**) | `(Visit)` / `(Delayed Purchase)` 세그 **← 이 폴더** |
| 6 | **세그 검수** | **수기** | 확정 세그 **← 이 폴더** |
| 7 | 데이터 추출·정제 | `data_extract/` | union CSV |

- **3단계**는 사람이 손으로 쓰는 명세입니다. 여기서 정한 customlink·eVar 값이 그대로 세그 정의가 됩니다.
- **4·5단계가 곧 위 0-1 의 3단계 루프**입니다 (캐시 준비 → input maker → v2.4).
  5단계는 eVar suite 가 필요해서 `--to-evar` 로 prop → evar 변환을 거칩니다.
- **6단계 검수를 건너뛰면 7단계 수치가 통째로 어긋납니다.** 반드시 거칠 것.
- 7단계는 `data_extract/` 소관입니다.

### 0-3. 세그 3종 구조 — 일반 / (Visit) / (Delayed Purchase)

`SCOPE_MODE = "visit,hit,delayed_purchase"` 면 콘텐츠 하나당 세그가 3개 나옵니다.
**셋은 같은 "조건 블록"을 공유하고, 감싸는 컨테이너와 결합 방식만 다릅니다.**

#### 공통 조건 블록 (= 접미사 없는 일반 세그 그 자체)

> `⟨n⟩` 은 **짝이 맞는 괄호 쌍** 표시입니다 — 여는 줄과 닫는 줄에 같은 번호가 붙습니다.
> (에디터에서 괄호를 색으로 보려면 이 예시를 `.dsl` 로 저장 — 맨 아래 VS Code fold 섹션 참고)

```
hit(                                                                      ⟨1⟩ scope = hit
  'cc04 component'!hit(                                                   ⟨2⟩ 바깥 컨테이너 (라벨 = 선두코드)
    hit(                                                                  ⟨3⟩ 조건 묶음 — ①~④ 전부 AND
      'cc04 component'!hit( customlink starts-with 'cc04_<content>' )     ① 클릭 콜
      AND
      'v26'!hit( event26 event-exists AND evar26 starts-with '<kw>' )     ② 배너 식별 (eVar)
      AND
      'site'!hit( prop1 starts-with 'es' OR evar1 starts-with 'es' )      ③ site 포함
      AND
      not 'site'!hit( prop1 starts-with 'uk' OR evar1 starts-with 'uk' )  ④ site 제외
    )                                                                     ⟨3⟩
  )                                                                       ⟨2⟩
)                                                                         ⟨1⟩
```

- **scope = `hit`** — 적중 1건 안에서 ①~④ 가 **전부 AND**
- **site 조건만 OR** (`prop1` 또는 `evar1` 중 하나만 맞아도 그 site)
- customlink 브랜치가 2개 이상이면 브랜치끼리는 **OR** 로 묶여 `( hit(A) OR hit(B) )` 그룹이 됩니다

#### (Visit) — 위 블록을 `visit()` 로 감싸고 **캠페인 메인 페이지 참조를 AND**

```
visit(                          ⟨1⟩ scope = visit   ← 일반 세그와 다른 점 1
  'page+content'!hit(           ⟨2⟩ 메인페이지 + 콘텐츠
    @<캠페인 메인 페이지 세그>  ← 다른 점 2 (프리웜 캐시에서 이름으로 찾은 참조)
    AND
    (                           ⟨3⟩ ↓ 일반 세그의 ⟨2⟩ 블록을 그대로
      <공통 조건 블록>
    )                           ⟨3⟩
  )                             ⟨2⟩
)                               ⟨1⟩
```

일반 세그와의 차이는 **딱 2개** — scope 가 `visit`, 메인 페이지 필터가 AND 로 추가.
뜻은 "그 **방문**에서 캠페인 메인 페이지를 봤고 + 그 콘텐츠를 클릭했다".

#### (Delayed Purchase) — 지연 전환. **THEN 두 번**으로 엮인 visitor 시퀀스

```
[sequence-after] visitor(                                  ⟨1⟩ 방문자 시퀀스 (After Sequence)
  visit(                                                   ⟨2⟩ ── 첫 방문 ──
    '<Visit 세그 이름>'!visit(                             ⟨3⟩ = Visit 세그 조건 그대로
      hit( @<메인 페이지 세그> AND ( <공통 조건 블록> ) )  ⓐ 콘텐츠 클릭
      THEN
      '[Global] Add to Cart Visit'!hit( @<ATC 세그> )      ⓑ 장바구니 담기
    )                                                      ⟨3⟩
    AND
    'Order (All Products)'!hit( NOT orders event-exists )  ⓒ 이 방문엔 주문 없음
  )                                                        ⟨2⟩
  THEN
  visit(                                                   ⟨4⟩ ── 이후 방문 ──
    'Order (All Products)'!hit( orders event-exists )      ⓓ 주문 발생
  )                                                        ⟨4⟩
)                                                          ⟨1⟩
```

읽는 순서: **ⓐ → ⓑ (같은 방문 안, `THEN`)** · 그 방문은 **ⓒ 주문 없음(`AND`)** · **→ ⓓ 나중 방문에서 주문(`THEN`)**.
`[sequence-after]` 는 Adobe UI 의 "After Sequence" (raw `sequence-prefix`) 입니다.

> 위 예시는 **AA UI 에서 보이는 대로** 적었습니다. `aa_segment_lookup.py` 로 역변환하면
> 맨 바깥에 조건 없는 `hit( … )` 한 겹이 더 붙어 나오는데, **읽을 때는 무시해도 됩니다** —
> AA 가 이 유형을 `container{ context:"hits", pred:{ func:"sequence-prefix", … } }` 로 저장해서
> 그 root container 가 DSL 로 풀린 것뿐입니다 (full `sequence` 는 hit-scope 가 거부되고
> `sequence-prefix` 만 허용되는 validator 룰 때문). **입력으로 넣을 땐 그 껍데기가 있어야 합니다.**

#### 한 눈에 비교

| | scope | 메인 페이지 ref | 결합 방식 |
|---|---|---|---|
| **일반** (접미사 없음) | `hit` | 없음 | 조건 전부 `AND` (site 만 `prop OR evar`) |
| **(Visit)** | `visit` | `AND` 로 추가 | 공통 블록 그대로 |
| **(Delayed Purchase)** | `visitor` 시퀀스 | Visit 블록 안에 포함 | 클릭 **THEN** 카트 → **AND** 주문없음 → **THEN** 다음 방문 주문 |

> 메인 페이지 참조와 ATC 참조는 **둘 다 프리웜 캐시에서 이름으로 찾아 박히는 것**입니다 (0-1 의 1단계).
> `delayed_purchase` 를 쓰면 캐시가 반드시 있어야 하는 이유가 이것입니다.

---

## 파일 목록

| 파일 | 용도 |
|---|---|
| `aa_create_segment_v2.4.py` | **권장** — CSV 입력 + AA validator 호환 patch 자동화. CREATE / UPDATE / mixed (`--update-or-create`) 지원 |
| `aa_segment_lookup.py` | 세그먼트 ID 또는 이름 키워드 검색 → CSV (기본 정보 + owner 이름/이메일 + structure 칼럼) + `.dsl` (DSL 역변환). v2.4 가 받는 입력 형식과 동일. 결과는 `lookup/` 하위 |
| `aa_segment_lookup_from_pjt.py` | project 안 panel·reportlet 이 참조하는 segment 들을 일괄 lookup → CSV + DSL (`lookup/` 하위) |
| `aa_delete_segment.py` | result CSV 기반 안전 삭제 (3중 안전장치: CSV 강제, 이름 prefix 검증, `--yes` 필수) |
| `input_csv_maker.py` | raw `seg_make_ref_*.csv` → v2.4 input CSV + `.dsl` + `_WARN.csv` 자동 변환. LCS 추출 / crystallize override / 양수·음수 site 필터 / 그룹화 |
| `input_csv_maker_us.py` / `input_csv_maker_from_ref_batch.py` | `input_csv_maker.py` 의 variant — US 룰 / from_ref 일괄 변환 룰 차이 |

## 예시 파일 (형식 참고용)

| 파일 | 보여주는 것 |
|---|---|
| `segments_input_example.csv` | v2.4 가 받는 입력 CSV 형식 (`structure` 칼럼은 ` \| ` 구분 한 줄 DSL) |
| `segments_input_example.dsl` | 같은 내용의 멀티라인 DSL (시각 확인용) |
| `segments_result_example.csv` | `--apply` 실행 결과 CSV 형식 (`Action` = create/update, `Status`, `SegmentId` …) |

> 예시 파일의 캠페인명·ID·RSID 는 placeholder (`[CAMPAIGN NAME]`, `YOUR_ID`, `YOUR_SEGMENT_ID`, `sscompany_name4mstglobal`). 실제 환경 값으로 바꿔 사용.

## 구조(DSL) 문법 요약

`structure` 칼럼(한 줄 ` | ` 구분) 또는 `.dsl` 파일에 쓰는 세그먼트 **조건 문법** 요약. `aa_segment_lookup` 출력 DSL 과 동일 문법이라 lookup→maker 왕복(round-trip) 가능.

```
hit(                          # 컨테이너 (hit/visit/visitor)
  page contains "keyword"
  AND NOT prop29 contains-any-of ["a", "b"]
)
OR
'Named'!hit(                  # 이름 지정 컨테이너
  evar22 = "value"
)
AND @YOUR_SEGMENT_ID          # 기존 세그먼트 참조 (@<segment_id>)
```

### sequence (THEN)

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

### 다중 세그먼트 입력 (.dsl)

여러 세그먼트를 `--- segment` 구분선으로 한 파일에 나열:

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

> 조건 연산자·부정(not-*) 패밀리·sequence 표기 등은 `aa_segment_lookup` 출력 DSL 과 동일 문법 (lookup↔maker 왕복).

## aa_create_segment_v2.4.py — CSV 입력 + AA validator patch 자동화 (권장)

CSV 한 장이면 생성·업데이트 다 됨.

```bash
python aa_create_segment_v2.4.py --input segments.csv                # dry-run + dryrun CSV
python aa_create_segment_v2.4.py --input segments.csv --apply        # 실제 POST (CREATE)
python aa_create_segment_v2.4.py --input segments.csv --update --apply              # PUT (모든 row update)
python aa_create_segment_v2.4.py --input segments.csv --update-or-create --apply    # mixed — id 있으면 PUT, 없으면 POST
python aa_create_segment_v2.4.py --input segments.csv --to-evar --apply             # (v2.4) prop/page → evar 변환 후 생성/업데이트
```

`--input` 비우면 폴더의 최신 `segments_input_*.csv` 자동 pick.
`--lookup-by-name` (default True) — `--update-or-create` 모드에서 segment_id 빈 row 는 폴더의 `segment_lookup_*.csv` 에서 name 매칭으로 자동 채움.

**(v2.4) prop/page → evar 변환** — `--to-evar` (또는 상단 `CONVERT_TO_EVAR=True`): 정의의 prop/page 디멘션을 evar 로 remap 후 생성/업데이트 (op·값·구조 보존, prop 키워드만 evar 로). 특수 페어링은 상단 `EVAR_SPECIAL_MAP`(예: `page→evar40`, `prop29→evar92`), 그 외 `prop{N}→evar{N}`(`EVAR_DEFAULT_PROP_TO_EVAR`). 변환 세그의 report suite 변경은 `EVAR_TARGET_RSID`, 변환 세그 이름 끝에 붙일 접미사는 `EVAR_NAME_SUFFIX`(기본 `"_Evar"`, 빈 값이면 미적용). ⚠ 페어링은 suite(회사)별로 다르니 상단 상수에서 확정할 것.

> ⚠ **`--update` 는 owner 변경 시 대화형 확인을 받는다.** 기존 owner 와 `OWNER_ID` 가 다르면 세그마다 `owner {현재} -> {새값} 로 바뀝니다. 정말 진행? (Y/N)` 프롬프트가 뜨고, `y` 가 아니면 그 세그는 `SKIP(owner)` 로 건너뛴다 (남의 세그 owner 탈취 방지). **무인/스케줄 실행이면 stdin 에서 멈추므로**, owner 를 안 바꿀 거면 `OWNER_ID = None` 으로 두어 게이트 자체가 안 걸리게 할 것.

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

- 위치: 같은 폴더 `segment_ref_cache_<name>.json` (`--cache <name>` 또는 상단 `CACHE_NAME`. 콤마로 다중 지정 → 모두 merge load, **첫 파일이 save target**)
- 동작: **캐시 우선** — 캐시에 있으면 AA 조회 없이 그대로 쓰고, 없을 때만 GET → 캐시에 추가
- 강제 갱신: 해당 항목 또는 cache 파일 삭제 후 재실행

> ⚠ **매번 새로 조회하지 않습니다.** `@<segment_id>` 는 (a) 캐시에서 정의를 꺼내 (b) **inline 으로 펼쳐 박습니다**
> (AA 가 POST 에서 `segment-ref` func 을 거부하므로 참조 링크가 아니라 정의 복사본이 들어감).
> 결과적으로 **두 겹으로 굳습니다** —
> 1. 캐시가 있으면 AA 에서 원본 세그를 고쳐도 **옛 정의**가 박힌다
> 2. 이미 만들어진 세그는 원본 세그와 링크가 없어, 나중에 원본을 고쳐도 **자동으로 안 따라간다**

**원본 세그가 바뀌었을 때 (중요)**

1. 캐시에서 **그 항목(또는 캐시 파일)을 지운다**
2. `prewarm_seg_ref_cache.py --cache <name>` **재실행** → 새 정의를 다시 받음
3. 그 참조를 쓰는 **파생 세그들을 `--update` 로 다시 돌린다** (이미 만들어진 세그는 자동 반영 X)

> ⚠ **1번을 건너뛰고 프리웜만 다시 돌리면 아무 일도 안 일어납니다** — 프리웜은
> **이미 캐시에 있는 id 를 건너뜁니다**. 반드시 **먼저 지우고** 재실행할 것.

### dry-run CSV

`--apply` 안 줘도 자동 생성: `segment_result_<ts>_dryrun.csv` (Name / Mode / ParseStatus / Error). 어떤 row 가 어떤 에러인지 한 눈에 식별.

> 접두는 코드 상단 `RESULT_CSV_PREFIX` 로 정합니다. **버전 번호를 뺀 `segment_result_`** 라
> 코드 버전을 올려도 파일명이 갈리지 않습니다. (2026-08-03 이전 실행분은 `segment_v2.2_result_*` 이름)

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
- `lookup/segment_lookup_<ts>.dsl` — 역변환된 DSL 멀티라인 (v2.4 입력으로 재사용 가능)

> **sequence dimension-restriction round-trip (2026-07-08)**: sequence THEN 스텝 사이 "within N \<dim\>" 제약(AA `dimension-restriction`)을 DSL `WITHIN N <dim>` 스텝으로 표기 (예: `WITHIN 1 page`). `aa_create_segment_v2.4` 이 되읽어 재생성 → lookup↔maker 왕복. 차원은 조건문과 같은 short var(`page`) 표기. (sequence label strip 도 `hit`/`visit`/`visitor` scope 전부 처리하도록 일반화.)
>
> **AA native 부정(not-*) func 패밀리 (2026-07-08)**: AA 네이티브 부정 func 을 읽기 쉬운 DSL 로 표기하고 maker 가 네이티브 func 그대로 되읽음(`without` 래핑 아님): `not-streq`→`not-equals`, `not-streq-in`→`not-equal-any-of`/`not-in`, `not-contains`, `not-contains-any-of`, `not-starts-with`, `not-ends-with`, `not-exists`. 예: `evar73 not-equal-any-of ["0","NA",...]`, `page not-starts-with "in:"`, `evar40 not-exists`. (별개 미처리 gap: `event-exists`, `eq`.)

## aa_segment_lookup_from_pjt.py 사용법

project ID 만 넣으면 그 project 가 의존하는 모든 segment 의 structure 까지 한 번에 추출.

```bash
python aa_segment_lookup_from_pjt.py <project_id>
python aa_segment_lookup_from_pjt.py <project_id> --include-disabled
python aa_segment_lookup_from_pjt.py YOUR_PROJECT_ID --suffix pjt
```

출력 포맷은 `aa_segment_lookup.py` 와 동일 (`lookup/` 하위에 `segment_lookup_pjt_<ts>.csv` / `.dsl`).

## input_csv_maker.py — raw → v2.4 input CSV 자동 변환

raw `seg_make_ref_*.csv` (사람이 손으로 작성한 segment 명세) → v2.4 가 받는 `segments_input_<ts>.csv` + `.dsl` + `_WARN.csv` 로 변환.

```bash
python input_csv_maker.py --input seg_make_ref_X.csv     # 파일 지정 (권장)
python input_csv_maker.py                                # 상단 SEG_MAKE_REF_CSV 값 사용
```

입력 파일은 **`--input` → 상단 `SEG_MAKE_REF_CSV` → 자동 선택** 순으로 정해집니다.

| 상황 | 동작 |
|---|---|
| `--input` 지정 | 그 파일 사용. 없으면 **에러 후 중단** |
| `--input` 없고 `SEG_MAKE_REF_CSV` 에 값 있음 | 그 파일 사용. 없으면 **에러 후 중단** (자동 선택으로 안 넘어감) |
| 둘 다 비어 있음 | 폴더에서 자동 선택 — **`seg_make_ref_<YYMMDD>_<HHMM>.csv` 패턴만** (파일명 사전순 최신 1개) |

> ⚠ 자동 선택은 **숫자로 시작하는 timestamp 파일만** 후보로 봅니다.
> `seg_make_ref_us_*.csv` / `_scenario_*.csv` / `_or_*.csv` 같은 파생과 `_tmp.` 이 붙은 건 **제외**됩니다.
> (사전순 정렬을 쓰는 이유: OneDrive 동기화·복사로 mtime 이 어긋날 수 있어서)

> `SEG_MAKE_REF_CSV` 기본값은 **빈 값**이라 그냥 돌리면 자동 선택으로 갑니다 (2026-08-03 변경).
> 여기에 파일명을 박으면 그 파일이 없을 때 **자동 선택으로 안 넘어가고 에러로 끝납니다** — 주의.

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
- **SCOPE_MODE**: 콤마 구분 (`visit,hit,delayed_purchase`). 한 그룹당 모드 수만큼 segment 자동 생성 (이름 suffix `(Visit)` / 없음 / `(Delayed Purchase)`). 옛 표기 `"both"` = `visit,hit`
- **공통 ref**: `@<COMMON_SEGMENT_REF>` 를 visit/visitor 모드에서 AND 로 묶음 (hit 모드는 안 묶음)
- **LCS 자동 추출**: evar 값들 (줄바꿈 split) 의 공통 substring ≥ `MIN_LCS_LENGTH(4)` → `evar<N> contains '<lcs>'`. 미만이면 `contains-any-of [...]`

출력 3 파일:

| 파일 | 용도 |
|---|---|
| `segments_input_<ts>.csv` | v2.4 가 받는 형식 (형식 예: `segments_input_example.csv`) |
| `segments_input_<ts>.dsl` | 시각 확인용 멀티라인 (형식 예: `segments_input_example.dsl`) |
| `segments_input_<ts>_WARN.csv` | 같은 customlink 가 여러 segment 에 쓰이는 case 경고 |

### 전체 흐름

```
raw seg_make_ref_*.csv (사람 작성)
   ↓ input_csv_maker.py
segments_input_<ts>.csv  +  .dsl (시각 확인)  +  _WARN.csv (검수)
   ↓ (필요시 수동 편집 → segments.csv 등 이름 변경)
aa_create_segment_v2.4.py --input segments.csv
   ↓ dry-run → segment_result_<ts>_dryrun.csv
   ↓ --apply → AA POST/PUT + segment_result_<ts>.csv (형식 예: segments_result_example.csv)
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
2. 이름 prefix 검증 (opt-in) — 삭제 직전 GET 으로 실제 이름 확인. `SAFE_NAME_PREFIX` (기본 `""` = 검증 비활성) 에 값을 박으면 그 prefix 로 시작 안 하는 segment 는 자동 skip. `--safe-prefix "<PREFIX>"` 로도 override
3. `--yes` 플래그 게이트 — 없으면 preview 만

`--from-csv` 생략 시 같은 폴더의 `result_*.csv` / `test_result_*.csv` 중 가장 최신 1 개 자동 선택.

> ⚠ create 출력 파일(`segment_result_*.csv`)은 `segment_` 로 시작해 이 자동 선택(`result_*` glob)에 **안 잡힌다** — create → delete 를 이어 돌릴 땐 `--from-csv` 로 파일을 직접 지정할 것.

PowerShell 주의: `--yes` 는 따옴표 밖에 둘 것.
