-- Migration 011: CASE-guard the remaining tenant_isolation policies
--
-- Applies the same fix as 010 (users) to documents, financials, audit_log,
-- and pending_uploads. Latent at time of writing -- every caller sets
-- app.tenant_id before touching these tables -- but "safe because no caller
-- has erred yet" is exactly the reasoning that left the users policy broken
-- in production since launch.
--
-- TWO DEFECTS FIXED HERE:
--
-- 1. `current_setting(...)::uuid` on an empty GUC raises 22P02
--    (invalid input syntax for type uuid: ""). Under a transaction pooler a
--    server connection that previously ran SET LOCAL app.tenant_id retains
--    the GUC as '' -- not NULL -- after the transaction ends. NULL::uuid is
--    legal and yields NULL; ''::uuid raises. This is why the bug only ever
--    appears under concurrent load.
--
--    A conjunct guard (`coalesce(...) <> '' AND tenant_id = ...::uuid`) does
--    NOT fix this: PostgreSQL does not guarantee left-to-right evaluation of
--    AND, and the planner may order the cast first. Confirmed live 2026-07-30,
--    still failing with 22P02 after such a guard was applied. CASE is the only
--    construct guaranteeing that an untaken branch is never evaluated.
--
-- 2. pending_uploads omitted the `, true` missing-ok flag entirely, so an
--    unset GUC raised `unrecognized configuration parameter` rather than
--    returning NULL. Different error, same root cause, brought in line here.
--
-- FAIL-CLOSED, deliberately: unlike users (migration 010), these tables have
-- no auth-bootstrap case. Empty or unset tenant context must return ZERO rows,
-- so the CASE yields false, not true. Inverting this would be a tenant
-- isolation breach, not merely an error.
--
-- These are FOR ALL policies with USING only, so PostgreSQL derives WITH CHECK
-- from the same expression. pending_uploads holds INSERT/UPDATE grants, so
-- writes without tenant context now fail cleanly instead of raising a cast
-- error.

DROP POLICY IF EXISTS tenant_isolation_documents ON documents;
CREATE POLICY tenant_isolation_documents ON documents
  USING (
    CASE
      WHEN coalesce(current_setting('app.tenant_id', true), '') = '' THEN false
      ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
    END
  );

DROP POLICY IF EXISTS tenant_isolation_financials ON financials;
CREATE POLICY tenant_isolation_financials ON financials
  USING (
    CASE
      WHEN coalesce(current_setting('app.tenant_id', true), '') = '' THEN false
      ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
    END
  );

DROP POLICY IF EXISTS tenant_isolation_audit ON audit_log;
CREATE POLICY tenant_isolation_audit ON audit_log
  USING (
    CASE
      WHEN coalesce(current_setting('app.tenant_id', true), '') = '' THEN false
      ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
    END
  );

DROP POLICY IF EXISTS tenant_isolation ON pending_uploads;
CREATE POLICY tenant_isolation ON pending_uploads
  USING (
    CASE
      WHEN coalesce(current_setting('app.tenant_id', true), '') = '' THEN false
      ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
    END
  );
