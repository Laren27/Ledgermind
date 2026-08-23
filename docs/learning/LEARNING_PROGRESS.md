# LedgerMind — Learning Progress

The single progress file for the 47-day course in
[`LEDGERMIND_MASTER_COURSE.md`](LEDGERMIND_MASTER_COURSE.md). There is no second
tracker; this one replaced `LEARNING_CHECKLIST.md` on 2026-08-23.

---

## How to use this file

Two things are tracked here, and they are different:

- **Part 1 — the day log.** Did I do the day? One row per day, 47 rows.
- **Part 2 — the concept tracker.** Do I actually *have* the concept? One block
  per concept, independent of which day introduced it.

A day can be finished while its concepts are still `LEARNING`. That is normal
and is the reason these are two separate tables rather than one.

### The seven dimensions

Six are checkboxes. The seventh is prose, and it is the most useful one.

```text
[ ] studied    I have read the day and the code it points at
[ ] explain    I can explain it out loud, without the file open
[ ] trace      I can find it in the repo and follow the data through it
[ ] modify     I could change it safely — I know what breaks, what test
               catches it, and what measurement would prove me right
[ ] debug      Given a symptom, I can decide whether this layer is the cause
[ ] revised    I have come back to it at least once, cold, after the fact

Still unclear:  ← prose. Write the actual question. "Nothing" is a valid answer
                  and is worth writing, because it dates the claim.
```

**`modify` is the one that matters.** Everything before it can be produced by
having read carefully. `modify` cannot.

**`debug` is not implied by `explain`.** Explaining reranking is knowing what a
cross-encoder does. Debugging it is being handed "the same query returned a
different confidence tier twice" and knowing to check `reranker_backend` first.

### Status vocabulary

One status per concept, derived from the boxes — do not set it by feel:

| Status | Rule |
|---|---|
| `NOT STARTED` | no boxes ticked |
| `LEARNING` | `studied` only |
| `UNDERSTOOD` | `studied` + one of `explain`/`trace` |
| `CAN EXPLAIN` | `studied` + `explain` + `trace` |
| `CAN MODIFY` | the above + `modify` |
| `CAN DEBUG` | the above + `debug` |
| `MASTERED` | all six, **and** `Still unclear:` reads `nothing` |

**Nothing is marked `MASTERED` automatically, ever.** Not by me, not by a day
being finished. You set that one, and only after the cold revision pass in
Part 4 — not on the day you first read it.

---

# Part 1 — Day log

`Status`: one of `NOT STARTED` · `IN PROGRESS` · `DONE`
`Cold recall`: did the Day-N+3 revision question come back without the file?

