#!/usr/bin/env python3
"""Fetch full text for papers in the database.

Iterates over papers that don't yet have full text and tries to retrieve
from local PMC, Europe PMC, and NCBI PMC OA.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import httpx
import psycopg2.extras

from src.db.connection import get_connection
from src.literature.fulltext import fetch_fulltext
from src.utils import setup_logging

setup_logging()
logger = logging.getLogger("fulltext")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch full text for papers")
    parser.add_argument("--limit", type=int, default=None, help="Max papers to process")
    parser.add_argument("--retry-failed", action="store_true", help="Retry papers that previously failed")
    args = parser.parse_args()

    conn = get_connection(autocommit=False)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Find papers without full text
    if args.retry_failed:
        cur.execute("""
            SELECT paper_id, doi, pmid, pmcid, title
            FROM papers
            WHERE fulltext IS NULL
            ORDER BY doi IS NOT NULL DESC, pmid IS NOT NULL DESC
        """)
    else:
        cur.execute("""
            SELECT paper_id, doi, pmid, pmcid, title
            FROM papers
            WHERE fulltext IS NULL AND fulltext_fetched_at IS NULL
            ORDER BY doi IS NOT NULL DESC, pmid IS NOT NULL DESC
        """)

    papers = cur.fetchall()
    if args.limit:
        papers = papers[:args.limit]

    logger.info("Papers to process: %d", len(papers))

    fetched = 0
    failed = 0
    skipped = 0

    async with httpx.AsyncClient(timeout=60) as client:
        for i, paper in enumerate(papers):
            pid = str(paper["paper_id"])
            doi = paper["doi"]
            pmid = paper["pmid"]
            pmcid = paper["pmcid"]
            title_short = (paper["title"] or "")[:60]

            if not doi and not pmid and not pmcid:
                # No identifiers to search with
                cur.execute("""
                    UPDATE papers SET fulltext_fetched_at = %s WHERE paper_id = %s
                """, (datetime.now(timezone.utc), pid))
                skipped += 1
                continue

            logger.info("[%d/%d] %s (doi=%s, pmid=%s, pmcid=%s)",
                        i + 1, len(papers), title_short,
                        doi or "-", pmid or "-", pmcid or "-")

            text, source = await fetch_fulltext(client, doi=doi, pmid=pmid, pmcid=pmcid)

            now = datetime.now(timezone.utc)
            if text:
                # Truncate extremely long texts (>500KB) to avoid DB bloat
                if len(text) > 500_000:
                    text = text[:500_000]

                cur.execute("""
                    UPDATE papers
                    SET fulltext = %s,
                        fulltext_source = %s,
                        fulltext_available = TRUE,
                        fulltext_fetched_at = %s
                    WHERE paper_id = %s
                """, (text, source, now, pid))
                fetched += 1
                logger.info("  -> %s: %d chars", source, len(text))
            else:
                cur.execute("""
                    UPDATE papers
                    SET fulltext_fetched_at = %s
                    WHERE paper_id = %s
                """, (now, pid))
                failed += 1
                logger.info("  -> no full text available")

            # Commit every 10 papers
            if (i + 1) % 10 == 0:
                conn.commit()
                logger.info("  [checkpoint] %d fetched, %d failed, %d skipped",
                            fetched, failed, skipped)

            # Rate limit: ~1 req/s for external APIs
            await asyncio.sleep(0.5)

    conn.commit()
    conn.close()

    logger.info("=" * 50)
    logger.info("FULLTEXT FETCH COMPLETE")
    logger.info("Fetched:  %d", fetched)
    logger.info("Failed:   %d", failed)
    logger.info("Skipped:  %d (no identifiers)", skipped)


if __name__ == "__main__":
    asyncio.run(main())
