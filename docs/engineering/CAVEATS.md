# LedgerMind — Caveats & Technical Debt Journal

Every temporary implementation, shortcut, fragile assumption, missing
validation, stubbed field and known defect found by reading the tree.

**This file does not hide debt because the system currently works.** Several
entries below have *no victim today* — that is recorded as part of the entry,
along with the trigger that would create one.

**Relationship to other docs.** `docs/audit/repo_audit_20260811.md` is the
formal audit (F1–F13). `docs/IMPLEMENTATION_DELTAS.md` §D is the latent-risk
register. This file is the working journal: it includes those, plus items found
in this pass that are not in either.

Opened: 2026-08-20. Sorted by severity, then discovery order.

---

## [CAVEAT-001] Request body can override the JWT's tenant_id

**Location:** `backend/app/api/query.py:110` and `:156`

```python
tenant_id = payload.tenant_id or current_user.get("tenant_id", "default")
```

**Problem.** `QueryRequest` accepts an optional `tenant_id` from the **request
body**, and it is preferred over the value in the verified JWT. That value then
flows into `make_initial_state()` → `QueryState["tenant_id"]` → `SET LOCAL
app.tenant_id` in `quant_engine._execute_sql`, the Qdrant `tenant_id` filter in
`retriever._build_filter`, and the `audit_log` row.

So an authenticated user of tenant A can post `{"query": "...", "tenant_id":
"<tenant-B-uuid>"}` and the RLS policy, the vector filter and the audit row will
all faithfully scope to tenant B. Every defence works exactly as designed — they
are all being told the wrong tenant by the layer above them.

**Why it exists.** The repository does not state why. **Likely rationale —
inferred:** an override for local testing or scripted evaluation from before
auth existed; `eval_runner.py` and the smoke-test scripts predate the auth layer.

**Current impact.** With one seeded tenant this is unexploitable in practice.
The moment a second tenant exists it is a full cross-tenant read. Note the
docstring in `app/db/session.py:8-11` states the opposite as an assumption:
*"As long as `state["tenant_id"]` is sourced from the verified JWT (see
api/query.py), those per-call connections are already RLS-correct."* That
assumption is what `api/query.py` breaks.

**Severity:** **Critical** (as a multi-tenant product). Low (as a single-tenant
demo today).

**Current workaround:** only one tenant is seeded.

**Proper solution:** drop `tenant_id` from `QueryRequest` and read it only from
`current_user`. If an override is genuinely needed for evals, gate it behind
`require_role("admin")` **and** an explicit environment flag, and record in the
audit row that an override was used.

**Status:** Open.

---

## [CAVEAT-002] The "load-bearing" DSL operation override can never fire

**Location:** `backend/app/engines/dsl_compiler.py:94-102`, reached from
`backend/app/engines/quant_engine.py:268`

**Problem.** `DSLValidator.validate()` takes a `preferred_operation` argument
and, when present, overrides the model's chosen operation. The block is
commented `⚡ PROGRAMMATIC OPERATION OVERRIDE (Load-Bearing Guardrail)`.

There is exactly **one** call site of `validate_dsl` in the codebase:

```python
validation = validate_dsl(raw_dict)          # quant_engine.py:268 — no second arg
```

`state["preferred_operation"]` is written by `router_node` when the UI sends
`execution_context.intended_operation` (the peer-comparison view sends
`"growth_comparison"`), and is then **never read by anything**. The override is
dead code.

**Why it exists.** The path override (`enforce_path` → `state["path"]`) and the
operation override were added together; only the first was wired to a consumer.

**Current impact.** The peer-comparison view still usually produces
`growth_comparison`, but only because `DSL_SYSTEM_PROMPT` contains an explicit
rule for "who grew revenue faster" — i.e. the *deterministic* guardrail is
absent and the *probabilistic* one is doing the work. That is the inverse of
this project's stated preference.

**Severity:** Medium.

