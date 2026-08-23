# LedgerMind — Technology Stack Foundations

One entry per technology **actually used** by this repository. Nothing here is
included because it is popular; if it is not in `requirements.txt`,
`package.json`, `docker-compose.yml` or the source, it is not here.

Each entry follows the same shape, so entries are comparable:

> what it is · what problem it solves · what came before · why LedgerMind uses it
> · where it lives · what it talks to · data in · data out · concepts · commands
> · files · common mistakes · trade-offs

**Mental models are included where one exists.** A mental model is a sentence
you can carry without the file open. Where a technology has no good one, none is
invented.

Cross-references: `[Day N]` is the course day that teaches it.
`[MENTAL MODEL]` marks the sentence worth memorising.

---

# 1. Docker & Docker Compose · [Day 1, 45]

**What it is.** A way to package a program with its entire operating-system
environment (an *image*), and run copies of it (*containers*). Compose runs
several containers together from one declarative file.

**What problem it solves.** "Works on my machine." A container carries its own
Python version, system libraries and environment, so the thing that runs on your
laptop is byte-identical to the thing that runs elsewhere.

**What came before.** Manual setup documents; then virtual machines, which
solved isolation but weighed gigabytes and booted in minutes. Containers share
the host kernel, so they are megabytes and start in seconds.

**Why LedgerMind uses it.** Seven interdependent services — Postgres, Redis,
Qdrant, backend, frontend, Celery worker, Celery beat — with startup ordering
and health gates. Doing that by hand is not reproducible.

**Where.** `docker-compose.yml` · `backend/Dockerfile` · `frontend/Dockerfile`

**Talks to.** Everything. It *is* the local environment.

**In / out.** In: a Dockerfile, an `.env`, a compose spec. Out: running services
on mapped ports.

**Concepts.** image vs container · layer caching · bind mount vs volume ·
`env_file` vs `environment` · `depends_on` with `condition: service_healthy` ·
port mapping · health check

**Commands.**
```bash
docker compose up -d --build      # the only correct way to run this stack
docker compose ps                 # what is actually running
docker compose logs -f backend    # follow one service
docker compose exec -T backend <cmd>
docker compose up -d --force-recreate backend
```

**Files.** `docker-compose.yml` (7 services) · `backend/Dockerfile` (6 thread-limiting `ENV` lines) · `frontend/Dockerfile`

**Common mistakes.**
- Running a local `uvicorn` alongside the container. Has caused multi-hour false-regression chases. Check `lsof -i :8000`.
- Assuming `up -d` means "ready". It returns when the container **starts**, not when uvicorn serves. Poll `/health`.
- Forgetting that `./backend:/app` is a bind mount — `docker compose cp` into `/app` writes **into your working tree**. Check `git status` afterwards. Container scratch belongs in `/tmp`, which is not mounted.
- Overriding cloud credentials in an `environment:` block. That exact override invalidated a week of measurements. Credentials flow through `env_file` only.
- On Git Bash for Windows, `-w /app` is path-rewritten and exec fails with *"Cwd must be an absolute path"*. Prefix with `MSYS_NO_PATHCONV=1`.

**Trade-offs.** Reproducibility and isolation, paid for in a build step, disk,
and a layer of indirection between you and the process. For a seven-service
stack that is clearly worth it; for a single script it would not be.

> **[MENTAL MODEL]** An image is a **recipe**; a container is a **meal cooked
> from it**. Changing the kitchen (`--force-recreate`) throws away anything you
> left on the counter.

---

# 2. Git · [Day 2, 40]

**What it is.** A content-addressed history of your files, where each commit is
a full snapshot plus a parent pointer.

**What problem it solves.** Not backup — **attribution over time**. What
changed, when, by whom, alongside what else, and why.

**What came before.** Copied folders named `final_v2_REAL`. Then centralised
systems (SVN) where history lived on a server and branching was expensive.

