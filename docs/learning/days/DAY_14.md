# Day 14 — Transactions, `SET LOCAL`, Row-Level Security

**Phase 4 · Weight: H (~120 min) · Prerequisites: Days 9, 11, 13**

---

## 1. Today's goal

By tonight you can:

- Explain ACID, and what a transaction guarantees in the retire-and-insert case.
- Explain Row-Level Security: what a policy is, when it applies, and what
  `FORCE` adds.
- Explain — and **demonstrate** — why *"zero rows"* is not *"no data"*, and why
  that is the most common silent failure in this codebase.
- Explain why the RLS policies use `CASE ... THEN FALSE` rather than `AND`, and
  what breaks with `AND`.
- Trace `tenant_id` from the HTTP request to the policy, and name the point where
  it stops being trustworthy.

---

## 2. Why now

Day 9 established what a role may *see*. Today is what a **tenant** may see — a
different question, answered a layer lower. Day 11 gave you `SET LOCAL`; today is
what it scopes. Day 13 gave you the tables; today is who may read their rows.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `SET` vs `SET LOCAL` | Day 11 | RLS reads the value they set |
| `with conn:` commits/rolls back | Day 11 | Transaction boundaries |
| The five tables | Day 13 | Three of them carry policies |
| `CAVEAT-001` | Day 5 | Where the chain breaks |

---

## 4. Concept lesson

### 4.1 Transactions, and the case that motivates them here