**Proper solution:** pass `state.get("preferred_operation")` through
`_generate_dsl` into `validate_dsl` — **or** delete the parameter and the UI
field. Either is defensible; having a guardrail that looks wired and is not is
the worst of the three.

**Status:** Open. Found 2026-08-20; not in the F1–F13 audit.

---

## [CAVEAT-003] A page whose column layout fails to parse is skipped silently

**Location:** `backend/app/ingestion/financial_extractor.py:785`

```python
try:
    column_map, column_centers = detect_column_layout(pdf_path, page_idx)
except Exception as e:
    continue          # `e` is bound and never used; nothing is logged
```

**Problem.** A page of a financial statement that raises during column detection
produces **no records and no log line**. The ingest completes, all gates pass,
and the missing rows look identical to rows the document never contained.

**Why it exists.** The repository does not say. **Likely rationale — inferred:**
defensive skipping so one malformed page cannot abort a whole ingest — a
reasonable goal, implemented without the log line that would make it observable.

**Current impact.** Unknown by construction: there is no signal to count. This
is a plausible contributor to audit **F6** (686 of 1437 rows unanchored) and to
any "why is this metric missing?" investigation.

**Severity:** Medium — silent data loss in the extraction path.

**Proper solution:** `logger.warning("Column layout failed on page %s: %s",
page_number, e)` before the `continue`. One line; no behaviour change.

**Status:** Open.

---

## [CAVEAT-004] The DSL schema cannot express "no metric" or "no period"

**Location:** `backend/app/engines/quant_engine.py:64` (`GeminiDSLResponse`)

**Problem.** `metric: str` and `fiscal_year: str` are **required**. A model
constrained to emit a value cannot answer "the user named none" — it invents
one, the invention validates cleanly, compiles to SQL, executes, and is stamped
`sql_verified=True`.

Measured instances, all live:
- `"What was Paytm's EBITDA for FY26?"` → returned `total_expenses`
  (₹8,523 Cr), verified. (2026-07-29)
- `"...the 207 crore impairment of loans and investments in associates..."` →
  returned `exceptional_items` (₹−186 Cr), verified. (2026-07-30)
- PQ012, `"financial exposure to Paytm Payments Bank"` — names no metric at all
  → `exceptional_items`, verified, **stable across five runs**. (2026-08-01)
- `"does management commentary align with its PAT decline?"` →
  `fiscal_year="FY25"` invented from nothing. (2026-07-29)

**Why it exists.** Making the fields optional would move the problem into the
validator rather than removing it, and Gemini's `response_schema` enforcement is
what makes the structured path reliable in the first place.

**Current impact.** Mitigated by **three separate regex guards**, all running
over the *raw query* before any LLM call, because the raw query is the only
place the user's real intent still exists:
- Stage 0 — `_query_names_derived_metric` (`quant_engine.py:297`)
- Stage 0b — `_query_names_unqueryable_metric` (`quant_engine.py:349`)
- Stage 0c — `_query_lacks_metric_anchor` (`cross_engine.py:94`)
- plus the period-assumption guard (`quant_engine.py:373`)

**Severity:** High (mechanism), Medium (residual, after the guards).

**Proper solution:** split extraction and classification into two calls, as
`router.py:38-40` already recommends for a related problem — or add an explicit
`metric_named: bool` field so the model can say "none" in-schema.

**Status:** Open, mitigated. PQ012 carries `known_deliberate_failure` in the
golden dataset; **do not "fix" it by editing its expectation** (`CLAUDE.md` §1.4).

---

## [CAVEAT-005] Every stored value is asserted to be in crore (audit F3)

**Location:** `backend/app/ingestion/financial_extractor.py:457, 560, 592, 607, 632`

**Problem.** `unit="crore_inr"` is hardcoded at five sites. No scale detection
exists anywhere in the ingest path.

