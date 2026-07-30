-- Migration 010: collapse users RLS into one CASE-guarded policy
--
-- Supersedes 006's two-policy split and 009's AND-guard, BOTH of which
-- still produced `invalid input syntax for type uuid: ""` (pgcode 22P02)
-- in production under concurrent load, confirmed 2026-07-30 09:24 UTC.
--
-- WHY 009 DID NOT WORK. It guarded the cast with
--   coalesce(current_setting('app.tenant_id', true), '') <> ''
--   AND tenant_id = current_setting('app.tenant_id', true)::uuid
-- which reads as a short-circuit but is not one: PostgreSQL does not
-- guarantee left-to-right evaluation of AND. The planner reorders boolean
-- clauses by estimated cost and may evaluate the cast first. CASE is the
-- only construct that guarantees evaluation order, so the cast must live
-- in a branch that is not taken rather than behind a conjunct.
--
-- WHY THE GUC IS EMPTY RATHER THAN NULL. Login opens its own connection,
-- but DATABASE_URL routes through a transaction pooler, so it is handed a
-- SERVER connection that previously served an /api/query request and ran
-- SET LOCAL app.tenant_id. The GUC stays defined on that server connection
-- and reverts to '' — not NULL — after the transaction ends. Idle service:
-- fresh server connections, current_setting returns NULL, NULL::uuid is
-- legal, everything passes. Under load: reused connections, ''::uuid, 500s.
-- This is why the bug was invisible to every low-traffic test.
--
-- Security property is unchanged from 006: no tenant context set => SELECT
-- permitted (the login-by-email bootstrap), tenant context set => strict
-- tenant isolation. One policy instead of two also removes the permissive-
-- OR interaction, where tenant_isolation was still being evaluated on rows
-- the bootstrap policy had already admitted.

DROP POLICY IF EXISTS auth_bootstrap_lookup ON users;
DROP POLICY IF EXISTS tenant_isolation ON users;

CREATE POLICY users_access ON users
  USING (
    CASE
      WHEN coalesce(current_setting('app.tenant_id', true), '') = '' THEN true
      ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
    END
  );
