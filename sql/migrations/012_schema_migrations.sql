-- Migration 012: schema_migrations ledger
--
-- WHY. Migrations here are applied BY HAND in the Supabase SQL editor -- no
-- deploy step runs them. Until now the only record of what production had
-- received was chat history plus a directory listing, which produced two real
-- problems: 009 was applied and later found to be wrong (superseded by 010,
-- with nothing in the DB recording that either was applied), and
-- 007a_seed_tenants.sql was applied to Supabase but never committed, so a
-- fresh environment built from this repo would silently lack it.
--
-- This table is the record. scripts/check_migrations.py diffs it against the
-- files on disk and reports pending / orphaned. It deliberately does NOT
-- apply anything: reading the SQL before it touches production is the point,
-- and an auto-applier on a manually-operated project is complexity nobody
-- asked for.
--
-- NO RLS. This is infrastructure metadata, not tenant data. Adding a
-- tenant_id here would be meaningless -- migrations are global.
--
-- NICE TO HAVE, not built: a sha256 column detecting a migration file edited
-- after it was applied (a fresh env would then get different SQL than
-- production did). Left out because 009 has already been edited post-apply to
-- add its superseded header, so the check would fire on a comment change from
-- day one. Add it if a real SQL-after-apply edit ever occurs.

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ,          -- NULL for backfilled rows, date unknown
    note        TEXT
);

GRANT SELECT ON schema_migrations TO ledgermind_app;

-- Backfill. applied_at is NULL for everything below because the real dates
-- were never recorded -- that is honest, and inventing timestamps would make
-- this table look more authoritative than it is.
INSERT INTO schema_migrations (filename, applied_at, note) VALUES
    ('003_financials_indexes.sql',            NULL, 'backfilled 2026-07-30'),
    ('006_users_auth_rls.sql',                NULL, 'backfilled; policies later replaced by 010'),
    ('007_seed_users.sql',                    NULL, 'backfilled 2026-07-30'),
    ('007a_seed_tenants.sql',                 NULL, 'APPLIED TO PRODUCTION, NOT IN REPO -- will report as orphaned until the file is recovered and committed'),
    ('008_pending_uploads.sql',               NULL, 'backfilled; policy later replaced by 011'),
    ('009_fix_auth_bootstrap_empty_guc.sql',  NULL, 'backfilled; applied but INEFFECTIVE, superseded by 010'),
    ('010_users_single_policy_case_guard.sql',NULL, 'backfilled 2026-07-30'),
    ('011_uniform_case_guard_all_policies.sql', NULL, 'backfilled 2026-07-30')
ON CONFLICT (filename) DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at, note)
VALUES ('012_schema_migrations.sql', now(), 'first migration recorded at apply time')
ON CONFLICT (filename) DO NOTHING;