**Current impact.** No demonstrated victim: where a scale declaration was found
it said crore (TITAN pages 7/8/14/15). But three of four scanned documents
declared **nothing**, and the ZOMATO annual report was not scanned at all.

**The non-obvious part** — and the reason this is not a small fix:
`clean_financial_number` contains

```python
val = re.sub(r'\.(?=\d{3}$)', '', val)      # reads "17.634" as a misread comma
```

which is correct for crore-scale Indian filings and would silently multiply
every genuine three-decimal value by 1000 in a lakh- or million-denominated one.
**The number cleaner would have to become scale-aware before normalisation could
run at all.** So "store native value + unit" is the only representation that
does not require rewriting it.

**Severity:** High at the trigger; dormant today.

**Proper solution:** detect the scale declaration, store it, and **fail closed** —
no declaration found must mean `needs_review=True`, never a crore default. A
silent 100× error on a headline figure is invisible to every arithmetic guard in
the file, because nothing about the digits is corrupt.

**Trigger:** the first ingest of a filing declaring a non-crore scale, or any
issuer switching scale inside one document.

**Status:** Open. Full scoping in `IMPLEMENTATION_DELTAS.md` §D — F3.

---

## [CAVEAT-006] The `financial_type` retrieval filter is inert (audit F7)

**Location:** `backend/app/engines/retriever.py:189-197`

**Problem.** The filter admits `financial_type == requested OR financial_type ==
"unknown"`. But only `FINANCIAL_STATEMENT` blocks inside a detected section get
a real `financial_type`; every narrative chunk keeps `"unknown"` by design
(`chunker.py:363`). So the OR admits nearly everything.

Measured: the condition excludes **17 of 2531** chunks.

**Current impact.** A "standalone" question retrieves consolidated narrative and
vice versa. Measured at 23/85 bleed in the audit.

**Severity:** Medium.

**Proper solution:** audit **F7** proposes splitting `"unknown"` into *narrative*
(genuinely not applicable — an N/A) and *undetermined* (classification failed).
The filter can then admit narrative and exclude undetermined.

**Status:** Open, third in the audit queue.

---

## [CAVEAT-007] A two-issuer query drops one issuer and denies it exists (audit F14)

**Location:** `backend/app/engines/router.py:21` (`RouterResponse.company`)

**Problem.** `company` is single-valued. A query naming two issuers either nulls
it or collapses to one — and then asserts the other is absent from the corpus.
Observed live returning *"no company named Eternal"* while ETERNAL has 732 rows.

**Severity:** High — same class as F3: a confident wrong claim, not a visible
failure.

**Proper solution (stated in `CLAUDE.md`):** `companies: list[str]` plus an
IN-style filter. **Not** a patch to the single field.

**Status:** Open. Second in the audit queue, after F3.

---

## [CAVEAT-008] The restatement confidence penalty has no producer (audit F5)

**Location:** `backend/app/engines/confidence.py:86`

```python
if state.get("restatement_disclosed"):
    ... cap tier to medium ...
```

`restatement_disclosed` is initialised `False` in `make_initial_state` and
**nothing ever writes it**. `response_generator`'s docstring says it will
("flag `restatement_disclosed=True` so confidence.py can apply its penalty") —
that code does not exist.

**Current impact.** The first real restatement in the corpus answers at full
confidence with no disclosure.

**Severity:** Medium.

**Status:** Open — audit F5, recorded but not queued as a task.

---

## [CAVEAT-009] `cache_hit` is surfaced in an admin dashboard and is structurally always 0

**Location:** `backend/app/api/metrics.py:44-50`, `QueryState.cache_hit`,
`audit_log.cache_hit`, `QueryResponse.cache_hit`

**Problem.** The Redis semantic cache (blueprint §15) was never built. Redis is
the Celery broker and a health check. `cache_hit` is set `False` at state
creation and never written again, so `AVG(CASE WHEN cache_hit …)` has no
producer and always returns 0.0.

