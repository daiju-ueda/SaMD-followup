"""Example API responses for documentation and testing.

These are concrete JSON examples showing what the API returns.
"""

# ---------------------------------------------------------------------------
# Product Detail example
# ---------------------------------------------------------------------------

PRODUCT_DETAIL_EXAMPLE = {
    "product_id": "550e8400-e29b-41d4-a716-446655440001",
    "canonical_name": "IDx-DR",
    "manufacturer_name": "Digital Diagnostics Inc.",
    "product_family": "IDx",
    "intended_use": "Autonomous detection of more than mild diabetic retinopathy (mtmDR) in adults with diabetes who have not been previously diagnosed with diabetic retinopathy, using fundus images taken with the Topcon NW400 camera",
    "disease_area": "Ophthalmology - Diabetic Retinopathy",
    "modality": "Fundus Photography",
    "standalone_samd": True,
    "technology_type": "Deep learning",
    "description": "First FDA-authorized autonomous AI diagnostic system. Provides a screening decision without the need for a clinician to interpret the image.",
    "aliases": [
        "IDx-DR",
        "LumineticsCore",
        "IDx DR",
        "Digital Diagnostics IDx-DR",
    ],
    "manufacturer_aliases": [
        "IDx Technologies",
        "Digital Diagnostics",
    ],
    "regulatory_entries": [
        {
            "region": "us",
            "pathway": "de_novo",
            "status": "authorized",
            "regulatory_id": "DEN180001",
            "date": "2018-04-11",
            "source_url": "https://www.accessdata.fda.gov/cdrh_docs/reviews/DEN180001.pdf",
            "evidence_tier": "tier_1",
        },
    ],
    "evidence_summary": {
        "exact_product": 47,
        "product_family": 3,
        "manufacturer_linked": 12,
        "indication_related": 230,
        "pending_review": 2,
        "evidence_gap": "No prospective multicenter RCT in non-US population found",
    },
}


# ---------------------------------------------------------------------------
# Product Papers response example
# ---------------------------------------------------------------------------

PRODUCT_PAPERS_EXAMPLE = {
    "product_id": "550e8400-e29b-41d4-a716-446655440001",
    "product_name": "IDx-DR",
    "exact_product": [
        {
            "paper_id": "paper-001",
            "title": "Pivotal Trial of an Autonomous AI-Based Diagnostic System for Detection of Diabetic Retinopathy in Primary Care Offices",
            "authors": [
                {"name": "Abramoff MD", "affiliation": "University of Iowa", "orcid": None},
                {"name": "Lavin PT", "affiliation": "Biostatistics Consulting", "orcid": None},
                {"name": "Birch M", "affiliation": "IDx Technologies", "orcid": None},
            ],
            "journal": "NPJ Digital Medicine",
            "year": 2018,
            "doi": "10.1038/s41746-018-0040-6",
            "pmid": "30137485",
            "is_open_access": True,
            "citation_count": 580,
            "study_tags": ["pivotal_trial", "prospective", "multicenter"],
            "link_type": "exact_product",
            "confidence_score": 0.95,
            "matched_terms": ["IDx-DR", "DEN180001", "autonomous AI"],
            "human_reviewed": True,
        },
        {
            "paper_id": "paper-002",
            "title": "Autonomous Artificial Intelligence Diabetic Retinopathy Screening Performance in a Clinical Environment",
            "authors": [
                {"name": "Kanagasingam Y", "affiliation": "CSIRO", "orcid": None},
            ],
            "journal": "Diabetes Care",
            "year": 2020,
            "doi": "10.2337/dc19-1877",
            "pmid": "31969345",
            "is_open_access": False,
            "citation_count": 45,
            "study_tags": ["clinical_validation", "retrospective"],
            "link_type": "exact_product",
            "confidence_score": 0.88,
            "matched_terms": ["IDx-DR"],
            "human_reviewed": True,
        },
    ],
    "product_family": [
        {
            "paper_id": "paper-003",
            "title": "Performance of IDx AI System Across Diverse Populations",
            "authors": [],
            "journal": "JAMA Ophthalmology",
            "year": 2021,
            "doi": None,
            "pmid": None,
            "is_open_access": False,
            "citation_count": 20,
            "study_tags": ["retrospective", "multicenter"],
            "link_type": "product_family",
            "confidence_score": 0.72,
            "matched_terms": ["IDx", "diabetic retinopathy", "autonomous"],
            "human_reviewed": False,
        },
    ],
    "manufacturer_linked": [
        {
            "paper_id": "paper-004",
            "title": "Digital Diagnostics Announces Real-World Data on AI Screening",
            "authors": [],
            "journal": "Ophthalmology Retina",
            "year": 2023,
            "doi": None,
            "pmid": None,
            "is_open_access": False,
            "citation_count": 5,
            "study_tags": ["post_market"],
            "link_type": "manufacturer_linked",
            "confidence_score": 0.55,
            "matched_terms": ["Digital Diagnostics", "diabetic retinopathy"],
            "human_reviewed": False,
        },
    ],
    "indication_related": [
        {
            "paper_id": "paper-005",
            "title": "Deep Learning for Diabetic Retinopathy Detection: A Systematic Review",
            "authors": [],
            "journal": "The Lancet Digital Health",
            "year": 2022,
            "doi": None,
            "pmid": None,
            "is_open_access": True,
            "citation_count": 150,
            "study_tags": ["systematic_review"],
            "link_type": "indication_related",
            "confidence_score": 0.35,
            "matched_terms": ["diabetic retinopathy", "deep learning", "fundus"],
            "human_reviewed": False,
        },
    ],
}


