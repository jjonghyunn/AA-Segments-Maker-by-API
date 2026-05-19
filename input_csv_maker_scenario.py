# input_csv_maker_scenario.py
# 2026-05-18  Jonghyun Park w/ Claude
# Scenario 통합본 — 한 raw csv 에 global / US row 가 섞여 있을 때 자동 분류 + 각 builder 호출 + 결과 통합.
"""
seg_make_ref_scenario_*.csv → row 별 Segment Name 에 'US_CC' 또는 'US_' 포함 여부로 자동 분류:
  · US row  → input_csv_maker_us.py (US 패턴: evar96instances + starts-with + 큰따옴표 + rsid_placeholder RSID + ATC=[US] + cache=*_us)
  · Global row → input_csv_maker.py    (Global 패턴: evar26 contains + 작은따옴표 + rsid_placeholder RSID + ATC=[Global] + cache=*_global)

분리된 임시 csv (`seg_make_ref_<ts>_global_tmp.csv`, `seg_make_ref_us_<ts>_us_tmp.csv`) 만들고 각 builder 를
subprocess 로 호출 → 결과 segments_input_<ts>.csv / .dsl / _WARN.csv 각각 받아 → 통합본 한 csv 로 merge.

사용:
  python input_csv_maker_scenario.py
"""
from __future__ import annotations

import csv
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

SEG_MAKE_REF_CSV = ""   # 빈 값이면 폴더 내 seg_make_ref_scenario_*.csv 사전순 최신 1 개 자동 pick

# 출력 통합본 파일명
OUTPUT_PREFIX = "segments_input"   # _scenario suffix 자동 붙음

# US 분류 키워드 — Segment Name 에 이것 매칭되면 US row 로 처리 (case-insensitive)
US_NAME_PATTERNS = ["US_CC", "US_"]

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent


def is_us_row(name: str) -> bool:
    name = name or ""
    return any(p.lower() in name.lower() for p in US_NAME_PATTERNS)


