# LedgerMind — Learning Journal

A running record of what I learned, where it lives in the code, and what
confused me on the way. Newest entries at the bottom.

Format per entry: **what I learned → where it appears → why it matters → what
confused me → resolution → beginner takeaway → what to learn next.**

---

## Entry 001 — 2026-08-20 — The whole system is one dictionary

### What I learned
LedgerMind has no controller/service/repository layering. A request creates one
`TypedDict` called `QueryState` and passes it through eight functions, each of
which mutates it and returns it. That dict *is* the architecture.

```python
class QueryState(TypedDict):
    query: str; tenant_id: str; user_id: str; request_id: str; start_time: float
    is_blocked: bool; block_reason: Optional[str]          # ← prompt_shield writes
    company: Optional[str]; fiscal_year: Optional[str]     # ← router writes
    path: Optional[Literal["semantic","quantitative","cross"]]
    retrieved_chunks: List[ChunkResult]                    # ← semantic_engine writes
    dsl_object: Optional[DSLObject]; sql_verified: bool    # ← quant_engine writes
    confidence_score: float; confidence_tier: ...          # ← several nodes write
    response_text: Optional[str]                           # ← response_generator
    llm_provider: Optional[str]; latency_ms: int           # ← audit fields
```

### Where it appears
`backend/app/engines/state.py:66`. Every node in `backend/app/engines/` has the
signature `def X_node(state: QueryState) -> QueryState`.

### Why it matters
It makes the pipeline **inspectable at every boundary**. Because there is one
object, the SSE endpoint can emit a partial update after each node without any
instrumentation inside the nodes — the trace is a byproduct of real execution, so
a node cannot silently fail to report itself (`api/query.py:150-153`).

It also makes the audit log trivially complete: `audit_writer` reads the same
dict everything else wrote to.

### What confused me
Shared mutable state is exactly what most architecture advice tells you to
avoid. Why is it right here?

### Resolution
Three properties make it safe in this specific case:
1. **The lifetime is one request.** No thread shares it, nothing outlives it.
2. **The write order is fixed by the graph.** Nodes are not independent actors
   racing; they are a sequence with declared edges.
3. **The fields are grouped in the declaration by which node writes them**, so
   the ownership that a class hierarchy would encode is encoded in comments and
   ordering instead.

The real cost shows up where a field is written by *several* nodes.
`confidence_tier` is set by the path engine, possibly capped by `confidence`,
and possibly overwritten again by `_reconcile_cross`. That needed an explicit
**AUTHORITY RULE** comment naming which module is final for which path — because
the type system cannot express it.

### Beginner takeaway
> A pipeline whose stages are strictly ordered can share one mutable object.
> A system whose components run concurrently cannot.

### Learn next
LangGraph `StateGraph`; TypedDict vs dataclass; why `record_llm_call()` exists
instead of direct assignment.

---

## Entry 002 — 2026-08-20 — Why the LLM is not allowed to touch numbers

### What I learned
Ask an LLM for a financial figure and it produces a fluent, unverifiable answer.
LedgerMind removes that possibility structurally: the model emits an **eight-field
JSON object**, and deterministic Python turns that into SQL.

```python
class GeminiDSLResponse(BaseModel):
    metric: str; entity: str; fiscal_year: str; quarter: Optional[str]
    financial_type: str; operation: str
    comparison_entity: Optional[str]; comparison_period: Optional[str]
```

### Where it appears
`app/engines/quant_engine.py` (generation + execution),
`app/engines/dsl_compiler.py` (validation + SQL compilation).

### Why it matters
The model's job shrinks from *"be right about finance"* to *"pick strings from
these lists"*. The first is unverifiable; the second is validated by
`DSLValidator` before a single byte reaches Postgres. All arithmetic — YoY,
CAGR, comparisons — happens in Python over values the database returned.

### What confused me
Why not just let the model write SQL? Text-to-SQL is a well-known technique.

### Resolution
Because a wrong `WHERE` clause **returns a real number for the wrong thing**,
and nothing downstream can detect it. A DSL has a finite vocabulary, so a wrong
metric name fails validation loudly; a wrong SQL predicate succeeds quietly.

