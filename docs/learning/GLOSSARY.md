# LedgerMind Glossary

Each entry: **simple explanation → how LedgerMind uses it → why we need it →
example from this repo → the common misconception**.

Terms are grouped, not alphabetised, because concepts are easier to learn in
clusters than in dictionary order.

---

# A. Retrieval & RAG

## RAG (Retrieval-Augmented Generation)
**Simple.** Instead of hoping a language model memorised a fact, you fetch the
relevant text first and hand it to the model along with the question.

**In LedgerMind.** The semantic path: `retrieve_and_rerank()` finds up to 5
chunks, `_generate_semantic_response()` gives them to Gemini with the
instruction to use *only* what is in them.

**Why we need it.** Filings are private, recent and specific. No model has them
memorised, and one that pretends to would be worse than useless.

**Common misconception.** "RAG stops hallucination." It does not. It makes
hallucination *checkable*, because every claim should trace to a retrieved
chunk. That is why this project treats a citation you cannot verify as a defect
— see the 0.05 citation-floor removal (`semantic_engine.py:61-93`).

## Chunking
**Simple.** Cutting a document into pieces small enough to embed and to fit in a
model's context.

**In LedgerMind.** `app/ingestion/chunker.py`. Tables are **never split** —
a table is one chunk regardless of size. Prose is split recursively at
paragraph → line → sentence → word → character boundaries. Transcripts split on
**speaker turns**.

**Why we need it.** Embeddings represent a fixed-size window; a 371-page annual
report cannot be one vector, and a whole page is too coarse to retrieve
precisely.

**Example.** `TARGET_TOKENS` in `chunker.py:47` — 250 tokens for risk
disclosures, 200 for MD&A, `None` (never split) for tables.

**Common misconception.** "Smaller chunks are more precise, so use small ones."
Too small orphans a fact from its subject. `OVERLAP_TOKENS` was raised 50 → 150
specifically because a mid-sentence split orphaned Paytm's PPBL impairment fact.

## Chunk overlap
**Simple.** Adjacent chunks deliberately share text so a fact split across the
boundary appears whole in at least one of them.

**In LedgerMind.** 150 tokens (600 characters) for filings. **Zero** between
transcript speaker turns — and the zero is the point: attribution threading plus
a `(cont.)` prefix reconnects continuations *by attribution* rather than by
repeated text.

**Why we need it.** Boundaries are arbitrary; facts are not.

**Common misconception.** "Overlap is free." It is not: two windows over the
same text can both win top-5 slots. That is what near-duplicate suppression
exists to fix.

## Embedding
**Simple.** A function that turns text into a list of numbers, arranged so that
texts about similar things land near each other.

**In LedgerMind.** `BAAI/bge-small-en-v1.5` via fastembed ONNX → **384
dimensions**, normalised, compared by cosine distance.

**Why we need it.** It lets "profitability commentary" find a passage that says
"margin improvement" without sharing a single word.

**Common misconception.** "Bigger embedding models are always better." 384-d
bge-small was chosen partly because the deploy target has 512 MB of RAM. A model
you cannot run is worth zero dimensions.

## Cosine similarity
**Simple.** Measures the **angle** between two vectors, ignoring their length —
so it compares direction (meaning) rather than magnitude.

**In LedgerMind.** The Qdrant `dense` vector distance. Embeddings are
normalised at generation, which is what makes cosine the right metric.

**Common misconception.** "Cosine similarity ≈ relevance." It is *topical*
similarity. A chunk can be maximally similar and still not answer the question —
which is exactly what the reranker is for.

## BM25 (lexical / sparse retrieval)
**Simple.** The classic keyword-scoring algorithm: rank documents by which query
words they contain, weighting rare words more and long documents less.

**In LedgerMind.** `Qdrant/bm25` via fastembed, stored as a **sparse vector**
alongside the dense one and searched by the same engine.

