# Day 13 — Relational Modelling: The LedgerMind Schema

**Phase 4 — Where the truth lives · Weight: M (~90 min) · Prerequisites: Day 10**

---

## 1. Today's goal

By tonight you can:

- Draw the five tables and their foreign keys **from the DDL alone**.
- Explain every column of `financials`, and why `value` is `NUMERIC` and not
  `FLOAT`.
- Explain what a `CHECK` constraint buys and why `audit_log.query_path` has one.
- Explain why `audit_log` has no `DELETE` grant — and why that is a *stronger*
  guarantee than a code rule.

---

## 2. Why now

Days 10–12 gave you Python. The quantitative path (Days 31–34) *is* SQL, and
tenant isolation (Day 14) is a database feature. Neither can be taught before
the schema exists in your head.

There is also a Day 4 thread to close: you learned there is no `DELETE` endpoint,
and that the reason lives "a layer below the API". Today is that layer.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| JSON has no exact decimals | Day 5 | Why `value` is `NUMERIC` |
| `db_transaction` yields a connection | Day 11 | You will query by hand today |
| No `psql` in the backend image | Day 1 | You query via `python -c` |

---

## 4. Concept lesson

### 4.1 The problem a relational database solves

**Before databases:** files. To find "Eternal's FY26 revenue" you read the whole
file. Two programs writing at once corrupt it. Nothing prevents storing
`"consolidated"` in one row and `"Consolidated"` in another.

**Key-value stores** solve lookup and concurrency, and solve nothing about
*structure*: nothing enforces that a value is a number, that a document exists
before a financial row references it, or that two writes happen together or not
at all.

**A relational database gives you four things no file can:**

| Property | What it prevents |
|---|---|
| **Types** | `value` holding `"about 54 thousand"` |
| **Constraints** | `financial_type = "Consolidated"` alongside `"consolidated"` |
| **Referential integrity** | A `financials` row citing a `doc_id` that does not exist |
| **Transactions** | Half a restatement — the old row retired, the new one never inserted |

**Mental model.** A spreadsheet is **a piece of paper**: you can write anything
anywhere. A relational table is **a form printed with typed boxes** — and a clerk
who rejects the form if a box is wrong.

---

### 4.2 The vocabulary, on this schema

- **Table** — `financials`.
- **Row** — one metric, for one company, one period, one basis.
- **Column** — `value`, `fiscal_year`, `is_latest`.
- **Primary key** — the column uniquely identifying a row. Here always a `UUID`.
- **Foreign key** — a column that must match a primary key elsewhere.
- **Constraint** — a rule the database enforces on every write.
- **Index** — a lookup structure. Day 15.

---

### 4.3 Why UUID primary keys?

```sql
tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid()
```

**Alternative:** `SERIAL` (auto-incrementing integer). Smaller, faster to index,
human-readable.

**Why UUID here — three reasons, in increasing importance:**

1. **Generatable client-side.** `document_classifier.derive_doc_id()` computes a
   doc_id in Python *before* any insert.
2. **Non-guessable.** A sequential id leaks how many rows exist and invites
   enumeration. In a multi-tenant system that matters.
3. **The decisive one — merge-safe across databases.** This project has **two**
   databases (local Docker, Supabase) sharing **one** Qdrant collection. With
   `SERIAL`, both mint id 1 for different documents.

That third reason is not hypothetical. Migrations 018–019 exist because
`doc_id` was `uuid4()` **per ingest**, so each database minted a different id for
the same PDF, and only one side's citations could resolve. The fix was
`uuid5(namespace, sha256_checksum)` — **derived from content**, so the same PDF
yields the same id everywhere, forever.

---

### 4.4 `NUMERIC` versus `FLOAT` — the most important type decision here

```sql
value NUMERIC NOT NULL
```

**`FLOAT`/`DOUBLE PRECISION`** is IEEE 754 binary floating point. Fast, and
**cannot represent most decimal fractions exactly**:

```python
>>> 0.1 + 0.2
0.30000000000000004
>>> 54364.10 + 0.05 == 54364.15
False
```

**`NUMERIC`** is arbitrary-precision decimal. Slower, exact.

**Why this is non-negotiable here.** The system's entire claim is that its
numbers are *exactly* right — `README.md` says the eval asserts exact values
because "this system's claim is that numbers are exactly right — a pass/fail
property, not a 0–1 faithfulness score". A float would introduce error at the
storage layer, beneath everything else, and it would be invisible until a
comparison failed by 0.0000001.

