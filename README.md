# SaMD Evidence Tracker

**Product-centric regulatory and literature database for Software as a Medical Device**

Collects approved/cleared SaMD products from the US (FDA) and Japan (PMDA), links them to English-language publications, and displays evidence organized by product — with strict separation between exact product evidence and related literature.

## Overview

| | |
|---|---|
| **Regions** | US (FDA), Japan (PMDA), EU (designed, Phase 2) |
| **FDA sources** | accessdata.fda.gov bulk files (foiclass, PMA, 510(k), De Novo) |
| **PMDA sources** | pmda.go.jp Excel lists (approved + certified devices) |
| **Literature** | PubMed, Europe PMC, OpenAlex |
| **Full text** | Europe PMC OA, NCBI PMC OA |
| **Database** | PostgreSQL |
| **UI** | FastAPI + Jinja2 |
| **Updates** | Monthly cron (1st of each month, 3:00 AM) |

## Architecture

```
Data Ingestion          Normalization & Linking      Display
──────────────          ───────────────────────      ───────
FDA bulk zips  ──┐
PMDA Excel     ──┼→ Product Master ──┐              Dashboard
                 │   (dedup, cross-   │              Product List
PubMed API     ──┐   region merge)   ├───────────→  Product Detail
Europe PMC API ──┼→ Paper Corpus ────┤              Paper Detail
OpenAlex API   ──┘   (DOI dedup)     │              SQL Console
                                     │
                  Scorer ────────────┘
                  (15 features, weighted)
                  exact_product / product_family /
                  manufacturer_linked / indication_related
```

## Evidence Classification

Papers are classified into three tiers, displayed separately to prevent misattribution:

| Classification | Meaning | Criteria |
|---|---|---|
| **exact_product** | Paper explicitly names the product | Product name in title/abstract + manufacturer corroboration for generic names |
| **manufacturer_linked** | Same manufacturer + matching indication | Manufacturer in author affiliations + disease/modality match |
| **indication_related** | Same disease area and modality | Disease + modality + AI/ML terms, no product-specific mention |

**False positive handling**: Generic product names (e.g., "Rapid", "HALO", "Vision") require manufacturer co-occurrence or regulatory ID confirmation. Without corroboration, they are demoted to `indication_related` with `human_review_needed=true`.

## Setup

### Prerequisites

- Python 3.10+
- PostgreSQL 9.5+

### Installation

```bash
# Create database
sudo -u postgres createuser -s $(whoami)
sudo -u postgres createdb samd_evidence -O $(whoami)

# Apply schema
psql -d samd_evidence -f src/db/schema_pg95.sql

# Configure API keys
cat > .env << EOF
SAMD_NCBI_API_KEY=your_key_here
SAMD_NCBI_EMAIL=your_email@example.com
EOF

# Install dependencies
pip install httpx pydantic pydantic-settings psycopg2-binary \
    fastapi uvicorn jinja2 pandas openpyxl beautifulsoup4 lxml
```

### Running

```bash
# Full pipeline: FDA + PMDA → literature search → scoring
python3 scripts/run_pipeline.py --fda-web --pmda-web --output data/pipeline_results.json

# Load into database (incremental upsert)
python3 scripts/load_to_db.py --fda-web --pmda-web

# Fetch full text for open-access papers
python3 scripts/fetch_fulltext.py

# Start web UI
python3 -m uvicorn src.ui.app:app --host 0.0.0.0 --port 8001
```

### CLI Options

```bash
# FDA only (from bulk files)
python3 scripts/run_pipeline.py --skip-pmda --fda-web

# PMDA only (from Excel download)
python3 scripts/run_pipeline.py --skip-fda --pmda-web

# Resume from checkpoint
python3 scripts/run_pipeline.py --resume 300 --max-products 600

# Retry failed full-text fetches
python3 scripts/fetch_fulltext.py --retry-failed
```

## Project Structure

