"""FDA data normalization — CSV parsing and deduplication.

This module handles:
- Parsing the FDA AI/ML-Enabled Medical Devices CSV
- Normalizing regulatory pathways from submission numbers
- Deduplicating products across sources

For data fetching (bulk files, web scraping), see fda_scraper.py.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.config import settings
from src.models.product import (
    AliasType,
    EvidenceTier,
    Product,
    ProductAlias,
    RegionCode,
    RegulatoryEntry,
    RegulatoryPathway,
    RegulatoryStatusNormalized,
)
from src.utils import parse_date

logger = logging.getLogger(__name__)

# AI/ML keyword list — used by fda_scraper.py for filtering
SAMD_KEYWORDS = [
    "software as a medical device",
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "computer-aided",
    "computer aided",
    "algorithm",
    "neural network",
    "autonomous",
    "automated detection",
    "automated diagnosis",
]


# ---------------------------------------------------------------------------
# Pathway inference
# ---------------------------------------------------------------------------

def infer_pathway(submission_number: str) -> tuple[RegulatoryPathway, RegulatoryStatusNormalized]:
    """Infer regulatory pathway from submission number prefix."""
    sub = submission_number.upper().strip()
    if sub.startswith("DEN"):
        return RegulatoryPathway.DE_NOVO, RegulatoryStatusNormalized.AUTHORIZED
    if sub.startswith("P"):
        return RegulatoryPathway.PMA, RegulatoryStatusNormalized.APPROVED
    if sub.startswith("H"):
        return RegulatoryPathway.HDE, RegulatoryStatusNormalized.APPROVED
    return RegulatoryPathway.K510, RegulatoryStatusNormalized.CLEARED


# ---------------------------------------------------------------------------
# CSV parsing (for local AI/ML list fallback)
# ---------------------------------------------------------------------------

def parse_fda_aiml_list(rows: list[dict[str, str]]) -> list[tuple[Product, RegulatoryEntry]]:
    """Parse the FDA AI/ML-Enabled Medical Devices CSV rows.

    Handles both current format (Date of Final Decision, Device, Company,
    Panel (Lead), Primary Product Code) and older formats.
    """
    results = []
    for row in rows:
        submission_number = row.get("Submission Number", "").strip()
        device_name = (row.get("Device", "") or row.get("Trade Name", "")).strip()
        company = row.get("Company", "").strip()
        date_str = (
            row.get("Date of Final Decision", "")
            or row.get("Date of Authorization", "")
            or row.get("Date", "")
        ).strip()
        panel = row.get("Panel (Lead)", row.get("Panel", "")).strip()
        product_code = row.get("Primary Product Code", row.get("Product Code", "")).strip()

        if not device_name:
            continue

        # Split semicolon-separated device names
        name_parts = [n.strip() for n in device_name.split(";") if n.strip()]
        canonical = name_parts[0]

        pathway, status = infer_pathway(submission_number)

        product = Product(
            canonical_name=canonical,
            manufacturer_name=company,
            standalone_samd=True,
            aliases=[
                ProductAlias(
                    product_id="00000000-0000-0000-0000-000000000000",
                    alias_name=name,
                    alias_type=AliasType.TRADE_NAME,
                    source="fda_aiml_list",
                )
                for name in name_parts[1:]
            ],
        )
        for alias in product.aliases:
            alias.product_id = product.product_id

        entry = RegulatoryEntry(
            product_id=product.product_id,
            region=RegionCode.US,
            regulatory_pathway=pathway,
            regulatory_status_raw=pathway.value,
            regulatory_status=status,
            regulatory_id=submission_number or None,
            clearance_date=parse_date(date_str),
            product_code=product_code or None,
            review_panel=panel or None,
            applicant=company,
            evidence_tier=EvidenceTier.TIER_1,
        )
        results.append((product, entry))
    logger.info("FDA AI/ML list: %d products parsed", len(results))
    return results


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_fda_products(
    products: list[tuple[Product, RegulatoryEntry]],
) -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Merge products that share the same regulatory_id."""
    by_reg_id: dict[str, tuple[Product, list[RegulatoryEntry]]] = {}
    no_id: list[tuple[Product, list[RegulatoryEntry]]] = []

    for product, entry in products:
        reg_id = entry.regulatory_id
        if reg_id and reg_id in by_reg_id:
            by_reg_id[reg_id][1].append(entry)
        elif reg_id:
            by_reg_id[reg_id] = (product, [entry])
        else:
            no_id.append((product, [entry]))

    result = list(by_reg_id.values()) + no_id
    logger.info("Deduplicated %d FDA records into %d unique products",
                len(products), len(result))
    return result
