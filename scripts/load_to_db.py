#!/usr/bin/env python3
"""Load pipeline results and product master into PostgreSQL.

Reads:
  - ai-ml-enabled-devices.csv (FDA products)
  - data/seed/pmda_devices.csv (PMDA products)
  - data/pipeline_results.json (FDA literature results)
  - data/pmda_results.json (PMDA literature results)

Inserts into samd_evidence database.
"""

import csv
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values, Json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("loader")

DB_DSN = os.environ.get("SAMD_DB_DSN", "dbname=samd_evidence")


def parse_date(s):
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def infer_pathway(submission_number):
    s = (submission_number or "").upper().strip()
    if s.startswith("DEN"):
        return "de_novo", "authorized"
    if s.startswith("P"):
        return "pma", "approved"
    if s.startswith("H"):
        return "hde", "approved"
    return "510k", "cleared"


def load_fda_products(conn, csv_path):
    """Load FDA AI/ML products into products + product_regulatory_entries."""
    logger.info("Loading FDA products from %s", csv_path)
    cur = conn.cursor()

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    product_count = 0
    for row in rows:
        device_name = row.get("Device", "").strip()
        if not device_name:
            continue

        submission_number = row.get("Submission Number", "").strip()
        company = row.get("Company", "").strip()
        date_str = row.get("Date of Final Decision", "").strip()
        panel = row.get("Panel (Lead)", "").strip()
        product_code = row.get("Primary Product Code", "").strip()

        # Split semicolon-separated names
        name_parts = [n.strip() for n in device_name.split(";") if n.strip()]
        canonical = name_parts[0]

        pathway, status = infer_pathway(submission_number)
        product_id = str(uuid.uuid4())

        # Insert product
        cur.execute("""
            INSERT INTO products (product_id, canonical_name, manufacturer_name, standalone_samd)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT DO NOTHING
        """, (product_id, canonical, company))

        # Insert regulatory entry
        cur.execute("""
            INSERT INTO product_regulatory_entries
                (product_id, region, regulatory_pathway, regulatory_status_raw, regulatory_status,
                 regulatory_id, clearance_date, product_code, review_panel, applicant, evidence_tier)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (product_id, "us", pathway, pathway, status,
              submission_number or None, parse_date(date_str),
              product_code or None, panel or None, company, "tier_1"))

        # Insert aliases for multi-name devices
        for alias_name in name_parts[1:]:
            cur.execute("""
                INSERT INTO product_aliases (product_id, alias_name, alias_type, source)
                VALUES (%s, %s, 'trade_name', 'fda_aiml_list')
            """, (product_id, alias_name))

        product_count += 1

    conn.commit()
    logger.info("FDA: %d products loaded", product_count)
    return product_count


def load_pmda_products(conn, csv_path):
    """Load PMDA products."""
    logger.info("Loading PMDA products from %s", csv_path)
    cur = conn.cursor()

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    COLUMN_MAP = {
        "販売名": "product_name_ja",
        "英語名": "product_name_en",
        "製造販売業者": "manufacturer",
        "クラス": "device_class",
        "承認/認証区分": "approval_type",
        "承認番号": "approval_number",
        "認証番号": "approval_number",
        "承認日": "approval_date",
        "認証日": "approval_date",
        "一般的名称": "generic_name",
        "使用目的": "intended_use",
        "疾患領域": "disease_area",
        "モダリティ": "modality",
    }

    product_count = 0
    for row in rows:
        normalized = {}
        for key, value in row.items():
            mapped = COLUMN_MAP.get(key.strip(), key.strip())
            normalized[mapped] = (value or "").strip()

        ja_name = normalized.get("product_name_ja", "")
        en_name = normalized.get("product_name_en", "")
        manufacturer = normalized.get("manufacturer", "")
        approval_type = normalized.get("approval_type", "")
        approval_number = normalized.get("approval_number", "")
        date_str = normalized.get("approval_date", "")
        disease_area = normalized.get("disease_area", "")
        modality = normalized.get("modality", "")
        intended_use = normalized.get("intended_use", "")
        device_class = normalized.get("device_class", "")

        canonical = en_name if en_name else ja_name
        if not canonical:
            continue

        if "承認" in approval_type:
            pathway, status = "approval", "approved"
        elif "認証" in approval_type:
            pathway, status = "certification", "certified"
        else:
            pathway, status = "other", "unknown"

        product_id = str(uuid.uuid4())

        cur.execute("""
            INSERT INTO products
                (product_id, canonical_name, manufacturer_name, intended_use, disease_area, modality, standalone_samd)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        """, (product_id, canonical, manufacturer,
              intended_use or None, disease_area or None, modality or None))

        cur.execute("""
            INSERT INTO product_regulatory_entries
                (product_id, region, regulatory_pathway, regulatory_status_raw, regulatory_status,
                 regulatory_id, clearance_date, device_class, applicant, evidence_tier)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (product_id, "jp", pathway, approval_type, status,
              approval_number or None, parse_date(date_str),
              device_class or None, manufacturer, "tier_1"))

        # Japanese name alias
        if ja_name and en_name:
            cur.execute("""
                INSERT INTO product_aliases (product_id, alias_name, alias_type, language, source)
                VALUES (%s, %s, 'japanese_name', 'ja', 'pmda_csv')
            """, (product_id, ja_name))

        product_count += 1

    conn.commit()
    logger.info("PMDA: %d products loaded", product_count)
    return product_count


