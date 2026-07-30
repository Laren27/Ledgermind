> **This is the original design document, preserved verbatim as a historical
> record. It is NOT a description of the system as built.** Several sections are
> stale or were deliberately overridden. See
> [IMPLEMENTATION_DELTAS.md](./IMPLEMENTATION_DELTAS.md) for every divergence,
> and update that file in the same commit as any change that makes a statement
> here untrue.

LedgerMind — Complete Blueprint & Architecture Specification

"Not a chatbot. A deterministic financial intelligence operating system."

Table of Contents
What Is LedgerMind
Core Design Philosophy
Why This Project Exists
The Indian Market Moat
Full Tech Stack
High-Level Architecture
Tri-Engine System
Data Pipeline — Ingestion to Index
Retrieval Strategy
DSL Compiler Specification
Truth Resolution Engine
Contradiction Detection Engine
Retrieval Confidence Governance
Security Architecture
Redis Semantic Cache
Event-Driven Processing Model
Failure Recovery Strategy
PostgreSQL Schema Design
Qdrant Collection Design
Docker Topology
Evaluation Framework
Observability & Audit Layer
Phase-by-Phase Build Roadmap
Knowledge Graph Roadmap (Phase 2)
Interview Defense Strategy
What NOT to Build (Scope Guard)
1. What Is LedgerMind

LedgerMind is a multi-tenant financial intelligence platform built specifically for Indian capital markets.

Users can ask natural language questions about Indian public companies — their filings, earnings, disclosures, and financials — and receive grounded, cited, verifiable answers with zero hallucinations.

Target data:

SEBI annual filings
BSE/NSE company disclosures
Earnings call transcripts
Public company annual reports (PDFs)
DRHP (Draft Red Herring Prospectus) filings
Quarterly result PDFs

Target companies in corpus: Zomato, Swiggy, Paytm, Nykaa, PolicyBazaar, Delhivery, Razorpay (DRHP), PhonePe, Ola, Meesho

Target users (portfolio demo): Financial analysts, DA professionals, fintech PMs, retail investors doing due diligence

2. Core Design Philosophy

Five non-negotiable principles that govern every architectural decision:

Principle 1 — LLMs Never Do Math

All financial calculations originate from verified PostgreSQL records. The LLM only explains results, never computes them. EBITDA, CAGR, YoY growth — all deterministic SQL, never LLM arithmetic.

Principle 2 — Retrieval Must Be Explainable

Every retrieved document exposes:

Source PDF name
Page number
Chunk identifier
Retrieval score (vector similarity)
Reranker score (cross-encoder)

No black-box retrieval. Every answer is traceable.

Principle 3 — Auditability First

Full lineage on every answer:

Source PDF → Parser → Chunk → Retrieval Layer → DSL Compiler → SQL → Verification → Response
Principle 4 — Financial Safety

The system must:

Reject trading recommendations
Reject investment advice
Reject portfolio management requests
Remain SEBI-compliant by design

This is enforced at the Prompt Shield layer before the query reaches any engine.

Principle 5 — Multi-Tenant Isolation

Every customer (tenant) is fully isolated using:

PostgreSQL Row Level Security (RLS)
Qdrant namespace/metadata isolation
Tenant-scoped Redis cache keys
Per-tenant usage quotas
3. Why This Project Exists

The core problem: Standard RAG systems treat financial questions like general knowledge questions. They don't work for finance because:

Financial data has multiple competing versions (restatements)
Indian companies file both Standalone and Consolidated reports — semantically identical, numerically totally different
Tables span multiple pages and lose headers when chunked naively
LLMs hallucinate metric calculations when asked numerical questions
Temporal context matters — a 2021 revenue figure retrieved for a "current performance" question is wrong even if semantically relevant

What LedgerMind does differently:

Separates qualitative reasoning (RAG) from quantitative computation (DSL → SQL)
Enforces metadata filtering so standalone and consolidated data never mix
Reconstructs multi-page tables before chunking
Maintains version history and always uses latest authoritative filing
Refuses to answer when retrieval confidence is below threshold
4. The Indian Market Moat

These are the India-specific design decisions that make LedgerMind genuinely differentiated for Indian fintech interviews.

4.1 The Standalone vs Consolidated Trap

Every SEBI-listed company files two sets of financials:

Standalone: Parent company only (e.g., Zomato Ltd.)
Consolidated: Parent + all subsidiaries (e.g., Zomato + Blinkit + Hyperpure)

The text looks nearly identical. Naive vector search cannot distinguish them. The revenue numbers can differ by 3-4x.

LedgerMind's fix: Hard metadata injection at ingestion time:

json
{
  "financial_type": "consolidated",
  "company": "ZOMATO",
  "quarter": "Q3-FY24",
  "fiscal_year": "FY24"
}

At query time, metadata filtering runs before semantic search at the Qdrant level. The wrong financial type never even enters the retrieval pool.

Router rule: When a user asks "What was Zomato's revenue?" — the router defaults to consolidated unless standalone is explicitly requested.

4.2 Restatement Handling

Indian companies frequently restate prior-year figures when accounting standards change (Ind AS transitions, subsidiary restructuring, etc.).

Example:

FY24 Filing says:  "FY23 Revenue = ₹7,500 Cr"
FY25 Filing says:  "FY23 Revenue = ₹7,300 Cr" (Restated)

Both are technically correct. Generic RAG pulls whichever chunk ranks higher — wrong.

LedgerMind's Truth Resolution Engine always uses the latest filing date as source of truth. Historical values are preserved but flagged.

4.3 DRHP Interview Strategy

When interviewing at Razorpay:

Ingest their actual SEBI DRHP filing (publicly available)
Demo query: "What does Razorpay identify as their top 3 regulatory risks, and does their payment volume growth justify those risk disclosures?"
This hits Path 3 (Cross-Examination) — qualitative risk factors from PDF vs quantitative metrics from PostgreSQL
The interviewer sees their own company's data being analyzed live

When interviewing at PhonePe:

