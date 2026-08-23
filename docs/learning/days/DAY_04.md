# Day 04 — HTTP → API → Endpoint → FastAPI

**Phase 1 — How a request arrives · Weight: M (~90 min) · Prerequisites: Days 1, 3**

---

## 1. Today's goal

By tonight you can:

- Describe an HTTP request and response in terms of their four and three parts.
- Say what an API is, what an endpoint is, and what a web framework adds that
  raw sockets do not.
- Read `app/main.py` line by line and explain **why the first eleven lines are a
  logging call and a comment saying "do not move this"**.
- Explain why `/health` is a *readiness* signal, not a liveness one, and read
  its output as a diagnostic.

---

## 2. Why now

Day 3 gave you `QueryState` — the object a request becomes. Today you learn how
a request physically *arrives* at the code that builds it. Without this, Day 5's
request contract and Day 6's streaming have nothing to attach to.

There is also a Day 1 loose end to close: you polled `/health` and read its JSON,
but you did not know what any of it meant. Today you do.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Ports and processes | Day 1 | An endpoint is a process listening on a port |
| `--host 0.0.0.0` | Day 1 | Explains what "listening" means at the interface level |
| `QueryState` | Day 3 | The thing an endpoint eventually builds |

---

## 4. Concept lesson

### 4.1 The problem HTTP solves

**Before HTTP.** Two programs that wanted to talk defined their own protocol —
their own byte layout, their own error signalling, their own idea of "done".
Every pair of programs needed a bespoke client. Nothing was reusable, and
nothing could be inspected by a tool that did not already know the protocol.

**What HTTP is.** A **request/response** protocol with a fixed, text-readable
shape that any two programs can agree on without knowing anything else about
each other.

**A request has four parts:**

```
POST /api/query HTTP/1.1                    ← METHOD and PATH
Host: localhost:8000                        ┐
Authorization: Bearer eyJhbGci...           │ HEADERS
Content-Type: application/json              ┘
                                            ← blank line separates
{"query": "What was Eternal's FY26 revenue?"}   ← BODY
```

**A response has three:**

```
HTTP/1.1 200 OK                             ← STATUS CODE
content-type: application/json              ← HEADERS
                                            
{"request_id": "...", "response_text": "..."}   ← BODY
```

**Mental model.** A request is a **letter**. The method is what you are asking
for, the path is the address, the headers are written on the envelope, and the
body is the contents.

---

### 4.2 Methods, and what they promise

| Method | Means | Used in LedgerMind |
|---|---|---|
| `GET` | Read. No side effects. Safe to repeat | `/health`, `/api/metrics`, `/api/documents/pending` |
| `POST` | Do something. May have side effects | `/auth/login`, `/api/query`, `/api/documents/upload` |
| `PUT` / `PATCH` | Replace / partially update | **not used here** |
| `DELETE` | Remove | **not used here** — `audit_log` is append-only by grant |

The absence of `DELETE` is informative. `sql/init.sql` grants
`SELECT, INSERT, UPDATE` on the five tables and the comment reads:

> audit_log is append-only — no UPDATE or DELETE granted, ever

There is no delete endpoint because there is no delete **permission**. The
architecture enforces it a layer below the API.

---

### 4.3 Status codes, as a decision tree

```
1xx  informational          (not used here)
2xx  it worked              200 OK
3xx  go somewhere else      (not used here)
4xx  YOUR fault             400 malformed · 401 who are you? · 403 no ·
                            413 too large · 422 failed validation
5xx  OUR fault              500 unhandled · 502 upstream failed ·
                            503 temporarily unavailable, retry
```

**Two distinctions this codebase depends on.**

**401 vs 403.** 401 = "I do not know who you are." 403 = "I know exactly who you
are, and no." `auth/dependencies.py` raises 401 for a bad or expired token and
403 for a valid token with insufficient role.

**500 vs 503.** This one is a deliberate design decision, in `auth/service.py`:

```python
# 503, not 500: a transient database failure is retryable and is not
# a defect in the request. The eval runner and the frontend can both
# act on that distinction; a 500 tells them nothing.
```

A 500 says "something broke, do not bother retrying". A 503 says "try again in a
moment". The eval runner and the browser behave differently on each — so
choosing the wrong one destroys information that a caller could have used.

---

### 4.4 API, endpoint, framework

**API** — the set of operations a program exposes, and the contract for using
them.

