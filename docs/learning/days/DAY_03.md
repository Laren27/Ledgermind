# Day 03 — Three Engines, One Dictionary

**Phase 0 — Ground · Weight: H (~120 min) · Prerequisites: Days 1–2**

---

## 1. Today's goal

By tonight you can:

- Explain what LedgerMind does, and — more importantly — what it **refuses** to
  do, and why the refusal is the design.
- Name the three query paths, say what kind of question each answers, and give
  an example of a question that would be *wrong* for each.
- Explain `QueryState`: what it is, why the whole architecture is one mutable
  dictionary, what that buys, and what it costs.
- Point at the textbook's dual-path case study and say precisely how LedgerMind
  differs from it.

This is the keystone day. Everything from Day 4 to Day 47 assumes it.

---

## 2. Why now

You can run the stack (Day 1) and read its history (Day 2). You cannot yet read
a single engine file, because every one of them has this signature:

```python
def X_node(state: QueryState) -> QueryState:
```

Until `QueryState` means something, those files are noise. This is the
"stack of cards" principle in its purest form: **`QueryState` is the card
everything else rests on.**

---

## 3. Concepts you must know first

| Concept | From | Why needed today |
|---|---|---|
| Which code is running | Day 1 | You will read `state.py` in the container's own checkout |
| Git as evidence | Day 2 | Several design choices are explained by their commit |
| A Python dictionary | assumed | `QueryState` is one, with type hints |

If "dictionary" is shaky: a dict maps keys to values, `d["key"]` reads,
`d["key"] = v` writes, and it is **mutable** — passing it to a function passes
the same object, not a copy. That last property is the whole of today.

---

## 4. Concept lesson

### 4.1 The one idea

From `CLAUDE.md`, the first non-negotiable:

> **A wrong answer with a ✓ tick is worse than a refusal.**

Sit with this. It inverts the usual product instinct, which is that a system
should answer as many questions as possible.

**Why it inverts here.** A financial analyst reading *"Eternal's FY26 revenue
was ₹54,364 Cr"* cannot tell a correct answer from a wrong one by reading it.
Both are fluent. Both carry a number. If the system marks the wrong one
"verified", it has done something worse than being unhelpful — it has **spent
its credibility to make a falsehood convincing**.

A refusal costs the user two minutes. A confident wrong number can cost them a
decision.

**How to use this idea while reading code.** When something looks
over-engineered — three regex guards before an LLM call, two threshold pairs for
one score, a field that is written and never read — do **not** ask "why is this
so complicated?". Ask:

> **What wrong answer does this prevent?**

There is a documented, measured answer every time. That is the reading strategy
for the next 44 days.

---

### 4.2 What problem the three paths solve

The naive design is: put the filings in a vector database, retrieve, let the LLM
answer. Here is why that fails, question by question:

| Question | What it needs | Why plain RAG fails |
|---|---|---|
| *"What was Eternal's FY26 revenue?"* | One exact number | An LLM reading a retrieved table **transcribes**. Nothing verifies. Nothing catches an OCR-dropped digit |
| *"Who grew revenue faster, Eternal or Paytm?"* | Arithmetic over 2 entities × 2 periods | LLM arithmetic is fluent and unverifiable |
| *"Does management's commentary match the actual PAT?"* | A narrative **and** a verified figure, compared | Retrieval alone gives you the narrative and nothing to check it against |
| *"What are Reliance's revenue drivers?"* | A refusal | Reliance is not in the corpus. Unfiltered retrieval returns *other issuers'* pages, which are topically relevant, and the answer cites a real page from the wrong company |

Note that only the first is a "search" problem at all. **The design follows from
the questions, not from the technology.**

---

### 4.3 The three paths

```
                          ┌──────── semantic ──── retrieval + LLM synthesis, cited
   user question ─router──┼──────── quantitative ─ DSL → SQL → Python arithmetic
                          └──────── cross ──────── both, then contradiction detection
```

**Path 1 — semantic.** Qualitative questions: risks, strategy, governance,
management commentary. Retrieve the most relevant chunks, hand them to the LLM,
instruct it to use **only** those, append citations. This is the one place an
LLM writes prose, and it is allowed there because *there is no ground-truth
number to protect* — only retrieved text to summarise faithfully.

