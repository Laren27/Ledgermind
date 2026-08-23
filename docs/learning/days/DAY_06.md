# Day 06 — Two Transports, One Pipeline

**Phase 1 · Weight: H (~120 min) · Prerequisites: Days 4, 5**

---

## 1. Today's goal

By tonight you can:

- Explain Server-Sent Events: the frame format, why a blank line matters, and
  why heartbeats exist.
- Explain why `/api/query` and `/api/query/stream` are **one pipeline with two
  transports**, and what would go wrong if they were two implementations.
- Explain the single most counter-intuitive line in this file: **the pipeline is
  never cancelled when the client disconnects** — and why that forces the queue
  to be unbounded.
- Read the SSE consumer in `lib/api.ts` and name the one failure class it
  retries, and the three it refuses to.

---

## 2. Why now

Days 4 and 5 got a request in. Today it comes back out — twice, two ways. This
completes Phase 1: after today you can trace a request from `curl` to a
response, and you have met every layer above the graph.

It also plants two ideas the rest of the course needs: **generators and async**
(formalised on Day 11) and **the audit row must always be written** (Day 44).

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Request/response, status codes, headers | Day 4 | SSE is an HTTP response that never ends |
| `QueryRequest` → `make_initial_state` | Day 5 | Both endpoints do this identically |
| `QueryState` mutated by each node | Day 3 | The stream reports each node's mutation |

---

## 4. Concept lesson

### 4.1 The problem streaming solves

A LedgerMind query takes **3–15 seconds**: an LLM classification, a vector
search, a rerank, sometimes a second LLM call. With a blocking request the user
sees a spinner and learns nothing. Worse, they cannot tell a slow query from a
hung one.

**What existed before, and why each was insufficient:**

| Approach | Problem |
|---|---|
| Just wait | No feedback. A 15 s wait and a hang look identical |
| **Polling** — client asks "done yet?" every second | N wasted round-trips; server must store partial state keyed by id; latency granularity is the poll interval |
| **WebSockets** | Bidirectional, and we need one direction. Requires a protocol upgrade, its own reconnect logic, and does not survive some proxies |
| **Server-Sent Events** | One-way server→client over ordinary HTTP. No upgrade, no new protocol, works through proxies (with one caveat, §6.4) |

**What SSE is.** An HTTP response with `Content-Type: text/event-stream` whose
body **never ends**. The server writes frames as things happen; the connection
stays open.

---

### 4.2 The frame format

```
event: node\n
data: {"node":"router","label":"ROUTER","status":"done"}\n
\n
```

Rules that matter:

1. `field: value`, one per line.
2. **A blank line terminates a frame.** This is the entire framing protocol.
3. A line starting with `:` is a **comment** — clients ignore it. This is what
   makes heartbeats possible.
4. Multiple `data:` lines in one frame are joined with newlines.

The server side is four lines:

```python
def _sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"
```

Note the `\n\n` at the end. Forget it and the client buffers your frame forever,
waiting for a terminator that never arrives.

**Mental model.** SSE is **a phone line left open**. The server speaks when it
has something to say; occasionally it says "still here" so the line is not cut.

---

### 4.3 Generators, briefly

An SSE handler cannot `return` — it must **yield** repeatedly over time.

```python
def counter():
    yield 1
    yield 2
```

Calling `counter()` runs **nothing**. It returns a generator. Each `next()` runs
until the following `yield`, then **suspends, keeping local state**. An
`async def` with `yield` is an *async generator*, and FastAPI's
`StreamingResponse` consumes one, writing each yielded string to the socket.

Formalised on **Day 11**. Today you only need: *a function that pauses, keeps its
variables, and resumes.*

---

## 5. The actual LedgerMind files

### `backend/app/api/query.py` — the whole file now

```
File:        backend/app/api/query.py (233 lines)
Purpose:     Both query endpoints, the SSE machinery, and node labelling
Why:         The pipeline must be reachable two ways without existing twice
Who imports: main.py
What it imports: get_current_user, role_filtered_response, get_graph,
             make_initial_state
Entry points: execute_query (POST /api/query)
             execute_query_stream (POST /api/query/stream)
Data in:     QueryRequest + JWT
Data out:    one JSON object, or a sequence of SSE frames ending in one
```

