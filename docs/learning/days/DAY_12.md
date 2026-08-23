# Day 12 — Module-Level State: Lazy Singletons and Import Order

**Phase 3 · Weight: M (~90 min) · Prerequisites: Day 11**

---

## 1. Today's goal

By tonight you can:

- Explain what runs at **import time** versus **call time**, and why that
  distinction decides whether a container starts.
- Explain the lazy-singleton pattern in `retriever.py`: why the models are not
  loaded at import, and why they are never loaded twice.
- Explain why **two** entrypoints each configure logging, and what silently
  vanishes when one does not.
- Explain the rule this codebase applies to defaults: **a plausible-but-wrong
  default is worse than a crash** — and name the three places it is applied.

---

## 2. Why now

You now have types (Day 10) and control flow (Day 11). The last Python topic
before the database is **state that outlives a single call**: module globals,
lazy loading, and the ordering rules that come with them.

This also closes a Day 1 loop. You measured a cold model load and were told *"a
local semantic failure is not a defect until it reproduces on a warm process."*
Today you learn what "warm" means mechanically.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Cold vs warm `exec` | Day 1 | Today is the mechanism |
| `Settings()` at module scope | Day 10 | An import-time side effect |
| `get_graph()` singleton | Day 6 | Today generalises it |

---

## 4. Concept lesson

### 4.1 What `import` actually does

```python
import app.engines.retriever
```

Python:

1. Checks `sys.modules`. **If present, returns it immediately** — the module body
   does **not** run again.
2. Otherwise finds the file, compiles it, creates a module object, and
   **executes the whole file top to bottom**.
3. Stores it in `sys.modules`.

Two consequences that govern everything today:

- **A module's body is executed exactly once per process.** Every subsequent
  `import` is a dictionary lookup.
- **Anything at module scope runs during import.** Constants, decorators,
  `logger.info(...)`, `re.compile(...)`, `Settings()` — all of it, before your
  program has done any work.

**Mental model.** Importing is **unpacking a box**. It happens once; after that
everyone shares the same contents.

---

### 4.2 Import time versus call time

```python
import logging
logger = logging.getLogger(__name__)      # ← import time
MODEL = os.getenv("GEMINI_MODEL")         # ← import time
logger.info("resolved model: %s", MODEL)  # ← import time

def do_work():
    return MODEL                          # ← call time
```

**Why it matters here.** The backend, the Celery worker and the Celery beat
scheduler are three containers built from **one image**. They import overlapping
sets of modules. Anything that happens at import time happens in **all three**,
whether or not that container will ever use it.

Three concrete consequences in this codebase:

| Import-time work | Consequence |
|---|---|
| `_resolve_gemini_model()` raising if unset | Would crash the worker, which may never call an LLM. **So it is call-time instead** |
| `UPLOAD_DIR.mkdir(...)` in `api/documents.py` | Runs in every container that imports it. `CAVEAT-024` |
| `logger.info(...)` at module scope | Discarded unless logging is configured **first** |
| `re.compile(...)` at module scope | Paid once; correct — compiling per call would be waste |

---

### 4.3 The lazy singleton

**The problem.** `retriever.py` needs three ONNX models. Loading them costs
**~30 seconds** and hundreds of megabytes.

**Naive — load at import:**

```python
_dense_model = TextEmbedding("BAAI/bge-small-en-v1.5")   # at module scope
```

Every container importing `retriever` — including the Celery beat scheduler,
which will never retrieve anything — pays 30 s of startup and the memory. On a
512 MB tier that is not a slow start; it is an `Exited with status 137`.

**Naive — load per call:**

```python
def encode(q):
    return TextEmbedding(...).query_embed(q)   # 30 s EVERY query
```

**The lazy singleton:**