| Day | Topic | Phase | Wt | Status | Date | Cold recall | Notes |
|---:|---|---|:-:|---|---|---|---|
| 1 | Processes, ports, env vars, containers | 0 | M | NOT STARTED | | | |
| 2 | Reading a repository: git as evidence | 0 | L | NOT STARTED | | | |
| 3 | What LedgerMind is: three engines, one dictionary | 0 | H | NOT STARTED | | | |
| 4 | HTTP → API → endpoint → FastAPI | 1 | M | NOT STARTED | | | |
| 5 | The contract: JSON, Pydantic, status codes, CORS | 1 | M | NOT STARTED | | | |
| 6 | Two transports, one pipeline (blocking vs SSE) | 1 | H | NOT STARTED | | | |
| 7 | Authentication: hashing, bcrypt, and not-passlib | 2 | M | NOT STARTED | | | |
| 8 | JWT and dependency injection | 2 | H | NOT STARTED | | | |
| 9 | Authorization: roles, field filtering, failing closed | 2 | M | NOT STARTED | | | |
| 10 | Three type systems, on purpose | 3 | M | NOT STARTED | | | |
| 11 | Context managers, generators, async, queues | 3 | H | NOT STARTED | | | |
| 12 | Module-level state: lazy singletons, import order | 3 | M | NOT STARTED | | | |
| 13 | Relational modelling: the schema | 4 | M | NOT STARTED | | | |
| 14 | Transactions, `SET LOCAL`, Row-Level Security | 4 | H | NOT STARTED | | | |
| 15 | Indexes, row locking, and the restatement model | 4 | H | NOT STARTED | | | |
| 16 | Migrations without a framework; two databases | 4 | M | NOT STARTED | | | |
| 17 | LLMs, tokens, context, hallucination | 5 | M | NOT STARTED | | | |
| 18 | Prompting, structured output, schema-as-prompt | 5 | H | NOT STARTED | | | |
| 19 | The shared LLM client: timeouts, failover, attribution | 5 | H | NOT STARTED | | | |
| 20 | Embeddings, vectors, cosine similarity | 6 | M | NOT STARTED | | | |
| 21 | Vector databases, HNSW, named vectors | 6 | H | NOT STARTED | | | |
| 22 | PDF → PageBlock: parsing, tables, OCR damage | 6 | H | NOT STARTED | | | |
| 23 | Classification by three-signal intersection | 6 | M | NOT STARTED | | | |
| 24 | Chunking and embedding | 6 | H | NOT STARTED | | | |
| 25 | Dense retrieval and query/document asymmetry | 7 | M | NOT STARTED | | | |
| 26 | Sparse retrieval: BM25, TF, IDF | 7 | M | NOT STARTED | | | |
| 27 | Hybrid retrieval, RRF, and where the filter goes | 7 | H | NOT STARTED | | | |
| 28 | Reranking, and two incompatible score scales | 7 | H | NOT STARTED | | | |
| 29 | Dedup, confidence scoring, CRAG | 7 | H | NOT STARTED | | | |
| 30 | The semantic path, whole | 8 | H | NOT STARTED | | | |
| 31 | The metric registry, and how a number becomes a row | 9 | H | NOT STARTED | | | |
| 32 | The DSL: schema, validation, repair loop | 9 | H | NOT STARTED | | | |
| 33 | SQL compilation and Python arithmetic | 9 | H | NOT STARTED | | | |
| 34 | The three guards, and verification | 9 | H | NOT STARTED | | | |
| 35 | LangGraph: nodes, conditional edges, the singleton | 10 | M | NOT STARTED | | | |
| 36 | The router: classification, entity resolution, refusal | 10 | H | NOT STARTED | | | |
| 37 | Cross-examination and contradiction detection | 10 | H | NOT STARTED | | | |
| 38 | HTML → TS → React → Next, against this app | 11 | M | NOT STARTED | | | |
| 39 | State, effects, and consuming SSE | 11 | H | NOT STARTED | | | |
| 40 | The zero-hallucination boundary — and dead code | 11 | H | NOT STARTED | | | |
| 41 | Auth state, upload flow, admin views | 11 | M | NOT STARTED | | | |
| 42 | The Prompt Shield and the security model | 12 | M | NOT STARTED | | | |
| 43 | Evaluation: golden dataset, gates, quota | 12 | H | NOT STARTED | | | |
| 44 | Observability, and debugging by layer | 12 | H | NOT STARTED | | | |
| 45 | Docker, deployment, and what breaks at 10× | 12 | M | NOT STARTED | | | |
| 46 | The master trace, from memory | 13 | H | NOT STARTED | | | |
| 47 | Failure drills, roads not taken, final viva | 13 | H | NOT STARTED | | | |

---

# Part 2 — Concept tracker

Grouped by cluster, not by day, because concepts are learnable in clusters and
unlearnable in dictionary order. `Day` says where it is introduced; several are
revisited later and the later day is listed too.

---

## A. Architecture

### The tri-engine model (semantic / quantitative / cross)
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 3, 35
**Locate:** `app/engines/graph.py`, `router.route_after_router`
**Check yourself:** why does a refusal skip the confidence node?
**Still unclear:**

### `QueryState` as a single shared mutable dict
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 3, 10
**Locate:** `app/engines/state.py`
**Check yourself:** what breaks if two nodes write the same field, and where is
that already true? (`confidence_tier`, written by three nodes and finally by
`_reconcile_cross`.)
**Still unclear:**

### LangGraph `StateGraph` + conditional edges
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 35
**Check yourself:** how would you add a fourth path, and which files change?
(Answer: `state.py` Literal, `graph.py`, `router.py` prompt + `route_after_router`,
`response_generator`, `page.tsx :: composeDocumentBody`, and the `audit_log`
CHECK constraint — that last one needs a migration.)
**Still unclear:**

### Streaming vs blocking transport sharing one pipeline
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 6
**Check yourself:** why must the graph task never be cancelled on client
disconnect?
**Still unclear:**

### Separation of ingestion (offline) from query (online)
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 3, 24, 41
**Check yourself:** what OOM-killed the web service, and what did that force?
**Still unclear:**

---

## B. Web, API, Python

