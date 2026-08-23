# Day 44 — Observability, and Debugging by Layer

**Phase 12 · Weight: H (~120 min) · Prerequisites: Days 43, 19**

**Textbook: Part 15 + 15B — CONFIRMS** on logging and tracing.
**Part 17 step 1 — DIVERGES.** The textbook's master flow opens every request
with a **cache check**. LedgerMind has no cache at all, and ships
`cache_hit_rate_pct` returning a permanent `0.0` **on purpose**. That decision —
to keep a metric with no producer rather than delete it — is one of today's two
central ideas. **D1.**

---

## 1. Today's goal

By tonight you can:

- Reconcile **every column** of one `audit_log` row against what you observed.
- Explain what "append-only" means here **exactly** — including the half of it
  that is convention rather than permission (CAVEAT-028).
- Explain why audit failure never blocks a response, and what that costs.
- Explain why `cache_hit_rate_pct` ships at a permanent `0.0` instead of being
  deleted, and why that is a decision rather than an oversight.
- Given a symptom, **name the responsible layer before touching any code.**
- Distinguish, from a single observation, a **network** signature from a
  **retrieval** signature.
- Explain why logs here are single-line with a pgcode.
- Run the four-step pre-flight and say what each step rules out.

---

## 2. Why now

Day 43 gave you the **offline** record: 91 questions, three gates, a JSON file
you must read the header of first. Today is the **online** record: one row per
request, written for every outcome including refusals and blocks.

**They answer different questions.** The eval says *"is the system correct on
questions whose answers we know?"* The audit log says *"what did the system
actually do at 14:44 on this request?"*

**And they share a hazard.** Day 43's rule — *print the providers before the
score* — has the same shape as today's: **establish which layer you are looking
at before forming a theory about it.**

Day 19 is the second prerequisite because provider attribution is the field that
makes an audit row interpretable, and its precedence rules were set there.

---

## 3. Concepts you must know first

| Concept | From | Why it is needed today |
|---|---|---|
| `QueryState`, mutated in place | Day 3 | The audit row is a projection of it |
| RLS, `SET LOCAL app.tenant_id` | Day 14 | The audit write sets it too |
| Provider attribution by precedence | Day 19 | `llm_provider` / `llm_model` |
| Cohere vs local ONNX scales | Day 28 | `reranker_scores` has no unit column |
| `route_after_shield` / `route_after_router` | Days 35–36 | Why `query_path` can be `blocked` |
| The three integrity gates | Day 43 | The same discipline, applied live |

---

## 4. Concept lesson

### 4.1 Lineage, not logging

Most systems log. LedgerMind writes a **lineage record**: one row per request
holding *every decision the pipeline made*, in structured columns.

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID,
    user_id              UUID,
    query_text           TEXT,
    query_path           TEXT        CHECK (query_path IN (
                             'semantic', 'quantitative', 'cross', 'blocked', 'unknown'
                         )),
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

**Read the columns as a map of the pipeline.** Each one is written by a node you
have already studied:

| Column | Written by | Day |
|---|---|---|
| `query_path` | `router_node`, or `'blocked'` | 36, 42 |
| `retrieved_chunk_ids`, `vector_scores`, `reranker_scores` | `retriever` | 27–28 |
| `dsl_generated`, `sql_executed` | `quant_engine` | 32–33 |
| `confidence_score` | `confidence_node` | 29 |
| `response_text` | `response_generator` | 30 |
| `llm_provider`, `llm_model` | `llm/client` precedence | 19 |
| `latency_ms` | `audit_writer` itself | today |
| `cache_hit` | **nothing** | today (D1) |

**`query_path` has a `CHECK` constraint** listing all five legal values —
including `'blocked'` and `'unknown'`. **The database knows about refusals.**
Adding a fourth engine path means a migration, which Day 35 named as the
blast-radius item everyone forgets.

**Three parallel arrays, one contract.** `retrieved_chunk_ids`, `vector_scores`
and `reranker_scores` are positionally aligned — index *i* is one chunk. Nothing
enforces that. It is the same unenforced-contract shape as Day 40's duck-typed
rows, stored this time.

**And `reranker_scores` has no unit column.** Day 28's whole lesson: a score
without its backend is meaningless, and Cohere `[0,1]` versus ONNX logits
`[-12,+2]` are stored in one `NUMERIC[]`. `reranker_backend` reaches the API
response (Day 9) but **is not a column here**. You can recover it from
`llm_provider`? No — different subsystem. **You cannot recover it from the audit
row at all.** Worth knowing before you try to analyse historic scores.

---

### 4.2 Append-only — exactly what is true

`sql/init.sql`:

```sql
GRANT SELECT, INSERT, UPDATE ON
    tenants, users, documents, financials, audit_log
TO ledgermind_app;

-- audit_log is append-only — no UPDATE or DELETE granted, ever
```

**Read those two statements against each other.** `audit_log` is in the grant
list. `UPDATE` is granted. The comment four lines below says it is not.

**Measure it, on both databases:**

```sql
SELECT table_name, privilege_type
FROM information_schema.table_privileges
WHERE grantee = 'ledgermind_app' AND table_schema = 'public'
ORDER BY table_name, privilege_type;
```

| Table | Local Docker | Supabase |
|---|---|---|
| `audit_log` | SELECT · INSERT · **UPDATE** | SELECT · INSERT · **UPDATE** |
| `documents` | SELECT · INSERT · UPDATE · **DELETE** | SELECT · INSERT · UPDATE |
| `financials` | SELECT · INSERT · UPDATE · **DELETE** | SELECT · INSERT · UPDATE |

**So state the property precisely, because the imprecise version is in the
security document:**

- **`DELETE` is genuinely absent on `audit_log`, on both databases.** A row
  **cannot be made to disappear.** That is a real, enforced guarantee and it is
  the important half.
- **`UPDATE` is granted, on both.** A row **can be rewritten in place** —
  `query_text`, `response_text`, `confidence_score`, `created_at`, all of it.
- **Nothing in `app/` or `scripts/` updates `audit_log`.** So the immutability of
  *content* holds **by convention** — which is exactly what
  `SECURITY_MODEL.md:151` says it does not rely on.

