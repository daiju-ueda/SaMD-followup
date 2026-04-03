# SaMD Evidence Tracker

**Product-centric AI/ML SaMD regulatory and literature database**

Collects AI/ML-enabled Software as a Medical Device (SaMD) products from the US (FDA) and Japan (PMDA), links them to English-language publications, and displays evidence organized by product — with strict separation between exact product evidence and related literature.

## Overview

| | |
|---|---|
| **Regions** | US (FDA), Japan (PMDA) |
| **FDA source** | Official AI/ML-Enabled Medical Devices CSV (1,430+ products, gold standard) |
| **FDA fallback** | accessdata.fda.gov bulk files (foiclass, PMA, pmn96cur, De Novo DB) |
| **PMDA sources** | pmda.go.jp Excel lists — AI flag for approved, keyword filter for certified |
| **Literature** | PubMed, Europe PMC (full-text search), OpenAlex |
| **Full text** | Europe PMC OA, NCBI PMC OA — used for scoring |
| **Database** | PostgreSQL (incremental upsert) |
| **UI** | FastAPI + Jinja2 (port 8001) |
| **Updates** | Monthly cron (1st of each month, 3:00 AM) |

## Architecture

```
Product Ingestion           Scoring & Linking              Display
─────────────────           ─────────────────              ───────
FDA CSV (gold)  ──┐
FDA bulk (fallback)┤→ Product Master ──┐                   Dashboard
PMDA Excel      ──┘  (cross-region     │                   Products (US/JP)
                      merge, dedup)    │                   Product Detail
PubMed          ──┐                    │                   Paper Detail
Europe PMC (FT) ──┼→ Paper Corpus ─────┼→ 15-feature ──→  Review Queue
OpenAlex        ──┘  (DOI dedup)       │   Scorer          SQL Console
                                       │
                  Fulltext fetch ───────┤
                  Fulltext rescore ─────┘
```

## Evidence Classification

Papers are classified into three tiers, displayed separately to prevent misattribution:

| Classification | Meaning | Criteria |
|---|---|---|
| **exact_product** | Paper explicitly names the product | Product name in title/abstract/fulltext + manufacturer corroboration for generic names |
| **manufacturer_linked** | Same manufacturer + matching indication | Manufacturer in author affiliations + disease/modality match |
| **indication_related** | Same disease area and modality | Disease + modality + AI/ML terms, no product-specific mention |

**False positive handling:**
- Generic product names (e.g., "Loop System", "Red Dot", "Vital Signs") require manufacturer co-occurrence or regulatory ID for `exact_product`
- Multi-word phrases: each word checked against generic word list (55+ words)
- Japanese product names: Latin tokens extracted via NFKC normalization, filtered for proper-noun patterns (mixed case, acronyms, special chars)
- Without corroboration, demoted to `indication_related`

**Fulltext scoring:**
- Pipeline fetches full text for top candidates (Europe PMC OA)
- `product_name_in_fulltext` feature (weight 10) catches mentions in paper body
- Post-pipeline rescore: scans DB fulltext, upgrades non-exact → exact when product name found in body, new links marked `human_review_needed=true`

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
# Full pipeline: FDA (CSV) + PMDA (web) → literature search → scoring
python3 scripts/run_pipeline.py --pmda-web --output data/pipeline_results.json

# Using FDA bulk files instead of CSV (fallback, ~50% coverage)
python3 scripts/run_pipeline.py --fda-web --pmda-web --output data/pipeline_results.json

# Load into database (incremental upsert)
python3 scripts/load_to_db.py --pmda-web

# Fetch full text for open-access papers
python3 scripts/fetch_fulltext.py

# Re-score using stored full text (upgrades + new links)
python3 scripts/rescore_fulltext.py

# Start web UI
python3 -m uvicorn src.ui.app:app --host 0.0.0.0 --port 8001
```

### CLI Options

```bash
# FDA only (from CSV, recommended)
python3 scripts/run_pipeline.py --skip-pmda

# FDA only (from bulk files, fallback)
python3 scripts/run_pipeline.py --skip-pmda --fda-web

