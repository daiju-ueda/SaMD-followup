"""Local PMC full-text reader.

Reads from the local PMC Open Access snapshot at ../../datasets/raw/pmc/extracted/
for full-text matching of product names that may not appear in title/abstract.

PMC XML files contain the full article text which can be used to:
1. Find product name mentions in the body of articles
2. Extract device-related sections (Methods, Discussion)
3. Improve scoring confidence when abstract-only matching is ambiguous
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PMC_EXTRACTED_DIR = Path(__file__).resolve().parents[2] / ".." / "datasets" / "raw" / "pmc" / "extracted"


def extract_fulltext_from_pmc_xml(xml_path: Path) -> Optional[str]:
    """Extract plain text from a PMC JATS XML file.

    Returns concatenated text from title, abstract, and body sections.
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except (ET.ParseError, FileNotFoundError):
        return None

    parts: list[str] = []

    # Title
    for title_el in root.findall(".//article-title"):
        text = "".join(title_el.itertext()).strip()
        if text:
            parts.append(text)

    # Abstract
    for abstract_el in root.findall(".//abstract"):
        text = "".join(abstract_el.itertext()).strip()
        if text:
            parts.append(text)

    # Body
    for body_el in root.findall(".//body"):
        text = "".join(body_el.itertext()).strip()
        if text:
            parts.append(text)

    return " ".join(parts) if parts else None


def search_pmc_fulltext(
    search_terms: list[str],
    pmc_dir: Path = DEFAULT_PMC_EXTRACTED_DIR,
    max_files: int = 10000,
) -> list[tuple[str, str]]:
    """Scan local PMC XML files for search terms in full text.

    Returns list of (pmcid, matched_text_snippet) tuples.

    This is a brute-force approach suitable for targeted product name searches.
    For production, consider building a full-text index.
    """
    terms_lower = [t.lower() for t in search_terms]
    results: list[tuple[str, str]] = []
    files_scanned = 0

    for subdir in ["oa_bulk/oa_comm", "oa_bulk/oa_noncomm", "oa_bulk/oa_other", "manuscript"]:
        dir_path = pmc_dir / subdir
        if not dir_path.exists():
            continue

        for xml_file in dir_path.rglob("*.xml"):
            if files_scanned >= max_files:
                break
            files_scanned += 1

            text = extract_fulltext_from_pmc_xml(xml_file)
            if not text:
                continue

            text_lower = text.lower()
            for term in terms_lower:
                if term in text_lower:
                    # Extract PMCID from filename or XML
                    pmcid = xml_file.stem  # Usually PMC + number
                    # Get a snippet around the match
                    idx = text_lower.index(term)
                    start = max(0, idx - 100)
                    end = min(len(text), idx + len(term) + 100)
                    snippet = text[start:end]
                    results.append((pmcid, snippet))
                    break

    logger.info(
        "PMC full-text scan: %d hits in %d files for terms: %s",
        len(results), files_scanned, search_terms[:3],
    )
    return results