Then a second, subtler thing clicked. The validator cannot catch everything —
`metric` is a **required** field, so a model that has no correct option
*substitutes the nearest one*, and the substitution is perfectly valid DSL.
Measured live: *"What was Paytm's EBITDA for FY26?"* returned `total_expenses`
(₹8,523 Cr) with `sql_verified=True`.

That is why three regex guards run over the **raw query text before any LLM call
happens at all**. Once the DSL exists, the user's real intent has been
overwritten. The raw query is the only place it still exists.

### Beginner takeaway
> Constrain the model's output space until wrongness becomes detectable. Then
> check the *input* separately for the wrongness the output constraint cannot
> express.

### Learn next
Structured output; the three Stage-0 guards; `registry.derived_metric_aliases()`
vs `metric_anchor_phrases()` and their opposite polarity.

---

## Entry 003 — 2026-08-20 — Two search algorithms, because neither is enough

### What I learned
Retrieval runs **two** searches and fuses them:

```python
result = client.query_points(
    prefetch=[
        Prefetch(query=dense_vector,  using="dense",  limit=20, filter=F),
        Prefetch(query=sparse_vector, using="sparse", limit=20, filter=F),
    ],
    query=FusionQuery(fusion=Fusion.RRF),
    limit=20,
)
```

### Where it appears
`app/engines/retriever.py:235-254`.

### Why it matters
Embeddings understand *meaning* — "profitability commentary" finds "margin
improvement". BM25 understands *tokens* — "PPBL", "Regulation 33", "FY26",
"Hyperpure". Financial questions need both, constantly.

### What confused me
Two things.

First: how do you combine a cosine score (0–1) with a BM25 score (unbounded)?

Second: why is the filter passed to *each* `Prefetch` rather than once at the
top?

### Resolution
**The scores are never combined.** RRF fuses **rankings**, not scores: each list
contributes `1/(k + rank)`. Position is the only thing the two systems share, so
position is what you fuse on.

And the filter is per-leg because filtering at fusion level means both legs
retrieve *unfiltered* candidates first — so the ranking each leg produces has
already been polluted by documents that should never have competed. The
docstring says it plainly: *"filtering at fusion level would allow unfiltered
candidates to pollute ranking."*

### Beginner takeaway
> When two systems produce incomparable numbers, compare their **orderings**.
> And filter before you rank, never after.

### Learn next
RRF's `k` parameter; why the RRF score (~0.016) is a **third** incompatible
scale; the bug where it was fed to reranker thresholds.

---

## Entry 004 — 2026-08-20 — A number is meaningless without knowing which instrument produced it

### What I learned
This is the most transferable idea in the repository, and it appears in three
independent places.

**Instance 1 — reranker scores.** Cohere returns relevance in 0–1. The local
ONNX cross-encoder returns raw logits, roughly −12…+2. A threshold of `−4.5`
means "quite good" on one scale and "always true" on the other.

```python
if backend == "cohere":
    high_threshold, medium_threshold = 0.5, 0.15
else:
    high_threshold, medium_threshold = -4.5, -7.5
```

**Instance 2 — LLM provider.** An answer served by the Groq fallback is a
materially different artifact from one served by Gemini, and must not be
indistinguishable in the audit log.

**Instance 3 — eval results.** `--model` was for a long time *only a label*.
Nothing recorded which model actually served a call, so two full sweeps were
reported under a model that never served one.

### Where it appears
`semantic_engine.py:36-59` (thresholds), `state.py:219-271` (attribution),
`response_shaping.py:95-131` (exposing `reranker_backend` at admin tier).

### Why it matters
Before the split thresholds existed, **the same query returned `tier=medium` on
one run and `tier=high` on another**, purely because a WSL2 network flap sent one
run to the fallback reranker. Both tiers were *correct for their scale*. The
defect was that the reader could not tell which scale they were on.

### What confused me
Why expose `reranker_backend` in the API response at all? The code already picks
the right thresholds, so the *tier* is correct either way.

### Resolution
Because the **score** is also in the response, next to the tier, with no unit.
A human reading `0.42` cannot know whether that is a mediocre Cohere score or an
impossible ONNX logit. And that is not hypothetical: reading `−3.39` as a Cohere
score produced a wrong conclusion about threshold calibration **that reached this
repo's documentation before it was caught**.

