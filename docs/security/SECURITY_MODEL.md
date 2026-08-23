# LedgerMind — Security Model

**Framing rule for this document:** a mechanism existing is not the same as a
threat being closed. Every section below states the **threat**, the **defence**,
the **implementation** and the **limitation**. Where the limitation is material,
it links to the caveat that tracks it.

---

## 0. Threat model in one paragraph

LedgerMind holds financial filings belonging to organisations (tenants) and
answers questions about them. The assets are: **another tenant's documents and
numbers**, **the audit trail**, and **the system's own credibility** (an answer
that looks verified but is not). The adversaries are: an authenticated user of
one tenant reaching another tenant's data; an unauthenticated attacker; and — a
category most systems ignore — **the user's own question**, as a vehicle for
prompt injection or for extracting regulated investment advice.

---

## 1. Authentication

**Threat.** Unauthenticated access to filings and query machinery.

**Defence.** Email/password login issuing a signed JWT.

**Implementation.**
- `POST /auth/login` → `app/auth/service.py:authenticate_user`
- Password verification: **bcrypt directly**, not passlib
  (`app/core/security.py:1-9` — passlib's `CryptContext` reads
  `bcrypt.__about__.__version__`, removed in bcrypt ≥ 4.1, which breaks its
  bcrypt backend on any current install).
- Token: HS256, `settings.JWT_SECRET`, **2-hour** expiry, claims
  `sub` (user_id), `tenant_id`, `role`, `iat`, `exp`.
- Verification: `app/auth/dependencies.py:get_current_user`, a FastAPI
  dependency on every protected route. `ExpiredSignatureError` and
  `InvalidTokenError` both become 401.
- Wrong email and wrong password return the **same** message
  ("Invalid email or password") — no user enumeration.

**Limitations.**
- **No refresh tokens, no revocation, no logout server-side.** A stolen token is
  valid until `exp`. Logout is `localStorage.removeItem`.
- **The secret is symmetric.** Anything that can read `JWT_SECRET` can mint an
  admin token for any tenant.
- **No rate limiting on `/auth/login`** — online password guessing is
  unthrottled. Blueprint §5/§14 specified per-tenant rate limiting;
  `IMPLEMENTATION_DELTAS.md` §A records it as **not built**.
- **No password policy, no lockout, no MFA.** Users are seeded by migration
  (`sql/migrations/007_seed_users.sql`); there is no self-service registration,
  which limits the exposure.

---

## 2. Authorization — three roles, two enforcement points

**Threat.** A viewer reading machinery they should not see; a non-admin
uploading documents or reading tenant-wide metrics.

**Defence.** Role in the token, enforced at the **route** and again at the
**field** level.

**Implementation.**

*Route level* — `require_role(minimum)` with a rank ladder
`viewer(0) < analyst(1) < admin(2)`:

| Route | Minimum role |
|---|---|
| `POST /api/documents/upload` | admin |
| `GET /api/documents/pending` | admin |
| `GET /api/metrics` | admin |
| `POST /api/query`, `/api/query/stream` | any authenticated user |

*Field level* — `app/api/response_shaping.py:role_filtered_response`:

| Tier | Sees |
|---|---|
| viewer | answer text, confidence **tier**, citations **without scores**, contradiction **type + severity only** |
| analyst | + `confidence_score`, full citations with `reranker_score`, full contradictions, `dsl_object`, `sql_query`, `sql_result`, `sql_verified`, `error_node` |
| admin | + `latency_ms`, `tokens_used`, `cache_hit`, `llm_provider`, `llm_model`, `reranker_backend` |

**Two properties worth internalising.**

1. **It fails closed.** An unrecognised role — a typo, a null, a role added to
   the database but not to this file — gets the **viewer** payload. Without the
   explicit `role not in _KNOWN_ROLES` check the function falls through every
   `if` and returns the **full admin response**. Fail-open and fail-closed are
   one line apart here.
2. **Filtering is a disclosure decision, not an execution one.** The graph always
   runs in full, and `audit_log` always receives the complete record. Only the
   HTTP response is filtered. A viewer's query is audited identically to an
   admin's.

