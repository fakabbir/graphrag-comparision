-- SEC EDGAR demo: relational store for raw disclosure text.
-- Mirrors README.md §4, trimmed to what the xbrlrss-2022-01 slice supports.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── entities ────────────────────────────────────────────────────────────────
CREATE TABLE company (
    cik                 INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    sic                 CHAR(4),
    sic_description     TEXT,
    fiscal_year_end     CHAR(4),
    assistant_director  TEXT
);
CREATE INDEX company_name_trgm ON company USING GIN (name gin_trgm_ops);
CREATE INDEX company_sic_idx   ON company (sic);

-- ── provenance spine ────────────────────────────────────────────────────────
CREATE TABLE filing (
    accession_number    CHAR(20) PRIMARY KEY,
    company_cik         INTEGER NOT NULL REFERENCES company(cik),
    form_type           TEXT NOT NULL,
    filing_date         DATE NOT NULL,
    period_of_report    DATE,
    acceptance_dt       TIMESTAMP,
    file_number         TEXT,
    index_url           TEXT,
    primary_doc_url     TEXT
);
CREATE INDEX filing_company_idx ON filing (company_cik, form_type, filing_date DESC);
CREATE INDEX filing_form_idx    ON filing (form_type, filing_date DESC);

CREATE TABLE filing_document (
    accession_number    CHAR(20) NOT NULL REFERENCES filing(accession_number),
    sequence            INTEGER NOT NULL,
    doc_type            TEXT,
    filename            TEXT,
    description         TEXT,
    size_bytes          BIGINT,
    inline_xbrl         BOOLEAN,
    url                 TEXT,
    PRIMARY KEY (accession_number, sequence)
);
CREATE INDEX filing_document_type_idx ON filing_document (doc_type);

-- ── the narrative text the GraphRAG hop pulls from ──────────────────────────
CREATE TABLE filing_section (
    accession_number    CHAR(20) NOT NULL REFERENCES filing(accession_number),
    item_code           TEXT NOT NULL,          -- '1', '1A', '7', '7A'
    item_title          TEXT,
    section_text        TEXT NOT NULL,
    char_len            INTEGER NOT NULL,
    company_cik         INTEGER NOT NULL,
    filing_date         DATE NOT NULL,
    PRIMARY KEY (accession_number, item_code)
);
CREATE INDEX filing_section_fts ON filing_section
    USING GIN (to_tsvector('english', section_text));
CREATE INDEX filing_section_cik_idx ON filing_section (company_cik, item_code);

-- ── vector store for the Vector-RAG baseline (all-MiniLM-L6-v2 = 384 dims) ──
CREATE TABLE section_chunk (
    chunk_id            BIGSERIAL PRIMARY KEY,
    accession_number    CHAR(20) NOT NULL REFERENCES filing(accession_number),
    item_code           TEXT NOT NULL,
    company_cik         INTEGER NOT NULL,
    company_name        TEXT NOT NULL,
    chunk_index         INTEGER NOT NULL,
    chunk_text          TEXT NOT NULL,
    embedding           vector(384)
);
CREATE INDEX section_chunk_hnsw ON section_chunk
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX section_chunk_acc_idx ON section_chunk (accession_number, item_code);

-- ── graph-edge source tables (also projected into Neo4j) ────────────────────
CREATE TABLE subsidiary (
    accession_number    CHAR(20) NOT NULL REFERENCES filing(accession_number),
    parent_cik          INTEGER NOT NULL,
    subsidiary_name     TEXT NOT NULL,
    name_normalized     TEXT NOT NULL,
    jurisdiction        TEXT,
    PRIMARY KEY (accession_number, name_normalized)
);
CREATE INDEX subsidiary_parent_idx ON subsidiary (parent_cik);
CREATE INDEX subsidiary_name_trgm  ON subsidiary USING GIN (subsidiary_name gin_trgm_ops);

CREATE TABLE reporting_owner (
    accession_number    CHAR(20) NOT NULL,
    owner_cik           INTEGER NOT NULL,
    owner_name          TEXT NOT NULL,
    issuer_cik          INTEGER NOT NULL,
    issuer_name         TEXT,
    relationship        TEXT,
    is_officer          BOOLEAN NOT NULL DEFAULT FALSE,
    is_director         BOOLEAN NOT NULL DEFAULT FALSE,
    is_ten_pct_owner    BOOLEAN NOT NULL DEFAULT FALSE,
    is_other            BOOLEAN NOT NULL DEFAULT FALSE,
    officer_title       TEXT,
    filing_date         DATE,
    period_of_report    DATE,
    PRIMARY KEY (accession_number, owner_cik)
);
CREATE INDEX reporting_owner_owner_idx  ON reporting_owner (owner_cik);
CREATE INDEX reporting_owner_issuer_idx ON reporting_owner (issuer_cik);
CREATE INDEX reporting_owner_name_trgm  ON reporting_owner USING GIN (owner_name gin_trgm_ops);

CREATE TABLE filing_auditor (
    accession_number    CHAR(20) PRIMARY KEY REFERENCES filing(accession_number),
    company_cik         INTEGER NOT NULL,
    auditor_name        TEXT NOT NULL,
    auditor_location    TEXT,
    pcaob_firm_id       INTEGER,
    fiscal_year         INTEGER
);
CREATE INDEX filing_auditor_name_idx ON filing_auditor (auditor_name);

-- ── convenience view used by the text-to-SQL baseline ───────────────────────
CREATE VIEW v_risk_factors AS
SELECT s.accession_number, s.company_cik, c.name AS company_name,
       c.sic, c.sic_description, s.filing_date, s.char_len, s.section_text
FROM filing_section s
JOIN company c ON c.cik = s.company_cik
WHERE s.item_code = '1A';

-- Where the company node came from: 'rss' = xbrlrss-2022-01 spine (has filings/text),
-- 'form345' = appears only as a Form 3/4/5 issuer (ownership edges only).
ALTER TABLE company ADD COLUMN source TEXT NOT NULL DEFAULT 'rss';
