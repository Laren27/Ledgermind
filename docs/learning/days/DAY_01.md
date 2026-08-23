# Day 01 — The Machine Underneath

**Phase 0 — Ground · Weight: M (~90 min) · Prerequisites: none**

---

## 1. Today's goal

By tonight you can:

- Say what each of the seven containers does and why it exists.
- Bring the stack up, confirm it is *actually serving*, and prove **which code**
  and **which database** you are talking to.
- Explain the difference between an image and a container, a bind mount and a
  volume, and `env_file` and `environment` — using this repository's own
  `docker-compose.yml` as the example.
- Name the one line in `docker-compose.yml` that has silently invalidated
  measurements, and say why.

You are **not** learning Docker in general today. You are learning *this stack*,
and the general concepts arrive as they become necessary to explain it.

---

## 2. Why now

Nothing in this repository can be read until it runs. That is not a slogan —
it is a practical fact about this specific codebase:

- `retriever.py` loads three ONNX models lazily; you cannot reason about its
  timing until you have felt a cold start versus a warm one.
- Every database query depends on a session variable that must be set first;
  you cannot see that until you can open a connection.
- The engines log heavily at INFO; those logs are the primary teaching material
  for Days 25–37, and they only exist in a running container.

There is a second reason, specific to this project. From `CLAUDE.md` §4:

> Environment-vs-code confusion has caused more lost time this month than
> application defects.

Today is the inoculation against that.

---

## 3. Concepts you must know first

None. This is Day 1. Everything below is built from nothing.

---

## 4. Concept lesson

### 4.1 A process

**What is it?** A running program. Not the file on disk — the *instance of it
executing*, with its own memory, its own open files, and its own copy of the
environment.

**What problem does it solve?** It is the unit the operating system schedules
and isolates. Two processes can run the same program and not interfere.

**What existed before?** Single-program machines, where a program owned the
whole computer until it finished.

**Mental model.** A program on disk is a **recipe**. A process is **someone
actually cooking it**, right now, with their own pans.

**In LedgerMind.** `uvicorn app.main:app` inside the backend container is one
process. A Celery worker is another. When you run
`docker compose exec backend python -c "..."` you start a **third**, entirely
separate process that happens to share the same filesystem. That last point
matters more than it sounds — see §4.4.

---

### 4.2 A port

**What is it?** A number, 0–65535, that a process claims on a machine so that
incoming network traffic addressed to that number is delivered to it.

**What problem does it solve?** One machine, one IP address, many programs that
all want to receive network traffic. The port disambiguates.

**Mental model.** The IP address is the **building**. The port is the **flat
number**.

**In LedgerMind.** From `docker-compose.yml`:

| Port | Service | What listens |
|---|---|---|
| 3000 | frontend | Next.js dev server |
| 8000 | backend | uvicorn |
| 5432 | postgres | PostgreSQL |
| 6379 | redis | Redis |
| 6333 | qdrant | Qdrant HTTP |

**The failure this causes here.** From `CLAUDE.md` §6:

> A backgrounded local uvicorn has caused multi-hour false-regression chases;
> check `lsof -i :8000`.

If a `uvicorn` is already running on your host at port 8000, and the container
also maps 8000, you can spend hours testing a container you are not actually
talking to. The `lsof` check exists because this happened.

---

### 4.3 An environment variable

**What is it?** A named string that a process receives from whatever started it.

**What problem does it solve?** Configuration that must differ between machines
(a database URL, an API key) without changing the code.

**The property that trips everyone up: environment is inherited, not global.**
A process gets a **copy** of its parent's environment. Setting a variable in a
child never affects the parent, and never affects a sibling.

```
your shell                          knows: PATH, HOME
   └─ docker compose                knows: the above, plus anything in .env
        └─ backend container        knows: what compose passed it
             └─ your `exec` process knows: what the container has
```

**In LedgerMind.** This is why the diagnostic is:

```bash
docker compose exec -T backend printenv GEMINI_MODEL
```

and not `echo $GEMINI_MODEL`. The second tells you about *your shell*. Only the
first tells you what the code will actually read.

---

### 4.4 Image versus container

