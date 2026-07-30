-- SUPERSEDED BY 010 — DO NOT RELY ON THIS FILE AS THE FIX.
--
-- This migration was applied to production on 2026-07-30 and did NOT resolve
-- the login failures it was written for. The empty-GUC diagnosis below is
-- correct, but the guard is not: PostgreSQL does not guarantee left-to-right
-- evaluation of AND, so the planner could still evaluate the ::uuid cast
-- before the `<> ''` conjunct. Confirmed still failing with pgcode 22P02
-- after this ran. 010 replaces both policies with a single CASE-guarded one,
-- CASE being the only construct that guarantees evaluation order.
--
-- Kept in the repo rather than deleted so the sequence of applied migrations
-- matches what production actually received.

-- Migration 008: fix auth_bootstrap_lookup for reused connections
--
-- BUG (found in production 2026-07-30): login returned 500 on ~95% of
-- attempts whenever any other traffic was running, while succeeding 10/10
-- when the service was idle.
--
-- Root cause: 006's bootstrap policy tested `current_setting(...) IS NULL`.
-- A custom GUC is only NULL on a connection where it has NEVER been set.
-- After any transaction runs `SET LOCAL app.tenant_id`, the GUC continues to
-- exist on that connection and reverts to the EMPTY STRING, not NULL. On a
-- reused connection the bootstrap policy therefore stopped matching, leaving
-- tenant_isolation to evaluate ''::uuid -> invalid input syntax for type uuid.
-- Failures were ~0.85s (instant reject) vs ~2.5s for successes, which is what
-- ruled out connection exhaustion and pointed here.
--
-- Fix: treat unset and empty as the same pre-auth state, and stop
-- tenant_isolation from ever attempting a cast on an empty setting.

DROP POLICY IF EXISTS auth_bootstrap_lookup ON users;
CREATE POLICY auth_bootstrap_lookup ON users
  FOR SELECT
  USING (coalesce(current_setting('app.tenant_id', true), '') = '');

DROP POLICY IF EXISTS tenant_isolation ON users;
CREATE POLICY tenant_isolation ON users
  USING (
    coalesce(current_setting('app.tenant_id', true), '') <> ''
    AND tenant_id = current_setting('app.tenant_id', true)::uuid
  );
