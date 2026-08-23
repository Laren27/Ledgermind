# Day 20 — Embeddings, Vectors, Cosine Similarity

**Phase 6 — RAG foundations and ingestion · Weight: M (~90 min) · Prerequisites: Day 17**

**Textbook: Parts 1.4–2.4 — CONFIRMS.** Read them first. LedgerMind implements
this exactly as described; today adds the model this system actually uses and the
mistakes it actually made.

---

## 1. Today's goal

By tonight you can:

- Explain what an embedding is, why the representation exists, and what keyword
  matching could not do.
- Compute cosine similarity by hand and say why it, not Euclidean distance, is
  the default for text.
- Explain why the **same model must embed documents and queries**, and why the
  failure mode raises no error.
- Explain LedgerMind's choice — `bge-small-en-v1.5`, 384 dimensions, ONNX, CPU —
  and the constraint that forced it.

---

## 2. Why now

Day 17 gave you the non-parametric half of RAG in principle. Today it becomes
concrete: the geometry that makes "find the relevant passage" a computable
question. Everything from Day 21 to Day 30 sits on top of it.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Parametric vs non-parametric memory | Day 17 | Embeddings are the non-parametric index |
| Lazy singletons | Day 12 | The model is loaded on first use |
| Tokens | Day 17 | Embedding models have input limits too |

---

## 4. Concept lesson

### 4.1 The problem

