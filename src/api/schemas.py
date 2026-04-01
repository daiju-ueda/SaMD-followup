"""API response schemas — what the frontend consumes.

These are separate from the internal domain models to allow independent
evolution of the API contract and internal data structures.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Product responses
# ---------------------------------------------------------------------------

class RegulatoryEntryResponse(BaseModel):
    region: str
    pathway: str
    status: str
    regulatory_id: Optional[str] = None
    date: Optional[date] = None
    source_url: Optional[str] = None
    evidence_tier: str = "tier_1"


class EvidenceSummary(BaseModel):
    exact_product: int = 0
    product_family: int = 0
    manufacturer_linked: int = 0
    indication_related: int = 0
    pending_review: int = 0
    evidence_gap: Optional[str] = None


class ProductListItem(BaseModel):
    product_id: str
    canonical_name: str
    manufacturer_name: str
    product_family: Optional[str] = None
    intended_use: Optional[str] = None
    disease_area: Optional[str] = None
    modality: Optional[str] = None
    standalone_samd: bool = True
    regions: list[str] = Field(default_factory=list)
    first_clearance_date: Optional[date] = None
    exact_evidence_count: int = 0
    related_papers_count: int = 0


class ProductDetail(BaseModel):
    product_id: str
    canonical_name: str
    manufacturer_name: str
    product_family: Optional[str] = None
    intended_use: Optional[str] = None
    disease_area: Optional[str] = None
    modality: Optional[str] = None
    standalone_samd: bool = True
    technology_type: Optional[str] = None
    description: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    manufacturer_aliases: list[str] = Field(default_factory=list)
    regulatory_entries: list[RegulatoryEntryResponse] = Field(default_factory=list)
    evidence_summary: EvidenceSummary = Field(default_factory=EvidenceSummary)


# ---------------------------------------------------------------------------
# Paper responses
# ---------------------------------------------------------------------------

class PaperAuthorResponse(BaseModel):
    name: str
    affiliation: Optional[str] = None
    orcid: Optional[str] = None


class PaperResponse(BaseModel):
    paper_id: str
    title: str
    authors: list[PaperAuthorResponse] = Field(default_factory=list)
    journal: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    is_open_access: Optional[bool] = None
    citation_count: Optional[int] = None
    study_tags: list[str] = Field(default_factory=list)

    # Link-specific fields (when returned in product context)
    link_type: Optional[str] = None
    confidence_score: Optional[float] = None
    matched_terms: list[str] = Field(default_factory=list)
    human_reviewed: Optional[bool] = None


class ProductPapersResponse(BaseModel):
    """Papers grouped by link classification for a single product."""
    product_id: str
    product_name: str
    exact_product: list[PaperResponse] = Field(default_factory=list)
    product_family: list[PaperResponse] = Field(default_factory=list)
    manufacturer_linked: list[PaperResponse] = Field(default_factory=list)
    indication_related: list[PaperResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Search / listing
# ---------------------------------------------------------------------------

class PaginatedResponse(BaseModel):
    items: list = Field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = 20
    pages: int = 0


class ProductListResponse(PaginatedResponse):
    items: list[ProductListItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Admin / review
# ---------------------------------------------------------------------------

class ReviewQueueItem(BaseModel):
    link_id: str
    product_name: str
    paper_title: str
    link_classification: str
    confidence_score: float
    matched_terms: list[str] = Field(default_factory=list)
    rationale: Optional[str] = None
    created_at: Optional[datetime] = None


class ReviewDecision(BaseModel):
    """Input schema for a human review decision."""
    status: str  # 'confirmed', 'reclassified', 'rejected'
    new_classification: Optional[str] = None  # if reclassified
    notes: Optional[str] = None


class IngestionStatus(BaseModel):
    source: str
    status: str
    records_fetched: int = 0
    records_created: int = 0
    records_updated: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class SystemStats(BaseModel):
    total_products: int = 0
    products_by_region: dict[str, int] = Field(default_factory=dict)
    total_papers: int = 0
    total_links: int = 0
    links_by_classification: dict[str, int] = Field(default_factory=dict)
    pending_reviews: int = 0
    last_ingestion: Optional[datetime] = None
