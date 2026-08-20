# LedgerMind — Why We Built It This Way

One entry per significant design decision. Each records the **problem**, the
**choice**, the **alternatives**, the **trade-off**, and its **current status**.

**Sourcing rule.** Where the repository states a rationale (in a code comment,
`IMPLEMENTATION_DELTAS.md`, or `CLAUDE.md`), it is quoted or cited. Where it
does not, the entry says so explicitly and any rationale offered is labelled
**"Likely rationale — inferred"**. Nothing here presents inference as fact.

---

## ED-001 — LLMs never do math

**Problem.** A language model asked "what was revenue growth from FY25 to FY26"
produces a number that is fluent, unverifiable, and sometimes wrong. Financial
users cannot distinguish a right answer from a wrong one by reading it.

**Decision.** The LLM emits a **DSL object** (eight fields). A deterministic
Python compiler turns that into parameterised SQL. All arithmetic — YoY,
comparison, CAGR, growth comparison — happens in Python over values fetched from
Postgres. The LLM never writes SQL and never sees the schema.
`app/engines/dsl_compiler.py`, `app/engines/quant_engine.py:495-600`.

**Why.** Stated as the project's first non-negotiable in `CLAUDE.md` §6 and
blueprint Principle 1. The model's job is reduced from "be right about finance"
to "pick strings from a list", which is a task it is actually good at, and one
where wrongness is *catchable* by a validator.

**Alternatives.**
- *Let the LLM write SQL (text-to-SQL).* Rejected implicitly — it hands the model
  the schema and unbounded query surface. A wrong `WHERE` clause silently returns
  a real number for the wrong thing.
- *Function calling with per-metric tools.* Would work; costs one tool definition
  per metric and per operation, i.e. a combinatorial explosion the registry
  already avoids.
- *Let the LLM compute from retrieved text.* This is the failure mode the whole
  project exists to defeat.

**Trade-offs.** Gained: every number is reproducible from a logged SQL string,
and `sql_verified` is a real flag rather than a vibe. Sacrificed: expressiveness
— only five operations exist, and a question outside them gets refused rather
than approximated. **Derived metrics (EBITDA, gross profit) have no compiler at
all** and are refused outright by a Stage 0 guard.

**Status.** Production-ready and load-bearing.

**Caveats.** `GeminiDSLResponse.metric` and `.fiscal_year` are **required**
fields, so the model can never answer "no metric" or "no period" — it invents
one. Three separate guards exist purely to catch that (`CAVEAT-004`).

**Future.** A derived-metric formula compiler would let EBITDA be computed from
its components with the same verifiability. `registry.not_yet_derivable_metrics()`
already enumerates the candidates.

---

## ED-002 — Hybrid retrieval (dense + BM25) fused by RRF, not embeddings alone

**Problem.** Financial questions contain exact tokens that embeddings blur:
"PPBL", "Regulation 33", "FY26", "Hyperpure", a metric name. Dense retrieval
ranks by semantic similarity and can miss a chunk that contains the literal
string being asked about.

**Decision.** Two prefetch legs — dense (`bge-small-en-v1.5`, 384-d) and sparse
(`Qdrant/bm25`) — fused by **Qdrant-native Reciprocal Rank Fusion**, not by
manual score merging. `app/engines/retriever.py:235-254`.

**Why.** Documented in the module header: native RRF avoids hand-merging two
incompatible score scales. RRF ranks by *position*, so the two legs never need a
common unit.

**Alternatives.**
- *Dense only.* Loses exact-token recall.
- *BM25 only.* Loses paraphrase recall ("profitability" vs "PAT").
- *Manual weighted score merge.* Requires a tuned α and re-tuning whenever
  either model changes. RRF has one parameter (k) and is scale-free.

**Trade-offs.** Gained: robustness across both query styles with no tuning.
Sacrificed: no ability to weight one signal over the other for a specific query
class.

**Status.** Production-ready.

**Caveats.** The RRF score (~0.016 at rank 1) is a **third** incompatible scale.
It must never be fed to confidence thresholds calibrated for a reranker — that
exact mistake shipped once and made every semantic query refuse
(`retriever.py:483-495`).

---

## ED-003 — Filter inside each prefetch leg, not at fusion level

