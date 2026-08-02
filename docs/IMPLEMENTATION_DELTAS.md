# LedgerMind — Implementation Deltas

Companion to `ARCHITECTURE.md`, which is preserved verbatim as the original
design record and is NOT edited in place. This file records every point where
the shipped system diverges from that spec.

**Why this file exists.** §17 of the blueprint specified a Groq fallback for
months. It named a model Groq had retired, and no fallback code existed at any
call site. Nothing caught it, because the spec lived outside the repository and
therefore never appeared in a diff. A design document that cannot be reviewed
alongside the code it describes will drift silently. Any change that makes a
blueprint statement untrue must add an entry here in the same commit.

Last verified: 2026-07-30.

---

## A. Corrected — the spec was wrong or has gone stale

### §17 — LLM failover
Blueprint: "Gemini Flash rate-limited → route to Groq llama-3.1-70b."

Two corrections. `llama-3.1-70b` is retired by Groq; the pinned model is
`llama-3.3-70b-versatile` (JSON mode, 128k context, free tier). And the
fallback did not exist in code until 2026-07-29 — `config.py` had a
`groq_api_key` field with zero call sites.

As shipped (`app/llm/client.py`, sole LLM entry point for all three callers):

- Timeouts: structured 8s, text 20s, Groq 20s. A timeout is what converts an
  unbounded hang into a catchable exception; without one, a fallback keyed on
  exceptions can never fire. Motivated by an observed 78s Gemini call that
  returned 200 and looked normal in the audit log.
- Fallback triggers: timeout, transport failure, 429, 5xx.
- Does NOT trigger on auth or invalid-argument errors — verified that an invalid
  `GEMINI_API_KEY` raises 400 INVALID_ARGUMENT and does not fall back, so a bad
  key cannot be masked as a provider outage.
- 429 handling: Google labels the quotaId `...PerDay...` for both the per-minute
  and per-day limit, so only `retryDelay` distinguishes them. <=5s sleeps once
  and retries Gemini; longer falls through to Groq. Both branches verified live.
- Groq has no `response_schema`, only `json_object`. `generate_structured`
  injects the schema into the prompt and validates with Pydantic; an off-schema
  response is treated as a provider failure and never reaches `validate_dsl`.
- `llm_provider` is recorded on `QueryState`, admin-tier only.