**The consequence you must remember.** psycopg2 returns `NUMERIC` as Python
`Decimal`, not `float`. That produces two real complications:

1. **JSON serialisation** — `audit_writer._safe_json` uses `default=str`
   (Day 5), so a `Decimal` survives as a string rather than being rounded.
2. **Comparison** — and this one bit hard. From `db_loader._stored_value_differs`:

   > Compared as FLOATS, deliberately, and not as Decimals. `value` is numeric,
   > so psycopg2 hands it back as Decimal, while `record.value` is a float that
   > came from the parser. `Decimal.__eq__` converts the float to its EXACT
   > binary expansion, so `Decimal("33.33") == 33.33` is **False** — the stored
   > figure and the one that produced it would compare as different, and every
   > such row would be "corrected" to itself on every run.

**Exactness in the database, float comparison at the boundary.** Both are
correct, for different questions, and the comment exists because getting it wrong
produced a self-perpetuating no-op write.

---

### 4.5 `CHECK` constraints — a closed set, enforced by the database

```sql
role TEXT NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer'))

financial_type TEXT NOT NULL CHECK (financial_type IN ('consolidated', 'standalone'))

doc_type TEXT NOT NULL CHECK (doc_type IN (
    'annual_report', 'quarterly_result', 'drhp', 'earnings_transcript'))

query_path TEXT CHECK (query_path IN (
    'semantic', 'quantitative', 'cross', 'blocked', 'unknown'))
```

**Why not enforce in Python?** Because **the database is not only accessed from
Python**. Migrations, the Supabase SQL editor, and maintenance scripts all write
directly. A Python-side check protects one path; a `CHECK` protects all of them.

**And it protects against your own future code.** Day 3, §12 Q19 asked what
changes when you add a fourth query path. The answer everyone misses is *a
migration* — because `audit_log.query_path`'s `CHECK` will reject the new value
and `audit_writer` will fail on every query of the new type. **The constraint is
doing its job**: it will not let the schema and the code disagree silently.

---

### 4.6 The grant model — three roles, and permission as architecture

```sql
CREATE ROLE ledgermind_app WITH LOGIN PASSWORD 'app_dev_pass'
    NOSUPERUSER NOCREATEDB NOCREATEROLE;

GRANT USAGE ON SCHEMA public TO ledgermind_app;
GRANT SELECT, INSERT, UPDATE ON
    tenants, users, documents, financials, audit_log
TO ledgermind_app;

-- audit_log is append-only — no UPDATE or DELETE granted, ever
```

Read the grant line carefully. `SELECT, INSERT, UPDATE`. **No `DELETE`,
anywhere.** And no DDL — `ledgermind_app` cannot `CREATE`, `ALTER` or `DROP`.

**Two consequences you have already met:**

1. **There is no delete endpoint** (Day 4) because there is no delete
   permission. The API surface reflects a database constraint.
2. **You cannot apply migrations** (`CLAUDE.md` §1). `psql "$DATABASE_URL"` fails
   with *"must be owner of table"*. Migrations are written as `.sql` files
   wrapped in `BEGIN;`/`COMMIT;` and applied by hand as a different role.

