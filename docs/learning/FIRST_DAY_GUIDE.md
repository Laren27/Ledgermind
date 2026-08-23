# First Day in the LedgerMind Codebase

For someone who has just cloned this repo and wants to be productive rather
than merely oriented.

**Time budget:** about a day. Do not try to read everything — 13,000 lines of
Python and 3,300 of TypeScript. Read the right 800 lines.

---

## Before you open any code: the one idea

> A wrong answer with a ✓ tick is worse than a refusal.

Almost every design choice that will look strange to you follows from that
sentence. When you find something that seems over-engineered — three separate
regex guards before an LLM call, two threshold pairs for one score, a pure
function whose only job is to be called by both the writer and its dry-run —
ask *"what wrong answer does this prevent?"* rather than *"why is this so
complicated?"*. There is a documented, measured answer every time.

---

## Hour 1 — Run it

```bash
cp .env.example .env      # fill in GEMINI_API_KEY, JWT_SECRET at minimum
docker compose up -d --build
```

Then **wait for readiness** — `up -d` returns when the container starts, not
when uvicorn binds the port:

```bash
until docker compose exec -T backend python -c \
  "import urllib.request;urllib.request.urlopen('http://localhost:8000/health',timeout=2)" \
  2>/dev/null; do sleep 2; done; echo READY

curl -s localhost:8000/health | python3 -m json.tool
```

You want `postgres`, `redis` and `qdrant` all `ok`. Frontend at
`http://localhost:3000`.

Then run the tests, because they are fast and they will teach you the
vocabulary:

```bash
docker compose exec -T -w /app backend env PYTHONPATH=/app python -m pytest tests/ -q
```

177 tests, ~2 seconds, no network. The `-w /app` is load-bearing.

---

## Hour 2 — Read these four files, in this order

This is the whole system's spine. **840 lines total.** Do not skip ahead.

### 1. `backend/app/engines/state.py` (271 lines) — read every line

`QueryState` is the object that flows through the entire pipeline. Every field
is grouped by which node writes it. Learn this and you can read any node.

Pay attention to `record_llm_call()` at the bottom and the comment above it. It
teaches you how this project thinks: a field was wrong in production, the fix
was not "set it more carefully" but "make the wrong assignment impossible".

### 2. `backend/app/engines/graph.py` (132 lines) — read every line

The entire topology in one picture. Eight nodes, two conditional edges. Note
that **two edges bypass the tail entirely** — `blocked` and `refused` both go
straight to `audit_writer`. Ask yourself why a refusal must skip the confidence
node. (Answer in the comment at line 95.)

### 3. `backend/app/api/query.py` (233 lines) — read every line

Where a request becomes a `QueryState`. Read `execute_query` first (short), then
`execute_query_stream`. The streaming endpoint is where you learn that this
codebase thinks about failure modes most people ignore: what happens to the
audit row when the browser disconnects mid-query.

### 4. `backend/app/engines/router.py` (388 lines) — read the prompt and `router_node`

The one LLM call that decides everything downstream. Read
`ROUTER_SYSTEM_PROMPT`, then `router_node`, then the long comment block at lines
296–314 — the most honest piece of documentation in the repository. It explains
precisely why a fix is **partial**, with the measurement.

---

## Hour 3 — Follow one query all the way down

Pick the simplest possible question and trace it by hand.

> "What was ETERNAL's consolidated revenue for FY26?"

```text
frontend/app/page.tsx :: handleSubmit
  → lib/api.ts :: submitQueryStreaming
  → api/query.py :: execute_query_stream
  → auth/dependencies.py :: get_current_user          (JWT → user dict)
  → engines/state.py :: make_initial_state            (the dict is born)
  → engines/graph.py :: astream
      → prompt_shield_node        (regex, passes)
      → router_node               (LLM #1 → path="quantitative", company="ETERNAL")
      → quant_engine_node         (LLM #2 → DSL → SQL → 1 row → sql_verified=True)
      → confidence_node           (no caps apply)
      → response_generator_node   (TEMPLATE, no LLM)
      → audit_writer_node         (INSERT INTO audit_log)
  → api/response_shaping.py :: role_filtered_response
  → SSE "complete"
  → page.tsx :: composeDocumentBody
```