The module docstring for the streaming endpoint states the design rule
outright:

> Deliberately NOT a second execution path: the graph, the state factory and
> `role_filtered_response` are all shared with `/api/query`, so the two
> endpoints cannot drift. The only difference is transport.

**Why "cannot drift" is the whole point.** Two implementations of one pipeline
means every future fix must be applied twice, and the day someone forgets, the
streaming answer and the blocking answer for the same question differ. In a
system whose claim is that its answers are checkable, that is fatal.

---

### `frontend/lib/api.ts` — the consumer

```
File:        frontend/lib/api.ts (~420 lines)
Purpose:     Every call the frontend makes, and the SSE consumer
Entry point: submitQueryStreaming(question, onNode, executionContext)
Data out:    a QueryResponse, plus a callback per node
```

---

## 6. Deep code walkthrough

### 6.1 The blocking endpoint — the baseline

```python
@router.post("/query")
async def execute_query(payload: QueryRequest,
                        current_user: Dict[str, Any] = Depends(get_current_user)):
    request_id = str(uuid.uuid4())
    tenant_id = payload.tenant_id or current_user.get("tenant_id", "default")
    user_id = str(current_user["user_id"])

    initial_state = make_initial_state(...)

    try:
        graph = get_graph()
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline execution failed: {e}")

    return role_filtered_response(final_state, current_user["role"])
```

Eight lines of substance. `get_graph()` returns the **compiled singleton**
(Day 35) — compilation is not free, so it happens once per process, not per
request. `await graph.ainvoke(...)` runs the whole pipeline and returns the final
state. `role_filtered_response` shapes it by role (Day 9).

---

### 6.2 The producer task, and the comment worth memorising

```python
async def _run_graph(initial_state: Dict[str, Any], queue: asyncio.Queue) -> None:
    """
    Drives the graph, pushing one item per completed node into `queue`.

    Runs as its own task so that a client disconnect kills only the SSE
    generator, never this. The pipeline always runs to completion --
    audit_writer_node included -- regardless of whether anyone is listening.
    """
    accumulated: Dict[str, Any] = dict(initial_state)
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
        await queue.put(None)   # sentinel: generator stops draining
```

**STATE BEFORE.** A fresh `QueryState` and an empty queue.

**Execute.**

**STATE AFTER.** The queue holds one `("node", name, partial)` per completed
node, then `("complete", None, full_state)`, then `None`.

**Four decisions, each load-bearing.**

**1. `stream_mode="updates"`.** LangGraph yields `{node_name: partial_state}` as
each node finishes. The docstring names why this matters:

> Node boundaries come from LangGraph's own `astream("updates")` rather than
> from instrumentation inside the nodes, so the trace is a byproduct of real
> execution and **a node cannot silently forget to report itself.**

If each node called `report_progress()` itself, a node could omit the call and
vanish from the trace while still running. Deriving the trace from execution
makes that impossible **by construction** rather than by discipline.

**2. `accumulated` is built by merging partials.** `astream` yields *partials*,
not the full state. The full state must be reassembled to send the final
`complete` event. `dict(initial_state)` then `.update(partial)` does it.

**3. The `finally` sentinel.** `await queue.put(None)` runs on success, on
exception, on anything. The consumer's `while True` loop breaks on `None`.
Without it, a crash in the producer leaves the consumer blocking on
`queue.get()` forever, and the HTTP connection hangs until a proxy kills it.

**4. Exceptions become an event, not a raise.** By the time a node fails, the
`200 OK` and its headers are **already on the wire**. You cannot retroactively
send a 500. The failure must travel as data.

---

### 6.3 The consumer generator

```python
async def event_stream() -> AsyncIterator[str]:
    # Unbounded on purpose: a bounded queue would let a disconnected
    # client block the producer mid-pipeline and strand the audit write.
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_run_graph(initial_state, queue))
    last_tick = time.perf_counter()

    yield _sse("start", {"request_id": request_id})

    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue

            if item is None:
                break
            kind, node_name, data = item

            if kind == "node":
                ...
                yield _sse("node", event)
            elif kind == "complete":
                yield _sse("complete", role_filtered_response(data, role))
            elif kind == "error":
                yield _sse("error", data)
    finally:
        # Never cancel `task`. If the client vanished mid-query the
        # pipeline must still finish so audit_writer_node writes its row.
        if task.done():
            task.exception()   # retrieve so asyncio doesn't warn
```