**Path 2 — quantitative.** "What was X in period Y." The LLM emits a structured
**DSL object** with eight fields. A deterministic Python compiler turns it into
parameterised SQL. Python does the arithmetic. **The LLM never writes SQL and
never sees the schema.** The answer is a *template*, not generated prose.

**Path 3 — cross.** "Does the commentary agree with the numbers?" Runs the
quantitative half **first**, then the semantic half with the verified figure
already in context, then compares narrative claims against the figure looking for
contradictions.

**The invariant that unifies them.** From `CLAUDE.md` §6:

> **LLMs never do math.** DSL → SQL only; derived metrics are Python-side
> arithmetic.

---

### 4.4 The idea that makes this repository unusual

**There is no controller / service / repository layering.**

If you have seen Spring, Django or a typical Node backend, you expect:

```
Controller  →  Service  →  Repository  →  Database
 (HTTP)        (logic)      (queries)
```

LedgerMind has none of that. It has:

```
one TypedDict, passed through eight functions,
each of which MUTATES it and returns it
```

That dictionary **is** the architecture. If you go looking for layers, you will
waste an afternoon and conclude the codebase is disorganised. It is not — it is
organised around a different idea.

**What idea?** That the *entire state of a request* should be one inspectable
object at every boundary. At any point in the pipeline you can print the dict
and see everything the system currently believes.

---

### 4.5 `TypedDict` — a dict with a contract

```python
class QueryState(TypedDict):
    query: str
    tenant_id: str
    companies: list[str]
    ...
```

**What it is.** A plain Python `dict` at runtime, with type annotations that
static checkers enforce and the runtime ignores.

**What problem it solves.** You want the cheapness and mutability of a dict, plus
a written-down list of what keys exist and what they hold.

**What existed before.** A bare `dict` (no contract — a typo creates a new key
silently) or a class (a contract, but not a dict, so it cannot be `.update()`-ed,
JSON-serialised or streamed without conversion).

**Mental model.** A `TypedDict` is **a form with named fields**. At runtime it is
just paper; the field names are for *whoever reads it*.

**Critical practical consequence.** From `CLAUDE.md` §7:

> `ChunkResult` is a **TypedDict** — use `chunk["text"]` / `.get()`, never
> `getattr`.

`getattr(chunk, "text")` will fail, because at runtime it is a dict with no such
attribute. This has been got wrong before, which is why it is in the rules.

---

## 5. The actual LedgerMind files

### `backend/app/engines/state.py` — read every line

```
File:        backend/app/engines/state.py (288 lines)
Purpose:     Define the single shared state object flowing through every node,
             plus the rules for recording which LLM served a call
Why it exists: So that "what does the system know right now" has exactly one
             answer, in one place
Who imports it: EVERY module in app/engines/. Also app/api/query.py
What it imports: `time` and `typing`. NOTHING from this project.
Entry points: make_initial_state(), record_llm_call(), clear_llm_attribution()
Data entering: a query string, tenant, user, request id
Data leaving: a fully-populated QueryState after the graph runs
```

**The dependency direction is deliberate and one-way:**

```
engines/*  ──imports──►  state.py
engines/*  ──imports──►  llm/client.py
state.py   ──imports──►  (nothing in this project)
```

`record_llm_call(state, result)` takes `result` **untyped** specifically so that
`state.py` need not import the LLM module. The docstring says so:

> Deliberately untyped here so that state.py does not import the LLM module —
> the dependency runs one way, engines -> state and engines -> client, never
> client -> state.

---

### The five field groups

`QueryState` is organised by **who writes what**. Read it that way:

```python
# ── Fixed at entry ──────────────────────────────────
query, tenant_id, user_id, request_id, start_time

# ── prompt_shield output ────────────────────────────
is_blocked, block_reason

# ── router / entity resolution output ───────────────
companies, ticker, company_unresolved, fiscal_year,
quarter, financial_type, resolved_query, path, route_reason

# ── Path 1 (semantic_engine) output ─────────────────
retrieved_chunks, citations

# ── Path 2 (quant_engine) output ────────────────────
period_assumed, dsl_object, dsl_valid, dsl_attempts,
sql_query, sql_result, sql_row_count, sql_verified

# ── Path 3 (cross_engine) output ────────────────────
contradictions

# ── Confidence, response, error, audit ──────────────
confidence_score, confidence_tier, crag_triggered, crag_count,
response_text, restatement_disclosed, error, error_node,
llm_provider, llm_model, tokens_used, cache_hit, latency_ms
```

Reading it by writer tells you the pipeline order without opening `graph.py`.

---

### The three nested types

`QueryState` contains four helper `TypedDict`s. Two matter today:

**`ChunkResult`** — one retrieved passage:

```python
class ChunkResult(TypedDict):
    chunk_id, doc_id, text, page_number
    company, fiscal_year, quarter, financial_type, chunk_type, filing_date
    dense_score, sparse_score, rrf_score
    reranker_score: float
    reranker_backend: str   # "cohere" | "local" | "none"
    speaker_role: str
```

Look at `reranker_backend`. A comment on that line reads:

> scores are on different scales per backend

**That one field is the most consequential thing in this file**, and you will
spend all of Day 28 on it. Note it now: a score without its backend is
meaningless.

**`DSLObject`** — the eight-field structure the LLM is allowed to emit:

```python
metric, entity, period, fiscal_year, quarter,
financial_type, operation, comparison_entity, comparison_period
```

That is the *entire* vocabulary the model has for a numeric question. It cannot
write SQL because it is never asked for SQL. **Day 32** is this object.

---

## 6. Deep code walkthrough

### 6.1 `make_initial_state()`

```python
def make_initial_state(query, tenant_id, user_id, request_id,
                       execution_context=None) -> QueryState:
    """Returns a fully-initialised QueryState with safe, explicit defaults."""
    return QueryState(
        query=query, tenant_id=tenant_id, user_id=user_id,
        request_id=request_id, start_time=time.time(),
        is_blocked=False, block_reason=None,
        companies=[], ticker=None, company_unresolved=None,
        ...
        confidence_score=0.0, confidence_tier="low",
        ...
    )
```

**STATE BEFORE.** A string typed by a user, a tenant id from a verified JWT.

**Execute.**

**STATE AFTER.** One dict with **every key present**, none missing.

**Why every key is initialised explicitly.** So that no node ever has to
distinguish "this key does not exist" from "this key holds a default". A
`KeyError` and a `None` are different bugs; this removes the first entirely.

**Now the sharp edge.** `confidence_tier="low"` is a **default**, and defaults
lie. A Prompt Shield block routes straight to `audit_writer` (Day 35), so
`confidence_node` **never runs** and nothing ever computes a tier. What reached
the client was this default `"low"` — **indistinguishable on the wire from a
tier that was computed and came out low.**

That was fixed on 2026-08-22, in commit `7d580df` — the one you read on Day 2 —
by *omitting* the key from the response rather than sending the default:

```python
if response["is_blocked"]:
    base.pop("confidence_tier", None)
```

**Why omitted rather than set to `null`?** Because that choice was *measured*.
`eval_runner.py`'s `out_of_corpus` scorer reads the tier through
`.get("confidence_tier", "low")` inside a PASS condition. An absent key scores
exactly as before; an explicit `null` flips the verdict from pass to fail. The
comment in `response_shaping.py` records the verification: all twelve golden
categories, field set three ways, absent and `"low"` agree everywhere, `null`
diverges.

**What if you removed the default?** Every node reading `confidence_tier` before
`confidence_node` would need a `.get()` with its own fallback — three or four
places, each free to choose a different one. The default is not the problem; the
problem was *reporting* it as if it were a measurement.

---

### 6.2 `record_llm_call()` — precedence, not last-writer-wins

```python
_PROVIDER_TAINT = {"gemini": 0, "groq": 1}

def record_llm_call(state, result) -> None:
    incoming = getattr(result, "provider", None)
    if not incoming:
        return
    current = state.get("llm_provider")
    if current is not None:
        if _PROVIDER_TAINT.get(incoming, 99) <= _PROVIDER_TAINT.get(current, 99):
            return
    state["llm_provider"] = incoming
    state["llm_model"] = getattr(result, "model", None)
```

