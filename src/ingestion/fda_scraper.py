"""FDA AI/ML device list web scraper.

Downloads the official AI/ML-Enabled Medical Devices CSV directly from
the FDA website, caches the raw file, and returns parsed products.

Source: https://www.fda.gov/medical-devices/software-medical-device-samd/artificial-intelligence-enabled-medical-devices
CSV URL: https://www.fda.gov/media/178541/download?attachment
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.ingestion.fda import deduplicate_fda_products, parse_fda_aiml_list
from src.ingestion.normalizer import enrich_product
from src.models.product import Product, RegulatoryEntry

logger = logging.getLogger(__name__)

FDA_AIML_CSV_URL = "https://www.fda.gov/media/178541/download?attachment"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "fda_raw"


def fetch_fda_aiml_csv() -> str:
    """Download the FDA AI/ML-Enabled Medical Devices CSV from the web.

    Returns the CSV content as a string.
    Caches the raw file under data/fda_raw/.
    """
    logger.info("Downloading FDA AI/ML CSV from %s", FDA_AIML_CSV_URL)
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    })
    # Visit the page first to get cookies
    fda_page = "https://www.fda.gov/medical-devices/software-medical-device-samd/artificial-intelligence-enabled-medical-devices"
    try:
        session.get(fda_page, timeout=30)
    except Exception:
        pass  # OK if this fails, still try direct download

    r = session.get(
        FDA_AIML_CSV_URL,
        headers={"Referer": fda_page},
        timeout=60,
        allow_redirects=True,
    )
    # FDA has aggressive bot detection — may redirect to abuse-detection page
    if r.status_code != 200 or "abuse-detection" in r.url or len(r.content) < 1000:
        raise RuntimeError(
            f"FDA download blocked (status={r.status_code}, url={r.url}). "
            "The FDA website blocks automated downloads. "
            "Download manually from the FDA AI/ML page and place at ai-ml-enabled-devices.csv"
        )

    # The FDA sometimes serves with different encodings
    content = r.content
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = content.decode("utf-8", errors="replace")

    # Cache raw file
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    cache_path = DATA_DIR / f"ai_ml_devices_{ts}.csv"
    cache_path.write_text(text, encoding="utf-8")
    logger.info("FDA CSV cached to %s (%d bytes)", cache_path, len(content))

    return text


def fetch_fda_aiml_products() -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Download and parse the FDA AI/ML device list from the web.

    Same return format as ingest_fda_from_csv() in pipeline.py.
    """
    csv_text = fetch_fda_aiml_csv()
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    logger.info("FDA CSV: %d rows downloaded", len(rows))

    raw = parse_fda_aiml_list(rows)
    deduped = deduplicate_fda_products(raw)

    enriched = []
    for product, entries in deduped:
        product = enrich_product(product)
        for entry in entries:
            entry.product_id = product.product_id
        product.regulatory_entries = entries
        enriched.append((product, entries))

    logger.info("FDA (web): %d unique products", len(enriched))
    return enriched
