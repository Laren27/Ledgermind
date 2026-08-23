# LedgerMind — "I Should Now Understand" Checklist

Track your own progress. Five levels per concept, and they are **not** the same
skill:

```text
[ ] I can explain it
[ ] I understand why LedgerMind uses it
[ ] I can locate it in the code
[ ] I can modify it safely
[ ] I can explain its trade-offs
```

The fourth is the one that matters. "I can modify it safely" means you know what
breaks, what test catches it, and what measurement would prove you right.

---

## Architecture

### The tri-engine model (semantic / quantitative / cross)
```text
[ ] I can explain it   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Locate:* `app/engines/graph.py`, `router.route_after_router`
*Check yourself:* why does a refusal skip the confidence node?

### `QueryState` as a single shared mutable dict
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Locate:* `app/engines/state.py:66`
*Check yourself:* what breaks if two nodes write the same field, and where is
that already true? (`confidence_tier`, written by three nodes and finally by
`_reconcile_cross`.)

### LangGraph `StateGraph` + conditional edges
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* how would you add a fourth path, and which files change?
(Answer: `state.py` Literal, `graph.py`, `router.py` prompt + `route_after_router`,
`response_generator`, `page.tsx :: composeDocumentBody`, and the `audit_log`
CHECK constraint — that last one needs a migration.)

### Streaming vs blocking transport sharing one pipeline
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* why must the graph task never be cancelled on client
disconnect?

### Separation of ingestion (offline) from query (online)
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

---

## Backend / Python

### FastAPI dependency injection and `Depends`
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* why is auth a dependency and not middleware?

### Pydantic models as an API contract
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### `TypedDict` vs `dataclass` vs Pydantic — and why this repo uses all three
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* `QueryState` is a TypedDict, `FinancialRecord` is a dataclass,
`RouterResponse` is a Pydantic model. Can you justify each choice?

### Lazy singletons for expensive models
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Locate:* `retriever._get_dense_model` and friends
*Check yourself:* why lazy rather than at import? (Docker startup, and the
Celery worker imports modules it may never call.)

### Generators and `async` streaming
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Context managers (`@contextmanager`, `with conn:`)
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* what does `with conn:` do on exit — and what does it **not**
do? (Commits/rolls back; does **not** close.)

---

## Frontend

### React state lifting and why `page.tsx` holds everything
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### `composeDocumentBody()` as the single path-aware boundary
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Consuming SSE with `fetch` + `ReadableStream`
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* why not `EventSource`? Why does the frame buffer keep a
partial frame instead of discarding it?

### The Zero UI-Hallucination Mandate
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* find a place where the UI **omits** rather than substitutes,
and say what the substitute would have implied.

### Graceful degradation in the client (stream → blocking fallback)
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

---

## Databases

### Row-Level Security, `FORCE`, and `SET LOCAL`
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* what does a query return when you forget the GUC, and why is
that answer dangerous?

### Partial unique indexes
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### `SELECT … FOR UPDATE` and the race it prevents
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### `IS NOT DISTINCT FROM` and NULL semantics
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### `ON CONFLICT DO NOTHING` and idempotent inserts
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Transaction boundaries in this codebase
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* how many connections does one `growth_comparison` query open?
(Four, plus audit. See CAVEAT-013.)

### Qdrant collections, named vectors, payload indexes
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* what happens to a filtered query if the payload index is
missing?

### Schema migration discipline without a framework
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

---

## RAG & retrieval

### Dense embeddings and cosine similarity
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### BM25 and sparse vectors
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Hybrid retrieval and Reciprocal Rank Fusion
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* why fuse by rank instead of by score?

### Filter placement — inside each prefetch leg, not at fusion
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Cross-encoder reranking, and **incompatible score scales**
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*This is the single highest-value item on this list.* If you can explain why a
`reranker_score` without its `reranker_backend` is meaningless, you understand
how this project reasons about evidence.

### Chunking strategy and overlap trade-offs
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Near-duplicate suppression
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### CRAG as a filter ladder
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* what is `crag_count` actually counting? (Rung index, not
retrievals performed.)

### Speaker-turn chunking and attribution
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

---

## LLMs

### Structured output, and why it does not imply correctness
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Provider failover, and why the trigger is narrow
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* why are 401 and 403 deliberately excluded?

### Timeouts as a precondition for fallback
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Provider attribution by precedence
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Prompt-order effects — earlier, concrete rules beat appended ones
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* name the three occasions this cost the project a fix.

### Rate limits: RPM vs daily, and why they need opposite handling
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

---

## Financial systems

### Consolidated vs standalone
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Indian fiscal years and quarters
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Restatement vs parser correction
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Accounting identities as a validation gate
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Locate:* `validate_financial_identities`, and the `>5%` hard failure gate
*Check yourself:* why is `DERIVED_OVERWRITE_MAX_DIVERGENCE` deliberately
allowed to **produce** identity failures?

### Units and scale (crore/lakh/million)
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* why can the extractor not simply normalise everything to
crore? (`clean_financial_number`'s decimal-as-comma rule.)

### Metric normalisation and alias collisions
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* what is the coverage floor of 0.5 protecting against, and what
does a `[METRIC TIE]` log line mean?

---

## Security

### JWT: signing, claims, expiry, what it does **not** protect
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### RBAC at two levels, failing closed
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Tenant isolation, defence in depth — **and its current hole**
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* trace `tenant_id` from the HTTP request to the RLS policy and
name the point where it stops being trustworthy. (CAVEAT-001.)

### Prompt injection: direct vs indirect
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* which one is undefended here, and why does the architecture
still bound the damage?

### Parameterised queries
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Append-only audit as a **grant**, not a convention
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

---

## Testing & evaluation

### Pure-function unit tests with a hard network guard
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* why does patching `socket` alone fail to block psycopg2?

### Tests that assert known defects as current behaviour
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* what do you do when one of them starts failing?

### Golden datasets and keyword assertion discipline
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* name three things you must never assert on. (Optional acronym
glosses; verb inflection; short strings a wrong answer would also satisfy.)

### Regression checks vs evals — cost, and what each proves
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Quota signatures vs real defects
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* failure at a fixed position with everything before it passing
— what is that?

---

## DevOps

### Docker Compose: bind mounts, `env_file` vs `environment`, `--force-recreate`
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Health checks and readiness vs liveness
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Deploying under a hard RAM ceiling
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* name three design decisions caused by 512 MB.
(Cohere as primary reranker; offline ingestion; `BATCH_SIZE = 8`.)

### Celery: brokers, `acks_late`, soft vs hard time limits
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

---

## System design (transferable)

### Single source of truth — one registry, one formatter, one decision function
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```
*Check yourself:* name three places this project consolidated a duplicated fact,
and the bug each duplication caused.

### Fail closed vs fail open
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Making degradation **visible**, not just survivable
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Refusal as a first-class outcome
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Determinism over agency
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

### Writing the measurement next to the constant
```text
[ ] Explain   [ ] Why here   [ ] Locate   [ ] Modify   [ ] Trade-offs
```

---

## The final exam

You have finished when you can answer these **without opening the repository**:

1. What are the three query paths, and how is one chosen?
2. Why does the LLM never write SQL, and what does it write instead?
3. What are the two retrieval signals, and how are they combined?
4. Why are there two sets of confidence thresholds?
5. What does `sql_verified = True` guarantee — and what does it not?
6. How is tenant isolation enforced, and where does it currently break?
7. Why does a refusal skip the confidence node?
8. What is the difference between a restatement and a parser correction?
9. Name three things that exist in `QueryState` with no producer.
10. Why was the 0.05 citation floor removed when the measurement behind it was
    correct?