**Limitations.**
- Roles are coarse (three, global). No per-document or per-company scoping.
- The SSE trace applies the same rules but in a second place
  (`api/query.py:_trace_detail` mirrors the viewer/analyst split for the
  `quant_engine` node) — a second copy of a rule, which is the drift class this
  project has been bitten by elsewhere.

---

## 3. Tenant isolation

**Threat.** Tenant A reading tenant B's documents, financials or audit rows.

**Defence.** Defence in depth: Postgres RLS plus a mandatory Qdrant payload
filter. (`CLAUDE.md` also lists "scoped Redis keys" as part of this invariant;
**no Redis key scoping exists in the code** — Redis is the Celery broker and a
health check only, since the semantic cache was never built.)

### 3a. PostgreSQL Row-Level Security

`sql/init.sql:135-166`, extended by migrations 006/009/010/011.

```sql
ALTER TABLE financials ENABLE ROW LEVEL SECURITY;
ALTER TABLE financials FORCE  ROW LEVEL SECURITY;   -- applies to the owner too

CREATE POLICY tenant_isolation_financials ON financials
    USING (
        CASE WHEN coalesce(current_setting('app.tenant_id', TRUE), '') = ''
             THEN FALSE
             ELSE tenant_id = current_setting('app.tenant_id', TRUE)::UUID
        END
    );
```

Three details, each load-bearing:

- **`FORCE`** means even the table owner is subject to the policy. Without it, a
  connection that happens to be the owner bypasses RLS entirely.
- **The `CASE`** exists because **`AND` is not a short-circuit operator in SQL**.
  A naïve `setting <> '' AND tenant_id = setting::uuid` can still evaluate the
  cast and error on an empty GUC. See `IMPLEMENTATION_DELTAS.md` §14.
- **`SET LOCAL`, never bare `SET`** (`db/session.py:13-17`). `SET LOCAL` is
  transaction-scoped and clears on COMMIT/ROLLBACK. A bare `SET` on a
  pooled/reused connection leaks one tenant's setting into the next request.

**The one deliberate exception:** login runs with `tenant_id=None`, relying on
the `auth_bootstrap_lookup` policy (migration 006) which permits `SELECT` on
`users` only when `app.tenant_id` is unset. That is the chicken-and-egg case —
you cannot scope to a tenant before you know which tenant the user belongs to.
`authenticate_user`'s docstring says: **do not reuse this pattern elsewhere.**

**Privilege separation:** `ledgermind_app` is `NOSUPERUSER NOCREATEDB
NOCREATEROLE` with `SELECT, INSERT, UPDATE` only — **no DDL, and no DELETE
anywhere**. `audit_log` additionally has no `UPDATE` grant: it is append-only by
permission, not by convention.

### 3b. Qdrant

`retriever._build_filter` always emits `tenant_id` and `is_latest` as `must`
conditions, before any optional filter. There is no code path that queries the
collection without them.

### 3c. The hole

**This is where the model breaks: see [CAVEAT-001].** `api/query.py:110` prefers
a `tenant_id` supplied in the **request body** over the one in the verified JWT.
Every mechanism above then works perfectly, on the wrong tenant. Currently
unexploitable because only one tenant is seeded — but it is the single highest
priority security item in this repository.

**Limitation of RLS generally:** a forgotten `SET app.tenant_id` returns **zero
rows, not an error**. That is a safe failure for confidentiality and a dangerous
one for diagnosis — it has repeatedly been misread as "the data is missing".

---

## 4. Prompt injection and jailbreak

**Threat.** A user overriding system instructions, extracting the system prompt,
or steering the model into behaviour outside its remit.

**Defence.** The **Prompt Shield** — the first node in the graph, before entity
resolution, before routing, before any engine.

**Implementation.** `app/engines/prompt_shield.py`. Pure regex, no LLM, no
network, synchronous. Seven injection/jailbreak patterns: instruction-override
(`ignore … previous instructions`), `disregard …`, `you are now a …`,
`act as … without restrictions`, `DAN` / `do anything now`,
`pretend … no restrictions`, `system prompt` / `system instructions`.

