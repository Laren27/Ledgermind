# 00 — The LedgerMind Learning Map

**Start here.** This file is the map; it is not the course. It answers *what is
this system, what is it built from, what depends on what, and in what order can
it be learned*. The course itself is
[`LEDGERMIND_MASTER_COURSE.md`](LEDGERMIND_MASTER_COURSE.md).

Written 2026-08-23 by reading the source tree at `017d97e`. **The code is the
authority.** Where a README, a docstring or the original blueprint disagrees
with the implementation, this file records what the implementation does and says
so.

| If you want… | Read |
|---|---|
| To be productive in a day | [`FIRST_DAY_GUIDE.md`](FIRST_DAY_GUIDE.md) |
| To understand it properly | [`LEDGERMIND_MASTER_COURSE.md`](LEDGERMIND_MASTER_COURSE.md), 47 days |
| A word defined | [`GLOSSARY.md`](GLOSSARY.md) |
| To know what is real vs. planned | [`../architecture/CAPABILITY_MATRIX.md`](../architecture/CAPABILITY_MATRIX.md) |
| To know why a decision was made | [`../architecture/ENGINEERING_DECISIONS.md`](../architecture/ENGINEERING_DECISIONS.md) |
| To know what is wrong | [`../engineering/CAVEATS.md`](../engineering/CAVEATS.md) |
| To know what is *unknown* | [`../engineering/KNOWN_UNKNOWNS.md`](../engineering/KNOWN_UNKNOWNS.md) |

---

# A. What LedgerMind is

LedgerMind answers natural-language questions about Indian public-company
filings — SEBI quarterly results, annual reports, earnings-call transcripts —
and returns an answer that is **traceable to a page of a specific PDF, or to a
figure verified by SQL against an extracted financial statement**.

Three companies are in the corpus: **ETERNAL** (formerly Zomato), **TITAN**,
**PAYTM**.

The thing to understand before anything else is not what it does but what it
*refuses* to do. From `CLAUDE.md`:

> A wrong answer with a ✓ tick is worse than a refusal.

Almost every design choice that looks over-engineered follows from that sentence.
Three regex guards before an LLM call, two threshold pairs for one score, a pure
function whose only job is to be called by both a writer and its dry-run — each
one prevents a *specific, measured* wrong answer. When something looks
complicated, ask **"what wrong answer does this prevent?"** rather than "why is
this so complicated?". There is a documented answer every time.

---

# B. What problem it solves

## The business problem

A financial analyst asking *"what was Eternal's revenue in FY26?"* needs the
number to be **right**, and needs to be able to **show where it came from**.
Neither a search engine nor a chatbot delivers both. A search engine returns
pages and leaves the synthesis to you. A chatbot synthesises fluently and cannot
prove anything.

Regulatory context adds a second constraint: under SEBI rules, a tool that
answers *"should I buy this?"* is giving investment advice. LedgerMind must
refuse that class of question structurally, not by tone.

## The technical problem

The natural instinct is "put the filings in a vector database and let RAG handle
it." That fails on the questions that matter most:

| Question | Why plain RAG fails |
|---|---|
| *"What was Eternal's FY26 revenue?"* | An LLM reading a retrieved table transcribes a number. Nothing verifies it. Nothing catches a digit dropped by OCR |
| *"Who grew revenue faster, Eternal or Paytm?"* | Requires arithmetic over two periods and two entities. LLM arithmetic is fluent and unverifiable |
| *"Does management's commentary match the actual PAT?"* | Requires both a retrieved narrative **and** a verified figure, compared |
| *"What are Reliance's revenue drivers?"* | Reliance is not in the corpus. The correct answer is a refusal, not a confident answer built from other issuers' pages |

LedgerMind's answer is **three paths, chosen per query**, with the LLM
structurally barred from arithmetic:

```
                  ┌─────────── semantic ──── retrieval + synthesis, cited
   question ──────┼─────────── quantitative ─ DSL → SQL → Python arithmetic
                  └─────────── cross ──────── both, then contradiction detection
```

**The invariant:** the LLM emits an eight-field DSL object. A deterministic
Python compiler turns it into parameterised SQL. All arithmetic happens in Python
over values fetched from Postgres. *The LLM never writes SQL and never sees the
schema.*

---

# C. The architecture, as built

```
┌────────────────────────────────────────────────────────────────────────┐
│  BROWSER — Next.js 14 App Router (Vercel)                              │
│  page.tsx holds all state · composeDocumentBody() is the ONLY           │
│  path-aware function · lib/api.ts consumes SSE via fetch+ReadableStream │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ HTTPS + Authorization: Bearer <JWT>
┌───────────────────────────────▼────────────────────────────────────────┐
│  FastAPI (Render)                                                      │
│    /auth/login          bcrypt → HS256 JWT (2h)                        │
│    /api/query           blocking                                       │
│    /api/query/stream    SSE — same pipeline, different transport        │
│    /api/documents/*     upload (admin), pending list                   │
│    /api/metrics         dashboard aggregates                           │
│    /health              postgres · redis · qdrant                      │
│                                                                        │
│  get_current_user (Depends) → require_role → role_filtered_response    │
└───────────────────────────────┬────────────────────────────────────────┘
                                │  one QueryState dict, mutated in place
┌───────────────────────────────▼────────────────────────────────────────┐
│  LangGraph StateGraph  (compiled once, module singleton)               │
│                                                                        │
│   prompt_shield ──blocked───────────────────────────┐                  │
│        │                                             │                  │
│      router ──refused (F2)───────────────────────────┤                  │
│        │                                             │                  │
│        ├─ quantitative → quant_engine ──┐            │                  │
│        ├─ semantic  → semantic_engine ──┼→ confidence│                  │
│        └─ cross     → cross_engine ─────┘      │     │                  │
│                                                 ▼     │                  │
│                                       response_generator                 │
│                                                 │     │                  │
│                                                 ▼     ▼                  │
│                                            audit_writer → END           │
└──────┬──────────────────┬───────────────────┬──────────┬───────────────┘
       │                  │                   │          │
┌──────▼──────┐  ┌────────▼───────┐  ┌────────▼──────┐  ┌▼─────────────┐
│ Qdrant Cloud│  │  PostgreSQL    │  │ Gemini → Groq │  │ Cohere Rerank│
│ dense+sparse│  │  RLS per tenant│  │ (failover)    │  │ → ONNX local │
│ named vecs  │  │  append-only   │  │               │  │ (fallback)   │
│             │  │  audit_log     │  │               │  │              │
└─────────────┘  └────────────────┘  └───────────────┘  └──────────────┘

OFFLINE, never in the request path:
  PDF → pdf_parser → document_classifier → section_classifier
      → chunker → embedder → qdrant_writer          (text  → Qdrant)
      → financial_extractor → db_loader             (numbers → Postgres)
```

**Two properties worth naming now.**

**There is no service/repository layering.** A request creates one `TypedDict`
(`QueryState`) and passes it through up to eight functions, each mutating it and
returning it. That dict *is* the architecture. If you arrive from Spring or
Django you will look for layers that do not exist.

**Ingestion is offline and operator-triggered.** Not a stylistic choice: loading
the embedding model in-process OOM-killed Render's 512 MB tier, confirmed by
repeated `Exited with status 137`. Upload records a `pending_uploads` row;
`scripts/process_pending_uploads.py` does the work elsewhere.

---

# D. The technologies, and when you meet each

Every row: what it is here, why, where, what it needs first, and the day.

## Platform and runtime

| Technology | Why LedgerMind uses it | Where | Prerequisites | Day |
|---|---|---|---|---|
| **Docker Compose** | Seven services with one command; reproducible local stack | `docker-compose.yml` | processes, ports, env vars | **1**, 45 |
| **Python 3.11** | Backend language | `backend/` | — | 10–12 |
| **Git** | History as evidence, not just backup | `.git` | files, diffs | **2**, 40 |

