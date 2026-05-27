# site_registry.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class SiteInfo:
    subsidiary: str | None
    country: str | None
    site_code: str          # 입력 받은 site_code (정규화 후)
    rsid: str               # 최종 report suite id

# ✅ 여기에 “정식” 매핑만 넣으면 됨 (언더스코어 포함 버전은 그대로)
_SITE_MASTER: dict[str, tuple[str | None, str | None, str]] = {
    # --- MST ---
    "mstglobal": (None, "MST Global", "rsid_placeholder"),

    # --- SEAU ---
    "au": ("FRNH", "Australia", "rsid_placeholder"),
    "bd": ("FVRY", "Bangladesh", "rsid_placeholder"),
    "in": ("FVRY", "India", "rsid_placeholder"),
    "id": ("FRVA", "Indonesia", "rsid_placeholder"),
    "my": ("FZR", "Malaysia", "rsid_placeholder"),
    "nz": ("FRAM", "New Zealand", "rsid_placeholder"),
    "ph": ("FRCPB", "Philippines", "rsid_placeholder"),
    "sg": ("FRFC", "Singapore", "rsid_placeholder"),
    "th": ("GFR", "Thailand", "rsid_placeholder"),
    "vn": ("FNIVAN", "Vietnam", "rsid_placeholder"),
    "sec": ("FRP", "Korea", "rsid_placeholder"),
    "mm": ("GFR", "Myanmar", "rsid_placeholder"),
    "jp": ("FRW", "Japan", "rsid_placeholder"),
    "cn": ("FPVP", "China", "rsid_placeholder"),
    "hk": ("FRUX", "HongKong", "rsid_placeholder"),
    "hk_en": ("FRUX", "HongKong", "rsid_placeholder"),
    "tw": ("FRG", "Taiwan", "rsid_placeholder"),
    "az": ("FREP", "Azerbaijan", "rsid_placeholder"),
    "kz_ru": ("FRPR", "Kazakhstan", "rsid_placeholder"),
    "kz_kz": ("FRPR", "Kazakhstan", "rsid_placeholder"),
    "ge": ("FREP", "Georgia", "rsid_placeholder"),
    "mn": ("FRPR", "Mongolia", "rsid_placeholder"),
    "ru": ("FREP", "Russia", "rsid_placeholder"),
    "ua": ("FRHP", "Ukraine", "rsid_placeholder"),
    "uz_ru": ("FRHM", "Uzbekistan", "rsid_placeholder"),
    "uz_uz": ("FRHM", "Uzbekistan", "rsid_placeholder"),
    "africa_en": ("FRJN", "Africa Pan", "rsid_placeholder"),
    "africa_fr": ("FRJN", "Africa Pan", "rsid_placeholder"),
    "eg": ("FRRT-F", "Egypt", "rsid_placeholder"),
    "iran": ("Iran", "Iran", "rsid_placeholder"),
    "il": ("FRVY", "Israel", "rsid_placeholder"),
    "iq_ku": ("FRYI", "Kurdistan", "rsid_placeholder"),
    "iq_ar": ("FRYI", "Iraq", "rsid_placeholder"),
    "levant": ("FRYI", "Levant", "rsid_placeholder"),
    "levant_ar": ("FRYI", "Levant", "rsid_placeholder"),
    "africa_pt": ("FRJN", "Africa Pan", "rsid_placeholder"),
    "n_africa": ("FRZNT", "North Africa", "rsid_placeholder"),
    "pk": ("FRCNX", "Pakistan", "rsid_placeholder"),
    "ps": ("FRVY", "Palestine", "rsid_placeholder"),
    "sa": ("FRFNE", "Saudi Arabia", "rsid_placeholder"),
    "tr": ("FRGX", "Turkey", "rsid_placeholder"),
    "ae": ("FTR", "UAE", "rsid_placeholder"),
    "ae_ar": ("FTR", "UAE", "rsid_placeholder"),
    "sa_en": ("FRFNE", "Saudi Arabia", "rsid_placeholder"),
    "za": ("FFN", "South Africa", "rsid_placeholder"),
    "lb": ("FRYI", "Lebanon", "rsid_placeholder"),

    # --- Europe etc ---
    "at": ("FRNF", "Austria", "rsid_placeholder"),
    "be": ("FROA", "Belgium", "rsid_placeholder"),
    "be_fr": ("FROA", "Belgium", "rsid_placeholder"),  # (표가 be/be_fr 같이 적혀있어서 일단 동일 RSID로 둠)
    "ba": ("FRNQ", "Bosnia", "rsid_placeholder"),
    "bg": ("FREBZ", "Bulgaria", "rsid_placeholder"),
    "hr": ("FRNQ", "Croatia", "rsid_placeholder"),
    "cz": ("FRPM", "Czech", "rsid_placeholder"),
    "dk": ("FRAN", "Denmark", "rsid_placeholder"),
    "ee": ("FRO", "Estonia", "rsid_placeholder"),
    "fi": ("FRAN", "Finland", "rsid_placeholder"),
    "fr": ("FRS", "France", "rsid_placeholder"),
    "de": ("FRT", "Germany", "rsid_placeholder"),
    "gr": ("FRTE", "Greece", "rsid_placeholder"),
    "hu": ("FRU", "Hungary", "rsid_placeholder"),
    "ie": ("FRHX", "Ireland", "rsid_placeholder"),
    "it": ("FRV", "Italy", "rsid_placeholder"),
    "lv": ("FRO", "Latvia", "rsid_placeholder"),
    "lt": ("FRO", "Lithuania", "rsid_placeholder"),
    "mk": ("FRNQ", "Macedonia", "rsid_placeholder"),
    "nl": ("FROA", "Netherlands", "rsid_placeholder"),
    "no": ("FRAN", "Norway", "rsid_placeholder"),
    "pl": ("FRCBY", "Poland", "rsid_placeholder"),
    "pt": ("FRVO", "Portugal", "rsid_placeholder"),
    "ro": ("FREBZ", "Romania", "rsid_placeholder"),
    "rs": ("FRNQ", "Serbia", "rsid_placeholder"),
    "sk": ("FRPM", "Slovakia", "rsid_placeholder"),
    "si": ("FRNQ", "Slovenia", "rsid_placeholder"),
    "es": ("FRVO", "Spain", "rsid_placeholder"),
    "se": ("FRAN", "Sweden", "rsid_placeholder"),
    "ch": ("FRNF", "Switzerland", "rsid_placeholder"),
    "ch_fr": ("FRNF", "Switzerland", "rsid_placeholder"),
    "uk": ("FRHX", "UK", "rsid_placeholder"),
    "al": ("FRNQ", "Albania", "rsid_placeholder"),

    # --- Americas ---
    "ar": ("FRNFN", "Argentina", "rsid_placeholder"),
    "br": ("FRQN", "Brazil", "rsid_placeholder"),
    "cl": ("FRPU", "Chile", "rsid_placeholder"),
    "co": ("FNZPBY", "Colombia", "rsid_placeholder"),
    "latin_en": ("FRYN", "Panama", "rsid_placeholder"),
    "latin": ("FRYN", "Panama", "rsid_placeholder"),
    "pe": ("FRCE", "Peru", "rsid_placeholder"),
    "uy": ("FRYN", "Uruguay", "rsid_placeholder"),
    "py": ("FRYN", "Paraguay", "rsid_placeholder"),
    "ca": ("FRPN", "Canada", "rsid_placeholder"),
    "ca_fr": ("FRPN", "Canada", "rsid_placeholder"),  
    "mx": ("FRZ", "Mexico", "rsid_placeholder"),
    "us": ("FRN", "US", "rsid_placeholder"), # ~ 2026-05-18
    # "us": ("FRN", "US", "rsid_placeholder"), # 2026-05-19 ~
}

def lookup_site(site_code: str) -> SiteInfo:
    sc = str(site_code).strip().lower()
    sc2 = sc.replace("_", "")  # alias

    # 1) 정식키 우선 (ca_fr)
    if sc in _SITE_MASTER:
        sub, country, rsid = _SITE_MASTER[sc]
        return SiteInfo(sub, country, sc, rsid)

    # 2) '_' 제거 alias (cafr)
    if sc2 in _SITE_MASTER:
        sub, country, rsid = _SITE_MASTER[sc2]
        return SiteInfo(sub, country, sc, rsid)

    # 3) 마스터에 없으면 fallback(그래도 '_' 제거 규칙)
    return SiteInfo(None, None, sc, f"sscompany_name4{sc2}")
