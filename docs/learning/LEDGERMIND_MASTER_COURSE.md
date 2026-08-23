# LedgerMind — Master Course

**47 days · 13 phases.** The specification for the course. Each day's full
material lives in [`days/DAY_NN.md`](days/); this file is the plan, the ordering
argument, and the contract each day is produced under.

Read [`00_LEARNING_MAP.md`](00_LEARNING_MAP.md) first. Track progress in
[`LEARNING_PROGRESS.md`](LEARNING_PROGRESS.md).

**Weight:** `L` ≈ 60 min · `M` ≈ 90 min · `H` ≈ 120 min. Days are deliberately
unequal. Compressing a hard topic to keep the day count tidy is the one thing
this course will not do.

---

## The two sources, and which one wins

| Source | Role |
|---|---|
| **The LedgerMind repository** | **The authority.** Every claim is checked against the code. Where a README, docstring or the blueprint disagrees with the implementation, the day records both and says the code wins |
| **`RAG_Complete_Textbook_v2`** | The **conceptual reference layer**. Supplies first-principles grounding for RAG concepts, in its own progression, cited per day |

Every textbook citation carries a label:

- **CONFIRMS** — LedgerMind implements it as described. Read the textbook first,
  then the code.
- **EXTENDS** — LedgerMind goes further than the textbook covers. Read the
  textbook for the floor, then learn what it does not reach.
- **DIVERGES** — LedgerMind deliberately does the opposite. Read both, then
  learn why.

### The divergence register

Thirteen places where following the textbook would install a belief this
codebase contradicts. Each is taught, not skipped.

| # | Textbook | LedgerMind | Day |
|---|---|---|---|
| D1 | Part 17 opens every request with a **cache check** | **No cache exists.** Redis is broker-only; `cache_hit` has no producer; `cache_hit_rate_pct` ships returning a permanent 0.0, recorded as open debt rather than deleted | 44 |
| D2 | 6.2 — cross-encoder scores are raw logits, not probabilities | True but incomplete. **Two** rerankers with **two incompatible scales** that swap under network flap. BUG-001 | 28 |
| D3 | 15B — the metadata filter that is *too strict* returns zero results | The **inverse**: a filter silently *dropped*, giving an unfiltered whole-tenant search that answered at tier=high. Audit F2 | 27 |
| D4 | 15B — "sandwich" chunk ordering in the prompt | **Not implemented.** A genuine unclaimed improvement → KNOWN_UNKNOWNS, not silently "fixed" | 30 |
| D5 | 4.6 — parent-child chunking | **Not built** (DELTAS §B). Per-block-type targets + 150-token overlap + near-duplicate suppression instead | 24 |
| D6 | 10.8 — evaluate with RAGAS faithfulness 0–1 | **Rejected.** Exact-value assertions: pass/fail, not a score | 43 |
| D7 | Part 11 — agentic RAG, ReAct loops | **Deliberately deterministic.** The DSL repair loop is bounded at 2 and repairs schema, not strategy | 47 |
| D8 | Part 12 — Graph RAG, Neo4j | Out of scope by decision: "flat retrieval is not yet the bottleneck" | 47 |
| D9 | 13.3 — caption tables into text | **Opposite.** Positional extraction into typed SQL rows. Captioning a balance sheet destroys the exact-value guarantee | 22, 31 |
| D10 | 10.2 — query rewriting, multi-query expansion | Minimal only: an entity/period prefix to boost BM25. No LLM rewrite | 25 |
| D11 | Part 9 — `data/ ingestion/ embeddings/ retrieval/ api/` | Real layout is `engines/ ingestion/ api/ auth/ llm/ metrics/` | 3 |
| D12 | Part 8 — FAISS / ChromaDB / OpenAI deep dives | None used. Taught as contrast so the Qdrant API stops looking arbitrary | 21 |
| D13 | Part 14 — classifier → SQL path *or* vector path → synthesiser | **Structurally LedgerMind.** LedgerMind adds a third path and a contradiction engine the textbook has no equivalent for | 3, 37 |

---

## The per-day production contract

Every day is produced by exactly this sequence. No step is skipped, and the last
one is not negotiable.

```
Day N
 ├─ 1. WRITE   docs/learning/days/DAY_NN.md
 ├─ 2. COMMENT the day's files — comments ONLY
 ├─ 3. EXPERIMENT — small, runnable, tied to that day's files
 ├─ 4. OPEN THESE YOURSELF — 2–5 exact paths
 ├─ 5. QUESTIONS — 5 basic / 5 code / 5 why / 3 debugging / 2 system design
 ├─ 6. ANSWER KEY — separately fenced, below the questions
 ├─ 7. MUST REMEMBER · MUST UNDERSTAND
 ├─ 8. VERIFY — pytest against the 218/25 baseline; git diff --stat
 ├─ 9. UPDATE — GLOSSARY · LEARNING_PROGRESS · MUST_KNOW ·
 │             TERMINAL_AND_COMMANDS · CODE_DOCUMENTATION_LOG ·
 │             CAVEATS / KNOWN_UNKNOWNS if anything is found
 ├─10. COMMIT — individual, logical commits, each preceded by:
 │             Files changed / Why / Functional code changed: YES-NO /
 │             Docs only / Comments only / Tests run / Message
 └─11. STOP.   NEVER PUSH.
```

### Constraints on step 2

Comments only. No renames, no logic changes, no reordering, no dependency
changes, no adjacent cleanup, no formatting churn. Comments explain **why**, not
what. Files already well commented are **read as teaching material, not
rewritten** — see the Tier 4 list in
[`CODE_DOCUMENTATION_LOG.md`](../journal/CODE_DOCUMENTATION_LOG.md).

**If a documentation change would require a functional change to be accurate:
STOP, report, do not modify.**

### Each day's structure

```
1.  Today's goal — what you can explain by tonight
2.  Why now — which earlier days make this readable
3.  Prerequisites — if one is missing, it is taught before proceeding
4.  Concept lesson — problem → why a solution was needed → old approach →
    its limitation → the new concept → mental model → simple example →
    LedgerMind example
5.  The actual files — purpose · why it exists · who imports it · what it
    imports · entry points · data in · data out
6.  Deep walkthrough — state before → the code → state after → object view →
    data shape → why this line → what breaks if removed → why not the
    alternative
7.  Data flow at every boundary
8.  Engineering decision — alternatives, trade-offs, current validity,
    what changes at 10×
9.  Failure modes
10. Hands-on experiment
11. Open these files yourself
12. Self-check questions  →  answers, separately fenced
13. Viva: 5 basic / 5 code / 5 why / 3 debugging / 2 system design
14. MUST REMEMBER · MUST UNDERSTAND
15. This connects to: previous → current → next
```

---

# PHASE 0 — GROUND (Days 1–3)