**Why we need it.** Financial questions contain exact tokens embeddings blur:
"PPBL", "Regulation 33", "FY26", "Hyperpure". BM25 matches those literally.

**Common misconception.** "Embeddings made BM25 obsolete." They made it
*insufficient*, not obsolete — which is why the hybrid exists.

## Sparse vs dense vectors
**Simple.** A **dense** vector is 384 numbers, almost all non-zero, each
meaningless on its own. A **sparse** vector is mostly zeros with a few non-zero
entries, each corresponding to a specific word.

**In LedgerMind.** One Qdrant point carries **both**, as named vectors `dense`
and `sparse`.

## Hybrid retrieval
**Simple.** Run a semantic search and a keyword search, then combine the
results.

**In LedgerMind.** `retriever.hybrid_search()` — two `Prefetch` legs into one
`query_points` call.

**Why we need it.** Neither signal alone is sufficient. See BM25 above.

**Common misconception.** "Hybrid means averaging the two scores." Here it means
**fusing the two rankings** — see RRF.

## RRF (Reciprocal Rank Fusion)
**Simple.** Combine rankings by **position**, not by score: each list
contributes `1/(k + rank)`, and the sums are added. A document ranked highly by
both legs wins.

**In LedgerMind.** `FusionQuery(fusion=Fusion.RRF)` — computed by Qdrant, not by
our code.

**Why we need it.** Dense scores (0–1 cosine) and BM25 scores (unbounded) have
no common unit. RRF never compares them; it compares their *positions*.

**Example.** RRF scores here are ~0.016 at rank 1. That is a **third**
incompatible scale, and feeding it to reranker-calibrated thresholds once made
every semantic query refuse (`retriever.py:483-495`).

**Common misconception.** "The RRF score means something." It is a fusion
artefact, not a relevance measure.

## Reranking / cross-encoder
**Simple.** A **bi-encoder** embeds the query and the document separately and
compares. A **cross-encoder** reads them *together* and outputs one relevance
score. Cross-encoders are far more accurate and far too slow to run over a whole
corpus — so you retrieve 20 cheaply, then rerank those 20 expensively.

**In LedgerMind.** `retriever.rerank()` — Cohere `rerank-english-v3.0` primary,
local ONNX `ms-marco-MiniLM-L-6-v2` fallback. All 20 candidates are scored
(Cohere bills per *search*, not per document), then deduplicated, then cut to 5.

**Why we need it.** RRF gets you the right neighbourhood; the reranker gets you
the right chunk.

**Common misconception — and this one cost real time.** "A reranker score is a
reranker score." **No.** Cohere returns 0–1 relevance; the local model returns
raw logits ≈ −12…+2. Applying the local thresholds to a Cohere score classified
*everything* as high confidence. Hence two threshold pairs and the standing rule:
**a score without its `reranker_backend` is meaningless.**

## CRAG (Corrective RAG)
**Simple.** If the first retrieval looks weak, retry differently instead of
answering from it.

**In LedgerMind.** `_broaden_retrieval()` — rung 1 drops the `quarter` filter,
rung 2 also drops `fiscal_year`. Max 2 rungs.

**Why this shape.** On a small corpus the commonest cause of weak retrieval is
an over-specific filter, not a badly worded query.

**Common misconception.** "CRAG means rewriting the query with an LLM." That is
one implementation. Here it is a deterministic filter ladder — cheaper, and it
addresses the observed cause.

## Near-duplicate suppression
**Simple.** Drop a result that mostly repeats a better result you already kept.

**In LedgerMind.** Token-set containment ≥ 0.70 against any higher-ranked chunk,
with the **smaller** chunk as the denominator so a short chunk fully contained
in a longer one scores 1.0.

**Why we need it.** Overlap is deliberate, so two windows over the same text can
both rank highly. Measured: two page-23 chunks, 87.8% overlap, consuming 2 of 5
slots with identical boilerplate.

