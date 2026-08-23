# Day 35 — LangGraph: Nodes, Conditional Edges, the Compiled Singleton

**Phase 10 — Orchestration · Weight: M (~90 min) · Prerequisites: Days 3, 11**

**Textbook: 14.2 "Architecture Overview" — CONFIRMS.** The case study's classifier
routing to one of two engines is structurally this, with a third path added.

---

## 1. Today's goal

By tonight you can:

- Draw the graph from `graph.py` alone, without the docstring diagram.
- Explain what a `StateGraph` is, what a node's contract is, and what a
  conditional edge does.
- Explain the **two edges that bypass the confidence tail**, and the measurement
  that forced the second one.
- Explain why the graph is compiled **once** into a module singleton.
- Explain why this codebase uses only `StateGraph` + `TypedDict` and none of
  LangGraph's agent abstractions.

---

## 2. Why now

You have now read every node: `prompt_shield` (Day 42 for the detail, but you
have seen it), `router` (Day 36 tomorrow), both engines (Days 30, 34),
`confidence` and `response_generator` (Day 30), and `audit_writer` (Day 44).
Today is the wiring that makes them one pipeline.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `QueryState` mutated by each node | Day 3 | The node contract |
| `get_graph()` compiled once | Day 6 | Today is why |
| `astream("updates")` | Day 6 | The trace comes from here |
| Lazy singletons | Day 12 | Same pattern, different resource |

---

## 4. Concept lesson

### 4.1 The problem

Three paths, two early exits, and a requirement that every step be observable.
Written as ordinary control flow:

```python
def run(state):
    state = prompt_shield(state)
    if state["is_blocked"]:
        return audit_writer(state)
    state = router(state)
    if state.get("error_node") == "router" and state.get("error"):
        return audit_writer(state)
    if state["path"] == "quantitative":
        state = quant_engine(state)
    elif state["path"] == "cross":
        state = cross_engine(state)
    else:
        state = semantic_engine(state)
    state = confidence(state)
    state = response_generator(state)
    return audit_writer(state)
```

**That works.** It is also:

- **not drawable** — the topology is implicit in the nesting;
- **not streamable** — there are no node boundaries to report (Day 6);
- **not inspectable** — you cannot ask it "which nodes exist?";
- **easy to extend wrongly** — a fourth path is another `elif`, and nothing
  connects it to the audit table's `CHECK` constraint (Day 13).

**A `StateGraph` makes the topology data.**

---

### 4.2 The three primitives

**A node** is a function with one contract:

```python
def X_node(state: QueryState) -> QueryState:
```

Take the state, mutate it, return it. **Every node in this codebase has exactly
that signature** (Day 3), which is why `graph.py` can register them uniformly.

**An edge** is unconditional: `A` then `B`.

**A conditional edge** is a *function* returning a string, plus a mapping from
strings to nodes:

```python
graph.add_conditional_edges(
    "prompt_shield",
    route_after_shield,           # (state) -> str
    {"router": "router",
     "blocked": "audit_writer"},
)
```

**The routing function reads state and returns a label.** The label is looked up
in the mapping. **The function does not know node names** — it returns
`"blocked"`, and the mapping decides that means `audit_writer`.

**Mental model.** A relay race with one baton (Day 3). A conditional edge is
**a marshal at a junction** who reads the baton and points down one of several
lanes.

---

### 4.3 The topology

```
  START
    ↓
  prompt_shield ──(blocked)──→ audit_writer ──→ END
    │
   (clean)
    ↓
  router ──(path=quantitative)──→ quant_engine ─────┐
    │                                                │
    ├──(path=semantic)──→ semantic_engine ──────────┤
    │                                                │
    ├──(path=cross)──→ cross_engine ────────────────┤
    │                                                ↓
    └──(refused)──→ audit_writer              confidence
                                                     ↓
                                            response_generator
                                                     ↓
                                               audit_writer
                                                     ↓
                                                    END
```

**Eight nodes. Two conditional edges. Two bypasses.**

