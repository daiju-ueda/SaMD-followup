#!/usr/bin/env python3
"""Load pipeline results into PostgreSQL.

Uses src.pipeline for ingestion (single source of truth)
and src.db.repositories for DB access.
"""

import json
import logging
import os
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from src.db.connection import get_connection
from src.db.repositories import ProductRepository, PaperRepository
from src.pipeline import ingest_fda_from_csv, ingest_pmda_from_csv
from src.utils import setup_logging

setup_logging()
logger = logging.getLogger("loader")


def load_products(conn, products_with_entries) -> int:
    """Load products and regulatory entries from pipeline output."""
    repo = ProductRepository(conn)
    count = 0
    for product, entries in products_with_entries:
        pid = str(product.product_id)
        repo.insert(
            pid, product.canonical_name, product.manufacturer_name,
            product.intended_use, product.disease_area, product.modality,
        )
        for entry in entries:
            repo.insert_regulatory_entry(
                pid, entry.region.value, entry.regulatory_pathway.value,
                entry.regulatory_status_raw or "", entry.regulatory_status.value,
                entry.regulatory_id,
                entry.clearance_date, entry.device_class,
                entry.product_code, entry.review_panel, entry.applicant,
            )
        for alias in product.aliases:
            repo.insert_alias(
                pid, alias.alias_name, alias.alias_type.value,
                alias.language, alias.source,
            )
        count += 1
    conn.commit()
    return count


def load_literature_results(conn, results_path: Path, label: str) -> tuple[int, int]:
    """Load paper links from pipeline results JSON."""
    prod_repo = ProductRepository(conn)
    paper_repo = PaperRepository(conn)

    results = json.loads(results_path.read_text())
    papers_inserted = links_inserted = 0

    for r in results:
        # Find product in DB
        products, _ = prod_repo.list_products(q=r.get("product", ""), per_page=1)
        if not products:
            continue
        product_id = str(products[0]["product_id"])

        # Update metadata
        prod_repo.update_metadata(product_id, r.get("disease_area"), r.get("modality"))

        for paper_info in r.get("top_exact_papers", []):
            title = paper_info.get("title", "")
            if not title:
                continue
            paper_id = str(uuid.uuid4())
            paper_repo.insert(paper_id, title, f"pipeline_{label}")
            paper_repo.insert_link(
                product_id, paper_id, "exact_product",
                paper_info.get("score", 0),
                paper_info.get("terms"),
                f"From pipeline run ({label})",
            )
            papers_inserted += 1
            links_inserted += 1

    conn.commit()
    return papers_inserted, links_inserted


def main():
    conn = get_connection(autocommit=False)
    logger.info("Connected to database")

    fda_csv = PROJECT_ROOT / "ai-ml-enabled-devices.csv"
    pmda_csv = PROJECT_ROOT / "data" / "seed" / "pmda_devices.csv"
    fda_results = PROJECT_ROOT / "data" / "pipeline_results.json"
    pmda_results = PROJECT_ROOT / "data" / "pmda_results.json"

    # Use the same ingestion logic as the pipeline
    fda_products = ingest_fda_from_csv(fda_csv) if fda_csv.exists() else []
    pmda_products = ingest_pmda_from_csv(pmda_csv) if pmda_csv.exists() else []

    fda_count = load_products(conn, fda_products)
    pmda_count = load_products(conn, pmda_products)

    fda_p = fda_l = pmda_p = pmda_l = 0
    if fda_results.exists():
        fda_p, fda_l = load_literature_results(conn, fda_results, "fda")
    if pmda_results.exists():
        pmda_p, pmda_l = load_literature_results(conn, pmda_results, "pmda")

    # Summary
    from src.db.repositories import StatsRepository
    stats = StatsRepository(conn)
    logger.info("=" * 50)
    logger.info("LOAD COMPLETE")
    logger.info("Products:  %d (FDA=%d, PMDA=%d)", fda_count + pmda_count, fda_count, pmda_count)
    logger.info("Papers:    %d", PaperRepository(conn).count())
    logger.info("Links:     %d", stats.link_count())
    logger.info("Aliases:   %d", stats.alias_count())
    conn.close()


if __name__ == "__main__":
    main()