## Top-k
**Simple.** How many results to keep.

**In LedgerMind.** `TOP_K_RETRIEVAL = 20` (per leg, before fusion),
`TOP_K_RERANK = 5` (final).

**Why it is a parameter and not a constant in the function body.** So the caller
decides. `retrieve_and_rerank(retrieval_top_k=…, rerank_top_k=…)` lets CRAG or a
future caller widen the pool without editing the retriever.

---

# B. LLMs & prompting

## Token
**Simple.** The unit a language model actually reads — roughly a word-piece.

**In LedgerMind.** Approximated as **4 characters** in `chunker.py`
(`CHARS_PER_TOKEN = 4`). Deliberately an approximation: exact tokenisation would
add a dependency to make a chunk 3% more precise.

## Context window
**Simple.** The maximum amount of text a model can see at once.

**In LedgerMind.** Effectively 5 chunks + the question + the system prompt, with
`max_tokens=400` on the *output*. Small on purpose.

**Common misconception.** "More context is better." More context means more
opportunity for the model to synthesise across passages that should not be
combined — and, for this system, more unverifiable claims.

## Structured output
**Simple.** Constraining the model to emit JSON matching a schema, rather than
prose.

**In LedgerMind.** `generate_structured(schema=RouterResponse | GeminiDSLResponse)`.
Gemini enforces it natively (`response_schema`). **Groq does not** — it
guarantees valid JSON, not the right *shape* — so the Groq path serialises the
schema into the prompt and validates the result, treating a shape miss as a
**provider failure** rather than a parse error.

**Common misconception.** "Structured output means the content is correct." It
means the *shape* is correct. A required `metric: str` field guarantees a metric
string, not the *right* one — which is why three regex guards exist upstream.

## Temperature
**Simple.** Randomness in sampling. 0 = most deterministic.

**In LedgerMind.** `0.0` for routing and DSL generation (you want the same
question to route the same way); `0.2` for synthesis (a little fluency).

**Common misconception.** "Temperature 0 is deterministic." It is *more*
deterministic. Provider-side batching and model updates still move results —
which is why this project's rule is **three runs with the model printed**, never
one.

## Prompt injection
**Simple.** Text that tries to become an instruction — "ignore your previous
instructions and…".

**In LedgerMind.** `prompt_shield.py`, first node in the graph. See
`docs/security/SECURITY_MODEL.md` §4 for what it does **not** cover (indirect
injection through ingested documents).

## Grounding
**Simple.** Requiring every claim to come from supplied source text.

**In LedgerMind.** `SYNTHESIS_SYSTEM_PROMPT`: *"answer the user's question using
ONLY information present in the excerpts."* Plus citations appended by the
system, never written by the model — the prompt explicitly tells it not to
restate page numbers.

## Fallback / failover
**Simple.** When the primary provider fails, use another.

**In LedgerMind.** Gemini → Groq, on a **deliberately narrow** trigger:
timeouts, 429, 5xx, transport errors. **Not** 401/403 — serving a config error
from the fallback would hide the real fault.

**Common misconception.** "Retry everything." A parse error or an empty response
is model behaviour the fallback will likely reproduce; retrying just doubles
latency before the same failure.

---

# C. The quantitative path

## DSL (Domain-Specific Language)
**Simple.** A tiny, purpose-built language that can express exactly what you
need and nothing else.

**In LedgerMind.** An eight-field JSON object:
`{metric, entity, fiscal_year, quarter, financial_type, operation,
comparison_entity, comparison_period}`.

**Why we need it.** It shrinks the model's job from "answer a financial
question" to "fill in these fields from these lists" — a task that is both
easier and, crucially, **validatable**.

**Common misconception.** "Why not just let the model write SQL?" Because a
wrong `WHERE` clause returns a real number for the wrong thing, and nothing
downstream can tell. A DSL has a finite, checkable vocabulary.

