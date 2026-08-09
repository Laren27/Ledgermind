-- 018 — deterministic doc_ids. LOCAL DOCKER POSTGRES ONLY.
-- Supabase is 019. Applying this file to Supabase aborts at the first
-- assertion (these old ids do not exist there), which is intended.
--
-- doc_id was uuid4() per ingest, so each database minted its own id for the
-- same PDF while ONE Qdrant collection served both — only one side's
-- citations could resolve to a documents row. doc_id is now
-- uuid5(7d252b65-ca68-559a-b80c-47363c5df49e, sha256_checksum), matching
-- derive_doc_id() in app/ingestion/document_classifier.py.
--
-- MUST BE APPLIED AS `ledger` OR A SUPERUSER: this drops and restores an FK,
-- and ledgermind_app owns neither table. financials_doc_id_fkey is NO ACTION
-- on update and NOT deferrable, so the UPDATE is rejected in either order
-- unless the constraint is removed first. Definition captured verbatim from
-- pg_get_constraintdef on 2026-08-09.
--
-- Apply with ON_ERROR_STOP=1. Without it psql continues past a failed
-- statement and COMMIT succeeds on a half-applied file.
--
-- IMPLIES A FULL RE-INGEST: chunk_id is md5(doc_id:...) and IS the Qdrant
-- point ID. Qdrant is stale from this commit until Phase 3 completes.

BEGIN;

CREATE TEMP TABLE doc_id_map (old_id UUID PRIMARY KEY, new_id UUID UNIQUE, label TEXT)
  ON COMMIT DROP;

INSERT INTO doc_id_map VALUES
  ('bd300f21-ae87-453a-b8e5-0c640bb82c51','d662a604-2f8c-549c-9374-06400875e04d','ETERNAL FY24 consolidated'),
  ('e46f92d7-bd14-44e2-9ee5-4d6503099b23','ebaf1089-031d-5605-8090-846308d68dc7','ETERNAL FY24 standalone'),
  ('b50dc351-9d99-4683-b097-b5093a9bbe8f','27091929-f1d5-5c8d-897c-3d6437963418','ETERNAL FY26Q4 consolidated'),
  ('4c024e0f-ab26-4007-8d9a-167420d715e3','e33b7e55-0b7b-5e38-9948-afb76e3df2dc','ETERNAL FY26Q4 standalone'),
  ('65ee6ef4-acab-49d1-aa37-cb47d97cd9d9','1d8061a3-cb75-5524-a897-48a7baa81a1a','ETERNAL transcript'),
  ('a529de7a-a2eb-4f8f-8d34-98dff7b72956','352e249b-ca7e-508d-9a9d-377d4fe7c48c','PAYTM consolidated'),
  ('f6390981-c503-4c08-8893-0f12f85b881f','bbf75eac-eaa6-506f-b92b-154423882f8d','PAYTM standalone'),
  ('7f3f7eb2-d62c-4ac1-9816-f70cc7adf5fb','6a07229b-7084-59e4-a7be-86cf7de8d94e','TITAN consolidated'),
  ('ab1cb2fb-b380-4920-bb05-c5e763030886','14b698c0-b6e4-58e6-89e2-e0c0e9844edf','TITAN standalone');

-- ---- Pre-state. Nothing has been written yet. ----
DO $$
DECLARE m INT; x INT; f INT; nl INT; missing INT; taken INT;
BEGIN
  -- Fail here, with a readable message, rather than at the ALTER TABLE with a
  -- permissions error that reads like a configuration problem.
  ASSERT pg_has_role(current_user, 'ledger', 'MEMBER')
      OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
      format('must run as ledger or a superuser; current_user is %s', current_user);

  SELECT count(*) FILTER (WHERE sha256_checksum ~ '^[0-9a-f]{64}_'),
         count(*) FILTER (WHERE sha256_checksum !~ '^[0-9a-f]{64}_')
    INTO m, x FROM documents;
  ASSERT m = 9, format('expected 9 migratable documents, found %s', m);
  ASSERT x = 2, format('expected 2 excluded (ZOMATO seed fixtures), found %s', x);

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
  ASSERT n = 9, format('expected 9 documents rows remapped, got %s', n);
END $$;

ALTER TABLE financials
  ADD CONSTRAINT financials_doc_id_fkey
  FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE;

-- ---- Post-state. ----
DO $$
DECLARE f INT; nl INT; d INT; landed INT; c INT;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE is_latest = FALSE) INTO f, nl FROM financials;
  ASSERT f  = 1437, format('financials count changed to %s', f);
  ASSERT nl = 0,    format('%s rows flipped to is_latest=FALSE', nl);

  SELECT count(DISTINCT doc_id) INTO d FROM financials;
  ASSERT d = 8, format('expected 8 distinct doc_ids in financials, got %s', d);

  -- uuid5 is NOT computed here: that needs uuid-ossp, and an extension that
  -- must behave identically on two databases is a dependency this migration
  -- does not need. The ids are verified against derive_doc_id() in Python,
  -- post-commit.
  SELECT count(*) INTO landed
    FROM documents d2 JOIN doc_id_map m ON d2.doc_id = m.new_id;
  ASSERT landed = 9, format('expected 9 documents at derived ids, got %s', landed);

  SELECT count(*) INTO c FROM pg_constraint
   WHERE conname = 'financials_doc_id_fkey' AND contype = 'f';
  ASSERT c = 1, 'financials_doc_id_fkey was not restored';
END $$;

INSERT INTO schema_migrations (filename, applied_at, note) VALUES
  ('018_deterministic_doc_ids_local.sql', NOW(),
   'doc_id -> uuid5(LEDGERMIND_DOC_NS, sha256_checksum); 9 documents, 1437 financials remapped');

COMMIT;