Now run it and watch it happen:

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"...","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -N -X POST localhost:8000/api/query/stream \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"What was ETERNAL'\''s consolidated revenue for FY26?"}'
```

You will see one event per node. **Log in as an admin** — provider, model,
timings and reranker backend are admin-tier only.

Then ask a **semantic** question ("What risk factors does Eternal disclose?")
and compare the traces. The difference between the two paths is the difference
between the two halves of this system.

---

## Hour 4 — Read the two engines

### `backend/app/engines/quant_engine.py` — read `quant_engine_node` (line 600 onward)

Skip the prompt-building at the top on a first pass. Read the numbered stages.
Notice that **Stages 0 and 0b run before any LLM call** and can refuse outright.
That ordering is the design.

Then read `dsl_compiler.py` **in full** (303 lines). It is the purest expression
of this project's philosophy: no LLM, no network, pure functions, exhaustive
validation.

### `backend/app/engines/retriever.py` — read `hybrid_search` then `rerank`

The two-`Prefetch` + `FusionQuery` call is the entire hybrid-retrieval story.
Then read the comment block above `NEAR_DUPLICATE_THRESHOLD` — it is a complete
worked example of how this project makes a decision: an observation, a
measurement, a rejected alternative with a stated reason, and a constant.

---

## What to ignore on day one

| Skip | Why |
|---|---|
| `backend/scripts/*` (37 files) | Diagnostic probes from specific investigations. Read one when you have that problem. |
| `backend/app/ingestion/pdf_parser.py`, `financial_extractor.py` | The hardest, least generalisable code in the repo — positional PDF table extraction and OCR repair. Come back when you need to fix an extraction bug. |
| `streamlit_frontend_archive/` | The superseded first UI. |
| `docs/ARCHITECTURE.md` (1318 lines) | The original blueprint, **not** a description of the system. Read `docs/architecture/LEDGERMIND_ARCHITECTURE.md` instead. |
| `docs/IMPLEMENTATION_DELTAS.md` (3522 lines) | Not linearly. It is a reference — search it when you have a specific question. |
| `frontend/components/environment/*` | Visual polish. |

---

## Concepts to learn, in dependency order

Each links to `GLOSSARY.md`.

**Tier 1 — you cannot read the code without these**
1. Embeddings and vector search
2. BM25 / lexical retrieval
3. Hybrid retrieval and RRF
4. Cross-encoder reranking
5. RAG as a whole
6. JWT authentication
7. SQL transactions and `SET LOCAL`

**Tier 2 — you cannot change the code safely without these**
8. Row-Level Security and why it fails silently
9. Chunking strategies and overlap trade-offs
10. Structured LLM output and why it does not imply correctness
11. `is_latest` / restatement modelling
12. Partial unique indexes
13. FastAPI dependency injection

**Tier 3 — specific to this project**
14. The DSL → SQL compilation model
15. Confidence tiers and why thresholds are backend-dependent
16. The three Stage-0 guards and why they read the raw query
17. Cross-path reconciliation quadrants
18. Provider attribution by precedence

---

## Before you modify anything

Read `CLAUDE.md` **in full**. It is the working agreement, and §1 is a
stop-and-ask list where a green result does not prove correctness. In summary:

**Never do these without asking:**
1. **Migrations** — you cannot apply them (`ledgermind_app` is `NOSUPERUSER`).
   Write the `.sql` wrapped in `BEGIN;/COMMIT;` and stop.
2. **Destructive data operations** — `--apply` on any purge, any re-ingest, any
   backfill. Dry runs are free; run them and stop.
3. **Measured constants** — `COHERE_HIGH` (0.5), `COHERE_MEDIUM` (0.15),
   `NEAR_DUPLICATE_THRESHOLD` (0.70), the coverage floor (0.5),
   `OVERLAP_TOKENS` (150), `BATCH_SIZE` (8). Each encodes a measurement not
   derivable from the code.
4. **Golden dataset edits** — never change an expectation to make a test pass.
5. **Prompt edits** — appended instructions have lost to earlier, more concrete
   rules in the same prompt **three separate times**.
6. **Any eval sweep** — quota is 500 calls/day; a full sweep is ~165.

**Always do these:**
- `git diff --stat` before every commit (`git add` on an unmodified file stages
  nothing and the commit is a silent no-op).
- **One commit per file.** Never batched.
- `grep -n` to verify every edit landed. AST parsing proves a file loads, not
  that your edit is in it.
- Update `docs/IMPLEMENTATION_DELTAS.md` in the **same commit** as any change
  that makes a blueprint statement untrue.

---

## Six facts about this environment that will otherwise cost you an hour each

1. **The backend container is Python only — there is no `psql` inside it.**
2. **`db_transaction()` yields a connection, not a cursor.** Use `conn.cursor()`.
3. **`ChunkResult` is a `TypedDict`** — use `chunk["text"]`, never `getattr`.
4. **Scripts run as `python -m scripts.X`**, never `python scripts/X.py`.
   `eval_runner` is the exception: it runs from the **host**, in `backend/`.
5. **`./backend:/app` is a bind mount** — the container path *is* your working
   tree. Anything the container writes to `/app` appears as untracked files.
   Container scratch goes in `/tmp`.
6. **`docker-compose.yml:51` overrides `DATABASE_URL`** to the local Postgres,
   so the running stack does **not** read the Supabase URL in `.env`. They are
   different databases with different document counts. Always state which one a
   measurement came from.

---

## Your first useful contribution

Not a feature. Pick one of these — each is small, real, and teaches you a layer:

| Task | Teaches you | Where |
|---|---|---|
| Add the missing `logger.warning` at `financial_extractor.py:785` | The extraction path, and why silent skips are the enemy | CAVEAT-003 |
| Add a test to `backend/tests/` for something currently untested | The test conventions and the network guard | `tests/conftest.py` |
| Wire `preferred_operation` through to `validate_dsl` **or** delete it | The DSL path end to end, and the UI override mechanism | CAVEAT-002 |
| Remove `cache_hit` from `MetricsResponse` | Why a field with no producer is an honesty risk | CAVEAT-009 |

Do **not** start with CAVEAT-001 (the tenant_id override) on day one. It is the
most important item in the repo, and it deserves someone who already understands
how `tenant_id` flows into RLS, the Qdrant filter and the audit row — which is
exactly what the four tasks above teach you.

---

## Where to go next

| Question | Document |
|---|---|
| What is this system? | `docs/architecture/LEDGERMIND_ARCHITECTURE.md` |
| Why is it built this way? | `docs/architecture/ENGINEERING_DECISIONS.md` |
| What actually works? | `docs/architecture/CAPABILITY_MATRIX.md` |
| What is broken or assumed? | `docs/engineering/CAVEATS.md` |
| Something is wrong, where do I look? | `docs/engineering/DEBUGGING_GUIDE.md` |
| How do I run X? | `docs/RUNBOOK.md` |
| What does this word mean? | `docs/learning/GLOSSARY.md` |
| Why does the code disagree with the blueprint? | `docs/IMPLEMENTATION_DELTAS.md` |
| What is the security posture? | `docs/security/SECURITY_MODEL.md` |
