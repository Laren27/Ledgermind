-- ============================================================
-- 017 — DATA correction: TITAN (9) and PAYTM (15) stale values
-- ============================================================
-- NOT A SCHEMA CHANGE. Same shape and the same reasoning as migrations 015 and
-- 016; read 015's header first. This corrects the remaining 24 stale values
-- found by running `backfill_financials --apply --correct-values` across all
-- four reference documents on 2026-08-04.
--
-- WHY THESE EXISTED AT ALL. backfill_financials reads existing doc_ids from the
-- documents table (deliberately — minting one would orphan the rows from their
-- source document and break Principle 3's lineage). That makes record.doc_id
-- always EQUAL the stored doc_id, so db_loader._upsert_one took its same-doc_id
-- branch on every record and fell through to ON CONFLICT DO NOTHING against
-- uq_financials_per_doc (doc_id, metric, fiscal_year, financial_type,
-- COALESCE(quarter,'')). That conflict key contains the METRIC but not the
-- VALUE. So a metric name never seen before INSERTed, and a metric already
-- present whose value the parser now read differently was dropped as "skipped".
-- Every extraction fix between the 2026-07-15 ingest and 2026-08-04 could add
-- rows and could not correct one.
--
-- regression_check never caught it because it reads EXTRACTION OUTPUT, not the
-- database: it parses, extracts, and asserts on records in memory. Every one of
-- those fixes passed 4/4 PASS, correctly — the extractor was right each time.
-- The database was simply never in the assertion path.
--
-- EVERY CORRECTED VALUE IS WHAT THE PAGE PRINTS. Each was traced back to its
-- printed row before being written here; the page and the raw printed label are
-- named against each group below.
--
-- WHY AN UPDATE AND NOT A RESTATEMENT: identical to 015. The filings never
-- changed; our reading of them did. `value` only — is_latest, doc_id,
-- filing_date, id and created_at are untouched. On the local docker postgres
-- these corrections are already applied and left is_latest = FALSE at zero rows,
-- 1437 rows against 1437 distinct live business keys, with created_at still at
-- the original ingest timestamps.
--
-- SELF-VERIFYING, exactly as 015/016: each UPDATE requires the value to be the
-- specific WRONG one and must affect exactly 1 row; a row already at the
-- corrected value reports SKIP so a second run is safe; anything else ABORTS
-- the whole transaction. 24 rows must be accounted for, and a final count check
-- fails the transaction if fewer than 24 were corrected-or-skipped.
--
-- NULL QUARTERS. Annual rows carry quarter IS NULL, and this migration mixes
-- annual and quarterly keys, so the predicate is
-- `quarter IS NOT DISTINCT FROM r.quarter` — plain `=` is never true against
-- NULL and would silently match nothing.
--
-- APPLIED BY HAND in the Supabase SQL editor. Supabase has NOT been queried
-- from this environment and this migration asserts nothing about what it holds;
-- that is what the guards are for.
-- ============================================================

BEGIN;

-- RLS: financials is tenant-scoped. Harmless if the executing role bypasses it.
SET LOCAL app.tenant_id = 'a0000000-0000-0000-0000-000000000001';

-- Before state, for the record.
SELECT 'BEFORE' AS phase, company, metric, fiscal_year, quarter, financial_type,
       value, is_latest, created_at
FROM   financials
WHERE  tenant_id = 'a0000000-0000-0000-0000-000000000001'
  AND  is_latest = TRUE
  AND  ((company = 'TITAN' AND metric IN ('total_income','total_expenses','profit_before_tax'))
     OR (company = 'PAYTM' AND metric IN ('depreciation','profit_before_exceptional_items','cash')))
ORDER  BY company, metric, fiscal_year, quarter NULLS FIRST, financial_type;

DO $$
DECLARE
    v_tenant  CONSTANT uuid := 'a0000000-0000-0000-0000-000000000001';
    r         RECORD;
    n         integer;
    n_fixed   integer := 0;
    n_skipped integer := 0;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            -- ============================================================
            -- TITAN_Q1FY26_PRESS_RELEASE_AND_FINANCIAL_RESULTS.pdf — 9 rows
            -- All CONSOLIDATED, all printed on PAGE 14.
            --
            -- The stored figures were the derivation's output from BEFORE
            -- `other_operating_revenue` joined the total_income sum (the fix
            -- recorded in _compute_derived_totals' own comment). total_expenses
            -- follows total_income through the derivation chain, which is why
            -- both moved by the same amount per period: 1709 / 1019 / 1043 /
            -- 3313. The corrected values are exactly what page 14 prints.
            -- ============================================================

            -- p.14, printed row: 'III. Total income +II) (I'
            ('TITAN', 'total_income', 'FY26', 'Q1', 'consolidated', 14919::numeric, 16628::numeric),
            ('TITAN', 'total_income', 'FY25', 'Q4', 'consolidated', 14013::numeric, 15032::numeric),
            ('TITAN', 'total_income', 'FY25', 'Q1', 'consolidated', 12343::numeric, 13386::numeric),
            ('TITAN', 'total_income', 'FY25', NULL, 'consolidated', 57629::numeric, 60942::numeric),

            -- p.14, printed row: 'IV. Total expenses'
            ('TITAN', 'total_expenses', 'FY26', 'Q1', 'consolidated', 13439::numeric, 15148::numeric),
            ('TITAN', 'total_expenses', 'FY25', 'Q4', 'consolidated', 12795::numeric, 13814::numeric),
            ('TITAN', 'total_expenses', 'FY25', 'Q1', 'consolidated', 11370::numeric, 12413::numeric),
            ('TITAN', 'total_expenses', 'FY25', NULL, 'consolidated', 53095::numeric, 56407::numeric),

            -- p.14, printed row: 'VII. Profit before tax (V+ VJ)'
            ('TITAN', 'profit_before_tax', 'FY25', NULL, 'consolidated', 4534::numeric, 4535::numeric),

            -- ============================================================
            -- FS-Results_Q4-&-Financial-Year-ended-March-31,-2026.pdf (PAYTM)
            -- — 15 rows
            -- ============================================================

            -- p.8 CONSOLIDATED, printed row: 'Depreciation and amortization expense'
            ('PAYTM', 'depreciation', 'FY26', 'Q4', 'consolidated', 175::numeric, 132::numeric),
            ('PAYTM', 'depreciation', 'FY26', 'Q3', 'consolidated', 167::numeric, 133::numeric),
            ('PAYTM', 'depreciation', 'FY25', 'Q4', 'consolidated', 146::numeric, 150::numeric),
            ('PAYTM', 'depreciation', 'FY26', NULL, 'consolidated', 643::numeric, 568::numeric),
            ('PAYTM', 'depreciation', 'FY25', NULL, 'consolidated', 640::numeric, 673::numeric),

            -- p.8 CONSOLIDATED, printed row:
            --   'Proft/(Loss) before exceptional items and tax'   [sic — OCR]
            -- Page 8 prints this line TWICE under different labels; the longer
            -- 'Profit/(Loss) before share of profit/ (loss) of associates/
            -- joint ventures, exceptional items and tax' variant reads
            -- 231 / 770 / -1471. The stored value now follows the shorter
            -- printed row, which is the one the extractor resolves to.
            ('PAYTM', 'profit_before_exceptional_items', 'FY26', 'Q3', 'consolidated', 231::numeric, 230::numeric),
            ('PAYTM', 'profit_before_exceptional_items', 'FY26', NULL, 'consolidated', 770::numeric, 768::numeric),
            ('PAYTM', 'profit_before_exceptional_items', 'FY25', NULL, 'consolidated', (-1471)::numeric, (-1468)::numeric),

            -- p.9 CONSOLIDATED, printed row: 'Cash and cash equivalents'
            --
            -- THE MOST SEVERE PAIR IN THIS MIGRATION. Both stored values are
            -- NEGATIVE for a balance-sheet metric that cannot be negative: an
            -- older parser claimed a cash-flow MOVEMENT line for a balance-sheet
            -- metric, so the sign AND the magnitude were both wrong, live since
            -- July. No golden question asserts `cash` for any company, which is
            -- why nothing caught it.
            ('PAYTM', 'cash', 'FY26', NULL, 'consolidated', (-710)::numeric, 3285::numeric),
            ('PAYTM', 'cash', 'FY25', NULL, 'consolidated', (-139)::numeric, 2077::numeric),

            -- p.17 STANDALONE, printed row: 'Depreciation and amortization expense'
            ('PAYTM', 'depreciation', 'FY26', 'Q4', 'standalone', 86::numeric, 13::numeric),
            ('PAYTM', 'depreciation', 'FY26', 'Q3', 'standalone', 119::numeric, 96::numeric),
            ('PAYTM', 'depreciation', 'FY25', 'Q4', 'standalone', 116::numeric, 146::numeric),
            ('PAYTM', 'depreciation', 'FY26', NULL, 'standalone', 448::numeric, 404::numeric),
            ('PAYTM', 'depreciation', 'FY25', NULL, 'standalone', 514::numeric, 657::numeric)
        ) AS t(company, metric, fiscal_year, quarter, financial_type, old_value, new_value)
    LOOP
        UPDATE financials
        SET    value = r.new_value
        WHERE  tenant_id      = v_tenant
          AND  company        = r.company
          AND  fiscal_year    = r.fiscal_year
          AND  quarter        IS NOT DISTINCT FROM r.quarter   -- NULL = annual
          AND  financial_type = r.financial_type
          AND  metric         = r.metric
          AND  is_latest      = TRUE
          AND  value          = r.old_value;

        GET DIAGNOSTICS n = ROW_COUNT;

        IF n = 1 THEN
            n_fixed := n_fixed + 1;
            RAISE NOTICE 'CORRECTED % | % | % | % | % : % -> %',
                         r.company, r.metric, r.fiscal_year,
                         COALESCE(r.quarter, 'ANNUAL'), r.financial_type,
                         r.old_value, r.new_value;
            CONTINUE;
        END IF;

        IF n = 0 THEN
            -- Distinguish "already applied" from "this database is not in the
            -- state this migration expects". Only the former is acceptable.
            SELECT count(*) INTO n
            FROM   financials
            WHERE  tenant_id      = v_tenant
              AND  company        = r.company
              AND  fiscal_year    = r.fiscal_year
              AND  quarter        IS NOT DISTINCT FROM r.quarter
              AND  financial_type = r.financial_type
              AND  metric         = r.metric
              AND  is_latest      = TRUE
              AND  value          = r.new_value;

            IF n = 1 THEN
                n_skipped := n_skipped + 1;
                RAISE NOTICE 'SKIP % | % | % | % | % : already at % — previously applied',
                             r.company, r.metric, r.fiscal_year,
                             COALESCE(r.quarter, 'ANNUAL'), r.financial_type,
                             r.new_value;
                CONTINUE;
            END IF;

            RAISE EXCEPTION
                'ABORT: % | % | % | % | % is neither the expected old value (%) '
                'nor the expected new value (%). This database is not in the '
                'state migration 017 was written against — inspect before '
                'forcing anything.',
                r.company, r.metric, r.fiscal_year,
                COALESCE(r.quarter, 'ANNUAL'), r.financial_type,
                r.old_value, r.new_value;
        END IF;

        RAISE EXCEPTION
            'ABORT: % | % | % | % | % matched % live rows, expected exactly 1. '
            'uq_financials_latest should make this impossible; investigate '
            'duplicate is_latest rows before proceeding.',
            r.company, r.metric, r.fiscal_year,
            COALESCE(r.quarter, 'ANNUAL'), r.financial_type, n;
    END LOOP;

    -- Every one of the 24 must be accounted for as either corrected or already
    -- applied. A loop that silently processed fewer rows than intended would
    -- otherwise commit a partial correction and report success.
    RAISE NOTICE '--- 017 summary: % corrected, % already applied, % total ---',
                 n_fixed, n_skipped, n_fixed + n_skipped;
    IF n_fixed + n_skipped <> 24 THEN
        RAISE EXCEPTION
            'ABORT: accounted for % rows, expected 24.', n_fixed + n_skipped;
    END IF;
END $$;

-- After state.
SELECT 'AFTER' AS phase, company, metric, fiscal_year, quarter, financial_type,
       value, is_latest, created_at
FROM   financials
WHERE  tenant_id = 'a0000000-0000-0000-0000-000000000001'
  AND  is_latest = TRUE
  AND  ((company = 'TITAN' AND metric IN ('total_income','total_expenses','profit_before_tax'))
     OR (company = 'PAYTM' AND metric IN ('depreciation','profit_before_exceptional_items','cash')))
ORDER  BY company, metric, fiscal_year, quarter NULLS FIRST, financial_type;

-- No PAYTM cash row may be negative after this migration.
SELECT 'negative cash rows (must be 0)' AS check, count(*) AS n
FROM   financials
WHERE  is_latest = TRUE AND metric = 'cash' AND value < 0;

-- Must still be 0. This migration retires nothing.
SELECT 'is_latest = FALSE rows' AS check, count(*) AS n
FROM   financials
WHERE  is_latest = FALSE;

INSERT INTO schema_migrations (filename, applied_at, note)
VALUES ('017_correct_titan_paytm_stale_values.sql', now(),
        'DATA: 24 stale values — TITAN 9 (total_income/total_expenses/'
        'profit_before_tax consolidated, p.14, pre-other_operating_revenue '
        'derivation output) and PAYTM 15 (depreciation p.8/p.17, '
        'profit_before_exceptional_items p.8, cash p.9). Stale because the '
        'loader''s same-doc_id branch could INSERT a new metric name but never '
        'UPDATE a changed figure. Value only; is_latest untouched — a parser '
        'correction is not a restatement.')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