**The unbounded queue — follow the reasoning carefully.**

A bounded `asyncio.Queue(maxsize=N)` blocks the *producer* on `put()` when full.
Now suppose the client disconnects: the consumer stops calling `get()`, the queue
fills, and the producer **blocks mid-pipeline** — possibly before
`audit_writer_node`. The audit row is never written. **A disconnecting client
would be able to erase its own audit trail.**

Unbounded removes that entirely. The cost is memory, bounded in practice by the
node count (eight items) — so the "unbounded" queue can hold at most nine things.

**The `finally` block, and the comment that explains it.** The natural instinct is
`task.cancel()` on client disconnect — do not waste work on an answer nobody will
read. **That is exactly wrong here.** From `CLAUDE.md` §6 and the audit design:
every query must produce an audit row, refusals and blocks included. A cancelled
pipeline is an unrecorded query.

`task.exception()` is called only to *retrieve* a stored exception so asyncio
does not log "Task exception was never retrieved".

**Heartbeats.** `asyncio.wait_for(..., timeout=15)`. If nothing arrives in 15
seconds — normal during a slow LLM call — yield `": heartbeat\n\n"`. The leading
colon makes it a comment; clients discard it. Without it, an idle proxy sees a
silent connection and closes it.

---

### 6.4 `X-Accel-Buffering: no`

```python
return StreamingResponse(
    event_stream(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # Render/nginx buffer proxied responses by default, which would
        # hold every event until the pipeline finished and defeat the
        # entire point. Must be verified live on Render, not just locally.
        "X-Accel-Buffering": "no",
    },
)
```

nginx buffers proxied responses to reduce syscalls. For a normal response that is
invisible. For SSE it is fatal: every frame is held until the response completes,
and the client receives all eight node events **at once, at the end**. The feature
appears to work locally (no proxy) and silently does nothing in production.

The comment's last sentence is the discipline: **must be verified live on Render,
not just locally.** A behaviour that only manifests behind a proxy cannot be
tested without one.

---

### 6.5 `_trace_detail` — the zero-hallucination rule, at the API layer

```python
def _trace_detail(node: str, partial: Dict[str, Any], role: str) -> Optional[str]:
    """
    One short, backend-sourced line describing what a node actually did.

    Every value here is read from real state -- nothing is inferred or
    invented, per the Zero UI-Hallucination Mandate. Returns None when the
    node has nothing meaningful to report, in which case the UI shows the
    label alone rather than filler text.
    """
    if node == "prompt_shield":
        return "Blocked by policy" if partial.get("is_blocked") else None
    if node == "router":
        path = partial.get("path")
        return path.upper() if path else None
    if node == "semantic_engine":
        n = len(partial.get("retrieved_chunks") or [])
        return f"{n} chunk{'s' if n != 1 else ''} retrieved" if n else None
    if node == "quant_engine":
        if role == "viewer":
            return "Verified" if partial.get("sql_verified") else None
        dsl = partial.get("dsl_object") or {}
        op = dsl.get("operation")
        return op.replace("_", " ") if op else None
    if node == "confidence":
        tier = partial.get("confidence_tier")
        return tier.upper() if tier else None
    return None
```

**Three things at once.**

**Every string is derived from state.** `"3 chunks retrieved"` is
`len(retrieved_chunks)`. Nothing says "Analysing…" or "Thinking…", because
neither corresponds to anything the backend knows.

**`None` is returned rather than filler.** The UI then shows the label alone.
This is the **omit-rather-than-substitute** rule you will meet again on Day 40.

**Role filtering appears here too.** A viewer sees `"Verified"`; an analyst sees
the DSL operation. It mirrors `role_filtered_response` (Day 9) — and *because it
is a mirror, it is a second copy of one rule*. The comment acknowledges it:
`# DSL/SQL machinery is analyst+ only; mirror role_filtered_response.` Worth
noting as a latent drift risk of exactly the kind this project consolidates
elsewhere.

