# LedgerMind

**Deterministic financial intelligence for Indian capital markets.**

LedgerMind is a multi-tenant RAG + SQL platform that answers natural language questions about Indian public company filings — with zero hallucination on numbers, full source citations, and SEBI-compliant guardrails. Built as a production-grade portfolio project demonstrating architecture patterns used in real fintech systems: deterministic SQL compilation, row-level security, explainable retrieval, and full audit lineage.

> *"Not a chatbot. A deterministic financial intelligence operating system."*

---

## The Core Problem

Generic RAG breaks on financial documents for reasons specific to Indian filings:

- **LLMs can't be trusted with arithmetic.** Ask an LLM for EBITDA and it will confidently invent a number.
- **Standalone vs Consolidated.** Every SEBI-listed company files both. The numbers can differ 5x. Naive vector search can't tell them apart — the text looks nearly identical.
- **Restatements.** Indian companies routinely restate prior-year figures. Two "correct" numbers can exist for the same metric/period.

LedgerMind solves this by **never letting an LLM compute or retrieve a number directly.** Numbers come from a DSL → SQL compiler. Text comes from cited, reranked retrieval. The two paths never blur.

---

## Architecture

```
User Query
    ↓
FastAPI (JWT auth, RBAC)
    ↓
Prompt Shield  ──→ blocks trading advice / investment recommendations (SEBI compliance)
    ↓
Entity Resolver ──→ company, fiscal year, quarter, financial_type
    ↓
Router (LangGraph) ──→ classifies intent, picks a path
    ↓
┌────────────────┬─────────────────────┬───────────────────────┐
│     Path 1     │       Path 2        │        Path 3         │
│  Semantic RAG  │  DSL → SQL Engine   │  Cross-Examination    │
│  (qualitative) │   (quantitative)    │ (contradiction check) │
└────────────────┴─────────────────────┴───────────────────────┘
    ↓
Confidence Scoring + Citation Attachment
    ↓
Response (role-shaped: viewer/analyst/admin see different levels of detail)
    ↓
Audit Log (append-only, RLS-scoped)
```

**The one rule that makes this system trustworthy:** the LLM generates a controlled JSON object (`{metric: "revenue", entity: "ETERNAL", period: "FY26"}`), never SQL. A deterministic Python compiler turns that into parameterised SQL. The LLM never sees the schema and never touches the database.

### Multi-tenant isolation

Every table enforces PostgreSQL Row-Level Security via `SET LOCAL app.tenant_id`, scoped per-request from a verified JWT — never from client input. Verified end-to-end: a Beta-tenant admin querying Alpha-tenant data gets `no_data_found`, not another tenant's numbers, through the live API (not just `psql`).

### Full tech stack

| Layer | Tool |
|-------|------|
| Backend | FastAPI, LangGraph, psycopg2 |
| Frontend | Streamlit |
| LLM | Gemini Flash 2.0 (free tier), Groq llama-3.1-70b (fallback) |
| Embeddings | bge-small-en-v1.5 (local CPU) |
| Reranking | ms-marco-MiniLM-L-6-v2 (local CPU) |
| Vector DB | Qdrant (hybrid dense + BM25 sparse) |
| Relational DB | PostgreSQL with RLS |
| Auth | JWT + bcrypt |
| Cache | Redis (tenant-scoped semantic cache) |
| Orchestration | Docker Compose |

**Total monthly cost: ₹0** — entirely free-tier stack.

---

## Quickstart

```bash
git clone https://github.com/Laren27/Ledgermind.git
cd Ledgermind
cp .env.example .env   # add your GEMINI_API_KEY, QDRANT_URL, QDRANT_API_KEY
docker-compose up --build
```

That's it. FastAPI, Streamlit, PostgreSQL, and Qdrant all boot from one command. No local Python environment, no dependency conflicts.

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:8501`
- API docs: `http://localhost:8000/docs`

### Demo accounts (seeded, password `demo1234` for all)

| Email | Role | Tenant |
|---|---|---|
| `admin@alpha.ledgermind.test` | Admin | Alpha |
| `analyst@alpha.ledgermind.test` | Analyst | Alpha |
| `viewer@alpha.ledgermind.test` | Viewer | Alpha |
| `admin@beta.ledgermind.test` | Admin | Beta |

Log in with different roles to see the same query return different levels of detail — viewers see the answer, analysts see the DSL and SQL, admins see latency and token usage.

---

## Data Governance by Role

