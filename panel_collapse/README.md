# panel_collapse/ — AA Workspace panel 내 subPanel 일괄 collapse (운영 사본)

repo: https://github.com/wimterrr/AA-Segments-Maker-by-API/tree/main/panel_collapse (예정)

운영 사본 — 실제 PROJECT_ID / AUTH 경로 박혀있는 작업본. generic 변경 (룰/기능) 은 repo 사본에도 동기화. 단순 운영값 (PROJECT_ID) 변경은 repo 안 올림.

기준 문서 업데이트일: 2026-05-21

---

## 용도

AA Workspace project 의 panel 안에 있는 모든 subPanel (테이블 / freeform / visualization) 의 `collapsed` 속성을 `True` 로 일괄 강제. 워크스페이스 진입 시 모든 테이블이 접힌 상태로 보임 — 큰 프로젝트의 첫 화면 부담 감소 + 사용자가 필요한 panel 만 펼쳐서 보는 UX.

| 대상 | collapse 여부 |
|---|---|
| panel 자체 헤더 (panel.collapsed) | **건드리지 않음** — 접으면 panel 진입 시 자체가 가려져 UX 나쁨 |
| panel.subPanels[*].collapsed | **True 로 강제** (이미 True 면 변동 없음) |

## 현재 운영 설정

| 상수 | 값 |
|---|---|
| `AUTH_JSON_PATH` | `C:\Users\YOUR_USER\OneDrive - YOUR_COMPANY\your_folder\aanalyticsact_auth.json` |
| `COMPANY_ID` | `your_aa_company_id` |
| `PROJECT_ID` | (실행 시 본인 project ID 로 교체) |
| `PANEL_NAME_PATTERN` | `""` (빈 문자열 = 전체 panel 처리) |

## 실행

```powershell
cd "C:\Users\YOUR_USER\OneDrive - YOUR_COMPANY\your_folder\2.data\99.PY,SQL-250429\your_workspace\AA_segment_maker\panel_collapse"

# 1) Dry-run (실제 PUT 안 함 — 변경 건수만 출력)
python collapse_panel_tables.py

# 2) 변경 전/후 project JSON 덤프 (선택, 디버그)
python collapse_panel_tables.py --dump

# 3) 실제 적용
python collapse_panel_tables.py --apply
```

## 출력 예시 (dry-run)

```
[2026-05-13 18:45:12] GET project 69ead3e44... ...

프로젝트: [part_name] 2026 ... Campaign Analysis
  owner    : ...
  modified : 2026-05-13T...

전체 패널 12개 모두 처리 (PANEL_NAME_PATTERN 비어있음)

--- panel 별 처리 ---
  ✓ [ws0 p 0] [ALL SITES] 2026 ... Campaign Traffic & CVR          subPanels= 18  changed= 14
  · [ws0 p 1] [US] 2026 ... Campaign Traffic & CVR                 subPanels= 12  changed=  0  ← 이미 다 접힘
  ✓ [ws0 p 2] [ALL SITES] 2025 ... Campaign Traffic & CVR          subPanels= 18  changed= 11
  ...

--- 합계 ---
  처리 panel    : 12 개
  전체 subPanel : 145 개
  collapse 변경 : 87 개 (나머지는 이미 collapsed=True)

ℹ️ Dry-run 모드 — 실제 PUT 안 함. 적용하려면 --apply
```

## 특정 panel 만 처리하려면

`PANEL_NAME_PATTERN` 에 정규식 지정. 예:

```python
PANEL_NAME_PATTERN = r"\[ALL SITES\]\s*2026"      # ALL SITES 2026 panel 만
PANEL_NAME_PATTERN = r"\[US\]"                     # US 시리즈 panel 전체
PANEL_NAME_PATTERN = r"campaign_name'?s\s*Day"           # 캠페인명 매칭
```

빈 문자열 (`""`) 이면 전체 panel 처리.

## 다른 프로젝트에 재사용할 때

코드 상단 상수만 교체:
- `PROJECT_ID` — 새 Workspace project (URL 의 `/workspace/edit/<여기>`)
- `PANEL_NAME_PATTERN` — 필요 시 좁히기

## 자매 도구

같은 폴더 (`AA_segment_maker/`) 안:

- `panel_date_update/update_panel_date.py` — 특정 panel 의 종료일 일괄 치환
- `panel_maker/clone_project_first_panel.py` — 첫 panel 복제 + segment swap (이 안에 `_collapse_all_subpanels` 동일 헬퍼가 있어 본 도구가 그 패턴을 재사용)
- `extract_panel_tables_json_v2.0.py` — panel × reportlet → /reports JSON 추출