**Every path ends at `audit_writer`.** There is no route to `END` that skips it —
which is the graph-level expression of "every query produces an audit row"
(Day 6).

---

### 4.4 The two bypasses

**Bypass 1 — `blocked`.** A Prompt Shield block goes straight to `audit_writer`:

```python
{"router": "router",
 "blocked": "audit_writer"},   # blocked queries skip everything else
```

Obvious in hindsight: a blocked query has no entities to extract, nothing to
retrieve, and `prompt_shield` already wrote its compliance message.

**And it has a consequence you met on Day 9:** because `confidence_node` never
runs, nothing writes a tier — so `role_filtered_response` **omits**
`confidence_tier` on a blocked query rather than sending
`make_initial_state`'s default `"low"`.

**Bypass 2 — `refused`.** This one is not obvious, and it was measured:

```python
# F2: a router refusal exits to audit directly, mirroring the
# "blocked" edge above. It must NOT enter the confidence ->
# response_generator tail: the refusal already carries its own
# response_text, and confidence would rescore it (measured
# 2026-08-12: confidence returns tier=high at 0.7095 on a query
# with no valid company).
"refused": "audit_writer",
```

**`confidence` would rescore a refusal.** The router writes
`confidence_tier="low"` and `confidence_score=0.0`; `confidence_node` reads the
state and — because the query has no retrieved chunks and no SQL result — takes
a path that produced **`tier=high` at `0.7095`** on a query the system had just
refused.

**A node whose contract is "cap, never raise" (Day 30) produced a raised tier**,
because "raise" was never the failure mode it was defending against. The
protection is topological: **do not send a refusal down that path at all.**

---

### 4.5 `route_after_router`, and the F2 fix

```python
def route_after_router(state: QueryState) -> str:
    # F2 step 0: a refusal written by router_node must actually terminate.
    # This function is the ONLY thing the graph consults after the router --
    # it reads `path`, never `error` -- so before this branch existed a
    # refused state still dispatched into an engine and ran the unfiltered
    # search the refusal exists to prevent. Measured 2026-08-12 on a Reliance
    # query: 5 citations returned, all TITAN/ZOMATO pages, tier=high.
    # Keyed on error_node, not bare `error`: `error` is written by several
    # nodes and only the router's belongs upstream of the engine dispatch.
    if state.get("error_node") == "router" and state.get("error"):
        return "refused"

    path = state.get("path")
    if path == "quantitative":
        return "quant_engine"
    if path == "cross":
        return "cross_engine"
    return "semantic_engine"
```

**Three things in one function:**

**1. The bug.** The router *wrote* a refusal — `error`, `response_text`,
`confidence_tier="low"` — and the graph **dispatched into an engine anyway**,
because the routing function only read `path`. The measured result: five
citations from TITAN and ZOMATO pages for a Reliance question, at `tier=high`.

**A refusal that does not terminate is not a refusal.**

**2. The key.** `error_node == "router"` **and** `error`, not bare `error`:

> `error` is written by several nodes and **only the router's belongs upstream of
> the engine dispatch.**

By this point `error` could plausibly have been set by an earlier node in a future
change; `error_node` says *who*. Keying on the pair makes the branch specific to
the condition it was written for.

**3. The default.** `return "semantic_engine"` — anything unrecognised routes to
semantic. Defensible: semantic is the most general path and degrades to a
refusal on low confidence (Day 29), so an unknown `path` fails *safe* rather than
crashing.

And note `route_after_shield` is the trivial counterpart:

```python
def route_after_shield(state: QueryState) -> str:
    return "blocked" if state["is_blocked"] else "router"
```

---

### 4.6 Compilation, and the singleton

```python
def build_graph():
    """
    Compiled once at FastAPI startup and reused across requests (graph
    compilation is not free — don't rebuild per-request).
    """
    graph = StateGraph(QueryState)
    graph.add_node("prompt_shield", prompt_shield_node)
    ...
    graph.set_entry_point("prompt_shield")
    graph.add_conditional_edges(...)
    graph.add_edge("semantic_engine", "confidence")
    ...
    compiled = graph.compile()
    logger.info("LedgerMind query graph compiled successfully")
    return compiled


_compiled_graph = None

def get_graph():
    """Returns the compiled graph singleton, building it on first call."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
```

