# panel_maker/ — AA Workspace project panel 복제 + segment swap (운영 사본)

repo: https://github.com/wimterrr/AA-Segments-Maker-by-API/tree/main/panel_maker (반영 예정)

운영 사본 — 실제 SOURCE/TARGET PROJECT_ID·키워드·MANUAL_OVERRIDES 박혀있는 작업본. generic 변경(룰/기능)은 repo 사본에 동기화. 단순 운영값(PROJECT_ID/키워드) 변경은 repo 안 올림.

기준 문서 업데이트일: 2026-05-21

---

## 용도

source project 의 panel(들) 을 미리 만들어둔 빈 target project 로 복제 + 그 안에 박혀있는 segment ID 들을 NEW 키워드 계열의 segment 로 자동 swap. 캠페인 갈아끼울 때 panel 구조 통째로 재사용:

> `[CAMPAIGN NAME]` 캠페인 프로젝트의 패널 구조 + 리포틀렛 + 차트 layout 그대로 → `[26 JH]` / `[CAMPAIGN NAME]` / CAMPAIGN NAME Recomm 같은 새 캠페인 프로젝트에 복제 + 캠페인 prefix 만 자동 교체.

UI 에서 한 panel 씩 손으로 복제+segment 다시 끼우는 노동을 자동화.

## 파일 구성

| 파일 | 매칭 방식 | source → target |
|---|---|---|
| `clone_project_first_panel.py` | 이름 정규화 (NAME_NORMALIZATION_PATTERNS regex 순서대로) | 첫 panel(1개) 만 |
| `panel_contents.py` | `CC_##.` / `US_CC_##.` + sub_num + suffix 패턴 매칭 | 전체 panel (`SOURCE_PANEL_INDICES="all"`) |
| `panel_contents_recomm_v1.2.py` | panel_contents 변형 — Recommendation 계열 fallback type 추가, US_CC_[US] 잔재 제외 룰 | recomm 패널 전체 |
| `panel_contents_target_seg.py` | panel_contents 변형 — `TARGET_SEG_NAME_KEYWORDS` 로 source/target 양쪽 좁힘 + (선택) 위치 기반 zip (SW_ORDER_MAPPING) | scenario / column 정제용 |
| `_inspect_panel.py` | (도구) source panel JSON 덤프 (디버그) | - |
| `old/` | 구버전 아카이브 (v1.0, v1.1_formac, v1.1_forwin) | - |
| `clone_first_panel_mapping_*.csv` | clone_project_first_panel 실행 결과 매핑 누적 | - |
| `panel_contents_mapping_*.csv` | panel_contents 계열 실행 결과 매핑 누적 | - |

## 현재 운영 설정 (스크립트별)

### clone_project_first_panel.py

| 상수 | 값 |
|---|---|
| `AUTH_JSON_PATH` | `C:\Users\YOUR_USER\OneDrive - YOUR_COMPANY\your_folder\aanalyticsact_auth.json` |
| `COMPANY_ID` | `your_aa_company_id` |
| `SOURCE_PROJECT_ID` | `YOUR_ID` (CAMPAIGN NAME 캠페인) |
| `TARGET_PROJECT_ID` | `YOUR_ID` (26 JH 캠페인) |
| `SOURCE_PANEL_INDEX` | `0` (첫 panel 만) |
| `OLD_KEYWORDS` | `["[CAMPAIGN NAME]", "CAMPAIGN NAME"]` |
| `NEW_KEYWORDS` | `["[26 JH]", "26 JH"]` |

### panel_contents.py

| 상수 | 값 |
|---|---|
| `SOURCE_PROJECT_ID` | `YOUR_ID` (CAMPAIGN NAME 구조 원본) |
| `TARGET_PROJECT_ID` | `YOUR_ID` (team 공유 CAMPAIGN NAME) |
| `SOURCE_PANEL_INDICES` | `"all"` |
| `OLD_KEYWORDS` / `NEW_KEYWORDS` | `[CAMPAIGN NAME]` → `[CAMPAIGN NAME]` |

