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

84-question golden dataset across three companies — Eternal (52), Titan (14), Paytm (18) — grounded entirely in verified corpus data. Every quantitative expected value was cross-checked against the `financials` table before the question was written; no estimated or assumed answers.

**Scored on `gemini-3.1-flash-lite`.** The model is part of the number, not a footnote to it: the runner records which provider served every answer and *withholds the headline score entirely* if any answer came from the Groq fallback. That is not hypothetical — during a quota exhaustion it withheld three separate runs whose raw tallies (13/14, 17/17, 43/52) would otherwise have looked publishable. A score printed under a caveat still ends up in a README.

| Category | Score | What it tests |
|----------|-------|---------------|
| Quantitative — point-in-time | 26/26 | Exact SQL value match across three companies and four fiscal years |
| Quantitative — YoY growth | 8/8 | Two-period SQL compilation + Python arithmetic, ±0.5% tolerance |
| Quantitative — standalone/consolidated isolation | 7/7 | `financial_type` filter never leaks between report types |
| Quantitative — cross-entity growth comparison | 1/1 | Four SQL queries, two entities, growth rates compared in Python |
| Semantic — management discussion | 8/8 | Non-GAAP definitions, forward-looking statements, retrieval + generation |
| Semantic — honest refusal | 2/2 | Topics genuinely absent from the corpus — states what is *not* covered rather than confabulating from adjacent chunks |
| Semantic — audit & compliance | 7/7 | Deloitte audit opinion, IND AS, SEBI LODR, going concern |
| Semantic — business & risk | 9/9 | Segment performance, regulatory notices, PPBL licence cancellation |
| Cross-examination | 1/1 | Narrative claim vs verified figure — asserts a NON-contradiction stays unflagged |
| Adversarial (Prompt Shield) | 11/11 | Trading advice, investment recommendations — all correctly blocked |
| Out-of-corpus refusal | 6/6 | Absent periods, unavailable metrics, uningested companies — no hallucination |
| **Total** | **84/84** | |

The cross-examination row is the newest and the thinnest. It asserts that the system does *not* fabricate disagreement: Paytm states it has no exposure to PPBL, and a ₹207 Cr impairment line appears in the same filing — the two are consistent, because note 4 reconciles that figure as ₹5 Cr + ₹12 Cr + ₹190 Cr against other entities, and PPBL was fully impaired in FY24. A system that flagged this would be broken in the way that matters most, since fabricated disagreement inverts the whole point of the path. One question is coverage of one availability case, not of the path; and no *real* contradiction exists in the current corpus to test the positive direction.

Run it yourself:
```bash
cd backend
python scripts/eval_runner.py --model gemini-3.1-flash-lite --delay 25 \
  --dataset ../golden_dataset/q4fy26_eternal.json --out ../golden_dataset/eval_results_eternal.json
# repeat for q_titan.json and q_paytm.json — distinct --out per company
```

`--delay 25` because a semantic question makes two LLM calls (router + synthesis) against a 5 RPM free tier. `--model` is required and must match `GEMINI_MODEL` in `.env`; it currently labels the report rather than asserting against the model that served, which is a known gap.

### Known caveats (documented, not hidden)

- **Free tiers are the reliability ceiling, not the architecture.** Gemini allows 500 requests/day per model and Groq 100k tokens/day; both can be exhausted in a working session, and when both are gone the semantic path falls back to returning the top retrieved excerpt unsynthesised rather than fabricating. The failover was exercised end-to-end this way. Stacking two free tiers does not compose into reliability — that needs a billing-enabled key.
- **A total provider outage is currently under-reported.** When no LLM answers, retrieval confidence is still genuinely high, so the response carries `confidence_tier="high"` with no error set. The tier is honest about retrieval and silent about synthesis. Fix identified (`error="synthesis_unavailable"`), not yet shipped.
- **The Groq fallback preserves availability, not behaviour.** Routing is itself an LLM call, and the two models classify borderline questions differently — one Titan question routes `semantic` on Gemini across four runs and `quantitative` on every Groq-served run. This is why the provider gate exists.
- **Citations are not floor-filtered.** After near-duplicate suppression, the lowest-ranked citations can score ~0.01–0.03 — technically retrieved, practically noise. A minimum relevance threshold for citation is open work.

---

## Known Limitations & Classification Caveats

The document classification pipeline (`section_classifier.py`, `financial_extractor.py`)
uses deterministic keyword/regex heuristics rather than ML classification, per the
project's design philosophy (see Section 2, Principle 1). This approach is
transparent and debuggable, but requires re-validation against each new document
type or company. Three confirmed failure patterns from Phase 3 finalization testing:

1. **Statement-title vocabulary differs by filing type.** SEBI quarterly results
   use regulatory headings ("Statement of Consolidated Financial Results for the
   Quarter Ended..."); Companies Act annual reports use formal statutory titles
   ("Statement of Profit and Loss," "Balance Sheet"). A classifier tuned on one
   filing type will under-classify the other unless both vocabularies are present
   in `STATEMENT_TITLE_ANCHORS`.

2. **Auditor reports name statements without being one.** Auditor's Report pages
   routinely enumerate the statements they reviewed ("...the Consolidated Balance
   Sheet, the Consolidated Statement of Profit and Loss...") in prose. A pure
   keyword/phrase classifier can misclassify these as primary statement pages.
   Mitigation: require the standard Ind AS "Particulars" column header as a
   structural co-signal alongside any title-phrase match, and maintain an
   explicit auditor-report exclusion list with a bounded continuation window
   (auditor reports lose their identifying header after the first page, same
   as multi-page statements).

3. **`is_latest` retirement must distinguish same-`doc_id` replay from
   different-`doc_id` re-ingestion.** A naive `filing_date >` comparison fails
   silently on same-date re-ingestion (common during iterative debugging, where
   a document is re-run with a fresh `doc_id` each time) — either accumulating
   duplicate `is_latest=TRUE` rows, or (if retirement is added naively) deleting
   the only `is_latest` row without a replacement. `db_loader.py`'s `_upsert_one()`
   now checks `doc_id` identity first: same `doc_id` → true no-op via
   `ON CONFLICT DO NOTHING`; different `doc_id` with `filing_date >=` existing →
   retire-then-insert.

    **Practical implication:** any new document type or company added to the corpus
    should be run through `scripts/regression_check.py` before trusting extracted
    figures — this script checks both classification precision (are the right pages
    tagged FINANCIAL_STATEMENT?) and extraction correctness (does the revenue figure
    match a known-good value?) across all reference documents in one pass.

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

**On the eval suite:** 84/84 isn't a vanity number — every expected value was pulled directly from the database before the question was written, and every failure during development traced to a real bug (a `KeyError` in the SQL compiler, a stale metric registry, an LLM silently substituting one metric for another) that got fixed, not a scorer that got loosened to pass. The runner also gates on which model served each answer and withholds the score outright if the fallback fired, because the most dangerous eval result is a plausible one measured under conditions nobody recorded.