# panel_date_update/ — AA Workspace 패널 시작/종료일 일괄 치환 (운영 사본)  
<sub>2026-06-17  Jonghyun Park w/ Claude</sub>  

repo: https://github.com/jjonghyunn/AA-Segments-Maker-by-API/tree/main/panel_date_update

운영 사본 — 실제 PROJECT_ID/AUTH 경로/캠페인별 패턴이 박혀있는 작업본. generic 변경(룰/기능)은 repo 사본에도 동기화할 것. 단순 운영값(PROJECT_ID/날짜) 변경은 repo 안 올림.

## 용도

`PANEL_NAME_PATTERN` 정규식에 매칭되는 모든 패널의 시작/종료일을 일괄 치환. 패널 subtree 안의 dateRange (ISO interval `<start>/<end>` 형태) 또는 start*/end* 키 값을 자동 탐지해서 교체. 같은 캠페인의 여러 사이트 패널을 한 번에 갱신할 때 유용.

## 현재 운영 설정 (update_panel_date.py 상단 상수 기준)

| 상수 | 값 |
|---|---|
| `AUTH_JSON_PATH` | `C:\Users\YOUR_USER\OneDrive - YOUR_COMPANY\your_folder\aanalyticsact_auth.json` |
| `COMPANY_ID` | `your_aa_company_id` |
| `PROJECT_ID` | `YOUR_ID` (user_id Content C) |
| `PANEL_NAME_PATTERN` | (실행 시 캠페인에 맞게 설정 — 예: `r"\[AU\]\s*campaign_name'?s\s*Day\s*Campaign"`) |
| `NEW_START_DATE` | `2026-05-11` |
| `NEW_END_DATE` | `2026-05-17` |
| `OLD_START_DATE` / `OLD_END_DATE` | `""` (빈 문자열 — 패널 name 안 substring 치환 안 함) |

> 코드 상단에 여러 PROJECT_ID 가 주석 토글 형태로 누적되어 있음 (team 공유 / user_id 개인 / CAMPAIGN NAME 등). 활성 1개만 남기고 나머지는 주석 처리.

## 실행

```powershell
cd "C:\Users\YOUR_USER\OneDrive - YOUR_COMPANY\your_folder\your_workspace\AA_segment_maker\panel_date_update"

# 1) Dry-run
python update_panel_date.py

# 2) 패널 JSON 구조 확인 (선택)
python update_panel_date.py --dump

# 3) 적용
python update_panel_date.py --apply
```

## 캠페인/사이트 바꿀 때 수정 포인트

다른 사이트(`[MX]`, `[UK]` 등) 또는 다른 캠페인 패널 갱신 시 코드 상단의 상수만 교체:

- `PROJECT_ID` — 새 Workspace project (URL 의 `/workspace/edit/<여기>`)
- `PANEL_NAME_PATTERN` — 사이트코드 + 캠페인명 정규식 (`\[` / `\]` literal escape 필수). 매칭 패널 0개면 abort
- `NEW_START_DATE` / `NEW_END_DATE` — ISO YYYY-MM-DD
- (선택) `OLD_START_DATE` / `OLD_END_DATE` — 패널 name 안 옛 날짜 substring 치환할 때만

같은 프로젝트 안에서 사이트별로 줄줄이 갱신할 경우, `PANEL_NAME_PATTERN` 만 `\[DE\]` → `\[MX\]` 로 바꿔가며 반복 실행하면 됨.

## 동작 원리

1. 매칭된 각 패널 subtree(JSON)를 재귀 탐색
   - 값이 ISO interval format `<ISO>/<ISO>` 면 (키 이름 무관) 시작/끝 둘 다 교체
   - 그 외 단일 ISO 날짜 값이고 key 이름이 `start*` 계열이면 `NEW_START_DATE` 로, `end*` 계열이면 `NEW_END_DATE` 로 교체
2. `OLD_*_DATE` 가 비어있지 않으면 추가로 패널 name 안의 OLD → NEW substring 치환
3. 모든 패널 변경을 합쳐 project 1회 PUT

## 안전장치

- 매칭 패널 0개면 abort
- 기본은 dry-run (`--apply` 명시 안 하면 PUT 안 함)
- 매칭 안 된 패널/프로젝트 메타데이터는 손대지 않음 — 매칭 패널 subtree 안에서만 치환

## 자매 도구

- `../panel_collapse/collapse_panel_tables.py` — panel 안 subPanel 일괄 collapse
- `../panel_maker/panel_contents.py` — source panel 복제 + segment swap
- 도구 사용법·동작 원리·안전장치 상세 → repo README 참고