### panel_contents_recomm_v1.2.py

| 상수 | 값 |
|---|---|
| `SOURCE_PROJECT_ID` | `YOUR_ID` (recomm 구조 원본) |
| `TARGET_PROJECT_ID` | `YOUR_ID` (CAMPAIGN NAME Contents Click Analysis (Product Recommendation), user_id) |
| `SOURCE_PANEL_INDICES` | `"all"` |
| `OLD_KEYWORDS` / `NEW_KEYWORDS` | `[CAMPAIGN NAME]` → `[CAMPAIGN NAME]` |

### panel_contents_target_seg.py

| 상수 | 값 |
|---|---|
| `SOURCE_PROJECT_ID` | `YOUR_ID` (CAMPAIGN NAME) |
| `TARGET_PROJECT_ID` | `YOUR_ID` (CAMPAIGN NAME Scenario CC_03 component only) |
| `TARGET_SEG_NAME_KEYWORDS` | `["CC_03.", "_Prop", "_Evar", "all visit"]` |
| `TARGET_SEG_NAME_MODE` | `"OR"` |
| `SW_ORDER_MAPPING` | `False` |

## 매칭 룰

### clone_project_first_panel.py (이름 정규화)

- `NAME_NORMALIZATION_PATTERNS` 의 (regex, replacement) 튜플들을 순서대로 적용 → 정규화 키 도출
- 예: `"[CAMPAIGN NAME] ALL SITES_Internal_GNB"` ↔ `"[26 JH] Internal_GNB"` → 둘 다 `"internal_gnb"` 로 정규화되어 매칭
- 매칭 안 되면 `MANUAL_OVERRIDES` 의 `source_id → target_id` 직접 매핑 적용 (dry-run 결과의 `NO_MATCH` / `AMBIGUOUS` 항목 보고 박음)
- 옵션: `RENAME_PANEL` (panel.name 안 OLD → NEW 치환), `COLLAPSE_ALL_SUBPANELS` (subPanel `collapsed=True` 강제)

### panel_contents.py / _recomm / _target_seg (CC 패턴)

- **1차 키** `(type, primary_num, suffix)` — 이름 prefix `CC_##.` / `US_CC_##.` + 끝 suffix (`(Visit)` / `(Delayed Purchase)` / 없음)
- **2차 키** `(type, sub_num, suffix)` — 이름 안의 ` - ##.` sub-breakdown
- source 에 sub_num 있으면 → 2차 키 매칭. 실패 시 같은 (type, primary_num, suffix) 의 SW 컨테이너 fallback. 그것도 없으면 `No Data`
- source 에 sub_num 없으면 → 1차 키로 매칭 (target 도 sub_num 없는 것끼리)
- **AMBIGUOUS** (2개 이상 매칭) → `PREFERRED_OWNER_ID` (예: user2) 가 만든 것 1개로 tie-break
- CC / US_CC 패턴 없는 segment 는 `_normalize_name` 으로 fallback
- `[CAMPAIGN NAME]` prefix 없는 system / 공용 segment (`No Data`, `PC User`, `[part_name]`, `[Global]` 등) 는 swap 후보에서 제외 — keep as-is
- `SKIP_KEYWORDS` (예: `"recomm"`) + sub_num 둘 다 있는 segment → 자동 매칭 제외 → `No Data` fallback
- 우선순위: `MANUAL_OVERRIDES` > keep(no [CAMPAIGN NAME]) > skip(SKIP_KEYWORDS + sub_num) > sub_num > primary CC (fallback 포함) > No Data > normalize

매칭 예시:

```
"[CAMPAIGN NAME] CC_01. Rewards Benefit"      ─ ("CC","01","")        ↔ "[CAMPAIGN NAME] CC_01. Rewards Benefit"
"[CAMPAIGN NAME] CC_01. ... (Visit)"          ─ ("CC","01","visit")   ↔ SW 같은 (Visit) 변형
"[CAMPAIGN NAME] CC_03. ... - 01. Trip Recall" ─ sub_num="01"
       ↔ "[CAMPAIGN NAME] CC_XX. ... - 01. ..."  (CC 번호 달라도 sub_num 같으면 매칭)
"[CAMPAIGN NAME] CC_08. Product Recommendation"        → sub_num 없음, recomm 포함 → 정상 매칭 시도
"[CAMPAIGN NAME] CC_08. Product Recommendation - 01. Foo" → sub_num 있고 recomm 포함 → No Data
```

