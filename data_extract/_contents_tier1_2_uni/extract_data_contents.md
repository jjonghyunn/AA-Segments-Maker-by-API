# extract_data_v4.3_contents.py — 추출기 가이드  
<sub>2026-08-10  Jonghyun Park w/ Claude</sub>  

Adobe Analytics Workspace 프로젝트의 패널/테이블을 그대로 읽어 `/reports` API 로 재현하고,
결과를 CSV 2개로 떨구는 도구입니다. 범용판 `extract_data_v4.3.py` 의 **contents 전용 분기본**입니다.

---

## 1. 범용판과 딱 하나 다른 것 — device 처리

같은 테이블을 PC / Mobile / App / Android / iOS **5번 뽑아야** 하는데, 그 방법이 다릅니다.

| | 범용 `DEVICE_CASES` | contents `DEVICE_SWAP_CASES` |
|---|---|---|
| 방식 | 세그를 globalFilter 에 **추가(add)** | 이미 박힌 세그를 **교체(replace)** |
| 전제 | 패널에 device 세그가 **없음** | 패널에 device 세그가 **있음** |
| contents 에 쓰면 | ❌ `PC User AND Mobile User` → 결과 0 | ✅ 정상 |

contents 패널에는 `PC User (Visit)` + `[Global] Excluded APP` 이 **기본으로 박혀 있습니다.**
그래서 PC 를 base 로 두고 그 세그 ID 를 device 별로 갈아끼웁니다.

```python
DEVICE_SWAP_CASES = [
    {"device": "pc",      "replace": {}},                                   # base 그대로
    {"device": "mobile",  "replace": {BASE_PC_ID: _SEG["mobile"]}},
    {"device": "app",     "replace": {BASE_PC_ID:           _SEG["app_only"],
                                      BASE_EXCLUDED_APP_ID: _SEG["all_visit"]},
     "requires_app": True},
    ...
]
```

App 계열은 `Excluded APP`(앱 **제외**) 을 `App Only`(앱**만**) 로 **뒤집어야** 하므로 2개를 함께 바꿉니다.

> ⚠ 두 방식을 동시에 켜면 세그가 중복으로 걸려 결과가 0 이 됩니다.
> 추출기가 시작할 때 검사해서 막습니다 (`DEVICE_CASES` 는 비워둘 것).

**세그 이름도 함께 바꿉니다.** 출력 CSV 의 `segments` 컬럼이 정제코드의 ITEM 소스라,
ID 만 바꾸고 이름을 두면 데이터는 Mobile 인데 표기는 PC 로 남습니다.

---

## 2. 반드시 확인할 상수 3개

```python
PROJECT_ID = "YOUR_PROJECT_ID"   # Tier1 프로젝트 (Tier2 도 이걸로)
US_PANEL_PREFIX     = "[US]"        # ★ 대괄호 필수
GLOBAL_PANEL_PREFIX = "[Global]"    # ★ 대괄호 필수
```

### `[US]` 대괄호 함정

이 프로젝트의 패널명은 `[US] Content Analysis` / `[Global] Content Analysis` 입니다.
범용판 기본값(`"US"`, `"Global"`)을 그대로 두면 `startswith` 가 안 걸려
**US/Global 분리가 통째로 무효화**되고, `us_old` 와 `us` 가 같은 패널을 중복 추출합니다.

정상 동작 시 로그:

```
SITE: ca      → ⊘ panel skip: [US] Content Analysis      (non-us site → [US] panel skip)
SITE: us_old  → ⊘ panel skip: [Global] Content Analysis  (us site → [Global] panel skip)
```

### PROJECT_ID 를 하나만 쓰는 이유

Tier2 전용 프로젝트가 따로 있었지만 컬럼 구조(5 reportlet × 17 valueN)가 이것과 완전히 같고,
안 쓰는 자리를 `No Data` 세그로 메워 자리수만 맞춘 사본이었습니다.
Tier1 이 상위집합이라 이것 하나로 전 site 를 뽑고, **정제 단계에서 valueN 을 골라냅니다.**

검증 결과 (site `ca`): Tier1 프로젝트로 뽑아 `value1,5,6,7,8` 만 남기면
기존 Tier2 프로젝트 결과와 **값이 완전히 일치**했습니다.

---

## 3. app 미론치 site 는 추출에서 거르지 않습니다

```python
DEVICE_SWAP_APPLY_APP_OX = False   # 기본
```

앱이 없는 나라도 app/android/ios 를 **일단 다 뽑습니다.** 여기서 skip 하면 행이 아예 안 생겨
정제 결과의 `value_orig` 에 실측값이 남지 않기 때문입니다.
0 처리는 정제코드가 담당합니다 (`value_fx` 만 0, 원본 보존).

---

## 3-b. 데이터가 없는 테이블도 0 행을 남깁니다

```python
EMIT_ZERO_ROWS_WHEN_EMPTY = True   # 기본
```

AA 가 세그 조합에 해당하는 데이터를 못 찾으면 `rows` 와 `summary` 를 **둘 다 비워** 보냅니다.
범용 v4.3 은 그 경우 아무것도 안 써서 **그 device 가 출력에서 통째로 사라집니다.**