**Why LedgerMind uses it.** In this project git is a **diagnostic instrument**.
`CLAUDE.md` §8: when local and production disagree, run `git status --short` and
`git log --oneline origin/main -1` *before* forming any code theory — every
"works locally, not in prod" has traced to an unpushed file.

**Where.** `.git/` · discipline documented in `CLAUDE.md` §3

**Concepts.** working tree vs staging area vs commit · diff · rename detection ·
`log -- <path>` · commit granularity as a design choice

**Commands.**
```bash
git status --short
git diff --stat                   # BEFORE every commit, every time
git diff --cached --stat          # what is actually staged
git log --oneline -40
git show --stat <hash>
git log -- <path>                 # this file's history alone
git log --reverse --date=short --pretty='%ad %h %s' | head
```

**Common mistakes.**
- `git add` on an **unmodified** file stages nothing and the commit is a silent no-op. This is why `git diff --stat` comes first, every time.
- Batching unrelated changes into one commit — it destroys the ability to attribute a regression to a change.
- Treating history as proof of *intent*. It records what changed, not why, unless the message says so. See `KU-004`.

**Trade-offs.** One commit per file gives clean attribution and a longer
history. This project chose attribution.

> **[MENTAL MODEL]** Git is a **lab notebook**, not a backup drive. A commit
> message that does not say *why* wastes the entry.

---

# 3. HTTP · [Day 4]

**What it is.** The request/response protocol of the web. A client sends a
**method** + **URL** + **headers** + optional **body**; a server returns a
**status code** + **headers** + optional **body**.

**What problem it solves.** A universal contract between programs that know
nothing about each other's internals.

**What came before.** Bespoke socket protocols, each needing its own client.

**Why LedgerMind uses it.** The browser must reach the API and neither can share
memory with the other.

**Where.** Everything under `app/api/` and `app/auth/`; every call in
`frontend/lib/api.ts`.

**Concepts.** methods (`GET`/`POST`/`PUT`/`PATCH`/`DELETE`) · status classes
(2xx success, 4xx *your* fault, 5xx *ours*) · headers · content type · CORS ·
Server-Sent Events

**Commands.**
```bash
curl -i http://localhost:8000/health
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"...","password":"..."}'
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/metrics
curl -N -X POST http://localhost:8000/api/query/stream -H "..." -d '...'
```

**Common mistakes.** Confusing 401 (who are you?) with 403 (I know who you are,
and no). Confusing 422 (validation) with 400 (malformed). Forgetting `-N` on
`curl` for a stream and wondering why nothing appears.

> **[MENTAL MODEL]** A request is a **letter**: method = what you are asking for,
> URL = the address, headers = the envelope, body = the contents.

---

# 4. FastAPI · [Day 4–6]

**What it is.** A Python web framework that derives validation, serialisation
and OpenAPI documentation from type hints.

**What problem it solves.** Writing a typed HTTP layer without hand-writing
parsing, validation and docs three times.

**What came before.** Flask (no typing, manual validation), Django (batteries
included, heavier, sync-first).

**Why LedgerMind uses it.** Async native — the pipeline makes network calls to
Qdrant, Gemini and Cohere. Pydantic integration makes the request contract
executable. `Depends` makes auth declarative and impossible to forget on a route.

**Where.** `app/main.py` · `app/api/*.py` · `app/auth/router.py`

**Talks to.** The frontend over HTTP; the graph below it.

**In / out.** In: HTTP request. Out: JSON, or an SSE stream.

**Concepts.** path operation · dependency injection · `Depends` · `APIRouter` ·
`StreamingResponse` · middleware vs dependency

**Files.** `main.py` (108) · `api/query.py` (233) · `api/documents.py` (184) ·
`api/metrics.py` (131)

**Common mistakes.**
- Putting auth in middleware, where it cannot be selectively applied or typed. This repo uses a dependency instead.
- Forgetting that a dependency runs **before** the handler body, so raising inside it means the handler never executes at all.
- Configuring logging after `app.*` imports — import-time INFO logs then fall through to `logging.lastResort` (fixed at WARNING) and vanish silently. Both entrypoints configure logging **first**, with `force=True`.

