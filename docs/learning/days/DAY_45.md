# Day 45 — Deployment, and the Ceiling

**Phase 12 · Weight: M (~90 min) · Prerequisites: Days 1, 44**

**Textbook: no citation.** The textbook deploys nothing. Today is entirely about
one number — **512 MB** — and the surprisingly long list of architectural
decisions that follow from it.

---

## 1. Today's goal

By tonight you can:

- Explain each of the **six** thread-limiting `ENV` lines in
  `backend/Dockerfile`, and what they prevent.
- Explain why there is exactly **one** uvicorn worker.
- List **every** architectural decision traceable to 512 MB — there are at least
  seven, and you have met all of them separately.
- Explain the `env_file` versus `environment:` distinction, and name the line in
  `docker-compose.yml` that currently makes every local measurement ambiguous.
- Read `/health` and say what "degraded" does and does not mean.
- Explain what Redis is used for here, and what it is **not**.
- Explain why *"stacked free tiers do not compose into reliability"*, using this
  system's own outages.
- Say what would have to change to serve 100 tenants.

---

## 2. Why now

Day 1 ran the stack. Day 44 gave you the record it leaves behind. Today closes
Phase 12 by explaining **why the stack is shaped the way it is** — and almost
every answer is a memory constraint you have already met without being told it
was the cause.

**This day is mostly recognition, not new material.** Cohere as primary
reranker (Day 28), offline ingestion (Day 41), `BATCH_SIZE = 8` (Day 24),
fastembed instead of torch (Day 20), one uvicorn worker, no cache (Day 44) —
you know all of them. **Today they become one decision seen seven times.**

---

## 3. Concepts you must know first

| Concept | From | Why it is needed today |
|---|---|---|
| Image vs container, bind mount vs volume | Day 1 | The compose file |
| `env_file` vs `environment:` | Day 1 | The override that invalidates measurements |
| Lazy singletons for expensive models | Day 12 | Why one worker matters |
| fastembed/ONNX rather than torch | Day 20 | The first RAM decision |
| Cohere primary, local ONNX fallback | Day 28 | *"0 MB local RAM"* |
| Offline ingestion, exit 137 | Day 41, ED-016 | The measured OOM |
| Redis is the Celery broker only | Day 44 | And the health check |

---

## 4. Concept lesson

### 4.1 The ceiling, and how it announces itself

Render's free web-service tier gives **512 MB of RAM**. Exceed it and the
container is **SIGKILLed** — exit status **137** (`128 + 9`).

**That is the whole constraint**, and it has three properties that make it
harsher than it sounds:

1. **There is no traceback.** SIGKILL cannot be caught. The application logs
   nothing, because the application is gone mid-instruction.
2. **It is not gradual.** No swap, no slow degradation. Fine, then dead.
3. **It takes the request path with it.** The web service serves queries. An
   ingestion job that OOMs takes live querying down with it.

Property 3 is why `api/documents.py`'s docstring is worded the way it is:

```python
# Running that step inside the same process that serves live queries is
# unsafe on this tier regardless of whether it's triggered via Celery or
# BackgroundTasks.
```

---

### 4.2 The six thread lines

```dockerfile
# 2. Constrain C++ / ONNX thread pools to prevent 64-core OOM crashes in 512MB RAM
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV ORT_INTRA_OP_NUM_THREADS=1
ENV ORT_INTER_OP_NUM_THREADS=1
ENV TOKENIZERS_PARALLELISM=false
```

**Six variables, four libraries, one problem.**

| Variable | Library | What it controls |
|---|---|---|
| `OMP_NUM_THREADS` | OpenMP | The generic parallel-region thread count. **Most of the others honour this too** |
| `MKL_NUM_THREADS` | Intel MKL | MKL's own pool, which overrides OpenMP when set |
| `OPENBLAS_NUM_THREADS` | OpenBLAS | Whichever BLAS numpy links against |
| `ORT_INTRA_OP_NUM_THREADS` | ONNX Runtime | Threads **within** one operator (a matmul split across cores) |
| `ORT_INTER_OP_NUM_THREADS` | ONNX Runtime | Operators run **concurrently** |
| `TOKENIZERS_PARALLELISM` | HuggingFace tokenizers (Rust) | Rayon's parallel tokenisation |

**The failure mode they prevent.** These libraries size their pools from
**detected core count**, not from the container's memory limit. A container on a
64-core host sees 64 cores. Each thread carries a stack and per-thread scratch
buffers. **Sixty-four ONNX intra-op threads allocate sixty-four times the
working memory of one** — and 512 MB is gone before the first embedding is
produced.

**Why so many for one idea.** Because each library reads its **own** variable and
the precedence is not uniform: MKL overrides OpenMP when set; ONNX Runtime uses
neither; the Rust tokenizer uses neither and wants a boolean. **Setting one and
assuming the rest follow is how this fails intermittently** — on the machine
where numpy happened to link OpenBLAS instead of MKL.

**And note where they live: in the image, not in compose.** They are `ENV` in the
`Dockerfile`, so they apply to **every** container built from it — backend,
worker and scheduler all share `build: ./backend`. **One place, three services.**

> **Genuine trade-off, stated:** on a large machine these lines make embedding
> *slower*. That is accepted. **A slow embedding completes; a parallel one gets
> SIGKILLed.**

---

### 4.3 One worker, and why it is not a typo