**Problem.** If you filter *after* fusion, both legs retrieve unfiltered
candidates first; the filter then removes some, and you are left with fewer than
`top_k` results ranked against competitors that should never have been in the
pool.

**Decision.** `filter=search_filter` is passed to **each** `Prefetch`
(`retriever.py:238-249`).

**Why.** Stated in the module docstring: *"filtering at fusion level would allow
unfiltered candidates to pollute ranking."*

**Trade-offs.** Slightly more work for Qdrant per leg; correct ranking. No
downside identified.

**Status.** Production-ready.

---

## ED-004 — A cross-encoder reranker, with a cloud primary and a local fallback

**Problem.** Bi-encoder retrieval (embed query, embed doc, compare) is fast but
approximate. The top-20 by RRF are usually *about* the right topic; ordering
within them is unreliable.

**Decision.** Rerank all 20 candidates with **Cohere `rerank-english-v3.0`**;
fall back to a local ONNX `ms-marco-MiniLM-L-6-v2` cross-encoder if Cohere is
unavailable. `retriever.py:364-447`.

**Why.** A cross-encoder reads query and document *together*, so it can judge
relevance rather than similarity. Cohere is primary because the local model costs
RAM the 512 MB deploy tier does not have (`retriever.py:372`).

**Alternatives.**
- *No reranking.* RRF ordering alone; measurably worse.
- *Local only.* RAM-infeasible on the deploy target.
- *LLM-as-reranker.* An extra LLM call per query against a 5 RPM quota.

**Trade-offs.** Gained: much better top-5. Sacrificed: **the two backends return
incompatible score scales** (Cohere 0–1; local raw logits ≈ −12…+2). This is
handled by keeping two threshold pairs and tagging every chunk with
`reranker_backend` — but it is a permanent complexity tax.

**Status.** Production-ready with a documented latent risk: the fallback can fire
silently on network flap, changing the meaning of every score in the response
(`IMPLEMENTATION_DELTAS.md` §D). `reranker_backend` is exposed at admin tier for
exactly this reason.

**Standing rule from `CLAUDE.md`:** *a `reranker_score` without its
`reranker_backend` is meaningless.*

---

## ED-005 — Near-duplicate suppression instead of reducing chunk overlap

**Problem.** `OVERLAP_TOKENS = 150` means adjacent chunks share ~150 tokens by
design. Two windows over the same text can therefore both win top-5 slots.
Measured 2026-07-30: two page-23 chunks, both 705 chars, 87.8% token overlap,
consuming 2 of 5 slots with identical boilerplate.

**Decision.** Keep the overlap; drop any chunk whose token-set containment with a
higher-ranked chunk is ≥ **0.70**. `retriever.py:316-361`.

**Why.** The overlap was raised from 50 to 150 specifically to stop a
mid-sentence split orphaning Paytm's PPBL impairment fact. Lowering it again
would reintroduce that bug to fix a different one.

**Alternatives.**
- *Reduce overlap.* Reintroduces the orphaning bug.
- *Embedding-cosine dedup.* Rejected in the comment: the text is already in
  hand, and a second model call to answer what a set intersection answers is
  cost for nothing.

**Trade-offs.** Denominator is the **smaller** chunk, so a short chunk fully
contained in a longer one scores 1.0 rather than being diluted. O(n²) over ~20
candidates ≈ 190 set intersections — negligible next to a network rerank call.

**Status.** Production. The 0.70 threshold is calibrated on **one** measured
pair and is logged at INFO with the real ratio so a distribution can be built.

---

## ED-006 — PostgreSQL with raw psycopg2, no ORM

**Problem.** Financial records are flat rows with a strict uniqueness invariant
and hand-written restatement logic.

**Decision.** Raw SQL through psycopg2. Stated in `db_loader.py`: *"SQLAlchemy
adds nothing for flat record inserts."*

**Why (as recorded).** Consistency with the Phase 2 decision to keep schema in
`.sql` files. **Likely rationale — inferred:** the tricky parts here are
`SELECT … FOR UPDATE`, a partial unique index, `IS NOT DISTINCT FROM` for
nullable quarters, and `SET LOCAL` for RLS. All four are things an ORM either
obscures or fights.