```python
_dense_model: Optional[TextEmbedding] = None

def _get_dense_model() -> TextEmbedding:
    global _dense_model
    if _dense_model is None:
        logger.info("Loading dense embedding model (ONNX/fastembed): %s", DENSE_MODEL_NAME)
        _dense_model = TextEmbedding(model_name=DENSE_MODEL_NAME, threads=1)
    return _dense_model
```

Loaded on **first use**, kept for the process lifetime.

**Mental model.** A **kettle in a shared office**. Nobody boils it when they
arrive; the first person who wants tea boils it, and it stays warm for everyone
after.

**This is precisely what "cold versus warm" means.** A fresh
`docker compose exec` is a **new process**, so `_dense_model is None` again and
the 30 s is paid again. A warm uvicorn has already paid it, and queries run in
0.36–0.41 s.

Hence `CLAUDE.md` §4:

> **A local semantic failure is not a defect until it reproduces on a warm
> process.**

---

### 4.4 Is `global` acceptable here?

`global` is usually a smell. Here it is the correct tool, and it is worth being
able to say why:

- The state is **genuinely process-wide** — one model per process is the goal,
  not an accident.
- It is **write-once**. Nothing sets it back to `None`.
- The alternatives are worse: a class with a class attribute is the same global
  with ceremony; `functools.lru_cache` on a no-argument function is the same
  thing with less obvious semantics; dependency injection would thread three
  models through every call site.

**The thread-safety question, answered honestly.** Two threads could both see
`None` and both construct a model. The loser's model is garbage-collected; the
winner's is kept. The cost is one wasted load, and the outcome is still correct
because the models are stateless. A lock would be strictly correct and is not
present — a defensible trade, not an oversight, and worth knowing rather than
assuming.

---

### 4.5 The defaults rule

> **A plausible-but-wrong default is worse than a crash.**

A crash costs five minutes. A wrong default *works*, silently, and poisons
everything downstream.

Three applications of this rule in this codebase:

| Value | Default | Consequence of a default |
|---|---|---|
| `GEMINI_MODEL` | **none — raises** | Two full eval sweeps reported under a model that never served a call |
| `JWT_SECRET` | **none — app will not start** | Every deployment shares a signing key anyone can read |
| `QDRANT_URL` | `http://qdrant:6333` in `Settings` | *Has* a default — and `retriever.py` **raises** instead |

That third row is the interesting one, and you will see why in §6.3.

---

## 5. The actual LedgerMind files

```
File:  backend/app/engines/retriever.py  (lines 60–170)
       Four lazy singletons: dense, sparse, reranker, Qdrant client (+ Cohere)

File:  backend/app/main.py               (lines 1–25)
File:  backend/app/worker.py             (lines 1–30)
       Two entrypoints, two identical logging blocks, one shared reason

File:  backend/app/core/config.py        (19 lines)
       Settings() constructed at module scope

File:  backend/app/engines/graph.py      (lines 110–132)
       The compiled-graph singleton
```

---

## 6. Deep code walkthrough

### 6.1 Four singletons, one pattern, four different reasons

```python
_dense_model: Optional[TextEmbedding] = None
_sparse_model: Optional[SparseTextEmbedding] = None
_reranker_model: Optional[TextCrossEncoder] = None
_qdrant_client: Optional[QdrantClient] = None
_cohere_client = None
```

**STATE BEFORE (import).** Five `None`s. Import cost is the `import` statements
themselves — measured at ~2.77 s and, importantly, **connection-free**
(`conftest.py` verified this to justify its network guard).

**STATE AFTER first query.** Whichever are needed are populated and stay so.

Each has a different reason for existing:

| Singleton | Why lazy | Reason |
|---|---|---|
| `_dense_model` | ~30 s + RAM | The 512 MB ceiling |
| `_sparse_model` | same | same |
| `_reranker_model` | same | **and it may never load at all** — Cohere is primary |
| `_qdrant_client` | needs env vars | Reading env at import couples import to configuration |
| `_cohere_client` | optional | Absent key = a supported configuration, not an error |