# PMDA only (from web Excel)
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
├── config/settings.py        # Environment-based configuration (env vars)
├── utils.py                  # Date parsing, logging, JP text extraction
├── pipeline.py               # Pipeline orchestrator
│
├── ingestion/                # Product data collection
│   ├── fda.py                # FDA CSV parsing, deduplication, pathway inference
│   ├── fda_scraper.py        # FDA bulk file download (foiclass/PMA/pmn96cur/De Novo)
│   ├── pmda.py               # PMDA CSV parser (fallback)
│   ├── pmda_scraper.py       # PMDA Excel download (AI flag + keyword filter)
│   ├── normalizer.py         # Name normalization, disease area/modality inference
│   ├── cross_region.py       # Cross-region product merge (FDA ↔ PMDA)
│   └── jp_mappings.py        # Japanese → English manufacturer name mappings
│
├── literature/               # Literature search and retrieval
│   ├── query_generator.py    # 5-level search query generation
│   ├── pubmed.py             # PubMed E-utilities client
│   ├── openalex.py           # OpenAlex API client
│   ├── europe_pmc.py         # Europe PMC API client (full-text search)
│   ├── fulltext.py           # Full-text fetcher (Europe PMC / NCBI PMC OA)
│   ├── parsers.py            # Shared parsers (abstract reconstruction, JATS XML)
│   ├── local_openalex.py     # Local OpenAlex snapshot search
│   └── local_pmc.py          # Local PMC XML full-text search
│
├── linking/                  # Product-paper linking
│   ├── scorer.py             # 15-feature scoring, generic name detection, classification
│   └── deduplicator.py       # DOI/PMID-based paper deduplication
│
├── models/                   # Pydantic domain models
│   ├── product.py            # Product, RegulatoryEntry, ProductAlias
│   ├── paper.py              # Paper, PaperAuthor (with fulltext field)
│   └── linking.py            # ProductPaperLink, scoring config + thresholds
│
├── db/                       # Database layer
│   ├── schema_pg95.sql       # PostgreSQL schema
│   ├── connection.py         # Connection management
│   └── repositories.py       # Product/Paper/Stats repositories (upsert, review)
│
└── ui/                       # Web UI
    ├── app.py                # FastAPI app (dashboard, products, papers, review, SQL)
    └── templates/            # Jinja2 templates

scripts/
├── run_pipeline.py           # Pipeline CLI (search + score)
├── load_to_db.py             # Database loader (incremental upsert)
├── fetch_fulltext.py         # Full-text batch fetcher
├── rescore_fulltext.py       # Re-score links using stored full text
└── monthly_update.sh         # Monthly cron update script
```

## Data Sources

### FDA (United States)

| Source | Method | Content |
|---|---|---|
| **AI/ML CSV** (primary) | Manual download from fda.gov | 1,430+ curated AI/ML SaMD products |
| foiclass.zip (fallback) | Auto-download from accessdata.fda.gov | Product classification → derive SaMD codes |
| pma.zip (fallback) | Auto-download | PMA approvals (pipe-delimited) |
| pmn96cur.zip (fallback) | Auto-download | All 510(k) since 1996 (pipe-delimited) |
| De Novo DB (fallback) | HTML scraping | De Novo authorizations |

The FDA CSV is the gold standard (FDA's own curated list). Bulk files capture ~50% due to product code heuristics limitations.

### PMDA (Japan)

| Source | Method | Content |
|---|---|---|
| Approved SaMD Excel | Auto-download from pmda.go.jp | Program medical devices with AI活用医療機器=○ flag + keyword supplement |
| Certified device Excel | Auto-download | Class II certified devices filtered by AI/ML keywords (解析, 検出, 診断支援, AI, etc.) |

## Scoring

15 features with weighted scores, classified by thresholds:

| Feature | Weight | Description |
|---|---|---|
| product_name_in_title | 30 | Product name in paper title |
| product_name_in_abstract | 20 | Product name in abstract |
| product_name_in_fulltext | 10 | Product name in paper body (OA only) |
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

1. PMDA: download Excel lists (AI-flagged approved + AI/ML-filtered certified)
2. FDA: use local CSV (primary) or download bulk files (fallback)
3. Literature search: PubMed + Europe PMC + OpenAlex (5-level queries)
4. Incremental DB update (upsert — preserves human reviews + full text)
5. Full-text fetch (new OA papers only)
6. Full-text rescore (upgrade links, find new fulltext-only matches)
```

## License

MIT
