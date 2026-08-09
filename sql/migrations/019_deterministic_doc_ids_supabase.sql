-- 019 — deterministic doc_ids. SUPABASE ONLY.
-- Local Docker Postgres is 018 (already applied 2026-08-09). Applying this
-- file to local aborts at the first assertion: local's old ids are different
-- and local carries the transcript plus two ZOMATO seed fixtures.
--
-- doc_id was uuid4() per ingest, so each database minted its own id for the
-- same PDF while ONE Qdrant collection served both — only one side's
-- citations could resolve to a documents row. doc_id is now
-- uuid5(7d252b65-ca68-559a-b80c-47363c5df49e, sha256_checksum), matching
-- derive_doc_id() in app/ingestion/document_classifier.py. Both databases
-- derive the same ids independently from sha256_checksum, which is byte-
-- identical across the two (verified 2026-08-09).
--
-- ALSO CORRECTS PAYTM'S LABELS. Supabase reads quarterly_result/Q4 while
-- local reads annual_report/NULL; regression_check.DOCUMENTS confirms annual
-- (128 annual rows vs 49 Q4). Folded into THIS transaction deliberately:
-- after the doc_id change both databases share a primary key, so leaving the
-- labels split would mean one document that two databases describe
-- differently — and Phase 3 must not ingest into that state.
--
-- Requires ownership of documents and financials (postgres owns both here;
-- NOT superuser, and superuser is not what ALTER TABLE requires).
-- financials_doc_id_fkey is NO ACTION on update and NOT deferrable, so the
-- UPDATE is rejected in either order unless the constraint is removed first.
--
-- Apply with ON_ERROR_STOP=1, or in the SQL editor as one statement.
--
-- IMPLIES A FULL RE-INGEST: chunk_id is md5(doc_id:...) and IS the Qdrant
-- point ID. Qdrant is stale until Phase 3 completes.

BEGIN;

CREATE TEMP TABLE doc_id_map (old_id UUID PRIMARY KEY, new_id UUID UNIQUE, label TEXT)
  ON COMMIT DROP;

INSERT INTO doc_id_map VALUES
  ('823639b3-1e86-42c6-a6c6-8447414891bf','d662a604-2f8c-549c-9374-06400875e04d','ETERNAL FY24 consolidated'),
  ('e8d25ab8-ab0b-4c77-8d61-2e6cef62743b','ebaf1089-031d-5605-8090-846308d68dc7','ETERNAL FY24 standalone'),
  ('b8a89f63-4213-42ce-a39c-d80733329d3d','27091929-f1d5-5c8d-897c-3d6437963418','ETERNAL FY26Q4 consolidated'),
  ('fe3fee03-2495-4f29-a646-f4cb192bc848','e33b7e55-0b7b-5e38-9948-afb76e3df2dc','ETERNAL FY26Q4 standalone'),
  ('55e1549e-2ad4-4cd4-b5a5-a0fad954f925','352e249b-ca7e-508d-9a9d-377d4fe7c48c','PAYTM consolidated'),
  ('5862fad6-facd-43bd-806e-b1981cbdbebf','bbf75eac-eaa6-506f-b92b-154423882f8d','PAYTM standalone'),
  ('919ea7e3-6c95-48b6-a46a-8ca04bf59757','6a07229b-7084-59e4-a7be-86cf7de8d94e','TITAN consolidated'),
  ('ba7e525b-689b-4a9a-b6c0-5e9800e8fda8','14b698c0-b6e4-58e6-89e2-e0c0e9844edf','TITAN standalone');

