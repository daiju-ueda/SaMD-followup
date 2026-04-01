#!/usr/bin/env python3
"""Run the SaMD Evidence Tracker pipeline.

Usage:
    python3.14 scripts/run_pipeline.py [--local] [--pmda-csv PATH]

Runs FDA ingestion → PMDA ingestion → literature search → scoring.
Results are printed to stdout (DB persistence not yet implemented).
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env manually (avoid dotenv dependency)
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from config.settings import settings
from src.ingestion.fda import fetch_fda_510k_samd, fetch_fda_denovo_samd, fetch_fda_pma_samd, deduplicate_fda_products, parse_fda_aiml_list, _infer_pathway_from_submission_number
from src.ingestion.pmda import load_pmda_csv_file
from src.ingestion.normalizer import enrich_product
from src.linking.deduplicator import deduplicate_papers
from src.linking.scorer import score_and_link, classify_study_type
from src.literature.query_generator import generate_all_queries
from src.literature.pubmed import search_pubmed, fetch_pubmed_details
from src.literature.europe_pmc import search_europe_pmc
from src.literature.openalex import search_openalex
from src.models.product import Product, ProductSearchTerms, RegulatoryEntry

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")


def build_search_terms(product: Product) -> ProductSearchTerms:
    """Build search terms from a product."""
    all_names = [product.canonical_name]
    family_names = []
    manufacturer_names = [product.manufacturer_name]
    regulatory_ids = []

    for alias in product.aliases:
        if alias.alias_type.value == "product_family":
            family_names.append(alias.alias_name)
        elif alias.language == "en" or alias.alias_type.value in ("trade_name", "abbreviation", "former_name"):
            all_names.append(alias.alias_name)

    for mfg_alias in product.manufacturer_aliases:
        manufacturer_names.append(mfg_alias.alias_name)

    for entry in product.regulatory_entries:
        if entry.regulatory_id:
            regulatory_ids.append(entry.regulatory_id)

    disease_kw = [product.disease_area] if product.disease_area else []
    modality_kw = [product.modality] if product.modality else []

    all_names = list(dict.fromkeys(all_names))
    manufacturer_names = list(dict.fromkeys(manufacturer_names))

    return ProductSearchTerms(
        product_id=product.product_id,
        canonical_name=product.canonical_name,
        all_names=all_names,
        family_names=family_names,
        manufacturer_names=manufacturer_names,
        intended_use_keywords=[],
        disease_area_keywords=disease_kw,
        modality_keywords=modality_kw,
        regulatory_ids=regulatory_ids,
    )


def run_fda_aiml_ingestion(csv_path: str) -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Run FDA product ingestion from the official AI/ML-Enabled Devices CSV."""
    import csv

    logger.info("=" * 60)
    logger.info("STEP 1: FDA AI/ML List Ingestion")
    logger.info("=" * 60)

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    raw = parse_fda_aiml_list(rows)
    deduped = deduplicate_fda_products(raw)

    enriched = []
    for product, entries in deduped:
        product = enrich_product(product)
        for entry in entries:
            entry.product_id = product.product_id
        product.regulatory_entries = entries
        enriched.append((product, entries))

    logger.info("FDA AI/ML total: %d unique SaMD products", len(enriched))
    return enriched


