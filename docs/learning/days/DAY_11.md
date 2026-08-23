# Day 11 — Context Managers, Generators, Async

**Phase 3 · Weight: H (~120 min) · Prerequisites: Days 6, 10**

---

## 1. Today's goal

By tonight you can:

- Explain what `with` guarantees, write a `@contextmanager`, and say precisely
  what `with conn:` does — **and what it does not do**.
- Explain `SET` versus `SET LOCAL` and why choosing the wrong one is a
  cross-tenant data leak waiting for a connection pool.
- Explain generators, `async`/`await`, and why blocking the event loop freezes
  *every* in-flight request.
- Explain `asyncio.Queue` and `create_task`, and re-derive Day 6's
  never-cancel/unbounded-queue pair from first principles.

---

## 2. Why now

Day 6 showed you `event_stream`, `_run_graph`, `asyncio.Queue` and
`create_task` and asked you to accept them. Day 10 gave you the types. Today the
control flow. After today, the only thing left before the database is
module-level state (Day 12).

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| The SSE generator | Day 6 | Today is the machinery behind it |
| `TypedDict` vs objects | Day 10 | `db_transaction` yields a connection, not a cursor |
| psycopg2 is synchronous | Day 4 | The reason `asyncio.to_thread` exists in `/health` |

---

## 4. Concept lesson

### 4.1 The problem `with` solves

Acquire a resource, use it, release it — **even if something goes wrong**.

```python
conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute("SELECT 1")     # ← raises
conn.close()                # ← never runs. Connection leaked.
```

`try/finally` fixes it and is verbose, and must be written correctly at every
call site. Miss one and you leak a connection under exactly the conditions
(errors, load) when you can least afford it.

**`with` moves the guarantee into the object:**

```python
with psycopg2.connect(url) as conn:
    ...
```

Python calls `__enter__` on entry and **`__exit__` on exit — normal, exceptional,
`return`, `break`.**

**Mental model.** `with` is **a borrowed key**. However you leave the room, the
key goes back.

---

### 4.2 `@contextmanager` — writing one with a generator

The class form needs `__enter__` and `__exit__`. The decorator form is one
function with one `yield`:

```python
from contextlib import contextmanager

@contextmanager
def borrowed_key():
    key = take_key()          # everything before yield = __enter__
    try:
        yield key             # the `with` body runs HERE
    finally:
        return_key(key)       # everything after = __exit__
```

The `try/finally` around the `yield` is what makes it exception-safe. Without it,
an exception in the body propagates out of the `yield` and the cleanup never
runs — the very bug `with` exists to prevent.

---

### 4.3 What `with conn:` actually does — and the trap

psycopg2's connection is itself a context manager, and **its behaviour surprises
people**:

```python
with conn:                # __enter__: begins a transaction
    with conn.cursor() as cur:
        cur.execute(...)
# __exit__: COMMIT on clean exit, ROLLBACK on exception
# __exit__ does NOT close the connection
```

> **`with conn:` commits or rolls back. It does not close.**

That is why every function in this codebase that opens a connection also has an
explicit `conn.close()` or a `finally` that closes it. Relying on `with conn:` to
close would leak a connection per call — and with `CAVEAT-013` (a new connection
per statement), that is a lot of connections.

---

### 4.4 `SET` versus `SET LOCAL` — the most consequential four letters today

Postgres session variables:

| | Scope | Cleared by |
|---|---|---|
| `SET x = v` | the **session** (the connection) | disconnect, or another `SET` |
| `SET LOCAL x = v` | the **current transaction** | `COMMIT` or `ROLLBACK`, automatically |

**Why this is a security decision here.** `app.tenant_id` drives every RLS policy
(Day 14). On a **pooled or reused** connection:

```
request A: SET app.tenant_id = 'tenant-A';  ... work ...  (connection returns to pool)
request B: (gets the same connection)       ... forgets to set ...
           SELECT * FROM financials         ← still scoped to TENANT A
```

**A cross-tenant read, caused by four missing letters.** `SET LOCAL` cannot do
this: the value dies with the transaction, so a connection handed back to a pool
carries nothing.

`db/session.py`'s docstring says exactly this:

