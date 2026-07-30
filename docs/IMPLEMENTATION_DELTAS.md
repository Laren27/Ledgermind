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

1. **Empty GUC, not NULL.** `DATABASE_URL` routes through a transaction pooler,
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

### §1 / §21 — Corpus
Shipped corpus is Eternal (formerly Zomato), Titan, and Paytm — not the ten
companies §1 lists. 83 golden questions across three datasets, above the
blueprint's 50.

### §11 — Truth Resolution: SPEC HONORED
Recorded here because it is frequently assumed unbuilt. `db_loader.py`
implements the Trap 3 fix as written: `SELECT ... FOR UPDATE` locks the existing
`is_latest=TRUE` row for the business key before any flip, retirement and insert
happen in one transaction, and the same-filing-date re-ingestion case is handled
separately from the newer-filing restatement case.

Caveat on scope: the race the lock defends against requires concurrent
ingestion, and ingestion is currently a single manual CLI run. The mitigation is
correct and untriggered.
