# RESHAPE_contents_tier1_2_v2.0.py — 정제코드 가이드  
<sub>2026-08-10  Jonghyun Park w/ Claude</sub>  

추출 CSV 를 읽어 보고서용 **union CSV 1개**로 만듭니다.
Tier1(넓은 범위)과 Tier2(좁은 범위)를 한 번에 처리합니다.

```bash
python RESHAPE_contents_tier1_2_v2.0.py
# → output/_union_contents_tier1_2_<YYMMDD_HHMM>.csv
```

---

## 1. 처리 순서 (실행 로그와 같은 순서)

| # | 단계 | 로그 태그 |
|---|---|---|
| 0 | 매트릭스 CSV 로드 (옵션: xlsx 갱신) | `[matrix]` |
| 1 | site 별 최신 추출 CSV 1개씩 선택 | `[input]` |
| 2 | 환율 / App O·X 로드 | `[currency]` |
| 3 | 전체 행 로드 (stack 은 컬럼 총계로 집계) | `[load]` |
| 4 | **valueN 필터** — site 별로 쓸 컬럼만 | `[valueN filter]` |
| 5 | App 미론치 site → `value_fx` 만 0 | `[app X zero]` |
| 6 | 매트릭스 False → `value_fx` 만 0 | `[matrix zero]` |
| 7 | Delayed 행을 본 행에 합산 | — |
| 8 | 출력 행 생성 (환율 적용) | `[WARN] 환율 미매칭` |
| 9 | `us_old` → `us` 통합 | `[normalize]` |
| 10 | 저장 | `[save]` |

---

## 2. valueN 필터 — 이 코드의 핵심

AA contents 테이블의 가로 컬럼은 `value1 … value17` 로 번호가 붙고,
**5개 테이블 전부 번호↔콘텐츠 대응이 같습니다.** 그래서 "이 나라가 쓰는 콘텐츠"를
번호 목록으로 표현할 수 있습니다.

```python
TIER1_SITES = ["br","de","es","in","mx","tr","uk","us","us_old",
               "it","pt","au","fr","be","hq"]
TIER1_VALUE_N = []                  # [] = 전체 통과
TIER2_VALUE_N = [1, 5, 6, 7, 8]     # 기준행 + CC_03 및 하위 01/02/03
SITE_VALUE_N_OVERRIDES = {"be": [1, 16]}
```

**우선순위: `SITE_VALUE_N_OVERRIDES` > tier 기본값**

| 분류 | 판정 | valueN | 매트릭스 |
|---|---|---|---|
| Tier1 | `TIER1_SITES` 에 있음 | 전체 | ✅ |
| Tier2 | 그 외 전부 | `1,5,6,7,8` | 표에 없어 무영향 |
| 예외 | `SITE_VALUE_N_OVERRIDES` | 직접 지정 | ✅ |

### `be` 가 왜 Tier1 에 있나

`be` 는 CC_10(SSD Banner) 배너만 집계하는 나라입니다.
Tier1 경로로 넣어 매트릭스 필터를 받게 하되, 실제 쓰는 컬럼은 예외 설정이 `1,16` 으로 좁힙니다.
예전 `sites_input.csv` 의 `only_cc_10_ssd_banner` 플래그를 코드로 옮긴 것입니다.

### site 를 추가할 때

- Tier1 로 넣으려면 → `TIER1_SITES` 에 site_code 추가
- Tier2 면 → **아무것도 안 해도 됩니다** (등록 안 된 site 는 자동 Tier2)
- 특이 케이스면 → `SITE_VALUE_N_OVERRIDES` 에 한 줄

---

## 3. 0 처리 3종 — 셋 다 다릅니다

| 무엇 | 행 | `value_fx` | `value_orig` |
|---|---|---|---|
| valueN 필터 | **사라짐** | — | — |
| 매트릭스 `False` | 남음 | **0** | 실측값 |
| App 미론치 | 남음 | **0** | 실측값 |

원본을 남기는 이유는 나중에 *"이 나라 이 콘텐츠 실제 수치가 뭐였나"* 를 되짚기 위해서입니다.
집계는 `value_fx` 로, 검증은 `value_orig` 으로 합니다.

> **합산 행(`Order+Delayed Order` 등)은 본 행과 delayed 행이 둘 다 0 대상일 때만 0** 입니다.
> 한쪽만 걸리면 합산 행은 실값이 나옵니다.

---

## 4. 콘텐츠 × 국가 매트릭스

"이 나라에서 이 콘텐츠를 실제로 노출했나" 표입니다. `False` 면 `value_fx` 를 0 으로 만듭니다.

```python
MATRIX_ENABLED           = True
MATRIX_CSV               = SCRIPT_DIR / "contents_by_country.csv"   # ← 원천 (사본)
MATRIX_REFRESH_FROM_XLSX = False    # 기본 끔 — 외부 경로 의존 없음
```

