"""Europe PMC search client.

Europe PMC provides full-text search capability for open-access articles,
making it valuable for finding product name mentions that appear only in
the body text (not title/abstract).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from src.config import settings
from src.models.paper import Paper, PaperAuthor

logger = logging.getLogger(__name__)


async def search_europe_pmc(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 500,
) -> list[Paper]:
    """Search Europe PMC and return parsed Paper objects.

    Europe PMC search supports boolean operators and can search full text
    of open-access articles.
    """
    papers: list[Paper] = []
    page_size = min(100, max_results)
    cursor_mark = "*"

    while len(papers) < max_results:
        params = {
            "query": query,
            "resultType": "core",
            "pageSize": str(page_size),
            "cursorMark": cursor_mark,
            "format": "json",
            "sort": "RELEVANCE",
        }
        resp = await client.get(
            f"{settings.europe_pmc_base_url}/search",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        result_list = data.get("resultList", {}).get("result", [])
        if not result_list:
            break

        for r in result_list:
            paper = _parse_europepmc_result(r)
            if paper:
                papers.append(paper)

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor_mark:
            break
        cursor_mark = next_cursor

        await asyncio.sleep(0.2)  # Europe PMC is generous with rate limits

    logger.info("Europe PMC search returned %d papers for: %.60s...", len(papers), query)
    return papers[:max_results]


def _parse_europepmc_result(r: dict[str, Any]) -> Optional[Paper]:
    """Parse a Europe PMC search result into a Paper model."""
    title = r.get("title", "")
    if not title:
        return None

    # Only English articles
    lang = r.get("language", "eng")
    if lang not in ("eng", "en"):
        return None

    doi = r.get("doi")
    pmid = r.get("pmid")
    pmcid = r.get("pmcid")

    journal = r.get("journalTitle")
    pub_year_str = r.get("pubYear")
    pub_year = int(pub_year_str) if pub_year_str and pub_year_str.isdigit() else None

    is_oa = r.get("isOpenAccess") == "Y"

    abstract = r.get("abstractText")

    # Authors
    authors: list[PaperAuthor] = []
    author_list = r.get("authorList", {}).get("author", [])
    for idx, a in enumerate(author_list, 1):
        full_name = a.get("fullName", "")
        affiliation = a.get("affiliation")
        if full_name:
            authors.append(PaperAuthor(
                paper_id="00000000-0000-0000-0000-000000000000",
                author_name=full_name,
                affiliation=affiliation,
                author_position=idx,
            ))

    # Check if full text is available
    fulltext_available = r.get("hasTextMinedTerms") == "Y" or bool(pmcid)

    paper = Paper(
        title=title,
        abstract=abstract,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        journal=journal,
        publication_year=pub_year,
        volume=r.get("journalVolume"),
        issue=r.get("issue"),
        pages=r.get("pageInfo"),
        language="en",
        is_open_access=is_oa,
        fulltext_available=fulltext_available,
        citation_count=r.get("citedByCount"),
        source="europe_pmc",
        authors=authors,
    )

    for author in paper.authors:
        author.paper_id = paper.paper_id

    return paper