```dockerfile
# 6. Start Uvicorn constrained to 1 worker to stay safely inside free-tier memory
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

**Uvicorn workers are separate processes.** Two workers means two copies of
everything the process holds — and Day 12's lazy singletons are precisely the
expensive things:

```
bge-small-en-v1.5 (dense, ONNX)  ─┐
Qdrant/bm25 (sparse)             ─┼─ loaded lazily, held per process
ms-marco-MiniLM-L-6-v2 (rerank)  ─┘
```

**Two workers, two copies, no sharing** — the models are in-process objects, not
a shared mapping.

**And this is where Cohere earns its place.** `retriever.py` describes Cohere as
*"0 MB local RAM"*. The local ONNX cross-encoder is a genuine fallback and
genuinely fits — but only because it is loaded **once, in one process**, and only
when Cohere is unreachable.

**What one worker costs.** No parallelism across CPU cores for CPU-bound work.
FastAPI is async, so I/O-bound waiting (LLM calls, Qdrant, Postgres) still
interleaves fine — **and this workload is overwhelmingly I/O-bound**: a semantic
query spends seconds waiting on Gemini and milliseconds computing. **The
constraint costs less here than it would in most systems**, which is part of why
it is tolerable.

**The one exception is fastembed**, which is CPU-bound and in-process — so a
query embedding blocks the event loop for its duration. At 0.36–0.41 s warm,
that is real but small; at 30 s cold it is the "first query after a restart is
slow" phenomenon from Day 1.

---

### 4.4 Every decision traceable to 512 MB

**This is the day's central list.** You have met all of these; today they get one
cause.

| Decision | Where | Day | The RAM argument |
|---|---|---|---|
| **fastembed (ONNX), not sentence-transformers** | `embedder.py`, `retriever.py` | 20 | torch does not fit. Commit `45cb1b9`, *"Migrate from sentence-transformers to fastembed to prevent Render OOM crashes"* |
| **Cohere as PRIMARY reranker** | `retriever.rerank` | 28 | *"0 MB local RAM"*. ONNX is the fallback, not the default |
| **`BATCH_SIZE = 8`** | `embedder.py` | 24 | 32 caused OOM at 1999+ chunks |
| **Ingestion is offline and operator-triggered** | `api/documents.py`, ED-016 | 41 | The embedding model OOM-killed the web service. Exit 137 |
| **One uvicorn worker** | `Dockerfile` | today | Two workers, two model copies |
| **Six thread-limit ENVs** | `Dockerfile` | today | Pools sized from core count, not memory |
| **No self-hosted LLM** | `llm/client.py` | 19 | Not arguable — nothing useful fits |
| **No cache** | — | 44 | Redis is the broker; a semantic cache would hold embeddings in-process |

**Eight, and they span embedding, retrieval, reranking, ingestion, serving and
observability.** A single infrastructure number reached into six subsystems.

**Two things to take from this, and the second is the one that transfers.**

**First: a constraint you cannot negotiate becomes a design input.** Nobody
chose "Cohere for quality". They chose the reranker that consumes no local
memory, and the fallback is the one that fits. **KU-005** keeps that honest —
whether Cohere was *also* chosen for quality is **not recorded**, and the RAM
argument alone does not explain the ordering, since the ONNX model fits too.

**Second: the resulting architecture is better than the unconstrained one would
have been.** Offline ingestion is *more* correct than in-request ingestion —
it makes a slow, memory-heavy, failure-prone operation observable and re-runnable
instead of hiding it inside a web request. **The constraint forced a separation
that a bigger box would have let you skip.**

**Do not over-romanticise it.** The same constraint also produced no cache, no
rate limiting and one worker — three things that are simply missing. **Constraints
produce good architecture where they force a separation, and gaps where they just
prevent work.**

---

### 4.5 `env_file` versus `environment:` — and the line that costs measurements

`CLAUDE.md` §6:

> `QDRANT_URL` and all cloud credentials flow purely through `env_file: .env`.
> **Never override via an `environment:` block** — that exact override
> invalidated every local measurement for a week.

**The mechanics.** In Compose, `environment:` **wins** over `env_file:`. So a key
present in both takes the compose value, and `.env` is silently ignored **for
that key only** — the rest of the file still applies, which is what makes it hard
to see.

**And the live instance, which `CLAUDE.md` names explicitly:**

```yaml
backend:
  env_file:
    - .env
  environment:
    DATABASE_URL: postgresql://ledgermind_app:app_dev_pass@postgres:5432/ledgermind
    REDIS_URL: redis://redis:6379/0
    ENVIRONMENT: development
