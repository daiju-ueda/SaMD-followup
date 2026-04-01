"""Product-Paper linking models and scoring."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class LinkClassification(str, Enum):
    EXACT_PRODUCT = "exact_product"
    PRODUCT_FAMILY = "product_family"
    MANUFACTURER_LINKED = "manufacturer_linked"
    INDICATION_RELATED = "indication_related"
    IRRELEVANT = "irrelevant"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    RECLASSIFIED = "reclassified"
    REJECTED = "rejected"


class LinkScoreDetail(BaseModel):
    """Feature-level scoring breakdown for a single product-paper link."""
    feature_name: str
    feature_value: float      # 0/1 binary or continuous
    weight: float
    weighted_score: float     # feature_value * weight
    evidence: Optional[str] = None  # matched text snippet


class ProductPaperLink(BaseModel):
    link_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    product_id: uuid.UUID
    paper_id: uuid.UUID
    link_classification: LinkClassification
    confidence_score: float           # 0.0 - 1.0 normalized
    raw_score: Optional[float] = None # unnormalized sum of feature weights
    matched_terms: list[str] = Field(default_factory=list)
    match_locations: Optional[dict] = None  # {"title": [...], "abstract": [...]}
    rationale: Optional[str] = None
    human_review_needed: bool = False
    review_status: ReviewStatus = ReviewStatus.PENDING
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None

    # Populated on detailed read
    score_details: list[LinkScoreDetail] = Field(default_factory=list)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Scoring configuration
# ---------------------------------------------------------------------------

# Default feature weights for scoring. These can be tuned with human review data.
DEFAULT_FEATURE_WEIGHTS: dict[str, float] = {
    "product_name_in_title": 30.0,
    "product_name_in_abstract": 20.0,
    "product_name_in_fulltext": 10.0,
    "product_alias_in_title": 20.0,
    "product_alias_in_abstract": 15.0,
    "product_family_in_title": 12.0,
    "product_family_in_abstract": 10.0,
    "manufacturer_in_author_affiliation": 8.0,
    "manufacturer_in_text": 5.0,
    "intended_use_match": 5.0,
    "disease_area_match": 3.0,
    "modality_match": 3.0,
    "regulatory_id_in_text": 25.0,
    "study_type_clinical": 5.0,
    "study_type_multicenter": 3.0,
}

# Classification thresholds
CLASSIFICATION_THRESHOLDS = {
    "exact_product_min_score": 50.0,
    "product_family_min_score": 30.0,
    "manufacturer_linked_min_score": 20.0,
    "indication_related_min_score": 10.0,
    "human_review_low": 20.0,
    "human_review_high": 50.0,
}
