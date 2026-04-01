-- =============================================================================
-- SaMD Evidence Tracker — PostgreSQL Schema
-- =============================================================================
-- Designed to support US (FDA), JP (PMDA), EU (EUDAMED) regulatory data
-- with product-centric literature linking and scoring.
-- =============================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- for fuzzy text search

-- =============================================================================
-- ENUM TYPES
-- =============================================================================

CREATE TYPE region_code AS ENUM ('us', 'jp', 'eu');

CREATE TYPE regulatory_pathway AS ENUM (
    -- US
    '510k', 'de_novo', 'pma', 'hde',
    -- JP
    'approval', 'certification', 'notification',
    -- EU
    'ce_mdd', 'ce_mdr', 'ce_ivdr',
    -- Generic
    'other'
);

CREATE TYPE regulatory_status_normalized AS ENUM (
    'cleared',          -- US 510(k)
    'authorized',       -- US De Novo
    'approved',         -- US PMA / JP 承認
    'certified',        -- JP 認証
    'ce_marked',        -- EU CE marked (active)
    'ce_marked_legacy', -- EU CE marked under MDD (transition period)
    'withdrawn',        -- Withdrawn from market
    'suspended',        -- Temporarily suspended
    'pending',          -- Application under review
    'unknown'
);

CREATE TYPE alias_type AS ENUM (
    'trade_name',
    'product_family',
    'former_name',
    'abbreviation',
    'regulatory_name',
    'generic_name',
    'japanese_name',
    'search_term'       -- manually added search term for literature retrieval
);

CREATE TYPE link_classification AS ENUM (
    'exact_product',
    'product_family',
    'manufacturer_linked',
    'indication_related',
    'irrelevant'
);

CREATE TYPE review_status AS ENUM (
    'pending',
    'confirmed',
    'reclassified',
    'rejected'
);

CREATE TYPE evidence_tier AS ENUM (
    'tier_1',   -- Official government database
    'tier_2',   -- Verified third-party (NB certificate, etc.)
    'tier_3',   -- Manufacturer self-reported
    'tier_4'    -- Secondary source (news, industry DB)
);

CREATE TYPE study_type_tag AS ENUM (
    'pivotal_trial',
    'clinical_validation',
    'retrospective',
    'prospective',
    'multicenter',
    'rct',
    'case_study',
    'systematic_review',
    'meta_analysis',
    'regulatory_submission',
    'post_market',
    'technical_validation',
    'editorial',
    'review',
    'letter',
    'other'
);

-- =============================================================================
-- PRODUCTS
-- =============================================================================