**Image:** a frozen, layered filesystem plus a default command. Built once,
identical everywhere.

**Container:** a running (or stopped) instance of an image, with its own
writable layer on top.

**Mental model.** The image is the **recipe**; the container is the **meal
cooked from it**. `docker compose up --build` rewrites the recipe.
`--force-recreate` throws away the meal and cooks a new one — **including
anything you left on the counter.**

**In LedgerMind, the trap this creates.** From `CLAUDE.md` §7:

> `docker compose cp <file> backend:/app/...` **writes into the repo.**

Because the backend service declares:

```yaml
volumes:
  - ./backend:/app
```

`/app` inside the container and `./backend` on your host are **the same
directory**. A file "copied into the container" appears under `backend/` and
shows up as untracked in `git status`. Container scratch belongs in `/tmp`,
which is *not* mounted:

```bash
docker compose exec -T backend sh -c 'cat > /tmp/x.py' < x.py
```

---

### 4.5 Bind mount versus volume

| | Bind mount | Volume |
|---|---|---|
| Syntax here | `./backend:/app` | `postgres_data:/var/lib/postgresql/data` |
| Lives | in your working tree | in Docker's storage area |
| You can edit it | yes, with your editor | not easily |
| Survives `--force-recreate` | yes (it is your files) | yes |
| Survives `down -v` | yes | **no** |

**Why LedgerMind uses both.** The bind mount on `./backend` is what makes
`--reload` work: you edit `retriever.py` in your editor and uvicorn restarts
inside the container. The volume on `postgres_data` is what makes your ingested
corpus survive a container rebuild.

---

### 4.6 A health check

**What is it?** A command Docker runs periodically inside a container; its exit
code determines whether the container is reported *healthy*.

**What problem does it solve?** "The container started" and "the service inside
it is ready to answer" are different events, often seconds or minutes apart.

**In LedgerMind:**

```yaml
postgres:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ledgermind_app -d ledgermind"]
    interval: 10s
    timeout: 5s
    retries: 5
```

and consumed by:

```yaml
backend:
  depends_on:
    postgres:
      condition: service_healthy
```

Without `condition: service_healthy`, `depends_on` only waits for the container
to *start*, and the backend races Postgres to readiness.

**The gap this does NOT close.** From `CLAUDE.md` §6:

> `docker compose up -d` returns when the container starts, not when uvicorn
> serves. Poll `/health` before minting a token.

The backend itself has **no** healthcheck in compose. So `up -d` returning tells
you Postgres, Redis and Qdrant are healthy — and tells you *nothing* about
uvicorn. You must poll.

---

## 5. The actual LedgerMind files

### `docker-compose.yml`

```
File:        docker-compose.yml (3,415 bytes, 7 services)
Purpose:     Declare the entire local development stack
Why:         Seven interdependent services with startup ordering.
             By hand this is not reproducible.
Who reads it: docker compose, and you
Entry point: `docker compose up -d --build`
Data in:     .env (via env_file), your working tree (via bind mounts)
Data out:    running services on mapped ports
```

**The seven services:**

| Service | Image / build | Role | Notes |
|---|---|---|---|
| `postgres` | `postgres:15-alpine` | Relational store | Runs `sql/init.sql` on **first** start only |
| `redis` | `redis:7-alpine` | Celery broker | **Not** a cache — nothing caches here |
| `qdrant` | `qdrant/qdrant:latest` | Local vector store | Usually bypassed; see §5.1 |
| `backend` | `./backend` | FastAPI + uvicorn | `--reload`, bind-mounted |
| `frontend` | `./frontend` | Next.js dev server | bind-mounted |
| `worker` | `./backend` | Celery worker | Same image, different command |
| `scheduler` | `./backend` | Celery beat | Same image again |

Three services share one image. That is deliberate: the worker imports the same
`app.*` modules the API does, so it must have the same dependencies.

---

### 5.1 Read this block closely