```

> **`DATABASE_URL` currently has exactly that override** (`docker-compose.yml:51`):
> the running stack reads the **local** Docker Postgres, not the Supabase URL in
> `.env`. The two are different databases with different document counts (11
> local vs 9 Supabase). **Always state which one a measurement came from.**

**Yesterday made this concrete.** Day 44's CAVEAT-028 measurement found the two
databases **disagree about DELETE grants** — so "which database?" is not only
about row counts. **A privilege check run locally proves nothing about
production.**

**Is the override wrong?** No — for `DATABASE_URL` it is arguably right: you want
local development against local Postgres. **The hazard is that it is invisible.**
Nothing warns you; `.env` still contains a Supabase URL that looks authoritative;
and `printenv DATABASE_URL` inside the container is the only way to know. **Hence
the pre-flight** (Day 44 §4.11).

**The general rule:** `environment:` for values that are **correct for local
development and wrong in production**; `env_file:` for secrets and cloud
endpoints. Never both for one key **unless you can state which wins without
looking it up.**

---

### 4.6 The seven services

```
postgres    postgres:15-alpine     :5432   healthcheck pg_isready
redis       redis:7-alpine         :6379   healthcheck redis-cli ping
qdrant      qdrant/qdrant:latest   :6333   healthcheck /dev/tcp probe
backend     build ./backend        :8000   depends_on all three healthy
frontend    build ./frontend       :3000   depends_on backend
worker      build ./backend        —       celery worker --concurrency=2
scheduler   build ./backend        —       celery beat
```

**Three of these do not exist in production.** Render runs `backend`; Vercel runs
`frontend`; Postgres is Supabase and Qdrant is Qdrant Cloud. **`worker` and
`scheduler` have no production counterpart at all** — which is consistent with
ingestion being operator-run (Day 41), and worth knowing before you look for the
Celery deployment.

**`depends_on` with `condition: service_healthy`** waits for the health checks,
not merely for the container to start. But `CLAUDE.md` §6 adds the part that
still bites:

> `docker compose up -d` returns when the container **starts**, not when uvicorn
> **serves**. Poll `/health` before minting a token, and `echo ${#TOKEN}` so an
> empty token fails loudly.

**`echo ${#TOKEN}` is the small habit worth stealing.** A failed login yields an
empty string, `curl` sends `Authorization: Bearer `, and every subsequent call
401s — which reads as an auth defect rather than as a startup race.

**Three volume decisions worth reading:**

```yaml
volumes:
  - ./backend:/app                       # bind mount — live reload
  - ./docs/raw:/app/docs/raw:ro          # source PDFs, read-only
  - ./golden_dataset:/app/golden_dataset:ro
```

The third carries its reasoning:

> **READ-ONLY, and read-only is the point.** `golden_dataset/` holds the three
> `q*.json` INPUTS and nothing else; `:ro` means nothing running in this
> container can write an eval output beside them, which is the failure that once
> left **79 outputs against 3 inputs** and crashed an anchor scan.

**A filesystem permission used to enforce a directory's meaning.** Day 37's
"scoped by placement, not by a conditional", at the infrastructure layer.

**And the bind mount has a consequence people trip over** (`CLAUDE.md` §7):

> **`docker compose cp <file> backend:/app/...` writes into the repo.** compose
> binds `./backend:/app`, so the container path and the working tree are the same
> path. Container scratch goes in `/tmp`, which is not mounted. **Check
> `git status` after either.**

---

### 4.7 `/health` — three checks, one honest verdict

```python
all_ok = all(v == "ok" for v in services.values())
return {"status": "healthy" if all_ok else "degraded", "services": services}
```

**Postgres, Redis, Qdrant — each checked independently, each reporting its own
error string.**

**Note `await asyncio.to_thread(check_postgres_sync)`.** psycopg2 is synchronous;
calling it directly in an async handler would block the event loop for the
duration of a TCP connect — and with **one worker** (§4.3), blocking the loop
blocks every concurrent request. **The thread offload is a direct consequence of
the worker count.**

**What "degraded" does and does not mean.**

| Service down | `/health` | Actually broken? |
|---|---|---|
| Postgres | degraded | **Yes** — no auth, no audit, no quantitative path |
| Qdrant | degraded | **Yes** for semantic and cross; quantitative still works |
| Redis | degraded | **No.** Nothing on the request path uses it |

**Redis being down degrades the health check and nothing else** — because Redis
is the Celery broker (Day 44), the worker has no production counterpart (§4.6),
and the semantic cache was never built.

**This is a readiness check reporting liveness of dependencies.** It is not
per-endpoint: it cannot tell you the quantitative path still works while the
semantic path does not, though the data to say so is right there in `services`.
**A more useful version would map dependencies to capabilities.** Not built, and
not a defect — just a limit worth knowing when a platform is using this endpoint
to decide whether to route traffic.

---

### 4.8 What Redis is not

`00_LEARNING_MAP.md`, in one line:

> **Redis** — Celery broker **only**. The semantic cache was never built.

**Three consequences, and you have met all three:**

1. **`cache_hit_rate_pct` is structurally 0.0** (Day 44, CAVEAT-009).
2. **The textbook's Part 17 master flow does not apply** — divergence D1.
3. **`/health` reports `degraded` for a service nothing on the request path
   uses** (§4.7).

**And a fourth that only appears today:** if a semantic cache were built, it
would need somewhere to hold embeddings and answers. Redis is deployed and would
be the obvious home — **but only in a topology where the worker exists in
production.** Today Render runs one service and there is no Redis instance
deployed beside it. **The cache is not a small addition; it is an infrastructure
change.**

---

### 4.9 Secrets, and the two URLs

**All secrets are environment variables, loaded from `.env` via `env_file`.**
`.env` is gitignored; `.env.example` carries placeholders and, unusually, **the
reasoning**:

```bash
# GEMINI_MODEL is asserted against the model the API reports at eval time --
# a score is meaningless without a stated model, so this must be accurate.

# Cohere Rerank is the PRIMARY reranker. If unset, retrieval falls back to the
# local ONNX cross-encoder -- which works, but returns raw logits on a different
# scale, so confidence thresholds switch with it.

# ADMIN_DATABASE_URL bypasses row-level security. Used by migrations and
# maintenance scripts only -- never by the request path.
```

**An `.env.example` that teaches.** The Cohere line tells you that an unset key
does not fail — it **silently changes the score scale**, which is Day 28's defect
in template form.

**Two database URLs, and the difference is a privilege boundary:**

| Variable | Role | RLS |
|---|---|---|
| `DATABASE_URL` | `ledgermind_app` — `NOSUPERUSER`, DML only | **Enforced**, with `FORCE` |
| `ADMIN_DATABASE_URL` | the owner role | **Bypassed by design** |

`SECURITY_MODEL.md` §8: *"`ADMIN_DATABASE_URL` bypasses RLS by design (migrations
and maintenance). **Its only protection is that no request-path code reads
it.**"*

**"Its only protection is that no request-path code reads it"** — a convention,
not a mechanism, and named as one. Same shape as `audit_log`'s `UPDATE` grant
(Day 44 §4.2): **a property that holds because nobody does the thing, not because
they cannot.**

**And `JWT_SECRET` has no default** — the app will not start without it.
`GEMINI_MODEL` likewise raises rather than defaulting, *"for evidential reasons,
not security ones"*: a defaulted model name would let a sweep be attributed to a
model that never served it (Day 43).

**Two known secret problems**, both recorded:

- `sql/init.sql` hardcodes `app_dev_pass`, and `docker-compose.yml` repeats it.
  Fine locally; **must never reach a deployed database.**
- **No rotation procedure is documented.**

---

### 4.10 Stacked free tiers do not compose into reliability

**The production stack, all free tier:**

| Component | Provider | Free-tier limit that has actually bitten |
|---|---|---|
| API | Render | 512 MB; **spins down when idle** → cold starts |
| Frontend | Vercel | Function timeouts; preview domains need CORS (CAVEAT-012) |
| Postgres | Supabase | Connection limits, and **a different database from local** (CAVEAT-015) |
| Vectors | Qdrant Cloud | Cluster size |
| LLM | Gemini | **5 RPM / 500 per day** — the binding constraint (Day 43) |
| Failover | Groq | Its own limits, `json_object` only |
| Rerank | Cohere | Trial-key limits; **fallback silently changes the score scale** |

**Each is individually free and individually adequate. The composition is not
reliable, and the failures are specific:**

- **Gemini's unbounded tail latency.** `SESSION_LOG.md`: the same query returned
  in 3.07 s, then **120 s** (a `curl --max-time` cut-off, still waiting), then
  3.00 s. Render logs confirmed **one** call taking 78 s, not an SDK retry. **No
  Gemini call site set a timeout**, so a slow provider blocked the request with
  no ceiling — *"On Vercel this is a hard-kill with no application hook, not a
  slow answer."*