```python
"""
CRITICAL: uses SET LOCAL, not SET. SET LOCAL is scoped to the current
transaction and clears automatically on COMMIT/ROLLBACK. A bare SET on a
pooled/reused connection can leak one tenant's setting into the next
request. Same class of bug as the superuser-bypasses-RLS issue fixed in
Phase 4 -- do not "simplify" this to a plain SET.
"""
```

**Note the honest complication.** `ingestion/pipeline.py` and `db_loader.py` use
plain `SET app.tenant_id` — because they are long-running batch jobs that own
their connection for its whole life and are not pooled. Different context,
different correct answer. The rule is not "always `SET LOCAL`"; it is *"`SET
LOCAL` wherever a connection could be reused by another request"*, and the
request path is exactly that place.

---

### 4.5 Generators

```python
def counter():
    print("start")
    yield 1
    print("between")
    yield 2
    print("end")

g = counter()          # prints NOTHING
next(g)                # prints "start",   returns 1
next(g)                # prints "between", returns 2
next(g)                # prints "end",     raises StopIteration
```

A generator **suspends at each `yield`, keeping its locals**, and resumes where
it left off.

**Two things this buys:**

1. **Laziness.** `range(10_000_000)` does not build a list.
2. **Producing over time.** An SSE handler cannot `return` — it must emit as
   events happen. That *is* a generator.

---

### 4.6 `async`/`await`, and the one rule

**The problem.** A web server spends most of its time **waiting** — for a
database, for Qdrant, for Gemini. With one thread per request, waiting threads
consume memory doing nothing.

**Async concurrency.** One thread, an **event loop**, and functions that
voluntarily yield control while waiting:

```python
async def handler():
    result = await some_io()   # ← "I'm waiting; run something else"
    return result
```

**Concurrency, not parallelism.** One thread. `await` marks a *suspension point*.

**The one rule that matters:**

> **Never do blocking work inside `async def`.**

There is no preemption. A blocking call freezes the **entire loop** — every
in-flight request, not just yours.

**Which is why `/health` does this** (Day 4):

```python
await asyncio.to_thread(check_postgres_sync)
```

psycopg2 is synchronous. Calling it directly inside `async def` would block the
loop for the duration of the query.

**And now the honest observation about this codebase.** The graph nodes —
`quant_engine_node`, `audit_writer_node` — call psycopg2 **directly**, and they
run inside `await graph.ainvoke(...)`. LangGraph runs sync node functions in a
thread pool, which is why this works. It is worth knowing that the safety comes
from *LangGraph's* behaviour, not from anything in this codebase — and that a
change in how nodes are invoked would make every DB call a loop-blocker.

---

### 4.7 `asyncio.Queue` and `create_task`

**`create_task(coro)`** schedules a coroutine to run **independently**. It starts
immediately; you do not `await` it unless you want its result.

**`asyncio.Queue`** is an async-aware queue: `await put()` suspends when full (if
bounded), `await get()` suspends when empty.

Together they decouple a **producer** from a **consumer**:

```
create_task(producer)  ──put──►  Queue  ──get──►  consumer (the generator)
```

That is exactly Day 6's design, and today you can say why each half was chosen.

---

## 5. The actual LedgerMind files

### `backend/app/db/session.py` — 46 lines, mostly docstring

```
File:        backend/app/db/session.py (46 lines)
Purpose:     One transaction helper: open, set the RLS tenant, yield, commit/rollback, close
Why it exists: So the SET LOCAL discipline lives in one place
Who imports it: auth/service.py, api/documents.py, api/metrics.py
Entry point: db_transaction(tenant_id: str | None)
Data in:     a tenant id, or None for the auth bootstrap
Data out:    a psycopg2 CONNECTION (not a cursor)
```

**Read its scope note, which is unusually candid:**

```python
"""
USAGE SCOPE (Phase 5): this is currently used ONLY by auth/service.py's
login lookup. quant_engine.py (and presumably contradiction.py /
audit_writer.py, following the same pattern) open their own psycopg2
connections per call and run `SET LOCAL app.tenant_id` themselves using
tenant_id read from QueryState -- they do not need a connection injected
from the HTTP layer.
"""
```

**"and presumably"** — the docstring is *not certain what the rest of the
codebase does*. That is honest and it is also a smell: the helper that owns the
transaction discipline does not know who follows it. And as Day 5 established,
its next sentence states an assumption `api/query.py` breaks (`CAVEAT-001`).

**The helper is also not quite as narrowly used as it says** — `api/documents.py`
and `api/metrics.py` both use it too. Documentation drift, again.

---

## 6. Deep code walkthrough

### 6.1 `db_transaction` — every line load-bearing

```python
def _get_raw_connection():
    return psycopg2.connect(settings.database_url)

