"""FDA AI/ML SaMD product ingestion via openFDA API.

Uses the openFDA REST API to fetch 510(k), PMA, and De Novo records
filtered by product codes known to contain AI/ML SaMD devices.

No web scraping or CSV download needed — openFDA has no bot detection.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.config import settings
from src.ingestion.fda import (
    SAMD_KEYWORDS_IN_DESCRIPTION,
    _infer_pathway_from_submission_number,
    deduplicate_fda_products,
)
from src.ingestion.normalizer import enrich_product
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

# Product codes found in the FDA AI/ML-Enabled Medical Devices list.
# These are the top codes covering ~95% of all AI/ML SaMD.
# Full list has 168 codes but the long tail has 1 device each.
AIML_PRODUCT_CODES = [
    "QIH", "LLZ", "IYN", "LNH", "JAK", "QAS", "QKB", "QFM",
    "MYN", "MUJ", "QDQ", "DQK", "KPS", "POK", "QNP", "DQD",
    "OWB", "JOY", "MNR", "OEB", "QBS", "DPS", "OEI", "QMT",
    "DTB", "QKQ", "NVD", "PHI", "LMD", "QPN", "NQI", "QRZ",
    "PSY", "DXA", "DPL", "DPM", "IYO", "OQG", "PIB", "MMO",
]

# Additional AI/ML-specific keywords beyond SAMD_KEYWORDS_IN_DESCRIPTION
AIML_KEYWORDS = SAMD_KEYWORDS_IN_DESCRIPTION + [
    "ai-based", "ai based", "ai-powered", "ai powered",
    "convolutional", "classification", "segmentation",
    "computer-assisted", "computer assisted",
    "cad", "detection software", "diagnostic software",
]

OPENFDA_BASE = "https://api.fda.gov/device"


def _is_aiml_candidate(record: dict[str, Any]) -> bool:
    """Check if an openFDA record is likely an AI/ML SaMD.

    Uses device_name + statement_or_summary for keyword matching.
    Also checks if the device name itself suggests software/AI.
    """
    description = " ".join(filter(None, [
        record.get("device_name", ""),
        record.get("statement_or_summary", ""),
    ])).lower()

    # Primary: AI/ML keywords in description
    if any(kw in description for kw in AIML_KEYWORDS):
        return True

    # Secondary: device name contains software-related terms
    # (many AI/ML devices just say "software" in the name)
    name = (record.get("device_name") or "").lower()
    software_terms = ["software", "program", "platform", "app ", "system"]
    if any(t in name for t in software_terms):
        return True

    return False


# ---------------------------------------------------------------------------
# openFDA query helpers
# ---------------------------------------------------------------------------

def _api_params(search: str, limit: int = 100, skip: int = 0) -> dict[str, str]:
    params = {"search": search, "limit": str(limit), "skip": str(skip)}
    if settings.openfda_api_key:
        params["api_key"] = settings.openfda_api_key
    return params


async def _fetch_all_from_endpoint(
    client: httpx.AsyncClient,
    endpoint: str,
    search: str,
    max_records: int = 10000,
) -> list[dict[str, Any]]:
    """Page through openFDA results."""
    all_results: list[dict] = []
    skip = 0
    limit = 100
    while skip < max_records:
        params = _api_params(search, limit=limit, skip=skip)
        url = f"{OPENFDA_BASE}/{endpoint}.json"
        resp = await client.get(url, params=params, timeout=30)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        body = resp.json()
        results = body.get("results", [])
        if not results:
            break
        all_results.extend(results)
        total = body.get("meta", {}).get("results", {}).get("total", 0)
        skip += limit
        if skip >= total:
            break
        await asyncio.sleep(1.0 / settings.openfda_requests_per_second)
    return all_results


# ---------------------------------------------------------------------------
# Record normalization
# ---------------------------------------------------------------------------

def _normalize_record(record: dict[str, Any]) -> tuple[Product, RegulatoryEntry]:
    """Convert an openFDA 510k/PMA record into Product + RegulatoryEntry."""
    device_name = (record.get("device_name") or "").strip()
    applicant = (record.get("applicant") or "").strip()
    product_code = record.get("product_code", "")

    # Determine submission number and pathway
    k_number = record.get("k_number", "")
    pma_number = record.get("pma_number", "")
    submission_number = k_number or pma_number or ""

    pathway, status = _infer_pathway_from_submission_number(submission_number)

    # Split semicolon-separated device names
    name_parts = [n.strip() for n in device_name.split(";") if n.strip()]
    canonical = name_parts[0] if name_parts else device_name
    aliases = [
        ProductAlias(
            product_id="00000000-0000-0000-0000-000000000000",
            alias_name=name,
            alias_type=AliasType.TRADE_NAME,
            source="openfda",
        )
        for name in name_parts[1:]
    ]

    product = Product(
        canonical_name=canonical,
        manufacturer_name=applicant,
        standalone_samd=True,
        aliases=aliases,
    )

    # Fix alias product_id
    for alias in product.aliases:
        alias.product_id = product.product_id

    date_str = record.get("decision_date") or record.get("date_received") or ""
    entry = RegulatoryEntry(
        product_id=product.product_id,
        region=RegionCode.US,
        regulatory_pathway=pathway,
        regulatory_status_raw=record.get("decision_description", ""),
        regulatory_status=status,
        regulatory_id=submission_number or None,
        clearance_date=parse_date(date_str),
        device_class=record.get("device_class"),
        product_code=product_code,
        review_panel=record.get("advisory_committee_description"),
        applicant=applicant,
        source_url=f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={submission_number}" if submission_number else None,
        evidence_tier=EvidenceTier.TIER_1,
        raw_data=record,
    )
    return product, entry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_fda_aiml_products() -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Fetch AI/ML SaMD products from openFDA API.

    Queries 510(k) and PMA endpoints filtered by AI/ML product codes.
    Returns enriched, deduplicated products.
    """
    code_clause = " OR ".join(f'"{c}"' for c in AIML_PRODUCT_CODES)
    search = f"product_code:({code_clause})"

    async with httpx.AsyncClient() as client:
        logger.info("Fetching FDA 510(k) AI/ML devices via openFDA API...")
        records_510k = await _fetch_all_from_endpoint(client, "510k", search)
        logger.info("  510(k): %d records", len(records_510k))

        logger.info("Fetching FDA PMA AI/ML devices via openFDA API...")
        records_pma = await _fetch_all_from_endpoint(client, "pma", search)
        logger.info("  PMA: %d records", len(records_pma))

    # Filter for AI/ML SaMD candidates and normalize
    all_raw = []
    for r in records_510k + records_pma:
        if not _is_aiml_candidate(r):
            continue
        product, entry = _normalize_record(r)
        all_raw.append((product, entry))
    logger.info("After AI/ML keyword filter: %d records", len(all_raw))

    # Deduplicate
    deduped = deduplicate_fda_products(all_raw)

    # Enrich
    enriched = []
    for product, entries in deduped:
        product = enrich_product(product)
        for entry in entries:
            entry.product_id = product.product_id
        product.regulatory_entries = entries
        enriched.append((product, entries))

    logger.info("FDA (openFDA API): %d unique AI/ML products", len(enriched))
    return enriched
