-- 013_fix_audit_query_path_check.sql
--
-- BUG: audit rows for path="cross" were REJECTED by
-- audit_log_query_path_check, which allowed 'cross_examination' (blueprint
-- §22's original naming) while the engines emit 'cross'. audit_writer logs
-- "Audit log write FAILED (response still delivered)" at ERROR and returns
-- the answer anyway -- correct for the user, but one of three paths had no
-- audit trail at all. Local DB confirmed 2026-07-30: 2646 rows, zero cross.
--
-- ROOT CAUSE is drift between the LIVE DATABASE and the repo, not between
-- code and spec: init.sql already carries the correct five-value set. The
-- local database predates that edit, and CREATE TABLE IF NOT EXISTS means
-- re-running init.sql against an existing volume is a no-op. Any environment
-- created before the init.sql fix carries the stale constraint.
--
-- The allowed set below is copied verbatim from init.sql. Do not "simplify"
-- it: 'unknown' is emitted by response_generator's final else branch when no
-- path matched, and dropping it would reintroduce this same bug for that row.
--
-- No backfill: no row has ever used 'cross_examination', so nothing to
-- migrate. NULL is unaffected -- CHECK does not reject NULL.

BEGIN;

ALTER TABLE audit_log
    DROP CONSTRAINT IF EXISTS audit_log_query_path_check;

ALTER TABLE audit_log
    ADD CONSTRAINT audit_log_query_path_check
    CHECK (query_path IN (
        'semantic', 'quantitative', 'cross', 'blocked', 'unknown'
    ));

INSERT INTO schema_migrations (filename, applied_at, note)
VALUES ('013_fix_audit_query_path_check.sql', now(),
        'live-DB constraint predated init.sql fix; cross audit rows were being rejected')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
