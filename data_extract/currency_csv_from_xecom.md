# currency_csv_from_xecom.md  
<sub>2026-09-23  Jonghyun Park w/ Claude</sub>  

추출 폴더의 `sites_input.csv` 날짜에 맞는 `currency.csv` 를 xe.com 환율표(`https://www.xe.com/currencytables/?from=USD&date=YYYY-MM-DD`)에서 만들어 주는 도구. RESHAPE 가 자동으로 부르는 게 기본이고, 직접 실행도 된다.

## 기준일

- 기준일 = `sites_input.csv` 의 `end_date` **최댓값** (`#` 주석행·end_date 가 빈 reuse 행 제외)
- 비교일 = 기준일의 **1년 전** (2/29 는 2/28)

## 출력 — `currency.csv` (그 폴더에 덮어쓰기)

```
site_code,currency_code,2026-09-15,2025-09-15
ae,AED,0.2722940776,0.2722940776
```

- 값 = **USD per unit** (현지통화 1단위의 USD 가치). RESHAPE 는 `revenue × rate` 로 USD 환산하고, 헤더 날짜를 **연도로** 읽어 각 행 `end_date` 연도의 환율을 고른다 (`load_currency_map()`).
- 유효숫자 10자리, 지수표기 없음. UTF-8(BOM 없음), CRLF.
- xe.com 표에 없는 통화(예: `IRR`, `RUB`)는 `#N/A` + 경고 → RESHAPE 는 그 site 환율을 못 찾은 것으로 처리한다.

## RESHAPE 자동 호출

RESHAPE_* 는 입력(추출 CSV)에 `revenue` 가 있으면 정제 전에 이 도구의 `ensure_currency_csv()` 를 부른다.

- 도구를 **RESHAPE 폴더 → 상위 2단계** 순으로 찾는다. 그래서 `data_extract` / cutoff 폴더 **최상단에 1개**만 두면 하위 폴더 RESHAPE 가 모두 쓴다 — 폴더째 복사해도 같이 따라간다.
- `currency.csv` 헤더 날짜가 이미 `[기준일, 1년 전]` 이면 **다시 받지 않는다**.
- 받기에 실패하면(아직 확정 전 날짜라 404, 네트워크 오류, 도구 없음) **경고만 찍고 기존 `currency.csv` 로 계속** 정제한다.
- 끄려면 RESHAPE 상단 `AUTO_CURRENCY_CSV = False`.

## 직접 실행

```bash
python currency_csv_from_xecom.py "<extract 폴더>"           # 이미 최신이면 skip
python currency_csv_from_xecom.py "<extract 폴더>" --force   # 무조건 다시 받음
python currency_csv_from_xecom.py                            # 폴더 경로 입력 프롬프트
```

## site ↔ 통화 매핑 — `currency_code_by_site.csv`

이 스크립트와 **같은 폴더**에 둔다. `site_code,currency_code` 2컬럼이고, 행 순서가 곧 `currency.csv` 출력 순서다. 새 site 는 여기에 한 줄 추가한다.

## 의존성

`requests`, `beautifulsoup4`
