# LedgerMind — Capability Matrix (real vs. planned)

**Rule for this file: the source code is the authority.** Nothing is marked
Implemented because a README, the blueprint, or a docstring says so. Each row
names the evidence.

Status vocabulary:

| Status | Meaning |
|---|---|
| **Implemented** | Code exists, runs in the request/ingest path, and is exercised. |
| **Partial** | Works for the current corpus but has a named condition under which it does not. |
| **Stubbed** | A field, column or flag exists with no producer. |
| **Planned** | Documented as intended; no implementation found. |
| **Doc-only** | Stated somewhere as existing; **no implementation found**. |
| **Broken** | Present and wrong. |
| **Experimental** | Built but unmeasured against the golden set. |

Last verified: **2026-08-20**, by reading the tree at commit `1bca3cb`.

---

## Query pipeline

| Capability | Status | Evidence |
|---|---|---|
| JWT login, 2-hour HS256 token | Implemented | `app/auth/service.py`, `app/core/security.py` |
| `POST /api/query` (blocking) | Implemented | `app/api/query.py:104` |
| `POST /api/query/stream` (SSE node trace) | Implemented | `app/api/query.py:137`; frontend reader `lib/api.ts:164` |
| Prompt Shield (advice + injection) | Implemented | `app/engines/prompt_shield.py`, 18 patterns |
| LLM router: entity extraction + path classification | Implemented | `app/engines/router.py:129` |
| Refusal on unresolvable company | **Partial** | `router.py:315-345`. Fires only when the model *returns* a name that fails the `_KNOWN_TICKERS` gate. A query naming an unheld company can still yield `company=None` with nothing recorded. Read `router.py:296-314`. |
| Refusal when no LLM provider reachable | Implemented | `routing_unavailable`, `router.py:319` |
| Semantic path (hybrid retrieval → rerank → synthesis) | Implemented | `semantic_engine.py`, `retriever.py`, `response_generator.py` |
| Quantitative path (DSL → SQL → Python math) | Implemented | `quant_engine.py`, `dsl_compiler.py` |
| Cross-examination path | **Experimental** | Built (`cross_engine.py`); `IMPLEMENTATION_DELTAS.md` §C records it as *BUILT but UNMEASURED*. |
| CRAG retry ladder (drop quarter → drop FY) | Implemented | `semantic_engine.py:209-265` |
| Confidence tiers, backend-aware thresholds | Implemented | `semantic_engine.py:105-174` |
| Contradiction detection (magnitude + direction) | Implemented | `contradiction.py` |
| Post-generation refusal detection | Implemented | `response_generator.py:70-105`; **semantic path only** — deliberately not applied to cross |
| Cross-path 4-quadrant reconciliation | Implemented | `response_generator.py:_reconcile_cross` |
| Append-only audit log with full lineage | Implemented | `audit_writer.py`; `audit_log` has no UPDATE/DELETE grant |
| Field-level RBAC, fail-closed | Implemented | `api/response_shaping.py:54-59` |
| Gemini → Groq failover | Implemented | `app/llm/client.py` |
| Provider/model attribution by precedence | Implemented | `state.py:219-271` |
| `/api/metrics` admin dashboard data | Implemented | `app/api/metrics.py` |

---

## Ingestion

| Capability | Status | Evidence |
|---|---|---|
| Pre-ingestion gate (is this a filing?) | Implemented | `ingestion/gate.py`, score ≥6 across ≥2 categories |
| Upload → Supabase Storage → `pending_uploads` | Implemented | `api/documents.py` |
| **Automatic ingestion on upload** | **Doc-only / deliberately not built** | Blueprint §16/§23 says event-driven. `api/documents.py:10-19` states why it is not, and `scripts/process_pending_uploads.py` is the actual trigger. |
| PDF parsing (text + positional tables) | Implemented | `pdf_parser.py` |
| Consolidated/standalone section detection | **Partial** | `document_classifier.py`. With no marker it creates ONE consolidated-only section — audit **F12(b)**; the docstring claims `needs_review=True` is set, which `tests/test_document_classifier.py:112` records as not matching behaviour. |
| Block classification (3-signal intersection) | Implemented | `section_classifier.py` |
| Chunking: tables whole, prose recursive | Implemented | `chunker.py` |
| Transcript speaker-turn chunking + roster | Implemented | `chunker.py:163-345` |
| Dense + sparse embedding | Implemented | `embedder.py` |
| Qdrant upsert with deterministic IDs | Implemented | `qdrant_writer.py`, `chunker.py:84` |
| Financial table extraction → `financials` | Implemented | `financial_extractor.py`, `db_loader.py` |
| Derived-total reconciliation (`total_income`, `total_expenses`) | Implemented | `financial_extractor.py:509` with a 5% divergence guard |
| Balance/P&L identity validation + hard gate at >5% | Implemented | `financial_extractor.py:638`, raises `RuntimeError` |
| Restatement handling (`is_latest` retirement) | Implemented | `db_loader.py:184` |
| Parser-correction path (`correct_values`) | Implemented, opt-in | `db_loader.py:94` |
| **Unit/scale detection** | **Not built** | Audit **F3**. `unit="crore_inr"` is hardcoded at 5 sites in `financial_extractor.py`. The *read* path is already unit-aware. |
| **Metadata validation against document content** | **Not built** | Audit **F4**. `company`, `fiscal_year`, `quarter`, `filing_date` are caller-asserted. |