- **Cohere flapping on WSL2.** 2026-08-02: raw socket connects to
  `api.cohere.com` succeeded **5 of 8** attempts, at random. The same query
  returned `tier=medium` on one run and `tier=high` on another, purely because a
  different backend scored it.
- **Two Postgres databases** that disagree about **document counts** (Day 1) and
  now about **grants** (Day 44).

**The fixes are the interesting part, because of their order.**
`SESSION_LOG.md` states it:

> **FIX ORDER:** (1) explicit per-call timeout via `types.HttpOptions` — converts
> an unbounded hang into a catchable exception at a chosen bound; (2) Groq
> fallback catches that exception. **Timeout first — it is a real improvement
> standalone, and without it a fallback keyed on exceptions would never fire on
> this failure mode.**

**A fallback keyed on exceptions cannot fire against a hang.** A hang throws
nothing. **The timeout is not a companion to the fallback; it is its
precondition** — Day 19's lesson, and it is in `MUST_KNOW.md`'s ten sentences for
that reason.

---

## 5. The actual LedgerMind files

```
File:  backend/Dockerfile (~30 lines)                    Tier 1 (no docstring)
Base:  python:3.11-slim
ENV:   PYTHONUNBUFFERED=1
       OMP_NUM_THREADS · MKL_NUM_THREADS · OPENBLAS_NUM_THREADS ·
       ORT_INTRA_OP_NUM_THREADS · ORT_INTER_OP_NUM_THREADS   all = 1
       TOKENIZERS_PARALLELISM=false
CMD:   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
Layers: requirements.txt copied BEFORE the app code, so a source change does
        not reinstall dependencies

File:  frontend/Dockerfile (7 lines)
Base:  node:20-alpine · corepack + pnpm --frozen-lockfile · CMD pnpm dev
Note:  runs `next dev` — a DEV server. Production is Vercel's build, not this.

File:  docker-compose.yml (~125 lines)
Services: postgres · redis · qdrant · backend · frontend · worker · scheduler
Note:  backend has env_file: .env AND an environment: block. The environment:
       block WINS. DATABASE_URL is overridden there — CAVEAT-015.
Mounts: ./backend:/app (rw) · ./docs/raw:ro · ./golden_dataset:ro

File:  .env.example (~60 lines)
Note:  documents WHY, not just WHAT. GEMINI_MODEL's evidential requirement,
       Cohere's silent scale switch, ADMIN_DATABASE_URL's RLS bypass.

File:  backend/app/worker.py (48 lines)
Note:  opens with basicConfig(force=True) BEFORE any app.* import — the same
       reasoning as main.py, because the worker never imports main.py and
       every import-time INFO log was being discarded.
Tasks: tasks.ping only. Ingestion is NOT wired to Celery in production.

File:  backend/app/main.py — CORS, four routers, /health
Note:  logging.basicConfig BEFORE the app.* imports, force=True
```

---

## 6. Deep walkthrough — `docker compose up -d --build`, and what has to be true

**Step 1 — build `./backend`.**

```dockerfile
COPY requirements.txt .
RUN pip install -v --no-cache-dir -r requirements.txt
COPY . .
```

**`requirements.txt` is copied and installed before `COPY . .`** — so editing a
`.py` file invalidates only the last layer. Reordering these two lines turns a
five-second rebuild into a five-minute one.

**`--no-cache-dir`** keeps pip's wheel cache out of the image. Smaller image,
and nothing is gained by caching wheels in a layer that is rebuilt rarely.

**Step 2 — the three infrastructure services start**, each with a health check.

**Step 3 — `backend` waits for `service_healthy` on all three.**

**Step 4 — the container starts.** `ENV` lines apply. `env_file: .env` loads,
then `environment:` **overrides** `DATABASE_URL`, `REDIS_URL`, `ENVIRONMENT`
(§4.5).

**Step 5 — uvicorn imports `app.main`.**

```python
logging.basicConfig(level=logging.INFO, format=…, force=True)

import asyncio
…
from app.api.documents import router as documents_router
```

**The order is load-bearing, and the comment explains it:**

> Importing `app.api.query` pulls in `app.engines.router`, which logs its
> resolved `GEMINI_MODEL` **at module scope**. With no root handler installed,
> Python falls back to `logging.lastResort` (fixed at WARNING) and the line is
> **discarded silently**. This applied to **every import-time INFO log in the
> codebase**, not just that one.

**`force=True`** so a dependency that installs a root handler cannot turn this
into a no-op later.

**`app/worker.py` carries the same block**, because the worker and scheduler
containers start at `app.worker:celery_app` and **never import `main.py`** — so
`main.py`'s `basicConfig` never ran for them. The same code logged in one
container and was silent in another.

**Step 6 — CORS.**

```python
allow_origins=["http://localhost:3000", "http://localhost:8000",
               "https://ledgermind-ypmv8v239-laren-house.vercel.app"],
allow_origin_regex=r"https://.*\.vercel\.app",
allow_credentials=True,
```

**`allow_origin_regex` matches every `*.vercel.app` domain** — every preview
deployment, and **every other Vercel user's app.** `CAVEAT-012` records it. With
`allow_credentials=True` this is a real widening; the mitigating fact today is
that the token is in `localStorage` and sent explicitly (Day 41), **not** in a
cookie the browser would attach automatically. **Move to `httpOnly` cookies —
CAVEAT-011's recommendation — and this regex becomes considerably more
dangerous.** Two caveats that interact, and neither entry mentions the other.

**Step 7 — models are NOT loaded.** Lazy singletons (Day 12). The container is
serving in a few seconds; the first semantic query pays ~30 s.

**Step 8 — the container is "up" and may not be serving.**

```bash
until curl -sf http://localhost:8000/health >/dev/null; do :; done
```

**STATE AFTER.** Seven containers. Uvicorn serving with one worker, six thread
limits, models unloaded, reading the **local** Postgres.

---

## 7. Data flow — deployment topology

