-- Migration 007a: seed demo tenants
--
-- RECONSTRUCTED 2026-07-30 from the live production rows. The original was
-- applied directly in the Supabase SQL editor on 2026-07-23 and never
-- committed, so a fresh environment built from this repo would have had no
-- tenants -- and therefore no valid tenant_id for 007_seed_users.sql's users
-- to reference. Surfaced by scripts/check_migrations.py as an orphaned entry
-- in schema_migrations.
--
-- created_at is left to DEFAULT now() rather than pinned to the original
-- 2026-07-23 timestamp: the value carries no meaning for demo tenants, and
-- writing a fake historical date into a fresh environment would be a small
-- lie for no benefit.
--
-- Numbered 007a to preserve ordering: it MUST run before 007_seed_users.sql,
-- whose rows carry a tenant_id foreign key into this table.
--
-- Idempotent -- safe to re-run against an environment that already has them.

INSERT INTO tenants (tenant_id, name, plan) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'Alpha', 'free'),
    ('b0000000-0000-0000-0000-000000000002', 'Beta',  'free')
ON CONFLICT (tenant_id) DO NOTHING;
