"""Local OpenAlex data reader.

Reads from the local OpenAlex snapshot at ../../datasets/raw/openalex/data/works/
instead of hitting the API. Each partition is a gzipped JSONL file.

This is much faster for bulk scanning (e.g., building the initial paper corpus)
than making API calls.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from pathlib import Path
from typing import Any, Generator, Optional

from src.literature.parsers import reconstruct_abstract
from src.models.paper import Paper, PaperAuthor

logger = logging.getLogger(__name__)

# Default local path — can be overridden
DEFAULT_OPENALEX_WORKS_DIR = Path(__file__).resolve().parents[2] / ".." / "datasets" / "raw" / "openalex" / "data" / "works"


def iter_openalex_partitions(
    works_dir: Path = DEFAULT_OPENALEX_WORKS_DIR,
) -> Generator[Path, None, None]:
    """Yield paths to all partition .gz files, sorted by date."""
    for date_dir in sorted(works_dir.iterdir()):
        if date_dir.is_dir() and date_dir.name.startswith("updated_date="):
            for gz_file in sorted(date_dir.iterdir()):
                if gz_file.name.endswith(".gz"):
                    yield gz_file


def iter_works_from_file(gz_path: Path) -> Generator[dict[str, Any], None, None]:
    """Read a single gzipped JSONL partition and yield work dicts."""
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def search_local_openalex(
    search_terms: list[str],
    works_dir: Path = DEFAULT_OPENALEX_WORKS_DIR,
    max_results: int = 500,
    min_year: Optional[int] = None,
) -> list[Paper]:
    """Scan local OpenAlex works for papers matching any of the search terms.

    This performs a brute-force scan — suitable for initial corpus building
    but not for real-time queries. For production, build a search index
    (e.g., Elasticsearch or pg_trgm on the papers table).

    Args:
        search_terms: List of terms to match in title/abstract
        works_dir: Path to local OpenAlex works directory
        max_results: Max papers to return
        min_year: Only include papers from this year onwards
    """
    terms_lower = [t.lower() for t in search_terms]
    results: list[Paper] = []

    logger.info(
        "Scanning local OpenAlex for terms: %s (max=%d)",
        search_terms[:5], max_results,
    )

    for gz_path in iter_openalex_partitions(works_dir):
        for work in iter_works_from_file(gz_path):
            # Quick filter by year
            pub_year = work.get("publication_year")
            if min_year and pub_year and pub_year < min_year:
                continue

            # Check title
            title = (work.get("title") or "").lower()

            # Reconstruct abstract for matching
            abstract_idx = work.get("abstract_inverted_index")
            abstract = reconstruct_abstract(abstract_idx).lower() if abstract_idx else ""

            searchable = title + " " + abstract
            if any(term in searchable for term in terms_lower):
                paper = _parse_work(work)
                if paper:
                    results.append(paper)
                    if len(results) >= max_results:
                        return results

    logger.info("Local OpenAlex scan found %d papers", len(results))
    return results



def _parse_work(work: dict[str, Any]) -> Optional[Paper]:
    """Convert an OpenAlex work dict to a Paper model."""
    title = work.get("title", "")
    if not title:
        return None

    # Language filter — English only
    lang = work.get("language")
    if lang and lang != "en":
        return None

    doi = work.get("doi", "")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")

    ids = work.get("ids", {})
    pmid = None
    if ids.get("pmid"):
        pmid = ids["pmid"].replace("https://pubmed.ncbi.nlm.nih.gov/", "")

    oa = work.get("open_access", {}) or {}

    # Authors (first 10 for efficiency)
    authors: list[PaperAuthor] = []
    for idx, authorship in enumerate(work.get("authorships", [])[:10], 1):
        author_info = authorship.get("author", {}) or {}
        name = author_info.get("display_name", "")
        institutions = authorship.get("institutions", [])
        affiliation = institutions[0].get("display_name", "") if institutions else None
        if name:
            authors.append(PaperAuthor(
                paper_id="00000000-0000-0000-0000-000000000000",
                author_name=name,
                affiliation=affiliation,
                author_position=idx,
            ))

    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))

    paper = Paper(
        title=title,
        abstract=abstract or None,
        doi=doi or None,
        pmid=pmid,
        openalex_id=work.get("id"),
        journal=(work.get("primary_location", {}) or {}).get("source", {}).get("display_name") if work.get("primary_location") else None,
        publication_year=work.get("publication_year"),
        is_open_access=oa.get("is_oa", False),
        citation_count=work.get("cited_by_count"),
        source="openalex_local",
        authors=authors,
    )
    for author in paper.authors:
        author.paper_id = paper.paper_id
    return paper