## Web and API

| Technology | Why | Where | Prerequisites | Day |
|---|---|---|---|---|
| **HTTP** | The protocol everything above rides on | — | — | **4** |
| **FastAPI** | Async, typed, generates `/docs`, dependency injection | `app/main.py`, `app/api/` | HTTP, Python | **4**–6 |
| **Pydantic v2** | The request/response contract, and LLM output schemas | `auth/schemas.py`, `router.RouterResponse` | Python types | **5**, 18 |
| **SSE** | Node-by-node execution trace while the pipeline runs | `api/query.py`, `lib/api.ts` | HTTP, generators | **6** |
| **CORS** | Vercel preview domains must reach the Render API | `main.py` | HTTP headers | **5** |

## Identity

| Technology | Why | Where | Prerequisites | Day |
|---|---|---|---|---|
| **bcrypt** | Password hashing. Called **directly** — passlib reads `bcrypt.__about__` which was removed in 4.1 | `core/security.py` | hashing | **7** |
| **PyJWT (HS256)** | Stateless identity carrying `sub`, `tenant_id`, `role` | `core/security.py` | HTTP headers | **8** |
| **FastAPI `Depends`** | Auth runs before business logic, declaratively | `auth/dependencies.py` | FastAPI, functions | **8** |
| **RBAC** | viewer < analyst < admin, enforced at route **and** field level | `dependencies.require_role`, `api/response_shaping.py` | authn | **9** |

## Data

| Technology | Why | Where | Prerequisites | Day |
|---|---|---|---|---|
| **PostgreSQL 15** | The numbers must be exact and queryable | `sql/init.sql` | tables, SQL | **13** |
| **psycopg2 (raw SQL)** | No ORM — "SQLAlchemy adds nothing for flat record inserts" | everywhere DB is touched | SQL | 13–15 |
| **Row-Level Security** | Tenant isolation in the database, not in application `WHERE` | `init.sql` policies | transactions | **14** |
| **Partial unique index** | One `is_latest` row per business key, enforced by the DB | `uq_financials_latest` | indexes | **15** |
| **Supabase** | Managed Postgres + Storage for the upload handoff | `.env`, `ingestion/storage.py` | Postgres | 16, 41 |
| **Qdrant Cloud** | Hybrid dense+sparse with native RRF and payload pre-filtering | `qdrant_writer.py`, `retriever.py` | vectors | **21**, 27 |
| **Redis** | Celery broker **only**. The semantic cache was never built | `worker.py` | queues | 45 |

## Retrieval and LLM

| Technology | Why | Where | Prerequisites | Day |
|---|---|---|---|---|
| **bge-small-en-v1.5** (fastembed ONNX) | 384-dim dense embeddings, CPU-only; torch does not fit 512 MB | `embedder.py`, `retriever.py` | embeddings | **20** |
| **Qdrant/bm25** (fastembed) | Sparse vectors for exact terms — tickers, metric names | same | TF-IDF | **26** |
| **RRF** | Fuses two rankings whose scores are not comparable | `retriever.hybrid_search` | ranking | **27** |
| **Cohere `rerank-english-v3.0`** | Primary cross-encoder, 0 MB local RAM | `retriever.rerank` | bi vs cross-encoder | **28** |
| **`ms-marco-MiniLM-L-6-v2`** (ONNX) | Fallback reranker — **different score scale**, which is the point | same | as above | **28** |
| **Gemini** (`google-genai`) | Primary LLM; `response_schema` guarantees output shape | `llm/client.py` | LLM basics | **17**–19 |
| **Groq** | Failover. `json_object` only, so shape is validated locally | same | as above | **19** |
| **LangGraph** | The router is an inspectable state machine, not nested `if`s | `engines/graph.py` | TypedDict | **35** |

