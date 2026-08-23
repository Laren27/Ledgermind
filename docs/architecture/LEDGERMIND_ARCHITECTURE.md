# LedgerMind — Architecture Map (as built)

**Status of this document.** Written 2026-08-20 by reading the source tree, not
the blueprint. Every claim below names the file and (where useful) the line it
came from. Where the code and `docs/ARCHITECTURE.md` (the original blueprint)
disagree, **the code wins and this file records what the code does**.

Three companion documents already exist and are not duplicated here:

| Document | What it holds |
|---|---|
| `docs/ARCHITECTURE.md` | The original blueprint, preserved verbatim. A historical record, **not** a description of the system. |
| `docs/IMPLEMENTATION_DELTAS.md` | Every divergence from that blueprint, dated, with measurements. The authoritative record of *why* things differ. |
| `docs/audit/repo_audit_20260811.md` | 13 findings (F1–F13) ranked by blast radius. |

This file is the **map**: what the parts are, how a request actually flows, and
where to look. `CAPABILITY_MATRIX.md` beside it says what is real vs. planned.

---

## 1. What LedgerMind is

LedgerMind answers natural-language questions about Indian public-company
filings — SEBI quarterly results, annual reports, earnings-call transcripts —
and returns an answer that is **traceable to a page of a specific PDF, or a
row of a database, or a refusal**.

Corpus today: **ETERNAL** (formerly Zomato), **TITAN**, **PAYTM**. Five source
PDFs live in `docs/raw/`.

The system is not trying to be a chatbot. Its stated objective (`CLAUDE.md`) is:

> correctness, reliability, explainability, and auditability. **A wrong answer
> with a ✓ tick is worse than a refusal.**

Almost every unusual design choice in this codebase follows from that one
sentence. If you understand nothing else, understand this: the system is
built to *decline*, loudly, rather than to answer plausibly.

---

## 2. The problem it actually solves

Naïve "chat with your PDF" RAG fails on financial documents in three specific
ways. LedgerMind is structured around defeating each one.

**Problem 1 — LLMs are bad at arithmetic and worse at admitting it.**
Ask an LLM "what was Eternal's revenue growth from FY25 to FY26" over retrieved
text and it will confidently produce a percentage that is subtly wrong. There is
no way to check it and no way to know it happened.
→ **LedgerMind's answer:** numbers never come from a language model. They come
from `SELECT value FROM financials`. See §6, the quantitative path.

**Problem 2 — retrieval that returns *something* always returns something.**
A vector search over an unfiltered corpus will always return its top-5 nearest
chunks, even when the right document is not in the corpus at all. Those chunks
are topically plausible, the model writes a fluent answer over them, and the
answer cites real pages of the wrong company's filing.
→ **LedgerMind's answer:** hard metadata filters (`tenant_id`, `company`,
`fiscal_year`, `is_latest`) applied *inside* each retrieval leg, plus a router
that refuses when it cannot resolve the company. See §5 and §7.

**Problem 3 — a document says one thing and its own numbers say another.**
Management commentary says "profitability improved"; the P&L says PAT fell.
A summariser reports the commentary.
→ **LedgerMind's answer:** the cross-examination path runs both halves and
detects the disagreement deterministically. See §8.

---

## 3. The stack, in one table

| Layer | Technology | Where |
|---|---|---|
| Frontend | Next.js (App Router) + React + Tailwind, TypeScript | `frontend/` |
| API | FastAPI (Python), Uvicorn | `backend/app/main.py` |
| Pipeline orchestration | **LangGraph** `StateGraph` over a `TypedDict` | `backend/app/engines/graph.py` |
| Relational store | PostgreSQL 15, **raw psycopg2** (no ORM) | `sql/init.sql`, `sql/migrations/` |
| Vector store | **Qdrant** (Cloud), named dense + sparse vectors | `backend/app/ingestion/qdrant_writer.py` |
| Embeddings | fastembed ONNX — `BAAI/bge-small-en-v1.5` (384-d dense) | `backend/app/ingestion/embedder.py` |
| Lexical retrieval | fastembed `Qdrant/bm25` **sparse vectors**, fused by Qdrant | same file |
| Reranking | **Cohere `rerank-english-v3.0`** primary, local ONNX `ms-marco-MiniLM-L-6-v2` fallback | `backend/app/engines/retriever.py:364` |
| LLM | **Gemini** primary, **Groq** failover, one shared client | `backend/app/llm/client.py` |
| Auth | JWT (HS256, PyJWT) + bcrypt, role in the token | `backend/app/core/security.py` |
| Async jobs | Celery + Redis (broker) | `backend/app/worker.py` |
| Blob storage | Supabase Storage over raw httpx | `backend/app/ingestion/storage.py` |
| Local dev | `docker compose up -d --build` | `docker-compose.yml` |