### HTTP: method, URL, headers, body, status code
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 4
**Still unclear:**

### FastAPI dependency injection and `Depends`
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 8
**Check yourself:** why is auth a dependency and not middleware?
**Still unclear:**

### Pydantic models as an API contract
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 5
**Check yourself:** what does a 422 body actually tell you?
**Still unclear:**

### `TypedDict` vs `dataclass` vs Pydantic — and why this repo uses all three
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 10
**Check yourself:** `QueryState` is a TypedDict, `FinancialRecord` is a dataclass,
`RouterResponse` is a Pydantic model. Can you justify each choice?
**Still unclear:**

### Lazy singletons for expensive models
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 12
**Locate:** `retriever._get_dense_model` and friends
**Check yourself:** why lazy rather than at import? (Docker startup, and the
Celery worker imports modules it may never call.)
**Still unclear:**

### Generators and `async` streaming
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 11
**Check yourself:** why is the SSE queue unbounded on purpose?
**Still unclear:**

### Context managers (`@contextmanager`, `with conn:`)
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 11
**Check yourself:** what does `with conn:` do on exit — and what does it **not**
do? (Commits/rolls back; does **not** close.)
**Still unclear:**

### Import-time side effects and logging configuration order
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 12
**Check yourself:** why do `main.py` and `worker.py` both configure logging, and
what disappears if you move the call below the `app.*` imports?
**Still unclear:**

---

## C. Databases

### Relational modelling: the LedgerMind schema
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 13
**Check yourself:** why is `value` `NUMERIC` and not `FLOAT`?
**Still unclear:**

### Row-Level Security, `FORCE`, and `SET LOCAL`
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 14, 42
**Check yourself:** what does a query return when you forget the GUC, and why is
that answer dangerous?
**Still unclear:**

### Why the RLS policy uses `CASE`, not `AND`
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 14
**Check yourself:** `AND` is not a short-circuit operator in SQL. What goes wrong?
**Still unclear:**

### Partial unique indexes
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 15
**Locate:** `uq_financials_latest`
**Still unclear:**

### `SELECT … FOR UPDATE` and the race it prevents
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 15
**Still unclear:**

### `IS NOT DISTINCT FROM` and NULL semantics
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 15
**Still unclear:**

### `ON CONFLICT DO NOTHING` and idempotent inserts
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 15
**Still unclear:**

### Transaction boundaries in this codebase
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 14, 45
**Check yourself:** how many connections does one `growth_comparison` query open?
(Four, plus audit. See CAVEAT-013.)
**Still unclear:**

### Qdrant collections, named vectors, payload indexes
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 21
**Check yourself:** what happens to a filtered query if the payload index is
missing?
**Still unclear:**

### Schema migration discipline without a framework
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 16
**Check yourself:** why can this repo's app user not apply its own migrations?
**Still unclear:**

### Two divergent databases, and which one a measurement came from
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 16
**Locate:** `docker-compose.yml:51`, audit F11, CAVEAT-015
**Still unclear:**

---

## D. LLMs

### What an LLM is: tokens, context window, next-token prediction
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 17
**Check yourself:** there is no database inside the model. What follows from that?
**Still unclear:**

### Parametric vs non-parametric memory, and why RAG exists
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 17, 20
**Still unclear:**

### Structured output, and why it does not imply correctness
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 18
**Check yourself:** Gemini guarantees the shape; Groq guarantees only valid JSON.
What does the client do about that difference?
**Still unclear:**

### The response schema is part of the prompt
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 18
**Locate:** `IMPLEMENTATION_DELTAS.md` §D
**Check yourself:** "no prompt block" is not "invisible to the model." Why not?
**Still unclear:**

### Prompt-order effects — earlier, concrete rules beat appended ones
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 18
**Check yourself:** name the three occasions this cost the project a fix.
**Still unclear:**

### Timeouts as a precondition for fallback
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 19
**Check yourself:** why can a fallback keyed on exceptions never fire against a
hang?
**Still unclear:**

### Provider failover, and why the trigger is narrow
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 19
**Check yourself:** why are 401 and 403 deliberately excluded?
**Still unclear:**

### Transport-class vs provider-class failure
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 19
**Locate:** `llm/client.py:_marker_class`
**Check yourself:** why is transport checked first when a message carries both?
**Still unclear:**

### Provider attribution by precedence, not last-writer-wins
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 19
**Locate:** `state.record_llm_call`, `_PROVIDER_TAINT`
**Still unclear:**