The SQL comment says so, and the frontend type says **"Do not render"** — but
the field is still in `MetricsResponse`, i.e. it is one dashboard tile away from
being reported as a measurement.

**Severity:** Low technically; **Medium as an honesty risk** — a 0.0% cache-hit
rate rendered as a stat is a fabricated measurement, which is precisely what
this project's UI mandate forbids.

**Proper solution:** remove it from `MetricsResponse` until a cache writes the
column.

**Status:** Open, documented in three places.

---

## [CAVEAT-010] `tokens_used` has no producer

**Location:** `state.py:149/213`, `audit_writer.py:97`, `response_shaping.py:81`

Initialised to 0; no call site increments it. It is written to `audit_log` and
exposed at admin tier as though it were a measurement. Neither `generate_text`
nor `generate_structured` reads usage metadata off the provider response, though
both providers return it.

**Severity:** Low. **Status:** Open. Same honesty class as CAVEAT-009.

---

## [CAVEAT-011] JWT in `localStorage`

**Location:** `frontend/lib/auth.ts:1-12`

Self-documented: *"localStorage is readable by any script on the page, which is
a real XSS exposure surface you'd want to close before this has real users."*
The token carries `tenant_id` and `role` and lives 2 hours.

**Severity:** Medium (accepted for a solo-user portfolio project).

**Proper solution:** an httpOnly, Secure, SameSite cookie set by the backend,
with CSRF protection. **Status:** Open, knowingly accepted.

---

## [CAVEAT-012] CORS allows every `*.vercel.app` origin with credentials

**Location:** `backend/app/main.py:39-51`

```python
allow_origin_regex=r"https://.*\.vercel\.app",
allow_credentials=True,
allow_methods=["*"], allow_headers=["*"],
```

Any Vercel-hosted page — including one an attacker deploys in 30 seconds — is an
allowed origin. Exposure is limited because auth is a `Bearer` header from
`localStorage` rather than a cookie, so `allow_credentials` does not carry the
session automatically; but the regex is far broader than the one deployment it
exists for.

**Severity:** Low–Medium. **Proper solution:** pin the preview-deployment
prefix, or read the allowed origins from an environment variable.
**Status:** Open.

---

## [CAVEAT-013] A new Postgres connection is opened per SQL statement

**Location:** `backend/app/engines/quant_engine.py:419-425`,
`backend/app/engines/audit_writer.py:27`, `backend/app/db/session.py:24`

Every helper calls `psycopg2.connect(...)` directly. No pool anywhere. A
`yoy_growth` query opens **two** connections, `growth_comparison` opens **four**,
plus one for the latest-fiscal-year lookup and one for the audit write.

**Current impact.** Fine at demo concurrency. Against Supabase's pooled
connection limits under real load it is the first thing that will break, and the
symptom (`FATAL: too many connections`) will look like a database problem rather
than an application one.

**Severity:** Medium at scale, Low today. **Proper solution:**
`psycopg2.pool.ThreadedConnectionPool` behind `db_transaction()`, and route
every caller through it. **Status:** Open.

---

## [CAVEAT-014] Audit-log writes are best-effort

**Location:** `backend/app/engines/audit_writer.py:117-122`

The write is wrapped in `try/except Exception` that logs and returns. This is
deliberate (`blueprint §17 graceful degradation` — an audit failure must not
block a delivered answer) and it is the right call for the user.

The caveat is the consequence: **an append-only audit trail with best-effort
writes has gaps that are invisible from inside the trail.** Nothing counts
failed writes, and nothing reconciles request IDs issued against rows landed.

**Severity:** Medium for an auditability-first product.
**Proper solution:** a failure counter or a local spool that retries.
**Status:** Open, deliberate trade-off.

---

## [CAVEAT-015] Two divergent databases, and compose points at the wrong one (audit F11)

**Location:** `docker-compose.yml:51`

`env_file: .env` supplies the Supabase `DATABASE_URL`, and then an
`environment:` block **overrides it** with the local Docker Postgres. The two
databases have different document counts (11 local vs 9 Supabase).

