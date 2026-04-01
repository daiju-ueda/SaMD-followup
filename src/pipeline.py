"""End-to-end pipeline orchestrator.

Coordinates the full flow:
1. Product ingestion (FDA / PMDA)
2. Product normalization & deduplication
3. Literature search query generation
4. Paper retrieval (API + local)
5. Paper deduplication
6. Product-paper linking & scoring
7. Human review queue population
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

from src.ingestion.fda import (
    deduplicate_fda_products,
    fetch_fda_510k_samd,
    fetch_fda_denovo_samd,
    fetch_fda_pma_samd,
    parse_fda_aiml_list,
)
from src.ingestion.normalizer import enrich_product, find_duplicate
from src.ingestion.pmda import load_pmda_csv_file
from src.linking.deduplicator import deduplicate_papers
from src.linking.scorer import classify_study_type, score_and_link
from src.literature.query_generator import QueryLevel, generate_all_queries
from src.literature.pubmed import fetch_pubmed_details, search_pubmed
from src.literature.openalex import search_openalex
from src.literature.europe_pmc import search_europe_pmc
from src.models.linking import ProductPaperLink
from src.models.paper import Paper
from src.models.product import Product, ProductSearchTerms, RegulatoryEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step 1: Product ingestion
# ---------------------------------------------------------------------------

async def ingest_fda_products() -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Fetch and normalize all FDA SaMD products."""
    async with httpx.AsyncClient() as client:
        # Fetch from all FDA sources in parallel
        results_510k, results_pma, results_denovo = await asyncio.gather(
            fetch_fda_510k_samd(client),
            fetch_fda_pma_samd(client),
            fetch_fda_denovo_samd(client),
        )

    # Combine and deduplicate
    all_raw = results_510k + results_pma + results_denovo
    deduped = deduplicate_fda_products(all_raw)

    # Enrich
    enriched = []
    for product, entries in deduped:
        product = enrich_product(product)
        for entry in entries:
            entry.product_id = product.product_id
        enriched.append((product, entries))

    logger.info("FDA ingestion complete: %d unique products", len(enriched))
    return enriched