**Alternatives.** SQLAlchemy Core / ORM; Django ORM. Both add a translation layer
between what you write and what Postgres executes — which is exactly what you do
*not* want when RLS silently returns zero rows.

**Trade-offs.** Gained: the SQL in the audit log is the SQL that ran. Sacrificed:
no migrations framework (hand-numbered `.sql` files + a `schema_migrations`
table), no connection pooling abstraction, and every call site must remember
`SET LOCAL app.tenant_id`.

**Status.** Production.

**Caveats.** psycopg2 adapts Python UUIDs as TEXT — casts are manual
(`ANY(%s::uuid[])`). `db_transaction()` yields a **connection**, not a cursor.

---

## ED-007 — Multi-tenancy by Postgres RLS + Qdrant payload filter, not by application `WHERE`

**Problem.** A tenant filter that lives only in application code is one forgotten
`WHERE` away from a cross-tenant data leak, and nothing fails loudly when it is
forgotten.

**Decision.** Row-Level Security policies on `documents`, `financials`,
`audit_log`, `FORCE`d so the owner is covered too; `SET LOCAL app.tenant_id` per
transaction. Qdrant gets a mandatory `tenant_id` payload condition in
`_build_filter`. `sql/init.sql:135-166`, `retriever.py:169-172`.

**Why.** Defence in depth: the database refuses to hand over another tenant's
rows even if the application asks.

**Alternatives.**
- *Schema-per-tenant / database-per-tenant.* Stronger isolation, far heavier
  operationally, and migrations multiply.
- *Application-level filtering only.* One missed clause is a breach.

**Trade-offs.** Gained: a hard boundary. Sacrificed: a missing GUC produces
**zero rows rather than an error**, which reads as "no data" and has repeatedly
been misdiagnosed as such — hence the standing warning in `CLAUDE.md` §6.

**Status.** Production. Note that `SET LOCAL` (transaction-scoped) is used
deliberately over `SET`; a bare `SET` on a pooled connection leaks a tenant
setting into the next request (`db/session.py:13-17`).

---

## ED-008 — LangGraph `StateGraph` over a `TypedDict`, and nothing more

**Problem.** The pipeline has conditional branching (three paths, two exits) and
needs per-node streaming for the UI trace.

**Decision.** A single `StateGraph(QueryState)` with eight nodes and two
conditional edges. **No `MessagesState`, no agent abstractions** — the module
docstring says this is "the stable subset of the LangGraph API".

**Why.** Recorded as a risk-management choice at the start of Phase 4: agent
abstractions in this library churn; `StateGraph` + `TypedDict` does not.

**Alternatives.**
- *Plain function composition.* Would work; loses the free per-node streaming
  that `astream("updates")` provides, which is what makes the UI trace
  trustworthy (a node cannot forget to report itself).
- *An agent framework with tool-calling.* Rejected by the project's stated
  preference for deterministic over agentic (`CLAUDE.md` §2).

**Trade-offs.** Gained: streaming for free, explicit topology in 60 readable
lines. Sacrificed: a dependency whose API surface must be kept to a minimum.

**Status.** Production.

---

## ED-009 — Two engine paths plus a hybrid, chosen by an LLM router

**Problem.** "What was revenue?" and "what risks are disclosed?" need completely
different machinery. One pipeline that does both does neither well.

**Decision.** Three paths — `quantitative`, `semantic`, `cross` — selected by an
LLM classification call that *also* extracts entities in the same call
(`router.py:129`).

**Why.** One call rather than two against a 5 RPM quota. The comment at
`router.py:29-40` notes that if extraction and classification ever conflict, the
right fix is **two calls**, not another prompt line.

**Alternatives.**
- *Keyword routing.* Brittle: "how did profitability trend" names no metric.
- *Always run both paths.* Doubles cost and latency; the cross path already does
  this deliberately when it is warranted.

**Trade-offs.** Gained: each engine stays simple. Sacrificed: a routing mistake
sends a question to the wrong machinery — currently the known open case is
TQ008, which routes `cross` where its golden expects `semantic`, cause unknown.

**Status.** Production, one known unexplained misroute.

---

## ED-010 — Refuse rather than search unfiltered (audit finding F2)