**The fallback preserves availability, not behaviour.** Added 2026-07-31 after
Gemini's daily quota was exhausted mid-eval. The router is itself an LLM call,
and Groq classifies differently: TQ006 ("How did Titan's Watches division
perform in Q1FY26?") routed to `quantitative` on every Groq-served run and to
`semantic` on all four historical Gemini runs — confirmed from `audit_log`, not
inferred. So a Groq-served answer is not equivalent to a Gemini-served one for
anything asserting `expected_path`, which is exactly why `eval_runner.py`
withholds the headline score rather than annotating it.

Two further observations from the same outage, both worth acting on:

- When BOTH providers fail, `_generate_semantic_response` returns its
  raw-excerpt floor and the pipeline reports `confidence_tier="high"` with
  `error=None`. The tier is honest about RETRIEVAL, which genuinely was
  high-confidence, but nothing in the state records that no LLM ran. A total
  outage is therefore indistinguishable from a real answer. Fix: set
  `error="synthesis_unavailable"` when the floor fires.
- `llm_provider` is set by whichever call last SUCCEEDED. The router sets it,
  then the floor returns `provider=None`, which never overwrites the stale
  value — so floor responses were logged as `provider="gemini"` or
  `provider="groq"`. Observed 2026-07-31: the gate reported 11/45 non-Gemini
  when the true figure was at least 13. The gate still fired, but it is less
  conservative than it appears.

Stacked free tiers do not compose into reliability. Groq's own daily token
limit (100k TPD) was exhausted within hours of Gemini's, leaving no provider at
all. The failover design is sound and was exercised end-to-end; the ceiling is
the free tier, not the architecture.

### §13 — Confidence thresholds are backend-dependent
Blueprint gives flat cutoffs (HIGH >0.8, LOW <0.5). Reranking runs on two
backends with incompatible score scales: Cohere Rerank returns 0–1, the local
ONNX cross-encoder returns raw logits (~-12 to +2). A single threshold set
silently classified every Cohere-scored query as HIGH.

As shipped: `ChunkResult.reranker_backend` tags each chunk, and
`_score_confidence()` selects LOCAL (-4.5 / -7.5) or COHERE (0.5 / 0.15)
thresholds accordingly. Cohere values reviewed against production reranker
scores 2026-07-29; no evidence of miscalibration, though the 0.15–0.5 band
remains unstressed by any observed query.

### §5 / §8 — Embedding runtime
`sentence-transformers` + `torch` replaced by `fastembed` ONNX
(`BAAI/bge-small-en-v1.5` dense, `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder).
Torch does not fit Render's 512MB tier. Both removed from `requirements.txt`.

Embedding `BATCH_SIZE` is 8, not the blueprint's 32. A 1999-chunk document
OOM-killed repeatedly at 32 even after raising the WSL2 ceiling to 8GB. Batch
size affects throughput only — per-chunk embeddings are mathematically
identical regardless of grouping.

### §5 / §9 — Reranking
Cohere Rerank is the PRIMARY reranker, not the "Phase 7 upgrade" the blueprint
describes. Local ONNX cross-encoder is the automatic fallback. Driven by the
same 512MB constraint. Measured side effect: semantic retrieval runs ~1283ms on
Render vs ~18233ms locally — a 14x gap. Never tune thresholds or judge latency
from local timings.

### §14 — RLS policies: AND is not a short-circuit operator
The blueprint's RLS example is `tenant_id = current_setting('app.tenant_id')::uuid`.
Shipped as written, this broke authentication in production from launch until
2026-07-30: roughly 95% of logins returned 500 whenever any other traffic was
running, while succeeding 10/10 when the service was idle.

Two stacked defects:

1. **Empty GUC, not NULL.** `DATABASE_URL` routes through Supabase's session
   pooler (port 5432),
   so a login gets handed a server connection that previously served a query and
   ran `SET LOCAL app.tenant_id`. The GUC stays defined on that connection and
   reverts to `''` — not NULL — when the transaction ends. `NULL::uuid` is legal;
   `''::uuid` raises 22P02. Idle service means fresh connections, which is why
   every low-traffic test passed.

2. **`AND` does not short-circuit.** The first fix guarded the cast with
   `coalesce(...) <> '' AND tenant_id = ...::uuid`. PostgreSQL does not guarantee
   left-to-right evaluation of boolean conjuncts — the planner orders them by
   estimated cost and may evaluate the cast first. Confirmed still failing with
   pgcode 22P02 after that guard shipped (migration 009).

As shipped: every policy is a single `CASE` expression, `CASE` being the only
construct that guarantees an untaken branch is never evaluated. `users`
(migration 010) yields true on empty context — the login-by-email bootstrap.
All other tables (migration 011) yield **false**: fail closed, since an empty
tenant context must return zero rows, not all rows.

Diagnosis note worth keeping: this took three attempts because Render's log
stream truncates multi-line tracebacks, hiding the exception type. It was
settled by logging `pgcode` on a single line in `auth/service.py`, which also
now returns 503 rather than 500 for transient DB failures.

### §5 / §14 — Per-tenant rate limiting — NOT BUILT
The blueprint specifies a Redis token bucket per API key at the FastAPI layer.
Verified 2026-07-30 by grep: no limiter exists anywhere in `app/`. Nothing
throttles per-tenant request volume, and no endpoint returns 429.

### §9 — DISABLE_LOCAL_RERANKER removed
An env flag added during the 512MB OOM crisis returned `hybrid_search`
candidates directly, skipping reranking. Those chunks carry
`reranker_score=float("-inf")` and `reranker_backend="none"`, so
`_score_confidence()` classified **every** semantic query as LOW and refused —
after burning all CRAG rungs on identical retrievals first. Never enabled in
production (the sweep returns `confidence=high` throughout, which is impossible
if it were set), so this was a loaded footgun rather than a live defect.

Removed rather than fixed: RRF scores (~0.016 at rank 1, k=60) are a third
incompatible scale needing their own calibrated thresholds — the exact mismatch
class that produced the Cohere-vs-local confidence bug. Cohere primary plus
local ONNX fallback already covers the RAM constraint. `_score_confidence()`
now logs an error if unscored chunks ever reach it, rather than reporting a
false refusal.

### §22 — Audit log path values
The blueprint writes `query_path: "cross_examination"`. The code uses `"cross"`
everywhere (Pydantic Literal, `route_after_router()`). The DB CHECK constraint
is `('semantic','quantitative','cross','blocked','unknown')` — matched to the
code rather than widening it to admit both spellings, which would have made two
names for one concept permanent schema drift. `'unknown'` is included because
`audit_writer_node` can emit it on a pre-router exception.

**Amended 2026-07-30.** The paragraph above described `init.sql`, not every
live database. The local Postgres volume predated that edit and still carried
the original four-value constraint admitting `cross_examination`. Because
`CREATE TABLE IF NOT EXISTS` makes re-running `init.sql` against an existing
volume a no-op, nothing ever corrected it. Every `path="cross"` query had its
audit row rejected for the lifetime of that database: 2646 rows, zero cross,
across the entire history of the table. `audit_writer` logged
`Audit log write FAILED (response still delivered)` at ERROR and returned the
answer anyway — correct for the user, and not silent, but never grepped for.
Repaired by migration 013. Supabase was verified as already correct, so this
affected local only.

Two things worth keeping. First, a delta recorded as "corrected" is a claim
about the code, not about every deployed schema; the two can diverge without
any diff showing it, which is the same failure mode this whole file exists to
prevent. Second, `check_migrations.py` structurally cannot catch this class —
it diffs migration filenames against `schema_migrations`, not schema contents
against the code that depends on them.

### §9 — Near-duplicate suppression after reranking
The blueprint's retrieval pipeline is top-20 hybrid → cross-encoder rerank →
top-5 to the LLM, with no diversity step. That assumes the 20 candidates are 20
distinct passages. They are not: `chunker.py` uses `OVERLAP_TOKENS=150`, so
adjacent chunks share roughly 150 tokens by design, and both windows over the
same text are independently retrievable points.

Measured live 2026-07-30 on an ETERNAL FY26 cross query: chunks `0b035c3c…` and
`387d1a8c…` were both page 23, both exactly 705 characters, offset by about 90
characters, 87.8% token overlap. They occupied 2 of the 5 slots with identical
forward-looking-statements boilerplate while the management-commentary chunk
that actually addressed the question sat at rank 2. The first run after the fix
suppressed **9 of 20** candidates, overlaps ranging 72.5%–90.3%, including one
cross-page catch (page 36 against page 25, a repeated auditor letterhead).

The overlap is not the bug and must stay. `OVERLAP_TOKENS` was raised from 50 to
150 specifically to fix a mid-sentence split that orphaned Paytm's PPBL
impairment fact in an unretrieved chunk. The bug is that two windows over the
same text could both survive to the final cut. So the fix sits in
`retriever.py`, after rerank and before the top-k slice, not in the chunker.

As shipped: `_deduplicate_near_identical()` compares token-set containment
(`|A∩B| / min(|A|,|B|)`, so a short chunk fully inside a longer one scores 1.0)
against a 0.70 threshold, keeping the higher-scored member of each pair. Token
sets rather than embedding cosine — the text is already in hand, and a second
model invocation to answer what a set intersection answers is cost for nothing.
Cohere is now asked to score the full candidate set rather than `top_n=5`; it
bills per search rather than per document, so this is free, and without it
dropping a duplicate left four chunks instead of promoting the sixth-best.

The threshold is calibrated on one measured distribution and every suppression
logs its real ratio, so the eval sweep accumulates evidence. Note the asymmetry:
those logs show only what was dropped. False positives live on the kept side and
would surface as a changed answer, not a log line. 83/83 held after the change,
which is evidence rather than proof.

Surfaced here, closed 2026-08-02 — see the citation relevance floor below.

### §9 — Citation relevance floor
With duplicates cleared, the trailing slots held chunks scoring 0.026 and 0.014:
noise presented to the user as numbered evidence. A citation is a CLAIM that a
passage supports the answer, so rendering a 0.02 chunk with the same visual
weight as a 1.00 match asserts support the score says is absent — a Zero
UI-Hallucination Mandate violation on the evidence list itself.

Measured 2026-08-02 across four live `semantic_risk` queries, 20 real citations.
Sorted, the scores fall into two clusters with nothing between them: noise at
0.0027–0.0290 (seven values) and genuine matches at 0.0883–0.9996 (thirteen).
`CITATION_RELEVANCE_FLOOR = 0.05` sits in a roughly threefold empty band, so it
is not a tuned constant — anything in 0.03–0.08 gives identical results on this
evidence. Consistent with the 2026-08-01 Cohere dump, where no 'poor' query
exceeded 0.0323.

Three properties are deliberate and load-bearing:

- **Display layer only.** The filter lives in `_build_citations()`, the single
  construction point, and does not touch `retrieved_chunks`. A weak chunk in the
  model's context is harmless and occasionally useful; the defect is presenting
  it as evidence. Filtering retrieval instead would change what the model sees on
  every semantic and cross query, risking the eval baseline for no gain.
- **Cannot move confidence.** `_score_confidence()` reads `chunks[0]` and
  `chunks[-1]` and runs BEFORE `_build_citations()` at every call site, so the
  tier cannot shift as a side effect of a display filter. Verified live: counts
  went 5→2, 5→5, 5→3, 5→3 across the four queries with every tier unchanged. If
  that call ordering ever changes, this guarantee changes with it.
- **Cohere scale only.** Local ONNX returns raw logits (thresholds -4.5/-7.5)
  where 0.05 sits above nearly every legitimate score and would drop everything.
  One threshold across two scales is the §13 bug rebuilt. No logit-scale floor
  exists because none has been measured, and inventing one for symmetry is the
  unmeasured-constant habit this project has already paid for.

It can never return an empty list: if every chunk falls below the floor the
top-scoring one is kept, since an answer with zero citations violates Principle 2
outright and one weak citation beats none.

The same asymmetry as near-duplicate suppression applies. The logs show only what
was dropped; a false positive — a genuinely relevant chunk scoring below 0.05 —
would surface as a missing citation, not a log line. Four queries found none,
which is evidence rather than proof. A suspiciously short citation list is the
tell, and this threshold is the first thing to check.

Incidental observation, not acted on: all seven chunks dropped across the three
affected queries came from Paytm pages 5–6, the same two pages recurring in every
query. Consistent with cover/boilerplate matter that retrieves on frame-matching
and scores near zero on subject-matching. Whether such pages should reach the
candidate pool at all is a chunker/classifier question, not a retrieval one; the
naive fix (filter by page number) is exactly the per-document hack this project
rejects.

### §10 — Stage 0 guards: refusing beats substituting
Blueprint §10's DSL self-healing loop assumes an invalid metric produces an
invalid DSL that the validator rejects. It does not. Constrained to emit some
metric string from the AVAILABLE list, the model substitutes the nearest
plausible neighbour, and the result is perfectly valid DSL — the validator has
nothing to catch. Nothing downstream records what the user actually asked, so
the only place that intent survives is the raw query text.

Two guards therefore run BEFORE any DSL is generated, both reading the raw
query against the shared registry:

- **Stage 0 — derived metrics** (`metric_type="derived"`). Observed live: "What
  was Paytm's EBITDA for FY26?" returned `total_expenses` ₹8,523 Cr with
  `sql_verified=True`.
- **Stage 0b — registered but not DSL-queryable** (`dsl_enabled=False`), added
  2026-07-31. Observed live: "...the 207 crore impairment of loans and
  investments in associates recorded in FY26?" returned
  `metric="exceptional_items"` and appended a ticked `₹-186 Cr` for a metric
  nobody asked about.
- **Stage 0c — no metric named at all**, added 2026-08-02. Lives in
  `cross_engine.py`, not `quant_engine.py`. `GeminiDSLResponse.metric` is a
  REQUIRED field, so a cross-routed query naming no metric cannot return "none"
  — the model manufactures one, which compiles, executes, and is stamped
  `sql_verified=True`. Observed live across five consecutive runs on
  gemini-3.1-flash-lite: PQ012, "Does Paytm have any financial exposure to Paytm
  Payments Bank following the license cancellation?", produced
  `metric="exceptional_items"` and appended a ticked `₹-186 Cr`. Stable
  classification, not non-determinism.

Stage 0b matches canonical names with underscores expanded, not only alias
tuples — load-bearing, since every stored alias for that metric uses a slash
("loans/investment") while both the query and the canonical name use "and".
A four-word floor is the false-positive guard: `dsl_enabled=False` covers
aliases like "cash", "equity", "others" and "india", and scanning those would
fire on nearly any query. 44 phrases survive the floor, all unambiguous
financial-statement language. It fails toward NOT firing.

Stage 0c inverts Stage 0b's polarity, and that is why it has NO word floor.
Stage 0b fires when it FINDS a phrase, so breadth makes it over-fire and it needs
`UNQUERYABLE_MIN_WORDS`. Stage 0c is consulted to find NOTHING, so breadth makes
it fire LESS: short aliases like "cash" and "india" are free safety rather than a
hazard. Failing to anchor leaves a query unguarded (the prior state, recoverable);
anchoring too eagerly would suppress a figure someone legitimately asked for (a
new defect). Its phrase set therefore unions aliases, underscore-expanded
canonical names, and `prompt_aliases` — the last being load-bearing, since
"delivery charges" and "employee benefits" exist only there, and a first pass
reading alias tuples alone left four quantitative golden questions unanchored.
Verified against all 84 golden questions: 28 have no anchor, every one of them
adversarial (blocked pre-router) or `semantic_*` (never invokes the quant half).

Stage 0c is scoped to the cross path BY PLACEMENT, not by a runtime conditional.
On `path=quantitative` the router has already asserted the user wants a number,
and refusing there would risk legitimate queries phrased outside registry
vocabulary. On cross the quant half is an adjunct to a qualitative answer, so
skipping it degrades to qualitative-only — a case `_reconcile_cross` already
handles as Quadrant 3. Living in `cross_engine.py` means Path 2 is untouched by
construction rather than by a check someone could later move. It deliberately
does NOT set `dsl_object`: leaving it None is what tells reconciliation this
query never asked for a figure, so `CROSS_NO_VERIFIED_FIGURE_NOTE` — which
asserts a metric was identified — stays correctly silent. No change to
`response_generator.py` was needed.

Note the router was NOT changed. Its cross rule ("verify or compare qualitative
claims against financial numbers") matches "financial exposure" defensibly, and
re-tuning a classification prompt used by every query to clear one test is the
prompt-versus-concrete-rule fight already lost twice here. With the guard in
place, routing cross costs nothing. PQ012's golden entry stays red on
`expected_path` as the single artifact recording that classification is imprecise
on this phrasing; it carries a `known_deliberate_failure` field saying so.

Stage 0b records a partial `dsl_object` before returning, so the cross path's
reconciliation can tell "a metric was identified but produced no verified
figure" from "no figure was ever requested" — without it, a refused query looks
identical to a purely qualitative one and the user is never told the system
declined to verify the figure they named.

### Cleanup lags correction — fixing a rule does not repair what it wrote
Three separate instances surfaced on 2026-07-30/31, and the pattern is worth
naming because none of them were caught by any existing check.

- The `audit_log` CHECK constraint admitted `cross_examination` while the code
  emitted `cross`. `init.sql` had already been corrected; one live database
  predated the edit, and `CREATE TABLE IF NOT EXISTS` makes re-running it a
  no-op. Every cross audit row was rejected for that database's lifetime.
- `META_RE` matched only round brackets until 2026-07-11, so a Paytm line item
  reading `[refer note 4]` kept its footnote suffix and was stored under the
  key `provision_for_impainnent_ofloans/investments_in_subsidiary/associate_[refer_note_4]`.
  The pattern was later widened to accept either bracket style; the two rows
  written under the old one survived until deleted by hand on 2026-07-31. The
  values were correct duplicates of correctly-named rows, so nothing was lost.
- Q052 and Q048 both required golden-dataset corrections after the code they
  asserted against changed meaning.

Nothing in the test suite compares LIVE DATA against CURRENT CODE.
`regression_check.py` re-runs extraction on source PDFs and would pass with
corrupt rows sitting in the database; `check_migrations.py` diffs migration
filenames against `schema_migrations` and cannot see a constraint definition at
all. Both are correct at what they do. The gap is real and currently unowned;
recorded here rather than papered over with a new component.

---

## B. Unbuilt — specified, not implemented

### §15 — Redis semantic cache — NOT BUILT
No cache module exists. Redis is present only as the Celery broker, a health
check, and a config URL. There is no cache key construction, no query embedding
at request time, and no >0.95 similarity match.

Consequence: `QueryState.cache_hit` is initialised `False` and never written.
`app/api/metrics.py` aggregates that column into `cache_hit_rate_pct`, which is
therefore structurally incapable of returning anything but 0.0. That is not "no
hits yet" — it is a metric with no producer. Either the field is removed from
the metrics response or the cache is built; a permanently-zero rate presented as
a measurement is the fabricated-trust-signal failure the frontend design system
explicitly prohibits.

Not currently a correctness risk: every answer is computed fresh, so no stale or
cross-tenant result can be served. The blueprint's Trap 5 (cross-tenant cache
collision) is unreachable because there is no cache.

### §9 — Parent-child chunking — NOT BUILT
No `parent_chunk_id` / `is_child` anywhere in the codebase, including the Qdrant
payload schema that §19 lists them in. Retrieval is single-granularity: the
chunk that matches is the chunk sent to the LLM.

Related open retrieval-quality issue: Cohere has been observed promoting a
balance-sheet chunk above on-topic narrative chunks for a risk-factors query. A
`_prefer_narrative` partition was built and reverted (61731ce, 7f34ab5) on a
causation theory later proven wrong. The underlying ranking problem is real and
unowned. Candidate design if revisited: ratio-based gating, measured against the
full golden set rather than one query's citation list.

Partially explained 2026-07-30. Some of what looked like bad ranking was
duplicate chunks consuming slots — see the near-duplicate entry in section A,
where 9 of 20 candidates for one query were repeats. That is now suppressed. It
does not account for all of it: a cover-letter chunk still outranked the only
relevant commentary chunk 0.584 to 0.245 on the same query, which is genuinely a
ranking problem and remains unowned. Worth separating the two before any further
attempt — the previous reverts conflated them.

### §21 — RAGAS — NOT USED
Replaced by `scripts/eval_runner.py`, an 83-question golden-dataset runner
asserting exact figures, refusal behaviour, routing path, and keyword presence.
RAGAS scores faithfulness on a 0–1 scale; this system's core claim is that
numerical answers are exactly right, which is a pass/fail property. The runner
also gates on `llm_provider` and withholds the headline score entirely if any
answer was Groq-served, since a score printed under a caveat still ends up in a
README.

Retrieval metrics (Recall@5, MRR, NDCG) are not computed. The golden datasets
assert answer correctness, not chunk-level ranking quality.

---

## C. Superseded — spec deliberately overridden

### §5 / §6 / §26 — Frontend is Next.js, not Streamlit
§26 explicitly lists "React/Next.js frontend" as out of scope. Overridden.
Streamlit cannot express the working-paper document model the interface is built
around, and cannot deploy to Vercel at all (stateful long-running server vs
serverless-only platform). The Streamlit draft is preserved at
`streamlit_frontend_archive/`.

### §16 / §23 — Ingestion is manual, not event-driven on upload
The blueprint fires `DocumentUploaded → ingestion worker`. As shipped, the
upload endpoint runs the pre-ingestion gate, stores the PDF in Supabase Storage,
and inserts a `pending_uploads` row. It does NOT trigger ingestion. A developer
runs `scripts/process_pending_uploads.py` locally to process the queue.

Justification: running embedding in-process on Render's 512MB web service caused
repeatable OOM kills (status 137, always immediately after the dense model
load), which would take down live query serving for all tenants during any
upload. A dedicated Render Background Worker has no free tier (~$7/mo). Manual
processing keeps the $0 cost baseline and matches how ingestion has always
worked on this project. The UI states that an uploaded document is not queryable
until processed.

### §12 / §7 — Cross-examination path is BUILT but UNMEASURED
`cross_engine.py` and `contradiction.py` exist and `router.py` routes to
`path="cross"`. Zero golden questions target it — verified by grepping
`expected_path` across all three datasets. The 83/83 figure therefore says
nothing about this path, and the LLM-client refactor touched
`response_generator`'s cross branch without any test exercising it.

This is the weakest claim in the system: an untested path in a platform whose
premise is verifiability. Closing it needs golden questions, not code.

Still true as of 2026-07-30, though the cross branch has since been rewritten
(see Trap 7 in section C) and verified by hand across repeated runs. Golden
questions were deliberately not written first: asserting against the old output
would have baked in behaviour known to be wrong. A useful set needs one question
per availability quadrant plus at least one where a real contradiction exists —
otherwise it only asserts that nothing fires.

### Trap 7 / §12 — Tolerance was necessary but never sufficient
The blueprint's Trap 7 prescribes a tolerance threshold so that "approximately
₹12,000 crore" in narrative text is not flagged as contradicting ₹12,114 crore
from SQL. That fix is correct and shipped. It is also incomplete, in a way two
separate production bugs demonstrated.

Tolerance answers *do the two halves disagree about a value?* That question only
has meaning once *does each half have anything at all?* has resolved. Asking
them in the wrong order produced both known failures: eleven false
`severity: high` magnitude flags on a query where the top-cited chunk was the
same cash-flow statement the SQL row came from, and a cross response whose
semantic half stated the documents did not contain the company's FY26 PAT
immediately above a quant template reporting ₹366 Cr.

The second was not a contradiction at all. The semantic engine's scope is the
top-k narrative chunks; when it reported no PAT figure there, that was a true
and unremarkable scoped negative — line items rarely appear in narrative prose.
Concatenating it with an SQL-verified figure promoted a scoped negative into a
global claim.

As shipped, two changes. `cross_engine` now runs the quant side **first**, and
`response_generator` passes the verified figure into the synthesis call as
established fact, so the model writes one coherent answer with nothing left to
contradict. Safe to reorder: `quant_engine` reads only DSL-relevant state and
never touches `retrieved_chunks`; the engines were always independent and only
the assembly was coupled. Separately, `_reconcile_cross()` classifies evidence
availability across four quadrants before contradiction detection runs.

Two earlier attempts failed and are recorded because the reason generalises.
Both tried to *suppress* the sentence — first by rewriting it post-hoc, then by
instructing the model not to say it. Neither worked, because the model was being
asked to withhold something true about the evidence it had been given. The
working fix made the statement false instead, by supplying the evidence. The
prompt-instruction attempt failed the same way the EBITDA silent substitution
did: an appended instruction lost to an earlier, more concrete rule in the same
prompt.

Accepted tradeoff. With the figure in context, the model restates it in prose in
roughly two runs out of three, so the numeral can appear twice. The deterministic
appended line is kept anyway. Dropping it would make an LLM-transcribed figure
the only one present in the response, trading a cosmetic redundancy for a
transcription risk on an SQL-verified number in a financial tool.

Quadrant 4 (both halves empty) was verified live and closed a latent bug of its
own: `semantic_engine`'s hard refusal empties `retrieved_chunks`, and the
resulting "No relevant information was found in the available documents" floor
string matches none of `REFUSAL_PATTERNS`, so it previously escaped post-
generation detection entirely. The generic detector is now scoped to
`path="semantic"`; on `cross` it had been capping SQL-verified answers to
`tier=low` with `error="low_confidence_refusal"`, which the frontend renders as
`MetricCallout status="refused"` beside a ticked, correct figure.

### §1 / §21 — Corpus
Shipped corpus is Eternal (formerly Zomato), Titan, and Paytm — not the ten
companies §1 lists. 84 golden questions across three datasets (Eternal 52,
Titan 14, Paytm 18), above the blueprint's 50.

Baseline as of 2026-08-02, provider-clean and model-clean on
gemini-3.1-flash-lite: **83/84** — Eternal 52/52, Titan 14/14, Paytm 17/18.
Do not read the 83 as the question count; the two numbers coinciding is an
accident of one open failure. The single failure is PQ012, which asserts
expected_path="semantic" while the current model routes it to "cross"; the
cross path then appends an unrelated SQL-verified figure ("Exceptional Items
for FY26 was ₹-186 Cr") to a question about PPBL exposure. Left failing
deliberately — editing the assertion to match observed behaviour would hide a
real substitution bug. Tracked as the lead backlog item.

The earlier 84/84 is VOID and must not be quoted: it predates the five
extraction fixes and the token-set coverage floor of 2026-08-01, which changed
metric resolution corpus-wide.

### §11 — Truth Resolution: SPEC HONORED
Recorded here because it is frequently assumed unbuilt. `db_loader.py`
implements the Trap 3 fix as written: `SELECT ... FOR UPDATE` locks the existing
`is_latest=TRUE` row for the business key before any flip, retirement and insert
happen in one transaction, and the same-filing-date re-ingestion case is handled
separately from the newer-filing restatement case.

Caveat on scope: the race the lock defends against requires concurrent
ingestion, and ingestion is currently a single manual CLI run. The mitigation is
correct and untriggered.