## Ingestion

| Technology | Why | Where | Prerequisites | Day |
|---|---|---|---|---|
| **pdfplumber** | Text *and* word-level positions — required to reconstruct table columns | `pdf_parser.py` | — | **22** |
| **Celery** | Ingestion off the request path | `worker.py`, `pipeline.py` | queues | 45 |

## Frontend

| Technology | Why | Where | Prerequisites | Day |
|---|---|---|---|---|
| **Next.js 14 (App Router)** | The working-paper layout needs real layout control | `frontend/app/` | React | **38** |
| **React 18** | Components, state, effects | `frontend/components/` | JS/TS | **38** |
| **TypeScript** | The API contract is checked at compile time | all `.ts`/`.tsx` | JS | **38** |
| **Tailwind** | Utility CSS | `globals.css` | CSS | 38 |

## Quality

| Technology | Why | Where | Prerequisites | Day |
|---|---|---|---|---|
| **pytest** | 194 pure-function tests, zero network, ~5s | `backend/tests/` | Python | **43** |
| **Golden dataset** | 91 questions with **exact expected values** — pass/fail, not a 0–1 score | `golden_dataset/` | the paths | **43** |

---

# E. Concept dependency graph

**Read an arrow as "you cannot understand the thing below until you understand
the thing above."** This graph, not the folder layout, determines the course
order.

```
                 processes · ports · env vars · containers
                                   │
                 ┌─────────────────┼─────────────────┐
                 ▼                 ▼                 ▼
              Docker         Python runtime         Git
                 └─────────────────┼─────────────────┘
                                   ▼
                    ONE SHARED DICT (QueryState)          ◄── the keystone
                                   │
        ┌──────────────────────────┴──────────────────────────┐
        ▼                                                     ▼
   HTTP → API → endpoint                          (nothing else can begin)
        ▼
   request · response · JSON · headers · status codes
        ▼
   FastAPI ──► Pydantic contract ──► dependency injection
        ▼                                    │
   authentication                            │
   hashing → JWT → claims → signature        │
        ▼                                    │
   authorization ◄───────────────────────────┘
   role hierarchy → field-level filtering → fail closed
        ▼
   Python idioms this repo uses
   TypedDict/dataclass/Pydantic · contextmanager · async · lazy singleton
        ▼
   relational data
   tables → transactions → SET LOCAL → RLS → indexes → migrations
        │
        ├──────────────────────────────┬──────────────────────────┐
        ▼                              ▼                          │
   LLM foundations              structured vs unstructured        │
   tokens → context →                  │                          │
   hallucination                       ▼                          │
        ▼                        metric registry                  │
   prompting → structured             │                           │
   output → schema-is-prompt          ▼                           │
        ▼                        DSL → validation →               │
   timeouts → failover →         SQL compilation →                │
   attribution                   Python arithmetic →              │
        │                        verification                     │
        ▼                              │                          │
   why RAG exists                      │                          │
        ▼                              │                          │
   vectors → cosine → embeddings       │                          │
        ▼                              │                          │
   vector DB → HNSW → payload filters  │                          │
        ▼                              │                          │
   PDF parsing → classification →      │                          │
   chunking → overlap → metadata       │                          │
        ▼                              │                          │
   dense retrieval ─┐                  │                          │
   BM25 (TF·IDF) ───┼─► RRF ─► rerank  │                          │
                    │   (cross-encoder,│                          │
                    │    SCORE SCALES) │                          │
                    ▼                  │                          │
              dedup → confidence → CRAG│                          │
                    ▼                  │                          │
              prompt construction → synthesis → citations         │
                    │                  │                          │
                    └────────┬─────────┘                          │
                             ▼                                    │
                  LangGraph: state · nodes · conditional edges ◄───┘
                             ▼
                  routing · entity resolution · refusal
                             ▼
                  cross-examination → contradiction detection
                             ▼
                  React → Next → SSE consumer → render boundary
                             ▼
                  security · evaluation · observability · deployment
```