@contextmanager
def db_transaction(tenant_id: str | None):
    """
    Opens a transaction, sets app.tenant_id for RLS (if provided), yields a
    connection, commits on success / rolls back on exception.

    tenant_id=None is reserved for the auth bootstrap case (login lookup
    only) -- see migration 006's auth_bootstrap_lookup policy. Every other
    caller must pass a real tenant_id.
    """
    conn = _get_raw_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                if tenant_id is not None:
                    cur.execute("SET LOCAL app.tenant_id = %s", (tenant_id,))
                yield conn
        # `with conn:` commits on clean exit, rolls back on exception
    finally:
        conn.close()
```

**STATE BEFORE.** No connection. No tenant context anywhere.

**`conn = _get_raw_connection()`** — a **new TCP connection and authentication
handshake**, every call. No pooling. That is `CAVEAT-013`, and it is not free:
one `growth_comparison` query opens four connections plus the audit write.

**`try: ... finally: conn.close()`** — the outer guarantee. Note the ordering:
`finally` closes **after** `with conn:` has already committed or rolled back.
Reverse them and you would close before committing.

**`with conn:`** — begins a transaction. On clean exit, `COMMIT`. On exception,
`ROLLBACK`. **Not close.**

**`with conn.cursor() as cur:`** — a cursor context manager, which *does* close
the cursor on exit.

**`SET LOCAL app.tenant_id = %s`** with a parameter tuple, not string formatting.
Even here — an internal value from a verified JWT — parameterisation is used.
Consistency means there is no site to audit as "the exception".

**`if tenant_id is not None`** — explicit, not truthy. The empty string is a
*different* case from `None`: `None` means "auth bootstrap, deliberately
unscoped", while `""` would mean "someone passed a broken value". With `if
tenant_id:` both take the unscoped branch. With `is not None`, an empty string
reaches Postgres, and the RLS policy's `CASE WHEN coalesce(...) = '' THEN FALSE`
returns **zero rows** — failing closed instead of silently unscoping.

**`yield conn`** — the `with` body runs here. **A connection, not a cursor**
(`CLAUDE.md` §7), so the caller does `with conn.cursor() as cur:` themselves.

**STATE AFTER, on the happy path:** committed, closed, and — critically —
`app.tenant_id` is **gone**, because `SET LOCAL` died with the transaction.

**What breaks if you change `SET LOCAL` to `SET`?** Nothing today, because there
is no pool. The moment a pooler is introduced (Supabase's session pooler is
already in `.env.example`), a connection returning to the pool carries the
previous request's tenant, and the next request that forgets to set it reads
another tenant's data. **A latent cross-tenant leak, activated by an
infrastructure change nobody would think of as a code change.**

---

### 6.2 The SSE generator, re-derived

Day 6 showed you this. Now derive each choice.

```python
async def event_stream() -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_run_graph(initial_state, queue))
    ...
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if item is None:
                break
            ...
    finally:
        if task.done():
            task.exception()
