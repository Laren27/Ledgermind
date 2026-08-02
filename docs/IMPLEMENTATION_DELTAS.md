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

Last verified: 2026-08-02.

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

**Measured properly 2026-08-01** (`scripts/cohere_score_dump.py`, 10 queries,
raw JSON under `docs/measurements/`). The prior belief that Cohere scores were
"flat" was an artefact of only ever inspecting the post-dedup top-5 of
well-retrieving queries. Across full candidate sets the separation is two orders
of magnitude with ZERO overlap: good queries top out at 0.329–0.999, poor ones
at 0.003–0.032. `COHERE_HIGH=0.5` was read as correct and unmovable — no poor
query cleared it, the highest reaching 0.0323, fifteen times below.

**That margin is SUPERSEDED as of 2026-08-02** — and the supersession is
ITSELF CORRECTED 2026-08-03; read the correction block below before quoting
any number in the next two paragraphs. A second measurement (17
queries, `docs/measurements/cohere_band_stress_2026-08-02.json`) found a poor
query at **0.4834** — "How does Eternal's board compose its audit committee?",
a topic this corpus does not contain. `poor` now spans 0.0012–0.4834. The
margin under `COHERE_HIGH=0.5` is therefore **0.017, not fifteenfold**.

The threshold is not moved on this: one query at 0.4834 against nine below
0.03 is an outlier, not a distribution, and raising `COHERE_HIGH` would change
confidence on every semantic query in the corpus. But the recorded confidence
in that threshold was overstated and the number should not be quoted as
fifteenfold again. A third measurement targeting the 0.03–0.5 region
specifically is the way to settle it.

Also measured and read at the time as DISCONFIRMED in the same run — DOWNGRADED
TO UNTESTED 2026-08-03, see the correction block below: the hypothesis that cross-style
framing ("does X align with its financial exposure to Y") lifts scores by
matching the financial-statement frame rather than the subject. Seven such
queries on absent topics scored 0.0002–0.0725, and the three controlled pairs —
same topic, bare versus cross-framed — went the WRONG way (audit committee
0.4834 bare against 0.0725 cross-framed). Framing is not the separating
variable. Recorded so it is not re-attempted. A single
0.584 cover-letter datapoint had suggested the opposite; ten points overruled
it. Note the median candidate on a GOOD query still scores ~0.006–0.08: Cohere
pushes nearly everything to the floor and lifts only real matches, which is what
makes the citation floor in section A safe at 0.05.

**CORRECTION 2026-08-03 — the 0.4834 query was MISLABELLED.** "How does
Eternal's board compose its audit committee?" was recorded above as a `poor`
query on "a topic this corpus does not contain". That is wrong. The topic IS in
the corpus. Its three top-ranked chunks are all page 135, FY24 — the ZOMATO
FY24 annual report, whose statutory corporate-governance section the quarterly
filings do not carry — and the stored previews contain the disclosure verbatim:
"Brief terms of reference, composition of these committees", "I. Audit
Committee", "governance policies". 0.4834 is Cohere scoring genuinely relevant
retrieved text. It is a correct score on a present topic, not an absent-query
outlier. Found by reading the committed JSON, not by re-running anything.

Three consequences, all of which narrow what the earlier paragraphs claimed:

1. THE RECORDED ABSENT MAXIMUM FALLS TO 0.2195. With the audit-committee query
   removed from the absent population, the highest score any genuinely absent
   query has produced across both runs is 0.2195 — "How does Titan's board
   evaluate the performance of its independent directors?"
   (`cohere_specificity_2026-08-03.json`, label `absent`). That one was checked
   the same way rather than assumed: its top chunk is the AUDITOR'S LETTERHEAD
   ADDRESS BLOCK (BS R & Co. LLP, Embassy Golf Links, page 11, TABLE), which is
   unrelated to the question. It is a genuine absent-query score.

   The `poor` span of 0.0012–0.4834 above therefore does not describe absent
   queries. The remaining band_stress `poor` entries top out at 0.0288 and the
   seven `absent_cross` at 0.0725.

