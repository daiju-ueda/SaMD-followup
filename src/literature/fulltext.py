"""Full-text retrieval for papers.

Fetches article full text from multiple sources in priority order:
1. Local PMC XML files (fastest, no network)
2. Europe PMC REST API (OA articles with PMCID or DOI)
3. PubMed Central OA Web Service (fallback)

Stores plain text (HTML/XML tags stripped).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

from src.config import settings
from src.literature.parsers import extract_text_from_jats_xml

logger = logging.getLogger(__name__)

# Local PMC path
LOCAL_PMC_DIR = Path(__file__).resolve().parents[2] / ".." / "datasets" / "raw" / "pmc" / "extracted"



# _extract_text_from_jats_xml and reconstruct_abstract are in src.literature.parsers

# ---------------------------------------------------------------------------
# Source 1: Local PMC XML
# ---------------------------------------------------------------------------

def fetch_from_local_pmc(pmcid: str, pmc_dir: Path = LOCAL_PMC_DIR) -> Optional[str]:
    """Try to find and extract full text from local PMC XML files."""
    if not pmcid or not pmc_dir.exists():
        return None

    # Normalize PMCID (remove "PMC" prefix if present for filename matching)
    pmcid_clean = pmcid.replace("PMC", "")

    # Search in extracted directories
    for subdir in ["oa_bulk/oa_comm", "oa_bulk/oa_noncomm", "oa_bulk/oa_other", "manuscript"]:
        search_dir = pmc_dir / subdir
        if not search_dir.exists():
            continue
        # PMC files are typically named like PMC1234567.xml or similar
        for pattern in [f"PMC{pmcid_clean}.xml", f"*{pmcid_clean}*.xml"]:
            matches = list(search_dir.rglob(pattern))
            if matches:
                try:
                    xml_content = matches[0].read_text(encoding="utf-8")
                    text = extract_text_from_jats_xml(xml_content)
                    if text and len(text) > 100:
                        logger.debug("Local PMC hit: %s -> %d chars", pmcid, len(text))
                        return text
                except Exception as e:
                    logger.warning("Failed to read local PMC %s: %s", matches[0], e)

    return None


# ---------------------------------------------------------------------------
# Source 2: Europe PMC API
# ---------------------------------------------------------------------------

async def fetch_from_europepmc(
    client: httpx.AsyncClient,
    pmcid: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
) -> Optional[str]:
    """Fetch full text from Europe PMC REST API.

    Tries PMCID first, then PMID, then DOI.
    Only works for open-access articles.
    """
    base = settings.europe_pmc_base_url

    # Try by PMCID (most reliable for full text)
    if pmcid:
        pmcid_clean = pmcid.replace("PMC", "")
        # Europe PMC format: /PMC1234567/fullTextXML (no slash between PMC and ID)
        url = f"{base}/PMC{pmcid_clean}/fullTextXML"
        try:
            resp = await client.get(url, timeout=30)
            if resp.status_code == 200:
                text = extract_text_from_jats_xml(resp.text)
                if text and len(text) > 100:
                    logger.debug("Europe PMC XML hit: PMC%s -> %d chars", pmcid_clean, len(text))
                    return text
        except Exception as e:
            logger.debug("Europe PMC XML failed for PMC%s: %s", pmcid_clean, e)

    # Discover PMCID via PMID search, then fetch XML
    if pmid:
        url = f"{base}/search?query=EXT_ID:{pmid}&resultType=core&format=json"
        try:
            resp = await client.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("resultList", {}).get("result", [])
                if results:
                    r = results[0]
                    pmc = r.get("pmcid")
                    if pmc:
                        # Recurse with discovered PMCID
                        return await fetch_from_europepmc(client, pmcid=pmc)
        except Exception:
            pass

    # Try by DOI
    if doi:
        url = f"{base}/search?query=DOI:{doi}&resultType=core&format=json"
        try:
            resp = await client.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("resultList", {}).get("result", [])
                if results:
                    r = results[0]
                    pmc = r.get("pmcid")
                    if pmc:
                        return await fetch_from_europepmc(client, pmcid=pmc)
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# Source 3: NCBI PMC OA Service
# ---------------------------------------------------------------------------

async def fetch_from_pmc_oa(
    client: httpx.AsyncClient,
    pmcid: Optional[str] = None,
) -> Optional[str]:
    """Fetch full text from NCBI PMC Open Access service."""
    if not pmcid:
        return None

    pmcid_clean = pmcid.replace("PMC", "")
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pmc",
        "id": pmcid_clean,
        "rettype": "xml",
    }
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key

    try:
        resp = await client.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            text = extract_text_from_jats_xml(resp.text)
            if text and len(text) > 100:
                logger.debug("PMC OA hit: PMC%s -> %d chars", pmcid_clean, len(text))
                return text
    except Exception as e:
        logger.debug("PMC OA failed for PMC%s: %s", pmcid_clean, e)

    return None


# ---------------------------------------------------------------------------
# Unified fetcher
# ---------------------------------------------------------------------------

async def fetch_fulltext(
    client: httpx.AsyncClient,
    doi: Optional[str] = None,
    pmid: Optional[str] = None,
    pmcid: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Fetch full text from all sources. Returns (text, source_name).

    Priority: local PMC -> Europe PMC -> PMC OA
    """
    # 1. Local PMC
    if pmcid:
        text = fetch_from_local_pmc(pmcid)
        if text:
            return text, "local_pmc"

    # 2. Europe PMC
    text = await fetch_from_europepmc(client, pmcid=pmcid, pmid=pmid, doi=doi)
    if text:
        return text, "europe_pmc"

    # 3. PMC OA (if we have or discovered a PMCID)
    if pmcid:
        text = await fetch_from_pmc_oa(client, pmcid=pmcid)
        if text:
            return text, "pmc_oa"

    return None, None