---

## 4. High-level architecture

```text
                        ┌─────────────────────────────────┐
   BROWSER              │  Next.js  (frontend/app/page.tsx)│
                        │  localStorage JWT, SSE reader    │
                        └───────────────┬─────────────────┘
                                        │ HTTPS + Bearer JWT
                        ┌───────────────▼─────────────────┐
   API                  │  FastAPI  (app/main.py)          │
                        │  /auth/login  /api/query          │
                        │  /api/query/stream  /api/metrics  │
                        │  /api/documents/*   /health       │
                        └───────────────┬─────────────────┘
                                        │  QueryState (TypedDict)
                        ┌───────────────▼─────────────────┐
   PIPELINE             │  LangGraph  (engines/graph.py)   │
                        │  8 nodes, 2 conditional edges     │
                        └───────────────┬─────────────────┘
             ┌──────────────────────────┼──────────────────────────┐
             ▼                          ▼                          ▼
     Qdrant Cloud              PostgreSQL (RLS)              Gemini / Groq
   (chunks + vectors)      (financials, audit_log,        (routing, DSL,
                            documents, users)              synthesis only)
```

**The whole system is one graph.** There is no service mesh, no message bus in
the request path, no controller/service/repository layering. A request builds
one dict, that dict is passed through eight functions, and each function
mutates it. That dict is `QueryState` (`app/engines/state.py:66`), and it is the
single most important type in the codebase.

---

## 5. The actual request lifecycle

Verified by reading `app/api/query.py` → `app/engines/graph.py` → each node.

```text
User types a question in QueryDock
        │
        ▼
frontend/lib/api.ts :: submitQueryStreaming()
        │   POST /api/query/stream   Authorization: Bearer <jwt>
        │   (falls back to submitQuery() → POST /api/query on ANY stream failure)
        ▼
app/auth/dependencies.py :: get_current_user()      ← JWT decoded, 401 on failure
        │   yields {user_id, tenant_id, role}
        ▼
app/api/query.py :: execute_query_stream()
        │   tenant_id comes from the VERIFIED TOKEN, not the request body*
        ▼
app/engines/state.py :: make_initial_state()        ← the dict is born here
        │
        ▼
app/engines/graph.py :: get_graph().astream(state)
        │
        ├─ 1. prompt_shield  (engines/prompt_shield.py)  pure regex, no network
        │        └─ blocked? ──────────────────────────────────► audit_writer ─► END
        │
        ├─ 2. router  (engines/router.py)                 ← LLM call #1
        │        extracts company / fiscal_year / quarter / financial_type
        │        classifies path ∈ {semantic, quantitative, cross}
        │        └─ refused? (unknown company, or no LLM reachable) ─► audit_writer ─► END
        │
        ├─ 3a. quant_engine    (path = quantitative)      ← LLM call #2 (DSL only)
        │  3b. semantic_engine (path = semantic)          ← no LLM call here
        │  3c. cross_engine    (path = cross)             ← runs 3a THEN 3b
        │
        ├─ 4. confidence  (engines/confidence.py)         caps only, never raises
        │
        ├─ 5. response_generator  (engines/response_generator.py)  ← LLM call #3
        │        quantitative → TEMPLATE, no LLM
        │        semantic     → Gemini synthesis over chunks
        │        cross        → synthesis + deterministic reconciliation
        │
        └─ 6. audit_writer  (engines/audit_writer.py)     INSERT INTO audit_log
                 │
                 ▼
        api/response_shaping.py :: role_filtered_response(state, role)
                 │   viewer / analyst / admin see different field sets
                 ▼
        SSE "complete" event  →  frontend composeDocumentBody()
```