-- ---- Pre-state. Nothing has been written yet. ----
DO $$
DECLARE m INT; x INT; f INT; nl INT; missing INT; taken INT; paytm INT;
BEGIN
  -- Ownership, not superuser: Supabase's postgres role is NOT superuser but
  -- DOES own both tables, which is what ALTER TABLE requires.
  ASSERT pg_has_role(current_user,
                     (SELECT tableowner FROM pg_tables WHERE tablename = 'financials'),
                     'MEMBER'),
      format('must own financials to drop its FK; current_user is %s', current_user);

  SELECT count(*) FILTER (WHERE sha256_checksum ~ '^[0-9a-f]{64}_'),
         count(*) FILTER (WHERE sha256_checksum !~ '^[0-9a-f]{64}_')
    INTO m, x FROM documents;
  ASSERT m = 8, format('expected 8 migratable documents, found %s', m);
  ASSERT x = 0, format('expected 0 excluded documents on Supabase, found %s', x);

  SELECT count(*), count(*) FILTER (WHERE is_latest = FALSE) INTO f, nl FROM financials;
  ASSERT f  = 1437, format('expected 1437 financials, found %s', f);
  ASSERT nl = 0,    format('expected 0 is_latest=FALSE rows, found %s', nl);

  SELECT count(*) INTO missing
    FROM doc_id_map m2 LEFT JOIN documents d ON d.doc_id = m2.old_id
    WHERE d.doc_id IS NULL;
  ASSERT missing = 0, format('%s mapped old_ids absent from documents', missing);

  SELECT count(*) INTO taken
    FROM doc_id_map m3 JOIN documents d ON d.doc_id = m3.new_id;
  ASSERT taken = 0, format('%s derived ids already in use', taken);

  -- The PAYTM labels must still be the DIVERGED ones. If they have already
  -- been corrected by some other route, abort rather than re-apply blindly.
  SELECT count(*) INTO paytm FROM documents
   WHERE company = 'PAYTM' AND doc_type = 'quarterly_result' AND quarter = 'Q4';
  ASSERT paytm = 2, format('expected 2 PAYTM rows at quarterly_result/Q4, found %s', paytm);
END $$;

-- ---- Children first, then parent. FK removed for the duration. ----
ALTER TABLE financials DROP CONSTRAINT financials_doc_id_fkey;

DO $$
DECLARE n INT;
BEGIN
  UPDATE financials f SET doc_id = m.new_id FROM doc_id_map m WHERE f.doc_id = m.old_id;
  GET DIAGNOSTICS n = ROW_COUNT;
  ASSERT n = 1437, format('expected 1437 financials rows remapped, got %s', n);

  UPDATE documents d SET doc_id = m.new_id FROM doc_id_map m WHERE d.doc_id = m.old_id;
  GET DIAGNOSTICS n = ROW_COUNT;
  ASSERT n = 8, format('expected 8 documents rows remapped, got %s', n);

  -- PAYTM labels -> match local and regression_check.DOCUMENTS.
  UPDATE documents SET doc_type = 'annual_report', quarter = NULL
   WHERE company = 'PAYTM' AND doc_type = 'quarterly_result' AND quarter = 'Q4';
  GET DIAGNOSTICS n = ROW_COUNT;
  ASSERT n = 2, format('expected 2 PAYTM label corrections, got %s', n);
END $$;

ALTER TABLE financials
  ADD CONSTRAINT financials_doc_id_fkey
  FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE;

-- ---- Post-state. ----
DO $$
DECLARE f INT; nl INT; d INT; landed INT; c INT; paytm INT;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE is_latest = FALSE) INTO f, nl FROM financials;
  ASSERT f  = 1437, format('financials count changed to %s', f);
  ASSERT nl = 0,    format('%s rows flipped to is_latest=FALSE', nl);

  SELECT count(DISTINCT doc_id) INTO d FROM financials;
  ASSERT d = 8, format('expected 8 distinct doc_ids in financials, got %s', d);

  -- uuid5 is NOT computed here: that needs uuid-ossp, and an extension that
  -- must behave identically on two databases is a dependency this migration
  -- does not need. Verified against derive_doc_id() in Python, post-commit.
  SELECT count(*) INTO landed
    FROM documents d2 JOIN doc_id_map m ON d2.doc_id = m.new_id;
  ASSERT landed = 8, format('expected 8 documents at derived ids, got %s', landed);

  SELECT count(*) INTO paytm FROM documents
   WHERE company = 'PAYTM' AND doc_type = 'annual_report' AND quarter IS NULL;
  ASSERT paytm = 2, format('expected 2 PAYTM rows at annual_report/NULL, got %s', paytm);

  SELECT count(*) INTO c FROM pg_constraint
   WHERE conname = 'financials_doc_id_fkey' AND contype = 'f';
  ASSERT c = 1, 'financials_doc_id_fkey was not restored';
END $$;

INSERT INTO schema_migrations (filename, applied_at, note) VALUES
  ('019_deterministic_doc_ids_supabase.sql', NOW(),
   'doc_id -> uuid5(LEDGERMIND_DOC_NS, sha256_checksum); 8 documents, 1437 financials remapped; PAYTM relabelled annual_report/NULL to match local');

COMMIT;