2. THE FRAMING HYPOTHESIS IS UNTESTED, NOT DISCONFIRMED. The disconfirmation
   rested on three controlled pairs, and the only pair that diverged was the
   audit-committee pair — 0.4834 bare against 0.0725 cross-framed. If the bare
   query was scoring real governance text while the cross-framed one was not,
   the two halves were never asking about the same available content, and the
   pair cannot separate framing from topic presence. The other two pairs (gold
   hedging 0.0012/0.0002, data privacy 0.0144/0.0102) did not diverge, which is
   consistent with the hypothesis being wrong but equally consistent with both
   halves simply being absent. Downgraded from DISCONFIRMED to UNTESTED. It may
   still be false; this run does not establish it either way.

3. `COHERE_MEDIUM=0.15` IS NOW MEASURED TO ADMIT A GENUINELY ABSENT QUERY. At
   0.2195, the Titan board-evaluation query clears 0.15 — scoring an auditor's
   address block. The 0.15–0.5 band is no longer unstressed: it has one
   genuinely absent occupant. Under `COHERE_HIGH=0.5` the margin against a
   genuine absent query is 0.2805 rather than the 0.017 stated above, since
   that 0.017 was computed from the mislabelled query.

**NO THRESHOLD WAS MOVED.** Not `COHERE_HIGH` (0.5), not `COHERE_MEDIUM`
(0.15), not the citation floor (0.05). This entry corrects the record only.
One absent query at 0.2195 is a single datapoint, and §1 requires proposing and
stopping on any of these constants. What it does establish is that the band
between MEDIUM and HIGH is reachable by a no-answer query, which the earlier
text said was unobserved.

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

### §8 — A guard that measures the raw label rejects real line items
`_should_skip_row()` drops rows whose description exceeds 80 characters or 12
words, on the reasoning that real P&L/BS line items are short LABELS and
anything longer is narrative prose that happened to parse as a table row. That
reasoning is sound. The implementation measured the RAW description while
`resolve_metric()` measured the NORMALISED one — two different strings, and the
guard ran first.

Bracketed note references, footnote markers and roman-numeral prefixes are not
part of a label, so counting them rejected genuine data:

- PAYTM standalone `Provision for impainnent ofloans/investments in
  subsidiary/associate [refer note 4]` — 83 raw characters, 69 normalised.
  Carries FY26 439.0 and FY25 37.0, resolves correctly to
  `impairment_of_loans_and_investments_in_associates`, and was dropped from
  every ingest after the guard landed 2026-07-31, while its 59-character
  CONSOLIDATED twin (207.0 / 30.0) passed. The standalone/consolidated
  asymmetry is what made it visible.
- TITAN `V. Profit before share of profit of an associate and tax (III -JV)` —
  13 raw words, 11 normalised. A real line item, the step between operating
  profit and PBT for a company with associate holdings.
- 69 further PAYTM cash-flow and OCI rows that had never entered the database
  at all: opening/closing cash balances, net cash from operating/investing/
  financing, FVTOCI fair-value changes. Internally consistent on inspection
  (FY25 closing 2072 = FY26 opening 2072).

Fixed by measuring the normalised label for the LENGTH tests only. The
remaining checks stay on `desc_lower` — their behaviour under normalisation has
not been measured, and this was one demonstrated defect, not a rewrite. Prose
is unaffected because it has no bracketed metadata to strip: the two known
cases measure 104->103 and 93->93 characters, both still caught.

HOW IT WAS FOUND, which is the part worth keeping. Adding PAYTM to
`regression_check.DOCUMENTS` extended `purge_orphaned_metrics.py` to its 395
previously-unevaluated rows. The first dry run flagged 46 candidates, and two
of them carried the standalone impairment figures with NO current-code
replacement anywhere. A blanket `--apply` would have deleted verified data and
nothing would have surfaced it until a standalone query returned empty. The
"verify raw/canonical pairs at identical values before deleting" rule from the
178-row purge is what stopped it.