**ACID:** Atomicity (all or nothing), Consistency (constraints hold), Isolation
(concurrent transactions do not see each other's partial work), Durability
(committed means survived).

**Atomicity, concretely, in this system.** A restatement is two writes:

```sql
UPDATE financials SET is_latest = FALSE WHERE id = <old>;   -- retire
INSERT INTO financials (...) VALUES (...);                  -- the new figure
```

Crash between them and you have **no `is_latest` row for that metric** — the
number silently vanishes from every query. Or, in the other order, **two**
`is_latest` rows, and `point_in_time` returns 2 rows and refuses with
`ambiguous_result` (Day 34).

A transaction makes both happen or neither.

**Mental model.** A transaction is **a sealed envelope**. Nobody sees inside
until you seal and post it; if you tear it up, nothing was ever sent.

---

### 4.2 The multi-tenancy problem

Tenant Alpha and tenant Beta both use this system. Alpha must never see Beta's
filings.

**Approach 1 — a `WHERE` clause in every query.**

```python
cur.execute("SELECT * FROM financials WHERE tenant_id = %s AND ...", (tenant_id, ...))
```

Works — until someone forgets. There are dozens of query sites, and a forgotten
clause is a **silent cross-tenant read**: no error, plausible data, wrong
company. `CLAUDE.md` §6 rejects this outright:

> Tenant isolation: Postgres RLS + Qdrant metadata + scoped Redis keys.

**Approach 2 — a database per tenant.** Perfect isolation, and impractical:
migrations × N, connections × N, cross-tenant analytics impossible.

**Approach 3 — Row-Level Security.** Tell Postgres the rule **once**, and it
applies to every query on that table, from every client, forever.

**Mental model.** Approach 1 is **asking every visitor to check their own
badge**. RLS is **a door that only opens for the right badge** — and it is the
same door regardless of who walks up.

---

### 4.3 How RLS works

```sql
ALTER TABLE financials ENABLE ROW LEVEL SECURITY;
ALTER TABLE financials FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_financials ON financials
    USING (
        CASE
            WHEN coalesce(current_setting('app.tenant_id', TRUE), '') = '' THEN FALSE
            ELSE tenant_id = current_setting('app.tenant_id', TRUE)::UUID
        END
    );
```

- **`ENABLE`** turns policies on. **With no policy defined, `ENABLE` denies
  everything** — which is a safe default and a surprising one.
- **`FORCE`** applies policies to the **table owner too**. Without it, the owner
  (`ledger`) bypasses RLS entirely. `init.sql`'s comment: *"belt + suspenders"*.
- **`USING (...)`** is a predicate silently ANDed into every `SELECT`, `UPDATE`
  and `DELETE`. A row for which it is false **does not exist** as far as the
  query is concerned.
- **`current_setting('app.tenant_id', TRUE)`** reads a session variable. The
  `TRUE` means *"return NULL if unset rather than raising"*.

**The critical property:** a filtered-out row produces **no error**. The query
succeeds and returns fewer rows. That is what makes RLS both safe and dangerous —
safe because it cannot leak, dangerous because a *missing* setting looks exactly
like *missing data*.

---

### 4.4 `CASE`, not `AND` — and why that is not style

The obvious way to write the policy:

```sql
USING (current_setting('app.tenant_id', TRUE) IS NOT NULL
       AND tenant_id = current_setting('app.tenant_id', TRUE)::UUID)
```

Reads fine. Is wrong.

**SQL's `AND` does not short-circuit.** Unlike Python, the planner may evaluate
either side first, or both. So when `app.tenant_id` is unset, the right-hand side
may still be evaluated — and `NULL::UUID` is fine, but an **empty string**
`''::UUID` raises:

```
ERROR: invalid input syntax for type uuid: ""
```

`IMPLEMENTATION_DELTAS.md` has a section titled *"§14 — RLS policies: AND is not
a short-circuit operator"*. This was found and fixed.

**The `CASE` version is a genuine conditional** — the `ELSE` branch is only
evaluated when the `WHEN` was false. And `coalesce(..., '')` collapses **both**
failure shapes — unset (`NULL`) and empty (`''`) — into one `THEN FALSE`.

Migrations 009, 010 and 011 are the history of getting this right: `009` was
applied and later found wrong, superseded by `010`, then `011` applied the same
uniform guard to every policy.

**The general lesson:** a security predicate must fail **closed** on every
malformed input, and in SQL you cannot assume evaluation order to get there.

---

### 4.5 The one deliberate exception

Migration 006, which you met on Day 7:

```sql
CREATE POLICY auth_bootstrap_lookup ON users
  FOR SELECT
  USING (current_setting('app.tenant_id', true) IS NULL);
```

**The exact inverse** of every other policy: it permits reads **only when no
tenant context is set**.

Its header states the reasoning and the guard rail:

> Login is the ONE deliberate exception to "tenant_id is always set before any
> query." At login time we don't know the user's tenant yet -- we're looking it
> up BY EMAIL to discover it. So this table gets a second policy that allows
> SELECT when no tenant context has been set at all (pre-auth state). This is
> intentionally narrow: it only permits reads, only on this table, and is
> documented here so nobody "fixes" it into a silent superuser bypass later.

**Two policies on one table are ORed.** So `users` is readable if
(tenant matches) **OR** (no tenant set). Once `SET LOCAL app.tenant_id` runs, the
bootstrap policy is false and normal isolation resumes — which you demonstrated
on Day 7, Experiment 5.

---

## 5. The actual LedgerMind files

```
File: sql/init.sql                 — ENABLE + FORCE + 3 policies
File: sql/migrations/006_users_auth_rls.sql — the bootstrap exception
File: sql/migrations/010, 011      — the uniform CASE guard
File: backend/app/db/session.py    — SET LOCAL, one place
File: backend/app/engines/quant_engine.py::_execute_sql — SET LOCAL, again
File: backend/app/engines/audit_writer.py — SET LOCAL, again
File: backend/app/ingestion/pipeline.py, db_loader.py — plain SET (batch)
```

**Note the count: `SET LOCAL app.tenant_id` appears in three request-path places,
not one.** `db_transaction` owns it for the HTTP layer; the engines set it
themselves because they have no HTTP context. Day 11 covered why that is
defensible; it does mean the discipline lives in three files.

---

## 6. Deep walkthrough

### 6.1 `_execute_sql` — the engine's own transaction

```python
def _execute_sql(sql: str, params: tuple, tenant_id: str) -> List[Dict[str, Any]]:
    """
    Execute a single parameterised SQL query with RLS tenant isolation.

    SET LOCAL app.tenant_id scopes the RLS policy to this transaction only.
    Uses DictCursor so results are accessible by column name.
    """
    conn = None
    try:
        conn = _get_db_connection()
        with conn:                                   # BEGIN
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SET LOCAL app.tenant_id = %s", (str(tenant_id),))
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(row) for row in rows]
    except psycopg2.Error as e:
        logger.error("SQL execution failed: %s | SQL: %s", e, sql[:200])
        raise
    finally:
        if conn:
            conn.close()
```

**STATE BEFORE.** No connection. No tenant context.

**`_get_db_connection()`** — a fresh connection. `CAVEAT-013`, and note the
multiplier: `growth_comparison` calls this **four times**, plus the audit write.
Five TCP connects and five auth handshakes for one question.

**`with conn:` then `SET LOCAL`** — order matters. `SET LOCAL` outside a
transaction is a no-op with a warning. Inside, it lives until `COMMIT`.

**`str(tenant_id)`** — psycopg2 adapts a Python `UUID` as **TEXT**, and
`current_setting` returns text anyway, so this is consistent. (The related trap
from `CLAUDE.md` §7 — `ANY(%s::uuid[])` with `[str(i) for i in ids]` — is the
same adaptation problem in the other direction.)

**`RealDictCursor`** — rows come back as dicts keyed by column name rather than
tuples. So `_compute_yoy_growth` can write `rows[0]["value"]`, and adding a column
to the `SELECT` does not shift positional indexes.

**`return` inside `with conn:`** — the `with` still commits on the way out, and
the outer `finally` still closes. Day 11's guarantee, load-bearing here.

**`except psycopg2.Error: ... raise`** — log and **re-raise**. The caller
(`quant_engine_node`) converts it into `sql_execution_failed` with a user-facing
message. Logging here and deciding there is the right split: this function knows
the SQL, the caller knows the user.

**STATE AFTER.** Rows returned, transaction committed, connection closed,
**`app.tenant_id` gone** — because `SET LOCAL` died with the transaction.

---

### 6.2 The failure that defines this day

```python
# CLAUDE.md §6
"""
Always `SET app.tenant_id` before `financials`/`documents` SELECTs.
RLS silently returns 0 rows otherwise — that is not a data-missing signal.
"""
```

**Walk the failure.**

1. A script queries `financials` and forgets the GUC.
2. The policy evaluates `coalesce(NULL, '') = ''` → `TRUE` → `THEN FALSE`.
3. **Every row is filtered out.**
4. The query **succeeds** and returns `[]`.
5. The script concludes: *"there is no revenue data for ETERNAL FY26."*
6. Someone re-ingests. Or files a bug. Or edits the extractor.

**No error was raised at any point.** This is the single most expensive silent
failure available in this codebase, and it costs an hour every time.

**The two-second check** that separates the two hypotheses:

```sql
SELECT count(*) FROM financials;            -- 0 → could be either
SET app.tenant_id = '<uuid>';
SELECT count(*) FROM financials;            -- >0 → it was the GUC
```

---

### 6.3 `tenant_id`'s full journey, and where it breaks

```
1. LOGIN                auth/service.py
   SELECT tenant_id FROM users WHERE email = %s
   └─ the ONLY unscoped query, permitted by auth_bootstrap_lookup

2. TOKEN                core/security.py
   payload = {"sub": ..., "tenant_id": <uuid>, "role": ...}   signed HS256

3. VERIFY               auth/dependencies.py
   payload = decode_access_token(token)
   user = {"tenant_id": payload["tenant_id"], ...}
   └─ CRYPTOGRAPHICALLY TRUSTWORTHY from here

4. ██████ CAVEAT-001 ██████            api/query.py
   tenant_id = payload.tenant_id or current_user.get("tenant_id", "default")
                └── FROM THE REQUEST BODY, preferred over the verified JWT
   └─ TRUST IS LOST HERE

5. STATE                make_initial_state(tenant_id=...)

6. ENFORCEMENT          three places, all obeying step 4:
   ├─ quant_engine._execute_sql  → SET LOCAL app.tenant_id → RLS
   ├─ retriever._build_filter    → Qdrant FieldCondition(key="tenant_id")
   └─ audit_writer               → SET LOCAL, and the row's tenant_id column
```

**Every defence in step 6 works exactly as designed.** They are all being handed
the wrong tenant by step 4.

From `CAVEAT-001`:

> With one seeded tenant this is unexploitable in practice. The moment a second
> tenant exists it is a full cross-tenant read.

**Severity: Critical** as a multi-tenant product. And note the compounding
irony — `db/session.py`'s docstring *asserts* that `state["tenant_id"]` is
sourced from the verified JWT, which is exactly the assumption `api/query.py`
breaks.

---

### 6.4 The Qdrant half, and the hole between the halves

```python
must_conditions = [
    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
    FieldCondition(key="is_latest", match=MatchValue(value=is_latest)),
]
```

Qdrant has no RLS. Isolation there is **a payload filter the application must
remember to add** — Approach 1, the one rejected for Postgres.

**Why it is acceptable here:** there is exactly **one** query path into Qdrant
(`retriever.hybrid_search`), and the filter is unconditional in `_build_filter`.
One site to audit, not dozens.

**`SECURITY_MODEL.md` §3c — "The hole".** The two stores enforce isolation by
different mechanisms with different failure modes: Postgres fails closed
(0 rows), Qdrant fails open if the condition is ever omitted. And the **company**
condition in that same function is genuinely dropped when no issuer resolves —
logged as `UNFILTERED WHOLE-TENANT SEARCH`. The tenant condition is not dropped,
but the code proves the shape is reachable.

---

## 7. Data flow

```
                      HTTP request + JWT
                             │
                    tenant_id extracted
                             │
              ╔══════════════╧══════════════╗
              ║  CAVEAT-001: the body wins  ║
              ╚══════════════╤══════════════╝
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
      ┌───────────────┐ ┌──────────┐ ┌──────────────┐
      │  POSTGRES     │ │  QDRANT  │ │  AUDIT LOG   │
      │ SET LOCAL     │ │ payload  │ │ SET LOCAL +  │
      │   ↓           │ │ filter   │ │ tenant_id    │
      │ RLS policy    │ │          │ │ column       │
      │   ↓           │ │ MUST be  │ │              │
      │ rows filtered │ │ remembered│ │             │
      │ FAILS CLOSED  │ │ FAILS OPEN│ │             │
      │ (0 rows)      │ │ (all rows)│ │             │
      └───────────────┘ └──────────┘ └──────────────┘
```

---

## 8. Engineering decision — RLS over application `WHERE`

**Problem.** Multi-tenant isolation that cannot be forgotten.

**Decision.** Postgres RLS with `FORCE`, driven by a `SET LOCAL` session
variable, plus a Qdrant payload filter.

`ENGINEERING_DECISIONS.md` **ED-007**.

| Alternative | Why not |
|---|---|
| **`WHERE tenant_id = %s` everywhere** | Dozens of sites; a forgotten one is a silent cross-tenant read |
| **A database per tenant** | Migrations × N; connections × N; no cross-tenant analytics |
| **A schema per tenant** | Better, still N × migrations, and `search_path` becomes the new thing to forget |
| **Application middleware** | Cannot protect a psql session, a migration, or a maintenance script |

**Trade-offs accepted.**

- **The GUC must be set, and forgetting it looks like missing data.** Traded a
  loud, rare leak for a quiet, frequent confusion — and the confusion is the
  better failure.
- **RLS costs planner work** on every query. Immaterial here.
- **Qdrant has no equivalent**, so that half is Approach 1 with one call site.
- **The discipline lives in three request-path files**, not one.

**Current validity.** The mechanism is sound; **the input is not** (`CAVEAT-001`).

**At 10×.** Connection pooling makes `SET` vs `SET LOCAL` a live risk (Day 11).
And `CAVEAT-001` must be closed *before* a second tenant holds data, not after.

---

## 9. Failure modes

| Symptom | Cause | Distinguishing check |
|---|---|---|
| **0 rows, no error** | GUC unset | `SET app.tenant_id` then re-run |
| `invalid input syntax for type uuid: ""` | A policy using `AND` instead of `CASE` | Fixed by 010/011 |
| Owner sees everything | `FORCE` missing | `\d+ financials` |
| An answer about another tenant's company | `CAVEAT-001` | Check the request body |
| Login 401 for a valid user | Bootstrap policy dropped, or a GUC set before login | Migration 006 |
| Batch job returns 0 rows | Plain `SET` outside a transaction | Batch jobs own their connection |
| Qdrant returns another tenant's chunks | The `tenant_id` condition omitted | One call site — `_build_filter` |
| `SET LOCAL` "has no effect" warning | Called outside a transaction | Must be inside `with conn:` |

---

## 10. Hands-on experiment

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SELECT tenant_id, name FROM tenants')
for r in cur.fetchall(): print(r)
c.close()"
```

`export T=<alpha uuid>`

### Experiment 1 — the silent zero

```bash
docker compose exec -T -e T="$T" backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()

cur.execute('SELECT count(*) FROM financials')
print('WITHOUT app.tenant_id :', cur.fetchone()[0], ' <- no error, no warning')

cur.execute('SET app.tenant_id = %s', (os.getenv('T'),))
cur.execute('SELECT count(*) FROM financials')
print('WITH    app.tenant_id :', cur.fetchone()[0])
print()
print('Same query. Same table. The first result is NOT a data-missing signal.')
c.close()"
```

**Sit with this.** It is the most expensive silent failure in this codebase.

### Experiment 2 — `SET LOCAL` dies with the transaction

```bash
docker compose exec -T -e T="$T" backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL'))
with c:
    with c.cursor() as cur:
        cur.execute('SET LOCAL app.tenant_id = %s', (os.getenv('T'),))
        cur.execute('SELECT count(*) FROM financials')
        print('inside txn      :', cur.fetchone()[0])
with c.cursor() as cur:
    cur.execute('SELECT count(*) FROM financials')
    print('after COMMIT    :', cur.fetchone()[0], ' <- SET LOCAL is gone')
c.close()"
```

### Experiment 3 — read the policies

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
cur.execute('''SELECT tablename, policyname, cmd, qual FROM pg_policies
               WHERE schemaname='public' ORDER BY tablename, policyname''')
for t,p,cmd,q in cur.fetchall():
    print(f'{t}.{p}  [{cmd}]'); print(f'   {q}'); print()
c.close()"
```

Find the **two** policies on `users` and note one is the inverse of the other.

### Experiment 4 — `AND` really does not short-circuit

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
c.autocommit = True
try:
    cur.execute(\"SELECT NULL IS NOT NULL AND ''::uuid IS NOT NULL\")
    print('result:', cur.fetchone())
except Exception as e:
    print('RAISED:', str(e).split(chr(10))[0])
    print('  <- the left side is FALSE, yet the right side still evaluated.')
    print('     This is why the policies use CASE, not AND.')
c.close()"
```

### Experiment 5 — atomicity

```bash
docker compose exec -T -e T="$T" backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL'))
try:
    with c:
        with c.cursor() as cur:
            cur.execute('SET LOCAL app.tenant_id = %s', (os.getenv('T'),))
            cur.execute('''INSERT INTO audit_log (tenant_id, query_path, query_text)
                           VALUES (%s,'semantic','ROLLBACK TEST')''', (os.getenv('T'),))
            cur.execute('SELECT count(*) FROM audit_log WHERE query_text=%s', ('ROLLBACK TEST',))
            print('inside txn, before failure:', cur.fetchone()[0])
            raise RuntimeError('simulated failure')
except RuntimeError as e:
    print('exception:', e)
with c:
    with c.cursor() as cur:
        cur.execute('SET LOCAL app.tenant_id = %s', (os.getenv('T'),))
        cur.execute('SELECT count(*) FROM audit_log WHERE query_text=%s', ('ROLLBACK TEST',))
        print('after rollback            :', cur.fetchone()[0], ' <- all or nothing')
c.close()"
```

### Experiment 6 — cross-tenant, end to end

Log in as `admin@beta.ledgermind.test` (tenant Beta holds no documents) and ask
an Eternal question:

```bash
BTOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@beta.ledgermind.test","password":"<password>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $BTOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What was Eternal revenue in FY26?"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error')); print(d.get('response_text')[:200])"
```

`no_data_found` — **from RLS, not from a missing document.** The row exists;
Beta cannot see it. `README.md` says Beta exists precisely to make this testable.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `sql/init.sql`, `sql/migrations/006_users_auth_rls.sql`, and
`backend/app/engines/quant_engine.py::_execute_sql`:

1. Which three tables have RLS? Which two do **not**, and why is that acceptable
   for each?
2. Write out the policy predicate and explain each of its three parts.
3. `users` has two policies. State both and explain how they combine.
4. In `_execute_sql`, why must `SET LOCAL` come **after** `with conn:`?
5. Trace `tenant_id` from `/auth/login` to the RLS policy and name the exact line
   where it stops being trustworthy.

---

## 12. Self-check questions

**Basic**
1. What does a transaction guarantee?
2. What is an RLS policy?
3. What does `FORCE ROW LEVEL SECURITY` add?
4. What does a query return when the GUC is unset?
5. Which session variable drives the policies?

**Code**
6. Where does `SET LOCAL app.tenant_id` appear on the request path?
7. Why `RealDictCursor` in `_execute_sql`?
8. What does `current_setting('app.tenant_id', TRUE)`'s `TRUE` do?
9. How does Qdrant enforce tenant isolation?
10. Which table's policy is the inverse of all the others?

**Why**
11. Why RLS instead of a `WHERE` clause?
12. Why `CASE` and not `AND`?
13. Why `FORCE`?
14. Why does `audit_log` have RLS but no foreign key on `tenant_id`?
15. Why is the Qdrant approach acceptable when the same approach was rejected for
    Postgres?

**Debugging**
16. A script reports "no financial data for ETERNAL FY26". What do you check
    first, and what is the two-command test?
17. `invalid input syntax for type uuid: ""` from a policy. What is wrong?
18. A Beta user gets an answer about Alpha's data. Name the two things that could
    have failed, and which is recorded.

**System design**
19. Write the fix for `CAVEAT-001`, including what must be recorded and why.
20. You add a connection pooler. Enumerate everything that must be audited, and
    write the test that would prevent regression.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **RLS on:** `documents`, `financials`, `audit_log` (in `init.sql`), plus
   `users` (added by migration 006). **Not on:** `tenants` — a tenant row is not
   tenant-scoped data (it *is* the tenant, and scoping it by itself is circular);
   and `schema_migrations` — migration 012's header says *"NO RLS. This is
   infrastructure metadata, not tenant data. Adding a tenant_id here would be
   meaningless — migrations are global."*
2. ```sql
   CASE
     WHEN coalesce(current_setting('app.tenant_id', TRUE), '') = '' THEN FALSE
     ELSE tenant_id = current_setting('app.tenant_id', TRUE)::UUID
   END
   ```
   **(a)** `current_setting(..., TRUE)` reads the GUC, returning `NULL` instead of
   raising when unset. **(b)** `coalesce(..., '')` collapses `NULL` and `''` into
   one case. **(c)** `THEN FALSE` fails closed; the `ELSE` compares, and is only
   reached when a non-empty value exists — so the `::UUID` cast can never see `''`.
3. `tenant_isolation` (normal: `tenant_id = current_setting(...)`) and
   `auth_bootstrap_lookup` (`FOR SELECT USING (current_setting(...) IS NULL)`).
   **Multiple permissive policies are ORed**, so `users` is readable if the tenant
   matches **or** no tenant is set. Once `SET LOCAL` runs the second is false and
   normal isolation resumes.
4. Because `SET LOCAL` is **transaction-scoped**. Outside a transaction it is a
   no-op that emits a warning, so the subsequent query would run with no tenant
   context and return zero rows.
5. `auth/service.py` reads it from `users` → `create_access_token` embeds it →
   `decode_access_token` verifies the signature → **trustworthy**. Then, in
   `api/query.py`:
   ```python
   tenant_id = payload.tenant_id or current_user.get("tenant_id", "default")
   ```
   **That line.** The request body is preferred over the verified JWT.
   `CAVEAT-001`.

### §12 — Basic

1. Atomicity, consistency, isolation, durability — practically: all the writes
   happen or none do, and constraints hold at commit.
2. A predicate Postgres silently ANDs into every query on the table. Rows for
   which it is false do not exist as far as that query is concerned.
3. It applies policies to the **table owner** as well. Without it, the owner
   bypasses RLS entirely.
4. **Zero rows, with no error.** That is not a data-missing signal.
5. `app.tenant_id`.

### §12 — Code

6. Three places: `db/session.py::db_transaction` (the HTTP layer),
   `quant_engine._execute_sql`, and `audit_writer_node`. Ingestion
   (`pipeline.py`, `db_loader.py`) uses a plain `SET` because it owns its
   connection and is never pooled.
7. So rows come back as dicts keyed by column name. `_compute_yoy_growth` writes
   `rows[0]["value"]`, and adding a column to the `SELECT` does not shift
   positional indexes.
8. It makes `current_setting` return `NULL` when the variable is unset instead of
   raising an error — which is what allows the policy to *decide* rather than
   blow up.
9. With a payload filter — `FieldCondition(key="tenant_id", ...)` — added
   unconditionally in `retriever._build_filter`. There is no RLS equivalent in
   Qdrant.
10. `users`, via `auth_bootstrap_lookup`, which permits reads **only when no
    tenant is set**.

### §12 — Why

11. Because a `WHERE` clause must be remembered at dozens of sites, and a
    forgotten one is a **silent cross-tenant read**. RLS states the rule once and
    Postgres applies it to every query from every client — including psql,
    migrations, and maintenance scripts that never touch the application code.
12. Because SQL's `AND` **does not short-circuit** — the planner may evaluate
    either side. With `AND`, an empty-string GUC reaches `''::UUID` and raises
    `invalid input syntax for type uuid`. `CASE` is a genuine conditional, so the
    cast is only reached when a non-empty value exists. Fixed across migrations
    009 → 010 → 011.
13. Because without it the table owner (`ledger`) bypasses every policy, so a
    migration or a maintenance session would silently operate across all tenants.
    `init.sql` calls it "belt + suspenders".
14. **RLS** so a tenant cannot read another tenant's audit trail. **No foreign
    key** so the audit row survives the deletion of the tenant it describes — a
    cascade would erase the trail exactly when it is most valuable.
15. Because there is exactly **one** query path into Qdrant
    (`retriever.hybrid_search` → `_build_filter`) and the tenant condition is
    unconditional there. One site to audit, not dozens. It is still the weaker
    mechanism — Postgres fails closed, Qdrant would fail open — which
    `SECURITY_MODEL.md` §3c records as "the hole".

### §12 — Debugging

16. **Whether `app.tenant_id` was set**, before anything else. The two-command
    test: `SELECT count(*) FROM financials;` (0 → ambiguous), then
    `SET app.tenant_id = '<uuid>';` and re-run (>0 → it was the GUC, and there is
    no data problem at all). Only after that is a data hypothesis worth forming.
17. A policy written with `AND` rather than `CASE`. The GUC is an empty string,
    the left side is false, but SQL's `AND` does not short-circuit so the
    `''::UUID` cast still evaluates and raises. Migrations 010 and 011 replaced
    every such policy with the uniform `CASE` guard.
18. (a) **`CAVEAT-001`** — the request body supplied Alpha's `tenant_id` and every
    layer obediently scoped to Alpha. **This is the recorded one.** (b) The Qdrant
    `tenant_id` payload condition was omitted, so vector search returned Alpha's
    chunks — not currently possible, since `_build_filter` adds it
    unconditionally, but it is the failure the Qdrant half is exposed to because
    it fails open rather than closed.

### §12 — System design

19. Remove `tenant_id` from `QueryRequest` entirely and read it only from
    `current_user`. If an override is genuinely needed for evaluation, gate it
    behind `require_role("admin")` **and** an explicit environment flag, and
    **record in the audit row that an override was used** — because otherwise an
    overridden query is indistinguishable from a normal one in the permanent
    record, which defeats the purpose of having one. Note this is a functional
    change requiring explicit authorisation, which is why the caveat is recorded
    rather than fixed.
20. **Audit:** every `SET app.tenant_id` on a path where a connection could be
    reused must be `SET LOCAL`. Today `ingestion/pipeline.py` and `db_loader.py`
    use a plain `SET` — safe **only** while unpooled, so the pooler's scope must
    exclude them or they must be converted. Also audit that every request-path
    connection actually sets the GUC at all (three places), and that nothing
    relies on a session-level setting persisting between statements.
    **The test:** a pure-function pytest that reads the request-path module
    sources and fails if `SET app.tenant_id` appears without `LOCAL`. It belongs
    in `backend/tests/` because it needs no database — exactly the kind of static
    check `conftest.py`'s zero-network contract is built for.

---

## 14. MUST REMEMBER

```text
- ALWAYS SET app.tenant_id before financials/documents SELECTs
- 0 ROWS IS NOT "NO DATA". It is the most expensive silent failure here
- The policy is CASE ... THEN FALSE, never AND — SQL's AND does not short-circuit
- FORCE applies RLS to the table OWNER too
- users has TWO policies; the bootstrap one is the inverse, SELECT-only
- Multiple permissive policies are ORed
- SET LOCAL must be INSIDE a transaction, or it is a no-op with a warning
- Qdrant has no RLS — isolation is a payload filter that must be remembered
- CAVEAT-001: the request body overrides the verified JWT's tenant_id
```

## 15. MUST UNDERSTAND

```text
- Why RLS trades a loud rare leak for a quiet frequent confusion — and why
  that is the better failure
- Why a security predicate must fail closed on EVERY malformed input, and why
  SQL's evaluation order means you cannot get there with AND
- Why the one exception (auth bootstrap) is safe: narrow, SELECT-only, on one
  table, with a policy that switches itself off the moment a tenant is set
- Why Postgres fails CLOSED and Qdrant fails OPEN, and what that asymmetry means
- That every defence in this chain works correctly while being handed a value
  the caller chose
```

---

## 16. This connects to

```text
Day 13 — the schema
   ↓
Day 14 — who may see which rows                  ← you are here
   ↓
Day 15 — indexes, locking, and restatements
```

Forward references:

- `_execute_sql` inside the quantitative path → **Day 33**
- `_build_filter` and the Qdrant conditions → **Day 27**
- `audit_writer`'s own `SET LOCAL` → **Day 44**
- Migrations 006, 009–011 → **Day 16**
- `CAVEAT-001` and the full threat model → **Day 42**
- Pooling and the `SET`/`SET LOCAL` audit → **Day 45**