# ---------------------------------------------------------------------------
# Product card (UI display model)
# ---------------------------------------------------------------------------

PRODUCT_CARD_EXAMPLE = {
    "product_id": "550e8400-e29b-41d4-a716-446655440001",
    "canonical_name": "IDx-DR",
    "manufacturer": "Digital Diagnostics Inc.",
    "intended_use": "Autonomous detection of diabetic retinopathy",
    "disease_area": "Ophthalmology - Diabetic Retinopathy",
    "modality": "Fundus Photography",
    "regions": {
        "us": {
            "pathway": "De Novo",
            "status": "Authorized",
            "id": "DEN180001",
            "date": "2018-04-11",
            "link": "https://www.accessdata.fda.gov/cdrh_docs/reviews/DEN180001.pdf",
        },
    },
    "first_clearance_date": "2018-04-11",
    "evidence": {
        "exact_product": {
            "count": 47,
            "top_study_types": ["pivotal_trial", "clinical_validation", "prospective"],
            "has_pivotal": True,
            "has_multicenter": True,
        },
        "manufacturer_linked": {"count": 12},
        "indication_related": {"count": 230},
    },
    "evidence_gap": "No prospective multicenter RCT in non-US population found",
    "tags": ["AI/ML", "Autonomous", "De Novo", "Ophthalmology"],
}


# ---------------------------------------------------------------------------
# Japanese product example (PMDA)
# ---------------------------------------------------------------------------

JP_PRODUCT_EXAMPLE = {
    "product_id": "550e8400-e29b-41d4-a716-446655440002",
    "canonical_name": "nodoca",
    "manufacturer_name": "Aillis Inc.",
    "product_family": None,
    "intended_use": "Detection of influenza infection from pharyngeal images using AI",
    "disease_area": "Infectious Disease - Influenza",
    "modality": "Pharyngeal Imaging",
    "standalone_samd": True,
    "technology_type": "Deep learning",
    "aliases": [
        "nodoca",
        "ノドカ",
        "AI搭載インフルエンザ検出装置 nodoca",
    ],
    "regulatory_entries": [
        {
            "region": "jp",
            "pathway": "approval",
            "status": "approved",
            "regulatory_id": "30400BZX00090000",
            "date": "2022-01-24",
            "source_url": "https://www.pmda.go.jp/...",
            "evidence_tier": "tier_1",
        },
    ],
    "evidence_summary": {
        "exact_product": 8,
        "product_family": 0,
        "manufacturer_linked": 3,
        "indication_related": 95,
        "pending_review": 1,
        "evidence_gap": "Limited prospective validation studies in English",
    },
}
