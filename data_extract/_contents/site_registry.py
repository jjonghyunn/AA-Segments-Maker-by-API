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
    "mstglobal": (None, "MST Global", "sscompany_name4mstglobal"),

    # --- SEAU ---
    "au": ("FRNH", "Australia", "sscompany_name4au"),
    "bd": ("FVRY", "Bangladesh", "sscompany_name4bd"),
    "in": ("FVRY", "India", "sscompany_name4in"),
    "id": ("FRVA", "Indonesia", "sscompany_name4id"),
    "my": ("FZR", "Malaysia", "sscompany_name4my"),
    "nz": ("FRAM", "New Zealand", "sscompany_name4nz"),
    "ph": ("FRCPB", "Philippines", "sscompany_name4ph"),
    "sg": ("FRFC", "Singapore", "sscompany_name4sg"),
    "th": ("GFR", "Thailand", "sscompany_name4th"),
    "vn": ("FNIVAN", "Vietnam", "sscompany_name4vn"),
    "hq": ("FRP", "Korea", "sscompany_name4hq"),
    "mm": ("GFR", "Myanmar", "sscompany_name4mm"),
    "jp": ("FRW", "Japan", "sscompany_name4jp"),
    "cn": ("FPVP", "China", "sscompany_name4cn"),
    "hk": ("FRUX", "HongKong", "sscompany_name4hk"),
    "hk_en": ("FRUX", "HongKong", "sscompany_name4hken"),
    "tw": ("FRG", "Taiwan", "sscompany_name4tw"),
    "az": ("FREP", "Azerbaijan", "sscompany_name4az"),
    "kz_ru": ("FRPR", "Kazakhstan", "sscompany_name4kzru"),
    "kz_kz": ("FRPR", "Kazakhstan", "sscompany_name4kzkz"),
    "ge": ("FREP", "Georgia", "sscompany_name4ge"),
    "mn": ("FRPR", "Mongolia", "sscompany_name4mn"),
    "ru": ("FREP", "Russia", "sscompany_name4ru"),
    "ua": ("FRHP", "Ukraine", "sscompany_name4ua"),
    "uz_ru": ("FRHM", "Uzbekistan", "sscompany_name4uzru"),
    "uz_uz": ("FRHM", "Uzbekistan", "sscompany_name4uzuz"),
    "africa_en": ("FRJN", "Africa Pan", "sscompany_name4africaen"),
    "africa_fr": ("FRJN", "Africa Pan", "sscompany_name4africafr"),
    "eg": ("FRRT-F", "Egypt", "sscompany_name4eg"),
    "iran": ("Iran", "Iran", "sscompany_name4iran"),
    "il": ("FRVY", "Israel", "sscompany_name4il"),
    "iq_ku": ("FRYI", "Kurdistan", "sscompany_name4ku"),
    "iq_ar": ("FRYI", "Iraq", "sscompany_name4iqar"),
    "levant": ("FRYI", "Levant", "sscompany_name4levant"),
    "levant_ar": ("FRYI", "Levant", "sscompany_name4levantar"),
    "africa_pt": ("FRJN", "Africa Pan", "sscompany_name4africapt"),
    "n_africa": ("FRZNT", "North Africa", "sscompany_name4nafrica"),
    "pk": ("FRPK", "Pakistan", "sscompany_name4pk"),
    "ps": ("FRVY", "Palestine", "sscompany_name4ps"),
    "sa": ("FRFNE", "Saudi Arabia", "sscompany_name4sa"),
    "tr": ("FRGX", "Turkey", "sscompany_name4tr"),
    "ae": ("FTR", "UAE", "sscompany_name4ae"),
    "ae_ar": ("FTR", "UAE", "sscompany_name4aear"),
    "sa_en": ("FRFNE", "Saudi Arabia", "sscompany_name4saen"),
    "za": ("FFN", "South Africa", "sscompany_name4za"),
    "lb": ("FRYI", "Lebanon", "sscompany_name4lb"),

    # --- Europe etc ---
    "at": ("FRNF", "Austria", "sscompany_name4at"),
    "be": ("FROA", "Belgium", "vrs_your_aa_company_id_p6webmstbelgiumestor"),
    "be_old": ("FROA", "Belgium", "sscompany_name4be"), # ~ 2025-12 말 경
    "be_fr": ("FROA", "Belgium", "vrs_your_aa_company_id_p6webmstbelgiumestor"),  # (표가 be/be_fr 같이 적혀있어서 일단 동일 RSID로 둠)
    "ba": ("FRNQ", "Bosnia", "sscompany_name4ba"),
    "bg": ("FREBZ", "Bulgaria", "sscompany_name4bg"),
    "hr": ("FRNQ", "Croatia", "sscompany_name4hr"),
    "cz": ("FRPM", "Czech", "sscompany_name4cz"),
    "dk": ("FRAN", "Denmark", "sscompany_name4dk"),
    "ee": ("FRO", "Estonia", "sscompany_name4ee"),
    "fi": ("FRAN", "Finland", "sscompany_name4fi"),
    "fr": ("FRS", "France", "sscompany_name4fr"),
    "de": ("FRT", "Germany", "vrs_your_aa_company_id_p6webmstgermanyestor_0"),
    "de_old": ("FRT", "Germany", "sscompany_name4de"), # ~ 2025-12 말 경
    "gr": ("FRTE", "Greece", "sscompany_name4gr"),
    "hu": ("FRU", "Hungary", "sscompany_name4hu"),
    "ie": ("FRHX", "Ireland", "sscompany_name4ie"),
    "it": ("FRV", "Italy", "sscompany_name4it"),
    "lv": ("FRO", "Latvia", "sscompany_name4lv"),
    "lt": ("FRO", "Lithuania", "sscompany_name4lt"),
    "mk": ("FRNQ", "Macedonia", "sscompany_name4mk"),
    "nl": ("FROA", "Netherlands", "vrs_your_aa_company_id_p6webmstnetherlandse_0"),
    "nl_old": ("FROA", "Netherlands", "sscompany_name4nl"), # ~ 2025-12 말 경
    "no": ("FRAN", "Norway", "sscompany_name4no"),
    "pl": ("FRCBY", "Poland", "sscompany_name4pl"),
    "pt": ("FRVO", "Portugal", "vrs_your_aa_company_id_p6webmstportugalesto"),
    "pt_old": ("FRVO", "Portugal", "sscompany_name4pt"), # ~ 2025-12 말 경
    "ro": ("FREBZ", "Romania", "sscompany_name4ro"),
    "rs": ("FRNQ", "Serbia", "sscompany_name4rs"),
    "sk": ("FRPM", "Slovakia", "sscompany_name4sk"),
    "si": ("FRNQ", "Slovenia", "sscompany_name4si"),
    "es": ("FRVO", "Spain", "vrs_your_aa_company_id_p6webmstspainestoreb"),
    "es_old": ("FRVO", "Spain", "sscompany_name4es"), # ~ 2025-12 말 경
    "se": ("FRAN", "Sweden", "sscompany_name4se"),
    "ch": ("FRNF", "Switzerland", "sscompany_name4ch"),
    "ch_fr": ("FRNF", "Switzerland", "sscompany_name4chfr"),
    "uk": ("FRHX", "UK", "vrs_your_aa_company_id_p6webmstukcopy"),
    "uk_old": ("FRHX", "UK", "sscompany_name4uk"), # ~ 2025-12 말 경
    "al": ("FRNQ", "Albania", "sscompany_name4al"),

    # --- Americas ---
    "ar": ("FRNFN", "Argentina", "sscompany_name4ar"),
    "br": ("FRQN", "Brazil", "sscompany_name4br"),
    "cl": ("FRPU", "Chile", "sscompany_name4cl"),
    "co": ("FNZPBY", "Colombia", "sscompany_name4co"),
    "latin_en": ("FRYN", "Panama", "sscompany_name4latinen"),
    "latin": ("FRYN", "Panama", "sscompany_name4latin"),
    "pe": ("FRCE", "Peru", "sscompany_name4pe"),
    "uy": ("FRYN", "Uruguay", "sscompany_name4uy"),
    "py": ("FRYN", "Paraguay", "sscompany_name4py"),
    "ca": ("FRPN", "Canada", "sscompany_name4ca"),
    "ca_fr": ("FRPN", "Canada", "sscompany_name4cafr"),  
    "mx": ("FRZ", "Mexico", "sscompany_name4mx"),
    "us_old": ("FRN", "US", "sscompany_namenewus"), # ~ 2026-05-18
    "us": ("FRN", "US", "sscompany_name4newus"), # 2026-05-19 ~
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