**Problem.** `_build_filter` appends a company condition only `if company:`. A
null company therefore silently widened the search to the whole tenant. Measured
2026-08-12: a Reliance query returned five citations from TITAN/ZOMATO pages at
`tier=high`, 0.7095.

**Decision.** `router_node` writes a refusal (`company_not_in_corpus` or
`routing_unavailable`) and `route_after_router` returns `"refused"`, which
`graph.py` maps **directly to `audit_writer`** — deliberately skipping the
confidence → response tail, which would otherwise rescore the refusal.

**Why.** A confident wrong answer is the failure this project defines itself
against.

**Alternatives considered and rejected in the code.**
- *Refuse when `company is None`.* Wrong: a multi-entity query nulls `company`
  even when every issuer resolves. Q051 ("Eternal or Paytm") would have been
  refused. Hence `company_mentioned` + `_resolve_mentioned_issuers`.
- *Add a prompt instruction telling the model to always fill the field.* Written,
  shipped, then **removed** for no measured loss (`router.py:30-40`). Removing the
  instruction did not remove the field from the model's input: `RouterResponse` is
  sent as the response schema on both providers, so the declaration is an input
  change whether or not any prompt line describes it. See
  `IMPLEMENTATION_DELTAS.md` section D, "The response schema is part of the prompt".

**Trade-offs.** Gained: unknown issuers now refuse honestly. Sacrificed: nothing
measured — Q051 stayed at `sql_verified=true`, confidence 1.0.

**Status.** Closed 2026-08-12. **Partial by construction:** it fires only when
the model *returns* an unresolvable name. A query naming an unheld company can
still produce `company=None` with no name recorded. Read `router.py:296-314`
before assuming otherwise.

---

## ED-011 — One shared LLM client with a narrow, exception-keyed fallback

**Problem.** Two independent production defects, both found 2026-07-29: no call
site set a **timeout** (one call measured at 120 s), and the promised
Gemini→Groq **fallback had never been implemented**.

**Decision.** One module, `app/llm/client.py`, two entry points
(`generate_text`, `generate_structured`), used by all three call sites.

**Why the two fixes are inseparable:** *"a timeout converts an unbounded hang
into a catchable exception at a bound we choose, and only then is there anything
for a fallback to catch."*

**Design details worth learning from.**
- **Fallback trigger is deliberately narrow** — timeouts, 429, 5xx, transport
  errors. **Not** 401/403/invalid-argument: serving those from the fallback
  would hide a config error.
- **Structured output is not symmetric.** Gemini has `response_schema`; Groq has
  only "valid JSON". So the Groq path serialises the schema into the prompt and
  validates the result — and a schema miss is treated as a **provider failure**,
  not a parse error.
- **RPM vs daily 429s need opposite handling.** Google labels both the same, so
  the code keys on the server's `retryDelay`: ≤5 s → sleep and retry once;
  otherwise fall through to Groq.
- **`GEMINI_MODEL` has no default and raises if unset.** A plausible-but-wrong
  default cost two full eval sweeps attributed to a model that never served a
  call.

**Status.** Production. `TIMEOUT_STRUCTURED_MS` was raised 8 s → 20 s on
2026-08-13 after measurement showed the tight bound was both wrong *and slower*
(a timeout costs 8 s plus a ~8.8 s Groq call, versus ~5.7 s served correctly).

---

## ED-012 — Provider attribution by precedence, not last-writer-wins

**Problem.** `llm_provider` was set by whichever call last **succeeded**. A
semantic query makes two calls; if the router fell back to Groq and synthesis
did not, the answer logged as pure Gemini. Measured 2026-07-31: the eval gate
reported 11/45 non-Gemini when the true figure was ≥13.

**Decision.** `record_llm_call()` moves attribution **only toward more
degraded** (`_PROVIDER_TAINT = {"gemini": 0, "groq": 1}`), and
`clear_llm_attribution()` nulls it when no LLM produced the text at all.
`state.py:219-271`.

**Why.** *"If either call is served by the fallback, the ANSWER is a fallback
artifact regardless of call order."*

**Trade-offs.** Gained: the audit row describes the artifact the user received.
Sacrificed: you cannot tell *which* call fell back from this field alone.

**Status.** Production. Direct assignment to `state["llm_provider"]` is
forbidden by comment.

---

## ED-013 — A single metric registry

