#!/usr/bin/env python3
"""CLI entry point for the SaMD pipeline.

Thin wrapper around src.pipeline — all logic lives there.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import src.bootstrap  # noqa: F401,E402
from src.bootstrap import PROJECT_ROOT

from src.config import settings
from src.pipeline import (
    ingest_fda_from_csv,
    ingest_fda_from_web,
    ingest_pmda_from_csv,
    ingest_pmda_from_web,
    merge_products,
    process_product,
)
from src.utils import setup_logging

import httpx
import logging

setup_logging()
logger = logging.getLogger("pipeline")


async def main():
    parser = argparse.ArgumentParser(description="SaMD Evidence Tracker Pipeline")
    parser.add_argument("--fda-csv", default=str(PROJECT_ROOT / "ai-ml-enabled-devices.csv"))
    parser.add_argument("--fda-web", action="store_true", help="Fetch FDA from bulk files (foiclass + PMA + 510k + De Novo)")
    parser.add_argument("--pmda-csv", default=str(PROJECT_ROOT / "data/seed/pmda_devices.csv"))
    parser.add_argument("--pmda-web", action="store_true", help="Fetch PMDA from web instead of CSV")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "data/pipeline_results.json"))
    parser.add_argument("--max-products", type=int, default=None)
    parser.add_argument("--resume", type=int, default=0, help="Resume from index")
    parser.add_argument("--skip-fda", action="store_true")
    parser.add_argument("--skip-pmda", action="store_true")
    args = parser.parse_args()

    logger.info("SaMD Evidence Tracker — Pipeline Start")
    logger.info("NCBI API key: %s...", settings.ncbi_api_key[:8] if settings.ncbi_api_key else "NOT SET")

    # Step 1: Ingest products
    all_products = []
    if not args.skip_fda:
        if args.fda_web:
            all_products.extend(ingest_fda_from_web())
        elif Path(args.fda_csv).exists():
            all_products.extend(ingest_fda_from_csv(args.fda_csv))
    if not args.skip_pmda:
        if args.pmda_web:
            all_products.extend(ingest_pmda_from_web())
        elif Path(args.pmda_csv).exists():
            all_products.extend(ingest_pmda_from_csv(args.pmda_csv))

    # Cross-region deduplication (FDA ↔ PMDA)
    if not args.skip_fda and not args.skip_pmda:
        all_products = merge_products(all_products)

    if args.max_products:
        all_products = all_products[:args.max_products]

    # Load existing results for resume
    output_path = Path(args.output)
    results = []
    if args.resume > 0 and output_path.exists():
        results = json.loads(output_path.read_text())
        logger.info("Resuming from %d, loaded %d existing results", args.resume, len(results))

    # Step 2: Search + Link
    logger.info("Processing %d products (starting at %d)", len(all_products), args.resume)
    async with httpx.AsyncClient(timeout=60) as client:
        for i, (product, entries) in enumerate(all_products):
            if i < args.resume:
                continue
            product.regulatory_entries = entries
            logger.info("[%d/%d] %s (%s)", i + 1, len(all_products),
                        product.canonical_name, product.manufacturer_name)
            try:
                result = await process_product(client, product)
            except Exception as e:
                logger.error("  FAILED: %s", e)
                result = {
                    "product": product.canonical_name,
                    "manufacturer": product.manufacturer_name,
                    "error": str(e),
                    "papers_unique": 0, "links_total": 0,
                    "exact_product": 0, "product_family": 0,
                    "manufacturer_linked": 0, "indication_related": 0,
                    "review_needed": 0,
                }
            results.append(result)
            logger.info("  -> papers=%d, exact=%d, family=%d, mfg=%d, ind=%d",
                        result["papers_unique"], result["exact_product"],
                        result["product_family"], result["manufacturer_linked"],
                        result["indication_related"])

            # Checkpoint every 50
            if (i + 1) % 50 == 0:
                output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
                logger.info("  [checkpoint] %d results saved", len(results))

    # Final save
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))

    # Summary
    total_exact = sum(r.get("exact_product", 0) for r in results)
    total_links = sum(r.get("links_total", 0) for r in results)
    logger.info("=" * 50)
    logger.info("COMPLETE: %d products, %d links, %d exact", len(results), total_links, total_exact)
    logger.info("Results: %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