**Trade-offs.** Type-hint magic is excellent until you need to know exactly what
it did; then you read FastAPI's source. Accepted for the validation and docs.

> **[MENTAL MODEL]** `Depends(f)` means: **"run `f` first, and if it raises, my
> function never happens."**

---

# 5. Pydantic v2 · [Day 5, 18]

**What it is.** Runtime validation and serialisation driven by type annotations.

**What problem it solves.** Data crossing a boundary — a network, an LLM — is
untrusted. Pydantic makes the shape check declarative and the failure loud.

**What came before.** Hand-written `if not isinstance(...)` checks, scattered
and inconsistent.

**Why LedgerMind uses it.** Two distinct jobs: the HTTP contract, **and** the
LLM output schema. `RouterResponse` and `GeminiDSLResponse` are Pydantic models
sent to Gemini as a `response_schema`.

**Where.** `auth/schemas.py` · `api/query.py:QueryRequest` ·
`router.RouterResponse` · `quant_engine.GeminiDSLResponse` · `core/config.py`

**Concepts.** `BaseModel` · `Optional` · `Literal` · `model_validate_json` ·
`model_json_schema()` · `BaseSettings` · `extra="ignore"`

**Common mistakes.** Assuming a validated model is a *correct* one. Gemini's
`response_schema` guarantees the **shape**; it says nothing about whether the
metric named is the metric asked for. That gap is why three regex guards exist.

**Trade-offs.** **The schema is model input.** Declaring a field changes the
prompt on both providers, whether or not any prompt text mentions it. See
`IMPLEMENTATION_DELTAS.md` §D. This is the least obvious fact on this page.

> **[MENTAL MODEL]** A Pydantic model at an LLM boundary is **a form the model
> must fill in** — and handing someone a form changes what they write, even if
> you say nothing.

---

# 6. JWT & bcrypt · [Day 7–8]

**What it is.** **bcrypt**: a deliberately slow password hash with a built-in
salt. **JWT**: a signed, base64-encoded JSON claims object.

**What problem it solves.** bcrypt: a stolen database must not yield passwords.
JWT: the server must recognise you on request 2 without storing session state.

**What came before.** Plain-text passwords, then fast hashes (MD5/SHA-1) that
GPUs brute-force. For sessions: server-side session tables, which do not scale
horizontally without shared state.

**Why LedgerMind uses it.** Stateless auth suits a single Render instance that
may restart. `bcrypt` is called **directly, not via passlib** — passlib's
`CryptContext` reads `bcrypt.__about__.__version__`, removed in bcrypt ≥ 4.1,
which breaks it on any current install. Documented in `core/security.py`. **Do
not re-add passlib.**

**Where.** `core/security.py` · `auth/service.py` · `auth/dependencies.py`

**In / out.** In: email + password. Out: an HS256 token carrying
`sub`, `tenant_id`, `role`, `iat`, `exp` (+2 h).

**Concepts.** salt · cost factor · one-way hashing · claims · signature vs
encryption · expiry · bearer scheme

**Common mistakes.**
- **Believing a JWT is encrypted. It is not.** Anyone holding it can read every claim; they simply cannot forge one. Never put a secret in a claim.
- Trusting a claim without verifying the signature.
- Storing the token where any script on the page can read it — this repo does exactly that (`localStorage`), and records it honestly as `CAVEAT-011` rather than pretending otherwise.

**Trade-offs.** Stateless means **you cannot revoke a token** before it expires.
Mitigated by a 2-hour lifetime. At scale you would add a revocation list, and
lose some of the statelessness that motivated the choice.

> **[MENTAL MODEL]** A JWT is **a signed ID card**. The server checks the
> signature, not a database. Anyone can read the card; nobody can forge it.

---

# 7. PostgreSQL · [Day 13–16]

