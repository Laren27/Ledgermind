-- ============================================================
-- 014 — audit_log: LLM attribution columns
-- ============================================================
-- Found 2026-07-31 while verifying that llm_model reached the admin API
-- payload: audit_log persists NEITHER llm_provider NOR llm_model, and never
-- has. llm_provider has been on QueryState and in the admin HTTP response
-- since it was introduced, but the audit row — the append-only record the
-- project's "full lineage on every answer" claim rests on — is silent on
-- both. All 2646 existing rows are unattributed and cannot be backfilled.
--
-- BOTH COLUMNS ARE NULLABLE, deliberately. NULL is a real, correct state:
--   - blocked queries: prompt_shield blocks before router_node, so no LLM
--     call is ever made
--   - synthesis floor: response_generator clears attribution when both
--     providers fail, so the row honestly records "no model served this"
--   - every pre-migration row
--
-- NO CHECK CONSTRAINT ON llm_provider, deliberately. An enum constraint here
-- is the exact shape of audit_log_query_path_check, which allowed
-- 'cross_examination' while the engines emitted 'cross' and silently rejected
-- every cross row for the table's entire lifetime (migration 013). The
-- provider vocabulary already has one authority, _PROVIDER_TAINT in
-- app/engines/state.py; a second copy in the database would drift.
--
-- Safe to run against a live table: ADD COLUMN with no default and no NOT
-- NULL takes a brief ACCESS EXCLUSIVE lock and rewrites nothing (PG 11+).
-- ============================================================

BEGIN;

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS llm_provider TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS llm_model    TEXT;

COMMENT ON COLUMN audit_log.llm_provider IS
    'Which provider served this query''s LLM calls: gemini | groq | NULL. '
    'NULL means no LLM call was made (blocked query) or all providers failed '
    '(synthesis floor). Worst provider wins across multiple calls — see '
    'record_llm_call() in app/engines/state.py.';

COMMENT ON COLUMN audit_log.llm_model IS
    'Resolved model id that served the call, e.g. gemini-3.1-flash-lite. '
    'Follows llm_provider. Asserted by scripts/eval_runner.py against its '
    '--model argument.';

INSERT INTO schema_migrations (filename, applied_at, note)
VALUES ('014_audit_llm_attribution.sql', now(),
        'audit_log persisted neither llm_provider nor llm_model since either existed')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