```

**Why `create_task` and not `await _run_graph(...)`?** `await` would run the
whole pipeline to completion **before** the first `yield` — a "stream" that emits
nothing for fifteen seconds and then everything. `create_task` starts it
independently so the consumer can emit as items arrive.

**Why a queue rather than yielding from the graph directly?** Because the
generator's lifetime is tied to the **client connection** and the graph's must
not be. The queue is the seam that lets one die while the other continues.

**Why unbounded?** Now you can derive it. A bounded queue makes `put()` suspend
when full. If the consumer stops (client disconnected), the producer suspends
mid-pipeline — possibly before `audit_writer_node`. **A disconnecting client could
prevent its own query from being recorded.** Unbounded removes the coupling, and
the cost is nine items maximum.

**Why `wait_for` instead of a plain `await queue.get()`?** A plain `get()` waits
forever, and during a slow LLM call an idle proxy closes the connection.
`wait_for` converts silence into a timeout, and the timeout into a heartbeat.

**Why is `task.cancel()` absent from `finally`?** Because the pipeline must reach
`audit_writer_node`. `task.exception()` is called only to *retrieve* a stored
exception so asyncio does not warn about it.

**Why `yield ": heartbeat\n\n"` and not a real event?** A `:` line is an SSE
comment — clients ignore it. Real bytes flow (satisfying the proxy) without the
client having to filter meaningless events.

---

### 6.3 `_run_graph` — the producer, and its `finally`

```python
async def _run_graph(initial_state, queue) -> None:
    accumulated = dict(initial_state)
    try:
        graph = get_graph()
        async for update in graph.astream(initial_state, stream_mode="updates"):
            for node_name, partial in update.items():
                if partial:
                    accumulated.update(partial)
                await queue.put(("node", node_name, partial or {}))
        await queue.put(("complete", None, accumulated))
    except Exception as exc:
        await queue.put(("error", None, {"message": str(exc)}))
    finally:
        await queue.put(None)
```

**`async for` over an async generator.** `graph.astream(...)` yields as each node
completes. The `async for` suspends between yields, letting the event loop serve
other requests.

**`accumulated = dict(initial_state)` then `.update(partial)`** — a shallow copy,
then merge. Because `astream` yields *partials*, and the final `complete` event
must carry the whole state.

**Shallow is fine here** because the values that matter (`retrieved_chunks`,
`citations`) are *replaced* by nodes, not mutated in place — nodes do
`state["retrieved_chunks"] = list(chunks)`.

**The `finally` sentinel is the deadlock guard.** It runs on success, on
exception, on cancellation. Without it, an unexpected error leaves the consumer's
`while True` spinning on heartbeats forever, and the HTTP connection hangs until
something external kills it.

**`except Exception` catching everything is deliberate.** By this point headers
are on the wire and a 500 is impossible (Day 6). Letting the exception escape
would kill the task with no notice to anyone; converting it to an event is the
only channel left.

---

## 7. Data flow — the two control-flow patterns side by side

```
SYNCHRONOUS, TRANSACTIONAL                 ASYNCHRONOUS, STREAMING
(db_transaction)                           (event_stream + _run_graph)

  caller                                     HTTP handler
    │  with db_transaction(t) as conn:         │  task = create_task(_run_graph)
    ▼                                          │  returns IMMEDIATELY
  psycopg2.connect()                           ▼
    │                                        ┌──────────────┐   ┌──────────────┐
    ▼                                        │  producer    │   │  consumer    │
  BEGIN  (with conn:)                        │  _run_graph  │   │ event_stream │
    │                                        │              │   │              │
    ▼                                        │ astream ──►  │   │ wait_for(get)│
  SET LOCAL app.tenant_id = t                │   put()  ────┼──►│   yield _sse │
    │                                        │              │   │              │
    ▼                                        │ put(complete)│   │ yield        │
  ══ caller's body runs ══                   │ put(None) ───┼──►│   break      │
    │                                        └──────┬───────┘   └──────┬───────┘
    ├─ ok    → COMMIT  → SET LOCAL DIES             │                  │
    └─ raise → ROLLBACK → SET LOCAL DIES        keeps running    dies with the
    │                                          to audit_writer   client socket
    ▼
  conn.close()   ← the outer finally
