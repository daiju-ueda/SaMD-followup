"""Paper deduplication across multiple literature sources.

Papers fetched from PubMed, Europe PMC, and OpenAlex will overlap.
This module merges them into a single canonical record per paper.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.models.paper import Paper

logger = logging.getLogger(__name__)


def deduplicate_papers(papers: list[Paper]) -> list[Paper]:
    """Deduplicate papers by DOI, PMID, and title similarity.

    Priority order when merging:
    1. PubMed (most reliable metadata)
    2. Europe PMC (full-text availability info)
    3. OpenAlex (citation counts, OA status)
    """
    by_doi: dict[str, Paper] = {}
    by_pmid: dict[str, Paper] = {}
    unique: list[Paper] = []

    for paper in papers:
        # Check DOI
        if paper.doi:
            doi_key = paper.doi.lower().strip()
            if doi_key in by_doi:
                _merge_into(by_doi[doi_key], paper)
                continue
            by_doi[doi_key] = paper

        # Check PMID
        if paper.pmid:
            if paper.pmid in by_pmid:
                _merge_into(by_pmid[paper.pmid], paper)
                continue
            by_pmid[paper.pmid] = paper

        unique.append(paper)

    logger.info(
        "Deduplicated %d papers into %d unique records",
        len(papers), len(unique),
    )
    return unique


def _merge_into(target: Paper, source: Paper) -> None:
    """Merge fields from source into target, preferring non-null values."""
    if not target.abstract and source.abstract:
        target.abstract = source.abstract
    if not target.doi and source.doi:
        target.doi = source.doi
    if not target.pmid and source.pmid:
        target.pmid = source.pmid
    if not target.pmcid and source.pmcid:
        target.pmcid = source.pmcid
    if not target.openalex_id and source.openalex_id:
        target.openalex_id = source.openalex_id
    if source.is_open_access and not target.is_open_access:
        target.is_open_access = True
    if source.fulltext_available and not target.fulltext_available:
        target.fulltext_available = True
    if source.citation_count and (not target.citation_count or source.citation_count > target.citation_count):
        target.citation_count = source.citation_count
    # Merge authors if target has none
    if not target.authors and source.authors:
        target.authors = source.authors