**이 폴더의 CSV 사본이 원천입니다.** 외부 Excel 을 절대경로로 물지 않아 다른 PC·repo 로
그대로 옮겨도 동작합니다. 표가 바뀌면 Excel 에서 새로 뽑아 이 CSV 를 **덮어쓰면** 됩니다.

CSV 형식 — 1열 `segment_name`, 2열부터 site 코드, 값은 `True`/`False`:

```csv
segment_name,br,de,es,in,mx,tr,uk,us,it,pt,au,fr,be
[CAMPAIGN] CC_10. SSD Banner,False,True,False,...,True
```

<details><summary>스크립트가 Excel 에서 직접 뽑게 하려면 (선택)</summary>

```python
MATRIX_REFRESH_FROM_XLSX = True
MATRIX_XLSX     = r"...\campaign_contents_segment_builder_260721.xlsx"
MATRIX_SHEET    = "1_Contents by Country"
MATRIX_ROWS     = (6, 15)      # segment 행 범위 (엑셀 행번호)
MATRIX_COLS     = ("F", "Q")   # site 열 범위 (엑셀 열문자)
MATRIX_NAME_COL = "C"          # segment_name 열
MATRIX_SITE_DEFAULTS = {"be": {"[CAMPAIGN] CC_10. SSD Banner": True}}
```

- Excel 셀이 다른 시트를 참조하는 **함수**라 계산된 값만 읽습니다(`data_only=True`).
  Excel 에서 한 번은 열어 저장돼 있어야 값이 들어있습니다.
- **실패해도 죽지 않습니다** — openpyxl 없음 / 파일 없음 / Excel 열려있음(PermissionError)
  → 경고 후 기존 CSV 로 진행.
- `MATRIX_SITE_DEFAULTS` = Excel 에 열이 없는 site 를 코드로 보정.
  지정한 segment 만 True, 나머지는 False 로 채웁니다.
</details>

### 이름 매칭 정규화 (`clean_segment_name`)

표 1행이 모든 변종을 잡도록 4단계로 깎습니다.

| 단계 | 예 |
|---|---|
| 끝 괄호 반복 제거 | `CC_03 … (Visit)` → `CC_03 …` |
| `US_CC_` → `CC_` | 미국 전용 세그도 같은 룰 적용 |
| breakdown 꼬리 제거 | `CC_03 … - 01. Smart Runners` → `CC_03 …` |
| 소문자화 | |

- `us_old` 는 `us` 의 매트릭스를 빌려 씁니다 (`SITE_CODE_NORMALIZE`)
- **매트릭스에 없는 CC 는 건드리지 않습니다** (안전 fallback).
  로그의 `매트릭스 미등록 CC row N건은 통과` 가 그 수치입니다.

---

## 5. 환율 — 반드시 **연도** 기준

```python
year = end_date[:4]
rate = currency.get((site.lower(), year)) or currency.get((site, year))
```

`currency.csv` 헤더의 `YYYY-MM-DD` 에서 **연도만** 읽어 `(site, 연도) → 환율` 로 만듭니다.

> **날짜 전체를 상수로 박지 마세요.** 폴더를 복사하면 `currency.csv` 헤더만 새 날짜로 갱신되고
> 상수는 옛 날짜로 남아 매칭이 깨집니다 → 환율 dict 가 비어 rate 1.0 적용 →
> **현지통화 금액이 USD 인 척 출력**됩니다. 에러가 안 나서 발견이 늦습니다.

- 환산 대상은 **`revenue` 가 들어간 metric 만**. Visits/Order 는 무관합니다.
- `origin_only_delayed_value` 는 **환산하지 않습니다** (현지통화 원본 유지).
- 환율을 못 찾아 1.0 이 적용되면 실행 끝에 **경고를 찍습니다** — 조용히 넘기지 않습니다.

```
[WARN] 환율 미매칭 2건 → rate=1.0 적용 (현지통화가 USD 인 척 나갑니다)
   - xx/2026
```

### 이 폴더 `currency.csv` 의 특이점

site 마다 **데이터가 끝난 시점의 차수 환율**을 써야 해서, 여러 차수 스냅샷을 **병합**했습니다.
출처는 `currency_source.csv` 에 site 별로 기록돼 있습니다. (repo 미포함 — 운영 파일)

| 차수 | 적용 site 예 |
|---|---|
| 2nd (05-24) | in, tr |
| 3rd (05-31) | ar, au, br, cz, eg, mx, pe, za |
| 4th (06-09) | bg, cl, co, dk, fi, fr, id, it, my, no, ph, pk, se, sg |
| 5th (06-30) | ca, ca_fr, ro |
| 5th (07-31) | hq |
| 무관(USD=1.0) | ae, ae_ar, be, de, es, latin, pt, sa, sa_en, uk, us, us_old |