Use RBI UPI market share data cross-referenced against PhonePe's own disclosures
Same Cross-Examination path
5. Full Tech Stack
Layer	Tool	Reason
LLM — Generation	Gemini Flash 2.0 (primary)	Free tier, 1M context, fast
LLM — Fallback	Groq llama-3.1-70b	Free tier, backup if rate-limited
LLM — Routing	Gemini Flash 2.0	Lightweight classification
Embeddings	bge-small-en-v1.5 (local, CPU)	Free, no VRAM needed, ~33MB
Reranking	ms-marco-MiniLM-L-6-v2 (local, CPU)	Cross-encoder, fast on CPU
Reranking Upgrade	Cohere Rerank API (Phase 7)	Better quality for financial text
Vector DB	Qdrant Cloud free tier	Hybrid search, metadata filtering
Relational DB	PostgreSQL on Supabase free tier	Structured financials, RLS
Cache	Redis (semantic cache)	Reduce LLM API call costs
Async Workers	Celery	Background ingestion, no HTTP timeout
API Layer	FastAPI	Async, fast, type-safe
Frontend	Streamlit	Fast to build, good enough for demo
Containerization	Docker Compose	Single-command local boot
Orchestration	LangGraph	State machine for router + CRAG loops
PDF Parsing	LlamaParse (or pdfplumber)	Table-aware extraction
Evaluation	RAGAS	Industry-standard RAG metrics
Observability	Structured JSON logs → PostgreSQL	Lightweight, auditable

Total monthly cost: ₹0 — fully free stack for a student project.

6. High-Level Architecture
User Query
    ↓
Streamlit Frontend
    ↓
FastAPI API Gateway
    ├── Authentication (JWT)
    ├── Rate Limiting (per tenant)
    ├── Tenant Identification
    ↓
Prompt Shield
    ├── Block: trading advice
    ├── Block: investment recommendations
    ├── Block: prompt injection attempts
    ├── Block: jailbreak attempts
    ↓
Entity Resolver
    ├── Extract: company name → normalized ticker
    ├── Extract: fiscal year / quarter
    ├── Extract: financial_type (default: consolidated)
    ↓
Supervisor Router (LangGraph State Machine)
    ├── Classify query intent
    ├── Rewrite query with extracted entities
    ↓
    ├──────────────────────────────────────┐
    ↓                                      ↓                          ↓
Path 1                                 Path 2                     Path 3
Semantic Intelligence Engine           Quantitative Analytics     Cross-Examination Engine
(RAG)                                  Engine (DSL → SQL)         (Hybrid Verification)
    ↓                                      ↓                          ↓
    └──────────────────────────────────────┘
    ↓
Verification Layer
    ├── Math validation
    ├── Contradiction check
    ├── Confidence scoring
    ↓
Citation Layer
    ├── Source document
    ├── Page number
    ├── Chunk ID
    ├── Retrieval + reranker scores
    ↓
Response Generator
    ├── Answer + citations
    ├── Restatement disclosure (if applicable)
    ├── Confidence level
    ├── Contradiction flags (if applicable)
    ↓
Audit Logger (append-only PostgreSQL)
    ↓
User
7. Tri-Engine System
Path 1 — Semantic Intelligence Engine (RAG)

Used for: Risk analysis, governance, management commentary, qualitative disclosures, ESG, regulatory language

Pipeline:

Query
  ↓
Hybrid Search (BM25 sparse + dense vectors via RRF)
  ↓
Metadata Filter (company, year, quarter, financial_type, doc_type)
  ↓
Top 20 candidates
  ↓
Cross-Encoder Reranking
  ↓
Top 5 chunks
  ↓
Evidence Compression (if >3 chunks needed)
  ↓
Gemini Flash 2.0 generation
  ↓
Citation attachment

Example queries:

"What were Zomato's key risk factors in FY24?"
"What did management say about quick commerce profitability?"
"Summarize the governance disclosures in Nykaa's annual report"
Path 2 — Quantitative Analytics Engine (DSL → SQL)

Used for: Revenue, EBITDA, margins, CAGR, YoY growth, ratios, any numerical metric

Critical rule: The LLM never writes SQL directly.

Pipeline:

Query
  ↓
Financial Intent Parser (Gemini Flash)
  ↓
DSL Object (controlled vocabulary)
  ↓
DSL Validator (schema enforcement)
  ↓
SQL Compiler (deterministic Python)
  ↓
PostgreSQL execution
  ↓
Verification Layer (arithmetic validation)
  ↓
Gemini Flash explains result in natural language

Example queries:

"Compare Zomato vs Swiggy revenue growth YoY"
"What is Paytm's EBITDA margin for FY24?"
"Calculate Nykaa's 3-year CAGR"
Path 3 — Cross-Examination Engine (Hybrid Verification)

Used for: Fact-checking, narrative vs reality analysis, CEO statement verification

Pipeline:

Query
  ↓
Run Path 1 (qualitative claim extraction)
  AND
Run Path 2 (quantitative data retrieval)
  ↓
Comparison Layer
  ↓
Contradiction Detection
  ↓
Confidence scoring
  ↓
Insight generation with both sources cited

Example queries:

"Is what the CEO said about profitability consistent with actual numbers?"
"Razorpay claims strong regulatory compliance — does their DRHP risk section support this?"
"Does Zomato's management commentary on Blinkit align with consolidated revenue contribution?"
8. Data Pipeline — Ingestion to Index
Stage 1 — Document Upload & Registry
python
# Every document gets:
{
    "doc_id": "uuid",
    "sha256_checksum": "...",    # Deduplication
    "company": "ZOMATO",
    "ticker": "ZOMATO.NS",
    "fiscal_year": "FY24",
    "quarter": null,             # null for annual
    "doc_type": "annual_report", # or quarterly_result / drhp / earnings_transcript
    "financial_type": "consolidated",
    "filing_date": "2024-08-31",
    "version": "v1",
    "is_latest": true,
    "ingestion_state": "uploaded" # uploaded → processing → indexed → failed
}