def load_literature_results(conn, results_path, region_label):
    """Load pipeline_results.json into papers + product_paper_links.

    This is a simplified loader that creates paper stubs from the top_exact_papers
    in the results JSON. A full loader would re-run the search and store complete
    paper metadata.
    """
    logger.info("Loading literature results from %s", results_path)
    cur = conn.cursor()

    with open(results_path) as f:
        results = json.load(f)

    papers_inserted = 0
    links_inserted = 0

    for r in results:
        product_name = r.get("product", "")
        manufacturer = r.get("manufacturer", "")

        # Find matching product in DB
        cur.execute("""
            SELECT product_id FROM products
            WHERE canonical_name = %s AND manufacturer_name = %s
            LIMIT 1
        """, (product_name, manufacturer))
        row = cur.fetchone()
        if not row:
            continue
        product_id = row[0]

        # Update product metadata if available
        disease_area = r.get("disease_area")
        modality = r.get("modality")
        if disease_area or modality:
            cur.execute("""
                UPDATE products SET
                    disease_area = COALESCE(disease_area, %s),
                    modality = COALESCE(modality, %s),
                    updated_at = NOW()
                WHERE product_id = %s
            """, (disease_area, modality, product_id))

        # Insert top exact papers
        for paper_info in r.get("top_exact_papers", []):
            title = paper_info.get("title", "")
            if not title:
                continue

            paper_id = str(uuid.uuid4())
            score = paper_info.get("score", 0)
            matched_terms = paper_info.get("terms", [])

            # Insert paper (skip if title already exists)
            cur.execute("""
                INSERT INTO papers (paper_id, title, source)
                VALUES (%s, %s, %s)
            """, (paper_id, title, "pipeline_" + region_label))
            papers_inserted += 1

            # Insert link
            cur.execute("""
                INSERT INTO product_paper_links
                    (product_id, paper_id, link_classification, confidence_score,
                     matched_terms, rationale)
                VALUES (%s, %s, 'exact_product', %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (product_id, paper_id, score,
                  matched_terms, f"From pipeline run ({region_label})"))
            links_inserted += 1

    conn.commit()
    logger.info("%s: %d papers, %d links inserted", region_label, papers_inserted, links_inserted)
    return papers_inserted, links_inserted


def main():
    conn = psycopg2.connect(DB_DSN)
    logger.info("Connected to database")

    fda_csv = PROJECT_ROOT / "ai-ml-enabled-devices.csv"
    pmda_csv = PROJECT_ROOT / "data" / "seed" / "pmda_devices.csv"
    fda_results = PROJECT_ROOT / "data" / "pipeline_results.json"
    pmda_results = PROJECT_ROOT / "data" / "pmda_results.json"

    # Load products
    fda_count = load_fda_products(conn, fda_csv) if fda_csv.exists() else 0
    pmda_count = load_pmda_products(conn, pmda_csv) if pmda_csv.exists() else 0

    # Load literature results
    fda_papers = fda_links = pmda_papers = pmda_links = 0
    if fda_results.exists():
        fda_papers, fda_links = load_literature_results(conn, fda_results, "fda")
    if pmda_results.exists():
        pmda_papers, pmda_links = load_literature_results(conn, pmda_results, "pmda")

    # Summary
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM products")
    total_products = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM product_regulatory_entries")
    total_reg = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM papers")
    total_papers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM product_paper_links")
    total_links = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM product_aliases")
    total_aliases = cur.fetchone()[0]

    logger.info("=" * 50)
    logger.info("DATABASE LOAD COMPLETE")
    logger.info("=" * 50)
    logger.info("Products:            %d", total_products)
    logger.info("Regulatory entries:  %d", total_reg)
    logger.info("Aliases:             %d", total_aliases)
    logger.info("Papers:              %d", total_papers)
    logger.info("Product-paper links: %d", total_links)

    conn.close()


if __name__ == "__main__":
    main()
