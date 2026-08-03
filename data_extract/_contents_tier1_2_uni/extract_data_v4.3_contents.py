# extract_data_v4.3_contents.py
# 2026-08-03  Jonghyun Park w/ Claude
#
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ 이 파일은 무엇인가 — 처음 보는 사람용 3줄 요약                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝
# · Adobe Analytics Workspace 프로젝트의 패널/테이블을 그대로 읽어 /reports API 로
#   재현하고, 결과를 CSV 2개(stack=long / table=가로)로 떨구는 추출기다.
# · 범용판 `extract_data_v4.3.py` 의 **contents 전용 분기본**이다. 차이는 딱 하나 —
#   "device 별로 같은 테이블을 5번 뽑는 방법"이 다르다 (아래 [분기 사유] 참조).
# · 뽑은 CSV 는 같은 폴더 `RESHAPE_contents_tier1_2_v2.0.py` 가 union 으로 정제한다.
#
# ── [분기 사유] 왜 범용 v4.3 을 그대로 못 쓰고 이 파일이 따로 있나 ──────────────
#   contents 프로젝트의 패널에는 device 세그(`PC User (Visit)`, `[Global] Excluded APP`)가
#   **이미 박혀 있다**. 그래서 device 를 바꾸려면 그 세그를 **다른 세그로 갈아끼워야(replace)** 한다.
#   반면 범용 v4.3 의 `DEVICE_CASES` 는 세그를 globalFilter 에 **덧붙이는(add)** 방식이라,
#   여기 쓰면 `PC User AND Mobile User` 가 되어 결과가 0 이 된다.
#   → 이 파일은 replace 방식(`DEVICE_SWAP_CASES` + `_apply_device_swap`)을 추가로 갖는다.
#      범용 `DEVICE_CASES`(add) 는 빈 리스트로 두어 비활성 — 둘을 같이 켜지 말 것.
#
# ── 범용 v4.3 대비 바뀐 것 (그 외는 전부 동일) ────────────────────────────────
#   1. PROJECT_ID          : contents 프로젝트로 교체
#   2. US_PANEL_PREFIX     : "US" → "[US]" / GLOBAL_PANEL_PREFIX : "Global" → "[Global]"
#      ★ 이 프로젝트의 패널명이 `[US] …` / `[Global] …` 형태라 대괄호가 없으면
#        startswith 매칭이 실패해 **US/Global 분리가 통째로 무효화**된다 (us 중복 추출).
#   3. DEVICE_SWAP_CASES + _apply_device_swap() : device 세그 replace 방식 (신규)
#   4. CLI `--device pc|mobile|app|android|ios` : 한 device 만 골라 추출
#
# ── 아래는 범용 v4.3 원본 헤더 (기능 이력) ────────────────────────────────────
# v4.3 (2026-07-29): site ↔ 패널 매칭 2종 추가. 전 사본 공통 코드이며, 동작 여부는 상단 상수로만 갈린다.
#   ① 패널 분류(group) 분기 — 패널을 이름 키워드로 분류하고(PANEL_GROUP_RULES),
#      sites_input 의 분류 컬럼으로 site 마다 어느 분류의 패널을 돌지 정한다.
#      분류값은 자유(B2B/B2C 는 예시). _load_sites_input 이 4-tuple 반환 / _panel_group() 신규.
#      + PANEL_GROUP_YEAR_OFFSETS : 분류별 YEAR_OFFSETS override
#      + PANEL_GROUP_SKIP_SITE_PANEL_RULE : 그 분류는 site↔패널 룰 면제
#      **기본 미사용** — 상수가 전부 비어 있어, 설정하기 전에는 모든 패널을 그대로 추출한다
#   ② SITE_PANEL_SITES — 패널명에 **site_code 가 토큰으로** 들어있으면 그 site 전용 패널로 보고,
#      등록 site 는 전용 패널만 / 미등록 site 는 공용 패널만 돈다 (기존 US·Global 접두 룰 대체).
#      토큰 경계(SITE_PANEL_BOUNDARY)로 'hq' 가 'second'/'section' 에 걸리는 오탐을 막는다.
#      비어 있으면([]) 기존 접두 룰이 그대로 쓰인다. SITE_PANEL_ALIAS 로 패널 키워드 지정 가능.
#   하위호환 3중 안전장치 — 다른 폴더에 그대로 복사해도 조용히 데이터가 빠지지 않게:
#      · PANEL_GROUP_COLUMN 이 비었거나 sites_input 에 그 컬럼이 없으면 → 분류 필터 미적용
#      · 컬럼이 있어도 **그 프로젝트에 해당 분류의 패널이 없으면** 그 site 만 미적용 + 경고
#      · SITE_PANEL_SITES 가 비어 있으면 접두 룰로 폴백
#   ※ v4.2 대비 **입력 상수(PROJECT_ID·LIMIT·MONTHLY·YEAR_OFFSETS 등)는 그대로**. 신규 상수만 추가됨.
# v4.2 (2026-07-24): 기간 분할·연도 shift 상수 2개 추가 (sites_input 에는 항상 site 별 "총기간"을 넣는다) —
#                    MONTHLY (총기간을 달력 월로 쪼개 월마다 dateRange override 로 각각 추출.
#                      출력에 period 컬럼('Jul 2025') 추가 + start_date/end_date 가 그 달 범위.
#                      AA 프로젝트에 daterangemonth breakdown 을 만들 필요가 없고 bd 슬롯은 그대로 남음
#                      → monthly × 기존 breakdown(예: 채널 detail) 병행 가능) +
#                    YEAR_OFFSETS (sites_input 날짜의 연도를 N 만큼 shift. [0,-1] 이면 올해+작년
#                      동기간을 한 실행으로. offset≠0 결과는 파일명에 _y{연도} 태그가 붙어 안 섞임)
#                    → 연도별 폴더 사본(y25/y26) 없이 폴더 1개로 동기간 YoY 추출.
#                    기본값(False, [0])은 v4.1 출력과 100% 동일. CLI --monthly / --year-offsets.
#                    그 외 추출 로직은 v4.1 과 동일.
# v4.1 (2026-07-09): breakdown 깊이/부모행 출력 제어 상수 2개 추가 —
#                    BREAKDOWN_MAX_DEPTH (정수 깊이 캡: -1=무제한, 0=총계만, 1=bd1까지, N=bdN까지) +
#                    INCLUDE_PARENT_ROWS (dim1 총계행 출력 포함 여부; False=breakdown행만 "bd만" 모드).
#                    "총계만 / 총계+bd1 / bd1만" 등을 enum 나열 없이 상수 2개 조합으로 표현.
#                    기본값(-1, True)은 v4.0 출력과 100% 동일. CLI --breakdown-max-depth / --no-parent-rows.
#                    그 외 추출 로직은 v4.0 과 동일.
# v4.0 (2026-06-29): 출력 CSV 쓰기 직후 자가 무결성 검증(_verify_csv_written) 추가 —
#                    stack/table CSV 를 다시 읽어 모든 행의 필드수가 헤더와 일치하는지 확인,
#                    정상이면 ✓(데이터 행수×칼럼수), 불일치 시 ⚠ 경고 + 재추출 권장 (OneDrive 동기화/복사 등 외부 손상 즉시 감지).
#                    + breakdown 단계별 행 cap 분리 — LIMIT_BD/BD2/BD3/BD4 (bd1~4 = level2~5, bd5+ 는 BD4), CLI --limit-bd2~bd4.
#                    + --estimate 사전 추정 모드 — breakdown 단계별 1경로 샘플 측정 → 총 /reports 호출수·ETA 출력 후 추출 생략.
#                    그 외 추출 로직은 v3.9 와 동일.
# 2026-06-15: 진행률 + ETA 콘솔 출력 추가 (VERBOSE_PROGRESS) — site 1개 끝날 때마다
#             [i/N]·소요·추출 row수·누적·평균·남은·전체 한 줄. 남은 = 완료 site 평균소요 × 남은 site 수,
#             전체 = 누적 + 남은 (SITE_WORKERS>1 이면 ÷ 워커수 근사). 추출 로직 불변(출력만 추가).
# v3.9 (2026-06-18): stack CSV 의 metric → metric_origin + 정제 metric 컬럼 추가
#                    (별칭 AppBounce→Bounces, 이벤트 괄호 제거·단위 괄호 유지).
# v3.8 (2026-06-12): device 케이스별 반복 추출 (DEVICE_CASES) —
#                    프로젝트 패널에 device 세그가 전혀 없을 때, 패널마다 (Seg1, Seg2) 세그 stack 을
#                    globalFilter 로 끼워 케이스별로 각각 추출. 케이스 수는 DEVICE_CASES 상수로
#                    자유 증감 (기본 5: PC/Mobile/App/Android/iOS — Downloads\device_case5.csv 참고).
#                    + app_O_X.csv 룰 — App 론치 X site 는 requires_app 케이스 제외(PC/Mobile 만).
#                      `_old` 접미사 site 는 `_old` 뗀 site명의 O/X 를 따름, 미매칭 site 는 경고 후 X 간주.
#                    + DEVICE_CASE_SITE_OVERRIDES — 구/별도 suite 에서 [Global] 세그가 0행을 만드는
#                      site 용 세그 치환 (us_old: [Global] Excluded APP → [US] Excluded APP).
#                    DEVICE_CASES=[] 면 v3.7 과 100% 동일 동작 (옵트인).
# v3.7 (2026-06-12): 레벨별 limit 분리 + 실제 행수 cap 적용 —
#                    LIMIT_LV1(dim1/1st level) / LIMIT_BD(breakdown/2nd level~) 로 분리.
#                    v3.6 까지 LIMIT 은 API page 크기로만 쓰여 페이지네이션(MAX_PAGES)이
#                    계속 돌아 행수 제한이 실제로 안 걸렸음 → _fetch_all_pages 에 max_rows
#                    cap 추가(초과분 truncate). 0 = 무제한(기존 동작). CLI --limit / --limit-bd.
#                    + 출력 CSV 2종 개편:
#                      · stack_data_extract_* (기존 extract_data_*) — long unpivot 유지 (세로 스택).
#                      · table_data_extract_* (기존 column_mapping_* 대체) — AA 테이블 모양 가로형:
#                        1행 = item(또는 breakdown/총계 행), value1..N 컬럼 + seg_value1..N 컬럼.
#                        seg_value{i} = "metric;; segments" (metric 맨앞, 구분자 ';;' — segments
#                        내부 구분자가 '; ' 라 세미콜론 2개로 분리. SEG_VALUE_SEP 상수).
# v3.6 (2026-06-10): site 단위 병렬 처리 포팅 (_contents 시리즈의 SITE_WORKERS) —
#                    SITE_WORKERS>1 이면 여러 site 동시 추출. 동시 API 요청 = SITE_WORKERS × workers.
#                    SITE_WORKERS=1 이면 v3.5 와 100% 동일(순차). CLI --site-workers 로 override.
# v3.5 (2026-06-10): N단계 dimension breakdown 추가 — dim1(행) 각 item 을 하위 차원으로 재귀 분해.
#                    AA /reports 의 type="breakdown" metricFilter 로 조상 (차원,itemId) 체인을 AND.
#                    CSV 에 도달 깊이만큼 bd{k}_dimension/itemId/value 컬럼 셋이 레벨당 추가됨.
#                    BREAKDOWN_ENABLED=False 면 v3.4 와 100% 동일 동작.
# v3.4 (2026-06-04): EXTRA_SEGMENTS name_keywords 패널-우선 해석 추가 (패널 내 1건->자동적용, 2건+->중단)
"""
extract_data — AA Workspace 프로젝트의 panel·reportlet 구조를 여러 site(RSID)로 추출.
한 프로젝트를 site별 RSID + 기간(dateRange) override 로 반복 호출해 long/wide CSV 2종으로 떨군다.

전체 흐름:
  1) 설정 로드 — AUTH(OAuth S2S), PROJECT_ID, sites_input.csv(site_code·총기간), 옵션 상수들.
       · (v4.2) YEAR_OFFSETS 로 sites_input 날짜 연도를 shift 한 run 을 site 당 N개로 확장.
  2) 인증 → 프로젝트 GET → panel 목록 확보. dateRange·segment name 미리 조회(캐시).
  3) site(×연도) 별 반복 (SITE_WORKERS 병렬):
       a. task 생성 = panel × reportlet/table × device case × 기간조각
            (모두 옵션 — panel·table 은 REQUIRED_PANEL_KEYWORDS / REQUIRED_TABLE_KEYWORDS 이름 필터,
             device case 는 DEVICE_CASES, 기간조각은 MONTHLY(v4.2). 비우면/끄면 전체 대상 1회).
       b. 각 task 의 globalFilter 구성:
            · 패널 기존 세그(panel.segmentGroups) 적용 — (옵션) SKIP_PANEL_SEGMENTS 로 패널 세그 전체 무시,
              SKIP_PANEL_SEGMENT_KEYWORDS 로 이름 키워드 매칭 세그만 골라 제외
            · (옵션) EXTRA_SEGMENTS 추가 — 이름→id 최초 1회 확정(+lookup 파일 저장) 후 매 task 적용, enabled 토글로 on/off
            · site 기간(start/end) dateRange override — MONTHLY 면 그 달 조각의 기간
       c. /reports 호출 (MAX_WORKERS 병렬) — 페이지네이션 + 레벨별 행수 cap(LIMIT_LV1 / LIMIT_BD~LIMIT_BD4).
       d. (옵션) N단계 breakdown(BREAKDOWN_ENABLED) — dim1 의 각 item 을 하위 차원으로 재귀 분해 (metricFilter 체인 AND).
       e. 결과를 CSV 2종으로 기록 후 무결성 자가검증(v4.0):
            · stack_data_extract_<site>_<ts>.csv — long unpivot (1행 = item × value_n). RESHAPE 입력용.
            · table_data_extract_<site>_<ts>.csv  — AA 테이블 가로형 (1행 = item, value1..N + seg_value1..N).
  4) site 마다 진행률·ETA 한 줄 출력, 마지막에 전체 요약.

옵션·동작 토글(EXTRA_SEGMENTS / SKIP_PANEL_SEGMENTS / DEVICE_CASES / BREAKDOWN_* / LIMIT_* 등)은
파일 상단 "사용자가 바꿔야 하는 부분" 설정 섹션의 각 상수 주석 참고.
버전별 변경 이력은 위 # 헤더 참고 (여기엔 중복 기재하지 않음).
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests
import aanalytics2 as api2

from site_registry import lookup_site, SiteInfo

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

# ─── 인증 ──────────────────────────────────────────────────────────
# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\path\to\your\aanalytics_auth.json"
COMPANY_ID = "your_aa_company_id"

# ─── 대상 프로젝트 ──────────────────────────────────────────────────
# v1 과 동일 — 같은 project 의 panel/reportlet 구조를 여러 site (rsid) 로 추출


# contents(CC_*) 프로젝트. Tier1 용 프로젝트 하나로 **Tier1·Tier2 전 site** 를 뽑는다.
#   Tier2 전용 프로젝트가 따로 있었지만, 컬럼 구조(5 reportlet × 17 valueN)가 이것과 완전히 같고
#   안 쓰는 자리를 'No Data' 세그로 메워 자리수만 맞춘 사본이었다 → Tier1 이 상위집합이라 통합.
#   Tier2 site 는 정제 단계에서 valueN(1,5,6,7,8)만 골라 쓴다 (RESHAPE 의 TIER2_VALUE_N).
PROJECT_ID = "YOUR_PROJECT_ID"  # CAMPAIGN NAME | Contents Click Analysis | API
# https://experience.adobe.com/#/@company_name/so:your_aa_company_id/analytics/spa/#/workspace/edit/YOUR_ID


# ─── input / 출력 ──────────────────────────────────────────────────
SITES_INPUT_CSV = Path(__file__).resolve().parent / "sites_input.csv"
OUTPUT_DIR      = Path(__file__).resolve().parent / "output"
# 출력 CSV 파일명 prefix. "" = 기존 동작. 예: "excl-seg_"(세그 제외 적용본), "full_"(세그 없는 전체)
OUTPUT_PREFIX   = ""
# 출력 CSV basename (v3.7 파일명 개편):
#   stack = 기존 extract_data_* (long unpivot, 1행 = item × value_n. RESHAPE 입력용 세로 스택)
#   table = 기존 column_mapping_* 대체 (AA 테이블 모양 가로형, 1행 = item. 아래 SEG_VALUE_SEP 참고)
OUTPUT_BASENAME_STACK = "stack_data_extract"
OUTPUT_BASENAME_TABLE = "table_data_extract"
# ─── 같은 site 가 group(B2B/B2C) 별로 여러 행일 때 파일명 충돌 방지 (2026-07-31) ──
# sites_input 에 같은 site_code 가 group 별로 2행 이상 있으면 offset 0 출력 경로가 완전히 같아져
# **나중에 끝난 run 이 앞 run 파일을 덮어쓴다** → 앞 run 데이터가 통째로 사라진다.
#   실측(2026-07-31): ca/in/sg 등의 **2026 B2B 가 B2C run 에 덮여 유실**. SITE_WORKERS 병렬이라
#   중간 시점에 따라 어느 쪽이 남는지도 달라져(경합) run 마다 결과가 바뀌었다. 에러가 안 나서 발견이 늦다.
# True = 그런 site 에 한해 파일명 site 뒤에 _{group} 을 붙여 분리한다 (단일 group site 는 무변경).
#   stack_data_extract_ca_B2B_{ts}.csv / stack_data_extract_ca_B2C_{ts}.csv
#   stack_data_extract_us_old_{ts}.csv        ← group 1개뿐이라 이름 그대로
# ⚠ 한 파일에 append 하는 방식은 못 쓴다 — 헤더가 run 단위로 계산돼 어긋난다
#    (dim1 컬럼명 evar73/daterangeyear, bd{k}_* 컬럼 수 17 vs 20).
# ⚠ 이름 규칙이 바뀌므로 **옛 이름 파일과 한 output 폴더에 섞지 말 것** — RESHAPE 는 파일명으로
#    site 를 구분해 'ca_260729_*'(옛) 와 'ca_B2B_260731_*'(신) 를 별개 site 로 보고 둘 다 union 에 넣는다.
GROUP_TAG_IN_FILENAME: bool = True
# seg_value{i} 컬럼 구분자 (table CSV) — "metric;; segments" 형태로 metric 을 맨앞에 두고 결합.
# segments 내부 구분자가 '; ' 라 세미콜론 2개(';;')로 분리 (split 시 SEG_VALUE_SEP 로 1회 split).
SEG_VALUE_SEP = ";; "

# ─── 요청 설정 ─────────────────────────────────────────────────────
MAX_WORKERS = 6
# site 단위 병렬 워커 수 (1 = 순차, v3.5 동일). 동시 API 요청 ≈ SITE_WORKERS × MAX_WORKERS — AA throttling 주의.
# 429 자주 보이면 5 → 3 → 2 로 줄이기. CLI --site-workers 로 override.
SITE_WORKERS = 5
REQUEST_TIMEOUT = 600
MAX_RETRIES = 10
# 레벨별 행 수 상한 (v3.7) — 0 = 무제한 (MAX_PAGES 페이지네이션까지 전체 수집, v3.6 동작)
#   ※ 용어: dim1(테이블 행 차원) 자체가 level1. 첫 breakdown 이 level2 → breakdown N단계 = level(N+1).
#   LIMIT_LV1 : dim1 = level1. reportlet 당 최대 행 수. CLI --limit.
#   LIMIT_BD  : breakdown 1단계 (bd1, = level2) 부모 item 1개당 최대 하위 행 수. CLI --limit-bd.
#   LIMIT_BD2 : breakdown 2단계 (bd2, = level3) 〃. CLI --limit-bd2.
#   LIMIT_BD3 : breakdown 3단계 (bd3, = level4) 〃. CLI --limit-bd3.
#   LIMIT_BD4 : breakdown 4단계+ (bd4~, = level5~) 〃. CLI --limit-bd4.
#               (dim1 포함 총 레벨 = breakdown 단계 + 1 → bd4 까지면 최대 5레벨. 테이블 실제 깊이까지만 적용)
#   ※ BREAKDOWN_TOP_N > 0 이면 breakdown 은 TOP_N 이 우선 (레벨별 상위 N 만 분해).
LIMIT_LV1 = 50000
LIMIT_BD  = 50000
LIMIT_BD2 = 15
LIMIT_BD3 = 15
LIMIT_BD4 = 15
MAX_PAGES = 100   # 무제한(0) 모드 페이지네이션 상한 (PAGE_SIZE_UNCAPPED × MAX_PAGES / reportlet)

# 진행률 + ETA 콘솔 출력 — site 1개 끝날 때마다 [i/N]·소요·row수·누적·평균·ETA 한 줄.
#   ETA = (완료 site 평균 소요) × 남은 site 수. SITE_WORKERS>1 이면 ÷ 워커수 (병렬이라 근사 '~').
#   False = 끄기 (기존 site별 summary 출력은 그대로 유지).
VERBOSE_PROGRESS = True

# ─── panel 필터 (v1 동일) ──────────────────────────────────────────
# 처리 대상 panel 을 이름으로 좁히는 필터.
#   []        → 모든 panel 처리 (기본)
#   [kw, ...] → panel.name 에 키워드 하나라도 포함된 panel 만 처리 (OR 매칭, 대소문자 구분),
#               나머지 panel 은 자동 skip
# 예: ["[Global]"]         → [Global] 로 시작하는 panel 만
#     ["Revisit", "B2B"]   → 이름에 Revisit OR B2B 포함된 panel 만
# 참고: EXTRA_SEGMENTS 의 panel_scope 와 역할이 다름.
#   - REQUIRED_PANEL_KEYWORDS : 그 panel 자체를 "처리할지 말지"
#   - panel_scope             : 처리되는 panel 중 추가 segment 를 "적용할지 말지"
REQUIRED_PANEL_KEYWORDS: list[str] = []

# ─── table(reportlet) 필터 ──────────────────────────────────────────
# 처리 대상 reportlet(테이블)을 이름으로 좁히는 필터 (panel 통과 후 추가 적용).
#   []        → panel 안 모든 freeform 테이블 처리 (기본)
#   [kw, ...] → reportlet.name 에 키워드 하나라도 포함된 테이블만 처리 (OR, 대소문자 구분)
# 예: ["Watch Visit debug"] → 그 이름 포함 테이블 1개만 (나머지 테이블 skip)
REQUIRED_TABLE_KEYWORDS: list[str] = []

# ─── 기간 분할 추출 / 연도 shift (v4.2) ─────────────────────────────
# sites_input.csv 에는 항상 site 별 **총기간**(start_date~end_date)을 넣는다.
# 아래 두 상수가 그 총기간을 "어떻게 뽑을지"(추출 방식)를 정한다.
#
# MONTHLY : 총기간을 통으로 1회 뽑을지, 달력 월 단위로 쪼개 월마다 뽑을지.
#   False → 총기간을 쪼개지 않고 1회로 추출 (period 컬럼 없음)
#   True  → 총기간을 달력 월로 쪼개 월마다 dateRange override 로 각각 추출.
#           · 출력에 period 컬럼 추가 ('Jul 2025' — AA daterangemonth 표기와 동일),
#             start_date/end_date 는 그 달 범위로 기록 (양 끝 부분월은 총기간에 맞춰 잘림.
#             예: 총기간 2025-07-06~07-21 → 'Jul 2025' 한 조각).
#           · AA 프로젝트에 daterangemonth breakdown 을 미리 만들 필요가 없다(어떤 프로젝트든 월별 가능).
#           · bd 슬롯을 안 쓰므로 기존 breakdown(예: 채널 detail)과 **병행** 가능.
#           · task 수 = 패널 × 테이블 × device케이스 × 월수 — API 호출 그만큼 증가.
MONTHLY: bool = False

# YEAR_OFFSETS : sites_input 날짜의 **연도**를 N 만큼 shift 해서 추출 (동기간 YoY 비교용).
#   [0]         → sites_input 날짜 그대로 1 run (파일명에 연도 태그 없음)
#   [0, -1]     → 올해 + 작년 동기간을 한 실행으로 (site 당 2 run)
#   [-2,-1,0], [0,1] 처럼 개수·부호 자유. 2/29 는 shift 후 없는 날이면 2/28 로 clamp.
#   ※ offset≠0 인 run 의 출력 파일명에는 `_y{연도}` 태그가 붙는다
#     (같은 output 폴더에서 연도별 파일이 안 섞이고, RESHAPE 의 "site별 최신 1개" 선택도 연도별로 분리).
#   → 연도별 폴더 사본(y25/y26)을 만들지 않고 폴더 1개로 두 연도를 뽑기 위한 옵션.
YEAR_OFFSETS: list[int] = [0]

# ─── N단계 breakdown (행 차원 재귀 분해) ────────────────────────────
# dim1(행 = dimensionSettings[0]) 의 각 item 을 하위 차원으로 분해해서 추출.
#   BREAKDOWN_ENABLED  : False 면 v3.4 동작(분해 안 함, dim1 만).
#   BREAKDOWN_DIMENSIONS : dim1 다음에 분해할 차원 id 순서 리스트.
#       []           → 테이블 freeformTable.breakdowns 의 nested 차원을 깊이순 자동감지(권장).
#       [id, ...]    → 자동감지 무시하고 이 순서로 분해. 예: ["variables/product"]
#                      3단계 테스트: ["variables/product", "variables/evar5"]
#   BREAKDOWN_TOP_N    : 0 = 각 레벨 전체 item 분해. N>0 = 레벨별 (정렬된) 상위 N item 만 분해.
#                        깊을수록 호출이 곱연산으로 늘어 → 테스트/성능시 N 으로 제한.
BREAKDOWN_ENABLED: bool = True
BREAKDOWN_DIMENSIONS: list[str] = []
BREAKDOWN_TOP_N: int = 0
#   BREAKDOWN_MAX_DEPTH (v4.1): breakdown 깊이 캡. 정수 1개로 모든 깊이 표현 (enum 나열 회피).
#       -1(또는 <0) → 무제한 (자동감지된 차원 / DIMENSIONS 전부까지 분해)
#        0          → 분해 안 함 (dim1 총계만 — BREAKDOWN_ENABLED=False 와 동일 효과)
#        1 = bd1까지, 2 = bd2까지, ... N = bdN까지
#   INCLUDE_PARENT_ROWS (v4.1): dim1 총계(부모) 행을 출력 CSV(stack/table)에 포함할지.
#        True  → 총계행 + breakdown행 둘 다 출력
#        False → breakdown행만 (총계행 제외, "bd만" 모드)
#   ※ 속도: INCLUDE_PARENT_ROWS True/False 는 API 호출량 동일 → 속도차 사실상 없음.
#     dim1(Lv1) 추출은 breakdown 부모 목록 확보용으로 항상 필요(_run_breakdowns frontier),
#     이 상수는 그 총계행을 CSV 에 쓰냐 마냐(디스크 몇 행)만 결정. 즉 "bd1까지"(PARENT=True)와
#     "bd1만"(PARENT=False)은 같은 시간. 속도를 줄이는 건 BREAKDOWN_MAX_DEPTH(깊이↓ = /reports 호출 곱연산↓),
#     INCLUDE_PARENT_ROWS 아님.
#   조합 예: 총계만=(MAX_DEPTH=0) · 총계+bd1=(MAX_DEPTH=1,PARENT=True) · bd1만=(MAX_DEPTH=1,PARENT=False)
BREAKDOWN_MAX_DEPTH: int = -1
INCLUDE_PARENT_ROWS: bool = True
# v4.0: --estimate 시 True — breakdown 단계별 1경로 샘플만 떠서 총 /reports 호출수·ETA 추정 후 추출 생략 (CLI 전용 런타임 플래그)
ESTIMATE_ONLY: bool = False

# ─── device 컬럼 (컬럼 stack 세그먼트명에서 device 추출) ─────────────
# 각 컬럼(value_n)은 세그먼트가 stack 돼있고, 그 중 device 세그(`[Device] Mobile`,
# 맨 `Mobile`, `Mobile (Visit)` 등)가 있으면 그 device 를 별도 `device` 컬럼으로 뽑는다.
# device 종류: Mobile / PC / Android / iOS / App.
#   DEVICE_FROM_SEGMENT  : True 면 device 컬럼 추가, False 면 추가 안 함.
#   DEVICE_APP_ONLY_PATTERN : 이 패턴(예 "App Only")이 stack 에 있으면 App 컨텍스트.
#       └ 샌드위치(App Only 하위로 All Visit/Android/iOS 로 갈림)일 땐 같은 컬럼 stack 에
#         Android/iOS 토큰이 있으면 그 플랫폼을, 없으면(All Visit 쪽) "App" 을 device 로.
#         (App Only 컨텍스트가 일반 규칙보다 우선)
#   DEVICE_SEGMENT_RULES : App 컨텍스트가 아닐 때 적용. (정규식, 라벨) 순서 리스트 — 위에서부터
#       먼저 매칭되는 게 채택. 세그명 어느 하나라도 매칭되면 그 컬럼 device 로 지정. 대소문자 무시.
#       `[Device] X` 형식을 일반 토큰보다 앞에 둬서 우선 매칭.
DEVICE_FROM_SEGMENT: bool = True
DEVICE_APP_ONLY_PATTERN: str = r"\bApp\s*Only\b"
DEVICE_SEGMENT_RULES: list[tuple[str, str]] = [
    (r"\[Device\]\s*Mobile",      "Mobile"),
    (r"\[Device\]\s*PC",          "PC"),
    (r"\[Device\]\s*Android",     "Android"),
    (r"\[Device\]\s*iOS",         "iOS"),
    (r"\[Device\]\s*Others",      "Others"),
    (r"\bAndroid\b",              "Android"),
    (r"\biOS\b",                  "iOS"),
    (r"\b(?:PC|Desktop)\b",       "PC"),
    (r"\bMobile\b",               "Mobile"),
    (r"\bApp\b",                  "App"),
]

# ─── device 케이스별 반복 추출 (v3.8) ────────────────────────────────
# 프로젝트 패널에 device 세그가 전혀 없을 때, 패널마다 세그 stack 을 globalFilter 로
# 끼워 케이스별로 각각 추출. 케이스 1개 = dict 1개 — 리스트에 추가/삭제로 자유 증감.
#   device       : 출력 CSV device 컬럼에 박히는 라벨
#   segment_ids  : 그 케이스에 끼울 세그 ID 들 (개수 자유)
#   requires_app : True 면 app_O_X.csv 의 O site 에서만 추출 (X site 는 이 케이스 skip)
# [] 면 v3.7 과 100% 동일 동작 (케이스 반복 없이 1회 추출).
# ※ task 수 = 패널 × 테이블 × 케이스 수 — API 호출 그만큼 증가. 429 빈발 시 --site-workers 축소.
# ※ 대상 프로젝트 패널에 device 세그가 이미 있으면 이 옵션 불필요 — 기본 전부 주석(비활성).
#    패널에 device 세그가 없을 때만 아래 케이스들 주석 해제해서 사용.
DEVICE_CASES: list[dict] = [
    # {"device": "PC",      "segment_ids": ["세그먼트_아이디_넘버",    # [Global] Excluded APP
    #                                       "세그먼트_아이디_넘버"]},  # PC User (Visit)
    # {"device": "Mobile",  "segment_ids": ["세그먼트_아이디_넘버",    # [Global] Excluded APP
    #                                       "세그먼트_아이디_넘버"]},  # Mobile User (Visit)
    # {"device": "App",     "segment_ids": ["세그먼트_아이디_넘버",    # [Global] App Only
    #                                       "세그먼트_아이디_넘버"],   # All VISITS
    #  "requires_app": True},
    # {"device": "Android", "segment_ids": ["세그먼트_아이디_넘버",    # [Global] App Only
    #                                       "세그먼트_아이디_넘버"],   # Android - Visit
    #  "requires_app": True},
    # {"device": "iOS",     "segment_ids": ["세그먼트_아이디_넘버",    # [Global] App Only
    #                                       "세그먼트_아이디_넘버"],   # IOS (Visit)
    #  "requires_app": True},


    # ── 참고: CAMPAIGN NAME CID vs 25 YM reference 테이블의 단일 [Device] 세그 5종 (필요 시 주석 해제) ──
    # {"device": "PC",      "segment_ids": ["세그먼트_아이디_넘버"]},  # [Device] PC
    # {"device": "Mobile",  "segment_ids": ["세그먼트_아이디_넘버"]},  # [Device] Mobile
    # {"device": "Android", "segment_ids": ["세그먼트_아이디_넘버"]},  # [Device] Android
    # {"device": "iOS",     "segment_ids": ["세그먼트_아이디_넘버"]},  # [Device] iOS
    # {"device": "Others",  "segment_ids": ["세그먼트_아이디_넘버"]},  # [Device] Others
]

# ═══════════════════════════════════════════════════════════════════════
# ★ contents 전용 — device 세그 치환(swap) 케이스
# ═══════════════════════════════════════════════════════════════════════
# 위 DEVICE_CASES(add) 와 **목적은 같고 방식이 반대**다.
#   · DEVICE_CASES  (add)     : 패널에 device 세그가 **없을 때** globalFilter 에 덧붙인다.
#   · DEVICE_SWAP_CASES (replace): 패널에 device 세그가 **이미 있을 때** 그걸 갈아끼운다.  ← contents
# contents 패널에는 `PC User (Visit)` + `[Global] Excluded APP` 이 기본으로 박혀 있어서,
# PC 를 base 로 두고 그 세그 ID 를 device 별로 치환해 같은 테이블을 5번 뽑는다.
#
# 동작:
#   payload 의 globalFilters + metricContainer.metricFilters 안 segmentId 를 replace 표대로 바꾸고,
#   출력 CSV 의 `segments` 컬럼에 드러나도록 세그 **이름**도 같이 바꾼다
#   (정제코드가 이 segments 값에서 ITEM 을 뽑으므로 이름 치환이 빠지면 ITEM 이 틀어진다).
#
# 케이스 추가/삭제는 리스트에서 dict 를 넣고 빼면 된다.
#   device       : 출력 CSV `device` 컬럼에 박히는 라벨
#   replace      : {바꿀_세그_ID: 새_세그_ID}  — 여러 개 가능. 빈 dict = 치환 없음(= base PC)
#   requires_app : True 면 app_O_X.csv 가 'O' 인 site 에서만 추출 (X site 는 이 케이스 skip)
#
# ⚠ DEVICE_CASES(add) 와 **동시에 켜지 말 것** — 세그가 중복으로 걸려 결과가 0 이 된다.
DEVICE_SWAP_ENABLED: bool = True

# 패널에 원래 박혀 있는 세그 (치환 대상) — 프로젝트가 바뀌면 이 두 개를 먼저 확인할 것
BASE_PC_NAME           = "PC User (visit)"
BASE_PC_ID             = "세그먼트_아이디_넘버"
BASE_EXCLUDED_APP_NAME = "[Global] Excluded APP"
BASE_EXCLUDED_APP_ID   = "세그먼트_아이디_넘버"

# 치환에 쓰는 세그 (name 은 CSV segments 컬럼 표기에 그대로 들어간다)
_SEG = {   # 라벨 → (name, id)
    "mobile":     ("Mobile User (Visit)", "세그먼트_아이디_넘버"),
    "android":    ("Android - Visit",     "세그먼트_아이디_넘버"),
    "ios":        ("iOS - Visit",         "세그먼트_아이디_넘버"),
    "app_only":   ("[Global] App Only",   "세그먼트_아이디_넘버"),
    "all_visit":  ("All Visit",           "세그먼트_아이디_넘버"),
}

DEVICE_SWAP_CASES: list[dict] = [
    # PC = base. 패널 원본 그대로라 치환 없음.
    {"device": "pc",      "replace": {}},
    # Mobile = PC 세그만 Mobile 로.
    {"device": "mobile",  "replace": {BASE_PC_ID: _SEG["mobile"]}},
    # App 계열 = 'Excluded APP'(앱 제외) 을 'App Only'(앱만) 로 뒤집어야 하므로 2개를 함께 치환.
    {"device": "app",     "replace": {BASE_PC_ID:           _SEG["app_only"],
                                      BASE_EXCLUDED_APP_ID: _SEG["all_visit"]},
     "requires_app": True},
    {"device": "android", "replace": {BASE_PC_ID:           _SEG["android"],
                                      BASE_EXCLUDED_APP_ID: _SEG["app_only"]},
     "requires_app": True},
    {"device": "ios",     "replace": {BASE_PC_ID:           _SEG["ios"],
                                      BASE_EXCLUDED_APP_ID: _SEG["app_only"]},
     "requires_app": True},
]

# ⚠ app 미론치(app_O_X.csv = X) site 도 **추출 단계에서는 거르지 않는 게 기본**이다.
#   requires_app 로 여기서 skip 하면 그 행이 아예 안 생겨 정제 결과에 `VALUE (원본)` 실측값이
#   남지 않는다. 정제코드가 App-X site 의 값을 0 으로 **표시만** 하고 원본은 보존하는 구조라,
#   추출은 5종을 다 뽑고 0 처리는 정제에 맡긴다. (True 로 바꾸면 추출 단계에서 skip)
DEVICE_SWAP_APPLY_APP_OX: bool = False

# 응답이 완전히 빈 테이블(rows·summary 둘 다 없음)도 컬럼별 0 행을 남길지.
#   True(기본) : 구 column_mapping 과 같은 행 구성 유지 — "0 이었다"가 출력에 남는다.
#   False      : 빈 테이블은 출력에서 생략 (범용 v4.3 기본 동작).
# ⚠ False 로 두면 데이터 없는 device 가 통째로 사라져, 나중에 "안 뽑힌 건지 0 인지" 구분이 안 된다.
#   실제 사례: us_old(구 US suite) 의 app — `[Global] App Only` 가 그 suite 에 데이터가 없다.
EMIT_ZERO_ROWS_WHEN_EMPTY: bool = True

# App 론치 O/X csv — 컬럼: site_code, App 론치 (O/X). X site 는 requires_app 케이스 제외.
# `_old` 접미사 site 가 csv 에 없으면 `_old` 뗀 site명의 O/X 를 따름. 그래도 없으면 경고 후 X 간주.
# 파일 자체가 없으면 전 site O 간주 (모든 케이스 추출).
APP_OX_CSV = Path(__file__).resolve().parent / "app_O_X.csv"

# site 별 device 세그 치환 — {site_code: {원본_seg_id: 대체_seg_id}}.
# 구/별도 suite 라서 [Global] 세그가 0행을 만드는 site 용. DEVICE_CASES 사용 시에만 의미 있음.
# 검증(2026-06-12): us_old(구 US suite)는 [Global] Excluded APP / App Only 가 0행
#   (PC User/Mobile User 는 정상) → Excluded APP 을 [US] 버전으로 치환하면 정상.
DEVICE_CASE_SITE_OVERRIDES: dict[str, dict[str, str]] = {
    # "us_old": {
    #     "세그먼트_아이디_넘버":   # [Global] Excluded APP
    #     "세그먼트_아이디_넘버",   # → [US] Excluded APP
    # },
}


# ─── site × panel prefix 룰 ─────────────────────────────────────────
# [US] panel 은 us site 에서만 추출 (다른 site 일 땐 자동 skip).
# [Global] panel 은 기본 모든 site 에서 추출. 단 us 에서는 [US] 와 중복되니
# 기본 skip — 같이 뽑고 싶으면 --include-global-for-us flag.
#   예) INCLUDE_GLOBAL_FOR_US = False → us_old(미국 구suite) 추출 시 [Global] 패널 skip
#       (=> [US] 패널만; us_old 의 글로벌 데이터는 us(신suite) 행이 [Global] 로 담당 → 미국 중복 방지)
#       INCLUDE_GLOBAL_FOR_US = True  → us_old 추출 시 [Global] 패널까지 함께 추출
#       (=> [US] + [Global] 둘 다; us 행 없이 us_old 한 site 로 글로벌 패널까지 받고 싶을 때)
US_SITE_CODE         = "us_old"
# ★ contents 프로젝트는 패널명이 '[US] …' / '[Global] …' 형태 — 대괄호를 반드시 포함할 것.
#   범용 v4.3 기본값("US"/"Global")을 그대로 두면 startswith 가 안 걸려 이 룰이 통째로
#   무효화되고, us_old 와 us 가 같은 [Global] 패널을 중복 추출한다.
US_PANEL_PREFIX      = "[US]"
GLOBAL_PANEL_PREFIX  = "[Global]"
INCLUDE_GLOBAL_FOR_US = False  # CLI --include-global-for-us 로 override

# ─── site_code ↔ 전용 패널 매칭 (접두 룰 대체, positive 매핑) ────────
# 위 접두(prefix) 룰은 "US"/"Global" 두 단어에만 걸리는 negative skip 이라,
# 그 밖의 패널(예: 'HQ B2B')은 전 site 에서 그냥 돌아 빈 결과만 만든다.
# 아래 목록에 site_code 를 넣으면 **패널명에 그 site_code 가 들어간 패널에서만** 그 site 를 뽑는다.
#
#   SITE_PANEL_SITES = []                 → 미사용. 위 US/Global 접두 룰만 적용된다.
#   SITE_PANEL_SITES = ["us_old","hq"]   → 접두 룰 **대신** 아래 규칙 적용:
#       · 패널명에 등록 site_code 가 든 패널 = 그 site 전용 → 그 site 에서만 RUN
#       · 어느 등록 site_code 도 없는 패널 = 공용 → **미등록 site 에서만** RUN
#         (등록 site 는 전용 패널만 봄. INCLUDE_GLOBAL_FOR_US=True 면 공용 패널도 같이 RUN)
#
# 매칭은 **토큰 경계** 기준(대소문자 무시) — 영숫자·언더바가 아닌 문자(또는 문자열 끝)로 둘러싸여야 한다.
#   'hq'    → 'HQ B2B' ✓ / '[HQ]' ✓ / 'HQ - B2B' ✓ / 'second' ✗ 'insect' ✗ 'section' ✗
#   'us_old' → 'US_old B2B (Y25용)' ✓ / 'US B2B' ✗ (us 뒤가 언더바라 'us' 토큰도 아님)
# 패널명이 site_code 와 다르게 적혀 있으면 SITE_PANEL_ALIAS 로 키워드를 따로 지정한다.
SITE_PANEL_SITES: list[str] = []
# ─── 등록 site 의 공용 패널 추가 허용 (positive 예외, 2026-07-31) ────
# 위 SITE_PANEL_SITES 에 등록된 site 는 기본적으로 **전용 패널만** 본다(all-or-nothing).
# 그런데 어떤 site 는 전용 패널 + 공용 패널을 함께 필요로 한다 — 이 캠페인의 `us` 가 그렇다:
#     us = 전용 B2B 패널 'US B2B (Y26용)'  +  공용 B2C 패널 'Global B2C'
# 여기에 {site_code: [패널명 키워드, ...]} 를 적으면 그 site 는 전용 패널에 **더해** 이 공용 패널도 돈다.
# 매칭은 부분일치(대소문자 무시).
#
# 왜 필요한가: 이게 없으면 "us 의 B2B 는 전용 패널, B2C 는 공용 패널" 을 표현할 방법이
# sites_input 의 group 컬럼(B2B/B2C 2행)밖에 없었다. 그 2행 구조가 같은 출력 경로를 놓고
# 경합해 **2026 B2B 가 통째로 유실되는 사고**(2026-07-31)를 냈다.
# 이 상수로 site 당 1행 + 한 파일에 전 패널을 담는 구조가 가능해진다
# (B2B/B2C 구분은 정제 단계에서 panel 컬럼으로 한다).
SITE_EXTRA_PANELS: dict[str, list[str]] = {}   # 예: {"us": ["Global B2C"]}
SITE_PANEL_ALIAS: dict[str, list[str]] = {}   # 예: {"us_old": ["US_old", "US old"]}
# 토큰 경계 정규식 — {kw} 자리에 키워드가 escape 되어 들어간다. 경계를 바꾸고 싶으면 여기서 조정.
SITE_PANEL_BOUNDARY = r"(?<![0-9A-Za-z_]){kw}(?![0-9A-Za-z_])"

# ─── 패널 분류(group) × site 매칭 ───────────────────────────────────
# **기본 미사용**(아래 값이 전부 비어 있음). 쓰려면 이 폴더에서만 값을 채운다.
#
# 무엇을 하나: 패널을 이름 키워드로 몇 개 분류로 나누고, sites_input.csv 의 분류 컬럼으로
#   site 마다 "이 site 는 어느 분류의 패널에서 뽑을지" 를 정한다.
#   분류가 안 맞는 패널은 그 site 에서 skip 된다.
#
# 설정 예 (BTS 캠페인의 B2B / B2C 분리 — **어디까지나 예시**다):
#     PANEL_GROUP_COLUMN        = "B2B_B2C"
#     PANEL_GROUP_RULES         = [("B2B", "B2B"), ("B2C", "B2C")]
#     PANEL_GROUP_SITE_DEFAULT  = "B2B"
#     PANEL_GROUP_PANEL_DEFAULT = "B2B"
#   → sites_input 의 us 행이 B2C 면 'Global B2C' 패널만 돌고 'Global B2B' 는 skip.
#   분류값은 자유다. 예컨대 ("Mobile","MO")/("PC","PC") 처럼 디바이스로 나눠도 된다.
#
# ⚠ RULES 는 **위에서부터 첫 매칭**이라 순서가 중요하다. 한 패널명이 여러 키워드를 갖는 경우
#    더 구체적인 쪽을 위에 둔다. (예: '② Global - B2B (B2C RS)' 는 B2B·B2C 를 다 가지므로
#     B2B 를 먼저 둬야 B2B 로 잡힌다. B2C 를 먼저 두면 B2C 로 오분류.)
#
# 안전장치 3중 — 다른 폴더에 그대로 복사해도 조용히 데이터가 빠지지 않는다.
# 아래 어느 경우든 **분류 필터를 적용하지 않고 그 site 에서 모든 패널을 추출**한다:
#   · PANEL_GROUP_COLUMN 이 비어 있을 때 (지금 기본값 — 기능 미사용)
#   · 컬럼명을 넣었는데 sites_input 헤더에 그 컬럼이 없을 때
#   · 컬럼이 있어도 그 프로젝트에 해당 분류의 패널이 하나도 없을 때 (그 site 만, 경고 출력)
PANEL_GROUP_COLUMN        = ""   # sites_input.csv 의 분류 컬럼명. "" 면 기능 미사용
PANEL_GROUP_RULES: list[tuple[str, str]] = []   # [(패널명 키워드, 분류값), ...] 위에서부터 부분일치
PANEL_GROUP_SITE_DEFAULT  = ""   # 컬럼은 있는데 셀이 빈 site 의 분류
PANEL_GROUP_PANEL_DEFAULT = ""   # 어느 키워드에도 안 걸리는 패널의 분류
PANEL_GROUP_OFF           = ""   # 미사용 sentinel — 이 값이면 분류 필터를 적용하지 않는다

# ─── 분류별 YEAR_OFFSETS override ───────────────────────────────────
# 여기 등록된 분류는 전역 YEAR_OFFSETS 대신 이 offset 목록만 돈다 (미등록 분류 = YEAR_OFFSETS).
# 기본 미사용({}). 특정 분류만 연도 확장을 막고 싶을 때 쓴다.
#   예) {"B2C": [0]} — 그 분류 site 는 올해만. 다른 연도는 sites_input 에 행을 따로 넣는다.
#       (BTS 사례: B2C 는 us 신 suite 뿐이라 연도 -1 이 빈 run 이 됨. 2025 는 us_old 행으로 별도 추출)
PANEL_GROUP_YEAR_OFFSETS: dict[str, list[int]] = {}

# ─── site↔패널 룰 면제 분류 ─────────────────────────────────────────
# 여기 등록된 분류는 아래 SITE_PANEL_SITES / US·Global 접두 룰을 적용하지 않는다.
# 기본 미사용([]). 그 분류에 패널이 하나뿐이라 중복 위험이 없을 때 쓴다.
#   예) ["B2C"] — B2C 패널이 'Global B2C' 하나뿐이면 접두 룰이 오히려 방해가 된다
#       (us_old 행이 "us site → Global panel skip" 에 걸려 아무 패널도 못 돌게 됨)
PANEL_GROUP_SKIP_SITE_PANEL_RULE: list[str] = []

# ─── 추가 세그먼트 (이름 검색 → globalFilter 적용) ─────────────────
# 비어있으면 v2 와 동일 동작. 항목 하나 = 추가 segment 1개.
# 항목은 segment_id (직접 지정) 또는 name_keywords (이름 검색) 둘 중 하나 사용.
#   segment_id    : 세그먼트 ID 직접 지정 — "세그먼트_아이디_넘버"
#                   검색 단계 생략, 바로 globalFilter 에 추가. lookup CSV/DSL 도 생성 안 함.
#                   (이미 lookup 으로 ID 확정한 경우 이게 가장 빠름)
#   name_keywords : 세그먼트 이름 검색. 두 가지 형식 지원:
#                   1) 풀네임 문자열 — "visitor id = d=mid, null (Exclude)"
#                      Adobe `name` 필터 = case-insensitive substring contains.
#                      이 문자열을 포함하는 모든 세그 반환 (완전 일치 + 더 긴 이름도).
#                   2) AND 키워드 리스트 — ["visitor id", "d=mid", "null", "Exclude"]
#                      첫 키워드 = server-side `name` 필터,
#                      나머지 = client-side AND (name + description, case-insensitive).
#                      가장 specific 한 키워드를 앞에 두는 게 효율적.
#   panel_scope   : "all"  → 모든 panel 에 적용 (생략 시 기본값)
#                   ["키워드1", ...] → panel.name 에 키워드 포함 시 적용 (OR, case-insensitive)
#   enabled       : 이 EXTRA 세그를 globalFilter 에 "추가할지 말지" 스위치 (include/exclude 가 아님).
#                   추가했을 때 포함/제외 효과는 세그 정의 자체가 결정 (Exclude 세그면 제외 효과).
#                   ┌ 패널에 그 세그 없음    + True  → 추가됨 (Exclude 세그면 제외 효과)
#                   ├ 패널에 그 세그 없음    + False → 안 넣음 → 그 세그 없는 상태로 추출
#                   ├ 패널에 그 세그 이미 있음 + True  → 패널 + EXTRA 중복 적용
#                   └ 패널에 그 세그 이미 있음 + False → EXTRA 중복만 빠짐 (패널 세그는 남아 계속 적용)
#                   ※ enabled 는 EXTRA_SEGMENTS 만 제어. 패널 자체 segmentGroups 는
#                     SKIP_PANEL_SEGMENTS 로 따로 제어 (이미 박힌 세그 빼려면 그쪽).
#                   키 없으면 True (적용).
EXTRA_SEGMENTS: list[dict] = [
    # ── 줄 안 지우고 끄기: "enabled": False ──
    # 적용 중인 항목을 삭제하지 말고 "enabled": False 만 붙이면 그 세그만 스킵됨 (나중에 True 로 되살리기 쉬움).
    # 예) {"segment_id": "세그먼트_아이디_넘버", "panel_scope": "all", "enabled": False},
    #     → 이 Exclude 세그를 빼고 "세그 없는 전체(full population)"로 재추출 (OUTPUT_PREFIX="full_" 권장)
    # ── name_keywords 패널-우선 해석 (v3.4) ──
    # 키워드는 먼저 "프로젝트 패널 안 세그(segmentGroups)"에서 매칭됨:
    #   패널 내 1건  → 그 세그 바로 적용 (회사 전체 검색 생략, 모호성 없음)
    #   패널 내 2건+ → 중단 (segment_id 직접 지정하거나 더 세밀한 키워드로)
    #   패널 내 0건  → 회사 전체 검색 fallback
    # 예) {"name_keywords": ["visitor id", "d=mid", "null"], "panel_scope": "all"},
    #     → 패널 안에 그 키워드들이 모두 포함된 세그가 1개뿐이면 그걸로 자동 확정
    # 예시 1 — ID 직접 지정 (이름 검색 생략):
    # {"segment_id": "세그먼트_아이디_넘버", "panel_scope": "all"},  # visitor id d=mid null (Exclude)
    # {"segment_id": "세그먼트_아이디_넘버", "panel_scope": "all"},  # [US] Excluded B2B
    # {"segment_id": "세그먼트_아이디_넘버", "panel_scope": "all" },  # [US] Excluded APP
    # 예시 2 — 풀네임 substring 검색:
    # {"name_keywords": "visitor id = d=mid, null (Exclude)"},
    # 예시 3 — AND 키워드 형식 / panel 일부 적용:
    # {
    #     "name_keywords": ["visitor id", "d=mid", "null", "Exclude"],
    #     "panel_scope": "all",
    # },
    # {
    #     "name_keywords": ["[Global] Excluded B2B"],
    #     "panel_scope": ["[Global]"],
    # },
]

# ─── 기존 패널 segmentGroups 무시 설정 ─────────────────────────────
# panel.segmentGroups (AA Workspace 패널 상단에 박힌 기존 세그) 를
# globalFilter 에 추가할지 결정. "기존 패널 세그 빼고 새 세그로 갈아끼우기" 용.
#
#   False              → 기존 동작 (panel.segmentGroups + EXTRA_SEGMENTS 누적, default)
#   True               → 모든 panel 에서 기존 세그 무시 (EXTRA_SEGMENTS + dateRange 만)
#   ["키워드1", ...]    → panel.name 에 키워드 포함된 panel 만 기존 세그 무시
#                         (OR 매칭, case-insensitive). 나머지 panel 은 기존 동작 유지.
# 예: True               → 전체 panel 적용
#     ["[US]"]           → [US] panel 만 적용
#     ["Revisit", "B2B"] → Revisit OR B2B 포함된 panel 만 적용
SKIP_PANEL_SEGMENTS: bool | list[str] = False

# ─── 특정 패널 세그만 제거 (v3.4) ──────────────────────────────────
# panel.segmentGroups 중 "이름에 아래 키워드를 모두(AND) 포함"하는 세그만
# globalFilter 에서 제외(개별 스킵). 나머지 패널 세그(B2B 등)는 그대로 유지.
#   []                                  → 아무것도 제거 안 함 (기본)
#   ["visitor id", "d=mid", "null"]     → 그 키워드 다 든 세그((SJ) 류)만 제거
# 주의: enabled:False 는 EXTRA 추가만 막을 뿐 패널 세그를 못 뺌 → 패널 세그 제거는 이 옵션으로.
SKIP_PANEL_SEGMENT_KEYWORDS: list[str] = []

# 세그먼트 검색 결과 lookup 파일 출력 (CSV + DSL)
LOOKUP_OUTPUT_DIR = Path(__file__).resolve().parent / "lookup"
LOOKUP_SEARCH_LIMIT = 500   # search API 최대 결과 (client-side AND 필터링 전 기준)

SETTINGS_FALLBACK = {
    "countRepeatInstances": True,
    "includeAnnotations": True,
    "nonesBehavior": "return-nones",
    "limit": LIMIT_LV1,
    "page": 0,
}

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════
# 무제한(limit=0) 모드일 때 API 1 page 크기 (cap 모드에선 settings.limit = cap 으로 1~수 page)
PAGE_SIZE_UNCAPPED = 5000
# ─── metric 정규화 (v3.9) — metric_origin → 정제 metric ──────────────
# 1) METRIC_ALIASES: 특이 변형 → 표준명 (공백제거+소문자 키 매칭). 예: AppBounce → Bounces
# 2) 끝 괄호 (…) 제거 — 단 METRIC_KEEP_PAREN_UNITS(단위) 면 유지.
#    "Order (purchase event)"→"Order", "Time Spent per Visit (seconds)"→그대로
METRIC_ALIASES = {
    "appbounce": "Bounces",
}
METRIC_KEEP_PAREN_UNITS = {
    "seconds", "second", "hq", "minutes", "minute", "min",
    "hours", "hour", "days", "day", "%",
}
_METRIC_PAREN_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def _normalize_metric(name):
    """metric_origin → 정제 metric (별칭 우선 + 끝 괄호 정리)."""
    if not name or not isinstance(name, str):
        return name
    s = name.strip()
    alias = METRIC_ALIASES.get(s.lower().replace(" ", ""))
    if alias:
        return alias
    m = _METRIC_PAREN_RE.match(s)
    if m and m.group(2).strip().lower() not in METRIC_KEEP_PAREN_UNITS:
        return m.group(1).strip()
    return s


SEG_ID_RE = re.compile(r"^s\d+_[0-9a-f]+$")
_DATE_RANGE_CACHE: dict[str, str] = {}
_SEG_NAME_CACHE: dict[str, str] = {}    # segment_id → fresh name (via /segments/{id} GET)

# device 추출 규칙 컴파일 (v3.5) — DEVICE_SEGMENT_RULES 를 (compiled, label) 로
_DEVICE_RULES_COMPILED = [(re.compile(pat, re.IGNORECASE), label) for pat, label in DEVICE_SEGMENT_RULES]
_DEVICE_APP_ONLY_RE = re.compile(DEVICE_APP_ONLY_PATTERN, re.IGNORECASE)
_DEVICE_ANDROID_RE = re.compile(r"\bAndroid\b", re.IGNORECASE)
_DEVICE_IOS_RE = re.compile(r"\biOS\b", re.IGNORECASE)


def _parse_device(seg_names: list[str]) -> str:
    """컬럼 stack 의 세그먼트명 리스트에서 device 라벨 1개 추출 (Mobile/PC/Android/iOS/App).
    1) 'App Only' 컨텍스트면: 같은 stack 에 Android/iOS 토큰 있으면 그 플랫폼(샌드위치 하위),
       없으면(All Visit 등) 'App'.
    2) 아니면 DEVICE_SEGMENT_RULES 순서대로 어느 세그명이든 먼저 매칭되는 라벨. 없으면 ''."""
    if not (DEVICE_FROM_SEGMENT and seg_names):
        return ""
    if any(nm and _DEVICE_APP_ONLY_RE.search(nm) for nm in seg_names):
        if any(nm and _DEVICE_ANDROID_RE.search(nm) for nm in seg_names):
            return "Android"
        if any(nm and _DEVICE_IOS_RE.search(nm) for nm in seg_names):
            return "iOS"
        return "App"
    for rx, label in _DEVICE_RULES_COMPILED:
        for nm in seg_names:
            if nm and rx.search(nm):
                return label
    return ""

# ─── aa_segment_lookup.py 헬퍼 import (search + decompile) ─────────
# 같은 폴더의 aa_segment_lookup.py 사본을 import — fork 시 별도 경로 손볼 필요 없음.
# 원본은 ...\260504_AA_segment_maker\segment_maker\aa_segment_lookup.py.
_SEG_LOOKUP_DIR = Path(__file__).resolve().parent
if str(_SEG_LOOKUP_DIR) not in sys.path:
    sys.path.insert(0, str(_SEG_LOOKUP_DIR))
from aa_segment_lookup import (   # noqa: E402
    _search_segments,
    _load_user_map,
    _enrich_owner_info,
    decompile_definition,
    format_dsl_block,
    _set_daterange_auth,
)


# ─── auth / project / panel / column tree — v1 과 동일 ─────────────
def _load_auth_headers() -> tuple[dict, str]:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h = dict(ags.header) if isinstance(getattr(ags, "header", None), dict) else {}
    h_lower = {k.lower(): v for k, v in h.items()}
    api_key = h_lower.get("x-api-key")
    auth = h_lower.get("authorization")
    gcid = h_lower.get("x-proxy-global-company-id")
    if not (api_key and auth and gcid):
        raise RuntimeError("필수 헤더 누락")
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


def _fetch_project(headers: dict, gcid: str, project_id: str) -> dict:
    url = f"https://analytics.adobe.io/api/{gcid}/projects/{project_id}"
    r = requests.get(url, headers=headers,
                     params={"expansion": "definition,ownerFullName,modifiedDate,sharesFullName"},
                     timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"GET project {project_id} 실패: {r.status_code} {r.text[:300]}")
    return r.json()


def _resolve_date_range(headers: dict, gcid: str, dr_id: str) -> str:
    if dr_id in _DATE_RANGE_CACHE:
        return _DATE_RANGE_CACHE[dr_id]
    url = f"https://analytics.adobe.io/api/{gcid}/dateranges/{dr_id}"
    r = requests.get(url, headers=headers, params={"expansion": "definition"}, timeout=30)
    if r.status_code == 200:
        definition = r.json().get("definition", "")
        _DATE_RANGE_CACHE[dr_id] = definition
        return definition
    _DATE_RANGE_CACHE[dr_id] = ""
    return ""


def _collect_date_range_ids(panels: list[dict]) -> set[str]:
    ids: set[str] = set()
    def _scan_nodes(nodes):
        for n in (nodes or []):
            comp = n.get("component") or {}
            if comp.get("type") == "DateRange" and comp.get("id"):
                ids.add(comp["id"])
            _scan_nodes(n.get("nodes"))
    for panel in panels:
        for sub in panel.get("subPanels") or []:
            rep = sub.get("reportlet") or {}
            ct = rep.get("columnTree") or {}
            _scan_nodes(ct.get("nodes"))
    return ids


def _prefetch_date_ranges(headers: dict, gcid: str, panels: list[dict]):
    dr_ids = _collect_date_range_ids(panels)
    if dr_ids:
        print(f"  dateRange 조회: {len(dr_ids)}개 ...")
    for dr_id in dr_ids:
        _resolve_date_range(headers, gcid, dr_id)


def _fetch_segment_name(headers: dict, gcid: str, seg_id: str) -> str:
    """`/segments/{seg_id}` GET → fresh name. 캐시 적용."""
    if seg_id in _SEG_NAME_CACHE:
        return _SEG_NAME_CACHE[seg_id]
    url = f"https://analytics.adobe.io/api/{gcid}/segments/{seg_id}"
    name = ""
    try:
        r = requests.get(url, headers=headers, params={"expansion": "name"}, timeout=30)
        if r.status_code == 200:
            name = r.json().get("name", "") or ""
    except Exception:
        pass
    _SEG_NAME_CACHE[seg_id] = name
    return name


def _collect_segment_ids(panels: list[dict]) -> set[str]:
    """panel.segmentGroups + columnTree + freeformTable.staticRows 에서 segment id 모두 수집."""
    ids: set[str] = set()
    def _scan_nodes(nodes):
        for n in (nodes or []):
            comp = n.get("component") or {}
            cid = comp.get("id")
            if comp.get("type") == "Segment" and isinstance(cid, str) and SEG_ID_RE.match(cid):
                ids.add(cid)
            _scan_nodes(n.get("nodes"))
    for panel in panels:
        for grp in panel.get("segmentGroups") or []:
            for opt in grp.get("componentOptions") or []:
                comp = opt.get("component") or {}
                cid = comp.get("id")
                if isinstance(cid, str) and SEG_ID_RE.match(cid):
                    ids.add(cid)
        for sub in panel.get("subPanels") or []:
            rep = sub.get("reportlet") or {}
            ct = rep.get("columnTree") or {}
            _scan_nodes(ct.get("nodes"))
            ff = rep.get("freeformTable") or {}
            for sr in ff.get("staticRows") or []:
                comp = sr.get("component") or {}
                cid = comp.get("id")
                if comp.get("type") == "Segment" and isinstance(cid, str) and SEG_ID_RE.match(cid):
                    ids.add(cid)
    return ids


def _prefetch_segment_names(headers: dict, gcid: str, panels: list[dict]):
    """project 안 모든 segment id 들 fresh name 한 번에 조회 → _SEG_NAME_CACHE 채움.
    panel 정의 snapshot 의 __metaData__.name 이 stale 일 수 있어 /segments/{id} 직접 조회."""
    seg_ids = _collect_segment_ids(panels)
    if not seg_ids:
        return
    print(f"  segment name 조회: {len(seg_ids)}개 ...")
    for sid in seg_ids:
        _fetch_segment_name(headers, gcid, sid)


def _list_panels(project: dict) -> list[dict]:
    out = []
    for ws in project.get("definition", {}).get("workspaces", []) or []:
        for p in ws.get("panels", []) or []:
            out.append(p)
    return out


def _comp_name(comp: dict) -> str:
    if not isinstance(comp, dict):
        return ""
    # segment id 가 cache 에 fresh name 으로 있으면 그걸 우선 (panel snapshot 의 stale name 회피)
    cid = comp.get("id")
    if isinstance(cid, str) and SEG_ID_RE.match(cid):
        cached = _SEG_NAME_CACHE.get(cid)
        if cached:
            return cached
    meta_name = (comp.get("__metaData__") or {}).get("name")
    if isinstance(meta_name, str) and meta_name:
        return meta_name
    n = comp.get("name")
    return n if isinstance(n, str) else ""


def _iter_panel_reportlets(panel: dict):
    current_sec = None
    sec_offset = 0
    for sub in panel.get("subPanels") or []:
        rep = sub.get("reportlet") or {}
        rep_type = rep.get("type")
        if rep_type == "SectionHeaderReportlet":
            current_sec = _parse_section_num(rep.get("name", ""))
            sec_offset = 0
            continue
        if rep_type != "FreeformReportlet":
            continue

        own = _parse_own_num(rep.get("name", ""))
        assigned = None
        if own is not None:
            assigned = own
            if current_sec is not None and current_sec[2] in ("range", "sequential"):
                start_y = current_sec[0][1]
                sec_offset = max(sec_offset, own[1] - start_y + 1)
        elif current_sec is not None:
            mode = current_sec[2]
            start_x, start_y = current_sec[0]
            if mode == "fixed":
                assigned = (start_x, start_y)
            else:
                assigned = (start_x, start_y + sec_offset)
                sec_offset += 1
        yield assigned, rep


def _parse_section_num(name: str):
    n = (name or "").strip()
    m = re.match(r"^(\d+)\s*-\s*(\d+)\s*~\s*(\d+)\s*-\s*(\d+)\s*\.", n)
    if m:
        x1, y1, x2, y2 = map(int, m.groups())
        return ((x1, y1), (x2, y2), "range")
    m = re.match(r"^(\d+)\s*-\s*(\d+)\s*\.", n)
    if m:
        x, y = map(int, m.groups())
        return ((x, y), (x, y), "fixed")
    m = re.match(r"^(\d+)\s*\.", n)
    if m:
        x = int(m.group(1))
        return ((x, 1), None, "sequential")
    return None


def _parse_own_num(reportlet_name: str):
    m = re.match(r"^\s*(\d+)\s*[-_.]\s*(\d+)\s*[.]\s", (reportlet_name or "") + " ")
    if not m:
        m = re.match(r"^\s*(\d+)\s*[-_.]\s*(\d+)", (reportlet_name or ""))
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


SLUG_ABBREVIATIONS = [
    (r"\bcampaign\b", "cmp"),
    (r"\bs\s*\.\s*com\b", "scom"),
    (r"\bs\.com\b", "scom"),
    (r"\bconversion\b", "cvr"),
    (r"\blogin\s*&\s*non[\s\-]*login\b", "loginout"),
    (r"\blogin\s*&\s*logout\b", "loginout"),
]
SLUG_NOISE_PATTERNS = [
    r"\[\s*vs\.?\s*prior\s*period\s*\]",
    r"\(\s*vs\.?\s*prior\s*period\s*\)",
    r"\[\s*w\.\s*prior\s*period\s*\]",
    r"\(\s*w\.\s*prior\s*period\s*\)",
    r"\(\s*values?\s*\d+(\s*[\+=]\s*\d+)*\s*\)",
    r"\(\s*total\s*\)",
    r"\(\s*w\.\s*cid\s*\)",
]


def _slugify(name: str) -> str:
    s = name.lower()
    for pat in SLUG_NOISE_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)
    for pat, repl in SLUG_ABBREVIATIONS:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


# ─── dateRange override 형식 ───────────────────────────────────────
def _build_date_range_definition(start_date: str, end_date: str) -> str:
    """ISO YYYY-MM-DD 두 개 → AA dateRange definition 형식.
    예: '2026-05-11', '2026-05-17' → '2026-05-11T00:00:00.000/2026-05-18T00:00:00.000'
    (end 는 다음날 00:00:00 — v1 의 _convert_date_range 와 동일 컨벤션)"""
    from datetime import datetime as _dt, timedelta
    start_dt = _dt.strptime(start_date, "%Y-%m-%d")
    end_dt = _dt.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    return f"{start_dt:%Y-%m-%dT}00:00:00.000/{end_dt:%Y-%m-%dT}00:00:00.000"


# ─── 연도 shift / 월 분할 (v4.2) ───────────────────────────────────
def _shift_year(date_str: str, offset: int) -> str:
    """'YYYY-MM-DD' 의 연도만 offset 만큼 이동. 2/29 처럼 shift 후 없는 날짜는 2/28 로 clamp.
    예: ('2026-07-21', -1) → '2025-07-21'"""
    if not offset:
        return date_str
    d = datetime.strptime(date_str, "%Y-%m-%d")
    try:
        return d.replace(year=d.year + offset).strftime("%Y-%m-%d")
    except ValueError:      # 2/29 → 평년
        return d.replace(year=d.year + offset, day=28).strftime("%Y-%m-%d")


# 월 라벨용 영문 약어 — strftime('%b') 는 로케일 의존이라 고정 테이블 사용 (AA 표기 'Jul 2025' 와 일치)
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _split_months(start_date: str, end_date: str) -> list[tuple[str, str, str]]:
    """총기간을 달력 월 조각으로 분할 → [(조각시작, 조각끝, 라벨), ...].
    MONTHLY=False 면 분할 없이 [(start, end, "")] 1개 (이하 로직 공통 처리용).
    라벨은 'Jul 2025' 형식 — AA daterangemonth 표기와 동일해서 후처리에서 그대로 파싱된다.
    양 끝은 총기간에 맞춰 잘림: ('2025-07-06','2025-07-21') → [('2025-07-06','2025-07-21','Jul 2025')]"""
    if not MONTHLY:
        return [(start_date, end_date, "")]
    s = datetime.strptime(start_date, "%Y-%m-%d")
    e = datetime.strptime(end_date, "%Y-%m-%d")
    if e < s:
        return [(start_date, end_date, "")]
    out: list[tuple[str, str, str]] = []
    cur = s
    while cur <= e:
        # 그 달의 마지막 날 = 다음 달 1일 - 1일
        nxt_month = cur.replace(day=1) + timedelta(days=32)
        month_end = nxt_month.replace(day=1) - timedelta(days=1)
        chunk_end = min(month_end, e)
        label = f"{_MONTH_ABBR[cur.month - 1]} {cur.year}"
        out.append((cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"), label))
        cur = chunk_end + timedelta(days=1)
    return out


# ─── EXTRA_SEGMENTS resolver (이름 → ID) ──────────────────────────
def _slugify_query(keywords: list[str]) -> str:
    """파일명용 슬러그. 키워드 각각 [A-Za-z0-9]+ 만 남기고 `__` 로 join.
    예: ['visitor id', 'd=mid', 'null', 'Exclude'] → 'visitor_id__d_mid__null__Exclude'"""
    slugs: list[str] = []
    for kw in keywords:
        s = re.sub(r"[^A-Za-z0-9]+", "_", kw).strip("_")
        if s:
            slugs.append(s)
    out = "__".join(slugs) if slugs else "query"
    return out[:120]


def _write_lookup_outputs(query_keywords: list[str], matches: list[dict], ts_str: str) -> tuple[Path, Path]:
    """매칭 결과를 CSV + DSL 두 파일로 저장. (csv_path, dsl_path) 반환.
    matches 는 aa_segment_lookup._search_segments() 반환 포맷."""
    LOOKUP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify_query(query_keywords)
    csv_path = LOOKUP_OUTPUT_DIR / f"segment_lookup_{slug}_{ts_str}.csv"
    dsl_path = LOOKUP_OUTPUT_DIR / f"segment_lookup_{slug}_{ts_str}.dsl"

    # CSV — aa_segment_lookup 의 컬럼 셋과 동일
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["segment_id", "name", "owner_id", "owner_name", "rsid",
                    "description", "tags", "structure", "error"])
        for r in matches:
            structure = ""
            if r.get("definition"):
                try:
                    dsl_text = decompile_definition(r["definition"])
                    structure = dsl_text.replace('"', "'").replace("\n", " | ")
                except Exception:
                    structure = "(decompile error)"
            w.writerow([
                r.get("segment_id", ""), r.get("name", ""),
                r.get("owner_id", ""), r.get("owner_name", ""),
                r.get("rsid", ""), r.get("description", ""),
                r.get("tags", ""), structure, r.get("error", ""),
            ])

    # DSL — 한 파일에 전부 이어서 '===' 구분선
    dsl_blocks: list[str] = []
    for r in matches:
        if not r.get("definition"):
            continue
        try:
            tag_list = [t.strip() for t in (r.get("tags") or "").split(",") if t.strip()]
            block = format_dsl_block(
                name=r.get("name", ""),
                description=r.get("description", ""),
                rsid=r.get("rsid", ""),
                tags=tag_list,
                definition=r["definition"],
            )
            dsl_blocks.append(block)
        except Exception as e:
            dsl_blocks.append(f"--- segment\nname: {r.get('name','')}\nerror: decompile 실패 — {e}\n")
    separator = "\n\n" + ("=" * 78) + "\n\n"
    dsl_path.write_text(separator.join(dsl_blocks) + "\n" if dsl_blocks else "", encoding="utf-8")
    return csv_path, dsl_path


def _resolve_extra_segment(spec: dict, headers: dict, gcid: str, ts_str: str, panels: list[dict] | None = None) -> str | None:
    """EXTRA_SEGMENTS 한 항목 → segment_id (1개 확정 시). 모호하거나 0개면 SystemExit.
    검색 케이스는 lookup CSV/DSL 항상 저장 (디버깅/공유용).

    spec 형식 (둘 중 하나):
      · {"segment_id": "s...."}                 → 검색 생략, 바로 사용
      · {"name_keywords": str | list[str]}      → 이름 검색
    """
    # ─── 1) segment_id 직접 지정 케이스 ───
    sid_raw = spec.get("segment_id")
    if sid_raw:
        sid = str(sid_raw).strip()
        if not SEG_ID_RE.match(sid):
            raise SystemExit(f"EXTRA_SEGMENTS segment_id 형식 오류: {sid!r} (예: 세그먼트_아이디_넘버)")
        # 이름 fetch — 캐시에 박아 _build_global_filters 의 segment_names 에 활용
        name = _fetch_segment_name(headers, gcid, sid)
        print(f"\n[segment by id] {sid}  '{name or '(name 조회 실패)'}'")
        return sid

    # ─── 2) name_keywords 검색 케이스 ───
    raw = spec.get("name_keywords")
    if not raw:
        raise SystemExit("EXTRA_SEGMENTS 항목에 segment_id 또는 name_keywords 가 없음")
    keywords = [raw] if isinstance(raw, str) else [k for k in raw if k]
    if not keywords:
        raise SystemExit("EXTRA_SEGMENTS 항목의 name_keywords 가 비어있음")

    kw_disp = " AND ".join(repr(k) for k in keywords)
    print(f"\n[segment search] {kw_disp}")
    # ── v3.4: 프로젝트 패널 segmentGroups 안에서 먼저 매칭 (패널 우선) ──
    #   패널 내 1건 → 그 세그 바로 사용 (회사 전체 검색 생략, 모호성 없음)
    #   패널 내 2건+ → 중단(segment_id 직접 지정하거나 더 세밀한 키워드)
    #   패널 내 0건 → 아래 회사 전체 검색으로 fallback
    kw_lower = [k.lower() for k in keywords]
    if panels:
        panel_hits: dict[str, str] = {}
        for _p in panels:
            for _grp in _p.get("segmentGroups") or []:
                for _opt in _grp.get("componentOptions") or []:
                    _c = _opt.get("component") or {}
                    _sid = _c.get("id")
                    if not (isinstance(_sid, str) and SEG_ID_RE.match(_sid)):
                        continue
                    _nm = _SEG_NAME_CACHE.get(_sid) or (_c.get("__metaData__") or {}).get("name", "") or ""
                    if _nm and all(k in _nm.lower() for k in kw_lower):
                        panel_hits[_sid] = _nm
        if len(panel_hits) == 1:
            _sid, _nm = next(iter(panel_hits.items()))
            _SEG_NAME_CACHE[_sid] = _nm
            print(f"  [panel-first] 패널 내 단일 매칭: {_sid}  '{_nm}'  -> 적용")
            return _sid
        if len(panel_hits) >= 2:
            print(f"  [panel-first] 패널 내 다중 매칭 {len(panel_hits)}건 - segment_id 직접 지정하거나 더 세밀한 키워드로:")
            for _sid, _nm in panel_hits.items():
                print(f"    {_sid}  '{_nm}'")
            raise SystemExit("패널 내 다중 매칭으로 중단.")
        print("  [panel-first] 패널 내 매칭 0건 -> 회사 전체 검색으로 fallback")
    matches = _search_segments(headers, gcid, keywords, rsid="", limit=LOOKUP_SEARCH_LIMIT)
    # owner_name 보강 — _search_segments 는 owner_id 만 채우므로 GET /users 로 이름 backfill
    #   (표준 CLI aa_segment_lookup.main 과 동일 동작; 미보강 시 lookup CSV 의 owner_name 이 빈칸)
    _user_map = _load_user_map(headers, gcid)
    if _user_map:
        _enrich_owner_info(matches, _user_map)
    n = len(matches)
    print(f"  매칭 결과: {n}건")

    # 항상 lookup 파일 저장
    csv_path, dsl_path = _write_lookup_outputs(keywords, matches, ts_str)
    print(f"  → CSV: {csv_path}")
    print(f"  → DSL: {dsl_path}")

    if n == 0:
        raise SystemExit(f"❌ 매칭 0건 — 키워드 확인 필요: {kw_disp}")

    if n == 1:
        m = matches[0]
        sid = m.get("segment_id", "")
        name = m.get("name", "")
        # 캐시에 박아서 _build_global_filters / segment_names 에 활용
        if sid:
            _SEG_NAME_CACHE[sid] = name
        print(f"  ✓ 단일 매칭: {sid}  '{name}'  (owner: {m.get('owner_name','')})")
        return sid

    if 2 <= n <= 5:
        print(f"  ⚠ 다중 매칭 ({n}건) — 하나로 좁혀서 다시 실행:")
        for i, m in enumerate(matches, 1):
            print(f"    {i:2}. {m.get('segment_id',''):40}  '{m.get('name','')}'"
                  f"  rsid={m.get('rsid','')}  owner={m.get('owner_name','')}")
        raise SystemExit("다중 매칭으로 중단. name_keywords 를 더 specific 하게 수정.")

    # n > 5
    print(f"  ⚠ 매칭 너무 많음 ({n}건) — 위 lookup CSV 확인 후 키워드 좁히기:")
    print(f"    {csv_path}")
    raise SystemExit("다중 매칭으로 중단.")


# ─── SKIP_PANEL_SEGMENTS 적용 여부 결정 ────────────────────────────
def _should_skip_panel_segments(panel_name: str) -> bool:
    """SKIP_PANEL_SEGMENTS 설정 → 해당 panel 에서 기존 segmentGroups 를 무시할지."""
    if SKIP_PANEL_SEGMENTS is True:
        return True
    if isinstance(SKIP_PANEL_SEGMENTS, list) and SKIP_PANEL_SEGMENTS:
        pname_lower = (panel_name or "").lower()
        return any(str(kw).lower() in pname_lower for kw in SKIP_PANEL_SEGMENTS)
    return False


# ─── panel 의 dateRange + rsid override + global filter 구성 ───────
def _build_global_filters(
    panel: dict,
    *,
    override_date_range: str | None = None,
    extra_segment_ids: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    filters: list[dict] = []
    segment_names: list[str] = []
    skip_existing = _should_skip_panel_segments(panel.get("name", ""))
    _skip_seg_kw = [k.lower() for k in SKIP_PANEL_SEGMENT_KEYWORDS]
    if not skip_existing:
        for grp in panel.get("segmentGroups") or []:
            for opt in grp.get("componentOptions") or []:
                if not opt.get("isActive", True):
                    continue
                comp = opt.get("component") or {}
                sid = comp.get("id")
                if isinstance(sid, str) and SEG_ID_RE.match(sid):
                    _nm = (_comp_name(comp) or _SEG_NAME_CACHE.get(sid, "") or "")
                    if _skip_seg_kw and _nm and all(k in _nm.lower() for k in _skip_seg_kw):
                        print(f"  [skip-panel-seg] globalFilter 에서 제거: {sid}  '{_nm}'")
                        continue
                    filters.append({"type": "segment", "segmentId": sid})
                    segment_names.append(_comp_name(comp))
    # extra segments (이름 검색으로 확정된 추가 filter)
    if extra_segment_ids:
        for sid in extra_segment_ids:
            if not (isinstance(sid, str) and SEG_ID_RE.match(sid)):
                continue
            filters.append({"type": "segment", "segmentId": sid})
            segment_names.append(_SEG_NAME_CACHE.get(sid, "") or sid)
    # dateRange — override 우선
    if override_date_range:
        filters.append({"type": "dateRange", "dateRange": override_date_range})
    else:
        dr = panel.get("dateRange") or {}
        definition = ""
        if isinstance(dr, dict):
            definition = (dr.get("__metaData__") or {}).get("definition", "")
        elif isinstance(dr, str):
            definition = dr
        if definition:
            filters.append({"type": "dateRange", "dateRange": definition})
    return filters, segment_names


# ─── columnTree walk — v1 동일 ────────────────────────────────────
def _walk_column_tree(root_nodes: list) -> list[dict]:
    leaves: list[dict] = []
    counter = [-1]
    def walk(node, segments, segment_names, date_ranges, date_range_names, metric, metric_name):
        counter[0] += 1
        my_pos = counter[0]
        comp = node.get("component") or {}
        ctype = comp.get("type")
        cid = comp.get("id")
        new_segments = list(segments)
        new_segment_names = list(segment_names)
        new_date_ranges = list(date_ranges)
        new_date_range_names = list(date_range_names)
        new_metric = metric
        new_metric_name = metric_name
        if ctype == "Segment" and cid:
            new_segments.append(cid)
            new_segment_names.append(_comp_name(comp))
        elif ctype == "DateRange" and cid:
            meta = comp.get("__metaData__") or {}
            definition = meta.get("definition", "")
            if not definition:
                definition = _DATE_RANGE_CACHE.get(cid, "")
            if definition:
                dr_filter = {"type": "dateRange", "dateRange": definition}
                if not SEG_ID_RE.match(cid):
                    dr_filter["dateRangeId"] = cid
                new_date_ranges.append(dr_filter)
            new_date_range_names.append(_comp_name(comp) or cid)
        elif ctype in ("Metric", "CalculatedMetric") and cid:
            new_metric = cid
            new_metric_name = _comp_name(comp)
        children = node.get("nodes") or []
        if not children:
            if new_metric is not None:
                leaves.append({
                    "metric_id": new_metric,
                    "metric_name": new_metric_name or "",
                    "segments": new_segments,
                    "segment_names": new_segment_names,
                    "date_ranges": new_date_ranges,
                    "date_range_names": new_date_range_names,
                    "leaf_id": node.get("id"),
                    "position": my_pos,
                })
            return
        for child in children:
            walk(child, new_segments, new_segment_names, new_date_ranges, new_date_range_names, new_metric, new_metric_name)
    for n in root_nodes or []:
        walk(n, [], [], [], [], None, None)
    return leaves


def _build_metric_container(reportlet: dict) -> tuple[dict, list[list[str]], list[str]]:
    column_tree = reportlet.get("columnTree") or {}
    leaves = _walk_column_tree(column_tree.get("nodes") or [])
    ff = reportlet.get("freeformTable") or {}
    static_rows_raw = ff.get("staticRows") or []
    row_segs: list[str] = []
    row_seg_names: list[str] = []
    for r in static_rows_raw:
        comp = r.get("component") or {}
        if comp.get("type") == "Segment" and comp.get("id"):
            row_segs.append(comp["id"])
            row_seg_names.append(_comp_name(comp))
    sort_cfg = ff.get("sort") or {}
    sort_target_leaf_id = sort_cfg.get("columnId")
    sort_asc = sort_cfg.get("asc")
    metrics: list[dict] = []
    metric_filters: list[dict] = []
    seg_names_per_metric: list[list[str]] = []
    metric_names_per_metric: list[str] = []
    if row_segs:
        global_idx = 0
        next_col_id = 0
        for row_seg, row_seg_name in zip(row_segs, row_seg_names):
            for leaf in leaves:
                row_filter_id = f"STATIC_ROW_COMPONENT_{2 * global_idx + 1}"
                col_position = 2 * global_idx
                col_filter_ids: list[str] = []
                for _sid in reversed(leaf["segments"]):
                    col_filter_ids.append(str(next_col_id))
                    next_col_id += 1
                dr_filter_ids: list[str] = []
                for _dr in leaf.get("date_ranges", []):
                    dr_filter_ids.append(str(next_col_id))
                    next_col_id += 1
                entry = {
                    "columnId": f"{leaf['metric_id']}:::{col_position}",
                    "id": leaf["metric_id"],
                    "filters": [row_filter_id] + col_filter_ids + dr_filter_ids,
                }
                metrics.append(entry)
                metric_filters.append({"id": row_filter_id, "type": "segment", "segmentId": row_seg})
                for fid, sid in zip(col_filter_ids, reversed(leaf["segments"])):
                    metric_filters.append({"id": fid, "type": "segment", "segmentId": sid})
                for fid, dr_filter in zip(dr_filter_ids, leaf.get("date_ranges", [])):
                    metric_filters.append({"id": fid, **dr_filter})
                cell_names = [row_seg_name] + list(leaf["segment_names"]) + list(leaf.get("date_range_names", []))
                seg_names_per_metric.append([n for n in cell_names if n])
                metric_names_per_metric.append(leaf.get("metric_name", "") or "")
                global_idx += 1
        return {"metrics": metrics, "metricFilters": metric_filters}, seg_names_per_metric, metric_names_per_metric
    next_filter_id = 0
    for leaf in leaves:
        filter_ids: list[str] = []
        for sid in reversed(leaf["segments"]):
            fid = str(next_filter_id)
            next_filter_id += 1
            metric_filters.append({"id": fid, "type": "segment", "segmentId": sid})
            filter_ids.append(fid)
        for dr_filter in leaf.get("date_ranges", []):
            fid = str(next_filter_id)
            next_filter_id += 1
            metric_filters.append({"id": fid, **dr_filter})
            filter_ids.append(fid)
        entry = {"columnId": str(leaf["position"]), "id": leaf["metric_id"], "filters": filter_ids}
        if sort_target_leaf_id and leaf["leaf_id"] == sort_target_leaf_id:
            entry["sort"] = "desc" if sort_asc is False else "asc"
        metrics.append(entry)
        all_names = list(leaf["segment_names"]) + list(leaf.get("date_range_names", []))
        seg_names_per_metric.append([n for n in all_names if n])
        metric_names_per_metric.append(leaf.get("metric_name", "") or "")
    return {"metrics": metrics, "metricFilters": metric_filters}, seg_names_per_metric, metric_names_per_metric


def _build_report_payload(project: dict, panel: dict, reportlet: dict, *,
                          override_rsid: str, override_date_range: str,
                          extra_segment_ids: list[str] | None = None
                          ) -> tuple[dict, list[list[str]], list[str], list[str], str, str]:
    """v1 과 달리 rsid + dateRange override 필수.
    v3: extra_segment_ids 로 panel globalFilter 에 segment 추가 적용."""
    rsid = override_rsid
    global_filters, panel_seg_names = _build_global_filters(
        panel,
        override_date_range=override_date_range,
        extra_segment_ids=extra_segment_ids,
    )
    metric_container, seg_names_per_metric, metric_names = _build_metric_container(reportlet)
    ff = reportlet.get("freeformTable") or {}
    dim_settings = ff.get("dimensionSettings") or []
    dimension = ""
    dimension_name = ""
    if dim_settings and isinstance(dim_settings[0], dict):
        dim_obj = dim_settings[0].get("dimension") or {}
        dimension = dim_obj.get("id", "")
        dimension_name = _comp_name(dim_settings[0]) or dim_obj.get("description", "") or dimension
    pagination = ff.get("pagination") or {}
    settings = {
        "countRepeatInstances": SETTINGS_FALLBACK.get("countRepeatInstances", True),
        "includeAnnotations": SETTINGS_FALLBACK.get("includeAnnotations", True),
        "nonesBehavior": SETTINGS_FALLBACK.get("nonesBehavior", "return-nones"),
        "limit": pagination.get("viewBy", SETTINGS_FALLBACK.get("limit")),
        "page": pagination.get("currentPage", SETTINGS_FALLBACK.get("page", 0)),
    }
    payload = {
        "rsid": rsid,
        "globalFilters": global_filters,
        "metricContainer": metric_container,
        "settings": settings,
        "capacityMetadata": {
            "associations": [
                {"name": "applicationName", "value": "Analysis Workspace UI"},
                {"name": "projectId", "value": project.get("id", "")},
                {"name": "projectName", "value": project.get("name", "")},
                {"name": "panelName", "value": reportlet.get("name", "")},
            ]
        },
    }
    if dimension:
        payload["dimension"] = dimension
    return payload, seg_names_per_metric, metric_names, panel_seg_names, dimension, dimension_name


# ─── N단계 breakdown (v3.5) ────────────────────────────────────────
def _cap_bd_depth(chain: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """BREAKDOWN_MAX_DEPTH 깊이 캡 적용 (v4.1). <0(또는 None)=무제한, N>=0 이면 chain[:N].
    N=0 이면 빈 체인 → breakdown 안 함(BREAKDOWN_ENABLED 여부와 무관하게 dim1 총계만)."""
    if BREAKDOWN_MAX_DEPTH is not None and BREAKDOWN_MAX_DEPTH >= 0:
        return chain[:BREAKDOWN_MAX_DEPTH]
    return chain


def _detect_breakdown_chain(reportlet: dict) -> list[tuple[str, str]]:
    """행(dim1) 아래로 분해할 하위 차원 체인을 (dim_id, dim_name) 리스트로 깊이순 반환.
    BREAKDOWN_DIMENSIONS 가 지정되면 그걸 우선 사용(name=""), 아니면 freeformTable.breakdowns 의
    nested dimension 을 깊이순으로 자동감지. 빈 리스트면 분해 안 함(v3.4 동작)."""
    if BREAKDOWN_DIMENSIONS:
        return _cap_bd_depth([(d, "") for d in BREAKDOWN_DIMENSIONS if d])
    chain: list[tuple[str, str]] = []
    ff = reportlet.get("freeformTable") or {}
    node_list = ff.get("breakdowns") or []
    seen: set[str] = set()
    while node_list:
        entry = node_list[0] if isinstance(node_list[0], dict) else None
        if not entry:
            break
        ds = entry.get("dimensionSettings") or []
        if not (ds and isinstance(ds[0], dict)):
            break
        dim_obj = ds[0].get("dimension") or {}
        did = dim_obj.get("id", "")
        if not did or did in seen:
            break
        seen.add(did)
        dname = ((ds[0].get("__metaData__") or {}).get("name")
                 or (dim_obj.get("__metaData__") or {}).get("name")
                 or dim_obj.get("description") or did)
        chain.append((did, dname))
        node_list = entry.get("breakdowns") or []
    return _cap_bd_depth(chain)


def _breakdown_top_branches(reportlet: dict) -> list[str]:
    """freeformTable.breakdowns 의 top-level 가지 dimension id 들을 전부 반환.
    auto-detect(_detect_breakdown_chain)는 가지[0] 만 쓰므로, 가지가 2개+면
    의도와 다른 차원(variables)이 잡혔을 수 있음 → 콘솔에서 BD가지 수로 경고."""
    if BREAKDOWN_DIMENSIONS:
        return []
    ff = reportlet.get("freeformTable") or {}
    out: list[str] = []
    for e in (ff.get("breakdowns") or []):
        if not isinstance(e, dict):
            continue
        ds = e.get("dimensionSettings") or []
        did = (ds[0].get("dimension") or {}).get("id", "") if (ds and isinstance(ds[0], dict)) else ""
        out.append(did or "?")
    return out


def _build_breakdown_payload(base_payload: dict,
                             ancestor_pairs: list[tuple[str, str]],
                             bd_dim_id: str, *, limit: int) -> dict:
    """base_payload(컬럼 metric 구조 동일)를 복제해 하위차원 분해용 payload 생성.
    ancestor_pairs = [(dim_id, item_id), ...] — dim1 부터 직전 레벨까지의 조상 (차원,itemId).
    각 metric.filters 에 조상 breakdown filter id 들을 모두 추가(AND)하고, dimension 을 bd_dim_id 로 교체."""
    payload = json.loads(json.dumps(base_payload))
    payload["dimension"] = bd_dim_id
    mc = payload.get("metricContainer") or {}
    metrics = mc.get("metrics") or []
    mfilters = mc.get("metricFilters") or []
    bd_fids: list[str] = []
    for k, (dim_id, item_id) in enumerate(ancestor_pairs):
        fid = f"BD{k}_{item_id}"
        mfilters.append({"id": fid, "type": "breakdown", "dimension": dim_id, "itemId": item_id})
        bd_fids.append(fid)
    for m in metrics:
        m["filters"] = list(m.get("filters") or []) + bd_fids
    mc["metricFilters"] = mfilters
    payload["metricContainer"] = mc
    payload.setdefault("settings", {})
    payload["settings"]["limit"] = min(limit, 100000) if limit > 0 else PAGE_SIZE_UNCAPPED
    payload["settings"]["page"] = 0
    return payload


def _run_breakdowns(task: dict, headers: dict, gcid: str, *, workers: int) -> int:
    """task 의 dim1 rows 를 breakdown_chain 따라 재귀 분해.
    결과를 task['breakdown_rows'] = [{'path':[(dim_id,dim_name,item_id,value),...], 'data':[...]}, ...] 에 저장.
    path[0] = dim1, path[k] = bd{k}. 반환값 = 이 task 에서 발생한 breakdown /reports 호출 수."""
    chain: list[tuple[str, str]] = task.get("breakdown_chain") or []
    task["breakdown_rows"] = []
    if not chain or not task.get("ok") or not task.get("rows"):
        return 0
    base = task["payload"]
    dim1_id = task.get("dimension_id", "")
    dim1_name = task.get("dimension_name", "")
    # frontier 항목: {"pairs":[(dim,item),...], "path":[(dim,name,item,value),...]}
    frontier: list[dict] = []
    for r in task["rows"]:
        iid = r.get("itemId")
        if iid is None:
            continue
        frontier.append({"pairs": [(dim1_id, iid)],
                         "path": [(dim1_id, dim1_name, iid, r.get("value", ""))]})

    all_bd_rows: list[dict] = []
    calls = 0
    _bd_t0 = datetime.now()
    for level, (bd_dim_id, bd_dim_name) in enumerate(chain, start=1):
        if not frontier:
            break
        if BREAKDOWN_TOP_N > 0:
            frontier = frontier[:BREAKDOWN_TOP_N] if level == 1 else frontier
        # v4.0: breakdown 단계별 행 cap — TOP_N 우선, 아니면 bd1~4 = LIMIT_BD / BD2 / BD3 / BD4 (bd5+ 는 BD4), 0=무제한.
        #   여기 level 변수 = breakdown 단계 (level==1 = 첫 분해 bd1 = 절대 level2). 테이블 실제 깊이까지만 적용.
        _bd_caps = [LIMIT_BD, LIMIT_BD2, LIMIT_BD3, LIMIT_BD4]
        lvl_limit = BREAKDOWN_TOP_N if BREAKDOWN_TOP_N > 0 else _bd_caps[min(level, 4) - 1]
        sub_tasks: list[dict] = []
        for fr in frontier:
            bp = _build_breakdown_payload(base, fr["pairs"], bd_dim_id, limit=lvl_limit)
            sub_tasks.append({"payload": bp, "tb_name": f"{task['tb_name']}/L{level}",
                              "max_rows": lvl_limit,   # v4.0: 레벨별 페이지네이션 행 cap
                              "rows": [], "summary_data": [], "ok": False, "error": "", "_fr": fr})
        next_frontier: list[dict] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_extract_one, st, headers, gcid): st for st in sub_tasks}
            for fut in as_completed(futs):
                st = fut.result()
                calls += 1
                if not st["ok"]:
                    continue
                child_rows = st["rows"]
                if BREAKDOWN_TOP_N > 0:
                    child_rows = child_rows[:BREAKDOWN_TOP_N]
                for r in child_rows:
                    iid = r.get("itemId")
                    if iid is None:
                        continue
                    new_path = st["_fr"]["path"] + [(bd_dim_id, bd_dim_name, iid, r.get("value", ""))]
                    all_bd_rows.append({"path": new_path, "data": r.get("data") or []})
                    next_frontier.append({"pairs": st["_fr"]["pairs"] + [(bd_dim_id, iid)],
                                          "path": new_path})
        frontier = next_frontier
        # v4.0: breakdown 단계별 진행 출력 (라이브) + 남은 예상시간 — 멈춘 것처럼 보이지 않게
        _dim_short = bd_dim_id.split("/")[-1]
        _elapsed = (datetime.now() - _bd_t0).total_seconds()
        _thr = calls / _elapsed if _elapsed > 0 else 0.0
        if level < len(chain) and next_frontier:
            # 남은 콜 = 다음 단계("다음 N행", 정확) + 그 이후 단계(캡으로 외삽)
            _rem, _fc, _unb = 0, len(next_frontier), False
            for _s in range(level + 1, len(chain) + 1):
                _rem += _fc
                if _s < len(chain):
                    _c = _bd_caps[min(_s, 4) - 1]
                    if _c and _c > 0:
                        _fc *= _c
                    else:
                        _unb = True
                        break
            _tail = (f" · 남은 ~{_rem:,}콜, 남은 예상시간 ~{_fmt_dur(_rem / _thr)}"
                     if (_thr > 0 and not _unb) else " · 남은 예상시간 ?")
        else:
            _tail = " · 마지막 단계, 완료"
        print(f"      bd{level}({_dim_short}, =level{level + 1}): {len(sub_tasks)}콜 완료 → 다음 {len(next_frontier)}행 "
              f"(누적 {calls}콜 / 누적 {_fmt_dur(_elapsed)}{_tail})", flush=True)
    task["breakdown_rows"] = all_bd_rows
    return calls


def _estimate_runtime(bd_tasks: list[dict], headers: dict, gcid: str, *, workers: int,
                      probe_parents: int = 5) -> None:
    """실제 breakdown 전, breakdown 단계별 fanout 을 1 경로 샘플로 실측해 총 /reports 호출 수·ETA 추정 출력.
    호출 수 ≈ N1·(1 + f1 + f1·f2 + …) (bd1~bd(D-1) 단계 fanout 누적곱). bd1(LIMIT_BD)이 가장 큰 곱셈 인자.
    worst = 캡 그대로 가정 / 추정 = 실측 fanout (실제 항목수가 캡보다 작을 때 더 정확).
    ※ 용어: bdK = breakdown K단계 = 절대 level(K+1). dim1 = level1 = N1."""
    caps = [LIMIT_BD, LIMIT_BD2, LIMIT_BD3, LIMIT_BD4]

    def _cap(stage: int) -> int:            # stage = breakdown 단계 (1-based)
        return caps[min(stage, 4) - 1]

    def _probe_cap(stage: int) -> int:      # 무제한(0) 캡이면 probe 만 200 으로 제한 (느린 probe 방지)
        c = _cap(stage)
        return c if c and c > 0 else 200

    def _calls(N1: int, D: int, fans: list):
        """총 breakdown 호출. fans[j-1] = bdj 단계 fanout (j=1..D-1 만 곱셈 인자). 0 있으면 None(무제한)."""
        total = 0.0
        prod = 1.0
        for k in range(1, D + 1):
            total += N1 * prod
            if k <= D - 1:
                cj = fans[k - 1] if k - 1 < len(fans) else 0
                if not cj or cj <= 0:
                    return None
                prod *= cj
        return int(round(total))

    print("\n  ── [estimate] 사전 추정 (breakdown 단계별 1경로 샘플 측정 → 외삽, 실제 추출 안 함) ──", flush=True)
    probe_calls = 0
    probe_t0 = datetime.now()
    rows_out = []
    for t in bd_tasks:
        chain = t.get("breakdown_chain") or []
        D = len(chain)
        N1 = len(t.get("rows") or [])
        if D == 0 or N1 == 0:
            continue
        base = t["payload"]
        dim1_id = t.get("dimension_id", "")
        fans: list = []        # 실측 fanout per breakdown stage (1-based)
        # ── bd1: dim1 부모 표본(최대 probe_parents) 병렬 호출 → 평균 fanout + deeper 경로 확보 ──
        sample = [r for r in t["rows"][:probe_parents] if r.get("itemId") is not None]
        sub = [{"payload": _build_breakdown_payload(base, [(dim1_id, r["itemId"])], chain[0][0],
                                                    limit=_probe_cap(1)),
                "tb_name": f"{t['tb_name']}/probe-bd1", "max_rows": _probe_cap(1),
                "rows": [], "summary_data": [], "ok": False, "error": "",
                "_pair": (dim1_id, r["itemId"])} for r in sample]
        counts = []
        path_pairs = None
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_extract_one, st, headers, gcid): st for st in sub}
            for fut in as_completed(futs):
                st = fut.result()
                probe_calls += 1
                if st["ok"]:
                    counts.append(len(st["rows"]))
                    if st["rows"] and path_pairs is None:
                        path_pairs = [st["_pair"], (chain[0][0], st["rows"][0].get("itemId"))]
        fans.append((sum(counts) / len(counts)) if counts else float(_cap(1) or 0))
        # ── bd2 ~ bd(D-1): 단일 경로로 1콜씩 측정 (deeper fanout) ──
        for stage in range(2, D):
            if not path_pairs or path_pairs[-1][1] is None:
                fans.append(float(_cap(stage) or 0))   # 경로 못 이으면 캡 가정
                continue
            bd_dim_id = chain[stage - 1][0]
            st = _extract_one({"payload": _build_breakdown_payload(base, path_pairs, bd_dim_id,
                                                                  limit=_probe_cap(stage)),
                               "tb_name": f"{t['tb_name']}/probe-bd{stage}", "max_rows": _probe_cap(stage),
                               "rows": [], "summary_data": [], "ok": False, "error": ""},
                              headers, gcid)
            probe_calls += 1
            if st["ok"] and st["rows"]:
                fans.append(float(len(st["rows"])))
                path_pairs = path_pairs + [(bd_dim_id, st["rows"][0].get("itemId"))]
            else:
                fans.append(float(_cap(stage) or 0))
                path_pairs = None
        worst_fans = [float(_cap(s) or 0) for s in range(1, D)]   # bd1~bd(D-1) 캡
        rows_out.append((t["tb_name"], N1, D, fans, worst_fans))

    probe_wall = (datetime.now() - probe_t0).total_seconds()
    thr = (probe_calls / probe_wall) if probe_wall > 0 else 0.0
    print(f"  · 측정: probe {probe_calls}콜 / {probe_wall:.1f}s → ~{thr:.1f} calls/s (workers={workers})", flush=True)

    gw = gr = 0
    gw_unb = False
    for name, N1, D, fans, worst in rows_out:
        worst_c = _calls(N1, D, worst)
        ref_c = _calls(N1, D, [max(1.0, round(f)) if f else 0 for f in fans])
        fan_str = " / ".join(f"{f:.0f}" for f in fans) if fans else "-"
        ws = "무제한캡" if worst_c is None else f"{worst_c:,}"
        rs = "무제한캡" if ref_c is None else f"{ref_c:,}"
        if worst_c is None:
            gw_unb = True
        else:
            gw += worst_c
        gr += ref_c or 0
        print(f"  · {name}: dim1(N1)={N1}, breakdown {D}단계 (=level{D + 1}까지), "
              f"단계별 실측 fanout=[{fan_str}]", flush=True)
        print(f"      → 호출 worst≈{ws} / 추정≈{rs}", flush=True)

    def _eta(c):
        return _fmt_dur(c / thr) if (thr > 0 and c) else "?"
    gwd = "무제한캡 포함" if gw_unb else f"{gw:,}"
    print(f"  · 합계 호출 worst≈{gwd} / 추정≈{gr:,}  →  예상 소요시간 worst≈{_eta(0 if gw_unb else gw)} / 추정≈{_eta(gr)}", flush=True)
    print("    (예상 소요시간 = 호출수 ÷ 측정 처리율. bd1 캡 LIMIT_BD 가 곱셈 핵심 — 줄이면 비례해 빨라짐. 마지막 단계 캡은 호출수 무관)", flush=True)
    print("    ※ 대량 구간은 429 throttling 으로 더 느려질 수 있음 → worst 예상 소요시간 을 상한으로 보세요", flush=True)
    print("  ──────────────────────────────────────────────────────────\n", flush=True)


# ─── /reports API + 페이징 (v1 동일) ───────────────────────────────
def _post_reports(session: requests.Session, headers: dict, gcid: str, payload: dict) -> dict:
    endpoint = f"https://analytics.adobe.io/api/{gcid}/reports"
    for attempt in range(MAX_RETRIES + 1):
        r = session.post(endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code < 400:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            ra = r.headers.get("Retry-After")
            sleep_sec = float(ra) if ra else min(2 ** attempt, 30)
            if attempt == MAX_RETRIES:
                r.raise_for_status()
            tag = "throttle(429)" if r.status_code == 429 else f"일시오류({r.status_code})"
            print(f"      ⚠ {tag} — {sleep_sec:.0f}s 후 재시도 [{attempt + 1}/{MAX_RETRIES}]", flush=True)
            time.sleep(sleep_sec)
            continue
        r.raise_for_status()
    raise RuntimeError("post_reports: unexpected fall-through")


def _fetch_all_pages(session: requests.Session, headers: dict, gcid: str, payload: dict,
                     max_rows: int = 0) -> tuple[list[dict], list[float]]:
    """페이지네이션 수집. max_rows > 0 이면 누적 행이 max_rows 도달 시 중단 + 초과분 truncate (v3.7).
    max_rows = 0 이면 무제한 (MAX_PAGES 까지, v3.6 동작)."""
    all_rows = []
    summary_data: list[float] = []
    limit = int(payload.get("settings", {}).get("limit", PAGE_SIZE_UNCAPPED))
    for page in range(MAX_PAGES):
        payload["settings"]["page"] = page
        res = _post_reports(session, headers, gcid, payload)
        if page == 0:
            sd = res.get("summaryData", {})
            if isinstance(sd, dict):
                totals = sd.get("totals", [])
                if totals:
                    summary_data = totals
                if not summary_data:
                    ft = sd.get("filteredTotals", [])
                    if ft:
                        summary_data = ft
        rows = res.get("rows", [])
        if not rows:
            break
        all_rows.extend(rows)
        if max_rows and len(all_rows) >= max_rows:
            del all_rows[max_rows:]
            break
        if res.get("lastPage") is True:
            break
        total_pages = res.get("totalPages")
        if isinstance(total_pages, int) and page >= total_pages - 1:
            break
        if limit and len(rows) < limit:
            break
    return all_rows, summary_data


def _extract_metrics_individually(session: requests.Session, headers: dict, gcid: str, original_payload: dict) -> list[float]:
    metrics = original_payload.get("metricContainer", {}).get("metrics", [])
    metric_filters = original_payload.get("metricContainer", {}).get("metricFilters", [])
    summary_values: list[float] = []
    for m in metrics:
        m_filter_ids = set(m.get("filters", []))
        needed_filters = [mf for mf in metric_filters if mf.get("id") in m_filter_ids]
        single_payload = json.loads(json.dumps(original_payload))
        single_payload["metricContainer"] = {"metrics": [m], "metricFilters": needed_filters}
        res = _post_reports(session, headers, gcid, single_payload)
        sd = res.get("summaryData", {})
        totals = sd.get("totals", [])
        if totals:
            summary_values.append(totals[0] if len(totals) == 1 else totals[0])
        else:
            ft = sd.get("filteredTotals", [])
            summary_values.append(ft[0] if ft else 0.0)
    return summary_values


def _extract_one(task: dict, headers: dict, gcid: str) -> dict:
    session = requests.Session()
    try:
        payload = json.loads(json.dumps(task["payload"]))
        rows, summary_data = _fetch_all_pages(session, headers, gcid, payload,
                                              max_rows=int(task.get("max_rows") or 0))
        task["rows"] = rows
        task["summary_data"] = summary_data
        task["ok"] = True
        task["error"] = ""
    except Exception as e:
        err_str = str(e)
        if "422" in err_str or "400" in err_str:
            try:
                summary_data = _extract_metrics_individually(session, headers, gcid, task["payload"])
                task["rows"] = []
                task["summary_data"] = summary_data
                task["ok"] = True
                task["error"] = "(fallback: individual metrics)"
            except Exception as e2:
                task["rows"] = []
                task["summary_data"] = []
                task["ok"] = False
                task["error"] = f"original: {err_str} | fallback: {str(e2)}"
        else:
            task["rows"] = []
            task["summary_data"] = []
            task["ok"] = False
            task["error"] = err_str
    finally:
        session.close()
    return task


# ─── sites_input.csv 로드 ──────────────────────────────────────────
def _load_sites_input(path: Path) -> list[tuple[str, str, str, str]]:
    """sites_input.csv 읽음 — site_code, start_date, end_date, site_group(B2B/B2C).

    - 빈 줄 / # 시작 주석 라인 무시.
    - site_code/start/end 중 하나라도 비면 skip → 앞에 빈칸(`,,,`)을 넣어 컬럼을 오른쪽으로
      민 행은 자동으로 **비활성 행**이 된다 (기존 관행 유지).
    - kind(4번째 값):
        · PANEL_GROUP_COLUMN 이 비었거나 헤더에 그 컬럼이 없으면 **전 행 PANEL_GROUP_OFF("")**
          → 분류 필터를 적용하지 않고 모든 패널을 추출한다.
        · 컬럼은 있는데 셀이 비면 PANEL_GROUP_SITE_DEFAULT.
        · 등록 안 된 값이면 경고 후 PANEL_GROUP_SITE_DEFAULT.
    """
    rows: list[tuple[str, str, str, str]] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8-sig") as f:
        # 주석 라인 skip 후 DictReader
        lines = [ln for ln in f if ln.strip() and not ln.strip().startswith("#")]
    known_groups = {k for _, k in PANEL_GROUP_RULES}
    reader = csv.DictReader(lines)
    # 컬럼명이 비어 있으면(기본값) 기능 자체를 쓰지 않는다
    has_group_col = bool(PANEL_GROUP_COLUMN) and PANEL_GROUP_COLUMN in (reader.fieldnames or [])
    for r in reader:
        site = (r.get("site_code") or "").strip()
        s = (r.get("start_date") or "").strip()
        e = (r.get("end_date") or "").strip()
        if has_group_col:
            kind = (r.get(PANEL_GROUP_COLUMN) or "").strip().upper() or PANEL_GROUP_SITE_DEFAULT
            if known_groups and kind not in known_groups:
                print(f"  ⚠ sites_input '{site}' 의 {PANEL_GROUP_COLUMN}='{kind}' 는 미등록 값 "
                      f"(등록: {sorted(known_groups)}) → {PANEL_GROUP_SITE_DEFAULT} 로 처리")
                kind = PANEL_GROUP_SITE_DEFAULT
        else:
            kind = PANEL_GROUP_OFF   # 분류 컬럼 미설정/미존재 → 필터 없이 전 패널 대상
        if site and s and e:
            rows.append((site, s, e, kind))
    return rows


# ─── app_O_X.csv 로드 + device 케이스 선택 (v3.8) ──────────────────
def _load_app_ox(path: Path) -> dict[str, str] | None:
    """app_O_X.csv → {site_code: "O"|"X"}. 1열 = site_code, 2열 = O/X (헤더명 무관, 위치 기준).
    파일 없으면 None (전 site O 간주)."""
    if not path.exists():
        return None
    ox: dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    for r in rows[1:]:   # 헤더 skip
        if len(r) < 2:
            continue
        site = (r[0] or "").strip()
        flag = (r[1] or "").strip().upper()
        if site and flag in ("O", "X"):
            ox[site] = flag
    return ox


def _app_flag(site_code: str, ox_map: dict[str, str] | None) -> str:
    """site 의 App 론치 O/X. ① 그대로 → ② `_old` 접미사 제거 후 → ③ 경고 + X 간주.
    ox_map 이 None (csv 없음) 이면 O."""
    if ox_map is None:
        return "O"
    sc = site_code.strip()
    if sc in ox_map:
        return ox_map[sc]
    if sc.endswith("_old") and sc[:-4] in ox_map:
        return ox_map[sc[:-4]]
    print(f"  ⚠ app_O_X.csv 에 '{sc}' 없음 (_old 제거 후에도 미매칭) → X 간주 (PC/Mobile 류만 추출)")
    return "X"


def _cases_for_flag(app_flag: str) -> list[dict]:
    """app 론치 flag(O/X)에 적용할 DEVICE_CASES 부분집합. X 면 requires_app 케이스 제외."""
    if app_flag == "O":
        return list(DEVICE_CASES)
    return [c for c in DEVICE_CASES if not c.get("requires_app")]


# ═══════════════════════════════════════════════════════════════════════
# ★ contents 전용 — device 세그 치환(swap)
# ═══════════════════════════════════════════════════════════════════════
def _swap_cases_for_flag(app_flag: str) -> list[dict]:
    """이 site 에 돌릴 DEVICE_SWAP_CASES 부분집합.
    DEVICE_SWAP_APPLY_APP_OX=False(기본)면 app 론치 여부와 무관하게 5종 전부 돌린다
    (0 처리는 정제 단계 담당 — 상단 상수 설명 참조)."""
    if not DEVICE_SWAP_ENABLED:
        return []
    if not DEVICE_SWAP_APPLY_APP_OX or app_flag == "O":
        return list(DEVICE_SWAP_CASES)
    return [c for c in DEVICE_SWAP_CASES if not c.get("requires_app")]


def _apply_device_swap(payload: dict,
                       seg_names_per_metric: list[list[str]],
                       panel_seg_names: list[str],
                       replace: dict[str, tuple[str, str]]) -> None:
    """payload 안 segmentId 를 replace 표대로 **제자리 치환**하고, 세그 이름도 같이 바꾼다.

    replace = {옛_세그_ID: (새_세그_이름, 새_세그_ID)}

    이름까지 바꾸는 이유: 출력 CSV 의 `segments` 컬럼이 곧 정제코드의 ITEM 소스라,
    ID 만 바꾸고 이름을 두면 실제 데이터는 Mobile 인데 표기는 PC 로 남는다.
    """
    if not replace:
        return
    id2new       = {old: new_id   for old, (_name, new_id) in replace.items()}
    name_by_old_id = {old: new_name for old, (new_name, _id) in replace.items()}
    # 옛 ID → 옛 이름 (payload 에는 이름이 없으므로 상단 상수로 역매핑)
    old_id2name = {BASE_PC_ID: BASE_PC_NAME, BASE_EXCLUDED_APP_ID: BASE_EXCLUDED_APP_NAME}
    old_name2new_name = {old_id2name[o].lower(): n
                         for o, n in name_by_old_id.items() if o in old_id2name}

    def _swap_ids(lst) -> None:
        for f in lst or []:
            if f.get("type") != "segment":
                continue
            sid = f.get("segmentId")
            if sid in id2new:
                f["segmentId"] = id2new[sid]

    _swap_ids(payload.get("globalFilters", []))
    _swap_ids(payload.get("metricContainer", {}).get("metricFilters", []))

    def _swap_name(name: str) -> str:
        return old_name2new_name.get((name or "").strip().lower(), name)

    for i, names in enumerate(seg_names_per_metric):
        seg_names_per_metric[i] = [_swap_name(n) for n in names]
    for i, n in enumerate(panel_seg_names):
        panel_seg_names[i] = _swap_name(n)


def _panel_group(panel_name: str) -> str:
    """패널명 → 'B2B' / 'B2C'. PANEL_GROUP_RULES 위에서부터 부분일치(대소문자 무시).
    ⚠ 'B2B' 가 먼저 — '② Global - B2B (B2C RS)' 처럼 두 토큰이 다 든 구 패널명 대응."""
    if not PANEL_GROUP_RULES:
        return PANEL_GROUP_OFF   # 분류 룰 미설정 = 기능 off
    pn = (panel_name or "").upper()
    for kw, kind in PANEL_GROUP_RULES:
        if kw.upper() in pn:
            return kind
    return PANEL_GROUP_PANEL_DEFAULT


def _site_panel_keywords(site_code: str) -> list[str]:
    """그 site 의 전용 패널 키워드. SITE_PANEL_ALIAS 우선, 없으면 site_code 자체."""
    sc = site_code.strip().lower()
    return SITE_PANEL_ALIAS.get(sc) or [sc]


def _panel_owner_sites(panel_name: str) -> list[str]:
    """패널명에 **토큰 경계**로 들어있는 등록 site_code 목록 (없으면 [] = 공용 패널).
    'hq' 가 'second'/'section' 에 우연히 걸리지 않도록 경계 정규식을 쓴다."""
    pn = panel_name or ""
    out = []
    for sc in SITE_PANEL_SITES:
        for kw in _site_panel_keywords(sc):
            if re.search(SITE_PANEL_BOUNDARY.format(kw=re.escape(kw)), pn, re.IGNORECASE):
                out.append(sc.strip().lower())
                break
    return out


def _skip_by_site_panel(panel_name: str, site_code: str, include_global_for_us: bool) -> tuple[bool, str]:
    """SITE_PANEL_SITES 매핑 룰. (skip, reason)."""
    sc = site_code.strip().lower()
    owners = _panel_owner_sites(panel_name)
    registered = {s.strip().lower() for s in SITE_PANEL_SITES}
    if owners:
        if sc not in owners:
            return True, f"'{'/'.join(owners)}' 전용 패널 → {sc} skip"
        return False, ""
    # (2026-07-31) positive 예외 — 등록 site 라도 SITE_EXTRA_PANELS 에 적힌 공용 패널은 함께 돈다.
    #   예) us = 전용 'US B2B (Y26용)' + 공용 'Global B2C'
    for _kw in SITE_EXTRA_PANELS.get(sc, []):
        if _kw.lower() in (panel_name or "").lower():
            return False, ""
    # 공용 패널 — 전용 패널을 가진 site 는 기본 제외 (기존 [Global] skip 과 같은 취지)
    if sc in registered and not include_global_for_us:
        return True, f"{sc} 는 전용 패널만 사용 → 공용 패널 skip (--include-global-for-us 로 포함)"
    return False, ""


def _skip_by_prefix(panel_name: str, site_code: str, include_global_for_us: bool) -> tuple[bool, str]:
    """기존 v4.2 US/Global 접두 룰. (skip, reason)."""
    is_us = site_code.lower() == US_SITE_CODE
    if panel_name.startswith(US_PANEL_PREFIX) and not is_us:
        return True, f"non-us site → {US_PANEL_PREFIX} panel skip"
    if panel_name.startswith(GLOBAL_PANEL_PREFIX) and is_us and not include_global_for_us:
        return True, f"us site → {GLOBAL_PANEL_PREFIX} panel skip (use --include-global-for-us to keep)"
    return False, ""


def _skip_by_site(panel_name: str, site_code: str, include_global_for_us: bool) -> tuple[bool, str]:
    """site ↔ panel 룰 — SITE_PANEL_SITES 가 비어있으면 기존 접두 룰, 아니면 매핑 룰."""
    if SITE_PANEL_SITES:
        return _skip_by_site_panel(panel_name, site_code, include_global_for_us)
    return _skip_by_prefix(panel_name, site_code, include_global_for_us)


def _should_skip_panel(panel_name: str, site_code: str, include_global_for_us: bool,
                       site_group: str = PANEL_GROUP_OFF) -> tuple[bool, str]:
    """site × panel 룰 적용. (skip, reason) 반환.
    ① site kind(B2B/B2C) ↔ panel kind 불일치면 skip  ② site ↔ panel 룰(_skip_by_site).
    site_group 가 PANEL_GROUP_OFF("") 면 ①을 건너뛴다 (분류 기능을 안 쓰는 폴더)."""
    s_grp = (site_group or PANEL_GROUP_OFF).strip().upper()
    if not s_grp:
        # 분류 미설정/미해당 → 필터 없이 site↔패널 룰만 적용
        return _skip_by_site(panel_name, site_code, include_global_for_us)
    p_grp = _panel_group(panel_name)
    if p_grp != s_grp:
        return True, f"site kind={s_grp} → {p_grp} panel skip"
    if s_grp in PANEL_GROUP_SKIP_SITE_PANEL_RULE:
        # kind 별 패널이 1개뿐이라 [US]/[Global] 중복 위험 없음 → site↔panel 룰 면제
        # (없으면 us_old(B2C) 행이 공용/Global 패널 skip 에 걸려 아무것도 못 돈다)
        return False, ""
    return _skip_by_site(panel_name, site_code, include_global_for_us)


def _extras_for_panel(panel_name: str, resolved_extras: list[tuple[str, object]]) -> list[str]:
    """resolved_extras = [(segment_id, panel_scope), ...] → 해당 panel 에 적용할 segment_id 목록."""
    out: list[str] = []
    pname_lower = (panel_name or "").lower()
    for sid, scope in resolved_extras:
        if scope == "all":
            out.append(sid)
        elif isinstance(scope, list) and any(str(kw).lower() in pname_lower for kw in scope):
            out.append(sid)
    return out


# ─── site 단위 처리 ────────────────────────────────────────────────
def _process_site(headers: dict, gcid: str, project: dict, panels: list[dict],
                  site: SiteInfo, start_date: str, end_date: str,
                  *, workers: int, limit: int, dry_run: bool, ts: str,
                  include_global_for_us: bool,
                  resolved_extras: list[tuple[str, object]] | None = None,
                  app_ox: dict[str, str] | None = None,
                  file_tag: str = "",
                  site_group: str = PANEL_GROUP_SITE_DEFAULT) -> dict:
    """한 site 의 모든 panel × reportlet 추출 + CSV 저장.
    resolved_extras: [(segment_id, panel_scope), ...] — v3 신규.
    app_ox: app_O_X.csv 로드 결과 (v3.8 DEVICE_CASES 케이스 선택용, None=csv 없음=전 site O).
    file_tag: 출력 파일명 site 뒤에 붙는 태그 (v4.2 YEAR_OFFSETS 의 '_y2025' 등, ""=없음).
    site_group: sites_input 의 B2B_B2C 값 — 그 종류의 패널만 돈다."""
    # v4.2: MONTHLY 면 총기간을 달력 월 조각으로 분할 (False 면 조각 1개 = 총기간)
    periods = _split_months(start_date, end_date)
    print(f"\n{'═'*78}\nSITE: {site.site_code}{file_tag}  →  rsid={site.rsid}  "
          f"({start_date} ~ {end_date})  [{site_group}]\n{'═'*78}")
    if MONTHLY:
        print(f"  기간 분할(MONTHLY): {len(periods)}개  "
              f"[{periods[0][2]} ~ {periods[-1][2]}]")
    if resolved_extras:
        print(f"  extra segments ({len(resolved_extras)}):")
        for sid, scope in resolved_extras:
            scope_str = "all panels" if scope == "all" else f"panel keyword {scope}"
            print(f"    + {sid}  '{_SEG_NAME_CACHE.get(sid, '')}'  → {scope_str}")

    # v3.8: 이 site 에 적용할 device 케이스 (DEVICE_CASES=[] 면 [None] = 케이스 반복 없음)
    site_cases: list = [None]
    site_seg_override: dict[str, str] = {}
    # ★ contents: device 세그 swap 케이스가 켜져 있으면 그걸 site_cases 로 쓴다.
    #   각 케이스에 "replace" 키가 있으면 아래 task 루프가 add 대신 swap 경로를 탄다.
    if DEVICE_SWAP_ENABLED and DEVICE_SWAP_CASES:
        _flag = _app_flag(site.site_code, app_ox)
        site_cases = _swap_cases_for_flag(_flag)
        _labels = [c["device"] for c in site_cases]
        _ox = "적용" if DEVICE_SWAP_APPLY_APP_OX else "미적용(정제에서 0 처리)"
        print(f"  device swap ({len(_labels)}종, app={_flag}, app_O_X {_ox}): {', '.join(_labels)}")
    elif DEVICE_CASES:
        _flag = _app_flag(site.site_code, app_ox)
        site_cases = _cases_for_flag(_flag)
        _labels = [c["device"] for c in site_cases]
        print(f"  device cases ({len(_labels)}, app={_flag}): {', '.join(_labels)}")
        site_seg_override = DEVICE_CASE_SITE_OVERRIDES.get(site.site_code) or {}
        for _src, _dst in site_seg_override.items():
            print(f"  [seg-override] {_SEG_NAME_CACHE.get(_src) or _src} → "
                  f"{_SEG_NAME_CACHE.get(_dst) or _dst}")

    # ─── kind 필터 유효성 검사 (하위호환 안전장치) ───────────────────
    # 이 프로젝트에 그 분류의 패널이 **아예 없으면** 분류 필터를 끈다.
    # 안 그러면 매칭되는 패널이 0개가 되어 그 site 가 통째로 skip 된다.
    #   실제 사례: App Traffic 프로젝트(패널 'Global'/'Korea' = 전부 B2B 판정)에
    #   Channel Detail 용 sites_input(us=B2C 행 포함)을 그대로 복사해 쓰던 폴더.
    eff_group = (site_group or PANEL_GROUP_OFF).strip().upper()
    if eff_group:
        avail_kinds = {_panel_group(p.get("name", "")) for p in panels}
        if eff_group not in avail_kinds:
            print(f"  ⚠ 이 프로젝트에 {eff_group} 패널이 없음 (있는 kind: {sorted(avail_kinds)}) "
                  f"→ 분류 필터 미적용 (이 site 는 모든 패널을 추출)")
            eff_group = PANEL_GROUP_OFF

    tasks: list[dict] = []
    task_order = 0
    for p_idx, panel in enumerate(panels):
        p_name = panel.get("name", f"(panel-{p_idx})")
        if REQUIRED_PANEL_KEYWORDS and not any(kw in p_name for kw in REQUIRED_PANEL_KEYWORDS):
            continue
        skip, reason = _should_skip_panel(p_name, site.site_code, include_global_for_us, eff_group)
        if skip:
            print(f"  ⊘ panel skip: {p_name}  ({reason})")
            continue
        extra_ids_for_panel = _extras_for_panel(p_name, resolved_extras or [])
        rep_iter = list(_iter_panel_reportlets(panel))
        for r_idx, (assigned_num, rep) in enumerate(rep_iter):
            r_name = rep.get("name", f"(reportlet-{r_idx})")
            # v3.3: 테이블(reportlet) 단위 필터 — REQUIRED_TABLE_KEYWORDS 비어있으면 통과(기존 동작)
            if REQUIRED_TABLE_KEYWORDS and not any(kw in r_name for kw in REQUIRED_TABLE_KEYWORDS):
                continue
            slug = _slugify(r_name)
            if assigned_num:
                x, y = assigned_num
                slug = f"{x}_{y}_{slug}" if not slug.startswith(f"{x}_{y}_") else slug
            tb_name = slug if slug else f"table_{r_idx}"
            # v3.8: device 케이스별 task 1개씩 (case=None 이면 v3.7 동작 — 1개)
            for case in site_cases:
                # ★ contents swap 케이스는 세그를 '추가'하지 않는다 (payload 안에서 치환).
                is_swap = bool(case) and "replace" in case
                # site 별 세그 치환 적용 (DEVICE_CASE_SITE_OVERRIDES — us_old 류)
                case_seg_ids = ([site_seg_override.get(s, s) for s in case["segment_ids"]]
                                if (case and not is_swap) else [])
                # 기간조각(MONTHLY)별 task 1개씩 (MONTHLY=False 면 조각이 1개 = 총기간 1회)
                for pd_start, pd_end, pd_label in periods:
                    payload, seg_names_per_metric, metric_names, panel_seg_names, dim_id, dim_name = \
                        _build_report_payload(project, panel, rep,
                                              override_rsid=site.rsid,
                                              override_date_range=_build_date_range_definition(pd_start, pd_end),
                                              extra_segment_ids=extra_ids_for_panel + case_seg_ids)
                    payload["settings"]["limit"] = min(limit, 100000) if limit > 0 else PAGE_SIZE_UNCAPPED
                    if is_swap:
                        # ★ contents: payload 안 device 세그를 갈아끼우고 세그 이름도 함께 치환
                        _apply_device_swap(payload, seg_names_per_metric,
                                           panel_seg_names, case["replace"])
                    elif case:
                        # 케이스 세그 name 을 각 컬럼 stack 에 append — CSV segments 컬럼에 드러나고
                        # _parse_device 도 같은 라벨 도출 (검증용 일관성)
                        case_names = [_SEG_NAME_CACHE.get(sid) or sid for sid in case_seg_ids]
                        seg_names_per_metric = [list(lst) + case_names for lst in seg_names_per_metric]
                    tasks.append({
                        "order": task_order,
                        "panel_idx": p_idx,
                        "panel_name": p_name,
                        "reportlet_name": r_name,
                        "tb_name": tb_name,
                        "device_case": case["device"] if case else "",   # v3.8
                        "period_start": pd_start,     # v4.2: 이 task 의 실제 추출 기간
                        "period_end": pd_end,
                        "period_label": pd_label,     # MONTHLY 일 때만 'Jul 2025', 아니면 ""
                        "payload": payload,
                        "max_rows": limit,   # v3.7: dim1(1st level) 행 cap (0=무제한)
                        "seg_names_per_metric": seg_names_per_metric,
                        "metric_names": metric_names,
                        "panel_segments": panel_seg_names,
                        "dimension_id": dim_id,
                        "dimension_name": dim_name,
                        "breakdown_chain": _detect_breakdown_chain(rep) if BREAKDOWN_ENABLED else [],
                        "breakdown_branches": _breakdown_top_branches(rep) if BREAKDOWN_ENABLED else [],
                        "breakdown_rows": [],
                        "rows": [],
                        "summary_data": [],
                        "ok": False,
                        "error": "",
                    })
                    task_order += 1
    print(f"  payload {len(tasks)}개 생성")

    if dry_run:
        return {"site": site, "tasks": tasks, "n_ok": 0, "n_fail": 0}

    print(f"  /reports 호출 (workers={workers}) ...")
    start_time = datetime.now()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_extract_one, t, headers, gcid): t for t in tasks}
        done_count = 0
        for fut in as_completed(futures):
            done_count += 1
            result = fut.result()
            status = "OK" if result["ok"] else f"FAIL: {result['error'][:50]}"
            dev_tag = f" [{result['device_case']}]" if result.get("device_case") else ""
            pd_tag = f" [{result['period_label']}]" if result.get("period_label") else ""   # v4.2
            print(f"    [{done_count}/{len(tasks)}] {result['tb_name']}{dev_tag}{pd_tag}: "
                  f"{len(result['rows'])} rows — {status}")
    elapsed = datetime.now() - start_time
    print(f"  소요: {elapsed}")

    tasks.sort(key=lambda t: t["order"])
    n_ok = sum(1 for t in tasks if t["ok"])
    n_fail = sum(1 for t in tasks if not t["ok"])

    # ─── N단계 breakdown (v3.5) — dim1 rows 를 하위 차원으로 재귀 분해 ───
    bd_tasks = [t for t in tasks if t.get("ok") and t.get("breakdown_chain")]
    if ESTIMATE_ONLY:
        if bd_tasks:
            _estimate_runtime(bd_tasks, headers, gcid, workers=workers)
        else:
            print("  [estimate] breakdown 대상 테이블 없음 — 추정 불가 (dim1 만)")
        return {"site": site, "tasks": tasks, "n_ok": n_ok, "n_fail": n_fail, "estimate": True}
    if bd_tasks:
        topn_str = "전체" if BREAKDOWN_TOP_N == 0 else f"레벨별 상위 {BREAKDOWN_TOP_N}"
        print(f"  breakdown 단계 시작 ({len(bd_tasks)}개 테이블, TOP_N={topn_str}) ...")
        bd_start = datetime.now()
        total_bd_calls = 0
        for t in bd_tasks:
            chain_str = " → ".join(d for d, _ in t["breakdown_chain"])
            calls = _run_breakdowns(t, headers, gcid, workers=workers)
            total_bd_calls += calls
            dev_tag = f" [{t['device_case']}]" if t.get("device_case") else ""
            dev_tag += f" [{t['period_label']}]" if t.get("period_label") else ""   # v4.2
            br = t.get("breakdown_branches") or []
            used = t["breakdown_chain"][0][0] if t["breakdown_chain"] else ""
            others = list(dict.fromkeys(d for d in br if d != used))
            bd_tag = (f"  ⚠ BD가지 {len(br)}개 (다른가지: {', '.join(others)})"
                      if len(br) > 1 else f"  · BD가지 {len(br)}개")
            print(f"    {t['tb_name']}{dev_tag}: dim1 {len(t['rows'])}행 → [{chain_str}] "
                  f"breakdown rows {len(t['breakdown_rows'])}개 (호출 {calls}회){bd_tag}")
        print(f"  breakdown 소요: {datetime.now() - bd_start}  (총 호출 {total_bd_calls}회)")

    # CSV 저장 (v3.7 파일명 개편 — 기존 extract_data_* → stack, column_mapping_* → table)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # v4.2: file_tag = YEAR_OFFSETS 로 shift 한 run 의 '_y2025' 등 (offset 0 / 미사용이면 "")
    stack_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}{OUTPUT_BASENAME_STACK}_{site.site_code}{file_tag}_{ts}.csv"
    table_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}{OUTPUT_BASENAME_TABLE}_{site.site_code}{file_tag}_{ts}.csv"

    # dim_short — task 들의 dimension 마지막 토큰 (variables/evar26 → evar26).
    # 여러 dimension 섞여있으면 generic "dim_value" 로 fallback.
    dim_short_set = set()
    for t in tasks:
        did = t.get("dimension_id", "")
        if did and "/" in did:
            dim_short_set.add(did.split("/")[-1])
        elif did:
            dim_short_set.add(did)
    dim_short = next(iter(dim_short_set)) if len(dim_short_set) == 1 else "dim_value"

    # long format unpivot: 1 row = 1 dimension value × 1 metric position
    # v3.5: breakdown 도달 최대 깊이만큼 bd{k}_dimension/itemId/value 컬럼 셋(레벨당 3개) 추가.
    #   parent(dim1 총계) 행은 bd* 전부 공백 → v3.4 출력 상위호환.
    max_bd_depth = max((len(br["path"]) - 1
                        for t in tasks for br in (t.get("breakdown_rows") or [])),
                       default=0)
    bd_blank = [""] * (3 * max_bd_depth)
    # v4.2: MONTHLY 일 때만 end_date 뒤에 period 컬럼 추가 (False 면 컬럼 자체가 없어 v4.1 출력과 동일)
    period_header = ["period"] if MONTHLY else []
    with open(stack_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        header = ["site_code", "rsid", "start_date", "end_date"] + period_header + [
                  "panel", "table", "reportlet", "dimension", "dimension_name",
                  "itemId", dim_short,
                  "value_n", "metric_origin", "metric", "segments", "device",
                  "value1"]
        for k in range(1, max_bd_depth + 1):
            header += [f"bd{k}_dimension", f"bd{k}_itemId", f"bd{k}_value"]
        w.writerow(header)
        for t in tasks:
            if not t["ok"]:
                continue
            dim_id = t.get("dimension_id", "")
            dim_name = t.get("dimension_name", "")
            metric_names = t.get("metric_names") or []
            seg_names_per_metric = t.get("seg_names_per_metric") or []
            # v4.2: 기간은 site 총기간이 아니라 이 task 의 기간조각 (MONTHLY=False 면 총기간과 동일)
            base_cols = ([site.site_code, site.rsid, t["period_start"], t["period_end"]]
                         + ([t["period_label"]] if MONTHLY else [])
                         + [t["panel_name"], t["tb_name"], t["reportlet_name"], dim_id, dim_name])
            # v4.1: INCLUDE_PARENT_ROWS=False 면 dim1 총계(부모) 행을 skip (breakdown 행만 출력)
            if INCLUDE_PARENT_ROWS:
                summary = t.get("summary_data", [])
                rows = t["rows"]
                # ★ contents: 데이터가 아예 없는 테이블도 컬럼별 0 행을 남긴다.
                #   AA 가 세그 조합에 해당하는 게 없으면 rows/summary 를 다 비워 보내는데,
                #   그대로 두면 그 device 가 출력에서 통째로 사라진다(구 column_mapping 은
                #   컬럼 기준이라 항상 17행을 썼다). 실제 사례: us_old(구 US suite)의 app —
                #   `[Global] App Only` 가 그 suite 에 데이터가 없어 5개 테이블 전부 빈 응답.
                #   행을 남겨야 "0 이었다"와 "안 뽑혔다"를 구분할 수 있다.
                if EMIT_ZERO_ROWS_WHEN_EMPTY and not summary and not rows and metric_names:
                    for i, m_name in enumerate(metric_names, start=1):
                        seg_list = seg_names_per_metric[i-1] if i-1 < len(seg_names_per_metric) else []
                        seg_str = "; ".join(s for s in seg_list if s)
                        device = t.get("device_case") or _parse_device(seg_list)
                        w.writerow(base_cols + ["", "(summary)", f"value{i}", m_name,
                                                _normalize_metric(m_name), seg_str, device, 0]
                                   + bd_blank)
                elif summary and not rows:
                    # summary 만 (dimension row 없음) — itemId/dim_value 비우고 metric N개 unpivot
                    for i, v in enumerate(summary, start=1):
                        m_name = metric_names[i-1] if i-1 < len(metric_names) else ""
                        seg_list = seg_names_per_metric[i-1] if i-1 < len(seg_names_per_metric) else []
                        seg_str = "; ".join(s for s in seg_list if s)
                        device = t.get("device_case") or _parse_device(seg_list)   # v3.8: 케이스 라벨 우선
                        w.writerow(base_cols + ["", "(summary)", f"value{i}", m_name, _normalize_metric(m_name), seg_str, device,
                                                v if v is not None else ""] + bd_blank)
                else:
                    # outer loop = metric (value_n), inner loop = dimension rows
                    # → value1 전체 dim → value2 전체 dim ... (의도 csv 의 정렬)
                    max_data = max((len(r.get("data") or []) for r in rows), default=0)
                    n = max(len(metric_names), max_data)
                    for i in range(n):
                        m_name = metric_names[i] if i < len(metric_names) else ""
                        seg_list = seg_names_per_metric[i] if i < len(seg_names_per_metric) else []
                        seg_str = "; ".join(s for s in seg_list if s)
                        device = t.get("device_case") or _parse_device(seg_list)   # v3.8: 케이스 라벨 우선
                        vn = f"value{i+1}"
                        for r in rows:
                            item_id = r.get("itemId", "")
                            dim_val = r.get("value", "")
                            data = r.get("data") or []
                            v = data[i] if i < len(data) else ""
                            w.writerow(base_cols + [item_id, dim_val, vn, m_name, _normalize_metric(m_name), seg_str, device,
                                                    v if v is not None else ""] + bd_blank)
            # v3.5: breakdown rows — itemId/dim_short = path[0](dim1 부모), bd{k}_* = path[k]
            bd_rows = t.get("breakdown_rows") or []
            if bd_rows:
                bd_n = max(len(metric_names),
                           max((len(br.get("data") or []) for br in bd_rows), default=0))
                for i in range(bd_n):
                    m_name = metric_names[i] if i < len(metric_names) else ""
                    seg_list = seg_names_per_metric[i] if i < len(seg_names_per_metric) else []
                    seg_str = "; ".join(s for s in seg_list if s)
                    device = t.get("device_case") or _parse_device(seg_list)   # v3.8: 케이스 라벨 우선
                    vn = f"value{i+1}"
                    for br in bd_rows:
                        path = br["path"]
                        p0 = path[0]   # (dim1_id, dim1_name, item_id, value)
                        data = br.get("data") or []
                        v = data[i] if i < len(data) else ""
                        bd_cells: list = []
                        for k in range(1, max_bd_depth + 1):
                            if k < len(path):
                                bdid, _bdname, bditem, bdval = path[k]
                                bd_cells += [bdid, bditem, bdval]
                            else:
                                bd_cells += ["", "", ""]
                        w.writerow(base_cols + [p0[2], p0[3], vn, m_name, _normalize_metric(m_name), seg_str, device,
                                                v if v is not None else ""] + bd_cells)
    print(f"  stack CSV: {stack_path.name}  ({dim_short} long unpivot, bd depth={max_bd_depth})")
    _verify_csv_written(stack_path, "stack")

    # ─── table_data_extract (v3.7, 기존 column_mapping 대체) — AA 테이블 모양 가로형 ───
    #   1행 = dimension item (또는 breakdown 행, 또는 "(summary)" 총계 행).
    #   value1..N = 컬럼 stack 별 값 (N = site 내 테이블들의 최대 컬럼 수, 모자라면 빈칸).
    #   seg_value{i} = "metric;; segments" — metric 맨앞 + SEG_VALUE_SEP(';;') + 컬럼 stack 세그 ('; ' join).
    #   테이블 블록 순서: (summary) 총계 행 → dim1 item 행들 → breakdown 행들 (bd{k}_* 컬럼).
    def _task_nvals(t: dict) -> int:
        n = max(len(t.get("metric_names") or []), len(t.get("summary_data") or []))
        n = max(n, max((len(r.get("data") or []) for r in (t.get("rows") or [])), default=0))
        n = max(n, max((len(br.get("data") or []) for br in (t.get("breakdown_rows") or [])), default=0))
        return n
    site_max_vals = max((_task_nvals(t) for t in tasks if t["ok"]), default=0)

    def _seg_values(t: dict) -> list[str]:
        out: list[str] = []
        metric_names = t.get("metric_names") or []
        seg_names_per_metric = t.get("seg_names_per_metric") or []
        for i in range(site_max_vals):
            m_name = metric_names[i] if i < len(metric_names) else ""
            seg_list = seg_names_per_metric[i] if i < len(seg_names_per_metric) else []
            seg_str = "; ".join(s for s in seg_list if s)
            out.append(f"{m_name}{SEG_VALUE_SEP}{seg_str}" if (m_name and seg_str) else (m_name or seg_str))
        return out

    def _vals_row(data: list) -> list:
        return [(data[i] if i < len(data) and data[i] is not None else "")
                for i in range(site_max_vals)]

    with open(table_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        header = ["site_code", "rsid", "start_date", "end_date"] + period_header + [
                  "panel", "table", "reportlet", "dimension", "dimension_name",
                  "device",   # v3.8: 같은 테이블이 device 케이스 수만큼 반복 — 행 구분용
                  "itemId", dim_short]
        header += [f"value{i}" for i in range(1, site_max_vals + 1)]
        header += [f"seg_value{i}" for i in range(1, site_max_vals + 1)]
        for k in range(1, max_bd_depth + 1):
            header += [f"bd{k}_dimension", f"bd{k}_itemId", f"bd{k}_value"]
        w.writerow(header)
        for t in tasks:
            if not t["ok"]:
                continue
            # v4.2: 기간 = 이 task 의 기간조각 (+ MONTHLY 면 period 라벨)
            base_cols = ([site.site_code, site.rsid, t["period_start"], t["period_end"]]
                         + ([t["period_label"]] if MONTHLY else [])
                         + [t["panel_name"], t["tb_name"], t["reportlet_name"],
                            t.get("dimension_id", ""), t.get("dimension_name", ""),
                            t.get("device_case", "")])   # v3.8
            seg_vals = _seg_values(t)
            # v4.1: INCLUDE_PARENT_ROWS=False 면 dim1 총계/summary 행을 skip (breakdown 행만 출력)
            if INCLUDE_PARENT_ROWS:
                summary = t.get("summary_data", [])
                if summary:
                    w.writerow(base_cols + ["", "(summary)"] + _vals_row(summary) + seg_vals + bd_blank)
                for r in t["rows"]:
                    w.writerow(base_cols + [r.get("itemId", ""), r.get("value", "")]
                               + _vals_row(r.get("data") or []) + seg_vals + bd_blank)
            for br in t.get("breakdown_rows") or []:
                path = br["path"]
                p0 = path[0]   # (dim1_id, dim1_name, item_id, value)
                bd_cells: list = []
                for k in range(1, max_bd_depth + 1):
                    if k < len(path):
                        bdid, _bdname, bditem, bdval = path[k]
                        bd_cells += [bdid, bditem, bdval]
                    else:
                        bd_cells += ["", "", ""]
                w.writerow(base_cols + [p0[2], p0[3]]
                           + _vals_row(br.get("data") or []) + seg_vals + bd_cells)
    print(f"  table CSV: {table_path.name}  (가로형 1행/item, value1..{site_max_vals}, bd depth={max_bd_depth})")
    _verify_csv_written(table_path, "table")
    print(f"  결과: 성공 {n_ok} / 실패 {n_fail}")
    return {"site": site, "tasks": tasks, "n_ok": n_ok, "n_fail": n_fail}


def _fmt_dur(seconds: float) -> str:
    """초 → '1h 5m 30s' / '3m 24s' / '36s' (몇H 몇M 몇S 표기)."""
    seconds = int(round(max(0.0, seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if h or m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ─── 출력 CSV 자가 무결성 검증 (v4.0) ──────────────────────────────
# 쓰기 직후 다시 읽어 모든 행의 필드수가 헤더 칼럼수와 같은지 확인. OneDrive 동기화/복사 등
# 외부 요인으로 큰 CSV 가 행 경계에서 깨지는 사례를 즉시 감지(손상 자체를 막진 못해도 모르고 쓰는 걸 차단).
def _csv_integrity_check(path: Path) -> tuple[int, int, list[int]]:
    """(nrows, ncol, bad_rows) 반환 — nrows=헤더포함 행수, ncol=헤더 칼럼수,
    bad_rows=헤더와 필드수 다른 행 번호. 못 읽으면 (-1, -1, [-1]).
    bare CR(\\r 단독)은 sentinel 로 치환해 파서 중단을 막고 손상 행으로 검출."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        return (-1, -1, [-1])
    text = re.sub(r"\r(?!\n)", "␍", text)
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return (0, 0, [])
    ncol = len(rows[0])
    bad = [i for i, r in enumerate(rows) if len(r) != ncol]
    return (len(rows), ncol, bad)