```yaml
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    dns:
      - 8.8.8.8
      - 1.1.1.1
    dns_opt:
      - timeout:1
      - attempts:5
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql://ledgermind_app:app_dev_pass@postgres:5432/ledgermind
      REDIS_URL: redis://redis:6379/0
      ENVIRONMENT: development
    depends_on:
      postgres:  { condition: service_healthy }
      redis:     { condition: service_healthy }
      qdrant:    { condition: service_healthy }
    volumes:
      - ./backend:/app
      - ./docs/raw:/app/docs/raw:ro
      - ./golden_dataset:/app/golden_dataset:ro
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Line by line, on the parts that carry consequences.**

**`dns:` and `dns_opt:`** — an explicit DNS configuration is unusual in a compose
file. It is here because WSL2's default resolver flaps. `docs/RUNBOOK.md` has a
whole section on it. The measured symptom was hostname lookups taking 8 seconds
while a naive test loop reported "20/20 clean" because it counted successes
without timing them. `timeout:1 attempts:5` converts one slow lookup into five
fast attempts.

**`env_file: .env` versus `environment:`** — both set variables; `environment:`
**wins**. From `CLAUDE.md` §6:

> `QDRANT_URL` and all cloud credentials flow purely through `env_file: .env`.
> Never override via an `environment:` block — that exact override invalidated
> every local measurement for a week.

**And now the live instance of that trap, in this very block.** `DATABASE_URL`
**is** in the `environment:` block. So:

- `.env` may contain the Supabase URL.
- The container reads the **local Docker Postgres** anyway.
- The two are different databases with different document counts — **11 local
  vs 9 Supabase**.

This is `CAVEAT-015` and audit finding **F11**. It is not a bug you are being
asked to fix. It is the reason that every measurement in this project must
**state which database it came from**.

**`- ./golden_dataset:/app/golden_dataset:ro`** — read-only, and the `:ro` is the
point. The comment in the file says why: without it, an eval output written into
that directory once left *79 outputs against 3 inputs* and crashed an anchor
scan.

**`--reload`** — uvicorn watches the bind-mounted files and restarts on change.
Convenient in development, and never used in production (the Dockerfile's `CMD`
has no `--reload` and pins `--workers 1`).

---

### `backend/Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV ORT_INTRA_OP_NUM_THREADS=1
ENV ORT_INTER_OP_NUM_THREADS=1
ENV TOKENIZERS_PARALLELISM=false
```

Six thread-limiting variables. **Every one of them exists because of a memory
ceiling**, and you will meet the full story on Day 45. The short version: ONNX
Runtime and the BLAS libraries under it size their thread pools from the *host's*
core count. On a 64-core machine that allocates 64 sets of per-thread buffers
inside a container limited to 512 MB, and the process is OOM-killed with
`Exited with status 137`.

`PYTHONUNBUFFERED=1` matters for a different reason: without it, Python buffers
stdout, and your logs arrive in bursts — or not at all, if the process dies
before the buffer flushes. When you are debugging a crash, the last log line
before it is the most valuable one you have.

---

## 6. Deep walkthrough — what `docker compose up -d --build` actually does

**STATE BEFORE.** No containers. Your source is on disk. `.env` exists.

**Execute:** `docker compose up -d --build`

**What happens, in order:**

1. Compose parses `docker-compose.yml` and reads `.env` for `env_file` values.
2. For each service with `build:`, it builds the image — running the Dockerfile
   layer by layer, reusing cached layers where nothing upstream changed.
   `COPY requirements.txt .` before `COPY . .` is deliberate: it means editing
   your source does **not** invalidate the pip-install layer.
3. It creates a network so services can reach each other **by service name**.
   This is why `DATABASE_URL` says `@postgres:5432` and not `@localhost` —
   `postgres` is a DNS name on that network.
4. It starts services with no dependencies first: `postgres`, `redis`, `qdrant`.
5. It runs each health check on its interval until healthy or retries exhaust.
6. Once all three report healthy, it starts `backend`, `worker`, `scheduler`.
7. `frontend` starts (it depends on `backend` **without** a condition, so it does
   not wait for readiness).
8. Compose returns.

**STATE AFTER.**

- Seven containers running.
- Ports mapped to your host.
- **uvicorn may not be serving yet.** Compose returned when the container
  started.
- On a *first ever* start, Postgres has run `sql/init.sql` from
  `/docker-entrypoint-initdb.d/`. On every later start it has **not** — that
  entrypoint only fires when the data directory is empty. This is why migrations
  are separate files and not edits to `init.sql`.

**What would break if `depends_on` conditions were removed?** The backend would
start immediately, `/health` would report `postgres: error: could not connect`,
and the first login would 503. It would then recover on its own within seconds —
which is worse than failing, because it makes startup non-deterministic.

---

## 7. Data flow — one HTTP request, at the infrastructure level

You will trace this at the *code* level on Days 4–6. Today, only the layers
below the code:

```
your browser / curl
      │  TCP to 127.0.0.1:3000  or  127.0.0.1:8000
      ▼