**Problem.** Metric definitions lived in three hand-maintained dicts
(`entity_resolver.METRIC_ALIASES`, `dsl_compiler.METRIC_REGISTRY`,
`quant_engine.ALIASES`). Three shipped bugs came directly from the split —
including `profit_before_tax` being absent from one of them, so the model had no
correct option and silently substituted `pat`.

**Decision.** `app/metrics/registry.py` is the only place a metric is defined.
Every consumer derives its own view via a function (`dsl_registry()`,
`all_alias_pairs()`, `prompt_metric_lines()`, `derived_metric_aliases()`,
`metric_anchor_phrases()`, …).

**Why.** One fact, one home. `CLAUDE.md` §6 forbids adding a second registry
anywhere.

**Design subtlety worth studying.** Two of the derived views have **opposite
polarity**, and the docstrings say so explicitly:
- `unqueryable_metric_aliases()` is consulted to **find** a phrase, so a broad
  set over-fires → it has a 4-word floor.
- `metric_anchor_phrases()` is consulted to **find nothing**, so a broad set
  fires *less* → no floor, and widening it is free safety.

That is the same data shaped by how the caller uses it, and getting the polarity
backwards would break both.

**Status.** Production.

**Caveats.** The registry defines *semantics*, deliberately **not** whether a
metric exists for a given company/period — that is data state, resolved by a
zero-row SQL result. Audit F6 records 174 stored metric names with no registry
anchor (686 of 1437 rows).

---

## ED-014 — Templated quantitative answers, generative semantic answers

**Problem.** Once SQL has produced a verified number, asking an LLM to write a
sentence around it reintroduces exactly the hallucination risk the SQL removed.

**Decision.** `path="quantitative"` builds its answer from a **deterministic
template** (`_format_quant_response`). `path="semantic"` uses generation, because
there is no ground-truth number to protect — only retrieved text to summarise.

**Why.** Stated at `response_generator.py:8-20`.

**Trade-offs.** Gained: zero hallucination surface on the number path.
Sacrificed: quantitative answers read mechanically.

**Status.** Production.

**Subtlety.** The cross path uses the **same formatter** to produce the injected
"verified fact" and the appended line — one formatter, deliberately, so the two
cannot drift (`response_generator.py:654-661`). This project has already paid
for a two-copies-of-one-formula bug.

---

## ED-015 — Corrective RAG as a filter ladder, not a query rewrite

**Problem.** On a small corpus the commonest cause of weak retrieval is an
over-specific metadata filter, not a badly worded query.

**Decision.** On `medium`/`low` confidence, retry with progressively broader
filters: rung 1 drops `quarter`, rung 2 drops `fiscal_year` too. Max 2 rungs.
`semantic_engine.py:209-265`.

**Why.** Cheap, deterministic, and addresses the actual observed cause.

**Alternatives.** LLM query rewriting (an extra call against quota, and
non-deterministic); web search fallback (out of scope — the corpus *is* the
truth boundary).

**Trade-offs / bug history.** Two real bugs lived here:
1. A rung that drops an already-unset filter re-issues an identical query. Now
   returns `None` to signal "nothing to broaden".
2. That `None` was originally handled with `break`, so any query with
   `quarter=None` — i.e. **every annual query** — skipped rung 2 as well, which
   is the rung that does real broadening. Now `continue`. *`crag_count` is the
   rung index reached, not the number of retrievals performed.*

**Status.** Production.

---

## ED-016 — Ingestion is offline and operator-triggered

**Problem.** Loading `bge-small-en-v1.5` in the web process OOM-killed Render's
512 MB free tier ("Exited with status 137", repeatedly).

**Decision.** `POST /api/documents/upload` runs the cheap gate, pushes the file
to Supabase Storage, and inserts a `pending_uploads` row. **It does not start
ingestion.** `scripts/process_pending_uploads.py` polls and runs the pipeline
where the RAM exists.

**Why.** Recorded at `api/documents.py:10-19`. Note the reasoning: this is unsafe
*regardless* of whether it is triggered via Celery or `BackgroundTasks`, because
the constraint is process RAM, not concurrency.

**Alternatives.** A dedicated Celery worker service (Render has no free
background-worker tier); a bigger instance (cost); a hosted embedding API
(another dependency and another key).