```
                    LOCAL (docker compose)              PRODUCTION
┌──────────────────────────────────────┐   ┌────────────────────────────────┐
│ frontend  :3000  next dev            │   │ Vercel — next build            │
│      │ NEXT_PUBLIC_API_URL           │   │      │ NEXT_PUBLIC_API_URL     │
│      ▼ localhost:8000                │   │      ▼ https://…onrender.com   │
│ backend   :8000  uvicorn --workers 1 │   │ Render — 512 MB, spins down    │
│   env_file .env                      │   │   env vars in the dashboard    │
│   environment: DATABASE_URL ◄─ WINS  │   │   DATABASE_URL = Supabase      │
│      ├─ postgres :5432  (local)      │   │      ├─ Supabase Postgres      │
│      ├─ qdrant   :6333  (local!)     │   │      ├─ Qdrant Cloud           │
│      └─ redis    :6379               │   │      └─ (no Redis deployed)    │
│ worker    celery --concurrency=2     │   │ (no worker)                    │
│ scheduler celery beat                │   │ (no scheduler)                 │
└──────────────────────────────────────┘   └────────────────────────────────┘
        │                                              │
        └──────────────► EXTERNAL, shared by both ◄────┘
              Gemini (5 RPM / 500 per day)  →  Groq failover
              Cohere Rerank  →  local ONNX fallback (DIFFERENT SCALE)
              Supabase Storage (upload handoff)

512 MB ⇒ fastembed not torch · Cohere primary · BATCH_SIZE 8 ·
         offline ingestion · one worker · six thread limits ·
         no self-hosted LLM · no cache
```

**Note two things about the local column.** `QDRANT_URL` defaults to the local
Qdrant service in `.env.example` — so a stack brought up from the template
measures a **different, smaller collection** than Cloud, and the warning
`Api key is used with an insecure connection` is the tell (Day 44). And **there
is no Redis in production**, so the worker and scheduler are local-only
conveniences.

---

## 8. Engineering decision — build for the ceiling, and say so

**Problem.** Serve a hybrid-retrieval RAG system with reranking and two LLM
providers, on 512 MB, at zero cost.

**Decision.** Push every memory-heavy component **out of process** — Cohere for
reranking, hosted LLMs, Qdrant Cloud, offline ingestion — and constrain what is
left to a single worker with single-threaded native pools.

| Alternative | Why not |
|---|---|
| **A paid tier** | The project's stated constraint. A Render Background Worker alone is ~$7/mo (DELTAS §C) |
| **Self-hosted LLM** | Nothing useful fits in 512 MB |
| **torch + sentence-transformers** | Measured: does not fit. Commit `45cb1b9` |
| **Multiple uvicorn workers** | Multiplies model memory with no sharing |
| **Ingest in-request** | Measured: exit 137, repeatedly, taking live querying with it |
| **Local reranker as primary** | Fits, but competes with fastembed and the request path in one 512 MB process. **KU-005: whether quality also drove this is not recorded** |
| **Redis-backed semantic cache** | Would need Redis deployed in production, which it is not. An infrastructure change, not a feature |

**Trade-offs accepted.**

- **Cold starts.** Render spins down when idle; the first request pays a restart
  **and** ~30 s of lazy model loading.
- **No horizontal scale.** One worker, one process.
- **Three external dependencies on the request path** — Gemini, Cohere, Qdrant
  Cloud — each with its own limits and its own failure mode.
- **Two databases** that disagree about counts **and** grants (CAVEAT-015,
  CAVEAT-028).
- **CORS accepts every `*.vercel.app`** with credentials (CAVEAT-012).
- **No rate limiting**, anywhere.
- **A dev password in `init.sql` and compose.**
- **`worker`/`scheduler` exist locally and nowhere else**, so Celery's behaviour
  is effectively untested in production.

**Current validity.** Correct and internally consistent for a zero-cost portfolio
deployment. **Everything it gives up is written down**, which is the part that
makes it defensible.

**At 10× — and this is the honest answer, not the reassuring one.**

| Change | Why |
|---|---|
| **A paid API tier with more RAM** | Everything else follows from removing this |
| **Ingestion as its own service** | Then Celery finally exists in production, and `pending_uploads` becomes a real queue |
| **Fix CAVEAT-001 FIRST** | The body-supplied `tenant_id` override. **Before a second tenant exists**, not after (Day 42) |
| **A paid LLM tier** | 500/day is ~5 users; it is already the binding constraint on *development* |
| **Rate limiting** | Per tenant, on `/auth/login` and the query endpoints |
| **Connection pooling** | CAVEAT-013 — a new connection per statement does not survive concurrency |
| **`audit_log` partitioning + retention** | Day 44 §13 Q20, and it forces CAVEAT-028's decision |
| **Company onboarding as data** | CAVEAT-019 — today it is a code edit |
| **Multiple workers, or replicas** | Only after the model memory is out of process |

**Notice what is *not* on that list: the retrieval architecture.** Hybrid
dense+sparse, RRF, reranking, the DSL compiler, the contradiction engine — none
of it changes. **The scaling work is all infrastructure and tenancy.** That is a
good sign about the design and a fair thing to say in an interview.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `Exited with status 137` | **SIGKILL — OOM.** No traceback exists. Something loaded a model |
| Backend restarts under load | Same, intermittently. Check the thread ENVs survived |
| First query after deploy takes 30 s | Lazy model load. **Not a defect** |
| Every query slow after idle | Render spun the service down. Cold start |
| Measurements disagree with `.env` | An `environment:` block overrides `env_file:` for that key |
| Local and prod disagree | Two databases. **State which one** — and note they differ in grants too |
| `Api key is used with an insecure connection` | Local Qdrant, not Cloud. **Every measurement invalid** |
| Import-time INFO logs missing in the worker | `basicConfig` moved below the `app.*` imports, or `force=True` dropped |
| `up -d` returns, then everything 401s | The container started before uvicorn served. Poll `/health`, `echo ${#TOKEN}` |
| A file "copied into the container" appears untracked | `./backend:/app` is a bind mount. Use `/tmp` |
| `exec failed: … possible container breakout` | Stale mount namespace after `--force-recreate`. Not a security event |
| A `.env` change has no effect | `env_file` values need `--force-recreate` — **which also destroys `docker compose cp` files** |
| An unbounded hang, not a slow answer | A provider call with no timeout. **The fallback cannot fire** |

---

## 10. Hands-on experiment

### Experiment 1 — read the six ENVs from inside

