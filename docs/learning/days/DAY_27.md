# Day 27 — Hybrid Retrieval, RRF, and Where the Filter Goes

**Phase 7 · Weight: H (~120 min) · Prerequisites: Days 14, 25, 26**

**Textbook: 5.4 "Hybrid Retrieval" — CONFIRMS · 15B "The Metadata Filter That
Silently Returns Zero Results" — EXTENDS/DIVERGES: LedgerMind's failure is the
INVERSE.**

---

## 1. Today's goal

By tonight you can:

- Explain why two rankings **cannot** be merged by averaging their scores.
- Explain Reciprocal Rank Fusion: the formula's intent, the role of `k`, and why
  rank is comparable when score is not.
- Explain **filter placement** — inside each prefetch leg, not at fusion — as a
  *correctness* decision.
- Explain audit finding **F2**: a filter that was silently **dropped**, and why
  that is worse than the textbook's too-strict filter.
- Explain audit finding **F7**: a filter that is functionally **inert**, and why.

---

## 2. Why now

Days 25 and 26 gave you two rankings of the same 20-candidate space, each strong
where the other is weak. Today they become one. Day 28 then reorders the survivors
with a far more expensive model — and Day 28 is unteachable before today, because
"the score scale is incomparable" is the idea today establishes.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Dense retrieval and its failure | Day 25 | One leg |
| BM25 and its failure | Day 26 | The other |
| Named vectors on one point | Day 21 | Why one query works |
| RLS fails closed, Qdrant fails open | Day 14 | Today's filter is the Qdrant half |

---

## 4. Concept lesson

### 4.1 The problem: two rankings, no common scale

Run both legs and you get:

```
DENSE                                   SPARSE (BM25)
1. chunk_A   cosine 0.87                1. chunk_D   bm25 8.94
2. chunk_B   cosine 0.81                2. chunk_A   bm25 6.12
3. chunk_C   cosine 0.79                3. chunk_E   bm25 5.03
```

**How do you merge these?**

**Attempt 1 — add the scores.** `0.87 + 6.12` is meaningless. Cosine is bounded
`[-1, 1]`; BM25 is unbounded and collection-dependent.

**Attempt 2 — normalise, then add.** Min-max normalise each list to `[0,1]` and
average. Better, and still broken:

- The normalisation depends on **this result set**, so the same chunk gets a
  different normalised score depending on what else was retrieved.
- A leg where everything scores similarly gets stretched to fill `[0,1]`,
  manufacturing separation that is not there.
- You now have a tunable weight (how much dense vs sparse?) with nothing to tune
  it against.

The textbook (5.4) states the core objection:

> Since dense retrieval produces cosine similarity scores (typically 0 to 1) and
> BM25 produces unbounded, differently-scaled scores, **the two cannot simply be
> added together numerically.**

---

### 4.2 Reciprocal Rank Fusion

**The insight: throw the scores away and keep the ranks.**

```
RRF_score(doc) = Σ over each method:  1 / (rank_in_that_method + k)
```

Textbook 5.4 on why:

> Rank position is directly comparable across methods regardless of how each
> method's internal scoring is scaled — "this document was the 1st most relevant
> according to BM25" and "this document was the 1st most relevant according to
> dense retrieval" are **directly combinable concepts**, whereas "BM25 score 8.94"
> and "cosine similarity 0.91" are **not on the same numerical scale at all**.

**Worked, with k = 60:**

| Chunk | dense rank | sparse rank | RRF |
|---|---|---|---|
| A | 1 | 2 | 1/61 + 1/62 = **0.03253** |
| D | — | 1 | 1/61 = **0.01639** |
| B | 2 | — | 1/62 = **0.01613** |
| C | 3 | — | 1/63 = **0.01587** |
| E | — | 3 | 1/63 = **0.01587** |

**Chunk A wins** — not because either leg ranked it first, but because **both**
ranked it highly. That is the property: agreement between independent signals
beats a strong showing in one.

**What `k` does.** With `k = 0`, rank 1 scores 1.0 and rank 2 scores 0.5 — rank 1
dominates absolutely. With `k = 60`, rank 1 is 0.0164 and rank 2 is 0.0161: a 2%
difference. **`k` compresses the gaps**, so a chunk appearing in both lists
outweighs a chunk that is first in one and absent from the other. 60 is the
convention from the original paper and is what Qdrant uses.

**And a consequence to internalise:** RRF scores are tiny — ~0.016 at rank 1.
`retriever.py` says so where it matters:

> RRF scores (~0.016 at rank 1, k=60) are a **third incompatible scale** needing
> their own calibrated thresholds

Three scales now: cosine, BM25, RRF. Day 28 adds a fourth and a fifth.

---

### 4.3 Filter placement — the decision that is about correctness

Two places a metadata filter could go:

```
OPTION A — at fusion                    OPTION B — inside each leg
  dense: top-20 of EVERYTHING             dense: top-20 of MATCHING
  sparse: top-20 of EVERYTHING            sparse: top-20 of MATCHING
        ↓ fuse                                  ↓ fuse
     filter the fused list                   fuse 40 already-valid
        ↓                                          ↓
     ~3 survivors, maybe 0                   20 valid results
```

**LedgerMind uses B**, and the module docstring is explicit:

> Filter runs INSIDE each prefetch leg (not at fusion level) so both dense and
> sparse candidates are pre-filtered before RRF. **This is the correct pattern —
> filtering at fusion level would allow unfiltered candidates to pollute
> ranking.**

**Three distinct problems with Option A:**

**1. Recall collapse.** If ETERNAL is a fifth of the corpus, roughly 4 of each
leg's 20 survive. You asked for 20 and got 8.

**2. Ranking pollution.** The *ranks* fed into RRF were computed against the
unfiltered population. A chunk at dense rank 7 among all companies might be rank 1
among ETERNAL's — RRF sees 7 and weights it accordingly. **The fusion is
arithmetically fine and semantically wrong.**

**3. Security.** Textbook 16's multi-tenancy row: *"Filtering after retrieval
instead of at the DB level"* is listed as the common mistake, and the interview
question is *"Why is filtering after retrieval a security risk?"* Because the
other tenant's data **left the database**. Any bug between retrieval and
filtering leaks it. Day 14 established that Qdrant fails **open**; Option A widens
that exposure.

**Mental model.** Option A is **searching the whole library and then discarding
the books you may not read**. Option B is **searching only your section**.

---

### 4.4 The textbook's failure, and LedgerMind's inverse

Textbook 15B:

> **The Metadata Filter That Silently Returns Zero Results** … if the filter is
> **too strict**, zero chunks pass the filter, and your retrieval returns empty
> results. If your prompt then receives empty context and produces an answer
> anyway, it is hallucinating.

LedgerMind handles that: `hybrid_search` returns `[]`, `semantic_engine` scores
`low`, and the system refuses (Day 25).

**LedgerMind's actual failure is the opposite.** Audit finding **F2**: the company
condition was **silently dropped**.

```python
must_conditions = [
    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
    FieldCondition(key="is_latest", match=MatchValue(value=is_latest)),
]

if companies is None or len(companies) == 0:
    logger.warning(
        "UNFILTERED WHOLE-TENANT SEARCH | no company condition applied | "
        "tenant_id=%s companies=%r fiscal_year=%r quarter=%r financial_type=%r",
        tenant_id, companies, fiscal_year, quarter, financial_type,
    )
else:
    must_conditions.append(
        FieldCondition(key="company", match=MatchAny(any=list(companies)))
    )
```

**Why dropped is worse than too strict:**

| Too strict | Dropped |
|---|---|
| Returns nothing | Returns **something** |
| Obviously wrong | **Plausibly right** |
| The system refuses | The system **answers confidently** |
| You investigate | You believe it |

The measured case, from `router.py`'s F2 comment:

> Measured 2026-08-11 on *"What were Reliance Industries revenue drivers in
> FY26?"*: `company=None`, `company_unresolved=None` … That query still runs
> **unfiltered over the whole tenant** and answers at **tier=high**.

And the projection:

> Three companies in three sectors currently mask that; at N+20 with several
> issuers in one sector an unfiltered search retrieves a competitor's chunk, **the
> reranker scores it highly because it IS topically relevant**, and the answer
> cites a real page from the wrong company.

**The reranker cannot save you**, because the wrong company's chunk is genuinely
about the right topic.

---

### 4.5 What `_build_filter` does and does not do

**It detects and reports. It does not refuse.** The comment is emphatic:

> **DETECT AND REPORT.** This deliberately does NOT refuse. Q051 ("Who grew
> revenue faster in FY26, Eternal or Paytm?") passes BECAUSE the search runs
> unfiltered here while the DSL carries both issuers through
> `entity`/`comparison_entity` — measured 2026-08-22: `path=quantitative`,
> ETERNAL faster, 168.56 vs 22.28, `sql_verified=true`, confidence 1.0.
> **Refusing on empty would refuse a passing golden question.**

**The refusal lives one layer up**, in `router_node` (Day 36), where it can
distinguish "the model named a company we do not hold" from "no company was
named". Here, that distinction is unavailable — `_build_filter` receives a list,
not a reason.

**And the explicit-check comment is worth reading for its own sake:**

> **EXPLICIT, not truthiness** — and behaviour-identical to the `if company:` this
> replaces. … Nothing about WHICH branch runs has changed; what changed is that
> **the branch is now named and recorded.**
>
> **WHY NOW, ahead of F14.** That change makes this field `companies: list[str]`,
> and `[]` is falsy too … Worse, the Gemini schema node loses `nullable` under a
> list type … so `[]` becomes the model's ONLY way to say "no issuer" — making
> this branch **MORE reachable** than the null it replaces.

**A defensive change made *before* the change that would make the hazard more
likely.** And it connects straight back to Day 18: the schema is prompt input, and
its shape changes what the model can express.

---

### 4.6 F7 — the filter that is inert

```python
if financial_type:
    must_conditions.append(
        Filter(should=[
            FieldCondition(key="financial_type", match=MatchValue(value=financial_type)),
            FieldCondition(key="financial_type", match=MatchValue(value="unknown")),
        ])
    )
```

An **OR**: match the requested type **or** `"unknown"`.

**Why the OR exists.** Day 24: non-statement chunks — risk disclosures, MD&A — are
genuinely not scoped to consolidated or standalone, so they carry
`financial_type="unknown"`. Without the OR, asking for consolidated would exclude
every risk factor.

**Why it is inert.** Audit **F7** / `CAVEAT-006`: because *most* narrative chunks
are `"unknown"`, the OR admits nearly everything. The filter excludes only
statement chunks of the *other* type — a small fraction.

**So it is correct and nearly useless**, and both halves are true. The fix is not
in the retriever: it is audit **F7**'s proposal to split `"unknown"` into
*narrative* (correctly unscoped) and *undetermined* (classification failed) — a
change in `chunker._build_metadata`, one layer up.

**And note `quarter`:**

```python
if quarter is not None:
    must_conditions.append(FieldCondition(key="quarter", match=MatchValue(value=quarter)))
```

`is not None`, not truthiness — so `quarter=""` would filter (and match nothing),
while `None` skips. `IMPLEMENTATION_DELTAS.md` §D records that the quarter filter
is *currently a no-op* for a different reason: the router rarely sets it, and
annual rows carry `quarter=None` in the payload.

**Three filters, three states:** `tenant_id` and `is_latest` always applied;
`company` applied-or-logged; `financial_type` applied-but-inert.

---

## 5. The actual LedgerMind file

```
File:        backend/app/engines/retriever.py — _build_filter, hybrid_search
Entry points: _build_filter(tenant_id, companies, fiscal_year, quarter,
                            financial_type, is_latest=True) -> Filter
             hybrid_search(query, tenant_id, companies, ..., top_k=20)
                            -> list[ChunkResult]
Data in:     a query string + QueryState metadata
Data out:    up to 20 ChunkResult, rrf_score populated, reranker_score = -inf
Invariants:  tenant_id ALWAYS applied · is_latest ALWAYS applied
             filter INSIDE each prefetch leg
```

---

## 6. Deep walkthrough

### 6.1 `_build_filter`, condition by condition

**STATE BEFORE.** Metadata from `QueryState`, any of which may be absent.

```python
must_conditions = [
    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
    FieldCondition(key="is_latest", match=MatchValue(value=is_latest)),
]
```

**Unconditional, both.** `tenant_id` because *"multi-tenant isolation is
non-negotiable"*; `is_latest` because a restated figure's superseded chunk must
not be retrievable (Day 15's model, applied to the vector store).

```python
if companies is None or len(companies) == 0:
    logger.warning("UNFILTERED WHOLE-TENANT SEARCH | ...")
else:
    must_conditions.append(FieldCondition(key="company", match=MatchAny(any=list(companies))))
```

**`MatchAny`, not `MatchValue`** — F14. And the comment closes the migration
question:

> A single-element list produces the same result set as the pre-F14 `MatchValue` …
> The `company` payload key is already indexed **KEYWORD**, which is the type
> `MatchAny` operates on, so **no re-index is required.**

Day 21's payload-index table is what made that true.

**The warning is single-line**, and the comment says why *and* names its own
limitation:

> Single line: Render truncates multi-line output. `tenant_id` is the only
> identifier in scope at this layer — `_build_filter` receives no `request_id` or
> query, and widening its signature to carry one is **a change to every caller
> rather than to this guard.**

A logging improvement declined because its blast radius exceeded its value. That
is a real engineering judgement, recorded.

**STATE AFTER.** A `Filter(must=[...])` with 2–5 conditions.

---

### 6.2 `hybrid_search` — one query, two legs

```python
dense_vector = _encode_dense(query)
sparse_vector = _encode_sparse(query)
search_filter = _build_filter(...)

result = client.query_points(
    collection_name=COLLECTION_NAME,
    prefetch=[
        Prefetch(query=dense_vector,  using=DENSE_VECTOR_NAME,  limit=top_k, filter=search_filter),
        Prefetch(query=sparse_vector, using=SPARSE_VECTOR_NAME, limit=top_k, filter=search_filter),
    ],
    query=FusionQuery(fusion=Fusion.RRF),
    limit=top_k,
    with_payload=True,
)
```

**The same `search_filter` object in both legs.** One construction, two uses —
they cannot diverge.

**`limit=top_k` appears three times:** 20 from dense, 20 from sparse, 20 after
fusion. The union of two 20-lists is 20–40 candidates; fusion ranks them and
returns the best 20.

**`Fusion.RRF` is server-side.** No scores cross the network to be merged in
Python. The docstring: *"Qdrant `FusionQuery(Fusion.RRF)` is used for native RRF —
**no manual score merging**."* One less place to get a scale wrong.

**And the failure path** (Day 25): `except Exception: return []`.

---

### 6.3 Payload → `ChunkResult`, and the score that changes meaning

```python
chunk = ChunkResult(
    ...,
    dense_score=0.0,
    sparse_score=0.0,
    rrf_score=point.score,
    reranker_score=float("-inf"),
    reranker_backend="none",
)
```

**`point.score` is the RRF score**, ~0.016 at rank 1. Not a cosine, not a BM25
score.

**`dense_score` and `sparse_score` are `0.0` and never populated** — server-side
fusion does not return the per-leg scores (Day 25 §6.3). Two fields with no
producer.

**`reranker_score = -inf` / `reranker_backend = "none"`** — sentinels, so Day 28's
scoring can tell "not yet reranked" from "scored badly".

---

### 6.4 `retrieve_and_rerank`, and the removed flag

```python
def retrieve_and_rerank(query, tenant_id, companies=None, ..., 
                        retrieval_top_k=TOP_K_RETRIEVAL, rerank_top_k=TOP_K_RERANK):
    candidates = hybrid_search(...)
    if not candidates:
        logger.warning("hybrid_search returned 0 results — skipping rerank")
        return []
    return rerank(query=query, chunks=candidates, top_k=rerank_top_k)
```

Between them sits one of the most instructive comments in the file:

> `DISABLE_LOCAL_RERANKER` removed 2026-07-30. It was a temporary RAM mitigation …
> and it was **silently broken**: it returned `hybrid_search` candidates directly,
> whose `reranker_score` is still `float("-inf")` and whose `reranker_backend` is
> `"none"`. `_score_confidence()` then read `-inf`, fell to the local logit
> thresholds, and classified **EVERY semantic query as LOW → refusal**, after
> burning all CRAG rungs on identical retrievals first. **Nothing surfaced an
> error; `confidence_score` simply read 0.0.**
>
> Not reinstated as a "fix" because RRF scores (~0.016 at rank 1, k=60) are a
> **third incompatible scale** needing their own calibrated thresholds — the exact
> class of mismatch that produced the Cohere-vs-local confidence bug.

**Three lessons in one comment:**

1. A "temporary mitigation" produced total semantic failure, silently.
2. The sentinels **worked** — `-inf` propagated exactly as designed. What was
   missing was a *check*, which Day 29 added.
3. Reinstating it would require calibrating a **third** scale — and mixing scales
   is the defining bug of this subsystem.

---

## 7. Data flow

```
resolved_query  +  QueryState metadata
        │
        ├──────────────┬───────────────────────────┐
        ▼              ▼                           ▼
  _encode_dense   _encode_sparse            _build_filter
   [384 floats]   SparseVector       ┌─────────────────────────────┐
        │              │             │ tenant_id      ALWAYS       │
        │              │             │ is_latest      ALWAYS       │
        │              │             │ company        MatchAny,    │
        │              │             │                or LOGGED    │
        │              │             │ fiscal_year    if set       │
        │              │             │ quarter        if not None  │
        │              │             │ financial_type OR "unknown" │
        │              │             └──────────┬──────────────────┘
        │              │                        │
        ▼              ▼                        │
  ┌──────────────┐  ┌──────────────┐            │
  │ Prefetch     │  │ Prefetch     │◄───────────┘  SAME filter object
  │ using=dense  │  │ using=sparse │               INSIDE each leg
  │ limit=20     │  │ limit=20     │
  │ filter=f     │  │ filter=f     │
  └──────┬───────┘  └──────┬───────┘
         │ 20 filtered     │ 20 filtered
         └────────┬────────┘
                  ▼
        FusionQuery(Fusion.RRF)      server-side, k=60
                  ▼
          20 points, rrf_score ~0.016 at rank 1
                  ▼
          ChunkResult × 20   (reranker_score = -inf)
                  ▼
             rerank()                                      (Day 28)
```

---

## 8. Engineering decision — RRF with in-leg filtering

**Problem.** Combine two rankings with incomparable scores, over a
metadata-restricted subset, without leaking another tenant's data.

**Decision.** Native server-side RRF over two prefetch legs, each carrying the
same filter.

`ENGINEERING_DECISIONS.md` **ED-002** (hybrid + RRF), **ED-003** (filter
placement).

| Alternative | Why not |
|---|---|
| **Normalise and average** | Normalisation depends on the result set; manufactures separation; introduces an untunable weight |
| **Dense only** | Fails on acronyms, tickers, `FY26` vs `FY25` |
| **Sparse only** | Scores zero on paraphrases |
| **Filter at fusion** | Recall collapse, ranking pollution, and a security exposure |
| **Client-side RRF** | Scores crossing the network to be merged by hand — one more scale to get wrong |
| **Weighted hybrid (α·dense + (1−α)·sparse)** | Requires a labelled set to tune α. RRF needs no tuning |

**Trade-offs accepted.**

- **RRF discards magnitude.** A chunk ranked 1 by a huge margin and one ranked 1
  narrowly score identically. Reranking (Day 28) is what recovers precision.
- **A third scale.** `rrf_score` ~0.016 is not comparable to anything else, and
  `DISABLE_LOCAL_RERANKER` proved what happens when it is treated as one.
- **The company filter can be absent**, logged not refused — because refusing
  would refuse a passing golden question.
- **`financial_type` is inert** (F7) — correct and nearly useless.

**Current validity.** The mechanism is right. The two open findings are F2
(partially closed one layer up, Day 36) and F7.

**At 10×** — in *issuers*, not documents. F2's projection is explicit: with several
issuers in one sector, an unfiltered search retrieves a competitor's chunk and the
reranker scores it highly *because it is topically relevant*. **Corpus diversity,
not corpus size, is what makes F2 dangerous.**

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| Confident answer about a company not in the corpus | **F2** — company condition dropped. `grep "UNFILTERED WHOLE-TENANT"` |
| `financial_type` appears to do nothing | **F7** — most chunks are `"unknown"` |
| Zero candidates | Network (Day 25) **or** a filter matching nothing |
| Fewer results than expected | Would be Option A's recall collapse — not this codebase |
| Every semantic query refuses | Historic `DISABLE_LOCAL_RERANKER`: `-inf` reaching confidence scoring |
| Superseded figures retrieved | `is_latest` condition removed |
| Cross-tenant chunks | The `tenant_id` condition omitted — **fails open** |
| RRF scores look tiny | **Correct.** ~0.016 at rank 1 with k=60 |

---

## 10. Hands-on experiment

### Experiment 1 — RRF by hand

```bash
docker compose exec -T backend python -c "
K = 60
dense  = ['A','B','C','F','G']
sparse = ['D','A','E','B','H']
scores = {}
for lst, name in ((dense,'dense'), (sparse,'sparse')):
    for rank, doc in enumerate(lst, start=1):
        scores.setdefault(doc, {})[name] = (rank, 1/(rank+K))
for doc, d in sorted(scores.items(), key=lambda kv: -sum(v[1] for v in kv[1].values())):
    total = sum(v[1] for v in d.values())
    where = ' '.join(f'{k}#{v[0]}' for k,v in d.items())
    print(f'  {doc}  RRF={total:.5f}   {where}')
print()
print('A is first in NEITHER list and wins. Agreement beats a strong single showing.')
"
```

### Experiment 2 — what `k` controls

```bash
docker compose exec -T backend python -c "
for K in (0, 1, 10, 60, 200):
    r1, r2, r5 = 1/(1+K), 1/(2+K), 1/(5+K)
    print(f'  k={K:4d}  rank1={r1:.5f} rank2={r2:.5f} rank5={r5:.5f}  '
          f'rank1/rank2={r1/r2:.3f}')
print()
print('k=0: rank 1 is 2x rank 2 — it dominates.')
print('k=60: rank 1 is 1.02x rank 2 — appearing in BOTH lists matters more.')
"
```

### Experiment 3 — dense, sparse, fused

```bash
docker compose exec -T backend python -c "
import os
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny, Prefetch, FusionQuery, Fusion
from app.engines.retriever import (_encode_dense, _encode_sparse, _get_qdrant_client,
                                   COLLECTION_NAME, DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME)
c = _get_qdrant_client()
T = os.getenv('T','')
f = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=T)),
                 FieldCondition(key='company',   match=MatchAny(any=['ETERNAL']))])
q = 'ETERNAL FY26 Q4 consolidated impairment and one-off charges'
dv, sv = _encode_dense(q), _encode_sparse(q)

def ids(r): return [str(p.id)[:8] for p in r.points]
d = c.query_points(collection_name=COLLECTION_NAME, query=dv, using=DENSE_VECTOR_NAME,  limit=8, query_filter=f)
s = c.query_points(collection_name=COLLECTION_NAME, query=sv, using=SPARSE_VECTOR_NAME, limit=8, query_filter=f)
h = c.query_points(collection_name=COLLECTION_NAME,
                   prefetch=[Prefetch(query=dv, using=DENSE_VECTOR_NAME, limit=20, filter=f),
                             Prefetch(query=sv, using=SPARSE_VECTOR_NAME, limit=20, filter=f)],
                   query=FusionQuery(fusion=Fusion.RRF), limit=8)
print('dense :', ids(d)); print('sparse:', ids(s)); print('fused :', ids(h))
print()
print('fused rank-1 rrf_score:', round(h.points[0].score, 5), '<- ~0.016, a THIRD scale')
print()
both = set(ids(d)) & set(ids(s))
print('appeared in BOTH legs:', sorted(both))
"
```

### Experiment 4 — filter placement, measured

```bash
docker compose exec -T backend python -c "
import os
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny, Prefetch, FusionQuery, Fusion
from app.engines.retriever import (_encode_dense, _encode_sparse, _get_qdrant_client,
                                   COLLECTION_NAME, DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME)
c = _get_qdrant_client(); T = os.getenv('T','')
tenant = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=T))])
scoped = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=T)),
                      FieldCondition(key='company',   match=MatchAny(any=['ETERNAL']))])
q = 'revenue growth drivers'
dv, sv = _encode_dense(q), _encode_sparse(q)

# OPTION A: unfiltered legs, filter afterwards (simulated)
a = c.query_points(collection_name=COLLECTION_NAME,
      prefetch=[Prefetch(query=dv, using=DENSE_VECTOR_NAME, limit=20, filter=tenant),
                Prefetch(query=sv, using=SPARSE_VECTOR_NAME, limit=20, filter=tenant)],
      query=FusionQuery(fusion=Fusion.RRF), limit=20, with_payload=True)
survivors = [p for p in a.points if p.payload.get('company')=='ETERNAL']
# OPTION B: filter inside each leg (what LedgerMind does)
b = c.query_points(collection_name=COLLECTION_NAME,
      prefetch=[Prefetch(query=dv, using=DENSE_VECTOR_NAME, limit=20, filter=scoped),
                Prefetch(query=sv, using=SPARSE_VECTOR_NAME, limit=20, filter=scoped)],
      query=FusionQuery(fusion=Fusion.RRF), limit=20, with_payload=True)
print('OPTION A  filter AFTER fusion : asked 20, got', len(survivors), 'ETERNAL chunks')
print('OPTION B  filter INSIDE legs  : asked 20, got', len(b.points), 'ETERNAL chunks')
print()
print('And in A, the RANKS fed to RRF were computed against the whole tenant.')
"
```

### Experiment 5 — see F2's warning fire

```bash
docker compose exec -T backend python -c "
import logging, os
logging.basicConfig(level=logging.INFO, force=True)
from app.engines.retriever import _build_filter
print('--- with companies ---')
f1 = _build_filter(tenant_id=os.getenv('T',''), companies=['ETERNAL'], fiscal_year='FY26')
print('conditions:', len(f1.must))
print()
print('--- with an EMPTY list ---')
f2 = _build_filter(tenant_id=os.getenv('T',''), companies=[], fiscal_year='FY26')
print('conditions:', len(f2.must), ' <- one fewer. The search is unfiltered by company.')
"
```

Then find it in production logs:

```bash
docker compose logs backend | grep -c "UNFILTERED WHOLE-TENANT SEARCH" || echo 0
```

### Experiment 6 — F7, quantified

```bash
docker compose exec -T backend python -c "
import os
from collections import Counter
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.engines.retriever import _get_qdrant_client, COLLECTION_NAME
c = _get_qdrant_client()
f = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=os.getenv('T','')))])
seen, offset = Counter(), None
for _ in range(20):
    pts, offset = c.scroll(collection_name=COLLECTION_NAME, scroll_filter=f,
                           limit=500, offset=offset, with_payload=['financial_type'])
    for p in pts: seen[p.payload.get('financial_type')] += 1
    if offset is None: break
total = sum(seen.values())
for k, v in seen.most_common():
    print(f'  {str(k):14} {v:6d}  {100*v/total:5.1f}%')
print()
print('The OR admits requested-type OR \"unknown\". If \"unknown\" dominates,')
print('the filter excludes almost nothing. That is audit F7.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/retriever.py::_build_filter` and `hybrid_search`:

1. Which two conditions are **always** applied, and what does each protect?
2. `if companies is None or len(companies) == 0` replaced `if company:`. The
   comment says behaviour is identical. What changed, then — and why was it done
   *before* F14?
3. `financial_type` is an OR with `"unknown"`. Why does the OR exist, and what
   does it cost?
4. The `UNFILTERED WHOLE-TENANT SEARCH` warning has no `request_id`. Find the
   comment explaining why, and say whether you agree.
5. Read the `DISABLE_LOCAL_RERANKER` comment. What failed, why was nothing
   raised, and why was it not reinstated?

---

## 12. Self-check questions

**Basic**
1. Why can two rankings not be merged by adding scores?
2. What is RRF, in one sentence?
3. What does `k = 60` do?
4. Where does the filter go, and why?
5. What is `rrf_score` roughly, at rank 1?

**Code**
6. Which conditions does `_build_filter` always add?
7. What is `MatchAny` for, and why no re-index?
8. How many `limit=top_k` are there in `hybrid_search`, and what does each mean?
9. Where does fusion happen — client or server?
10. What is `reranker_score` set to here?

**Why**
11. Why is rank comparable when score is not?
12. Why is filtering at fusion a *security* issue and not only a recall one?
13. Why does `_build_filter` log rather than refuse?
14. Why is a dropped filter worse than a too-strict one?
15. Why would reinstating `DISABLE_LOCAL_RERANKER` require new calibration?

**Debugging**
16. An answer confidently cites the wrong company. What do you grep for?
17. `financial_type="standalone"` seems not to filter. Which finding, and where is
    the cause?
18. Every semantic query refuses, with `confidence_score = 0.0` and no error. What
    class of bug?

**System design**
19. Close F2 properly. Where does the fix belong, and what must it not break?
20. F7: `financial_type` is inert. Design the fix, and name every file it touches.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `tenant_id` — multi-tenant isolation, described as non-negotiable, and the
   **only** isolation Qdrant has (it fails open, unlike RLS). `is_latest` — so a
   superseded chunk from a restated filing cannot be retrieved, mirroring the
   `financials` model (Day 15) in the vector store.
2. **Nothing about which branch runs changed** — for `Optional[str]` the falsy set
   is exactly `{None, ""}`, so the explicit check takes the same branch on every
   reachable value. What changed is that the branch is **named and logged**. It
   was done ahead of F14 because F14 makes the field `list[str]`, `[]` is also
   falsy, **and** the Gemini schema node loses `nullable` under a list type — so
   `[]` becomes the model's only way to express "no issuer", making the branch
   **more reachable** than the null it replaced. A guard hardened before the change
   that would stress it.
3. **Exists because** non-statement chunks (risk, MD&A) are genuinely not scoped
   to consolidated or standalone and carry `"unknown"` (Day 24); without the OR,
   asking for consolidated would exclude every risk factor. **Costs:** because
   `"unknown"` dominates the narrative corpus, the OR admits nearly everything —
   audit **F7**, the filter is correct and nearly useless.
4. The comment says `_build_filter` receives no `request_id` or query, and
   *"widening its signature to carry one is a change to every caller rather than
   to this guard."* **Reasonable to agree:** the blast radius exceeds the value,
   and `tenant_id` plus a timestamp usually suffices to correlate. **Reasonable to
   disagree:** correlating this warning with a specific answer is exactly what you
   want when investigating F2, and a single optional keyword argument defaulting
   to `None` would not force every caller to change. Either answer is defensible;
   what matters is recognising it as a *recorded judgement*, not an oversight.
5. It returned `hybrid_search` candidates **directly**, so `reranker_score` stayed
   `float("-inf")` and `reranker_backend` stayed `"none"`. `_score_confidence` read
   `-inf`, fell through to the local logit thresholds, and classified **every**
   semantic query as LOW → refusal — after burning all CRAG rungs on identical
   retrievals. **Nothing raised** because `-inf` is a valid float and every
   comparison against it is well-defined; `confidence_score` simply read `0.0`.
   **Not reinstated** because RRF scores (~0.016) are a *third* incompatible scale
   needing their own calibrated thresholds — the same mismatch class as the
   Cohere-vs-local confidence bug.

### §12 — Basic

1. Because the scales are incomparable: cosine is bounded `[-1,1]`, BM25 is
   unbounded and collection-dependent. Normalising first makes the score depend on
   what else was retrieved and introduces an untunable weight.
2. Sum `1/(rank + k)` across methods — fuse by **rank**, discarding scores.
3. It compresses the gaps between ranks, so appearing in **both** lists outweighs
   being first in one and absent from the other.
4. **Inside each prefetch leg**, so both legs' ranks are computed over the same
   filtered population and no non-matching data leaves the store.
5. ~0.016 (`1/61` from one leg, ~`0.0325` if both legs rank it first).

### §12 — Code

6. `tenant_id` and `is_latest`.
7. Matching **any** value in a list — F14's multi-issuer filter. No re-index
   because `company` is already a `KEYWORD` payload index, which is the type
   `MatchAny` operates on.
8. Three: 20 from the dense leg, 20 from the sparse leg, 20 after fusion. The
   union of the legs is 20–40 candidates; fusion ranks them and returns 20.
9. **Server-side**, via `FusionQuery(Fusion.RRF)`. No manual score merging.
10. `float("-inf")`, with `reranker_backend = "none"` — sentinels meaning "not yet
    reranked".

### §12 — Why

11. Because "1st according to BM25" and "1st according to dense retrieval" are the
    same *kind* of statement, while "8.94" and "0.91" are quantities on unrelated
    scales.
12. Because with Option A the other tenant's (or other company's) chunks **leave
    the database** and exist in application memory. Any bug between retrieval and
    filtering leaks them, and Qdrant's isolation already **fails open** (Day 14).
13. Because refusing on an empty list would refuse a **passing golden question** —
    Q051 passes precisely because the search runs unfiltered while the DSL carries
    both issuers. The refusal belongs one layer up in `router_node`, where the
    *reason* for the empty list is available.
14. Because a too-strict filter returns nothing, which is obviously wrong and
    triggers a refusal. A dropped filter returns something plausible, and the
    system answers **confidently** from the wrong company's pages — measured at
    `tier=high`.
15. Because it would feed **RRF scores** into confidence scoring, and RRF is a
    third scale (~0.016 at rank 1) with no calibrated thresholds. Reusing the
    Cohere or ONNX thresholds against it is exactly the scale-mismatch bug this
    subsystem has already been burned by.

### §12 — Debugging

16. `grep "UNFILTERED WHOLE-TENANT SEARCH"` in the backend logs, correlated by
    `tenant_id` and time. That is **F2**'s signature. Then check `route_reason` and
    `company_unresolved` on the response, and whether `router_node`'s refusal
    should have fired (Day 36).
17. **Audit F7 / `CAVEAT-006`.** The cause is **not** in the retriever: it is in
    `chunker._build_metadata` (Day 24), which stamps `financial_type="unknown"` on
    every non-statement chunk — correctly, since risk and MD&A are genuinely
    unscoped. Because `"unknown"` dominates, the OR admits nearly everything.
18. **A silent scale mismatch.** Unscored chunks (`reranker_score = -inf`) reached
    `_score_confidence`, which compared `-inf` against thresholds and returned
    `low` for everything. Nothing raised because every comparison was
    well-defined. This is the `DISABLE_LOCAL_RERANKER` failure, and Day 29's loud
    error exists to catch it.

### §12 — System design

19. **The fix belongs in `router_node`, not `_build_filter`** — and it is partly
    there already (F2 step 1, Day 36): refuse when the model named issuers and
    **none** resolved. `_build_filter` cannot make this decision because it
    receives a list, not a *reason* for the list being empty. **What it must not
    break:** Q051 — "Who grew revenue faster in FY26, Eternal or Paytm?" —
    passes *because* the search runs unfiltered while the DSL carries both issuers
    through `entity`/`comparison_entity`. So the refusal must key on
    "named-but-unresolvable", never on "empty". **What remains open:** F2 is
    "partial by construction" — the router prompt offers the model only
    "normalise to a ticker" or "return null", so the common case (a company the
    model knows is out of scope) still produces an empty list with no unresolved
    name. Closing that fully is a **prompt change**, which is STOP-AND-ASK, and
    the router comment warns against the shape that has lost three times.
20. **The fix is F7's own proposal: split `"unknown"` into two values** —
    `"narrative"` (correctly unscoped: risk, MD&A, transcripts) and
    `"undetermined"` (classification failed). Then the retrieval OR admits
    `requested OR "narrative"`, and `"undetermined"` chunks are excluded from a
    typed query — which is what a scoped question should get. **Files touched:**
    `ingestion/chunker.py::_build_metadata` (emit the new value);
    `ingestion/models.py` (`FinancialType` or a new constant set);
    `engines/retriever.py::_build_filter` (the OR's second arm);
    `frontend/app/page.tsx::buildCitationItems`, which currently hides the tag
    when it equals `"unknown"` and would need to hide `"narrative"` too; **and a
    full re-ingest**, because the value lives in the Qdrant payload — plus the
    corresponding Qdrant purge, since chunk IDs are unchanged but payloads are
    not (`upsert` would overwrite in place here, so no orphans — a rare case where
    a metadata change is *cheaper* than a chunker change, Day 24). No Postgres
    change: `financials.financial_type` is a different field with a `CHECK`
    constraint and is not affected.

---

## 14. MUST REMEMBER

```text
- Two rankings CANNOT be merged by score. RRF fuses RANKS
- RRF = Σ 1/(rank + k), k = 60. Agreement beats a strong single showing
- rrf_score ≈ 0.016 at rank 1 — a THIRD incompatible scale
- THE FILTER GOES INSIDE EACH PREFETCH LEG. Never at fusion
- tenant_id and is_latest are ALWAYS applied
- company uses MatchAny (F14); no re-index because it was already KEYWORD
- F2: an EMPTY company list DROPS the condition — logged, not refused
- Dropped is worse than too strict: it answers PLAUSIBLY instead of failing
- F7: financial_type ORs with "unknown", which dominates → nearly inert
- Fusion is SERVER-SIDE. No manual score merging
```

## 15. MUST UNDERSTAND

```text
- Why rank is comparable across methods when score is not
- Why filter placement is a CORRECTNESS and SECURITY decision, not performance:
  recall collapse, ranking pollution, and data leaving the store
- Why the reranker cannot rescue a dropped company filter — the wrong company's
  chunk is genuinely on-topic
- Why _build_filter detects and reports rather than refusing, and where the
  refusal correctly lives instead
- Why a guard was hardened BEFORE the change that would make its branch more
  reachable
- Why DISABLE_LOCAL_RERANKER caused total semantic failure with no error
```

---

## 16. This connects to

```text
Day 26 — BM25
   ↓
Day 27 — fusing two rankings, and where the filter goes    ← you are here
   ↓
Day 28 — reranking, and TWO MORE incompatible score scales
```

Forward references:

- `rerank()` and the Cohere/ONNX scales → **Day 28**
- The `-inf` sentinel's loud error → **Day 29**
- F2's refusal, one layer up → **Day 36**
- F7's cause in `_build_metadata` → **Day 24** (already read)
- Qdrant failing open vs RLS failing closed → **Day 42**