**Endpoint** — one (method, path) pair. `POST /api/query` is an endpoint;
`GET /api/query` is not (it does not exist and returns 405).

**What a web framework adds.** Without one you would write: a socket server,
HTTP parsing, routing (path → function), body deserialisation, validation,
serialisation, error-to-status-code mapping, and documentation — for every
endpoint. FastAPI derives most of that **from type hints**.

```python
@router.post("/query")
async def execute_query(
    payload: QueryRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
```

From those three lines FastAPI knows: the method, the path, that the body must
parse into `QueryRequest` (returning 422 if not), that `get_current_user` must
run first and its return value is passed in, and enough to generate the OpenAPI
schema behind `/docs`.

---

## 5. The actual LedgerMind files

### `backend/app/main.py`

```
File:        backend/app/main.py (108 lines)
Purpose:     The FastAPI application object: logging, CORS, router mounting,
             and the /health endpoint
Why it exists: Something must be the process entrypoint. `uvicorn app.main:app`
             names this module and the `app` object inside it.
Who imports it: uvicorn (via the compose `command`). Nothing in the codebase.
What it imports: the four routers, settings, and three client libraries
Entry point: the module-level `app = FastAPI(...)`
Data in:     HTTP requests
Data out:    HTTP responses, and /health's service dictionary
```

**Note what it does NOT contain:** no business logic, no database queries other
than `SELECT 1`, no engine imports. It is a wiring file. Every real decision is
one import away.

---

## 6. Deep code walkthrough

### 6.1 The first eleven lines, and why they are first

```python
# ---------------------------------------------------------------------------
# Logging must be configured BEFORE any `app.*` import.
#
# Importing app.api.query pulls in app.engines.router, which logs its
# resolved GEMINI_MODEL at module scope. With no root handler installed,
# Python falls back to logging.lastResort (fixed at WARNING) and the line
# is discarded silently. This applied to every import-time INFO log in the
# codebase, not just that one.
#
# force=True so a dependency that installs a root handler cannot turn
# basicConfig into a silent no-op later.
#
# DO NOT move this below the app.* imports.
# ---------------------------------------------------------------------------
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
```

**STATE BEFORE.** Python has started. No logging is configured. `logging` falls
back to an internal `lastResort` handler fixed at `WARNING`.

**Execute the `basicConfig` call.**

**STATE AFTER.** A root handler exists at `INFO`, with a timestamped format.

**Why the order matters — trace it precisely.**

```
main.py line 30:  from app.api.query import router
                      ↓ imports
                  app.engines.graph
                      ↓ imports
                  app.engines.router
                      ↓ at MODULE SCOPE, not inside a function:
                  logger.info("...GEMINI_MODEL...")
```

That `logger.info` runs **during the import**, before any request. If
`basicConfig` had not yet run, the root logger has no handler, `lastResort`
handles it at `WARNING`, and an `INFO` line is **silently dropped**. No error.
No warning about the dropped warning.

**Why `force=True`?** Without it, `basicConfig` is a **no-op if the root logger
already has a handler**. Some libraries install one on import. `force=True`
removes existing handlers and installs yours, so a dependency cannot silently
disable your logging.

**What breaks if you move it below the imports?** Every import-time `INFO` line
in the codebase disappears. The application still works perfectly. You simply
stop being told which model is configured — which is exactly the information
that, when missing, cost this project two unusable eval sweeps.

**Where the same fix appears again.** `app/worker.py` has an identical block,
with a comment explaining that the Celery worker starts at `app.worker:celery_app`
and **never imports `main.py`**, so `main.py`'s configuration never ran for it.
The same code, logging differently in two containers.

---

### 6.2 CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8000",
        "https://ledgermind-ypmv8v239-laren-house.vercel.app",
    ],
    # Covers all Vercel preview + production domains.
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**What CORS is.** A **browser** rule. A page served from origin A may not read a
response from origin B unless B's response says it may. It is enforced by the
browser, not the server — `curl` ignores CORS entirely.

**Why it is needed here.** The frontend is served from `localhost:3000` (dev) or
`*.vercel.app` (prod). The API is on `localhost:8000` or Render. Different
origins. Without these headers the browser fetches the response and then refuses
to hand it to your JavaScript.

**The trade-off, stated honestly.** `allow_origin_regex=r"https://.*\.vercel\.app"`
combined with `allow_credentials=True` means **any** site on `vercel.app` — not
just yours — is an allowed origin. This is `CAVEAT-012`. It is a real widening,
accepted for preview-deploy convenience, and recorded rather than hidden.

