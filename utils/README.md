# utils/  
<sub>2026-06-09  Jonghyun Park w/ Claude</sub>  

Adobe Analytics 유틸리티 스크립트 모음.

기준 문서 업데이트일: 2026-05-21

## 파일 목록

| 파일 | 용도 |
|---|---|
| `_probe_segment.py` | 기존 세그먼트 GET → definition 구조 확인 (디버깅 / API 함수명 학습용) |
| `compare_panel_segments.py` | 두 Workspace panel의 세그먼트 차집합 비교 → CSV/XLSX |
| `extract_panel_tables_json_v2.0.py` | panel × reportlet → `/reports` JSON 자동 추출 + 매핑 CSV |
| `find_user_id.py` | AA company 사용자 검색 → numeric loginId 추출 (segment owner 지정용) |

## find_user_id.py 사용법

```bash
python find_user_id.py --ims-user-id "B22...e"    # IMS ID 정확 매칭
python find_user_id.py --login user1            # login substring
python find_user_id.py --email user@example.com    # email substring
python find_user_id.py --all --csv users.csv       # 전체 사용자 CSV dump
```

## extract_panel_tables_json_v2.0.py

상단 상수(`PROJECT_ID`, `OUTPUT_CSV_DIR`, `JSON_ROOT` 등)만 바꿔서 캠페인별로 재사용.
상세 문서: 같은 폴더의 [`extract_panel_tables_json_v2.0.md`](extract_panel_tables_json_v2.0.md) 참조.