Docker's port mapping ("3000:3000" / "8000:8000")
      │  rewrites destination into the compose network
      ▼
container's own network interface
      │
      ▼
the listening process   (next dev  /  uvicorn --host 0.0.0.0)
      │
      ├─► needs Postgres?  resolves the name `postgres` on the compose network
      ├─► needs Redis?     resolves `redis`
      └─► needs Qdrant?    resolves QDRANT_URL — which for real work is a
                           PUBLIC https URL, leaving the compose network entirely
```

**`--host 0.0.0.0` and why it is not optional.** If uvicorn bound to `127.0.0.1`
it would accept connections only from *inside its own container*. Docker's port
mapping arrives on the container's external interface, so the process must bind
all interfaces to receive it.

---

## 8. Engineering decision — why Docker Compose here?

**The problem.** Seven services, three of which are third-party databases, with
startup ordering and shared configuration.

**The choice.** Docker Compose, with three services built from one Dockerfile.

**Alternatives, and why not:**

| Alternative | Why not |
|---|---|
| Install Postgres/Redis/Qdrant natively | Version drift between machines; no reproducibility; the exact class of problem this project is most allergic to |
| Kubernetes locally (kind/minikube) | Enormous operational overhead for a single-developer stack. `README.md` lists microservices under *Deliberately out of scope*: "Python modules inside FastAPI are sufficient at this scale" |
| A shell script starting everything | No dependency ordering, no health gating, no isolation |

**Trade-offs accepted.** A build step, disk usage, and a layer of indirection
between you and the process. In exchange: one command, and an environment that
matches everyone else's.

**Is it still appropriate?** Yes for local development. Production does **not**
use compose — the backend runs on Render, the frontend on Vercel, Postgres on
Supabase, Qdrant on Qdrant Cloud. So compose describes the *development*
topology, not the deployed one, and Day 45 is where that distinction is drawn
properly.

**What changes at 10×?** Nothing about compose; it would simply stop being the
production question. The interesting scaling question here is not orchestration
but the 512 MB ceiling, which is Day 45.

---

## 9. Failure modes

| Symptom | Cause | What to do |
|---|---|---|
| `curl: (7) Failed to connect to localhost port 8000` | uvicorn not serving yet | Poll `/health`; compose returned early |
| `/health` shows `postgres: error: ...` | Postgres not ready, or wrong `DATABASE_URL` | Check `docker compose ps` for health status |
| `Exited with status 137` | OOM kill | Day 45 |
| `exec failed: ... possible container breakout detected` | **Stale mount namespace**, usually after `--force-recreate`. **Not a security event.** `-w /app` does not help; no `cd` helps; *every* exec fails | Confirm with `docker compose exec -T backend echo alive`, then `docker compose up -d --force-recreate backend` and poll `/health` |
| `Cwd must be an absolute path` (Git Bash on Windows) | `-w /app` was path-rewritten to a Windows path | Prefix the command with `MSYS_NO_PATHCONV=1` |
| Changes to source have no effect | You are talking to a host uvicorn, not the container | `lsof -i :8000` |
| A measurement contradicts a previous one | Possibly a different database | State which one. See `CAVEAT-015` |

---

## 10. Hands-on experiment

Run these in order. Do not skip the reasoning between them.

### Experiment 1 — bring it up and prove it is serving

```bash
docker compose up -d --build
```

```bash
docker compose ps
```

Read the `STATUS` column. Note which services report `(healthy)` and which
report only `Up`. **The backend is in the second group.** That is the whole
lesson of §4.6.

```bash
curl -s http://localhost:8000/health
```

If this fails, compose returned before uvicorn was listening. Retry until it
answers. Then read the JSON: it reports `postgres`, `redis` and `qdrant`
separately, so a partial outage is visible rather than being flattened into "up".

### Experiment 2 — which code is actually running?

```bash
docker compose exec -T backend python -c "import app.engines.retriever as m; print(m.__file__)"
```

Expected: `/app/app/engines/retriever.py`. Because of the bind mount, that file
is `backend/app/engines/retriever.py` in your working tree. **The container is
running the code you can see.** Confirm it by adding a comment to that file and
re-running — the file's mtime changes and uvicorn reloads.

### Experiment 3 — which environment?

```bash
docker compose exec -T backend printenv GEMINI_MODEL
docker compose exec -T backend printenv QDRANT_URL
docker compose exec -T backend printenv DATABASE_URL
```

Now compare the third against your `.env`:

```bash
grep '^DATABASE_URL' .env
```

**They differ.** You have just observed `CAVEAT-015` first-hand. Write down which
one the container uses, because for the rest of this course "which database?" is
a question you must be able to answer instantly.

### Experiment 4 — cold versus warm

```bash
time docker compose exec -T backend python -c "import app.engines.retriever"
time docker compose exec -T backend python -c "import app.engines.retriever"
```

The first is slower. Each `exec` is a **new process** (§4.1), so nothing is
shared between them — but the OS page cache is warm for the second. Now
contrast with the real cost:

```bash
time docker compose exec -T backend python -c "
import app.engines.retriever as r
r._get_dense_model()
print('model loaded')"
```

That is a cold model load, and it is the reason `CLAUDE.md` says:

> A local semantic failure is not a defect until it reproduces on a **warm**
> process.

### Experiment 5 — the bind mount is not a copy

```bash
docker compose exec -T backend sh -c 'echo "scratch" > /app/PROOF.txt'
ls backend/PROOF.txt
git status --short
```

The file the container created is in **your working tree** and shows as
untracked. Now clean up and do it correctly:

```bash
rm backend/PROOF.txt
docker compose exec -T backend sh -c 'echo "scratch" > /tmp/PROOF.txt'
ls backend/PROOF.txt 2>&1        # No such file — /tmp is not mounted
git status --short                # clean
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT. Do not scroll to §13.**

