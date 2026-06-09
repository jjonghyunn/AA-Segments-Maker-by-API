# segment_share/ — segment 키워드 매칭 → 일괄 share 추가 (운영 사본)

repo: https://github.com/jjonghyunn/AA-Segments-Maker-by-API/tree/main/segment_share (예정)

운영 사본 — 실제 KEYWORDS / SHARE_USER_IDS / AUTH 경로 박혀있는 작업본. generic 변경은 repo 사본에 동기화.

기준 문서 업데이트일: 2026-05-21

---

## 용도

본인이 owner 인 segment 중 이름 (또는 description) 에 특정 키워드가 들어간 것들을 한 번에 골라 여러 사람에게 share 추가. 운영 시나리오:

> 분석 시즌 시작 시 동일 라벨이 붙은 운영용 segment 들을 팀원들에게 일괄 공유 → 매 segment UI 들어가서 share 메뉴 클릭 안 해도 됨.

## 현재 운영 설정 (add_segment_shares.py 상단 상수 기준)

| 상수 | 값 |
|---|---|
| `AUTH_JSON_PATH` | `C:\Users\YOUR_USER\OneDrive - YOUR_COMPANY\your_folder\aanalyticsact_auth.json` |
| `COMPANY_ID` | `your_aa_company_id` |
| `RSID` | `""` (전체 RSID — 빈 문자열이면 server-side RSID 필터 안 함) |
| `OWN_LOGIN_ID` | `YOUR_LOGIN_ID` (User 1 — 본인 owner segment 만 client-side 필터) |
| `KEYWORDS` | `["visit"]` (name/description AND substring 매칭, case-insensitive) |
| `OWNER_ID_FILTER` | `[]` (비어있으면 미사용) |
| `OWNER_FULLNAME_INCLUDES` | `[]` (비어있으면 미사용) |
| `TARGET_SEGMENT_IDS` | (코드 안 `TARGET_SEGMENT_IDS_RAW` 한 줄에 하나씩 박기 — 빈 리스트면 매칭 전체 대상) |
| `SHARE_USER_IDS` | 본인 + 7명 (총 8명) |

### SHARE_USER_IDS 멤버

| email | loginId | fullName |
|---|---|---|
| user1@company_name.com | YOUR_LOGIN_ID | User 1 |
| user2@company_name.com | YOUR_LOGIN_ID | User 2 |
| user3@company_name.com | YOUR_LOGIN_ID | User 3 |
| user4@company_name.com | YOUR_LOGIN_ID | User 4 |
| user5@company_name.com | YOUR_LOGIN_ID | User 5 |
| user6@company_name.com | YOUR_LOGIN_ID | User 6 |
| user7@company_name.com | YOUR_LOGIN_ID | User 7 |
| user8@... | YOUR_LOGIN_ID | User 8 |

ID lookup 은 상위 폴더의 `company_name_aa_id_*.csv` 자동 pick (`AA_USER_CSV` — 가장 최신 timestamp). `find_user_id.py --all --csv ...` 로 갱신.

## 실행

```powershell
cd "C:\Users\YOUR_USER\OneDrive - YOUR_COMPANY\your_folder\your_workspace\AA_segment_maker\segment_share"

# 1) Dry-run — 매칭 segment 목록 + segment 별 실제 추가될 user id 미리보기 + segments_matched_*.csv 생성
python add_segment_shares.py

# 2) 실제 적용 — dry-run 출력 후 input() 으로 y/N 한 번 더 확인 받음
python add_segment_shares.py --apply
```

## 권장 워크플로우 (TARGET_SEGMENT_IDS 활용)

1. **첫 실행 (dry-run)** → `segments_matched_YYMMDD_HHMM.csv` 생성
2. CSV 열어 share 추가할 segment 의 `SegmentId` 컬럼 값 복사 → 코드의 `TARGET_SEGMENT_IDS_RAW` 안에 한 줄씩 박기
3. `--apply` 로 다시 실행 → 그것만 share 추가
4. `TARGET_SEGMENT_IDS_RAW` 안 라인 앞에 `#` 붙이면 해당 id 만 제외 (주석 처리)

## 출력 예시 (dry-run)

```
[2026-05-21 19:00:00] segment share 일괄 추가 도구
  KEYWORDS         : ['visit']
  RSID 필터        : ''  (전체)
  추가할 user id   : [YOUR_LOGIN_ID, YOUR_LOGIN_ID, ..., YOUR_LOGIN_ID]

GET /segments (본인 owner) ...
  본인 owner segment 총 N개

KEYWORDS=['visit'] 매칭 segment: M개

--- 매칭된 segment 목록 (첫 5개) ---
  #  segment id                    rsid                name
----------------------------------------------------------------------------------------
  1  s200001591_xxxxxxxx...        sscompany_name4mstglobal [part_name] Campaign Visit
  ...

--- 변경 미리보기 (segment 별 추가될 user id) ---
  + s200001591_xxxxxxxx...  [part_name] Campaign Visit               추가: [YOUR_LOGIN_ID, YOUR_LOGIN_ID, ...]
  · s200001591_zzzzzzzz...  [part_name] Already Shared               이미 모두 share 됨 (skip)
  ...

--- 합계 ---
  매칭 segment    : M개
  share 추가 대상 : K개 (나머지는 이미 share 되어있어 skip)

ℹ️ Dry-run 모드 — 실제 PUT 안 함. 적용하려면 --apply
```

`--apply` 모드면 위 출력 후 `진행하려면 'y' 또는 'yes' 입력:` 프롬프트 → y 입력 시 PUT 시작.

## 안전장치

| 장치 | 내용 |
|---|---|
| 본인 owner 만 | `OWN_LOGIN_ID` 와 일치하는 segment 만 client-side 필터 (다른 사람 owner 면 PUT 403 사전 방지) |
| 매칭 목록 항상 출력 | 키워드 매칭된 segment 목록을 항상 먼저 보여줘서 "이 세그들이 맞는지" 확인 |
| 기존 shares 보존 | 새 user id 만 append. 기존 share 는 그대로 |
| 중복 ID 제거 | 이미 share 된 ID 는 추가 안 함 (변경 0 인 segment 는 skip) |
| Dry-run 기본 | `--apply` 명시 안 하면 PUT 안 함 |
| input() confirm | `--apply` 모드에서도 y/N 한 번 더 |
| TARGET_SEGMENT_IDS | 명시적 화이트리스트 — 키워드 매칭으로 광범위하게 잡힌 것 중 진짜 share 할 것만 추려서 박기 |

## 키워드 / 인원 변경할 때

코드 상단 상수 수정:

- `KEYWORDS` — 매칭할 키워드 리스트 (AND substring 매칭). 첫 키워드는 server-side `name` 파라미터로도 사용 → 가장 specific 한 키워드를 앞에
- `SHARE_USER_IDS` — 추가할 numeric loginId 리스트
- `RSID` — RSID 필터 (`""` 이면 전체 RSID)
- `OWNER_ID_FILTER` / `OWNER_FULLNAME_INCLUDES` — 본인 외 segment 도 처리하려면 (admin 권한 필요)

## 자매 도구

- `../utils/find_user_id.py` — email/login/name 으로 numeric loginId 찾기
- `../company_name_aa_id_*.csv` — 회사 전체 user id 매핑 (`find_user_id.py --all --csv ...` 로 생성)
- `../segment_maker/aa_create_segment_v2.2.py` — segment 생성 시 `OWNER_ID` 설정으로 본인 명의 보장