**Admin-only timing:**

```python
if role == "admin":
    event["duration_ms"] = int((now - last_tick) * 1000)
```

Per-node timing is operational detail, restricted the same way `latency_ms` is.

---

### 6.6 The client, and its four error classes

```typescript
export class UnauthorizedError extends Error {}
export class PipelineError extends Error {}       // server emitted an `error` event
export class RequestFailedError extends Error {}  // non-2xx before the stream began
export class TransportError extends Error {}      // never connected
```

The retry rule, from the function's own docstring:

> **RETRIES EXACTLY ONE CASE:** the socket dropped after the stream started and
> before `complete` arrived. Nothing else falls back.
>
> The previous version retried every failure, and the cost was not theoretical.
> `api/query.py` deliberately never cancels the graph task on client disconnect
> …so one user question became **two full pipeline runs, two LLM spends against a
> 500/day ceiling, and two audit_log rows with nothing marking either as a
> retry.**

**Read that twice.** The server's decision (never cancel) and the client's
decision (retry freely) were individually reasonable and jointly a bug. A blind
retry doubled cost against a hard daily quota *and* corrupted the audit trail by
recording one question as two.

The mechanism:

```typescript
let streamStarted = false;   // set the moment a readable body is in hand
...
if (err instanceof UnauthorizedError) throw err;   // retrying would 401 again
if (err instanceof PipelineError) throw err;       // re-running reproduces it
if (err instanceof RequestFailedError) throw err;  // server said no
if (!streamStarted) throw new TransportError(...); // nothing to have dropped
outcome = "retry";                                  // ONLY a mid-stream drop
```

And the fallback is placed deliberately:

```typescript
// Outside the try on purpose: a throw from this call must reach the caller,
// not be caught by the block above and retried a second time.
if (outcome === "retry") return submitQuery(question, executionContext);
```

Placing it inside the `try` would make a failing retry look like another
retryable failure — an accidental loop.

**Frame parsing:**

```typescript
buffer += decoder.decode(value, { stream: true });
const frames = buffer.split("\n\n");
buffer = frames.pop() ?? "";   // keep the incomplete tail
```

A TCP chunk boundary can land **anywhere**, including mid-JSON. `split("\n\n")`
then `pop()` keeps the trailing partial frame in the buffer until its terminator
arrives. Discarding it instead would corrupt roughly one frame per chunk
boundary — intermittently, and only under load.

---

## 7. Data flow

```
BLOCKING                              STREAMING
POST /api/query                       POST /api/query/stream
  │                                     │
  ├─ Depends(get_current_user)          ├─ Depends(get_current_user)
  ├─ make_initial_state()               ├─ make_initial_state()
  │                                     │
  ├─ await graph.ainvoke(state)         ├─ create_task(_run_graph)
  │      (blocks ~3-15s)                │      │ producer
  │                                     │      ├─ astream("updates")
  │                                     │      ├─ queue.put per node
  │                                     │      └─ queue.put(None)
  │                                     │
  │                                     ├─ event_stream() generator
  │                                     │      ├─ yield "start"
  │                                     │      ├─ yield "node" × 8
  │                                     │      ├─ yield ": heartbeat" as needed
  │                                     │      └─ yield "complete"
  ▼                                     ▼
role_filtered_response(final, role)   role_filtered_response(accumulated, role)
  ▼                                     ▼
one JSON object                       SSE frames, last one carries the same object
```

**Both arrows converge on `role_filtered_response`.** That is what "one pipeline,
two transports" means concretely.

---

## 8. Engineering decision — why both endpoints exist

**Problem.** A 3–15 s pipeline with no feedback, on a free tier where a hung
request is indistinguishable from a slow one.

**Decision.** Add SSE **alongside** the blocking endpoint, sharing the graph, the
state factory and the response shaper.

