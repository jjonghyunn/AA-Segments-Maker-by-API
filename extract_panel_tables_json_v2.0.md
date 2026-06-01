# extract_panel_tables_json_v2.0.py (segment_maker 위치)

`260413_CAMPAIGN NAME/launch/extract_panel_tables_json_v2.0.py` 와 **동일한 코드** — 캠페인 폴더가 아닌 일반 utility 폴더에 둔 사본.

기준 문서 업데이트일: 2026-05-07

## 사용 패턴 — 캠페인별로 PROJECT_ID 만 바꿔 재사용

이 파일은 segment_maker 폴더(일반 도구 모음 위치)에 두고, 다른 캠페인이나 ad-hoc 추출이 필요할 때 **상단 상수만 바꿔서** 실행.

| 상수 | 변경 예시 |
|---|---|
| `PROJECT_ID` | 새 캠페인의 Workspace URL `/workspace/edit/{이부분}` |
| `OUTPUT_CSV_NAME_TEMPLATE` | `tb_column_name_mapping_{캠페인약어}_{ts}.csv` 로 캠페인 표시 |
| `OUTPUT_CSV_DIR` | 보통 `Path(__file__).parent.parent / "ref"` (캠페인 폴더 ref/) |
| `JSON_ROOT` | 보통 `Path(__file__).parent.parent / "json"` (캠페인 폴더 json/) |
| `CURRENT_YEAR` / `LAST_YEAR` | 캠페인 시즌에 맞춰 |
| `SPECIAL_TABLE_NAMES` | 캠페인별 특수 테이블 이름이 다르면 추가/수정 |

## 자세한 동작

상세 로직, section header 파싱, 카테고리 분류, 변형 룰 등은 `260413_CAMPAIGN NAME/launch/extract_panel_tables_json_v2.0.md` 참조 (same code, same docs).

## 자매 파일 (같은 폴더)

- `compare_panel_segments.py` — 본 스크립트의 walk 패턴 참고 원본 (panel 안의 segment ID 추출)
- `aa_create_segment.py` / `aa_delete_segment.py` — segment 자체를 다루는 도구
- `_probe_segment.py` — 기존 segment 구조 학습/디버그
- `find_user_id.py` — segment owner 의 numeric loginId 찾기

## 공개 repo

이 폴더는 `https://github.com/wimterrr/AA-Segments-Maker-by-API` 와 매핑됨. repo 커밋 시 sanitize:
- `AUTH_JSON_PATH` → 플레이스홀더 경로
- `COMPANY_ID` → `"your_aa_company_id"`
- `PROJECT_ID` → 빈 값 또는 `"YOUR_PROJECT_ID"`
- 캠페인 식별자 (`MD`, `CAMPAIGN NAME`) → 일반화 / 제거