**The reranker is the interesting one.** If `COHERE_API_KEY` is set, the local
ONNX cross-encoder is **never loaded** — the memory is never spent. It loads only
when Cohere fails. Lazy loading is not an optimisation here; it is what makes the
fallback affordable.

---

### 6.2 `_get_cohere_client` — three failure modes, three answers

```python
def _get_cohere_client():
    global _cohere_client
    if _cohere_client is None:
        api_key = os.getenv("COHERE_API_KEY")
        if api_key:
            try:
                import cohere
                logger.info("Initializing Cohere client for cloud reranking (0MB RAM)")
                _cohere_client = cohere.Client(api_key=api_key)
            except ImportError:
                logger.error("COHERE_API_KEY set but 'cohere' package not installed.")
                return None
            except Exception as e:
                logger.error("Failed to initialize Cohere client: %s", e)
                return None
    return _cohere_client
```

Three distinct situations, deliberately distinguished:

1. **No key** → returns `None` silently. A **supported configuration**: run
   without Cohere and the local reranker takes over.
2. **Key set, package missing** → `logger.error` and `None`. This is a
   **misconfiguration** — someone intended Cohere and it cannot work. Loud, and
   still degrades.
3. **Key set, construction failed** → `logger.error` and `None`. An operational
   failure.

**The asymmetry is the design.** Case 1 is silent because it is a choice; cases
2 and 3 are loud because they are mistakes. All three return `None` so the caller
has one contract.

**Note the lazy `import cohere` inside the function.** If the package is missing,
importing `retriever` still succeeds. The dependency is optional *at import*, not
just at runtime.

**And note the subtle re-entry cost.** On failure `_cohere_client` stays `None`,
so the **next call retries** — including re-reading the environment and
re-importing. For a persistent failure that is a small repeated cost per query.
Deliberate or not, it means a transient construction failure self-heals.

---

### 6.3 `_get_qdrant_client` — raising instead of defaulting

```python
def _get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        if not qdrant_url:
            raise RuntimeError("QDRANT_URL environment variable not set")
        logger.info("Connecting to Qdrant: %s", qdrant_url)
        _qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key,
                                      timeout=QDRANT_TIMEOUT_SECONDS)
    return _qdrant_client
```

**`Settings.qdrant_url` has a default of `http://qdrant:6333`. This function
ignores `Settings` entirely and raises.** Why?

Because a default that points at **local Docker Qdrant** while you believe you
are on **Qdrant Cloud** produces a working system searching an empty or stale
collection. You would get plausible answers from the wrong index. That is the
exact family as the `GEMINI_MODEL` disaster: *the wrong value works*.

The evidence that this is a live concern is in `CLAUDE.md` §4:

> `UserWarning: Api key is used with an insecure connection` → you are on local
> Docker Qdrant, not Cloud. **Every measurement is invalid.**

`/health` uses `settings.qdrant_url` (with the default) because a health check
that cannot start is useless. **The retriever raises because a retriever pointed
at the wrong store is worse than one that will not run.** Two consumers, two
correct answers, for the same value.

**`timeout=QDRANT_TIMEOUT_SECONDS` — 10 seconds, and the comment says why:**

```python
# An unbounded call to an external service in the request path is the same
# structural defect fixed for Gemini on 2026-07-29: without a timeout, a
# fallback keyed on exceptions can never fire, so a stall stays a stall.
# Warm queries measured at 0.36-0.41s against Qdrant Cloud (2026-08-01), so
# 10s is ~25x headroom
```

**Note where the timeout lives: on the client, set once at construction.** A
per-call timeout would have to be remembered at every call site. Putting it on
the singleton means it cannot be forgotten. That is a real advantage of the
pattern beyond avoiding repeated setup.

---

### 6.4 Two entrypoints, two logging blocks

`main.py` and `worker.py` open with the same eleven lines. `worker.py`'s comment
explains why the duplication is correct:

```python
# Identical reasoning to app/main.py, which is the OTHER entrypoint into this
# codebase. The worker and beat containers start at `app.worker:celery_app`
# and never import main.py, so main.py's basicConfig never ran for them:
# every import-time INFO log under app.* fell through to logging.lastResort,
# which is fixed at WARNING, and was discarded silently. The engines'
# module-scope lines (e.g. router's resolved GEMINI_MODEL) were therefore
# visible in the backend container and invisible in the worker, for the same
# code.
```

**"visible in the backend container and invisible in the worker, for the same
code."** That sentence is the whole lesson of import-time side effects. Identical
source, different observable behaviour, decided entirely by which module was
imported first.

**`force=True`** appears in both, with an extra reason in the worker:

> Celery installs its own logging on worker startup; `force=True` is what keeps
> this from being overridden.

**Is this duplication a violation of "single source of truth"?** No — and the
distinction is worth holding. The single-source rule is about **facts that can
drift into disagreement** (a metric's aliases, a model name). This is **an
initialisation step that must happen before any import**, and factoring it into a
shared module would require *importing that module* — which is itself the
ordering problem. The duplication is structural, not incidental.

---

### 6.5 `get_graph()` — the same pattern, a different resource

```python
_compiled_graph = None

def get_graph():
    """Returns the compiled graph singleton, building it on first call."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
```

And `build_graph`'s docstring:

> Compiled once at FastAPI startup and reused across requests (graph compilation
> is not free — don't rebuild per-request).

Same shape as the models: expensive to build, immutable once built, needed by
every request. Note that **importing `graph.py` imports every engine**, which
transitively imports `retriever.py` — but the *models* still do not load, because
they are behind their own lazy functions. **Lazy loading composes.**

---

### 6.6 `Settings()` at module scope — the deliberate exception

```python
settings = Settings()      # runs at IMPORT
```

Everything else today is lazy. This is eager, on purpose: validation must happen
at **startup**, not on the first login. A missing `JWT_SECRET` should crash the
container, not produce a 500 for the first user.

**A second, quieter consequence.** Every module that does
`from app.core.config import settings` gets the **same instance** — because the
module body ran once. Configuration is therefore consistent across the process by
construction, not by discipline.

---

## 7. Data flow — a process's lifetime

```
docker compose up
   │
   ▼
uvicorn imports app.main
   │
   ├─ logging.basicConfig(force=True)          ← FIRST. Not negotiable
   │
   ├─ import app.api.query
   │     └─ import app.engines.graph
   │           └─ import app.engines.retriever
   │                 ├─ COLLECTION_NAME = "ledgermind_chunks"   ← import time
   │                 ├─ QDRANT_TIMEOUT_SECONDS = 10             ← import time
   │                 ├─ _dense_model = None                     ← import time
   │                 └─ (fastembed imported; NO model loaded)
   │           └─ import app.engines.router
   │                 └─ logger.info(...)   ← VISIBLE, because logging ran first
   │
   ├─ from app.core.config import settings
   │     └─ Settings()  ← validates NOW. Missing JWT_SECRET = startup crash
   │
   ▼
app = FastAPI(...)  ;  routers included  ;  uvicorn serves
   │
   │  ── PROCESS IS UP.  ~2.8 s.  Memory: small. ──
   │
   ▼
FIRST QUERY ARRIVES
   ├─ get_graph()          → builds and caches the graph      (~ms)
   ├─ _get_dense_model()   → loads bge-small                  (~10 s, ~130 MB)
   ├─ _get_sparse_model()  → loads BM25                       (~5 s)
   ├─ _get_qdrant_client() → reads env, raises if unset       (~ms)
   └─ _get_cohere_client() → reads env, or None               (~ms)
   │
   │  ── FIRST QUERY: SLOW.  This is COLD. ──
   ▼
SECOND QUERY
   └─ every _get_* returns the cached object                  0.36–0.41 s
   │
   │  ── WARM. This is the only state worth measuring. ──
```

---

## 8. Engineering decision — lazy singletons over eager construction

**Problem.** Three ONNX models and two network clients, needed by *some*
requests in *some* containers, inside a 512 MB ceiling.

**Decision.** Module-level `None` plus a `_get_*` accessor per resource.

| Alternative | Why not |
|---|---|
| **Eager at import** | Every container pays ~30 s and hundreds of MB, including beat, which will never retrieve. On 512 MB that is an OOM kill |
| **Per-call construction** | 30 s per query |
| **`functools.lru_cache`** | Functionally equivalent for a no-arg function; less explicit, and hides that this is process-wide state |
| **A DI container** | Real machinery for five objects, and would have to thread through LangGraph nodes that take only `state` |
| **A separate model server** | The correct answer at scale. Another service, another network hop, and RAM this project does not have |

**Trade-offs accepted.**

- **The first query is slow.** Visible to a user; invisible in every subsequent
  measurement. Handled by *discipline* (warm before measuring) rather than by
  code.
- **No lock.** Two threads can duplicate a load. Costs one wasted load; correct
  because the models are stateless.
- **Failure is deferred.** A missing `QDRANT_URL` surfaces on the first query,
  not at startup. Mitigated because `/health` touches Qdrant — so a *readiness
  check* catches it before a user does. That is `/health` earning its keep.

**Current validity.** Correct for this constraint. **The constraint is the
reason** — on a machine with 8 GB and a warm-up hook, eager loading with a
startup probe would be better.

**At 10×.** Multiple uvicorn workers means one copy of every model **per worker**
(Day 45). The fix is not a different singleton pattern; it is moving inference
out of the web process entirely.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| Container OOMs at startup | Something loads a model at import |
| First query 30 s, rest fast | **Working as intended** — cold vs warm |
| Every query 30 s | A model constructed per call rather than cached |
| Import-time INFO logs missing | `basicConfig` not first, or missing in that entrypoint |
| Logs visible in backend, absent in worker | `worker.py`'s logging block removed |
| `RuntimeError: QDRANT_URL not set` | Deliberate — a default would be worse |
| Searching an empty collection | `QDRANT_URL` points at local Docker, not Cloud |
| Config error on first request instead of startup | Something moved `Settings()` out of module scope |
| `/tmp/ledgermind_uploads` created in every container | `CAVEAT-024` — import-time `mkdir` |
| Two models loaded briefly | The unlocked race. Harmless |

---

## 10. Hands-on experiment

### Experiment 1 — a module body runs once

```bash
docker compose exec -T backend python -c "
import sys
print('retriever already imported?', 'app.engines.retriever' in sys.modules)
import app.engines.retriever as r1
print('after 1st import:', 'app.engines.retriever' in sys.modules)
import app.engines.retriever as r2
print('same object?', r1 is r2, ' <- the body did NOT run twice')
"
```

### Experiment 2 — import is cheap; the model is not

```bash
docker compose exec -T backend python -c "
import time
t=time.perf_counter(); import app.engines.retriever as r
print(f'import                 : {time.perf_counter()-t:6.2f}s')
print('  _dense_model is None :', r._dense_model is None, ' <- nothing loaded')
t=time.perf_counter(); r._get_dense_model()
print(f'first _get_dense_model : {time.perf_counter()-t:6.2f}s  <- COLD')
t=time.perf_counter(); r._get_dense_model()
print(f'second call            : {time.perf_counter()-t:6.4f}s  <- WARM')
"
```

**Those two numbers are the entire point of the pattern.**

### Experiment 3 — cold vs warm, across processes

```bash
for i in 1 2 3; do
  docker compose exec -T backend python -c "
import time, app.engines.retriever as r
t=time.perf_counter(); r._get_dense_model()
print(f'  exec $i: {time.perf_counter()-t:.2f}s')"
done
```

**Every run is slow.** Each `exec` is a **new process**, so the module body runs
again and `_dense_model` is `None` again. Now contrast with the *server*, which
loaded once and stays warm — run the same query twice through the API and compare
`latency_ms`.

### Experiment 4 — the defaults rule, both halves

```bash
docker compose exec -T backend python -c "
from app.core.config import settings
import os
print('Settings.qdrant_url (has a default):', settings.qdrant_url)
print('os.getenv(QDRANT_URL)              :', os.getenv('QDRANT_URL'))
"

docker compose exec -T -e QDRANT_URL= backend python -c "
import app.engines.retriever as r
try:
    r._get_qdrant_client()
except RuntimeError as e:
    print('retriever RAISES:', e)
    print('  <- it ignores the Settings default on purpose')
"
```

### Experiment 5 — logging order, proven

```bash
docker compose logs backend | grep -i -m 5 "GEMINI_MODEL\|Loading\|graph compiled"
echo "--- now the worker, which has its OWN logging block ---"
docker compose logs worker | head -20
```

Both show import-time lines. Now consider what §6.4 says would happen without
`worker.py`'s block: identical code, invisible logs, in one container only.

### Experiment 6 — the reranker that never loads

```bash
docker compose exec -T backend python -c "
import os, app.engines.retriever as r
print('COHERE_API_KEY set?  ', bool(os.getenv('COHERE_API_KEY')))
print('_reranker_model      ', r._reranker_model)
print()
print('If Cohere is configured, the local ONNX cross-encoder is never loaded')
print('and its memory is never spent. Lazy loading is what makes the')
print('fallback affordable on a 512 MB tier.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/retriever.py` (lines 60–170), `backend/app/main.py`
and `backend/app/worker.py`:

1. Name all five lazily-constructed objects in `retriever.py`. Which one may
   never be constructed at all in normal operation, and why is that valuable?
2. `_get_cohere_client` has three failure paths. Which is silent, which are
   loud, and what principle decides?
3. `_get_qdrant_client` raises rather than using `Settings`' default. Give the
   concrete bad outcome a default would produce.
4. `main.py` and `worker.py` contain the same logging block. Why is that not a
   single-source-of-truth violation?
5. Find `_get_gemini` in `llm/client.py`. It declares `global _gemini_client` and
   **never assigns it**. What does that mean, and where is it recorded?

---

## 12. Self-check questions

**Basic**
1. How many times does a module body run per process?
2. What is a lazy singleton?
3. What does "warm" mean here, precisely?
4. Which entrypoints configure logging?
5. What is loaded at import in `retriever.py`?

**Code**
6. What does `_get_dense_model()` do on the second call?
7. Why is `import cohere` inside a function?
8. Where is the Qdrant timeout set, and why there?
9. Why is `settings = Settings()` at module scope?
10. What does `force=True` protect against, and what extra reason applies in the
    worker?

**Why**
11. Why are the models not loaded at import?
12. Why does `_get_qdrant_client` raise instead of defaulting?
13. Why does `_get_cohere_client` return `None` silently when no key is set?
14. Why is a missing `QDRANT_URL` at first query acceptable when a missing
    `JWT_SECRET` at first login would not be?
15. Why is the unlocked singleton race acceptable?

**Debugging**
16. Every query takes 30 s, not just the first. What happened?
17. Logs appear in the backend and not the worker, same code. Cause?
18. Retrieval returns nothing and no error. Name the environment check you run
    first, and the warning that would confirm it.

**System design**
19. Add a fourth model. Write the pattern, and name the one thing you must check
    against the memory ceiling before shipping it.
20. Two uvicorn workers are added. Trace what happens to the five singletons, and
    say why the fix is not a better singleton.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `_dense_model`, `_sparse_model`, `_reranker_model`, `_qdrant_client`,
   `_cohere_client`. **`_reranker_model` may never be constructed**: if
   `COHERE_API_KEY` is set, reranking goes to the Cohere API and the local ONNX
   cross-encoder is never loaded. That is valuable because its memory is never
   spent — on a 512 MB tier, lazy loading is what makes having a fallback
   affordable at all.
2. **Silent:** no key set — a *supported configuration*, not a mistake.
   **Loud:** key set but the `cohere` package missing (`ImportError`), and key
   set but construction failed. The principle: **a choice is silent, a mistake is
   loud.** All three return `None`, so the caller has a single contract.
3. A default of `http://qdrant:6333` while you believe you are on Qdrant Cloud
   gives a **working system searching an empty or stale local collection** — you
   get plausible answers from the wrong index, with no error. `CLAUDE.md` §4
   records the signature: `UserWarning: Api key is used with an insecure
   connection` means every measurement in that session is invalid.
4. Because the single-source rule is about **facts that can drift into
   disagreement**. This is an **initialisation step that must run before any
   import** — factoring it into a shared module would require importing that
   module, which is the ordering problem itself. The duplication is structural,
   not incidental.
5. It declares `global _gemini_client` and then returns a freshly constructed
   client without ever assigning the global — so the "singleton" is dead code and
   a new client is built on every call. Harmless (construction opens no
   connection, and the comment explains the client must be rebuilt when the
   timeout bound changes), but the `global` is misleading. Recorded as
   **`CAVEAT-020`**.

### §12 — Basic

1. Exactly once. Subsequent imports are a `sys.modules` lookup.
2. Module-level state initialised to `None`, constructed on first access by an
   accessor function, then reused for the process lifetime.
3. The process has already constructed the expensive objects, so `_get_*` returns
   a cached reference. Queries then take 0.36–0.41 s instead of ~30 s.
4. `main.py` (uvicorn) and `worker.py` (Celery worker and beat). They are the two
   entrypoints, and neither imports the other.
5. Constants, the `None` placeholders, and the third-party packages themselves
   (`fastembed`, `qdrant_client`). **No models.** Measured ~2.77 s and
   connection-free.

### §12 — Code

6. Sees `_dense_model is not None` and returns the cached object immediately.
7. So that a missing `cohere` package does not break the **import** of
   `retriever.py`. The dependency is optional at import time, not merely at
   runtime.
8. On the `QdrantClient` at construction, inside the singleton. Because a
   per-call timeout must be remembered at every call site; setting it once on the
   shared client means it cannot be forgotten.
9. So configuration is validated at **startup**. A missing `JWT_SECRET` crashes
   the container rather than producing a 500 for the first user. It also means
   every importer shares one instance.
10. Against a dependency that already installed a root log handler, which would
    otherwise make `basicConfig` a silent no-op. In the worker there is an extra
    reason: **Celery installs its own logging on startup**, and `force=True` is
    what stops that overriding this configuration.

### §12 — Why

11. Because three containers are built from one image and import overlapping
    modules. Eager loading would cost every container ~30 s and hundreds of MB —
    including Celery beat, which never retrieves anything. On a 512 MB tier that
    is an OOM kill, not a slow start.
12. Because a wrong-but-plausible value **works**: it searches a local, empty or
    stale collection and returns confident answers from the wrong index. A crash
    costs five minutes; that costs a whole set of invalid measurements.
13. Because running without Cohere is a **supported configuration** — the local
    ONNX reranker takes over. Logging an error for a deliberate choice trains
    readers to ignore errors.
14. Because `/health` touches Qdrant, so a **readiness check catches it before a
    user does** (Day 4). There is no equivalent probe for `JWT_SECRET`, and its
    failure would be a live 500 during a login. Different detection paths,
    different acceptable failure timing.
15. Because the loss is one duplicated model load, and the models are
    **stateless** — the loser is garbage-collected and the winner is used by
    everyone. A lock would be strictly correct and buys almost nothing. Worth
    knowing rather than assuming.

### §12 — Debugging

16. The model is being constructed per call instead of cached — either the
    accessor was bypassed and `TextEmbedding(...)` is called directly, or the
    `global` declaration was dropped so the assignment created a *local* variable
    and the module-level `None` was never updated. That second failure is
    exactly the shape of `CAVEAT-020`, and it is silent.
17. `worker.py`'s `logging.basicConfig` block was removed or moved below the
    `app.*` imports. The worker never imports `main.py`, so nothing else
    configures logging for it, and every import-time INFO line falls through to
    `logging.lastResort` (fixed at `WARNING`) and is discarded.
18. `docker compose exec -T backend printenv QDRANT_URL` — confirm it is the
    **Cloud https URL**, not `http://qdrant:6333`. The confirming warning is
    `UserWarning: Api key is used with an insecure connection`, which means you
    are on local Docker Qdrant. Also worth knowing: **an empty candidate set is a
    network signature; a low-scoring one is a retrieval signature.**

### §12 — System design

19. ```python
    _new_model: Optional[SomeModel] = None

    def _get_new_model() -> SomeModel:
        global _new_model
        if _new_model is None:
            logger.info("Loading new model: %s", NEW_MODEL_NAME)
            _new_model = SomeModel(model_name=NEW_MODEL_NAME, threads=1)
        return _new_model
    ```
    **The thing to check:** whether it can be loaded **simultaneously** with the
    others inside 512 MB. Lazy loading spreads the cost over time but does not
    reduce the peak — if a single query needs dense + sparse + reranker + the new
    model at once, the peak is their sum. Also pass `threads=1`, matching the six
    `ENV` lines in the Dockerfile, or ONNX will size its pool from the host's
    core count.
20. Each worker is a **separate process** with its own `sys.modules`, so each
    gets its own copy of all five singletons — five objects become ten, and the
    model memory doubles. The fix is not a better singleton, because processes
    cannot share Python objects: it is to **move inference out of the web
    process** (a model server, or Cohere for reranking as this codebase already
    does for exactly this reason). That is Day 45's argument, and it is why the
    scaling limit here is memory rather than the transport or the framework.

---

## 14. MUST REMEMBER

```text
- A module body runs ONCE per process; later imports are a dict lookup
- Anything at module scope runs at IMPORT — in every container that imports it
- Lazy singleton: module-level None + a _get_* accessor + `global`
- COLD = new process, ~30s.  WARM = cached, 0.36-0.41s
- A local semantic failure is not a defect until it reproduces WARM
- main.py AND worker.py each configure logging first, with force=True
- GEMINI_MODEL and JWT_SECRET have NO default. QDRANT_URL raises in retriever
- A plausible-but-wrong default is worse than a crash
- Settings() runs at import, deliberately — validation belongs at startup
```

## 15. MUST UNDERSTAND

```text
- Why import-time vs call-time decides whether a container starts at all
- Why lazy loading is what makes having a FALLBACK affordable, not just a
  startup optimisation
- Why a choice is silent and a mistake is loud, in the same function
- Why the same value (QDRANT_URL) correctly has a default in one consumer and
  raises in another
- Why duplicated logging setup is NOT a single-source-of-truth violation
- Why lazy loading spreads cost over time but does not reduce PEAK memory
```

---

## 16. This connects to

```text
Day 10 — types
Day 11 — control flow
   ↓
Day 12 — state that outlives a call                ← END OF PHASE 3
   ↓
Day 13 — the database: where truth is actually stored
```

Forward references:

- `_get_dense_model` in retrieval → **Day 25**
- `_get_sparse_model` and BM25 → **Day 26**
- `_get_reranker` and the two score scales → **Day 28**
- `_get_qdrant_client` and payload filters → **Days 21, 27**
- `GEMINI_MODEL`'s no-default rule in full → **Day 19**
- `CAVEAT-020` (`_get_gemini`'s unassigned global) → **Day 19**
- Workers, memory and the 512 MB ceiling → **Day 45**
