-- =============================================================================
-- SaMD Evidence Tracker — PostgreSQL 9.5 Compatible Schema
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- =============================================================================
-- PRODUCTS
-- =============================================================================

CREATE TABLE products (
    product_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_name      TEXT NOT NULL,
    manufacturer_name   TEXT NOT NULL,
    product_family      TEXT,
    intended_use        TEXT,
    disease_area        TEXT,
    modality            TEXT,
    standalone_samd     BOOLEAN DEFAULT TRUE,
    technology_type     TEXT,
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
-- PRODUCT ALIASES
-- =============================================================================

CREATE TABLE product_aliases (
    alias_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    alias_name          TEXT NOT NULL,
    alias_type          TEXT NOT NULL,  -- trade_name, product_family, former_name, abbreviation, regulatory_name, generic_name, japanese_name, search_term
    language            TEXT DEFAULT 'en',
    is_primary          BOOLEAN DEFAULT FALSE,
    source              TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_product_aliases_product ON product_aliases(product_id);
CREATE INDEX idx_product_aliases_name ON product_aliases USING gin (alias_name gin_trgm_ops);

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
-- REGULATORY ENTRIES
-- =============================================================================

CREATE TABLE product_regulatory_entries (
    entry_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    region              TEXT NOT NULL,       -- us, jp, eu
    country             TEXT,
    regulatory_pathway  TEXT NOT NULL,       -- 510k, de_novo, pma, approval, certification, etc.
    regulatory_status_raw TEXT,
    regulatory_status   TEXT NOT NULL,       -- cleared, authorized, approved, certified, etc.
    regulatory_id       TEXT,
    clearance_date      DATE,
    expiration_date     DATE,
    device_class        TEXT,
    product_code        TEXT,
    review_panel        TEXT,
    applicant           TEXT,
    source_url          TEXT,
    source_document     TEXT,
    evidence_tier       TEXT NOT NULL DEFAULT 'tier_1',
    raw_data            JSONB,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_entries_product ON product_regulatory_entries(product_id);
CREATE INDEX idx_reg_entries_region ON product_regulatory_entries(region);
CREATE INDEX idx_reg_entries_regulatory_id ON product_regulatory_entries(regulatory_id);
CREATE INDEX idx_reg_entries_pathway ON product_regulatory_entries(regulatory_pathway);
CREATE INDEX idx_reg_entries_status ON product_regulatory_entries(regulatory_status);
CREATE INDEX idx_reg_entries_date ON product_regulatory_entries(clearance_date);

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
    source              TEXT,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_papers_doi ON papers(doi);
CREATE INDEX idx_papers_pmid ON papers(pmid);
CREATE INDEX idx_papers_title ON papers USING gin (title gin_trgm_ops);
CREATE INDEX idx_papers_year ON papers(publication_year);

-- =============================================================================
-- PAPER AUTHORS
-- =============================================================================

CREATE TABLE paper_authors (
    author_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    paper_id            UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    author_name         TEXT NOT NULL,
    affiliation         TEXT,
    orcid               TEXT,
    author_position     INTEGER,
    is_corresponding    BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_paper_authors_paper ON paper_authors(paper_id);
CREATE INDEX idx_paper_authors_affiliation ON paper_authors USING gin (affiliation gin_trgm_ops);

-- =============================================================================
-- PAPER STUDY TAGS
-- =============================================================================

CREATE TABLE paper_study_tags (
    paper_id            UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    tag                 TEXT NOT NULL,
    confidence          REAL,
    source              TEXT,
    PRIMARY KEY (paper_id, tag)
);

-- =============================================================================
-- PRODUCT-PAPER LINKS
-- =============================================================================

CREATE TABLE product_paper_links (
    link_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    paper_id            UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    link_classification TEXT NOT NULL,       -- exact_product, product_family, manufacturer_linked, indication_related
    confidence_score    REAL NOT NULL,
    raw_score           REAL,
    matched_terms       TEXT[],
    match_locations     JSONB,
    rationale           TEXT,
    human_review_needed BOOLEAN DEFAULT FALSE,
    review_status       TEXT DEFAULT 'pending',
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
-- LINK SCORE DETAILS
-- =============================================================================

CREATE TABLE link_score_details (
    link_id             UUID NOT NULL REFERENCES product_paper_links(link_id) ON DELETE CASCADE,
    feature_name        TEXT NOT NULL,
    feature_value       REAL NOT NULL,
    weight              REAL NOT NULL,
    weighted_score      REAL NOT NULL,
    evidence            TEXT,
    PRIMARY KEY (link_id, feature_name)
);

-- =============================================================================
-- SEARCH QUERIES
-- =============================================================================

CREATE TABLE search_queries (
    query_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id          UUID NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    source              TEXT NOT NULL,
    query_level         INTEGER NOT NULL,
    query_text          TEXT NOT NULL,
    result_count        INTEGER,
    papers_retrieved    INTEGER,
    papers_linked       INTEGER,
    executed_at         TIMESTAMPTZ,
    status              TEXT DEFAULT 'pending',
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_search_queries_product ON search_queries(product_id);

-- =============================================================================
-- INGESTION LOG
-- =============================================================================

CREATE TABLE ingestion_log (
    log_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source              TEXT NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    status              TEXT DEFAULT 'running',
    records_fetched     INTEGER DEFAULT 0,
    records_created     INTEGER DEFAULT 0,
    records_updated     INTEGER DEFAULT 0,
    records_skipped     INTEGER DEFAULT 0,
    error_message       TEXT,
    parameters          JSONB
);
