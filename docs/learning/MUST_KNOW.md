# LedgerMind — Must Know

The final revision document. **Recall only** — if you need the repository open,
it does not belong here.

**How this file grows.** Each day's `MUST REMEMBER` items land in the matching
section. Sections are seeded with their headings and fill in as the course
proceeds; empty ones are marked *(pending Day N)*.

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

# 7. Authorization and security · *(pending Day 9, 42)*

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

# 22. Frontend, React, Next.js · *(pending Day 38–41)*

# 23. Docker and deployment · *(pending Day 1, 45)*

# 24. Testing and evaluation · *(pending Day 43)*

# 25. Observability and debugging · *(pending Day 44)*

# 26. Transferable system design · *(pending — accumulates from Day 9 onward)*

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