def run_pmda_ingestion(csv_path: str) -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Run PMDA product ingestion from CSV."""
    logger.info("=" * 60)
    logger.info("STEP 1b: PMDA Product Ingestion")
    logger.info("=" * 60)

    raw = load_pmda_csv_file(csv_path)
    results = []
    for product, entry, aliases in raw:
        product = enrich_product(product)
        product.aliases = aliases
        entry.product_id = product.product_id
        product.regulatory_entries = [entry]
        results.append((product, [entry]))

    logger.info("PMDA total: %d products", len(results))
    return results


async def search_and_link_product(
    client: httpx.AsyncClient,
    product: Product,
    terms: ProductSearchTerms,
) -> dict:
    """Search literature and link papers for a single product."""
    queries = generate_all_queries(terms)
    all_papers = []

    # Only run Level 1 (exact) and Level 4 (reg ID) for speed in initial run
    priority_queries = [q for q in queries if q.level.value <= 2 or q.level.value == 4]

    for query in priority_queries[:6]:  # cap queries per product
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
            logger.warning("  Query failed (%s): %s", query.source, e)

    # Deduplicate
    unique_papers = deduplicate_papers(all_papers)

    # Score and link
    links = []
    for paper in unique_papers:
        link = score_and_link(paper, terms)
        if link is not None:
            links.append(link)

    links.sort(key=lambda l: l.confidence_score, reverse=True)

    exact = [l for l in links if l.link_classification.value == "exact_product"]
    family = [l for l in links if l.link_classification.value == "product_family"]
    mfg = [l for l in links if l.link_classification.value == "manufacturer_linked"]
    indication = [l for l in links if l.link_classification.value == "indication_related"]
    review_needed = [l for l in links if l.human_review_needed]

    result = {
        "product": product.canonical_name,
        "manufacturer": product.manufacturer_name,
        "disease_area": product.disease_area,
        "modality": product.modality,
        "regulatory_ids": [e.regulatory_id for e in product.regulatory_entries if e.regulatory_id],
        "queries_run": len(priority_queries[:6]),
        "papers_found": len(all_papers),
        "papers_unique": len(unique_papers),
        "links_total": len(links),
        "exact_product": len(exact),
        "product_family": len(family),
        "manufacturer_linked": len(mfg),
        "indication_related": len(indication),
        "review_needed": len(review_needed),
        "top_exact_papers": [
            {
                "title": _get_paper_title(unique_papers, l.paper_id),
                "score": l.confidence_score,
                "terms": l.matched_terms[:5],
            }
            for l in exact[:5]
        ],
    }
    return result


def _get_paper_title(papers, paper_id):
    for p in papers:
        if p.paper_id == paper_id:
            return p.title[:120]
    return "?"


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="SaMD Evidence Tracker Pipeline")
    parser.add_argument("--fda-aiml-csv", default=str(PROJECT_ROOT / "ai-ml-enabled-devices.csv"))
    parser.add_argument("--pmda-csv", default=str(PROJECT_ROOT / "data/seed/pmda_devices.csv"))
    parser.add_argument("--max-products", type=int, default=None, help="Limit products to process")
    parser.add_argument("--skip-fda", action="store_true", help="Skip FDA ingestion")
    parser.add_argument("--skip-pmda", action="store_true", help="Skip PMDA ingestion")
    parser.add_argument("--resume", type=int, default=0, help="Resume from product index (0-based)")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "data" / "pipeline_results.json"))
    args = parser.parse_args()

    logger.info("SaMD Evidence Tracker — Pipeline Start")
    logger.info("NCBI API key: %s...", settings.ncbi_api_key[:8] if settings.ncbi_api_key else "NOT SET")
    logger.info("NCBI email: %s", settings.ncbi_email or "NOT SET")

    all_products: list[tuple[Product, list[RegulatoryEntry]]] = []

    # Ingestion — FDA AI/ML list (CSV-based, no API needed)
    if not args.skip_fda and Path(args.fda_aiml_csv).exists():
        fda_products = run_fda_aiml_ingestion(args.fda_aiml_csv)
        all_products.extend(fda_products)
    elif not args.skip_fda:
        logger.warning("FDA AI/ML CSV not found at %s — skipping FDA", args.fda_aiml_csv)

    if not args.skip_pmda and Path(args.pmda_csv).exists():
        pmda_products = run_pmda_ingestion(args.pmda_csv)
        all_products.extend(pmda_products)

    if args.max_products:
        all_products = all_products[:args.max_products]

    logger.info("=" * 60)
    logger.info("STEP 2: Literature Search & Linking (%d products)", len(all_products))
    logger.info("=" * 60)

    # Load existing results if resuming
    output_path = Path(args.output)
    results = []
    if args.resume > 0 and output_path.exists():
        with open(output_path) as f:
            results = json.load(f)
        logger.info("Loaded %d existing results, resuming from index %d", len(results), args.resume)

    async with httpx.AsyncClient(timeout=60) as client:
        for i, (product, entries) in enumerate(all_products):
            if i < args.resume:
                continue
            logger.info("[%d/%d] %s (%s)",
                        i + 1, len(all_products),
                        product.canonical_name,
                        product.manufacturer_name)
            terms = build_search_terms(product)
            try:
                result = await search_and_link_product(client, product, terms)
            except Exception as e:
                logger.error("  FAILED: %s", e)
                result = {"product": product.canonical_name, "manufacturer": product.manufacturer_name,
                          "error": str(e), "papers_unique": 0, "links_total": 0,
                          "exact_product": 0, "product_family": 0, "manufacturer_linked": 0,
                          "indication_related": 0, "review_needed": 0}
            results.append(result)
            logger.info(
                "  → papers=%d, exact=%d, family=%d, mfg=%d, indication=%d",
                result["papers_unique"],
                result["exact_product"],
                result["product_family"],
                result["manufacturer_linked"],
                result["indication_related"],
            )

            # Save every 50 products
            if (i + 1) % 50 == 0:
                with open(output_path, "w") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False, default=str)
                logger.info("  [checkpoint] Saved %d results to %s", len(results), output_path)

    # Final save
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)

    # Summary
    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE — Summary")
    logger.info("=" * 60)
    total_exact = sum(r.get("exact_product", 0) for r in results)
    total_links = sum(r.get("links_total", 0) for r in results)
    total_papers = sum(r.get("papers_unique", 0) for r in results)
    total_review = sum(r.get("review_needed", 0) for r in results)

    logger.info("Products processed: %d", len(results))
    logger.info("Total unique papers: %d", total_papers)
    logger.info("Total links: %d", total_links)
    logger.info("  exact_product: %d", total_exact)
    logger.info("  review_needed: %d", total_review)
    logger.info("Results written to %s", output_path)

    # Print top products by evidence
    logger.info("")
    logger.info("Top products by exact evidence:")
    for r in sorted(results, key=lambda x: x.get("exact_product", 0), reverse=True)[:20]:
        if r.get("exact_product", 0) > 0:
            logger.info("  %s — exact=%d, total=%d", r["product"], r["exact_product"], r["links_total"])


if __name__ == "__main__":
    asyncio.run(main())
