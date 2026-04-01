"""Shared utility functions."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional


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