```
src/
├── bootstrap.py              # Path + .env setup (shared by all entry points)
├── config/settings.py        # Environment-based configuration
├── utils.py                  # Date parsing, logging
├── pipeline.py               # Pipeline orchestrator
│
├── ingestion/                # Product data collection
│   ├── fda.py                # FDA CSV parsing, deduplication, pathway inference
│   ├── fda_scraper.py        # FDA bulk file download (foiclass/PMA/510k/De Novo)
│   ├── pmda.py               # PMDA CSV parser (fallback)
│   ├── pmda_scraper.py       # PMDA Excel download (approved + certified)
│   ├── normalizer.py         # Name normalization, disease area/modality inference
│   ├── cross_region.py       # Cross-region product merge (FDA ↔ PMDA)
│   └── jp_mappings.py        # Japanese → English manufacturer name mappings
│
├── literature/               # Literature search and retrieval
│   ├── query_generator.py    # 5-level search query generation
│   ├── pubmed.py             # PubMed E-utilities client
│   ├── openalex.py           # OpenAlex API client
│   ├── europe_pmc.py         # Europe PMC API client
│   ├── fulltext.py           # Full-text fetcher (Europe PMC / NCBI PMC OA)
│   ├── parsers.py            # Shared parsers (abstract reconstruction, JATS XML)
│   ├── local_openalex.py     # Local OpenAlex snapshot search
│   └── local_pmc.py          # Local PMC XML full-text search
│
├── linking/                  # Product-paper linking
│   ├── scorer.py             # 15-feature weighted scoring, 5-way classification
│   └── deduplicator.py       # DOI/PMID-based paper deduplication
│
├── models/                   # Pydantic domain models
│   ├── product.py            # Product, RegulatoryEntry, ProductAlias
│   ├── paper.py              # Paper, PaperAuthor
│   └── linking.py            # ProductPaperLink, scoring config
│
├── db/                       # Database layer
│   ├── schema_pg95.sql       # PostgreSQL schema
│   ├── connection.py         # Connection management
│   └── repositories.py       # Product/Paper/Stats repositories (upsert)
│
└── ui/                       # Web UI
    ├── app.py                # FastAPI application
    └── templates/            # Jinja2 templates

scripts/
├── run_pipeline.py           # Pipeline CLI
├── load_to_db.py             # Database loader (incremental upsert)
├── fetch_fulltext.py         # Full-text batch fetcher
└── monthly_update.sh         # Monthly cron update script
```

## Data Sources

### FDA (United States)

| Source | URL | Content |
|---|---|---|
| foiclass.zip | accessdata.fda.gov/premarket/ftparea/ | Product classification → derive SaMD codes |
| pma.zip | Same | PMA approvals (pipe-delimited) |
| pmnlstmn.zip | Same | 510(k) monthly clearances (pipe-delimited) |
| De Novo DB | accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/denovo.cfm | De Novo authorizations (HTML) |

### PMDA (Japan)

| Source | URL | Content |
|---|---|---|
| Approved SaMD Excel | pmda.go.jp | Class III/IV approved program medical devices |
| Certified device Excel | pmda.go.jp | Class II certified devices (filtered by SaMD keywords) |

## Scoring

15 features with weighted scores, classified by thresholds:

| Feature | Weight | Description |
|---|---|---|
| product_name_in_title | 30 | Product name appears in paper title |
| product_name_in_abstract | 20 | Product name appears in abstract |
| regulatory_id_in_text | 25 | Regulatory ID (e.g., K210000) in text |
| product_alias_in_title | 20 | Alias/trade name in title |
| product_alias_in_abstract | 15 | Alias/trade name in abstract |
| product_family_in_title | 12 | Product family name in title |
| product_family_in_abstract | 10 | Product family name in abstract |
| manufacturer_in_affiliation | 8 | Manufacturer in author affiliations |
| manufacturer_in_text | 5 | Manufacturer mentioned in text |
| intended_use_match | 5 | Intended use keywords match |
| disease_area_match | 3 | Disease area match |
| modality_match | 3 | Imaging modality match |
| study_type_clinical | 5 | Clinical validation terms present |
| study_type_multicenter | 3 | Multicenter study |

## Monthly Updates

```
cron: 0 3 1 * *

1. PMDA: download Excel lists (approved + certified) → literature search
2. FDA: download bulk files (foiclass + PMA + 510k + De Novo) → literature search
3. Incremental DB update (upsert — preserves human reviews + full text)
4. Full-text fetch (new papers only)
```

## License

MIT
