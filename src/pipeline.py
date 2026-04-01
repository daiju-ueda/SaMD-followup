"""End-to-end pipeline orchestrator.

Single source of truth for:
1. Product ingestion (FDA CSV / PMDA CSV / FDA API)
2. Product normalization & deduplication
3. Literature search query generation
4. Paper retrieval
5. Paper deduplication
6. Product-paper linking & scoring
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

import httpx

from src.ingestion.fda import deduplicate_fda_products, parse_fda_aiml_list
from src.ingestion.normalizer import enrich_product
from src.ingestion.pmda import load_pmda_csv_file
from src.linking.deduplicator import deduplicate_papers
from src.linking.scorer import classify_study_type, score_and_link
from src.literature.query_generator import generate_all_queries
from src.literature.pubmed import fetch_pubmed_details, search_pubmed
from src.literature.openalex import search_openalex
from src.literature.europe_pmc import search_europe_pmc
from src.models.linking import ProductPaperLink
from src.models.paper import Paper
from src.models.product import Product, ProductSearchTerms, RegulatoryEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Product ingestion
# ---------------------------------------------------------------------------

def ingest_fda_from_csv(csv_path: str | Path) -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Load FDA products from the official AI/ML-Enabled Devices CSV."""
    logger.info("Ingesting FDA AI/ML list from %s", csv_path)
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    raw = parse_fda_aiml_list(rows)
    deduped = deduplicate_fda_products(raw)

    enriched = []
    for product, entries in deduped:
        product = enrich_product(product)
        for entry in entries:
            entry.product_id = product.product_id
        product.regulatory_entries = entries
        enriched.append((product, entries))

    logger.info("FDA: %d unique products", len(enriched))
    return enriched


def ingest_pmda_from_csv(csv_path: str | Path) -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Load PMDA products from curated CSV."""
    logger.info("Ingesting PMDA products from %s", csv_path)
    raw = load_pmda_csv_file(csv_path)
    results = []
    for product, entry, aliases in raw:
        product = enrich_product(product)
        product.aliases = aliases
        entry.product_id = product.product_id
        product.regulatory_entries = [entry]
        results.append((product, [entry]))

    logger.info("PMDA: %d products", len(results))
    return results


# ---------------------------------------------------------------------------
# Search terms
# ---------------------------------------------------------------------------

def build_search_terms(product: Product) -> ProductSearchTerms:
    """Build searchable terms from a Product and its relations."""
    all_names = [product.canonical_name]
    family_names = []
    manufacturer_names = [product.manufacturer_name]
    regulatory_ids = []

    for alias in product.aliases:
        if alias.alias_type.value == "product_family":
            family_names.append(alias.alias_name)
        elif alias.language == "en" or alias.alias_type.value in (
            "trade_name", "abbreviation", "former_name",
        ):
            all_names.append(alias.alias_name)

    for mfg_alias in product.manufacturer_aliases:
        manufacturer_names.append(mfg_alias.alias_name)

    for entry in product.regulatory_entries:
        if entry.regulatory_id:
            regulatory_ids.append(entry.regulatory_id)

    intended_use_kw = []
    if product.intended_use:
        intended_use_kw = [
            w.strip() for w in product.intended_use.split() if len(w.strip()) > 4
        ][:10]

    disease_kw = [product.disease_area] if product.disease_area else []
    modality_kw = [product.modality] if product.modality else []

    return ProductSearchTerms(
        product_id=product.product_id,
        canonical_name=product.canonical_name,
        all_names=list(dict.fromkeys(all_names)),
        family_names=family_names,
        manufacturer_names=list(dict.fromkeys(manufacturer_names)),
        intended_use_keywords=intended_use_kw,
        disease_area_keywords=disease_kw,
        modality_keywords=modality_kw,
        regulatory_ids=regulatory_ids,
    )


# ---------------------------------------------------------------------------
# Literature search
# ---------------------------------------------------------------------------

async def search_papers_for_product(
    client: httpx.AsyncClient,
    terms: ProductSearchTerms,
    max_queries: int = 6,
) -> list[Paper]:
    """Search literature for a product. Returns deduplicated papers."""
    queries = generate_all_queries(terms)
    # Prioritize exact + family + regulatory ID queries
    priority = [q for q in queries if q.level.value <= 2 or q.level.value == 4]
    all_papers: list[Paper] = []

    for query in priority[:max_queries]:
        try:
            if query.source == "pubmed":
                pmids = await search_pubmed(client, query.query_text, max_results=100)
                if pmids:
                    papers = await fetch_pubmed_details(client, pmids[:50])
                    all_papers.extend(papers)
            elif query.source == "europe_pmc":
                papers = await search_europe_pmc(client, query.query_text, max_results=50)
                all_papers.extend(papers)
            elif query.source == "openalex":
                papers = await search_openalex(client, query.query_text, max_results=50)
                all_papers.extend(papers)
        except Exception as e:
            logger.warning("Query failed (%s): %s", query.source, e)

    unique = deduplicate_papers(all_papers)
    return unique


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------

def link_papers_to_product(
    papers: list[Paper],
    terms: ProductSearchTerms,
) -> list[ProductPaperLink]:
    """Score and classify all candidate papers for a product."""
    links = []
    for paper in papers:
        link = score_and_link(paper, terms)
        if link is not None:
            paper.study_tags = classify_study_type(paper)
            links.append(link)
    links.sort(key=lambda l: l.confidence_score, reverse=True)
    return links


# ---------------------------------------------------------------------------
# Per-product pipeline step (search + link)
# ---------------------------------------------------------------------------

async def process_product(
    client: httpx.AsyncClient,
    product: Product,
) -> dict:
    """Run literature search and linking for a single product. Returns summary dict."""
    terms = build_search_terms(product)
    papers = await search_papers_for_product(client, terms)
    links = link_papers_to_product(papers, terms)

    papers_by_id = {str(p.paper_id): p for p in papers}

    by_type: dict[str, int] = {}
    for l in links:
        by_type[l.link_classification.value] = by_type.get(l.link_classification.value, 0) + 1

    # Serialize linked papers with full metadata
    linked_papers = []
    for l in links:
        paper = papers_by_id.get(str(l.paper_id))
        if not paper:
            continue
        linked_papers.append({
            "title": paper.title,
            "doi": paper.doi,
            "pmid": paper.pmid,
            "pmcid": paper.pmcid,
            "openalex_id": paper.openalex_id,
            "journal": paper.journal,
            "publication_year": paper.publication_year,
            "is_open_access": paper.is_open_access,
            "citation_count": paper.citation_count,
            "source": paper.source,
            "authors": [
                {"name": a.author_name, "affiliation": a.affiliation}
                for a in paper.authors[:10]
            ],
            "link_classification": l.link_classification.value,
            "confidence_score": l.confidence_score,
            "matched_terms": l.matched_terms[:10],
            "human_review_needed": l.human_review_needed,
        })

    return {
        "product": product.canonical_name,
        "manufacturer": product.manufacturer_name,
        "disease_area": product.disease_area,
        "modality": product.modality,
        "regulatory_ids": [e.regulatory_id for e in product.regulatory_entries if e.regulatory_id],
        "papers_found": len(papers),
        "papers_unique": len(papers),
        "links_total": len(links),
        "exact_product": by_type.get("exact_product", 0),
        "product_family": by_type.get("product_family", 0),
        "manufacturer_linked": by_type.get("manufacturer_linked", 0),
        "indication_related": by_type.get("indication_related", 0),
        "review_needed": sum(1 for l in links if l.human_review_needed),
        "linked_papers": linked_papers,
    }