```bash
docker compose exec -T backend printenv | grep -E "THREAD|PARALLELISM|UNBUFFERED" | sort
docker compose exec -T backend python -c "import os; print('cores visible:', os.cpu_count())"
```

**`os.cpu_count()` reports the host's cores**, not a limit. That number is what
the native pools would have used.

### Experiment 2 — measure the resident set

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}" | head -10
```

Then load the models and measure again:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@alpha.ledgermind.test","password":"demo1234"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "token length: ${#TOKEN}"

time curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What did management say about quick commerce?"}' > /dev/null

docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}" | head -10
```

**Compare the backend's memory before and after.** The delta is the lazy
singletons. **Now multiply it by two and ask whether it fits in 512 MB.**

### Experiment 3 — warm versus cold

```bash
for i in 1 2 3; do
  /usr/bin/time -f "run $i: %e s" curl -s -X POST http://localhost:8000/api/query \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"query":"What did management say about quick commerce?"}' > /dev/null
done
```

**Run 1 is cold; runs 2 and 3 are warm.** `CLAUDE.md` §4: a local semantic
failure **is not a defect until it reproduces on a warm process.**

### Experiment 4 — prove the override

```bash
grep -n "DATABASE_URL" .env | sed 's/:.*=.*/: (value hidden)/'
docker compose exec -T backend printenv DATABASE_URL
```

**The `.env` line and the running value differ.** Now find the line responsible:

```bash
grep -n "environment:" -A 4 docker-compose.yml | head -20
```

### Experiment 5 — `/health`, and what degraded means

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Now stop the one service nothing on the request path uses:

```bash
docker compose stop redis
curl -s http://localhost:8000/health | python3 -m json.tool
```

**`degraded`.** Then confirm the system still answers:

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What was Titan revenue in Q1FY26?"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('path:', d.get('path'), '| verified:', d.get('sql_verified'))"

docker compose start redis
until curl -sf http://localhost:8000/health | grep -q '"redis": "ok"'; do :; done; echo "redis ok"
```

**A "degraded" system that answers correctly.** That is §4.7's limit, observed.

### Experiment 6 — the read-only mount

```bash
docker compose exec -T backend sh -c 'touch /app/golden_dataset/should_fail.json' ; echo "exit=$?"
docker compose exec -T backend sh -c 'touch /tmp/fine && echo "/tmp is writable"'
git status --short golden_dataset/
```

**Expect a read-only filesystem error and a clean `git status`.** *That* is the
79-outputs-against-3-inputs failure, prevented by a mount flag.

### Experiment 7 — the bind mount writes into your repo

```bash
docker compose exec -T backend sh -c 'echo "scratch" > /app/DELETE_ME.txt'
git status --short | head -3
docker compose exec -T backend rm -f /app/DELETE_ME.txt
git status --short | head -3
```

**It appeared as untracked in your working tree.** `CLAUDE.md` §7 — check
`git status` after anything that writes to `/app`.

### Experiment 8 — the layer cache

```bash
touch backend/app/main.py
time docker compose build backend 2>&1 | tail -5
```

**Fast**, because `requirements.txt` was copied first and its layer is cached.

### Experiment 9 — list the eight RAM decisions from memory

Without opening anything, write the eight. Then check §4.4. **If you get fewer
than six, re-read Days 20, 24, 28 and 41** — you learned every one of them
without being told the cause.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/Dockerfile`, `docker-compose.yml` and `.env.example`:

1. Explain each of the six thread-limiting `ENV` lines. Why six for one idea?
   What breaks if you set only `OMP_NUM_THREADS`?
2. Why `--workers 1`? What exactly is duplicated by a second worker, and what
   does the constraint cost **in this workload specifically**?
3. Find every `environment:` key that is also in `.env`. Which wins? Which one
   has cost measurements, and what is the current consequence?
4. List every decision traceable to 512 MB. Aim for eight.
5. Which three compose services have **no** production counterpart, and why is
   that consistent rather than an oversight?

---

## 12. Self-check questions

**Basic**

1. What is Render's free-tier RAM, and what is exit 137?
2. How many uvicorn workers, and why?
3. Which services does `/health` check?
4. What is Redis used for here?
5. Which two directories are mounted read-only?

**Code**

6. Why is `requirements.txt` copied before the app code?
7. Why is `check_postgres_sync` run via `asyncio.to_thread`?
8. Why does `logging.basicConfig` come before the `app.*` imports — and why
   again in `worker.py`?
9. What does `force=True` protect against?
10. Which wins, `env_file` or `environment:`?

**Why**

11. Why six thread variables rather than one?
12. Why is Cohere the primary reranker?
13. Why is ingestion offline, and why wouldn't Celery have solved it?
14. Why is `golden_dataset/` mounted `:ro`?
15. Why "stacked free tiers do not compose into reliability"? Give two measured
    examples.

**Debugging**

16. The backend restarts under load with no traceback. Diagnose.
17. A measurement disagrees with `.env`. Diagnose.
18. The same query returns in 3 s, then 120 s, then 3 s. Diagnose, and give the
    fix **in order**.

**System design**