**What it is.** A relational database: typed tables, constraints, transactions,
and a query planner.

**What problem it solves.** Storing facts such that they cannot become
internally inconsistent, and querying them exactly.

**What came before.** Files, then key-value stores — neither of which can
enforce "this value must be one of these two strings" or "these two writes
happen together or not at all".

**Why LedgerMind uses it.** The whole promise is **exact numbers**. A financial
figure must be `NUMERIC`, not a float; a `financial_type` must be
`consolidated` or `standalone` and nothing else; a restatement must retire the
old row and insert the new one atomically. Vector databases cannot do any of
that.

**Where.** `sql/init.sql` · 17 migrations · `db/session.py` ·
`quant_engine._execute_sql` · `db_loader.py` · `audit_writer.py`

**Talks to.** The quant engine, the audit writer, auth, the metrics endpoint,
the ingestion loader.

**Concepts.** table · PK/FK · `CHECK` · `NUMERIC` vs `FLOAT` · transaction ·
`SET LOCAL` · **Row-Level Security** · `FORCE ROW LEVEL SECURITY` · partial
unique index · `SELECT … FOR UPDATE` · `ON CONFLICT` · `IS NOT DISTINCT FROM`

**Commands.** No `psql` in the backend image — it is a Python container. Query
through Python:
```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur = c.cursor()
cur.execute(\"SET app.tenant_id = %s\", ('<uuid>',))
cur.execute('SELECT count(*) FROM financials'); print(cur.fetchone())"
```

**Files.** `sql/init.sql` · `sql/migrations/*.sql` (17) · `db/session.py` ·
`ingestion/db_loader.py` (743)

**Common mistakes.**
- **Forgetting `SET app.tenant_id`. RLS then silently returns 0 rows, and 0 rows reads as "no data".** This is the single most common silent failure here.
- Using `SET` instead of `SET LOCAL` on a pooled connection — one tenant's setting leaks into the next request.
- Writing the policy as `setting IS NOT NULL AND tenant_id = setting::uuid`. SQL's `AND` does not short-circuit. The policies use an explicit `CASE`.
- Trying to apply a migration as `ledgermind_app`. It is `NOSUPERUSER`; you get *"must be owner of table"*. Write the `.sql` and stop.

**Trade-offs.** Raw psycopg2 rather than an ORM: more SQL to read, but the SQL
*is* the thing being reasoned about, and "SQLAlchemy adds nothing for flat
record inserts". A per-statement connection is simple and wasteful (`CAVEAT-013`).

> **[MENTAL MODEL]** RLS is **not a `WHERE` clause the database adds for you**.
> It is a gate that returns *nothing* when it does not know who you are.

---

# 8. Qdrant · [Day 21, 27]

**What it is.** A vector database: stores high-dimensional vectors with
attached payloads and returns the nearest ones to a query vector, fast.

**What problem it solves.** Comparing a query against a million vectors by brute
force is too slow for a live request. Approximate nearest-neighbour indexes make
it milliseconds.

**What came before.** FAISS (a *library*, no persistence, no filtering);
Elasticsearch (excellent lexical search, no native dense vectors).

**Why LedgerMind uses it.** Three things together, which few alternatives offer:
**dense and sparse vectors in one point**, **native RRF fusion**, and **payload
pre-filtering inside each prefetch leg**. That last one is a correctness
property, not a performance one.

**Where.** `ingestion/qdrant_writer.py` (write) · `engines/retriever.py` (read)

**In / out.** In: `PointStruct` with `dense` + `sparse` vectors and a full
metadata payload. Out: scored points with payloads.

**Concepts.** collection · named vectors · payload · payload index · HNSW ·
`Prefetch` · `FusionQuery(Fusion.RRF)` · `MatchValue` vs `MatchAny` · upsert

**Files.** `qdrant_writer.py` (399) · `retriever.py` (574) ·
`scripts/create_qdrant_collection.py`

