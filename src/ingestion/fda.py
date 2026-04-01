"""FDA data ingestion — 510(k), De Novo, PMA, and AI/ML device list.

Uses the openFDA API as the primary structured source, supplemented by the
FDA-maintained AI/ML-Enabled Medical Devices list.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

import httpx

from config.settings import settings
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

# ---- SaMD-related FDA product codes ----------------------------------------
# These are product codes commonly associated with AI/ML and SaMD devices.
# Not exhaustive — the AI/ML list is the primary filter.
SAMD_PRODUCT_CODES = {
    "QAS",  # Radiological CAD
    "QBS",  # CADe — Computer-aided detection
    "QDQ",  # Radiology decision support
    "QFM",  # ECG analysis software
    "QMT",  # AI/ML-based imaging
    "POK",  # Digital pathology
    "QIH",  # Ophthalmic AI
    "QKQ",  # Stroke triage
    "QJU",  # Cardiac MRI analysis
    "LLZ",  # Clinical decision support
    "QPN",  # AI-based dermatology
    "QRZ",  # Ultrasound AI
}

SAMD_KEYWORDS_IN_DESCRIPTION = [
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


@dataclass
class FDARecord:
    """Raw record from openFDA before normalization."""
    k_number: Optional[str] = None       # 510(k) number
    pma_number: Optional[str] = None
    de_novo_number: Optional[str] = None
    device_name: str = ""
    applicant: str = ""
    decision_date: Optional[str] = None
    product_code: str = ""
    advisory_committee: str = ""
    device_class: str = ""
    statement_or_summary: Optional[str] = None
    raw: dict = field(default_factory=dict)


# ---- openFDA query helpers --------------------------------------------------

def _openfda_url(endpoint: str) -> str:
    return f"{settings.openfda_base_url}/device/{endpoint}.json"


def _build_params(
    search: str,
    limit: int = 100,
    skip: int = 0,
) -> dict[str, str]:
    params: dict[str, str] = {"search": search, "limit": str(limit), "skip": str(skip)}
    if settings.openfda_api_key:
        params["api_key"] = settings.openfda_api_key
    return params


async def _fetch_all(
    client: httpx.AsyncClient,
    endpoint: str,
    search: str,
    max_records: int = 5000,
) -> list[dict[str, Any]]:
    """Page through openFDA results up to *max_records*."""
    all_results: list[dict[str, Any]] = []
    skip = 0
    limit = 100
    while skip < max_records:
        params = _build_params(search, limit=limit, skip=skip)
        resp = await client.get(_openfda_url(endpoint), params=params, timeout=30)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        body = resp.json()
        results = body.get("results", [])
        if not results:
            break
        all_results.extend(results)
        skip += limit
        total = body.get("meta", {}).get("results", {}).get("total", 0)
        if skip >= total:
            break
    return all_results


# ---- SaMD filtering ---------------------------------------------------------

def _is_samd_candidate(record: dict[str, Any]) -> bool:
    """Heuristic: is this openFDA record likely a SaMD?"""
    product_code = record.get("product_code", "")
    if product_code in SAMD_PRODUCT_CODES:
        return True

    description = (
        record.get("device_name", "")
        + " "
        + record.get("statement_or_summary", "")
    ).lower()

    return any(kw in description for kw in SAMD_KEYWORDS_IN_DESCRIPTION)


# ---- Normalization -----------------------------------------------------------

def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(date_str[:10], fmt).date()
        except ValueError:
            continue
    return None


def _pathway_and_status(record: dict[str, Any]) -> tuple[RegulatoryPathway, RegulatoryStatusNormalized, Optional[str]]:
    """Determine regulatory pathway and normalized status."""
    if record.get("k_number"):
        return RegulatoryPathway.K510, RegulatoryStatusNormalized.CLEARED, record["k_number"]
    if record.get("pma_number"):
        return RegulatoryPathway.PMA, RegulatoryStatusNormalized.APPROVED, record["pma_number"]
    # De Novo records from openFDA use the same 510k endpoint but have DEN numbers
    k_num = record.get("k_number", "")
    if k_num and k_num.upper().startswith("DEN"):
        return RegulatoryPathway.DE_NOVO, RegulatoryStatusNormalized.AUTHORIZED, k_num
    return RegulatoryPathway.OTHER, RegulatoryStatusNormalized.UNKNOWN, None


def normalize_fda_record(record: dict[str, Any]) -> tuple[Product, RegulatoryEntry]:
    """Convert a raw openFDA record into a Product + RegulatoryEntry."""
    pathway, status, reg_id = _pathway_and_status(record)
    device_name = record.get("device_name", "").strip()
    applicant = record.get("applicant", "").strip()

    product = Product(
        canonical_name=device_name,
        manufacturer_name=applicant,
        intended_use=record.get("statement_or_summary"),
        standalone_samd=True,
    )

    entry = RegulatoryEntry(
        product_id=product.product_id,
        region=RegionCode.US,
        regulatory_pathway=pathway,
        regulatory_status_raw=record.get("decision_description", ""),
        regulatory_status=status,
        regulatory_id=reg_id,
        clearance_date=_parse_date(record.get("decision_date") or record.get("date_received")),
        device_class=record.get("device_class"),
        product_code=record.get("product_code"),
        review_panel=record.get("advisory_committee_description"),
        applicant=applicant,
        source_url=f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={reg_id}" if reg_id else None,
        evidence_tier=EvidenceTier.TIER_1,
        raw_data=record,
    )
    return product, entry


# ---- Public API --------------------------------------------------------------

async def fetch_fda_510k_samd(client: httpx.AsyncClient) -> list[tuple[Product, RegulatoryEntry]]:
    """Fetch 510(k) cleared SaMD devices from openFDA."""
    # Search by product codes known to be SaMD-related
    code_clause = " OR ".join(f'"{c}"' for c in SAMD_PRODUCT_CODES)
    search = f"product_code:({code_clause})"
    logger.info("Fetching FDA 510(k) SaMD candidates: %s", search)
    records = await _fetch_all(client, "510k", search)
    results = []
    for r in records:
        if _is_samd_candidate(r):
            results.append(normalize_fda_record(r))
    logger.info("FDA 510(k): %d SaMD candidates from %d total records", len(results), len(records))
    return results


async def fetch_fda_pma_samd(client: httpx.AsyncClient) -> list[tuple[Product, RegulatoryEntry]]:
    """Fetch PMA-approved SaMD devices from openFDA."""
    code_clause = " OR ".join(f'"{c}"' for c in SAMD_PRODUCT_CODES)
    search = f"product_code:({code_clause})"
    logger.info("Fetching FDA PMA SaMD candidates: %s", search)
    records = await _fetch_all(client, "pma", search)
    results = []
    for r in records:
        if _is_samd_candidate(r):
            results.append(normalize_fda_record(r))
    logger.info("FDA PMA: %d SaMD candidates from %d total records", len(results), len(records))
    return results


async def fetch_fda_denovo_samd(client: httpx.AsyncClient) -> list[tuple[Product, RegulatoryEntry]]:
    """Fetch De Novo authorized SaMD devices.

    openFDA does not have a dedicated De Novo endpoint as of 2025.
    De Novo devices appear in the 510k endpoint with DEN-prefixed numbers,
    or must be parsed from the FDA De Novo database HTML/PDF.
    """
    # Attempt via 510k endpoint with DEN prefix
    search = 'k_number:"DEN*"'
    logger.info("Fetching FDA De Novo SaMD candidates")
    records = await _fetch_all(client, "510k", search)
    results = []
    for r in records:
        k_num = r.get("k_number", "")
        if k_num.upper().startswith("DEN") and _is_samd_candidate(r):
            results.append(normalize_fda_record(r))
    logger.info("FDA De Novo: %d SaMD candidates", len(results))
    return results


# ---- AI/ML list ingestion (CSV/Excel) ---------------------------------------

def parse_fda_aiml_list(rows: list[dict[str, str]]) -> list[tuple[Product, RegulatoryEntry]]:
    """Parse the FDA AI/ML-Enabled Medical Devices list.

    The FDA publishes this as a table (PDF/Excel) with columns like:
      - Submission Number
      - Device / Trade Name
      - Company
      - Date of Authorization
      - Panel (Advisory Committee)
      - Decision Type (510(k), De Novo, PMA)

    This function expects already-parsed rows as dicts.
    """
    results = []
    for row in rows:
        submission_number = row.get("Submission Number", "").strip()
        device_name = row.get("Device", row.get("Trade Name", "")).strip()
        company = row.get("Company", "").strip()
        date_str = row.get("Date of Authorization", row.get("Date", "")).strip()
        decision_type = row.get("Decision Type", row.get("Type", "")).strip().lower()

        if not device_name:
            continue

        # Determine pathway
        if "de novo" in decision_type:
            pathway = RegulatoryPathway.DE_NOVO
            status = RegulatoryStatusNormalized.AUTHORIZED
        elif "pma" in decision_type:
            pathway = RegulatoryPathway.PMA
            status = RegulatoryStatusNormalized.APPROVED
        else:
            pathway = RegulatoryPathway.K510
            status = RegulatoryStatusNormalized.CLEARED

        product = Product(
            canonical_name=device_name,
            manufacturer_name=company,
            standalone_samd=True,
        )
        entry = RegulatoryEntry(
            product_id=product.product_id,
            region=RegionCode.US,
            regulatory_pathway=pathway,
            regulatory_status_raw=decision_type,
            regulatory_status=status,
            regulatory_id=submission_number or None,
            clearance_date=_parse_date(date_str),
            applicant=company,
            evidence_tier=EvidenceTier.TIER_1,
        )
        results.append((product, entry))
    logger.info("FDA AI/ML list: %d products parsed", len(results))
    return results


# ---- Deduplication across sources -------------------------------------------

def deduplicate_fda_products(
    products: list[tuple[Product, RegulatoryEntry]],
) -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Merge products that share the same regulatory_id or very similar names.

    Returns a list of (canonical Product, list of RegulatoryEntry).
    """
    by_reg_id: dict[str, tuple[Product, list[RegulatoryEntry]]] = {}
    no_id: list[tuple[Product, list[RegulatoryEntry]]] = []

    for product, entry in products:
        reg_id = entry.regulatory_id
        if reg_id and reg_id in by_reg_id:
            # Merge into existing
            existing_product, entries = by_reg_id[reg_id]
            entries.append(entry)
        elif reg_id:
            by_reg_id[reg_id] = (product, [entry])
        else:
            no_id.append((product, [entry]))

    result = list(by_reg_id.values()) + no_id
    logger.info("Deduplicated %d FDA records into %d unique products",
                len(products), len(result))
    return result