A computer comparing `"eligible for a refund"` with `"how do I get my money
back"` sees two character sequences with almost no overlap. Keyword matching
(Day 26's BM25) scores this near zero — **correctly, by its own rules, and
uselessly.**

The textbook (2.1) states it exactly:

> A document saying "eligible for a refund" will not match a search for "get my
> money back" under pure keyword matching, because no words overlap, even though
> the meaning is identical.

**The insight.** Convert *meaning* into *geometry*. Map each text to a point in a
high-dimensional space such that texts with similar meaning land close together.
Then "find the relevant passage" becomes "find the nearest point" — a solved
computational problem.

**Mental model.** An embedding is **a postcode for meaning**. Two texts about the
same thing get neighbouring postcodes. You do not read the postcode; you measure
the distance between them.

---

### 4.2 What an embedding model is

A neural network trained on a **different objective** from a chat model. Not
"predict the next token" but: *make vectors of similar texts close, and vectors
of dissimilar texts far apart.*

LedgerMind uses **`BAAI/bge-small-en-v1.5`**:

| Property | Value | Consequence |
|---|---|---|
| Dimensions | **384** | Each text → 384 floats |
| Runtime | **ONNX via fastembed** | No PyTorch — torch does not fit 512 MB |
| Device | **CPU** | No GPU on the free tier |
| Threads | **1** | `threads=1`, matching the Dockerfile's six `ENV` lines (Day 1) |
| Size | ~130 MB | Loaded lazily, once per process (Day 12) |

**Why 384 and not 1536?** The textbook's examples use OpenAI's 1536-dim model.
Smaller vectors mean less RAM, faster comparison, and a smaller index — and for
this corpus the retrieval quality is adequate. **It is a memory decision**, like
almost every other decision in this system.

---

### 4.3 Cosine similarity

```
cosine_similarity(A, B) = (A · B) / (‖A‖ × ‖B‖)
```

The **angle** between two vectors, ignoring their length.

| Value | Meaning |
|---|---|
| 1.0 | same direction — maximally similar |
| 0.0 | perpendicular — unrelated |
| −1.0 | opposite |

**Why the angle and not the distance.** A short sentence and a long paragraph
about the same topic produce vectors pointing in a similar **direction** but with
different **magnitudes**. Euclidean distance would penalise the length
difference; cosine ignores it.

The textbook's numerical walkthrough (2.4) is worth doing by hand once:

```
A = [1, 2, 1]   "refund policy"
B = [1, 2, 0]   "money back guarantee"
C = [0, 0, 5]   "engineering server logs"

cos(A,B) = 5 / (2.449 × 2.236) ≈ 0.913   HIGH
cos(A,C) = 5 / (2.449 × 5.000) ≈ 0.408   LOW
```

Note that `A·B` and `A·C` are **both 5** — the raw dot product does not
discriminate. Normalisation is what makes the comparison meaningful.

**In LedgerMind:** `embedder.py` says `normalize_embeddings=True: required for
cosine similarity in Qdrant`, and the collection is created with COSINE distance.
When vectors are pre-normalised, the dot product **is** the cosine — so Qdrant
does the cheap operation and gets the right answer.

---

### 4.4 The mistake that raises no error

The textbook (2.2) calls it the common beginner mistake, and it is worth
memorising in its own words:

> Using a different embedding model for documents than for queries. Each
> embedding model defines its own coordinate system — dimension 42 in one
> model's output might encode something entirely different from dimension 42 in
> another model's output. Mixing models produces **numerically valid but
> semantically meaningless** comparisons.

And the failure mode:

> The system appears to work — it returns results, produces answers — but the
> answers are consistently off-topic or generic. **No error is raised.**

**How LedgerMind defends against it.** The model name is a module constant in
**both** places:

```python
# ingestion/embedder.py   — the document side
DENSE_MODEL_NAME  = "BAAI/bge-small-en-v1.5"

# engines/retriever.py    — the query side
DENSE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
```

**Two constants, two files, one value.** That is a copy — the failure class this
project consolidates elsewhere (Day 10). Nothing enforces agreement; nothing
tests it. If someone upgraded one, retrieval would degrade silently.

This is **not** in `CAVEATS.md`. It is a genuine, small, unrecorded coupling, and
noticing it is today's most useful reading exercise.

---

### 4.5 Query/document asymmetry

BGE models are **asymmetric**: they expect a prefix on the query side but not on
the document side.

`embedder.py` documents the decision:

> No prefix on document side (BGE asymmetric retrieval convention). Query side
> adds prefix in Phase 4: `"Represent this sentence for searching relevant
> passages: "`

**And here is the thing to check rather than assume.** `retriever._encode_dense`
reads:

```python
def _encode_dense(query: str) -> List[float]:
    """Encode query with bge-small-en-v1.5 (ONNX) → 384-dim dense vector."""
    model = _get_dense_model()
    vector = next(model.query_embed(query))
    return vector.tolist()
```

No literal prefix string. It calls **`model.query_embed()`** rather than
`model.embed()` — fastembed's query-side entry point, which applies the model's
configured query prefix internally. So the intent is honoured, by the library
rather than by this code. The docstring in `embedder.py` describing a Phase 4
prefix is therefore **describing something fastembed now does**, not something
`retriever.py` does — a small piece of documentation drift of the kind Day 2
taught you to check.

---

## 5. The actual LedgerMind files

```
File:        backend/app/ingestion/embedder.py (289 lines)
Purpose:     Turn Chunk objects into EmbeddedChunk (dense + sparse vectors)
Why:         Embedding must happen once, offline, not per query
Who imports: ingestion/pipeline.py
Entry point: embed_chunks(chunks, batch_size=None) -> list[EmbeddedChunk]
Data in:     list[Chunk]
Data out:    list[EmbeddedChunk] — SAME ORDER as input
Constants:   DENSE_MODEL_NAME, SPARSE_MODEL_NAME, BATCH_SIZE=8,
             DENSE_DIMENSIONS=384
```

The query side lives in `retriever._encode_dense` (Day 25) and uses the same
model name.

---

## 6. Deep walkthrough — `embed_chunks`

```python
def embed_chunks(chunks, batch_size=None) -> list[EmbeddedChunk]:
    if not chunks:
        logger.info("embed_chunks called with empty list — nothing to do")
        return []

    effective_batch = batch_size or BATCH_SIZE
    texts = [c.text for c in chunks]

    try:
        dense_vectors = _embed_dense(texts)
    except Exception as e:
        raise RuntimeError(f"Dense embedding failed: {e}") from e

    try:
        sparse_pairs = _embed_sparse(texts)
    except Exception as e:
        raise RuntimeError(f"Sparse embedding failed: {e}") from e

    embedded, skipped = [], 0
    for chunk, dense_vec, (sparse_idx, sparse_val) in zip(chunks, dense_vectors, sparse_pairs):
        if len(dense_vec) != DENSE_DIMENSIONS:
            logger.error("Dense vector dim mismatch: expected %d, got %d — skipping chunk %s",
                         DENSE_DIMENSIONS, len(dense_vec), chunk.chunk_id)
            skipped += 1
            continue
        if not sparse_idx:
            logger.warning("Empty sparse vector for chunk %s ... — storing with zero sparse", ...)
            # Don't skip — zero sparse vector is valid (very short text)
        ...
```

**STATE BEFORE.** A list of `Chunk` dataclasses with `.text` and `.metadata`.

**`BATCH_SIZE = 8`**, and the constant carries its own history:

```python
BATCH_SIZE = 8  # reduced from 32 — 32 caused OOM/near-freeze on large docs
                # (1999+ chunks) even at 8GB WSL2 cap
```

The textbook (15B, "The Embedding Batch Size Memory Crash") predicts exactly
this. `CLAUDE.md` §3 freezes the value: it encodes a measurement not derivable
from the code.

**Two different failure policies, and the asymmetry is the design:**

| Failure | Response | Why |
|---|---|---|
| Whole dense batch fails | `raise RuntimeError` | Systemic — model load, OOM. Continuing would produce a partial index that looks complete |
| One vector has wrong dimensions | log ERROR, **skip that chunk** | Per-chunk anomaly; the rest of the document is fine |
| Empty sparse vector | log WARNING, **keep the chunk** | Legitimate for very short text — the dense vector still works |

The docstring states the rule: *"Individual chunk failures are logged and skipped
— never raise on partial failure."*

**`len(dense_vec) != DENSE_DIMENSIONS`** is the **one guard that would catch a
model swap** — a different model with a different output size fails loudly here.
A different model with the *same* 384 dimensions would pass silently. That is the
gap in §4.4.

**`zip(chunks, dense_vectors, sparse_pairs)`** — positional correspondence, and
the docstring promises "same order as input chunks". Nothing enforces it beyond
`_embed_dense` and `_embed_sparse` preserving order, which they do because
fastembed's `embed()` is order-preserving. **A silent contract**, and the kind
worth knowing about.

**STATE AFTER.** `list[EmbeddedChunk]`, each wrapping its `Chunk` plus a 384-float
dense vector and a sparse `(indices, values)` pair.

---

## 7. Data flow

```
Chunk(text="Revenue from operations grew 68% to INR 54,364 crore...",
      metadata=ChunkMetadata(company="ETERNAL", fiscal_year="FY26", ...))
        │
        ▼
_embed_dense([text, ...])        bge-small-en-v1.5, ONNX, CPU, threads=1
        │                        batched at 8
        ▼
[0.021, -0.113, 0.087, ...]      384 floats, NORMALISED (‖v‖ = 1)
        │
        ├─ len(v) == 384?  no → log ERROR, skip this chunk
        │
        ▼
_embed_sparse([text, ...])       Qdrant/bm25                     (Day 26)
        │
        ▼
(indices=[142, 8891, ...], values=[0.31, 0.88, ...])
        │
        ├─ empty?  → log WARNING, KEEP (dense still works)
        │
        ▼
EmbeddedChunk(chunk=<Chunk>, dense_vector=[...],
              sparse_indices=[...], sparse_values=[...])
        │
        ▼
qdrant_writer.write_chunks()      ONE point, TWO named vectors   (Day 21)
```

**At query time, the mirror image:**

```
"What were Eternal's revenue drivers?"
        │
        ▼
retriever._encode_dense()  →  model.query_embed()  →  384 floats
        │                        ↑
        │            SAME MODEL as ingestion. Different entry point.
        ▼
Qdrant: cosine similarity against every stored dense vector
```

---

## 8. Engineering decision — a small CPU model, run locally

**Problem.** Embed ~2,300 chunks at ingest and one query per request, inside
512 MB, at ₹0.

**Decision.** `bge-small-en-v1.5`, 384-dim, ONNX via fastembed, CPU, `threads=1`,
`BATCH_SIZE=8`.

| Alternative | Why not |
|---|---|
| **OpenAI `text-embedding-3-small`** (the textbook's default) | A paid API call per chunk *and per query* — a network hop on the request path, and a bill |
| **A larger local model** (`bge-large`, 1024-dim) | Better quality; does not fit the memory budget alongside the reranker and the API process |
| **PyTorch instead of ONNX** | torch alone exceeds the 512 MB tier |
| **Cohere Embed** (they already use Cohere for reranking) | Would be consistent, and adds a second per-query network call. Reranking is worth a hop because it is 5–20 documents; embedding is on the critical path for every query |

**Trade-offs accepted.**

- **384 dimensions is less expressive** than 1536. Mitigated by hybrid retrieval
  (Day 27) — BM25 catches what dense misses.
- **CPU-only** means ~30 s cold load and a few hundred ms per batch. Lazy loading
  keeps it off startup (Day 12).
- **The model name is duplicated** in `embedder.py` and `retriever.py` with no
  enforcement.

**Current validity.** Sound for this corpus and budget. The duplication is the
one thing worth fixing.

**At 10×.** Embedding stays offline and scales with ingest volume, not query
volume — so it is not the bottleneck. Query-side embedding is ~10 ms warm and
also not the bottleneck. **The scaling pressure is elsewhere** (memory, Day 45).

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| Answers consistently off-topic, **no error** | Different models for ingest and query. The textbook's canonical silent failure |
| `Dense embedding failed` | Model load failure or OOM — systemic, raises |
| `Dense vector dim mismatch` | A model with different output size — the one guard that catches a swap |
| `Empty sparse vector` warning | Very short chunk. Legitimate; the chunk is kept |
| OOM during ingest | `BATCH_SIZE` raised above 8 |
| First query 30 s | Cold model load (Day 12) |
| Retrieval degraded after a "model upgrade" | Stored vectors were built with the old model. **Re-embed the entire corpus** |

---

## 10. Hands-on experiment

### Experiment 1 — embed something, and look at it

```bash
docker compose exec -T backend python -c "
from app.ingestion.embedder import _embed_dense, DENSE_MODEL_NAME, DENSE_DIMENSIONS
print('model:', DENSE_MODEL_NAME, '| dims:', DENSE_DIMENSIONS)
v = _embed_dense(['Revenue from operations grew to INR 54,364 crore'])[0]
print('length:', len(v))
print('first 8:', [round(x,4) for x in v[:8]])
import math
print('norm  :', round(math.sqrt(sum(x*x for x in v)), 6), '<- normalised to 1.0')
"
```

### Experiment 2 — cosine similarity, by hand and by model

```bash
docker compose exec -T backend python -c "
import math
def cos(a,b):
    d = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
    return d/(na*nb)

A,B,C = [1,2,1],[1,2,0],[0,0,5]
print('textbook 2.4:')
print('  dot(A,B) =', sum(x*y for x,y in zip(A,B)), ' dot(A,C) =', sum(x*y for x,y in zip(A,C)))
print('  cos(A,B) =', round(cos(A,B),3), ' cos(A,C) =', round(cos(A,C),3))
print('  Both dot products are 5. NORMALISATION is what discriminates.')
print()
from app.ingestion.embedder import _embed_dense
texts = ['revenue from operations',
         'top line and turnover',
         'money the company earned from selling things',
         'the auditor issued an unmodified opinion',
         'warehouse capacity in square feet']
vs = _embed_dense(texts)
print('real embeddings, all vs [0]:')
for t,v in zip(texts, vs):
    print(f'  {cos(vs[0],v):.4f}  {t}')
"
```

Note that *"money the company earned from selling things"* scores well despite
**sharing no words** with "revenue from operations". That is the whole point.

### Experiment 3 — the failure BM25 cannot fix, and vice versa

```bash
docker compose exec -T backend python -c "
import math
from app.ingestion.embedder import _embed_dense
def cos(a,b): return sum(x*y for x,y in zip(a,b))   # normalised → dot == cosine

pairs = [
  ('eligible for a refund',      'how do I get my money back'),
  ('PPBL',                        'Paytm Payments Bank Limited'),
  ('INV-2024-XR7',                'INV-2024-XR8'),
  ('revenue declined sharply',    'revenue grew strongly'),
]
for a,b in pairs:
    va,vb = _embed_dense([a,b])
    print(f'  {cos(va,vb):.4f}  {a!r} vs {b!r}')
print()
print('Row 3: two DIFFERENT invoice codes score high — dense retrieval cannot')
print('       distinguish exact identifiers. That is Day 26.')
print('Row 4: opposite meanings score high — same topic, opposite direction.')
print('       That is why contradiction detection is NOT done by embeddings.')
"
```

**Row 4 is the important one.** It is why `contradiction.py` uses regex and
arithmetic, not similarity (Day 37).

### Experiment 4 — the model-mismatch failure, safely

```bash
docker compose exec -T backend python -c "
from app.ingestion.embedder import DENSE_MODEL_NAME as INGEST
from app.engines.retriever import DENSE_MODEL_NAME as QUERY
print('ingestion side:', INGEST)
print('query side    :', QUERY)
print('agree?        :', INGEST == QUERY)
print()
print('TWO constants in TWO files with no shared source and no test.')
print('If one were upgraded, retrieval would degrade with NO ERROR.')
"
```

### Experiment 5 — batch size and memory

```bash
docker compose exec -T backend python -c "
import time
from app.ingestion.embedder import _embed_dense, BATCH_SIZE
print('BATCH_SIZE =', BATCH_SIZE, '(reduced from 32 after OOM at 1999+ chunks)')
texts = ['Revenue from operations for the period grew substantially.'] * 40
t = time.perf_counter(); _embed_dense(texts)
print(f'40 chunks: {time.perf_counter()-t:.2f}s (warm)')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/ingestion/embedder.py`:

1. `BATCH_SIZE = 8`. Find the comment. What was it before, what happened, and
   why must you not raise it?
2. `embed_chunks` **raises** on a whole-batch failure and **skips** on a
   per-chunk one. State the principle behind the asymmetry.
3. An empty sparse vector logs a warning and keeps the chunk; a wrong-dimension
   dense vector logs an error and drops it. Why the different treatment?
4. Find `DENSE_MODEL_NAME` in this file, then find it again in
   `engines/retriever.py`. What coupling does that create, and what would the
   failure look like?
5. `embedder.py`'s docstring says the query side "adds prefix in Phase 4". Open
   `retriever._encode_dense` and check. What do you actually find?

---

## 12. Self-check questions

**Basic**
1. What is an embedding?
2. How many dimensions here, and which model?
3. What does cosine similarity measure?
4. Why cosine rather than Euclidean distance for text?
5. What is `BATCH_SIZE` and why?

**Code**
6. What does `embed_chunks` return, and in what order?
7. What is `DENSE_DIMENSIONS` used for?
8. What happens to a chunk with an empty sparse vector?
9. Which fastembed method does the query side call, and why that one?
10. Where is `normalize_embeddings=True` relevant?

**Why**
11. Why must the same model embed documents and queries?
12. Why does that failure raise no error?
13. Why 384 dimensions rather than 1536?
14. Why is embedding done offline rather than per query?
15. Why does a whole-batch failure raise while a single chunk is skipped?

**Debugging**
16. Answers are consistently generic and off-topic. No errors. What do you check?
17. Ingest OOMs on a large document. What changed?
18. Retrieval degrades right after an embedding-model upgrade. What was missed?

**System design**
19. You want to upgrade to a 768-dim model. List everything that must change.
20. The model name is duplicated in two files. Propose a fix, and say why it has
    not been done.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. It was **32**. That caused OOM / near-freeze on large documents (1999+ chunks)
   **even at an 8 GB WSL2 cap**. You must not raise it because the value encodes
   a measurement not derivable from the code — `CLAUDE.md` §3 lists it among the
   frozen constants, alongside `OVERLAP_TOKENS` and the Cohere thresholds.
2. **Systemic versus local.** A whole-batch failure means the model could not
   load or the process is out of memory — continuing would produce a **partial
   index that looks complete**, which is worse than failing. A per-chunk anomaly
   affects one chunk; dropping it and continuing preserves the rest of the
   document. The docstring states it: *"Individual chunk failures are logged and
   skipped — never raise on partial failure."*
3. Because an empty sparse vector is **legitimate** — very short text may produce
   no BM25 terms, and the dense vector still works, so the chunk is retrievable.
   A wrong-dimension dense vector is **never legitimate**: it means a different
   model produced it, and storing it would corrupt the index (Qdrant would reject
   it, or worse, accept it into a mismatched collection).
4. Two module constants in two files with **no shared source and no test**
   asserting agreement. If one were upgraded, documents and queries would live in
   different coordinate systems: comparisons remain numerically valid and become
   semantically meaningless. **The failure raises no error** — answers just become
   generic. The one partial guard is `DENSE_DIMENSIONS`, which catches a model
   with a *different* output size but not one with the same 384.
5. **No literal prefix string.** `_encode_dense` calls `model.query_embed(query)`
   rather than `model.embed(...)` — fastembed's query-side entry point, which
   applies the model's configured query prefix internally. So the intent is
   honoured by the library, and `embedder.py`'s docstring describes a "Phase 4"
   implementation that does not exist as written. Documentation drift; the
   behaviour is correct.

### §12 — Basic

1. A fixed-length list of floats representing the *meaning* of a text, positioned
   so that similar meanings are geometrically close.
2. **384**, `BAAI/bge-small-en-v1.5`.
3. The **angle** between two vectors — direction, ignoring magnitude.
4. Because a short sentence and a long paragraph on the same topic point in
   similar directions but have different magnitudes. Cosine isolates meaning
   (direction) from length (magnitude).
5. **8**. Reduced from 32 after OOM on large documents.
6. `list[EmbeddedChunk]`, in the **same order** as the input chunks — a contract
   maintained by `zip` and by fastembed preserving order.
7. Validating each returned vector: `len(dense_vec) != DENSE_DIMENSIONS` logs an
   error and skips the chunk. It is the only automatic guard against a model swap.
8. It is **kept**, with a WARNING. Legitimate for very short text, and the dense
   vector still makes it retrievable.
9. `model.query_embed()` — the query-side entry point, which applies BGE's
   asymmetric query prefix. Documents use `model.embed()` with no prefix.
10. Qdrant's collection uses COSINE distance. Pre-normalised vectors mean the dot
    product *is* the cosine, so the cheap operation gives the right answer.

### §12 — Why

11. Because each model defines its **own coordinate system**. Dimension 42 in one
    model encodes something unrelated to dimension 42 in another, so cross-model
    comparisons are numerically valid and semantically meaningless.
12. Because nothing is malformed. The vectors have the right type and (possibly)
    the right length; the arithmetic succeeds; results are returned and ranked.
    Only the *relevance* is destroyed, and relevance has no exception.
13. Memory. 384-dim vectors are smaller in RAM and in the index, and faster to
    compare — and the quality is adequate for this corpus, especially with BM25
    covering what dense retrieval misses.
14. Because embedding ~2,300 chunks takes minutes and hundreds of MB. Doing it per
    query would put that on the request path. Offline embedding is the entire
    reason a vector index exists.
15. See §11 Q2.

### §12 — Debugging

16. **Whether the same embedding model is used on both sides.** Compare
    `DENSE_MODEL_NAME` in `embedder.py` and `retriever.py`, and check whether the
    stored vectors were built with the current model — Qdrant does not record
    which model wrote a point. Also check `reranker_backend` before blaming
    embeddings (Day 28): a Cohere/ONNX swap produces confusing scores for a
    different reason.
17. `BATCH_SIZE` was raised above 8, or a document produced far more chunks than
    usual. The constant is frozen at 8 for exactly this.
18. **The corpus was not re-embedded.** Stored vectors were built with the old
    model and now live in a different coordinate system from the queries. The
    textbook's rule for chunk size applies identically to model choice: *treat it
    like a database schema migration — changing it requires rebuilding the entire
    index from scratch.*

### §12 — System design

19. `DENSE_DIMENSIONS` in `embedder.py`; `DENSE_MODEL_NAME` in **both**
    `embedder.py` and `retriever.py`; the Qdrant collection must be **recreated**
    with the new vector size (`create_qdrant_collection.py`) because vector
    dimensionality is fixed at collection creation; **every chunk must be
    re-embedded and re-upserted**; payload indexes recreated; and the memory
    budget re-checked against the 512 MB ceiling, since a 768-dim model is
    typically also a physically larger model. Nothing in Postgres changes.
    Practically this is a full re-ingest.
20. **Fix:** put the model name in one place — a shared constant module (or the
    existing `app/metrics/registry.py`-style pattern: one module both sides
    import), plus a unit test asserting the two agree. It belongs in the
    zero-network pytest suite because it is a pure comparison. **Why it has not
    been done:** most likely because the two files were written in different
    phases (ingestion in Phase 3, retrieval in Phase 4) and the duplication was
    never surfaced by a failure — which is precisely the profile of the three
    metric registries before they caused three shipped bugs. It is cheap, it is
    not recorded in `CAVEATS.md`, and it is worth recording.

---

## 14. MUST REMEMBER

```text
- Embedding = text → a fixed-length vector where distance means similarity
- bge-small-en-v1.5, 384 dims, ONNX via fastembed, CPU, threads=1
- Cosine measures ANGLE, not distance — length-independent
- Vectors are NORMALISED, so dot product == cosine
- THE SAME MODEL MUST EMBED DOCUMENTS AND QUERIES. No error if not
- BATCH_SIZE = 8, reduced from 32 after OOM. FROZEN
- DENSE_DIMENSIONS=384 is the only automatic guard against a model swap
- Query side calls model.query_embed(); document side calls model.embed()
- Whole-batch failure RAISES; per-chunk failure is logged and SKIPPED
```

## 15. MUST UNDERSTAND

```text
- Why converting meaning into geometry makes relevance COMPUTABLE
- Why the model-mismatch failure is silent, and why silence is the hard part
- Why embeddings score OPPOSITE claims as similar — and why that rules them
  out for contradiction detection
- Why embeddings cannot distinguish two similar identifiers, and what covers it
- Why changing an embedding model is a schema migration, not a config change
```

---

## 16. This connects to

```text
Day 19 — the LLM client
   ↓
Day 20 — embeddings: meaning as geometry          ← you are here
   ↓
Day 21 — where the vectors live: Qdrant, HNSW, named vectors
```

Forward references:

- The sparse half → **Day 26**
- `model.query_embed` on the query side → **Day 25**
- Why opposites score high, and what does contradiction instead → **Day 37**
- Payload indexes and filtered search → **Days 21, 27**
- The 512 MB ceiling behind every choice here → **Day 45**