```

**The two patterns answer different questions.** The transaction is about
**atomicity and scope**; the stream is about **decoupled lifetimes**. Both are
`with`-shaped, and that is not a coincidence — both are "acquire, use, guarantee
release".

---

## 8. Engineering decision — a hand-rolled context manager and no pool

**Problem.** Every request-path database access must run inside a transaction
with the correct RLS scope, and must not leak connections.

**Decision.** A ten-line `@contextmanager` yielding a raw connection. **No
connection pool.**

| Alternative | Why not |
|---|---|
| **`psycopg2.pool`** | The right answer for production. But pooling makes `SET` versus `SET LOCAL` a live cross-tenant risk, and adds lifecycle management. Deferred, and recorded as `CAVEAT-013` |
| **SQLAlchemy** | Rejected project-wide: "SQLAlchemy adds nothing for flat record inserts". Would bring a pool and a session — and an ORM nobody wants |
| **Inject a connection via `Depends`** | The docstring says the engines do not need it — they read `tenant_id` from `QueryState`, not from the HTTP layer. It would also couple the engines to FastAPI |
| **A global connection** | Not thread-safe; LangGraph runs sync nodes in a thread pool |

**Trade-offs accepted.**

- **A connection per statement.** `CAVEAT-013`. One `growth_comparison` opens
  four plus the audit write. Each is a TCP connect and an auth handshake, and
  Postgres has a connection limit — `pgcode 53300` (Day 7) is what exhausting it
  looks like.
- **The helper is bypassed by the engines**, which open their own connections and
  set the tenant themselves. Correct given they have no HTTP context, and it does
  mean the discipline exists in **three** places rather than one.

**Current validity.** Fine at current volume; the first thing to change under
load. **And note the interaction:** adding a pooler without auditing every `SET`
would activate the latent leak.

**At 10×.** Pool with `SET LOCAL` enforced everywhere, and a test that fails if a
plain `SET app.tenant_id` appears on a request path.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| Connection leak under errors | Missing `finally: conn.close()` — `with conn:` does not close |
| `pgcode 53300` too many connections | `CAVEAT-013` compounding under load |
| One tenant sees another's data | `SET` instead of `SET LOCAL` on a pooled connection |
| Zero rows, no error | `app.tenant_id` unset — RLS fails closed (Day 14) |
| Every request slow | Blocking call inside `async def` freezing the loop |
| A stream emits nothing then everything | `await` instead of `create_task` (or proxy buffering) |
| A stream hangs forever | Producer died before the `finally` sentinel |
| Connection closes mid-stream | Heartbeat missing or interval too long |
| `RuntimeError: generator didn't stop` | `@contextmanager` with more than one `yield` |
| "Task exception was never retrieved" | `task.exception()` not called |

---

## 10. Hands-on experiment

### Experiment 1 — write one, and break it

```bash
docker compose exec -T backend python -c "
from contextlib import contextmanager

@contextmanager
def borrowed_key():
    print('  __enter__: take key')
    try:
        yield 'KEY-42'
    finally:
        print('  __exit__ : return key')

print('happy path:')
with borrowed_key() as k:
    print('  body, holding', k)

print()
print('exception path:')
try:
    with borrowed_key() as k:
        print('  body, about to raise')
        raise ValueError('boom')
except ValueError as e:
    print('  caught outside:', e)
print()
print('Note __exit__ ran BOTH times. That is the whole guarantee.')
"
```

### Experiment 2 — `with conn:` does not close

```bash
docker compose exec -T backend python -c "
import psycopg2, os
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
print('after connect :  closed =', conn.closed)
with conn:
    with conn.cursor() as cur:
        cur.execute('SELECT 1')
        print('inside with   :  ', cur.fetchone())
print('after with    :  closed =', conn.closed, ' <- STILL OPEN')
conn.close()
print('after close   :  closed =', conn.closed)
"
```

`closed = 0` after the `with`. **This is the trap**, in three lines of output.

### Experiment 3 — `SET` versus `SET LOCAL`, demonstrated

```bash
docker compose exec -T backend python -c "
import psycopg2, os
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
T='11111111-1111-1111-1111-111111111111'

def show(label):
    with conn.cursor() as c:
        c.execute(\"SELECT coalesce(current_setting('app.tenant_id', TRUE), '<unset>')\")
        print(f'{label:34} {c.fetchone()[0]}')

with conn:
    with conn.cursor() as c:
        c.execute('SET LOCAL app.tenant_id = %s', (T,))
    show('inside txn, after SET LOCAL   :')
show('AFTER COMMIT (SET LOCAL)      :')

with conn:
    with conn.cursor() as c:
        c.execute('SET app.tenant_id = %s', (T,))
    show('inside txn, after plain SET   :')
show('AFTER COMMIT (plain SET)      :')
print()
print('The last line is the leak: the value SURVIVED the transaction.')
conn.close()
"
```