CREATE TABLE products (
    product_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_name      TEXT NOT NULL,
    manufacturer_name   TEXT NOT NULL,
    product_family      TEXT,                -- e.g., "Viz.ai" family
    intended_use        TEXT,
    disease_area        TEXT,                -- e.g., "Ophthalmology - Diabetic Retinopathy"
    modality            TEXT,                -- e.g., "CT", "MRI", "Fundus Photography"
    standalone_samd     BOOLEAN DEFAULT TRUE,
    technology_type     TEXT,                -- e.g., "deep learning", "rule-based", "ML classifier"
    description         TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_products_canonical_name ON products USING gin (canonical_name gin_trgm_ops);
CREATE INDEX idx_products_manufacturer ON products USING gin (manufacturer_name gin_trgm_ops);
CREATE INDEX idx_products_disease_area ON products (disease_area);
CREATE INDEX idx_products_modality ON products (modality);

-- =============================================================================
-- PRODUCT ALIASES (name variants for search)
-- =============================================================================

CREATE TABLE product_aliases (
    alias_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    alias_name          TEXT NOT NULL,
    alias_type          alias_type NOT NULL,
    language            TEXT DEFAULT 'en',   -- 'en', 'ja', etc.
    is_primary          BOOLEAN DEFAULT FALSE,
    source              TEXT,                -- where this alias was found
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_product_aliases_product ON product_aliases(product_id);
CREATE INDEX idx_product_aliases_name ON product_aliases USING gin (alias_name gin_trgm_ops);
CREATE INDEX idx_product_aliases_type ON product_aliases(alias_type);

-- =============================================================================
-- MANUFACTURER ALIASES
-- =============================================================================

CREATE TABLE manufacturer_aliases (
    alias_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    alias_name          TEXT NOT NULL,
    is_former_name      BOOLEAN DEFAULT FALSE,
    effective_from      DATE,
    effective_to        DATE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_manufacturer_aliases_product ON manufacturer_aliases(product_id);
CREATE INDEX idx_manufacturer_aliases_name ON manufacturer_aliases USING gin (alias_name gin_trgm_ops);

-- =============================================================================
-- REGULATORY ENTRIES (per-region regulatory records for a product)
-- =============================================================================

CREATE TABLE product_regulatory_entries (
    entry_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    region              region_code NOT NULL,
    country             TEXT,                -- specific country if relevant (e.g., for EU member states)
    regulatory_pathway  regulatory_pathway NOT NULL,
    regulatory_status_raw TEXT,              -- original status text as-is from source
    regulatory_status   regulatory_status_normalized NOT NULL,
    regulatory_id       TEXT,                -- e.g., K210000, DEN200000, PMA P200000
    clearance_date      DATE,
    expiration_date     DATE,                -- for EU certificates with expiry
    device_class        TEXT,                -- e.g., "Class II", "クラスIII"
    product_code        TEXT,                -- FDA product code, JMDN code, etc.
    review_panel        TEXT,                -- FDA advisory committee
    applicant           TEXT,                -- may differ from manufacturer
    source_url          TEXT,
    source_document     TEXT,                -- link to approval letter, review report, etc.
    evidence_tier       evidence_tier NOT NULL DEFAULT 'tier_1',
    raw_data            JSONB,               -- full raw record from source
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_entries_product ON product_regulatory_entries(product_id);
CREATE INDEX idx_reg_entries_region ON product_regulatory_entries(region);
CREATE INDEX idx_reg_entries_regulatory_id ON product_regulatory_entries(regulatory_id);
CREATE INDEX idx_reg_entries_pathway ON product_regulatory_entries(regulatory_pathway);
CREATE INDEX idx_reg_entries_status ON product_regulatory_entries(regulatory_status);
CREATE INDEX idx_reg_entries_date ON product_regulatory_entries(clearance_date);

-- Prevent duplicate regulatory entries for the same product + region + regulatory_id
CREATE UNIQUE INDEX idx_reg_entries_unique
    ON product_regulatory_entries(product_id, region, regulatory_id)
    WHERE regulatory_id IS NOT NULL;

-- =============================================================================
-- PAPERS
-- =============================================================================

CREATE TABLE papers (
    paper_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title               TEXT NOT NULL,
    abstract            TEXT,
    doi                 TEXT UNIQUE,
    pmid                TEXT UNIQUE,
    pmcid               TEXT,
    openalex_id         TEXT,
    journal             TEXT,
    publication_date    DATE,
    publication_year    INTEGER,
    volume              TEXT,
    issue               TEXT,
    pages               TEXT,
    language            TEXT DEFAULT 'en',
    is_open_access      BOOLEAN,
    fulltext_available  BOOLEAN DEFAULT FALSE,
    citation_count      INTEGER,
    source              TEXT,                -- 'pubmed', 'europe_pmc', 'openalex'
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_papers_doi ON papers(doi);
CREATE INDEX idx_papers_pmid ON papers(pmid);
CREATE INDEX idx_papers_title ON papers USING gin (title gin_trgm_ops);
CREATE INDEX idx_papers_year ON papers(publication_year);
CREATE INDEX idx_papers_journal ON papers(journal);

-- =============================================================================
-- PAPER AUTHORS
-- =============================================================================

CREATE TABLE paper_authors (
    author_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    paper_id            UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    author_name         TEXT NOT NULL,
    affiliation         TEXT,
    orcid               TEXT,
    author_position     INTEGER,             -- 1 = first author, etc.
    is_corresponding    BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_paper_authors_paper ON paper_authors(paper_id);
CREATE INDEX idx_paper_authors_affiliation ON paper_authors USING gin (affiliation gin_trgm_ops);

-- =============================================================================
-- PAPER STUDY TAGS
-- =============================================================================

CREATE TABLE paper_study_tags (
    paper_id            UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    tag                 study_type_tag NOT NULL,
    confidence          REAL,                -- 0.0 - 1.0
    source              TEXT,                -- 'auto' or 'human'
    PRIMARY KEY (paper_id, tag)
);

-- =============================================================================
-- PRODUCT-PAPER LINKS (the core linking table)
-- =============================================================================

CREATE TABLE product_paper_links (
    link_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    paper_id            UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    link_classification link_classification NOT NULL,
    confidence_score    REAL NOT NULL,       -- 0.0 - 1.0
    raw_score           REAL,                -- unnormalized score from feature weights
    matched_terms       TEXT[],              -- which terms matched
    match_locations     JSONB,               -- {"title": [...], "abstract": [...], "fulltext": [...]}
    rationale           TEXT,                -- human-readable explanation
    human_review_needed BOOLEAN DEFAULT FALSE,
    review_status       review_status DEFAULT 'pending',
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    review_notes        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, paper_id)
);

CREATE INDEX idx_pp_links_product ON product_paper_links(product_id);
CREATE INDEX idx_pp_links_paper ON product_paper_links(paper_id);
CREATE INDEX idx_pp_links_classification ON product_paper_links(link_classification);
CREATE INDEX idx_pp_links_score ON product_paper_links(confidence_score DESC);
CREATE INDEX idx_pp_links_review ON product_paper_links(human_review_needed, review_status);

-- =============================================================================
-- LINK SCORE DETAILS (feature-level breakdown)
-- =============================================================================

CREATE TABLE link_score_details (
    link_id             UUID NOT NULL REFERENCES product_paper_links(link_id) ON DELETE CASCADE,
    feature_name        TEXT NOT NULL,       -- e.g., 'product_name_in_title'
    feature_value       REAL NOT NULL,       -- 0.0 or 1.0 (binary) or continuous
    weight              REAL NOT NULL,       -- weight applied
    weighted_score      REAL NOT NULL,       -- feature_value * weight
    evidence            TEXT,                -- the matched text snippet
    PRIMARY KEY (link_id, feature_name)
);

-- =============================================================================
-- SEARCH QUERIES (track what was searched per product)
-- =============================================================================

CREATE TABLE search_queries (
    query_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    source              TEXT NOT NULL,       -- 'pubmed', 'europe_pmc', 'openalex'
    query_level         INTEGER NOT NULL,    -- 1=exact, 2=family, 3=manufacturer, 4=reg_id, 5=indication
    query_text          TEXT NOT NULL,       -- the actual query string
    result_count        INTEGER,
    papers_retrieved    INTEGER,
    papers_linked       INTEGER,
    executed_at         TIMESTAMPTZ,
    status              TEXT DEFAULT 'pending', -- pending, completed, failed
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_search_queries_product ON search_queries(product_id);
CREATE INDEX idx_search_queries_status ON search_queries(status);

-- =============================================================================
-- INGESTION LOG (track data imports)
-- =============================================================================

CREATE TABLE ingestion_log (
    log_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source              TEXT NOT NULL,       -- 'fda_510k', 'fda_denovo', 'fda_pma', 'fda_aiml', 'pmda', 'eudamed'
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    status              TEXT DEFAULT 'running', -- running, completed, failed
    records_fetched     INTEGER DEFAULT 0,
    records_created     INTEGER DEFAULT 0,
    records_updated     INTEGER DEFAULT 0,
    records_skipped     INTEGER DEFAULT 0,
    error_message       TEXT,
    parameters          JSONB                -- query params, date ranges, etc.
);

-- =============================================================================
-- VIEWS
-- =============================================================================

-- Product with evidence counts
CREATE OR REPLACE VIEW product_evidence_summary AS
SELECT
    p.product_id,
    p.canonical_name,
    p.manufacturer_name,
    p.disease_area,
    p.modality,
    COUNT(*) FILTER (WHERE ppl.link_classification = 'exact_product') AS exact_product_count,
    COUNT(*) FILTER (WHERE ppl.link_classification = 'product_family') AS product_family_count,
    COUNT(*) FILTER (WHERE ppl.link_classification = 'manufacturer_linked') AS manufacturer_linked_count,
    COUNT(*) FILTER (WHERE ppl.link_classification = 'indication_related') AS indication_related_count,
    COUNT(*) FILTER (WHERE ppl.human_review_needed = TRUE AND ppl.review_status = 'pending') AS pending_review_count
FROM products p
LEFT JOIN product_paper_links ppl ON p.product_id = ppl.product_id
GROUP BY p.product_id;

-- Review queue
CREATE OR REPLACE VIEW review_queue AS
SELECT
    ppl.link_id,
    p.canonical_name AS product_name,
    pa.title AS paper_title,
    ppl.link_classification,
    ppl.confidence_score,
    ppl.matched_terms,
    ppl.rationale,
    ppl.created_at
FROM product_paper_links ppl
JOIN products p ON p.product_id = ppl.product_id
JOIN papers pa ON pa.paper_id = ppl.paper_id
WHERE ppl.human_review_needed = TRUE
  AND ppl.review_status = 'pending'
ORDER BY ppl.confidence_score DESC;

-- Products with regulatory info (flattened for listing)
CREATE OR REPLACE VIEW product_listing AS
SELECT
    p.product_id,
    p.canonical_name,
    p.manufacturer_name,
    p.product_family,
    p.intended_use,
    p.disease_area,
    p.modality,
    p.standalone_samd,
    array_agg(DISTINCT pre.region) AS regions,
    MIN(pre.clearance_date) AS first_clearance_date,
    jsonb_agg(
        jsonb_build_object(
            'region', pre.region,
            'pathway', pre.regulatory_pathway,
            'status', pre.regulatory_status,
            'regulatory_id', pre.regulatory_id,
            'date', pre.clearance_date
        )
    ) AS regulatory_entries
FROM products p
LEFT JOIN product_regulatory_entries pre ON p.product_id = pre.product_id
GROUP BY p.product_id;