**STATE BEFORE.** `llm_provider` is `None`, or holds a provider from an earlier
call in the same query.

**Execute** — after each LLM call.

**STATE AFTER.** `llm_provider` holds the **worst** provider seen so far.

**Why "worst wins" and not "last wins".** A semantic query makes **two** LLM
calls: the router classification, then the synthesis. If *either* is served by
the fallback, the answer the user received is a **fallback artifact**, regardless
of which call happened last. Last-writer-wins would report a Groq-classified,
Gemini-synthesised answer as "gemini".

**And this is not hypothetical.** The comment block above the function records
two production failures:

> `llm_provider` was set by whichever call last SUCCEEDED. The synthesis floor
> returns provider=None, which overwrote nothing, so floor responses logged as
> "gemini". Measured 2026-07-31: the eval provider gate reported 11/45
> non-Gemini when the true figure was >= 13.

**What breaks if you delete `_PROVIDER_TAINT`?** Nothing visibly. Every answer
still returns. The audit log simply becomes **wrong about which system produced
each answer** — and stays wrong silently. This is the defining shape of a
LedgerMind bug: not a crash, an *unnoticed loss of truthfulness*.

---

### 6.3 `clear_llm_attribution()`

```python
def clear_llm_attribution(state) -> None:
    state["llm_provider"] = None
    state["llm_model"] = None
```

Called from exactly one place: the semantic **synthesis floor** — the path taken
when *both* providers failed and the system returns raw excerpts instead of a
generated answer.

Without this, the router's earlier successful attribution would still be sitting
in the state, and **a total LLM outage would be recorded as a normally-served
answer.**

Three functions, all about the same principle: *the record must not be able to
overstate what happened.*

---

## 7. Data flow — one query, at the state level

```
POST /api/query  {"query": "What was Eternal's FY26 revenue?"}
        │
        ▼
make_initial_state(...)
        │   {query:"...", companies:[], path:None, is_blocked:False,
        │    confidence_tier:"low", response_text:None, ...}
        ▼
prompt_shield_node        writes: is_blocked=False, block_reason=None
        ▼
router_node               writes: companies=["ETERNAL"], fiscal_year="FY26",
                                  path="quantitative", resolved_query="ETERNAL
                                  FY26 consolidated What was...",
                                  llm_provider="gemini"
        ▼
quant_engine_node         writes: dsl_object={metric:"revenue", ...},
                                  sql_query="SELECT value...", sql_result=[...],
                                  sql_verified=True, confidence_tier="high"
        ▼
confidence_node           may LOWER confidence_tier. Never raises it.
        ▼
response_generator_node   writes: response_text="Revenue was ₹54,364 Cr..."
        ▼
audit_writer_node         writes: latency_ms; INSERTs the whole thing to Postgres
        ▼
role_filtered_response(state, role)   →  JSON, filtered by role
```

**The property worth naming.** At every arrow, the *complete* state exists as one
printable object. There is no hidden context, no request-scoped service holding
half the answer. That is what "one dictionary" buys.

**And the cost, equally clearly.** Any node can write any field. Nothing stops
`response_generator` from setting `sql_verified`. The discipline that prevents
chaos is convention plus code review — not the type system. `confidence_tier` is
written by three different nodes and finally arbitrated by `_reconcile_cross`,
and you have to *know* that; the type does not tell you.

---

## 8. Engineering decision — one mutable dict, or something else?

**Problem.** A branching pipeline of eight stages, where later stages need
almost everything earlier stages produced, and an audit record needs *all* of it.

**Decision.** One `TypedDict`, mutated in place, returned by every node.

**Alternatives, and why not:**