| Alternative | Why not |
|---|---|
| Replace blocking with SSE | The blocking endpoint is the client's *fallback* for exactly one failure class. Removing it means a dropped socket has nowhere to fall back to |
| Polling | N round-trips; server-side partial state keyed by id; poll-interval latency |
| WebSockets | Bidirectional when one direction is needed; upgrade handshake; own reconnect logic; worse proxy behaviour |
| Instrument nodes to report progress | A node could silently forget. `astream("updates")` makes the trace a byproduct of execution |

**Trade-offs accepted.** Two endpoints to keep in sync — mitigated by sharing
every component. An open connection per in-flight query — bounded by the free
tier's concurrency anyway. Proxy buffering — handled by a header that must be
verified in production.

**Current validity.** Sound. The one structural risk is `_trace_detail`'s role
check being a second copy of the role rule in `response_shaping.py`.

**At 10×.** Each open SSE connection holds a task and a queue. With one uvicorn
worker (Day 45), concurrency is bounded by the event loop. You would need
multiple workers, and then `_compiled_graph` and the ONNX models exist once per
worker — a memory multiplication that the 512 MB ceiling does not permit.
**The scaling limit here is memory, not the transport.**

---

## 9. Failure modes

| Symptom | Cause | Note |
|---|---|---|
| All events arrive at once, at the end | Proxy buffering | `X-Accel-Buffering: no`; verify **on Render** |
| Connection closes after ~30–60 s | Idle proxy timeout | Heartbeats every 15 s prevent it |
| Client hangs forever, no events | Producer died before the sentinel | The `finally` sentinel exists for this |
| One question, two audit rows | Client retried a non-retryable failure | Fixed — `d3d3caa` |
| Frames parse intermittently | Partial frame discarded | Keep the tail: `frames.pop()` |
| 500 mid-stream is impossible | Headers already sent | Failure travels as an `error` **event** |
| A node missing from the trace | Would require LangGraph not to yield it | Cannot be caused by a node forgetting |

---

