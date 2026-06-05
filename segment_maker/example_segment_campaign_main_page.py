# example_segment_campaign_main_page.py
# 2026-05-04  Jonghyun Park w/ Claude
"""
실제 어도비에 만들어져있는 segment 1개 예시 — 그대로 가져와 SEGMENT_DEFINITION에 복붙 가능.

원본:
  Name : temp.[25 MD] ALL SITES_Campaign Main Page_Prop (user_id)
  ID   : 세그먼트_아이디_넘버
  RSID : sscompany_name4mstglobal (MST Global)
  URL  : https://experience.adobe.com/#/@company_name/so:your_aa_company_id/analytics/spa/#/components/segments/edit/세그먼트_아이디_넘버

조건 의미:
  Hit 단위에서, 다음 두 조건 중 하나(OR)에 해당하는 hit:
    [A] Page에 "campaign_name" 포함 AND Page에 [whatsapp, explore, crm, ...] 중 어느 것도 포함 안 함
    [B] prop29(URL without Parameter)에 "campaign_name" 포함 AND prop29에 같은 제외 리스트 어느 것도 포함 안 함

배운 것 (이 segment에서):
  · `contains-any-of` — UI "contains any of"의 API func (-of suffix 있음, -any 아님)
  · `without`        — UI "does not ~" 처리 시 사용되는 wrapper. {"func":"without", "pred": {...}} 형태로 내부 pred를 부정.
                       (단순 "not"은 다른 용도 — pred 단순 부정. UI에서 "does not contain any of" 만들면 Adobe가 without으로 직렬화.)
  · 중첩 container   — pred 안에 또 container를 둘 수 있음. UI에서 그룹핑하면 이렇게 직렬화됨.
"""
from __future__ import annotations

# 위 segment 1개를 새로 만들고 싶을 때 — test_create_segment.py의 SEGMENT_DEFINITION 자리에 그대로 박으면 됨
SEGMENT_DEFINITION = {
    "func": "segment",
    "version": [1, 0, 0],
    "container": {
        "func": "container",
        "context": "hits",
        "pred": {
            "func": "or",
            "preds": [
                # ───── [A] Page 기반 조건 ─────
                {
                    "func": "container",
                    "context": "hits",
                    "description": "Page",
                    "pred": {
                        "func": "and",
                        "preds": [
                            # A-1) Page에 "campaign_name" 포함
                            {
                                "func": "container",
                                "context": "hits",
                                "pred": {
                                    "func": "contains",
                                    "val": {"func": "attr", "name": "variables/page"},
                                    "str": "campaign_name",
                                    "description": "Page",
                                },
                            },
                            # A-2) Page에 [exclusion 리스트] 어느 것도 포함 안 함
                            {
                                "func": "container",
                                "context": "hits",
                                "pred": {
                                    "func": "without",
                                    "pred": {
                                        "func": "contains-any-of",
                                        "val": {"func": "attr", "name": "variables/page"},
                                        "list": [
                                            "whatsapp", "explore", "crm", "bespoke", "rtb",
                                            "cid=", "&utm", "mc=TS",
                                            "2021", "2022", "2023", "2024", "2025",
                                            "news", "members", "ios",
                                            "25", "promo", "offline", "online", "watchsapp",
                                        ],
                                        "description": "Page",
                                    },
                                },
                            },
                        ],
                    },
                },
                # ───── [B] prop29 기반 조건 (같은 패턴) ─────
                {
                    "func": "container",
                    "context": "hits",
                    "description": "P29",
                    "pred": {
                        "func": "and",
                        "preds": [
                            # B-1) prop29에 "campaign_name" 포함
                            {
                                "func": "container",
                                "context": "hits",
                                "pred": {
                                    "func": "contains",
                                    "val": {"func": "attr", "name": "variables/prop29"},
                                    "str": "campaign_name",
                                    "description": "URL without Parameter (p29)",
                                },
                            },
                            # B-2) prop29에 [exclusion 리스트] 어느 것도 포함 안 함
                            {
                                "func": "container",
                                "context": "hits",
                                "pred": {
                                    "func": "without",
                                    "pred": {
                                        "func": "contains-any-of",
                                        "val": {"func": "attr", "name": "variables/prop29"},
                                        "list": [
                                            "whatsapp", "explore", "crm", "bespoke", "rtb",
                                            "cid=", "&utm", "mc=TS",
                                            "2021", "2022", "2023", "2024", "2025",
                                            "news", "members", "ios",
                                            "25", "promo", "offline", "online", "watchsapp",
                                        ],
                                        "description": "URL without Parameter (p29)",
                                    },
                                },
                            },
                        ],
                    },
                },
            ],
        },
    },
}


# ─── 사용법 ─────────────────────────────────────────────────────
# test_create_segment.py에서:
#   1. 위 SEGMENT_DEFINITION을 통째로 복사해 SEGMENT_DEFINITION 자리에 붙여넣기
#   2. SEGMENT_NAME / SEGMENT_DESCRIPTION / SEGMENT_TAGS / RSID 등 메타정보 본인 케이스에 맞게 수정
#   3. python test_create_segment.py        ← dry-run으로 payload 확인
#   4. python test_create_segment.py --apply ← 문제 없으면 실제 POST


if __name__ == "__main__":
    # 직접 실행 시 정의 출력 (디버그용)
    import json
    print(json.dumps(SEGMENT_DEFINITION, ensure_ascii=False, indent=2))