---

## Storage & data

| Capability | Status | Evidence |
|---|---|---|
| Postgres RLS on documents/financials/audit_log | Implemented | `sql/init.sql:135-166`, `FORCE`d |
| Partial unique index enforcing one current value | Implemented | `uq_financials_latest` |
| Qdrant tenant + `is_latest` filter always applied | Implemented | `retriever.py:169-172` |
| `financial_type` retrieval filter | **Broken / inert** | Audit **F7**. The filter admits `financial_type OR "unknown"`, and nearly every narrative chunk is `"unknown"` because only FINANCIAL_STATEMENT blocks get a real type. Measured: excludes 17 of 2531. |
| `quarter` retrieval filter | **Latent risk** | `IMPLEMENTATION_DELTAS.md` §D. Currently a no-op because `quarter` and `fiscal_year` are collinear in this corpus. Triggers the first time one company has both an annual and a quarterly filing in one FY. |
| Multi-tenant Qdrant isolation | Implemented | payload `tenant_id` condition |
| Migration ledger | Implemented | `schema_migrations`, migration 012 |

---

## Explicitly NOT built (specified in the blueprint)

| Feature | Status | Evidence |
|---|---|---|
| **Redis semantic cache** (blueprint §15) | **Not built** | `IMPLEMENTATION_DELTAS.md` §B. Redis is the Celery broker and a health check only. `QueryState.cache_hit` is set `False` in `make_initial_state` and never written again — so `/api/metrics`' `cache_hit_rate_pct` is **structurally always 0.0**, and `api/metrics.py:44-49` says so in a comment. |
| **Parent-child chunking** (§9) | **Not built** | `IMPLEMENTATION_DELTAS.md` §B; deferred in `chunker.py` header. |
| **RAGAS evaluation** (§21) | **Not used** | `IMPLEMENTATION_DELTAS.md` §B. Evaluation is `scripts/eval_runner.py` against `golden_dataset/`. |
| **Per-tenant rate limiting** (§5/§14) | **Not built** | `IMPLEMENTATION_DELTAS.md` §A. |
| **Knowledge graph** (§26) | **Planned** | Blueprint roadmap only. |
| **Restatement confidence penalty** | **Stubbed** | `confidence.py:86` reads `restatement_disclosed`; nothing sets it. Audit **F5**. |
| **Derived-metric SQL compiler** (EBITDA etc.) | **Not built, honestly refused** | `registry.py` header; `quant_engine.py` Stage 0 guard refuses before calling the LLM. |

---

## Fields that exist but have no producer

These are the ones to be careful of — they will silently read as a default.

| Field | Where | Reality |
|---|---|---|
| `cache_hit` | `QueryState`, `audit_log.cache_hit`, `QueryResponse` | Always `False`. Frontend comment says **do not render**. |
| `tokens_used` | `QueryState`, `audit_log` | Initialised 0; no call site increments it. |
| `restatement_disclosed` | `QueryState` | Initialised `False`; `confidence.py` reads it, nothing writes it (F5). |
| `dense_score`, `sparse_score` | `ChunkResult` | Always `0.0` — Qdrant native RRF returns only a fused score. |
| `preferred_operation` | `QueryState` | Written only by the UI workflow override; `validate_dsl` accepts it as a parameter but `quant_engine._generate_dsl` calls `validate_dsl(raw_dict)` **without it**. |

---

## Test coverage

| | Status |
|---|---|
| pytest suite | **177 tests**, pure functions, zero network/DB/LLM, ~2 s. `backend/tests/`. |
| Network guard in tests | Implemented — patches `socket` **and** `psycopg2.connect` by name (libpq connects in C and bypasses Python sockets). `tests/conftest.py`. |
| Tests that assert **known defects** as current behaviour | Yes, deliberately — F1, F2, F7, F9, F12b are named in docstrings. When one starts failing, that is the fix landing. |
| Extraction regression gate | `scripts/regression_check.py`, 5 documents, **zero LLM calls**. |
| End-to-end eval | `scripts/eval_runner.py` over 91 golden questions across 4 datasets. Quota-gated; requires explicit approval per run. |
| Frontend tests | **None found.** |
| Integration tests hitting a live DB/Qdrant | **None found** — by design; the conftest guard forbids it. |

---

## Deployment

| | Status |
|---|---|
| Local | `docker compose up -d --build` — postgres, redis, qdrant, backend, frontend, worker, scheduler |
| Backend prod | Render (referenced throughout: 512 MB free tier, UTC logs, response buffering) |
| Frontend prod | Vercel (CORS allowlist + `*.vercel.app` regex in `main.py`) |
| Managed Postgres | Supabase (**note:** `docker-compose.yml:51` overrides `DATABASE_URL` to the *local* Postgres, so the local stack does not read Supabase. Two different databases, different document counts.) |
| Qdrant | Qdrant Cloud (`QDRANT_URL` must be the https URL; `http://qdrant:6333` means you are on the local collection and measurements do not count) |
| CI | **None found** — no `.github/workflows`. |