def _pick_latest(pattern: str) -> Path | None:
    cands = sorted(OUTPUT_DIR.glob(pattern), reverse=True)
    return cands[0] if cands else None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 1) raw csv pick
    if SEG_MAKE_REF_CSV.strip():
        raw_path = OUTPUT_DIR / SEG_MAKE_REF_CSV.strip()
        if not raw_path.exists():
            print(f"ERROR: SEG_MAKE_REF_CSV 못 찾음 — {raw_path}")
            return 1
    else:
        raw_path = _pick_latest("seg_make_ref_scenario_*.csv")
        if raw_path is None:
            print(f"ERROR: seg_make_ref_scenario_*.csv 못 찾음 — {OUTPUT_DIR}")
            return 1
        print(f"[scenario auto-latest] {raw_path.name}")

    # 2) row 분류
    with open(raw_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        global_rows: list[dict] = []
        us_rows: list[dict] = []
        for r in reader:
            (us_rows if is_us_row(r.get("Segment Name", "")) else global_rows).append(r)

    ts = datetime.now().strftime("%y%m%d_%H%M")
    tmp_global = OUTPUT_DIR / f"seg_make_ref_{ts}_global_tmp.csv"
    tmp_us = OUTPUT_DIR / f"seg_make_ref_us_{ts}_us_tmp.csv"

    if global_rows:
        with open(tmp_global, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(global_rows)
    if us_rows:
        with open(tmp_us, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(us_rows)

    print(f"  raw {raw_path.name} → global {len(global_rows)} rows + US {len(us_rows)} rows")
    print()

    # 3) 각 builder subprocess 호출 — --output-ts 로 명시적으로 다른 파일명 박음
    #    (같은 분 내 호출 시 maker 들이 같은 datetime.now() ts 로 결과 파일 덮어쓰는 버그 방지)
    out_global_csv = None
    out_us_csv = None
    if global_rows:
        global_ts = f"{ts}_global"
        print(f"[1/2] input_csv_maker.py — global rows")
        rc = subprocess.run(
            [sys.executable, str(OUTPUT_DIR / "input_csv_maker.py"),
             "--input", tmp_global.name, "--output-ts", global_ts],
            cwd=str(OUTPUT_DIR),
        ).returncode
        if rc != 0:
            print(f"  ⚠️ input_csv_maker.py rc={rc}")
        out_global_csv = OUTPUT_DIR / f"segments_input_{global_ts}.csv"
        print()
    if us_rows:
        us_ts = f"{ts}_us"
        print(f"[2/2] input_csv_maker_us.py — US rows")
        rc = subprocess.run(
            [sys.executable, str(OUTPUT_DIR / "input_csv_maker_us.py"),
             "--input", tmp_us.name, "--output-ts", us_ts],
            cwd=str(OUTPUT_DIR),
        ).returncode
        if rc != 0:
            print(f"  ⚠️ input_csv_maker_us.py rc={rc}")
        out_us_csv = OUTPUT_DIR / f"segments_input_{us_ts}.csv"
        print()

    # 4) 결과 통합 — segments_input_<ts>_scenario.csv / .dsl
    out_csv = OUTPUT_DIR / f"{OUTPUT_PREFIX}_{ts}_scenario.csv"
    out_dsl = OUTPUT_DIR / f"{OUTPUT_PREFIX}_{ts}_scenario.dsl"

    merged_rows: list[dict] = []
    merged_fieldnames: list[str] = []
    for src in (out_global_csv, out_us_csv):
        if src is None or not src.exists():
            continue
        with open(src, encoding="utf-8-sig") as f:
            r = csv.DictReader(f)
            if not merged_fieldnames and r.fieldnames:
                merged_fieldnames = list(r.fieldnames)
            for row in r:
                merged_rows.append(row)
    if merged_rows and merged_fieldnames:
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=merged_fieldnames)
            w.writeheader()
            w.writerows(merged_rows)
        print(f"[scenario merge] {len(merged_rows)} rows → {out_csv.name}")

    # dsl 도 두 개 concat
    dsl_blocks: list[str] = []
    for src in (out_global_csv, out_us_csv):
        if src is None:
            continue
        dsl_src = src.with_suffix(".dsl")
        if dsl_src.exists():
            with open(dsl_src, encoding="utf-8") as f:
                dsl_blocks.append(f.read())
    if dsl_blocks:
        with open(out_dsl, "w", encoding="utf-8") as f:
            f.write("\n\n".join(dsl_blocks))
        print(f"[scenario merge] dsl → {out_dsl.name}")

    # 5) WARN.csv 통합본 — global/us WARN 합쳐서 scenario_WARN.csv 로 (있을 때만)
    out_warn = OUTPUT_DIR / f"{OUTPUT_PREFIX}_{ts}_scenario_WARN.csv"
    merged_warn_rows: list[dict] = []
    merged_warn_fields: list[str] = []
    for src in (out_global_csv, out_us_csv):
        if src is None:
            continue
        warn_src = src.with_name(src.stem + "_WARN.csv")
        if not warn_src.exists():
            continue
        with open(warn_src, encoding="utf-8-sig") as f:
            r = csv.DictReader(f)
            if not merged_warn_fields and r.fieldnames:
                merged_warn_fields = list(r.fieldnames)
            for row in r:
                merged_warn_rows.append(row)
    if merged_warn_rows and merged_warn_fields:
        with open(out_warn, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=merged_warn_fields)
            w.writeheader()
            w.writerows(merged_warn_rows)
        print(f"[scenario merge] WARN → {out_warn.name}  ({len(merged_warn_rows)} 행)")

    # 6) 임시 + 부수 파일 정리 — global/us 별도 결과 (csv/dsl/WARN) 모두 삭제, scenario 통합본만 남김
    to_cleanup: list[Path] = [tmp_global, tmp_us]
    for src in (out_global_csv, out_us_csv):
        if src is None:
            continue
        to_cleanup.append(src)
        to_cleanup.append(src.with_suffix(".dsl"))
        to_cleanup.append(src.with_name(src.stem + "_WARN.csv"))
    for p in to_cleanup:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    print()
    print(f"다음 단계 — v2.2:")
    print(f"  python aa_create_segment_v2_2.py --input {out_csv.name} --update-or-create --lookup-by-name --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