**Common mistakes.**
- Filtering **after** retrieval instead of inside it — a security risk as well as a relevance one.
- Reading a `reranker_score` without knowing which backend produced it.
- **`UserWarning: Api key is used with an insecure connection`** means you are on local Docker Qdrant, not Cloud. Every measurement in that session is invalid.
- **`UserWarning: Failed to obtain server version`** means the client failed its construction-time probe; the next query in that process will die.

**Trade-offs.** Cloud-hosted means a network hop in the request path (measured
0.36–0.41 s warm) and a dependency on someone else's uptime. Self-hosting would
remove both and add operational burden and RAM this project does not have.

> **[MENTAL MODEL]** Qdrant stores **geometry, not meaning**. All the meaning is
> in the embedding model. A great vector DB cannot rescue a poor embedding.

---

# 9. Embeddings & reranking · [Day 20, 25, 26, 28]

**What it is.** **Embedding (bi-encoder):** text → a fixed-length vector, where
distance approximates semantic similarity. **Reranking (cross-encoder):** a
query and one document read **together**, producing one relevance score.

**What problem it solves.** Keyword search misses paraphrases. Embeddings match
meaning. But bi-encoders never see query and document together, so they miss
fine distinctions — cross-encoders fix that, at a cost that only a shortlist can
afford.

**What came before.** TF-IDF and BM25 alone: exact and fast, blind to synonyms.

**Why LedgerMind uses it.** Both, deliberately. Dense retrieval finds *"risks
around quick commerce"*; BM25 finds *"PPBL"* and `ETERNAL`. Neither alone is
sufficient for financial text, which is dense with proper nouns **and**
paraphrase.

**Where.** `embedder.py` (ingest side) · `retriever.py` (query side)

**Models.** `BAAI/bge-small-en-v1.5` (384-dim dense, fastembed ONNX) ·
`Qdrant/bm25` (sparse) · Cohere `rerank-english-v3.0` (**primary** reranker) ·
`Xenova/ms-marco-MiniLM-L-6-v2` (ONNX fallback reranker)

**Concepts.** cosine similarity · bi-encoder vs cross-encoder · top-k · RRF ·
**score calibration** · near-duplicate suppression

**Common mistakes.**
- Using different embedding models for documents and queries. Numerically valid, semantically meaningless, and **no error is raised**.
- **Reading a reranker score without its backend.** Cohere returns `[0,1]`; local ONNX returns logits around `[-12,+2]`. One threshold pair applied to both classified every Cohere-served query as "high confidence" (BUG-001). There are now two pairs, and `reranker_backend` ships on the wire.
- Batching all chunks into one embed call. `BATCH_SIZE` was reduced 32 → 8 after OOM at 1999+ chunks.

**Trade-offs.** Cohere as primary costs a network hop and a third-party
dependency, and buys 0 MB of local RAM inside a 512 MB ceiling. The fallback
keeps the system alive and **changes the meaning of every score it produces** —
which is why the backend is recorded rather than assumed.

> **[MENTAL MODEL]** A bi-encoder describes two people separately and compares
> the descriptions. A cross-encoder **puts them in a room together**. The second
> is better and does not scale.

---

# 10. Gemini & Groq · [Day 17–19]

**What it is.** Hosted large language models behind an HTTP API. Gemini is
primary; Groq is the failover.

**What problem it solves.** Classifying a question, emitting a structured DSL
object, and writing grounded prose — three things deterministic code cannot do.

**Why LedgerMind uses it.** Free tier, fast, and **structured output support**.
Gemini's `response_schema` guarantees the output parses into the Pydantic model.

**Where.** `app/llm/client.py` — **the only place an LLM call is made.** Three
call sites use it: router classification, DSL generation, semantic synthesis.

**Concepts.** system vs user message · temperature · `response_mime_type` +
`response_schema` · timeout · failover trigger · RPM vs daily quota ·
provider attribution

**Commands.**
```bash
docker compose exec -T backend printenv GEMINI_MODEL   # NO default; must be set
```