## Compiler (in the DSL sense)
**Simple.** Deterministic translation from one representation to another.

**In LedgerMind.** `SQLCompiler.compile()` maps each operation to a fixed SQL
template with bound parameters. Same DSL in → same SQL out, every time.

## Parameterised query / SQL injection
**Simple.** Send the SQL and the values separately, so a value can never be read
as SQL.

**In LedgerMind.** `cur.execute(sql, params)` everywhere. There is no f-string
interpolation of model output into SQL anywhere in `dsl_compiler.py`.

## Idempotency
**Simple.** Doing it twice has the same effect as doing it once.

**In LedgerMind.** Deterministic chunk IDs (`md5(doc_id:page:position:text[:100])`)
so a re-ingest overwrites rather than duplicates; `ON CONFLICT DO NOTHING` on
the financials insert; `classify_upsert` returning `"skipped"` for a same-doc_id
replay.

**Why we need it.** Ingestion is retried (Celery `max_retries=2`) and re-run by
hand constantly. Without idempotency each retry doubles the data.

## `is_latest` / restatement
**Simple.** Companies revise previously published figures. You must keep the old
one *and* know which is current.

**In LedgerMind.** A boolean column plus a **partial unique index**:

```sql
CREATE UNIQUE INDEX uq_financials_latest ON financials
  (tenant_id, company, fiscal_year, quarter, financial_type, metric)
  WHERE is_latest = TRUE;
```

**Why we need it.** It makes "exactly one current value per business key" a
database-enforced invariant while allowing unlimited history.

**Common misconception.** "A changed number means a restatement." Not
necessarily. If *our parser* changed and the filing did not, that is a **parser
correction** — updated in place, `is_latest` untouched. Recording it as a
restatement would manufacture a filing history that never existed
(`db_loader.py:82-96`).

## Partial index
**Simple.** An index over only the rows matching a condition.

**In LedgerMind.** The `WHERE is_latest = TRUE` above. Smaller, faster, and it
lets a `UNIQUE` constraint apply to *current* rows only.

## Transaction / `SET LOCAL`
**Simple.** A transaction is a group of statements that all succeed or all roll
back. `SET LOCAL` sets a session variable **for that transaction only**.

**In LedgerMind.** `SET LOCAL app.tenant_id = %s` before every RLS-protected
query.

**Why `LOCAL` matters.** A bare `SET` on a pooled or reused connection persists
and leaks one tenant's setting into the next request. Same class as the
superuser-bypasses-RLS bug fixed in Phase 4.

## `SELECT … FOR UPDATE` (row locking)
**Simple.** Lock the rows you read so nobody else can change them until you
commit.

**In LedgerMind.** `_SQL_LOCK_LATEST` in `db_loader.py`, before deciding whether
to retire the existing `is_latest` row.

**Why we need it.** Two concurrent ingests of the same metric could both see
"no current row" and both insert — the 142-row duplicate incident.

**Note the sibling.** `_SQL_PEEK_LATEST` is the identical predicate **without**
`FOR UPDATE`, for the dry-run preview: it must classify without taking locks.
The two are kept adjacent precisely so they cannot drift.

## `IS NOT DISTINCT FROM`
**Simple.** Like `=`, but `NULL IS NOT DISTINCT FROM NULL` is **true**, whereas
`NULL = NULL` is `NULL`.

**In LedgerMind.** `quarter IS NOT DISTINCT FROM %(quarter)s` — annual rows have
`quarter = NULL`, and plain `=` would never match them.

**Common misconception.** "`NULL = NULL` is true." It is not. This is the single
most common SQL bug involving nullable business keys.

## CAGR
**Simple.** The constant annual growth rate that would take you from a starting
value to an ending value over n years.

**In LedgerMind.** `_compute_cagr()` — **in Python**, over values fetched by
SQL. Requires ≥2 data points and a positive start value; otherwise it returns a
structured error rather than a number.