Backfilled with `scripts/backfill_financials.py` (new): Stage 7 only, doc_ids
READ from the documents table rather than minted. A full pipeline run would
have re-chunked, re-embedded and re-upserted Qdrant, all producing byte-
identical output for an unchanged document — pure risk, and rewriting Qdrant
payloads is exactly what produced the PAYTM FY99 drift. `pipeline.py` has
`--skip-financials` for stages 1-6; there was no inverse.

Safe because `db_loader._SQL_LOCK_LATEST` matches on the BUSINESS KEY
(company, metric, fiscal_year, financial_type, quarter), not `doc_id`. PAYTM
395 -> 464 rows (69 inserted, 351 skipped as already-present, 0 errors), TITAN
+4. The purge then ran clean at 44, every candidate verified as either paired
to a replacement at identical values (`operating_profit` ->
`operating_profit/(loss)_before_working_capital_changes`, `equity` ->
`paid_up_equity_share_capital`, `payables` -> `increase/(_decrease)_in_trade_
payables`, and so on) or a component summing into a preserved total
(`deferred_tax` + `current_tax` + `adjustment_of_tax_relating_to_earlier_years`
= `tax_expense`, verified arithmetically for all four periods). 464 -> 420,
second run idempotent, every golden value unchanged.

TAX COMPOSITION EVALUABILITY, measured 2026-08-03 against live `is_latest` rows.
The premise that this identity "reconciles on every PAYTM period" is FALSE as a
statement about the data: 2 of 10 period groups carry all four rows. The formula
is not what is in question — both complete groups reconcile exactly (18+10+2=30,
6+(-3)+2=5, diff 0.00) — what is missing is components, not agreement.

Zero-coalescing those absences would have manufactured a FALSE FAILURE. FY25
annual consolidated holds `current_tax` 20.00 against `tax_expense` 18.00 with
`deferred_tax` ABSENT, which `.get(m, 0)` renders as an 11.11% identity breach
when the true statement is that a component was never extracted. That is why
`validate_financial_identities` check 4 asserts only on complete groups and
routes the rest to `[IDENTITY NOT EVALUATED]` — the same third outcome
`purge_orphaned_metrics` gives period groups no source document produces.

```
fy     q    type          current_tax deferred_tax  adjustment tax_expense  evaluable?
--------------------------------------------------------------------------------------
FY25   Q4   consolidated        1.00       2.00     ABSENT       3.00  NO   missing: adjustment_of_tax_relating_to_earlier_years
FY25   Q4   standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY25   —    consolidated       20.00     ABSENT     ABSENT      18.00  NO   missing: deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY25   —    standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY26   Q3   consolidated        6.00      -3.00       2.00       5.00  YES  sum=5.00 vs 5.00 diff=0.00
FY26   Q3   standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY26   Q4   consolidated       -2.00      13.00     ABSENT      11.00  NO   missing: adjustment_of_tax_relating_to_earlier_years
FY26   Q4   standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY26   —    consolidated       18.00      10.00       2.00      30.00  YES  sum=30.00 vs 30.00 diff=0.00
FY26   —    standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years

groups total: 10   fully evaluable: 2   not evaluable: 8
```

NOT RECONCILED with the "verified arithmetically for all four periods" claim
directly above. That verification ran at purge time against the 464-row table;
this measurement is against the 420-row post-purge state, and which four periods
were checked then was not recorded. The two are therefore not directly
comparable and the earlier claim is neither confirmed nor retracted here.
Recorded as an open discrepancy — whether the purge removed component rows that
would otherwise make more groups evaluable is unmeasured, and worth one dry run
to settle before the next extraction change.

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
`path="cross"`. No golden question ASSERTS `expected_path="cross"` — verified by
grepping across all three datasets — so the path is exercised incidentally
rather than deliberately.