> Nothing in this repository can be read until the stack runs and `QueryState`
> makes sense. This phase buys both.

## DAY 01 — The machine underneath
**Weight** M · **Prerequisites** none
**Concepts** process · port · environment variable · image vs container · bind mount · service dependency · health check
**Files** `docker-compose.yml` · `backend/Dockerfile` · `frontend/Dockerfile` · `.env.example` · `docs/RUNBOOK.md`
**Textbook** — (pre-textbook material)
**Hands-on** Bring the stack up. Poll `/health` until it reports healthy. Prove *which code is running* with `import app.engines.retriever; print(m.__file__)`. Find the `DATABASE_URL` override at `docker-compose.yml:51` and work out which of the two databases you are actually talking to.
**Inspect** `docker-compose.yml` · `backend/Dockerfile` · `.env.example`
**Must remember** 7 services · `env_file` vs `environment` · `up -d` returns when the container starts, not when uvicorn serves
**Must understand** why `--force-recreate` destroys files copied in with `docker compose cp`; why a bind mount means "copied into the container" and "in your working tree" are the same thing
**Capability** Explain what each container does and state which database a measurement came from.

## DAY 02 — Reading a repository
**Weight** L · **Prerequisites** D1
**Concepts** commit · diff · staged vs working tree · history as evidence · commit granularity
**Files** `CLAUDE.md` §3 · the git history itself
**Hands-on** `git log --oneline -40`. `git show 7d580df`. Find the commit that renamed `company` → `companies`. Read `git log -- backend/tests/conftest.py` and notice it stops at 2026-08-11.
**Inspect** `CLAUDE.md` §3 · `docs/journal/PROJECT_TIMELINE.md`
**Must remember** `git add` on an unmodified file stages nothing and the commit is a silent no-op — run `git diff --stat` first, every time
**Must understand** why one commit per file; what git history can establish vs. only suggest
**Capability** Use history as evidence rather than as backup.

## DAY 03 — Three engines, one dictionary
**Weight** H · **Prerequisites** D1, D2
**Concepts** the guarantee · path routing · shared mutable state · offline vs online
**Files** `README.md` · `docs/architecture/LEDGERMIND_ARCHITECTURE.md` §1–3 · **`backend/app/engines/state.py` — every line**
**Textbook** 14.1–14.2 **CONFIRMS** (the dual-path case study is structurally this system) · Part 9 **DIVERGES** (folder layout)
**Hands-on** Compare the textbook's two-path case study against LedgerMind's three. List what the third path adds. Compare textbook Part 9's proposed folder layout to the real one and account for every difference.
**Inspect** `engines/state.py` · `engines/graph.py` · `README.md`
**Must remember** semantic / quantitative / cross · `QueryState` is a `TypedDict` mutated in place · the LLM never does arithmetic
**Must understand** why one dict instead of layers; what that buys (inspectable at every boundary) and what it costs (any node can write any field)
**Capability** Explain the whole system to someone else without opening the repo.

---

# PHASE 1 — HOW A REQUEST ARRIVES (Days 4–6)

## DAY 04 — HTTP → API → endpoint → FastAPI
**Weight** M · **Prerequisites** D1, D3
**Concepts** client/server · method · URL · headers · body · status codes · what a framework adds
**Files** `backend/app/main.py` · the `/health` endpoint
**Hands-on** `curl -i` against `/health`; read every response header. Open `/docs`. Stop the Qdrant container and watch the response change from `healthy` to `degraded` — then explain why that is a *readiness* signal, not a liveness one.
**Inspect** `app/main.py`
**Must remember** a request is method + URL + headers + body; 2xx/4xx/5xx and what each class means
**Must understand** why logging is configured **before** the `app.*` imports, and what disappears if it is not
**Capability** Read any endpoint signature and predict its HTTP surface.

## DAY 05 — The contract
**Weight** M · **Prerequisites** D4
**Concepts** JSON · serialisation · schema validation · 422 vs 400 vs 500 · CORS
**Files** `api/query.py:QueryRequest` · `auth/schemas.py` · `main.py` CORS block
**Hands-on** POST a malformed body and read the 422 in full. POST from a disallowed origin and read the CORS failure. Then read `CAVEAT-001` and find the field in `QueryRequest` that should not be there.
**Inspect** `auth/schemas.py` · `api/query.py` (top 40 lines)
**Must remember** 422 is *validation*, 400 is *malformed*, 500 is *ours*
**Must understand** why a typed contract beats a dict at a network boundary; why `allow_origin_regex` for every `*.vercel.app` with credentials is a real trade-off (CAVEAT-012)
**Capability** Read a Pydantic model as an API contract.

## DAY 06 — Two transports, one pipeline
**Weight** H · **Prerequisites** D4, D5
**Concepts** blocking vs streaming · Server-Sent Events · frame format · heartbeat · proxy buffering · backpressure
**Files** `api/query.py` — both endpoints, `_run_graph`, `_sse`, `_trace_detail` · `frontend/lib/api.ts`
**Hands-on** `curl -N` the stream endpoint and watch `event: node` frames arrive one per graph node. Count the heartbeats during a slow LLM call.
**Inspect** `api/query.py` (all 233 lines) · `lib/api.ts:submitQueryStreaming`
**Must remember** SSE frames are separated by a blank line; `X-Accel-Buffering: no` exists because Render/nginx buffer by default
**Must understand** why the graph task is **never** cancelled on client disconnect (the audit row must still be written), and why the queue is unbounded as a direct consequence
**Capability** Explain why two endpoints share one pipeline and cannot drift.

---

# PHASE 2 — IDENTITY AND PERMISSION (Days 7–9)

## DAY 07 — Authentication
**Weight** M · **Prerequisites** D5
**Concepts** identity vs identification · hashing vs encryption · salt · cost factor · timing safety
**Files** `core/security.py` · `auth/service.py` · `auth/router.py`
**Hands-on** Hash the same password twice; observe two different hashes; verify both. Then read why `db_transaction(tenant_id=None)` is used here and **nowhere else**.
**Inspect** `core/security.py` · `auth/service.py`
**Must remember** hashing is one-way; bcrypt embeds its own salt; passlib is deliberately avoided (`bcrypt.__about__` was removed in 4.1)
**Must understand** why a DB failure during login returns **503, not 500** — retryable vs. defective, and who acts on the difference
**Capability** Explain the login path end to end.

## DAY 08 — JWT and dependency injection
**Weight** H · **Prerequisites** D7
**Concepts** stateless auth · claims · signature · expiry · bearer scheme · dependency injection · when a dependency runs
**Files** `core/security.py:create_access_token/decode_access_token` · `auth/dependencies.py:get_current_user`
**Hands-on** Mint a token. Base64-decode the payload **by hand** and read the claims. **Tamper with one claim and watch verification fail.** Let one expire and read the 401.
**Inspect** `core/security.py` · `auth/dependencies.py`
**Must remember** **a JWT is signed, not encrypted** — anyone can read it, nobody can forge it without the secret
**Must understand** why auth is a `Depends` and not middleware; what `request.state.user` buys downstream
**Capability** Trace a token from mint to verification and name every failure mode.

