# project_share_owner/ — 프로젝트(Workspace) 일괄 share / owner 이관  
<sub>2026-08-31  Jonghyun Park w/ Claude</sub>  

repo: https://github.com/jjonghyunn/AA-Segments-Maker-by-API/tree/main/project_share_owner

Adobe Analytics **프로젝트(Workspace)** 를 여러 사람에게 일괄 share 하거나 owner(주인)를 이관하는 도구.
상단 `MODE` 상수 하나로 동작이 바뀐다.

`segment_share/add_segment_shares.py`(segment share) 와
segment owner 이관 도구의 **프로젝트 버전**이다. 로컬 운영 사본엔 실제 project id·loginId 를 박아 쓰지만
**이 repo 사본은 placeholder** — generic 변경은 repo 사본에도 동기화.

---

## MODE

| 값 | 동작 | 필요한 상수 |
|---|---|---|
| `"share"` | 공유만 (`POST /componentmetadata/shares`) | `SHARE_USER_IDS` |
| `"owner"` | owner 이관만 (`PUT /projects/{id}` 의 `owner.id`) | `NEW_OWNER_ID` |
| `"both"` | **owner 이관 후 공유** — 이관 뒤에도 본인·팀이 조회 가능하도록 | 둘 다 |

`MODE` 가 세 값 외면 시작 시 종료. `owner` 포함인데 `NEW_OWNER_ID` 가 `None`,
`share` 포함인데 `SHARE_USER_IDS` 가 비어 있어도 안내 후 종료 (인증 전에 걸러진다).

---

## 상단 설정 상수

| 상수 | repo 기본값 / 설명 |
|---|---|
| `AUTH_JSON_PATH` | `C:\path\to\your\aanalytics_auth.json` — OAuth S2S auth json |
| `COMPANY_ID` | `your_aa_company_id` — global company id |
| `MODE` | `"share"` (위 표 참조) |
| `TARGET_PROJECT_IDS_RAW` | 대상 project id 를 한 줄에 하나씩. 빈 줄 무시, `#` 로 시작하면 제외. **id 가 있으면 이게 우선** |
| `NAME_KEYWORDS` | `[]` — id 를 안 줄 때 이름/설명 **AND** substring 매칭 (case-insensitive) |
| `NEW_OWNER_ID` | `None` — 새 owner 의 numeric loginId (`utils/find_user_id.py` 로 조회) |
| `SHARE_USER_IDS` | `[]` — share 대상 loginId. **본인도 포함**(owner 본인만으론 조회가 막히는 경우가 있어 최소 본인 share 1건 필요) |
| `PRINT_FIRST_N` | `5` — 콘솔에 보여줄 매칭 개수 (전체는 CSV) |
| `BACKUP_BEFORE_PUT` | `True` — owner PUT 전 GET 응답 JSON 을 파일로 저장 |
| `PUT_STRIP_KEYS` | owner PUT 시 body 에서 덜어낼 read-only / expansion 전용 키 |

> project id = AA URL 의 `/workspace/projects/<id>` 부분 (24-hex).

---

## 실행

```bash
python project_share_owner.py            # dry-run — 대상 목록 + 변경 미리보기만
python project_share_owner.py --apply    # 미리보기 → y/N confirm → 실제 적용
```

안전장치 2단: **매칭 목록을 먼저 보여주고**(CSV + 콘솔), `--apply` 에서도 키보드 confirm 을 한 번 더 받는다.

### 출력물 (스크립트와 같은 폴더)

| 파일 | 내용 |
|---|---|
| `projects_matched_YYMMDD_HHMM.csv` | 매칭된 프로젝트 (id, 이름, owner, 기존 share) — dry-run 에서도 생성 |
| `project_backup_<id>_YYMMDD_HHMM.json` | owner PUT **직전** 의 프로젝트 GET 응답 원본 |
| `project_share_owner_YYMMDD_HHMM.csv` | 실행 결과 (owner before/after, owner_ok, shares_added, error) |

---

## 동작 상세

1. **대상 선정** — `TARGET_PROJECT_IDS` 가 있으면 각 id 를 단건 GET, 없으면 `/projects?includeType=all` 전량 페이징 후 `NAME_KEYWORDS` 로 client-side AND 매칭.
   `/segments` 와 달리 server-side `name` 필터를 쓰지 않는다 — 프로젝트 수는 segment 보다 훨씬 적어 전량 페이징이 부담 없다.
2. **미리보기** — 프로젝트별로 `owner 현재→새 값` 과 `share 추가 예정 id` 를 출력. 이미 share 된 id 는 자동 제외.
3. **owner 이관** — `GET /projects/{id}?expansion=definition,...` → 백업 JSON 저장 → `owner` 만 교체하고 `PUT_STRIP_KEYS` 만 덜어낸 **나머지 그대로** PUT → read-back 으로 `owner.id` 확인.
4. **share** — 미공유 id 마다 `POST /componentmetadata/shares` 단건
   (`{shareToId, shareToType:"user", componentType:"project", componentId}`).
5. `both` 는 **owner 먼저, share 나중**.

---

## 주의 (실패하기 쉬운 지점)

- **`definition` 을 반드시 함께 PUT 해야 한다.** 빠지면 워크스페이스 내용이 날아간다.
  그래서 이 도구는 필드를 새로 조립하지 않고 **GET 응답을 통째로 PUT** 하고,
  PUT 후 read-back 의 panel/reportlet 개수를 PUT 전과 비교해 보존을 검증한다 (다르면 경고 + 실패 카운트).
- **owner 변경은 보통 admin 권한이 필요**하다. 권한이 없으면 200 을 받고도 조용히 안 바뀔 수 있어
  read-back 검증이 필수 — 미변경 건은 `owner 미변경(현재 …)` 로 결과 CSV·콘솔에 남는다.
- **share 는 `PUT` 으로 보내면 silently drop** 된다. `componentmetadata/shares` 엔드포인트로만 적용된다
  (segment 에서 확인된 동작).
- **`componentType="project"` 는 2026-08-31 실측 확인됨** (share POST 200 / owner PUT 도 통과,
  definition 바이트 동일 보존). 그래도 400 이 나면 응답 본문이 그대로 출력되니
  그 메시지를 보고 `SHARE_COMPONENT_TYPE` 상수를 조정할 것.

---

## 권장 검증 순서 (처음 쓸 때)

1. dry-run 으로 대상·미리보기 확인 (쓰기 없음).
2. `MODE="share"` + 안전한 프로젝트 1개 + `SHARE_USER_IDS` 에 본인 1명만 → `--apply`.
   (`componentType="project"` 자체는 2026-08-31 실측 통과 — 계정 권한 확인용 단계)
3. AA UI 에서 공유 상태 확인 후 대상 인원을 넓혀 재실행.
4. `MODE="owner"` 는 잃어도 되는 테스트용 프로젝트 1개로 먼저 — read-back owner 확인 +
   백업 JSON 과 panel/reportlet 개수 비교로 워크스페이스 보존 확인.