**Trade-offs.** Gained: the query service never OOMs. Sacrificed: upload is not
self-service — a human runs a script. `pending_uploads.status` is surfaced in the
UI so this is visible rather than mysterious.

**Status.** Production, and explicitly a **superseded** blueprint item
(`IMPLEMENTATION_DELTAS.md` §C: "Ingestion is manual, not event-driven").

---

## ED-017 — Deterministic chunk IDs

**Decision.** `chunk_id = UUID(md5(f"{doc_id}:{page}:{position}:{text[:100]}"))`.
`chunker.py:84`.

**Why.** Re-ingesting the same PDF produces the same IDs, so Qdrant `upsert`
overwrites rather than duplicating. Idempotency without a delete-then-write
window.

**Trade-off.** The ID is only stable while `doc_id`, page numbering, split
positions and the first 100 chars all hold. A chunker change re-IDs everything
and **orphans the old points** — Qdrant has no cascade. See
`IMPLEMENTATION_DELTAS.md` §D, "Orphaned vector rows".

**Status.** Production, with a known cleanup obligation.

---

## ED-018 — Restatements retire rows; they never delete or overwrite them

**Decision.** `classify_upsert()` (`db_loader.py:184`) is a **pure function**
returning one of `inserted / corrected / skipped / restated / reingested`.
`_upsert_one` acts on that label rather than re-deciding.

**Why this is a pure function.** So the `--dry-run` preview and the writer cannot
drift: *"a hand-written mirror is a copy that drifts silently… Now there is one
decision, in one place, exercised by both."*

**The distinction worth learning.** A **restatement** is the issuer publishing a
revised figure → retire the old row, insert a new one. A **parser correction** is
*our reading* changing while the filing did not → update `value` **in place**,
touching nothing else. Recording the second through the first's machinery would
"manufacture a filing history that does not exist" (`db_loader.py:82-96`).

**Status.** Production. `correct_values` is opt-in and off by default.

**Caveat.** The *confidence* penalty for restatements (`confidence.py:86`) reads
`restatement_disclosed`, which nothing currently sets — audit finding **F5**.

---

## ED-019 — Contradiction detection is deliberately strict

**Problem.** The first version treated every crore figure in every chunk as a
claim about the queried metric. Live result: **eleven** "severity: high"
contradictions against PAT = ₹366 Cr, including +4730.6%. None were real. Worse,
the top-cited chunk was the *cash-flow statement of the same document the
`financials` row was extracted from* — the engine was flagging disagreement
between a value and its own source.

**Decision.** Three constraints: (A) narrative chunk types only; (B) a figure is
a claim about metric M only if an alias of M appears within
`PROXIMITY_WINDOW = 120` characters; (C) ±5% tolerance.

**Why.** Stated in the module docstring: *"A FALSE contradiction is worse than a
missed one. This system's stated value is surfacing disagreement instead of
fabricating certainty; fabricating disagreement is the one failure that directly
inverts that claim."*

**Trade-off, accepted explicitly.** These rules will miss real contradictions
phrased at a distance from the metric name. *"That trade is intentional."*

**Status.** Built. `IMPLEMENTATION_DELTAS.md` §C records the cross path as
**BUILT but UNMEASURED**.

---

## ED-020 — Speaker-turn chunking for transcripts, with a roster parsed from the document

**Problem.** Generic 200-token chunking of an earnings transcript produced a
chunk opening mid-sentence on an **analyst's premise** with no attribution,
carrying a different speaker's name later in the same chunk. In this document
management *denies* several analyst premises in the very next turn. An
unattributed analyst assertion reads as a company claim — which is precisely how
a contradiction detector manufactures disagreement.

**Decision.** Transcripts split on speaker turns; `speaker_role` is a **stored**
metadata field (`management` / `analyst` / `moderator` / `unknown`); the
management roster is parsed from the document's own page-1 declaration; a missing
roster is a **hard ingest failure**, never a default.

**Supporting details.** A module-level thread carries "who is still talking" past
page boundaries (`parse_pdf` emits one block per page). Continuation pieces are
prefixed `"<Speaker> (cont.): "` because *"a continuation piece that reads as a
fresh verbatim attribution is text this system invented at the data-entry
point."* Turn-to-turn overlap is **zero** — and only became safe once threading
existed.

