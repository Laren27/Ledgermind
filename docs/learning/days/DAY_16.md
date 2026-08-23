# Day 16 — Migrations Without a Framework, and Two Databases

**Phase 4 · Weight: M (~90 min) · Prerequisites: Day 15**

---

## 1. Today's goal

By tonight you can:

- Explain why `init.sql` cannot be edited to change a schema, and what a
  migration is.
- Explain why this project applies migrations **by hand**, and why
  `check_migrations.py` deliberately does **not** apply anything.
- Explain the `schema_migrations` ledger — including why `applied_at` is `NULL`
  for most rows and why that is more honest than inventing dates.
- Explain the **two-database problem**: why one Qdrant collection served two
  Postgres databases with disjoint primary keys, what it broke, and how
  deterministic doc_ids fixed it.

---

## 2. Why now

Days 13–15 built the schema in your head. Today: how it *changes*, and the
operational reality around it. This closes Phase 4 and settles a question that
has hung over every day since Day 1 — **"which database did that number come
from?"**

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `init.sql` runs only on a first-ever start | Day 13 | The reason migrations exist |
| `ledgermind_app` has no DDL grant | Day 13 | The reason they are applied by hand |
| `doc_id` links `financials` → `documents` | Day 13 | What the two-database bug broke |
| The `DATABASE_URL` override | Day 1 | Today is what it means |

---

## 4. Concept lesson

### 4.1 Why you cannot just edit `init.sql`

Postgres runs `/docker-entrypoint-initdb.d/*.sql` **only when the data directory
is empty**. Add a column to `init.sql` and:

- a **fresh** environment gets it,
- every **existing** environment does not,
- and nothing reports the difference.

**Two environments silently diverge**, and the code — which assumes the column
exists — works in one and fails in the other. That is the "works locally, not in
prod" class from Day 2, with a database instead of an unpushed file.

**A migration** is a numbered, forward-only script capturing one change, applied
in order, exactly once per database.

**Mental model.** `init.sql` is **the original blueprint of a building**.
Migrations are **the record of every alteration since**. You do not renovate by
editing the blueprint — nobody would know what was actually built.

---

### 4.2 Forward-only, and why there are no `down` scripts

Many frameworks pair every migration with a rollback. This project has none, and
that is a deliberate position:

- A rollback that **loses data** (dropping a column that has been written to) is
  not a rollback; it is a second, destructive migration.
- Rollbacks are written when the forward migration is written, and are almost
  never tested.
- The real recovery path for a bad migration is **another forward migration** —
  which is what happened here: `009` was applied, found wrong, and superseded by
  `010`.

**Trade-off:** you cannot cleanly undo. You can only go forward, so a migration
must be read carefully **before** it runs — which is exactly the discipline the
next section enforces.

---

### 4.3 Applied by hand, on purpose

From `CLAUDE.md` §1, the first STOP-AND-ASK rule:

> **Migrations.** You cannot apply them — `ledgermind_app` is NOSUPERUSER and
> `psql "$DATABASE_URL"` fails with "must be owner of table". Write the `.sql`
> file wrapped in `BEGIN;`/`COMMIT;`, then stop. The user applies it by hand in
> the Supabase SQL editor. Afterwards verify **both** `schema_migrations` and
> `information_schema`, and state which database you queried.

Three separate constraints in one paragraph:

1. **You cannot** — the application role has no DDL grant (Day 13). This is
   enforced by Postgres, not by convention.
2. **Wrapped in `BEGIN;`/`COMMIT;`** — so a migration is atomic. A half-applied
   schema change is worse than none.
3. **Verify both** — the ledger says what was recorded; `information_schema` says
   what actually exists. They can disagree, and only checking both catches it.

And migration 012's header states the *design* reason, which is stronger than the
permission reason:

> It deliberately does NOT apply anything: **reading the SQL before it touches
> production is the point**, and an auto-applier on a manually-operated project
> is complexity nobody asked for.

---

### 4.4 The ledger

Migration 012 created `schema_migrations`, and its header is a short history of
what went wrong without one:

> Until now the only record of what production had received was chat history plus
> a directory listing, which produced two real problems: **009 was applied and
> later found to be wrong (superseded by 010, with nothing in the DB recording
> that either was applied)**, and **007a_seed_tenants.sql was applied to Supabase
> but never committed**, so a fresh environment built from this repo would
> silently lack it.