| Alternative | Why not here |
|---|---|
| **Immutable state, new object per node** | Safer, and genuinely better for reasoning. But LangGraph's node contract is `state -> partial update`, and every node would need to construct a full copy. Eight copies of a dict holding 20 retrieved chunks is real memory on a 512 MB tier |
| **A class with properties** | You get encapsulation, and you lose `dict`-ness: no `.update()`, no direct JSON serialisation, no streaming a partial as-is. `api/query.py` streams *partial state updates* straight to the client — that works because they are dicts |
| **Separate objects per path** | Cross-examination runs **both** engines. You would need conversion at the boundary, and the audit writer would need to handle three shapes |
| **Pydantic model** | Validation on every mutation, which is cost you pay eight times per request for a value that never crosses a trust boundary. Pydantic is used at the boundaries that *do* — HTTP and LLM output |

**Trade-off accepted.** Total inspectability, at the cost of no write protection.
The audit log, the SSE trace and role filtering all fall out of the dict for
free. In exchange, "who wrote this field?" is answered by reading, not by types.

**Current validity.** Sound at this size. `state.py` is 288 lines of which most
is comment; the dict has ~40 keys. At three times that it would need splitting.

**At 10× / in production.** The pressure point is not the dict but *concurrency*:
mutation is safe here only because one request owns one dict on one task. If a
node ever forked parallel work over the same state, this design would need to
change first.

**Where the repository states this.** `ENGINEERING_DECISIONS.md` **ED-008** —
"LangGraph `StateGraph` over a `TypedDict`, and nothing more".

---

## 9. Failure modes

| Failure | Cause | Signature |
|---|---|---|
| `KeyError` on a state field | A node read a key `make_initial_state` does not set | Immediate crash — the *good* case |
| A field silently `None` when it should hold data | The producing node returned early on an error path | `.get()` returns `None`; downstream renders nothing. **No error.** |
| A default reported as a measurement | `confidence_tier="low"` on a blocked query | Fixed in `7d580df` by omitting the key |
| Wrong provider in the audit log | Last-writer-wins instead of precedence | Fixed by `_PROVIDER_TAINT` |
| An outage recorded as a normal answer | Attribution not cleared on the synthesis floor | Fixed by `clear_llm_attribution` |
| `getattr(chunk, "text")` fails | `ChunkResult` is a **dict** | Use `chunk["text"]` |

Notice that four of six are **silent**. That is the failure mode this codebase is
built to fight, and it is why so much of `state.py` is comment.

---

## 10. Hands-on experiment

### Experiment 1 — build a state and look at it

```bash
docker compose exec -T backend python -c "
from app.engines.state import make_initial_state
import json
s = make_initial_state(
    query='What was Eternal revenue in FY26?',
    tenant_id='t-1', user_id='u-1', request_id='r-1')
print(json.dumps(s, indent=2, default=str))
print()
print('KEYS:', len(s))
"
```

Read every key. Note which are `None`, which are `[]`, which have a default. Ask
of each: *if a node never writes this, what does the user see?*

### Experiment 2 — the default that lied

```bash
docker compose exec -T backend python -c "
from app.engines.state import make_initial_state
s = make_initial_state(query='q', tenant_id='t', user_id='u', request_id='r')
print('tier before ANY node runs:', repr(s['confidence_tier']))
print('score before ANY node runs:', repr(s['confidence_score']))
"
```

`low` and `0.0` — and nothing has computed anything. **This is exactly what a
blocked query used to return to the client.** Now read the fix:

```bash
grep -n -A 3 'if response\["is_blocked"\]' backend/app/api/response_shaping.py
```

### Experiment 3 — precedence in action

```bash
docker compose exec -T backend python -c "
from app.engines.state import make_initial_state, record_llm_call
from types import SimpleNamespace

s = make_initial_state(query='q', tenant_id='t', user_id='u', request_id='r')

record_llm_call(s, SimpleNamespace(provider='groq',   model='gpt-oss-120b'))
print('after groq  :', s['llm_provider'], s['llm_model'])

record_llm_call(s, SimpleNamespace(provider='gemini', model='flash-lite'))
print('after gemini:', s['llm_provider'], s['llm_model'], ' <- did NOT overwrite')
"
```

Gemini did **not** replace Groq. The answer was still touched by the fallback, so
the record must keep saying so.

### Experiment 4 — the same object, not a copy