### Rate limits: RPM vs daily, and why they need opposite handling
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 19, 43
**Check yourself:** the error body says "PerDay" in both cases. What tells them
apart?
**Still unclear:**

---

## E. RAG and retrieval

### Dense embeddings and cosine similarity
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 20
**Check yourself:** why must the same model embed both documents and queries?
**Still unclear:**

### Vector databases and HNSW
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 21
**Check yourself:** what is HNSW trading away, and why is that trade acceptable?
**Still unclear:**

### PDF parsing: tables before text, positional extraction, OCR damage
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 22
**Check yourself:** why is a financial table not captioned into prose here?
**Still unclear:**

### Classification by three-signal intersection
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 23
**Check yourself:** why is content alone insufficient to identify a financial
statement?
**Still unclear:**

### Chunking strategy and overlap trade-offs
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 24
**Check yourself:** why is `OVERLAP_TOKENS = 150` frozen, and what did raising it
from 50 fix?
**Still unclear:**

### Deterministic chunk IDs and idempotent re-ingestion
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 24
**Still unclear:**

### Speaker-turn chunking and attribution
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 24, 37
**Check yourself:** why does `speaker_role` matter to contradiction detection?
**Still unclear:**

### Dense retrieval, and where it fails
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 25
**Still unclear:**

### BM25 and sparse vectors
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 26
**Check yourself:** explain IDF's intuition without writing the formula.
**Still unclear:**

### Hybrid retrieval and Reciprocal Rank Fusion
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 27
**Check yourself:** why fuse by rank instead of by score?
**Still unclear:**

### Filter placement — inside each prefetch leg, not at fusion
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 27
**Check yourself:** what pollutes the ranking if you filter at fusion instead?
**Still unclear:**

### A filter that is silently *dropped* (audit F2)
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 27, 36
**Locate:** `retriever._build_filter`, the `UNFILTERED WHOLE-TENANT SEARCH` warning
**Check yourself:** the textbook warns about filters that are too strict. This is
the opposite failure. Why is it worse?
**Still unclear:**

### Cross-encoder reranking, and **incompatible score scales**
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 28
*This is the single highest-value item on this list.* If you can explain why a
`reranker_score` without its `reranker_backend` is meaningless, you understand
how this project reasons about evidence.
**Check yourself:** same query, two runs, two different confidence tiers. Cause?
**Still unclear:**

### Near-duplicate suppression
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 29
**Check yourself:** why is the denominator the *smaller* chunk?
**Still unclear:**

### Confidence scoring, and what it does not measure
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 29, 30
**Check yourself:** `confidence_tier` scores *retrieval*, before any answer text
exists. What does that leave uncovered, and what covers it?
**Still unclear:**

### CRAG as a filter ladder
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 29
**Check yourself:** what is `crag_count` actually counting? (Rung index, not
retrievals performed.)
**Still unclear:**

### Prompt construction, synthesis, and the citation contract
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 30
**Check yourself:** why was the 0.05 citation floor removed when the measurement
behind it was correct?
**Still unclear:**

---

## F. The quantitative path

### Structured vs unstructured, and which questions belong where
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 31
**Still unclear:**

### Single source of truth: one metric registry
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 31
**Check yourself:** name the three registries this replaced and the bug each
duplication caused.
**Still unclear:**

### Metric normalisation and alias collisions
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 31
**Check yourself:** what is the coverage floor of 0.5 protecting against, and what
does a `[METRIC TIE]` log line mean?
**Still unclear:**

### Accounting identities as a validation gate
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 31
**Locate:** `validate_financial_identities`, and the `>5%` hard failure gate
**Check yourself:** why is `DERIVED_OVERWRITE_MAX_DIVERGENCE` deliberately
allowed to **produce** identity failures?
**Still unclear:**

