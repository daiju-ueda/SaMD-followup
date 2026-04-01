"""Product normalization — deduplication and enrichment across sources.

This module handles:
1. Fuzzy matching of products across sources (FDA + PMDA + future EU)
2. Manufacturer name normalization
3. Disease area / modality tagging
4. Product family grouping
"""

from __future__ import annotations

import logging
import re
import uuid
from difflib import SequenceMatcher
from typing import Optional

from src.models.product import (
    AliasType,
    ManufacturerAlias,
    Product,
    ProductAlias,
    RegulatoryEntry,
)

logger = logging.getLogger(__name__)

# Similarity threshold for fuzzy product name matching
PRODUCT_NAME_SIMILARITY_THRESHOLD = 0.85
MANUFACTURER_SIMILARITY_THRESHOLD = 0.80


def _normalize_text(text: str) -> str:
    """Lowercase, strip whitespace, remove common noise words."""
    text = text.lower().strip()
    # Remove trademark symbols
    text = re.sub(r"[™®©]", "", text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def _similarity(a: str, b: str) -> float:
    """String similarity ratio using SequenceMatcher."""
    return SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()


# ---- Known manufacturer aliases (global) ------------------------------------
# Maps variant names → canonical name
KNOWN_MANUFACTURER_CANONICAL: dict[str, str] = {
    "digital diagnostics": "Digital Diagnostics Inc.",
    "idx technologies": "Digital Diagnostics Inc.",
    "idx": "Digital Diagnostics Inc.",
    "viz.ai": "Viz.ai Inc.",
    "viz ai": "Viz.ai Inc.",
    "aidoc": "Aidoc Medical Ltd.",
    "arterys": "Tempus Radiology (formerly Arterys)",
    "tempus radiology": "Tempus Radiology (formerly Arterys)",
    "zebra medical": "Zebra Medical Vision",
    "zebra-med": "Zebra Medical Vision",
    "heartflow": "HeartFlow Inc.",
    "caption health": "Caption Health (acquired by GE)",
    "ge healthcare": "GE HealthCare",
    "ge healthineers": "GE HealthCare",
    "siemens healthineers": "Siemens Healthineers AG",
    "philips": "Philips Healthcare",
    "canon medical": "Canon Medical Systems",
    "canon medical systems": "Canon Medical Systems",
    "fujifilm": "Fujifilm Corporation",
}


def normalize_manufacturer_name(name: str) -> str:
    """Normalize a manufacturer name to a canonical form."""
    key = _normalize_text(name)
    for variant, canonical in KNOWN_MANUFACTURER_CANONICAL.items():
        if variant in key or _similarity(key, variant) > MANUFACTURER_SIMILARITY_THRESHOLD:
            return canonical
    # Return original with cleaned-up whitespace
    return name.strip()


# ---- Disease area inference -------------------------------------------------

DISEASE_AREA_PATTERNS: list[tuple[str, str]] = [
    (r"diabetic retinopathy|retinal|fundus|eye", "Ophthalmology - Diabetic Retinopathy"),
    (r"retinal|macular|amd|age.related macular", "Ophthalmology - Retinal Disease"),
    (r"stroke|large vessel occlusion|lvo|cerebr", "Neurology - Stroke"),
    (r"intracranial hemorrhage|ich|brain bleed", "Neurology - Intracranial Hemorrhage"),
    (r"pulmonary embolism|pe detection", "Pulmonology - Pulmonary Embolism"),
    (r"pneumothorax", "Pulmonology - Pneumothorax"),
    (r"lung.?nodule|pulmonary nodule|lung cancer", "Oncology - Lung"),
    (r"breast|mammo|tomosynthesis", "Oncology - Breast"),
    (r"prostate|psa", "Oncology - Prostate"),
    (r"colon|colorectal|polyp", "Oncology - Colorectal"),
    (r"liver|hepatic", "Oncology - Liver"),
    (r"skin|derm|melanoma|lesion", "Dermatology"),
    (r"cardiac|heart|coronary|ecg|ekg|arrhythmia|atrial fibrillation|afib", "Cardiology"),
    (r"fracture|bone|orthop|musculoskeletal|spine", "Orthopedics"),
    (r"pathology|histology|cytology|biopsy", "Pathology"),
    (r"radiology|x.?ray|ct|mri|imaging", "Radiology - General"),
    (r"ultrasound|echo", "Radiology - Ultrasound"),
    (r"sepsis|icu|critical care", "Critical Care"),
    (r"diabetes|glucose|hba1c", "Endocrinology - Diabetes"),
    (r"sleep|apnea", "Sleep Medicine"),
    (r"mental|psychiatr|depress", "Psychiatry"),
]


def infer_disease_area(text: str) -> Optional[str]:
    """Infer disease area from device description / intended use."""
    text_lower = text.lower()
    for pattern, area in DISEASE_AREA_PATTERNS:
        if re.search(pattern, text_lower):
            return area
    return None


# ---- Modality inference -----------------------------------------------------

MODALITY_PATTERNS: list[tuple[str, str]] = [
    (r"fundus|retinal imaging", "Fundus Photography"),
    (r"ct scan|computed tomography", "CT"),
    (r"mri|magnetic resonance", "MRI"),
    (r"x.?ray|radiograph", "X-ray"),
    (r"mammogra|tomosynthesis", "Mammography"),
    (r"ultrasound|echo", "Ultrasound"),
    (r"ecg|ekg|electrocardiogra", "ECG"),
    (r"pathology|histology|whole slide", "Digital Pathology"),
    (r"dermoscop|skin imaging", "Dermoscopy"),
    (r"pet|positron emission", "PET"),
    (r"endoscop", "Endoscopy"),
    (r"oct|optical coherence", "OCT"),
]


def infer_modality(text: str) -> Optional[str]:
    """Infer imaging modality from device description."""
    text_lower = text.lower()
    for pattern, modality in MODALITY_PATTERNS:
        if re.search(pattern, text_lower):
            return modality
    return None


# ---- Product deduplication --------------------------------------------------

def find_duplicate(
    new_product: Product,
    existing_products: list[Product],
) -> Optional[Product]:
    """Find a potential duplicate in existing products.

    Returns the matching existing product, or None.
    """
    new_name = _normalize_text(new_product.canonical_name)
    new_mfg = _normalize_text(new_product.manufacturer_name)

    for existing in existing_products:
        existing_name = _normalize_text(existing.canonical_name)
        existing_mfg = _normalize_text(existing.manufacturer_name)

        name_sim = _similarity(new_name, existing_name)
        mfg_sim = _similarity(new_mfg, existing_mfg)

        # Strong name match + reasonable manufacturer match
        if name_sim >= PRODUCT_NAME_SIMILARITY_THRESHOLD and mfg_sim >= 0.6:
            return existing

        # Check against aliases
        for alias in existing.aliases:
            alias_sim = _similarity(new_name, _normalize_text(alias.alias_name))
            if alias_sim >= PRODUCT_NAME_SIMILARITY_THRESHOLD and mfg_sim >= 0.6:
                return existing

    return None


def enrich_product(product: Product) -> Product:
    """Enrich a product with inferred disease_area, modality, etc."""
    text = " ".join(filter(None, [
        product.canonical_name,
        product.intended_use,
        product.description,
    ]))

    if not product.disease_area and text:
        product.disease_area = infer_disease_area(text)

    if not product.modality and text:
        product.modality = infer_modality(text)

    product.manufacturer_name = normalize_manufacturer_name(product.manufacturer_name)

    return product