---

# D. Web, API and platform

## API endpoint
**Simple.** One URL + method the server responds to.

**In LedgerMind.** `POST /auth/login`, `POST /api/query`,
`POST /api/query/stream`, `GET /api/metrics`, `POST /api/documents/upload`,
`GET /api/documents/pending`, `GET /health`.

## JWT (JSON Web Token)
**Simple.** A signed blob containing claims. The server verifies the signature
instead of looking the session up in a database.

**In LedgerMind.** HS256, 2-hour expiry, claims `sub` / `tenant_id` / `role`.

**Common misconception.** "JWTs are encrypted." They are **signed**, not
encrypted — anyone can read the payload. Never put a secret in one.

## RBAC (Role-Based Access Control)
**Simple.** Permissions attach to roles; users get roles.

**In LedgerMind.** `viewer < analyst < admin`, enforced twice: at the route
(`require_role`) and at the field (`role_filtered_response`).

**Worth stealing:** the field-level filter **fails closed** — an unrecognised
role gets the *most* restrictive payload, not the least.

## RLS (Row-Level Security)
**Simple.** The database itself refuses to return rows you are not allowed to
see, no matter what your query says.

**In LedgerMind.** Policies on `documents`, `financials`, `audit_log`, `FORCE`d
so the table owner is covered too.

**Common misconception.** "RLS errors when you forget to set the tenant." It
returns **zero rows**. Silent. That has repeatedly been misread as "the data is
missing".

## Middleware
**Simple.** Code that wraps every request.

**In LedgerMind.** Only `CORSMiddleware`. Auth is a **dependency**, not
middleware — see below.

## Dependency Injection
**Simple.** A function declares what it needs; the framework builds it and
passes it in, instead of the function constructing it itself.

**In LedgerMind.**

```python
async def execute_query(payload: QueryRequest,
                        current_user: dict = Depends(get_current_user)):
```

FastAPI sees `Depends(get_current_user)`, runs that function first, and passes
its return value in. If it raises a 401, the endpoint body never runs.

**Why we need it.** Auth is declared *once per route* and cannot be forgotten
inside the body. It also makes the endpoint testable — swap the dependency, no
token needed.

**Common misconception.** "DI is an enterprise pattern for big Java apps." It is
just "pass what you need in rather than making it inside". `require_role("admin")`
is a *dependency factory*: it returns a checker function configured with the
minimum role.

## SSE (Server-Sent Events)
**Simple.** A long-lived HTTP response over which the server pushes named events
until it closes. One direction only.

**In LedgerMind.** `POST /api/query/stream` emits `start`, one `node` per graph
node, then `complete` (or `error`).

**Why not WebSockets.** The traffic is one-directional and short-lived; SSE is
plain HTTP and needs no protocol upgrade.