**`StateGraph(QueryState)`** — the state type is a constructor argument.
LangGraph uses it to know how to merge partial updates.

**`graph.compile()`** validates the topology — unreachable nodes, missing edges,
conditional-edge labels with no mapping — and returns an object with `.ainvoke()`
and `.astream()`.

**Compiled once** (Day 12's lazy singleton). Note the docstring says "at FastAPI
startup" while the code is lazy — compiled on the **first request**, not at
import. Small documentation drift; the behaviour is the lazier and better one.

**And it composes with the model singletons.** Importing `graph.py` imports every
engine, which imports `retriever.py` — but **no ONNX model loads**, because those
are behind their own lazy functions (Day 12). Import is cheap; the first query is
expensive.

---

### 4.7 What is deliberately not used

```python
"""
Uses StateGraph + TypedDict pattern only (no MessagesState, no agent
abstractions) — this is the stable subset of the LangGraph API per the
risk noted at the start of Phase 4 planning.
"""
```

**Three abstractions declined:**

| Not used | Why |
|---|---|
| `MessagesState` | A chat-message list as state. There is no conversation here; the state is a domain object |
| Agent / tool-calling nodes | The model would choose which tool to call. This system chooses (Day 17) |
| Checkpointing / persistence | State is per-request and ends at `audit_writer` |

**"The stable subset of the LangGraph API"** is the operative phrase. A
fast-moving dependency is a maintenance risk, and using less of it is the
mitigation.

**And it connects to `ENGINEERING_DECISIONS.md` ED-008 and to Day 17's
determinism preference:** an agentic router would let the model decide the path
*and* the tools; here the model produces one classification and the graph does
the rest.

---

## 5. The actual LedgerMind file

```
File:        backend/app/engines/graph.py (132 lines)
Purpose:     Wire eight nodes into one StateGraph
Why:         A topology you can draw, stream and validate
Who imports: api/query.py (both endpoints)
What it imports: all eight node functions + two routing functions + QueryState
Entry points: build_graph() · get_graph()
Data in:     a QueryState
Data out:    a populated QueryState
Nodes:       prompt_shield · router · semantic_engine · quant_engine
             cross_engine · confidence · response_generator · audit_writer
Edges:       2 conditional, 5 unconditional, 1 entry point, 1 END
```

**132 lines, of which about half are the docstring diagram and comments.** The
wiring itself is short — which is the point of making topology data.

---

## 6. Deep walkthrough — `build_graph`

**STATE BEFORE.** Nothing. First call in the process.

**Step 1 — construct.**

```python
graph = StateGraph(QueryState)
```

**Step 2 — register nodes.** Eight `add_node(name, fn)` calls. The name is the
key that appears in `astream("updates")` (Day 6) and in `_NODE_LABELS` in
`api/query.py`:

```python
_NODE_LABELS = {
    "prompt_shield": "PROMPT SHIELD",
    "router": "ROUTER",
    ...
}
# Keys must match the node names registered in app/engines/graph.py exactly --
# an unmapped node still streams, it just falls back to its raw name.
```

**Two copies of one list**, in two files — mitigated by the fallback: an unmapped
node degrades to its raw name rather than breaking the stream.

**Step 3 — entry point.**

```python
graph.set_entry_point("prompt_shield")
```

**The shield is first, always.** `CLAUDE.md` §6: *"Prompt Shield runs
pre-router."* Topological, not conventional — there is no way to reach the router
without passing it.

**Step 4 — the two conditional edges.**

**Step 5 — convergence.**

```python
graph.add_edge("semantic_engine", "confidence")
graph.add_edge("quant_engine", "confidence")
graph.add_edge("cross_engine", "confidence")
```

**All three engines converge.** Cross-cutting adjustments (Day 30) apply
regardless of path, which is why `confidence` is a node rather than three
functions.

**Step 6 — the tail.**

```python
graph.add_edge("confidence", "response_generator")
graph.add_edge("response_generator", "audit_writer")
graph.add_edge("audit_writer", END)
```

**Step 7 — compile and log.**

**STATE AFTER.** A compiled graph, cached in `_compiled_graph`.

---

### 6.1 What a node actually receives

LangGraph runs each node with the accumulated state and merges its return value.
Because every node here **mutates and returns the same dict** (Day 3), the merge
is trivial — but note what `astream("updates")` yields (Day 6):

```python
async for update in graph.astream(initial_state, stream_mode="updates"):
    for node_name, partial in update.items():
```

**A `{node_name: partial}` mapping per completed node.** Which is why
`_run_graph` accumulates:

```python
accumulated = dict(initial_state)
...
if partial:
    accumulated.update(partial)
```

**The trace is a byproduct of execution** (Day 6) — a node cannot forget to
report itself, because the report is LangGraph's, not the node's.

---

### 6.2 Adding a fourth path — the full blast radius

Day 3 asked this. Here is the complete answer, because it is the best test of
whether the topology is understood:

| File | Change |
|---|---|
| `engines/state.py` | `path: Optional[Literal[...]]` gains a value |
| `engines/forecast_engine.py` | the node itself |
| `engines/graph.py` | `add_node`, plus a mapping entry in `add_conditional_edges` |
| `engines/router.py` | the PATH CLASSIFICATION prompt block **and** `route_after_router` |
| `engines/response_generator.py` | a branch to format it |
| `api/query.py` | `_NODE_LABELS` and `_trace_detail` |
| `frontend/app/page.tsx` | `composeDocumentBody` |
| **`sql/migrations/0NN_*.sql`** | **`audit_log.query_path`'s `CHECK` constraint** |

**The migration is the one people miss** — and `audit_writer` would fail on
*every* query of the new type, at the very last node, after all the work.

**Note also the prompt change**, which is STOP-AND-ASK (Day 18).

---

## 7. Data flow

```
api/query.py
   get_graph()  ──► _compiled_graph (built on first call, cached)
        │
        ▼ graph.ainvoke(state)   or   graph.astream(state, "updates")
        │
   START
        ▼
   prompt_shield_node          writes is_blocked, block_reason,
        │                             response_text if blocked
        ▼
   route_after_shield(state) -> "blocked" | "router"
        │
        ├── "blocked" ──────────────────────────────────┐
        │                                                │
        ▼ "router"                                       │
   router_node                 writes companies, fiscal_year,
        │                             path, route_reason,
        │                             llm_provider/model
        ▼
   route_after_router(state) -> "refused" | "quant_engine"
                                | "cross_engine" | "semantic_engine"
        │
        ├── "refused" ──────────────────────────────────┤
        │                                                │
        ├── "quant_engine" ──► quant_engine_node ──┐    │
        ├── "cross_engine" ──► cross_engine_node ──┤    │
        └── "semantic_engine" ► semantic_engine ───┤    │
                                                    ▼    │
                                            confidence_node
                                            CAPS ONLY    │
                                                    ▼    │
                                        response_generator_node
                                                    │    │
                                                    ▼    ▼
                                              audit_writer_node
                                                    ▼
                                                   END

EVERY path reaches audit_writer. There is no route to END that skips it.
```

---

## 8. Engineering decision — `StateGraph` over control flow

**Problem.** A branching pipeline that must be drawable, streamable and safely
extensible.

**Decision.** LangGraph `StateGraph` over a `TypedDict`, using only nodes,
edges, conditional edges and `compile()`.

`ENGINEERING_DECISIONS.md` **ED-008**.

| Alternative | Why not |
|---|---|
| **Plain if/elif** | Works, and is not drawable, streamable or inspectable. A fourth path is another `elif` with no structural prompt to update the migration |
| **A hand-rolled state machine** | You would rebuild `astream`, and node-boundary reporting would be instrumentation a node could forget |
| **Celery chains** | Serialisation between steps, and `QueryState` holds non-JSON-friendly objects mid-flight |
| **LangGraph agents / `MessagesState`** | The model would choose the path *and* the tools. This system chooses (Day 17) |
| **An orchestration framework (Airflow, Prefect)** | Built for scheduled batch DAGs, not a 3-second request path |

**Trade-offs accepted.**

- **A dependency on a fast-moving library**, mitigated by using its smallest
  stable subset.
- **Node names duplicated** in `_NODE_LABELS`, with a graceful fallback.
- **The topology is not enforced against the database** — `audit_log`'s `CHECK`
  is a separate copy of the path list.
- **`route_after_router` defaults to semantic** on an unrecognised path, which
  hides a bug rather than surfacing it. Defensible (it fails safe) and worth
  knowing.

**Current validity.** Sound. The graph is genuinely small, and its smallness is
what makes the two bypasses reviewable.

**At 10×.** The graph is not the constraint — it is in-process control flow with
no I/O of its own. If nodes ever needed to run in parallel, the shared-mutable-dict
design would have to change first (Day 3).

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| A refusal still returns citations | `route_after_router` not keying on `error_node` — **the F2 bug** |
| A refusal reported at `tier=high` | The refusal entered the confidence tail |
| A blocked query carrying a confidence tier | The `blocked` bypass removed, or the response not omitting it |
| No audit row | Some path reaches `END` without `audit_writer` |
| A node missing from the SSE trace | It would have to be missing from the graph — the trace is LangGraph's |
| A new `query_path` fails at `audit_writer` | The `CHECK` migration was not applied |
| First query slow | Graph compiled on first call, plus model loads (Day 12) |
| A node name shown raw in the UI | Not in `_NODE_LABELS`. **Degrades gracefully** |

---

## 10. Hands-on experiment

### Experiment 1 — read the topology out of the compiled object

```bash
docker compose exec -T backend python -c "
from app.engines.graph import get_graph
g = get_graph()
gr = g.get_graph()
print('NODES:')
for n in gr.nodes: print('  ', n)
print()
print('EDGES:')
for e in gr.edges:
    cond = ' [conditional]' if getattr(e, 'conditional', False) else ''
    lbl  = f'  ({e.data})' if getattr(e, 'data', None) else ''
    print(f'  {e.source:20} -> {e.target}{lbl}{cond}')
"
```

**Draw it from this output alone**, then compare with the docstring diagram.

### Experiment 2 — the routing functions, driven directly

```bash
docker compose exec -T backend python -c "
from app.engines.router import route_after_shield, route_after_router
from app.engines.state import make_initial_state

def s(**kw):
    st = make_initial_state(query='q', tenant_id='t', user_id='u', request_id='r')
    st.update(kw); return st

print('route_after_shield:')
print('  clean  ->', route_after_shield(s(is_blocked=False)))
print('  blocked->', route_after_shield(s(is_blocked=True)))
print()
print('route_after_router:')
for label, st in [
  ('quantitative',          s(path='quantitative')),
  ('semantic',              s(path='semantic')),
  ('cross',                 s(path='cross')),
  ('router refusal',        s(path='semantic', error='company_not_in_corpus', error_node='router')),
  ('semantic engine error', s(path='semantic', error='low_confidence_refusal', error_node='semantic_engine')),
  ('unknown path',          s(path='forecast')),
  ('no path at all',        s(path=None)),
]:
    print(f'  {label:24} -> {route_after_router(st)}')
"
```

**Row 5 is the important one.** A `semantic_engine` error does **not** route to
`refused` — only the router's does. That is what `error_node` keying buys.

### Experiment 3 — the singleton

```bash
docker compose exec -T backend python -c "
import time, app.engines.graph as G
print('_compiled_graph at import:', G._compiled_graph)
t=time.perf_counter(); g1 = G.get_graph()
print(f'first  get_graph(): {time.perf_counter()-t:.4f}s')
t=time.perf_counter(); g2 = G.get_graph()
print(f'second get_graph(): {time.perf_counter()-t:.6f}s')
print('same object:', g1 is g2)
"
```

### Experiment 4 — the bypasses, end to end

```bash
echo "--- BLOCKED ---"
curl -N -s -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"query":"Should I buy Zomato?"}' | grep '^event:\|"node"' | head -20
echo
echo "--- NORMAL SEMANTIC ---"
curl -N -s -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"query":"What risks does Eternal disclose?"}' | grep '"node"' | head -20
```

**Count the node events.** The blocked query emits **two**: `prompt_shield` and
`audit_writer`. The topology is visible in the stream.

### Experiment 5 — the F2 measurement, reconstructed

```bash
docker compose exec -T backend python -c "
from app.engines.router import route_after_router
from app.engines.state import make_initial_state
st = make_initial_state(query='What were Reliance Industries revenue drivers in FY26?',
                        tenant_id='t', user_id='u', request_id='r')
st.update(path='semantic', error='company_not_in_corpus', error_node='router',
          confidence_tier='low', confidence_score=0.0,
          response_text='This query names a company that is not present ...')
print('WITH the F2 branch      ->', route_after_router(st))
print()
print('WITHOUT it (path only)  -> semantic_engine')
print()
print('Measured 2026-08-12: 5 citations, all TITAN/ZOMATO pages, tier=high,')
print('for a Reliance question the router had already refused.')
print('A refusal that does not terminate is not a refusal.')
"
```

### Experiment 6 — what compile() validates

```bash
docker compose exec -T backend python -c "
from langgraph.graph import END, StateGraph
from app.engines.state import QueryState
g = StateGraph(QueryState)
g.add_node('a', lambda s: s)
g.add_node('orphan', lambda s: s)     # registered, never reached
g.set_entry_point('a')
g.add_edge('a', END)
try:
    g.compile()
    print('compiled — note LangGraph does not reject every orphan')
except Exception as e:
    print('compile REJECTED:', type(e).__name__, e)
print()
g2 = StateGraph(QueryState)
g2.add_node('a', lambda s: s)
g2.set_entry_point('a')
g2.add_conditional_edges('a', lambda s: 'nowhere', {'somewhere': END})
try:
    g2.compile(); print('conditional-edge mismatch: compiled (fails at RUNTIME)')
except Exception as e:
    print('conditional-edge mismatch REJECTED:', type(e).__name__)
"
```

**Find out what `compile()` catches and what it does not.** That is worth knowing
before relying on it.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/graph.py` (all 132 lines) and
`backend/app/engines/router.py`'s two routing functions:

1. Draw the graph from the code, not the docstring. How many nodes, how many
   conditional edges, and how many routes reach `END`?
2. Which two edges bypass `confidence` and `response_generator`? For each, say
   what would go wrong without the bypass.
3. `route_after_router` keys on `error_node == "router"` **and** `error`. Why not
   just `error`?
4. Why is the graph compiled once? Where is the singleton, and what pattern is it?
5. The module docstring names three LangGraph features deliberately not used.
   Which, and why each?

---

## 12. Self-check questions

**Basic**
1. How many nodes and how many conditional edges?
2. What is a node's contract?
3. What does a conditional-edge function return?
4. Which node is the entry point, and why that one?
5. Where does every path end?

**Code**
6. What does `StateGraph(QueryState)` use its argument for?
7. What does `graph.compile()` return?
8. What does `_NODE_LABELS` mirror, and what happens on a miss?
9. Which three edges converge on `confidence`?
10. What does `route_after_router` return on an unrecognised path?

**Why**
11. Why a graph rather than if/elif?
12. Why does a refusal bypass the confidence tail?
13. Why key the refusal branch on `error_node`?
14. Why compile once?
15. Why is only the "stable subset" of LangGraph used?

**Debugging**
16. A refused query returns citations from other companies. What is wrong?
17. A new query path fails at the last node on every query. What was forgotten?
18. A node is missing from the SSE trace. Where do you look, and what can it
    *not* be?

**System design**
19. Add a node that runs **after** `audit_writer` (say, a webhook). What changes,
    and what is the risk?
20. `route_after_router` defaults to `semantic_engine`. Argue for and against, and
    say what you would do.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **Eight nodes**, **two conditional edges**, and **one** route reaches `END` —
   from `audit_writer`. Every path converges there first; there is no edge to
   `END` from anywhere else.
2. **`prompt_shield --(blocked)--> audit_writer`** and
   **`router --(refused)--> audit_writer`**. Without the first, a blocked query
   would run the router (an LLM call, spending quota on a query the system has
   already declined) and then the engines. Without the second — the measured
   case — a refused query **dispatches into an engine and runs the unfiltered
   search the refusal exists to prevent**, and `confidence` **rescores the
   refusal to `tier=high` at `0.7095`**.
3. Because `error` is written by **several** nodes — `semantic_engine`'s
   `low_confidence_refusal`, `quant_engine`'s seven codes,
   `response_generator`'s `synthesis_unavailable` — and **only the router's
   belongs upstream of the engine dispatch**. Keying on bare `error` would be a
   branch that is correct today and wrong the moment any earlier node sets an
   error. `error_node` says *who*.
4. Because compilation is not free and the topology never changes at runtime.
   `_compiled_graph` plus `get_graph()` in `graph.py` — the **lazy singleton**
   pattern (Day 12), the same shape as `_get_dense_model`. Note the docstring says
   "compiled at FastAPI startup" while the code is lazy: it is compiled on the
   **first request**. Small drift; the code's behaviour is the better one.
5. **`MessagesState`** — a chat-message list as state, which does not fit a domain
   state object and there is no conversation. **Agent / tool-calling nodes** — the
   model would choose the path and the tools, contradicting "the model classifies,
   the system decides" (Day 17). **Checkpointing/persistence** — state is
   per-request and terminates at `audit_writer`. All three under one heading:
   *"the stable subset of the LangGraph API"*, because a fast-moving dependency is
   a maintenance risk and using less of it is the mitigation.

### §12 — Basic

1. Eight nodes; two conditional edges (after `prompt_shield` and after `router`).
2. `def X_node(state: QueryState) -> QueryState` — take the state, mutate it,
   return it.
3. A **string label**, which the edge's mapping resolves to a node name.
4. `prompt_shield`. Because the shield must run before anything else —
   topologically, so there is no way to reach the router without passing it.
5. `audit_writer`, then `END`.

### §12 — Code

6. The state schema — LangGraph uses it to know how to merge partial updates from
   each node.
7. A compiled application object exposing `.invoke()`, `.ainvoke()` and
   `.astream()`.
8. The node names registered in `graph.py`. On a miss, `api/query.py` falls back
   to the node's raw name uppercased — the stream is unaffected.
9. `semantic_engine`, `quant_engine`, `cross_engine`.
10. `"semantic_engine"` — the default branch.

### §12 — Why

11. Because the topology becomes **data**: drawable, validated at compile time,
    and streamable via `astream("updates")`, so node boundaries are a byproduct of
    execution rather than instrumentation a node could forget (Day 6).
12. Because the refusal already carries its own `response_text`, and
    `confidence_node` — reading a state with no chunks and no SQL result — produced
    **`tier=high` at 0.7095** on a query the router had just refused. The
    protection is topological rather than a check inside `confidence`.
13. See §11 Q3.
14. Compilation is not free, the topology is static, and rebuilding per request
    would add latency to every query for no benefit.
15. Because LangGraph moves quickly, and the agent abstractions would hand routing
    decisions to the model — the opposite of this system's determinism preference.
    Using the smallest stable subset bounds both the upgrade risk and the design
    risk.

### §12 — Debugging

16. **`route_after_router` is not returning `"refused"`** — either the F2 branch
    is missing, or `error_node` was not set to `"router"` when the refusal was
    written. The refusal reached an engine, which ran an unfiltered search
    (because the company did not resolve — Day 27's F2) and returned real pages
    from the wrong issuers.
17. **The migration for `audit_log.query_path`'s `CHECK` constraint.** The new path
    value is rejected by the database, so `audit_writer` — the **last** node —
    fails on every query of that type, after all the work is done. It is the change
    outside Python that the graph gives you no structural prompt to make.
18. Look at `graph.py` — the node would have to be **absent from the graph**, or
    the routing function never returns the label that reaches it. What it **cannot**
    be is a node "forgetting to report itself": the trace comes from LangGraph's
    `astream("updates")`, not from instrumentation inside the node, so a node that
    runs always appears. If it appears with a raw uppercase name instead of a
    friendly label, that is `_NODE_LABELS`, not the graph.

### §12 — System design

19. **Changes:** `add_node("webhook", webhook_node)`; replace
    `add_edge("audit_writer", END)` with `add_edge("audit_writer", "webhook")` and
    `add_edge("webhook", END)`; add it to `_NODE_LABELS`. **The risk is the
    important part:** every route currently terminates at `audit_writer`, and the
    audit row is the system's guarantee that every query is recorded. Putting a
    node *after* it means a webhook failure happens **after** the record is safe —
    which is the right order — but it also means the node now sits on the critical
    path of the SSE stream and the blocking endpoint, so a slow or hanging webhook
    delays the user's response even though their answer is complete. The correct
    shape is almost certainly **not** a graph node at all: fire it from
    `_run_graph`'s completion, or from a Celery task, so a failure there cannot
    affect the response. If it must be a node, it needs its own timeout and must
    never raise (like `audit_writer`, which logs and continues — Day 44).
20. **For:** semantic is the most general path, and it degrades to a refusal on
    low confidence (Day 29), so an unrecognised `path` fails *safe* — the user gets
    a refusal rather than a 500. It also means a router that returns garbage does
    not take the system down. **Against:** it **hides a bug**. If a code change
    introduced a fourth path value without updating this function, every query of
    that type would silently run semantic retrieval and produce a plausible
    answer — the failure class this whole codebase is built against. **What I would
    do:** keep the safe default *and* make it loud — `logger.error("Unrecognised
    path %r — defaulting to semantic_engine", path)` before returning. That
    preserves the graceful degradation while ensuring the condition is
    observable, which is the same pattern as `_build_filter`'s
    `UNFILTERED WHOLE-TENANT SEARCH` warning (Day 27): **detect and report, do not
    refuse.**

---

## 14. MUST REMEMBER

```text
- EIGHT nodes, TWO conditional edges, and EVERY path ends at audit_writer
- Node contract: def X_node(state: QueryState) -> QueryState
- A conditional-edge function returns a LABEL; the mapping resolves it
- prompt_shield is the ENTRY POINT — topologically, not by convention
- TWO bypasses: "blocked" and "refused", both straight to audit_writer
- A refusal must NOT enter the confidence tail — it would be RESCORED to high
- route_after_router keys on error_node == "router" AND error, not bare error
- Compiled ONCE into _compiled_graph (lazy singleton)
- Only StateGraph + TypedDict. No MessagesState, no agents, no checkpointing
- A fourth path needs a MIGRATION for audit_log.query_path's CHECK
```

## 15. MUST UNDERSTAND

```text
- Why making topology DATA buys drawability, validation and streaming — and why
  the trace being LangGraph's means a node cannot forget to report itself
- Why a refusal that does not terminate is not a refusal
- Why the protection against rescoring is TOPOLOGICAL rather than a check inside
  confidence_node
- Why keying on error_node makes a branch specific to the condition it was
  written for, rather than correct-today
- Why using the smallest stable subset of a fast-moving dependency is the
  mitigation for depending on it
```

---

## 16. This connects to

```text
Day 34 — the quantitative path
   ↓
Day 35 — how the paths are wired                  ← you are here
   ↓
Day 36 — the router: how a question becomes a path
   ↓
Day 37 — the third path: cross-examination
```

Forward references:

- `router_node` and the F2 refusal it writes → **Day 36**
- `cross_engine_node` calling both engines → **Day 37**
- `prompt_shield_node` in detail → **Day 42**
- `audit_writer_node` → **Day 44**
- `_NODE_LABELS` and `_trace_detail` → **Day 6** (already read)
