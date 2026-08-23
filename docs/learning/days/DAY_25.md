# Day 25 — Dense Retrieval, and Where It Fails

**Phase 7 — Retrieval engineering · Weight: M (~90 min) · Prerequisites: Days 20, 21**

**Textbook: 5.2 "Dense Retrieval" — CONFIRMS · 10.2 "Query Rewriting" —
DIVERGES (minimal only).**

> **Phase 7 is strictly ordered.** Day 28 (score scales) is unteachable before
> Day 27 (ranking). Do not skip ahead.

---

## 1. Today's goal

By tonight you can:

- Describe dense retrieval end to end: query → vector → nearest neighbours.
- Name precisely **where dense retrieval fails**, and why the failure is
  structural rather than a tuning problem.
- Explain `_build_resolved_query` — LedgerMind's minimal query rewrite — what it
  adds and what it deliberately does not do.
- Explain `TOP_K_RETRIEVAL = 20` and `TOP_K_RERANK = 5`, and why retrieval casts
  wider than the answer needs.

---

## 2. Why now

Days 20–24 built the index. Today is the first query against it. Day 26 adds the
complementary signal; Day 27 fuses them.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Embeddings, cosine, normalisation | Day 20 | The mechanism |
| `query_embed` vs `embed` | Day 20 | The asymmetry |
| Named vectors, HNSW is approximate | Day 21 | What is being searched |
| `resolved_query` in `QueryState` | Day 3 | Today is where it comes from |

---

## 4. Concept lesson

### 4.1 Dense retrieval, in three steps

Textbook 5.2:

> **Input:** a query string.
> **Internal workflow:** the query is embedded using the same model used for the
> stored chunks → the resulting query vector is compared against every (or, with
> HNSW, an efficiently narrowed-down subset of) stored vector → the K vectors
> with the highest cosine similarity are selected.
> **Output:** a ranked list of K chunks.

**In LedgerMind:**

```python
def _encode_dense(query: str) -> List[float]:
    """Encode query with bge-small-en-v1.5 (ONNX) → 384-dim dense vector."""
    model = _get_dense_model()
    vector = next(model.query_embed(query))
    return vector.tolist()
```

Three lines. `_get_dense_model()` is the lazy singleton (Day 12), `query_embed`
is the asymmetric query-side entry point (Day 20), and `next(...)` takes the
single result from a generator that would normally yield one per input.

**`.tolist()`** converts numpy → a Python list, because the Qdrant client
serialises to JSON and JSON has no numpy type (Day 5). A small boundary, and the
same class as `str(uuid)` in the payload writer.

---

### 4.2 Where dense retrieval fails — and why it is structural

Textbook 5.2:

> **Where dense retrieval fails:** exact identifiers — product codes, invoice
> numbers, proper nouns the embedding model has not learned strong associations
> for. A query for `INV-2024-XR7` produces an embedding that captures only a
> vague, generic sense of "this looks like a code", not the specific characters
> of that code.

**This is not a quality problem, it is a representational one.** An embedding is
a *summary of meaning*. Two invoice numbers differing in one character have
essentially the same meaning — "an invoice number" — so they land in nearly the
same place. Day 20's Experiment 3 demonstrated it: `INV-2024-XR7` and
`INV-2024-XR8` score high.

**In this corpus the affected terms are everywhere:**

| Term | Why dense struggles |
|---|---|
| `PPBL` | An acronym; almost no training signal for its expansion |
| `FY26` vs `FY25` | One character apart, near-identical embeddings |
| `ETERNAL` | A common English word used as a ticker |
| `Q4` vs `Q3` | Same |
| `Hyperpure`, `Blinkit` | Post-training-cutoff proper nouns |

**Everything that identifies *which* filing you mean is exactly what dense
retrieval is worst at.** That is the argument for Day 26, made from the failure
side rather than from enthusiasm for BM25.

---

### 4.3 The minimal query rewrite

Textbook 10.2 recommends LLM-based query rewriting and multi-query expansion.
LedgerMind does neither. It does this:

```python
def _build_resolved_query(original_query, companies, fiscal_year, quarter,
                          financial_type) -> str:
    # Every named issuer joins the BM25 prefix. One issuer produces exactly
    # the pre-F14 string; none produces no prefix, also as before. Two now
    # contribute both tickers where previously the null contributed nothing.
    prefix_parts = [p for p in [*companies, fiscal_year, quarter, financial_type] if p]
    return f"{' '.join(prefix_parts)} {original_query}" if prefix_parts else original_query
```

