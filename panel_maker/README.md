# panel_maker/ — AA Workspace project panel 복제 + segment swap  
<sub>2026-07-28  Jonghyun Park w/ Claude</sub>  

source project 의 panel 구조를 그대로 빈 target project 로 복제하면서 그 안의 segment ID 를 새 캠페인 segment 로 자동 swap. `SOURCE_PROJECT_ID` / `TARGET_PROJECT_ID` / `OLD_KEYWORDS` / `NEW_KEYWORDS` 등 환경 값은 스크립트 상단 상수로 본인 환경에 맞게 교체.

---

## 용도

source project 의 panel(들) 을 미리 만들어둔 빈 target project 로 복제 + 그 안에 박혀있는 segment ID 들을 NEW 키워드 계열의 segment 로 자동 swap. 캠페인 갈아끼울 때 panel 구조 통째로 재사용:

> `[CAMPAIGN NAME]` 캠페인 프로젝트의 패널 구조 + 리포틀렛 + 차트 layout 그대로 → `[NEW CAMPAIGN NAME]` 같은 새 캠페인 프로젝트에 복제 + 캠페인 prefix 만 자동 교체.

UI 에서 한 panel 씩 손으로 복제+segment 다시 끼우는 노동을 자동화.

## 파일 구성

| 파일 | 매칭 방식 | source → target |
|---|---|---|
| `panel_contents.py` | `CC_##.` / `US_CC_##.` + sub_num + suffix 패턴 매칭 | 전체 panel (`SOURCE_PANEL_INDICES="all"`) |
| `panel_contents_recomm_v1.2.py` | panel_contents 변형 — Recommendation 계열 fallback type 추가, US_CC_[US] 잔재 제외 룰 | recomm 패널 전체 |
| `panel_contents_mapping_example.csv` | 실행 결과 매핑 CSV 형식 예시 (Source/Target SegId·SegName·MatchStatus, placeholder 값) | - |

> 실행하면 타임스탬프가 붙은 결과 매핑 CSV(`panel_contents_mapping_<ts>.csv`)가 같은 폴더에 생성된다. 실행 산출물이라 repo 에는 올리지 않는다 — 형식은 위 `_example` 파일 참고.

## 사용자 설정 (스크립트별 상단 상수)

| 상수 | 변경 시점 |
|---|---|
| `SOURCE_PROJECT_ID` / `TARGET_PROJECT_ID` | 프로젝트마다 변경 |
| `SOURCE_PANEL_INDICES` | `"all"` 또는 `[0, 2, 5]` 식 index 리스트 |
| `OLD_KEYWORDS` / `NEW_KEYWORDS` | 캠페인 prefix swap (예: `[CAMPAIGN NAME]` → `[NEW CAMPAIGN NAME]`) |
| `SKIP_KEYWORDS` | sub_num 같이 있을 때 자동 매칭 제외할 키워드 |
| `PREFERRED_OWNER_ID` | AMBIGUOUS tie-breaker — 우선할 owner 의 numeric loginId |
| `MANUAL_OVERRIDES` | source_id → target_id 직접 매핑 |
| `REMOVE_UNMATCHED_FROM_PANEL` | `True` 면 NO_MATCH source segment 를 swap 대신 panel 에서 컬럼째 제거 (False=원본 ID 잔존). `panel_contents.py` 에 있음 |

## 매칭 룰 (CC 패턴)

- **1차 키** `(type, primary_num, suffix)` — 이름 prefix `CC_##.` / `US_CC_##.` + 끝 suffix (`(Visit)` / `(Delayed Purchase)` / 없음)
- **2차 키** `(type, sub_num, suffix)` — 이름 안의 ` - ##.` sub-breakdown
- source 에 sub_num 있으면 → 2차 키 매칭. 실패 시 같은 (type, primary_num, suffix) fallback. 그것도 없으면 `No Data`
- source 에 sub_num 없으면 → 1차 키로 매칭
- **AMBIGUOUS** (2개 이상 매칭) → `PREFERRED_OWNER_ID` 가 만든 것 1개로 tie-break
- `[CAMPAIGN NAME]` prefix 없는 system / 공용 segment (`No Data`, `PC User`, `[part_name]`, `[Global]` 등) 는 swap 후보에서 제외 — keep as-is
- 우선순위: `MANUAL_OVERRIDES` > keep(no prefix) > skip(SKIP_KEYWORDS + sub_num) > sub_num > primary CC (fallback 포함) > No Data > normalize

매칭 예시:

```
"[CAMPAIGN NAME] CC_01. Content B"       ─ ("CC","01","")      ↔ "[NEW CAMPAIGN NAME] CC_01. Content B"
"[CAMPAIGN NAME] CC_01. ... (Visit)"           ─ ("CC","01","visit") ↔ 같은 (Visit) 변형
"[CAMPAIGN NAME] CC_03. ... - 01. Trip Recall" ─ sub_num="01"
       ↔ "[NEW CAMPAIGN NAME] CC_XX. ... - 01. ..."  (CC 번호 달라도 sub_num 같으면 매칭)
```

## 실행

```powershell
# dry-run (default) — 매핑 표 + 매칭 안된 목록 + CSV. PUT 안 함
python panel_contents.py
python panel_contents_recomm_v1.2.py

# source panel JSON 덤프 (디버깅)
python panel_contents.py --debug

# 실제 PUT
python panel_contents.py --apply
```

dry-run 결과 CSV (실행한 폴더에 timestamp 단위로 누적):
- `panel_contents_mapping_{ts}.csv` — panel_contents 계열 공통 (type, primary_num, sub_num, suffix 컬럼 포함)

## 새 캠페인으로 바꿀 때 수정 포인트

1. `SOURCE_PROJECT_ID` / `TARGET_PROJECT_ID` 교체 (target 은 미리 UI 에서 본인 계정으로 빈 프로젝트 생성)
2. `OLD_KEYWORDS` / `NEW_KEYWORDS` 교체 (예: `[CAMPAIGN NAME]` → `[NEW CAMPAIGN NAME]`)
3. dry-run 실행 → 결과 CSV 의 `MatchStatus = NO_MATCH` / `AMBIGUOUS` 항목 확인
4. 자동 매칭 안 된 건 → `MANUAL_OVERRIDES` 에 직접 박기 또는 NEW segment 생성 (`segment_maker/aa_create_segment_v2.4.py`). 타겟에 없어 대체 불가한 source 는 `REMOVE_UNMATCHED_FROM_PANEL=True` 로 컬럼째 제거
5. 다시 dry-run → 모두 OK 면 `--apply`

## 안전장치

- 매칭 안된 segment 가 1개라도 있으면 콘솔 빨간 표시 + CSV `MatchStatus` 컬럼
- dry-run 기본 (`--apply` 명시 없으면 PUT 안 함)
- target project 가 빈 프로젝트인지 확인 후 PUT — 기존 panel 누적되므로 미리 정리

## 자매 도구

- `../panel_collapse/collapse_panel_tables.py` — panel 안 모든 subPanel `collapsed=True` 강제
- `../panel_date_update/update_panel_date.py` — panel 종료일 일괄 치환
- `../segment_maker/aa_segment_lookup_from_pjt.py` — project 안 panel 들이 참조하는 segment 일괄 lookup
