# _probe_segment.py
# 2026-05-04  Jonghyun Park w/ Claude
"""
MST Global RSID에 이미 있는 segment 1개를 GET해서 definition 구조 확인.
test_create_segment.py의 SEGMENT_DEFINITION을 어떤 형태로 보내야 하는지 reference.
"""
from __future__ import annotations

import json
import sys

import requests
import aanalytics2 as api2

# Adobe Analytics OAuth S2S auth json — 각자 환경에 맞게 변경
AUTH_JSON_PATH = r"C:\Users\user_name\path\to\auth.json"
COMPANY_ID = "company_id"
RSID = "rsid_placeholder"


def main() -> int:
    api2.importConfigFile(AUTH_JSON_PATH)
    api2.Login()
    ags = api2.Analytics(COMPANY_ID)
    h = dict(ags.header) if isinstance(getattr(ags, "header", None), dict) else {}
    h_lower = {k.lower(): v for k, v in h.items()}
    headers = {
        "x-api-key": h_lower["x-api-key"],
        "Authorization": h_lower["authorization"],
        "x-proxy-global-company-id": h_lower["x-proxy-global-company-id"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    gcid = h_lower["x-proxy-global-company-id"]

    # MST Global RSID에 1건만 가져오고 definition까지 확장
    url = f"https://analytics.adobe.io/api/{gcid}/segments"
    r = requests.get(
        url,
        headers=headers,
        params={"limit": 3, "expansion": "definition,name,description,owner,tags"},
        timeout=60,
    )
    print(f"GET status: {r.status_code}")
    if r.status_code != 200:
        print(r.text[:500])
        return 1

    body = r.json()
    items = body.get("content", body) if isinstance(body, dict) else body
    print(f"\n총 {len(items)}건 sample:")
    for i, s in enumerate(items, 1):
        print(f"\n──[{i}] id={s.get('id')}  name={s.get('name')}")
        print(f"   rsid={s.get('rsid')}  owner={s.get('owner')}")
        print(f"   definition:")
        print(json.dumps(s.get("definition"), ensure_ascii=False, indent=4))
    return 0


if __name__ == "__main__":
    sys.exit(main())