```bash
docker compose exec -T backend python -c "
from app.engines.state import make_initial_state
def fake_node(state):
    state['path'] = 'quantitative'
    return state
s = make_initial_state(query='q', tenant_id='t', user_id='u', request_id='r')
r = fake_node(s)
print('same object?', r is s)
print('original mutated?', s['path'])
"
```

`True` and `quantitative`. **Every node mutates the caller's dict.** Returning it
is a convention that makes the flow readable — it is not a copy.

### Experiment 5 — read the file, all of it

```bash
docker compose exec -T backend sh -c 'wc -l /app/app/engines/state.py'
```

288 lines. Open `backend/app/engines/state.py` in your editor and read it top to
bottom, comments included. **This is the only file in the course you are asked to
read in full on the day it appears.** Most of it is prose explaining why a field
has the shape it has, and that prose is the day's real material.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/state.py` and answer:

1. Which fields does the **router** write? Which does **quant_engine** write?
   (The section comments tell you — but say what each one *means*.)
2. Find the field whose comment says it is *"WRITTEN AND NEVER READ on the query
   path"*. What is it, and why was it kept single-valued rather than made a list?
3. `companies` is `list[str]` and an **empty list is legal**. What does an empty
   list mean, and what does the retriever do when it sees one?
4. Find `_PROVIDER_TAINT`. Explain the comparison `<=` in `record_llm_call` — why
   `<=` and not `<`?
5. `ChunkResult` has both `rrf_score` and `reranker_score`. Why two, and what is
   `reranker_score` initialised to before reranking runs?

Then open `backend/app/engines/graph.py` (132 lines) and answer:

6. Which two edges bypass `confidence` and `response_generator` entirely, and
   what does the comment say happens if they do not?

---

## 12. Self-check questions

**Basic**
1. Name the three query paths and the kind of question each answers.
2. What is `QueryState`, at runtime?
3. Who decides which path a query takes?
4. What does "the LLM never does math" mean concretely?
5. What are `llm_provider` and `llm_model` for?

**Code**
6. What does `make_initial_state` guarantee about the returned dict?
7. Why does `record_llm_call` take an untyped `result`?
8. What is `clear_llm_attribution` for, and who calls it?
9. How do you read the text of a `ChunkResult`, and how do you **not**?
10. What is `resolved_query`, and how does it differ from `query`?

**Why**
11. Why is one mutable dictionary preferred over service/repository layers here?
12. Why is `confidence_tier` **omitted** rather than set to `null` on a blocked
    query?
13. Why is provider attribution by precedence rather than last-writer-wins?
14. Why does `state.py` import nothing from this project?
15. Why does the DSL have exactly eight fields instead of letting the model write
    SQL?

**Debugging**
16. An answer renders with no citations and no error. Name two state fields you
    would print first.
17. The audit log says `llm_provider = "gemini"` but the answer looks degraded.
    What are the two things that could have gone wrong historically?
18. A node raises `KeyError: 'period_assumed'`. What does that tell you about
    where the state came from?

**System design**
19. You must add a fourth path, `"forecast"`. List every file that changes.
20. Name one concrete situation in which the mutable-shared-dict design would
    have to be replaced, and what you would replace it with.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **Router writes:** `companies`, `ticker`, `company_unresolved`,
   `fiscal_year`, `quarter`, `financial_type`, `resolved_query`, `path`,
   `route_reason` — i.e. *what the question is about* and *which engine should
   handle it*. **quant_engine writes:** `period_assumed`, `dsl_object`,
   `dsl_valid`, `dsl_attempts`, `sql_query`, `sql_result`, `sql_row_count`,
   `sql_verified` — i.e. *the structured request, the SQL it compiled to, and
   whether the result is trustworthy*.
2. **`ticker`.** The comment states it is written and never read on the query
   path, verified by grep on 2026-08-22. It was kept single-valued and derived
   rather than made a list because *"making a dead field plural would imply a
   consumer that does not exist."* That is a real design principle: a type
   should not advertise a capability nobody uses.