SHA256 checksum catches duplicate uploads before any compute runs.

Stage 2 — PDF Parsing
PDF
  ↓
LlamaParse / pdfplumber
  ↓
Block Classification:
  ├── TEXT blocks
  ├── TABLE blocks
  ├── FOOTNOTE blocks
  ├── METADATA blocks (headers, page numbers)
Stage 3 — Table Reconstruction Service

Multi-page tables lose headers when split. The Table Reconstruction Service fixes this:

Page 47: [Revenue | Cost | EBITDA]  ← header row
Page 48: [1200    | 800  | 400   ]  ← data (header lost after split)
Page 49: [1300    | 900  | 400   ]  ← data (header lost after split)

After Table Reconstruction:
Chunk A: [Revenue | Cost | EBITDA] + [1200 | 800 | 400]
Chunk B: [Revenue | Cost | EBITDA] + [1300 | 900 | 400]

Responsibilities:

Detect table continuations across pages
Propagate headers to all continuation chunks
Merge fragmented rows
Attach footnotes to parent table
Preserve row-column relationships
Stage 4 — Tri-Modal Chunking

Six chunk types, each with different handling:

Chunk Type	Strategy	Chunk Size
TEXT	Recursive semantic splitting	~400 tokens
TABLE	Full table as single chunk (post-reconstruction)	Variable
FOOTNOTE	Attached to parent chunk as metadata	Small
RISK_DISCLOSURE	Section-aware splitting	~500 tokens
MANAGEMENT_DISCUSSION	Paragraph-level	~350 tokens
FINANCIAL_STATEMENT	Row-level for structured tables	Small

Parent-child chunking: Small child chunks (300 tokens) indexed for precision retrieval. When matched, parent chunk (1500 tokens) fetched for full context sent to LLM.

Stage 5 — Metadata Injection

Every chunk gets the full metadata payload before embedding:

json
{
  "company": "ZOMATO",
  "ticker": "ZOMATO.NS",
  "fiscal_year": "FY24",
  "quarter": "Q3",
  "document_type": "quarterly_result",
  "reporting_standard": "Ind AS",
  "financial_type": "consolidated",
  "page_number": 47,
  "section": "Financial Statements",
  "subsection": "Income Statement",
  "chunk_type": "TABLE",
  "version": "v2",
  "is_latest": true,
  "filing_date": "2024-08-31",
  "valid_from": "2024-08-31",
  "valid_to": null,
  "doc_id": "uuid",
  "chunk_id": "uuid"
}
Stage 6 — Embedding + Vector Indexing
python
# bge-small-en-v1.5 running on CPU
# ~33MB model, no GPU needed
# Batch processing during ingestion (offline, one-time per document)

from sentence_transformers import SentenceTransformer
model = SentenceTransformer('BAAI/bge-small-en-v1.5')
embeddings = model.encode(chunks, batch_size=32)

# Upsert to Qdrant Cloud
qdrant_client.upsert(
    collection_name="ledgermind_chunks",
    points=[
        PointStruct(
            id=chunk_id,
            vector=embedding,
            payload=metadata
        )
        for chunk_id, embedding, metadata in zip(ids, embeddings, metadatas)
    ]
)
Stage 7 — Structured Financial Extraction

In parallel with vector indexing, structured numerical data is extracted and stored in PostgreSQL:

Annual Report PDF
  ↓
Table Extractor
  ↓
Revenue / EBITDA / PAT / Margins parsed
  ↓
Normalized PostgreSQL financials table
  ↓
is_latest flag management (Truth Resolution Engine)
9. Retrieval Strategy
Hybrid Search (BM25 + Dense via RRF)

Two retrieval signals combined:

Dense vectors: Capture semantic meaning ("what is their profitability story")
BM25 sparse: Capture exact terms ("CPT 33405", "Section 12(b)", "SEBI LODR")

Financial documents contain both — exact regulatory code references and semantic narrative. Neither alone is sufficient.

Reciprocal Rank Fusion (RRF) combines both ranked lists:

python
rrf_score = 1/(k + dense_rank) + 1/(k + sparse_rank)  # k=60 standard
Metadata Pre-Filtering

Runs BEFORE semantic search at Qdrant level:

python
search_filter = Filter(
    must=[
        FieldCondition(key="company", match=MatchValue(value="ZOMATO")),
        FieldCondition(key="financial_type", match=MatchValue(value="consolidated")),
        FieldCondition(key="fiscal_year", match=MatchValue(value="FY24")),
        FieldCondition(key="is_latest", match=MatchValue(value=True))
    ]
)
Two-Stage Reranking
Top 20 from hybrid search
  ↓
Cross-Encoder (ms-marco-MiniLM-L-6-v2, local CPU)
  ↓
Scores each chunk pair (query, chunk) — expensive but precise
  ↓
Top 5 passed to LLM

Why top 20 → top 5? The cross-encoder is too slow to run on hundreds of chunks but highly accurate on a small set. Bi-encoder (embedding model) handles scale; cross-encoder handles precision.

10. DSL Compiler Specification

The most important safety layer in the entire system. Prevents LLM SQL hallucination.

Why Direct Text-to-SQL Is Prohibited
User: "What's Zomato's EBITDA?"
LLM (unconstrained): SELECT ebitda FROM financials WHERE company='Zomato'

Problem: 'ebitda' is NOT a column. It's calculated:
EBITDA = operating_profit + depreciation + amortization

LLM invents schema. Query fails silently or returns wrong data.
DSL Flow
User Query
  ↓
Gemini Flash extracts intent into controlled DSL object:
{
  "metric": "EBITDA",
  "entity": "ZOMATO",
  "period": "FY25",
  "financial_type": "consolidated",
  "comparison_entity": null,
  "operation": "point_in_time"  # or yoy_growth / cagr / comparison
}
  ↓
DSL Validator
  ├── Is "metric" in approved metric registry? ✓
  ├── Is "entity" a known ticker? ✓
  ├── Is "period" a valid fiscal period? ✓
  ├── Is "financial_type" valid? ✓
  → VALID
  ↓