Open these files and answer from them alone:

**`docker-compose.yml`**
1. Which three services are built from the **same** Dockerfile, and why is that
   safe?
2. Which volume mounts are read-only, and what does each `:ro` prevent?
3. `frontend` declares `depends_on: [backend]` with no condition. What is the
   practical consequence?

**`backend/Dockerfile`**
4. Why does `COPY requirements.txt .` come before `COPY . .`?
5. The production `CMD` pins `--workers 1`. The compose `command` overrides it
   with `--reload`. Which wins, and why does that matter?

**`.env.example`**
6. Which variable has **no** safe default, and what does the code do if it is
   missing?

---

## 12. Self-check questions

**Basic**
1. What is the difference between an image and a container?
2. What does `docker compose up -d` return *after*?
3. Which port does the API listen on, and which does the frontend?
4. What does `env_file` do, and what beats it?
5. Name the three services with health checks.

**Code**
6. In `docker-compose.yml`, what does `./backend:/app` mean for a file created
   at `/app/foo.txt`?
7. What does `--host 0.0.0.0` do and what breaks without it?
8. What is `PYTHONUNBUFFERED=1` for?
9. Where does `sql/init.sql` get mounted, and when does it run?
10. What does `condition: service_healthy` add over plain `depends_on`?

**Why**
11. Why are there six thread-limiting `ENV` lines in `backend/Dockerfile`?
12. Why must cloud credentials flow through `env_file` and never `environment:`?
13. Why is `golden_dataset` mounted read-only?
14. Why does the backend have no health check in compose, and what must you do
    as a result?
15. Why is `postgres_data` a volume while `./backend` is a bind mount?