**And the two databases disagree about `DELETE` elsewhere.** Locally,
`documents` and `financials` carry it; on Supabase they do not; and **no file in
the repository grants it**. Three scripts issue `DELETE` against those tables
(`purge_orphaned_metrics`, `purge_mangled_metrics`, `db_loader`'s test cleanup),
so **all three succeed locally and would fail on Supabase.**

**Recorded as CAVEAT-028**, both halves, not fixed — one side needs a migration,
the other needs a decision about intent.

**What to take from this.** Not that the design is wrong: `NOSUPERUSER`, no DDL,
no `DELETE` on the audit table, and RLS with `FORCE` are genuinely strong. **The
lesson is that a permission claim is checkable in one query, and this one had
not been checked.** *"A mechanism existing is not the same as a threat being
closed"* — `SECURITY_MODEL.md`'s own framing rule, applied to itself.

---

### 4.3 Audit failure never blocks the response

```python
except Exception as e:
    # Never let audit failure block the response from reaching the user
    logger.error(
        "Audit log write FAILED (response still delivered) | request_id=%s error=%s",
        state["request_id"], e,
    )
return state
```

**A deliberate, documented trade** — *"blueprint §17 graceful degradation
philosophy (no single point of failure kills the system)"*.

**And `CAVEAT-014` records the cost honestly:**

> The caveat is the consequence: **an append-only audit trail with best-effort
> writes.**

**Hold both.** Rows cannot be deleted — but a row that was never written is also
not there, and nothing reconciles "responses served" against "rows written". The
guarantee is about **rows that exist**, not about **completeness**.

**The counterweight is that `api/query.py` protects the write from the client
side** (Day 6):

```python
finally:
    # Never cancel `task`. If the client vanished mid-query the
    # pipeline must still finish so audit_writer_node writes its row.
```

**Two decisions pointing the same way:** a disconnected client must not prevent
the write; a failed write must not prevent the answer.

---

### 4.4 A measurement that changed how a column is written

```python
# Bound in FULL. This line used to take a 500-character prefix, under
# a variable named for a summary, and that name is why the truncation
# read as intentional for as long as it did: 1516 of 4168 stored
# rows (36.4%) were unmarked prefixes. The column is unbounded TEXT in
# sql/init.sql and live, and `query_text` and `sql_executed` on this
# same parameter tuple always bound whole -- the database would have
# accepted the full text all along; the writer never offered it.
response_full = state.get("response_text") or ""
```

**Four things in one comment, and each is worth extracting.**

**1. A variable name concealed a defect.** `response_summary` made truncation
look like a feature. **The name was the documentation, and it was wrong.**

**2. The damage is quantified.** *1516 of 4168 rows (36.4 %)* — not "some rows".

**3. The rows are unmarked.** No ellipsis, no flag. A truncated `response_text`
is byte-indistinguishable from a genuinely short one. **Another
one-value-two-meanings instance** — the sixth in this course — and this one is
*stored*, so it cannot be fixed forward.

**4. Its neighbours proved it was unnecessary.** `query_text` and `sql_executed`
ride the same parameter tuple and always bound whole. **The database would have
accepted it all along.**

**And then the next comment refuses to add a new cap:**

```python
# DETECT AND REPORT, pre-write. Every stored value above 486 chars is
# a prefix, so the true length distribution has never been observed by
# anything. Record it and act on nothing: no cap, and deliberately no
# warn-above-N, because a threshold warning is a cap that has not
# fired yet. Single line -- Render truncates multi-line output.
logger.info(
    "Audit response length | request_id=%s chars=%d path=%s",
    state["request_id"], len(response_full), state.get("path"),
)
```

**"A threshold warning is a cap that has not fired yet."** A `warn if > 5000`
would encode a limit before anyone knows the distribution — and the whole reason
this is being logged is that **the distribution has never been observed**,
because the old truncation destroyed it. **Measure first; the constant comes
after.** `KU-006` tracks the open question.

---

### 4.5 `NULL` as a real state

```python
# NULL is a real state, not missing data: a blocked
# query makes no LLM call, and the synthesis floor
# clears attribution when every provider fails. Both
# must record honestly as "no model served this"
# rather than inheriting whatever ran earlier.
state.get("llm_provider"),
state.get("llm_model"),
```

**Two distinct cases collapse to the same honest answer:** a Prompt Shield block
(Day 42), and a total synthesis outage (Day 19). In both, **no model produced
the answer**, and the row says so.

**The alternative is worse than it sounds.** "Inheriting whatever ran earlier"
means a blocked query recording `gemini` because the previous request did — and
the row would then look like a normal Gemini-served answer with a suspiciously
short latency.

**And it has a downstream consumer** (Day 43): `_integrity_counters` excludes
blocked rows *because* `None` here is a correct record. **The honesty of this
column is what makes the eval gate correct.**

---

### 4.6 A metric with no producer, shipped on purpose

```sql
-- WARNING: structurally always 0.0. The semantic cache described in
-- blueprint §15 was never built (no cache module exists; Redis is only
-- the Celery broker + health check). QueryState.cache_hit is set False
-- in make_initial_state and never written again, so this AVG has no
-- producer. Do NOT surface this in a dashboard as a measurement until
-- a cache actually writes the column. See docs/IMPLEMENTATION_DELTAS.md §B.
ROUND(AVG(CASE WHEN cache_hit THEN 1.0 ELSE 0.0 END) * 100, 1) AS cache_hit_rate_pct,
```

**This is divergence D1**, and the most interesting thing in
`api/metrics.py`.

**The textbook's Part 17 master flow opens every request with a cache check.**
LedgerMind has no cache. Redis is deployed — as the Celery broker and a health
check target — and nothing else. `cache_hit` is set `False` at
`make_initial_state` and never touched again.

**So why does the column exist, the field ship, and the aggregate compute?**

**Because deleting it would delete the record of the debt.** `CAVEAT-009` exists
because the field does. Remove the column, the SQL and the response field, and
in six months the only trace is a blueprint section nobody reads, and someone
re-derives the cache from scratch without knowing it was specified and skipped.

**The mitigation is not deletion. It is a warning at the point of use**, plus a
caveat, plus `lib/api.ts`:

```ts
// Always false — the semantic cache (blueprint §15) was never built and
// nothing writes this. Kept in the contract for when it is. Do not render.
cache_hit?: boolean;
```

**Marked at every layer it passes through: SQL, caveat, TypeScript.** And the
frontend obeys — nothing renders it (Day 40's mandate: a stat with no producer
must not be shown).

**Contrast this with dead code (Day 40).** `AnswerCard` is unreachable and ships
nothing. `cache_hit_rate_pct` **executes on every metrics request** and returns a
real number that means nothing. **Live code with no input is a different problem
from code with no caller**, and it is more dangerous, because it looks like a
measurement.

**And it is not alone.** `CAVEAT-010`: `tokens_used` also has no producer.
`CAVEAT-008`: the restatement confidence penalty has no producer. **Three
fields, three caveats, all recorded rather than deleted.**

**The rule this establishes:** *do not delete evidence of unfinished work; mark
it at every layer, and do not render it.*

---

### 4.7 The metrics endpoint, and what it can and cannot tell you

Five aggregates, all `WHERE tenant_id = current_setting('app.tenant_id')::uuid`,
admin-only:

| Aggregate | Reads | Trustworthy? |
|---|---|---|
| `total_queries` | `COUNT(*)` | **Yes** — but counts retries as separate questions (Day 39) |
| `cache_hit_rate_pct` | `AVG(cache_hit)` | **No.** Structurally 0.0 |
| `avg_latency_ms` | `AVG(latency_ms)` | Yes, and **blocked rows drag it down** — a block is single-digit ms |
| `p95_latency_ms` | `PERCENTILE_CONT(0.95)` | Yes, and the more useful one |
| `refusal_rate_pct` | `AVG(confidence_score < 0.5)` | **Careful** — §4.8 |

Plus four distributions: `path_distribution`, `volume_by_day`,
`confidence_distribution`, `avg_latency_by_path`.

**Note the explicit `WHERE` clause.** Every other tenant-scoped query in this
codebase relies on RLS. Here the predicate is written out **as well** —
belt-and-braces, and harmless, since RLS applies regardless.

**And `avg_latency_by_path` adds `AND latency_ms > 0`**, excluding rows where
the write happened before timing was set.

---

### 4.8 `refusal_rate_pct` is a proxy, and it drifts

```sql
ROUND(AVG(CASE WHEN confidence_score < 0.5 THEN 1.0 ELSE 0.0 END) * 100, 1) AS refusal_rate_pct
```

**A "refusal" here is defined as `confidence_score < 0.5`.** That is a proxy, and
it has three problems worth naming — **none of them is recorded as a caveat, and
this is where today's reading adds something.**

**(1) Blocked queries have `confidence_score = 0.0`.** Deliberately (Day 42:
*"`confidence_score` is deliberately LEFT AT 0.0 … making it null would
retroactively change what those aggregates mean"*). So **every Prompt Shield
block counts as a refusal** — defensible, and worth knowing when 12 % of your
golden set is adversarial.

**(2) A genuine `low_confidence_refusal` and a merely-mediocre answer both
qualify** if the score lands under 0.5. The column records a *score*, not an
*outcome*; `error` and `error_node` are not consulted.

**(3) The threshold is duplicated.** `0.5` here is a **third** copy of a boundary
that lives as `COHERE_HIGH = 0.5` in `semantic_engine.py` and again in the
`confidence_distribution` CASE just below. Change the measured constant and this
aggregate silently means something else.

**That is the `_compute_derived_totals` / `validate_financial_identities` failure
class again** (Days 31, 37, 43) — its fourth appearance, in SQL this time. It has
no victim today because the constant is frozen (`ED-025`), **and the freeze is
what is protecting it**, not the design.

> **Observation, not a defect.** Nothing is currently wrong. Recorded here
> because the day after `COHERE_HIGH` moves, two dashboard numbers mean something
> different and nothing will say so.

---

### 4.9 Single-line logs, and pgcode

`CLAUDE.md` §7:

> Render truncates multi-line tracebacks — log exceptions single-line with
> pgcode.

**Two separate instructions.**

**Single-line** because Render's log viewer truncates multi-line output, so a
traceback arrives with its most useful line missing.

**With pgcode** because psycopg2 exceptions carry `e.pgcode` (a five-character
SQLSTATE) and `e.pgerror`. `pgcode` **classifies** the failure where the message
only describes it:

| SQLSTATE | Meaning | What you would check |
|---|---|---|
| `23505` | unique violation | the partial unique index (Day 15) |
| `42501` | insufficient privilege | **CAVEAT-028** |
| `23503` | FK violation | `doc_id` missing from `documents` (CAVEAT-016) |
| `55P03` | lock not available | `SELECT … FOR UPDATE` (Day 15) |
| `57014` | query cancelled | a timeout |

**And `SECURITY_MODEL.md` §9 records the same discipline from the privacy side:**
login failures log `pgcode`/`pgerror`, **never the password**.

**Render logs are UTC; the shell is IST** — a 5 h 30 m offset, and correlating a
log line with a wall-clock complaint without that is how you conclude nothing
happened.

---

### 4.10 Debugging by layer — the actual method

`DEBUGGING_GUIDE.md` §0 opens with four rules that come **before** any debugging:

> 1. **Never patch blind.** Diagnose from real output before writing any fix.
> 2. **When a diagnostic contradicts a stated prediction, stop.** Do not continue
>    past it.
> 3. **When a fix does not work, stop tuning the number and go measure.**
> 4. **Cause cannot be assigned from a single before/after pair.** This was
>    attempted three times in one session and was wrong every time. The instrument
>    that settled each: *three runs, with provider and model printed per run.*

**And one corollary specific to this codebase:**

> **An empty candidate set is a network signature; a low-scoring one is a
> retrieval signature.** Establish which you have before theorising.

**That single sentence saves the most time**, so understand *why* it works.
Retrieval returns candidates and scores them. **Zero candidates** means the
search never happened or reached nothing — Qdrant unreachable, wrong URL,
collection empty, filter over-constrained. **Twenty candidates scoring −11**
means the search happened and the corpus does not contain the answer. **The two
have no remedy in common**, and both present as "the answer is wrong".

**The layer ladder**, from "the answer is wrong" downward:

```
Is it a WRONG NUMBER or WRONG TEXT?
  │
  ├─ WRONG NUMBER  → quantitative
  │     sql_verified?  no → refused before the LLM. Read `error`
  │     dsl_object    → did the model pick the right metric/period? (CAVEAT-004)
  │     sql_query     → does the compiled SQL say what you expect?
  │     run it by hand WITH `SET app.tenant_id` — else 0 rows, NOT an error
  │     still wrong  → EXTRACTION, not the query path. regression_check.
  │
  └─ WRONG TEXT    → semantic / cross
        citations empty?  → NETWORK first, retrieval second
        reranker_backend? → cohere [0,1] or local [-12,+2]. WITHOUT THIS THE
                            SCORES MEAN NOTHING
        chunks relevant but answer wrong → SYNTHESIS. Read response_text in
                            full, never the eval's 200-char preview
        chunks irrelevant → RETRIEVAL. Check the filter, then the query text
```

**Notice where the LLM appears: last.** The most common instinct is to blame the
model. The ladder puts it after network, retrieval, ranking, filtering and
compilation — because in this system almost every wrong answer has a determinate
cause upstream of it.

---

### 4.11 The pre-flight, and what each step rules out

```bash
# (a) which code is actually running?
docker compose exec -T backend python -c "import app.engines.retriever as m; print(m.__file__)"

# (b) which environment?
docker compose exec -T backend printenv GEMINI_MODEL
docker compose exec -T backend printenv QDRANT_URL     # MUST be the https Cloud URL
docker compose exec -T backend printenv DATABASE_URL   # local Docker, NOT Supabase

# (c) warm the process — a fresh exec costs ~4s cold; loop 5 and read the later ones

# (d) for anything reading reranker_score: read reranker_backend from the SAME response
```

| Step | Rules out |
|---|---|
| (a) | A backgrounded local uvicorn serving stale code (`lsof -i :8000`) |
| (b) `GEMINI_MODEL` | Attributing a result to a model that never served it |
| (b) `QDRANT_URL` | Measuring the small local collection instead of Cloud |
| (b) `DATABASE_URL` | Comparing 11 local documents against 9 on Supabase |
| (c) | Reporting a 30 s cold fastembed/ONNX load as a latency defect |
| (d) | Reading an ONNX logit as a Cohere probability |

**Known-bad signatures, each a real observation:**

| What you see | What it means |
|---|---|
| `UserWarning: Api key is used with an insecure connection` | You are on **local Docker Qdrant**, not Cloud. **Every measurement is invalid** |
| `UserWarning: Failed to obtain server version` | `qdrant_client` failed its construction-time probe. The next query in that process **will die** |
| A local semantic failure on a **cold** process | Not a defect until it reproduces warm. Cold ≈ 30 s; warm is 0.36–0.41 s |
| `exec failed: … possible container breakout detected` | A stale mount namespace after `--force-recreate`. **Not a security event.** Confirm with `docker compose exec -T backend echo alive`, then recreate and poll `/health` |

**That last one is worth the space it takes.** The message names a security
event; the cause is a stale namespace; `-w /app` does not help and no `cd`
helps, **because every exec fails, including `echo`**. The one-liner that
confirms it is the diagnostic.

---

## 5. The actual LedgerMind files

```
File:  backend/app/engines/audit_writer.py (~140 lines)      Tier 4
Entry: audit_writer_node(state) -> QueryState            TERMINAL NODE, every path
Helpers: _get_db_connection() · _safe_json(value)
Writes: 15 columns + created_at, one INSERT, inside SET LOCAL app.tenant_id
Never raises. Logs the failure and returns state unchanged.
Note:  opens its OWN connection rather than using db_transaction — CAVEAT-013
       (a new connection per statement) applies here too.

File:  backend/app/api/metrics.py (~130 lines)               Tier 1 (no docstring)
Entry: GET /api/metrics                                       admin only
SQL:   _SQL_SUMMARY · _SQL_PATH_DIST · _SQL_VOLUME_BY_DAY ·
       _SQL_CONFIDENCE_DIST · _SQL_LATENCY_BY_PATH
Models: MetricsResponse{summary, path_distribution, volume_by_day,
        confidence_distribution, avg_latency_by_path}
Note:  cache_hit_rate_pct is structurally 0.0 and carries a WARNING comment
       (D1, CAVEAT-009). refusal_rate_pct's 0.5 is a THIRD copy of COHERE_HIGH.

File:  sql/init.sql — audit_log DDL, GRANTs, RLS + FORCE
Note:  line 126 grants UPDATE on audit_log; line 130 says it does not.
       CAVEAT-028.

File:  docs/engineering/DEBUGGING_GUIDE.md (12 sections)
       §0 four rules · §1 pre-flight · §2 trace one request · §3 which kind of
       wrong · §4 wrong number · §5 wrong text · §6 LLM · §7 auth · §8 database ·
       §9 docker · §10 frontend · §11 evaluation · §12 the cheap test
```

---

## 6. Deep walkthrough — `audit_writer_node`

**STATE BEFORE.** Every node has run (or been skipped). `response_text` is set.
The user **already has the answer** — `api/query.py` yields `complete` from the
graph's final state, and this node runs inside that graph.

**Step 1 — latency, computed here and written back.**

```python
latency_ms = int((time.time() - state["start_time"]) * 1000)
state["latency_ms"] = latency_ms
```

**Note it is written back into state**, not just into the row — so
`role_filtered_response` can serve it to an admin (Day 9). **One computation, two
consumers.**

**And note what it measures:** `start_time` to *now*, i.e. **the whole pipeline
including this node's own preamble**, but **not** the audit INSERT itself
(computed before the try block). Not network time to the browser.

**Step 2 — the three parallel arrays.**

```python
retrieved_chunk_ids = [c["chunk_id"] for c in state.get("retrieved_chunks", [])]
vector_scores       = [c["rrf_score"] for c in state.get("retrieved_chunks", [])]
reranker_scores     = [c["reranker_score"] for c in state.get("retrieved_chunks", [])]
```

**Three list comprehensions over the same list** — so alignment is guaranteed *by
construction here*, and by nothing at all once stored. On a quantitative or
blocked path all three are `[]`.

`ChunkResult` is a **TypedDict**, so `c["chunk_id"]` — never `getattr`
(`CLAUDE.md` §7).

**Step 3 — the full response, and the pre-write log** (§4.4).

**Step 4 — connection, transaction, tenant.**

```python
conn = _get_db_connection()
with conn:
    with conn.cursor() as cur:
        cur.execute("SET LOCAL app.tenant_id = %s", (str(state["tenant_id"]),))
```

**`with conn:` is a transaction, not a connection context manager** — psycopg2
commits on clean exit and rolls back on exception, and **does not close**. Hence
the explicit `conn.close()` after the block.

**`SET LOCAL` is transaction-scoped**, so the GUC dies with the transaction and
cannot leak into a pooled connection's next user (Day 14). And it is
**parameterised** — `SECURITY_MODEL.md` §6 flags that an f-string here would make
the tenant boundary injectable.

**Step 5 — one INSERT, fifteen columns.**

```python
state.get("path") or ("blocked" if state["is_blocked"] else "unknown"),
```

**The `or` supplies the CHECK-legal value** when `path` is `None`. Both fallbacks
are in the constraint's list, so an unrouted, unblocked failure lands as
`'unknown'` rather than violating the constraint and losing the row entirely.

```python
json.dumps(_safe_json(state.get("dsl_object"))) if state.get("dsl_object") else None,
```

**`_safe_json` exists for one reason:**

```python
"""
psycopg2 JSONB adapter needs plain JSON-serialisable values.
Decimal types from SQL results need explicit float conversion.
"""
try:
    return json.loads(json.dumps(value, default=str))
except (TypeError, ValueError):
    return None
```

**A round-trip through `json.dumps(default=str)` to coerce `Decimal`,
`datetime` and `UUID` into strings.** Postgres `NUMERIC` arrives as
`decimal.Decimal`, which is not JSON-serialisable — a real failure that would
otherwise lose the whole row.

**And it returns `None` on failure rather than raising** — so an unserialisable
DSL costs you `dsl_generated`, not the audit row.

**Step 6 — the success log.**

```python
logger.info(
    "Audit log written | request_id=%s path=%s latency_ms=%d "
    "confidence=%.2f provider=%s model=%s", …
)
```

**Single line, pipe-delimited, six named fields.** Greppable, and it survives
Render's truncation.

**Step 7 — the catch that never re-raises** (§4.3).

**STATE AFTER.** One row. `state["latency_ms"]` set. The graph ends.

---

## 7. Data flow — one request, and the two records it leaves

```
POST /api/query  ──►  make_initial_state()   start_time, cache_hit=False
        │
        ▼  prompt_shield ─ blocked ─────────────────┐   NO LLM CALL
        ▼  router          path=…                   │   llm_provider stays None
        ▼  engine(s)       chunks · dsl · sql       │
        ▼  confidence      confidence_score         │
        ▼  response_gen    response_text · provider │
        │                                            │
        └──────────────────┬─────────────────────────┘
                           ▼
                  audit_writer_node
                    latency_ms = now - start_time      ← written back to state
                    3 parallel arrays from retrieved_chunks
                    response_full = response_text      ← IN FULL (was a 500-char prefix)
                    logger.info("Audit response length | …")   pre-write, single line
                    ┌──────────────────────────────────────┐
                    │ SET LOCAL app.tenant_id = %s          │ transaction-scoped
                    │ INSERT INTO audit_log (…15 cols…)     │ INSERT only
                    │   query_path = path or 'blocked'/'unknown'   ← CHECK-legal
                    │   dsl_generated = _safe_json(…)       ← Decimal → str
                    │   llm_provider = NULL if no model served
                    └──────────────────────────────────────┘
                    on exception: logger.error, DO NOT RAISE   ← CAVEAT-014
                           │
                           ▼
                    the user already has the answer
                           │
          ┌────────────────┴─────────────────┐
          ▼                                  ▼
   GET /api/metrics (admin)          psql, by hand
     total_queries                     the actual row
     cache_hit_rate_pct  ← 0.0 ALWAYS. No producer. D1 / CAVEAT-009
     avg / p95 latency
     refusal_rate_pct    ← confidence_score < 0.5 — a PROXY, third copy of 0.5
     path_distribution · volume_by_day · confidence_distribution
```

---

## 8. Engineering decision — one row, written for every outcome, best-effort

**Problem.** Make every answer explainable after the fact, in a system where
"why did it say that?" is the product question — without letting the record
become a failure point.

**Decision.** A single append-only-by-grant row per request, written by the
**terminal node of every path**, with a write failure logged and swallowed.

| Alternative | Why not |
|---|---|
| **Log to stdout only** | Render truncates, retains for a limited window, and structured queries over it are not possible |
| **Write only successful queries** | Refusals and blocks are the outcomes most worth auditing — `query_path` has `'blocked'` in its CHECK |
| **Raise on audit failure** | The user already has the answer. Failing then would turn an observability outage into a product outage |
| **Reconcile responses against rows** | Would close CAVEAT-014's completeness gap. **Not built** — needs a second counter nothing currently maintains |
| **Delete `cache_hit_rate_pct`** | Deletes the record of the debt. **Marked at every layer instead** — D1 |
| **A tracing system (OTel/Jaeger)** | A span tree per request, which is more than needed, and another service against a 512 MB ceiling. The SSE trace already gives per-node visibility live (Day 39) |

**Trade-offs accepted.**

- **Best-effort writes** (CAVEAT-014): rows cannot be deleted, and a missing one
  is invisible.
- **`UPDATE` is granted** (CAVEAT-028): content immutability is convention.
- **A new connection per statement** (CAVEAT-013), here too.
- **`reranker_scores` has no backend column** — historic scores are two scales in
  one array, unrecoverably.
- **Three parallel arrays with no enforced alignment.**
- **`query_text` is stored in full, indefinitely**, with no TTL and no redaction,
  in a table with no DELETE grant — `SECURITY_MODEL.md` §9 calls this *"a
  deliberate durability property with a privacy cost."* **You could not delete a
  user's query if asked.**
- **`refusal_rate_pct` is a proxy** with the threshold duplicated (§4.8).
- **Two metrics with no producer** (`cache_hit`, `tokens_used`).

**Current validity.** The lineage design is genuinely strong and is what makes
the debugging ladder possible. The weak parts are completeness and the
permissions the document overstates.

**At 10×.** `audit_log` becomes the largest table and needs partitioning by
`created_at` plus a retention policy — **which requires a `DELETE` grant**, and
therefore requires deciding what "append-only" is going to mean. **CAVEAT-028 is
the conversation that has to happen first.**

---

## 9. Failure modes

| Symptom | Layer | First check |
|---|---|---|
| Answer served, no audit row | audit | The `Audit log write FAILED` line. It never raises |
| `INSERT` fails on `query_path` | audit | A value outside the CHECK — a fourth path without a migration |
| `dsl_generated` is NULL on a quantitative row | audit | `_safe_json` returned `None` — an unserialisable value |
| Zero rows from `audit_log` by hand | **database** | `SET app.tenant_id` first. **Zero rows, not an error** |
| `cache_hit_rate_pct` is 0.0 | **none** | Correct. No producer, by design |
| `avg_latency_ms` suspiciously low | metrics | Blocked rows are single-digit ms and are counted |
| `refusal_rate_pct` moved with no code change | metrics | The 0.5 threshold vs `COHERE_HIGH` (§4.8) |
| Empty candidate set | **network** | Qdrant URL, DNS, the insecure-connection warning |
| Low-scoring candidate set | **retrieval** | Filter, then query text. **Not the same problem** |
| Same query, two confidence tiers | **reranking** | `reranker_backend` — Cohere vs ONNX on network flap |
| A number is wrong and `sql_verified` is true | **extraction** | Not the query path. `regression_check` |
| A traceback missing its useful line | logging | Render truncates multi-line. Single-line with pgcode |
| Log timestamps 5.5 h off | logging | Render is UTC, the shell is IST |
| Every `exec` fails, "container breakout" | docker | Stale mount namespace. `echo alive`, recreate, poll `/health` |

---

## 10. Hands-on experiment

### Experiment 1 — the pre-flight, all four steps

```bash
docker compose exec -T backend python -c "import app.engines.retriever as m; print(m.__file__)"
docker compose exec -T backend printenv GEMINI_MODEL
docker compose exec -T backend printenv QDRANT_URL
docker compose exec -T backend printenv DATABASE_URL
```

**Write down which database you are about to query.** Every result below is
about that one.

### Experiment 2 — one query, then its row, reconciled

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@alpha.ledgermind.test","password":"demo1234"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "token length: ${#TOKEN}"

curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What was Titan revenue in Q1FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for k in ['request_id','path','sql_verified','confidence_tier','confidence_score',
          'latency_ms','llm_provider','llm_model','reranker_backend']:
    print(f'{k:18}', d.get(k))
print('citations         ', len(d.get('citations', [])))
"
```

Now the row:

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -x -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   SELECT query_path, confidence_score, latency_ms, tokens_used,
          llm_provider, llm_model, cache_hit,
          array_length(retrieved_chunk_ids,1) AS n_chunks,
          length(response_text) AS response_chars,
          left(sql_executed, 70) AS sql_head, created_at
   FROM audit_log ORDER BY created_at DESC LIMIT 1;"
```

**Reconcile every column against the response.** `tokens_used` will be `0`
(CAVEAT-010) and `cache_hit` `false` (CAVEAT-009). **Both are expected.**

### Experiment 3 — a block, and the honest NULLs

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"Should I buy Titan stock?"}' > /dev/null

docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   SELECT query_path, llm_provider IS NULL AS provider_null,
          llm_model IS NULL AS model_null, confidence_score, latency_ms
   FROM audit_log ORDER BY created_at DESC LIMIT 1;"
```

**`blocked`, two `t`, `0.0`, single-digit ms.** Then note the consequence: this
row counts as a refusal in `refusal_rate_pct` and drags `avg_latency_ms` down.

### Experiment 4 — the grants, on the database you are using

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -c \
  "SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privs
   FROM information_schema.table_privileges
   WHERE grantee='ledgermind_app' AND table_schema='public'
   GROUP BY table_name ORDER BY table_name;"
```

**Find `audit_log`.** Is `UPDATE` there? Is `DELETE`? Then read `sql/init.sql`
lines 126–130 and CAVEAT-028.

Then prove the enforced half:

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   DELETE FROM audit_log WHERE false;"
```

**Expect exactly this** (verified 2026-08-23):

```
ERROR:  permission denied for table audit_log
```

`WHERE false` matches nothing — **the permission check fires before the
predicate is ever evaluated**, which is what makes this safe to run and what
makes it a real demonstration rather than a lucky one.

### Experiment 5 — the metrics endpoint

```bash
curl -s http://localhost:8000/api/metrics -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool | head -40
```

**Look at `cache_hit_rate_pct` first**, and say why it is `0.0` before reading
anything else. Then compare `avg_latency_ms` with `p95_latency_ms` and account
for the gap using what Experiment 3 showed.

### Experiment 6 — network signature vs retrieval signature

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What did management say about quick commerce?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
cits = d.get('citations') or []
print('citations       :', len(cits))
print('reranker_backend:', d.get('reranker_backend'))
for c in cits[:5]:
    print(f\"   p.{c.get('page_number'):>3}  score={c.get('reranker_score')}\")
print()
print('EMPTY  -> network signature.  LOW-SCORING -> retrieval signature.')
print('And a score without its backend means nothing (Day 28).')
"
```

### Experiment 7 — the arrays, and what you cannot recover

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -x -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   SELECT array_length(retrieved_chunk_ids,1) AS ids,
          array_length(vector_scores,1)       AS vec,
          array_length(reranker_scores,1)     AS rer,
          reranker_scores[1:3]
   FROM audit_log WHERE query_path='semantic' ORDER BY created_at DESC LIMIT 3;"
```

**Three equal lengths, by construction and by nothing else.** Now ask: **which
backend produced `reranker_scores[1]`?** There is no column. **You cannot answer
it from this table** — which is exactly Day 28's point, made permanent.

### Experiment 8 — the response-length distribution nobody has seen

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   SELECT count(*) FILTER (WHERE length(response_text) BETWEEN 495 AND 505) AS near_500,
          count(*) AS total,
          round(avg(length(response_text))) AS avg_chars,
          max(length(response_text)) AS max_chars
   FROM audit_log WHERE response_text IS NOT NULL;"
```

**A cluster at ~500 is the historic truncation** (§4.4). **Measured on the local
Docker database, 2026-08-23:**

```
 rows | near_500 | max_chars
------+----------+-----------
 4301 |     1516 |      1648
```

**1516 truncated rows, unchanged** from the 1516-of-4168 figure in the code
comment — the count is frozen because the truncation stopped, while the total
kept growing. Rows above 505 were written after the fix; `max_chars = 1648` is
the first real evidence of what the distribution looks like.

**And notice what you cannot do:** separate a truncated 500-char row from a
genuinely 500-char one. **The data is gone.**

### Experiment 9 — read the debugging guide as an index

```bash
grep -n "^## " docs/engineering/DEBUGGING_GUIDE.md
```

**Twelve sections.** For each of three symptoms — *"the number is wrong"*,
*"login fails intermittently"*, *"retrieval is irrelevant"* — name the section
**before** opening it.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/audit_writer.py`, `backend/app/api/metrics.py` and
`sql/init.sql`:

1. Run one query. Find its row. **Reconcile every column** against what you
   observed, and name the node that wrote each.
2. Find `cache_hit_rate_pct`. Why does it ship? Name the three layers it is
   marked at, and the *other* two fields in the same situation.
3. What exactly is guaranteed about `audit_log`'s immutability? Check the grants
   yourself and say which half is permission and which is convention.
4. Find `_safe_json`. What does it protect against, and what does it cost when it
   fires?
5. Given *"the same query returned a different confidence tier twice"* — name the
   layer, the field, and the command, **without opening any other file.**

---

## 12. Self-check questions

**Basic**

1. Which node writes the audit row, and on which paths?
2. What are the five legal values of `query_path`?
3. What does `latency_ms` measure?
4. Why is `cache_hit_rate_pct` always 0.0?
5. What does `refusal_rate_pct` actually count?

**Code**

6. Why does `audit_writer` swallow its exception?
7. What does `_safe_json` do, and why is it needed?
8. Why `SET LOCAL` rather than `SET`?
9. Why is `latency_ms` written back into state?
10. Why does `query_path` fall back to `'blocked'`/`'unknown'` rather than NULL?

**Why**

11. Why is `response_text` bound in full now, and what did the old name conceal?
12. Why no warn-above-N on response length?
13. Why is `NULL` in `llm_provider` a *record* rather than missing data — and
    which downstream consumer depends on that?
14. Why is a metric with no producer kept rather than deleted?
15. Why is an empty candidate set a different problem from a low-scoring one?

**Debugging**

16. *"The answer is hallucinated."* Name the layer, the field, the command.
17. *"The same query gives two different tiers."* Same.
18. *"Login fails intermittently."* Same.

**System design**

19. Close CAVEAT-014: make audit completeness verifiable. What do you add, where,
    and what does it cost?
20. `audit_log` is now the largest table and needs a retention policy. Design it,
    and say what has to be decided first.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Marking scheme: `tenant_id`/`user_id` from the JWT via `make_initial_state`;
   `query_text` from the request; `query_path` from `router_node` (or the
   `blocked`/`unknown` fallback); the three arrays from `retriever`;
   `dsl_generated`/`sql_executed` from `quant_engine`; `confidence_score` from
   `confidence_node`; `response_text` from `response_generator`;
   `llm_provider`/`llm_model` from `llm/client`'s precedence rules;
   `latency_ms` from **this node**; `cache_hit` from **nothing**; `created_at`
   from `NOW()`.
2. It ships because **deleting it would delete the record of the debt** — the
   blueprint specified a cache, it was not built, and a removed column leaves no
   trace that the decision was made. **Three layers:** the SQL comment
   ("structurally always 0.0 … Do NOT surface this in a dashboard"),
   `CAVEAT-009`, and `lib/api.ts`'s `// Always false … Do not render`. **The
   other two:** `tokens_used` (CAVEAT-010) and the restatement confidence penalty
   (CAVEAT-008).
3. **Guaranteed by permission:** rows cannot be **deleted** — no `DELETE` grant,
   on either database, verifiable in one query. **Convention only:** rows cannot
   be **modified** — `UPDATE` *is* granted (`init.sql:126` includes `audit_log`),
   and nothing in `app/` or `scripts/` uses it. **CAVEAT-028**, and note that
   `SECURITY_MODEL.md:151` claims the stronger version.
4. It protects the INSERT from values psycopg2's JSONB adapter cannot serialise
   — `Decimal` from Postgres `NUMERIC`, plus `datetime` and `UUID` — by
   round-tripping through `json.dumps(default=str)`. **When it fires it returns
   `None`**, so the cost is losing `dsl_generated` for that row rather than losing
   the row.
5. **Layer: reranking.** **Field: `reranker_backend`.** **Command:** read it from
   the same response as the score —
   `curl … | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('reranker_backend'))"`
   (admin only). Cohere returns `[0,1]`, local ONNX returns logits `[-12,+2]`, the
   thresholds are split accordingly, and the fallback fires on network flap — so
   the *tier* is correct on either backend while the *score* is on a different
   scale.

### §12 — Basic

1. `audit_writer_node`, the **terminal node of every path** — including blocked
   and refused, which reach it on their own edges.
2. `'semantic'`, `'quantitative'`, `'cross'`, `'blocked'`, `'unknown'` — a CHECK
   constraint.
3. `time.time() - state["start_time"]`, in ms: the whole pipeline up to this
   node. **Not** the audit INSERT, and not network time to the browser.
4. Because `cache_hit` has **no producer**: `make_initial_state` sets it `False`
   and nothing writes it again. The semantic cache (blueprint §15) was never
   built; Redis is the Celery broker only.
5. The share of rows with `confidence_score < 0.5` — a **proxy** for refusal.
   It includes every Prompt Shield block (score 0.0) and any merely-mediocre
   answer, and it never reads `error`.

### §12 — Code

6. Because **the user already has the answer.** Raising would turn an
   observability failure into a product failure. Blueprint §17 graceful
   degradation; the cost is CAVEAT-014, best-effort writes.
7. Round-trips a value through `json.dumps(default=str)` so psycopg2's JSONB
   adapter can take it — `Decimal` from `NUMERIC` is the specific case. Returns
   `None` on failure rather than raising.
8. Because `SET LOCAL` is **transaction-scoped**: the GUC dies with the
   transaction and cannot leak into a pooled connection's next user. A plain
   `SET` is session-scoped and would.
9. So `role_filtered_response` can serve it to an admin. **One computation, two
   consumers** — the row and the response.
10. Because `query_path` carries a **CHECK constraint**, and both fallbacks are
    in its list. A NULL would be legal but uninformative; a value outside the list
    would fail the INSERT and **lose the whole row**.

### §12 — Why

11. Because it was silently storing a **500-character prefix** under a variable
    named `response_summary` — and *that name is why the truncation read as
    intentional for as long as it did.* **1516 of 4168 rows (36.4 %) were unmarked
    prefixes.** The column is unbounded `TEXT`, and `query_text` and `sql_executed`
    on the same tuple always bound whole, so **the database would have accepted the
    full text all along.**
12. Because *"a threshold warning is a cap that has not fired yet"* — and the
    distribution has **never been observed**, precisely because the old truncation
    destroyed it. Measure first; the constant comes after. KU-006.
13. Because a blocked query makes **no LLM call**, and the synthesis floor clears
    attribution when every provider fails — so "no model served this" is the true
    record, and inheriting an earlier value would make a block look like a
    Gemini-served answer. **The consumer is `eval_runner._integrity_counters`**,
    which excludes blocked rows from the provider gate *because* `None` here is
    correct.
14. Because deleting it deletes the **evidence of unfinished work**. The mitigation
    is marking it at every layer — SQL comment, caveat, TypeScript comment — and
    not rendering it.
15. **Empty** means retrieval never happened or reached nothing: Qdrant
    unreachable, wrong URL, empty collection, over-constrained filter — a
    **network/config** problem. **Low-scoring** means it happened and the corpus
    does not contain the answer — a **retrieval** problem. **No remedy in common**,
    and both present as "the answer is wrong".

### §12 — Debugging

16. **Layer: synthesis — but only after ruling out retrieval.** **Fields:**
    `citations` (how many?), `reranker_score` **with** `reranker_backend`,
    then `response_text`. **Commands:** run the query with an admin token and read
    the citations; if there are none, that is network-or-retrieval, not the model;
    if there are citations and they are relevant, read `response_text` **in full**
    — never `eval_runner`'s 200-char preview, which truncates before the phrase in
    question (`CLAUDE.md` §5). **And if a number is wrong while `sql_verified` is
    true, it is not synthesis at all — it is extraction**, and the tool is
    `regression_check`.
17. **Layer: reranking.** **Field: `reranker_backend`.** **Command:** read it from
    the same response as the score. Cohere `[0,1]` vs local ONNX logits, with the
    fallback firing on WSL2 network flap — the same query genuinely scored by two
    systems.
18. **Layer: auth — and the word "intermittently" is the whole diagnosis.**
    **Fields:** the JWT's `exp` (2 h) and the client's `expiresAt`. **Commands:**
    decode the stored token's payload and compare `exp * 1000` with `expiresAt`
    (Day 41); check whether failures cluster around the 2-hour mark. **Then the
    less obvious one:** `authenticate_user` runs with `tenant_id=None` relying on
    the `auth_bootstrap_lookup` policy, so a connection that carries a **leaked
    `app.tenant_id`** from a previous request fails that policy and the login
    fails — which is intermittent by construction. Check whether `SET LOCAL`
    (not `SET`) is used everywhere.

### §12 — System design

19. **What to add.** A counter of *responses served* to compare against *rows
    written*. Simplest correct version: increment a Redis key
    (`audit:served:<date>`) in `api/query.py` immediately after the graph returns —
    **before** `audit_writer` would have run — and a second key
    (`audit:written:<date>`) inside `audit_writer`'s success branch. A daily
    comparison job reports the delta.
    **Where.** Redis is already deployed as the Celery broker, so no new service.
    **What it costs.** Two Redis round-trips per request on the request path
    (small, and already the pattern for the health check). **And a real
    subtlety:** if Redis is down, the counter is wrong in the same direction as the
    thing it is measuring — so it detects *database* failures, not general ones.
    **Honest alternative:** log a single `AUDIT_INTENT request_id=…` line before
    the INSERT and reconcile logs against rows, which needs no new dependency and
    is only as good as the log retention window.
    **What it does not fix:** completeness of the *content*. A row that was written
    and later `UPDATE`d (CAVEAT-028) is still counted.
20. **What has to be decided first, and it is not technical: what "append-only"
    means.** Retention requires `DELETE` — or partition detachment, which requires
    DDL that `ledgermind_app` deliberately does not have. So the decision is:
    does a *separate, privileged* role perform retention while `ledgermind_app`
    keeps no `DELETE` (preserving the guarantee for the application), or does the
    guarantee weaken? **CAVEAT-028 is that conversation**, and it must precede the
    design.
    **The design, assuming a separate role.** `PARTITION BY RANGE (created_at)`,
    monthly. A migration creates the partitioned parent and the initial
    partitions; the app role keeps `INSERT`/`SELECT` and never sees DDL. Retention
    is `ALTER TABLE … DETACH PARTITION` followed by an archive-then-drop, run by a
    maintenance role from a scheduled job.
    **What else changes.** `api/metrics.py`'s `volume_by_day` and the two latency
    aggregates all scan the whole table today; partition pruning helps only if the
    queries carry a `created_at` predicate, and **none of them does** — so they
    must gain a window (`WHERE created_at >= now() - interval '90 days'`), which
    **changes what the dashboard numbers mean** and needs saying out loud rather
    than shipping quietly.
    **And the privacy angle finally gets addressed**: `SECURITY_MODEL.md` §9 notes
    `query_text` is retained indefinitely with no TTL and no redaction. **A
    retention policy is the first thing that would give a "delete my query"
    request an answer** — which is arguably the stronger reason to do it than
    table size.

---

## 14. MUST REMEMBER

```text
- ONE audit row per request, written by the TERMINAL node of EVERY path —
  including blocks and refusals
- query_path has a CHECK constraint: semantic · quantitative · cross ·
  blocked · unknown. A fourth path needs a MIGRATION
- Audit failure LOGS AND SWALLOWS. The user already has the answer
  (CAVEAT-014: append-only, best-effort)
- APPEND-ONLY, EXACTLY: no DELETE grant on either database (ENFORCED);
  UPDATE IS granted on both (CONVENTION). CAVEAT-028
- The two databases DISAGREE about DELETE on documents/financials, and no
  file in the repo grants it
- NULL in llm_provider is a RECORD, not missing data — and eval_runner's
  provider gate depends on it
- response_text is bound IN FULL. It used to be a 500-char prefix under a
  variable named for a summary: 1516/4168 rows (36.4%), UNMARKED
- "A threshold warning is a cap that has not fired yet" — no warn-above-N,
  because the distribution has never been observed
- cache_hit_rate_pct is structurally 0.0 and SHIPS ANYWAY (D1, CAVEAT-009).
  Marked in SQL, in CAVEATS, and in lib/api.ts. NOT deleted, NOT rendered
- tokens_used has no producer either (CAVEAT-010)
- refusal_rate_pct is a PROXY: confidence_score < 0.5, which counts every
  block, and 0.5 is a THIRD copy of COHERE_HIGH
- reranker_scores has NO backend column — historic scores are unrecoverable
- EMPTY candidate set = NETWORK. LOW-SCORING = RETRIEVAL. Establish which
  BEFORE theorising
- Single-line logs with pgcode. Render truncates multi-line, and is UTC
  while the shell is IST
- SET LOCAL, not SET — transaction-scoped, cannot leak into a pooled
  connection
- "possible container breakout detected" is a STALE MOUNT NAMESPACE, not a
  security event. Confirm with `exec -T backend echo alive`
```

## 15. MUST UNDERSTAND

```text
- Why a lineage record differs from logging: columns a query can aggregate,
  written for every outcome, versus text a human greps
- Why the layer ladder puts the LLM LAST, and why the instinct to blame the
  model costs the most time
- Why a permission claim is checkable in ONE QUERY, and why this one had not
  been checked
- Why a variable NAME can conceal a defect for months, and why the fix is
  measured (36.4%) rather than described
- Why refusing to add a warn-above-N is the same discipline as refusing to
  tune a constant without a measurement
- Why deleting a metric with no producer destroys the record of the debt, and
  why marking it at every layer is the alternative
- Why LIVE CODE WITH NO INPUT is more dangerous than code with no caller: it
  returns a number, and a number looks like a measurement
- Why a duplicated threshold in SQL is the _compute_derived_totals failure
  class again, and why only the FREEZE is protecting it today
```

---

## 16. This connects to

```text
Day 19 — provider attribution by precedence
Day 28 — two incompatible reranker scales
Day 42 — blocked queries make no LLM call
Day 43 — the offline record, and the three gates
   ↓
Day 44 — observability, and debugging by layer
   ↓
Day 45 — deployment, and the ceiling
```

Forward references:

- Render's 512 MB, and why `avg_latency_ms` is what it is → **Day 45**
- Redis as broker-only, the reason `cache_hit` has no producer → **Day 45**
- The full backwards-reasoning drill, from symptom to layer → **Day 47 Part 1**

Records opened today:

- **CAVEAT-028** — `audit_log` is append-only against `DELETE` only, and the two
  databases disagree about `DELETE` elsewhere. **Measured on both.**
- §4.8's duplicated `0.5` is recorded here as an **observation with no victim
  today**, protected by `ED-025`'s freeze rather than by design.