3. Empty means **no issuer resolved**. `retriever._build_filter` then **drops the
   company condition entirely** and logs a `WARNING` — the search runs
   unfiltered across the whole tenant. This is legal and load-bearing: golden
   question Q051 ("Who grew revenue faster in FY26, Eternal or Paytm?") passes
   *because* retrieval runs unfiltered while the DSL carries both issuers
   through `entity`/`comparison_entity`.
4. `<=` means "if the incoming provider is **no worse** than the current one,
   keep the current one". With `<`, an equal-taint provider would overwrite —
   harmless for the provider string, but it would also overwrite `llm_model`,
   which could replace the recorded model with a later call's. `<=` makes the
   function idempotent for repeat calls from the same provider.
5. `rrf_score` is the **fusion** score from hybrid search (dense + sparse merged
   by rank). `reranker_score` is the **cross-encoder** score from the second,
   more precise pass. Two stages, two scores. Before reranking runs,
   `reranker_score` is initialised to `float("-inf")` and `reranker_backend` to
   `"none"` — chosen so that unscored chunks reaching the confidence scorer are
   **detectable**, not silently treated as terrible matches. You will see the
   loud error that fires on this on **Day 29**.
6. The **`blocked`** edge (from `prompt_shield`) and the **`refused`** edge
   (from `router`), both going straight to `audit_writer`. The comment in
   `graph.py` says the refusal must not enter the confidence tail because the
   refusal already carries its own `response_text` and **confidence would rescore
   it** — measured 2026-08-12, `confidence` returned `tier=high` at `0.7095` on a
   query with no valid company.

### §12 — Basic

1. **Semantic** — qualitative/textual (risks, strategy, commentary).
   **Quantitative** — a specific figure or computation.
   **Cross** — verify a qualitative claim against a quantitative fact.
2. A plain Python `dict`. The type annotations are for static checkers; the
   runtime ignores them entirely.
3. The **router node**, using one LLM classification call, unless the UI sends an
   `execution_context` override — which is applied *after* the refusal check, so
   an override cannot route past a failed entity resolution.
4. The LLM emits a DSL object with eight fields. A Python compiler turns it into
   parameterised SQL; Python computes YoY, comparisons and CAGR from the returned
   values. The model never writes SQL and never sees the schema.
5. Recording **which provider and model actually served the call**, because an
   answer produced by the fallback is a materially different artifact from one
   produced by the primary and must not be indistinguishable in the record.
   Admin-tier only in the response; always written to the audit log.

### §12 — Code

6. That **every key exists** with an explicit default — so no node ever has to
   distinguish "missing key" from "default value", and a `KeyError` becomes a
   real bug rather than an expected condition.
7. So that `state.py` imports nothing from this project. The dependency runs
   `engines → state` and `engines → client`, never `client → state`. Typing the
   parameter would create the back-edge.
8. It nulls `llm_provider` and `llm_model` when a response was **not** produced
   by an LLM — currently only the semantic synthesis floor, reached when both
   providers fail. Without it, the router's earlier attribution would remain and
   a total outage would be logged as a normally-served answer.
9. `chunk["text"]` or `chunk.get("text")`. **Never** `getattr(chunk, "text")` —
   it is a dict at runtime and has no such attribute.
10. `query` is the user's raw text. `resolved_query` is the same text prefixed
    with the extracted entity and period tokens (e.g. `"ETERNAL FY26 Q4
    consolidated <original>"`), which gives BM25 exact terms to match on. It is
    what retrieval actually searches with.

### §12 — Why

11. Because the *entire* state of a request should be one inspectable object at
    every boundary. The audit log, the SSE node trace and role-based filtering
    all fall out of that for free. Layers would give write protection and cost
    inspectability plus conversion at three boundaries.
12. Because the choice was **measured**. `eval_runner`'s `out_of_corpus` scorer
    reads the tier through `.get("confidence_tier", "low")` inside a PASS
    condition — an absent key scores exactly as before, while an explicit `null`
    flips pass to fail. Verified across all twelve golden categories with the
    field set three ways.
13. Because a semantic query makes two LLM calls, and if **either** is served by
    the fallback the answer is a fallback artifact regardless of call order.
    Last-writer-wins reported floor responses as `"gemini"` and understated the
    non-Gemini count in an eval gate (11/45 reported, ≥13 actual).
