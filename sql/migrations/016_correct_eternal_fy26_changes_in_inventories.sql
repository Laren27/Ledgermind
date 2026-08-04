-- ============================================================
-- 016 — DATA correction: ETERNAL FY26 annual consolidated,
--       changes_in_inventories
-- ============================================================
-- NOT A SCHEMA CHANGE. Same shape and the same reasoning as migration 015; read
-- that file's header first. This corrects ONE stored value:
--
--   ETERNAL | FY26 | ANNUAL | consolidated | changes_in_inventories
--   -2002.0  ->  -2042.0
--
-- A DIFFERENT DEFECT FROM 015, on a different page. 015 corrects the misread
-- revenue on p.31 whose blast radius was measured at exactly one cell. This row
-- is printed on p.33 as "-Inventories" (a cash-flow line, reaching
-- changes_in_inventories through the alias table), and the current parser reads
-- [-2042.0, -88.0] for [FY26 annual, FY25 annual].
--
-- WHERE IT CAME FROM, AND WHY IT SAT UNNOTICED. It is NOT caused by the
-- fragment-joining fix. It is a stale value from an EARLIER parser generation:
-- one of the extraction fixes landed since the 2026-07-15 ingest re-read this
-- cell correctly, and nothing could carry that improvement into the database.
-- backfill_financials always reads existing doc_ids, so db_loader took its
-- same-doc_id branch on every record and fell through to ON CONFLICT DO
-- NOTHING — which can INSERT a metric name never seen before, but can never
-- UPDATE a figure that changed. Every extraction fix in that window could add
-- rows and could not correct one. This value is the first evidence of that
-- found in the wild; migration 015's three are the second case, and
-- --correct-values now exists to close it.
--
-- WHY AN UPDATE AND NOT A RESTATEMENT: identical to 015. The filing never
-- changed; our reading of it did. `value` only — is_latest, doc_id,
-- filing_date, id and created_at are untouched.
--
-- NOTE THE NULL QUARTER. This is an ANNUAL row, so quarter IS NULL and the
-- predicate must be `quarter IS NULL`. `quarter = NULL` is never true and would
-- match zero rows, which this migration would then correctly report as an
-- ABORT rather than silently doing nothing.
--
-- SELF-VERIFYING, exactly as 015: the UPDATE requires the value to be the
-- specific WRONG one and must affect exactly 1 row; a row already at the
-- corrected value reports SKIP so a second run is safe; anything else aborts
-- the whole transaction.
--
-- APPLIED BY HAND in the Supabase SQL editor. Supabase has NOT been queried
-- from this environment and this migration asserts nothing about what it
-- currently holds — that is what the guards are for. On the local docker
-- postgres this correction was already made by
-- `backfill_financials --company ETERNAL --apply --correct-values`.
-- ============================================================

BEGIN;

-- RLS: financials is tenant-scoped. Harmless if the executing role bypasses it.
SET LOCAL app.tenant_id = 'a0000000-0000-0000-0000-000000000001';

-- Before state, for the record. Both periods shown: only the FY26 annual row is
-- touched, and seeing FY25 alongside it confirms the right row was picked.
SELECT 'BEFORE' AS phase, metric, fiscal_year, quarter, value, is_latest, created_at
FROM   financials
WHERE  tenant_id      = 'a0000000-0000-0000-0000-000000000001'
  AND  company        = 'ETERNAL'
  AND  financial_type = 'consolidated'
  AND  metric         = 'changes_in_inventories'
ORDER  BY fiscal_year, quarter NULLS FIRST;

DO $$
DECLARE
    v_tenant CONSTANT uuid := 'a0000000-0000-0000-0000-000000000001';
    r        RECORD;
    n        integer;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('changes_in_inventories', 'FY26', (-2002)::numeric, (-2042)::numeric)
        ) AS t(metric, fiscal_year, old_value, new_value)
    LOOP
        UPDATE financials
        SET    value = r.new_value
        WHERE  tenant_id      = v_tenant
          AND  company        = 'ETERNAL'
          AND  fiscal_year    = r.fiscal_year
          AND  quarter        IS NULL          -- ANNUAL row; see header
          AND  financial_type = 'consolidated'
          AND  metric         = r.metric
          AND  is_latest      = TRUE
          AND  value          = r.old_value;

        GET DIAGNOSTICS n = ROW_COUNT;

        IF n = 1 THEN
            RAISE NOTICE 'CORRECTED % % ANNUAL : % -> %',
                         r.metric, r.fiscal_year, r.old_value, r.new_value;
            CONTINUE;
        END IF;

        IF n = 0 THEN
            -- Distinguish "already applied" from "this database is not in the
            -- state this migration expects". Only the former is acceptable.
            SELECT count(*) INTO n
            FROM   financials
            WHERE  tenant_id      = v_tenant
              AND  company        = 'ETERNAL'
              AND  fiscal_year    = r.fiscal_year
              AND  quarter        IS NULL
              AND  financial_type = 'consolidated'
              AND  metric         = r.metric
              AND  is_latest      = TRUE
              AND  value          = r.new_value;

            IF n = 1 THEN
                RAISE NOTICE 'SKIP % % ANNUAL : already at % — previously applied',
                             r.metric, r.fiscal_year, r.new_value;
                CONTINUE;
            END IF;

            RAISE EXCEPTION
                'ABORT: % % ANNUAL is neither the expected old value (%) nor the '
                'expected new value (%). This database is not in the state '
                'migration 016 was written against — inspect before forcing '
                'anything.', r.metric, r.fiscal_year, r.old_value, r.new_value;
        END IF;

        RAISE EXCEPTION
            'ABORT: % % ANNUAL matched % live rows, expected exactly 1. '
            'uq_financials_latest should make this impossible; investigate '
            'duplicate is_latest rows before proceeding.',
            r.metric, r.fiscal_year, n;
    END LOOP;
END $$;

-- After state. The FY26 annual row must read -2042; FY25 must be unchanged.
SELECT 'AFTER' AS phase, metric, fiscal_year, quarter, value, is_latest, created_at
FROM   financials
WHERE  tenant_id      = 'a0000000-0000-0000-0000-000000000001'
  AND  company        = 'ETERNAL'
  AND  financial_type = 'consolidated'
  AND  metric         = 'changes_in_inventories'
ORDER  BY fiscal_year, quarter NULLS FIRST;

-- Must still be 0. This migration retires nothing.
SELECT 'is_latest = FALSE rows' AS check, count(*) AS n
FROM   financials
WHERE  is_latest = FALSE;

INSERT INTO schema_migrations (filename, applied_at, note)
VALUES ('016_correct_eternal_fy26_changes_in_inventories.sql', now(),
        'DATA: ETERNAL FY26 annual consolidated changes_in_inventories '
        '-2002 -> -2042. Stale value from an earlier parser generation, never '
        'propagated because the loader''s same-doc_id branch could INSERT a new '
        'metric name but never UPDATE a changed figure. Value only; is_latest '
        'untouched — a parser correction is not a restatement.')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
