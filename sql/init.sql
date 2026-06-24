-- ============================================================
-- LedgerMind PostgreSQL Schema
-- Phase 2 — Two-role security model
-- ledger        = admin/migration user (owns schema)
-- ledgermind_app = application user (RLS enforced)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- APPLICATION USER
-- Created here so grants below work in the same script.
-- Password matches docker-compose APP_DB_PASSWORD env var.
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledgermind_app') THEN
        CREATE ROLE ledgermind_app WITH LOGIN PASSWORD 'app_dev_pass' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

-- ============================================================
-- TENANTS
-- ============================================================
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    plan        TEXT        NOT NULL DEFAULT 'free',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    user_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    email       TEXT        NOT NULL UNIQUE,
    role        TEXT        NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- DOCUMENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS documents (
    doc_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    company          TEXT        NOT NULL,
    ticker           TEXT,
    fiscal_year      TEXT,
    quarter          TEXT,
    doc_type         TEXT        NOT NULL CHECK (doc_type IN (
                         'annual_report', 'quarterly_result',
                         'drhp', 'earnings_transcript'
                     )),
    financial_type   TEXT        NOT NULL CHECK (financial_type IN ('consolidated', 'standalone')),
    filing_date      DATE        NOT NULL,
    version          TEXT        NOT NULL DEFAULT 'v1',
    is_latest        BOOLEAN     NOT NULL DEFAULT TRUE,
    sha256_checksum  TEXT        UNIQUE,
    ingestion_state  TEXT        NOT NULL DEFAULT 'uploaded' CHECK (ingestion_state IN (
                         'uploaded', 'processing', 'indexed', 'failed'
                     )),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- FINANCIALS
-- ============================================================
CREATE TABLE IF NOT EXISTS financials (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    doc_id           UUID        NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    company          TEXT        NOT NULL,
    ticker           TEXT,
    fiscal_year      TEXT        NOT NULL,
    quarter          TEXT,
    financial_type   TEXT        NOT NULL CHECK (financial_type IN ('consolidated', 'standalone')),
    metric           TEXT        NOT NULL,
    value            NUMERIC     NOT NULL,
    unit             TEXT        NOT NULL DEFAULT 'crore_inr',
    filing_date      DATE        NOT NULL,
    is_latest        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_financials_latest
    ON financials (tenant_id, company, fiscal_year, quarter, financial_type, metric)
    WHERE is_latest = TRUE;

-- ============================================================
-- AUDIT LOG
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID,
    user_id              UUID,
    query_text           TEXT,
    query_path           TEXT        CHECK (query_path IN (
                             'semantic', 'quantitative', 'cross_examination', 'blocked'
                         )),
    retrieved_chunk_ids  TEXT[],
    vector_scores        NUMERIC[],
    reranker_scores      NUMERIC[],
    dsl_generated        JSONB,
    sql_executed         TEXT,
    confidence_score     NUMERIC,
    response_text        TEXT,
    cache_hit            BOOLEAN     DEFAULT FALSE,
    latency_ms           INTEGER,
    tokens_used          INTEGER,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- GRANTS — ledgermind_app gets DML only, never DDL
-- ============================================================
GRANT USAGE ON SCHEMA public TO ledgermind_app;

GRANT SELECT, INSERT, UPDATE ON
    tenants, users, documents, financials, audit_log
TO ledgermind_app;

-- audit_log is append-only — no UPDATE or DELETE granted, ever

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================
ALTER TABLE documents  ENABLE ROW LEVEL SECURITY;
ALTER TABLE financials ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log  ENABLE ROW LEVEL SECURITY;

-- FORCE applies RLS to table owner (ledger) too — belt + suspenders
ALTER TABLE documents  FORCE ROW LEVEL SECURITY;
ALTER TABLE financials FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_log  FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_documents ON documents
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

CREATE POLICY tenant_isolation_financials ON financials
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

CREATE POLICY tenant_isolation_audit ON audit_log
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_financials_lookup
    ON financials (company, fiscal_year, financial_type, metric, is_latest);

CREATE INDEX IF NOT EXISTS idx_documents_company
    ON documents (company, fiscal_year, financial_type, is_latest);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_time
    ON audit_log (tenant_id, created_at DESC);