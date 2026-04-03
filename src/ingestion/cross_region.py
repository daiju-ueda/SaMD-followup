"""Cross-region product deduplication.

Merges products that exist in multiple regulatory regions (e.g., FDA + PMDA)
into a single canonical product with multiple regulatory entries.

Matching criteria:
1. Exact manufacturer name match + fuzzy product name (threshold: 0.8)
2. Known cross-region product mappings (manual overrides)
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Optional

from src.models.product import AliasType, Product, ProductAlias, RegulatoryEntry

logger = logging.getLogger(__name__)

# Manual cross-region mappings: (FDA name, PMDA name) pairs
# These are products known to exist in both markets under different names.
KNOWN_CROSS_REGION = [
    # (us_name_fragment, jp_name_fragment)
    ("AI-Rad Companion", "AI-Rad Companion"),
    ("Apple ECG", "Apple ECG"),
    ("Fitbit ECG", "Fitbit ECG"),
]

NAME_SIMILARITY_THRESHOLD = 0.80


def _normalize(s: str) -> str:
    return s.lower().strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _manufacturer_match(m1: str, m2: str) -> bool:
    """Check if two manufacturer names refer to the same company."""
    n1, n2 = _normalize(m1), _normalize(m2)
    # Exact match
    if n1 == n2:
        return True
    # One contains the other
    if n1 in n2 or n2 in n1:
        return True
    # Fuzzy match
    return _similarity(n1, n2) > 0.7


def merge_cross_region(
    all_products: list[tuple[Product, list[RegulatoryEntry]]],
) -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Merge products that exist in multiple regions into single entries.

    The first occurrence (typically FDA) becomes the canonical product.
    Subsequent matches have their regulatory entries merged in.
    """
    merged: list[tuple[Product, list[RegulatoryEntry]]] = []
    used: set[int] = set()

    for i, (p1, e1) in enumerate(all_products):
        if i in used:
            continue

        # Collect all matches for this product
        combined_entries = list(e1)
        combined_aliases = list(p1.aliases)

        for j, (p2, e2) in enumerate(all_products):
            if j <= i or j in used:
                continue

            # Same region? Skip (intra-region dedup is handled elsewhere)
            if e1[0].region == e2[0].region:
                continue

            # Check if these are the same product
            if _is_same_product(p1, p2):
                logger.info(
                    "Cross-region merge: %s (%s) ← %s (%s)",
                    p1.canonical_name, e1[0].region.value,
                    p2.canonical_name, e2[0].region.value,
                )
                # Merge entries
                for entry in e2:
                    entry.product_id = p1.product_id
                    combined_entries.append(entry)
                # Add the other name as alias
                if p2.canonical_name != p1.canonical_name:
                    combined_aliases.append(ProductAlias(
                        product_id=p1.product_id,
                        alias_name=p2.canonical_name,
                        alias_type=AliasType.TRADE_NAME,
                        language="ja" if e2[0].region.value == "jp" else "en",
                        source="cross_region_merge",
                    ))
                combined_aliases.extend(p2.aliases)
                used.add(j)

        # Enrich the canonical product
        if not p1.disease_area:
            for _, entries in [(p2, e2) for j2, (p2, e2) in enumerate(all_products) if j2 in used]:
                if p2.disease_area:
                    p1.disease_area = p2.disease_area
                    break
        if not p1.modality:
            for _, entries in [(p2, e2) for j2, (p2, e2) in enumerate(all_products) if j2 in used]:
                if p2.modality:
                    p1.modality = p2.modality
                    break

        p1.regulatory_entries = combined_entries
        p1.aliases = combined_aliases
        merged.append((p1, combined_entries))

    n_merged = len(all_products) - len(merged)
    if n_merged > 0:
        logger.info("Cross-region merge: %d products merged (%d → %d)",
                    n_merged, len(all_products), len(merged))
    return merged


def _is_same_product(p1: Product, p2: Product) -> bool:
    """Determine if two products from different regions are the same device."""
    # Known mappings
    for us_frag, jp_frag in KNOWN_CROSS_REGION:
        if (us_frag.lower() in p1.canonical_name.lower() and
                jp_frag.lower() in p2.canonical_name.lower()):
            return True
        if (jp_frag.lower() in p1.canonical_name.lower() and
                us_frag.lower() in p2.canonical_name.lower()):
            return True

    # Same manufacturer + similar product name
    if not _manufacturer_match(p1.manufacturer_name, p2.manufacturer_name):
        return False

    # Product name similarity
    if _similarity(p1.canonical_name, p2.canonical_name) >= NAME_SIMILARITY_THRESHOLD:
        return True

    # Check aliases
    p1_names = {_normalize(p1.canonical_name)} | {_normalize(a.alias_name) for a in p1.aliases}
    p2_names = {_normalize(p2.canonical_name)} | {_normalize(a.alias_name) for a in p2.aliases}

    for n1 in p1_names:
        for n2 in p2_names:
            if _similarity(n1, n2) >= NAME_SIMILARITY_THRESHOLD:
                return True

    return False
