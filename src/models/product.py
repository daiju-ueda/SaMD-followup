"""Product domain models — Pydantic schemas for the product master."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RegionCode(str, Enum):
    US = "us"
    JP = "jp"
    EU = "eu"


class RegulatoryPathway(str, Enum):
    K510 = "510k"
    DE_NOVO = "de_novo"
    PMA = "pma"
    HDE = "hde"
    APPROVAL = "approval"          # JP 承認
    CERTIFICATION = "certification" # JP 認証
    NOTIFICATION = "notification"   # JP 届出
    CE_MDD = "ce_mdd"
    CE_MDR = "ce_mdr"
    CE_IVDR = "ce_ivdr"
    OTHER = "other"


class RegulatoryStatusNormalized(str, Enum):
    CLEARED = "cleared"
    AUTHORIZED = "authorized"
    APPROVED = "approved"
    CERTIFIED = "certified"
    CE_MARKED = "ce_marked"
    CE_MARKED_LEGACY = "ce_marked_legacy"
    WITHDRAWN = "withdrawn"
    SUSPENDED = "suspended"
    PENDING = "pending"
    UNKNOWN = "unknown"


class AliasType(str, Enum):
    TRADE_NAME = "trade_name"
    PRODUCT_FAMILY = "product_family"
    FORMER_NAME = "former_name"
    ABBREVIATION = "abbreviation"
    REGULATORY_NAME = "regulatory_name"
    GENERIC_NAME = "generic_name"
    JAPANESE_NAME = "japanese_name"
    SEARCH_TERM = "search_term"


class EvidenceTier(str, Enum):
    TIER_1 = "tier_1"  # Official government database
    TIER_2 = "tier_2"  # Verified third-party
    TIER_3 = "tier_3"  # Manufacturer self-reported
    TIER_4 = "tier_4"  # Secondary source


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class ProductAlias(BaseModel):
    alias_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    product_id: uuid.UUID
    alias_name: str
    alias_type: AliasType
    language: str = "en"
    is_primary: bool = False
    source: Optional[str] = None


class ManufacturerAlias(BaseModel):
    alias_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    product_id: uuid.UUID
    alias_name: str
    is_former_name: bool = False
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None


class RegulatoryEntry(BaseModel):
    entry_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    product_id: uuid.UUID
    region: RegionCode
    country: Optional[str] = None
    regulatory_pathway: RegulatoryPathway
    regulatory_status_raw: Optional[str] = None
    regulatory_status: RegulatoryStatusNormalized
    regulatory_id: Optional[str] = None
    clearance_date: Optional[date] = None
    expiration_date: Optional[date] = None
    device_class: Optional[str] = None
    product_code: Optional[str] = None
    review_panel: Optional[str] = None
    applicant: Optional[str] = None
    source_url: Optional[str] = None
    source_document: Optional[str] = None
    evidence_tier: EvidenceTier = EvidenceTier.TIER_1
    raw_data: Optional[dict] = None


class Product(BaseModel):
    product_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    canonical_name: str
    manufacturer_name: str
    product_family: Optional[str] = None
    intended_use: Optional[str] = None
    disease_area: Optional[str] = None
    modality: Optional[str] = None
    standalone_samd: bool = True
    technology_type: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None

    # Populated on read
    aliases: list[ProductAlias] = Field(default_factory=list)
    manufacturer_aliases: list[ManufacturerAlias] = Field(default_factory=list)
    regulatory_entries: list[RegulatoryEntry] = Field(default_factory=list)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Search / query helpers
# ---------------------------------------------------------------------------

class ProductSearchTerms(BaseModel):
    """All searchable terms for a product, used to generate literature queries."""
    product_id: uuid.UUID
    canonical_name: str
    all_names: list[str]           # canonical + all aliases
    family_names: list[str]        # product family aliases
    manufacturer_names: list[str]  # manufacturer + manufacturer aliases
    intended_use_keywords: list[str]
    disease_area_keywords: list[str]
    modality_keywords: list[str]
    regulatory_ids: list[str]      # 510(k) numbers, etc.