The comment adds a detail I would not have thought of: the exposed value reports
what is actually *tagged*, and `None` when nothing is — deliberately **not**
`_score_confidence`'s `.get("reranker_backend", "local")` default. There, "local"
is a *safety choice* (assume the stricter scale when unsure). Here it would be an
*observation*, and reporting an assumption as an observation is exactly how the
mistake happened.

### Beginner takeaway
> Never ship a number without shipping the thing that gives it meaning. And
> never let a safe default in one place become a stated fact in another.

### Learn next
`clear_llm_attribution()`; `_PROVIDER_TAINT`; why the eval runner asserts
`llm_model` against its own `--model` argument rather than trusting it.

---

## Entry 005 — 2026-08-20 — Refusal is a first-class outcome with its own plumbing

### What I learned
"Refuse" is not an error path bolted onto the side. It has dedicated edges in
the graph:

```python
graph.add_conditional_edges("router", route_after_router, {
    "semantic_engine": "semantic_engine",
    "quant_engine":    "quant_engine",
    "cross_engine":    "cross_engine",
    "refused":         "audit_writer",     # ← skips the entire tail
})
```

### Where it appears
`app/engines/graph.py:88-103`, `router.py:368-389`.

### Why it matters
Refusals occur at five distinct points, each with its own reason string:
prompt shield (blocked), router (unknown company / no provider), quant Stage 0
and 0b (metric not computable / not queryable), semantic (low confidence after
CRAG), and response generator (post-generation refusal detected).

### What confused me
Why does a refusal skip `confidence` and `response_generator`? Those nodes would
just pass a refusal through unchanged… wouldn't they?

### Resolution
No — and this is measured. `confidence_node` **rescores**. On 2026-08-12 a
refused Reliance query that reached the tail came out at **`tier=high`, score
0.7095**. The refusal text was correct and the confidence badge beside it said
"high". So the edge exists to prevent a downstream node from contradicting an
upstream decision.

There is a second lesson hiding in the same fix. Before that edge existed,
`router_node` was already *writing* the refusal — and `route_after_router` still
dispatched into an engine, because that function reads `path` and never looked at
`error`. **Writing a refusal is not the same as terminating.** The state said
"refused" and the graph went right on retrieving.

### Beginner takeaway
> Setting a flag is not the same as changing control flow. If a decision must
> stop a pipeline, something in the *routing* has to read it.