14. To keep the dependency one-way. If `state.py` imported the LLM client, and
    the client ever needed a state type, you would have a cycle. Keeping the
    keystone module dependency-free means anything can import it safely.
15. Because a fixed vocabulary is **checkable**. Eight named fields can be
    validated, and an invalid combination produces a repair hint rather than a
    wrong answer. Free-form SQL from a model can be syntactically perfect and
    semantically wrong — and there is no validator for "this SQL answers a
    different question than the one asked".

### §12 — Debugging

16. `error` and `error_node` — they name which node gave up and why. Then
    `retrieved_chunks` versus `citations`: if chunks exist but citations do not,
    the divergence is in citation building; if neither exists, retrieval returned
    nothing and the question is *why* (empty candidate set = network signature;
    low-scoring = retrieval signature).
17. (a) **Last-writer-wins** — a Groq-served call followed by a Gemini-served one
    reported `"gemini"`. (b) **The synthesis floor** returned `provider=None`,
    which overwrote nothing, so a floor response inherited the router's earlier
    `"gemini"`. Both are fixed; both are why the mechanism looks elaborate.
18. That the state did **not** come from `make_initial_state`. Someone built a
    dict by hand or reconstructed a partial. `make_initial_state` sets every key,
    so a `KeyError` on a declared field means the constructor was bypassed.
19. `state.py` (the `path` `Literal`), `graph.py` (register node + edge),
    `router.py` (the prompt's PATH CLASSIFICATION block **and**
    `route_after_router`), a new `forecast_engine.py`, `response_generator.py`
    (a branch to format it), `api/query.py` (`_NODE_LABELS`, `_trace_detail`),
    `frontend/app/page.tsx` (`composeDocumentBody`), and — easily forgotten —
    **a migration**, because `audit_log.query_path` has a `CHECK` constraint
    listing the legal values.
20. **Parallel sub-queries within one request.** If a node ever fanned out
    concurrent work that wrote back into the same state, mutation would race.
    The replacement: immutable state with each branch returning a partial, merged
    by an explicit reducer — which is the LangGraph pattern this codebase
    deliberately did not need yet. (Also acceptable: multi-turn conversation
    state, where one dict per request stops being the right unit.)

---

## 14. MUST REMEMBER

```text
- A wrong answer with a ✓ tick is worse than a refusal
- Three paths: semantic (text) · quantitative (numbers) · cross (both)
- LLMs never do math — they emit an 8-field DSL; Python compiles and computes
- QueryState is ONE TypedDict, mutated in place by every node
- ChunkResult is a dict: chunk["text"], never getattr
- companies is a LIST; the empty list is legal and means "no issuer resolved"
- reranker_score is meaningless without reranker_backend
- Attribution moves only toward MORE degraded — never last-writer-wins
```

## 15. MUST UNDERSTAND

```text
- Why the design follows from the QUESTIONS, not from the technology
- What one shared dict buys (total inspectability, free audit + trace + role
  filtering) and what it costs (no write protection; convention only)
- Why a default reported as a measurement is a lie, even when the value is right
- Why four of six failure modes here are SILENT — and why that shapes the code
- The reading strategy: when something looks over-engineered, ask
  "what wrong answer does this prevent?"
```

---

## 16. This connects to

```text
Day 2 — history as evidence
   ↓
Day 3 — the system: three paths, one dictionary        ← THE KEYSTONE
   ↓
Day 4 — how a request physically arrives at the code that builds this dict
```

Forward references — every one of these is a field you met today:

- `is_blocked` → **Day 42** (Prompt Shield)
- `companies`, `path`, `route_reason` → **Day 36** (the router)
- `retrieved_chunks`, `citations`, `confidence_tier` → **Days 25–30**
- `reranker_backend` → **Day 28** (the most important day in the course)
- `dsl_object`, `sql_verified` → **Days 31–34**
- `contradictions` → **Day 37**
- `llm_provider`, `llm_model` → **Day 19**
- `latency_ms`, and the audit row → **Day 44**
