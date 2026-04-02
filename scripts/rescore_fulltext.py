#!/usr/bin/env python3
"""Re-score product-paper links using stored full text.

Scans papers that have fulltext in DB, checks if product names appear
in the body text, and upgrades links from indication_related/manufacturer_linked
to exact_product when found.

This catches papers where the product name appears only in the body,
not in the title/abstract.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import src.bootstrap  # noqa: F401,E402

import psycopg2.extras

from src.db.connection import get_connection
from src.linking.scorer import _text_contains
from src.utils import setup_logging

setup_logging()
logger = logging.getLogger("rescore")


def main():
    conn = get_connection(autocommit=False)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Get all products with their search names
    cur.execute("""
        SELECT p.product_id, p.canonical_name,
               array_agg(DISTINCT pa.alias_name) FILTER (WHERE pa.alias_name IS NOT NULL) AS aliases
        FROM products p
        LEFT JOIN product_aliases pa ON p.product_id = pa.product_id
        GROUP BY p.product_id
    """)
    products = {str(r["product_id"]): r for r in cur.fetchall()}

    # Get papers with fulltext that have non-exact links
    cur.execute("""
        SELECT ppl.link_id, ppl.product_id, ppl.paper_id,
               ppl.link_classification, ppl.confidence_score,
               pa.title, pa.fulltext, pa.doi
        FROM product_paper_links ppl
        JOIN papers pa ON pa.paper_id = ppl.paper_id
        WHERE pa.fulltext IS NOT NULL
          AND ppl.link_classification != 'exact_product'
    """)
    candidates = cur.fetchall()
    logger.info("Candidates for re-scoring: %d (non-exact links with fulltext)", len(candidates))

    # Also check: papers with fulltext that have NO link to a product
    # (might have been missed because title/abstract didn't match)
    cur.execute("""
        SELECT pa.paper_id, pa.title, pa.fulltext, pa.doi
        FROM papers pa
        WHERE pa.fulltext IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM product_paper_links ppl
              WHERE ppl.paper_id = pa.paper_id
              AND ppl.link_classification = 'exact_product'
          )
    """)
    unlinked = cur.fetchall()
    logger.info("Unlinked papers with fulltext: %d", len(unlinked))

    upgraded = 0
    new_links = 0

    # Part 1: Upgrade existing non-exact links
    for cand in candidates:
        product = products.get(str(cand["product_id"]))
        if not product:
            continue

        names = [product["canonical_name"]]
        if product.get("aliases"):
            names.extend(product["aliases"])

        # Check fulltext for product name
        fulltext = cand["fulltext"]
        for name in names:
            if name and len(name) > 3 and _text_contains(fulltext, name):
                # Upgrade to exact_product
                cur.execute("""
                    UPDATE product_paper_links
                    SET link_classification = 'exact_product',
                        rationale = rationale || ' [upgraded via fulltext: ' || %s || ']',
                        updated_at = NOW()
                    WHERE link_id = %s
                """, (name, str(cand["link_id"])))
                upgraded += 1
                break

    # Part 2: Find new exact links from unlinked papers
    for paper in unlinked:
        fulltext = paper["fulltext"]
        for pid, product in products.items():
            names = [product["canonical_name"]]
            if product.get("aliases"):
                names.extend(product["aliases"])

            for name in names:
                if name and len(name) > 5 and _text_contains(fulltext, name):
                    # Check if link already exists
                    cur.execute("""
                        SELECT 1 FROM product_paper_links
                        WHERE product_id = %s AND paper_id = %s
                    """, (pid, str(paper["paper_id"])))
                    if not cur.fetchone():
                        cur.execute("""
                            INSERT INTO product_paper_links
                                (product_id, paper_id, link_classification,
                                 confidence_score, matched_terms, rationale,
                                 human_review_needed)
                            VALUES (%s, %s, 'exact_product', 0.15, %s,
                                    'Found via fulltext scan', TRUE)
                        """, (pid, str(paper["paper_id"]), [name]))
                        new_links += 1
                    break

    conn.commit()
    conn.close()

    logger.info("=" * 50)
    logger.info("FULLTEXT RE-SCORING COMPLETE")
    logger.info("Upgraded:  %d (non-exact → exact_product)", upgraded)
    logger.info("New links: %d (found in fulltext only, needs review)", new_links)


if __name__ == "__main__":
    main()
