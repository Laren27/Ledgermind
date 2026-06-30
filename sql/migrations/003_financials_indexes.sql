-- Migration 003: Financials table indexes for is_latest enforcement
-- and idempotent re-ingestion.
--
-- Run this on the existing DB. Safe to re-run (uses IF NOT EXISTS / OR REPLACE).
-- Assumes financials table already exists from Phase 2 schema.
--
-- Connect as: ledger (admin user, POSTGRES_USER)
-- psql -U ledger -d ledgermind -f sql/migrations/003_financials_indexes.sql

-- ---------------------------------------------------------------------------
-- Index 1: Idempotent re-ingestion guard
--
-- Prevents duplicate rows when the same PDF is ingested twice.
-- Scope: one row per (doc_id, metric, fiscal_year, quarter, financial_type).
-- NULL-safe: quarter can be NULL for annual reports.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_financials_per_doc
ON financials (doc_id, metric, fiscal_year, financial_type, COALESCE(quarter, ''))
;

-- ---------------------------------------------------------------------------
-- Index 2: is_latest truth enforcement (partial unique index)
--
-- Enforces that at most ONE row has is_latest = TRUE for any given
-- (company, metric, fiscal_year, quarter, financial_type) combination.
-- This is what makes Truth Resolution deterministic — no query can
-- accidentally return two "latest" values for the same metric/period.
--
-- NULL-safe: COALESCE(quarter, '') handles annual reports (null quarter).
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_financials_latest
ON financials (company, metric, fiscal_year, financial_type, COALESCE(quarter, ''))
WHERE is_latest = TRUE
;

-- ---------------------------------------------------------------------------
-- Index 3: Query performance
-- Most retrieval queries filter on (company, fiscal_year, financial_type, is_latest).
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_financials_query
ON financials (company, fiscal_year, financial_type, is_latest)
;

-- Verify
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'financials'
ORDER BY indexname;