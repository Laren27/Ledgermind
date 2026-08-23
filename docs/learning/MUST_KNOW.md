# LedgerMind — Must Know

The final revision document. **Recall only** — if you need the repository open,
it does not belong here.

**How this file grows.** Each day's `MUST REMEMBER` items land in the matching
section. Sections are seeded with their headings and fill in as the course
proceeds; empty ones are marked *(pending Day N)*.

**Current state, 2026-08-23.** Sections **7, 22, 23, 24, 25 and 26** are filled,
from Days 38-47. **Sections 1-6 and 8-21 remain pending**, because Days 1-37
were written in an earlier session that did not carry their `MUST REMEMBER`
blocks across. That gap is stated rather than hidden - each of those days ends
with its own `MUST REMEMBER` block, and moving them here is a mechanical pass
that has not been done.

**What goes here vs. elsewhere:**

| This file | `GLOSSARY.md` | `LEARNING_PROGRESS.md` |
|---|---|---|
| Facts to **recall cold** | Terms to **look up** | What you **currently know** |
| "JWT is signed, not encrypted" | "JWT — a signed, base64 JSON claims object…" | "JWT: can explain ✓, can debug ✗" |

Opened 2026-08-23.

---

# 0. The ten sentences

If everything else is forgotten, these remain. They are the ones that change how
you *read* the codebase, not just what you know about it.

1. **A wrong answer with a ✓ tick is worse than a refusal.** Every design choice
   that looks over-engineered follows from this.
2. **The LLM never does arithmetic.** It emits an eight-field DSL object; Python
   compiles it to SQL and does the maths.
3. **A `reranker_score` without its `reranker_backend` is meaningless.** Cohere
   returns `[0,1]`; local ONNX returns logits ~`[-12,+2]`; the fallback fires on
   network flap.
4. **RLS returns 0 rows when `app.tenant_id` is unset — and 0 rows reads as "no
   data".** Always set it first.
5. **A JWT is signed, not encrypted.** Readable by anyone, forgeable by nobody.
6. **The response schema is part of the prompt.** Declaring a field is a model
   input change, whether or not any prompt text mentions it.
7. **A timeout is a precondition for a fallback.** A fallback keyed on
   exceptions can never fire against a hang.
8. **Refusal is a first-class outcome** with its own graph edge, its own audit
   row, and its own tests.
9. **A false contradiction is worse than a missed one** — it inverts the
   system's stated value.
10. **Cause cannot be assigned from a single before/after pair.** Three runs,
    with provider and model printed per run.

---

# 1. Terminal · *(pending Day 1–2)*

# 2. Git · *(pending Day 2)*

# 3. Python · *(pending Day 10–12)*

# 4. HTTP · *(pending Day 4)*

# 5. APIs and FastAPI · *(pending Day 4–6)*

# 6. Authentication and JWT · *(pending Day 7–8)*

# 7. Authorization and security

*Day 9's items land here when that day is worked; below is what Days 41-42
established.*

- **Three gates on an admin feature, and only two are security.** The sidebar's
  role check is **usability** - `session.role` comes from `localStorage` and is
  editable. `require_role` reads a **signature-verified** JWT claim.
  `role_filtered_response` filters by field and **fails closed** on an unknown
  role.
- **The client decides what is SHOWN; the token decides what is SERVED.**
- **The Prompt Shield is the graph's entry point.** 18 patterns - 11 advice, 7
  injection - pure regex, no LLM, no network, on every query.
- **Match the request STRUCTURE, not the word.** *"should I buy"* blocks;
  *"what did Zomato buy?"* passes.
- **Compliance blocks explain and show how to rephrase. Injection blocks say
  nothing** - an attacker gets no feedback signal. CAVEAT-021 is the cost: a
  false positive is indistinguishable from a real block.
- **A block costs ZERO LLM calls** and still writes an audit row:
  `query_path='blocked'`, `llm_provider=NULL`.
- **Indirect injection via corpus content is UNDEFENDED.** The shield inspects
  `state["query"]` only.
- **What bounds an injection is ARCHITECTURE, not the shield.** The model never
  sees the schema, never writes SQL, never does arithmetic - so it can influence
  prose and cannot forge a verified figure.
- **CAVEAT-001 is the highest-priority security item**: the request body can
  override the JWT's `tenant_id`, and RLS, the vector filter and the audit row
  then all faithfully enforce the attacker's choice. **Unexploitable only because
  one tenant is seeded** - a property of the data, not the code.
- **No rate limiting anywhere**, including `/auth/login`.
- **Layered defences downstream of one poisoned variable do not compose.**

# 8. PostgreSQL and SQL · *(pending Day 13–16)*

# 9. Backend architecture · *(pending Day 3, 10–12, 35)*

# 10. LLMs · *(pending Day 17–19)*

