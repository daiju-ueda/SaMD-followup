"""Product-Paper linking scorer.

Takes a candidate paper and a product's search terms, computes feature-level
scores, classifies the link, and determines whether human review is needed.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from src.models.linking import (
    DEFAULT_FEATURE_WEIGHTS,
    LinkClassification,
    LinkScoreDetail,
    ProductPaperLink,
    get_classification_thresholds,
)
from src.models.paper import Paper, PaperAuthor, PaperStudyTag, StudyTypeTag
from src.models.product import ProductSearchTerms

logger = logging.getLogger(__name__)

# Terms that indicate a clinical study
CLINICAL_TERMS = {
    "clinical validation", "clinical trial", "clinical study",
    "prospective", "retrospective", "multicenter", "multi-center",
    "randomized", "randomised", "controlled trial", "rct",
    "pivotal", "fda", "ce mark", "regulatory",
    "post-market", "real-world", "sensitivity", "specificity",
    "auc", "roc", "accuracy", "performance",
}


def _text_contains(text: str, term: str) -> bool:
    """Case-insensitive whole-word boundary check.

    'EndoBRAIN' matches 'EndoBRAIN system' but not 'EndoBRAINing'.
    'syngo.via' matches 'using syngo.via for' but not 'asyncgo.via'.
    """
    if not text or not term:
        return False
    pattern = r'\b' + re.escape(term) + r'\b'
    return bool(re.search(pattern, text, re.IGNORECASE))


def _any_term_in_text(text: str, terms: list[str]) -> tuple[bool, list[str]]:
    """Check if any of the terms appear in the text. Return (found, matched_terms)."""
    matched: list[str] = []
    for term in terms:
        if _text_contains(text, term):
            matched.append(term)
    return bool(matched), matched


def compute_features(
    paper: Paper,
    terms: ProductSearchTerms,
) -> list[LinkScoreDetail]:
    """Compute all scoring features for a paper-product pair."""
    features: list[LinkScoreDetail] = []
    weights = DEFAULT_FEATURE_WEIGHTS

    title = paper.title or ""
    abstract = paper.abstract or ""
    body = paper.fulltext or ""
    # full_text = all searchable text combined (for manufacturer, indication matching)
    full_text = title + " " + abstract + (" " + body if body else "")

    # ---- Product name features ----
    # Title
    found, matched = _any_term_in_text(title, terms.all_names)
    features.append(LinkScoreDetail(
        feature_name="product_name_in_title",
        feature_value=1.0 if found else 0.0,
        weight=weights["product_name_in_title"],
        weighted_score=weights["product_name_in_title"] if found else 0.0,
        evidence=", ".join(matched) if matched else None,
    ))

    # Abstract
    found_abs, matched_abs = _any_term_in_text(abstract, terms.all_names)
    features.append(LinkScoreDetail(
        feature_name="product_name_in_abstract",
        feature_value=1.0 if found_abs else 0.0,
        weight=weights["product_name_in_abstract"],
        weighted_score=weights["product_name_in_abstract"] if found_abs else 0.0,
        evidence=", ".join(matched_abs) if matched_abs else None,
    ))

    # Full text (body only — not title/abstract, to avoid double counting)
    if body:
        found_ft, matched_ft = _any_term_in_text(body, terms.all_names)
        features.append(LinkScoreDetail(
            feature_name="product_name_in_fulltext",
            feature_value=1.0 if found_ft else 0.0,
            weight=weights["product_name_in_fulltext"],
            weighted_score=weights["product_name_in_fulltext"] if found_ft else 0.0,
            evidence=", ".join(matched_ft) if matched_ft else None,
        ))

    # ---- Product alias features ----
    alias_names = [n for n in terms.all_names if n != terms.canonical_name]
    found_alias_title, matched_alias = _any_term_in_text(title, alias_names)
    features.append(LinkScoreDetail(
        feature_name="product_alias_in_title",
        feature_value=1.0 if found_alias_title else 0.0,
        weight=weights["product_alias_in_title"],
        weighted_score=weights["product_alias_in_title"] if found_alias_title else 0.0,
        evidence=", ".join(matched_alias) if matched_alias else None,
    ))

    found_alias_abs, matched_alias_abs = _any_term_in_text(abstract, alias_names)
    features.append(LinkScoreDetail(
        feature_name="product_alias_in_abstract",
        feature_value=1.0 if found_alias_abs else 0.0,
        weight=weights["product_alias_in_abstract"],
        weighted_score=weights["product_alias_in_abstract"] if found_alias_abs else 0.0,
        evidence=", ".join(matched_alias_abs) if matched_alias_abs else None,
    ))

    # ---- Product family features ----
    found_fam_title, matched_fam = _any_term_in_text(title, terms.family_names)
    features.append(LinkScoreDetail(
        feature_name="product_family_in_title",
        feature_value=1.0 if found_fam_title else 0.0,
        weight=weights["product_family_in_title"],
        weighted_score=weights["product_family_in_title"] if found_fam_title else 0.0,
        evidence=", ".join(matched_fam) if matched_fam else None,
    ))

    found_fam_abs, matched_fam_abs = _any_term_in_text(abstract, terms.family_names)
    features.append(LinkScoreDetail(
        feature_name="product_family_in_abstract",
        feature_value=1.0 if found_fam_abs else 0.0,
        weight=weights["product_family_in_abstract"],
        weighted_score=weights["product_family_in_abstract"] if found_fam_abs else 0.0,
        evidence=", ".join(matched_fam_abs) if matched_fam_abs else None,
    ))

    # ---- Manufacturer features ----
    # Author affiliations
    affiliation_text = " ".join(
        a.affiliation or "" for a in paper.authors
    )
    found_mfg_affil, matched_mfg_affil = _any_term_in_text(
        affiliation_text, terms.manufacturer_names,
    )
    features.append(LinkScoreDetail(
        feature_name="manufacturer_in_author_affiliation",
        feature_value=1.0 if found_mfg_affil else 0.0,
        weight=weights["manufacturer_in_author_affiliation"],
        weighted_score=weights["manufacturer_in_author_affiliation"] if found_mfg_affil else 0.0,
        evidence=", ".join(matched_mfg_affil) if matched_mfg_affil else None,
    ))

    # Manufacturer in text
    found_mfg_text, matched_mfg_text = _any_term_in_text(
        full_text, terms.manufacturer_names,
    )
    features.append(LinkScoreDetail(
        feature_name="manufacturer_in_text",
        feature_value=1.0 if found_mfg_text else 0.0,
        weight=weights["manufacturer_in_text"],
        weighted_score=weights["manufacturer_in_text"] if found_mfg_text else 0.0,
        evidence=", ".join(matched_mfg_text) if matched_mfg_text else None,
    ))

    # ---- Indication features ----
    found_use, _ = _any_term_in_text(full_text, terms.intended_use_keywords)
    features.append(LinkScoreDetail(
        feature_name="intended_use_match",
        feature_value=1.0 if found_use else 0.0,
        weight=weights["intended_use_match"],
        weighted_score=weights["intended_use_match"] if found_use else 0.0,
    ))

    found_disease, _ = _any_term_in_text(full_text, terms.disease_area_keywords)
    features.append(LinkScoreDetail(
        feature_name="disease_area_match",
        feature_value=1.0 if found_disease else 0.0,
        weight=weights["disease_area_match"],
        weighted_score=weights["disease_area_match"] if found_disease else 0.0,
    ))

    found_modality, _ = _any_term_in_text(full_text, terms.modality_keywords)
    features.append(LinkScoreDetail(
        feature_name="modality_match",
        feature_value=1.0 if found_modality else 0.0,
        weight=weights["modality_match"],
        weighted_score=weights["modality_match"] if found_modality else 0.0,
    ))

    # ---- Regulatory ID ----
    found_reg, matched_reg = _any_term_in_text(full_text, terms.regulatory_ids)
    features.append(LinkScoreDetail(
        feature_name="regulatory_id_in_text",
        feature_value=1.0 if found_reg else 0.0,
        weight=weights["regulatory_id_in_text"],
        weighted_score=weights["regulatory_id_in_text"] if found_reg else 0.0,
        evidence=", ".join(matched_reg) if matched_reg else None,
    ))

    # ---- Study type ----
    found_clinical, _ = _any_term_in_text(full_text, list(CLINICAL_TERMS))
    features.append(LinkScoreDetail(
        feature_name="study_type_clinical",
        feature_value=1.0 if found_clinical else 0.0,
        weight=weights["study_type_clinical"],
        weighted_score=weights["study_type_clinical"] if found_clinical else 0.0,
    ))

    found_multicenter = _text_contains(full_text, "multicenter") or _text_contains(full_text, "multi-center")
    features.append(LinkScoreDetail(
        feature_name="study_type_multicenter",
        feature_value=1.0 if found_multicenter else 0.0,
        weight=weights["study_type_multicenter"],
        weighted_score=weights["study_type_multicenter"] if found_multicenter else 0.0,
    ))

    return features


def classify_link(
    features: list[LinkScoreDetail],
    terms: ProductSearchTerms,
) -> tuple[LinkClassification, float, bool]:
    """Classify a product-paper link based on computed features.

    Returns (classification, raw_score, human_review_needed).
    """
    raw_score = sum(f.weighted_score for f in features)
    thresholds = get_classification_thresholds()

    # Check which feature groups fired
    product_name_hit = any(
        f.feature_value > 0
        for f in features
        if f.feature_name in ("product_name_in_title", "product_name_in_abstract", "product_name_in_fulltext")
    )
    product_alias_hit = any(
        f.feature_value > 0
        for f in features
        if f.feature_name in ("product_alias_in_title", "product_alias_in_abstract")
    )
    family_hit = any(
        f.feature_value > 0
        for f in features
        if f.feature_name in ("product_family_in_title", "product_family_in_abstract")
    )
    manufacturer_hit = any(
        f.feature_value > 0
        for f in features
        if f.feature_name in ("manufacturer_in_author_affiliation", "manufacturer_in_text")
    )
    indication_hit = any(
        f.feature_value > 0
        for f in features
        if f.feature_name in ("intended_use_match", "disease_area_match", "modality_match")
    )

    # --- Generic name detection ---
    # Short or common English words produce massive false positives.
    # Require additional corroboration (manufacturer or regulatory ID) for these.
    is_generic_name = is_generic_product_name(terms.canonical_name)

    reg_id_hit = any(
        f.feature_value > 0 for f in features
        if f.feature_name == "regulatory_id_in_text"
    )

    # --- Classification logic ---
    if product_name_hit or product_alias_hit:
        if is_generic_name:
            # Generic name: require manufacturer co-occurrence OR regulatory ID
            if manufacturer_hit or reg_id_hit:
                classification = LinkClassification.EXACT_PRODUCT
            else:
                # Name hit alone is not enough for generic names
                classification = LinkClassification.INDICATION_RELATED
        else:
            classification = LinkClassification.EXACT_PRODUCT
    elif raw_score >= thresholds["product_family_min_score"] and family_hit:
        classification = LinkClassification.PRODUCT_FAMILY
    elif manufacturer_hit and indication_hit:
        classification = LinkClassification.MANUFACTURER_LINKED
    elif indication_hit and raw_score >= thresholds["indication_related_min_score"]:
        classification = LinkClassification.INDICATION_RELATED
    else:
        classification = LinkClassification.IRRELEVANT

    # --- Human review logic ---
    human_review_needed = False

    # Ambiguous zone: moderate score without clear product name hit
    if (
        thresholds["human_review_low"] <= raw_score < thresholds["human_review_high"]
        and not product_name_hit
    ):
        human_review_needed = True

    # Generic names always need review even if classified as exact
    if is_generic_name and classification == LinkClassification.EXACT_PRODUCT:
        human_review_needed = True

    # Multiple products matching the same paper is suspicious
    # (handled at the pipeline level, not here)

    return classification, raw_score, human_review_needed


# Common English words and short terms that are likely to be product names
# but also appear frequently in medical literature as regular words.
_GENERIC_WORDS = {
    # Single common words
    "rapid", "halo", "aurora", "venue", "vision", "insight", "connect",
    "care", "health", "smart", "pro", "guardian", "vital", "embrace",
    "loop", "rho", "koala", "impala", "caddie", "jazz", "pearl",
    "ruby", "nova", "iris", "echo", "focus", "core", "edge", "apex",
    "wave", "flow", "link", "plus", "max", "one", "air", "red", "dot",
    "contact", "second", "deep", "signs", "station",
    # Short abbreviations that are also common words
    "andi", "cina", "rus",
    # Common terms extracted from Japanese product names
    "attractive", "viewer", "confirm", "elements", "leaf", "neo",
    "join", "cvs", "ffr", "dxa", "bsi", "pwv", "pro",
    "basic", "advance", "standard", "lite", "premium",
    "system", "suite", "studio", "navigator", "planner",
    "monitor", "analyzer", "tracker", "manager", "assist",
    "select", "guide", "scan", "view", "image",
    # Medical/technical terms commonly in JP product names
    "holter", "eclipse", "velocity", "harmony", "central",
    "synthetic", "imagine", "kidney", "reveal", "clarus",
    "athena", "eureka", "mosaic", "simple", "neutral",
    "customize", "parallel", "natural", "cycles",
    # False positives found during validation
    "falcon", "lumi", "kosmos", "cosmos", "precise",
    "second opinion", "red dot", "loop", "vital signs",
    "automatic registration", "precise position",
}


_MIN_SPECIFIC_NAME_LENGTH = 6  # Single words shorter than this are likely generic


def is_generic_product_name(name: str) -> bool:
    """Check if a product name is a common English word/phrase (high FP risk).

    Checks:
    1. Full name against generic word/phrase list
    2. Each individual word against the list
    3. Single short words (< 6 chars)
    """
    name_lower = name.lower().strip()
    # Full name match
    if name_lower in _GENERIC_WORDS:
        return True
    # Each word match — if ALL words are generic, the name is generic
    words = name_lower.split()
    if words and all(w in _GENERIC_WORDS or len(w) < _MIN_SPECIFIC_NAME_LENGTH for w in words):
        return True
    # Single short word
    if len(words) == 1 and len(name_lower) < _MIN_SPECIFIC_NAME_LENGTH:
        return True
    return False


def score_and_link(
    paper: Paper,
    terms: ProductSearchTerms,
) -> Optional[ProductPaperLink]:
    """Full scoring pipeline: compute features → classify → create link.

    Returns None if classified as irrelevant.
    """
    features = compute_features(paper, terms)
    classification, raw_score, human_review = classify_link(features, terms)

    if classification == LinkClassification.IRRELEVANT:
        return None

    # Normalize score to 0-1 range
    max_possible = sum(DEFAULT_FEATURE_WEIGHTS.values())
    confidence_score = min(raw_score / max_possible, 1.0)

    # Collect matched terms
    matched_terms: list[str] = []
    for f in features:
        if f.evidence:
            matched_terms.extend(f.evidence.split(", "))
    matched_terms = list(set(matched_terms))

    # Build rationale
    fired = [f"{f.feature_name}={f.weighted_score:.0f}" for f in features if f.weighted_score > 0]
    rationale = f"Classification: {classification.value}, raw_score={raw_score:.1f}, features: {', '.join(fired)}"

    link = ProductPaperLink(
        product_id=terms.product_id,
        paper_id=paper.paper_id,
        link_classification=classification,
        confidence_score=round(confidence_score, 3),
        raw_score=round(raw_score, 1),
        matched_terms=matched_terms,
        rationale=rationale,
        human_review_needed=human_review,
        score_details=features,
    )

    return link


def classify_study_type(paper: Paper) -> list[PaperStudyTag]:
    """Auto-classify study type from title/abstract."""
    text = f"{paper.title or ''} {paper.abstract or ''}".lower()
    tags: list[PaperStudyTag] = []

    tag_patterns: list[tuple[StudyTypeTag, list[str]]] = [
        (StudyTypeTag.PIVOTAL_TRIAL, ["pivotal"]),
        (StudyTypeTag.RCT, ["randomized controlled", "randomised controlled", "rct"]),
        (StudyTypeTag.PROSPECTIVE, ["prospective"]),
        (StudyTypeTag.RETROSPECTIVE, ["retrospective"]),
        (StudyTypeTag.MULTICENTER, ["multicenter", "multi-center", "multicentre"]),
        (StudyTypeTag.SYSTEMATIC_REVIEW, ["systematic review"]),
        (StudyTypeTag.META_ANALYSIS, ["meta-analysis", "meta analysis"]),
        (StudyTypeTag.CLINICAL_VALIDATION, ["clinical validation", "clinical performance"]),
        (StudyTypeTag.TECHNICAL_VALIDATION, ["technical validation", "analytical validation"]),
        (StudyTypeTag.POST_MARKET, ["post-market", "post market", "real-world"]),
        (StudyTypeTag.REGULATORY_SUBMISSION, ["fda submission", "regulatory submission", "510(k)", "de novo"]),
        (StudyTypeTag.CASE_STUDY, ["case report", "case study", "case series"]),
        (StudyTypeTag.REVIEW, ["review", "overview", "survey"]),
        (StudyTypeTag.EDITORIAL, ["editorial", "commentary", "opinion"]),
        (StudyTypeTag.LETTER, ["letter to the editor", "correspondence"]),
    ]

    for tag, patterns in tag_patterns:
        for pattern in patterns:
            if pattern in text:
                tags.append(PaperStudyTag(
                    paper_id=paper.paper_id,
                    tag=tag,
                    confidence=0.8,
                    source="auto",
                ))
                break  # one match per tag is enough

    return tags