**Middleware vs dependency.** CORS is middleware because it applies to *every*
response uniformly and needs to run outside the route. Authentication is a
**dependency** because it applies selectively and its result must be *passed
into* the handler. You will see that contrast properly on Day 8.

---

### 6.3 `/health` — read it as an instrument

```python
@app.get("/health")
async def health_check():
    services = {}

    try:
        await asyncio.to_thread(check_postgres_sync)
        services["postgres"] = "ok"
    except Exception as e:
        services["postgres"] = f"error: {e}"

    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        services["redis"] = "ok"
    except Exception as e:
        services["redis"] = f"error: {e}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.qdrant_url}/",
                headers={"api-key": settings.qdrant_api_key},
                timeout=5.0,
            )
            services["qdrant"] = ("ok" if resp.status_code == 200
                                  else f"http_{resp.status_code}")
    except Exception as e:
        services["qdrant"] = f"error: {e}"

    all_ok = all(v == "ok" for v in services.values())
    return {"status": "healthy" if all_ok else "degraded", "services": services}
```

**Four design choices, each worth naming.**

**1. Each dependency is checked separately and reported separately.** A single
boolean would collapse "Qdrant is down" and "Postgres is down" into one
uninformative `false`. The dictionary form means the *first* thing you learn is
which dependency failed.

**2. Failures are caught and reported, never raised.** `/health` returns **200**
even when degraded. That is deliberate: a health endpoint that 500s tells a
monitor "the app is broken" when the truth is "the app is fine and Redis is not".

**3. `asyncio.to_thread(check_postgres_sync)`.** psycopg2 is a **synchronous**
library. Calling it directly inside an `async def` would block the event loop —
freezing every other in-flight request for the duration. `to_thread` moves it to
a worker thread. This is the standard bridge between sync libraries and async
handlers, and you will see the same pattern nowhere else in this codebase,
because everything else that touches Postgres runs in a sync context anyway.

**4. A timeout on the Qdrant call, and only that one.** `timeout=5.0`. Without
it, an unreachable Qdrant would hang `/health` indefinitely — and `/health` is
what you poll to decide whether the service is up. A health check that can hang
is worse than none. (Postgres and Redis have their own client-level defaults.)

**How to read the output.**

```json
{"status": "degraded",
 "services": {"postgres": "ok", "redis": "ok",
              "qdrant": "error: [Errno -3] Temporary failure in name resolution"}}
```

That is a **DNS** failure, not a Qdrant failure. `Errno -3` is name resolution —
which connects straight back to the `dns:`/`dns_opt:` block you read on Day 1.

**Readiness, not liveness.** *Liveness* asks "is the process alive?" *Readiness*
asks "can it serve a request?" `/health` answers the second. The process can be
perfectly alive and unable to answer a single query because Qdrant is
unreachable. Day 1's rule — poll `/health` before minting a token — is a
readiness check.

---

### 6.4 Router mounting

```python
app.include_router(auth_router)       # prefix="/auth"
app.include_router(query_router)      # prefix="/api"
app.include_router(metrics_router)    # prefix="/api"
app.include_router(documents_router)  # prefix="/api/documents"
```

Each router declares its own prefix and tags where it is defined, so `main.py`
does not need to know any paths. The complete surface:

| Method | Path | Auth | Day |
|---|---|---|---|
| `GET` | `/health` | none | 4 |
| `POST` | `/auth/login` | none | 7 |
| `POST` | `/api/query` | any role | 6 |
| `POST` | `/api/query/stream` | any role | 6 |
| `GET` | `/api/metrics` | analyst+ | 44 |
| `POST` | `/api/documents/upload` | **admin** | 41 |
| `GET` | `/api/documents/pending` | **admin** | 41 |

Seven endpoints. That is the entire API. A system this capable having so small a
surface is itself a design statement.

---

## 7. Data flow

```
curl / browser
   │  TCP connect to 127.0.0.1:8000
   ▼
Docker port map 8000:8000                        (Day 1)
   ▼
uvicorn, bound 0.0.0.0:8000
   │  parses raw bytes into an HTTP request object
   ▼
Starlette (FastAPI's foundation)
   │  runs MIDDLEWARE — CORS
   ▼
Router: match (method, path) → handler function
   │  no match → 404;  path matches, method does not → 405
   ▼
DEPENDENCIES run first (Day 8)
   │  raise → handler never runs
   ▼
Body → Pydantic model                            (Day 5)
   │  invalid → 422, handler never runs
   ▼
YOUR HANDLER FUNCTION
   │  returns a dict / model
   ▼
Serialised to JSON, status attached, headers added
   ▼
back down through middleware
   ▼
bytes on the socket
```