So:

```
"What were the revenue drivers?"
        ↓  router extracts ETERNAL / FY26 / Q4 / consolidated
"ETERNAL FY26 Q4 consolidated What were the revenue drivers?"
```

**The comment says "the BM25 prefix", and that names the intent.** The added
tokens are exactly the ones dense retrieval handles worst and BM25 handles best.
It is a **hybrid-aware** rewrite: written for one leg of a two-leg search.

**What it deliberately does not do:**

| Textbook 10.2 | Here |
|---|---|
| LLM rewrites the query into search-optimised form | No — the router already made **one** LLM call; a second doubles cost against 500/day |
| Multi-query expansion (several phrasings) | No — N queries per question |
| Conversational context resolution ("their revenue") | No — there is no conversation state |

**And it has a cost worth naming.** The prefix goes into the **dense** encoding
too, because both legs share `resolved_query`. Prepending four tokens shifts the
query vector slightly toward "a document about ETERNAL FY26" and away from the
question itself. For short queries that shift is proportionally larger.

Nothing in the repository measures this. It is a reasonable trade — the terms are
genuinely relevant — but it is **an assumption, not a measurement**, and worth
recognising as one.

---

### 4.4 Retrieve 20, answer with 5

```python
TOP_K_RETRIEVAL = 20
TOP_K_RERANK    = 5
```

