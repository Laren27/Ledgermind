"""
Migration drift checker (read-only).

Diffs sql/migrations/*.sql on disk against the schema_migrations table in the
database. Reports what is pending and what is orphaned. Applies NOTHING --
migrations are applied by hand in the Supabase SQL editor after reading them.

Usage (from backend/, with DATABASE_URL set for the target you name):
    ../venv/bin/python -m scripts.check_migrations --target supabase
    docker compose exec -T backend env PYTHONPATH=/app \
        python -m scripts.check_migrations --target local \
        --migrations-dir /path/to/sql/migrations

Exit codes:
    0  in sync
    1  drift found (pending and/or orphaned)
    2  could not run (bad target, no DATABASE_URL, dir not found, table
       missing, connection failed, target/URL mismatch)

WHY --target IS REQUIRED
------------------------
This project runs TWO databases with deliberately divergent migration sets: the
local docker Postgres and Supabase. They are not two copies of one history.
Some migrations belong to exactly one of them:

  - 018_deterministic_doc_ids_local.sql    is the local counterpart of
    019_deterministic_doc_ids_supabase.sql. Each database has had its doc_ids
    remapped once, by its own migration.
  - 015/016/017 correct values that were wrong on Supabase because it was
    ingested BEFORE the parser fix. The local database was re-ingested after
    the fix and never held the misread figures, so these do not apply to it.
  - 020 registers the transcript row on Supabase. Local registered it through
    its own ingest.

Without a target the tool compared every on-disk file against one connection
and could not be clean by construction. It reported "drift found" permanently
and told the reader to apply 018 in the Supabase SQL editor -- which would
re-run a doc_id remap against an already-remapped database. A checker whose
steady state is a false alarm trains the reader to ignore it; that was the
actual hazard, worse than the drift it claimed to find.

HOW A MIGRATION DECLARES ITS TARGET
-----------------------------------
Filename suffix, for anything written from now on:
    *_local.sql     -> local only
    *_supabase.sql  -> Supabase only
    everything else -> both

Four files predate that convention and cannot be renamed: schema_migrations
stores the filename verbatim, so renaming one would make it read as pending
under its new name and orphaned under its old one, in both databases at once.
They are pinned in _TARGET_OVERRIDES below instead.

NOTE ON WHICH DATABASE: the connection comes from DATABASE_URL, and --target
only says which files to EXPECT. The two are cross-checked below, because
naming one target while pointed at the other produces a confident, meaningless
report -- the precise confusion this flag exists to end.
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg2

TARGETS = ("local", "supabase")

# Migrations whose target is not derivable from the filename. Every entry is a
# file that predates the suffix convention; do not add to this list to avoid
# naming a new migration correctly.
_TARGET_OVERRIDES = {
    # Value corrections for rows that were only ever wrong on Supabase, which
    # was ingested before the parser fix in dba2af8. The local database was
    # re-ingested afterwards and never held the misread figures. Verified
    # 2026-08-11: both databases already agree on all three corrected values.
    "015_correct_eternal_fy26q4_misread_revenue.sql": "supabase",
    "016_correct_eternal_fy26_changes_in_inventories.sql": "supabase",
    "017_correct_titan_paytm_stale_values.sql": "supabase",
    # "supabase" is medial here, not a suffix, so the rule below misses it.
    # Local registered the transcript through its own ingest.
    "020_supabase_transcript_row.sql": "supabase",
}


def migration_target(filename: str) -> str:
    """'local', 'supabase', or 'both' for one migration filename."""
    if filename in _TARGET_OVERRIDES:
        return _TARGET_OVERRIDES[filename]
    stem = filename[:-4] if filename.endswith(".sql") else filename
    for target in TARGETS:
        if stem.endswith(f"_{target}"):
            return target
    return "both"


def applies_to(filename: str, target: str) -> bool:
    declared = migration_target(filename)
    return declared in (target, "both")


def resolve_migrations_dir(explicit: str | None) -> tuple[Path | None, list[Path]]:
    """
    Locate sql/migrations. Returns (resolved_or_None, candidates_tried).

    sql/ lives at the repo root, OUTSIDE the ./backend:/app bind mount, so
    parents[2] resolves to "/" inside the container and the old single-guess
    path could never be found there. Candidates are tried in order and the
    winner is always printed, so which directory was read is never implicit.
    """
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return (path if path.is_dir() else None), [path]

    env = os.getenv("LEDGERMIND_MIGRATIONS_DIR")
    if env:
        path = Path(env).expanduser().resolve()
        return (path if path.is_dir() else None), [path]

    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "sql" / "migrations",   # host checkout: backend/scripts -> repo root
        here.parents[1] / "sql" / "migrations",   # sql/ vendored under backend/
        Path("/repo/sql/migrations"),             # container, if repo root is mounted
    ]
    for path in candidates:
        if path.is_dir():
            return path, candidates
    return None, candidates


def url_looks_like(database_url: str) -> str | None:
    """Best-effort target inference from the connection string, or None."""
    host = database_url.split("@")[-1].split("/")[0].lower()
    if "supabase" in host:
        return "supabase"
    if host.startswith(("postgres:", "localhost:", "127.0.0.1:")):
        return "local"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only migration drift checker. Applies nothing."
    )
    parser.add_argument(
        "--target", required=True, choices=TARGETS,
        help="Which database's expected migration set to check against. "
             "REQUIRED: the two databases have divergent sets by design.",
    )
    parser.add_argument(
        "--migrations-dir", default=None,
        help="Path to sql/migrations. Falls back to $LEDGERMIND_MIGRATIONS_DIR, "
             "then to paths derived from this file's location.",
    )
    parser.add_argument(
        "--allow-target-mismatch", action="store_true",
        help="Proceed even when DATABASE_URL does not look like --target.",
    )
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set.")
        print("  export $(grep -v '^#' ~/ledgermind/.env | xargs)")
        return 2

    migrations_dir, tried = resolve_migrations_dir(args.migrations_dir)
    if migrations_dir is None:
        print("ERROR: migrations dir not found. Looked in:")
        for path in tried:
            print(f"    {path}")
        print()
        print("  sql/ lives at the REPO ROOT, which is outside the ./backend:/app")
        print("  bind mount -- so it is not visible inside the backend container")
        print("  by default. Pass --migrations-dir, or set")
        print("  LEDGERMIND_MIGRATIONS_DIR, or run from a host checkout.")
        return 2

    inferred = url_looks_like(database_url)
    if inferred is not None and inferred != args.target and not args.allow_target_mismatch:
        print(f"ERROR: --target {args.target} but DATABASE_URL looks like {inferred}.")
        print(f"  URL host: {database_url.split('@')[-1].split('/')[0]}")
        print("  Checking one database against the other's expected set produces")
        print("  a confident, meaningless report. Fix DATABASE_URL or --target.")
        print("  Override with --allow-target-mismatch if the inference is wrong.")
        return 2

    all_on_disk = {p.name for p in migrations_dir.glob("*.sql")}
    on_disk = {f for f in all_on_disk if applies_to(f, args.target)}
    not_for_target = sorted(all_on_disk - on_disk)

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

    print(f"Target:         {args.target}")
    print(f"Migrations dir: {migrations_dir}")
    print(f"Database:       {database_url.split('@')[-1].split('/')[0]}")
    print()
    print(f"Applied ({len(applied)}):")
    for f in applied:
        note = recorded.get(f)
        print(f"  [ok]      {f}" + (f"  -- {note}" if note else ""))

    if not_for_target:
        print()
        print(f"Not for this target ({len(not_for_target)}) -- on disk, other database's:")
        for f in not_for_target:
            print(f"  [skip]    {f}  -- belongs to: {migration_target(f)}")

    if pending:
        print()
        print(f"PENDING ({len(pending)}) -- on disk, NOT applied to {args.target}:")
        for f in pending:
            print(f"  [pending] {f}")
        if args.target == "supabase":
            print("  Apply these in the Supabase SQL editor, then add a row to")
            print("  schema_migrations recording each one.")
        else:
            print("  Apply these to the LOCAL docker Postgres, then add a row to")
            print("  schema_migrations recording each one. Do NOT apply them in")
            print("  the Supabase SQL editor -- this is the local target.")

    if orphaned:
        print()
        print(f"ORPHANED ({len(orphaned)}) -- applied, NOT expected for {args.target}:")
        for f in orphaned:
            note = recorded.get(f)
            print(f"  [orphan]  {f}" + (f"  -- {note}" if note else ""))
        print("  Either the file was deleted from the repo, or it is recorded in")
        print("  the wrong database. A fresh environment will NOT have these.")

    print()
    if pending or orphaned:
        print("RESULT: drift found.")
        return 1
    print("RESULT: in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
