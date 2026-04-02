"""Paper domain models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class StudyTypeTag(str, Enum):
    PIVOTAL_TRIAL = "pivotal_trial"
    CLINICAL_VALIDATION = "clinical_validation"
    RETROSPECTIVE = "retrospective"
    PROSPECTIVE = "prospective"
    MULTICENTER = "multicenter"
    RCT = "rct"
    CASE_STUDY = "case_study"
    SYSTEMATIC_REVIEW = "systematic_review"
    META_ANALYSIS = "meta_analysis"
    REGULATORY_SUBMISSION = "regulatory_submission"
    POST_MARKET = "post_market"
    TECHNICAL_VALIDATION = "technical_validation"
    EDITORIAL = "editorial"
    REVIEW = "review"
    LETTER = "letter"
    OTHER = "other"


class PaperAuthor(BaseModel):
    author_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    paper_id: uuid.UUID
    author_name: str
    affiliation: Optional[str] = None
    orcid: Optional[str] = None
    author_position: Optional[int] = None
    is_corresponding: bool = False


class PaperStudyTag(BaseModel):
    paper_id: uuid.UUID
    tag: StudyTypeTag
    confidence: Optional[float] = None
    source: str = "auto"  # 'auto' or 'human'


class Paper(BaseModel):
    paper_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    title: str
    abstract: Optional[str] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    openalex_id: Optional[str] = None
    journal: Optional[str] = None
    publication_date: Optional[date] = None
    publication_year: Optional[int] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    language: str = "en"
    is_open_access: Optional[bool] = None
    fulltext: Optional[str] = None
    fulltext_available: bool = False
    citation_count: Optional[int] = None
    source: Optional[str] = None
    raw_data: Optional[dict] = None

    # Populated on read
    authors: list[PaperAuthor] = Field(default_factory=list)
    study_tags: list[PaperStudyTag] = Field(default_factory=list)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