**Superseded 2026-08-02.** The heading is now wrong in both directions and is
kept only so the correction has something to attach to. The path IS measured:
three golden questions assert `expected_path="cross"` and all three pass
provider-clean.

- **PQ018** — predates today and was missed when this section was first written.
  Asserts note 4's reconciliation with four substantive keywords (`ppbl`, `190`,
  `optionally convertible debentures`, `march 31, 2024`) at medium confidence.
- **Q053** — Quadrant 1, RICH-NARRATIVE. Management commentary on quick commerce
  against SQL revenue 54,364. Five citations at 0.95-0.99.
- **Q054** — Quadrant 1, EVIDENCE-THIN. The narrative genuinely does not explain
  the D&A rise, so the qualitative half returns an accurate scoped negative and
  the verified 1,597 Cr is appended beneath it — the exact juxtaposition of the
  cross self-contradiction bug fixed on 2026-07-31.

All three assert `expected_contradictions: 0`, which against this corpus is the
strongest available assertion: it guards the failure that actually occurred
(eleven false `severity: high` flags on a query whose top-cited chunk was the
same cash-flow statement the SQL value came from), and nothing else in the
golden set asserts that a non-contradiction stays unflagged.

**Three quadrants remain unassertable, each for a measured reason, and none is
closable by writing a cleverer question.**

*A genuine contradiction does not exist in this corpus.* Three zero-quota
retrieval probes looked for one. Every profitability-framed query returns
financial statements — cash flow, auditor's report, results statement, balance
sheet — because in a results filing that is where profit lives. The narrative
discusses NOV, order mix, store counts and category growth. The two halves
address different subjects, so there is nothing to disagree about. Closing this
needs a DOCUMENT containing a real disagreement (an earnings-call transcript, an
investor presentation making directional claims), not another question. A
manufactured contradiction would train the system to fire on approximation,
which is Trap 7 inverted and worse than no test at all.

*Quadrant 2 (qual refused + quant verified) is EXERCISED as of 2026-08-02* by
**PQ019** — "Is Paytm's stated workforce strategy consistent with its financial
exposure to attrition?" Three consecutive gemini-3.1-flash-lite runs plus a
scoped eval sweep: `path=cross`, tier=medium, `sql_verified=false`,
`dsl_object=None`, one citation, every run opening with a scoped negative about
the absent workforce strategy. It carries NO keywords by design — a scoped
negative has no stable vocabulary, and asserting on refusal phrasing is the
brittleness the golden keyword rule warns against. Note the model does transcribe
an employee benefits expense figure from a retrieved chunk as adjacent context;
unticked and `sql_verified=false`, so not a defect, but if a future change makes
that number appear verified the entry should start failing. The paragraph below
records the three attempts that preceded it and why they failed, which is still
the useful part.

Q054 was authored
expecting it and landed in Quadrant 1 instead, at tier=high. `_is_refusal_text`
correctly declined to call the answer a refusal: the scoped negative is followed
by roughly 380 characters of substantive content, well past the 120-character
tail guard. That is the right call — the answer IS substantive. Reaching
Quadrant 2 needs a metric the retrieved chunks are wholly silent on, and three
attempts (finance costs, depreciation, Paytm exceptional items) each found the
opposite, because the financial statements are themselves retrievable text and
Cohere ranks them 0.99 for a metric-named query.

*Quadrant 4 (both halves empty) — blocker UNKNOWN. An earlier claim here is
RETRACTED.*

Written and pushed earlier on 2026-08-02: that Quadrant 4 was blocked on
`COHERE_MEDIUM=0.15`, on the evidence of a genuine no-answer cross query
returning citation scores of 0.3211 / 0.1734 and tier=medium. **That was
wrong.** Those were not Cohere scores. The Cohere API had failed on that
request and the local ONNX cross-encoder served it instead — the numbers were
raw logits, on which 0.32 is unremarkable. The same query scored 0.0002
through `cohere_score_dump.py`, which aborts on a non-Cohere backend and was
therefore right while the API reading was wrong.

