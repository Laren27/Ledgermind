-- Tracks uploads awaiting manual/local ingestion (Option A2: no auto-trigger
-- in the web process, since embedding-model loading OOM-crashed Render's
-- 512MB free tier when run in-process — see session notes). A CLI script
-- (backend/scripts/process_pending_uploads.py) polls this table and runs
-- ingestion locally instead.

CREATE TABLE IF NOT EXISTS pending_uploads (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL,
    storage_key   TEXT NOT NULL,
    company       TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    fiscal_year   TEXT NOT NULL,
    quarter       TEXT,
    doc_type      TEXT NOT NULL,
    filing_date   TEXT NOT NULL,
    version       TEXT NOT NULL DEFAULT 'v1',
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending / processing / done / failed
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE pending_uploads ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON pending_uploads
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT, UPDATE ON pending_uploads TO ledgermind_app;