## The four orderings that are load-bearing

Violating any of these produces a *confidently wrong* mental model, which is
harder to repair than an absent one.

**1. Ranking before score scales (Day 27 before Day 28).** The most
consequential idea in this repository is that a `reranker_score` is meaningless
without its `reranker_backend` — Cohere returns `[0,1]`, local ONNX returns
logits around `[-12,+2]`, and the fallback fires on network flap. That idea is
unteachable before RRF and ranking exist.

**2. Transactions before RLS (inside Day 14).** Taught in the wrong order, RLS
is learned as "a `WHERE` clause the database adds". It is not. It is a policy
that returns **zero rows** when the GUC is unset — and "zero rows" then reads as
"no data", which is the most common silent failure in this codebase.

**3. SQL and RLS before the DSL (Days 13–15 before Days 31–34).** The
quantitative path *is* SQL. The DSL only makes sense once you know what it
compiles to and why the compiler, not the model, writes it.

**4. Both engines before cross-examination (Days 30 and 34 before Day 37).**
`cross_engine_node` literally calls `quant_engine_node` and
`semantic_engine_node`. It is unreadable before both.

---

# F. Codebase dependency graph

Who imports whom. Arrows point from importer to imported.

```
main.py
 ├─► api/query.py ──► auth/dependencies ──► core/security ──► core/config
 │        ├─► api/response_shaping.py          (leaf — pure dict shaping)
 │        └─► engines/graph.py
 ├─► api/documents.py ──► ingestion/{gate, pdf_text, storage}, db/session
 ├─► api/metrics.py ──► db/session
 └─► auth/router.py ──► auth/{schemas, service} ──► db/session

engines/graph.py
 └─► engines/state.py          ◄── EVERY node imports this; it imports nothing back
      ├─► prompt_shield.py                       (leaf — regex only)
      ├─► router.py ──► ingestion/entity_resolver ──► metrics/registry
      │        ├─► engines/dsl_compiler ──────────► metrics/registry
      │        └─► llm/client.py
      ├─► semantic_engine.py ──► retriever.py ──► [fastembed · qdrant · cohere]
      ├─► quant_engine.py ──► dsl_compiler + llm/client + psycopg2
      ├─► cross_engine.py ──► quant_engine + semantic_engine + contradiction
      ├─► confidence.py                          (leaf — pure)
      ├─► response_generator.py ──► llm/client + metrics/registry
      └─► audit_writer.py ──► psycopg2

ingestion/ — a SEPARATE graph; imports nothing from engines/
 pipeline.py
  ├─► pdf_parser.py ──────────► models.py
  ├─► document_classifier.py ─► models.py
  ├─► section_classifier.py ──► models.py
  ├─► chunker.py ─────────────► models.py
  ├─► embedder.py ────────────► models.py
  ├─► qdrant_writer.py ───────► models.py
  ├─► financial_extractor.py ─► entity_resolver ──► metrics/registry
  └─► db_loader.py ───────────► models.py

frontend/
 app/page.tsx
  ├─► lib/api.ts ──► lib/auth.ts
  ├─► components/document/*        (23 components)
  └─► components/environment/*     (4 components)

 components/AnswerCard.tsx ──► ConfidenceBadge.tsx     ◄── UNREACHABLE (CAVEAT-026)
 components/CorpusPanel.tsx                            ◄── UNREACHABLE
```

## Three structural facts

**`metrics/registry.py` is imported by five modules across both graphs.** That
convergence is deliberate — it replaced three hand-maintained dicts whose drift
caused three shipped bugs, each named in its docstring.

**`engines/state.py` is imported by everything and imports nothing from
`engines/`.** The dependency runs one way. `record_llm_call` takes an untyped
`result` specifically so `state.py` need not import the LLM module.