## DAY 09 — Authorization
**Weight** M · **Prerequisites** D8
**Concepts** authn vs authz · role hierarchy · route-level vs field-level enforcement · fail closed
**Files** `auth/dependencies.py:require_role` · `api/response_shaping.py`
**Hands-on** Run the same query as viewer, analyst and admin. `diff` the three JSON responses. Find the field that is **omitted** on a blocked query and explain why omitted rather than null.
**Inspect** `api/response_shaping.py` (all 177 lines) · `auth/dependencies.py`
**Must remember** viewer 0 < analyst 1 < admin 2 · the graph always runs in full; only the **response** is filtered
**Must understand** why an unrecognised role gets the *most* restrictive payload; why `confidence_tier` is omitted but `confidence_score` deliberately is not
**Capability** Explain field-level RBAC and why it fails closed.

---

# PHASE 3 — PYTHON AS THIS CODEBASE USES IT (Days 10–12)

## DAY 10 — Three type systems, on purpose
**Weight** M · **Prerequisites** D3, D5
**Concepts** `TypedDict` · `dataclass` · `frozen=True` · Pydantic `BaseModel` · what each validates and when
**Files** `engines/state.py` · `ingestion/models.py` · `metrics/registry.py` · `auth/schemas.py`
**Hands-on** Mutate a `TypedDict`; try to mutate a frozen dataclass; feed an invalid value to a Pydantic model. Three different failure modes, three different times.
**Inspect** `ingestion/models.py` · `metrics/registry.py` (top 90 lines)
**Must remember** `ChunkResult` is a `TypedDict` — use `chunk["text"]`, never `getattr`
**Must understand** why all three coexist: a dict that must stay cheap to mutate; records that must not mutate; a boundary that must validate
**Capability** Choose the right one, and say why.

## DAY 11 — Context managers, generators, async
**Weight** H · **Prerequisites** D6, D10
**Concepts** `with` · `@contextmanager` · `yield` · generator · `async`/`await` · `asyncio.Queue` · `create_task`
**Files** `db/session.py` · `api/query.py:event_stream`, `_run_graph`
**Hands-on** Write a five-line `@contextmanager`. Raise inside it. Observe the rollback. Then find what `with conn:` does **not** do.
**Inspect** `db/session.py` (all 46 lines) · `api/query.py:event_stream`
**Must remember** `db_transaction()` yields a **connection**, not a cursor — use `conn.cursor()`
**Must understand** why `SET LOCAL` and not `SET`; why the SSE queue is unbounded; why the producer task outlives the consumer
**Capability** Read async code in this repo without guessing.

## DAY 12 — Module-level state
**Weight** M · **Prerequisites** D11
**Concepts** import-time vs call-time · lazy singleton · module globals · logging configuration order · cold start
**Files** `retriever.py:_get_dense_model` and siblings · `main.py` header · `worker.py` header · `core/config.py`
**Hands-on** Time a cold `docker compose exec` against a warm one. Move `basicConfig` below the `app.*` imports in a scratch copy and watch every import-time INFO log vanish.
**Inspect** `app/main.py` (top 25 lines) · `app/worker.py` · `retriever.py` (lines 60–130)
**Must remember** a fresh `exec` costs ~30 s of cold fastembed/ONNX load; warm calls are 0.36–0.41 s. **A local semantic failure is not a defect until it reproduces warm**
**Must understand** why models load lazily; why `force=True` on `basicConfig`; why `CAVEAT-020` (`_get_gemini` declares a global it never assigns) is harmless but wrong
**Capability** Predict what happens at import time in any module here.

---

# PHASE 4 — WHERE THE TRUTH LIVES (Days 13–16)

## DAY 13 — Relational modelling
**Weight** M · **Prerequisites** D10
**Concepts** table · row · column · primary key · foreign key · `CHECK` · `NUMERIC` vs `FLOAT` · normalisation · `JOIN` · `GROUP BY`
**Files** `sql/init.sql`
**Textbook** 3.1 **DIVERGES** — the textbook explains why a relational DB *cannot* do vector search; this day is why LedgerMind still needs one
**Hands-on** Draw the five tables and their foreign keys from the DDL alone. Then run `SELECT`, a `JOIN` and a `GROUP BY` against `financials`.
**Inspect** `sql/init.sql`
**Must remember** the five tables: `tenants` · `users` · `documents` · `financials` · `audit_log`
**Must understand** why `value` is `NUMERIC` and not `FLOAT`; why `audit_log` has no `UPDATE` or `DELETE` grant, ever
**Capability** Explain every column of `financials`.

## DAY 14 — Transactions, `SET LOCAL`, Row-Level Security
**Weight** H · **Prerequisites** D13, D9, D11
**Concepts** ACID · transaction scope · session GUC · RLS policy · `FORCE ROW LEVEL SECURITY` · defence in depth
**Files** `sql/init.sql` policies · `db/session.py` · `quant_engine._execute_sql` · `audit_writer`
**Hands-on** **Query `financials` without setting `app.tenant_id` → 0 rows. Then set it → rows.** Sit with that. Then read why the policy uses `CASE ... WHEN ... THEN FALSE` rather than `AND`.
**Inspect** `sql/init.sql` (policy block) · `db/session.py`
**Must remember** **RLS silently returns 0 rows when the GUC is unset. That is not a data-missing signal.** Always `SET app.tenant_id` first
**Must understand** why `SET LOCAL` and not `SET` on a pooled connection; why `AND` is not a short-circuit operator in SQL; where `tenant_id` stops being trustworthy (CAVEAT-001)
**Capability** Trace `tenant_id` from HTTP request to RLS policy and name the weak point.

## DAY 15 — Indexes, locking, and restatement
**Weight** H · **Prerequisites** D14
**Concepts** B-tree index · partial index · unique constraint · `SELECT … FOR UPDATE` · `ON CONFLICT DO NOTHING` · `IS NOT DISTINCT FROM` · idempotency
**Files** `ingestion/db_loader.py` · `uq_financials_latest` · migrations 015–017
**Textbook** 15B upsert-vs-insert **CONFIRMS**
**Hands-on** Try to insert a second `is_latest = TRUE` row for the same business key; watch the partial index reject it. Trace `_upsert_one`'s lock → peek → retire → insert sequence.
**Inspect** `db_loader.py` (lines 49–380)
**Must remember** restatements **retire** rows (`is_latest = FALSE`); they never delete or overwrite
**Must understand** why a fixed parser re-reading a fixed document is a *correction*, not a restatement — and why `correct_values=True` is off by default
**Capability** Explain how the same document ingested twice produces no duplicates.