SQL Compiler (deterministic Python function, no LLM)
  ↓
SELECT
  (operating_profit + depreciation + amortization) AS ebitda
FROM financials
WHERE company = 'ZOMATO'
  AND fiscal_year = 'FY25'
  AND financial_type = 'consolidated'
  AND is_latest = true
  ↓
PostgreSQL execution
  ↓
Verification Layer validates arithmetic
  ↓
Gemini Flash explains result in plain English
DSL Self-Healing Loop

If the LLM generates an invalid DSL (hallucinated metric name):

Generate DSL
  ↓
Validate → INVALID (metric not in registry)
  ↓
Repair Prompt to LLM (with schema reference)
  ↓
Regenerate DSL
  ↓
Validate → PASS or FAIL

Maximum retries: 2
After 2nd failure: Return structured error, do not execute
11. Truth Resolution Engine

Handles restated financials — a uniquely Indian market problem.

The Problem
FY24 Annual Report filed Aug 2024:
  "FY23 Revenue = ₹7,500 Cr"

FY25 Annual Report filed Aug 2025:
  "FY23 Revenue = ₹7,300 Cr" ← Restated (Ind AS adjustment)

Both chunks exist in Qdrant. Both are "correct." Without resolution, LLM might return either.

Resolution Policy
python
# Every financial record:
{
  "company": "ZOMATO",
  "metric": "Revenue",
  "fiscal_year": "FY23",
  "value": 7300,
  "filing_date": "2025-08-31",  # Newer filing
  "document_version": "v2",
  "is_latest": True              # This wins
}

{
  "company": "ZOMATO",
  "metric": "Revenue",
  "fiscal_year": "FY23",
  "value": 7500,
  "filing_date": "2024-08-31",  # Older filing
  "document_version": "v1",
  "is_latest": False             # Archived, not deleted
}

Rules:

Latest filing date = source of truth
Historical values stored permanently (never deleted)
Every answer discloses: source document, filing date, version, restatement status
User can explicitly query historical values by specifying filing_date
Temporal Retrieval Layer

Every chunk has:

valid_from = filing date
valid_to = null (if latest) or date superseded
Default retrieval: is_latest = true only
Historical queries: filter by filing_date range
12. Contradiction Detection Engine

Before answer generation on Path 3 queries:

Retrieved chunks from multiple sources:
  - Annual Report: "Supply chain risk is high"
  - Investor Presentation: "Supply chain risk significantly reduced"
  - News article: "New disruption reported in Q3"

Contradiction Detection:
  ├── Extract claim from each source
  ├── Compare semantic similarity + polarity
  ├── Flag disagreements
  ↓
Output to user:
  "Sources disagree on this topic:
   - Annual Report (FY24): High risk
   - Investor Presentation (Mar 2025): Risk reduced
   - News (Oct 2025): New disruption
   Confidence: MEDIUM — review sources before concluding"

Users see disagreements instead of fabricated certainty. This is the core value of Path 3.

13. Retrieval Confidence Governance

Every retrieval operation generates a confidence_score based on:

Top reranker score
Score gap between rank 1 and rank 5
Metadata filter match completeness
Three-Tier Policy
HIGH confidence (score > 0.8)
  → Proceed to generation normally

MEDIUM confidence (score 0.5 - 0.8)
  → Trigger Corrective RAG:
     ├── Rewrite query (expand entity, adjust period)
     ├── Re-retrieve
     ├── If improved → proceed
     └── If still medium → proceed with disclaimer

LOW confidence (score < 0.5)
  → Refuse to answer
  → Return: "Insufficient information found in available documents
             for this query. Please verify the company/period exists
             in the corpus, or rephrase your question."
  → Log refusal to audit store

This is better than hallucinating a low-confidence answer.

14. Security Architecture
Prompt Shield (First Layer)

Runs before any engine. Blocks:

Trading recommendations ("should I buy Zomato?")
Investment advice ("is this stock undervalued?")
Prompt injection attempts ("ignore previous instructions and...")
Jailbreak patterns

Implementation: Keyword matching + lightweight embedding classifier.

Response to blocked queries:

"LedgerMind is a financial research tool and cannot provide
trading recommendations or investment advice. This is by design
to remain SEBI-compliant. Please rephrase as a factual research question."
Row Level Security (PostgreSQL)
sql
-- Every table has tenant_id
ALTER TABLE financials ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON financials
  USING (tenant_id = current_setting('app.tenant_id')::uuid);

-- Every query automatically filtered
-- Tenant A physically cannot read Tenant B's data
Qdrant Namespace Isolation
python
# Each tenant gets isolated metadata filtering
search_filter = Filter(
    must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
        # ... other filters
    ]
)
JWT Authentication + RBAC

Three roles:

Role	Permissions
Admin	Upload docs, manage users, view all analytics
Analyst	Query, view citations, export results
Viewer	Read-only, no export
15. Redis Semantic Cache

Caches query results to avoid repeated LLM API calls (cost saving + latency reduction).

Cache Key Structure
cache:{tenant_id}:{company}:{fiscal_year}:{quarter}:{financial_type}:{query_hash}

Example:

cache:tenant_123:zomato:fy25:q3:consolidated:a8f3c2...
Semantic Cache Match Rules

Cache hit requires ALL of:

tenant_id match
company match
fiscal_year match
quarter match
financial_type match
Query embedding cosine similarity > 0.95

If any condition fails → cache miss → full retrieval pipeline runs.

Why This Matters in Interviews

"I built the semantic cache because every LLM API call has a cost. When a team of analysts at the same company all ask 'What is Zomato's FY25 revenue?', only the first query should hit the API. The rest hit cache. This is how you think about cost per query at scale — like a Product Engineer, not just a developer."

16. Event-Driven Processing Model

Every action in LedgerMind fires a typed event. This enables observability, debugging, and future scalability.