**`ingestion/` never imports `engines/`.** The two halves are genuinely
independent; they meet only in Qdrant and Postgres. This is why ingestion can
run on a laptop while the API runs on Render.

---

# G. The learning order, and why it is this

**47 days, 13 phases.** The number is derived, not chosen: 13,514 lines of
backend Python, 3,053 of frontend TypeScript, 17 migrations and 39 operational
scripts, at 60–120 minutes a day, with the four load-bearing orderings above
respected and no difficult topic compressed.

| Phase | Days | What it establishes | Why it cannot come earlier |
|---|---|---|---|
| **0** Ground | 1–3 | Run it; read history; the one dict | Nothing can be read before the stack runs and `QueryState` is understood |
| **1** The request | 4–6 | HTTP → FastAPI → SSE | Needs a running system to point at |
| **2** Identity | 7–9 | Hashing → JWT → `Depends` → RBAC | Needs headers (D4) and contracts (D5) |
| **3** Python here | 10–12 | Three type systems, context managers, async, lazy singletons | Needs the dict (D3) and async (D6) to have something to explain |
| **4** Data | 13–16 | Schema → RLS → indexes → migrations | Needs `tenant_id` to come from somewhere real (D9) |
| **5** LLM | 17–19 | Tokens → prompting → failover → attribution | Independent of D13–16; both are prerequisites for everything below |
| **6** RAG + ingestion | 20–24 | Embeddings → Qdrant → PDF → chunking | Needs tokens (D17) and tables (D13) |
| **7** Retrieval | 25–29 | Dense → sparse → RRF → rerank → CRAG | **Strictly ordered.** See load-bearing ordering 1 |
| **8** Semantic whole | 30 | The complete path, every arrow | Needs all of Phase 7 |
| **9** Quantitative | 31–34 | Registry → DSL → SQL → guards | Needs SQL/RLS (D13–15) and structured output (D18) |
| **10** Orchestration | 35–37 | LangGraph → router → cross-examination | Needs **both** paths complete |
| **11** Frontend | 38–41 | React → SSE consumer → render boundary | Needs to know what it is rendering (D30, D34) |
| **12** Production | 42–45 | Shield → eval → observability → deploy | Evaluation asserts across all three paths |
| **13** Capstone | 46–47 | The master trace from memory; failure drills | Needs everything |

## Why not the textbook's order

[`RAG_Complete_Textbook_v2`](#) opens with LLMs and embeddings, which is correct
for learning RAG in general and wrong for learning *this* system. You cannot read
a single engine file here until `QueryState` makes sense, and `QueryState` does
not make sense until you have watched one request move through it.

The textbook is used as the **conceptual reference layer**, cited per day and
labelled:

- **CONFIRMS** — LedgerMind implements it as described. Read the textbook first.
- **EXTENDS** — LedgerMind goes further. Read the textbook for the floor.
- **DIVERGES** — LedgerMind deliberately does the opposite. Learn why.

Thirteen divergences are catalogued in the master course. The most important:
the textbook's Part 17 master flow opens every request with a **cache check**;
LedgerMind has no cache at all, and `cache_hit_rate_pct` ships on the metrics
endpoint returning a permanent 0.0 because nothing writes the column it averages.
That is recorded as open debt rather than quietly deleted — which is itself the
lesson.

## What you will be able to do at the end

Not "I used Qdrant, BM25, JWT, FastAPI and RAG." Rather:

> I understand why LedgerMind uses Qdrant here, what happens to a document before
> it reaches Qdrant, how the query reaches retrieval, why dense and sparse
> retrieval are both used, how the results are fused and reranked, how the
> context reaches the LLM, how authentication protects the request, how the
> structured path differs from the semantic path, what can fail at each stage,
> and why we made each architectural decision.

Track it in [`LEARNING_PROGRESS.md`](LEARNING_PROGRESS.md) — seven dimensions per
concept, of which `modify` and `debug` are the two that cannot be faked by
careful reading.
