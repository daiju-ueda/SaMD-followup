"""PubMed search client using NCBI E-utilities."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Optional

import httpx

from config.settings import settings
from src.models.paper import Paper, PaperAuthor

logger = logging.getLogger(__name__)


def _eutils_url(endpoint: str) -> str:
    return f"{settings.pubmed_base_url}/{endpoint}"


def _base_params() -> dict[str, str]:
    params: dict[str, str] = {"retmode": "xml"}
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key
    if settings.ncbi_email:
        params["email"] = settings.ncbi_email
    return params


async def search_pubmed(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 500,
) -> list[str]:
    """Search PubMed and return a list of PMIDs.

    Uses esearch endpoint with retmax pagination.
    """
    pmids: list[str] = []
    retstart = 0
    retmax = min(200, max_results)

    while retstart < max_results:
        params = {
            **_base_params(),
            "db": "pubmed",
            "term": query,
            "retstart": str(retstart),
            "retmax": str(retmax),
            "sort": "relevance",
        }
        resp = await client.get(_eutils_url("esearch.fcgi"), params=params, timeout=30)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        id_list = root.find("IdList")
        if id_list is None:
            break

        batch = [id_el.text for id_el in id_list.findall("Id") if id_el.text]
        if not batch:
            break

        pmids.extend(batch)
        count_el = root.find("Count")
        total = int(count_el.text) if count_el is not None and count_el.text else 0
        retstart += retmax
        if retstart >= total:
            break

        # Rate limit
        await asyncio.sleep(1.0 / settings.pubmed_requests_per_second)

    logger.info("PubMed search returned %d PMIDs for query: %.80s...", len(pmids), query)
    return pmids[:max_results]


async def fetch_pubmed_details(
    client: httpx.AsyncClient,
    pmids: list[str],
    batch_size: int = 200,
) -> list[Paper]:
    """Fetch full article metadata from PubMed for a list of PMIDs.

    Uses efetch endpoint with XML response.
    """
    papers: list[Paper] = []

    for i in range(0, len(pmids), batch_size):
        batch = pmids[i : i + batch_size]
        params = {
            **_base_params(),
            "db": "pubmed",
            "id": ",".join(batch),
        }
        resp = await client.get(_eutils_url("efetch.fcgi"), params=params, timeout=60)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        for article_el in root.findall(".//PubmedArticle"):
            paper = _parse_pubmed_article(article_el)
            if paper:
                papers.append(paper)

        if i + batch_size < len(pmids):
            await asyncio.sleep(1.0 / settings.pubmed_requests_per_second)

    logger.info("Fetched details for %d papers from PubMed", len(papers))
    return papers


def _text(el: Optional[ET.Element]) -> str:
    """Safe text extraction from an XML element."""
    if el is None:
        return ""
    return (el.text or "").strip()


def _parse_pubmed_article(article_el: ET.Element) -> Optional[Paper]:
    """Parse a PubmedArticle XML element into a Paper model."""
    medline = article_el.find("MedlineCitation")
    if medline is None:
        return None

    pmid_el = medline.find("PMID")
    pmid = _text(pmid_el)

    article = medline.find("Article")
    if article is None:
        return None

    # Title
    title_el = article.find("ArticleTitle")
    title = _text(title_el)
    if not title:
        return None

    # Abstract
    abstract_parts: list[str] = []
    abstract_el = article.find("Abstract")
    if abstract_el is not None:
        for abs_text in abstract_el.findall("AbstractText"):
            label = abs_text.get("Label", "")
            text = abs_text.text or ""
            # Include tail text from child elements
            full_text = "".join(abs_text.itertext()).strip()
            if label:
                abstract_parts.append(f"{label}: {full_text}")
            else:
                abstract_parts.append(full_text)
    abstract = " ".join(abstract_parts) if abstract_parts else None

    # Journal
    journal_el = article.find("Journal")
    journal = ""
    if journal_el is not None:
        journal_title = journal_el.find("Title")
        journal = _text(journal_title)

    # Publication date
    pub_date = None
    pub_year = None
    journal_issue = journal_el.find("JournalIssue") if journal_el is not None else None
    if journal_issue is not None:
        pd = journal_issue.find("PubDate")
        if pd is not None:
            year = _text(pd.find("Year"))
            month = _text(pd.find("Month"))
            if year:
                pub_year = int(year)

    # Volume / Issue
    volume = _text(journal_issue.find("Volume")) if journal_issue is not None else None
    issue = _text(journal_issue.find("Issue")) if journal_issue is not None else None

    # DOI
    doi = None
    for id_el in article.findall("ELocationID"):
        if id_el.get("EIdType") == "doi":
            doi = _text(id_el)
            break
    # Also check PubmedData/ArticleIdList
    pubmed_data = article_el.find("PubmedData")
    if pubmed_data is not None:
        for aid in pubmed_data.findall(".//ArticleId"):
            if aid.get("IdType") == "doi" and not doi:
                doi = _text(aid)
            if aid.get("IdType") == "pmc":
                pmcid = _text(aid)

    # Authors
    authors: list[PaperAuthor] = []
    author_list = article.find("AuthorList")
    if author_list is not None:
        for idx, author_el in enumerate(author_list.findall("Author"), 1):
            last = _text(author_el.find("LastName"))
            first = _text(author_el.find("ForeName"))
            name = f"{last} {first}".strip() if last else first

            # Affiliation
            affil_el = author_el.find("AffiliationInfo/Affiliation")
            affiliation = _text(affil_el) if affil_el is not None else None

            if name:
                authors.append(PaperAuthor(
                    paper_id="00000000-0000-0000-0000-000000000000",  # placeholder, set later
                    author_name=name,
                    affiliation=affiliation,
                    author_position=idx,
                ))

    paper = Paper(
        title=title,
        abstract=abstract,
        doi=doi,
        pmid=pmid,
        journal=journal or None,
        publication_year=pub_year,
        volume=volume or None,
        issue=issue or None,
        language="en",
        source="pubmed",
        authors=authors,
    )

    # Fix author paper_id references
    for author in paper.authors:
        author.paper_id = paper.paper_id

    return paper
