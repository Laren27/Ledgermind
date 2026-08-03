-- ============================================================
-- 015 — DATA correction: ETERNAL FY26 Q4 consolidated, misread revenue
-- ============================================================
-- NOT A SCHEMA CHANGE. This corrects three stored values that are wrong
-- because the PARSER was wrong, and does not alter any table definition.
--
-- WHAT WAS WRONG. ETERNAL_Q4FY26 p.31 prints consolidated revenue as 17,292.
-- OCR split it into two words, "I" (the leading 1) and "7,292", both landing in
-- the same column bucket 0.5pt apart. pdf_parser's comma rule then kept "7,292"
-- and discarded the leading digit. _compute_derived_totals recomputed
-- total_income and total_expenses FROM that corrupted revenue and OVERWROTE two
-- rows that had been read CORRECTLY, so all three stored figures are 10,000 Cr
-- low and mutually consistent. The printed column is self-consistent at 17,xxx:
--   17,292 revenue + 342 other income = 17,634 total income
--   17,634 total income -  228 PBT    = 17,406 total expenses
-- all three printed on the page. Parser fixed in commit dba2af8; blast radius
-- measured at exactly ONE cell corpus-wide.
--
-- WHY THIS IS AN UPDATE AND NOT A RESTATEMENT. is_latest, retirement and a new
-- row all encode a claim about the FILING's history — that the issuer published
-- a revised figure. The issuer did no such thing. The filing never changed; our
-- reading of it did. Recording this through the restatement machinery would
-- manufacture a filing history that does not exist: a retired "original" row
-- the issuer never filed, sitting in the audit trail as though it had, and
-- is_latest = FALSE would stop meaning "superseded by the issuer".
--
-- So this migration touches `value` and NOTHING else. is_latest, doc_id,
-- filing_date, id and created_at are all left exactly as they are. created_at
-- correctly remains the original ingest timestamp — that IS when the row was
-- created.
--
-- doc_id IS DELIBERATELY NOT IN THE WHERE CLAUSE. This database was ingested
-- separately from the local docker one and its doc_ids are not assumed to
-- match. The business key plus is_latest already identifies exactly one live
-- row (uq_financials_latest), and each UPDATE additionally requires the value
-- to be the specific wrong one, so a mismatch aborts rather than guessing.
--
-- SELF-VERIFYING. Every statement must affect exactly 1 row. Anything else
-- raises and rolls the whole transaction back. If a row is already at the
-- corrected value the migration reports SKIP and continues, so a second run is
-- safe; but a row that is neither the expected old value nor the expected new
-- value aborts, because that means this database is not in the state this
-- migration was written against.
--
-- APPLIED BY HAND in the Supabase SQL editor. Verified equivalent changes on
-- the local docker postgres via
-- `backfill_financials --company ETERNAL --apply --correct-values`, which
-- reported 4 corrected / 456 skipped / 0 errors and left is_latest = FALSE at
-- zero rows. See the fourth correction noted at the bottom of this file.
-- ============================================================

BEGIN;

-- RLS: financials is tenant-scoped. Harmless if the executing role bypasses it.
SET LOCAL app.tenant_id = 'a0000000-0000-0000-0000-000000000001';

-- Before state, for the record.
SELECT 'BEFORE' AS phase, metric, value, is_latest, filing_date, created_at
FROM   financials
WHERE  tenant_id      = 'a0000000-0000-0000-0000-000000000001'
  AND  company        = 'ETERNAL'
  AND  fiscal_year    = 'FY26'
  AND  quarter        = 'Q4'
  AND  financial_type = 'consolidated'
  AND  metric IN ('revenue', 'total_income', 'total_expenses')
ORDER  BY metric;

