# LedgerMind

**Deterministic financial intelligence for Indian capital markets.**

A multi-tenant RAG + SQL platform that answers natural-language questions about Indian public company filings — with zero hallucination on numbers, source citations that resolve, and SEBI-compliant guardrails.

> **87 / 91** on the golden dataset — `gemini-3.1-flash-lite`, single provider, single model, Cohere-served reranking on every scored answer. All four failures are named and explained below. Zero wrong figures.

---

## The problem generic RAG cannot solve

Three properties of Indian filings break a standard vector-search pipeline:

**LLMs are unreliable calculators.** Ask a model for EBITDA and it will produce a confident number. It is not retrieved, not computed, and not checkable.

**Standalone vs consolidated.** Every SEBI-listed company files both. The text is nearly identical; the numbers differ by multiples. A cosine similarity search cannot distinguish them.

**Restatements.** Companies revise prior-year figures under Ind AS transitions. Two contradictory numbers for the same metric and period are both legitimately "correct" — they differ by filing date.

LedgerMind's answer is a hard separation: **numbers come from SQL, prose comes from cited retrieval, and the two paths never blur.**

---

## The guarantee

The LLM generates a controlled JSON object. It never writes SQL, never sees the schema, and never performs arithmetic.

```
  {"metric": "revenue", "entity": "ETERNAL", "period": "FY26",
   "financial_type": "consolidated"}
              │
              ▼
   DSL validator  ──►  rejects unknown metrics, entities, periods
              │
              ▼
   Deterministic Python compiler  ──►  parameterised SQL
              │
              ▼
   PostgreSQL (RLS-scoped)  ──►  verified figure  ──►  LLM explains it
```

Every figure the system reports carries `sql_verified: true` and traces back through the DSL to a row, a document, and a page.

---

## Architecture

```
User query
    │
    ▼
FastAPI  ── JWT auth, RBAC, tenant resolution
    │
    ▼
Prompt Shield  ────────►  blocks trading advice / investment recommendations
    │
    ▼
Entity Resolver  ──────►  company, fiscal year, quarter, financial_type
    │
    ▼
Router (LangGraph state machine)
    │
    ├──────────────────┬─────────────────────┬──────────────────────┐
    ▼                  ▼                     ▼                      │
 Path 1             Path 2                Path 3                    │
 Semantic RAG       DSL → SQL             Cross-Examination         │
 (qualitative)      (quantitative)        (claim vs figure)         │
    │                  │                     │                      │
    └──────────────────┴─────────────────────┴──────────────────────┘
    │
    ▼
Confidence scoring  ──  backend-aware thresholds (Cohere 0-1 vs ONNX logits)
    │
    ▼
Citation attachment  ──  doc, page, chunk id, retrieval score, reranker score
    │
    ▼
Response, role-shaped  ──  viewer / analyst / admin see different detail levels
    │
    ▼
Audit log  ──  append-only, RLS-scoped
```

### Retrieval

Hybrid dense + sparse search in Qdrant (384-dim `bge-small-en-v1.5` and BM25, fused with native RRF), metadata pre-filtered on company, fiscal year and `financial_type` *before* semantic search runs — so standalone chunks never enter a consolidated query's candidate pool. Reranking is Cohere Rerank with a local ONNX cross-encoder as automatic fallback.

Near-duplicate suppression sits between reranking and the final cut: `OVERLAP_TOKENS=150` means adjacent chunks share text by design, and without suppression two windows over the same passage could both consume citation slots. One measured query had 9 of 20 candidates suppressed as duplicates.

### Multi-tenant isolation

Every table enforces PostgreSQL row-level security via `SET LOCAL app.tenant_id`, scoped per-request from a verified JWT — never from client input. Policies are single `CASE` expressions rather than `AND` chains, because PostgreSQL does not guarantee left-to-right evaluation of boolean conjuncts and a guarded cast can still be evaluated first. That distinction caused a production outage; see the engineering log.

Verified end-to-end: a Beta-tenant admin querying Alpha-tenant data receives `no_data_found` through the live API, not another tenant's numbers.

---

## Evaluation

91 questions across four datasets, every expected value cross-checked against the `financials` table before the question was written.

**The model is part of the number.** The runner asserts the stated model against what the API actually reports, and **withholds the headline score entirely** if any answer came from the Groq fallback. That is not hypothetical: a sweep with a raw tally of 55/55 was withheld on `{gemini: 33, groq: 15}` — the same tally as the published run, produced by two different systems, and uninterpretable.