**Debugging**
16. You change `retriever.py`, restart nothing, and behaviour does not change.
    Name two possible causes.
17. Every `docker compose exec` fails with *"possible container breakout
    detected"*. What is it, and what is the first command you run?
18. A measurement today contradicts one from last week. What is the first thing
    you check — before looking at any code?

**System design**
19. You must add a second backend replica. What in this compose file has to
    change, and what breaks if you change nothing else?
20. Compose is the development topology. Production is Render + Vercel +
    Supabase + Qdrant Cloud. Name two things compose therefore **cannot** tell
    you about production behaviour.

---

## 13. Answer key

> **Only read this after attempting §11 and §12.**

### §11

1. **`backend`, `worker`, `scheduler`.** Safe because they run the same
   `app.*` package — the worker imports the same ingestion and engine modules,
   so it needs identical dependencies. Three images would have to be kept in
   sync by hand.
2. `./docs/raw:/app/docs/raw:ro` — the corpus PDFs cannot be modified by
   anything running in the container. `./golden_dataset:/app/golden_dataset:ro`
   — nothing in the container can write an eval output beside the inputs. The
   file's own comment records why: 79 outputs against 3 inputs, and a crashed
   anchor scan.
3. It waits for the backend container to **start**, not to serve. The frontend
   can therefore be up and issuing requests that fail while uvicorn is still
   booting. In development that is harmless (the user retries); it is worth
   knowing so you do not read it as a bug.
4. **Docker layer caching.** Layers are invalidated top-down. If source were
   copied first, every source edit would invalidate the `pip install` layer and
   rebuild every dependency. With `requirements.txt` copied first, the install
   layer is reused until dependencies actually change.
5. **The compose `command:` wins** — it replaces the image's `CMD`. It matters
   because it means the container you develop against (`--reload`, unlimited
   workers) is *not* configured the way production is (`--workers 1`, no
   reload). Behaviour that depends on worker count will not reproduce locally.
6. **`GEMINI_MODEL`.** `llm/client.py:_resolve_gemini_model()` raises
   `RuntimeError` rather than defaulting, and the docstring says why: on
   2026-07-31 two full eval sweeps were reported under a model that never served
   a single call. A crash costs five minutes; a plausible-but-wrong default cost
   ~60 calls and two unusable result files.

### §12 — Basic

1. An image is a frozen layered filesystem plus a default command; a container
   is a running instance of one, with its own writable layer.
2. After the **containers start** — not after the services inside them are ready.
3. API 8000, frontend 3000.
4. `env_file` loads variables from a file into the container. An `environment:`
   block **overrides** it.
5. `postgres`, `redis`, `qdrant`. Notably **not** `backend`.

### §12 — Code

6. `/app` and `./backend` are the same directory. `/app/foo.txt` appears as
   `backend/foo.txt` in your working tree and shows as untracked in
   `git status`.
7. It binds all network interfaces. Bound to `127.0.0.1`, the process would
   accept connections only from inside its own container, and Docker's port
   mapping — which arrives on the container's external interface — would never
   reach it.
8. It disables Python's stdout buffering so log lines appear immediately. When a
   process dies, the last line before the crash is the most valuable one you
   have, and buffering can lose it.
9. `/docker-entrypoint-initdb.d/init.sql`. The Postgres image runs everything in
   that directory **only when the data directory is empty** — i.e. on first ever
   start. This is precisely why schema changes are separate migration files
   rather than edits to `init.sql`.
10. Plain `depends_on` waits for the container to start. `condition:
    service_healthy` waits for the health check to pass, which is the difference
    between "Postgres exists" and "Postgres will answer a query".

### §12 — Why

11. ONNX Runtime and the BLAS libraries beneath it size their thread pools from
    the **host** core count. On a many-core machine that allocates per-thread
    buffers far exceeding a 512 MB container limit, and the process is OOM-killed
    (`Exited with status 137`). Full story on Day 45.
12. Because `environment:` silently wins over `env_file`, and a stale override
    is indistinguishable from correct configuration at runtime. That exact
    mistake invalidated every local measurement for a week. `DATABASE_URL` is a
    live instance of it today (`CAVEAT-015`).
