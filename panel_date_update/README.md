# panel_date_update/ — AA Workspace 패널 종료일 치환 (운영 사본)

repo: https://github.com/juser1n/AA-Segments-Maker-by-API/tree/main/panel_date_update

운영 사본 — 실제 PROJECT_ID/AUTH 경로/캠페인별 패턴이 박혀있는 작업본. generic 변경(룰/기능)은 repo 사본에도 동기화할 것. 단순 운영값(PROJECT_ID/날짜) 변경은 repo 안 올림.

기준 문서 업데이트일: 2026-05-11

## 현재 운영 설정

| 상수 | 값 |
|---|---|
| `AUTH_JSON_PATH` | `C:\Users\user_name\path\to\auth.json |
| `COMPANY_ID` | `company_id` |
| `PROJECT_ID` | `YOUR_ID` ([part_name] 2026 CAMPAIGN NAME Campaign Revisit & Repurchase Analysis _(DE, MX)) |
| `PANEL_NAME_PATTERN` | `r"\[DE\]\s*campaign_name'?s\s*Day\s*Campaign"` |
| `OLD_END_DATE` | `2026-05-10` |
| `NEW_END_DATE` | `2026-05-11` |

## 실행

```powershell
cd "C:\Users\user_name\path\to\auth.json"

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
- `PANEL_NAME_PATTERN` — 사이트코드 + 캠페인명 (정확히 1개만 매칭되도록 좁힐 것)
- `OLD_END_DATE` / `NEW_END_DATE` — 패널 name·dateRange 양쪽에서 공통으로 박힌 종료일 ISO 문자열

같은 프로젝트 안에서 사이트별로 줄줄이 갱신할 경우, `PANEL_NAME_PATTERN` 만 `\[DE\]` → `\[MX\]` 로 바꿔가며 반복 실행하면 됨.

## 도구 사용법·동작 원리·안전장치

repo 의 `README.md` 참고 (https://github.com/juser1n/AA-Segments-Maker-by-API/tree/main/panel_date_update)