DO $$
DECLARE
    v_tenant CONSTANT uuid := 'a0000000-0000-0000-0000-000000000001';
    r        RECORD;
    n        integer;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('revenue',        7292::numeric, 17292::numeric),
            ('total_income',   7634::numeric, 17634::numeric),
            ('total_expenses', 7406::numeric, 17406::numeric)
        ) AS t(metric, old_value, new_value)
    LOOP
        UPDATE financials
        SET    value = r.new_value
        WHERE  tenant_id      = v_tenant
          AND  company        = 'ETERNAL'
          AND  fiscal_year    = 'FY26'
          AND  quarter        = 'Q4'
          AND  financial_type = 'consolidated'
          AND  metric         = r.metric
          AND  is_latest      = TRUE
          AND  value          = r.old_value;

        GET DIAGNOSTICS n = ROW_COUNT;

        IF n = 1 THEN
            RAISE NOTICE 'CORRECTED % : % -> %', r.metric, r.old_value, r.new_value;
            CONTINUE;
        END IF;

        IF n = 0 THEN
            -- Distinguish "already applied" from "this database is not in the
            -- state this migration expects". Only the former is acceptable.
            SELECT count(*) INTO n
            FROM   financials
            WHERE  tenant_id      = v_tenant
              AND  company        = 'ETERNAL'
              AND  fiscal_year    = 'FY26'
              AND  quarter        = 'Q4'
              AND  financial_type = 'consolidated'
              AND  metric         = r.metric
              AND  is_latest      = TRUE
              AND  value          = r.new_value;

            IF n = 1 THEN
                RAISE NOTICE 'SKIP % : already at % — previously applied',
                             r.metric, r.new_value;
                CONTINUE;
            END IF;

            RAISE EXCEPTION
                'ABORT: % is neither the expected old value (%) nor the expected '
                'new value (%). This database is not in the state migration 015 '
                'was written against — inspect before forcing anything.',
                r.metric, r.old_value, r.new_value;
        END IF;

        RAISE EXCEPTION
            'ABORT: % matched % live rows, expected exactly 1. uq_financials_latest '
            'should make this impossible; investigate duplicate is_latest rows '
            'before proceeding.', r.metric, n;
    END LOOP;
END $$;

-- After state. revenue/total_income/total_expenses must read
-- 17292 / 17634 / 17406, with is_latest and created_at unchanged.
SELECT 'AFTER' AS phase, metric, value, is_latest, filing_date, created_at
FROM   financials
WHERE  tenant_id      = 'a0000000-0000-0000-0000-000000000001'
  AND  company        = 'ETERNAL'
  AND  fiscal_year    = 'FY26'
  AND  quarter        = 'Q4'
  AND  financial_type = 'consolidated'
  AND  metric IN ('revenue', 'total_income', 'total_expenses')
ORDER  BY metric;

-- Must still be 0. This migration retires nothing.
SELECT 'is_latest = FALSE rows' AS check, count(*) AS n
FROM   financials
WHERE  is_latest = FALSE;

INSERT INTO schema_migrations (filename, applied_at, note)
VALUES ('015_correct_eternal_fy26q4_misread_revenue.sql', now(),
        'DATA: ETERNAL FY26 Q4 consolidated revenue/total_income/total_expenses '
        '7292/7634/7406 -> 17292/17634/17406; OCR split "17,292" into "I" + '
        '"7,292" and derivation propagated the misread. Value only; is_latest '
        'untouched — a parser correction is not a restatement.')
ON CONFLICT (filename) DO NOTHING;

COMMIT;


-- ============================================================
-- NOT PART OF THIS MIGRATION — a fourth stale value, pending a decision
-- ============================================================
-- The local correction run reported FOUR corrections, not three. The extra one
-- is unrelated to the misread revenue above:
--
--   changes_in_inventories | ETERNAL | FY26 | ANNUAL | consolidated
--   -2002.0 -> -2042.0     (p.33, printed as "-Inventories")
--
-- It is NOT caused by the fragment-joining fix, whose blast radius was measured
-- at exactly one cell on p.31. It is a stale value from an EARLIER parser
-- generation: some extraction fix landed since the original ingest re-read this
-- cell, and nothing could propagate it, because backfill could only ever INSERT
-- metric names it had never seen and never correct a changed value.
--
-- Whether this database holds -2002 has NOT been verified — it was not queried.
-- Left out deliberately: only the three corrections above were approved, and a
-- data correction should not be smuggled in beside an approved one. If wanted,
-- it belongs in its own migration after confirming the stored value here.
--
-- BEGIN;
-- SET LOCAL app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
-- UPDATE financials SET value = -2042
--  WHERE tenant_id = 'a0000000-0000-0000-0000-000000000001'
--    AND company = 'ETERNAL' AND fiscal_year = 'FY26' AND quarter IS NULL
--    AND financial_type = 'consolidated' AND metric = 'changes_in_inventories'
--    AND is_latest = TRUE AND value = -2002;
-- COMMIT;
-- ============================================================