# 11. Embeddings · *(pending Day 20)*

# 12. Vector databases and Qdrant · *(pending Day 21)*

# 13. Chunking · *(pending Day 24)*

# 14. BM25 and sparse retrieval · *(pending Day 26)*

# 15. Hybrid retrieval and RRF · *(pending Day 27)*

# 16. Reranking and cross-encoders · *(pending Day 28)*

# 17. Prompt engineering · *(pending Day 18)*

# 18. RAG end to end · *(pending Day 30)*

# 19. Query routing · *(pending Day 35–36)*

# 20. The DSL and the quantitative path · *(pending Day 31–34)*

# 21. Financial data · *(pending Day 13, 22, 31)*

# 22. Frontend, React, Next.js

- **`frontend/app/` holds THREE files, so exactly ONE route.** There is no second
  entry point a component could be mounted from.
- **ONE page, FIVE views**; `Sidebar` declares four. `upload-history` is a
  continuation of Intake, not a destination.
- **`"use client"` is a BOUNDARY, not a per-file label.** Everything imported
  below one joins the client bundle whether or not it declares the directive.
  Eleven files carry it; `app/layout.tsx` is the only Server Component.
- **State or an event handler means a client component.**
- **THIRTEEN `useState` in `page.tsx`.** State lives at the lowest common
  ancestor of everything that reads it - `QueryDock`'s input text is not lifted.
- **`setState` is async.** The value in scope is fixed for the whole render,
  which is why the trace is collected into a **local array** as well as into
  state.
- **`useCallback` preserves FUNCTION IDENTITY** for dependency arrays. Not
  performance.
- **NOT `EventSource`**: GET-only, and cannot set `Authorization`.
- **SSE frames split on a blank line; `frames.pop()` puts the PARTIAL frame
  back.** Decode with `{stream: true}`, because a chunk boundary can split a
  multi-byte character.
- **FOUR error classes - `UnauthorizedError`, `PipelineError`,
  `RequestFailedError`, `TransportError` - and NONE is retried.** The one
  retryable case has no class: the stream started and nothing reported a failure.
- **A retry is a SECOND PIPELINE** - a second LLM spend against 500/day and a
  second append-only audit row with nothing marking it a retry.
- **`composeDocumentBody()` is the ONLY path-aware frontend function** (ED-024).
  Five branches, and **order is semantics**.
- **Branches 3 and 4 DUCK-TYPE a SQL row** across a language boundary. No type
  error, no test, if a backend key is renamed.
- **OMIT rather than substitute - but omission still needs a glyph.** An em-dash,
  never an empty cell.
- **`companies: []` is legal** and means retrieval ran **unfiltered**.
- **The evidence list is rebuilt FROM DATA**; `cleanProseText` strips the model's
  own `Sources:` block.
- **The JWT lives in `localStorage`** (CAVEAT-011), and `expiresAt` is computed
  **client-side** - a convenience, not a control.
- **`tsc --noEmit` proves types, never reachability.** It validates dead code
  just as carefully.

# 23. Docker and deployment

*Day 1's items land here when that day is worked; below is Day 45.*

- **512 MB is Render's free tier. Exit 137 = 128 + 9 = SIGKILL = OOM.** No
  traceback exists.
- **EIGHT decisions traceable to 512 MB:** fastembed not torch, Cohere primary,
  `BATCH_SIZE = 8`, offline ingestion, one uvicorn worker, six thread-limit
  ENVs, no self-hosted LLM, no cache.
- **SIX thread ENVs**, because four libraries read four different variables with
  non-uniform precedence, and they size pools from **core count**, not memory.
- **`--workers 1`**, because a worker is a process and the three models are
  in-process objects with no sharing.
- **`environment:` WINS over `env_file:`.** `DATABASE_URL` is overridden at
  `docker-compose.yml:51`, so the stack reads **local** Postgres.
- **The two databases differ in document counts (11 vs 9) AND in grants
  (CAVEAT-028). Always state which one.**
- **`golden_dataset/` is read-only** - the mount flag that prevented 79 outputs
  against 3 inputs.
- **`./backend:/app` is a bind mount**: `docker compose cp` writes into the repo.
  Container scratch goes in `/tmp`. Check `git status`.
- **`up -d` returns when the container STARTS, not when uvicorn SERVES.** Poll
  `/health`, and echo the token length so an empty token fails loudly.
- **`basicConfig` before any `app.*` import, `force=True`** - and again in
  `worker.py`, which never imports `main.py`.
- **Redis is the Celery broker and a `/health` target.** Nothing on the request
  path uses it, and **there is no Redis in production**.
- **A TIMEOUT IS THE PRECONDITION FOR A FALLBACK.** A hang throws nothing.

# 24. Testing and evaluation