Injection blocks return a **minimal** message deliberately — they do not explain
what triggered, so an attacker gets no feedback signal.

**Limitations — and these are real.**
- **A regex blocklist is not a defence against prompt injection.** It catches
  the naïve forms. Paraphrase, encoding, translation, or splitting an
  instruction across a long query all pass.
- **The shield inspects the user's query only.** It does **not** inspect
  retrieved document text. An adversarial instruction embedded in an ingested
  PDF flows straight into `SYNTHESIS_SYSTEM_PROMPT`'s context window. The
  ingestion gate (`gate.py`) filters for filing-shaped documents, which raises
  the bar, and uploads are admin-only — but indirect injection via corpus
  content is **not defended against**.
- False positives exist (CAVEAT-021): `\bDAN\b` blocks any query containing
  "Dan" as a word.

**What genuinely limits blast radius here is architecture, not the shield.** A
successful injection can influence *prose*. It cannot make the system emit an
unverified number as verified: the quantitative path's numbers come from SQL
compiled by Python from a validated eight-field object, and the model never sees
the schema, never writes SQL, and never performs arithmetic. That is a
structural containment, and it is worth more than the regex list.

---

## 5. Regulatory compliance (SEBI)

**Threat.** The system giving investment advice, which is a regulated activity.

**Defence.** The same shield, first category: eleven patterns matching the
**structure of an advice request**, not the words.

```text
"should I buy Zomato?"                     → BLOCK  (first-person buy decision)
"what did Zomato buy?"                     → PASS   (third-party factual)
"is Zomato a good investment?"             → BLOCK  (recommendation request)
"what was Zomato's investment in Blinkit?" → PASS   (factual acquisition)
"investing in delivery infrastructure"     → PASS   (business context)
```

Covered categories: `trading_advice`, `investment_advice`, `price_prediction`,
`portfolio_advice`. Blocked queries get a compliance message that **shows the
user how to rephrase** — and are still written to `audit_log` with
`query_path='blocked'`, because a refusal is an audit-worthy event.

**Limitations.** Recall, again. "Thoughts on Titan at these levels?" matches
nothing. There is no LLM-based classifier behind the regex, deliberately — that
would put a probabilistic component in the compliance path.

---

## 6. SQL injection

**Threat.** Query text reaching the database as SQL.

**Defence.** Two independent layers.

1. **The LLM never writes SQL.** It emits a DSL object; `SQLCompiler` builds the
   statement from **fixed string literals** with `%s` placeholders. There is no
   string interpolation of model output into SQL anywhere in
   `dsl_compiler.py`.
2. **All parameters are bound**, via psycopg2's parameterisation
   (`cur.execute(sql, params)`), never f-strings.

Values that *are* interpolated into the SQL text are structural only — the
`AND quarter = %s` vs `AND quarter IS NULL` branch — and even there the value is
bound.

**Limitation.** `SET LOCAL app.tenant_id = %s` is parameterised, which is
correct. If a future change makes it an f-string, the tenant boundary becomes
injectable. Worth a comment in the code.

---

## 7. Input validation

| Input | Validation | Gap |
|---|---|---|
| `query` | Prompt Shield; empty string → blocked | **No length limit.** A megabyte query is accepted and sent to the LLM. |
| Upload file | `.pdf` extension; 50 MB cap enforced **during** the streaming write, not from a trailing header | Extension only — no magic-byte check. `pdfplumber` will reject a non-PDF later. |
| Upload metadata | Required form fields; `financial_type` is deliberately **not** accepted (auto-detected from content, blueprint Trap 1) | `company`, `fiscal_year`, `quarter`, `filing_date` are **caller-asserted and never checked against the document** — audit **F4**. A misfiled document is invisible; it has already happened twice. |
| DSL from the LLM | `DSLValidator` — required fields, enum checks, operation-specific preconditions, cross-field consistency | Cannot catch a *valid DSL for the wrong question* (CAVEAT-004). |
| `execution_context` | **None.** `Dict[str, Any]`, straight into the router. | `enforce_path` lets a client force a path. Placed *after* the F2 refusal deliberately, so an override cannot route past a failed entity resolution — but it is unvalidated client input steering the pipeline. |

