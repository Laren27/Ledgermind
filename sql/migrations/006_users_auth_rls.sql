-- Migration 006: Auth support on users table
-- Adds password_hash column and RLS policies for authenticated access.
--
-- DESIGN NOTE (read before modifying):
-- Login is the ONE deliberate exception to "tenant_id is always set before any query."
-- At login time we don't know the user's tenant yet -- we're looking it up BY EMAIL
-- to discover it. So this table gets a second policy that allows SELECT when no
-- tenant context has been set at all (pre-auth state). This is intentionally narrow:
-- it only permits reads, only on this table, and is documented here so nobody
-- "fixes" it into a silent superuser bypass later. Every other table/query in the
-- app always has app.tenant_id set before touching the DB.

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;

-- Normal authenticated access: standard tenant isolation, same pattern as financials/documents
DROP POLICY IF EXISTS tenant_isolation ON users;
CREATE POLICY tenant_isolation ON users
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- Auth bootstrap exception: allow SELECT only when app.tenant_id has never been set
-- in this transaction (true = don't error if unset, just return NULL).
-- This is what makes POST /auth/login able to look a user up by email.
DROP POLICY IF EXISTS auth_bootstrap_lookup ON users;
CREATE POLICY auth_bootstrap_lookup ON users
  FOR SELECT
  USING (current_setting('app.tenant_id', true) IS NULL);