Two failure directions:

| Direction | Symptom |
|---|---|
| Applied, not recorded | Nobody knows what production has |
| Applied, not committed | A fresh environment lacks it, and nothing says so |

The table:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ,          -- NULL for backfilled rows, date unknown
    note        TEXT
);
GRANT SELECT ON schema_migrations TO ledgermind_app;
```

**`GRANT SELECT` only.** The application can read what has been applied; it can
never claim to have applied something.

**No RLS**, and the header says why:

> This is infrastructure metadata, not tenant data. Adding a `tenant_id` here
> would be meaningless — migrations are global.

**`applied_at` is `NULL` for every backfilled row**, and this is the most
instructive line in the file:

> Backfill. `applied_at` is NULL for everything below because the real dates were
> never recorded — that is honest, and **inventing timestamps would make this
> table look more authoritative than it is**.

A plausible date would be indistinguishable from a recorded one. `NULL` says *"we
do not know"*, which is true, and which stops anyone reasoning from a fabricated
ordering. Same principle as omitting `confidence_tier` on a blocked query
(Day 9): **do not assert what you did not measure.**

**And the feature deliberately not built:**

> NICE TO HAVE, not built: a `sha256` column detecting a migration file edited
> after it was applied … Left out because 009 has already been edited post-apply
> to add its superseded header, so the check would fire on a comment change from
> day one. Add it if a real SQL-after-apply edit ever occurs.

A good idea, rejected **because it would produce a known false positive on day
one** — and a check that cries wolf immediately is a check nobody reads.

---

### 4.5 The two-database problem

This is the operational fact that shapes every measurement in this project.

```
      ┌──────────────────────────┐        ┌──────────────────────────┐
      │  LOCAL Docker Postgres   │        │   SUPABASE Postgres      │
      │  11 documents            │        │   9 documents            │
      └────────────┬─────────────┘        └────────────┬─────────────┘
                   │                                   │
                   └───────────────┬───────────────────┘
                                   ▼
                   ┌───────────────────────────────┐
                   │   ONE Qdrant Cloud collection │
                   │      ledgermind_chunks        │
                   └───────────────────────────────┘
```

**How this happened.** `docker-compose.yml` sets `DATABASE_URL` in an
`environment:` block, which **overrides** `env_file: .env` (Day 1). So the local
container reads local Postgres regardless of what `.env` says. Meanwhile
`QDRANT_URL` flows through `env_file` and points at the Cloud collection in both.

**What broke.** `register_sections` minted `uuid4()` **per ingest**, so the same
PDF got a different `doc_id` in each database, and the dedup conflict is
per-database. One Qdrant chunk carries one `doc_id` in its payload — so **only
one side's citations could resolve to a `documents` row**.

**And then the repair made it worse.** From the README:

> 139 Qdrant chunks were deleted as "orphans" on the strength of a lookup against
> local only — they were production's Paytm and Titan corpus. *A checker that can
> structurally only inspect one of two stores passes having inspected nothing.*

**The fix — migrations 018 and 019.** Make `doc_id` **derived from content**:

```
doc_id = uuid5(namespace, sha256_checksum)
```

The same PDF now yields the same `doc_id` in every database, forever. Migration
018 is the local remap, 019 the Supabase one, and 018's header says the split is
deliberate:

> LOCAL DOCKER POSTGRES ONLY. Supabase is 019. **Applying this file to Supabase
> aborts at the first assertion** (these old ids do not exist there), which is
> intended.

**A migration that refuses to run in the wrong place.** That is a safety property
built into the SQL itself, not a warning in a comment.

---

## 5. The actual LedgerMind files

```
sql/migrations/            17 files. Numbering starts at 003, includes a 007a
sql/migrations/006_*.sql   the auth bootstrap policy            (Day 7, 14)
sql/migrations/009,010,011 the RLS CASE guard, over three tries (Day 14)
sql/migrations/012_*.sql   the schema_migrations ledger
sql/migrations/013_*.sql   fixes audit_log's query_path CHECK
sql/migrations/014_*.sql   adds llm_provider / llm_model        (Day 19)
sql/migrations/015,016,017 DATA corrections — misread values
sql/migrations/018,019     deterministic doc_ids, one per database
sql/migrations/020_*.sql   the Supabase transcript row
backend/scripts/check_migrations.py   diffs the ledger against the directory
```

**Note the numbering.** It starts at `003` and includes `007a` — so **neither the
file count nor the highest number is a migration count**. Count the files
(Day 13's correction to `LEDGERMIND_ARCHITECTURE.md` was exactly this mistake).

**Note `015`–`017` are data corrections, not schema changes.** Fixing a misread
value in production is a migration here, because the application role cannot do
it and because the change must be recorded and reviewable. Their names say what
they are: `correct_eternal_fy26q4_misread_revenue`,
`correct_titan_paytm_stale_values`.

---

## 6. Deep walkthrough — migration 018

```sql
-- 018 — deterministic doc_ids. LOCAL DOCKER POSTGRES ONLY.
-- ...
-- MUST BE APPLIED AS `ledger` OR A SUPERUSER: this drops and restores an FK,
-- and ledgermind_app owns neither table. financials_doc_id_fkey is NO ACTION
-- on update and NOT deferrable, so the UPDATE is rejected in either order
-- unless the constraint is removed first. Definition captured verbatim from
-- pg_get_constraintdef on 2026-08-09.
--
-- Apply with ON_ERROR_STOP=1. Without it psql continues past a failed
-- statement and COMMIT succeeds on a half-applied file.
--
-- IMPLIES A FULL RE-INGEST: chunk_id is md5(doc_id:...) and IS the Qdrant
-- point ID. Qdrant is stale from this commit until Phase 3 completes.