**Look at the last line.** With a pool, that surviving value is the next
request's RLS scope.

### Experiment 4 — a generator suspends

```bash
docker compose exec -T backend python -c "
def counter():
    print('  start');   yield 1
    print('  between'); yield 2
    print('  end')

print('g = counter()  ->', end=' ')
g = counter()
print('(nothing printed — no code has run)')
print('next(g) ->'); print('   returned', next(g))
print('next(g) ->'); print('   returned', next(g))
print('next(g) ->')
try: next(g)
except StopIteration: print('   StopIteration')
"
```

### Experiment 5 — blocking the event loop

```bash
docker compose exec -T backend python -c "
import asyncio, time

async def blocking_task(n):
    time.sleep(1)                 # WRONG: blocks the loop
    return n
async def async_task(n):
    await asyncio.sleep(1)        # correct: yields control
    return n

async def main():
    t = time.perf_counter()
    await asyncio.gather(*(blocking_task(i) for i in range(3)))
    print(f'3x time.sleep(1)        : {time.perf_counter()-t:.2f}s  <- serialised')
    t = time.perf_counter()
    await asyncio.gather(*(async_task(i) for i in range(3)))
    print(f'3x await asyncio.sleep(1): {time.perf_counter()-t:.2f}s  <- concurrent')
asyncio.run(main())
"
```

~3 s versus ~1 s. In a web server the first number is *every user's* latency, not
just the blocker's.

### Experiment 6 — producer/consumer, and the bounded-queue deadlock

```bash
docker compose exec -T backend python -c "
import asyncio

async def producer(q, n, label):
    for i in range(n):
        await q.put(i)
        print(f'  {label} put {i}')
    await q.put(None)

async def consumer(q, stop_after=None):
    got = 0
    while True:
        item = await q.get()
        if item is None: break
        got += 1
        if stop_after and got >= stop_after:
            print('  consumer STOPS early (client disconnected)')
            return
asyncio.run(asyncio.wait_for(
    asyncio.gather(producer(asyncio.Queue(), 3, 'unbounded'),
                   ), timeout=5))
print()
print('Now the bounded case:')
async def demo():
    q = asyncio.Queue(maxsize=2)
    prod = asyncio.create_task(producer(q, 6, 'bounded  '))
    cons = asyncio.create_task(consumer(q, stop_after=1))
    await cons
    try:
        await asyncio.wait_for(prod, timeout=2)
    except asyncio.TimeoutError:
        print('  PRODUCER IS STUCK on put() — this is the audit-write hazard')
asyncio.run(demo())
"
```

That stuck producer is Day 6's argument, reproduced in fifteen lines.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/db/session.py` and `backend/app/api/query.py`:

1. `db_transaction` has both `with conn:` and `finally: conn.close()`. What does
   each guarantee, and why is neither redundant?
2. Why `if tenant_id is not None` rather than `if tenant_id:`? What does an empty
   string do in each case?
3. Find another module that sets `app.tenant_id` with a plain `SET`. Is that a
   bug? Justify your answer from the module's usage.
4. In `event_stream`, why `create_task` rather than `await`?
5. `_run_graph` puts `None` in a `finally`. Describe the exact failure without it.

---

## 12. Self-check questions

**Basic**
1. What does `with` guarantee?
2. What does `with conn:` do — and what does it not do?
3. What is the difference between `SET` and `SET LOCAL`?
4. What does a generator do at `yield`?
5. What is the one rule of `async def`?

**Code**
6. Does `db_transaction` yield a connection or a cursor?
7. What clears `app.tenant_id` after `db_transaction` exits?
8. What is `asyncio.to_thread` for, and where is it used?
9. What does `create_task` return, and when does the coroutine start?
10. Why is `queue.get()` wrapped in `wait_for`?

**Why**
11. Why must `SET LOCAL` be used on the request path?
12. Why is there no connection pool, and what does that cost?
13. Why is the SSE queue unbounded — derive it, do not recite it.
14. Why does `_run_graph` catch `Exception` broadly?
15. Why does `/health` need `to_thread` when the graph nodes do not?