**Why this is stronger than a code rule.** A code rule ("never delete from
audit_log") is enforced by everyone remembering. A missing grant is enforced by
Postgres, against every client, including a psql session at 2 a.m.

**The comment `-- audit_log is append-only — no UPDATE or DELETE granted, ever`
is slightly inaccurate**, and worth noticing: `UPDATE` *is* granted on all five
tables including `audit_log`. Nothing uses it there, and the intent is clear —
but the comment overstates what the grant enforces. A small documentation drift,
in a security-relevant place.

---

## 5. The actual LedgerMind file

### `sql/init.sql`

```
File:        sql/init.sql
Purpose:     The complete schema — roles, five tables, grants, RLS, indexes
Why it exists: Postgres runs everything in /docker-entrypoint-initdb.d on a
             FIRST-EVER start. This file is that bootstrap.
Runs when:   the data directory is empty. NEVER again.
Consequence: every later change is a numbered migration
Data in:     nothing. It is DDL.
Data out:    a schema
```

That "never again" is why `sql/migrations/` has 17 files. Editing `init.sql` to
add a column changes what a *fresh* environment gets and does nothing to any
existing one — **the two would silently diverge**.

---

## 6. Deep walkthrough — the five tables

### 6.1 `tenants` and `users`

```sql
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    plan        TEXT        NOT NULL DEFAULT 'free',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    user_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    email       TEXT        NOT NULL UNIQUE,
    role        TEXT        NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**`REFERENCES tenants(tenant_id)`** — a user cannot exist without a tenant. The
database refuses the insert.

**`ON DELETE CASCADE`** — deleting a tenant deletes its users. Note the tension
with the grant model: `ledgermind_app` cannot delete a tenant, so this only fires
for an admin role. It encodes intent for the day someone does.

**`email TEXT NOT NULL UNIQUE`** — `UNIQUE` creates an index, which is what makes
the login lookup fast (Day 7).

**`password_hash` is absent.** It was added by migration 006 — evidence that the
schema evolved, and the reason `schema_migrations` exists.

**`role` has a `CHECK`, and `ROLE_RANK` in `dependencies.py` has the same three
values** (Day 8). **Two copies of one fact, in two languages.** Add a role to the
`CHECK` without adding it to `ROLE_RANK` and you get a `KeyError` → 500. This is
exactly the drift class the metric registry was built to end — and here it is
unconsolidated, because one copy is in SQL.

---

### 6.2 `documents`

```sql
CREATE TABLE IF NOT EXISTS documents (
    doc_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    company          TEXT        NOT NULL,
    ticker           TEXT,
    fiscal_year      TEXT,
    quarter          TEXT,
    doc_type         TEXT        NOT NULL CHECK (doc_type IN (...)),
    financial_type   TEXT        NOT NULL CHECK (financial_type IN ('consolidated','standalone')),
    filing_date      DATE        NOT NULL,
    version          TEXT        NOT NULL DEFAULT 'v1',
    is_latest        BOOLEAN     NOT NULL DEFAULT TRUE,
    sha256_checksum  TEXT        UNIQUE,
    ingestion_state  TEXT        NOT NULL DEFAULT 'uploaded' CHECK (ingestion_state IN (
                         'uploaded','processing','indexed','failed')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**`fiscal_year TEXT`, not an integer.** Because Indian fiscal years are `FY26`,
`FY25` — a label, not a number. The consequence is recorded in
`quant_engine._latest_fiscal_year`:

> `fiscal_year` is TEXT, so `MAX()` is **lexical**. Correct for FY23..FY99;
> would break at FY100. Acceptable for the next 74 years.

A limitation, stated with its expiry date. That is how a constraint should be
recorded.

**`sha256_checksum TEXT UNIQUE`** — deduplication. Upload the same PDF twice and
the second insert violates the constraint.

But **one PDF produces TWO rows** (consolidated + standalone), and both would
share a checksum. `document_classifier.section_checksum()` resolves it:

```python
"SHA256 stored as {file_sha256}_{financial_type} to allow two documents
 rows from one PDF while still catching duplicate uploads"
```

**`ingestion_state`** — a state machine in a column: `uploaded → processing →
indexed | failed`. `pipeline._update_document_states` moves it.

---

### 6.3 `financials` — the table the whole quantitative path exists to query

```sql
CREATE TABLE IF NOT EXISTS financials (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    doc_id           UUID        NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    company          TEXT        NOT NULL,
    ticker           TEXT,
    fiscal_year      TEXT        NOT NULL,
    quarter          TEXT,
    financial_type   TEXT        NOT NULL CHECK (financial_type IN ('consolidated','standalone')),
    metric           TEXT        NOT NULL,
    value            NUMERIC     NOT NULL,
    unit             TEXT        NOT NULL DEFAULT 'crore_inr',
    filing_date      DATE        NOT NULL,
    is_latest        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Column by column, with the consequence:**

| Column | Note |
|---|---|
| `doc_id` | **Provenance.** Every number traces to the document it came from. Without this, "verified" would mean nothing |
| `company` + `ticker` | Denormalised — both live on `documents` too. A join saved on every quantitative query |
| `quarter TEXT` **nullable** | `NULL` means *annual*. This is why `db_loader` needs `IS NOT DISTINCT FROM` (Day 15) and why `_base_select` writes `AND quarter IS NULL` explicitly |
| `metric TEXT` | A free-text column with **no `CHECK`** — the closed set lives in `app/metrics/registry.py` instead. See below |
| `value NUMERIC` | Exact. §4.4 |
| `unit` defaults `'crore_inr'` | **Audit finding F3.** Never derived, only asserted |
| `is_latest BOOLEAN` | The restatement mechanism. Day 15 |

**Why `metric` has no `CHECK` when `financial_type` does.** `financial_type` has
two values that will never change. `metric` has ~40 and grows with every filing
type ingested — a `CHECK` would need a migration per metric, and the registry
already owns that list. It is a defensible asymmetry, and it means **the database
cannot stop a typo'd metric name**. That is precisely how audit finding **F6**
happened: 174 stored metric names with no registry anchor, 686 of 1,437 rows.

**`unit` and audit finding F3 — the open blocker.** The default is `'crore_inr'`
and **nothing detects scale**. From `CAVEAT-005`: every value is *asserted* to be
in crore. If a filing reports in millions, the number is stored as though it were
crore and is wrong by 10×, silently. It works today because every corpus document
reports in crore. It is the blocker for arbitrary documents.

---

### 6.4 `audit_log`

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID,
    user_id              UUID,
    query_text           TEXT,
    query_path           TEXT        CHECK (query_path IN (
                             'semantic','quantitative','cross','blocked','unknown')),
    retrieved_chunk_ids  TEXT[],
    vector_scores        NUMERIC[],
    reranker_scores      NUMERIC[],
    dsl_generated        JSONB,
    sql_executed         TEXT,
    confidence_score     NUMERIC,
    response_text        TEXT,
    cache_hit            BOOLEAN     DEFAULT FALSE,
    latency_ms           INTEGER,
    tokens_used          INTEGER,
    llm_provider         TEXT,
    llm_model            TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**`tenant_id UUID` with no `REFERENCES` and no `NOT NULL`** — unlike every other
table. Deliberate: an audit row must survive the deletion of the tenant it
describes. A foreign key with `ON DELETE CASCADE` would erase the audit trail
exactly when it matters most.

**`TEXT[]` and `NUMERIC[]`** — Postgres arrays. `retrieved_chunk_ids`,
`vector_scores` and `reranker_scores` are **parallel arrays**: index *i* of each
describes the same chunk. Fragile — nothing enforces equal lengths — and chosen
over a child table because these are written once and read as a unit.

**`JSONB` for `dsl_generated`** — binary JSON, queryable with `->` and `->>`. The
DSL's shape may evolve, so a schema-less column is right here in a way it would
not be for `financials`.

**`llm_provider` and `llm_model` — nullable and unconstrained, on purpose:**

```sql
-- Nullable and unconstrained by design — see migration 014's header for
-- why NULL is a real state here and why there is no CHECK on provider.
```

`NULL` is **a real state**: a blocked query makes no LLM call, and the synthesis
floor clears attribution when every provider fails (Day 3). No `CHECK` because
adding a provider must not require a migration during an outage.

**`cache_hit BOOLEAN DEFAULT FALSE`** — and nothing ever writes `TRUE`. The
semantic cache was never built. `api/metrics.py` still aggregates it and returns
a permanent 0.0, recorded as open debt rather than deleted (Day 44).

---

## 7. Data flow — how a number gets in and out

```
INGEST                                   QUERY
PDF                                      "Eternal's FY26 revenue?"
 │ pdf_parser                             │ router → DSL (Day 32)
 ▼                                        ▼
PageBlock                                SELECT value, metric, fiscal_year, ...
 │ document_classifier                    FROM financials
 ▼                                        WHERE tenant_id = %s
documents row  ──────────┐                  AND company = %s
 (doc_id, checksum,      │                  AND metric = %s
  filing_date, is_latest)│                  AND fiscal_year = %s
 │ financial_extractor   │                  AND financial_type = %s
 ▼                       │                  AND is_latest = TRUE
FinancialRecord          │                  AND quarter IS NULL
 │ db_loader             │                │
 ▼                       ▼                ▼
financials row ── doc_id ┘           one row, exactly
 (value NUMERIC,                          │
  is_latest,                              ▼
  unit='crore_inr')                  Decimal → float → response
                                          │
                                          ▼
                                     audit_log row
```

**The `doc_id` link is the whole provenance story.** A number in `financials`
points at a `documents` row, which carries `filing_date` and `sha256_checksum`.
That chain is what lets an answer say *"₹54,364 Cr, from this filing"* rather
than just asserting a number.

---

## 8. Engineering decision — raw SQL, no ORM

**Problem.** Store and query exact financial figures with provenance and tenant
isolation.

**Decision.** PostgreSQL, accessed with **raw psycopg2**. No ORM.

`db_loader.py` states it:

> Design decision: psycopg2 with raw SQL. Consistent with Phase 2 decision (raw
> SQL files, no ORM). SQLAlchemy adds nothing for flat record inserts.

| Alternative | Why not |
|---|---|
| **SQLAlchemy Core / ORM** | The SQL *is* the thing being reasoned about here. `_SQL_LOCK_LATEST` with `FOR UPDATE` and `IS NOT DISTINCT FROM` would be harder to read as expression trees, and an ORM's generated SQL is one more thing to verify |
| **Django ORM** | Would bring migrations, admin, and a framework that is not in use |
| **A NoSQL store** | No transactions across a retire-and-insert; no `CHECK`; no `NUMERIC` |
| **DuckDB / SQLite** | No RLS, no concurrent multi-process writes |

**Trade-offs accepted.**

- **You write SQL by hand**, so you must get parameterisation right — and this
  codebase does, everywhere (Day 33).
- **No migration framework.** Hence `schema_migrations` and
  `check_migrations.py` (Day 16).
- **Denormalised `company`/`ticker`** on `financials` — a join saved, at the cost
  of a value that could drift from `documents`.

**Current validity.** Strong. The gaps are `metric` having no `CHECK`
(audit F6) and `unit` being asserted rather than detected (audit F3).

**At 10×.** `financials` would need partitioning by tenant or fiscal year, and
`audit_log` would need a retention policy — it grows without bound and nothing
prunes it (there is no `DELETE` grant to prune it *with*).

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `must be owner of table` | You are `ledgermind_app`. Migrations are applied by hand as a different role |
| `violates check constraint` | A value outside a closed set — often a new `query_path` added in code without a migration |
| `violates foreign key constraint` | A `financials` row citing a `doc_id` that does not exist |
| `duplicate key value violates unique constraint` on checksum | The same PDF, same `financial_type`, uploaded twice |
| A figure wrong by exactly 10× or 100× | Audit **F3** — `unit` asserted as crore, never detected |
| A metric that answers nothing | Audit **F6** — a stored name with no registry anchor |
| Zero rows, no error | RLS. **Day 14** |
| `Decimal("33.33") == 33.33` is False | Compare as floats at the boundary — `_stored_value_differs` |
| Schema change invisible in an existing environment | `init.sql` only runs on a first-ever start |

---

## 10. Hands-on experiment

**No `psql` in the backend image** (Day 1) — query through Python.

```bash
q() { docker compose exec -T backend python -c "
import psycopg2, os, sys
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
cur.execute(\"SET app.tenant_id = %s\", (os.getenv('T','00000000-0000-0000-0000-000000000000'),))
cur.execute(sys.argv[1])
for r in cur.fetchall(): print(r)
c.close()" "$1"; }
```

First find a real tenant:

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
cur.execute('SELECT tenant_id, name FROM tenants')
for r in cur.fetchall(): print(r)
c.close()"
```

Export it: `export T=<the alpha tenant uuid>`

### Experiment 1 — draw the schema from the DDL

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
cur.execute('''
  SELECT table_name, column_name, data_type, is_nullable
  FROM information_schema.columns
  WHERE table_schema='public' AND table_name IN
        ('tenants','users','documents','financials','audit_log')
  ORDER BY table_name, ordinal_position''')
last=None
for t,c_,d,n in cur.fetchall():
    if t!=last: print(f'\n== {t} =='); last=t
    print(f'  {c_:22} {d:26} {\"NULL\" if n==\"YES\" else \"NOT NULL\"}')
c.close()"
```

Now **close this document** and draw the foreign keys from memory. Then check:

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
cur.execute('''
  SELECT tc.table_name, kcu.column_name, ccu.table_name AS refs
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name
  JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name=ccu.constraint_name
  WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' ''')
for r in cur.fetchall(): print(f'  {r[0]}.{r[1]}  ->  {r[2]}')
c.close()"
```

### Experiment 2 — `NUMERIC` versus `FLOAT`, felt

```bash
docker compose exec -T backend python -c "
print('Python float : 0.1 + 0.2 ==', 0.1 + 0.2)
print('             : 54364.10 + 0.05 == 54364.15 ?', 54364.10 + 0.05 == 54364.15)
from decimal import Decimal
print('Decimal      :', Decimal('0.1') + Decimal('0.2'))
print()
print('The Decimal-vs-float comparison trap:')
print('  Decimal(\"33.33\") == 33.33        ->', Decimal('33.33') == 33.33)
print('  float(Decimal(\"33.33\")) == 33.33 ->', float(Decimal('33.33')) == 33.33)
print()
print('That is why _stored_value_differs compares as FLOATS.')
"
```

### Experiment 3 — a `CHECK` refusing a write

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
cur.execute(\"SET app.tenant_id = %s\", (os.getenv('T'),))
try:
    cur.execute('''INSERT INTO audit_log (tenant_id, query_path, query_text)
                   VALUES (%s, 'forecast', 'test')''', (os.getenv('T'),))
except psycopg2.errors.CheckViolation as e:
    print('REFUSED:', str(e).split(chr(10))[0])
    print()
    print('This is why adding a 4th query path needs a MIGRATION, not just code.')
c.close()"
```

### Experiment 4 — real data, real queries

```bash
q "SELECT company, count(*) FROM financials WHERE is_latest GROUP BY company ORDER BY 2 DESC"
q "SELECT metric, value, unit, fiscal_year FROM financials
   WHERE company='ETERNAL' AND fiscal_year='FY26' AND financial_type='consolidated'
     AND quarter IS NULL AND is_latest ORDER BY metric LIMIT 12"
```

Now a **JOIN**, which is the provenance chain made visible:

```bash
q "SELECT f.metric, f.value, d.filing_date, d.doc_type
   FROM financials f JOIN documents d ON f.doc_id = d.doc_id
   WHERE f.company='ETERNAL' AND f.metric='revenue' AND f.is_latest"
```

### Experiment 5 — the grant model

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
cur.execute('''SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type)
               FROM information_schema.role_table_grants
               WHERE grantee='ledgermind_app' GROUP BY table_name ORDER BY table_name''')
for t,p in cur.fetchall(): print(f'  {t:20} {p}')
print()
print('No DELETE anywhere. That is why there is no delete endpoint.')
c.close()"
```

Then prove the DDL restriction:

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
try:
    cur.execute('ALTER TABLE financials ADD COLUMN test_col TEXT')
except Exception as e:
    print('DDL refused:', str(e).split(chr(10))[0])
    print('  <- this is why you cannot apply migrations')
c.close()"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `sql/init.sql`:

1. List every `CHECK` constraint and the closed set it enforces. For each, say
   where the *same* list also appears in Python.
2. Why does `audit_log.tenant_id` have no `REFERENCES` when every other
   `tenant_id` does?
3. `financial_type` has a `CHECK`; `metric` does not. Give the reason, and name
   the audit finding that resulted.
4. `financials.company` also exists on `documents`. Why store it twice, and what
   is the risk?
5. Find the comment claiming `audit_log` has no `UPDATE` grant. Check the actual
   `GRANT` statement. What do you find?

---

## 12. Self-check questions

**Basic**
1. Name the five tables.
2. What does a foreign key guarantee?
3. What does `NUMERIC` give you that `FLOAT` does not?
4. When does `init.sql` run?
5. Which privileges does `ledgermind_app` hold?

**Code**
6. What does `quarter IS NULL` mean semantically?
7. What is `sha256_checksum` for, and how does one PDF yield two rows?
8. What are `retrieved_chunk_ids`, `vector_scores` and `reranker_scores`, and
   what links them?
9. Why is `dsl_generated` `JSONB`?
10. What does `ingestion_state` track?

**Why**
11. Why UUID primary keys instead of `SERIAL`?
12. Why no `DELETE` grant?
13. Why is `fiscal_year` `TEXT`, and what does that cost?
14. Why are `llm_provider` and `llm_model` nullable and unconstrained?
15. Why raw psycopg2 instead of an ORM?

**Debugging**
16. `violates check constraint "audit_log_query_path_check"`. What did someone
    just do?
17. A stored figure is exactly 10× too large. Which audit finding, and why did
    nothing catch it?
18. A backfill reports every row "corrected" on every run, changing nothing.
    What is the bug?

**System design**
19. `audit_log` grows without bound and there is no `DELETE` grant. Design a
    retention policy that does not weaken the append-only guarantee.
20. Audit F3: `unit` is asserted, never detected. Sketch how you would detect
    scale, and name the property of the current corpus that makes this
    unmeasurable today.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `users.role` → `('admin','analyst','viewer')`, mirrored by `ROLE_RANK` in
   `auth/dependencies.py`. `documents.doc_type` → four values, mirrored by the
   `doc_type` form field in `api/documents.py` and `TRANSCRIPT_DOC_TYPE` in
   `chunker.py`. `documents.financial_type` and `financials.financial_type` →
   `('consolidated','standalone')`, mirrored by `FinancialType` in
   `ingestion/models.py` and `VALID_FINANCIAL_TYPES` in `dsl_compiler.py`.
   `documents.ingestion_state` → four values, mirrored by `DocState`.
   `audit_log.query_path` → five values, mirrored by the `path` `Literal` in
   `engines/state.py`. **Every one is two copies of one fact in two languages.**
2. So an audit row **survives the deletion of the tenant it describes**. With
   `REFERENCES ... ON DELETE CASCADE` (the pattern used everywhere else), deleting
   a tenant would erase its entire audit trail — precisely when that trail is
   most valuable.
3. `financial_type` has two values that will never change; `metric` has ~40 and
   grows with every new filing type, so a `CHECK` would need a migration per
   metric. The registry owns that list instead. **The cost is audit finding F6**:
   174 stored metric names with no registry anchor, across 686 of 1,437 rows —
   the database cannot stop a typo'd metric name.
4. **Denormalisation**: it saves a join on every quantitative query, and
   `_base_select` filters on `company` directly. **The risk** is drift — if a
   `documents` row's company were corrected, the `financials` rows would keep the
   old value, and nothing enforces agreement.
5. The comment says *"audit_log is append-only — no UPDATE or DELETE granted,
   ever"*, but the actual `GRANT SELECT, INSERT, UPDATE ON tenants, users,
   documents, financials, audit_log` **does grant `UPDATE`** on `audit_log`.
   Nothing uses it, and `DELETE` is genuinely absent — but the comment overstates
   what the grant enforces. A small documentation drift in a security-relevant
   place, worth recording.

### §12 — Basic

1. `tenants`, `users`, `documents`, `financials`, `audit_log`.
2. That the referenced row exists. The database refuses an insert citing a
   non-existent key.
3. **Exactness.** `NUMERIC` is arbitrary-precision decimal; `FLOAT` is binary and
   cannot represent most decimal fractions exactly.
4. Only when the Postgres data directory is **empty** — a first-ever start. Never
   again, which is why every later change is a numbered migration.
5. `SELECT, INSERT, UPDATE` on the five tables, plus `USAGE` on the schema.
   **No `DELETE`, no DDL.** The role is `NOSUPERUSER NOCREATEDB NOCREATEROLE`.

### §12 — Code

6. **Annual**, as opposed to a specific quarter. It is why `_base_select` writes
   `AND quarter IS NULL` explicitly rather than binding a parameter, and why
   `db_loader` needs `IS NOT DISTINCT FROM` to match on it.
7. Deduplication of uploads. One PDF yields two `documents` rows (consolidated
   and standalone), and `section_checksum()` stores
   `{file_sha256}_{financial_type}` so both rows have distinct checksums while a
   genuine re-upload of the same PDF still collides.
8. **Parallel arrays** describing the retrieved chunks: index *i* of each refers
   to the same chunk. Nothing enforces equal lengths — the coupling is by
   convention.
9. Because the DSL's shape may evolve, and `JSONB` stores it without a schema
   while still being queryable with `->` / `->>`. The opposite choice from
   `financials`, where the shape is fixed and exactness matters.
10. The ingestion state machine: `uploaded → processing → indexed | failed`,
    moved by `pipeline._update_document_states`.

### §12 — Why

11. Three reasons, in increasing importance: they can be generated client-side
    (`derive_doc_id` computes one before insert); they are non-guessable, so a
    sequential id does not leak row counts in a multi-tenant system; and —
    decisively — they are **merge-safe across databases**. This project has two
    databases sharing one Qdrant collection, and `SERIAL` would mint id 1 for
    two different documents. Migrations 018–019 exist because even `uuid4()` was
    insufficient: the id had to be *derived from content*.
12. So that append-only is enforced by **Postgres against every client**, not by
    everyone remembering a code rule. It is also why there is no delete endpoint
    (Day 4) — the API reflects a permission that does not exist.
13. Because Indian fiscal years are labels (`FY26`), not numbers. **The cost:**
    `MAX(fiscal_year)` is lexical, which is correct for FY23–FY99 and breaks at
    FY100 — recorded in `_latest_fiscal_year` with its expiry date.
14. `NULL` is a **real state**: a blocked query makes no LLM call, and the
    synthesis floor clears attribution when every provider fails. No `CHECK`
    because adding a provider must not require a migration — least of all during
    an outage, which is when you would be adding one.
15. Because the SQL *is* the thing being reasoned about. `_SQL_LOCK_LATEST` with
    `FOR UPDATE` and `IS NOT DISTINCT FROM` is clearer as SQL than as an
    expression tree, and an ORM's generated SQL is one more thing to verify.
    "SQLAlchemy adds nothing for flat record inserts."

### §12 — Debugging

16. Added a new `query_path` value in Python — most likely a fourth engine path —
    **without a migration** to extend the `CHECK`. `audit_writer` now fails on
    every query of that type. The constraint is working: it refuses to let the
    schema and the code disagree silently.
17. **Audit finding F3** (`CAVEAT-005`). `unit` defaults to `'crore_inr'` and
    nothing detects scale, so a filing reporting in millions is stored as though
    it were crore. Nothing caught it because there is no cross-check between the
    stated unit and the magnitude — and every corpus document happens to report
    in crore, so the assertion has never been wrong yet.
18. `_stored_value_differs` is comparing a `Decimal` (from psycopg2) against a
    `float` (from the parser) **directly**. `Decimal.__eq__` converts the float to
    its exact binary expansion, so `Decimal("33.33") == 33.33` is `False` and
    every row appears changed. The fix — already in the code — is to compare as
    floats: `float(existing) != float(new)`.

### §12 — System design

19. Partition `audit_log` by month (`PARTITION BY RANGE (created_at)`) and
    **detach** old partitions rather than deleting rows — a detached partition can
    be archived to cold storage or dropped **as the owner role**, never by
    `ledgermind_app`. The append-only guarantee is untouched because the
    application still has no `DELETE`, and the operation is a DDL action
    performed deliberately by a privileged role, which is exactly the boundary
    the grant model draws. (Simply granting `DELETE` to prune would destroy the
    guarantee.)
20. **How to detect:** parse the unit declaration that financial statements
    print in their header — "(₹ in crore)", "(Rs. in millions)", "(₹ in lakhs)" —
    during `section_classifier`, attach it to the `DocSection`, and carry it into
    `FinancialRecord.unit` instead of defaulting. Cross-check with a magnitude
    sanity rule (a listed company's annual revenue in crore is rarely 8 digits).
    **What makes it unmeasurable today:** every document in the corpus reports in
    crore, so there is **no negative case** — any detector would pass trivially
    and you would learn nothing about whether it works. Testing it requires
    ingesting a filing that reports in a different unit, which is why F3 is the
    named blocker for arbitrary documents rather than a bug with a quick fix.

---

## 14. MUST REMEMBER

```text
- Five tables: tenants · users · documents · financials · audit_log
- value is NUMERIC (exact), NOT float. psycopg2 returns Decimal
- Compare Decimal vs float AS FLOATS, or everything looks changed
- quarter IS NULL means ANNUAL
- unit defaults to 'crore_inr' and is NEVER detected — audit F3
- metric has NO CHECK — the registry owns that list — audit F6
- ledgermind_app: SELECT, INSERT, UPDATE. No DELETE. No DDL
- init.sql runs ONLY on a first-ever start. Everything after is a migration
- audit_log.tenant_id has no FK, so the trail survives tenant deletion
```

## 15. MUST UNDERSTAND

```text
- Why a missing GRANT is a stronger guarantee than a code rule
- Why UUIDs were required, and why even uuid4() was insufficient across two
  databases sharing one vector store
- Why exactness belongs in the database and float comparison at the boundary
- Why a CHECK constraint refusing your write is the schema doing its job
- Why denormalising company/ticker trades a join for a drift risk
- Why F3 (unit) is unmeasurable today: there is no negative case in the corpus
```

---

## 16. This connects to

```text
Day 12 — module state
   ↓
Day 13 — the schema                              ← you are here
   ↓
Day 14 — who is allowed to see which rows: transactions, SET LOCAL, RLS
   ↓
Day 15 — indexes, locking, and how restatements work
   ↓
Day 16 — migrations, and the two-database problem
```

Forward references:

- RLS policies on these tables → **Day 14**
- `is_latest`, `IS NOT DISTINCT FROM`, `FOR UPDATE` → **Day 15**
- Migrations 006, 012, 018–019 → **Day 16**
- `_base_select` querying `financials` → **Day 33**
- `unit` / audit F3 → **Day 31**
- The `audit_log` write in full → **Day 44**
