-- ============================================================
-- LedgerMind Seed Data — Phase 2 Verification Only
-- ============================================================

-- Two tenants
INSERT INTO tenants (tenant_id, name, plan) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'Tenant Alpha', 'free'),
    ('b0000000-0000-0000-0000-000000000002', 'Tenant Beta',  'free');

-- One user per tenant
INSERT INTO users (tenant_id, email, role) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'analyst@alpha.com', 'analyst'),
    ('b0000000-0000-0000-0000-000000000002', 'analyst@beta.com',  'analyst');

-- Documents for Tenant Alpha
INSERT INTO documents (
    doc_id, tenant_id, company, ticker,
    fiscal_year, doc_type, financial_type,
    filing_date, version, is_latest, sha256_checksum, ingestion_state
) VALUES
    (
        'a1000000-0000-0000-0000-000000000001',
        'a0000000-0000-0000-0000-000000000001',
        'ZOMATO', 'ZOMATO.NS',
        'FY24', 'annual_report', 'consolidated',
        '2024-08-31', 'v1', TRUE,
        'abc123_zomato_fy24', 'indexed'
    ),
    (
        'a1000000-0000-0000-0000-000000000002',
        'a0000000-0000-0000-0000-000000000001',
        'ZOMATO', 'ZOMATO.NS',
        'FY25', 'annual_report', 'consolidated',
        '2025-08-31', 'v1', TRUE,
        'abc123_zomato_fy25', 'indexed'
    );

-- Financial records — including restatement scenario
-- FY24 revenue as reported in FY24 filing (will be superseded)
INSERT INTO financials (
    tenant_id, doc_id, company, ticker,
    fiscal_year, financial_type, metric, value, filing_date, is_latest
) VALUES
    (
        'a0000000-0000-0000-0000-000000000001',
        'a1000000-0000-0000-0000-000000000001',
        'ZOMATO', 'ZOMATO.NS',
        'FY24', 'consolidated', 'revenue', 12114,
        '2024-08-31', FALSE   -- superseded by FY25 restatement below
    ),
    (
        'a0000000-0000-0000-0000-000000000001',
        'a1000000-0000-0000-0000-000000000002',
        'ZOMATO', 'ZOMATO.NS',
        'FY24', 'consolidated', 'revenue', 11925,  -- restated figure
        '2025-08-31', TRUE    -- this is now source of truth
    ),
    (
        'a0000000-0000-0000-0000-000000000001',
        'a1000000-0000-0000-0000-000000000002',
        'ZOMATO', 'ZOMATO.NS',
        'FY25', 'consolidated', 'revenue', 17500,
        '2025-08-31', TRUE
    );

-- Tenant Beta gets NO Zomato data — RLS verification target