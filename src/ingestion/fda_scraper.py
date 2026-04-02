"""FDA SaMD product ingestion via accessdata.fda.gov bulk files.

Uses official FDA FTP-area bulk data (no bot detection, no API rate limits):
- foiclass.zip: Product Classification → derive SaMD product codes
- pma.zip: PMA approvals (pipe-delimited)
- pmnlstmn.zip: 510(k) clearances (pipe-delimited)
- De Novo: HTML scraping of De Novo search/detail pages

Based on FDA SaMD Monitor script.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.ingestion.fda import SAMD_KEYWORDS, deduplicate_fda_products, infer_pathway
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

UA = "Mozilla/5.0 (compatible; SaMD-Evidence-Tracker/1.0)"

FOICLASS_ZIP_URL = "https://www.accessdata.fda.gov/premarket/ftparea/foiclass.zip"
PMA_ZIP_URL = "https://www.accessdata.fda.gov/premarket/ftparea/pma.zip"
PMNLSTMN_ZIP_URL = "https://www.accessdata.fda.gov/premarket/ftparea/pmnlstmn.zip"
DENOVO_SEARCH_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/denovo.cfm"

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "fda_raw"
STATE_DIR = DATA_DIR / "state"

# Keywords to identify SaMD product codes from foiclass
SOFTWARE_KEYWORDS = [
    "software", "software as a medical device",
    "mobile medical application", "mobile medical app",
    "artificial intelligence", "machine learning", "algorithm",
    "computer-aided", "computer assisted", "decision support",
    "image processing", "analysis software", "triage software",
    "notification software", "cloud", "app", "application", "program",
]

# Manual allowlist for product codes known to be SaMD/AI
MANUAL_PRODUCT_CODE_ALLOWLIST: set[str] = set()

TIMEOUT = 60


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def _normalize(x: object) -> str:
    if x is None:
        return ""
    s = str(x).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _parse_mmddyyyy(s: str) -> Optional[date]:
    s = _normalize(s)
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_zip_first_member(data: bytes, encoding: str = "latin-1") -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [n for n in zf.namelist() if not n.endswith("/")]
        if not members:
            raise RuntimeError("zip is empty")
        with zf.open(members[0]) as f:
            return f.read().decode(encoding, errors="replace")


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _save_raw(name: str, data: bytes, suffix: str = ".zip") -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = DATA_DIR / f"{name}_{ts}{suffix}"
    path.write_bytes(data)
    return path


def _load_hash(name: str) -> Optional[str]:
    p = STATE_DIR / f"{name}.sha256"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _save_hash(name: str, digest: str) -> None:
    (STATE_DIR / f"{name}.sha256").write_text(digest, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Product Classification (foiclass.zip) → SaMD product codes
# ---------------------------------------------------------------------------

FOICLASS_COLUMNS = [
    "review_panel", "medical_specialty", "product_code", "device_name",
    "device_class", "unclassified_reason_code", "gmp_exempt_flag",
    "third_party_review_eligible", "third_party_review_code",
    "regulation_number", "submission_type_id", "definition",
    "physical_state", "technical_method", "target_area", "implant_flag",
    "life_sustain_support_flag", "summary_malfunction_reporting",
]


def _parse_foiclass(data: bytes) -> pd.DataFrame:
    text = _read_zip_first_member(data, encoding="latin-1")
    rows = []
    for line in text.splitlines():
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < len(FOICLASS_COLUMNS):
            parts += [""] * (len(FOICLASS_COLUMNS) - len(parts))
        rows.append(parts[:len(FOICLASS_COLUMNS)])
    df = pd.DataFrame(rows, columns=FOICLASS_COLUMNS)
    for col in df.columns:
        df.loc[:, col] = df[col].map(_normalize)
    return df


def derive_samd_product_codes(foiclass_df: pd.DataFrame) -> list[str]:
    """Extract product codes likely to contain SaMD devices."""
    text_cols = ["device_name", "definition", "technical_method", "target_area"]
    pattern = re.compile("|".join(re.escape(k) for k in SOFTWARE_KEYWORDS), re.IGNORECASE)

    mask = pd.Series(False, index=foiclass_df.index)
    for col in text_cols:
        mask |= foiclass_df[col].fillna("").str.contains(pattern, na=False)

    mask |= foiclass_df["product_code"].isin(MANUAL_PRODUCT_CODE_ALLOWLIST)
    codes = foiclass_df.loc[mask, "product_code"].dropna().unique().tolist()
    logger.info("Derived %d SaMD product codes from foiclass", len(codes))
    return sorted(codes)


# ---------------------------------------------------------------------------
# 2. PMA (pma.zip)
# ---------------------------------------------------------------------------

PMA_COLUMNS = [
    "pma_number", "supplement_number", "applicant", "street_1", "street_2",
    "city", "state", "zip", "zip_ext", "generic_name", "trade_name",
    "product_code", "advisory_committee", "supplement_type", "supplement_reason",
    "expedited_review_granted", "date_received", "date_decision", "docket_number",
    "date_federal_register_notice", "decision_code", "approval_order_statement",
]


def _parse_pma(data: bytes) -> pd.DataFrame:
    text = _read_zip_first_member(data, encoding="latin-1")
    rows = []
    for line in text.splitlines():
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < len(PMA_COLUMNS):
            parts += [""] * (len(PMA_COLUMNS) - len(parts))
        rows.append(parts[:len(PMA_COLUMNS)])
    df = pd.DataFrame(rows, columns=PMA_COLUMNS)
    for col in df.columns:
        df.loc[:, col] = df[col].map(_normalize)
    df.loc[:, "decision_date"] = df["date_decision"].map(_parse_mmddyyyy)
    return df


def _pma_to_products(
    df: pd.DataFrame,
    samd_codes: list[str],
) -> list[tuple[Product, RegulatoryEntry]]:
    hits = df[df["product_code"].isin(samd_codes)].copy()
    results = []
    for _, row in hits.iterrows():
        trade_name = row.get("trade_name", "")
        if not trade_name:
            continue
        product = Product(
            canonical_name=trade_name,
            manufacturer_name=row.get("applicant", ""),
            standalone_samd=True,
        )
        entry = RegulatoryEntry(
            product_id=product.product_id,
            region=RegionCode.US,
            regulatory_pathway=RegulatoryPathway.PMA,
            regulatory_status_raw="approved",
            regulatory_status=RegulatoryStatusNormalized.APPROVED,
            regulatory_id=row.get("pma_number"),
            clearance_date=row.get("decision_date"),
            product_code=row.get("product_code"),
            review_panel=row.get("advisory_committee"),
            applicant=row.get("applicant"),
            evidence_tier=EvidenceTier.TIER_1,
        )
        results.append((product, entry))
    logger.info("PMA: %d SaMD products", len(results))
    return results


# ---------------------------------------------------------------------------
# 3. 510(k) (pmnlstmn.zip) — pipe-delimited
# ---------------------------------------------------------------------------

def _parse_510k(data: bytes) -> pd.DataFrame:
    """Parse pmnlstmn.zip — uses header row for column names."""
    text = _read_zip_first_member(data, encoding="latin-1")
    lines = [l.rstrip("\r\n") for l in text.splitlines() if l.strip()]
    if not lines:
        return pd.DataFrame()

    # First line is header
    header = [h.strip().lower() for h in lines[0].split("|")]
    rows = []
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) < len(header):
            parts += [""] * (len(header) - len(parts))
        rows.append(parts[:len(header)])

    df = pd.DataFrame(rows, columns=header)
    for col in df.columns:
        df.loc[:, col] = df[col].map(_normalize)

    # Normalize column names to match our code
    col_map = {
        "knumber": "k_number", "applicant": "applicant",
        "devicename": "device_name", "productcode": "product_code",
        "decisiondate": "decision_date", "decision": "decision_description",
        "datereceived": "date_received", "stateorsumm": "statement_or_summary",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "decision_date" in df.columns:
        df["decision_date_parsed"] = df["decision_date"].map(_parse_mmddyyyy)
    return df


def _510k_to_products(
    df: pd.DataFrame,
    samd_codes: list[str],
) -> list[tuple[Product, RegulatoryEntry]]:
    if "product_code" not in df.columns:
        logger.warning("510(k) data missing product_code column")
        return []

    hits = df[df["product_code"].isin(samd_codes)].copy()
    results = []
    for _, row in hits.iterrows():
        device_name = str(row.get("device_name", "")).strip()
        if not device_name:
            continue

        name_parts = [n.strip() for n in device_name.split(";") if n.strip()]
        canonical = name_parts[0]
        aliases = [
            ProductAlias(
                product_id="00000000-0000-0000-0000-000000000000",
                alias_name=n, alias_type=AliasType.TRADE_NAME, source="fda_510k_zip",
            )
            for n in name_parts[1:]
        ]

        k_number = str(row.get("k_number", ""))
        if k_number.upper().startswith("DEN"):
            pathway = RegulatoryPathway.DE_NOVO
            status = RegulatoryStatusNormalized.AUTHORIZED
        else:
            pathway = RegulatoryPathway.K510
            status = RegulatoryStatusNormalized.CLEARED

        product = Product(
            canonical_name=canonical,
            manufacturer_name=str(row.get("applicant", "")),
            standalone_samd=True,
            aliases=aliases,
        )
        for a in product.aliases:
            a.product_id = product.product_id

        entry = RegulatoryEntry(
            product_id=product.product_id,
            region=RegionCode.US,
            regulatory_pathway=pathway,
            regulatory_status_raw=str(row.get("decision_description", "")),
            regulatory_status=status,
            regulatory_id=k_number or None,
            clearance_date=row.get("decision_date_parsed"),
            product_code=str(row.get("product_code", "")),
            applicant=str(row.get("applicant", "")),
            evidence_tier=EvidenceTier.TIER_1,
        )
        results.append((product, entry))
    logger.info("510(k): %d SaMD products", len(results))
    return results


# ---------------------------------------------------------------------------
# 4. De Novo (HTML scraping)
# ---------------------------------------------------------------------------

@dataclass
class _DeNovoRecord:
    denovo_number: str
    device_name: str
    requester: str
    product_code: str
    decision_date: str
    decision: str


_LABEL_PATTERNS = {
    "denovo_number": r"De Novo Number\s+([A-Z0-9]+)",
    "device_name": r"Device Name\s+(.+?)\s+Requester",
    "requester": r"Requester\s+(.+?)\s+Contact",
    "product_code": r"Classification Product Code\s+([A-Z0-9]+)",
    "decision_date": r"Decision Date\s+([0-9/]+)",
    "decision": r"Decision\s+(.+?)\s+(?:Classification Advisory|Review Advisory|Page Last)",
}



def _fetch_denovo_detail(denovo_id: str) -> Optional[_DeNovoRecord]:
    try:
        r = _session().get(DENOVO_SEARCH_URL, params={"id": denovo_id}, timeout=TIMEOUT)
        r.raise_for_status()
        text = _normalize(BeautifulSoup(r.text, "html.parser").get_text("\n", strip=True))
        values: dict[str, str] = {}
        for key, pattern in _LABEL_PATTERNS.items():
            m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            values[key] = _normalize(m.group(1)) if m else ""
        return _DeNovoRecord(
            denovo_number=values.get("denovo_number") or denovo_id,
            device_name=values.get("device_name", ""),
            requester=values.get("requester", ""),
            product_code=values.get("product_code", ""),
            decision_date=values.get("decision_date", ""),
            decision=values.get("decision", ""),
        )
    except Exception as e:
        logger.warning("De Novo detail failed for %s: %s", denovo_id, e)
        return None


def _denovo_to_products(
    samd_codes: list[str],
) -> list[tuple[Product, RegulatoryEntry]]:
    """Fetch De Novo records from FDA De Novo database (HTML scraping).

    Uses a single search to get all De Novo IDs, then fetches details
    with rate limiting (1 req/sec) to avoid 403.
    """
    import time

    # Step 1: Get all De Novo IDs in one search (no product code filter)
    sess = _session()
    params = {
        "sortcolumn": "decisiondatedesc",
        "start_search": "1",
        "pagenum": "500",
    }
    all_ids: set[str] = set()
    try:
        r = sess.get(DENOVO_SEARCH_URL, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select('a[href*="denovo.cfm?id="], a[href*="denovo.cfm?ID="]'):
            href = a.get("href", "")
            m = re.search(r"id=([A-Za-z0-9]+)", href, flags=re.IGNORECASE)
            if m:
                den = m.group(1).upper()
                if den.startswith("DEN"):
                    all_ids.add(den)
    except Exception:
        logger.exception("De Novo search failed")
        return []

    logger.info("De Novo: %d IDs found from search", len(all_ids))

    # Step 2: Fetch detail pages (rate limited)
    results = []
    sw_pattern = re.compile(
        "|".join(re.escape(k) for k in SOFTWARE_KEYWORDS), re.IGNORECASE,
    )
    for i, den_id in enumerate(sorted(all_ids)):
        rec = _fetch_denovo_detail(den_id)
        if not rec or not rec.device_name:
            if i < len(all_ids) - 1:
                time.sleep(1.0)
            continue

        # Filter: product code in SaMD codes OR device name matches software keywords
        if rec.product_code not in samd_codes and not sw_pattern.search(rec.device_name):
            if i < len(all_ids) - 1:
                time.sleep(1.0)
            continue

        product = Product(
            canonical_name=rec.device_name,
            manufacturer_name=rec.requester,
            standalone_samd=True,
        )
        entry = RegulatoryEntry(
            product_id=product.product_id,
            region=RegionCode.US,
            regulatory_pathway=RegulatoryPathway.DE_NOVO,
            regulatory_status_raw=rec.decision,
            regulatory_status=RegulatoryStatusNormalized.AUTHORIZED,
            regulatory_id=rec.denovo_number,
            clearance_date=_parse_mmddyyyy(rec.decision_date),
            product_code=rec.product_code,
            applicant=rec.requester,
            evidence_tier=EvidenceTier.TIER_1,
        )
        results.append((product, entry))

        if i < len(all_ids) - 1:
            time.sleep(1.0)

    logger.info("De Novo: %d SaMD products", len(results))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fda_samd_products() -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Fetch all FDA SaMD products from bulk data files.

    Steps:
    1. Download foiclass.zip → derive SaMD product codes
    2. Download pma.zip → filter by SaMD codes
    3. Download pmnlstmn.zip → filter by SaMD codes (510k + De Novo in bulk)
    4. Scrape De Novo search pages for additional records
    5. Deduplicate and enrich
    """
    _ensure_dirs()
    sess = _session()

    # 1. Product Classification → SaMD codes
    logger.info("Downloading foiclass.zip...")
    r = sess.get(FOICLASS_ZIP_URL, timeout=TIMEOUT)
    r.raise_for_status()
    foiclass_data = r.content
    _save_raw("foiclass", foiclass_data)

    foiclass_df = _parse_foiclass(foiclass_data)
    samd_codes = derive_samd_product_codes(foiclass_df)

    # 2. PMA
    logger.info("Downloading pma.zip...")
    r = sess.get(PMA_ZIP_URL, timeout=TIMEOUT)
    r.raise_for_status()
    _save_raw("pma", r.content)
    pma_df = _parse_pma(r.content)
    pma_products = _pma_to_products(pma_df, samd_codes)

    # 3. 510(k)
    logger.info("Downloading pmnlstmn.zip...")
    r = sess.get(PMNLSTMN_ZIP_URL, timeout=TIMEOUT)
    r.raise_for_status()
    _save_raw("pmnlstmn", r.content)
    k510_df = _parse_510k(r.content)
    k510_products = _510k_to_products(k510_df, samd_codes)

    # 4. De Novo (single search + detail pages with rate limiting)
    denovo_products = _denovo_to_products(samd_codes)

    # 5. Combine, deduplicate, enrich
    all_raw = pma_products + k510_products + denovo_products
    logger.info("FDA raw total: %d (PMA=%d, 510k=%d, De Novo=%d)",
                len(all_raw), len(pma_products), len(k510_products), len(denovo_products))

    deduped = deduplicate_fda_products(all_raw)
    enriched = []
    for product, entries in deduped:
        product = enrich_product(product)
        for entry in entries:
            entry.product_id = product.product_id
        product.regulatory_entries = entries
        enriched.append((product, entries))

    logger.info("FDA (bulk files): %d unique SaMD products", len(enriched))
    return enriched
