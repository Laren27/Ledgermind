# Day 26 — Sparse Retrieval: BM25, TF, IDF

**Phase 7 · Weight: M (~90 min) · Prerequisites: Day 25**

**Textbook: 5.3 "Sparse Retrieval: BM25" — CONFIRMS.**

---

## 1. Today's goal

By tonight you can:

- Explain BM25's three components — term frequency, inverse document frequency,
  length normalisation — **without writing the formula**.
- Explain what a **sparse vector** is and why it is stored differently from a
  dense one.
- Explain exactly where BM25 wins and where it loses, and why that is the
  complement of Day 25.
- Explain why BM25 here runs through `fastembed` and lives **in Qdrant**, not in
  Elasticsearch.

---

## 2. Why now

Day 25 ended on a failure: dense retrieval cannot distinguish `PPBL` from
generic text, or `FY26` from `FY25`. Those are exactly the tokens that identify
*which filing you mean*. Today is the signal that handles them. Tomorrow fuses
the two.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Where dense retrieval fails | Day 25 | The gap this fills |
| `resolved_query`'s entity prefix | Day 25 | Written for **this** leg |
| Named vectors: `"dense"` and `"sparse"` | Day 21 | Both live on one point |

---

## 4. Concept lesson

### 4.1 What came before embeddings

BM25 ("Best Match 25") is not a fallback or a legacy option. It is the algorithm
that ran search for two decades — Lucene, Elasticsearch, Solr — and it is still
the strongest single signal for exact-term matching.

The textbook's framing (2.1) is worth keeping:

> this approach, BM25, is covered in Part 5, and **is still used today as a
> complement to embeddings, not a complete replacement.**

**LedgerMind uses both, deliberately.** Not "embeddings with a legacy fallback" —
two signals, each strongest where the other is weakest.

---

### 4.2 The three ideas, without the formula

The textbook (5.3) gives the formula and then says the useful thing:

> **Conceptual breakdown (the formula's intent, not memorization).**

**1. Term frequency (TF) — repetition is evidence.** A document mentioning
"impairment" eight times is more likely about impairment than one mentioning it
once.

**With diminishing returns.** The eighth mention adds less than the second — the
`k1` parameter controls the curve. Without saturation, a page repeating a word
fifty times would dominate everything.

**2. Inverse document frequency (IDF) — rarity is informative.** A term appearing
in nearly every document ("the", "company", "financial") carries almost no
signal. A term in three documents out of two thousand is a strong discriminator.

**This is the idea that does the work.** In this corpus:

| Term | Document frequency | IDF weight |
|---|---|---|
| "the", "and" | every document | ≈ 0 |
| "revenue", "consolidated" | most filings | low |
| `PPBL` | Paytm only | **high** |
| `Hyperpure` | Eternal only | **high** |
| `FY26` | one period's filings | **high** |

**The high-IDF terms are precisely the ones dense retrieval cannot represent.**
That is not a coincidence — rare, specific tokens are rare *because* they are
specific, and an embedding is a summary of meaning that specificity gets averaged
out of.

**3. Length normalisation — long documents do not win by being long.** A
50-page document contains more of every word. Without correction, length alone
would rank it above a precise one-paragraph match. BM25 divides by document
length relative to the collection average (the `b` parameter).

**Mental model.** BM25 asks: *"Does this document use this word a lot (TF),
is this word unusual (IDF), and is the document not just long (normalisation)?"*

---

### 4.3 Sparse vectors

A **dense** vector: 384 floats, nearly all non-zero, meaning distributed across
every dimension.

A **sparse** vector: one dimension **per vocabulary term** — tens of thousands —
of which a chunk uses maybe fifty. Storing 30,000 floats that are almost all zero
would be absurd, so only the non-zeros are stored:

```python
SparseVector(
    indices=[142, 8891, 20114, ...],   # which vocabulary terms
    values=[0.31, 0.88, 0.42, ...],    # their BM25 weights
)
```

| | Dense | Sparse |
|---|---|---|
| Dimensions | 384 | vocabulary-sized |
| Non-zero | ~all | ~50 per chunk |
| Storage | 384 floats | (index, value) pairs |
| A dimension means | nothing interpretable | **one specific term** |
| Comparison | cosine over all dims | dot product over shared indices |

**The interpretability difference is real.** You cannot say what dimension 42 of a
dense vector encodes. You *can* say index 8891 is the token `"ppbl"`.

**In `models.py`, `EmbeddedChunk` stores them separately:**

```python
@dataclass
class EmbeddedChunk:
    chunk: Chunk
    dense_vector: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]
```

Two parallel lists rather than a dict or a `SparseVector` object — because this
dataclass is serialised (`asdict`) and reassembled by `qdrant_writer._build_point`
into the client's `SparseVector` at the boundary. **The transport type stays out
of the domain type.**

---

### 4.4 BM25 through fastembed, and where it lives

```python
SPARSE_MODEL_NAME = "Qdrant/bm25"

def _encode_sparse(query: str) -> SparseVector:
    """Encode query with BM25 fastembed → SparseVector for Qdrant."""
    model = _get_sparse_model()
    sparse_result = next(model.query_embed(query))
    return SparseVector(
        indices=sparse_result.indices.tolist(),
        values=sparse_result.values.tolist(),
    )
```

**"Qdrant/bm25" is not a neural model.** It is a tokeniser plus BM25 weighting,
distributed through fastembed's model interface so it can be called with the same
API as the dense encoder.

**Why that consistency matters:** `embedder.py` calls `_embed_dense` and
`_embed_sparse` side by side, `retriever.py` calls `_encode_dense` and
`_encode_sparse` side by side, and both are lazy singletons (Day 12). One idiom
for two very different algorithms.

**Where the index lives.** In **Qdrant**, as a named sparse vector on the same
point as the dense one (Day 21). Not in Elasticsearch, not in Postgres
full-text search.

**That single decision is what makes Day 27 possible.** With BM25 in a separate
system you would have: two queries, two result sets, two filter implementations,
and a manual merge — plus a second store to keep in sync with the corpus.

---

### 4.5 Where BM25 wins and loses

Textbook 5.3:

> **Where BM25 wins:** exact term matches — codes, names, technical jargon,
> anything where the literal characters matter rather than the general meaning.
>
> **Where BM25 fails:** synonyms and paraphrases. A query for "money back" scores
> **zero** against a document containing only the word "refund", because there is
> no character-level overlap, despite identical meaning.

**Zero, not "low".** If the term is absent, the document contributes nothing. That
is a hard failure, and it is the exact inverse of dense retrieval's soft one.

| Query | Dense | BM25 |
|---|---|---|
| "risks around quick commerce" | **good** | poor — few exact terms |
| `PPBL` | poor | **excellent** |
| `FY26` vs `FY25` | cannot distinguish | **exact** |
| "money the company earned" vs "revenue" | **good** | **zero** |
| "impairment of loans and investments in associates" | good | **excellent** |

**Read the last row.** A long, specific financial phrase is good for *both* — many
rare terms *and* coherent meaning. Those are the easy queries. The interesting
ones are rows 2–4, where exactly one signal works.

**Mental model.** Dense retrieval is **a librarian who understands your topic**.
BM25 is **the index at the back of the book**. Ask about "quick commerce risk" and
the librarian is better. Ask for `PPBL` and the index is better. You want both,
and Day 27 is how you get both in one answer.

---

## 5. The actual LedgerMind files

```
Query side:   backend/app/engines/retriever.py
              SPARSE_MODEL_NAME = "Qdrant/bm25"
              SPARSE_VECTOR_NAME = "sparse"
              _get_sparse_model()   lazy singleton
              _encode_sparse(query) -> SparseVector

Ingest side:  backend/app/ingestion/embedder.py
              SPARSE_MODEL_NAME = "Qdrant/bm25"        ← the SAME string, again
              _embed_sparse(texts) -> list[(indices, values)]

Storage:      backend/app/ingestion/qdrant_writer.py
              SPARSE_VECTOR_NAME = "sparse"
              _build_point() assembles SparseVector at the boundary
```

**The same duplication as Day 20.** `SPARSE_MODEL_NAME` appears in two files with
no shared source and no test asserting agreement — and here the consequence is
sharper than for the dense model: **a different tokeniser produces different
vocabulary indices**, so index 8891 would mean two different terms on the two
sides. Comparisons would be numerically valid and semantically meaningless, with
no error.

---

## 6. Deep walkthrough

### 6.1 `_encode_sparse`, state by state

**STATE BEFORE.** A string: `"ETERNAL FY26 Q4 consolidated What were the revenue
drivers?"` — Day 25's resolved query, **built for exactly this leg**.

**`_get_sparse_model()`** — lazy singleton. Much cheaper than the dense model
(tokeniser + weights, not a neural network), so its cold cost is seconds rather
than tens of seconds.

**`next(model.query_embed(query))`** — same generator idiom as the dense side.

**`.indices.tolist()` / `.values.tolist()`** — numpy → Python lists, for JSON
(Day 25's `.tolist()` for the same reason).

**STATE AFTER.** A `SparseVector` with perhaps 8–12 non-zero entries for a short
query. **The entity prefix contributes `ETERNAL`, `FY26`, `Q4`, `consolidated` as
four high-IDF terms** — which is the whole reason the prefix exists.

**What the prefix does here, concretely.** Without it, the query terms are
"revenue", "drivers", "were", "what" — mostly low-IDF. With it, four rare,
discriminating terms enter the sparse vector and pull the correct company and
period to the top of the sparse leg.

---

### 6.2 The ingest side, and the empty-vector case

```python
def _embed_sparse(texts: list[str]) -> list[tuple[list[int], list[float]]]:
    ...
```

Batch, order-preserving, same shape as `_embed_dense`.

And in `embed_chunks` (Day 20):

```python
if not sparse_idx:
    logger.warning(
        "Empty sparse vector for chunk %s (text: '%s...') — storing with zero sparse",
        chunk.chunk_id, chunk.text[:50],
    )
    # Don't skip — zero sparse vector is valid (very short text)
```

**An empty sparse vector is legal.** A chunk of "Total 54,364" may produce no
weighted terms after stopword removal. **The chunk is kept**, because its dense
vector still works and the chunk is still retrievable.

**Contrast with the dense side, which drops a wrong-dimension vector.** Same
function, two policies — because one condition is legitimate and the other is
impossible. Day 20 §6 covered the asymmetry; here is the sparse half of it.

**And note the downstream consequence:** a chunk with an empty sparse vector can
**never** be retrieved by the sparse leg. It is dense-only. Nothing tracks how
many such chunks exist.

---

### 6.3 The sparse leg in `hybrid_search`

```python
Prefetch(
    query=sparse_vector,
    using=SPARSE_VECTOR_NAME,     # "sparse"
    limit=top_k,                  # 20
    filter=search_filter,         # the SAME filter as the dense leg
)
```

**Structurally identical to the dense leg.** Same limit, same filter, different
`using=` and a different query type. That symmetry is what makes Day 27's fusion
clean: two comparable rankings, produced the same way.

**And it is why the filter must be inside the leg.** If the sparse leg returned
20 unfiltered candidates and the dense leg 20 filtered ones, fusion would be
comparing rankings drawn from different populations. Day 27.

---

### 6.4 What `Qdrant/bm25` does to your text

Roughly:

1. **Lowercase** — `ETERNAL` → `eternal`.
2. **Tokenise** on non-alphanumerics — `FY26` may become `fy26` or `fy` + `26`.
3. **Remove stopwords** — "the", "were", "what".
4. **Stem** — "drivers" → "driver", "reported" → "report".
5. **Weight** each surviving term by TF × IDF with length normalisation.
6. **Map** each term to a vocabulary index.

**Step 2 is worth checking rather than assuming.** Whether `FY26` survives as one
token or splits into `fy` and `26` changes how well the period filter's *term*
signal works — and Experiment 3 below is how you find out for this tokeniser
rather than in general.

**Step 4 has a real consequence in this domain:** stemming maps "impairment" and
"impaired" together, which is usually right — and it also maps "operating" and
"operate", which occasionally is not.

---

## 7. Data flow

```
INGEST                                    QUERY
Chunk.text                                resolved_query
"Impairment of loans and                  "ETERNAL FY26 Q4 consolidated
 investments in associates                 What were the revenue drivers?"
 amounted to INR 207 crore"                       │
        │                                         │
        ▼ _embed_sparse (batch)                   ▼ _encode_sparse
   tokenise · stopwords · stem                tokenise · stopwords · stem
        ▼                                         ▼
   TF × IDF, length-normalised              same weighting
        ▼                                         ▼
 indices=[142, 8891, 20114, ...]          SparseVector(indices=[...],
 values =[0.31, 0.88, 0.42,  ...]                       values =[...])
        │                                         │
        ├─ empty? WARN, keep (dense-only)         │
        ▼                                         ▼
 EmbeddedChunk.sparse_indices/values        Prefetch(using="sparse",
        │                                            limit=20, filter=f)
        ▼ _build_point                               │
 PointStruct(vector={"dense": [...],                 ▼
                     "sparse": SparseVector})   dot product over SHARED indices
        │                                            │
        ▼                                            ▼
   Qdrant, one point, two vectors            20 ranked candidates
                                                     │
                                                     ▼
                                            RRF fusion with the dense leg
                                                            (Day 27)
```

---

## 8. Engineering decision — BM25 inside Qdrant

**Problem.** Exact-term matching for tickers, acronyms and fiscal-year labels,
combined with semantic matching, in one query.

**Decision.** `Qdrant/bm25` via fastembed, stored as a named sparse vector on the
same point as the dense vector.

`ENGINEERING_DECISIONS.md` **ED-002**.

| Alternative | Why not |
|---|---|
| **Elasticsearch / OpenSearch** | The canonical BM25 home. A second service, a second index to keep in sync, a second filter implementation, and RAM the 512 MB tier does not have |
| **Postgres full-text search** (`tsvector`) | Already running, no new service. **But** the chunk text is in Qdrant, not Postgres — you would duplicate the corpus a third time, and you would still have two result sets to merge by hand |
| **Dense only** | Fails on `PPBL`, `FY26` vs `FY25` — the tokens that identify which filing you mean |
| **A separate Qdrant collection for sparse** | Two queries, two result sets, payload duplicated or joined by id |
| **SPLADE / learned sparse** | Better than BM25 in benchmarks; a neural model to run, which is memory this system does not have |

**Trade-offs accepted.**

- **No BM25 parameter control.** `k1` and `b` are fastembed's defaults. Tuning
  them is not exposed, and nothing here has needed it.
- **`SPARSE_MODEL_NAME` is duplicated** in two files — and a tokeniser mismatch
  would silently misalign vocabulary indices.
- **Empty sparse vectors are stored**, making some chunks dense-only, and nothing
  counts them.
- **Stemming is not domain-aware.** Financial vocabulary is stemmed by a general
  English stemmer.

**Current validity.** Correct for the constraint. The duplication is worth
recording; the rest is proportionate.

**At 10×.** BM25's IDF gets *better* with more documents — rarity is measured
against the collection. The pressure point is the vocabulary index and the sparse
storage, both of which Qdrant handles.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| A paraphrase finds nothing on the sparse leg | **By design** — zero overlap means zero score. Dense covers it |
| An exact acronym still not found | Check whether the chunk's sparse vector was empty at ingest |
| Sparse results look unrelated | Tokeniser mismatch between ingest and query |
| The period term does not help | `FY26` may be splitting into `fy` + `26` — Experiment 3 |
| A chunk never appears in sparse results | Empty sparse vector at ingest. Nothing tracks this |
| Sparse and dense disagree wildly | **Normal.** That is what fusion is for (Day 27) |

---

## 10. Hands-on experiment

### Experiment 1 — look at a sparse vector

```bash
docker compose exec -T backend python -c "
from app.engines.retriever import _encode_sparse, SPARSE_MODEL_NAME
print('model:', SPARSE_MODEL_NAME)
for q in ['PPBL', 'What were the revenue drivers?',
          'ETERNAL FY26 Q4 consolidated What were the revenue drivers?']:
    sv = _encode_sparse(q)
    print(f'\n{q!r}')
    print(f'  non-zero terms: {len(sv.indices)}')
    pairs = sorted(zip(sv.indices, sv.values), key=lambda p: -p[1])[:8]
    for i, v in pairs: print(f'    index {i:7d}  weight {v:.4f}')
"
```

Compare the second and third: **the prefix adds terms**, and their weights are
high because they are rare.

### Experiment 2 — dense vs sparse, head to head

```bash
docker compose exec -T backend python -c "
import os
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.engines.retriever import (_encode_dense, _encode_sparse,
                                   _get_qdrant_client, COLLECTION_NAME)
c = _get_qdrant_client()
f = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=os.getenv('T','')))])

def top(q, using):
    v = _encode_dense(q) if using=='dense' else _encode_sparse(q)
    r = c.query_points(collection_name=COLLECTION_NAME, query=v, using=using,
                       limit=3, query_filter=f, with_payload=True)
    return [(round(p.score,4), p.payload.get('company'), p.payload.get('page_number'),
             (p.payload.get('text') or '')[:48]) for p in r.points]

for q in ['PPBL', 'money the company earned from operations',
          'impairment of loans and investments in associates']:
    print(f'\n=== {q!r}')
    for using in ('dense','sparse'):
        print(f'  {using}:')
        for s,co,pg,t in top(q, using): print(f'    {s:8} {co:8} p{pg:<3} {t!r}')
"
```

**Read all three.** Query 1: sparse wins. Query 2: dense wins. Query 3: both do
well. **That is the argument for hybrid, in one command.**

### Experiment 3 — what the tokeniser actually does

```bash
docker compose exec -T backend python -c "
from app.engines.retriever import _encode_sparse
tests = ['FY26', 'FY25', 'fy26', 'PPBL', 'Q4', 'revenue', 'revenues', 'the and of']
for t in tests:
    sv = _encode_sparse(t)
    print(f'  {t!r:12} -> {len(sv.indices)} terms  indices={list(sv.indices)[:4]}')
print()
print('Compare FY26 vs FY25 (different indices?) and revenue vs revenues')
print('(same index => stemming). Stopwords should produce few or no terms.')
"
```

### Experiment 4 — IDF, demonstrated on the real corpus

```bash
docker compose exec -T backend python -c "
import os
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.engines.retriever import _encode_sparse, _get_qdrant_client, COLLECTION_NAME
c = _get_qdrant_client()
f = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=os.getenv('T','')))])
for term in ['company', 'revenue', 'consolidated', 'PPBL', 'Hyperpure']:
    r = c.query_points(collection_name=COLLECTION_NAME, query=_encode_sparse(term),
                       using='sparse', limit=1, query_filter=f, with_payload=True)
    top = r.points[0].score if r.points else 0
    print(f'  {term:14} top sparse score {top:8.4f}')
print()
print('Rare terms score far higher. That IS inverse document frequency.')
"
```

### Experiment 5 — the failure mode, made explicit

```bash
docker compose exec -T backend python -c "
import os
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.engines.retriever import _encode_sparse, _get_qdrant_client, COLLECTION_NAME
c = _get_qdrant_client()
f = Filter(must=[FieldCondition(key='tenant_id', match=MatchValue(value=os.getenv('T','')))])
for q in ['revenue from operations',
          'money the company earned from selling things']:
    r = c.query_points(collection_name=COLLECTION_NAME, query=_encode_sparse(q),
                       using='sparse', limit=3, query_filter=f, with_payload=True)
    print(f'{q!r}')
    for p in r.points: print(f'   {p.score:8.4f} p{p.payload.get(\"page_number\")}')
    print()
print('Same MEANING, very different sparse scores. Zero overlap, zero signal.')
print('This is the hard failure dense retrieval covers.')
"
```

### Experiment 6 — the duplicated constant

```bash
docker compose exec -T backend python -c "
from app.ingestion.embedder import SPARSE_MODEL_NAME as INGEST
from app.engines.retriever import SPARSE_MODEL_NAME as QUERY
print('ingest:', INGEST); print('query :', QUERY); print('agree :', INGEST == QUERY)
print()
print('A different TOKENISER means index 8891 is a different term on each side.')
print('Numerically valid. Semantically meaningless. No error.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/retriever.py` and
`backend/app/ingestion/embedder.py`:

1. `_encode_sparse` and `_encode_dense` have almost the same shape. Name every
   difference and say why each exists.
2. Why does `EmbeddedChunk` store `sparse_indices` and `sparse_values` as two
   parallel lists rather than a `SparseVector`?
3. An empty sparse vector is kept with a warning; a wrong-dimension dense vector
   is dropped with an error. Justify both.
4. What does a chunk with an empty sparse vector lose, and does anything measure
   how many there are?
5. `SPARSE_MODEL_NAME` appears in two files. Why is a mismatch *worse* here than
   for the dense model?

---

## 12. Self-check questions

**Basic**
1. What do TF, IDF and length normalisation each contribute?
2. What is a sparse vector?
3. Where does BM25 win? Where does it fail?
4. Which model name, and where does the index live?
5. What does the entity prefix contribute to this leg?

**Code**
6. What does `_encode_sparse` return?
7. Why `.tolist()` on indices and values?
8. Which named vector does the sparse `Prefetch` use?
9. What happens to a chunk whose sparse vector is empty?
10. Where is `SparseVector` assembled on the ingest path?

**Why**
11. Why keep BM25 at all when embeddings exist?
12. Why is BM25's failure "zero" rather than "low"?
13. Why is BM25 in Qdrant rather than Elasticsearch?
14. Why is IDF the component that does the most work here?
15. Why does the sparse leg use the same filter and limit as the dense leg?

**Debugging**
16. An acronym query returns nothing useful from either leg. Two hypotheses.
17. Sparse results look random. What do you check?
18. A paraphrased question scores zero on sparse. Bug?

**System design**
19. You want to boost `FY26` matches specifically. Two approaches — one inside
    BM25, one outside. Which fits this codebase, and why?
20. Some chunks are dense-only because their sparse vector was empty. Design a way
    to know how many, and say where it belongs.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **(a)** `_encode_sparse` returns a `SparseVector`, `_encode_dense` a
   `list[float]` — different Qdrant query types. **(b)** Sparse extracts two
   arrays (`indices`, `values`); dense one. **(c)** `_get_sparse_model` loads a
   tokeniser plus weights, `_get_dense_model` a neural network — so their cold
   costs differ by an order of magnitude. **(d)** Both call `query_embed` and both
   are lazy singletons — the *sameness* is deliberate, so two very different
   algorithms present one idiom.
2. Because `EmbeddedChunk` is a **domain type** that gets `asdict()`-ed and passed
   between pipeline stages, while `SparseVector` is the **Qdrant client's
   transport type**. Keeping the client type out of the domain type means
   `models.py` does not import `qdrant_client`, and the conversion happens once,
   at the boundary, in `_build_point`. Same principle as `state.py` importing
   nothing from this project (Day 3).
3. **Empty sparse is legitimate** — a chunk of "Total 54,364" may produce no
   weighted terms after stopwords, and its dense vector still makes it
   retrievable. **A wrong-dimension dense vector is impossible** — it means a
   different model produced it, and storing it would corrupt the index. One
   condition is a valid state; the other is a defect.
4. It loses the ability to be retrieved by the **sparse leg** at all — it is
   dense-only, so it can never surface for an exact-term query. **Nothing
   measures it.** The warning is logged per chunk at ingest and never aggregated,
   so the count is unknown unless you grep the ingest log.
5. Because a different **tokeniser** produces a different **vocabulary mapping**.
   With the dense model, a mismatch means two different coordinate systems; with
   sparse, index 8891 would literally denote a different *term* on each side — so
   a dot product would be adding weights for unrelated words. Both fail silently;
   the sparse case is harder to reason about because the numbers still look
   structurally correct.

### §12 — Basic

1. **TF:** repetition is evidence, with diminishing returns. **IDF:** rare terms
   discriminate; ubiquitous terms carry no signal. **Length normalisation:** a long
   document must not win by containing more of everything.
2. A vector with one dimension per vocabulary term, of which only a handful are
   non-zero — stored as parallel `indices` and `values` arrays.
3. **Wins:** exact terms — codes, acronyms, tickers, proper nouns.
   **Fails:** synonyms and paraphrases, where it scores **zero**.
4. `Qdrant/bm25`, via fastembed. The index lives **in Qdrant**, as a named sparse
   vector on the same point as the dense vector.
5. Four high-IDF terms — `ETERNAL`, `FY26`, `Q4`, `consolidated` — which are
   exactly the discriminating tokens the raw question lacks.

### §12 — Code

6. A `SparseVector(indices=[...], values=[...])`.
7. numpy arrays are not JSON-serialisable, and the Qdrant client serialises to
   JSON. Same boundary as `_encode_dense`'s `.tolist()`.
8. `SPARSE_VECTOR_NAME = "sparse"`, via `using="sparse"`.
9. It is **kept**, with a WARNING. It becomes dense-only and can never be
   retrieved by the sparse leg.
10. `qdrant_writer._build_point`, which reassembles `SparseVector(indices=...,
    values=...)` from `EmbeddedChunk`'s two lists.

### §12 — Why

11. Because they fail in opposite places. Embeddings cannot represent exact
    identifiers — acronyms, codes, `FY26` vs `FY25` — and those are precisely the
    tokens that identify *which filing you mean*. The textbook is explicit that
    BM25 is "a complement to embeddings, not a complete replacement".
12. Because BM25 scores **term overlap**. If the term is absent from the document,
    it contributes nothing at all — not a small amount. That is a hard failure, and
    it is why the complementary signal must be a *different kind* of matcher rather
    than a better-tuned one.
13. Because it puts both vectors on **one point** with **one payload**, so hybrid
    search is a single query with server-side fusion (Day 27). Elasticsearch would
    mean a second service, a second index to keep synchronised, a second filter
    implementation, a manual merge — and RAM this tier does not have.
14. Because this corpus's discriminating terms are rare by nature: `PPBL`,
    `Hyperpure`, `FY26`. IDF is what makes them outweigh ubiquitous words like
    "company" and "revenue" — and those rare terms are exactly the ones dense
    retrieval averages away.
15. So that fusion compares two rankings drawn from the **same population**.
    Different filters or limits would mean fusing rankings over different candidate
    sets, and the RRF ranks would not be comparable (Day 27).

### §12 — Debugging

16. **(a)** The acronym's chunk had an **empty sparse vector** at ingest, so it is
    dense-only — and dense cannot represent acronyms, so neither leg finds it.
    **(b)** A **tokeniser mismatch** between ingest and query, so the query's
    vocabulary indices do not correspond to the stored ones. Check the ingest log
    for the "Empty sparse vector" warning on that chunk, and compare
    `SPARSE_MODEL_NAME` on both sides.
17. **`SPARSE_MODEL_NAME` on both sides**, first. Then whether the corpus was
    re-ingested after a model change. Structurally-valid-but-meaningless results
    are the signature of a vocabulary mismatch, and there is no error to look for.
18. **No — that is the design.** Zero term overlap means zero BM25 score. The
    dense leg is what covers paraphrases, and fusion combines them. A paraphrase
    scoring zero on sparse is the system working as intended.

### §12 — System design

19. **Inside BM25:** raise the query-term weight for `FY26` (a boosted term), or
    tune `k1`/`b`. **Outside:** use the **metadata filter** — `fiscal_year` is
    already an indexed payload field (Day 21), and `_build_filter` already applies
    it when the router extracts a year. **The outside approach fits**, and is
    already what the system does: a filter is *deterministic* and *exact*, whereas
    a term boost is a ranking nudge that can still be outweighed. This codebase
    consistently prefers a hard constraint over a soft one — and note that
    fastembed does not expose `k1`/`b` anyway, so the inside approach is not
    available without replacing the model.
20. **Where it belongs:** the ingest summary, not a query-time metric.
    `embed_chunks` already counts `skipped`; add an `empty_sparse` counter beside
    it and return it in the summary that `pipeline.py` logs, so the number appears
    once per ingest with the document it belongs to. **Why not query-time:** a
    dense-only chunk is not an error, so it must not raise or warn per query; it is
    a *corpus property*, and corpus properties belong to the run that created them.
    **Why it matters:** if the count is 2 out of 2,300 it is noise; if it is 400 it
    means the tokeniser or the chunker is producing fragments too short to weight,
    and the sparse leg is quietly covering far less of the corpus than assumed.
    Today that distinction is unknowable — which is the same shape as `CAVEAT-003`
    (Day 22): *the failure rate is unknown by construction.*

---

## 14. MUST REMEMBER

```text
- BM25 = TF (repetition, saturating) × IDF (rarity) ÷ length normalisation
- Sparse vector: one dimension per VOCABULARY TERM, ~50 non-zero per chunk
- Stored as parallel indices[] and values[] — SparseVector is assembled at the
  Qdrant boundary only
- Model: "Qdrant/bm25" via fastembed. The index lives IN QDRANT, named "sparse"
- BM25 wins on exact terms; it scores ZERO on paraphrases, not "low"
- The entity prefix exists FOR THIS LEG — four high-IDF terms
- An empty sparse vector is LEGAL: the chunk is kept and becomes dense-only
- SPARSE_MODEL_NAME is duplicated in two files; a tokeniser mismatch misaligns
  vocabulary indices with no error
```

## 15. MUST UNDERSTAND

```text
- Why the terms that identify WHICH FILING you mean are exactly the ones dense
  retrieval cannot represent — and why that is not a coincidence
- Why "zero, not low" makes BM25's failure hard rather than soft, and why the
  complement must be a different KIND of matcher
- Why putting BM25 in Qdrant rather than Elasticsearch is what makes one-query
  hybrid search possible
- Why the sparse leg must use the SAME filter and limit as the dense leg
- Why "how many chunks are dense-only?" is currently unknowable, and why that
  is the same shape as CAVEAT-003
```

---

## 16. This connects to

```text
Day 25 — dense retrieval, and where it fails
   ↓
Day 26 — the complementary signal                 ← you are here
   ↓
Day 27 — fusing two incomparable rankings, and where the filter goes
```

Forward references:

- `FusionQuery(Fusion.RRF)` and why rank, not score → **Day 27**
- Filter placement inside each prefetch leg → **Day 27**
- Reranking the fused candidates → **Day 28**
- `resolved_query`'s construction → **Day 36**