### panel_contents_target_seg.py 추가 룰

- `TARGET_SEG_NAME_KEYWORDS` + `TARGET_SEG_NAME_MODE` ("AND"/"OR") → source/target 양쪽에서 이 키워드 매칭되는 segment 만 swap 대상으로 좁힘
- `SW_ORDER_MAPPING=True` 면 sub_num / primary CC 정확 매칭 무시. 같은 type 안에서 SW segment list 와 MD source list 를 `(primary_num, sub_num, suffix)` 정렬 후 위치 기반 zip 매핑 (SW 가 정의하는 column 순서대로 채움)

## 실행

```powershell
cd "C:\Users\YOUR_USER\OneDrive - YOUR_COMPANY\your_folder\your_workspace\260504_AA_segment_maker\panel_maker"

# dry-run (default) — 매핑 표 + 매칭 안된 목록 + CSV 떨어뜨림. PUT 안 함
python clone_project_first_panel.py
python panel_contents.py
python panel_contents_recomm_v1.2.py
python panel_contents_target_seg.py

# source panel JSON 덤프 (디버깅)
python clone_project_first_panel.py --debug
python panel_contents.py --debug

# 실제 PUT
python clone_project_first_panel.py --apply
python panel_contents.py --apply
```

dry-run 결과 CSV:
- `clone_first_panel_mapping_{ts}.csv` — `RequestedAt / SourceSegId / SourceSegName / NormalizedName / TargetSegId / TargetSegName / MatchStatus`
- `panel_contents_mapping_{ts}.csv` — panel_contents 계열 공통 (type, primary_num, sub_num, suffix 컬럼 포함)

## 새 캠페인으로 바꿀 때 수정 포인트

1. `SOURCE_PROJECT_ID` / `TARGET_PROJECT_ID` 교체 (target 은 미리 UI 에서 본인 계정으로 빈 프로젝트 생성)
2. `OLD_KEYWORDS` / `NEW_KEYWORDS` 교체 (예: `[CAMPAIGN NAME]` → `[26 BF]`)
3. dry-run 실행 → 결과 CSV 의 `MatchStatus = NO_MATCH` / `AMBIGUOUS` 항목 확인
4. 의미상 맞지만 자동 매칭 안 된 건 → `MANUAL_OVERRIDES` 에 `source_id → target_id` 박기 (clone_project_first_panel) 또는 NEW segment 자체를 만들기 (segment_maker/v2.2)
5. 다시 dry-run → 모두 OK 면 `--apply`

## 안전장치

- 매칭 안된 segment 가 1개라도 있으면 콘솔에 빨간 표시 + CSV 의 `MatchStatus` 컬럼으로 식별
- dry-run 기본 (`--apply` 명시 없으면 PUT 안 함)
- target project 가 빈 프로젝트인지 확인 후 PUT — 기존 panel 이 있으면 덮어쓰지 않고 panel 들이 누적되므로 미리 정리 필요

## 자매 도구

- `../panel_collapse/collapse_panel_tables.py` — panel 안 모든 subPanel `collapsed=True` 강제 (clone_project_first_panel 도 `COLLAPSE_ALL_SUBPANELS=True` 옵션 내장)
- `../panel_date_update/update_panel_date.py` — panel 종료일 일괄 치환
- `../utils/extract_panel_tables_json_v2.0.py` — panel × reportlet → /reports JSON 추출 (panel 구조 확인 시)
- `../segment_maker/aa_segment_lookup_from_pjt.py` — project 안 panel 들이 참조하는 segment 일괄 lookup (NEW segment 만들기 전 확인)