\* `QueryRequest` does accept an optional `tenant_id` in the body and
`payload.tenant_id or current_user["tenant_id"]` prefers it
(`api/query.py:110`). That is a real caveat — see `CAVEATS.md` **CAVEAT-001**.

### Two transports, one pipeline

`/api/query` and `/api/query/stream` share the state factory, the graph, and
`role_filtered_response`. The streaming endpoint exists so the UI can show node
boundaries as they happen; the node names come from LangGraph's own
`astream(stream_mode="updates")`, so **a node cannot forget to report itself**
(`api/query.py:150-153`).

Two details worth internalising:

- The graph runs in its own `asyncio` task (`_run_graph`) with an **unbounded**
  queue. If the browser disconnects mid-query, the generator dies but the
  pipeline finishes — so `audit_writer` still writes its row
  (`api/query.py:168-220`). Auditability outranks resource tidiness.
- Errors after the first byte cannot be a 500 (headers are gone), so they
  travel as an SSE `error` event.

---

## 6. Path 2 — the quantitative path (numbers)

This is the path that makes LedgerMind a financial system rather than a
document search engine.

```text
"What was ETERNAL's consolidated revenue for FY26?"
        │
        ▼
quant_engine_node                                (engines/quant_engine.py:600)
        │
        ├─ Stage 0   derived-metric guard    ── query names "EBITDA"? refuse.
        ├─ Stage 0b  unqueryable-metric guard ── query names a known-but-not-
        │                                        DSL-exposed metric? refuse.
        │            (both are regex over the RAW query, BEFORE any LLM call)
        │
        ├─ Stage 1   _generate_dsl()          ← LLM emits a JSON DSL object,
        │            up to 2 attempts with a repair_hint on failure
        │            {metric, entity, fiscal_year, quarter, financial_type,
        │             operation, comparison_entity, comparison_period}
        │
        ├─ period-assumption guard  ── if the query named NO period and the LLM
        │            invented one, replace it with MAX(fiscal_year) from the
        │            corpus and set period_assumed=True (disclosed, not hidden)
        │
        ├─ Stage 2   compile_dsl()             (engines/dsl_compiler.py:180)
        │            deterministic Python → parameterised SQL. The LLM never
        │            sees the schema and never writes SQL.
        │
        ├─ Stage 3   _execute_sql()  →  SET LOCAL app.tenant_id; SELECT …
        │
        ├─ Stage 4   verify row counts
        │            point_in_time: exactly 1 row. 0 → no_data_found.
        │                                          >1 → ambiguous_result (refuse)
        │
        └─ Stage 5   derived arithmetic IN PYTHON
                     _compute_yoy_growth / _compute_comparison /
                     _compute_growth_comparison / _compute_cagr
```

**The DSL is the whole trick.** The model's job is reduced from "answer a
financial question" to "fill in eight fields, using strings from this list".
Everything after that is deterministic Python that can be unit-tested. The five
supported operations live in `OPERATION_REGISTRY` (`dsl_compiler.py:18`):
`point_in_time`, `yoy_growth`, `comparison`, `cagr`, `growth_comparison`.

`sql_verified=True` is set only when the SQL actually returned what the
operation required. That flag is what the UI renders as a ✓.

---

## 7. Path 1 — the semantic path (text)

```text
"What risk factors does Eternal disclose in Q4FY26?"
        │
        ▼
semantic_engine_node                          (engines/semantic_engine.py:272)
        │
        ▼
retrieve_and_rerank()                         (engines/retriever.py:454)
        │
        ├─ _encode_dense(query)   →  384-d vector   (bge-small-en-v1.5)
        ├─ _encode_sparse(query)  →  BM25 SparseVector
        │
        ├─ client.query_points(
        │      prefetch=[ Prefetch(dense , limit=20, filter=F),
        │                 Prefetch(sparse, limit=20, filter=F) ],
        │      query=FusionQuery(fusion=Fusion.RRF) )
        │
        │   F = tenant_id AND is_latest AND [company] AND [fiscal_year]
        │       AND [quarter] AND (financial_type OR "unknown")
        │   ── the filter is inside EACH LEG, not at fusion level ──
        │
        ├─ rerank()  → Cohere rerank-english-v3.0 over ALL 20 candidates
        │              (falls back to local ONNX cross-encoder on failure)
        │
        ├─ _deduplicate_near_identical()  → drop chunks with ≥70% token overlap
        │                                    with a higher-ranked chunk
        └─ top 5
        │
        ▼
_score_confidence()  → (score, tier) using the threshold pair that MATCHES
                        the backend that actually scored (Cohere 0.5/0.15 vs
                        local −4.5/−7.5). Reading a Cohere score against local
                        thresholds classified everything "high" — real bug,
                        documented at semantic_engine.py:52.
        │
        ├─ tier = low     → refuse, empty citations, error=low_confidence_refusal
        ├─ tier = medium  → CRAG retry: drop `quarter`, then drop `fiscal_year`
        └─ tier = high    → build citations, proceed
```

