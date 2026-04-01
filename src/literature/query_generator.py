"""Literature search query generator.

Generates search queries for PubMed, Europe PMC, and OpenAlex
from a product's search terms (canonical name, aliases, manufacturer, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

from src.models.product import ProductSearchTerms

logger = logging.getLogger(__name__)


class QueryLevel(IntEnum):
    """Query specificity levels — lower = more specific."""
    EXACT_PRODUCT = 1
    PRODUCT_FAMILY = 2
    MANUFACTURER_INDICATION = 3
    REGULATORY_ID = 4
    INDICATION_BROAD = 5


@dataclass
class SearchQuery:
    """A generated search query for a specific source."""
    product_id: str
    source: str            # 'pubmed', 'europe_pmc', 'openalex'
    level: QueryLevel
    query_text: str
    description: str       # human-readable description of what this query targets


def _quote(term: str) -> str:
    """Wrap a term in double quotes for exact phrase matching."""
    return f'"{term}"'


def _or_join(terms: list[str]) -> str:
    return " OR ".join(terms)


def _and_join(terms: list[str]) -> str:
    return " AND ".join(f"({t})" for t in terms)


# ---- PubMed query builders --------------------------------------------------

CLINICAL_TERMS = (
    "validation OR performance OR accuracy OR sensitivity OR specificity "
    'OR "clinical trial" OR "clinical study"'
)

AI_TERMS = (
    '"artificial intelligence" OR "machine learning" OR "deep learning" '
    'OR algorithm OR "computer-aided" OR "computer aided" OR software'
)


def generate_pubmed_queries(terms: ProductSearchTerms) -> list[SearchQuery]:
    """Generate PubMed E-utilities search queries for a product."""
    queries: list[SearchQuery] = []
    pid = str(terms.product_id)

    # Level 1: Exact product name search
    if terms.all_names:
        name_clause = _or_join([_quote(n) for n in terms.all_names])
        q = f"({name_clause})"
        queries.append(SearchQuery(
            product_id=pid,
            source="pubmed",
            level=QueryLevel.EXACT_PRODUCT,
            query_text=q,
            description=f"Exact product names: {', '.join(terms.all_names[:3])}...",
        ))

        # Level 1b: Product name + clinical validation terms
        q_clinical = _and_join([name_clause, CLINICAL_TERMS])
        queries.append(SearchQuery(
            product_id=pid,
            source="pubmed",
            level=QueryLevel.EXACT_PRODUCT,
            query_text=q_clinical,
            description="Product names + clinical validation terms",
        ))

    # Level 2: Product family search
    if terms.family_names:
        family_clause = _or_join([_quote(n) for n in terms.family_names])
        disease_clause = _or_join([_quote(d) for d in terms.disease_area_keywords]) if terms.disease_area_keywords else ""
        if disease_clause:
            q = _and_join([family_clause, disease_clause])
        else:
            q = f"({family_clause})"
        queries.append(SearchQuery(
            product_id=pid,
            source="pubmed",
            level=QueryLevel.PRODUCT_FAMILY,
            query_text=q,
            description=f"Product family: {', '.join(terms.family_names[:3])}",
        ))

    # Level 3: Manufacturer + indication + AI terms
    if terms.manufacturer_names and (terms.intended_use_keywords or terms.disease_area_keywords):
        mfg_clause = _or_join([_quote(m) for m in terms.manufacturer_names])
        indication_terms = terms.intended_use_keywords + terms.disease_area_keywords
        indication_clause = _or_join([_quote(t) for t in indication_terms[:5]])
        q = _and_join([mfg_clause, indication_clause, AI_TERMS])
        queries.append(SearchQuery(
            product_id=pid,
            source="pubmed",
            level=QueryLevel.MANUFACTURER_INDICATION,
            query_text=q,
            description=f"Manufacturer ({terms.manufacturer_names[0]}) + indication + AI",
        ))

    # Level 4: Regulatory ID search
    if terms.regulatory_ids:
        reg_clause = _or_join([_quote(r) for r in terms.regulatory_ids])
        queries.append(SearchQuery(
            product_id=pid,
            source="pubmed",
            level=QueryLevel.REGULATORY_ID,
            query_text=f"({reg_clause})",
            description=f"Regulatory IDs: {', '.join(terms.regulatory_ids[:3])}",
        ))

    # Level 5: Broad indication search (used for indication_related papers)
    if terms.disease_area_keywords and terms.modality_keywords:
        disease_clause = _or_join([_quote(d) for d in terms.disease_area_keywords])
        modality_clause = _or_join([_quote(m) for m in terms.modality_keywords])
        q = _and_join([disease_clause, modality_clause, AI_TERMS, CLINICAL_TERMS])
        queries.append(SearchQuery(
            product_id=pid,
            source="pubmed",
            level=QueryLevel.INDICATION_BROAD,
            query_text=q,
            description=f"Broad indication: {terms.disease_area_keywords[0]} + {terms.modality_keywords[0]}",
        ))

    return queries


# ---- Europe PMC query builders -----------------------------------------------

def generate_europepmc_queries(terms: ProductSearchTerms) -> list[SearchQuery]:
    """Generate Europe PMC search queries.

    Europe PMC supports full-text search, which catches product mentions
    not in the title/abstract.
    """
    queries: list[SearchQuery] = []
    pid = str(terms.product_id)

    # Level 1: Exact product name in full text
    if terms.all_names:
        name_clause = _or_join([_quote(n) for n in terms.all_names])
        queries.append(SearchQuery(
            product_id=pid,
            source="europe_pmc",
            level=QueryLevel.EXACT_PRODUCT,
            query_text=name_clause,
            description=f"Product names in full text (Europe PMC)",
        ))

    # Level 3: Manufacturer + indication
    if terms.manufacturer_names and terms.disease_area_keywords:
        mfg_clause = _or_join([_quote(m) for m in terms.manufacturer_names[:3]])
        disease_clause = _or_join([_quote(d) for d in terms.disease_area_keywords[:3]])
        q = _and_join([mfg_clause, disease_clause, AI_TERMS])
        queries.append(SearchQuery(
            product_id=pid,
            source="europe_pmc",
            level=QueryLevel.MANUFACTURER_INDICATION,
            query_text=q,
            description="Manufacturer + indication in full text (Europe PMC)",
        ))

    return queries


# ---- OpenAlex query builders -------------------------------------------------

def generate_openalex_queries(terms: ProductSearchTerms) -> list[SearchQuery]:
    """Generate OpenAlex API queries.

    OpenAlex uses a different query format — filter-based rather than
    boolean search strings.
    """
    queries: list[SearchQuery] = []
    pid = str(terms.product_id)

    # Level 1: Title/abstract search for product name
    if terms.all_names:
        # OpenAlex supports search parameter for title+abstract
        for name in terms.all_names[:5]:  # limit to avoid excessive queries
            queries.append(SearchQuery(
                product_id=pid,
                source="openalex",
                level=QueryLevel.EXACT_PRODUCT,
                query_text=name,
                description=f"OpenAlex title/abstract search: {name}",
            ))

    return queries


# ---- Unified generator -------------------------------------------------------

def generate_all_queries(terms: ProductSearchTerms) -> list[SearchQuery]:
    """Generate search queries for all sources."""
    queries: list[SearchQuery] = []
    queries.extend(generate_pubmed_queries(terms))
    queries.extend(generate_europepmc_queries(terms))
    queries.extend(generate_openalex_queries(terms))
    logger.info(
        "Generated %d queries for product %s (%s)",
        len(queries), terms.canonical_name, terms.product_id,
    )
    return queries