13. So nothing in the container can write beside the inputs. It once left 79
    output files against 3 input files and crashed an anchor scan that globbed
    the directory.
14. Nobody added one. The consequence is that `up -d` returning tells you
    nothing about uvicorn, so you must **poll `/health`** before doing anything
    that assumes the API is up — including minting a token.
15. `./backend` must be editable with your editor and reloadable by uvicorn, so
    it is a bind mount. `postgres_data` is database internals you should never
    edit by hand, and it must survive rebuilds, so it is a volume.

### §12 — Debugging

16. (a) A host `uvicorn` is bound to 8000 and you are talking to it instead of
    the container — check `lsof -i :8000`. (b) `--reload` did not fire, or the
    container is running a different checkout — verify with
    `python -c "import app.engines.retriever as m; print(m.__file__)"`.
17. A stale mount namespace, usually following `--force-recreate`. **Not** a
    security event and **not** a cwd problem — `-w /app` does not help and no
    `cd` helps, because *every* exec fails. First command:
    `docker compose exec -T backend echo alive` to confirm the diagnosis. Then
    `docker compose up -d --force-recreate backend` and poll `/health`.
18. **Which database each measurement came from.** Local Docker Postgres and
    Supabase hold different document counts (11 vs 9), and the `environment:`
    block points the container at the local one regardless of `.env`.
    Environment before code — always.

### §12 — System design

19. `ports: "8000:8000"` cannot be duplicated — two containers cannot claim one
    host port. You would need a port range or a reverse proxy, plus
    `deploy.replicas`. What breaks if you change nothing else: the Celery
    scheduler would run twice and fire every scheduled task twice, and any
    module-level singleton (the compiled graph, the loaded ONNX models) would
    exist once *per replica*, doubling memory — which on a 512 MB tier is fatal.
20. (a) **Memory behaviour.** Your laptop is not memory-capped the way Render's
    tier is; the six `ENV` lines are invisible locally and load-bearing in
    production. (b) **Proxy behaviour.** Render/nginx buffer proxied responses by
    default, which would hold every SSE event until the pipeline finished —
    which is why `api/query.py` sets `X-Accel-Buffering: no` and why the comment
    there says it must be verified live on Render, not just locally. Also
    acceptable: network topology (Qdrant Cloud is a public hop in both, but
    Postgres is local here and a pooled Supabase connection there).

---

## 14. MUST REMEMBER

```text
- 7 services; backend, worker and scheduler share ONE image
- `docker compose up -d --build` is the only correct way to run this stack
- `up -d` returns when the container STARTS, not when uvicorn SERVES → poll /health
- `environment:` beats `env_file:` — and DATABASE_URL is overridden today
- ./backend:/app is a BIND MOUNT: /app and your working tree are the same place
- container scratch goes in /tmp, which is NOT mounted
- `sql/init.sql` runs only on a first-ever Postgres start
- Git Bash on Windows: prefix `MSYS_NO_PATHCONV=1` or `-w /app` is rewritten
```

## 15. MUST UNDERSTAND

```text
- Why environment is inherited rather than global — and why that makes
  `printenv` inside the container the only trustworthy answer
- Why "the container started" and "the service is ready" are different events,
  and which one compose reports
- Why an `environment:` override is more dangerous than a missing variable:
  a missing variable fails loudly, an override succeeds wrongly
- Why every measurement in this project must state which database produced it
- Why a cold process is not evidence of a defect
```

---

## 16. This connects to

```text
Day 1 — the stack runs, and you can prove what it is running
   ↓
Day 2 — reading the repository's history as evidence
   ↓
Day 3 — what the system actually does: three engines, one dictionary
```

Forward references you will meet again:

- The **512 MB ceiling** behind the six `ENV` lines → **Day 45**
- The **two databases** and `CAVEAT-015` → **Day 16**
- **Lazy model loading** and cold-vs-warm → **Day 12**
- **`/health` as readiness, not liveness** → **Day 4**
- **`X-Accel-Buffering: no`** and proxy buffering → **Day 6**