Synthesis happens **later**, in `response_generator`, not here. The separation
matters: `semantic_engine` scores *retrieval*; `response_generator` scores what
the model did with it. A high-scoring retrieval can still yield a refusal once
the model reads the excerpts, which is why there is a second, post-generation
refusal detector (`response_generator.py:70`).

---

## 8. Path 3 — cross-examination

The most interesting path, and the one with the most scar tissue.

```text
"Does management commentary align with Eternal's actual FY26 PAT?"
        │
        ▼
cross_engine_node                              (engines/cross_engine.py:103)
        │
        ├─ resolve_parent_entity()   BLINKIT/HYPERPURE → ETERNAL
        │
        ├─ Stage 0c  metric-anchor guard: does the raw query name ANY known
        │            metric? If not, skip the quant half entirely — otherwise
        │            the model invents a metric and it gets stamped ✓ verified.
        │
        ├─ 1. quant_engine_node(...)   ← RUNS FIRST, deliberately
        ├─ 2. semantic_engine_node(...) ← the verified figure is then injected
        │                                 into the synthesis context as fact
        ├─ 3. detect_contradictions()   (engines/contradiction.py)
        │        narrative chunks only, metric-proximity anchored, ±5% tolerance
        │
        └─ 4. combined confidence = the WEAKER of the two halves
        │
        ▼
response_generator :: _reconcile_cross()    ← FINAL AUTHORITY for this path
        4-way availability quadrant, no judgment calls:
          qual ok  + quant ok      → both halves + contradiction block
          qual refused + quant ok  → suppress the false global negative
          qual ok  + quant absent  → disclose the gap IF a metric was identified
          qual refused + quant absent → one refusal, one voice
```

Why quant runs first is worth reading in full at `cross_engine.py:131-150`.
The short version: two earlier attempts tried to stop the model from saying
"the documents do not contain the PAT figure" — an instruction to withhold
something *true*. Both failed. The working fix made the statement *false* by
putting the verified figure in context before the model wrote anything.

---

## 9. Ingestion — how a PDF becomes queryable

Ingestion is **offline and operator-triggered**. The upload endpoint stores the
file and records a `pending_uploads` row; it does **not** start ingestion
(loading the embedding model in the web process OOM-killed Render's 512 MB tier
— `api/documents.py:10-19`).

```text
PDF
 │
 ├─ [gate]      app/ingestion/gate.py
 │              deterministic keyword scoring over the first ~6000 chars.
 │              Needs score ≥6 across ≥2 signal categories. Rejects a CV.
 │
 ▼  scripts/process_pending_uploads.py  →  pipeline._run_ingestion()
 │
 ├─ [1] parse_pdf()          pdfplumber → list[PageBlock]  (ONE BLOCK PER PAGE)
 ├─ [2] detect_sections()    find "statement of consolidated/standalone" markers
 │      register_sections()  → 1-2 rows in `documents`, one doc_id per section
 ├─ [3] classify_blocks()    intersection of structure × location × content
 │                           → FINANCIAL_STATEMENT / TABLE / RISK_DISCLOSURE /
 │                             MANAGEMENT_DISCUSSION / TEXT / UNKNOWN
 ├─ [4] chunk_blocks()       tables never split; prose recursively split;
 │                           TRANSCRIPTS split on SPEAKER TURNS with a
 │                           management roster parsed from page 1
 ├─ [5] embed_chunks()       dense (384-d) + sparse (BM25), BATCH_SIZE=8
 ├─ [6] write_chunks()       Qdrant upsert, point id = deterministic chunk_id
 └─ [7] extract_all_financial_records()  →  load_financial_records()
            positional column detection → rows → FinancialRecord →
            _compute_derived_totals() → validate_financial_identities() →
            PostgreSQL `financials` with is_latest / restatement handling
```