**The thing to notice:** your handler is at the *bottom* of a stack of things
that can reject the request before it. 404, 405, CORS, dependency failure and
validation failure **all happen before your first line runs**. That is why "my
endpoint isn't being called" is usually not a bug in the endpoint.

---

## 8. Engineering decision — FastAPI, and why not the alternatives

**Problem.** An HTTP layer that is typed, async, self-documenting, and cheap to
extend.

**Decision.** FastAPI 0.111 with uvicorn.

| Alternative | Why not |
|---|---|
| **Flask** | Sync-first. This pipeline makes network calls to Qdrant, Gemini and Cohere; blocking the event loop on each would serialise concurrent requests. No type-driven validation — every endpoint would hand-parse |
| **Django + DRF** | Batteries included, most of which are unused here: no ORM (raw psycopg2 by decision), no templates (Next.js), no admin. You would carry the weight for the routing |
| **Raw Starlette** | FastAPI *is* Starlette plus validation and OpenAPI. Dropping to Starlette means writing both by hand |
| **Node / Express** | The entire ML stack — fastembed, ONNX, pdfplumber, psycopg2 — is Python |

**What the README says:** "Async, typed; LangGraph gives the router an
inspectable state machine rather than nested conditionals."

**Trade-off accepted.** Type-hint magic is excellent until you need to know
exactly what it did, and then you read FastAPI's source. Accepted for validation
and generated docs.

**At 10×.** FastAPI is not the constraint. `--workers 1` is, and that is a
memory decision, not a framework one (Day 45).

---

## 9. Failure modes

| Symptom | Meaning | First check |
|---|---|---|
| Connection refused | Nothing listening | `docker compose ps`; poll `/health` |
| 404 | Path does not exist | Is the router included? Is the prefix right? |
| 405 | Path exists, method does not | `POST` vs `GET` |
| 422 | Body failed validation | Read the body of the 422 — it names the field |
| 401 | Missing/expired/invalid token | Day 8 |
| 403 | Valid token, insufficient role | Day 9 |
| 500 | Unhandled exception | Container logs |
| 503 from `/auth/login` | Transient DB failure — **retryable** | `/health` |
| Browser: "blocked by CORS policy" | Origin not allowed | The response arrived; the browser withheld it |
| Import-time INFO logs missing | `basicConfig` moved below `app.*` imports | Check the top of `main.py` |

---

## 10. Hands-on experiment

### Experiment 1 — see the whole response

```bash
curl -i http://localhost:8000/health
```

`-i` prints headers. Read every one: `content-type`, `content-length`, `server`,
`date`. You are looking at the three parts of a response.

### Experiment 2 — make each failure happen

```bash
curl -i http://localhost:8000/nope                    # 404
curl -i -X GET http://localhost:8000/api/query        # 405
curl -i -X POST http://localhost:8000/api/query       # 401 — no token
```

The third is the interesting one: it fails at the **dependency**, before any
body validation, because dependencies run first.

### Experiment 3 — degrade a dependency on purpose

```bash
curl -s http://localhost:8000/health
docker compose stop redis
curl -s http://localhost:8000/health
docker compose start redis
```

Watch `status` flip to `degraded` while the response stays **200**. Read the
`redis` value — it is the exception text, which is a diagnostic, not a label.

### Experiment 4 — the generated documentation

Open <http://localhost:8000/docs>. Every endpoint, its parameters, its request
schema and its response schema — all derived from type hints. Nothing here was
written by hand.

### Experiment 5 — prove the logging-order claim

```bash
docker compose logs backend | head -30
```