Nothing about `COHERE_MEDIUM` is established by that evidence. The real
blocker is open: Quadrant 4 requires empty `retrieved_chunks`, which arises
from `semantic_engine`'s hard refusal and not from chunks that were retrieved
and turned out useless. Whether a cross-routed genuine no-answer can reach
tier=low is unmeasured on EITHER scale. Re-measuring requires pinning the
backend, which is now possible: `reranker_backend` is exposed at admin tier
(section A).

Four cross-routed candidates were authored that day and all returned
tier=medium, so the question-authoring difficulty is real. The cleanest is
*"Does Eternal's disclosed approach to franchisee dispute resolution align
with its financial exposure to those disputes?"* — three runs, `path=cross`,
`dsl=None`, and a response stating the documents contain neither half,
transcribing no figure. By content it is Quadrant 4. Reuse it when
re-measuring.

WHY THIS WAS NOT CAUGHT: the query response exposed `reranker_score` with no
indication of which backend produced it, and §13 of this file states plainly
that the two scales are incompatible. The check was one field away and was not
made. That gap is now closed, but the lesson is the reason this entry is
written out rather than quietly deleted.

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
gemini-3.1-flash-lite: **87/88** — Eternal 54/54, Titan 15/15, Paytm 18/19,
after PQ019 was added and verified (see §12/§7). The preceding 86/87 differs by
that one question only.
Every sweep reported `Providers: {'gemini': N}` and
`Models served: {'gemini-3.1-flash-lite': N}`, with blocked queries correctly
excluded (7 + 2 + 2, no LLM call).

Composition note, because the number was not observed in a single run: the
Eternal sweep returned 53/54 with Q038 failing, Q038 was then recategorised from
`semantic_honest_refusal` to `semantic_audit` (its expectation was stale — see
below), and a scoped `semantic_audit` re-run returned 6/6. Nothing else changed
between the two. A single clean 54/54 artifact would need one more full Eternal
sweep, roughly 100 calls, and has not been run.

Q038 was golden-data staleness, not a defect. Its expectation assumed the corpus
held only the Q4FY26 SRE 2410 limited review, which disclaims opining on
internal-control effectiveness. But ETERNAL spans TWO fiscal years in Qdrant,
and the FY24 statutory audit does address internal financial controls under
s143(3)(i). The live answer cites FY24 pages 164-166 at 0.87-0.98, scopes the
finding explicitly to the year ended March 31 2024, and notes the absence of an
FY26 assessment. Accurate and better than a refusal. The question also
implicitly exercises CRAG rung 2 — it names no period, which is what makes the
FY24 report reachable.

The previous **83/84** is superseded. It predates the three golden questions
added 2026-08-02 (Q053, Q054, TQ015) and the Q038 recategorisation.

Do not read the 86 as the question count; there are 87 questions. The single failure is PQ012, which asserts
expected_path="semantic" while the current model routes it to "cross".

