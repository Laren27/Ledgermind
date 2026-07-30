"""
Migration drift checker (read-only).

Diffs sql/migrations/*.sql on disk against the schema_migrations table in the
database. Reports what is pending and what is orphaned. Applies NOTHING --
migrations are applied by hand in the Supabase SQL editor after reading them.

Usage (from backend/):
    python scripts/check_migrations.py

Exit codes:
    0  in sync
    1  drift found (pending and/or orphaned)
    2  could not run (no DATABASE_URL, table missing, connection failed)

NOTE ON WHICH DATABASE: this reads DATABASE_URL from the environment, which
points at Supabase (production). The backend container uses its own hardcoded
LOCAL Postgres URL and holds DIFFERENT data. Know which one you are checking.
"""

import os
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "sql" / "migrations"


def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set.")
        print("  export $(grep -v '^#' ~/ledgermind/.env | xargs)")
        return 2

    if not MIGRATIONS_DIR.is_dir():
        print(f"ERROR: migrations dir not found: {MIGRATIONS_DIR}")
        return 2

    on_disk = {p.name for p in MIGRATIONS_DIR.glob("*.sql")}

    try:
        conn = psycopg2.connect(database_url)
    except Exception as e:
        print(f"ERROR: could not connect: {e}")
        return 2

    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations')")
            if cur.fetchone()[0] is None:
                print("ERROR: schema_migrations table does not exist.")
                print("  Apply sql/migrations/012_schema_migrations.sql first.")
                return 2
            cur.execute("SELECT filename, note FROM schema_migrations")
            recorded = {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()

    pending = sorted(on_disk - recorded.keys())
    orphaned = sorted(recorded.keys() - on_disk)
    applied = sorted(on_disk & recorded.keys())

    print(f"Migrations dir: {MIGRATIONS_DIR}")
    print(f"Database:       {database_url.split('@')[-1].split('/')[0]}")
    print()
    print(f"Applied ({len(applied)}):")
    for f in applied:
        note = recorded.get(f)
        print(f"  [ok]      {f}" + (f"  -- {note}" if note else ""))

    if pending:
        print()
        print(f"PENDING ({len(pending)}) -- on disk, NOT applied:")
        for f in pending:
            print(f"  [pending] {f}")
        print("  Apply these in the Supabase SQL editor, then add a row to")
        print("  schema_migrations recording each one.")

    if orphaned:
        print()
        print(f"ORPHANED ({len(orphaned)}) -- applied, NOT in repo:")
        for f in orphaned:
            note = recorded.get(f)
            print(f"  [orphan]  {f}" + (f"  -- {note}" if note else ""))
        print("  A fresh environment built from this repo will NOT have these.")

    print()
    if pending or orphaned:
        print("RESULT: drift found.")
        return 1
    print("RESULT: in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