**Status.** Production.

---

## ED-021 — Field-level RBAC that fails closed

**Decision.** `role_filtered_response()` builds the viewer payload first, then
adds machinery for analyst, then admin-only operational fields. An unrecognised
role (typo, null, a role added to the DB but not here) gets the **viewer**
payload. `api/response_shaping.py:54-59`.

**Why.** Without the explicit `role not in _KNOWN_ROLES` check the function falls
through every `if` and returns the **full admin response**. Fail-closed is a
one-line difference from fail-open here.

**Critically:** the graph always runs in full and `audit_log` always receives the
complete record. Only the HTTP response is filtered. Filtering is a *disclosure*
decision, not an execution one.

**Status.** Production.

---

## ED-022 — The Prompt Shield matches request structure, not keywords

**Problem.** SEBI compliance forbids investment advice. A keyword blocklist on
"buy"/"invest" blocks legitimate research ("what did Zomato buy?", "investment in
Blinkit").

**Decision.** Regex patterns that match the **advice-request grammar**:
`should\s+i\s+(buy|sell|…)`, `is\s+\w+\s+(a\s+good)…(investment|buy|stock)`,
`will\s+the\s+stock\s+(go up|…)`. Pure regex, no network, runs on every query
including the ones that will later be refused.

**Why.** Blueprint Trap 4. The five worked examples in the module docstring are
the specification.

**Trade-offs.** Gained: zero latency, fully testable, no LLM in the security
path. Sacrificed: recall — paraphrases outside the pattern set pass. Injection
patterns return a **minimal** message deliberately (don't tell an attacker what
triggered).

**Status.** Production.

---

## ED-023 — Next.js frontend, not Streamlit

**Decision.** The blueprint specified Streamlit; the shipped UI is Next.js.
`streamlit_frontend_archive/` retains the original.

**Why (as recorded in `IMPLEMENTATION_DELTAS.md` §C).** Deliberate override.
**Likely rationale — inferred:** the SSE execution trace, the paged working-paper
metaphor, and per-component role gating are not expressible in Streamlit's
rerun-the-script model.

**Trade-offs.** Gained: a real UI with real streaming. Sacrificed: a build step,
a second language, and a second deploy target (Vercel).

**Status.** Production.

---

## ED-024 — `composeDocumentBody()` is the only path-aware frontend function

**Decision.** Every `components/document/*` component takes plain props. Only
`composeDocumentBody()` in `app/page.tsx` reads `data.path`, `data.sql_result`,
`data.error`.

**Why.** Stated as an invariant in `CLAUDE.md` §6. It means adding a fourth
engine path touches one function, not twenty components.

**Related invariant — the Zero UI-Hallucination Mandate.** No badge, count, stat
or citation number may exist as static copy. Where a field is absent the UI
**omits** rather than substitutes — e.g. `buildCitationItems` drops the
financial-type tag when it is `"unknown"`, because rendering "(unknown)" reads as
a classification failure when it is a correct N/A.

**Status.** Production.

---

## ED-025 — Constants that encode measurements are frozen

**Decision.** `CLAUDE.md` §1.3 forbids changing `COHERE_HIGH` (0.5),
`COHERE_MEDIUM` (0.15), the near-duplicate threshold (0.70), the alias coverage
floor (0.5), `OVERLAP_TOKENS` (150), `BATCH_SIZE` (8) without approval.

**Why.** *"Each encodes a measurement that is not derivable from the code."*
The number in the source is the *conclusion*; the measurement is in the comment
above it or in `docs/measurements/`.

**The instructive counter-example.** The citation relevance floor (0.05) was
**deliberately removed** on 2026-08-08. The constant was not wrong — the
measurement behind it stands. What was wrong was allowing `retrieved_chunks` and
`citations` to diverge at all: the floor did not prevent an unsupported claim, it
guaranteed the claim could not be *checked*. Read `semantic_engine.py:61-93`
before ever reintroducing it.

**Also worth knowing:** `COHERE_MEDIUM` (0.15) is the refuse-vs-answer boundary
and **has never been exercised by a real query**. It is unvalidated, not
validated — which is why it must not be tuned casually.

**Status.** Standing policy.