Find the import-time lines (they appear before any request). Now, in a scratch
copy of `main.py`, move the `logging.basicConfig` block below the `app.*`
imports, restart, and look again. **Restore the file afterwards.**

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/main.py` and answer:

1. Why is `check_postgres_sync` a separate function rather than inline in the
   handler?
2. `/health` returns 200 even when degraded. Give one concrete reason that is
   better than returning 503.
3. Only one of the three checks sets an explicit timeout. Which, and why does it
   matter more than the other two?
4. `main.py` imports four routers but declares no paths. Where do the paths live?
5. What is `force=True` protecting against, specifically?

Then, using `curl` only:

6. Get a 404, a 405 and a 401 from three different requests, and say which layer
   produced each.

---

## 12. Self-check questions

**Basic**
1. What are the four parts of an HTTP request?
2. What is the difference between 401 and 403?
3. What is an endpoint?
4. What does CORS protect, and who enforces it?
5. How many endpoints does this API have?

**Code**
6. Why is `logging.basicConfig` the first executable statement in `main.py`?
7. What does `force=True` do?
8. Why `asyncio.to_thread(check_postgres_sync)` instead of calling it directly?
9. Which `/health` check has an explicit timeout, and why?
10. Where is `/api/query`'s path prefix declared?

**Why**
11. Why does `/auth/login` return 503 rather than 500 on a database failure?
12. Why is there no `DELETE` endpoint anywhere in this API?
13. Why is `/health` a readiness check rather than a liveness check?
14. Why is CORS middleware while auth is a dependency?
15. Why does `/health` report each service separately instead of one boolean?

**Debugging**
16. `/health` reports `qdrant: error: [Errno -3] Temporary failure in name
    resolution`. Is Qdrant down? What is your next step?
17. Your endpoint's first line never executes, and the client gets 422. What
    happened, and where?
18. Import-time INFO logs appear in the `backend` container and not in `worker`.
    What is the cause?

**System design**
19. Add a `DELETE /api/documents/{id}` endpoint. Name every layer that would have
    to change, including one outside the Python code.
20. `/health` currently checks three dependencies serially. At 10× traffic, name
    one problem with that and one way to fix it.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Because it is **synchronous** and must be handed to `asyncio.to_thread`,
   which takes a callable. Inlining it inside the `async def` would block the
   event loop.
2. Because a 5xx from a health endpoint tells a monitor "the application is
   broken", when the truth may be "the application is fine and one optional
   dependency is not". Returning 200 with a `degraded` body preserves the
   distinction and lets the caller decide. (Also: a load balancer configured to
   pull an instance out of rotation on a non-2xx would remove a
   partially-working instance.)
3. **Qdrant**, `timeout=5.0`. It matters most because Qdrant is a *public
   network* call — over the internet to Qdrant Cloud — where a hang is a real
   possibility. Postgres and Redis are on the compose network, one hop away.
   And a health check that can hang is worse than no health check, because
   `/health` is the thing you poll to decide whether the service is up.
4. In each router module: `APIRouter(prefix="/api", tags=["Query"])` in
   `api/query.py`, `prefix="/auth"` in `auth/router.py`, and so on. `main.py`
   deliberately knows no paths.
5. Against a **dependency that installs a root log handler on import**. Without
   `force=True`, `basicConfig` is a silent no-op when a handler already exists —
   so your configuration would appear to succeed and do nothing.
6. `curl -i http://localhost:8000/nope` → 404, produced by the **router** (no
   path match). `curl -i -X GET http://localhost:8000/api/query` → 405, also the
   router (path matched, method did not). `curl -i -X POST
   http://localhost:8000/api/query` → 401, produced by the **dependency**
   (`get_current_user`), which runs after routing and before the handler.

### §12 — Basic

1. Method, path (URL), headers, body.
2. 401 — "I do not know who you are." 403 — "I know who you are, and you may not
   do this."
3. One (method, path) pair, mapped to a handler function.
4. It protects a *user's* data from a malicious page reading responses from a
   site the user is logged into. It is enforced by the **browser**, not the
   server — `curl` ignores it entirely.
5. Seven.

### §12 — Code

6. Because importing `app.api.query` transitively imports `app.engines.router`,
   which logs at **module scope**. With no root handler installed, Python's
   `lastResort` handler (fixed at `WARNING`) discards that `INFO` line silently.
7. Removes any existing root handlers and installs the new configuration, so a
   dependency that already installed one cannot turn `basicConfig` into a no-op.
8. psycopg2 is synchronous. Calling it inside an `async def` blocks the event
   loop, freezing every other in-flight request for the duration of the query.
9. Qdrant. It is a public internet call where a hang is realistic, and `/health`
   must never hang because it is the readiness probe.
10. In `api/query.py`: `router = APIRouter(prefix="/api", tags=["Query"])`.

