# segment_lookup.py
# 2026-05-15  Jonghyun Park w/ Claude
# updated: 2026-05-15 13:00  — owner_name을 aa_user_id CSV에서 보강
"""
세그먼트 ID 리스트 → 기본 정보 CSV + DSL 구조 파일(.dsl) 출력.

.dsl 파일은 aa_create_segment_v2.py의 입력으로 바로 재사용 가능.

사용법:
  python segment_lookup.py s200001591_abc123 s200001591_def456
  python segment_lookup.py --from-file segment_ids.txt
  python segment_lookup.py --search "campaign" --rsid rsid_placeholder
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import aanalytics2 as api2

# ════════════════════════════════════════════════════════════════════
# 사용자가 바꿔야 하는 부분
# ════════════════════════════════════════════════════════════════════

AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
# Mac: AUTH_JSON_PATH = str(Path(__file__).resolve().parent.parent / "aa_auth.json")
COMPANY_ID = "company_id"

# ════════════════════════════════════════════════════════════════════
# 내부 사용
# ════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_PREFIX = "segment_lookup_"

# ─── AA user 매핑 CSV (owner_name 보강용) ─────────────────────────
# 상위 폴더의 aa_user_id_*.csv 자동 탐색. 없으면 owner_name 빈값 그대로.
AA_USER_CSV_DIR = Path(__file__).resolve().parent.parent

# ─── 변수 단축어 (decompile용) ────────────────────────────────────
VARIABLE_ALIASES: dict[str, str] = {
    "page": "variables/page",
    "sitesection": "variables/sitesection",
    "country": "variables/country",
    "mktchannel": "variables/marketingchannel",
    "marketingchannel": "variables/marketingchannel",
    "referrer": "variables/referrer",
    "devicetype": "variables/devicetype",
    "revenue": "metrics/revenue",
    "orders": "metrics/orders",
    "visits": "metrics/visits",
    "visitors": "metrics/visitors",
    "pageviews": "metrics/pageviews",
}

# 연산자 역매핑
FUNC_TO_DSL: dict[str, str] = {
    "streq": "=",
    "contains": "contains",
    "contains-any-of": "contains-any-of",
    "contains-all-of": "contains-all-of",
    "streq-in": "in",
    "starts-with": "starts-with",
    "ends-with": "ends-with",
    "matches-regex": "matches",
    "exists": "exists",
    "gt": ">",
    "ge": ">=",
    "lt": "<",
    "le": "<=",
}

CONTEXT_TO_SCOPE = {"hits": "hit", "visits": "visit", "visitors": "visitor"}


# ═══════════════════════════════════════════════════════════════════
# 변수 역매핑
# ═══════════════════════════════════════════════════════════════════

def _reverse_variable(full_name: str) -> str:
    rev = {v: k for k, v in VARIABLE_ALIASES.items()}
    if full_name in rev:
        return rev[full_name]
    m = re.match(r"^variables/evar(\d+)$", full_name)
    if m:
        return f"evar{m.group(1)}"
    m = re.match(r"^variables/prop(\d+)$", full_name)
    if m:
        return f"prop{m.group(1)}"
    m = re.match(r"^metrics/event(\d+)$", full_name)
    if m:
        return f"event{m.group(1)}"
    if full_name.startswith("variables/"):
        return full_name[len("variables/"):]
    if full_name.startswith("metrics/"):
        return full_name[len("metrics/"):]
    return full_name


# ═══════════════════════════════════════════════════════════════════
# Decompiler (AA JSON → DSL)
# ═══════════════════════════════════════════════════════════════════

def _decompile_pred(pred: dict, indent: int, parent_context: str) -> list[str]:
    func = pred.get("func", "")
    pad = "  " * indent

    if func == "without":
        inner = pred.get("pred", {})
        inner_func = inner.get("func", "")
        # 복합 조건 (and/or/sequence 등) → NOT ( ... ) 블록
        if inner_func in ("and", "or", "sequence", "sequence-prefix", "sequence-suffix", "container"):
            inner_lines = _decompile_pred(inner, indent + 1, parent_context)
            return [f"{pad}NOT ("] + inner_lines + [f"{pad})"]
        # 단순 leaf → NOT 접두사 한 줄
        inner_lines = _decompile_leaf(inner, parent_context)
        if inner_lines:
            return [f"{pad}NOT {inner_lines[0].strip()}"]
        return [f"{pad}NOT ..."]

    if func in ("and", "or"):
        preds = pred.get("preds", [])
        lines: list[str] = []
        for i, p in enumerate(preds):
            child_lines = _decompile_pred(p, indent, parent_context)
            if i > 0:
                lines.append(f"{pad}{func.upper()}")
                lines.extend(child_lines)
            else:
                lines.extend(child_lines)
        return lines

    if func == "container":
        ctx = pred.get("context", parent_context)
        desc = pred.get("description", "")
        inner_pred = pred.get("pred", {})
        if ctx == parent_context and not desc:
            return _decompile_pred(inner_pred, indent, ctx)
        scope = CONTEXT_TO_SCOPE.get(ctx, ctx)
        prefix = f"'{desc}'!" if desc else ""
        inner_lines = _decompile_pred(inner_pred, indent + 1, ctx)
        lines = [f"{pad}{prefix}{scope}("]
        lines.extend(inner_lines)
        lines.append(f"{pad})")
        return lines

    # sequence (then 로직)
    if func in ("sequence", "sequence-prefix", "sequence-suffix"):
        stream = pred.get("stream", pred.get("preds", []))
        lines: list[str] = []
        for i, step in enumerate(stream):
            step_lines = _decompile_pred(step, indent, parent_context)
            if i > 0:
                lines.append(f"{pad}THEN")
            lines.extend(step_lines)
        return lines

    # segment-ref
    if func == "segment-ref":
        seg_id = pred.get("segmentId", "?")
        return [f"{pad}@{seg_id}"]

    leaf = _decompile_leaf(pred, parent_context)
    return [f"{pad}{l}" for l in leaf]


def _decompile_leaf(pred: dict, parent_context: str) -> list[str]:
    func = pred.get("func", "")
    negated = False
    if func == "without":
        negated = True
        pred = pred.get("pred", {})
        func = pred.get("func", "")

    # segment-ref
    if func == "segment-ref":
        seg_id = pred.get("segmentId", "?")
        prefix = "NOT " if negated else ""
        return [f"{prefix}@{seg_id}"]

    val = pred.get("val") or pred.get("evt") or {}
    var_name = val.get("name", "")
    short_var = _reverse_variable(var_name) if var_name else "?"

    dsl_op = FUNC_TO_DSL.get(func, func)

    if "list" in pred:
        val_str = json.dumps(pred["list"], ensure_ascii=False)
    elif "str" in pred:
        val_str = f'"{pred["str"]}"'
    elif "regex" in pred:
        val_str = f'"{pred["regex"]}"'
    elif "num" in pred:
        val_str = str(pred["num"])
    elif func == "exists":
        val_str = ""
    else:
        val_str = ""

    prefix = "NOT " if negated else ""
    condition = f"{prefix}{short_var} {dsl_op}"
    if val_str:
        condition += f" {val_str}"
    return [condition]


def decompile_definition(definition: dict) -> str:
    container = definition.get("container", {})
    ctx = container.get("context", "hits")
    desc = container.get("description", "")
    pred = container.get("pred", {})

    scope = CONTEXT_TO_SCOPE.get(ctx, ctx)
    prefix = f"'{desc}'!" if desc else ""

    inner_lines = _decompile_pred(pred, 1, ctx)

    lines = [f"{prefix}{scope}("]
    lines.extend(inner_lines)
    lines.append(")")
    return "\n".join(lines)


def format_dsl_block(name: str, description: str, rsid: str,
                     tags: list[str], definition: dict) -> str:
    parts = ["--- segment"]
    parts.append(f"name: {name}")
    if description:
        parts.append(f"description: {description}")
    parts.append(f"rsid: {rsid}")
    if tags:
        parts.append(f"tags: [{', '.join(tags)}]")
    parts.append("")
    parts.append(decompile_definition(definition))
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# AA user CSV → loginId → name 매핑 (owner_name 보강)
# ═══════════════════════════════════════════════════════════════════

def _load_user_map() -> dict[str, str]:
    """aa_user_id_*.csv → {loginId(str): fullName} dict. 없으면 빈 dict."""
    import glob
    pattern = str(AA_USER_CSV_DIR / "aa_user_id_*.csv")
    files = sorted(glob.glob(pattern), reverse=True)
    if not files:
        return {}
    user_map: dict[str, str] = {}
    with open(files[0], "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lid = row.get("loginId", "").strip()
            name = row.get("fullName", "").strip()
            if lid and name:
                user_map[lid] = name
    return user_map


def _enrich_owner_name(results: list[dict], user_map: dict[str, str]) -> None:
    """owner_name이 비어있으면 user_map에서 보강."""
    for r in results:
        if not r["owner_name"] and r["owner_id"]:
            r["owner_name"] = user_map.get(str(r["owner_id"]), "")


# ═══════════════════════════════════════════════════════════════════
# 인증
# ═══════════════════════════════════════════════════════════════════

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
        raise RuntimeError(
            f"필수 헤더 누락: api_key={bool(api_key)}, "
            f"auth={bool(auth)}, gcid={bool(gcid)}"
        )
    return {
        "x-api-key": api_key,
        "Authorization": auth,
        "x-proxy-global-company-id": gcid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, gcid


# ═══════════════════════════════════════════════════════════════════
# 세그먼트 조회
# ═══════════════════════════════════════════════════════════════════

def _lookup_segment(headers: dict, gcid: str, seg_id: str) -> dict:
    """GET /segments/{id} → 세그먼트 상세."""
    url = f"https://analytics.adobe.io/api/{gcid}/segments/{seg_id}"
    r = requests.get(
        url, headers=headers,
        params={"expansion": "definition,name,description,owner,tags,reportSuiteName"},
        timeout=60,
    )
    if r.status_code != 200:
        return {
            "segment_id": seg_id,
            "name": "",
            "owner_id": "",
            "owner_name": "",
            "rsid": "",
            "description": "",
            "tags": "",
            "definition": None,
            "error": f"{r.status_code} {r.reason}: {r.text[:200]}",
        }

    data = r.json()
    owner = data.get("owner", {})
    tag_names = ", ".join(
        t.get("name", "") for t in data.get("tags", [])
    ) if data.get("tags") else ""

    return {
        "segment_id": seg_id,
        "name": data.get("name", ""),
        "owner_id": owner.get("id", "") if isinstance(owner, dict) else "",
        "owner_name": owner.get("name", "") if isinstance(owner, dict) else "",
        "rsid": data.get("rsid", ""),
        "description": data.get("description", ""),
        "tags": tag_names,
        "definition": data.get("definition"),
        "error": "",
    }


def _search_segments(headers: dict, gcid: str, keyword: str,
                     rsid: str = "", limit: int = 50) -> list[dict]:
    """GET /segments — 이름 키워드로 검색. 결과를 _lookup_segment 포맷으로 반환."""
    url = f"https://analytics.adobe.io/api/{gcid}/segments"
    params: dict[str, Any] = {
        "expansion": "definition,name,description,owner,tags,reportSuiteName",
        "name": keyword,
        "limit": limit,
        "includeType": "all",
    }
    if rsid:
        params["rsids"] = rsid

    r = requests.get(url, headers=headers, params=params, timeout=60)
    if r.status_code != 200:
        print(f"ERROR: 검색 실패 — {r.status_code} {r.reason}: {r.text[:200]}")
        return []

    data = r.json()
    items = data.get("content", [])
    results: list[dict] = []
    for item in items:
        owner = item.get("owner", {})
        tag_names = ", ".join(
            t.get("name", "") for t in item.get("tags", [])
        ) if item.get("tags") else ""
        results.append({
            "segment_id": item.get("id", ""),
            "name": item.get("name", ""),
            "owner_id": owner.get("id", "") if isinstance(owner, dict) else "",
            "owner_name": owner.get("name", "") if isinstance(owner, dict) else "",
            "rsid": item.get("rsid", ""),
            "description": item.get("description", ""),
            "tags": tag_names,
            "definition": item.get("definition"),
            "error": "",
        })
    return results


# ═══════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="세그먼트 ID 조회 또는 이름 키워드 검색 → CSV + DSL 구조 파일 출력"
    )
    parser.add_argument("ids", nargs="*", help="segment ID(s)")
    parser.add_argument("--from-file", help="segment ID 목록 파일 (한 줄에 하나)")
    parser.add_argument("--search", help="세그먼트 이름 키워드 검색")
    parser.add_argument("--rsid", default="", help="검색 시 RSID 필터 (선택)")
    parser.add_argument("--limit", type=int, default=50, help="검색 결과 최대 건수 (기본 50)")
    args = parser.parse_args()

    # ID 수집
    seg_ids: list[str] = list(args.ids)
    if args.from_file:
        fp = Path(args.from_file)
        if not fp.exists():
            print(f"ERROR: 파일 없음: {fp}")
            return 1
        for line in fp.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                seg_ids.append(stripped)

    search_mode = bool(args.search)

    if not seg_ids and not search_mode:
        print("ERROR: 세그먼트 ID 또는 --search 키워드가 필요합니다.")
        print("  python segment_lookup.py s200001591_abc123")
        print("  python segment_lookup.py --from-file ids.txt")
        print("  python segment_lookup.py --search \"campaign\" --rsid rsid_placeholder")
        return 1

    now = datetime.now()
    timestamp = now.strftime("%y%m%d_%H%M")
    requested_at = now.strftime("%Y-%m-%d %H:%M:%S")

    if search_mode:
        print(f"[{requested_at}] segment search — \"{args.search}\"")
        if args.rsid:
            print(f"  RSID filter: {args.rsid}")
    else:
        print(f"[{requested_at}] segment lookup — {len(seg_ids)}개 조회")
    print(f"  Company: {COMPANY_ID}")
    print()

    # 인증
    print("Authenticating ...")
    headers, gcid = _load_auth_headers()
    print()

    # 조회
    results: list[dict] = []
    if search_mode:
        results = _search_segments(headers, gcid, args.search,
                                   rsid=args.rsid, limit=args.limit)
        print(f"  검색 결과: {len(results)}건")
        for r in results:
            print(f"    {r['segment_id']}  {r['name']}")
    else:
        for i, seg_id in enumerate(seg_ids):
            print(f"  [{i+1}/{len(seg_ids)}] {seg_id} ...", end=" ")
            info = _lookup_segment(headers, gcid, seg_id)
            results.append(info)
            if info["error"]:
                print(f"FAIL — {info['error'][:60]}")
            else:
                print(f"OK — {info['name']}")

    print()

    # owner_name 보강 (AA API가 owner.name을 빈값으로 반환하는 경우)
    user_map = _load_user_map()
    if user_map:
        _enrich_owner_name(results, user_map)
        print(f"  owner_name 보강: aa_user_id CSV ({len(user_map)}명)")
    print()

    # CSV 출력
    csv_path = OUTPUT_DIR / f"{RESULT_PREFIX}{timestamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["segment_id", "name", "owner_id", "owner_name", "rsid",
                     "description", "tags", "structure", "error"])
        for r in results:
            # structure: decompiled DSL을 한 줄로
            structure = ""
            if r["definition"]:
                try:
                    dsl_text = decompile_definition(r["definition"])
                    # CSV 안전: 쌍따옴표→작은따옴표, 줄바꿈→ " | "
                    structure = dsl_text.replace('"', "'").replace("\n", " | ")
                except Exception:
                    structure = "(decompile error)"
            w.writerow([
                r["segment_id"], r["name"], r["owner_id"], r["owner_name"],
                r["rsid"], r["description"], r["tags"], structure, r["error"],
            ])
    print(f"CSV: {csv_path}")

    # DSL 출력
    dsl_path = OUTPUT_DIR / f"{RESULT_PREFIX}{timestamp}.dsl"
    dsl_blocks: list[str] = []
    for r in results:
        if r["definition"] is None:
            continue
        try:
            tag_list = [t.strip() for t in r["tags"].split(",") if t.strip()] if r["tags"] else []
            block = format_dsl_block(
                name=r["name"],
                description=r["description"],
                rsid=r["rsid"],
                tags=tag_list,
                definition=r["definition"],
            )
            dsl_blocks.append(block)
        except Exception as e:
            print(f"  WARN: {r['segment_id']} decompile 실패 — {e}")

    if dsl_blocks:
        dsl_path.write_text("\n\n".join(dsl_blocks) + "\n", encoding="utf-8")
        print(f"DSL 구조: {dsl_path}")
        print(f"  → aa_create_segment_v2.py --input {dsl_path.name} 으로 재사용 가능")
    else:
        print("DSL 구조: (유효한 definition 없음, 파일 미생성)")

    # 콘솔 요약
    ok = sum(1 for r in results if not r["error"])
    fail = sum(1 for r in results if r["error"])
    print(f"\n[summary] 성공: {ok}, 실패: {fail}")

    # 성공한 것들 콘솔 출력
    for r in results:
        if r["error"]:
            continue
        print(f"\n{'─' * 50}")
        print(f"  ID: {r['segment_id']}")
        print(f"  Name: {r['name']}")
        print(f"  Owner: {r['owner_name']} ({r['owner_id']})")
        print(f"  RSID: {r['rsid']}")
        if r["tags"]:
            print(f"  Tags: {r['tags']}")
        if r["definition"]:
            print(f"  구조:")
            dsl = decompile_definition(r["definition"])
            for line in dsl.splitlines():
                print(f"    {line}")

    return 0


if __name__ == "__main__":
    sys.exit(main())