**Why not `EventSource`** (the browser's built-in SSE client)? It is **GET-only
and cannot set an `Authorization` header**, and moving the JWT into a query
string would put it in server access logs and browser history. Hence
`fetch` + `ReadableStream` (`lib/api.ts:153-163`).

## Graceful degradation
**Simple.** Lose a feature, not the system.

**In LedgerMind.** Cohere fails → local reranker. Gemini fails → Groq. Both
fail → raw excerpts **plus** `error=synthesis_unavailable` and cleared provider
attribution. Audit write fails → the answer still ships.

**The important subtlety.** Degradation must be **visible**. A total LLM outage
that returns an apology string while `confidence_tier` still reads "high" is
worse than a failure — it is a lie. That exact bug is why `SynthesisOutcome`
carries a three-valued `status` instead of `provider is None`.

## Health check
**In LedgerMind.** `GET /health` probes Postgres, Redis and Qdrant and returns
`healthy` or `degraded` with per-service detail. Poll it after `docker compose
up -d` — compose returns when the container *starts*, not when uvicorn serves.

## Celery / message broker / worker
**Simple.** A queue plus a pool of processes that consume it, so slow work
happens outside the request.

**In LedgerMind.** Celery with Redis as the broker. **But note:** the upload
endpoint does *not* dispatch a task — ingestion is triggered by
`scripts/process_pending_uploads.py` because loading the embedding model
OOM-killed the 512 MB web tier.

---

# E. Concepts specific to this project

## `QueryState`
The `TypedDict` (`app/engines/state.py:66`) that flows through all eight graph
nodes. **The single most important type in the codebase.** ~40 fields grouped by
which node writes them. Every node takes it and returns it, mutated.

## Node / conditional edge (LangGraph)
A **node** is a function `QueryState -> QueryState`. A **conditional edge** is a
function `QueryState -> str` whose return value names the next node.
`route_after_router` is the whole routing decision, in 12 lines.

## Path
Which engine handles the query: `semantic`, `quantitative`, or `cross`. Chosen
by the router, overridable by the UI (`enforce_path`), and recorded in
`audit_log.query_path`.

## `sql_verified`
The flag meaning *this number came out of the database and satisfied the row-count
verification for its operation*. It is what the UI renders as a ✓, so **anything
that lets an unverified number carry it is a critical bug** — the recurring
theme behind Stages 0, 0b and 0c.

## Guard (Stage 0 / 0b / 0c)
A deterministic regex check over the **raw user query**, run **before** any LLM
call, that refuses rather than letting the model substitute a metric it can
compute for the one you asked about.

- **Stage 0** — derived metrics (EBITDA): no formula compiler exists.
- **Stage 0b** — registered but not DSL-exposed metrics.
- **Stage 0c** — cross path only: the query names *no* metric at all.

**Why the raw query.** By the time the DSL exists, the user's actual intent has
been overwritten by whatever the model chose. The raw query is the only place it
still exists.

## Confidence tier
`high` / `medium` / `low`, derived from the **top reranker score** against the
threshold pair matching that score's backend. It gates behaviour: `low` refuses,
`medium` triggers CRAG.

**What it does not mean.** It reflects **retrieval** quality, computed before any
answer text exists. `eval_runner.py`'s own header warns that `confidence_tier`
alone cannot be trusted to signal "did we find the right content" — only whether
the system was willing to answer. That is why a second, post-generation refusal
detector exists.

## Citation
A `Citation` TypedDict — `chunk_id`, `doc_id`, `page_number`, `company`,
`fiscal_year`, `financial_type`, `filing_date`, `reranker_score`,
`text_preview`. Built from the chunks the model actually received.

**The rule this project learned the hard way:** `retrieved_chunks` and
`citations` must never diverge. If the model read it, the user must be able to
check it.

## Golden dataset
91 questions across four JSON files in `golden_dataset/`, each with expected
path, tier, value, keywords. The regression baseline for the whole system.
**Editing an expectation to make a test pass is explicitly forbidden**
(`CLAUDE.md` §1.4) — PQ012 even carries a `known_deliberate_failure` field.

## Financial type
`consolidated` (parent + subsidiaries) vs `standalone` (parent only). **A
mandatory dimension on every financial fact** — the same company reports both,
and mixing them silently produces a wrong answer that looks right. Default is
always `consolidated`; `standalone` only when explicitly requested.

## Fiscal year (Indian)
April → March. FY26 = April 2025 → March 2026. Q1 = Apr–Jun, Q4 = Jan–Mar.
`_date_to_period()` in `financial_extractor.py` implements the mapping.

## Crore
10,000,000 (10 million). Indian filings report in crore, and `unit="crore_inr"`
is currently **hardcoded** at extraction — audit finding F3 (CAVEAT-005).

## Restatement vs parser correction
See `is_latest` above. The distinction is one of the sharper pieces of thinking
in this codebase: *did the filing change, or did our reading of it change?*