---

## 8. Secrets management

**Implementation.** All secrets from environment variables, loaded from `.env`
via `env_file:` in compose. `.env` is gitignored; `.env.example` carries
placeholders. Keys read: `GEMINI_API_KEY`, `GROQ_API_KEY`, `COHERE_API_KEY`,
`QDRANT_API_KEY`, `SUPABASE_SECRET_KEY`, `JWT_SECRET`, `DATABASE_URL`.

`JWT_SECRET` has **no default** in `Settings` — the app will not start without
it. `GEMINI_MODEL` likewise raises rather than defaulting (for evidential
reasons, not security ones).

**Limitations.**
- `sql/init.sql` hardcodes the dev password `app_dev_pass`, and
  `docker-compose.yml` repeats it. Fine for local dev; must never reach a
  deployed database.
- `ADMIN_DATABASE_URL` **bypasses RLS** by design (migrations and maintenance).
  Its only protection is that no request-path code reads it.
- No secret rotation procedure is documented.

---

## 9. Logging and data exposure

**Good practice already in place:**
- Query text is truncated to 60 characters in shield logs.
- `response_summary` is capped at 500 characters in `audit_log`.
- Login failures log `pgcode`/`pgerror`, never the password.
- Errors are logged single-line with `pgcode` because Render truncates
  multi-line tracebacks.

**Exposure to be aware of:**
- `audit_log.query_text` stores the **full** query. If a user pastes something
  sensitive it is retained indefinitely with no TTL and no redaction, in a table
  with no DELETE grant. That is a deliberate durability property with a privacy
  cost.
- `HTTPException(detail=f"Pipeline execution failed: {str(e)}")`
  (`api/query.py:127`) returns the raw exception string to the client. That can
  disclose internal structure. Prefer a generic message plus a `request_id` the
  user can quote.
- Frontend `localStorage` holds the token (CAVEAT-011).

---

## 10. Availability

| Mechanism | Where |
|---|---|
| Bounded LLM timeouts (20 s) + Groq failover | `app/llm/client.py` |
| Qdrant client timeout (10 s), ~25× measured warm latency | `retriever.py:56` |
| Upload size cap enforced during the write | `api/documents.py:76-85` |
| Audit-write failure never blocks the response | `audit_writer.py:117` |
| Celery `soft_time_limit=540` / `time_limit=600`, `acks_late=True`, 2 retries | `pipeline.py` |
| SSE heartbeat every 15 s so idle proxies do not close the connection | `api/query.py:32` |
| Unbounded SSE queue so a disconnected client cannot stall the pipeline and strand the audit write | `api/query.py:169-171` |

**Limitations.** No rate limiting at any layer. No request size limit. No
circuit breaker on Cohere — every query retries it and falls back on failure.
An unbounded queue is a deliberate memory trade in favour of audit completeness.

---

## 11. Honest summary

**Genuinely strong:**
- RLS with `FORCE`, `SET LOCAL`, and a `NOSUPERUSER` app role with no DELETE.
- Append-only audit by grant, not by convention.
- Fail-closed field-level RBAC.
- Structural containment of LLM output: the model cannot emit an unverified
  number as verified, regardless of what it is told.

**Genuinely weak:**
- **[CAVEAT-001]** — the body-supplied `tenant_id` override defeats the tenant
  boundary from above. Fix this before a second tenant exists.
- No rate limiting anywhere, including on login.
- Indirect prompt injection via corpus content is undefended.
- Token theft has no mitigation: `localStorage`, no revocation, 2-hour window.

**Do not describe LedgerMind as secure because these mechanisms exist.** Describe
it as: *a system whose tenant boundary is enforced in the database, whose LLM
cannot fabricate a verified number by construction, and which currently trusts
an authenticated caller's stated tenant_id.*