**Why not retrieve 5 directly?** Because dense retrieval is fast and
**approximately** ordered (Day 21's HNSW, and textbook 6.1):

> the top-20 results from retrieval are a reasonably good shortlist, but **not
> necessarily perfectly ordered** by true relevance.

Retrieval's job is **recall** — get the right chunk *somewhere* in the list.
Reranking's job is **precision** — put it first. Ask retrieval for precision and
you get neither.

**And there is a second reason specific to this codebase.** Near-duplicate
suppression (Day 29) drops overlapping chunks after reranking, and needs a pool
to backfill from. `rerank()`'s comment:

> Score ALL candidates, not just `top_k`. Cohere bills per SEARCH (not per
> document), so widening this is free, and dedup below needs a pool to backfill
> from — with `top_n=5`, dropping a near-duplicate left **4 chunks instead of
> swapping in the 6th-best**.

**20 is the pool that makes suppression non-destructive.**

---

## 5. The actual LedgerMind files

```
File:        backend/app/engines/retriever.py (574 lines) — today, lines 60-200
Purpose:     Query-side retrieval. hybrid_search() and rerank()
Who imports: semantic_engine, cross_engine
Entry points (today): _encode_dense(query) -> list[float]
Constants:   COLLECTION_NAME, DENSE_VECTOR_NAME="dense",
             TOP_K_RETRIEVAL=20, TOP_K_RERANK=5,
             DENSE_MODEL_NAME, QDRANT_TIMEOUT_SECONDS=10

File:        backend/app/engines/router.py — _build_resolved_query
Purpose:     Produce the entity-prefixed string retrieval actually searches with
```

**The module docstring's design decisions, worth reading now** because Days 26–29
each land on one:

> - Models are lazy-loaded singletons … Docker startup stays fast
> - Qdrant `FusionQuery(Fusion.RRF)` is used for native RRF — no manual score merging
> - **Filter runs INSIDE each prefetch leg (not at fusion level)** so both dense
>   and sparse candidates are pre-filtered before RRF
> - `is_latest=True` is ALWAYS applied unless explicitly bypassed
> - `tenant_id` is ALWAYS applied — multi-tenant isolation is non-negotiable
> - quarter filtering is OPTIONAL

---

## 6. Deep walkthrough

### 6.1 `_encode_dense`, state by state

**STATE BEFORE.** A string. `_dense_model` may be `None`.

**`_get_dense_model()`** — first call in the process loads ~130 MB and takes ~10 s
(Day 12). Every later call is a dictionary lookup. **This is why a cold
measurement is not evidence.**

**`model.query_embed(query)`** returns a **generator**, because fastembed's API is
batch-first. `next(...)` takes the one result.

**Why `query_embed` and not `embed`.** BGE models are asymmetric: the query side
gets a prefix instructing the model to produce a *search* vector rather than a
*document* vector. Using `embed()` here would produce a document-style vector and
compare it against document vectors — which sounds symmetric and is subtly wrong,
and would degrade retrieval **with no error**.

**STATE AFTER.** A 384-float Python list, normalised (‖v‖ = 1), ready to send.

---

### 6.2 The dense leg inside `hybrid_search`

```python
result = client.query_points(
    collection_name=COLLECTION_NAME,
    prefetch=[
        Prefetch(query=dense_vector, using=DENSE_VECTOR_NAME,
                 limit=top_k, filter=search_filter),
        ...
    ],
    query=FusionQuery(fusion=Fusion.RRF),
    limit=top_k,
    with_payload=True,
)
```

Today, only the dense `Prefetch`:

- **`using="dense"`** — the named vector (Day 21).
- **`limit=top_k`** — 20 from *this leg*.
- **`filter=search_filter`** — inside the leg. Day 27's decision.
- **`with_payload=True`** — return the payload, including `text` (Day 21), so no
  second lookup.

**And the failure handling:**

```python
except Exception as e:
    logger.error("Qdrant hybrid_search failed: %s", e)
    return []
```

**Returns `[]`, does not raise.** The caller checks:

```python
if not candidates:
    logger.warning("hybrid_search returned 0 results — skipping rerank")
    return []
```

and `semantic_engine` then scores confidence `low` and refuses. **A retrieval
outage degrades to a refusal**, which is the correct behaviour for this system —
and it is why `CLAUDE.md` §4 insists:

> **An empty candidate set is a network signature. A low-scoring one is a
> retrieval signature.** Check which before theorising.

Both produce a refusal. Only one is a bug in retrieval.

---

### 6.3 Payload → `ChunkResult`

```python
for point in points:
    payload = point.payload or {}
    chunk = ChunkResult(
        chunk_id=str(point.id),
        text=payload.get("text", ""),
        page_number=payload.get("page_number", 0),
        company=payload.get("company", ""),
        ...
        rrf_score=point.score,
        reranker_score=float("-inf"),
        reranker_backend="none",
        speaker_role=payload.get("speaker_role", "unknown"),
    )
```

**Every field comes from the payload** — no database call. Day 21's decision to
store the text is what makes this possible.

**`rrf_score=point.score`.** After fusion, `point.score` is the **RRF** score, not
a cosine similarity. `ChunkResult` also declares `dense_score` and `sparse_score`
— both set to `0.0` and **never populated**, because Qdrant's server-side fusion
does not return the per-leg scores. Two declared fields with no producer: the same
shape as `cache_hit` (Day 44) and `preferred_operation` (`CAVEAT-002`), and not
recorded anywhere.

**`reranker_score=float("-inf")` and `reranker_backend="none"`** are *sentinels*,
chosen so that an unscored chunk reaching the confidence scorer is **detectable**
rather than silently treated as terrible. Day 29 shows the loud error that fires
on them.

---

## 7. Data flow

```
"What were the revenue drivers?"                         (the user's words)
        │
        ▼  router_node                                    (Day 36)
   companies=["ETERNAL"] fiscal_year="FY26" quarter="Q4"
   financial_type="consolidated"
        │
        ▼  _build_resolved_query
   "ETERNAL FY26 Q4 consolidated What were the revenue drivers?"
        │
        ├──────────────────────────────┬─────────────────────────┐
        ▼                              ▼                         │
   _encode_dense()               _encode_sparse()      (Day 26)   │
   model.query_embed()                                            │
        │                              │                          │
   [384 floats, ‖v‖=1]           SparseVector(...)                │
        │                              │                          │
        ▼                              ▼                          │
   Prefetch(using="dense",      Prefetch(using="sparse",          │
            limit=20,                    limit=20,                │
            filter=f)                    filter=f)  ◄─────────────┘
        │                              │
        └──────────┬───────────────────┘
                   ▼
          FusionQuery(Fusion.RRF)                       (Day 27)
                   ▼
          20 points WITH PAYLOAD
                   ▼
   ChunkResult × 20   (rrf_score set; reranker_score = -inf)
                   ▼
          rerank()                                      (Day 28)
                   ▼
          top 5
```

---

## 8. Engineering decision — search with the resolved query

**Problem.** A user's question names entities and periods in prose; the corpus
identifies them by ticker and fiscal-year label.

**Decision.** Prefix the router-extracted entity and period tokens onto the query
before encoding, and use that string for **both** legs.

| Alternative | Why not |
|---|---|
| **Search the raw query** | Loses `ETERNAL FY26 Q4` as *terms* — the tokens BM25 is best at and dense is worst at |
| **LLM query rewrite** (textbook 10.2) | A second LLM call per query against a 500/day ceiling |
| **Multi-query expansion** | N searches per question |
| **Metadata filter only, no prefix** | The filter already restricts the *set*; the prefix helps **rank** within it. Both, not either |
| **Separate strings per leg** | Two encodings, and a divergence risk with no measured benefit |

**Trade-offs accepted.**

- **The prefix perturbs the dense vector**, unmeasured (§4.3).
- **It depends on the router being right.** A wrong ticker prefixes a wrong term
  — and the F2 refusal (Day 36) exists because the *filter* half of that failure
  was worse.
- **No conversational rewriting**, so follow-up questions must be self-contained.
  There is no conversation state anywhere in `QueryState`.

**Current validity.** Sound and cheap. The unmeasured dense-side effect is the
open question — and the honest framing is that it is an assumption.

**At 10×.** Query rewriting becomes more attractive as phrasings diversify, but
the quota is the binding constraint, not the idea.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| **Empty candidate set** | **A network signature** — Qdrant unreachable. Not retrieval |
| Low-scoring candidates | **A retrieval signature** — genuinely nothing relevant, or the filter is too tight |
| An exact term not found | Dense weakness. What Day 26 exists for |
| Cold first query ~30 s | Lazy model load (Day 12) |
| Wrong-company chunks | The filter, not the encoding (Day 27, audit F2) |
| Retrieval degraded, no error | Ingest and query used different models (Day 20) |
| Results differ run to run | HNSW is approximate — or `reranker_backend` changed (Day 28) |

---

## 10. Hands-on experiment

### Experiment 1 — encode a query

```bash
docker compose exec -T backend python -c "
import time, math
from app.engines.retriever import _encode_dense, DENSE_MODEL_NAME
print('model:', DENSE_MODEL_NAME)
t=time.perf_counter(); v = _encode_dense('What were the revenue drivers?')
print(f'first call : {time.perf_counter()-t:6.2f}s  <- COLD')
t=time.perf_counter(); v = _encode_dense('What were the revenue drivers?')
print(f'second call: {time.perf_counter()-t:6.4f}s  <- WARM')
print('dims :', len(v))
print('norm :', round(math.sqrt(sum(x*x for x in v)), 6))
"
```

### Experiment 2 — `query_embed` versus `embed`

```bash
docker compose exec -T backend python -c "
import math
from app.engines.retriever import _get_dense_model
m = _get_dense_model()
q = 'What were the revenue drivers?'
qv = next(m.query_embed(q)).tolist()
dv = next(m.embed([q])).tolist()
sim = sum(a*b for a,b in zip(qv,dv))
print('query_embed vs embed, same text — cosine:', round(sim, 4))
print()
print('Close, NOT identical. BGE is asymmetric. Using embed() on the query')
print('side would degrade retrieval with NO ERROR.')
"
```

### Experiment 3 — the resolved query

```bash
docker compose exec -T backend python -c "
from app.engines.router import _build_resolved_query
cases = [
 (['ETERNAL'], 'FY26', 'Q4', 'consolidated', 'What were the revenue drivers?'),
 (['ETERNAL','PAYTM'], 'FY26', None, 'consolidated', 'Who grew revenue faster?'),
 ([], None, None, 'consolidated', 'What are the key risks?'),
]
for comp, fy, q, ft, query in cases:
    print(f'  companies={comp} fy={fy} q={q}')
    print(f'    -> {_build_resolved_query(query, comp, fy, q, ft)!r}')
print()
print('Two issuers now contribute BOTH tickers. Pre-F14, company nulled and')
print('the prefix contributed nothing.')
"
```

### Experiment 4 — dense alone, on an exact term

```bash
docker compose exec -T backend python -c "
import os
from app.engines.retriever import _encode_dense, _get_qdrant_client, COLLECTION_NAME
from qdrant_client.models import Filter, FieldCondition, MatchValue
c = _get_qdrant_client()
f = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=os.getenv('T','')))])
for q in ['PPBL', 'Paytm Payments Bank', 'risks around quick commerce delivery']:
    r = c.query_points(collection_name=COLLECTION_NAME, query=_encode_dense(q),
                       using='dense', limit=3, query_filter=f, with_payload=True)
    print(f'\n{q!r}')
    for p in r.points:
        print(f'  {p.score:.4f} p{p.payload.get(\"page_number\")} '
              f'{p.payload.get(\"company\")} {p.payload.get(\"text\",\"\")[:64]!r}')
"
```

Compare `'PPBL'` with `'risks around quick commerce delivery'`. The acronym does
worse. **That gap is Day 26.**

### Experiment 5 — does the prefix help or hurt the dense leg?

```bash
docker compose exec -T backend python -c "
import os
from app.engines.retriever import _encode_dense, _get_qdrant_client, COLLECTION_NAME
from qdrant_client.models import Filter, FieldCondition, MatchValue
c = _get_qdrant_client()
f = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=os.getenv('T','')))])
for label, q in [('raw     ', 'What were the revenue drivers?'),
                 ('resolved', 'ETERNAL FY26 Q4 consolidated What were the revenue drivers?')]:
    r = c.query_points(collection_name=COLLECTION_NAME, query=_encode_dense(q),
                       using='dense', limit=3, query_filter=f, with_payload=True)
    print(f'{label}: ' + ' | '.join(f'{p.score:.3f} p{p.payload.get(\"page_number\")}' for p in r.points))
print()
print('The prefix is FOR the BM25 leg. Whether it helps or hurts the DENSE leg')
print('is unmeasured in this repository. You just measured it once.')
"
```

### Experiment 6 — empty vs low-scoring

```bash
docker compose exec -T backend python -c "
import os
from app.engines.retriever import hybrid_search
r = hybrid_search(query='what is the capital of France',
                  tenant_id=os.getenv('T',''), companies=['ETERNAL'],
                  fiscal_year='FY26', top_k=5)
print('irrelevant question ->', len(r), 'candidates')
for c in r[:3]:
    print(f'  rrf={c[\"rrf_score\"]:.4f} p{c[\"page_number\"]} {c[\"text\"][:56]!r}')
print()
r2 = hybrid_search(query='revenue', tenant_id='00000000-0000-0000-0000-000000000000',
                   companies=['ETERNAL'], top_k=5)
print('wrong tenant        ->', len(r2), 'candidates  <- EMPTY, but not a network failure')
print()
print('Empty = network signature. Low-scoring = retrieval signature.')
print('A wrong FILTER also produces empty. Check which before theorising.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/retriever.py` (lines 60–200) and
`backend/app/engines/router.py::_build_resolved_query`:

1. Why does `_encode_dense` call `query_embed` rather than `embed`? What would
   break, and would you see an error?
2. `hybrid_search` returns `[]` on a Qdrant exception rather than raising. Trace
   what the user eventually sees, and say whether that is right.
3. `ChunkResult` declares `dense_score` and `sparse_score`. Find where they are
   set. What do you conclude?
4. Why is `reranker_score` initialised to `float("-inf")` rather than `0.0`?
5. `_build_resolved_query`'s comment calls it "the BM25 prefix". It is used for
   the dense encoding too. What does that imply, and is it measured?

---

## 12. Self-check questions

**Basic**
1. What are the three steps of dense retrieval?
2. Where does dense retrieval fail?
3. What is `TOP_K_RETRIEVAL`, and `TOP_K_RERANK`?
4. What is `resolved_query`?
5. What does `with_payload=True` avoid?

**Code**
6. What does `next(model.query_embed(q))` return?
7. Why `.tolist()`?
8. What is `point.score` after fusion?
9. What does `hybrid_search` return on a Qdrant failure?
10. Which two `ChunkResult` fields are sentinels, and what for?

**Why**
11. Why retrieve 20 and answer with 5?
12. Why does the entity prefix exist?
13. Why no LLM query rewriting?
14. Why is dense retrieval's weakness structural rather than tunable?
15. Why must the same model embed queries and documents?

**Debugging**
16. Zero candidates. What are the two possible causes, and how do you tell them
    apart?
17. A query naming `PPBL` returns nothing about Paytm Payments Bank. Which
    weakness, and what covers it?
18. Retrieval quality drops after an "infrastructure change". What do you check
    before touching retrieval code?

**System design**
19. Add conversational follow-ups ("what about the previous year?"). What is
    needed, and what does it cost?
20. The prefix perturbs the dense vector and nothing measures it. Design the
    measurement.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Because BGE is **asymmetric**: the query side needs a prefix that tells the
   model to produce a *search* vector rather than a *document* vector.
   `query_embed` applies it; `embed` does not. Using `embed` would produce a
   document-style vector compared against document vectors — plausible, subtly
   wrong, and **no error would be raised**. Day 20's Experiment 2 shows the two
   are close but not identical, which is exactly what makes it hard to notice.
2. `hybrid_search` returns `[]` → `retrieve_and_rerank` logs *"returned 0 results
   — skipping rerank"* and returns `[]` → `semantic_engine._score_confidence`
   returns `(0.0, "low")` → the CRAG ladder runs and cannot improve on nothing →
   `confidence_tier == "low"` → a **refusal** with "Insufficient information found
   in the available documents". **It is right:** a retrieval outage must not
   become a confident answer from partial evidence. The cost is that a network
   failure and a genuinely empty corpus produce the same user-facing message,
   which is why `CLAUDE.md` §4 makes empty-vs-low-scoring a named diagnostic.
3. Both are set to `0.0` in `hybrid_search` and **never written again**. Qdrant's
   server-side RRF returns only the fused score, not the per-leg ones.
   **Conclusion:** two declared fields with no producer — the same shape as
   `cache_hit` and `preferred_operation`. Harmless, and worth recording, because a
   reader could reasonably assume `dense_score` means something.
4. So that an unscored chunk is **detectable**. `0.0` is a plausible score on the
   Cohere scale and a good one on the ONNX logit scale, so it would pass silently
   into confidence scoring. `-inf` cannot be mistaken for a real score, and
   `_score_confidence` explicitly checks for it and logs an error rather than
   returning a tier (Day 29).
5. It implies the prefix is designed for **one leg** and applied to **both**. Four
   prepended tokens shift the query vector toward "a document about ETERNAL FY26"
   and away from the question — proportionally more for short queries. **It is not
   measured** anywhere in this repository. It is a reasonable trade, and it is an
   assumption rather than a finding.

### §12 — Basic

1. Embed the query with the same model used for the documents; compare against
   stored vectors (via HNSW); return the top-K by cosine similarity.
2. On **exact identifiers** — codes, acronyms, near-identical tokens (`FY26` vs
   `FY25`), and proper nouns with little training signal.
3. 20 and 5. Retrieve 20 candidates, rerank down to 5.
4. The query with router-extracted entity and period tokens prefixed:
   `"ETERNAL FY26 Q4 consolidated <original>"`.
5. A second lookup. The chunk text and all metadata come back with the hit, so no
   Postgres join or follow-up fetch is needed.

### §12 — Code

6. A single 384-dimension numpy vector — `query_embed` yields a generator (the
   API is batch-first) and `next` takes the one result.
7. Because the Qdrant client serialises to JSON, and JSON has no numpy type. Same
   boundary class as `str(uuid)` in the payload writer.
8. The **RRF fusion score** (~0.016 at rank 1 with k=60), not a cosine similarity.
9. `[]`, after logging the error. It does not raise.
10. `reranker_score = float("-inf")` and `reranker_backend = "none"` — sentinels
    marking "not yet reranked", so an unscored chunk reaching confidence scoring
    is detected loudly instead of scored as terrible.

### §12 — Why

11. Because retrieval optimises **recall** and reranking optimises **precision**.
    Retrieval is fast and approximately ordered; asking it for a perfectly ordered
    top-5 gets you neither. There is also a second, codebase-specific reason:
    near-duplicate suppression needs a **pool to backfill from**, and with a pool
    of 5, dropping a duplicate leaves 4 chunks instead of promoting the 6th.
12. To put the entity and period in as **terms** for the BM25 leg — precisely the
    tokens dense retrieval handles worst. The metadata filter restricts the *set*;
    the prefix helps **rank** within it.
13. Cost. The router already makes one LLM call per query; a rewrite call doubles
    it against a 500/day ceiling, and multi-query expansion multiplies searches.
14. Because an embedding is a **summary of meaning**, and two invoice numbers or
    two fiscal-year labels differing by one character mean nearly the same thing —
    "an identifier of this kind". No amount of tuning changes what the
    representation is for.
15. Because each model defines its own coordinate system; cross-model comparisons
    are numerically valid and semantically meaningless, with no error (Day 20).

### §12 — Debugging

16. **(a)** Qdrant unreachable — a **network** signature; `hybrid_search` caught
    an exception and returned `[]`. **(b)** The **filter** excluded everything —
    wrong tenant, wrong company, wrong fiscal year. **Tell them apart** by reading
    the backend log: a network failure logs `Qdrant hybrid_search failed: …`,
    while a filter exclusion logs nothing and simply returns `hybrid_search
    returned 0 points`. Also check `printenv QDRANT_URL` and look for the
    insecure-connection warning (Day 21).
17. **Dense retrieval's weakness on acronyms** — `PPBL` has almost no training
    signal for its expansion, so its embedding is generic. **BM25 covers it**
    (Day 26) by matching the literal characters, which is why hybrid retrieval
    exists.
18. **Which Qdrant you are pointed at.** `printenv QDRANT_URL` — a local Docker
    Qdrant emits `UserWarning: Api key is used with an insecure connection`, and
    if that fires, every measurement in the session is invalid. Then check whether
    the corpus was re-embedded with a different model, and whether
    `reranker_backend` changed. Environment before code (Day 1).

### §12 — System design

19. **Needed:** conversation state — a store keyed by session holding the previous
    turns; a resolution step turning "the previous year" into `FY25` (either an
    LLM call, or deterministic rules over the *previous* `QueryState`, which is
    cheaper and fits this codebase's preference for determinism); and a change to
    `QueryRequest` and `QueryState` to carry it. **Costs:** server-side state,
    which nothing here currently has (JWTs are stateless, Redis is broker-only);
    an extra LLM call per turn if resolution is model-driven; and — the one people
    miss — **cache-key and audit implications**: the textbook's 15B semantic-cache
    collision case is exactly this, where "what was their revenue?" means different
    things in different conversations. The audit row would also need the
    conversation id, or the trail stops being reconstructible.
20. **The measurement:** run the golden dataset's semantic questions twice —
    once encoding the **raw** query for the dense leg and the **resolved** query
    for the sparse leg, once with `resolved` for both (current behaviour) — with
    everything else held fixed, and compare pass rates per category and the rank
    of the known-correct chunk. **What makes it valid:** three runs per arm with
    provider, model and `reranker_backend` printed (`CLAUDE.md` §8 — cause cannot
    be assigned from a single pair); `--delay 45`; the largest dataset first as a
    gate; and **report, do not interpret**. **What makes it cheap:** it needs no
    LLM at all if you score on *retrieval rank of the expected chunk* rather than
    on answer text — turning a 165-call sweep into a zero-call one. That is the
    version worth building first, and it would also give Day 27 and Day 28 a
    reusable instrument.

---

## 14. MUST REMEMBER

```text
- _encode_dense uses model.query_embed(), NOT embed() — BGE is asymmetric
- Dense retrieval fails on EXACT IDENTIFIERS: acronyms, codes, FY26 vs FY25
- TOP_K_RETRIEVAL = 20 → TOP_K_RERANK = 5. Recall first, precision second
- 20 is also the POOL near-duplicate suppression backfills from
- resolved_query = "ETERNAL FY26 Q4 consolidated <question>" — the BM25 prefix
- hybrid_search returns [] on failure; it never raises
- EMPTY = network signature. LOW-SCORING = retrieval signature
- reranker_score starts at -inf so an unscored chunk is DETECTABLE
- dense_score / sparse_score are declared and never populated
```

## 15. MUST UNDERSTAND

```text
- Why dense retrieval's weakness is REPRESENTATIONAL, not a tuning problem —
  and why everything identifying WHICH filing you mean falls in that gap
- Why retrieval optimises recall and reranking optimises precision, and what
  goes wrong if you ask retrieval for precision
- Why the entity prefix is written for one leg and applied to both, and that
  its dense-side effect is an ASSUMPTION, not a measurement
- Why a retrieval outage degrading to a refusal is correct, and what it costs
  in diagnosability
```

---

## 16. This connects to

```text
Day 24 — the index is built
   ↓
Day 25 — the first query: dense retrieval        ← you are here
   ↓
Day 26 — the complementary signal: BM25
   ↓
Day 27 — fusing them, and where the filter goes
```

Forward references:

- `_encode_sparse` and BM25 → **Day 26**
- `Prefetch` + `FusionQuery(RRF)`, and filter placement → **Day 27**
- `rerank()` and the two score scales → **Day 28**
- The `-inf` sentinel's loud error → **Day 29**
- `_build_resolved_query`'s caller → **Day 36**