**Updated 2026-08-02.** The substitution half of that failure is FIXED: the
cross path no longer appends an unrelated SQL-verified figure ("Exceptional
Items for FY26 was ₹-186 Cr") to a question about PPBL exposure. See Stage 0c in
section A. Verified live — `dsl_object` is None, no figure is appended, and the
answer states the impairment correctly; the scoped `semantic_risk` sweep passes
its keyword and confidence assertions and fails on `expected_path` alone.

It stays red on purpose and carries a `known_deliberate_failure` field saying
so. Editing `expected_path` to "cross" would buy 84/84 by deleting the only
artifact recording that router classification is imprecise on "financial
exposure to X" phrasing.

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

---

## D. Latent risks — mechanism real, currently no victims

Entries here are NOT divergences. The code matches the spec and behaves
correctly today; each describes a failure mode that a specific, nameable change
in the data would activate. Recorded so the trigger is recognised when it
arrives, rather than rediscovered as a bug.

### §9 — The `quarter` filter is currently a no-op
`_build_filter` adds `quarter` as a hard `must` when set, which would exclude
every chunk whose `quarter` is null — i.e. all annual narrative. That mechanism
is real. It presently has no victims, and the recorded fear that annual
commentary was unreachable was mistaken.

Measured 2026-08-02 (`scripts/check_quarter_payload.py`, zero quota) across
ETERNAL's 2268 chunks. Storage is clean: exactly two shapes, `STORED_NULL`
(1999) and `VALUE:'Q4'` (269). No missing keys, no empty strings, no `"None"`
strings — so the `IsNullCondition` versus `IsEmptyCondition` distinction the
concern was framed around does not arise.

The decisive finding is that `quarter` and `fiscal_year` are perfectly
COLLINEAR in this corpus: every FY24 chunk is null (the Zomato annual report,
two doc_ids) and every FY26 chunk is `Q4` (the Q4FY26 filing, two doc_ids). So
`fiscal_year=FY26 AND quarter=Q4` excludes exactly what `fiscal_year=FY26`
excludes on its own — the quarter condition removes nothing. That is why a
probe under a Q4 filter returned the same candidate count as every other filter
shape.

**Trigger:** the first time one company has BOTH an annual and a quarterly
filing in the SAME fiscal year. That does not exist today — ETERNAL spans FY24
(annual) and FY26 (Q4), TITAN is Q1FY26 only, PAYTM is FY26. At that point a
`quarter=Qn` query would genuinely exclude that year's annual narrative, and the
fix is an OR-branch admitting stored-null alongside the requested quarter. Do
not build it before then: it would add a filter path with no test that fails
without it.

`fiscal_year` remains a hard `must` regardless. That one was never in question.

### §10 — `metric_anchor_phrases()` matches substrings, not words
Stage 0c tests `phrase in query` against raw lowercased text, and the anchor set
includes two- and three-character aliases: `da` (depreciation), `gov` (gross
order value). So `da` matches inside "**da**ta" and `gov` inside
"**gov**ernance". Confirmed 2026-08-02 while authoring cross candidates.

Not a defect today, for the reason §10 already gives: Stage 0c's polarity is
inverted relative to Stage 0b. It is consulted to find NOTHING, so a broader set
makes the guard fire LESS — leaving a query unguarded, which is the prior state
and recoverable — rather than suppressing a figure someone asked for, which would
be a new defect.

The consequence worth recording is that the effective anchor set is far broader
than the docstring implies, which makes authoring anchor-free queries harder than
expected. **Trigger:** if word-boundary matching is ever introduced it would
NARROW the set and could unguard queries currently caught, so it must be measured
against the full golden set first, exactly as the Stage 0c verification was.

### §9 / §13 — The reranker backend switches silently under network flap
Cohere is the primary reranker with the local ONNX cross-encoder as an
automatic fallback on API failure. The fallback is correct and necessary — it
is what keeps the 512MB tier serving when the API is unreachable. The latent
risk is that it changes the SCALE of every score in the response, and until
2026-08-02 nothing in the response said so.

Measured that day: raw socket connects from the backend container to
`api.cohere.com:443` succeeded 5 of 8 attempts, failing at random with
`TimeoutError` and `[Errno 111] Connection refused`; `api.cohere.ai:443`
behaved the same way, so it is not a hostname issue. This is the WSL2 network
flakiness family, same as the DNS work recorded elsewhere. One fallback
appeared in the whole log, so it is rare — but rare and silent is the
dangerous combination.

Consequence observed: the same query returned tier=medium on one run and
tier=high on another, purely because a different backend scored it. Both tiers
were CORRECT for their scale — `_score_confidence` selects thresholds by
backend and did its job. The defect was that the reader could not tell.

Closed by exposing `reranker_backend` at admin tier (section A). **Standing
rule: a `reranker_score` read from the API is meaningless without the backend
read from the same response.** Do not compare scores across runs without
checking it, and do not compare API scores against
`cohere_score_dump.py` output without checking it — that script has a hard
abort for non-Cohere backends and the API does not.