**Common mistakes.**
- Defaulting the model name. `GEMINI_MODEL` has **no default and raises** if unset: on 2026-07-31 two full sweeps were reported under a model that never served a single call.
- Assuming the fallback is equivalent. Groq offers only `json_object`, which guarantees valid JSON, **not the requested shape** — so the Groq path validates against the model itself and treats a miss as a *provider failure*.
- Treating the free tier as reliable. 5 RPM and 500 requests/day, shared with everything else that day. A semantic question makes **two** calls.

**Trade-offs.** Structured output is not symmetric across providers. The timeout
was raised 8 s → 20 s after measurement showed calls routinely exceed 8 s and a
tight bound was *slower* overall (a timeout costs the full 8 s **and then** a
Groq call). Correctness over latency; outages are rare.

> **[MENTAL MODEL]** The LLM is **a very good reader and a very bad
> accountant.** Let it read; never let it count.

---

# 11. LangGraph · [Day 35]

**What it is.** A library for expressing a workflow as a state machine: nodes
that receive and return a shared state, edges (some conditional) between them.

**What problem it solves.** A branching pipeline written as nested `if`s cannot
be drawn, streamed, or reasoned about. As a graph it can be all three.

**Why LedgerMind uses it.** Three paths, two early exits, and a requirement that
the trace be a **byproduct of real execution** rather than instrumentation a node
could forget. `graph.astream(..., stream_mode="updates")` gives node boundaries
for free.

**Where.** `engines/graph.py` (assembly) · `engines/state.py` (the state)

**Concepts.** `StateGraph` · node · `add_conditional_edges` · entry point ·
`compile()` · `astream("updates")`

**Common mistakes.**
- Reaching for agent abstractions. This repo uses `StateGraph` + `TypedDict` **only** — the stable subset — and says so in the module docstring.
- Rebuilding the graph per request. It is compiled once into a module singleton.
- Assuming every path reaches the tail. `blocked` and `refused` go straight to `audit_writer`, because `confidence_node` would otherwise **rescore a refusal**.

**Trade-offs.** A dependency, and a small vocabulary to learn, in exchange for an
inspectable topology and free streaming. The mitigation for lock-in is using as
little of the API as possible.

> **[MENTAL MODEL]** A **relay race with one baton**. Each runner may write on
> the baton. Two runners can hand it straight to the finish line.

---

# 12. Celery & Redis · [Day 45]

**What it is.** Celery runs Python functions out-of-process; Redis is the queue
between submitter and worker.