`CLAUDE.md` §6 states the general rule — *"Never override via an `environment:`
block — that exact override invalidated every local measurement for a week"* —
and then records that `DATABASE_URL` is currently doing exactly that.

**Severity:** High for measurement integrity; not a runtime defect.
**Workaround:** state which database every measurement came from. Always.
**Status:** Open.

---

## [CAVEAT-016] Qdrant holds chunks whose `doc_id` has no row in `documents`

**Location:** data, not code. Recorded in `IMPLEMENTATION_DELTAS.md` §D.

139 of 2555 points reference a `doc_id` absent from `documents`. **All 115 PAYTM
chunks are affected — Paytm's entire semantic corpus is uncitable.** A live
query returned `tier=high` with five citations, every one resolving to `None`.

Root cause is structural: Qdrant has no foreign keys and no cascade, and
deterministic chunk IDs only prevent duplication while `doc_id`, page numbering
and split positions all hold.

**Severity:** High — this is the "citation that cannot be checked" failure the
citation-floor removal was about, arriving by a different route.
**Status:** Open. `scripts/purge_orphaned_chunks.py` exists; read the
**CORRECTION** entry in the deltas file before running it — a previous repair
attempt degraded production.

---

## [CAVEAT-017] `metric_anchor_phrases()` matches substrings, not words

**Location:** `backend/app/engines/cross_engine.py:85` (`_ANCHOR_RE`)
+ `registry.metric_anchor_phrases()`

The anchor set includes two- and three-character aliases (`da`, `gov`), and
Stage 0c tests them against raw lowercased text — so `da` matches inside
"**da**ta" and `gov` inside "**gov**ernance".

**Not a defect today**, because Stage 0c's polarity is inverted: it is consulted
to find *nothing*, so a broader set makes the guard fire **less**. The
consequence is that the effective anchor set is much broader than the docstring
implies.

**Trigger:** introducing word-boundary matching would **narrow** the set and
could unguard queries currently caught. It must be measured against the full
golden set first.

**Severity:** Low today; a trap for the next person who "fixes" it.
**Status:** Open by design.

---

## [CAVEAT-018] `_KNOWN_TICKERS` is larger than the corpus

**Location:** `backend/app/engines/router.py:56`, `entity_resolver.COMPANY_REGISTRY`

SWIGGY, NYKAA, DELHIVERY and POLICYBAZAAR resolve to valid tickers with **zero
documents**. So "unknown company" and "known company, no documents" are
different situations, and only the first produces a refusal. The second falls
through to a filtered search that finds nothing, then to a low-confidence
refusal — a *different* message for a *different* reason, by accident rather
than design.

**Severity:** Low. **Proper solution:** a corpus-presence check after
resolution, with its own refusal reason. **Status:** Open, recorded in
`CLAUDE.md`.

---

## [CAVEAT-019] Company onboarding requires a code edit (audit F1)

**Location:** `backend/app/ingestion/entity_resolver.py:18`

`COMPANY_REGISTRY` is a Python list. Adding a company means editing source and
redeploying. `resolve_company` is now **exact-alias-only** — the substring
fallback was removed (F1 closed) because it silently misfiled "Titan Biotech
Limited" (a separately listed company) into TITAN.

The fix is correct; the caveat is the remaining ergonomics: a near-miss name now
fails ingest outright with a clear error, which is better, but onboarding is
still a deploy.

**Severity:** Low (correctness), Medium (operability).
**Status:** F1 closed; onboarding-as-data is unbuilt.

---

## [CAVEAT-020] `_get_gemini` declares a global it never assigns

**Location:** `backend/app/llm/client.py:154-165`

```python
global _gemini_client
...
return genai.Client(...)        # never assigned to _gemini_client
```

