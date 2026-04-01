"""OpenAlex search client.

OpenAlex provides a free, open API for scholarly works with good coverage
and citation graph data. Useful for:
- Additional paper discovery beyond PubMed
- Citation-based expansion (cited-by / references)
- Author affiliation data
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from src.config import settings
from src.models.paper import Paper, PaperAuthor

logger = logging.getLogger(__name__)


def _base_params() -> dict[str, str]:
    params: dict[str, str] = {}
    if settings.openalex_email:
        params["mailto"] = settings.openalex_email
    return params


async def search_openalex(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 200,
) -> list[Paper]:
    """Search OpenAlex works by title/abstract text.

    Uses the /works endpoint with the search parameter.
    """
    papers: list[Paper] = []
    per_page = min(50, max_results)
    page = 1

    while len(papers) < max_results:
        params = {
            **_base_params(),
            "search": query,
            "per_page": str(per_page),
            "page": str(page),
            "filter": "language:en,type:article",
            "sort": "relevance_score:desc",
        }
        resp = await client.get(
            f"{settings.openalex_base_url}/works",
            params=params,
            timeout=30,
        )
        if resp.status_code == 404:
            break
        resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])
        if not results:
            break

        for work in results:
            paper = _parse_openalex_work(work)
            if paper:
                papers.append(paper)

        page += 1
        total = data.get("meta", {}).get("count", 0)
        if len(papers) >= total or len(papers) >= max_results:
            break

        await asyncio.sleep(1.0 / settings.openalex_requests_per_second)

    logger.info("OpenAlex search returned %d papers for: %.60s...", len(papers), query)
    return papers[:max_results]


async def fetch_cited_by(
    client: httpx.AsyncClient,
    openalex_id: str,
    max_results: int = 50,
) -> list[Paper]:
    """Fetch papers that cite a given work (for citation graph expansion)."""
    params = {
        **_base_params(),
        "filter": f"cites:{openalex_id}",
        "per_page": str(min(50, max_results)),
        "sort": "cited_by_count:desc",
    }
    resp = await client.get(
        f"{settings.openalex_base_url}/works",
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return [p for w in results if (p := _parse_openalex_work(w))]


def _parse_openalex_work(work: dict[str, Any]) -> Optional[Paper]:
    """Parse an OpenAlex work object into a Paper model."""
    title = work.get("title", "")
    if not title:
        return None

    # DOI
    doi = work.get("doi", "")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")

    # IDs
    openalex_id = work.get("id", "")
    pmid = None
    pmcid = None
    ids = work.get("ids", {})
    if ids.get("pmid"):
        pmid = ids["pmid"].replace("https://pubmed.ncbi.nlm.nih.gov/", "")
    if ids.get("pmcid"):
        pmcid = ids["pmcid"]

    # Publication info
    pub_year = work.get("publication_year")
    pub_date_str = work.get("publication_date")

    # Journal
    source = work.get("primary_location", {}) or {}
    journal_source = source.get("source", {}) or {}
    journal = journal_source.get("display_name")

    # Open access
    oa = work.get("open_access", {}) or {}
    is_oa = oa.get("is_oa", False)

    # Authors
    authors: list[PaperAuthor] = []
    for idx, authorship in enumerate(work.get("authorships", []), 1):
        author_info = authorship.get("author", {}) or {}
        name = author_info.get("display_name", "")
        orcid = author_info.get("orcid", "")
        if orcid and orcid.startswith("https://orcid.org/"):
            orcid = orcid.replace("https://orcid.org/", "")

        # Affiliation — take the first institution
        institutions = authorship.get("institutions", [])
        affiliation = institutions[0].get("display_name", "") if institutions else None

        if name:
            authors.append(PaperAuthor(
                paper_id="00000000-0000-0000-0000-000000000000",
                author_name=name,
                affiliation=affiliation,
                orcid=orcid or None,
                author_position=idx,
            ))

    # Abstract (OpenAlex provides inverted index — reconstruct)
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

    paper = Paper(
        title=title,
        abstract=abstract,
        doi=doi or None,
        pmid=pmid,
        pmcid=pmcid,
        openalex_id=openalex_id or None,
        journal=journal,
        publication_year=pub_year,
        is_open_access=is_oa,
        citation_count=work.get("cited_by_count"),
        source="openalex",
        authors=authors,
        raw_data=work,
    )

    for author in paper.authors:
        author.paper_id = paper.paper_id

    return paper


def _reconstruct_abstract(inverted_index: Optional[dict[str, list[int]]]) -> Optional[str]:
    """Reconstruct abstract from OpenAlex inverted index format."""
    if not inverted_index:
        return None
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)