> `de/es/pt/uk/be` 가 1.0 인 건 오류가 아닙니다 — 가상 리포트 수트(`vrs_`)라
> 매출이 이미 USD 로 리포팅됩니다.

---

## 6. Delayed 합산

`… - Delayed Order` / `… - Delayed Revenue` 테이블 행을 본 행에 붙여
`Order+Delayed Order` / `Revenue+Delayed Revenue` 를 만듭니다.

**짝짓기 키** = `(site, device, panel, reportlet 기본형, value_n, 정규화된 segments)`

- reportlet 기본형: `… - Delayed Order` → `… - Order`
- segments 정규화: 각 토큰의 끝 괄호를 떼어 `(Visit)` / `(Delayed Purchase)` 차이를 없앰
- delayed 행 자체는 단독 출력하지 않습니다 (합산에만 쓰임)
- `origin_only_delayed_value` 컬럼에 지연전환분만 따로 남습니다

---

## 7. 입력 2종 자동 인식

| 파일 | 출처 |
|---|---|
| `stack_data_extract_<site>_<ts>.csv` | 현행 `extract_data_v4.3_contents.py` |
| `column_mapping_<site>_<ts>.csv` | 구버전 추출기 (v3.2 계열, repo 미포함) |

- **site 별 최신 ts 1개씩**만 씁니다 (site 마다 추출 시각이 달라도 됨)
- 같은 site 에 두 형식이 다 있으면 ts 가 더 최신인 쪽
- stack 은 `(site, rsid, 기간, panel, reportlet, device, value_n, segments)` 로 묶어
  값을 **합산**해 컬럼 총계를 만듭니다 (행 차원이 1개면 그대로)
- **breakdown 하위 행(`bd*_itemId` 가 채워진 행)은 제외** — 부모 총계와 이중집계됩니다

---

## 8. 출력 컬럼

보고서 raw 시트와 **같은 이름·같은 순서** (그대로 붙여넣기 가능):

```
tier, subs, country, site_code, report_no, device_type, metric, item,
value_fx, value_orig, origin_only_delayed_value,
rsid, start_date, end_date, value_n
```

- `tier` = `Tier 1` / `Tier 2` 자동 기입 (`TIER1_LABEL_SITES` 기준, `TIER_LABEL_ENABLED` 로 on/off)
- `subs` 는 **빈 값** — 캠페인마다 명칭 디테일이 달라 채우지 않습니다
- `value_fx` = 환율 적용 + 0 처리 반영 (집계용) / `value_orig` = 환율 미적용 원본 통화 (검증용)
- `item` 은 세그 stack 마지막 토큰에서 추출:
  `[CAMPAIGN] CC_03. Scenario: … (Visit)` → `03. Scenario: …`.
  `No Data` 컬럼은 행 자체를 버립니다
- 숫자는 정수면 정수, 아니면 소수 2자리로 통일

### tier 가 두 종류인 이유

| 상수 | 뜻 | 반영되는 곳 |
|---|---|---|
| `TIER1_SITES` | **데이터 처리** — 어느 valueN 을 쓰고 매트릭스를 적용할까 | 행 필터링 (로그에 표시) |
| `TIER1_LABEL_SITES` | **사업 분류** — 보고서상 몇 티어 나라인가 | 출력 `tier` 컬럼 |

두 목록은 거의 같지만 **`be` 에서 갈립니다** — 처리는 Tier1 경로(전체 valueN + 매트릭스),
사업 tier 는 `Tier 2`. 그래서 `be` 행은 `tier=Tier 2` 인데 매트릭스 필터를 받습니다 (정상).

---

## 9. 자주 겪는 문제

| 증상 | 원인 / 확인 |
|---|---|
| 행 수가 예상보다 적다 | `[valueN filter]` 로그 확인 — Tier2 판정이면 5개 컬럼만 남음 |
| 매출이 현지통화 그대로 | `[WARN] 환율 미매칭` 확인 — `currency.csv` 에 site 행/연도 컬럼 있는지 |
| 매트릭스가 안 먹는다 | `[matrix] … 엔트리 로드` 가 0 이면 CSV 부재. `[matrix zero]` 가 0 이면 이름 매칭 실패 |
| Excel 고쳤는데 반영 안 됨 | 원천은 폴더의 `contents_by_country.csv` — 새로 뽑아 덮어쓸 것 |
| US 데이터가 절반쯤 빔 | `sites_input.csv` 에 `us_old` 행이 있는지 (US 는 2행 필수) |
| 합산 행만 값이 이상 | delayed 짝짓기 실패 — `delayed pair used` 수치가 기대보다 적은지 확인 |