BEGIN;

CREATE TEMP TABLE doc_id_map (old_id UUID PRIMARY KEY, new_id UUID UNIQUE, label TEXT)
  ON COMMIT DROP;

INSERT INTO doc_id_map VALUES
  ('bd300f21-...','d662a604-...','ETERNAL FY24 consolidated'),
  ...
```

**Six things this header does that a bare `ALTER TABLE` would not.**

**1. Names the target database, in the first line.** And makes running it
elsewhere *fail* rather than corrupt.

**2. States the role required, and why.** `financials_doc_id_fkey` is
`NO ACTION ON UPDATE` and **not deferrable**, so updating `documents.doc_id`
before `financials.doc_id` violates the FK, and the reverse order violates it too.
The constraint must be dropped and restored — which needs ownership.

**3. Records where the constraint definition came from.** *"Captured verbatim
from `pg_get_constraintdef` on 2026-08-09."* The restore is not reconstructed from
memory; it is a copy of what actually existed.

**4. Names the psql flag and what happens without it.** Without
`ON_ERROR_STOP=1`, psql **continues past a failed statement** and `COMMIT`
succeeds on a half-applied file. The transaction does not protect you if the
client keeps going.

**5. States the blast radius outside Postgres.** `chunk_id` is
`md5(doc_id:...)` and **is** the Qdrant point ID — so changing `doc_id` changes
every chunk ID, and **Qdrant is stale from this commit until a full re-ingest
completes**. A schema migration whose real cost is in a different data store.

**6. Enumerates the mapping explicitly** rather than computing it. Nine hard-coded
`(old_id, new_id, label)` triples, each labelled with a human-readable document
name. Computing the new ids inside the migration would work — and would be
unreviewable. Written out, a human can check them against
`derive_doc_id(checksum)` before anything runs.

**`CREATE TEMP TABLE ... ON COMMIT DROP`** — scratch space that cannot survive
the transaction, so a failed migration leaves nothing behind.

**`DO $$ ... $$` assertion blocks** — the migration checks its own preconditions
and raises if they do not hold. This is what makes running 018 against Supabase
abort at the first assertion rather than doing damage.

---

### 6.1 `check_migrations.py` — a differ, not an applier

```
Purpose:  diff schema_migrations against sql/migrations/*.sql
Reports:  PENDING  — on disk, not in the ledger
          ORPHANED — in the ledger, not on disk
Applies:  NOTHING
```

**Why not auto-apply?** Migration 012 answers it: *reading the SQL before it
touches production is the point.* An auto-applier turns a reviewed, deliberate
act into an invisible one.

**And it had its own bug**, recorded in `CAVEATS.md` as audit finding **F11**:

> `check_migrations` gave wrong advice about two databases — **Closed** (the
> tool); the two databases remain — `CAVEAT-015`.

The tool checked one database and reported as though it had checked the system.
Same shape as the 139-chunk deletion: *a checker that can structurally only
inspect one of two stores passes having inspected nothing.*

**Hence the rule** in `CLAUDE.md` §1: after applying, verify **both**
`schema_migrations` and `information_schema`, **and state which database you
queried.**

---

## 7. Data flow — a schema change, end to end

```
1. A code change needs a column
        │
2. Write sql/migrations/0NN_name.sql
        ├─ BEGIN; ... COMMIT;
        ├─ header: which database, which role, what it implies elsewhere
        ├─ DO $$ ... $$ preconditions
        └─ INSERT INTO schema_migrations (filename, applied_at, note)
        │
3. STOP.  ← You cannot apply it. NOSUPERUSER.
        │
4. A human applies it, in the Supabase SQL editor / as `ledger`,
   with ON_ERROR_STOP=1
        │
5. VERIFY BOTH:
        ├─ SELECT * FROM schema_migrations   ← what was recorded
        └─ information_schema.columns        ← what actually exists
        │
6. STATE WHICH DATABASE.  Local and Supabase are different.
        │
7. If the change alters doc_id or chunk_id → RE-INGEST, because
   Qdrant point IDs are derived from them
```

**Step 7 is the one people forget.** Postgres is not the only store.

---

## 8. Engineering decision — no migration framework

**Problem.** Evolve a schema across two databases, one of which is
hand-operated, with an application role that has no DDL rights.

**Decision.** Numbered `.sql` files, applied by hand, recorded in a
`schema_migrations` table, diffed by a read-only script.

| Alternative | Why not |
|---|---|
| **Alembic** | Auto-generates migrations from ORM models — and there is no ORM (Day 13). Auto-generated DDL is exactly what the header discipline exists to prevent |
| **Auto-apply on deploy** | *Reading the SQL before it touches production is the point.* The application role also cannot do it |
| **`django-migrate`-style** | Framework not in use |
| **Just edit `init.sql`** | Silently diverges fresh and existing environments |
| **Down migrations** | Rarely tested; a data-losing "rollback" is a second destructive migration. Recovery here is another forward migration |

**Trade-offs accepted.**

- **Manual application** — a human step, therefore forgettable. Mitigated by the
  ledger and `check_migrations.py`.
- **No rollback.** Recovery is forward-only. `009 → 010` is the worked example.
- **No checksum detection**, deliberately, because it would false-positive on day
  one.
- **Two databases stay two databases.** `CAVEAT-015` is open, and the compose
  override is still there.

**Current validity.** Appropriate for a hand-operated project. The ledger closed
the recording gap; the two-database divergence is unclosed and is why every
measurement must state its source.

**At 10×.** A deploy pipeline that applies migrations automatically **against one
database**, with the review moved to pull-request time rather than removed. The
two-database problem must be eliminated first, not automated around.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `must be owner of table` | You are `ledgermind_app`. Correct — write the file and stop |
| Half-applied migration despite `BEGIN;` | `ON_ERROR_STOP=1` missing; psql continued past a failure |
| Fresh environment lacks a table | A migration applied but never committed (the `007a` case) |
| Ledger and `information_schema` disagree | Applied without recording, or recorded without applying |
| Citations resolve to nothing | The two-database `doc_id` divergence |
| Chunks "orphaned" that are not | A checker inspecting one of two stores |
| Qdrant stale after a migration | `doc_id` changed → `chunk_id` changed → re-ingest required |
| Two environments give different answers | `CAVEAT-015`. **State which database.** |

---

## 10. Hands-on experiment

### Experiment 1 — count them correctly

```bash
ls sql/migrations/*.sql | wc -l          # 17 — the migration count
ls sql/*.sql | wc -l                     # 2  — init.sql + seed.sql
ls sql/migrations/ | head -20
```

Note it starts at `003` and includes `007a`. **Neither the highest number nor the
directory listing is a count.**

### Experiment 2 — read the ledger

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
try:
    cur.execute('SELECT filename, applied_at, note FROM schema_migrations ORDER BY filename')
    for f,a,n in cur.fetchall():
        print(f'{f:48} {str(a):28} {n or \"\"}')
except Exception as e:
    print('no ledger in this database:', str(e).split(chr(10))[0])
c.close()"
```

Most `applied_at` values are `NULL`. **That is the honesty**, not a bug.

### Experiment 3 — verify both sides

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('''SELECT column_name FROM information_schema.columns
               WHERE table_name='audit_log' AND column_name IN ('llm_provider','llm_model')''')
print('columns 014 added, actually present:', [r[0] for r in cur.fetchall()])
cur.execute(\"SELECT filename FROM schema_migrations WHERE filename LIKE '014%'\")
print('ledger says applied              :', [r[0] for r in cur.fetchall()])
print()
print('Both must agree. Checking one proves nothing.')
c.close()"
```

### Experiment 4 — which database are you actually on?

```bash
echo "--- .env says ---";               grep '^DATABASE_URL' .env | sed 's/:[^:@]*@/:***@/'
echo "--- compose overrides with ---";  grep -A1 'DATABASE_URL' docker-compose.yml | head -2
echo "--- the container reads ---";     docker compose exec -T backend printenv DATABASE_URL | sed 's/:[^:@]*@/:***@/'
echo
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SELECT current_database(), inet_server_addr()')
print('connected to:', cur.fetchone())
cur.execute('SELECT count(*) FROM documents')
print('documents   :', cur.fetchone()[0], ' <- 11 local vs 9 Supabase')
c.close()"
```

**Write the answer down.** For the rest of this course, "which database?" must be
instantly answerable.

### Experiment 5 — deterministic doc_ids, verified

```bash
docker compose exec -T backend python -c "
from app.ingestion.document_classifier import derive_doc_id, LEDGERMIND_DOC_NS
print('namespace:', LEDGERMIND_DOC_NS)
for ck in ('abc123', 'abc123', 'def456'):
    print(f'  derive_doc_id({ck!r:10}) = {derive_doc_id(ck)}')
print()
print('Same checksum -> same id. Every database. Forever.')
print('That is what migrations 018/019 installed.')
"
```

Then check a real one:

```bash
docker compose exec -T backend python -c "
import psycopg2, os
from app.ingestion.document_classifier import derive_doc_id
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SELECT doc_id, sha256_checksum, company FROM documents LIMIT 3')
for doc_id, ck, co in cur.fetchall():
    print(f'{co:10} stored={doc_id}')
    print(f'{\"\":10} derived={derive_doc_id(ck)}  match={str(doc_id)==derive_doc_id(ck)}')
c.close()"
```

### Experiment 6 — the diff tool

```bash
docker compose exec -T -w /app backend env PYTHONPATH=/app python -m scripts.check_migrations 2>&1 | head -30
```

Read what it reports **and** what it does not do: it never applies anything.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `sql/migrations/012_schema_migrations.sql` and
`sql/migrations/018_deterministic_doc_ids_local.sql`:

1. Two real problems motivated the ledger. Name both, and say which direction of
   failure each represents.
2. Why is `applied_at` `NULL` for the backfilled rows?
3. One feature is described as "NICE TO HAVE, not built". What is it, and what is
   the reason for leaving it out?
4. Migration 018 says it must be applied as `ledger` or a superuser. Give the
   specific constraint that forces this.
5. 018 says it "IMPLIES A FULL RE-INGEST". Trace the chain: which value changes,
   what is derived from it, and which store goes stale.

---

## 12. Self-check questions

**Basic**
1. Why can `init.sql` not be edited to change the schema?
2. How many migrations are there, and how do you count them correctly?
3. What does `check_migrations.py` do — and not do?
4. Which role applies migrations?
5. What does `schema_migrations` record?

**Code**
6. What privilege does `ledgermind_app` have on `schema_migrations`?
7. Why does `schema_migrations` have no RLS?
8. What does `ON COMMIT DROP` do?
9. What does `ON_ERROR_STOP=1` prevent?
10. What does `derive_doc_id` compute from?

**Why**
11. Why apply by hand instead of automatically?
12. Why are there no down migrations?
13. Why is `applied_at` `NULL` rather than a plausible date?
14. Why are 018 and 019 separate files?
15. Why is a data correction a migration here?

**Debugging**
16. A fresh environment lacks a table that production has. What happened, and
    what would have caught it?
17. Citations resolve to no document. Which problem, and what is the fix?
18. A migration is half-applied despite `BEGIN;`/`COMMIT;`. How?

**System design**
19. Design the elimination of the two-database problem. Name what must change and
    what must be verified.
20. `check_migrations.py` once gave wrong advice by inspecting one of two stores.
    Generalise that failure and name two other places in this system with the same
    shape.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **(a)** `009` was applied and later found wrong (superseded by `010`), with
   **nothing in the database recording that either had been applied** — the
   *applied-but-not-recorded* direction. **(b)** `007a_seed_tenants.sql` was
   applied to Supabase but **never committed**, so a fresh environment built from
   the repo would silently lack it — the *applied-but-not-committed* direction.
2. Because the real dates were never recorded. Inventing plausible timestamps
   would make the table **look more authoritative than it is** — a fabricated date
   is indistinguishable from a recorded one, and someone would later reason from
   an ordering that was never observed. `NULL` says "we do not know", which is
   true.
3. A `sha256` column detecting a migration file edited **after** it was applied
   (so a fresh environment would receive different SQL than production did). Left
   out because `009` had already been edited post-apply to add its superseded
   header, **so the check would fire on a comment change from day one** — and a
   check that false-positives immediately is a check nobody reads. To be added if
   a real SQL-after-apply edit ever occurs.
4. `financials_doc_id_fkey` is `NO ACTION ON UPDATE` and **not deferrable**.
   Updating `documents.doc_id` first violates the FK from `financials`; updating
   `financials.doc_id` first violates it against `documents`. **Neither order
   works**, so the constraint must be dropped and restored — and
   `ledgermind_app` owns neither table.
5. `doc_id` changes → `chunk_id` is `md5(doc_id:...)` → **`chunk_id` IS the Qdrant
   point ID** → every point in `ledgermind_chunks` for those documents now has an
   ID that no longer matches what the code will compute. **Qdrant goes stale**
   until a full re-ingest rewrites the points. A Postgres migration whose real
   blast radius is in a different data store.

### §12 — Basic

1. It runs **only when the data directory is empty**. Editing it changes what a
   fresh environment receives and does nothing to any existing one, so the two
   silently diverge.
2. **17.** Count the files in `sql/migrations/`. The numbering starts at `003` and
   includes a `007a`, so neither the highest number nor the `sql/` directory
   listing (19 files, including `init.sql` and `seed.sql`) is a count.
3. **Does:** diff `schema_migrations` against the files on disk and report
   pending / orphaned. **Does not:** apply anything.
4. `ledger`, or another superuser. Not `ledgermind_app`, which is `NOSUPERUSER`
   with no DDL grant.
5. `filename` (primary key), `applied_at` (nullable), and a free-text `note`.

### §12 — Code

6. `SELECT` only. It can read what has been applied and can never claim to have
   applied something.
7. Migrations are **global infrastructure metadata**, not tenant data. A
   `tenant_id` on this table would be meaningless.
8. Drops the temp table when the transaction commits — so a failed migration
   leaves no scratch state behind.
9. psql **continuing past a failed statement**, which would let `COMMIT` succeed
   on a half-applied file. `BEGIN;`/`COMMIT;` alone does not protect you if the
   client keeps sending statements after an error.
10. `uuid5(LEDGERMIND_DOC_NS, sha256_checksum)` — derived from the **file's
    content**, so the same PDF yields the same id in every database.

### §12 — Why

11. Because reading the SQL before it touches production is the point — an
    auto-applier turns a reviewed, deliberate act into an invisible one. And
    because the application role structurally cannot: it has no DDL grant.
12. Because a rollback that loses data is not a rollback but a second destructive
    migration; because rollbacks are written at the same time as the forward
    migration and almost never tested; and because the real recovery path is
    another forward migration — `009 → 010` is the worked example.
13. Because a plausible date is indistinguishable from a recorded one, and would
    invite reasoning from an ordering nobody observed. Same principle as omitting
    `confidence_tier` on a blocked query: **do not assert what you did not
    measure.**
14. Because they remap **different sets of old ids** — the ids that exist in local
    Postgres do not exist in Supabase and vice versa. And because 018 asserts its
    preconditions, so running it against the wrong database **aborts at the first
    assertion**, which the header calls intended. A safety property in the SQL,
    not a warning in a comment.
15. Because `ledgermind_app` cannot perform it (no DDL, and these correct stored
    values through owner-level access), and because a change to production data
    must be **recorded and reviewable** exactly like a schema change. `015`–`017`
    are named for what they correct.

### §12 — Debugging

16. A migration was applied to production but **never committed to the repo** —
    the `007a` case. `check_migrations.py` catches it as **orphaned**: present in
    the ledger, absent from disk. That is precisely why the ledger exists.
17. The **two-database `doc_id` divergence**. Each database minted its own
    `uuid4()` per ingest for the same PDF, while one Qdrant collection served
    both, so a chunk's payload `doc_id` resolved in one database and not the
    other. Fixed by migrations 018/019 making `doc_id = uuid5(ns, checksum)` —
    derived from content, identical everywhere.
18. `ON_ERROR_STOP=1` was not set. psql continued past the failed statement and
    the eventual `COMMIT` succeeded on whatever had run. The transaction is only
    as good as the client's willingness to stop.

### §12 — System design

19. **Eliminate, do not automate around.** Point local development at the same
    Postgres as production is *not* the answer (you would develop against live
    data). The real fix has two parts: **(a)** remove the `DATABASE_URL` override
    from the `environment:` block so `.env` is the single source, per the rule
    that credentials flow only through `env_file`; **(b)** give each database its
    **own Qdrant collection** — the root problem is not two databases, it is one
    vector store serving both. Verify by: confirming `derive_doc_id` agrees with
    every stored `doc_id` in both databases; confirming every Qdrant point's
    payload `doc_id` resolves in its own collection's database; and re-running
    `check_migrations.py` **against each database separately**, stating which.
20. **The generalisation:** *a checker whose scope is narrower than the system it
    reports on passes having inspected nothing* — and its pass is read as
    evidence about the whole. Two other instances in this system: **(a)**
    `regression_check.py` asserts on extraction output **in memory** and passed
    4/4 while 28 stored figures were stale — it validated the producer, not the
    store (Day 43). **(b)** Ingest completion **Gate 4** read a tenant-wide chunk
    count, which was already satisfied before the run started, so it "passed
    unconditionally on any ingest into a non-empty tenant, including one that
    indexed zero chunks" — audit finding **F8**. All three share the shape: the
    instrument could not observe the thing it claimed to certify.

---

## 14. MUST REMEMBER

```text
- init.sql runs ONLY on a first-ever start. Everything after is a migration
- 17 migrations. Numbering starts at 003 and includes 007a — COUNT THE FILES
- You CANNOT apply migrations. Write the .sql in BEGIN;/COMMIT; and STOP
- Apply with ON_ERROR_STOP=1, or COMMIT can succeed on a half-applied file
- Afterwards verify BOTH schema_migrations AND information_schema,
  and STATE WHICH DATABASE
- applied_at is NULL where the date is unknown — never invent one
- Two databases: 11 documents local, 9 Supabase, ONE Qdrant collection
- doc_id = uuid5(ns, sha256) → same PDF, same id, every database
- chunk_id = md5(doc_id:...) IS the Qdrant point ID → doc_id change = re-ingest
```

## 15. MUST UNDERSTAND

```text
- Why editing init.sql silently diverges environments, and why that is the same
  failure class as an unpushed file
- Why "reading the SQL before it touches production" is a stronger reason for
  manual application than the missing permission is
- Why a fabricated timestamp is worse than a NULL one
- Why a good check was rejected for false-positiving on day one
- The general failure: A CHECKER NARROWER THAN THE SYSTEM IT REPORTS ON PASSES
  HAVING INSPECTED NOTHING — and its three instances here
- Why a Postgres migration's real blast radius can be in Qdrant
```

---

## 16. This connects to

```text
Day 15 — how a row is written
   ↓
Day 16 — how the schema itself changes            ← END OF PHASE 4
   ↓
Day 17 — LLM foundations (independent of Phase 4; both feed Phase 6 onward)
```

Forward references:

- Migration 014's `llm_provider` / `llm_model` → **Day 19**
- `doc_id` → `chunk_id` → Qdrant point IDs → **Days 21, 24**
- `regression_check` validating the producer, not the store → **Day 43**
- Gate 4 / audit **F8** → **Day 43**
- `CAVEAT-015` and stating your database → every measurement day