## 10. Hands-on experiment

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@alpha.ledgermind.test","password":"<password>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo ${#TOKEN}
```

### Experiment 1 — watch the frames arrive

```bash
curl -N -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What risks does Eternal disclose in Q4 FY26?"}'
```

`-N` disables curl's own buffering. Watch `event: node` frames appear **one at a
time**. Count them; match them against `graph.py`'s eight nodes. Notice the blank
line after each.

### Experiment 2 — the same answer, two ways

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What was Eternal revenue in FY26?"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['response_text'])"
```

Then the same question through the stream and read the `complete` frame's
`data`. Same shape, same fields — because both call `role_filtered_response`.

### Experiment 3 — see a heartbeat

```bash
curl -N -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"Summarise every risk factor Eternal discloses, in detail."}' \
  | cat -A | grep -m 3 '^:'
```

`cat -A` makes line endings visible. `: heartbeat` lines are comments the browser
discards — and the only reason the proxy does not.

### Experiment 4 — the pipeline outlives the client

Start a query and kill `curl` after the first node event (Ctrl-C). Then:

```bash
docker compose logs --tail 40 backend | grep -i "audit log written"
```

**The audit row was still written.** The client vanished; the pipeline did not.
That is `_run_graph` as its own task plus the `finally` that refuses to cancel.

### Experiment 5 — role changes the trace

Run Experiment 1 as `admin`, then as `viewer` (log in with the viewer account).
Compare the `node` events:

- admin has `duration_ms`; viewer does not.
- On `quant_engine`, admin sees the DSL operation; viewer sees `"Verified"`.

That is `_trace_detail`'s role branch, mirroring Day 9's rule.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/api/query.py` and answer:

1. Why is the queue unbounded? What specific bad outcome does a bounded queue
   allow?
2. Find the `finally` block in `event_stream`. Why is `task.cancel()` *not*
   there, and what is `task.exception()` for?
3. `_run_graph` puts `None` in a `finally`. What breaks without it?
4. Why must a mid-pipeline failure be an `error` **event** rather than a 500?

Open `frontend/lib/api.ts`:

5. Name the four error classes and say which one is retried.
6. Why is `if (outcome === "retry") return submitQuery(...)` placed **outside**
   the `try` block?

---

## 12. Self-check questions

**Basic**
1. What terminates an SSE frame?
2. What does a line beginning with `:` mean?
3. What is the heartbeat interval here?
4. What does `-N` do in `curl`?
5. How many node events does one full query produce?

**Code**
6. What does `stream_mode="updates"` yield?
7. Why is `accumulated` needed when `astream` already yields partials?
8. What is `_sse()` and what would break if you dropped one `\n`?
9. What does `X-Accel-Buffering: no` prevent?
10. How does the client keep a partial frame across chunk boundaries?

**Why**
11. Why is the pipeline never cancelled on client disconnect?
12. Why does that decision force an unbounded queue?
13. Why derive node boundaries from `astream` rather than instrumenting nodes?
14. Why does `_trace_detail` return `None` instead of "Processing…"?
15. Why does the client retry exactly one failure class?

**Debugging**
16. All eight node events arrive at once in production but stream fine locally.
    Cause?
17. The connection closes after ~45 s with no error. Cause, and what prevents it?
18. One user question produced two `audit_log` rows. What happened, and which
    commit fixed it?

**System design**
19. Add a `progress` event carrying a percentage. What is the honest problem with
    that, given this system's rules?
20. To serve 10× concurrent streams you add uvicorn workers. Name the resource
    that multiplies, and why that is fatal on this tier.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Because a bounded queue blocks the **producer** on `put()` when full. If the
   client disconnects, the consumer stops draining, the queue fills, and the
   producer blocks **mid-pipeline** — possibly before `audit_writer_node`. A
   disconnecting client would be able to prevent its own query from being
   recorded.
2. `task.cancel()` is absent because the pipeline must run to completion so
   `audit_writer_node` writes its row, regardless of whether anyone is listening.
   `task.exception()` is called only to *retrieve* a stored exception so asyncio
   does not emit "Task exception was never retrieved" — it is bookkeeping, not
   error handling.
3. The consumer's `while True` loop never terminates. It blocks on `queue.get()`
   forever (well, on the 15 s timeout, so it emits heartbeats forever) and the
   HTTP connection hangs until a proxy or the client kills it.
4. Because by the time a node fails, `200 OK` and the response headers are
   **already on the wire**. HTTP has no mechanism to retract a status code. The
   only remaining channel is the response body — so the failure must travel as
   data.
5. `UnauthorizedError`, `PipelineError`, `RequestFailedError`, `TransportError`.
   **None of them is retried.** The single retried case is not an error class at
   all — it is the reader draining with no `complete` event, i.e. the socket
   dropped mid-stream.
6. So that a throw from `submitQuery` reaches the caller. Inside the `try`, a
   failing retry would be caught by the same `catch`, classified as another
   retryable failure, and retried again — an accidental loop, each iteration
   costing a full pipeline run.

### §12 — Basic

1. A blank line (`\n\n`).
2. A comment. Clients ignore it — which is what makes it usable as a heartbeat.
3. `_HEARTBEAT_SECONDS = 15`.
4. Disables curl's output buffering so you see frames as they arrive.
5. Up to eight (one per graph node), fewer on paths that exit early — a blocked
   query produces `prompt_shield` and `audit_writer` only.

### §12 — Code

6. `{node_name: partial_state_dict}` for each node as it completes — the fields
   that node wrote, not the whole state.
7. Because the final `complete` event must carry the **full** state, and
   `astream` only ever yields partials. `accumulated` starts as a copy of the
   initial state and merges each partial as it arrives.
8. `_sse` formats one frame: `f"event: {event}\ndata: {json.dumps(payload)}\n\n"`.
   Dropping one `\n` removes the blank-line terminator, so the client buffers the
   frame indefinitely waiting for a terminator that never arrives — and every
   subsequent frame is appended to it.
9. nginx (and Render's proxy) buffering the response, which would hold every
   event until the pipeline finished and deliver them in one burst — defeating
   the entire purpose while appearing to work locally, where there is no proxy.
10. `buffer.split("\n\n")` then `frames.pop()` — the last element is the
    incomplete tail and is put back into the buffer rather than parsed.

### §12 — Why

11. Because every query must produce an audit row — refusals and blocks included.
    A cancelled pipeline is an **unrecorded query**, and the audit trail is one of
    this system's core promises.
12. Because a bounded queue lets a stopped consumer block the producer, which
    would strand the pipeline before the audit write. Unbounded decouples them.
    The memory cost is trivial: at most nine items, one per node plus the
    sentinel.
13. So the trace is a **byproduct of real execution**. Self-reporting nodes can
    silently omit their report — a node would keep running and vanish from the
    trace. Deriving it from LangGraph's own yields makes that impossible by
    construction rather than by discipline.
14. Because "Processing…" is not a fact the backend knows. Under the Zero
    UI-Hallucination Mandate, every rendered string must be wired to a real
    backend value; when there is none, the UI shows the label alone. Omit rather
    than substitute.
15. Because the server never cancels the pipeline on disconnect. Retrying a
    failure the pipeline already reported means running it twice: two LLM spends
    against a 500/day ceiling, and two audit rows with nothing marking either as
    a retry. Only a **dropped socket** is genuinely retryable, because nothing
    reported a failure — the bytes just stopped.

### §12 — Debugging

16. Proxy buffering. The `X-Accel-Buffering: no` header is missing, being
    stripped, or the proxy ignores it. It cannot reproduce locally because there
    is no proxy locally — which is why the code comment says it must be verified
    live on Render.
17. An idle proxy or load-balancer timeout closing a connection with no traffic.
    Prevented by the 15 s heartbeat, which keeps bytes flowing during a slow LLM
    call.
18. The client retried a failure the **pipeline had already reported and
    completed**. Because the server never cancels, both runs completed and both
    wrote audit rows. Fixed in `d3d3caa` — *"fix(frontend): retry only a dropped
    socket, never a pipeline failure"* — and pinned by `f9e4561`, which added
    inline negative controls.

### §12 — System design

19. There is no honest source for a percentage. The graph has eight nodes but the
    *path* is not known until `router` finishes, and node durations vary by more
    than an order of magnitude (a regex shield versus two LLM calls). Any
    percentage would be invented — precisely what `_trace_detail` refuses to do.
    The honest version already exists: report **which node just completed**, and
    let the UI render progress from real boundaries.
20. **Memory.** Each uvicorn worker is a separate process with its own
    `_compiled_graph` singleton **and** its own lazily-loaded ONNX models —
    `bge-small`, the BM25 model, and the local cross-encoder. Those are the
    largest objects in the process. On Render's 512 MB tier a second worker is
    what `Exited with status 137` is made of. The transport is not the
    constraint; the memory ceiling is (Day 45).

---

## 14. MUST REMEMBER

```text
- SSE frame = "event: X\ndata: {...}\n\n" — the BLANK LINE terminates it
- A ":" line is a comment → heartbeat, every 15s
- Both endpoints share the graph, the state factory, and role_filtered_response
- THE PIPELINE IS NEVER CANCELLED ON DISCONNECT → the audit row is always written
- Which forces the queue to be unbounded
- The client retries EXACTLY ONE case: a socket dropped mid-stream
- X-Accel-Buffering: no — and it must be verified on Render, not locally
- Once headers are sent, a 500 is impossible; failure travels as an event
```

## 15. MUST UNDERSTAND

```text
- Why one pipeline with two transports beats two implementations
- Why deriving the trace from astream makes "a node forgot to report" impossible
  BY CONSTRUCTION rather than by discipline
- How two individually-reasonable decisions (server never cancels, client always
  retries) combined into one bug that doubled cost and corrupted the audit trail
- Why _trace_detail returns None rather than filler text
- Why the scaling limit here is memory, not the transport
```

---

## 16. This connects to

```text
Day 5 — the request contract
   ↓
Day 6 — the response, twice: blocking and streamed     ← END OF PHASE 1
   ↓
Day 7 — who is allowed to send these requests at all
```

Forward references:

- `Depends(get_current_user)` → **Day 8**
- `role_filtered_response` → **Day 9**
- generators, `async`, `asyncio.Queue` → **Day 11**
- `get_graph()` and the eight nodes → **Day 35**
- `audit_writer_node` and why it must always run → **Day 44**
- the frontend consumer in full → **Day 39**
- workers and the memory ceiling → **Day 45**