### Units and scale (crore / lakh / million)
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 31
**Locate:** audit F3, CAVEAT-005
**Check yourself:** why can the extractor not simply normalise everything to
crore? (`clean_financial_number`'s decimal-as-comma rule.)
**Still unclear:**

### Consolidated vs standalone
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 13, 23
**Still unclear:**

### Indian fiscal years and quarters
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 13
**Still unclear:**

### Restatement vs parser correction
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 15
**Check yourself:** why is a fixed parser re-reading a fixed document *not* a
restatement?
**Still unclear:**

### The DSL: what it can and cannot express
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 32
**Locate:** CAVEAT-004
**Check yourself:** the schema requires `metric` and `fiscal_year`. What does that
force the model to do when the query names neither?
**Still unclear:**

### The self-healing repair loop, and what it must not retry
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 32
**Check yourself:** why does `LLMUnavailable` break the loop instead of retrying?
**Still unclear:**

### SQL compilation and Python-side arithmetic
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 33
**Check yourself:** why does `growth_comparison` need four queries?
**Still unclear:**

### Parameterised queries
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 33, 42
**Still unclear:**

### The three pre-LLM guards: refusing beats substituting
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 34
**Check yourself:** why do the guards run against the *raw query* rather than the
generated DSL?
**Still unclear:**

### Verification: what `sql_verified = True` guarantees, and what it does not
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 34
**Check yourself:** what is `period_assumed` disclosing, and why is hiding it
worse than showing it?
**Still unclear:**

---

## G. Routing and cross-examination

### Entity resolution, and a field that overloads `null`
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 36
**Locate:** audit F2 and F14
**Check yourself:** "not in corpus" and "more than one" were the same value. Why
is that one defect class and not two?
**Still unclear:**

### Refusal as a first-class outcome with its own plumbing
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 34, 36
**Check yourself:** a refusal is written, and then still answered. What was
missing? (`route_after_router` read `path`, never `error`.)
**Still unclear:**

### Cross-examination: why quant runs before semantic
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 37
**Check yourself:** two earlier fixes tried to suppress a true sentence and
failed. What did the working fix do instead?
**Still unclear:**

### Contradiction detection, and why it is deliberately strict
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 37
**Check yourself:** why is a false contradiction worse than a missed one, *for
this system specifically*?
**Still unclear:**

---

## H. Frontend

### React components, props, state, hooks — against this app
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 38
**Still unclear:**

### React state lifting and why `page.tsx` holds everything
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 38, 39
**Still unclear:**

### Consuming SSE with `fetch` + `ReadableStream`
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 39
**Check yourself:** why not `EventSource`? Why does the frame buffer keep a
partial frame instead of discarding it?
**Still unclear:**

### Graceful degradation in the client (stream → blocking fallback)
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 39
**Check yourself:** exactly one failure class is retried. Which, and what did
retrying all of them cost?
**Still unclear:**

### `composeDocumentBody()` as the single path-aware boundary
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 40
**Still unclear:**

### The Zero UI-Hallucination Mandate
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 40
**Check yourself:** find a place where the UI **omits** rather than substitutes,
and say what the substitute would have implied.
**Still unclear:**

### Dead code: identifying it, and deciding whether removal is safe
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 40
**Locate:** `components/AnswerCard.tsx`, `ConfidenceBadge.tsx`, `CorpusPanel.tsx`
**Check yourself:** what does git history tell you here, and what does it *not*?
**Still unclear:**

---

## I. Security

### JWT: signing, claims, expiry, what it does **not** protect
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 8
**Check yourself:** signed, not encrypted. What follows for what you put in it?
**Still unclear:**

### Password hashing: salts, cost factor, and why not passlib here
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 7
**Still unclear:**

### RBAC at two levels, failing closed
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 9
**Check yourself:** what does an unrecognised role receive, and why that way round?
**Still unclear:**

### Tenant isolation, defence in depth — **and its current hole**
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 14, 42
**Check yourself:** trace `tenant_id` from the HTTP request to the RLS policy and
name the point where it stops being trustworthy. (CAVEAT-001.)
**Still unclear:**

### Prompt injection: direct vs indirect
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 42
**Check yourself:** which one is undefended here, and why does the architecture
still bound the damage?
**Still unclear:**

### Matching request *structure*, not keywords
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 42
**Check yourself:** "should I buy Zomato?" blocks; "what did Zomato buy?" passes.
What is the pattern actually matching?
**Still unclear:**

### Append-only audit as a **grant**, not a convention
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 44
**Still unclear:**

---

## J. Testing, evaluation, operations

### Pure-function unit tests with a hard network guard
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 43
**Check yourself:** why does patching `socket` alone fail to block psycopg2?
**Still unclear:**

### Tests that assert known defects as current behaviour
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 43
**Check yourself:** what do you do when one of them starts failing?
**Still unclear:**

### Golden datasets and keyword assertion discipline
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 43
**Check yourself:** name three things you must never assert on. (Optional acronym
glosses; verb inflection; short strings a wrong answer would also satisfy.)
**Still unclear:**

### Regression checks vs evals — cost, and what each proves
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 43
**Check yourself:** `regression_check` passed 4/4 after every OCR fix while 28
stored figures were stale. How?
**Still unclear:**

### Quota signatures vs real defects
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 43
**Check yourself:** failure at a fixed position with everything before it passing
— what is that?
**Still unclear:**

### Reading a result file before trusting its number
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 43
**Check yourself:** which five lines do you read before the score?
**Still unclear:**

### Audit lineage: one row, every decision
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 44
**Still unclear:**

### Debugging by layer, backwards from a symptom
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 44
**Check yourself:** an empty candidate set and a low-scoring one are different
signatures. Of what?
**Still unclear:**

### Docker Compose: bind mounts, `env_file` vs `environment`, `--force-recreate`
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 1, 45
**Still unclear:**

### Health checks, readiness vs liveness
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 4, 45
**Still unclear:**

### Deploying under a hard RAM ceiling
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 45
**Check yourself:** name three design decisions caused by 512 MB.
(Cohere as primary reranker; offline ingestion; `BATCH_SIZE = 8`.)
**Still unclear:**

### Celery: brokers, time limits, and what Redis is *not* used for here
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 45
**Still unclear:**

### Git history as evidence
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 2, 40
**Check yourself:** what can `git log` establish, and what can it only suggest?
**Still unclear:**

---

## K. Transferable system design

### Fail closed vs fail open
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 9
**Still unclear:**

### Making degradation **visible**, not just survivable
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 19, 44
**Still unclear:**

### Determinism over agency
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 47
**Still unclear:**

### Writing the measurement next to the constant
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 29, 47
**Check yourself:** which constants in this repo carry their measurement, and what
would be lost if the comment were deleted?
**Still unclear:**

### A check satisfied by absence is not a check
```text
[ ] studied  [ ] explain  [ ] trace  [ ] modify  [ ] debug  [ ] revised
```
**Status:** NOT STARTED · **Day:** 43, 44
**Still unclear:**

---

# Part 3 — Open confusions

The running list. Move an item here the moment you write something in a
`Still unclear:` field, so it is visible without re-reading the whole file.
Delete a line only when you can answer it **without opening the repo**.

| Opened | Concept | The actual question | Day raised | Resolved | How |
|---|---|---|---|---|---|
| | | | | | |

---

# Part 4 — Revision schedule

Spaced repetition, not re-reading. On each revision day, answer the
`Check yourself` prompt **with the file closed**, then tick `revised`.

| Revise on | Cover material from |
|---|---|
| Day 4 | Day 1 |
| Day 7 | Days 2–3 |
| Day 12 | Days 4–6 (the whole request path) |
| Day 17 | Days 7–9 (identity) |
| Day 20 | Days 10–12 (Python) |
| Day 24 | Days 13–16 (Postgres) |
| Day 28 | Days 17–19 (LLM) |
| Day 31 | Days 20–24 (ingestion) |
| Day 35 | Days 25–30 (**retrieval — the heaviest revision**) |
| Day 38 | Days 31–34 (quantitative) |
| Day 42 | Days 35–37 (orchestration) |
| Day 45 | Days 38–41 (frontend) |
| Day 46 | Everything — this is the master trace |
| Day 47 | Whatever Day 46 exposed as thin |

---

# Part 5 — The final exam

You have finished when you can answer these **without opening the repository**:

1. What are the three query paths, and how is one chosen?
2. Why does the LLM never write SQL, and what does it write instead?
3. What are the two retrieval signals, and how are they combined?
4. Why are there two sets of confidence thresholds?
5. What does `sql_verified = True` guarantee — and what does it not?
6. How is tenant isolation enforced, and where does it currently break?
7. Why does a refusal skip the confidence node?
8. What is the difference between a restatement and a parser correction?
9. Name three things that exist in `QueryState` with no producer.
10. Why was the 0.05 citation floor removed when the measurement behind it was
    correct?

Answer key: not written down on purpose. Every one of these is answerable from
[`GLOSSARY.md`](GLOSSARY.md), [`ENGINEERING_DECISIONS.md`](../architecture/ENGINEERING_DECISIONS.md)
and [`BUGS_AND_LESSONS.md`](../journal/BUGS_AND_LESSONS.md) — and looking it up
and *finding* it is the point.
