# utils/  
<sub>2026-07-29  Jonghyun Park w/ Claude</sub>  

Adobe Analytics 유틸리티 스크립트 모음.

## 파일 목록

| 파일 | 용도 |
|---|---|
| `find_user_id.py` | AA company 사용자 검색 → numeric loginId 추출 (segment owner 지정용) |

## find_user_id.py 사용법

```bash
python find_user_id.py --ims-user-id "B22...e"    # IMS ID 정확 매칭
python find_user_id.py --login user1            # login substring
python find_user_id.py --email user@example.com    # email substring
python find_user_id.py --name "user1"              # fullName substring
python find_user_id.py --all --csv users.csv       # 전체 사용자 CSV dump
```
