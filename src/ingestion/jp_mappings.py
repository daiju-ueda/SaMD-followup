"""Japanese → English manufacturer name mappings.

Single source of truth for JP corporate name → English name resolution.
Used by both CSV parser (pmda.py) and web scraper (pmda_scraper.py).
"""

MANUFACTURER_JP_EN: dict[str, str] = {
    "オリンパス": "Olympus Corporation",
    "キヤノンメディカルシステムズ": "Canon Medical Systems",
    "富士フイルム": "Fujifilm Corporation",
    "シーメンスヘルスケア": "Siemens Healthineers",
    "フィリップス": "Philips Healthcare",
    "GEヘルスケア": "GE HealthCare",
    "テルモ": "Terumo Corporation",
    "島津製作所": "Shimadzu Corporation",
    "エムスリー": "M3 Inc.",
    "アイリス": "Aillis Inc.",
    "エルピクセル": "LPIXEL Inc.",
    "サイバネットシステム": "Cybernet Systems",
    "アップル": "Apple Inc.",
    "ファーウェイ": "Huawei",
    "フィリップス・レスピロニクス": "Philips Respironics",
    "エレクタ": "Elekta",
}


def map_manufacturer(jp_name: str) -> tuple[str, str | None]:
    """Return (english_name, japanese_name_if_mapped).

    If no mapping found, returns the original name as-is.
    """
    for jp, en in MANUFACTURER_JP_EN.items():
        if jp in jp_name:
            return en, jp_name
    return jp_name, None