- **Never run `eval_runner.py` without explicit per-run approval.**
- **`--delay 25`, not 15.** 5 RPM, 500/day, **two** calls per semantic question.
- **Largest dataset first, as a gate.** Read `Providers:`, then `Models served:`,
  then the score. **If either gate is unclean, STOP.**
- **REPORT, DO NOT INTERPRET.** A withheld score is withheld, not annotated - a
  tally under a caveat ends up in a README.
- **91 questions, 4 files, 12 categories.** 11 adversarial (12 per cent).
- **THREE integrity gates, deliberately SEPARATE**: provider, model, reranker
  backend. Different faults, different remedies.
- **Blocked queries are EXCLUDED from the provider gate** - no LLM call is made.
  The tell was the unknown count matching the adversarial count exactly, in
  three datasets.
- **`synthesis_unavailable` is EXCLUDED from numerator AND denominator**, and the
  IDs are listed: clustered means a defect, scattered means transient.
- **pytest baseline is 218 passed / 25 errors. NOT GREEN.** CAVEAT-025.
- **conftest patches `socket` AND `psycopg2.connect` BY NAME** - libpq bypasses
  Python sockets, and that was **measured**.
- **Some tests assert KNOWN DEFECTS. Their failure means the fix landed.**
- **`eval_results/*.json` are NOT baselines.** Print rows, passes, providers,
  backends and mtime **before** the score. Baselines live in
  `IMPLEMENTATION_DELTAS.md`, dated.
- **Quota fails by POSITION. A real defect fails by CATEGORY.**
- **RAGAS was rejected**: a score is not a decision, and the judge shares the
  failure modes of the judged.
- **A keyword a WRONG answer also satisfies is not an assertion.**
- **`regression_check.py` is zero LLM calls** - after **every** extraction
  change, not batched. Parse each PDF **once**.

# 25. Observability and debugging

- **ONE audit row per request**, written by the **terminal node of every path**,
  including blocks and refusals.
- **`query_path` has a CHECK constraint** - semantic, quantitative, cross,
  blocked, unknown. A fourth path needs a **migration**.
- **Audit failure LOGS AND SWALLOWS.** The user already has the answer
  (CAVEAT-014).
- **APPEND-ONLY, EXACTLY:** no `DELETE` grant on either database (**enforced**);
  `UPDATE` **is** granted on both (**convention**). CAVEAT-028.
- **`NULL` in `llm_provider` is a RECORD**, not missing data - and
  `eval_runner`'s provider gate depends on it.
- **`response_text` is bound IN FULL.** It was a 500-char prefix under a variable
  named for a summary: **1516 of 4168 rows, unmarked.**
- **"A threshold warning is a cap that has not fired yet."**
- **`cache_hit_rate_pct` is structurally 0.0 and SHIPS ANYWAY** (D1,
  CAVEAT-009). Marked in SQL, in CAVEATS, and in `lib/api.ts`. **Not deleted,
  not rendered.** `tokens_used` is the same (CAVEAT-010).
- **`refusal_rate_pct` is a PROXY**: `confidence_score < 0.5`, counting every
  block - and that `0.5` is a **third copy** of `COHERE_HIGH`.
- **EMPTY candidate set means NETWORK. LOW-SCORING means RETRIEVAL.** Establish
  which before theorising.
- **The layer ladder puts the LLM LAST.**
- **Single-line logs with pgcode.** Render truncates multi-line, and is **UTC**
  while the shell is IST.
- **`SET LOCAL`, not `SET`** - transaction-scoped, cannot leak into a pooled
  connection.
- **"possible container breakout detected" is a STALE MOUNT NAMESPACE**, not a
  security event. Confirm with a bare `echo alive` exec.

# 26. Transferable system design

*Accumulates from Day 9 onward. Below is what Days 38-47 added.*

- **Keep FACT, EVIDENCE, INFERENCE and UNKNOWN separate.** The failure mode is
  not being wrong - it is **category slippage**, an inference written in the
  grammar of a fact. **The test: what command reproduces this?**
- **A heading is not a record.** Read to the end of an entry before quoting its
  title.
- **A permission claim is checkable in ONE QUERY.**
- **Live code with no input is more dangerous than code with no caller** - it
  returns a number, and a number looks like a measurement.
- **Do not delete evidence of unfinished work.** Mark it at every layer, and do
  not render it.
- **A "safe default" is a claim**, and a schema change can turn a correct
  fallback into a 100-per-cent-firing falsehood without the fallback changing.
- **A correct measurement can justify a WRONG constant** (the 0.05 citation
  floor). *Measure before reverting*, not only before shipping.
- **A documented invariant the code violates is a finding to RECORD**, not a bug
  to fix - fixing means deciding which side was right, and that is the author's
  call.
