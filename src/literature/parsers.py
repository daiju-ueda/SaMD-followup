"""Shared parsing utilities for literature modules.

Avoids duplication of abstract reconstruction (OpenAlex) and
JATS XML extraction (PMC) across multiple files.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional


def reconstruct_abstract(inverted_index: Optional[dict[str, list[int]]]) -> Optional[str]:
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return None
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)


def extract_text_from_jats_xml(xml_str: str) -> Optional[str]:
    """Extract plain text from JATS/NLM XML (PMC article format).

    Handles both namespaced and non-namespaced elements.
    """
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return None

    parts: list[str] = []
    for tag in ("article-title", "abstract", "body"):
        # Try with and without namespace
        for el in root.findall(f".//{tag}"):
            text = "".join(el.itertext()).strip()
            if text:
                parts.append(text)
        for el in root.findall(f".//{{{_PMC_NS}}}{tag}"):
            text = "".join(el.itertext()).strip()
            if text:
                parts.append(text)

    if not parts:
        text = "".join(root.itertext()).strip()
        if text and len(text) > 200:
            return text

    return " ".join(parts) if parts else None


_PMC_NS = "http://www.ncbi.nlm.nih.gov/pmc"