### Learn next
Why the refusal keys on `error_node == "router"` rather than a bare `error`
(several nodes write `error`, and only the router's belongs upstream of dispatch).

---

## Entry 006 — 2026-08-20 — Fixing a contradiction by making the statement false

### What I learned
The most elegant fix in the codebase, and it is not a code trick.

The cross path used to produce answers that contradicted themselves:

> "The retrieved documents do not contain the PAT figure for FY26.
>  ETERNAL's consolidated PAT for FY26 was ₹366 Cr."

### Where it appears
`app/engines/cross_engine.py:131-150`.

### Why it matters
Two earlier attempts tried to **suppress** the first sentence — first by
rewriting the output afterwards, then by adding a prompt instruction telling the
model not to say it. Both failed.

### What confused me
Why would a direct prompt instruction fail? "Do not say the documents lack the
figure" is unambiguous.

### Resolution
Because the sentence was **true**. The semantic engine's context was narrative
chunks, and line items rarely appear in narrative text — so the model was
reporting a correct fact about the evidence it had been handed. The instruction
was asking it to withhold something true, and it lost to
`SYNTHESIS_SYSTEM_PROMPT`'s earlier, more concrete rule: *"say what is and isn't
covered."*

The working fix reordered the engine. Run **quant first**, then inject the
verified figure into the synthesis context as established fact. Now there is
nothing to conceal, because the statement the model would have made is no longer
true.

The comment names the general pattern: *"Neither worked, because the model was
being asked to withhold something TRUE about the evidence it was given. The
working fix is to make it false."*

And a third, structural detail: the injected fact and the appended line come from
**the same formatter** (`_format_quant_response`), deliberately — if they came
from separate code paths they could drift, which is a failure class this project
has already paid for once.

### Beginner takeaway
> If a model keeps saying something you do not want, check whether it is true
> before trying to suppress it. If it is, change the world, not the instruction.
>
> And: within one prompt, an earlier concrete rule beats a later appended one.
> This has cost the project three separate fixes.

### Learn next
`_reconcile_cross`'s four quadrants; why availability must be classified before
contradiction is assessed.

---

## Entry 007 — 2026-08-20 — The same data, shaped by how the caller uses it

### What I learned
`app/metrics/registry.py` holds one tuple of 70 `MetricDefinition`s and exposes
**eleven** accessor/derived-view functions. Two of them are near-opposites, and the difference is
not arbitrary:

```python
def unqueryable_metric_aliases(min_words=4):   # 4-WORD FLOOR
    ...
def metric_anchor_phrases():                    # NO FLOOR, deliberately
    ...
```

### Where it appears
`registry.py:681` and `registry.py:730`.

### Why it matters
Both build a set of phrases from the same registry. But:

- `unqueryable_metric_aliases` is consulted to **find** a phrase in a query. A
  broad set makes the guard **over-fire** and refuse questions it should answer.
  So it needs a floor: short aliases like "cash", "equity", "others", "india"
  would match almost anything.
- `metric_anchor_phrases` is consulted to **find nothing**. A broad set makes
  the guard fire **less**, leaving a query unguarded — which is the prior state
  and recoverable. So breadth is *free safety* here.

The docstring states the rule directly: *"Widen this set freely; never narrow it
without measuring against the full golden set."*

### What confused me
It looked like inconsistency. Two functions over the same data with opposite
strictness felt like one of them was wrong.

### Resolution
The asymmetry is in the **consequences of being wrong**, not in the data. Over-
firing costs a legitimate answer (a new defect). Under-firing costs a guard (the
status quo). When the two errors are not equally bad, the thresholds should not
be equal either.

### Beginner takeaway
> Before tuning a threshold, ask what each *kind* of error costs. Symmetric
> thresholds are only correct when the errors are symmetric.

### Learn next
The 0.5 alias coverage floor in `entity_resolver.resolve_metric` — a third
threshold, calibrated on measured data with an empty band on either side.

---

## Entry 008 — 2026-08-20 — Pure functions as drift insurance

### What I learned
`db_loader.classify_upsert()` is a pure function — no cursor, no I/O, no side
effects — whose only job is to return **what a write would do**:

```python
def classify_upsert(*, existing_doc_id, existing_value, existing_filing_date,
                    record, correct_values=False) -> str:
    # returns "inserted" | "corrected" | "skipped" | "restated" | "reingested"
```

`_upsert_one` calls it and **acts on the label** rather than re-deciding.

### Where it appears
`app/ingestion/db_loader.py:184`.

### Why it matters
Before it existed, `_upsert_one` decided and acted in one pass, so the `--dry-run`
preview in `backfill_financials.py` had to **re-implement the branch order by
hand**. The docstring is worth memorising:

> *"A hand-written mirror is a copy that drifts silently: it agrees on the day
> it is written and diverges at the first change to either side, and the whole
> value of a preview is that it tells the truth about the writer."*

### What confused me
Two SQL constants that are byte-identical apart from three words:

```sql
_SQL_LOCK_LATEST = "SELECT … WHERE … FOR UPDATE"
_SQL_PEEK_LATEST = "SELECT … WHERE …"              -- no FOR UPDATE
```

Why not one constant plus a boolean?

### Resolution
Because the preview must classify **without taking row locks or holding a write
transaction**, and `FOR UPDATE` is not decoration — it changes what the database
does. The comment says they are *"kept beside its locking twin so the two
predicates cannot drift."*

So this file solves the same problem twice, in opposite directions: the
**decision** is shared (one function, two callers), while the **SQL** is
duplicated but co-located. Both choices come from the same fear.

### Beginner takeaway
> A dry run that re-implements the real thing is worse than no dry run — it
> gives you confidence in a copy. Share the decision; if you must duplicate,
> keep the copies adjacent.

### Learn next
`_compute_derived_totals` vs `validate_financial_identities` — two independent
formula copies that `CLAUDE.md` requires be updated together. The unsolved
instance of the same problem.

---

## Entry 009 — 2026-08-20 — A constant is a conclusion; the measurement is the fact

### What I learned
Nearly every magic number in this codebase carries the measurement that produced
it in the comment directly above:

```python
# Measured on ZOMATO FY24 pages 169/170/176/292 (2026-08-01): every
# coincidental match scored <=0.43 coverage, every genuine paraphrase
# >=0.60. 0.5 sits in the empty band with ~0.07 margin either side.
if len(alias_words) / len(normalized_words) < 0.5:
    continue
```

### Where it appears
`entity_resolver.py:233` (0.5 coverage floor), `retriever.py:296-316` (0.70
near-duplicate), `semantic_engine.py:40-49` (Cohere 0.5/0.15),
`financial_extractor.py:459-491` (5% derivation divergence).

### Why it matters
`CLAUDE.md` §1.3 forbids changing any of them without approval, on the grounds
that *"each encodes a measurement that is not derivable from the code."* You
cannot re-derive 0.5 by reading the function. You can only re-measure it.

### What confused me
One constant was **removed** rather than protected: the 0.05 citation relevance
floor.

If the measurement behind it was sound — and the comment says it was, two score
clusters with an empty band between them — why delete it?

### Resolution
This is the sharpest lesson in the repository, and it is not about the constant.

The floor dropped sub-0.05 chunks from `citations` while **leaving them in
`retrieved_chunks`**. So the model still read them; the user just could not see
them. A live answer then stated *"warehousing capacity was 4.8 million square
feet in FY24"* — a real, correctly extracted figure from page 19 at score 0.0165
— and carried **one** citation, to a transcript page containing no such figure.

> *"The number was real, correctly extracted, and UNTRACEABLE."*

The comment states the conclusion exactly: **"THE 0.05 CONSTANT WAS NOT WRONG.
What was wrong is that `retrieved_chunks` and `citations` were allowed to
diverge at all."** The floor did not prevent an unsupported claim; it guaranteed
the claim could not be *checked*.

It also records the rejected alternative and *why*: applying the floor to
`retrieved_chunks` too would close the hole by narrowing what the model reads on
**every** query — altering retrieval to fix an evidence-list problem, with a
blast radius far larger than the defect.

### Beginner takeaway
> A correct measurement can support a wrong decision. Check what the number is
> being *used for*, not just whether it is right.
>
> And the invariant that came out of it: **if the model reads it, the user must
> be able to check it.**

### Learn next
`COHERE_MEDIUM` (0.15) — the refuse-vs-answer boundary that has **never been
exercised by a real query**. It is unvalidated, not validated. Understanding why
that distinction matters is the same skill.

---

## Entry 010 — 2026-08-20 — Deployment constraints are design constraints

### What I learned
Three architectural decisions trace directly to one number: **512 MB of RAM** on
Render's free tier.

1. **Cohere is the primary reranker**, not a nice-to-have. The local ONNX
   cross-encoder costs RAM the tier does not have — the comment literally reads
   *"0MB RAM"*.
2. **Ingestion does not run in the web process.** Loading
   `bge-small-en-v1.5` in-process produced repeated `Exited with status 137`
   (OOM-kill). So upload stores the file and records a `pending_uploads` row; a
   script run elsewhere does the work.
3. **`BATCH_SIZE = 8`**, reduced from 32, which *"caused OOM/near-freeze on
   large docs (1999+ chunks) even at 8GB WSL2 cap."*

### Where it appears
`retriever.py:108`, `api/documents.py:10-19`, `embedder.py:34`.

### Why it matters
A textbook design would put ingestion behind a Celery worker and call it done.
Celery *is* configured here — and it is still not the trigger, because the
constraint is **process RAM, not concurrency**. The comment says so explicitly:
*"Running that step inside the same process that serves live queries is unsafe on
this tier regardless of whether it's triggered via Celery or BackgroundTasks."*

### What confused me
Why keep the local reranker at all if it cannot run on the deploy target?

### Resolution
Because "cannot run alongside a live query service" is not "cannot run". The
fallback exists so that when Cohere's API is unreachable — measured: raw socket
connects to `api.cohere.com:443` succeeded **5 of 8** attempts under WSL2 network
flap — the system degrades to a working reranker instead of failing.

The cost of that choice is the entire two-scale threshold problem in Entry 004.
A degradation path is never free; it buys availability with complexity, and the
complexity has to be *managed*, not merely accepted.

### Beginner takeaway
> "It works on my machine with 16 GB" is not a design. Know your tightest
> resource constraint before choosing an architecture — and when you add a
> fallback, ask what its existence changes about the *meaning* of everything
> downstream.

### Learn next
`docs/RUNBOOK.md`; why `docker compose up -d` returning is not readiness.