def _verify_csv_written(path: Path, label: str) -> bool:
    """쓴 CSV 를 다시 읽어 전 행 필드수 검증. 정상이면 ✓, 손상이면 ⚠/❌ 출력."""
    nrows, ncol, bad = _csv_integrity_check(path)
    if bad == [-1]:
        print(f"  ❌ {label} CSV 검증 불가(재읽기 실패) — 재추출 권장: {path.name}")
        return False
    if bad:
        sample = ", ".join(str(b) for b in bad[:10])
        more = " ..." if len(bad) > 10 else ""
        print(f"  ⚠ {label} CSV 무결성 경고: {len(bad)}개 행 필드수 불일치 (행 {sample}{more})")
        print(f"     → 쓰여진 뒤 외부 요인(OneDrive 동기화/복사 등)으로 손상됐을 수 있음. 재추출 권장: {path.name}")
        return False
    print(f"  ✓ {label} 무결성 OK (데이터 {max(0, nrows - 1):,}행 × {ncol}칼럼)")
    return True


# ─── main ────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="사이트별 RSID + dateRange override 데이터 추출 (v3: EXTRA_SEGMENTS 옵션)")
    parser.add_argument("--dry-run", action="store_true", help="payload 생성까지만")
    parser.add_argument("--estimate", action="store_true",
                        help="breakdown 단계별 1경로 샘플로 총 /reports 호출수·예상 소요시간 추정 후 종료 (실제 추출·파일생성 안 함)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"병렬 워커 수 (default {MAX_WORKERS})")
    parser.add_argument("--limit", type=int, default=LIMIT_LV1,
                        help=f"1st level(dim1) reportlet 당 최대 행 수 (default {LIMIT_LV1}, 0=무제한)")
    parser.add_argument("--limit-bd", type=int, default=LIMIT_BD, metavar="N",
                        help=f"breakdown 1단계 (bd1, =level2) 부모 item 당 최대 행 수 "
                             f"(default {LIMIT_BD}, 0=무제한. BREAKDOWN_TOP_N>0 이면 TOP_N 우선)")
    parser.add_argument("--limit-bd2", type=int, default=LIMIT_BD2, metavar="N",
                        help=f"breakdown 2단계 (bd2, =level3) 부모 item 당 최대 행 수 (default {LIMIT_BD2}, 0=무제한)")
    parser.add_argument("--limit-bd3", type=int, default=LIMIT_BD3, metavar="N",
                        help=f"breakdown 3단계 (bd3, =level4) 부모 item 당 최대 행 수 (default {LIMIT_BD3}, 0=무제한)")
    parser.add_argument("--limit-bd4", type=int, default=LIMIT_BD4, metavar="N",
                        help=f"breakdown 4단계+ (bd4~, =level5~) 부모 item 당 최대 행 수 (default {LIMIT_BD4}, 0=무제한)")
    parser.add_argument("--site", action="append", default=[], metavar="SITE_CODE",
                        help="특정 site 만 처리 (여러 개 가능). 없으면 sites_input.csv 전체")
    # ★ contents 전용 — device 를 하나만 골라 추출 (재추출·디버깅용). 생략하면 5종 전부.
    parser.add_argument("--device", action="append", default=[], metavar="DEVICE",
                        choices=[c["device"] for c in DEVICE_SWAP_CASES],
                        help="이 device 만 추출 (여러 번 지정 가능). 생략 시 전 device")
    parser.add_argument("--include-global-for-us", action="store_true",
                        default=INCLUDE_GLOBAL_FOR_US,
                        help=f"us site 일 때도 {GLOBAL_PANEL_PREFIX} panel 추출 "
                             f"(기본 skip, [US] panel 과 중복 방지)")
    parser.add_argument("--breakdown-top-n", type=int, default=None, metavar="N",
                        help="breakdown 레벨별 상위 N item 만 분해 (BREAKDOWN_TOP_N 상수 override). "
                             "0=전체, 미지정 시 상수값 사용")
    parser.add_argument("--breakdown-dims", type=str, default=None, metavar="d1,d2,...",
                        help="dim1 다음 분해 차원 id 들을 콤마로 (BREAKDOWN_DIMENSIONS override). "
                             "예: variables/product,variables/evar92")
    parser.add_argument("--breakdown-max-depth", type=int, default=None, metavar="N",
                        help="breakdown 깊이 캡 (BREAKDOWN_MAX_DEPTH override). "
                             "-1=무제한, 0=총계만(분해안함), 1=bd1까지, N=bdN까지")
    parser.add_argument("--no-parent-rows", action="store_true",
                        help="dim1 총계(부모) 행을 출력에서 제외 — breakdown 행만 (INCLUDE_PARENT_ROWS=False)")
    parser.add_argument("--monthly", dest="monthly", action="store_true", default=None,
                        help="(v4.2) sites_input 총기간을 달력 월로 쪼개 월별 추출 (MONTHLY=True). 출력에 period 컬럼 추가")
    parser.add_argument("--no-monthly", dest="monthly", action="store_false",
                        help="(v4.2) 총기간 1회 추출 (MONTHLY=False)")
    parser.add_argument("--year-offsets", type=str, default=None, metavar="0,-1",
                        help="(v4.2) sites_input 날짜 연도를 shift 해 추출 (YEAR_OFFSETS override). "
                             "예: '0,-1' = 올해+작년 동기간. offset≠0 은 파일명에 _y{연도} 태그")
    parser.add_argument("--site-workers", type=int, default=SITE_WORKERS, metavar="N",
                        help=f"site 단위 병렬 워커 수 (default {SITE_WORKERS}, 1=순차). "
                             f"동시 API 요청 ≈ N × --workers — 429 뜨면 줄이기")
    args = parser.parse_args()

    # ★ contents: --device 로 일부 device 만 추출 (생략 시 전체)
    if args.device:
        _want = set(args.device)
        globals()["DEVICE_SWAP_CASES"] = [c for c in DEVICE_SWAP_CASES if c["device"] in _want]
        print(f"[--device] {', '.join(args.device)} 만 추출 ({len(DEVICE_SWAP_CASES)}종)")

    # CLI 로 breakdown 설정 override (검증·성능 조절용)
    if args.breakdown_top_n is not None:
        globals()["BREAKDOWN_TOP_N"] = args.breakdown_top_n
    if args.breakdown_dims is not None:
        globals()["BREAKDOWN_DIMENSIONS"] = [d.strip() for d in args.breakdown_dims.split(",") if d.strip()]
    if args.breakdown_max_depth is not None:
        globals()["BREAKDOWN_MAX_DEPTH"] = args.breakdown_max_depth
    if args.no_parent_rows:
        globals()["INCLUDE_PARENT_ROWS"] = False
    # v4.2: 기간 분할 / 연도 shift override
    if args.monthly is not None:
        globals()["MONTHLY"] = args.monthly
    if args.year_offsets is not None:
        try:
            _offs = [int(v.strip()) for v in args.year_offsets.split(",") if v.strip()]
        except ValueError:
            raise SystemExit(f"❌ --year-offsets 형식 오류 (정수 콤마 나열): {args.year_offsets!r}")
        if not _offs:
            raise SystemExit("❌ --year-offsets 가 비었음 (예: --year-offsets 0,-1)")
        globals()["YEAR_OFFSETS"] = _offs
    # v3.7: breakdown limit 은 _run_breakdowns 가 module 전역을 참조 → CLI 값으로 갱신
    globals()["LIMIT_BD"] = args.limit_bd
    globals()["LIMIT_BD2"] = args.limit_bd2
    globals()["LIMIT_BD3"] = args.limit_bd3
    globals()["LIMIT_BD4"] = args.limit_bd4
    if args.estimate:
        globals()["ESTIMATE_ONLY"] = True

    ts = datetime.now().strftime("%y%m%d_%H%M")
    print(f"[{ts}] {Path(__file__).name}")
    print(f"  project       : {PROJECT_ID}")
    print(f"  input         : {SITES_INPUT_CSV.name}")
    print(f"  workers       : {args.workers}")
    print(f"  site workers  : {args.site_workers}  (1=순차)")
    print(f"  limit (lv1)   : {args.limit if args.limit > 0 else '무제한'}  (dim1 행 cap)")
    _bd_caps_disp = " / ".join('무제한' if v == 0 else str(v)
                               for v in (args.limit_bd, args.limit_bd2, args.limit_bd3, args.limit_bd4))
    print(f"  limit (bd1~4) : {_bd_caps_disp}  (breakdown 1~4단계 = level2~5, 부모 item 당 행 cap)")
    print(f"  EXTRA_SEGMENTS: {len(EXTRA_SEGMENTS)}건")
    # v3.8: device 케이스 + app_O_X
    app_ox = _load_app_ox(APP_OX_CSV)
    if DEVICE_CASES:
        _case_labels = ", ".join(c["device"] for c in DEVICE_CASES)
        print(f"  DEVICE_CASES  : {len(DEVICE_CASES)}건 ({_case_labels})  — task 수 = 패널×테이블×케이스")
        if app_ox is None:
            print(f"  app_O_X       : {APP_OX_CSV.name} 없음 → 전 site O 간주 (모든 케이스 추출)")
        else:
            _n_o = sum(1 for v in app_ox.values() if v == "O")
            _n_x = sum(1 for v in app_ox.values() if v == "X")
            print(f"  app_O_X       : {APP_OX_CSV.name}  O={_n_o} X={_n_x}  "
                  f"(X site 는 requires_app 케이스 제외, 미매칭 site 는 _old 제거→X 간주)")
    else:
        print(f"  DEVICE_CASES  : 0건 (add 방식 미사용 — contents 는 아래 swap 사용)")
    # ★ contents: device 세그 swap
    if DEVICE_SWAP_ENABLED and DEVICE_SWAP_CASES:
        if DEVICE_CASES:
            print("\n❌ DEVICE_CASES(add) 와 DEVICE_SWAP_CASES(replace) 를 동시에 켤 수 없습니다.")
            print("   둘 다 적용되면 device 세그가 중복으로 걸려 결과가 0 이 됩니다. 한쪽을 비우세요.")
            return 1
        _sw_labels = ", ".join(c["device"] for c in DEVICE_SWAP_CASES)
        print(f"  DEVICE_SWAP   : {len(DEVICE_SWAP_CASES)}종 ({_sw_labels})  — task 수 = 패널×테이블×device")
        print(f"                  base '{BASE_PC_NAME}' / '{BASE_EXCLUDED_APP_NAME}' 을 device 별로 치환")
        if DEVICE_SWAP_APPLY_APP_OX:
            print(f"  app_O_X       : 적용 — X site 는 app/android/ios 케이스 skip")
        else:
            print(f"  app_O_X       : 미적용 — 5종 전부 추출 (0 처리는 정제 단계, 원본값 보존)")
    _parent_disp = "포함" if INCLUDE_PARENT_ROWS else "제외(bd만)"
    if BREAKDOWN_ENABLED and BREAKDOWN_MAX_DEPTH != 0:
        _bd_dims = BREAKDOWN_DIMENSIONS or "(테이블 breakdowns 자동감지)"
        _bd_topn = "전체" if BREAKDOWN_TOP_N == 0 else f"레벨별 상위 {BREAKDOWN_TOP_N}"
        _bd_depth = "무제한" if (BREAKDOWN_MAX_DEPTH is None or BREAKDOWN_MAX_DEPTH < 0) else f"bd{BREAKDOWN_MAX_DEPTH}까지"
        print(f"  BREAKDOWN     : ON  dims={_bd_dims}  top_n={_bd_topn}  max_depth={_bd_depth}  parent_rows={_parent_disp}")
    else:
        _why = "MAX_DEPTH=0" if (BREAKDOWN_ENABLED and BREAKDOWN_MAX_DEPTH == 0) else "BREAKDOWN_ENABLED=False"
        print(f"  BREAKDOWN     : OFF (dim1 총계만, {_why})  parent_rows={_parent_disp}")
    # v4.2: 기간 분할 / 연도 shift
    print(f"  MONTHLY       : {'ON (총기간을 달력 월로 분할 — period 컬럼 추가)' if MONTHLY else 'OFF (총기간 1회)'}")
    print(f"  YEAR_OFFSETS  : {YEAR_OFFSETS}  ({'sites_input 그대로' if YEAR_OFFSETS == [0] else 'site 당 ' + str(len(YEAR_OFFSETS)) + ' run — offset≠0 은 파일명 _y{연도} 태그'})")

    # sites_input.csv 로드
    sites_rows = _load_sites_input(SITES_INPUT_CSV)
    if not sites_rows:
        print(f"\n❌ {SITES_INPUT_CSV} 에 site 정보 없음 (header 빼고 #-comment 외 데이터 라인 0)")
        print(f"   샘플 형식: site_code,start_date,end_date")
        return 1

    if args.site:
        site_filter = set(args.site)
        sites_rows = [r for r in sites_rows if r[0] in site_filter]
        if not sites_rows:
            print(f"\n❌ --site {args.site} 매칭되는 row 없음")
            return 1

    # v4.2: YEAR_OFFSETS 만큼 run 확장 — (site_code, start, end, file_tag, site_group)
    #   offset 0 → tag "" (v4.1 과 동일한 파일명). offset≠0 → "_y{shift 된 연도}"
    #   kind 가 PANEL_GROUP_YEAR_OFFSETS 에 등록돼 있으면 그 kind 는 전역 YEAR_OFFSETS 대신 그 목록을 쓴다
    #   (B2C=[0] — us 신 suite 에 2025 가 없어 offset -1 이 빈 run 이 되므로. us_old 행으로 따로 뽑는다)
    # 2026-07-31: 같은 site_code 가 group 별로 2행 이상이면 파일명에 _{group} 태그를 붙인다.
    #   (안 붙이면 offset 0 출력 경로가 같아져 나중 run 이 앞 run 을 덮어써 데이터가 유실된다.
    #    GROUP_TAG_IN_FILENAME 주석 참고.)  group 이 1개뿐인 site 는 기존 파일명 그대로.
    _grp_by_site: dict[str, set] = {}
    for _sc, _s, _e, _g in sites_rows:
        _grp_by_site.setdefault(_sc, set()).add(_g)
    _multi_group_sites = {sc for sc, gs in _grp_by_site.items() if len(gs) > 1 and any(gs)}

    runs: list[tuple[str, str, str, str, str]] = []
    kind_off_used: dict[str, list[int]] = {}
    for site_code, s_date, e_date, s_grp in sites_rows:
        offs = PANEL_GROUP_YEAR_OFFSETS.get(s_grp, YEAR_OFFSETS)
        kind_off_used[s_grp] = offs
        g_tag = (f"_{s_grp}" if GROUP_TAG_IN_FILENAME and site_code in _multi_group_sites
                 and s_grp else "")
        for off in offs:
            s2, e2 = _shift_year(s_date, off), _shift_year(e_date, off)
            y_tag = "" if off == 0 else f"_y{s2[:4]}"
            runs.append((site_code, s2, e2, f"{g_tag}{y_tag}", s_grp))

    _group_cnt: dict[str, int] = {}
    for r in sites_rows:
        _group_cnt[r[3]] = _group_cnt.get(r[3], 0) + 1
    print(f"  처리 site: {len(sites_rows)}개 → {[r[0] for r in sites_rows]}")
    print(f"  site kind: " + ", ".join(f"{k}={v}" for k, v in sorted(_group_cnt.items()))
          + "  (kind 별 패널만 추출 — " + ", ".join(f"{kw}→{kd}" for kw, kd in PANEL_GROUP_RULES) + ")")
    if _multi_group_sites:
        print(f"             ⚠ group 중복 site {sorted(_multi_group_sites)} → 파일명에 _{{group}} 태그 "
              f"(같은 경로 덮어쓰기 방지). 옛 이름 파일과 한 폴더에 섞지 말 것")
    elif GROUP_TAG_IN_FILENAME and len(_group_cnt) > 1:
        print(f"             · group 중복 site 없음 → 파일명 태그 미적용 (기존 이름 그대로)")
    for _k in sorted(_group_cnt):
        if _k in PANEL_GROUP_YEAR_OFFSETS:
            print(f"             ⚠ {_k} 는 YEAR_OFFSETS override = {PANEL_GROUP_YEAR_OFFSETS[_k]} "
                  f"(전역 {YEAR_OFFSETS} 미적용 — 다른 연도는 sites_input 에 행을 따로 넣을 것)")
    if YEAR_OFFSETS != [0]:
        _yrs = sorted({r[1][:4] for r in runs})
        print(f"  처리 run : {len(runs)}개 (site {len(sites_rows)} × 연도 {len(YEAR_OFFSETS)} → {_yrs})")
    print()

    # 인증 + project 한 번만
    headers, gcid = _load_auth_headers()
    # decompile_definition 안에서 datetime-interval-ref 만나면 dateranges API 호출 가능 → 인증 셋업
    _set_daterange_auth(headers, gcid)
    project = _fetch_project(headers, gcid, PROJECT_ID)
    panels = _list_panels(project)
    print(f"Project panels: {len(panels)}개")
    _prefetch_date_ranges(headers, gcid, panels)
    _prefetch_segment_names(headers, gcid, panels)

    # v3: EXTRA_SEGMENTS resolve (이름 → ID 1개씩 확정. 모호하면 SystemExit)
    resolved_extras: list[tuple[str, object]] = []
    for spec in EXTRA_SEGMENTS:
        if not spec.get("enabled", True):
            label = spec.get("segment_id") or spec.get("name_keywords") or "(?)"
            print(f"  [disabled] EXTRA_SEGMENTS skip: {label}")
            continue
        sid = _resolve_extra_segment(spec, headers, gcid, ts, panels=panels)
        if sid:
            resolved_extras.append((sid, spec.get("panel_scope", "all")))

    # v3.8: DEVICE_CASES 세그 ID 검증 + fresh name prefetch (_SEG_NAME_CACHE 적재)
    if DEVICE_CASES:
        print(f"\n[device cases] {len(DEVICE_CASES)}건 세그 name 조회:")
        for case in DEVICE_CASES:
            label = case.get("device", "")
            seg_ids = case.get("segment_ids") or []
            if not (label and seg_ids):
                raise SystemExit(f"❌ DEVICE_CASES 항목 형식 오류 (device/segment_ids 필수): {case}")
            names = []
            for sid in seg_ids:
                if not (isinstance(sid, str) and SEG_ID_RE.match(sid)):
                    raise SystemExit(f"❌ DEVICE_CASES '{label}' 의 segment_id 형식 오류: {sid!r}")
                nm = _fetch_segment_name(headers, gcid, sid)
                if not nm:
                    raise SystemExit(f"❌ DEVICE_CASES '{label}' 세그 name 조회 실패 (존재/권한 확인): {sid}")
                names.append(nm)
            print(f"  [{label}] " + " + ".join(f"'{n}'" for n in names))
        # site 별 치환 세그 name 도 prefetch (출력 CSV segments 컬럼용)
        for _site, _mapping in DEVICE_CASE_SITE_OVERRIDES.items():
            for _src, _dst in _mapping.items():
                for sid in (_src, _dst):
                    if not (isinstance(sid, str) and SEG_ID_RE.match(sid)):
                        raise SystemExit(f"❌ DEVICE_CASE_SITE_OVERRIDES '{_site}' 세그 ID 형식 오류: {sid!r}")
                nm = _fetch_segment_name(headers, gcid, _dst)
                if not nm:
                    raise SystemExit(f"❌ DEVICE_CASE_SITE_OVERRIDES '{_site}' 대체 세그 name 조회 실패: {_dst}")
                print(f"  [override:{_site}] '{_SEG_NAME_CACHE.get(_src) or _src}' → '{nm}'")

    # ★ contents: DEVICE_SWAP_CASES 세그 ID 검증 (오타/권한 문제를 추출 전에 잡는다)
    if DEVICE_SWAP_ENABLED and DEVICE_SWAP_CASES:
        print(f"\n[device swap] {len(DEVICE_SWAP_CASES)}종 세그 검증:")
        for sid, nm in ((BASE_PC_ID, BASE_PC_NAME), (BASE_EXCLUDED_APP_ID, BASE_EXCLUDED_APP_NAME)):
            if not (isinstance(sid, str) and SEG_ID_RE.match(sid)):
                raise SystemExit(f"❌ base 세그 ID 형식 오류: {sid!r}")
            real = _fetch_segment_name(headers, gcid, sid)
            if not real:
                raise SystemExit(f"❌ base 세그 name 조회 실패 (존재/권한 확인): {sid}")
            if real.strip().lower() != nm.strip().lower():
                print(f"  ⚠ base 세그명 불일치: 상수 '{nm}' vs 실제 '{real}' "
                      f"→ 상수를 실제 이름으로 맞추세요 (segments 컬럼 치환이 어긋납니다)")
            else:
                print(f"  [base] '{real}'")
        for case in DEVICE_SWAP_CASES:
            label = case.get("device", "")
            rep = case.get("replace")
            if not label or rep is None:
                raise SystemExit(f"❌ DEVICE_SWAP_CASES 항목 형식 오류 (device/replace 필수): {case}")
            if not rep:
                print(f"  [{label}] 치환 없음 (base 그대로)")
                continue
            parts = []
            for old_id, pair in rep.items():
                if not (isinstance(pair, (tuple, list)) and len(pair) == 2):
                    raise SystemExit(f"❌ DEVICE_SWAP_CASES '{label}' replace 값은 (name, id) 여야 합니다: {pair!r}")
                new_name, new_id = pair
                for sid in (old_id, new_id):
                    if not (isinstance(sid, str) and SEG_ID_RE.match(sid)):
                        raise SystemExit(f"❌ DEVICE_SWAP_CASES '{label}' 세그 ID 형식 오류: {sid!r}")
                real = _fetch_segment_name(headers, gcid, new_id)
                if not real:
                    raise SystemExit(f"❌ DEVICE_SWAP_CASES '{label}' 세그 name 조회 실패: {new_id}")
                # 세그 ID 는 맞는데 AA 상의 이름 표기만 다른 경우 — 오류 아님.
                # 출력 CSV segments 컬럼에는 **상수 이름**을 쓴다 (구버전 산출물과 표기를 맞추기 위함).
                # 표기를 AA 실제 이름으로 바꾸고 싶으면 위 _SEG 의 name 을 고치면 된다.
                if real.strip().lower() != str(new_name).strip().lower():
                    print(f"     ↪ 표기 참고: AA 실제명 '{real}' / CSV 표기 '{new_name}' (의도된 표기 유지)")
                parts.append(f"'{_SEG_NAME_CACHE.get(old_id) or old_id}' → '{new_name}'")
            print(f"  [{label}] " + " + ".join(parts))

    # EXTRA(추가) vs SKIP_PANEL_SEGMENT_KEYWORDS(제거) 충돌 검사 (v3.4)
    #   같은 세그를 EXTRA로 추가 + SKIP로 제거하면 모순 → 경고 후 중단
    if SKIP_PANEL_SEGMENT_KEYWORDS and resolved_extras:
        _skw = [k.lower() for k in SKIP_PANEL_SEGMENT_KEYWORDS]
        for _sid, _ in resolved_extras:
            _nm = _SEG_NAME_CACHE.get(_sid) or _fetch_segment_name(headers, gcid, _sid) or ""
            if _nm and all(k in _nm.lower() for k in _skw):
                raise SystemExit(
                    f"❌ 충돌: EXTRA_SEGMENTS 로 추가하는 세그 '{_nm}' ({_sid}) 가 "
                    f"SKIP_PANEL_SEGMENT_KEYWORDS {SKIP_PANEL_SEGMENT_KEYWORDS} 제거 대상과 동일 — "
                    f"추가(EXTRA)와 제거(SKIP)가 모순입니다. 둘 중 하나를 빼고 다시 실행하세요."
                )

    # 사이트별 처리 — v3.6: SITE_WORKERS>1 이면 site 단위 병렬 (_contents 시리즈 포팅)
    def _run_one(item):
        site_code, start_date, end_date, file_tag, site_group = item   # v4.2: file_tag / site_group
        site_info = lookup_site(site_code)
        _t0 = datetime.now()
        res = _process_site(headers, gcid, project, panels,
                            site_info, start_date, end_date,
                            workers=args.workers, limit=args.limit,
                            dry_run=args.dry_run, ts=ts,
                            include_global_for_us=args.include_global_for_us,
                            resolved_extras=resolved_extras,
                            app_ox=app_ox,
                            file_tag=file_tag,
                            site_group=site_group)
        res["file_tag"] = file_tag
        res["elapsed_sec"] = (datetime.now() - _t0).total_seconds()
        return res

    n_sites = len(runs)   # v4.2: site × 연도(YEAR_OFFSETS) run 수
    prog = {"done": 0, "elapsed_sum": 0.0}
    run_start = datetime.now()

    def _report(res):
        if not VERBOSE_PROGRESS:
            return
        prog["done"] += 1
        el = res.get("elapsed_sec", 0.0)
        prog["elapsed_sum"] += el
        rows = sum(len(t.get("rows") or []) + len(t.get("breakdown_rows") or [])
                   for t in res["tasks"] if t.get("ok"))
        left = n_sites - prog["done"]
        avg = prog["elapsed_sum"] / prog["done"]
        eta = avg * left / max(1, args.site_workers) if args.site_workers > 1 else avg * left
        wall = (datetime.now() - run_start).total_seconds()
        total = wall + eta
        _site_disp = f"{res['site'].site_code}{res.get('file_tag', '')}"
        print(f"  [{prog['done']:2}/{n_sites}] site={_site_disp:<10} "
              f"✓ {el:6.1f}s  rows {rows:>8,}  | "
              f"누적 {_fmt_dur(wall)} | 평균 {avg:.1f}s/site | "
              f"남은 ~{_fmt_dur(eta)} | 전체 ~{_fmt_dur(total)} ({left} left)")

    results = []
    if args.site_workers <= 1:
        for item in runs:
            res = _run_one(item)
            results.append(res)
            _report(res)
    else:
        print(f"  [site-parallel] {args.site_workers} sites 동시 처리 "
              f"(총 동시 API 요청 ≈ {args.site_workers * args.workers})")
        with ThreadPoolExecutor(max_workers=args.site_workers) as ex:
            futures = {ex.submit(_run_one, item): f"{item[0]}{item[3]}" for item in runs}
            for fut in as_completed(futures):
                sc = futures[fut]
                try:
                    res = fut.result()
                    results.append(res)
                    _report(res)
                except Exception as e:
                    print(f"  [site {sc}] ERROR: {e}")

    # 전체 요약
    print(f"\n{'═'*78}\n[전체 summary]\n{'═'*78}")
    total_ok = sum(r["n_ok"] for r in results)
    total_fail = sum(r["n_fail"] for r in results)
    print(f"  처리 run  : {len(results)}" + (f"  (site {len(sites_rows)} × 연도 {len(YEAR_OFFSETS)})"
                                             if YEAR_OFFSETS != [0] else ""))
    print(f"  성공 task : {total_ok}")
    print(f"  실패 task : {total_fail}")
    if resolved_extras:
        print(f"  extra seg : {len(resolved_extras)}건 적용")
    print(f"\n사이트별:")
    for r in results:
        s = r["site"]
        print(f"  {s.site_code + r.get('file_tag', ''):16}  ({s.rsid:35})  "
              f"성공={r['n_ok']:3}  실패={r['n_fail']:3}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