## DAY 16 — Migrations, and two databases
**Weight** M · **Prerequisites** D15
**Concepts** schema migration · forward-only · migration ledger · privilege separation · environment divergence
**Files** `sql/migrations/` (17 files) · `scripts/check_migrations.py` · `docker-compose.yml:51`
**Hands-on** Read migrations 018–019 (deterministic doc_ids) and reconstruct, from the SQL alone, the bug they fix. Then read the README's account and compare.
**Inspect** `sql/migrations/018_*.sql`, `019_*.sql` · `check_migrations.py`
**Must remember** `ledgermind_app` is `NOSUPERUSER`. **You cannot apply migrations** — write the `.sql`, wrapped in `BEGIN;`/`COMMIT;`, and stop
**Must understand** why local Docker Postgres and Supabase hold different document counts, and why every measurement must state which one it came from (CAVEAT-015)
**Capability** Write a migration and know who applies it.

---

# PHASE 5 — LLM FOUNDATIONS (Days 17–19)

## DAY 17 — What an LLM is
**Weight** M · **Prerequisites** none (independent of Phase 4)
**Concepts** next-token prediction · tokenisation · context window · hallucination · parametric vs non-parametric memory · grounding
**Files** `CLAUDE.md` §6 · `docs/architecture/ENGINEERING_DECISIONS.md` ED-001
**Textbook** **Part 1, in full** — **CONFIRMS**
**Hands-on** Ask a bare LLM for a figure that exists only in the corpus. Watch it produce a fluent, wrong number. That experiment is the whole justification for the rest of the course.
**Inspect** `ENGINEERING_DECISIONS.md` ED-001
**Must remember** there is no database inside the model; a context window is a ceiling, not a target
**Must understand** why "can the model remember accurately" was replaced by "can we retrieve the right document" — and why that trade is the entire point of RAG
**Capability** Explain why RAG exists, unprompted, in one paragraph.

## DAY 18 — Prompting and structured output
**Weight** H · **Prerequisites** D17, D10
**Concepts** system vs user message · temperature · JSON mode · response schema · schema-as-input · prompt ordering
**Files** `router.ROUTER_SYSTEM_PROMPT` · `quant_engine.DSL_SYSTEM_PROMPT` · `response_generator.SYNTHESIS_SYSTEM_PROMPT` · `IMPLEMENTATION_DELTAS.md` §D
**Textbook** Part 15 "Prompting" **EXTENDS**
**Hands-on** Read §D, *"The response schema is part of the prompt"*. Count the bytes F14 added to the Gemini schema and removed from Groq's. Then find the field in `RouterResponse` with **no prompt block** and read why an instruction was written, shipped, and removed.
**Inspect** `engines/router.py` (prompt + `RouterResponse`) · `IMPLEMENTATION_DELTAS.md` §D
**Must remember** **"no prompt block" is not "invisible to the model."** Declaring a schema field is itself an input change
**Must understand** why appended instructions have lost to earlier, more concrete rules **three separate times**; why prompt edits need reading, not testing
**Capability** Predict whether a proposed prompt change is safe — and know that "predict" is not "know".

## DAY 19 — The shared LLM client
**Weight** H · **Prerequisites** D18, D12
**Concepts** timeout · retry vs ladder · transport-class vs provider-class failure · failover · degradation visibility · attribution precedence
**Files** `llm/client.py` (all 444 lines) · `state.record_llm_call`, `clear_llm_attribution`
**Textbook** 10.6 **EXTENDS**
**Hands-on** Trace `_TRANSPORT_MARKERS` vs `_PROVIDER_MARKERS`. Find why `"resolution"` was added on 2026-08-22 and what failed without it. Then read why 401/403 are **deliberately excluded** from the fallback trigger.
**Inspect** `llm/client.py` · `engines/state.py` (bottom third)
**Must remember** one attempt plus **exactly one** retry, never a ladder · Gemini primary, Groq fallback · attribution moves only toward "more degraded"
**Must understand** why a timeout is a **precondition** for a fallback — a fallback keyed on exceptions can never fire against a hang; why a Groq-served answer must not look identical to a Gemini-served one in the audit log
**Capability** Explain what happens to a query when Gemini is down, and what the record shows afterwards.

---

# PHASE 6 — RAG FOUNDATIONS AND INGESTION (Days 20–24)

## DAY 20 — Embeddings
**Weight** M · **Prerequisites** D17
**Concepts** vector · dimension · semantic geometry · cosine similarity · dot product · Euclidean distance · model symmetry
**Files** `ingestion/embedder.py` · `retriever._encode_dense`
**Textbook** **Parts 1.4–2.4** — **CONFIRMS**
**Hands-on** Embed two sentences with `bge-small-en-v1.5`. Compute cosine similarity by hand. Then embed a paraphrase and a contradiction and compare the three scores.
**Inspect** `ingestion/embedder.py`
**Must remember** 384 dimensions · cosine measures **direction**, not magnitude · the same model must embed documents and queries
**Must understand** why mixing embedding models produces numerically valid, semantically meaningless scores — and why that failure raises no error
**Capability** Explain what a vector *is* here, and what it is not.

## DAY 21 — Vector databases
**Weight** H · **Prerequisites** D20
**Concepts** ANN · HNSW · named vectors · payload · payload index · upsert · collection
**Files** `ingestion/qdrant_writer.py` · `scripts/create_qdrant_collection.py`
**Textbook** **Part 3** **CONFIRMS** · **Part 8** as contrast (FAISS/Chroma are **not** used — **DIVERGES**)
**Hands-on** Read the textbook's FAISS and ChromaDB snippets, then read `_build_point` and `write_chunks`. Account for every difference in the API. List the collection's payload indexes and say what each one enables.
**Inspect** `qdrant_writer.py` · `create_qdrant_collection.py`
**Must remember** collection `ledgermind_chunks` · named vectors `dense` and `sparse` in **one** point · point ID = deterministic `chunk_id`
**Must understand** what HNSW trades away and why the trade is acceptable; what happens to a filtered query when the payload index is missing
**Capability** Explain why this needs *named* vectors when the textbook's examples do not.

## DAY 22 — PDF → PageBlock
**Weight** H · **Prerequisites** D10
**Concepts** text extraction · table extraction · word positions · column detection · OCR damage · lossy input
**Files** `ingestion/pdf_parser.py` · `ingestion/models.py`
**Textbook** 13.3–13.4 — **DIVERGES** (captioning is rejected here)
**Hands-on** Parse one corpus PDF. Print the block-type distribution per page. Find a `TYPO_MAP` entry actually firing on real text. **Parse once and reuse — parsing twice exhausts WSL RAM and restarts the distro.**
**Inspect** `pdf_parser.py` (`parse_financial_line`, `extract_financials_positional`) · `models.py`
**Must remember** tables are extracted **before** text, and text blocks have table regions masked out
**Must understand** why a financial table is reconstructed *positionally* rather than captioned into prose — captioning destroys the exact-value guarantee
**Capability** Explain how a printed table becomes structured rows, and where that can silently fail (CAVEAT-003).

