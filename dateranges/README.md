# dateranges/ — Adobe Analytics Date Range 일괄 도구 모음

회사 전체 Date Range 컴포넌트를 **조회 / 갱신 / 생성 / upsert** 하는 4종 도구. 단일 record CRUD 는 상위 폴더의 `aa_daterange.py` 사용.

기준 문서 업데이트일: 2026-05-14

## 도구 비교

| 도구 | API | 용도 |
|---|---|---|
| `aa_dateranges_list.py` | GET `/dateranges?includeType=all` | 회사 전체에서 이름 키워드로 골라서 ID·정의 추출 |
| `aa_dateranges_update.py` | GET + PUT `/dateranges/{id}` | 사이트별 6값 입력 → 매칭되는 기존 daterange 일괄 갱신 |
| `aa_dateranges_create.py` | POST `/dateranges` | 사이트별 6값 입력 → 새 daterange 일괄 생성 |
| `aa_dateranges_upsert.py` | GET + PUT + POST | 사이트별 6값 입력 → fetch 결과와 매칭해서 **이미 있으면 UPDATE / 없으면 CREATE** 자동 분류. `--apply` 전 분류 결과 출력 + y/N input() confirm |

## 공통 입력 — 사이트별 6값

`aa_dateranges_update.py` / `aa_dateranges_create.py` / `aa_dateranges_upsert.py` 공통:

```
SITE, THIS_START, THIS_END, LAST_START, LAST_END, BEFORE_BASE
```

| 값 | 의미 |
|---|---|
| `SITE` | 사이트 코드 (예: US, MX, DE) |
| `THIS_START` / `THIS_END` | 올해 캠페인 시작·종료 (ISO YYYY-MM-DD) |
| `LAST_START` / `LAST_END` | 작년 같은 캠페인 시작·종료 |
| `BEFORE_BASE` | '이전 전체' daterange 의 baseline 시작점. 캠페인·시점마다 다름 (예: MD=2024-04-01, SW=2024-05-01) |

입력 소스 우선순위:
1. 같은 폴더의 CSV (Excel 편집)
   - update 도구: `dateranges_sites_input.csv`
   - create 도구: `dateranges_sites_input_create.csv` (별도 파일 — 보통 update 와 다른 캠페인 값)
2. 1순위 파일 없으면 코드 안 `SITES_INLINE` dict (fallback)

## 8개 daterange 자동 산출 공식 (사이트당, 글로벌 `THIS_YEAR_YY`/`LAST_YEAR_YY`/`CAMPAIGN_TAG` 사용)

| # | 이름 | 시작 | 종료 |
|---|---|---|---|
| 1 | `[<S> <yy> <T>]` | `THIS_START` | `THIS_END` |
| 2 | `[<S> <yy> <T> 이전 전체]` | `BEFORE_BASE` | `THIS_START - 1d` |
| 3 | `[<S> <yy> <T> 직전 4주]` | `THIS_START - 28d` | `THIS_START - 1d` |
| 4 | `[<S> <yy> <T> 직전 4주 이전 전체]` | `BEFORE_BASE` | `THIS_START - 29d` |
| 5 | `[<S> <yy> <T> 포함 이전 전체]` | `BEFORE_BASE` | `THIS_END` |
| 6 | `[<S> <ly> <T>]` | `LAST_START` | `LAST_END` |
| 7 | `[<S> <ly> <T> 이전 전체]` | `BEFORE_BASE` | `LAST_START - 1d` |
| 8 | `[<S> <ly> <T> 포함 이전 전체]` | `BEFORE_BASE` | `LAST_END` |

`<S>` = 사이트, `<yy>`=THIS_YEAR_YY (올해 두자리), `<ly>`=LAST_YEAR_YY, `<T>`=CAMPAIGN_TAG (예: MD, SW, BF, CAMPAIGN NAME)

→ 8 사이트 × 8 타입 = 64개 daterange 자동 생성/갱신.

## 1. aa_dateranges_list.py

**read-only** 도구. 회사 전체 Date Range 일괄 조회 + 이름 키워드 필터.

### 사용

```powershell
python aa_dateranges_list.py            # 콘솔 + CSV 출력
python aa_dateranges_list.py --no-csv   # CSV 비활성, 콘솔만
```

### 동작

1. `GET /dateranges?includeType=all&limit=1000&page=N` 페이지네이션 (회사 전체 23k+ 1-2분 소요)
2. 각 record 의 `name` 을 `NAME_INCLUDES` 키워드와 부분 일치(대소문자 무시) 검사
3. 매칭 결과를 콘솔 표 + `dateranges_filtered_{ts}.csv` 로 저장

### 출력 CSV 컬럼

`MatchedKeyword / Id / Name / Definition / OwnerId / OwnerLogin / OwnerName / Modified / Description / Tags`

### 흔한 함정

- `totalElements: 0` 나오면 → `INCLUDE_TYPE = "all"` 누락 (default 는 본인 소유만)
- 매칭 0건 → NAME_INCLUDES 띄어쓰기/대소문자 차이 (실제로는 substring 매칭)

## 2. aa_dateranges_update.py

기존 Date Range 들을 사이트별 6값으로 산출한 새 정의로 일괄 PUT.

### 사용

```powershell
python aa_dateranges_update.py            # dry-run (PUT body 만 출력)
python aa_dateranges_update.py --apply    # 실제 PUT
```

### 동작