The same query returns different response shapes depending on who's asking — this is the RBAC layer proving real data governance, not just endpoint gatekeeping:

| Field | Viewer | Analyst | Admin |
|-------|:------:|:-------:|:-----:|
| Answer + citations | ✅ | ✅ | ✅ |
| Confidence tier | ✅ | ✅ | ✅ |
| DSL object | ❌ | ✅ | ✅ |
| Compiled SQL | ❌ | ✅ | ✅ |
| Retrieval scores | ❌ | ✅ | ✅ |
| Latency / token usage | ❌ | ❌ | ✅ |

---

## Evaluation Results

50-question golden dataset, grounded entirely in verified corpus data (Eternal Q4FY26 shareholder letter + results). Every quantitative expected value was cross-checked against the `financials` table before the question was written — no estimated or assumed answers.

| Category | Score | What it tests |
|----------|-------|---------------|
| Quantitative — point-in-time | 15/15 | Exact SQL value match across 10 metrics, 2 fiscal years |
| Quantitative — YoY growth | 5/5 | Two-period SQL compilation + Python arithmetic, ±0.5% tolerance |
| Quantitative — standalone/consolidated isolation | 5/5 | `financial_type` filter never leaks between report types |
| Semantic — management discussion | 8/8 | Non-GAAP definitions, forward-looking statements, retrieval + generation |
| Semantic — audit & compliance | 7/7 | Deloitte audit opinion, IND AS, SEBI LODR, going concern |
| Adversarial (Prompt Shield) | 7/7 | Trading advice, investment recommendations — all correctly blocked |
| Out-of-corpus refusal | 3/3 | FY23 data, unavailable metrics, uningested companies — no hallucination |
| **Total** | **50/50** | |

Run it yourself:
```bash
python3 scripts/generate_golden_dataset.py
python3 scripts/eval_runner.py --delay 15
```

### Known caveats (documented, not hidden)

- **Refusal rate on the observability dashboard reads ~25%**, higher than the 0% actual semantic refusal rate — an upstream bug where `audit_writer` reads `confidence_score` before `quant_engine` finishes writing it, so quantitative rows log `0.0` and get miscounted as refusals in the aggregate stat. Cosmetic on the dashboard; does not affect the eval suite, which scores against `sql_verified` and `confidence_tier` from the live response, not the audit log.
- **Gemini free tier (5 RPM)** occasionally times out under load — a known external rate-limit behaviour, not a system defect. The eval runner uses a 15s delay between calls to stay under the limit.

---

## What's Deliberately Out of Scope

Documented here to preempt "why didn't you build X" — these were conscious scope decisions for a solo portfolio project, not oversights:

| Item | Reasoning |
|------|-----------|
| Microservices | Python modules inside FastAPI are sufficient at this scale |
| Kafka / Airflow | PostgreSQL event log + Celery Beat cover the same need |
| React frontend | Streamlit is sufficient for a portfolio demo |
| Real SaaS billing | Simulated via `tenant_id` in schema |
| Knowledge graph (Neo4j) | Documented as a Phase 2 roadmap item, not built |

---

## Roadmap (parked, not forgotten)

- Contradiction View + Document Upload Streamlit screens
- Refresh token pairs (currently: single 2hr access token, re-login on expiry)
- Rate limiting, cost tracking per tenant
- Cohere reranker upgrade (currently: local cross-encoder)
- `audit_writer` timing fix for accurate quantitative confidence logging
- Corpus expansion: FY24 Annual Report → Paytm quarterly → DRHP filings

---

## Interview Talking Points

**The one-sentence pitch:** LedgerMind separates qualitative reasoning from deterministic financial computation to guarantee zero hallucination on numerical queries — built specifically around the standalone-vs-consolidated problem unique to Indian company reporting.

**On the DSL compiler:** The LLM generates a controlled JSON object; a deterministic Python function compiles it to parameterised SQL. The LLM never writes SQL and never sees the schema. Every answer is traceable from source PDF chunk through retrieval scores to final response.

**On multi-tenancy:** RLS via `SET LOCAL`, not `SET` — transaction-scoped so a pooled connection can never leak one tenant's context into another's request. Proven live: a Beta admin querying Alpha's data gets `no_data_found`, not wrong data.

**On the eval suite:** 50/50 isn't a vanity number — every expected value was pulled directly from the database before the question was written, and every failure during development traced to a real bug (a `KeyError` in the SQL compiler, a stale metric registry) that got fixed, not a scorer that got loosened to pass.