## DAY 23 — Classification
**Weight** M · **Prerequisites** D22
**Concepts** structure signal · location signal · content signal · intersection vs union · false positives
**Files** `ingestion/document_classifier.py` · `ingestion/section_classifier.py`
**Textbook** 10.1 metadata filtering **EXTENDS**
**Hands-on** Feed a synthetic `PageBlock` list to `detect_sections` (the `make_block` fixture in `conftest.py` shows how). Break one of the three signals and watch the classification change.
**Inspect** `section_classifier.py` (top 250 lines) · `document_classifier.py`
**Must remember** one PDF → **two** `documents` rows (consolidated + standalone) · `financial_type` is detected from **content**, never from a filename or a form field
**Must understand** why "Revenue from operations" appearing in prose is a false positive, and why all three signals must align
**Capability** Explain why intersection and not union.

## DAY 24 — Chunking and embedding
**Weight** H · **Prerequisites** D23, D20
**Concepts** chunk size trade-off · overlap · recursive splitting · deterministic IDs · speaker turns · batch size
**Files** `ingestion/chunker.py` · `ingestion/embedder.py`
**Textbook** **Part 4** (all strategies) **EXTENDS** · 4.6 parent-child **DIVERGES** (not built) · 15B chunk-size trap **CONFIRMS** · 15B batch-size crash **CONFIRMS**
**Hands-on** Chunk one document. Find two adjacent chunks sharing ~150 tokens. Re-run and confirm the chunk IDs are byte-identical.
**Inspect** `chunker.py` (`_recursive_split`, `_split_speaker_turns`, `_make_chunk_id`)
**Must remember** `OVERLAP_TOKENS = 150` is **frozen** · `BATCH_SIZE = 8` (32 caused OOM at 1999+ chunks) · chunk IDs are deterministic, so re-ingestion overwrites cleanly
**Must understand** why overlap was raised from 50 (a mid-sentence split orphaned Paytm's PPBL impairment); why the textbook's "chunk size is a system-wide constant, treat it like a schema migration" is exactly right here
**Capability** Explain the full ingestion chain, and what re-chunking would cost.

---

# PHASE 7 — RETRIEVAL ENGINEERING (Days 25–29)

> **Strictly ordered.** Day 28 is unteachable before Day 27.

## DAY 25 — Dense retrieval
**Weight** M · **Prerequisites** D20, D21
**Concepts** query embedding · top-k · where dense fails · query rewriting
**Files** `retriever._encode_dense` · `router._build_resolved_query`
**Textbook** 5.2 **CONFIRMS** · 10.2 query rewriting **DIVERGES** (minimal only)
**Hands-on** Search for a ticker or a proper noun using dense retrieval alone. Watch it underperform. Then read what `_build_resolved_query` prefixes and why.
**Inspect** `retriever.py` (lines 175–200) · `router._build_resolved_query`
**Must remember** dense retrieval fails on exact identifiers — codes, tickers, proper nouns
**Must understand** why the entity/period prefix is a *minimal* query rewrite, and why no LLM rewrite exists here
**Capability** Predict which queries dense retrieval will handle badly.

## DAY 26 — Sparse retrieval: BM25
**Weight** M · **Prerequisites** D25
**Concepts** term frequency · inverse document frequency · length normalisation · sparse vector · where BM25 fails
**Files** `retriever._encode_sparse` · `embedder._embed_sparse`
**Textbook** 5.3 **CONFIRMS**
**Hands-on** Run the Day 25 proper-noun query through BM25 and compare. Then run a paraphrase query through both and watch the reverse happen.
**Inspect** `retriever.py:_encode_sparse` · `embedder.py:_embed_sparse`
**Must remember** BM25 wins on exact terms, loses on synonyms; "money back" scores zero against "refund"
**Must understand** IDF's intuition — a term in every document carries no signal — without writing the formula
**Capability** Say which of two queries each method will win, and why.

## DAY 27 — Hybrid retrieval, RRF, and where the filter goes
**Weight** H · **Prerequisites** D25, D26, D14
**Concepts** fusion · reciprocal rank fusion · the `k` constant · pre-filter vs post-filter · prefetch legs · metadata filtering as access control
**Files** `retriever.hybrid_search` · `retriever._build_filter`
**Textbook** 5.4 **CONFIRMS** · 15B "metadata filter returns zero results" **EXTENDS/DIVERGES** — LedgerMind's failure is the **inverse**
**Hands-on** In a **scratch copy**, move the filter from the prefetch legs to fusion level and observe unfiltered candidates polluting the ranking. Then read the `UNFILTERED WHOLE-TENANT SEARCH` warning and audit finding F2.
**Inspect** `retriever.py` (lines 200–330)
**Must remember** RRF fuses **ranks**, not scores, because BM25 8.94 and cosine 0.91 are not on one scale · the filter runs **inside each prefetch leg**
**Must understand** why a *dropped* filter is worse than a too-strict one: too-strict returns nothing and is obvious; dropped returns a confident answer from the wrong issuer's pages
**Capability** Explain filter placement as a *correctness* decision, not a performance one.

## DAY 28 — Reranking, and two incompatible score scales
**Weight** H · **Prerequisites** D27
**Concepts** bi-encoder vs cross-encoder · why cross-encoders cannot do first-pass retrieval · relevance score · **score calibration** · backend switching
**Files** `retriever.rerank` · `_cohere_with_retry` · `semantic_engine.py` threshold block · `api/response_shaping._reranker_backend`
**Textbook** **Part 6** — **EXTENDS.** *The most important divergence in the course*
**Hands-on** Rerank the same candidate set through Cohere and through local ONNX. Put the two score lists side by side. Then read BUG-001 and reconstruct how one threshold pair applied to both scales made every Cohere-served query "high confidence".
**Inspect** `retriever.py:rerank` · `semantic_engine.py` (lines 30–95)
**Must remember** **Cohere `[0,1]`; local ONNX logits ~`[-12,+2]`. A `reranker_score` without its `reranker_backend` is meaningless**
**Must understand** why the fallback fires at random on WSL2 network flap; why the fix was two threshold pairs, not one averaged pair; why `reranker_backend` ships to admins on the wire
**Capability** Given "the same query returned two different confidence tiers", know what to check first.

## DAY 29 — Dedup, confidence, CRAG
**Weight** H · **Prerequisites** D28, D24
**Concepts** near-duplicate suppression · token-set containment · confidence tiers · normalisation · gap bonus · corrective retrieval as a filter ladder
**Files** `retriever._deduplicate_near_identical` · `semantic_engine._score_confidence`, `_broaden_retrieval`, `semantic_engine_node`
**Textbook** 10.x Corrective RAG **EXTENDS**
**Hands-on** Find a real near-duplicate pair in a top-20 and compute the containment ratio by hand. Then trace the CRAG ladder for a query with `quarter=None` and see why rung 1 is a no-op.
**Inspect** `retriever.py` (dedup block) · `semantic_engine.py` (all 388 lines)
**Must remember** threshold 0.70, denominator is the **smaller** chunk · CRAG rungs `continue`, they do not `break` · `crag_count` is a **rung index**, not a retrieval count
**Must understand** why the fix was suppression rather than reducing overlap (overlap is load-bearing); why the `break`-vs-`continue` bug silently removed CRAG from every annual query
**Capability** Explain the measured constants and why they are frozen.

---

# PHASE 8 — THE SEMANTIC PATH, WHOLE (Day 30)

## DAY 30 — From question to cited answer
**Weight** H · **Prerequisites** D29, D19, D18
**Concepts** prompt assembly · grounding instruction · faithfulness · citation contract · post-generation refusal detection · the synthesis floor
**Files** `engines/response_generator.py` (all 709 lines)
**Textbook** **Part 7** + **Part 17** **CONFIRMS** · 15B "retrieval looks good but the answer is wrong" **CONFIRMS** · 15B sandwich ordering **DIVERGES** (not implemented — record, do not fix)
**Hands-on** Trace one real semantic question from typed text to rendered citation. Then read the deleted citation-floor comment block and reconstruct, from it alone, why a *correct* measurement produced a *wrong* design.
**Inspect** `response_generator.py` · `semantic_engine._build_citations`
**Must remember** quantitative answers are **templated**; semantic answers are **generative**; only the latter involves an LLM writing prose
**Must understand** why `confidence_tier` scores retrieval *before any answer exists*, and what `_is_refusal_text` covers that it cannot; why the citation floor's removal was right even though its measurement was sound
**Capability** **Explain every arrow of the semantic pipeline.**

---

# PHASE 9 — THE QUANTITATIVE PATH (Days 31–34)

## DAY 31 — The registry, and how a number becomes a row
**Weight** H · **Prerequisites** D13, D24
**Concepts** single source of truth · canonical name vs alias · metric type · derived vs raw · label normalisation · accounting identities · units and scale
**Files** `metrics/registry.py` (768 lines) · `ingestion/financial_extractor.py` (908 lines) · `ingestion/entity_resolver.py` (metric half) · `scripts/regression_check.py`
**Textbook** 14.3–14.5 **CONFIRMS**
**Hands-on** Trace one metric name through all five consumers of the registry. Then run `regression_check` **once**, tee to `/tmp`, and grep the file.
**Inspect** `metrics/registry.py` · `financial_extractor.py` (`_compute_derived_totals`, `validate_financial_identities`)
**Must remember** **`app/metrics/registry.py` is the single metric registry — never add a second** · **both formula copies must be updated together** (`_compute_derived_totals` and `validate_financial_identities` are independent)
**Must understand** why three registries caused three shipped bugs; why every value is asserted to be `crore_inr` with no scale detection (audit F3 — the open blocker for arbitrary documents)
**Capability** Explain how a printed figure becomes a `financials` row, and every place it can go wrong.

> **Refinement, 2026-08-23.** `financial_extractor.py` lands here rather than in
> Phase 6, because its job is *turning parsed rows into registry-anchored metric
> records* — that is the registry's domain. `entity_resolver.py` splits: its
> metric half (`normalize_metric_label`, `resolve_metric`) is taught here, its
> company half (`COMPANY_REGISTRY`, `resolve_ticker`) on Day 36 with the router.

## DAY 32 — The DSL
**Weight** H · **Prerequisites** D31, D18
**Concepts** domain-specific language · schema design · validation vs parsing · repair hint · bounded self-healing · expressiveness limits
**Files** `engines/dsl_compiler.py:DSLValidator` · `quant_engine._generate_dsl`, `GeminiDSLResponse`
**Hands-on** Hand `validate_dsl` a deliberately broken object and read the `repair_hint` it returns. Then read CAVEAT-004 and list what the schema **cannot say**.
**Inspect** `dsl_compiler.py` (lines 55–190) · `quant_engine.py` (lines 180–300)
**Must remember** eight fields · `MAX_DSL_ATTEMPTS = 2` · `LLMUnavailable` **breaks** the loop rather than retrying
**Must understand** why "no provider answered" is not a DSL defect and a repair hint cannot fix it; why a required `metric` field forces the model to invent one
**Capability** Explain what the DSL is *for*, and where its schema is the weak point.

## DAY 33 — Compilation and arithmetic
**Weight** H · **Prerequisites** D32, D15
**Concepts** compiler · parameterised query · SQL injection · operation dispatch · derived metric computation
**Files** `dsl_compiler.py:SQLCompiler` · `quant_engine._compute_yoy_growth`, `_compute_comparison`, `_compute_cagr`, `_compute_growth_comparison`
**Hands-on** Compile all five operations and read the generated SQL. Verify one YoY percentage by hand against the two SQL results.
**Inspect** `dsl_compiler.py` (lines 190–300) · `quant_engine.py` (lines 470–620)
**Must remember** five operations · `growth_comparison` needs **four** queries · **LLMs never do math**
**Must understand** why parameterisation is the injection defence and why the LLM never touching SQL makes it structural; why `_base_select` always includes `financial_type` (blueprint Trap 2)
**Capability** Write a new operation, and name every file that would change.

## DAY 34 — The guards, and verification
**Weight** H · **Prerequisites** D33
**Concepts** pre-LLM guards · refusing vs substituting · period assumption · row-count verification · disclosure
**Files** `quant_engine.quant_engine_node` — Stages 0, 0b, 1–5 · `_query_names_period`, `_latest_fiscal_year`
**Textbook** 15B silent-zero-result **EXTENDS**
**Hands-on** Ask for `ebitda` (a derived metric) and watch it refuse **before any LLM call**. Then ask a question with no period and inspect `period_assumed` in the response.
**Inspect** `quant_engine.py` (lines 620–915)
**Must remember** three guards, all regex over the **raw query**, all before the LLM · `point_in_time` expects **exactly one** row; more is `ambiguous_result`
**Must understand** why the raw query is the only place the user's intent still exists; why `period_assumed` must be **disclosed** rather than hidden behind a tick
**Capability** Explain what `sql_verified = True` guarantees — and precisely what it does not.

---

# PHASE 10 — ORCHESTRATION (Days 35–37)

## DAY 35 — LangGraph
**Weight** M · **Prerequisites** D3, D11
**Concepts** state machine · node · edge · conditional edge · entry point · compilation · singleton
**Files** `engines/graph.py`
**Textbook** 14.2 **CONFIRMS**
**Hands-on** Draw the graph from `graph.py` alone, without the docstring diagram. Identify the two edges that skip the confidence tail and say why each does.
**Inspect** `engines/graph.py` (all 132 lines)
**Must remember** eight nodes · two conditional edges · compiled once, module singleton
**Must understand** why `blocked` and `refused` bypass `confidence_node` — it would **rescore a refusal** (measured: tier=high @ 0.7095 on a query with no valid company)
**Capability** Add a node on paper and name every file that changes.

## DAY 36 — The router
**Weight** H · **Prerequisites** D35, D19, D31
**Concepts** classification · entity extraction · alias resolution · refusal · a field that overloads `null`
**Files** `engines/router.py` (428 lines) · `ingestion/entity_resolver.py` (company half) · `scripts/router_probe.py`
**Hands-on** Run `router_probe.py`. Ask about a company not in the corpus and watch `company_not_in_corpus` fire. Then find the case where it does **not** fire, and read why F2 is "partial by construction".
**Inspect** `router.py` · `entity_resolver.py` (`COMPANY_REGISTRY`, `resolve_ticker`)
**Must remember** `resolve_ticker` **never returns None** — it uppercases its input, so the `_KNOWN_TICKERS` gate is where an unknown company is actually detected · `_KNOWN_TICKERS` is larger than the corpus
**Must understand** F2 and F14 as **one defect class**: a single-valued field overloading `null` with two incompatible meanings. "Not in corpus" and "more than one" were the same value
**Capability** Explain how a question becomes a path, and every way that can fail.

## DAY 37 — Cross-examination
**Weight** H · **Prerequisites** D36, D30, D34
**Concepts** hybrid verification · sub-engine composition · execution order as a fix · claim eligibility · proximity anchoring · severity
**Files** `engines/cross_engine.py` · `engines/contradiction.py` (456 lines)
**Textbook** Part 14 **EXTENDS** — the textbook's case study has no equivalent
**Hands-on** Reconstruct BUG-003's eleven false "high severity" contradictions, then work out which of the three current rules kills each one.
**Inspect** `cross_engine.py` · `contradiction.py` (docstring + `_is_claim_eligible`)
**Must remember** quant runs **first**, deliberately · narrative chunks only · a figure is a claim only within `PROXIMITY_WINDOW = 120` chars of a metric alias
**Must understand** why two earlier fixes failed (both tried to suppress a *true* statement) and why running quant first worked (it made the statement false); **why a false contradiction is worse than a missed one — it directly inverts the system's stated value**
**Capability** Explain the third path and why it is not just "run both".

---

# PHASE 11 — FRONTEND (Days 38–41)

## DAY 38 — React and Next, against this app
**Weight** M · **Prerequisites** D5
**Concepts** HTML · TypeScript · component · props · JSX · App Router · server vs client component
**Files** `frontend/app/layout.tsx` · `frontend/app/page.tsx` (structure) · `components/document/DocumentPage.tsx`
**Hands-on** Change one string and watch hot reload. Remove `"use client"` from a component that needs it and read the error.
**Inspect** `app/layout.tsx` · `components/document/DocumentPage.tsx`
**Must remember** one page, five views, all state in `page.tsx`
**Must understand** why `"use client"` exists and what it marks; why the working-paper layout needed Next.js rather than Streamlit (ED-023)
**Capability** Read any component in this repo and say what it receives and renders.

## DAY 39 — State, effects, and the SSE consumer
**Weight** H · **Prerequisites** D38, D6
**Concepts** `useState` · `useEffect` · state lifting · `ReadableStream` · frame buffering · retry classification
**Files** `frontend/lib/api.ts:submitQueryStreaming` · `page.tsx` state · `components/document/ExecutionTrace.tsx`
**Hands-on** Kill the backend **mid-stream** and observe the single retry. Kill it **before connect** and observe no retry. Read `api.retry.guard.ts`.
**Inspect** `lib/api.ts` · `lib/api.retry.guard.ts`
**Must remember** four error classes: `UnauthorizedError` · `PipelineError` · `RequestFailedError` · `TransportError`. **Only a dropped socket after stream start is retried**
**Must understand** why retrying everything cost two pipeline runs, two LLM spends against a 500/day ceiling, and two audit rows with nothing marking either as a retry
**Capability** Explain the client's failure taxonomy and why each class is terminal or not.

## DAY 40 — The render boundary, and dead code
**Weight** H · **Prerequisites** D39, D30, D34
**Concepts** single path-aware boundary · omit vs substitute · zero UI-hallucination · **dead code identification** · evidence vs inference
**Files** `page.tsx:composeDocumentBody` · `WorkingPaperHeader.tsx` · `EvidenceList.tsx` · and, as a case study: `components/AnswerCard.tsx`, `ConfidenceBadge.tsx`, `CorpusPanel.tsx`
**Hands-on**
1. Pick a rendered value and trace it back to the backend field that produced it. Find one field the UI **omits** rather than substitutes, and say what a substitute would have implied.
2. **The dead-code exercise.** Establish by grep that three components are unreachable. Read `git log` and `git show 945b7d4`. Then separate, explicitly: what the repository **proves**, what it **suggests**, and what is **unknowable from it** (KU-004). Decide whether deletion would be safe, and list what you would check first.
**Inspect** `page.tsx:composeDocumentBody` · `components/AnswerCard.tsx` · `CAVEAT-026` · `KU-004`
**Must remember** `composeDocumentBody()` is the **only** function aware of path/engine internals · omit rather than substitute · glass/blur is permitted in exactly one component (`QueryDock`)
**Must understand** how components become unused; why teams leave dead code temporarily; how to determine deletion is safe (dynamic imports, string-keyed maps, open branches); **what git history can establish and what it can only suggest** — no story is manufactured about *why* these three are unreferenced, because the repository does not contain one
**Capability** Identify dead code, and state your confidence separately from your evidence.

## DAY 41 — Auth state, upload, admin
**Weight** M · **Prerequisites** D40, D9, D7
**Concepts** client session storage · token expiry handling · multipart upload · pre-ingestion gate · admin views
**Files** `lib/auth.ts` · `LoginForm.tsx` · `UploadPanel.tsx` · `UploadHistoryTable.tsx` · `AuditLogTable.tsx` · `api/documents.py` · `ingestion/gate.py`
**Hands-on** Log in as each of the three roles and compare what renders. Upload a non-filing PDF and watch the gate reject it with its score and matched categories.
**Inspect** `lib/auth.ts` · `ingestion/gate.py` · `api/documents.py`
**Must remember** the JWT lives in `localStorage` (CAVEAT-011) · upload is admin-only · ingestion is **not** auto-triggered
**Must understand** the localStorage-vs-httpOnly-cookie trade-off as *stated in the code*; why the gate is deterministic keyword scoring and not an LLM call
**Capability** Explain the full upload lifecycle, and why it stops at `pending`.

---

# PHASE 12 — PRODUCTION ENGINEERING (Days 42–45)

## DAY 42 — The Prompt Shield and the security model
**Weight** M · **Prerequisites** D9, D14, D35
**Concepts** direct vs indirect prompt injection · matching structure not keywords · regulatory refusal · defence in depth · threat/defence/limitation
**Files** `engines/prompt_shield.py` · `docs/security/SECURITY_MODEL.md`
**Textbook** Part 15 "Prompting" **EXTENDS**
**Hands-on** `"Should I buy Zomato?"` blocks; `"What did Zomato buy?"` passes. Work out what the pattern is actually matching. Then find a **false positive** (CAVEAT-021).
**Inspect** `prompt_shield.py` · `SECURITY_MODEL.md`
**Must remember** pure regex, no LLM, no network, runs on **every** query including cached ones · two categories: SEBI advice, and injection/jailbreak
**Must understand** why matching the *advice-request structure* beats matching the word "buy"; which injection class is undefended and why the architecture still bounds the damage
**Capability** State each threat, its defence, **and its limitation**.

## DAY 43 — Evaluation
**Weight** H · **Prerequisites** D30, D34, D37
**Concepts** golden dataset · exact-value assertion · category scoring · integrity gates · quota discipline · a check satisfied by absence
**Files** `golden_dataset/` (91 questions) · `scripts/eval_runner.py` · `scripts/regression_check.py` · `backend/tests/`
**Textbook** 10.8 **DIVERGES** — RAGAS explicitly rejected
**Hands-on** Run the pytest suite (**expect 218 passed / 25 errors** — CAVEAT-025, and know why before you run it). **Read** an existing `eval_results/*.json`: print row count, pass count, provider set, reranker set and mtime **before** looking at the score. **No sweep without explicit approval.**
**Inspect** `golden_dataset/q_titan.json` · `eval_runner.py` (docstring + scoring) · `tests/conftest.py`
**Must remember** **never run `eval_runner.py` without per-run approval** · `--delay 45` · largest dataset first as a gate · **report, do not interpret** · `eval_results/*.json` are **not baselines**
**Must understand** why RAGAS was rejected for exact-value assertions; why failure at a **fixed position** is a quota signature while a real defect fails **by category**; why the conftest patches `psycopg2.connect` by name as well as `socket`
**Capability** Read a result file and know whether to trust it — before reading the number.

## DAY 44 — Observability and debugging by layer
**Weight** H · **Prerequisites** D43, D19
**Concepts** audit lineage · append-only as a grant · aggregate metrics · a metric with no producer · single-line logging · reasoning backwards from a symptom
**Files** `engines/audit_writer.py` · `api/metrics.py` · `docs/engineering/DEBUGGING_GUIDE.md`
**Textbook** **Part 15 + 15B** **CONFIRMS** · Part 17 step 1 (cache) **DIVERGES**
**Hands-on** Run one query. Find its `audit_log` row. Reconcile **every** column against what you observed. Then find `cache_hit_rate_pct` returning 0.0 and read why it ships anyway.
**Inspect** `audit_writer.py` · `api/metrics.py` · `DEBUGGING_GUIDE.md`
**Must remember** the audit row is written **even for blocks and refusals** · audit failure never blocks the response · Render truncates multi-line tracebacks — log single-line with pgcode
**Must understand** **an empty candidate set is a network signature; a low-scoring one is a retrieval signature** — establish which before theorising; why a metric with no producer is recorded as open debt rather than deleted
**Capability** Given a wrong answer, **name the responsible layer** before touching any code.

## DAY 45 — Deployment and the ceiling
**Weight** M · **Prerequisites** D1, D44
**Concepts** image build · thread pools · memory limits · secrets · free-tier composition · what changes at 10×
**Files** `docker-compose.yml` · `backend/Dockerfile` · `frontend/Dockerfile` · `.env.example` · `app/worker.py`
**Hands-on** Explain each of the six thread-limiting `ENV` lines in `backend/Dockerfile`. Then list every architectural decision traceable to 512 MB.
**Inspect** `backend/Dockerfile` · `docker-compose.yml` · `.env.example`
**Must remember** `OMP_NUM_THREADS=1` and five siblings exist to stop 64-core thread pools OOMing a 512 MB container · one uvicorn worker · **cloud credentials flow only through `env_file`, never an `environment:` block**
**Must understand** how a hard RAM ceiling produced Cohere-as-primary, offline ingestion and `BATCH_SIZE = 8`; why "stacked free tiers do not compose into reliability"
**Capability** Explain what would have to change to serve 100 tenants.

---

# PHASE 13 — CAPSTONE (Days 46–47)

## DAY 46 — The master trace, from memory
**Weight** H · **Prerequisites** all
**Deliverable** **You write `docs/architecture/MASTER_REQUEST_TRACE.md`** — browser → rendered answer, every arrow carrying its file, function, input, output, why it exists, and its failure mode. Written **without the repository open**. Then checked against the code, with every gap marked and fed back into `LEARNING_PROGRESS.md` Part 3.
**Textbook** Part 17 **CONFIRMS** — compare your trace to the textbook's generic master flow and account for every difference
**Capability** The document is the proof.

## DAY 47 — Failure drills, roads not taken, viva
**Weight** H · **Prerequisites** D46
**Part 1 — backwards reasoning.** Symptoms from `BUGS_AND_LESSONS.md` with the answers hidden. *"The answer is hallucinated."* *"Login fails intermittently."* *"Retrieval is irrelevant."* *"The same query gives two different tiers."* Name the layer, then the file, then the check.
**Part 2 — roads not taken.** **Textbook Parts 11 (Agentic), 12 (Graph), 13 (Multimodal)** — **DIVERGES**, all three. What LedgerMind deliberately did not build, what each would have bought, and what each would have cost. Determinism over agency; flat retrieval before knowledge graphs; positional extraction over captioning.
**Part 3 — design the next one.** Given everything: what would you keep, what would you change, and what would you measure first?
**Capability** Explain the system, its trade-offs, and its limits — at implementation and at architecture level, without the repository open.

---

## Maintaining this file

The day count is derived from the codebase, not chosen. If a day proves too
large in practice, **split it and renumber** — do not compress it. Record any
change here and in `LEARNING_PROGRESS.md`, and say why.

Refinements so far:

| Date | Change | Reason |
|---|---|---|
| 2026-08-23 | `financial_extractor.py` moved from Phase 6 to Day 31; `entity_resolver.py` split across Days 31 and 36 | The first audit pass left the repo's largest undocumented file unassigned. Its job is registry-anchored record production, so it belongs with the registry, and `entity_resolver` genuinely has two halves used by two different subsystems |