### §12 — Why

11. Because a transient database failure is **retryable** and is not a defect in
    the request. A 500 tells the caller nothing actionable; a 503 tells the eval
    runner and the frontend to try again. The comment in `auth/service.py` says
    exactly this, and notes it was added after several rounds of debugging
    intermittent login failures.
12. Because there is no delete **permission**. `sql/init.sql` grants only
    `SELECT, INSERT, UPDATE`, and the comment says `audit_log` is append-only
    "no UPDATE or DELETE granted, ever". The API surface reflects a constraint
    enforced a layer below it.
13. Because the process being alive says nothing about whether it can answer.
    `/health` actually attempts each dependency, so it answers "can it serve?".
14. CORS applies uniformly to every response and needs to run outside the route,
    so it is middleware. Auth applies selectively (`/health` and `/auth/login`
    are unauthenticated), and its **result** — the user dict — must be passed
    into the handler. A dependency can do both; middleware can do neither.
15. So the first thing you learn is **which** dependency failed. A single boolean
    collapses three different outages into one uninformative value.

### §12 — Debugging

16. **No — that is a DNS failure, not a Qdrant failure.** `Errno -3` is name
    resolution. Next step: check DNS from inside the container, and recall the
    `dns:`/`dns_opt:` block in `docker-compose.yml`, which exists because WSL2's
    resolver flaps. Do not restart Qdrant; it is probably fine.
17. FastAPI parsed the body against the Pydantic model, it failed, and the
    framework returned 422 **before invoking your handler**. This happens in the
    layer between routing and the handler, alongside dependency resolution. Read
    the 422 body — it names the offending field and the reason.
18. `worker.py` is a **separate entrypoint**. The Celery containers start at
    `app.worker:celery_app` and never import `main.py`, so `main.py`'s
    `basicConfig` never ran for them. That is precisely why `worker.py` carries
    an identical logging block with its own explanatory comment.

### §12 — System design

19. The endpoint in `api/documents.py`; an admin-only `require_role` dependency;
    the frontend call in `lib/api.ts` and a UI control; cascade handling for
    `financials` and `chunks` rows that reference the document; a **Qdrant**
    deletion path (Qdrant is a separate store with no foreign keys — see
    `CAVEAT-016`, where deleted "orphans" turned out to be production's corpus);
    and **outside Python: a `GRANT DELETE` migration**, because the app role
    currently cannot delete anything. That last one is the answer most people
    miss and is the point of the question.
20. **Problem:** the three checks run one after another, so `/health`'s latency
    is the *sum* of three network round-trips, and one slow dependency slows the
    probe for everyone. Under frequent polling that is wasted work and can itself
    contribute to load. **Fix:** run them concurrently with
    `asyncio.gather(...)`, so latency is the max rather than the sum. (Also
    acceptable: cache the result for a few seconds; or split into a cheap
    liveness endpoint and a deeper readiness one polled less often.)

---

## 14. MUST REMEMBER

```text
- Request = method + path + headers + body.  Response = status + headers + body
- 401 "who are you?" · 403 "I know, and no" · 422 validation · 503 retryable
- /health returns 200 even when degraded, and reports each service separately
- Logging is configured BEFORE any app.* import, with force=True. Do not move it
- worker.py needs its own copy, because it never imports main.py
- CORS is enforced by the BROWSER; curl ignores it
- Seven endpoints, total
```

## 15. MUST UNDERSTAND

```text
- Why a framework's value is the layers that reject a request BEFORE your handler
- Why /health is readiness, not liveness — and why that changes how you poll it
- Why 500 vs 503 is an information-preserving decision, not a cosmetic one
- Why the absence of DELETE is enforced by a database grant, not by the API
- Why a health check that can hang is worse than no health check
```

---

## 16. This connects to

```text
Day 3 — QueryState, the object a request becomes
   ↓
Day 4 — how a request physically arrives                    ← you are here
   ↓
Day 5 — the CONTRACT: what a valid request body looks like
   ↓
Day 6 — the same pipeline, streamed
```

Forward references:

- `Depends(get_current_user)` → **Day 8**
- `QueryRequest` and 422 → **Day 5**
- `role_filtered_response` → **Day 9**
- `CAVEAT-012` (the Vercel CORS regex) → **Day 42**
- `/api/metrics` and `cache_hit_rate_pct` → **Day 44**
- `--workers 1` and the memory ceiling → **Day 45**