19. Serve 100 tenants. What changes, in what order, and what does **not** change?
20. Add the semantic cache the blueprint specified. What has to exist first, and
    what would you measure before believing it helps?

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `OMP_NUM_THREADS` (OpenMP), `MKL_NUM_THREADS` (Intel MKL),
   `OPENBLAS_NUM_THREADS` (OpenBLAS), `ORT_INTRA_OP_NUM_THREADS` (threads within
   one ONNX operator), `ORT_INTER_OP_NUM_THREADS` (operators run concurrently),
   `TOKENIZERS_PARALLELISM` (the Rust tokenizer's Rayon pool). **Six because each
   library reads its own variable and precedence is not uniform** — MKL overrides
   OpenMP when set, ONNX Runtime uses neither, the Rust tokenizer uses neither.
   **Setting only `OMP_NUM_THREADS`** leaves ONNX Runtime and the tokenizer sizing
   pools from the host's core count, which is the actual OOM path — **and it
   fails intermittently**, depending on which BLAS numpy linked against.
2. Because **workers are separate processes** and the three lazily-loaded models
   (dense bge-small, sparse bm25, the ONNX cross-encoder) are in-process objects
   with no sharing — two workers means two copies. **The cost is no CPU
   parallelism**, which matters less here than usual because the workload is
   overwhelmingly I/O-bound: a semantic query spends seconds waiting on Gemini and
   milliseconds computing. **The exception is fastembed**, which is CPU-bound and
   blocks the event loop for 0.36–0.41 s warm.
3. `DATABASE_URL`, `REDIS_URL`, `ENVIRONMENT`. **`environment:` wins.**
   `DATABASE_URL` is the costly one: the stack reads **local Docker Postgres**,
   not the Supabase URL in `.env`, and the two differ in document counts (11 vs 9)
   **and in DELETE grants** (CAVEAT-028). **Consequence: every measurement must
   state which database it came from.**
4. fastembed instead of torch; Cohere as primary reranker; `BATCH_SIZE = 8`;
   offline operator-triggered ingestion; one uvicorn worker; six thread-limit
   ENVs; no self-hosted LLM; no cache.
5. **`worker`, `scheduler` and `frontend`.** Consistent because ingestion is
   deliberately operator-run (ED-016), so there is nothing for Celery to do in
   production; and the frontend is built and served by Vercel, not by `next dev`
   in a container. **The compose `frontend` service runs a dev server** — it is a
   local convenience, not a production artefact.

### §12 — Basic

1. **512 MB.** Exit **137** is `128 + 9` — SIGKILL, i.e. the OOM killer. **No
   traceback**, because the process is gone mid-instruction.
2. **One.** Each worker is a separate process holding its own copy of three
   lazily-loaded models.
3. **Postgres, Redis, Qdrant** — independently, each reporting its own error
   string.
4. **The Celery broker and result backend, and a `/health` target.** Nothing on
   the request path uses it. The semantic cache was never built.
5. `./docs/raw:/app/docs/raw:ro` and `./golden_dataset:/app/golden_dataset:ro`.
6. Layer caching: editing a `.py` invalidates only the final `COPY . .` layer, so
   the dependency install is reused.
7. Because psycopg2 is **synchronous**, and blocking the event loop in an async
   handler blocks **every** concurrent request — which matters more with **one
   worker** (§4.3).
8. Because `app.engines.router` logs its resolved `GEMINI_MODEL` **at module
   scope**; with no root handler, Python falls back to `logging.lastResort`, fixed
   at WARNING, and the line is discarded silently. **Again in `worker.py`** because
   the worker and scheduler start at `app.worker:celery_app` and **never import
   `main.py`**, so the same code logged in one container and was silent in
   another.
9. A dependency installing a root handler later, which would turn `basicConfig`
   into a silent no-op. Celery installs its own logging on worker startup.
10. **`environment:`.**

### §12 — Why

11. See §11 Q1.
12. **`retriever.py` records "0 MB local RAM".** The local ONNX cross-encoder is
    the fallback. **And KU-005 keeps this honest:** the ONNX model *also* fits, so
    RAM alone does not explain the *ordering*, and no ADR or commit records a
    quality comparison. The plausible reading — that running it as primary would
    compete with fastembed and the request path in one 512 MB process — is
    **inference, not record.**
13. Because loading `bge-small-en-v1.5` in-process **OOM-killed** the 512 MB web
    service (exit 137, repeatedly), taking live querying down with it. **Celery
    would not have helped** because the constraint is **RAM in one container**, not
    the trigger mechanism — a `BackgroundTask` and a Celery worker on the same box
    load the same model into the same 512 MB.
14. So nothing in the container can write an eval output beside the three input
    files. **The failure it prevents actually happened: 79 outputs against 3
    inputs, which crashed an anchor scan.** A filesystem flag enforcing a
    directory's meaning.
15. Because each tier's limits are independent and they compound. **Two measured
    examples:** (a) Gemini's **unbounded tail latency** — 3.07 s / 120 s / 3.00 s
    on the same query, confirmed from Render logs as **one** 78 s call rather than
    an SDK retry, because no call site set a timeout; (b) **Cohere flapping on
    WSL2** — raw socket connects succeeding 5 of 8 at random, so the same query
    returned `tier=medium` and then `tier=high` purely because a different backend
    scored it.

### §12 — Debugging

16. **OOM, and expect no evidence in the application logs.** **(1)** Confirm the
    signature: exit code 137 in the platform's event log — SIGKILL leaves nothing
    in the app's own output. **(2)** `docker stats` under load to see the resident
    set approaching the limit. **(3)** Check the six thread ENVs actually reached
    the running container (`printenv | grep THREAD`) — a rebuilt image, a changed
    base, or a compose `environment:` block could shadow them. **(4)** Check
    `--workers 1` survived. **(5)** Ask what loaded a model: an ingestion script
    run against the serving container is the classic cause (ED-016).
17. **An `environment:` block is overriding `env_file:` for that key.** **(1)**
    `docker compose exec -T backend printenv <KEY>` — the running value is the only
    truth. **(2)** `grep -n "environment:" -A 6 docker-compose.yml`. **(3)** If
    they differ, `environment:` won. **(4)** And if you *changed* `.env` and
    nothing happened, that is a different cause: `env_file` values need
    `--force-recreate` — **which also destroys anything put in with
    `docker compose cp`.**
18. **An unbounded provider hang, not a slow answer.** **Diagnosis:** the fast
    runs match the recorded baseline, so nothing is structurally slow — the tail is
    unbounded. Confirm from the provider's own log lines that it is **one** call,
    not an SDK retry (`AFC is enabled` → `AFC remote call 1 is done`, 78 s). Then
    confirm no call site sets a timeout. **Fix, in order: (1) an explicit per-call
    timeout**, which converts an unbounded hang into a catchable exception at a
    chosen bound; **(2) the fallback**, which catches that exception. **Order
    matters absolutely: a fallback keyed on exceptions can never fire against a
    hang, because a hang throws nothing.** The timeout is the fallback's
    precondition, not its companion.

### §12 — System design

19. **In order.**
    **(0) Fix CAVEAT-001 before anything else.** The request body can override the
    JWT's `tenant_id`, and it is unexploitable **only because one tenant is
    seeded**. Multi-tenancy is exactly the event that ends that. **Fix, then create
    the second tenant to verify — never the reverse** (Day 42).
    **(1) Leave the free tier.** Everything else is downstream of 512 MB.
    **(2) Ingestion as its own service**, with its own memory budget — Celery
    finally exists in production and `pending_uploads` becomes a real queue.
    **(3) A paid LLM tier.** 500/day is roughly five users, and it already
    constrains *development*.
    **(4) Connection pooling** (CAVEAT-013): a new connection per statement does
    not survive concurrency.
    **(5) Rate limiting**, per tenant, on login and query — currently absent
    everywhere.
    **(6) Company onboarding as data** (CAVEAT-019), or every new tenant is a code
    edit and a deploy.
    **(7) `audit_log` partitioning and retention** — which forces CAVEAT-028's
    decision about what append-only means.
    **(8) Then, and only then, replicas.**
    **What does NOT change: the retrieval architecture.** Hybrid dense+sparse,
    RRF, reranking, the DSL compiler, the guards, the contradiction engine. **The
    scaling work is entirely infrastructure and tenancy**, which is a genuine
    result about the design rather than a compliment to it.
20. **What has to exist first.** **(a) Redis in production** — it is deployed
    locally and nowhere else, so this is an infrastructure change before it is a
    feature. **(b) A cache key that is actually safe**, and this is the hard part:
    it must include `tenant_id` (or one tenant serves another's answer — a
    confidentiality bug, not a staleness bug), the **role** (or a viewer receives
    an analyst's unstripped payload — `role_filtered_response` runs *after* the
    graph, so caching the graph's output and re-shaping per role is the correct
    layering), and a **corpus version**, or a re-ingest silently serves stale
    answers. **(c) A decision about semantic versus exact matching.** Exact query
    text is safe and will hit rarely; embedding-similarity matching risks serving
    the answer to a *different* question, which in a system whose claim is *"a
    wrong answer with a ✓ is worse than a refusal"* is the worst available
    failure. **Start exact.**
    **What to measure before believing it helps.** **(1) The hit rate on real
    traffic** — `audit_log.query_text` already holds every query ever asked, so
    this is answerable **today, offline, with a `GROUP BY`, before writing any
    code.** If exact repeats are rare, the cache is not worth building. **(2)
    Latency saved**, against `p95_latency_ms` — a cache that saves 200 ms on a
    3 s query is not the win. **(3) The LLM calls saved**, which against a 500/day
    ceiling is the real prize and the honest reason to build it.
    **And the thing to fix on day one:** `cache_hit` finally gets a producer, so
    `cache_hit_rate_pct` stops being structurally 0.0 and CAVEAT-009 closes. **The
    field is already wired end-to-end — column, aggregate, response, TypeScript —
    which is exactly why it was kept rather than deleted** (Day 44).

---

## 14. MUST REMEMBER

```text
- 512 MB is Render's free tier. Exit 137 = 128 + 9 = SIGKILL = OOM.
  NO TRACEBACK EXISTS
- SIX thread ENVs, because four libraries read four different variables and
  precedence is not uniform. They size pools from CORE COUNT, not memory
- --workers 1, because a worker is a PROCESS and the three models are
  in-process objects with no sharing
- EIGHT decisions traceable to 512 MB: fastembed not torch · Cohere primary ·
  BATCH_SIZE 8 · offline ingestion · one worker · six thread limits ·
  no self-hosted LLM · no cache
- `environment:` WINS over `env_file:`. DATABASE_URL is overridden at
  docker-compose.yml:51 — the stack reads LOCAL Postgres, not Supabase
- The two databases differ in DOCUMENT COUNTS (11 vs 9) and in GRANTS
  (CAVEAT-028). ALWAYS STATE WHICH ONE
- golden_dataset/ is :ro — the flag that prevents 79 outputs against 3 inputs
- ./backend:/app is a BIND MOUNT: `docker compose cp` writes into your REPO.
  Container scratch goes in /tmp. Check git status
- `up -d` returns when the container STARTS, not when uvicorn SERVES.
  Poll /health, and echo ${#TOKEN} so an empty token fails loudly
- basicConfig BEFORE any app.* import, force=True — and AGAIN in worker.py,
  because the worker never imports main.py
- Redis is the Celery broker and a /health target. NOTHING on the request
  path uses it. There is NO Redis in production
- worker and scheduler have NO production counterpart
- A TIMEOUT IS THE PRECONDITION FOR A FALLBACK. A hang throws nothing, so a
  fallback keyed on exceptions can never fire against one
- Stacked free tiers do not compose into reliability: 3.07s / 120s / 3.00s
  on one query; Cohere connecting 5 of 8 times at random
```

## 15. MUST UNDERSTAND

```text
- Why a hard RAM ceiling is a DESIGN INPUT, and how one number reached into
  six subsystems
- Why the constraint made some things BETTER (offline ingestion is a real
  separation) and other things simply MISSING (no cache, no rate limiting) —
  and why conflating those two is the romantic error
- Why "which database?" is now a question about PERMISSIONS as well as rows
- Why one uvicorn worker costs less in THIS workload than it would in most,
  and where the exception is (fastembed, in-process, CPU-bound)
- Why the timeout must precede the fallback, and why that ordering is a
  general rule about exception-keyed recovery
- Why a health check that reports "degraded" for Redis is honest about
  DEPENDENCIES and silent about CAPABILITIES
- Why the scaling list is entirely infrastructure and tenancy, and what that
  says about the retrieval design
- Why CAVEAT-001 has to be fixed BEFORE the second tenant exists, not after
```

---

## 16. This connects to

```text
Day  1 — the stack, images, mounts, env_file vs environment
Day 20 — fastembed, and why not torch
Day 28 — Cohere primary, ONNX fallback, two scales
Day 41 — offline ingestion, ED-016, exit 137
Day 44 — the record, and the metric with no producer
   ↓
Day 45 — deployment, and the ceiling            ← END OF PHASE 12
   ↓
Day 46 — the master trace, from memory          ← PHASE 13, THE CAPSTONE
```

Forward references:

- **Day 46** is a deliverable, not a lesson: you write
  `docs/architecture/MASTER_REQUEST_TRACE.md` **without the repository open.**
- **Day 47** Part 3 asks what you would change — §8's "At 10×" table is the
  material, and the reason CAVEAT-001 is item zero.