**Debugging**
16. Under load, every endpoint slows including `/health`. What class of bug?
17. `pgcode 53300`. What is happening, and which caveat is it?
18. A stream emits heartbeats forever and never completes. Where do you look?

**System design**
19. You add a connection pooler. What must be audited first, and what test would
    you write?
20. LangGraph currently runs sync nodes in a thread pool. If it stopped doing so,
    what would break and how would you fix it?

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `with conn:` guarantees the **transaction** resolves — `COMMIT` on clean exit,
   `ROLLBACK` on exception. `finally: conn.close()` guarantees the **socket** is
   released. They are different resources with different lifetimes; psycopg2's
   connection context manager deliberately does not close, so relying on it alone
   leaks a connection per call.
2. `is not None` distinguishes "deliberately unscoped" (`None`, the auth
   bootstrap) from "someone passed a broken value" (`""`). With `if tenant_id:`
   both take the unscoped branch. With `is not None`, `""` is sent to Postgres,
   and the RLS policy's `CASE WHEN coalesce(...) = '' THEN FALSE` returns **zero
   rows** — failing closed rather than silently unscoping.
3. `ingestion/pipeline.py` (`_SQL_SET_TENANT = "SET app.tenant_id = %s"`) and
   `db_loader.py`. **Not a bug**: these are long-running batch jobs that own their
   connection for its entire life and are never pooled or handed to another
   request. The rule is "`SET LOCAL` wherever a connection could be reused by a
   different request", and ingestion is not that place.
4. `await` would run the whole pipeline to completion before the first `yield`,
   producing a "stream" that emits nothing for fifteen seconds and then
   everything at once. `create_task` starts it independently so the consumer can
   emit as items arrive.
5. Without it, the consumer's `while True` never breaks. If the producer dies from
   an unexpected error before reaching `put(("error", ...))`, nothing is ever
   enqueued, `wait_for` times out forever, and the connection emits heartbeats
   indefinitely until a proxy or the client kills it. The `finally` sentinel runs
   on success, on exception and on cancellation.

### §12 — Basic

1. That `__exit__` runs however the block is left — normally, by exception, by
   `return`, by `break`.
2. Begins a transaction; commits on clean exit, rolls back on exception.
   **It does not close the connection.**
3. `SET` is scoped to the **session** (survives the transaction, and therefore
   survives a return to a pool). `SET LOCAL` is scoped to the **transaction** and
   is cleared automatically on `COMMIT`/`ROLLBACK`.
4. Suspends, keeping its local variables, and returns the yielded value. Resumes
   from that point on the next `next()`.
5. Never do blocking work inside it. There is no preemption, so a blocking call
   freezes the entire event loop and every in-flight request.

### §12 — Code

6. A **connection**. The caller does `with conn.cursor() as cur:` itself.
7. `COMMIT` or `ROLLBACK`, automatically, because `SET LOCAL` is
   transaction-scoped.
8. Running a synchronous function in a worker thread so it does not block the
   event loop. Used in `main.py`'s `/health` for the psycopg2 check.
9. A `Task`. The coroutine is scheduled and starts running at the next
   opportunity — you do not need to `await` it for it to run.
10. So that silence becomes a timeout rather than an indefinite wait. The timeout
    is converted into a heartbeat, which keeps an idle proxy from closing the
    connection during a slow LLM call.

### §12 — Why

11. Because a connection that could be reused by another request must not carry
    the previous request's tenant. With a plain `SET`, the value survives the
    transaction; the next request that forgets to set it inherits the previous
    tenant's RLS scope — a cross-tenant read caused by four missing letters.
12. Simplicity, and because pooling makes the `SET`/`SET LOCAL` distinction a
    live cross-tenant risk that must be audited first. It costs a TCP connect and
    auth handshake per statement — `CAVEAT-013` — and one `growth_comparison`
    query opens four connections plus the audit write.
13. Derivation: a bounded queue suspends the producer on `put()` when full. If the
    consumer stops (client disconnected), the producer suspends **mid-pipeline**,
    possibly before `audit_writer_node`. A disconnecting client could therefore
    prevent its own query from being recorded. Unbounded removes the coupling;
    the memory cost is at most nine items.