**Why LedgerMind uses it.** Ingestion must not run inside the web process —
loading the embedding model there OOM-killed Render's 512 MB tier (`Exited with
status 137`).

**Where.** `app/worker.py` (Celery app) · `ingestion/pipeline.py` (the task) ·
compose services `worker` and `scheduler`

**Common mistakes.**
- Expecting Redis to be a cache here. **It is the broker only.** The semantic cache in the blueprint was never built; `cache_hit` has no producer and `cache_hit_rate_pct` returns a permanent 0.0 — recorded as open debt, not deleted.
- Expecting upload to trigger ingestion. It records a `pending_uploads` row; `scripts/process_pending_uploads.py` does the work.

**Trade-offs.** Two more containers and a serialisation boundary, in exchange
for the web process surviving. On this tier that is not optional.

> **[MENTAL MODEL]** Redis here is a **noticeboard**, not a memory. Things are
> pinned to it and taken down; nothing is kept.

---

# 13. pdfplumber · [Day 22]

**What it is.** A PDF text extractor that also exposes **word-level x/y
positions** and can reconstruct table structure.

**What problem it solves.** A PDF has no concept of a table — only glyphs at
coordinates. Recovering "this number belongs to that row and that period column"
is a geometry problem.

**Why LedgerMind uses it.** Positions are non-negotiable. A financial statement's
meaning lives in its column layout, and a plain text dump destroys it.

**Where.** `ingestion/pdf_parser.py` (677) · `ingestion/pdf_text.py`

**Concepts.** page · word box · `extract_tables()` · `extract_text()` · column
centres · tolerance · OCR artefacts

**Common mistakes.**
- **Parsing the same PDF twice in one script. It exhausts WSL RAM and restarts the distro.** Parse once, reuse; tee output to `/tmp` and grep the file.
- Trusting extracted text without inspecting it. `TYPO_MAP` exists because real filings render `III` as `Ill` and `interest` as `I nterest`.
- Assuming a failed page is an empty page. A page whose column layout fails to parse is currently skipped **silently** (`CAVEAT-003`).

**Trade-offs.** Positional extraction is fragile and precise. Captioning tables
into prose (the textbook's usual production advice) would be robust and would
destroy the exact-value guarantee. This project chose precision and pays for it
in extraction-correctness work — roughly two-thirds of all commits.

> **[MENTAL MODEL]** A PDF is **a picture of a document**, not a document.
> Everything else follows from that.

---

# 14. Next.js & React · [Day 38–41]

**What it is.** React builds UI from composable components with local state.
Next.js adds routing, build tooling and rendering strategy.

**Why LedgerMind uses it.** The working-paper document model needs real layout
control. Recorded in `ED-023`; Streamlit was the blueprint's choice and was
deliberately superseded.

**Where.** `frontend/app/` (2 files) · `frontend/components/` (30) ·
`frontend/lib/` (3)

**Concepts.** component · props · state · `useState`/`useEffect` · state lifting
· `"use client"` · App Router · `ReadableStream`

**Common mistakes.**
- Using `EventSource` for SSE. It is GET-only and cannot set an `Authorization` header; moving the JWT into a query string would put it in server logs and browser history. This repo uses `fetch` + `ReadableStream`.
- Discarding a partial SSE frame. A chunk boundary can land mid-JSON; incomplete frames stay buffered.
- Letting a component learn which engine produced the data. **`composeDocumentBody()` is the only path-aware function**, by mandate.

**Trade-offs.** All state in `page.tsx` (584 lines) is simple and honest at this
size, and would need splitting well before it doubled.

> **[MENTAL MODEL]** The frontend is a **typesetter**, not an analyst. It
> renders what the backend established, and **omits** what the backend did not.

---

# 15. pytest · [Day 43]

**What it is.** A test runner that collects `test_*` functions and reports
pass/fail.

**Why LedgerMind uses it.** A **pure-function** suite — no network, no DB, no
LLM — that runs in seconds on a quota-exhausted day, against a
mid-migration database.

**Where.** `backend/tests/` — 12 files, 194 test functions, 243 collected.

**Concepts.** fixture · `autouse` · `monkeypatch` · parametrisation · collection
vs execution · **asserting current behaviour rather than desired behaviour**

**Commands.**
```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend \
  env PYTHONPATH=/app python -m pytest tests/ -q
```

**Common mistakes.**
- **Expecting green.** The current baseline is **218 passed, 25 errors** (`CAVEAT-025`) and has been since 2026-08-22. Compare against that, not against zero.
- Assuming patching `socket` blocks every client. It does not: psycopg2 connects through libpq in C and bypasses Python sockets entirely. The conftest patches `psycopg2.connect` **by name** as well, and says why.
- Reading a defect-asserting test as a passing feature. Several tests assert **known defects as current behaviour**, naming the audit finding in the docstring. When one starts failing, that is the fix landing.

**Trade-offs.** Pure-function scope means the suite cannot catch integration
regressions — that is `regression_check.py`'s job, and it is far more expensive.

> **[MENTAL MODEL]** This suite's job is **detecting change**, not proving
> correctness. To detect change it must first describe the present accurately —
> including the parts that are wrong.

---

## Adding an entry

Add one when a technology is *introduced to the repository*, not when it is
first mentioned. Use the same shape. Where the repository does not state a
rationale, say so — do not supply one.