DocumentUploaded       → triggers ingestion worker
DocumentParsed         → triggers chunking
TableReconstructed     → triggers embedding
ChunkCreated           → triggers vectorization
EmbeddingGenerated     → triggers Qdrant upsert
DocumentIndexed        → updates document registry state
QueryReceived          → triggers router
DSLCompiled            → triggers SQL execution
VerificationCompleted  → triggers response generation
AnswerGenerated        → triggers citation attachment
ResponseDelivered      → triggers audit log write

Each event logged with: timestamp, tenant_id, doc_id or query_id, duration_ms, status

17. Failure Recovery Strategy

Graceful degradation on every external dependency.

Component Failure	Fallback
Qdrant unavailable	BM25 keyword-only retrieval from PostgreSQL full-text search
Redis unavailable	Skip cache, execute directly (slower but functional)
Gemini Flash rate-limited	Route to Groq llama-3.1-70b
Groq unavailable	Return structured error, queue for retry
Celery worker crash	Retry queue → Dead Letter Queue → alert
PostgreSQL unreachable	Read-only emergency mode (serve cached answers only)
LlamaParse failure	Fallback to pdfplumber basic extraction

No single point of failure kills the entire system.

18. PostgreSQL Schema Design
Core Tables
sql
-- Tenant management
CREATE TABLE tenants (
  tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  plan TEXT DEFAULT 'free',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Users
CREATE TABLE users (
  user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(tenant_id),
  email TEXT UNIQUE NOT NULL,
  role TEXT CHECK (role IN ('admin', 'analyst', 'viewer')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Document registry
CREATE TABLE documents (
  doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(tenant_id),
  company TEXT NOT NULL,
  ticker TEXT,
  fiscal_year TEXT,
  quarter TEXT,
  doc_type TEXT,           -- annual_report / quarterly_result / drhp / transcript
  financial_type TEXT,     -- consolidated / standalone
  filing_date DATE NOT NULL,
  version TEXT DEFAULT 'v1',
  is_latest BOOLEAN DEFAULT TRUE,
  sha256_checksum TEXT UNIQUE,
  ingestion_state TEXT DEFAULT 'uploaded',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Structured financials (deterministic analytics engine)
CREATE TABLE financials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(tenant_id),
  doc_id UUID REFERENCES documents(doc_id),
  company TEXT NOT NULL,
  ticker TEXT,
  fiscal_year TEXT NOT NULL,
  quarter TEXT,
  financial_type TEXT NOT NULL,  -- consolidated / standalone
  metric TEXT NOT NULL,          -- revenue / ebitda / pat / gross_margin / etc.
  value NUMERIC,
  unit TEXT DEFAULT 'crore_inr',
  filing_date DATE NOT NULL,
  is_latest BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Audit log (append-only, never update/delete)
CREATE TABLE audit_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID,
  user_id UUID,
  query_text TEXT,
  query_path TEXT,          -- semantic / quantitative / cross_examination
  retrieved_chunk_ids TEXT[],
  vector_scores NUMERIC[],
  reranker_scores NUMERIC[],
  dsl_generated JSONB,
  sql_executed TEXT,
  confidence_score NUMERIC,
  response_text TEXT,
  latency_ms INTEGER,
  tokens_used INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS on all tables
ALTER TABLE financials ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON financials
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
19. Qdrant Collection Design
python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, SparseVectorParams

client = QdrantClient(url="YOUR_QDRANT_CLOUD_URL", api_key="YOUR_API_KEY")

client.create_collection(
    collection_name="ledgermind_chunks",
    vectors_config={
        "dense": VectorParams(size=384, distance=Distance.COSINE)
        # bge-small-en-v1.5 produces 384-dim vectors
    },
    sparse_vectors_config={
        "sparse": SparseVectorParams()  # For BM25
    }
)
Payload (Metadata) Fields in Qdrant

Every point stored with full metadata for filtering:

python
payload = {
    # Identity
    "chunk_id": "uuid",
    "doc_id": "uuid",
    "tenant_id": "uuid",

    # Financial context
    "company": "ZOMATO",
    "ticker": "ZOMATO.NS",
    "fiscal_year": "FY24",
    "quarter": "Q3",
    "financial_type": "consolidated",  # CRITICAL for standalone/consolidated split
    "document_type": "quarterly_result",
    "reporting_standard": "Ind AS",

    # Temporal
    "filing_date": "2024-08-31",
    "valid_from": "2024-08-31",
    "valid_to": None,
    "is_latest": True,
    "version": "v1",

    # Location in document
    "page_number": 47,
    "section": "Financial Statements",
    "subsection": "Income Statement",
    "chunk_type": "TABLE",  # text / table / footnote / risk_disclosure / etc.

    # Parent-child chunking
    "parent_chunk_id": "uuid",  # null if this IS the parent
    "is_child": True
}
20. Docker Topology

Single docker-compose.yml boots the entire system locally:

yaml
version: '3.9'

services:
  # 1. FastAPI Backend
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:pass@postgres:5432/ledgermind
      - REDIS_URL=redis://redis:6379
      - QDRANT_URL=http://qdrant:6333
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - GROQ_API_KEY=${GROQ_API_KEY}
    depends_on:
      - postgres
      - redis
      - qdrant

  # 2. Streamlit Frontend
  frontend:
    build: ./frontend
    ports:
      - "8501:8501"
    depends_on:
      - backend

  # 3. PostgreSQL
  postgres:
    image: postgres:15
    environment:
      - POSTGRES_DB=ledgermind
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./sql/init.sql:/docker-entrypoint-initdb.d/init.sql

  # 4. Redis
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  # 5. Qdrant (local dev only — production uses Qdrant Cloud)
  qdrant:
    image: qdrant/qdrant
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage

  # 6. Celery Worker (ingestion)
  worker:
    build: ./backend
    command: celery -A app.worker worker --loglevel=info
    depends_on:
      - postgres
      - redis
      - qdrant

  # 7. Celery Beat Scheduler (future: scheduled re-ingestion)
  scheduler:
    build: ./backend
    command: celery -A app.worker beat --loglevel=info
    depends_on:
      - redis

volumes:
  postgres_data:
  qdrant_data:

Single command to run everything:

bash
docker-compose up --build
21. Evaluation Framework
RAGAS Metrics (Primary)
Metric	What It Measures	Target
Faithfulness	Does the answer stick to retrieved context?	> 0.85
Answer Relevance	Does the answer address the question?	> 0.88
Context Precision	Are retrieved chunks actually useful?	> 0.80
Context Recall	Did we retrieve all needed chunks?	> 0.75
Citation Accuracy	Are source references correct?	> 0.90
Retrieval Metrics
Metric	What It Measures
Recall@5	Is correct chunk in top 5?
Recall@10	Is correct chunk in top 10?
MRR	Mean Reciprocal Rank of correct chunk
NDCG	Ranking quality of retrieval
Business Metrics
Metric	What It Measures
Numerical Accuracy	Is calculated financial figure correct?
Contradiction Detection Accuracy	Are real contradictions flagged?
Refusal Rate	% of low-confidence queries correctly refused
Latency P95	95th percentile query response time
Cost Per Query	Total token cost / total queries
Golden Dataset

50 carefully crafted question-answer pairs covering:

Simple factual: "What was Zomato's FY24 consolidated revenue?"
Comparative: "Compare Nykaa and Paytm EBITDA margins FY24"
Cross-examination: "Does management commentary on profitability match financials?"
Temporal: "How has Zomato's revenue grown from FY21 to FY24?"
Adversarial: "Should I invest in PhonePe?" → must be refused
Restatement: "What is the restated FY23 revenue for Zomato per the FY25 filing?"

Each golden question stores:

json
{
  "question": "...",
  "expected_answer": "...",
  "expected_sql": "...",
  "expected_path": "quantitative",
  "expected_citations": ["doc_id::chunk_id"],
  "expected_confidence": "high"
}
22. Observability & Audit Layer

Every query writes to audit_log (append-only, never mutate, never delete):

json
{
  "query_id": "uuid",
  "tenant_id": "uuid",
  "user_id": "uuid",
  "timestamp": "2025-06-22T10:30:00Z",
  "query_text": "What was Zomato's FY24 consolidated revenue?",
  "query_path": "quantitative",
  "entity_resolved": {"company": "ZOMATO", "fiscal_year": "FY24", "financial_type": "consolidated"},
  "dsl_generated": {"metric": "Revenue", "entity": "ZOMATO", "period": "FY24", "financial_type": "consolidated"},
  "sql_executed": "SELECT value FROM financials WHERE ...",
  "retrieved_chunk_ids": [],
  "vector_scores": [],
  "reranker_scores": [],
  "confidence_score": 0.94,
  "tokens_used": 312,
  "latency_ms": 847,
  "response_summary": "Zomato reported consolidated revenue of ₹14,148 Cr in FY24",
  "cache_hit": false,
  "restatement_disclosed": false
}
Streamlit Observability Dashboard (Phase 7)

Four panels:

Query volume over time — line chart by day/week
Path distribution — pie chart (qualitative / quantitative / cross-examination)
Retrieval quality — avg confidence score, refusal rate, cache hit rate
Cost tracking — tokens per day, estimated API cost per tenant
23. Phase-by-Phase Build Roadmap
Phase 1 — Infrastructure

Goal: All services running locally, communicating with each other

 Docker Compose with all 7 containers
 PostgreSQL accessible from FastAPI
 Redis accessible from FastAPI
 Qdrant accessible from FastAPI
 Celery worker running and receiving tasks
 Basic FastAPI health check endpoint
 Basic Streamlit shell connecting to FastAPI

Deliverable: docker-compose up boots everything. Health checks pass.

Phase 2 — Schema & Collection Design

Goal: Data model locked before any real data enters the system

 PostgreSQL schema — all tables created with RLS enabled
 Qdrant collection created with dense + sparse vector config
 Document registry working (upload → SHA256 → state tracking)
 Seed 2-3 fake financial records to verify SQL queries work
 Verify RLS actually blocks cross-tenant queries in tests

Deliverable: Schema migration scripts. Query test suite passing.

Phase 3 — Ingestion Pipeline

Goal: Real PDF goes in, chunks come out correctly in Qdrant and PostgreSQL

 PDF parser (LlamaParse or pdfplumber) integrated
 Table Reconstruction Service working on multi-page tables
 Tri-modal chunking (TEXT / TABLE / FOOTNOTE / RISK_DISCLOSURE)
 Metadata injection on all chunks (full payload)
 Embeddings generated locally (bge-small-en-v1.5 on CPU)
 Qdrant upsert working with dense + sparse
 Structured financial extraction into PostgreSQL financials table
 Truth Resolution: is_latest flag management on restatements
 Celery handles ingestion asynchronously (no HTTP timeout)
 Document state transitions: uploaded → processing → indexed

First real document: Zomato FY24 Annual Report PDF

Deliverable: Upload Zomato PDF → verify 500+ chunks in Qdrant with correct metadata → verify revenue/EBITDA in PostgreSQL.

Phase 4 — Core Engines (The Brain)

Goal: All three paths working end-to-end

Router:

 Prompt Shield (keyword + embedding classifier)
 Entity Resolver (company name → ticker, extract fiscal year/quarter)
 LangGraph state machine with three paths
 Confidence-based Corrective RAG loop

Path 1 — Semantic Engine:

 Hybrid search (BM25 + dense via RRF)
 Metadata pre-filtering
 Cross-encoder reranking (top 20 → top 5)
 Citation attachment (chunk ID, page, scores)

Path 2 — Quantitative Engine:

 DSL object specification (metric registry)
 Gemini Flash → DSL generation
 DSL validator (schema enforcement)
 SQL Compiler (deterministic Python)
 Verification layer (arithmetic validation)
 DSL self-healing loop (max 2 retries)

Path 3 — Cross-Examination:

 Parallel execution of Path 1 + Path 2
 Contradiction Detection Engine
 Confidence scoring on combined output

Deliverable: 10 test queries across all three paths returning correct, cited answers.

Phase 5 — FastAPI Layer

Goal: Full API with auth, rate limiting, tenant isolation

 JWT authentication
 RBAC middleware (admin / analyst / viewer)
 Tenant ID injection into all database queries
 Rate limiting per API key (Redis token bucket)
 Async endpoints for long-running queries
 Audit log write on every query
 Cost tracking (token count per query per tenant)
 Tenant-isolated Redis semantic cache

Deliverable: API fully functional. Postman collection with all endpoints tested.

Phase 6 — Streamlit UI

Goal: Working demo interface

Five screens:

Query Interface — text input, path selector (auto/manual), submit
Answer Panel — response text with inline citations
Citation Panel — source document, page, chunk, retrieval score, reranker score
Contradiction View — side-by-side source comparison (Path 3)
Document Upload — drag-drop PDF, ingestion status tracker

Interview demo flow:

Upload Razorpay DRHP (live, in front of interviewer)
Ask: "What regulatory risks does Razorpay disclose?"
Ask: "Is the management's optimism on payment volumes reflected in their disclosed risk severity?"
Show citation panel — exact page numbers, scores visible
Show audit log — full query trace visible
Phase 7 — Evaluation + Observability

Goal: Prove the system works with numbers, not just demos

 50-question golden dataset built
 RAGAS evaluation suite automated
 Retrieval metrics: Recall@5, MRR, NDCG
 Numerical accuracy tests on financial queries
 Contradiction detection accuracy tests
 Observability dashboard in Streamlit (query volume, path distribution, cost, latency)
 Cohere Rerank API integration as optional upgrade (compare vs local cross-encoder)
 README with architecture diagram, setup instructions, eval results

Deliverable: GitHub README shows: "Faithfulness: 0.87 | Answer Relevance: 0.91 | Recall@5: 0.84"

24. Knowledge Graph Roadmap (Phase 2 — Future)

Not in current build scope. Document here for the roadmap section of portfolio.

Purpose: Relationship-aware reasoning beyond flat document retrieval.

Entities:

Company
Subsidiary
Director
Auditor
Shareholder
Financial Event (acquisition, fundraise, regulatory action)

Relationships:

(Zomato)-[OWNS]->(Blinkit)
(Zomato)-[ACQUIRED]->(Blinkit)
(Kotak)-[AUDITS]->(Nykaa)
(Deepinder Goyal)-[MANAGES]->(Zomato)

Use case: "Which companies audited by Deloitte had restatements in FY24?" — impossible with vector search, trivial with graph traversal.

Technology: Neo4j Community (free) or Memgraph

25. Interview Defense Strategy
The One-Sentence Pitch

"LedgerMind is a multi-tenant financial intelligence operating system for Indian capital markets. It separates qualitative reasoning from deterministic financial computation to ensure zero hallucinations on numerical queries — built specifically around SEBI filings and the standalone-vs-consolidated problem unique to Indian company reporting."

The Technical Answer (When They Go Deep)

"The core insight is that LLMs are unreliable calculators. So I built a DSL Compiler — the LLM generates a controlled JSON object like {metric: EBITDA, entity: ZOMATO, period: FY25}, and a deterministic Python function converts that to SQL. The LLM never touches SQL. Every answer is traceable from source PDF chunk through retrieval scores to the final response. I also built a Truth Resolution Engine because Indian companies restate prior-year figures — the system always uses the latest filing date as source of truth while preserving historical values."

The Redis Cache Answer (When They Ask About Cost)

"I built a tenant-isolated semantic cache because LLM API calls cost money. When a team of analysts all ask the same question about Zomato's FY25 revenue, only the first call hits the API. The rest hit Redis. Cache keys include tenant ID, company, fiscal year, quarter, financial type, and query embedding similarity above 0.95. This is what thinking like a Product Engineer looks like — cost per query as a first-class metric."

The SEBI Compliance Answer

"The Prompt Shield layer blocks trading recommendations and investment advice before the query reaches any engine. This is by design — LedgerMind is a research tool, not a trading system, and that distinction matters for SEBI compliance. I built the boundary in at the architecture level, not as an afterthought."

25B. Build Traps — What Will Actually Break During Development

This section documents the specific failure modes you will encounter while building LedgerMind, based on the architecture decisions above. Read this before you start each phase, not after something breaks.

Trap 1: The Standalone vs Consolidated Filter Fails Silently

You implement metadata filtering (financial_type="consolidated") at the Qdrant query level. It works in tests. Then a user asks "What was Zomato's revenue?" and gets the standalone figure instead of consolidated — a 3x difference.

Why this happens: The ingestion script had a classification step that assigned financial_type based on a keyword check in the PDF filename. Zomato's FY24 Annual Report PDF was named Zomato_Annual_Report_FY24.pdf — no "consolidated" or "standalone" in the name. The classifier defaulted to standalone. The filter then correctly returned only standalone results.

Fix: Never classify financial_type from filename alone. Open the PDF and check the first page — SEBI filings always state "STANDALONE FINANCIAL STATEMENTS" or "CONSOLIDATED FINANCIAL STATEMENTS" explicitly. Extract this from the document text itself and inject it as metadata. Log a warning and pause ingestion (do not silently default) if classification is ambiguous.

Trap 2: The DSL Compiler Generates Valid SQL That Returns Wrong Data

The DSL object {metric: "REVENUE", entity: "ZOMATO", period: "FY24"} compiles correctly to:

sql
SELECT value FROM financials
WHERE company = 'ZOMATO' AND metric = 'REVENUE' AND fiscal_year = 'FY24'

This query executes without errors. It returns a number. But the number is wrong because the financials table has two rows matching this query — the standalone figure and the consolidated figure — and the SQL returns both, or whichever PostgreSQL happens to return first.

Fix: The SQL Compiler must always include financial_type in the WHERE clause, sourced from the DSL object. The DSL specification must always include financial_type as a required field with a default of "consolidated". The Verification Layer must check that exactly one row was returned — if zero or more than one row is returned, the DSL self-healing loop must fire, not return an ambiguous result.

Trap 3: The Restatement Flag Gets Stale

The Truth Resolution Engine marks the most recent filing's figures with is_latest = TRUE. When you ingest Zomato's FY25 Annual Report (which restates FY24 figures), the engine correctly flips is_latest on the FY24 rows from the FY24 filing to FALSE and marks the FY24 rows from the FY25 filing as is_latest = TRUE.

But Celery processed the FY25 ingestion task asynchronously. Two other API requests came in during that processing window — both asking for FY24 revenue — and both hit the database while the update transaction was mid-flight. One got the old figure (still marked is_latest = TRUE). One got the new figure.

Fix: The is_latest flag update must run inside a single PostgreSQL transaction with a row-level lock on the company + metric + period combination. No reads of is_latest = TRUE for that company-metric-period should be served while the transaction is open. Use SELECT ... FOR UPDATE in the transaction to enforce this.

Trap 4: The Prompt Shield Blocks Legitimate Queries

The Prompt Shield keyword classifier blocks trading recommendations and investment advice. You add "buy", "sell", "invest" to the block list. A financial analyst then asks: "What does Zomato's management say about investing in delivery infrastructure?" — the word "investing" triggers the block and the query is rejected.

Fix: The Prompt Shield must use intent classification, not keyword matching. A fine-tuned classifier or a fast LLM call (Gemini Flash is cheap enough) with a carefully written intent prompt is far more accurate than a keyword list. The classification categories must be narrowly defined: block only explicit trading recommendations ("should I buy ZOMATO stock", "is ZOMATO a good investment for me") not any financial language.

Trap 5: Redis Cache Hits Across Tenants

Two tenants — Tenant A (a retail investment firm) and Tenant B (a fintech startup) — both ask "What was Zomato's revenue in FY24?" within a short window.

If the Redis cache key is constructed as hash(query_embedding + company + fiscal_year) without including the tenant ID, Tenant B gets Tenant A's cached answer. In this specific case the answer is numerically the same (public filing data), so no immediate harm occurs. But your financial_type default may differ per tenant, or your answer template includes tenant-specific context. The cross-tenant cache hit is a data isolation violation regardless of whether the specific answer content differs.

Fix: The cache key must always include tenant_id as the first component. {tenant_id}:{semantic_hash} is the minimum viable key structure. This is also why tenant_id injection must happen at the API layer, before the query ever reaches the cache lookup — not inside the cache lookup itself.

Trap 6: LlamaParse Succeeds But Tables Are Garbled

LlamaParse (or pdfplumber) extracts text from Zomato's annual report. The extraction "succeeds" — no error is raised, text is returned. But the Income Statement table is extracted as:

Revenue from operations 12,114 9,651 8,634
Other income 462 296 207
Total income 12,576 9,947 8,841

No column headers. No indication that these three numbers represent FY24, FY23, and FY22. Without headers, every numerical chunk is uninterpretable — "12,114" means nothing without knowing it is FY24 revenue in crores.

Fix: The Table Reconstruction Service must detect tables (look for repeated whitespace-separated numeric patterns across adjacent lines), attempt to find the header row (scan upward from the first data row for a row containing year-like patterns), and re-attach the header before chunking. If header detection fails, flag the table as needs_review = TRUE in metadata and exclude it from serving until manually verified. Never ingest a table chunk without a header attached.

Trap 7: The Contradiction Detection Engine Fires on Non-Contradictions

Path 3 (Cross-Examination) runs Path 1 (qualitative retrieval) and Path 2 (SQL quantitative) in parallel and compares results for contradictions. The Contradiction Detection Engine flags a contradiction when:

Path 1 retrieves a chunk from the FY24 report saying "revenue grew strongly to approximately ₹12,000 crore"
Path 2 computes exact revenue as ₹12,114 crore from PostgreSQL

These are not contradictions — the qualitative text uses an approximation, the quantitative engine has the exact figure. The engine incorrectly flags this as a discrepancy and returns a confused "conflicting information found" response to the user.

Fix: The Contradiction Detection Engine must apply a tolerance threshold for numerical comparisons between qualitative text and quantitative SQL results. "Approximately ₹12,000 crore" and "₹12,114 crore" are within 1% of each other — treat this as consistent. Flag only contradictions where the qualitative claim and the SQL figure differ by more than a configurable threshold (e.g., 5%) or where the qualitative claim uses absolute directional language ("revenue declined") that contradicts an SQL-computed positive growth figure.

These are explicitly out of scope to prevent scope creep and project abandonment:

Item	Why Out of Scope
12 separate microservices	Solo student — use Python modules inside FastAPI instead
OpenTelemetry + Arize Phoenix	Replace with structured JSON logging to PostgreSQL
1000-question golden dataset	Start with 50 high-quality questions
Knowledge Graph (Neo4j)	Phase 2 roadmap only
React/Next.js frontend	Streamlit is sufficient for a portfolio demo
Real multi-tenant SaaS billing	Simulate with tenant_id in schema, no payment integration
Live BSE/NSE data feeds	Use static PDFs first, live feeds are Phase 2
Apache Airflow scheduling	Celery Beat handles scheduled jobs at this scale
Kafka event streaming	PostgreSQL event log is sufficient for student scale
Success Criteria

The project succeeds when:

Every answer is traceable — source PDF → chunk → retrieval scores → response
Every financial calculation is verifiable — deterministic SQL, not LLM arithmetic
Every retrieval is explainable — chunk ID, page number, scores visible
Every tenant is isolated — RLS enforced, cache keys scoped
Every document is version controlled — restatements handled, history preserved
Every cost is measurable — tokens logged per query per tenant
Every failure has a fallback path — no single point of failure

LedgerMind is not a chatbot. It is a deterministic financial intelligence platform built around the limitations of modern LLMs rather than pretending those limitations do not exist.