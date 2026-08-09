-- 020 — register the ETERNAL Q4FY26 earnings transcript on SUPABASE.
-- SUPABASE ONLY. Local already holds this row (ingested 2026-08-08).
--
-- WHY THIS IS AN INSERT AND NOT AN INGEST. Since 019, doc_id is
-- uuid5(LEDGERMIND_DOC_NS, sha256_checksum), so the id this row must carry is
-- derivable without parsing the PDF. The transcript's 124 Qdrant points
-- already exist under 1d8061a3-cb75-5524-a897-48a7baa81a1a; they were written
-- by the local ingest and Qdrant is shared. Inserting this row makes them
-- resolve on Supabase too. No re-embedding, no parse.
--
-- check_citation_integrity.py currently FAILS on Supabase with exactly these
-- 124 points across 1 doc_id. This migration is the fix.
--
-- EXPECT ZERO financials. A transcript has no statements; local holds none for
-- this doc_id either. That is correct, not an incomplete migration.
--
-- Values copied from local's row, read 2026-08-09. ingestion_state='indexed'
-- is truthful here: the chunks exist and are queryable.

BEGIN;

DO $$
DECLARE existing INT; chk INT; tenant INT; fin INT;
BEGIN
  SELECT count(*) INTO tenant FROM tenants
   WHERE tenant_id = 'a0000000-0000-0000-0000-000000000001';
  ASSERT tenant = 1, 'tenant a0000000-...-0001 absent; documents.tenant_id FK would reject';

  SELECT count(*) INTO existing FROM documents
   WHERE doc_id = '1d8061a3-cb75-5524-a897-48a7baa81a1a';
  ASSERT existing = 0, 'transcript doc_id already present — nothing to do';

  -- A row with this checksum under a DIFFERENT doc_id would mean the split is
  -- not fully closed and UNIQUE(sha256_checksum) would reject the insert.
  SELECT count(*) INTO chk FROM documents
   WHERE sha256_checksum = '318bb5e00927a7243ee5553bd32b7bca8cf53646c38c1164fae802cb5c4c6b3d_consolidated';
  ASSERT chk = 0, format('%s row(s) already hold the transcript checksum', chk);

  SELECT count(*) INTO fin FROM financials;
  ASSERT fin = 1437, format('expected 1437 financials before insert, found %s', fin);
END $$;

INSERT INTO documents (
    doc_id, tenant_id, company, ticker,
    fiscal_year, quarter, doc_type, financial_type,
    filing_date, version, is_latest,
    sha256_checksum, ingestion_state
) VALUES (
    '1d8061a3-cb75-5524-a897-48a7baa81a1a',
    'a0000000-0000-0000-0000-000000000001',
    'ETERNAL', 'ETERNAL',
    'FY26', 'Q4', 'earnings_transcript', 'consolidated',
    '2026-04-28', 'v1', TRUE,
    '318bb5e00927a7243ee5553bd32b7bca8cf53646c38c1164fae802cb5c4c6b3d_consolidated',
    'indexed'
);

DO $$
DECLARE docs INT; fin INT;
BEGIN
  SELECT count(*) INTO docs FROM documents;
  ASSERT docs = 9, format('expected 9 documents after insert, got %s', docs);

  SELECT count(*) INTO fin FROM financials;
  ASSERT fin = 1437, format('financials moved to %s — nothing here should touch it', fin);
END $$;

INSERT INTO schema_migrations (filename, applied_at, note) VALUES
  ('020_supabase_transcript_row.sql', NOW(),
   'registers ETERNAL Q4FY26 earnings transcript at its derived doc_id; resolves 124 dangling Qdrant points');

COMMIT;
