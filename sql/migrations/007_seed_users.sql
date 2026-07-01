-- Migration 007: Seed demo users for auth testing
-- Password for ALL seeded users: demo1234
-- Hash generated with: bcrypt.hashpw(b'demo1234', bcrypt.gensalt())
-- This is a fixed dev/demo credential for a portfolio project -- not a real secret.
-- Run as the `ledger` (admin) role via ADMIN_DATABASE_URL, same as other migrations
-- (this INSERT is exempt from the RLS bootstrap concern -- migrations run pre-auth,
-- outside the app's request lifecycle, as superuser, which is the correct and only
-- place superuser access belongs in this system).

INSERT INTO users (tenant_id, email, role, password_hash) VALUES
  ('a0000000-0000-0000-0000-000000000001', 'admin@alpha.ledgermind.test',   'admin',   '$2b$12$2qlLZOznMNA1QrUBWWza.uUELw9C9Tn.U76YuZYRZVh/piPXbQUtO'),
  ('a0000000-0000-0000-0000-000000000001', 'analyst@alpha.ledgermind.test', 'analyst', '$2b$12$2qlLZOznMNA1QrUBWWza.uUELw9C9Tn.U76YuZYRZVh/piPXbQUtO'),
  ('a0000000-0000-0000-0000-000000000001', 'viewer@alpha.ledgermind.test',  'viewer',  '$2b$12$2qlLZOznMNA1QrUBWWza.uUELw9C9Tn.U76YuZYRZVh/piPXbQUtO'),
  ('b0000000-0000-0000-0000-000000000002', 'admin@beta.ledgermind.test',    'admin',   '$2b$12$2qlLZOznMNA1QrUBWWza.uUELw9C9Tn.U76YuZYRZVh/piPXbQUtO'),
  ('b0000000-0000-0000-0000-000000000002', 'analyst@beta.ledgermind.test',  'analyst', '$2b$12$2qlLZOznMNA1QrUBWWza.uUELw9C9Tn.U76YuZYRZVh/piPXbQUtO'),
  ('b0000000-0000-0000-0000-000000000002', 'viewer@beta.ledgermind.test',   'viewer',  '$2b$12$2qlLZOznMNA1QrUBWWza.uUELw9C9Tn.U76YuZYRZVh/piPXbQUtO')
ON CONFLICT (email) DO NOTHING;