1. `dateranges_sites_input.csv` (또는 `SITES_INLINE`) 읽음
2. 글로벌 상수 (`THIS_YEAR_YY`, `LAST_YEAR_YY`, `CAMPAIGN_TAG`) + 위 8개 산출 공식 → 64개 (이름, definition) 매핑 산출
3. 회사 전체 `GET /dateranges?includeType=all` fetch
4. 이름 prefix `[...]` 가 산출 매핑과 일치하는 daterange 매칭 (같은 이름 다중 owner 면 각각)
5. 매칭마다 PUT body 생성 — 기존 owner/tags/description 보존, name·definition 만 덮어씀
6. dry-run 이면 결과 CSV 만, `--apply` 면 실제 PUT 수행

### 출력 CSV 컬럼

`MatchKey / Id / OwnerLogin / OwnerId / OwnerName / CurrentName / NewName / CurrentDefinition / NewDefinition / Status / ResponseCode / ErrorMessage`

### Status 값

| Status | 의미 |
|---|---|
| `WOULD_UPDATE` (dry-run) | 변경 예정 |
| `NO_CHANGE` (dry-run) | 새 값과 기존 값이 동일 → PUT 안 함 |
| `OK` (apply) | PUT 성공 (200/201/204) |
| `FAIL` (apply) | PUT 실패 — `ResponseCode`/`ErrorMessage` 확인 |
| `NO_CHANGE_SKIP` (apply) | 동일 값이라 PUT 자동 스킵 |
| `SKIP_OWNER_FILTER` (apply) | `APPLY_OWNER_FILTER` 에 안 걸려서 skip |

### 권한 — owner 가 다른 사람일 때

- Date Range 는 보통 동료들이 만든 것을 공유해서 씀 → fetch 결과 owner 가 본인 아닌 경우 흔함
- PUT 시 owner ≠ 본인 + admin 권한 없으면 **403** 가능
- `APPLY_OWNER_FILTER`:
  - `"all"` (default): 매칭된 모든 daterange 시도 (admin 권한 있을 때)
  - `"self"`: 본인 owner 만 (자동 lookup via `/users/me`)
  - 정수 loginId: 그 owner 만
- 첫 실행 권장: dry-run 으로 owner 분포 확인 → admin 권한 있으면 `--apply` 진행

## 3. aa_dateranges_create.py

새 Date Range 들을 사이트별 6값으로 산출한 정의로 일괄 POST.

### 사용

```
python aa_dateranges_create.py            # dry-run (POST body 만 출력)
python aa_dateranges_create.py --apply    # 실제 POST
```

### 동작

1. `dateranges_sites_input_create.csv` (또는 `SITES_INLINE`) 읽음 — update 와 다른 별도 파일
2. 글로벌 상수 + 8개 산출 공식 → N×8 (이름, definition) 매핑 산출
3. owner 결정 — `NEW_OWNER_ID=0` 이면 GET `/users/me` 자동 lookup
4. dry-run 이면 결과 CSV 만, `--apply` 면 실제 POST 수행

### 차이점 (update 대비)

- fetch + 매칭 단계 없음 → 빠름 (POST 만 N×8 회)
- 본인이 owner 가 됨 → admin 권한 의존 없음
- 결과 CSV 에 새로 생성된 ID 들이 기록됨 (`CreatedId` 컬럼)

### 출력 CSV 컬럼

`MatchKey / NewName / NewDefinition / OwnerId / Status / ResponseCode / CreatedId / ErrorMessage`

### Status 값

| Status | 의미 |
|---|---|
| `WOULD_CREATE` (dry-run) | 생성 예정 |
| `OK` (apply) | POST 성공 (200/201) — `CreatedId` 에 새 ID |
| `FAIL` (apply) | POST 실패 — `ResponseCode`/`ErrorMessage` 확인 |
| `EXCEPTION` (apply) | 네트워크 등 예외 |

### ⚠️ 중복 생성 주의

같은 이름의 daterange 가 이미 있어도 POST 는 새로 생성됨 (Adobe API 동작) → **동일 이름 중복 가능**. 실수로 두 번 돌리지 않도록:
- 첫 실행은 항상 dry-run
- `--apply` 후 결과 CSV 보관 (다시 돌릴지 판단 자료)
- 이미 만들어진 캠페인을 갱신하려면 `aa_dateranges_update.py` 사용

### owner 처리 (`POST_TRANSFER_TO_OWNER_ID`)

Adobe API 는 POST 시 `owner.id` 를 명시해도 **token holder 로 강제 덮어씀** (실측 확인 — 본인 토큰으로 POST 하면 무조건 본인 owner). 다른 사람 owner 로 만들고 싶으면 POST 후 PUT 으로 owner 이전 필요.

| `POST_TRANSFER_TO_OWNER_ID` | 동작 |
|---|---|
| `0` (기본) | POST 만 — owner = 본인 (token holder) |
| `<numeric loginId>` | POST 직후 자동 PUT 으로 owner 이전 (admin 권한 필요) |

예) user2_login 명의로 일괄 생성하려면: `POST_TRANSFER_TO_OWNER_ID = YOUR_LOGIN_ID` 설정 후 `--apply`. 결과 CSV 의 `TransferStatus` 컬럼이 `TRANSFERRED` 로 떨어짐.

## 자매 도구

- 상위 폴더 `aa_daterange.py` — 단일 record CRUD (CREATE 또는 UPDATE, DATERANGE_ID 토글)
- 상위 폴더 `compare_panel_segments.py` — 두 Workspace panel 의 segment 차집합 비교 (참고용)