구 `column_mapping` 은 컬럼 기준이라 항상 17행을 썼기 때문에, 그대로 두면 행 구성이 어긋납니다.
그래서 이 분기본은 빈 테이블도 컬럼별 0 행을 남깁니다.

> 실제 사례: `us_old`(구 US suite)의 `app` — `[Global] App Only` 세그가 그 suite 에 데이터가 없어
> 5개 테이블 전부 빈 응답이 옵니다. 이 옵션이 없으면 `us_old` 의 App 이 통째로 누락돼
> 정제 결과가 **85행 부족**해집니다 (2026-08-03 실제로 겪은 문제).

"0 이었다"와 "안 뽑혔다"를 구분할 수 있어야 하므로 켜두는 것이 기본입니다.

---

## 4. 출력 CSV 2종

```
output/stack_data_extract_<site>_<YYMMDD_HHMM>.csv    ← 정제코드가 읽는 것
output/table_data_extract_<site>_<YYMMDD_HHMM>.csv    ← 눈으로 볼 때 (AA 테이블 모양)
```

### `stack` (long) — 1행 = 컬럼 × 디멘션 항목

```
site_code, rsid, start_date, end_date, panel, table, reportlet,
dimension, dimension_name, itemId, <디멘션>, value_n,
metric_origin, metric, segments, device, value1 [, bd*_...]
```

- `<디멘션>` 컬럼명은 테이블 행 차원에 따라 달라집니다 (contents 는 `daterangeyear`)
- contents 테이블은 행 차원이 **1개뿐**이라 사실상 `1행 = 1컬럼(valueN)` 입니다
- `metric_origin` = AA 원본 표기, `metric` = 정제본. 정제코드는 **원본을 우선** 씁니다

### 구버전 `column_mapping_*` 과의 관계

구버전 추출기(v3.2 계열, repo 미포함)는 `column_mapping_<site>_<ts>.csv` 를 떨궜습니다.
컬럼이 1:1 대응하므로 정제코드는 **둘 다 인식**합니다.

| 구 `column_mapping` | 신 `stack` |
|---|---|
| `data_value` | `value1` |
| `metric` | `metric_origin` |
| `value_n` / `segments` / `device` | 동일 |

---

## 5. 범용판에서 그대로 물려받은 기능

기본값이 전부 꺼져 있어 **평소엔 신경 쓸 필요 없습니다.** 필요할 때만 켭니다.

| 상수 | 용도 |
|---|---|
| `BREAKDOWN_*` | 테이블의 N단계 분해(breakdown) 행까지 추출 |
| `MONTHLY` | 총기간을 달력 월로 쪼개 `period` 컬럼 추가 |
| `YEAR_OFFSETS` | 작년 같은 기간을 함께 추출 (파일명 `_y2025` 태그) |
| `LIMIT_LV1` / `LIMIT_BD*` | 단계별 행 수 상한 |
| `--estimate` | 실제 호출 전 요청량 추정 |
| `SITE_WORKERS` | site 병렬 수 (429 잦으면 낮출 것) |
| `_verify_csv_written` | CSV 쓰고 나서 되읽어 필드 수 검사 (자동) |

---

## 6. 실행

```bash
python extract_data_v4.3_contents.py                    # sites_input.csv 전체
python extract_data_v4.3_contents.py --site hq         # 한 site 만
python extract_data_v4.3_contents.py --device pc        # 한 device 만
python extract_data_v4.3_contents.py --dry-run          # payload 생성까지만 (API 호출 X)
```

`--dry-run` 은 패널 skip 판정과 세그 치환을 API 호출 없이 확인할 수 있어,
설정을 바꾼 직후 **먼저 돌려보길 권합니다.**

### 시작 로그에서 확인할 것

```
DEVICE_CASES  : 0건 (add 방식 미사용 — contents 는 아래 swap 사용)
DEVICE_SWAP   : 5종 (pc, mobile, app, android, ios)
                base 'PC User (visit)' / '[Global] Excluded APP' 을 device 별로 치환
app_O_X       : 미적용 — 5종 전부 추출 (0 처리는 정제 단계, 원본값 보존)

[device swap] 5종 세그 검증:
  [base] 'PC User (Visit)'
  [mobile] 'PC User (Visit)' → 'Mobile User (Visit)'
  ...
```

> `↪ 표기 참고: AA 실제명 '…' / CSV 표기 '…'` 는 오류가 아닙니다.
> 세그 ID 는 맞고 표기만 다른 경우로, 구버전 산출물과 표기를 맞추려고 상수 이름을 씁니다.

---

## 7. 다른 캠페인에 재사용하려면

1. `PROJECT_ID` 를 그 캠페인 프로젝트로 교체
2. 패널명 확인 → `US_PANEL_PREFIX` / `GLOBAL_PANEL_PREFIX` 를 실제 형식에 맞춤
3. 패널에 device 세그가 **있으면** `BASE_PC_ID` / `BASE_EXCLUDED_APP_ID` 를 그 세그로 교체,
   **없으면** `DEVICE_SWAP_ENABLED = False` 로 끄고 범용 `DEVICE_CASES` 를 사용
4. `--dry-run` 으로 패널 skip 판정과 세그 치환이 의도대로인지 확인
5. `sites_input.csv` / `currency.csv` / `app_O_X.csv` 교체
