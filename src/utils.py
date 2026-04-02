"""Shared utility functions."""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Optional

# Precompiled patterns
_JP_CHARS = re.compile(r'[\u3000-\u9fff\uf900-\ufaff]')
_LATIN_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9\-\.]{2,}')


def parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse dates from multiple formats: ISO, US slash, compact, Japanese era.

    Handles:
        2024-01-15, 01/15/2024, 20240115,
        令和6年1月15日, 平成30年1月15日
    """
    if not date_str:
        return None
    s = date_str.strip()[:10]

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    # Japanese era: 令和X年Y月Z日
    m = re.match(r"令和(\d+)年(\d+)月(\d+)日", date_str)
    if m:
        return date(2018 + int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = re.match(r"平成(\d+)年(\d+)月(\d+)日", date_str)
    if m:
        return date(1988 + int(m.group(1)), int(m.group(2)), int(m.group(3)))

    return None


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging consistently across all entry points."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def is_japanese(text: str) -> bool:
    """Check if text contains Japanese characters."""
    return bool(_JP_CHARS.search(text))


# Known SaMD product names that don't match structural heuristics
_KNOWN_PRODUCT_NAMES = {
    "nodoca", "fitbit", "garmin", "exocad", "medicad",
}


def extract_latin_from_mixed(text: str) -> Optional[str]:
    """Extract product-name-like Latin tokens from a mixed JP/EN string.

    Only returns tokens that look like proper nouns:
    - Mixed case within word (EndoBRAIN, RayStation)
    - All-uppercase acronyms >= 4 chars (EIRL, QSPECT)
    - Contains digits or special chars (Pinnacle3, syngo.via)
    - Known product name dictionary fallback

    Returns None for common English words (Eclipse, Velocity, Holter).
    """
    text = unicodedata.normalize("NFKC", text)
    tokens = _LATIN_TOKEN.findall(text)
    if not tokens:
        return None

    product_tokens = []
    for t in tokens:
        has_mixed_case = any(c.isupper() for c in t[1:]) and any(c.islower() for c in t)
        is_acronym = t.isupper() and len(t) >= 4
        has_special = "-" in t or "." in t
        has_digit = any(c.isdigit() for c in t)
        if has_mixed_case or is_acronym or has_special or has_digit:
            product_tokens.append(t)

    if product_tokens:
        return " ".join(product_tokens)

    # Fallback: known product names
    for t in tokens:
        if t.lower() in _KNOWN_PRODUCT_NAMES:
            return t

    return None