def ingest_pmda_products(csv_path: str | Path) -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Load and normalize PMDA products from curated CSV."""
    raw = load_pmda_csv_file(csv_path)
    results = []
    for product, entry, aliases in raw:
        product = enrich_product(product)
        product.aliases = aliases
        entry.product_id = product.product_id
        results.append((product, [entry]))

    logger.info("PMDA ingestion complete: %d products", len(results))
    return results


# ---------------------------------------------------------------------------
# Step 2: Build product search terms
# ---------------------------------------------------------------------------

def build_search_terms(product: Product) -> ProductSearchTerms:
    """Build a ProductSearchTerms object from a Product and its relations."""
    all_names = [product.canonical_name]
    family_names = []
    manufacturer_names = [product.manufacturer_name]
    regulatory_ids = []

    for alias in product.aliases:
        if alias.alias_type.value == "product_family":
            family_names.append(alias.alias_name)
        else:
            all_names.append(alias.alias_name)

    for mfg_alias in product.manufacturer_aliases:
        manufacturer_names.append(mfg_alias.alias_name)

    for entry in product.regulatory_entries:
        if entry.regulatory_id:
            regulatory_ids.append(entry.regulatory_id)

    # Extract keywords from intended_use and disease_area
    intended_use_kw = []
    if product.intended_use:
        # Simple keyword extraction — split on common delimiters
        intended_use_kw = [
            w.strip() for w in product.intended_use.split()
            if len(w.strip()) > 4
        ][:10]

    disease_kw = [product.disease_area] if product.disease_area else []
    modality_kw = [product.modality] if product.modality else []

    # Deduplicate while preserving order
    all_names = list(dict.fromkeys(all_names))
    manufacturer_names = list(dict.fromkeys(manufacturer_names))

    return ProductSearchTerms(
        product_id=product.product_id,
        canonical_name=product.canonical_name,
        all_names=all_names,
        family_names=family_names,
        manufacturer_names=manufacturer_names,
        intended_use_keywords=intended_use_kw,
        disease_area_keywords=disease_kw,
        modality_keywords=modality_kw,
        regulatory_ids=regulatory_ids,
    )


# ---------------------------------------------------------------------------
# Step 3: Literature search
# ---------------------------------------------------------------------------

async def search_papers_for_product(
    terms: ProductSearchTerms,
    use_local: bool = False,
) -> list[Paper]:
    """Execute all search queries for a product and return candidate papers."""
    queries = generate_all_queries(terms)
    all_papers: list[Paper] = []

    async with httpx.AsyncClient() as client:
        for query in queries:
            try:
                if query.source == "pubmed":
                    pmids = await search_pubmed(client, query.query_text, max_results=200)
                    if pmids:
                        papers = await fetch_pubmed_details(client, pmids)
                        all_papers.extend(papers)

                elif query.source == "europe_pmc":
                    papers = await search_europe_pmc(client, query.query_text, max_results=200)
                    all_papers.extend(papers)

                elif query.source == "openalex":
                    papers = await search_openalex(client, query.query_text, max_results=100)
                    all_papers.extend(papers)

            except Exception:
                logger.exception("Query failed: %s (%s)", query.description, query.source)

    # Local data augmentation
    if use_local:
        try:
            from src.literature.local_openalex import search_local_openalex
            local_papers = search_local_openalex(terms.all_names[:5], max_results=100)
            all_papers.extend(local_papers)
        except Exception:
            logger.exception("Local OpenAlex search failed")

    # Deduplicate
    unique_papers = deduplicate_papers(all_papers)
    logger.info(
        "Paper search for %s: %d raw → %d unique",
        terms.canonical_name, len(all_papers), len(unique_papers),
    )
    return unique_papers


# ---------------------------------------------------------------------------
# Step 4: Link and score
# ---------------------------------------------------------------------------

def link_papers_to_product(
    papers: list[Paper],
    terms: ProductSearchTerms,
) -> list[ProductPaperLink]:
    """Score all candidate papers against a product and return valid links."""
    links: list[ProductPaperLink] = []

    for paper in papers:
        link = score_and_link(paper, terms)
        if link is not None:
            # Also classify study type
            study_tags = classify_study_type(paper)
            paper.study_tags = study_tags
            links.append(link)

    # Sort by confidence descending
    links.sort(key=lambda l: l.confidence_score, reverse=True)

    logger.info(
        "Linking for %s: %d papers → %d links (exact=%d, family=%d, mfg=%d, indication=%d, review=%d)",
        terms.canonical_name,
        len(papers),
        len(links),
        sum(1 for l in links if l.link_classification.value == "exact_product"),
        sum(1 for l in links if l.link_classification.value == "product_family"),
        sum(1 for l in links if l.link_classification.value == "manufacturer_linked"),
        sum(1 for l in links if l.link_classification.value == "indication_related"),
        sum(1 for l in links if l.human_review_needed),
    )
    return links


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

async def run_full_pipeline(
    pmda_csv_path: Optional[str] = None,
    use_local_data: bool = False,
) -> dict:
    """Run the complete ingestion → search → linking pipeline.

    Returns a summary dict with counts and status.
    """
    summary = {
        "products_ingested": 0,
        "papers_found": 0,
        "links_created": 0,
        "reviews_needed": 0,
    }

    # 1. Ingest products
    logger.info("=== Step 1: Product ingestion ===")
    all_products: list[tuple[Product, list[RegulatoryEntry]]] = []

    fda_products = await ingest_fda_products()
    all_products.extend(fda_products)

    if pmda_csv_path:
        pmda_products = ingest_pmda_products(pmda_csv_path)
        all_products.extend(pmda_products)

    summary["products_ingested"] = len(all_products)
    logger.info("Total products: %d", len(all_products))

    # 2-4. For each product: build terms → search → link
    logger.info("=== Step 2-4: Literature search & linking ===")
    for product, entries in all_products:
        product.regulatory_entries = entries
        terms = build_search_terms(product)

        papers = await search_papers_for_product(terms, use_local=use_local_data)
        summary["papers_found"] += len(papers)

        links = link_papers_to_product(papers, terms)
        summary["links_created"] += len(links)
        summary["reviews_needed"] += sum(1 for l in links if l.human_review_needed)

        # TODO: persist to database

    logger.info("=== Pipeline complete ===")
    logger.info("Summary: %s", summary)
    return summary
