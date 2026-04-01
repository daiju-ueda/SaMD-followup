"""PMDA (Japan) data ingestion.

Japan has no public API for medical device data. Data comes from:
1. PMDA website — approved/certified device pages (HTML scraping)
2. PMDA SaMD-specific pages
3. Manual CSV imports (curated lists)

This module handles both scraping and CSV-based ingestion.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from src.utils import parse_date
from src.models.product import (
    AliasType,
    EvidenceTier,
    ManufacturerAlias,
    Product,
    ProductAlias,
    RegionCode,
    RegulatoryEntry,
    RegulatoryPathway,
    RegulatoryStatusNormalized,
)

logger = logging.getLogger(__name__)


# ---- Japanese → English mapping helpers ------------------------------------

# Common manufacturer name mappings (JP corporate name → English)
MANUFACTURER_JP_EN_MAP: dict[str, str] = {
    "オリンパス": "Olympus Corporation",
    "キヤノンメディカルシステムズ": "Canon Medical Systems",
    "富士フイルム": "Fujifilm Corporation",
    "シーメンスヘルスケア": "Siemens Healthineers",
    "フィリップス": "Philips",
    "GEヘルスケア": "GE HealthCare",
    "テルモ": "Terumo Corporation",
    "島津製作所": "Shimadzu Corporation",
    "エムスリー": "M3 Inc.",
    "アイリス": "Aillis Inc.",
    # Extended as needed during curation
}


def _map_manufacturer(jp_name: str) -> tuple[str, Optional[str]]:
    """Return (english_name, japanese_name) for a manufacturer.

    If no mapping is found, the original name is returned as-is (assumed
    to already be in English or transliterated).
    """
    for jp, en in MANUFACTURER_JP_EN_MAP.items():
        if jp in jp_name:
            return en, jp_name
    return jp_name, None


_parse_jp_date = parse_date  # unified parser handles JP era formats


def _determine_jp_pathway(
    device_class: str,
    approval_type: str,
) -> tuple[RegulatoryPathway, RegulatoryStatusNormalized]:
    """Map Japanese device class / approval type to pathway + status."""
    approval_lower = approval_type.lower() if approval_type else ""
    class_str = device_class.strip() if device_class else ""

    if "承認" in approval_lower or class_str in ("III", "IV", "クラスIII", "クラスIV"):
        return RegulatoryPathway.APPROVAL, RegulatoryStatusNormalized.APPROVED
    if "認証" in approval_lower or class_str in ("II", "クラスII"):
        return RegulatoryPathway.CERTIFICATION, RegulatoryStatusNormalized.CERTIFIED
    if "届出" in approval_lower or class_str in ("I", "クラスI"):
        return RegulatoryPathway.NOTIFICATION, RegulatoryStatusNormalized.UNKNOWN

    return RegulatoryPathway.OTHER, RegulatoryStatusNormalized.UNKNOWN


# ---- CSV ingestion (primary approach for MVP) -------------------------------

def parse_pmda_csv(
    csv_content: str,
    *,
    encoding: str = "utf-8",
) -> list[tuple[Product, RegulatoryEntry, list[ProductAlias]]]:
    """Parse a curated CSV of Japanese SaMD products.

    Expected columns (flexible — mapped by header):
      - 販売名 / product_name_ja: Japanese product name
      - 英語名 / product_name_en: English product name (optional)
      - 製造販売業者 / manufacturer: Manufacturer name
      - クラス / device_class: Device class (II, III, IV)
      - 承認/認証区分 / approval_type: 承認 or 認証
      - 承認番号 / approval_number: Regulatory ID
      - 承認日 / approval_date: Approval/certification date
      - 一般的名称 / generic_name: Generic device name
      - 使用目的 / intended_use: Intended use
      - 疾患領域 / disease_area: Disease area (optional)
      - モダリティ / modality: Modality (optional)
    """
    # Normalize column names
    COLUMN_MAP = {
        "販売名": "product_name_ja",
        "英語名": "product_name_en",
        "製造販売業者": "manufacturer",
        "クラス": "device_class",
        "承認/認証区分": "approval_type",
        "承認番号": "approval_number",
        "認証番号": "approval_number",
        "承認日": "approval_date",
        "認証日": "approval_date",
        "一般的名称": "generic_name",
        "使用目的": "intended_use",
        "疾患領域": "disease_area",
        "モダリティ": "modality",
    }

    reader = csv.DictReader(io.StringIO(csv_content))
    results = []

    for row in reader:
        # Map Japanese headers to normalized keys
        normalized: dict[str, str] = {}
        for key, value in row.items():
            mapped_key = COLUMN_MAP.get(key.strip(), key.strip())
            normalized[mapped_key] = (value or "").strip()

        ja_name = normalized.get("product_name_ja", "")
        en_name = normalized.get("product_name_en", "")
        manufacturer_raw = normalized.get("manufacturer", "")
        device_class = normalized.get("device_class", "")
        approval_type = normalized.get("approval_type", "")
        approval_number = normalized.get("approval_number", "")
        date_str = normalized.get("approval_date", "")
        generic_name = normalized.get("generic_name", "")
        intended_use = normalized.get("intended_use", "")
        disease_area = normalized.get("disease_area", "")
        modality = normalized.get("modality", "")

        if not ja_name and not en_name:
            continue

        # Canonical name: prefer English, fall back to Japanese
        canonical = en_name if en_name else ja_name
        manufacturer_en, manufacturer_ja = _map_manufacturer(manufacturer_raw)
        pathway, status = _determine_jp_pathway(device_class, approval_type)

        product = Product(
            canonical_name=canonical,
            manufacturer_name=manufacturer_en,
            intended_use=intended_use or None,
            disease_area=disease_area or None,
            modality=modality or None,
            standalone_samd=True,
        )

        entry = RegulatoryEntry(
            product_id=product.product_id,
            region=RegionCode.JP,
            regulatory_pathway=pathway,
            regulatory_status_raw=approval_type,
            regulatory_status=status,
            regulatory_id=approval_number or None,
            clearance_date=_parse_jp_date(date_str),
            device_class=device_class or None,
            applicant=manufacturer_raw,
            evidence_tier=EvidenceTier.TIER_1,
            raw_data=dict(normalized),
        )

        # Build aliases
        aliases: list[ProductAlias] = []
        if ja_name:
            aliases.append(ProductAlias(
                product_id=product.product_id,
                alias_name=ja_name,
                alias_type=AliasType.JAPANESE_NAME,
                language="ja",
                is_primary=not en_name,
                source="pmda_csv",
            ))
        if en_name and ja_name:
            aliases.append(ProductAlias(
                product_id=product.product_id,
                alias_name=en_name,
                alias_type=AliasType.TRADE_NAME,
                language="en",
                is_primary=True,
                source="pmda_csv",
            ))
        if generic_name:
            aliases.append(ProductAlias(
                product_id=product.product_id,
                alias_name=generic_name,
                alias_type=AliasType.GENERIC_NAME,
                language="ja",
                source="pmda_csv",
            ))

        results.append((product, entry, aliases))

    logger.info("PMDA CSV: %d products parsed", len(results))
    return results


def load_pmda_csv_file(path: str | Path) -> list[tuple[Product, RegulatoryEntry, list[ProductAlias]]]:
    """Load and parse a PMDA CSV file from disk."""
    content = Path(path).read_text(encoding="utf-8")
    return parse_pmda_csv(content)