| Category | Score | What it tests |
|---|---|---|
| Quantitative — point-in-time | 27/27 | Exact SQL value match, three companies, four fiscal years |
| Quantitative — YoY growth | 8/8 | Two-period compilation + Python arithmetic, ±0.5% tolerance |
| Quantitative — standalone isolation | 7/7 | `financial_type` filter never leaks between report types |
| Quantitative — cross-entity comparison | 1/1 | Four queries, two entities, growth compared deterministically |
| Adversarial (Prompt Shield) | 11/11 | Trading advice and investment recommendations, all blocked |
| Out-of-corpus refusal | 6/6 | Absent periods, unavailable metrics, uningested companies |
| Semantic — management discussion | 8/8 | Non-GAAP definitions, forward-looking disclaimers |
| Semantic — audit & compliance | 6/6 | Auditor opinion, Ind AS, SEBI LODR, going concern |
| Semantic — honest refusal | 2/2 | States what is *absent* rather than confabulating from adjacent chunks |
| Semantic — business | 4/5 | Segment performance, store counts, ESG disclosures |
| Semantic — risk | 3/4 | Regulatory notices, licence cancellation exposure |
| Cross-examination | 4/6 | Narrative claim against verified figure |
| **Total** | **87/91** | |

### The four failures

None is a wrong number. Three are router-versus-golden path disagreements and one is a keyword assertion.

| ID | Failure | Status |
|---|---|---|
| `PQ012` | expected `semantic`, routed `cross` | Deliberate. Carries a `known_deliberate_failure` field — it is the only artifact recording that classification is imprecise on "financial exposure to X" phrasing. Editing the expectation would buy a green score by deleting the evidence. |
| `TQ008` | expected `semantic`, routed `cross` | Open. Stable across runs, cause unknown. |
| `ETQ001` | expected `cross`, routed `semantic` | Open. Observed twice, same direction. |
| `PQ018` | missing keyword `ppbl` | Open. Either a regression or a keyword assertion a correct answer can fail — undecided on one observation. |

### What this score does *not* establish

A golden score bounds the correctness of what it asserts and says nothing about anything else. Measured: of 269 `(company, metric)` pairs live in the database, **16 are value-pinned by any question** — 93.3% are unasserted.

This is not theoretical. A run of `backfill_financials --correct-values` found **28 stale figures** in the database while the suite scored 100%, and not one of the 28 was asserted by any question. Two of them were negative cash balances, which is impossible on its face.

The suite scores *answers*, not citation provenance, not retrieval quality, and not the 253 unasserted metric pairs. Stating that is more useful than a perfect number.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + LangGraph | Async, typed; LangGraph gives the router an inspectable state machine rather than nested conditionals |
| Frontend | Next.js on Vercel | The working-paper document model needs real layout control |
| LLM — primary | Gemini `gemini-3.1-flash-lite` | Free tier, fast, structured-output support |
| LLM — fallback | Groq `llama-3.3-70b-versatile` | Fires on timeout, transport failure, 429, 5xx — never on auth errors, so a bad key can't masquerade as an outage |
| Embeddings | `bge-small-en-v1.5` via fastembed ONNX | 384-dim, CPU-only; torch does not fit Render's 512MB tier |
| Reranking | Cohere Rerank, local ONNX fallback | Same memory constraint; the fallback changes score *scale*, so confidence thresholds are backend-aware |
| Vector DB | Qdrant Cloud | Hybrid dense + sparse with native RRF and payload pre-filtering |
| Relational DB | Supabase PostgreSQL | Row-level security, session pooler |
| Async | Celery + Redis | Redis is the broker only — the semantic cache in the design spec is **not built** |
| Deploy | Render (API), Vercel (UI), Docker Compose (local) | |

Running cost: **₹0** — entirely free tier. That ceiling is real: Gemini allows 500 requests/day and Groq 100k tokens/day, and both can be exhausted in one working session. Stacked free tiers do not compose into reliability.

---

## Engineering log

The most valuable artifact in this repository is [`docs/IMPLEMENTATION_DELTAS.md`](docs/IMPLEMENTATION_DELTAS.md) — every divergence between the design spec and the shipped system, every defect found, and the reasoning that found it. Five entries worth reading:

**A 10,000 Cr error laundered through arithmetic.** OCR split `17,292` into `I` and `7,292`; a rule that treated any comma-bearing fragment as a complete number kept the second and discarded the first. Derivation then recomputed total income and total expenses *from* the corrupted revenue, overwriting two rows OCR had read correctly. The stored column was internally self-consistent — which is exactly why it survived review. The system had logged the disagreement on every run for weeks, in a list scanned by count rather than by magnitude. *Standing rule: a derivation overwrite whose magnitude is not rounding-scale is a misread component until proven otherwise.*

**A green gate that validated the producer, not the store.** `regression_check` parses PDFs and asserts on extraction output in memory. It passed 4/4 after every OCR fix — correctly, because the extractor was right each time. Meanwhile the loader's same-`doc_id` branch reasoned "same document replayed, nothing can have changed," which is false when the *parser* changed, and dropped every corrected value on the floor as `skipped`. 28 stale figures, invisible to a green suite for three weeks.

**A correct number in the wrong row.** `cash` for Paytm was stored as −710 Cr. A balance-sheet stock cannot be negative. The figure was read perfectly — it was the cash-flow *movement* line, claimed for the wrong metric. No arithmetic guard could catch it, because nothing about the digits was corrupt; only a semantic claim ("a stock cannot be negative") separates it. That claim is now `scripts/check_balance_invariants.py`, which caught a second instance in a different company the first time it ran against production.

**A citation floor that guaranteed untraceability.** Low-scoring chunks were filtered out of the citation list but left in the model's context. An answer then reported "4.8 million square feet in FY24" — a real figure, correctly extracted, supplied by a chunk scoring 0.0165 that had been removed from the citations. The answer was built on five passages and cited one. The fix was to delete the floor: a weak citation rendered honestly beats a hidden one.

**One vector store, two databases, disjoint primary keys.** Local and production held the same documents under different `doc_id`s, because `register_sections` mints a UUID per call and the dedup conflict is per-database. 139 Qdrant chunks were deleted as "orphans" on the strength of a lookup against local only — they were production's Paytm and Titan corpus. *A checker that can structurally only inspect one of two stores passes having inspected nothing.* Resolved by deterministic doc_ids (migrations 018–019).

---

## Quickstart

```bash
git clone https://github.com/Laren27/Ledgermind.git
cd Ledgermind
cp .env.example .env      # add GEMINI_API_KEY, COHERE_API_KEY, QDRANT_URL
docker compose up --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |

Local demo accounts are seeded across two tenants and three roles. Log in as each to see the same query return different detail — viewers get the answer and citations, analysts additionally see the DSL and compiled SQL, admins also see latency, token usage, provider and reranker backend.

### Running the evaluation

```bash
cd backend
python3 -m scripts.eval_runner \
  --model gemini-3.1-flash-lite \
  --dataset ../golden_dataset/q4fy26_eternal.json \
  --out ../eval_results/eval_eternal.json \
  --delay 45
```

`--model` is required and asserted against the model the API reports. `--delay 45` because a semantic question makes two LLM calls against a 5 RPM free tier. Read the provider, model and reranker-backend lines *before* the score — a contaminated run is withheld, not annotated.

---

## Deliberately out of scope

| Item | Reasoning |
|---|---|
| Microservices | Python modules inside FastAPI are sufficient at this scale |
| Kafka / Airflow | PostgreSQL event log and Celery Beat cover the same need |
| Knowledge graph (Neo4j) | Phase 2 roadmap; flat retrieval is not yet the bottleneck |
| RAGAS | Replaced by exact-value assertions. This system's claim is that numbers are *exactly* right — a pass/fail property, not a 0–1 faithfulness score |
| Redis semantic cache | Specified in the design, not built. The metrics endpoint that would report its hit rate is disabled rather than reporting a permanent zero as a measurement |

---

## Design principles

1. **LLMs never do math.** Every figure originates in SQL against verified rows.
2. **Retrieval is explainable.** Document, page, chunk id, retrieval score and reranker score on every citation.
3. **Auditability first.** Source PDF → chunk → retrieval → DSL → SQL → response, on every answer.
4. **Refusal beats a plausible answer.** Below the confidence floor, the system declines and logs it.
5. **A number without a producer is not a measurement.** Every figure in this README was pulled from a committed artifact, not recalled.