14. Because by that point the `200 OK` and headers are already on the wire, so a
    500 is impossible. Letting the exception escape would kill the task silently
    with no notice to anyone. Converting it to an `error` event is the only
    remaining channel.
15. `/health` runs in a genuine `async def` handler on the event loop, so a
    synchronous psycopg2 call there blocks it. The graph nodes are **sync
    functions** that LangGraph runs in a thread pool, so their blocking calls
    happen off the loop. The safety comes from LangGraph's behaviour, not from
    anything in this codebase.

### §12 — Debugging

16. Something blocking the event loop — a synchronous call (a DB query, a `requests`
    call, `time.sleep`, a CPU-heavy loop) inside an `async def`. The signature is
    that **unrelated** endpoints slow down together, including trivial ones.
17. Postgres's connection limit is exhausted. `CAVEAT-013`: a new connection per
    statement, with no pool, so concurrent queries multiply connections quickly
    — `growth_comparison` alone is five.
18. The **producer**. Either `_run_graph` died before its `finally` (unlikely — it
    is a `finally`), or the task was never created, or the graph itself is hung on
    an unbounded external call. Check the backend logs for the node the trace
    stopped at, then check whether that node makes a network call with no timeout
    (which is exactly the class of defect `llm/client.py` was created to fix —
    Day 19).

### §12 — System design

19. **Audit first:** every `SET app.tenant_id` on the request path must be `SET
    LOCAL`. Today `ingestion/pipeline.py` and `db_loader.py` use a plain `SET`;
    they are batch jobs and are safe **only while they are not pooled**, so the
    pooler's scope must be verified to exclude them. **The test:** a static check
    (a pytest that greps the request-path modules) failing if `SET app.tenant_id`
    appears without `LOCAL`. It belongs in the pure-function suite because it
    needs no database — exactly the kind of check `conftest.py` is built for.
20. Every graph node that calls psycopg2 (`quant_engine._execute_sql`,
    `audit_writer`), plus the ONNX model calls in `retriever`, would run **on the
    event loop** and block it — so one query would freeze every concurrent
    request. Fix: wrap the sync bodies in `asyncio.to_thread` (as `/health`
    already does), or make the nodes `async def` with async DB and HTTP clients.
    The important part of the answer is recognising that **the current safety is
    inherited from a dependency's implementation detail**, not asserted by this
    codebase — which makes it worth a `KNOWN_UNKNOWNS` entry.

---

## 14. MUST REMEMBER

```text
- `with` guarantees __exit__ however the block is left
- `with conn:` COMMITS or ROLLS BACK. It does NOT close
- db_transaction() yields a CONNECTION, not a cursor
- SET LOCAL = transaction-scoped, cleared on COMMIT/ROLLBACK
- SET       = session-scoped → leaks across a pooled connection
- ingestion uses plain SET deliberately: it owns its connection, unpooled
- Never block inside async def — it freezes EVERY in-flight request
- create_task starts a coroutine independently; await would serialise it
- @contextmanager: everything before yield is __enter__, after is __exit__,
  and the try/finally around the yield is what makes it exception-safe
```

## 15. MUST UNDERSTAND

```text
- Why four missing letters (SET vs SET LOCAL) are a latent cross-tenant leak,
  activated by an INFRASTRUCTURE change nobody would call a code change
- Why the same rule has two correct answers in two contexts
- How to DERIVE the unbounded queue from "the audit row must always be written"
- Why concurrency is not parallelism, and why one blocking call is everyone's
  problem
- That the safety of blocking DB calls in graph nodes is inherited from
  LangGraph's thread pool, not asserted here
```

---

## 16. This connects to

```text
Day 10 — the types
   ↓
Day 11 — the control flow those types move through     ← you are here
   ↓
Day 12 — state that outlives a single call: lazy singletons and import order
```

Forward references:

- `SET LOCAL` → RLS policies in full → **Day 14**
- `CAVEAT-013` (connection per statement) → **Days 14, 45**
- `_execute_sql`'s own transaction → **Day 33**
- `audit_writer`'s own connection → **Day 44**
- The SSE consumer on the client → **Day 39**
- Timeouts on external calls → **Day 19**
