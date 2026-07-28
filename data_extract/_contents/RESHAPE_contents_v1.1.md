# RESHAPE_contents_v1.1.py  
<sub>2026-07-28  Jonghyun Park w/ Claude</sub>  

`extract_data_v3.2_contents.py` 가 떨군 `column_mapping_*.csv` 들을 union 형태로 정제해서 분석용 wide CSV (`_union_contents_<ts>.csv`) 생성.

---

## 개요

- 최신 timestamp batch (`column_mapping_<site_code>_YYMMDD_HHMM.csv`) 만 자동 선택해서 union
- 5개 device × N site × ITEM 별 row 로 펼침
- Order ↔ Delayed Order, Revenue ↔ Delayed Revenue 합산 → 합산 row + 단독 row 둘 다 보존 (TYPE 5종)
- Revenue 만 환율 적용 (currency.csv, end_date 년도 기준)
- App 미론치 site (`app_O_X.csv` 의 'X') → app/android/ios device row 의 data_value 를 0 으로 (row 자체는 유지, reference notebook 와 동일 동작)
- COUNTRY 자동 채움 (`site_registry.lookup_site(site_code).country`)
- SUBS / TIER 컬럼은 빈 값 (수기 채움)
- **[v1.1]** 출력 직전 SITE CODE 정규화 (`SITE_CODE_NORMALIZE` 표 적용 — `us_old` → `us` 등). `rsid` / `start_date` / `end_date` 는 원본 유지.

---

## 실행

```bash
cd data_extract/_contents
python RESHAPE_contents_v1.1.py
```

옵션 없음. 동작 제어는 파일 상단 상수로:

| 상수 | 기본값 | 용도 |
|---|---|---|
| `INPUT_DIR` | `./output` | 최신 batch 자동 스캔 |
| `OUTPUT_DIR` | `./output` | union 결과 저장 |
| `SITES_FILTER` | `[]` | 빈 리스트면 batch 전체 site 처리. `['us','au']` 처럼 박으면 그 site 만 |
| `OUTPUT_BASENAME` | `_union_contents` | → `_union_contents_<yymmdd_hhmm>.csv` |
| `SITE_CODE_NORMALIZE` | `{"us_old": "us"}` | **[v1.1]** 출력 직전 SITE CODE 치환표. 분리된 RSID 데이터(us_old / us 등)를 동일 SITE CODE 로 통합. 다른 RSID 분리 케이스 추가 시 한 줄 더 |

---

## 입력

| 파일 | 내용 |
|---|---|
| `output/column_mapping_<site>_<ts>.csv` | extract_data_v3_contents 출력 (최신 ts batch 자동 선택) |
| `currency.csv` | site_code × 연도 환율 |
| `app_O_X.csv` | App 론치 여부 (O/X) per site |

> ⚠️ `currency.csv` / `app_O_X.csv` 는 **repo 미포함** (운영 데이터). 상위 폴더 `data_extract/` 의
> **`currency_example.csv` / `app_O_X_example.csv`** 로 형식을 확인한 뒤, `_example` 을 뗀 이름으로
> 본인 데이터를 채워 **이 `_contents/` 폴더 안에** 두면 된다 (경로 기준 = 스크립트와 같은 폴더).

---

## 출력 컬럼

```
TIER, SUBS, COUNTRY, SITE CODE, REPORT NO., DEVICE TYPE, TYPE, ITEM,
VALUE, VALUE (원본), origin_only_delayed_value,
rsid, start_date, end_date, value_n
```

| 컬럼 | 값 |
|---|---|
| `TIER`, `SUBS` | 빈 값 (수기 채움) |
| `COUNTRY` | `site_registry` 의 country (au → Australia, br → Brazil 등) |
| `SITE CODE` | input CSV 의 site_code → 출력 직전 `SITE_CODE_NORMALIZE` 표 적용 (v1.1) |
| `REPORT NO.` | metric=Visits → `Engagement by Contents` / 나머지 → `Order by Contents` |
| `DEVICE TYPE` | pc → PC, mobile → Mobile, app → App, android → Android, ios → iOS |
| `TYPE` | Visits / Order / Revenue / Order+Delayed Order / Revenue+Delayed Revenue (5종) |
| `ITEM` | segments 마지막 토큰 룰 (아래) |
| `VALUE` | 환율 적용 후 (Revenue 만 환산, 그 외 = 원본) |
| `VALUE (원본)` | data_value 그대로 |
| `origin_only_delayed_value` | 합산 row 의 Delayed 부분만 (환율 적용 안 함, 원본 통화) |
| `rsid, start_date, end_date, value_n` | input row 원본 metadata (SITE CODE 정규화돼도 출처 구분 가능) |

---

## SITE CODE 정규화 (v1.1)

2026-05-19 부로 US 의 RSID 가 `sscompany_namenewus` → `sscompany_name4newus` 로 갈리면서
`extract_data_v3.2_contents.py` 가 두 site_code 로 분리 추출:
- `us_old` (rsid `sscompany_namenewus`, 기간 ~5-18, `[US]` panel)
- `us` (rsid `sscompany_name4newus`, 기간 5-19~, `[Global]` panel)

이 두 row 를 분석 단계에선 동일 SITE CODE 로 봐야 하므로,
**모든 join·합산·환율·ITEM 정제가 끝난 출력 직전 단계**에서 `SITE CODE` 컬럼만 치환:

```python
SITE_CODE_NORMALIZE: dict[str, str] = {
    "us_old": "us",
}
```

- `delayed_index` 등 합산 join key 는 여전히 원본 site_code 사용 → RSID 다른 데이터끼리 cross-매칭 없음
- `rsid` / `start_date` / `end_date` 는 원본 그대로 → CSV 에서 출처 구분 가능 (e.g. `rsid=sscompany_namenewus` 면 us_old 원본)
- 변환 카운트는 `[normalize] SITE CODE 정규화: N rows (us_old→us(N))` 형식으로 콘솔 출력

향후 다른 RSID 분리 케이스 (예: `uk_old` → `uk`) 생기면 `SITE_CODE_NORMALIZE` 표에 한 줄만 추가.

---

## TYPE 5종

| TYPE | 생성 조건 |
|---|---|
| `Visits` | metric 이 Visits 인 row |
| `Order` | reportlet 이 ` - Order` 끝, Delayed 짝 있어도 단독 row 보존 |
| `Revenue` | reportlet 이 ` - Revenue` 끝, Delayed 짝 있어도 단독 row 보존 |
| `Order+Delayed Order` | Order row + 같은 (site, device, panel, value_n, segments 정규화) Delayed Order 합산 |
| `Revenue+Delayed Revenue` | Revenue row + 같은 키 Delayed Revenue 합산 |

합산 row 의 ITEM 은 단독 row 와 동일 (CC_NN. 토큰). 단 value1 (CC_ 없는 총합 row) 만 `Campaign Main Visit > Order (Visit)` → `Campaign Main Visit > Order (Visitor)` 로 분기.

---

## ITEM 룰

`segments` 컬럼의 마지막 토큰 기준 (`; ` split 후 마지막).

### CC_ 있음 (개별 ITEM row)

```
[CAMPAIGN NAME] CC_03. Scenario: Your Daily Sync - 03. New movers (Delayed Purchase)
→ 03. Scenario: Your Daily Sync - 03. New movers
```

단계:
1. 캠페인 prefix `[XX YY]` 제거 (정규식 `\[[^\]]+\]` — `[CAMPAIGN NAME]`, `[25 YM]`, `[CAMPAIGN NAME]` 등 모두)
2. `CC_` 앞의 모든 문자 + `CC_` 까지 제거 (US_CC_NN. 같은 패턴도 NN. 만 남음)
3. 끝 괄호 ( ... ) 반복 제거: `(Delayed Purchase)`, `(Visitor)`, `(Web)` 등 모두

### CC_ 없음 (value1 총합 row)

| metric | reportlet 의 Delayed 여부 | ITEM |
|---|---|---|
| Visits | - | `Campaign Main Visit` |
| Order / Revenue | 없음 | `Campaign Main Visit > Order (Visit)` |
| Order / Revenue | 있음 (Delayed 짝 reportlet) | `Campaign Main Visit > Order (Visitor)` |

### No Data row

segments 마지막 토큰에 `No Data` 포함 → row 제외 (출력 안 함).

---

## Delayed 합산 join 키

```
(site_code, device, panel, reportlet_base, value_n, segments_normalize)
```

- `site_code` 는 **원본 그대로** (us_old / us 분리). SITE CODE 정규화는 join 끝난 뒤 적용
- `reportlet_base`: reportlet 의 ` - Delayed ` → ` - ` 로 치환 (예: `Contents Order Conversion - Delayed Order` → `Contents Order Conversion - Order`). metric 토큰 (Order/Revenue) 은 유지 — Order ↔ Delayed Order, Revenue ↔ Delayed Revenue 만 정확 매칭 (cross-매칭 방지)
- `segments_normalize`: 각 토큰의 끝 괄호 ( ... ) 반복 제거 (`(Visit)` / `(Visitor)` / `(Delayed Purchase)` 등 모두). 이래야 Order value_n 과 Delayed Order value_n 가 같은 키로 join

---

## 환율 적용

- Revenue metric 인 row 만 `data_value * rate`
- rate = `currency.csv` 의 `(site_code, end_date 년도)` 컬럼
- 매칭 없으면 1.0 (USD fallback)
- `origin_only_delayed_value` 는 환율 적용 안 함 (원본 통화 유지)

---

## App 미론치 site 처리

`app_O_X.csv` 에서 `X` 인 site → 그 site 의 app/android/ios device row 의 `data_value` 를 `"0"` 으로 덮어쓰기. row 자체는 출력에 유지. (reference notebook `RESHAPE_main_raw_v4.3.ipynb` 와 동일 동작)

---

## numeric 포맷

엑셀에서 text 인식 방지 위해 출력 직전 일관 포맷:
- 정수 가능한 float → `int` 로 (예: 67.0 → 67)
- 그 외 → `round(2)` (예: 71873.764259... → 71873.76)
- `csv.QUOTE_MINIMAL` 명시

---

## 변경 이력

- **v1.1 (2026-05-26)** — 출력 SITE CODE 정규화 단계 추가 (`SITE_CODE_NORMALIZE` 표). `us_old` → `us` 통합. `rsid` / `start_date` / `end_date` 는 원본 유지.
- **v1.0 (2026-05-18)** — initial.