The module-level `_gemini_client` is permanently `None`. This is **not** a bug
in behaviour — the comment explains that the client is deliberately rebuilt
because the timeout is a per-client property and the two entry points need
different bounds. The `global` statement and the module variable are leftovers.

**Severity:** Trivial. **Proper solution:** delete both, or memoise per timeout
value. **Status:** Open, cosmetic.

---

## [CAVEAT-021] Prompt Shield false positives

**Location:** `backend/app/engines/prompt_shield.py:161, 171`

- `\bDAN\b` blocks any query containing "DAN" as a standalone word.
- `\bsystem\s*prompt\b` blocks a legitimate question that happens to discuss
  system prompts.

Both are cheap to trigger and both return the minimal `INJECTION_RESPONSE`,
which deliberately does not explain what matched — so a user cannot tell a false
positive from a real block.

**Severity:** Low. **Status:** Open; accepted trade for keeping injection blocks
uninformative.

---

## [CAVEAT-022] No CI, no frontend tests

No `.github/workflows`. The 177-test pytest suite is run manually
(`docker compose exec -T -w /app backend env PYTHONPATH=/app python -m pytest
tests/ -q`, ~2 s). `frontend/` has no test runner configured.

Note what the suite deliberately does *not* cover: it is pure-function only, with
a conftest guard that patches `socket` **and** `psycopg2.connect` by name (libpq
connects in C and bypasses Python sockets). Anything touching the network, the
DB or an LLM is untested by construction, and that includes every graph node's
integration.

**Severity:** Medium. **Status:** Open — F10 partially closed (the suite exists;
automation does not).

---

## [CAVEAT-023] `--min-chunks` defaults to 100 and TITAN legitimately produces 24

**Location:** `backend/app/ingestion/pipeline.py`, `chunker.py`

A fully successful TITAN ingest **exits 1** on the post-write completion gate.
The instruction is to read the log rather than re-run, and to pass
`--min-chunks 20` for TITAN.

This is audit **F9** — a constant fitted to the current corpus. Its siblings:
`SCAN_CHAR_LIMIT` (6000), the section continuation windows, `TOP_K_RETRIEVAL`
(20), `TOP_K_RERANK` (5).

**Severity:** Low. **Status:** Open by design; the alternative (a per-document
threshold) has no caller yet.

---

## [CAVEAT-024] `UPLOAD_DIR.mkdir()` runs at import time

**Location:** `backend/app/api/documents.py:44-45`

Importing the module creates `/tmp/ledgermind_uploads`. Harmless today; it means
the module cannot be imported in a read-only filesystem, which is a common
container hardening setting.

**Severity:** Trivial. **Status:** Open.

---

## Closed / superseded

| ID | Item | Outcome |
|---|---|---|
| F1 | Substring company matching misfiled distinct issuers | **Closed 2026-08-11** — exact-alias-only; `tests/test_entity_resolver.py` guards it |
| F2 | Router failure → unfiltered whole-tenant search | **Closed 2026-08-12** — refusal edge to `audit_writer`; see ED-010. Partial by construction |
| F8 | Ingest Gate 4 could not observe its own run | **Closed** — gates rescoped to this run's `doc_ids` |
| F10 | No test suite | **Partially closed** — 177 tests exist; no CI |
| F11 | `check_migrations` gave wrong advice about two databases | **Closed** (the tool); the two databases remain — CAVEAT-015 |
| F12 | Documented-vs-actual drift | **Closed** for the named instances; F12(b) is asserted as current behaviour in tests |
| F13 | Loose ends (`Ellipsis` collection, dead payload fields) | **Closed** |
| — | Citation relevance floor (0.05) | **Removed 2026-08-08.** The constant was not wrong; allowing `retrieved_chunks` and `citations` to diverge was. **Do not reintroduce** — read `semantic_engine.py:61-93` first. |
| — | `DISABLE_LOCAL_RERANKER` | **Removed 2026-07-30** — it silently returned unscored chunks, making every semantic query refuse |