Two structural facts that explain a lot of downstream behaviour:

1. **`parse_pdf` emits one block per page.** That is why the transcript chunker
   needs a "speaker still talking at the end of this page" thread
   (`chunker.py:195`) — a turn spanning a page break would otherwise lose its
   attribution.
2. **Only FINANCIAL_STATEMENT blocks inside a detected section get a real
   `financial_type`.** Everything else stays `"unknown"` by design
   (`chunker.py:363`). That is why the retrieval filter has to admit `unknown`
   alongside the requested type — and why that filter is nearly inert today
   (audit finding **F7**).

---

## 10. Data model

### PostgreSQL (`sql/init.sql` + 17 migrations)

> `sql/` holds **19 `.sql` files**: `init.sql`, `seed.sql`, and **17** numbered
> migrations under `sql/migrations/`. The earlier "19 migrations" here counted
> the directory, not the migrations. Numbering is not contiguous — it starts at
> `003` and includes a `007a` — so neither the file count nor the highest number
> is a reliable migration count. Count the files in `sql/migrations/`.

| Table | Purpose | Key columns |
|---|---|---|
| `tenants` | tenant registry | `tenant_id UUID PK` |
| `users` | login + role | `email UNIQUE`, `role CHECK IN (admin, analyst, viewer)`, `password_hash` |
| `documents` | one row per **section** of a PDF | `doc_id`, `financial_type`, `filing_date`, `sha256_checksum UNIQUE`, `ingestion_state` |
| `financials` | the numbers | `company, fiscal_year, quarter, financial_type, metric, value, unit, filing_date, is_latest` |
| `audit_log` | append-only lineage | `query_path`, `retrieved_chunk_ids[]`, `vector_scores[]`, `reranker_scores[]`, `dsl_generated JSONB`, `sql_executed`, `llm_provider`, `llm_model` |
| `pending_uploads` | upload → ingest handoff | `storage_key`, `status` |
| `schema_migrations` | applied-migration ledger | (migration 012) |

The single most important index:

```sql
CREATE UNIQUE INDEX uq_financials_latest
    ON financials (tenant_id, company, fiscal_year, quarter, financial_type, metric)
    WHERE is_latest = TRUE;
```

A **partial unique index**. It makes "there is exactly one current value for
this company/period/metric" a database-enforced invariant, while still allowing
unlimited superseded history rows. Restatements are modelled by flipping the old
row's `is_latest` to `FALSE`, never by deleting it.

RLS is enabled **and `FORCE`d** on `documents`, `financials`, `audit_log`, so
even the table owner is subject to it. Every policy has the same shape:

```sql
CASE WHEN coalesce(current_setting('app.tenant_id', TRUE), '') = '' THEN FALSE
     ELSE tenant_id = current_setting('app.tenant_id', TRUE)::UUID END
```

The `CASE` is load-bearing: `AND` is not a short-circuit operator in SQL, so a
naïve `setting <> '' AND tenant_id = setting::uuid` can still evaluate the cast
and error on an empty GUC. See `IMPLEMENTATION_DELTAS.md` §14.

**Consequence you must remember:** forgetting `SET app.tenant_id` returns
**zero rows, not an error**. Zero rows is not evidence that data is missing.

### Qdrant (`ledgermind_chunks`)

One collection, two named vectors per point: `dense` (384-d, cosine) and
`sparse` (BM25). Point id = the deterministic chunk UUID from
`hashlib.md5(f"{doc_id}:{page}:{position}:{text[:100]}")` — so re-ingesting the
same PDF overwrites cleanly instead of duplicating.

Payload carries the full `ChunkMetadata` plus the chunk text. Payload indexes
are created on every filterable field; without them, filtered queries are a
linear scan.

---

## 11. Security model (summary — full version in `docs/security/SECURITY_MODEL.md`)

Four layers, in the order a request meets them:

1. **JWT** — HS256, 2-hour expiry, carries `sub` / `tenant_id` / `role`.
2. **Route RBAC** — `require_role("admin")` on uploads and `/api/metrics`.
3. **Field-level RBAC** — `role_filtered_response()` **fails closed**: an
   unrecognised role gets the *viewer* payload, never the admin one.
4. **Postgres RLS** — `SET LOCAL app.tenant_id` per transaction, never a bare
   `SET` (a bare `SET` on a pooled connection leaks across requests).

Plus the **Prompt Shield** (`engines/prompt_shield.py`), which runs *before* the
router — pure regex, no network, blocking SEBI-non-compliant advice requests
("should I buy X") and instruction-override attempts. Its design principle is
worth stealing: *match the advice-request structure, not the word*, so "what did
Zomato buy?" passes while "should I buy Zomato?" blocks.

---

## 12. Data shapes, end to end

```text
INGESTION
  PDF file
    → PageBlock(page_number, content, block_type, table?)      pdf_parser
    → DocSection(financial_type, page_start, page_end, doc_id)  document_classifier
    → PageBlock.block_type filled in                            section_classifier
    → Chunk(chunk_id, text, ChunkMetadata)                      chunker
    → EmbeddedChunk(chunk, dense_vector, sparse_indices, values) embedder
    → Qdrant PointStruct                                        qdrant_writer
    ↘ FinancialRecord(company, fy, quarter, ft, metric, value)  financial_extractor
    → rows in `financials`                                      db_loader

QUERY
  str (the question)
    → QueryRequest        {query, tenant_id?, execution_context?}   api/query.py
    → QueryState          ~40 fields, one dict, mutated in place    engines/state.py
        ├─ ChunkResult[]  retrieval output, carries reranker_backend
        ├─ Citation[]     what the UI renders as evidence
        ├─ DSLObject      the compiled query intent
        └─ ContradictionFlag[]
    → role_filtered_response(dict, role)  →  JSON
    → QueryResponse (TypeScript)                                 frontend/lib/api.ts
    → React tree                                                 composeDocumentBody()
```

---

## 13. Frontend

`frontend/app/page.tsx` is a single client component holding all state. The
metaphor is a **working paper**: each answer becomes a numbered sheet you can
page through (`PageNavigator`), with a `WorkingPaperHeader` carrying the
company/period/WP-ref, an `ExecutionTrace` fed by the SSE node events, and an
`EvidenceList` of citations.

Two rules are enforced structurally:

- **`composeDocumentBody()` is the ONLY function that knows which engine
  produced the data.** Every document component below it receives plain props.
- **Zero UI-hallucination mandate.** No badge, count or citation number exists
  as static copy — each is wired to a real backend field, and where a field is
  absent the UI *omits* rather than substitutes. `buildCitationItems` dropping
  the `(unknown)` financial-type tag is the canonical example
  (`page.tsx:47-60`).

---

## 14. Where to look — a file index

| I want to understand… | Read |
|---|---|
| the shared state every node mutates | `app/engines/state.py` |
| how the graph is wired | `app/engines/graph.py` (132 lines, read it all) |
| routing + entity extraction + refusal | `app/engines/router.py` |
| hybrid retrieval + RRF + rerank + dedup | `app/engines/retriever.py` |
| CRAG and confidence tiers | `app/engines/semantic_engine.py` |
| DSL → SQL | `app/engines/dsl_compiler.py` |
| SQL execution, verification, derived math | `app/engines/quant_engine.py` |
| answer assembly + the cross quadrants | `app/engines/response_generator.py` |
| provider failover and timeouts | `app/llm/client.py` |
| every metric the system knows | `app/metrics/registry.py` |
| PDF → rows | `app/ingestion/financial_extractor.py` |
| PDF → chunks | `app/ingestion/chunker.py` |
| restatement / is_latest logic | `app/ingestion/db_loader.py:184` (`classify_upsert`) |
| schema, RLS, indexes | `sql/init.sql` |

---

## 15. What this document does not claim

- It does not claim the system is correct. `CAPABILITY_MATRIX.md` and
  `../engineering/CAVEATS.md` list what is broken, unmeasured or assumed.
- It does not restate the measurements in `IMPLEMENTATION_DELTAS.md`. That file
  is the evidence; this one is the map.
- Anything I could not determine from the code is marked as such in
  `CAVEATS.md` rather than guessed at here.