- **The strongest security property here is a SIDE EFFECT of a correctness
  decision.** Reducing what a component may *do* shrinks what compromising it is
  worth.
- **Some coverage gaps need a DOCUMENT, not a better test** - and manufacturing
  the missing case would be worse than leaving it open.
- **A refusal you can only defend is not understood.** Argue both sides.

---

# A. Numbers worth knowing cold

Seeded now because these appear across many days and are asked about directly.
Each is **measured**, not chosen; the measurement lives beside the constant in
the code.

| Value | What | Why not another value |
|---|---|---|
| `384` | dense embedding dimensions (`bge-small-en-v1.5`) | fixed by the model |
| `20 → 5` | retrieve top-20, rerank to top-5 | the standard two-stage shape |
| `0.70` | near-duplicate threshold, denominator = **smaller** chunk | calibrated on one measured pair; logged at INFO so the distribution accumulates |
| `150` | `OVERLAP_TOKENS` | raised from 50 after a mid-sentence split orphaned Paytm's PPBL impairment |
| `8` | embedding `BATCH_SIZE` | 32 caused OOM at 1999+ chunks |
| `0.5 / 0.15` | Cohere confidence thresholds | 0.5 validated against 83 questions; **0.15 has never been exercised** |
| `-4.5 / -7.5` | local ONNX thresholds | a different scale entirely — this is the point |
| `2` | `MAX_DSL_ATTEMPTS`, `MAX_CRAG_RETRIES` | bounded self-healing, never a ladder |
| `2 h` | JWT lifetime | stateless auth cannot revoke; the window is the mitigation |
| `20 s` | structured-call timeout | raised from 8 s — measurement showed calls routinely exceed 8 s, and the tight bound was *slower* overall |
| `5 RPM / 500 per day` | Gemini free tier | a semantic question makes **two** calls |
| `512 MB` | Render's ceiling | caused Cohere-as-primary, offline ingestion, `BATCH_SIZE=8` |
| `91` | golden questions across 4 datasets | 55 + 15 + 20 + 1 |
| `218 / 25` | current pytest baseline | **not green** — CAVEAT-025 |

**Do not modify any of these without approval.** `CLAUDE.md` §3.

---

# B. Signatures — symptom to cause

Seeded now because these save the most time. Each is a real observation.

| You see | It means |
|---|---|
| `UserWarning: Api key is used with an insecure connection` | you are on **local** Docker Qdrant, not Cloud. Every measurement this session is invalid |
| `UserWarning: Failed to obtain server version` | qdrant_client failed its construction-time probe; the next query in that process will die |
| `exec failed: ... possible container breakout detected` | stale mount namespace after `--force-recreate`. **Not** a security event. Confirm with `exec -T backend echo alive`, then recreate |
| `Cwd must be an absolute path` (Git Bash) | `-w /app` was path-rewritten. Prefix `MSYS_NO_PATHCONV=1` |
| `Exited with status 137` | OOM kill |
| 0 rows from `financials` | check `app.tenant_id` **before** concluding the data is missing |
| **Empty** candidate set | a **network** signature |
| **Low-scoring** candidate set | a **retrieval** signature |
| Eval failure at a **fixed position**, everything before it passing | a **quota** signature. A real defect fails by **category** |
| Same query, two different confidence tiers | check `reranker_backend` first |
| "Works locally, not in prod" | run `git status --short` and `git log --oneline origin/main -1` **before** any code theory. Every instance has traced to an unpushed file |

---

# C. Refusals — every way this system says no

Seeded now because refusal is a first-class outcome here and the list is
finite. Learn it as a set.

| Refusal | Raised by | Trigger |
|---|---|---|
| Prompt Shield block | `prompt_shield` | SEBI advice, or injection/jailbreak pattern |
| `routing_unavailable` | `router` | no LLM provider reachable |
| `company_not_in_corpus` | `router` | every named issuer failed `_KNOWN_TICKERS` |
| `low_confidence_refusal` | `semantic_engine` | tier still LOW after the CRAG ladder |
| `metric_not_computable` | `quant_engine` Stage 0 | a **derived** metric was named |
| `metric_not_queryable` | `quant_engine` Stage 0b | a registered but non-`dsl_enabled` metric |
| `dsl_generation_failed` | `quant_engine` Stage 1 | invalid DSL after 2 attempts |
| `no_data_found` | `quant_engine` Stage 4 | zero rows |
| `ambiguous_result` | `quant_engine` Stage 4 | `point_in_time` returned >1 row |
| `insufficient_data_for_cagr` | `quant_engine` | fewer than 2 data points |
| (qualitative-only) | `cross_engine` Stage 0c | query names no known metric — degrade, do not refuse |

**Two of these skip the confidence tail entirely** — the Prompt Shield block and
the router refusal — because `confidence_node` would otherwise rescore a
refusal.
