# Day 21 — Vector Databases, HNSW, Named Vectors

**Phase 6 · Weight: H (~120 min) · Prerequisites: Day 20**

**Textbook: Part 3 — CONFIRMS. Part 8 — contrast.** Read Part 3 first. Part 8's
FAISS and ChromaDB deep dives are **not** what this system uses, and reading them
as contrast is what stops Qdrant's API from looking arbitrary.

---

## 1. Today's goal

By tonight you can:

- Explain why a relational database cannot do vector search, and why brute force
  does not scale.
- Explain HNSW: the structure, the search, and the trade it makes.
- Explain **named vectors** — one point carrying both a dense and a sparse vector
  — and why that is what makes single-query hybrid search possible.
- Explain **payload indexes**, and what happens to a filtered query without one.
- Explain why the Qdrant point ID **is** the deterministic `chunk_id`, and what
  that buys.

---

## 2. Why now

Day 20 produced vectors. Today they get somewhere they can be searched. Days 25–27
then query them.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Embeddings, cosine, normalisation | Day 20 | What is being stored |
| `EmbeddedChunk` | Days 10, 20 | The input |
| Indexes cost writes to speed reads | Day 13 | Same trade, different store |
| `doc_id` → `chunk_id` derivation | Day 16 | The point ID |

---

## 4. Concept lesson

### 4.1 Why Postgres cannot do this

The textbook (3.1) puts it precisely:

> Vector similarity search asks a fundamentally different question: "of these one
> million 1536-dimensional vectors, which ones are mathematically closest to this
> query vector?" **There is no exact match — every comparison is a matter of
> degree.**

A B-tree indexes an *ordering*. "Closest in 384 dimensions" has no ordering to
index — a vector's nearest neighbour is not adjacent in any single dimension.

**Brute force** — compare against all 2,300 stored vectors — is actually fine at
this corpus size (384 multiplications × 2,300 ≈ 900k operations, milliseconds).
**Qdrant is not here for speed at 2,300 chunks.** It is here for hybrid search,
payload filtering and RRF, which are the properties Day 27 needs.

That is worth being honest about: at this scale the vector database earns its
place on **features**, not performance.

---

### 4.2 What a vector database stores

Per entry:

- the **vector**,
- an **ID**,
- a **payload** — arbitrary metadata.

And the textbook's warning (3.2), which is the sentence to keep:

> A vector database **does not understand the meaning** of the vectors it holds …
> The semantic meaning is entirely encoded in the geometry produced by the
> embedding model … **if your embedding model is poor, no vector database,
> however well-engineered, can compensate.**

That is Day 20's model-mismatch failure restated from the storage side. Qdrant
will happily search a corpus embedded with the wrong model and return confident
nonsense.

---

### 4.3 HNSW

**Hierarchical Navigable Small World** — a multi-layer graph built at insert time.

- **Top layer:** few nodes, long-range links. Good for jumping to the right
  region.
- **Lower layers:** progressively more nodes, shorter links. Good for local
  precision.

Search starts at the top, greedily moves to the nearest node, drops a layer, and
repeats until the bottom.

**The trade** (textbook 3.3): O(n) → ~O(log n), at the cost of being
**approximate** — it does not guarantee the true top-K every time. In practice the
error is negligible for RAG.

**Mental model.** HNSW is **an express train then a local one**. The express gets
you to the right district in a few stops; the local finds the street.

**Where it appears in LedgerMind:** in the collection configuration, and nowhere
else. `retriever.py` never mentions it. That is correct — HNSW is an
implementation detail of the store, and the only reason to know it is to
understand that results are *approximate by design*, so a chunk missing from the
top-20 is not proof it is absent.

---

### 4.4 Named vectors — the feature this system is built on

Most vector databases store **one** vector per entry. Qdrant stores a **named
map**:

```python
PointStruct(
    id=ec.chunk.chunk_id,
    vector={
        DENSE_VECTOR_NAME:  ec.dense_vector,                      # "dense"
        SPARSE_VECTOR_NAME: SparseVector(indices=..., values=...) # "sparse"
    },
    payload=_metadata_to_payload(ec),
)
```

**One point. One payload. Two vectors.**

**Why this matters enormously.** Without it you would need two collections — one
dense, one sparse — and hybrid search would mean two queries, two result sets,
and a manual merge in Python, with the payload duplicated or joined by ID.

With named vectors, Day 27's hybrid query is **one** call:

```python
client.query_points(
    prefetch=[
        Prefetch(query=dense_vector,  using="dense",  limit=top_k, filter=f),
        Prefetch(query=sparse_vector, using="sparse", limit=top_k, filter=f),
    ],
    query=FusionQuery(fusion=Fusion.RRF),
    limit=top_k,
)
```

Two legs, one round trip, fusion server-side. **The `using=` parameter is the
named vector.**

The textbook's comparison table (3.4) marks hybrid search as Qdrant's
differentiator, and this is what that means concretely.

---

### 4.5 Payload indexes

A payload field is just JSON until you index it. A filter on an unindexed field
forces Qdrant to check every candidate.

```python
index_fields = {
    "tenant_id":      PayloadSchemaType.KEYWORD,
    "company":        PayloadSchemaType.KEYWORD,
    "financial_type": PayloadSchemaType.KEYWORD,
    "fiscal_year":    PayloadSchemaType.KEYWORD,
    "quarter":        PayloadSchemaType.KEYWORD,
    "chunk_type":     PayloadSchemaType.KEYWORD,
    "is_latest":      PayloadSchemaType.BOOL,
    "page_number":    PayloadSchemaType.INTEGER,
    "doc_id":         PayloadSchemaType.KEYWORD,
    "filing_date":    PayloadSchemaType.KEYWORD,
}
```

**Ten fields, three types.** Note `filing_date` is `KEYWORD`, not a date — it is
matched exactly, never ranged, so a string index is sufficient and cheaper.

**`KEYWORD` is what `MatchAny` operates on**, and `retriever._build_filter`'s
comment relies on it:

> The `company` payload key is already indexed KEYWORD, which is the type
> `MatchAny` operates on, so **no re-index is required.**

That sentence is F14's migration plan in one line: the schema change from
`MatchValue` to `MatchAny` needed no re-index because the index type already
supported it.

**The function is idempotent** — *"safe to call on an existing collection"* — so
it can run on every ingest without a "have I done this?" check.

---

### 4.6 The point ID is the chunk ID

```python
PointStruct(id=ec.chunk.chunk_id, ...)
```

And `chunk_id` is deterministic (Day 24):

```python
fingerprint = f"{doc_id}:{page_number}:{position}:{text[:100]}"
return str(uuid.UUID(hashlib.md5(fingerprint.encode()).hexdigest()))
```

**What this buys: idempotent re-ingestion.** Re-ingest the same PDF and every
chunk produces the same ID, so `upsert` **overwrites in place**. No duplicates, no
delete-first step.

The textbook (15B, "The Vector Database Upsert vs Insert Confusion") describes
exactly the trap this avoids — and also the residual one it does **not**:

> if the new version of the document has *fewer* pages, the old chunks from pages
> that no longer exist remain in the index, now **orphaned and stale** — they will
> still be retrieved.

**LedgerMind has this residual problem**, recorded as `CAVEAT-016`: Qdrant holds
chunks whose `doc_id` has no row in `documents`, so citations resolve to nothing.
Deterministic IDs make re-ingestion clean *for chunks that still exist*; they do
nothing for chunks that stopped existing.

And `IMPLEMENTATION_DELTAS.md` §D adds the sharper version:

> Orphaned vector rows — Qdrant has no purge, and **deterministic IDs only help
> while boundaries hold.**

Change the chunker's target size and every chunk ID changes — because `position`
and `text[:100]` change — so the old points become orphans with no way to find
them.

---

## 5. The actual LedgerMind file

```
File:        backend/app/ingestion/qdrant_writer.py (399 lines)
Purpose:     Upsert EmbeddedChunk objects into Qdrant Cloud
Why:         Writing is separate from reading; retriever.py never writes
Who imports: ingestion/pipeline.py, and several scripts/
Entry points: write_chunks(embedded_chunks, ...) -> dict
             create_payload_indexes(client) -> None
             verify_collection(tenant_id) -> dict
Data in:     list[EmbeddedChunk]
Data out:    an upsert summary
Collection:  ledgermind_chunks
Batch:       UPSERT_BATCH_SIZE = 100
Client:      lazy singleton, timeout=60 (ingest, not a request path)
```

**Note `timeout=60` here versus `QDRANT_TIMEOUT_SECONDS = 10` in
`retriever.py`.** Same store, two clients, two bounds — because one is a batch
upsert of 100 points and the other is on a user's request path. The right timeout
is a property of the **caller**, not of the service.

---

## 6. Deep walkthrough

### 6.1 `_metadata_to_payload` — everything travels

```python
def _metadata_to_payload(ec: EmbeddedChunk) -> dict:
    meta    = ec.chunk.metadata
    payload = asdict(meta)
    payload["chunk_id"] = str(meta.chunk_id)
    payload["doc_id"]   = str(meta.doc_id)
    payload["text"]     = ec.chunk.text
    return payload
```

**`asdict(meta)`** — the whole `ChunkMetadata` dataclass, flattened (Day 10).

**Two explicit `str()` casts.** `chunk_id` and `doc_id` may be `UUID` objects, and
JSON has no UUID type (Day 5). Without the cast the client would fail or coerce
unpredictably.

**`payload["text"] = ec.chunk.text` — the full chunk text is stored in the
payload.** This is the single most consequential line in the file:

- Retrieval returns the text **with** the hit — no second lookup, no Postgres
  join. `hybrid_search` builds a `ChunkResult` straight from the payload.
- The reranker (Day 28) needs the text to score it. Fetching it separately would
  mean a round trip per candidate.
- **Cost:** the corpus text is duplicated in Qdrant. At ~2,300 chunks that is
  trivial; at a million it is a real storage decision.

**And a consequence worth naming:** the text lives in a third-party cloud store.
For a system holding client filings that is a data-residency question the
repository does not address.

---

### 6.2 `write_chunks`

```python
def write_chunks(embedded_chunks, batch_size=None, ...):
    ...
    points = [_build_point(ec) for ec in embedded_chunks]
    for i in range(0, len(points), effective_batch):
        batch = points[i:i + effective_batch]
        client.upsert(collection_name=COLLECTION_NAME, points=batch, wait=True)
```

**STATE BEFORE.** A list of `EmbeddedChunk`. Qdrant holds whatever a previous
ingest left.

**`UPSERT_BATCH_SIZE = 100`**, *"safe for network reliability"* — not a memory
limit like `BATCH_SIZE=8`, but a **payload size** limit. Each point carries a
384-float vector, a sparse vector and the full chunk text; a thousand at once is
a large HTTP body over a link that has been observed flapping (Day 19).

**`upsert`, not `insert`.** Same ID → overwrite. This is what makes re-ingestion
idempotent, and it is the textbook's 15B distinction.

**`wait=True`** — block until Qdrant has indexed the batch. Slower, and it means
the completion gate that follows is checking a store that has actually finished.
Without it, a verification step immediately after could read a collection mid-index
and report a false count.

**STATE AFTER.** Points present, searchable, payload-indexed.

---

### 6.3 `verify_collection` — and the gate that could not fail

```python
def verify_collection(tenant_id: str) -> dict:
```

This is the post-write completion check, and it is the subject of audit finding
**F8**. From the audit:

```python
# This previously read verify_collection(ALPHA_TENANT)["total_points"],
# a TENANT-WIDE count. ETERNAL alone holds 2268 chunks, so a threshold of
# 100 was already satisfied before the run started -- the gate passed
# unconditionally on any ingest into a non-empty tenant, including one
# that indexed zero chunks. It could not fail.
```

**Read that again.** The gate asked *"does this tenant have more than 100
chunks?"* — a question whose answer was already yes before the ingest began. It
reported success for an ingest that indexed nothing.

**The general failure**, which you met on Day 16: *a checker whose scope is
broader (or narrower) than the thing it certifies passes having inspected
nothing.* F8 is the third instance, alongside `regression_check` validating the
producer rather than the store, and `check_migrations` inspecting one of two
databases.

The fix was to scope the gate to **this run's `doc_ids`**.

---

### 6.4 The lazy client, and the two warnings

```python
def _get_client():
    global _qdrant_client
    if _qdrant_client is None:
        url     = os.environ.get("QDRANT_URL")
        api_key = os.environ.get("QDRANT_API_KEY")
        if not url:
            raise RuntimeError("QDRANT_URL not set. Add it to your .env:\n"
                               "  QDRANT_URL=https://your-cluster.qdrant.io")
        _qdrant_client = QdrantClient(url=url, api_key=api_key, timeout=60)
```

Day 12's pattern, and Day 12's defaults rule: **raise rather than default**,
because a default pointing at local Docker Qdrant while you believe you are on
Cloud produces confident answers from the wrong index.

Note the error message names the **shape** of the correct value
(`https://your-cluster.qdrant.io`), which is what makes the local-vs-cloud
mistake visible at the moment it matters.

**The two warnings to recognise** (`CLAUDE.md` §4):

| Warning | Means |
|---|---|
| `UserWarning: Api key is used with an insecure connection` | You are on **local Docker Qdrant**, not Cloud. **Every measurement in that session is invalid** |
| `UserWarning: Failed to obtain server version` | The client failed its construction-time probe; **the next query in that process will die** |

The second is the more useful one: it tells you the failure is coming *before* it
arrives.

---

## 7. Data flow

```
EmbeddedChunk
  ├─ chunk.chunk_id  = uuid(md5(f"{doc_id}:{page}:{position}:{text[:100]}"))
  ├─ chunk.text
  ├─ chunk.metadata  (ChunkMetadata dataclass)
  ├─ dense_vector    [384 floats]
  └─ sparse_indices / sparse_values
        │
        ▼  _build_point
PointStruct
  ├─ id     = chunk_id                    ← THE SAME VALUE. Idempotency.
  ├─ vector = {"dense":  [384 floats],
  │            "sparse": SparseVector(indices, values)}     ← NAMED VECTORS
  └─ payload = asdict(metadata)
               + chunk_id (str) + doc_id (str) + TEXT
        │
        ▼  batches of 100, wait=True
   Qdrant Cloud · collection "ledgermind_chunks"
        │
        ├─ HNSW index over "dense"
        ├─ inverted index over "sparse"
        └─ payload indexes on 10 fields
        │
        ▼  QUERY TIME (Days 25-27)
   query_points(prefetch=[dense leg, sparse leg], query=FusionQuery(RRF))
        │
        ▼
   scored points WITH PAYLOAD
        │
        ▼  hybrid_search builds ChunkResult straight from payload["text"]
   no second lookup anywhere
```

---

## 8. Engineering decision — Qdrant Cloud

**Problem.** Store and search ~2,300 chunk vectors with metadata filtering and
hybrid dense+sparse retrieval, inside 512 MB and ₹0.

**Decision.** Qdrant Cloud, one collection, named vectors, ten payload indexes,
deterministic point IDs.

`ENGINEERING_DECISIONS.md` **ED-002/ED-003**.

| Alternative | Why not |
|---|---|
| **FAISS** (textbook 3.4) | A *library*, not a database: no persistence, no metadata filtering, no client/server. Every restart re-embeds |
| **ChromaDB** | Easiest entry point; **no hybrid search**, and basic filtering only. Hybrid is not optional here |
| **Pinecone** | Managed and fine; limited hybrid, and data on a third party with no self-host escape |
| **pgvector** (in the Postgres you already run) | Tempting — one store, transactional. **No BM25, no native RRF, no sparse vectors.** You would rebuild Day 26–27 by hand |
| **Self-hosted Qdrant** | Compose already defines it. Costs RAM the 512 MB tier does not have |

**Trade-offs accepted.**

- **A network hop on the request path.** Measured 0.36–0.41 s warm, bounded at
  10 s (Day 19's reasoning applied to a different provider).
- **Corpus text duplicated** in a third-party store — a storage cost, and an
  unaddressed data-residency question.
- **No transactions.** Qdrant and Postgres can disagree, and do: `CAVEAT-016`.
- **HNSW is approximate.** A missing chunk is not proof of absence.

**Current validity.** The features justify it; the performance argument does not,
at 2,300 chunks. Say the real reason.

**At 10×.** Where Qdrant starts genuinely earning its keep. The pressure points
would be payload storage (the duplicated text) and the absence of a purge path
for orphans.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `Api key is used with an insecure connection` | **Local Docker Qdrant, not Cloud. Every measurement invalid** |
| `Failed to obtain server version` | Construction-time probe failed; the next query will die |
| Filtered query slow or returning oddities | Missing payload index |
| `RuntimeError: QDRANT_URL not set` | Deliberate — a default would search the wrong store |
| Duplicate chunks after re-ingest | Non-deterministic IDs, or the chunker's parameters changed |
| Citations resolve to no document | `CAVEAT-016` — orphaned chunks |
| Ingest gate passes on zero indexed chunks | Audit **F8** — a tenant-wide count |
| Empty candidate set | **A network signature**, not a retrieval one (`CLAUDE.md` §4) |

---

## 10. Hands-on experiment

### Experiment 1 — inspect the collection

```bash
docker compose exec -T backend python -c "
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME
c = _get_client()
info = c.get_collection(COLLECTION_NAME)
print('points        :', info.points_count)
print('status        :', info.status)
vp = info.config.params
print('named vectors :', list((vp.vectors or {}).keys()))
print('sparse vectors:', list((vp.sparse_vectors or {}).keys()))
for name, cfg in (vp.vectors or {}).items():
    print(f'  {name}: size={cfg.size} distance={cfg.distance}')
"
```

**Two named vectors on one collection.** That is §4.4, verified.

### Experiment 2 — read one point

```bash
docker compose exec -T backend python -c "
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME
c = _get_client()
pts, _ = c.scroll(collection_name=COLLECTION_NAME, limit=1,
                  with_payload=True, with_vectors=True)
p = pts[0]
print('id      :', p.id)
print('vectors :', {k: (f'{len(v)} floats' if isinstance(v, list) else type(v).__name__)
                    for k, v in p.vector.items()})
print()
print('payload keys:')
for k, v in sorted(p.payload.items()):
    s = str(v)
    print(f'  {k:16} {s[:70]}{\"...\" if len(s) > 70 else \"\"}')
"
```

Find `text` in the payload. **That is why retrieval needs no second lookup.**

### Experiment 3 — prove the point ID is the chunk ID

```bash
docker compose exec -T backend python -c "
import hashlib, uuid
from app.ingestion.chunker import _make_chunk_id
a = _make_chunk_id('doc-1', 5, 0, 'Revenue from operations grew to INR 54,364 crore')
b = _make_chunk_id('doc-1', 5, 0, 'Revenue from operations grew to INR 54,364 crore')
c = _make_chunk_id('doc-1', 5, 1, 'Revenue from operations grew to INR 54,364 crore')
print('same inputs      :', a); print('                 :', b, '  identical:', a==b)
print('position 0 -> 1  :', c, '  changed:', a!=c)
print()
print('The Qdrant point id IS this value. Re-ingest overwrites in place.')
print('Change the CHUNKER and every id changes -> the old points become orphans.')
"
```

### Experiment 4 — payload indexes, and MatchAny

```bash
docker compose exec -T backend python -c "
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME
c = _get_client()
info = c.get_collection(COLLECTION_NAME)
ps = info.payload_schema or {}
for f, s in sorted(ps.items()):
    print(f'  {f:16} {getattr(s, \"data_type\", s)}')
print()
print('company is KEYWORD -> which is the type MatchAny operates on.')
print('That is why F14 (MatchValue -> MatchAny) needed NO re-index.')
"
```

### Experiment 5 — HNSW is approximate

```bash
docker compose exec -T backend python -c "
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME
from app.ingestion.embedder import _embed_dense
c = _get_client()
v = _embed_dense(['risk factors disclosed by the company'])[0]
for limit in (5, 20, 50):
    r = c.query_points(collection_name=COLLECTION_NAME, query=v,
                       using='dense', limit=limit, with_payload=False)
    ids = [str(p.id)[:8] for p in r.points[:5]]
    print(f'limit={limit:3d}  top-5 ids: {ids}')
print()
print('Top-5 is usually stable, and HNSW does not GUARANTEE it.')
print('A chunk missing from top-20 is not proof it is absent.')
"
```

### Experiment 6 — the tenant filter, at the store

```bash
docker compose exec -T backend python -c "
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME
import os
c = _get_client()
def count(f=None):
    return c.count(collection_name=COLLECTION_NAME, count_filter=f, exact=True).count
print('all points            :', count())
print('tenant filter applied :', count(Filter(must=[
    FieldCondition(key='tenant_id', match=MatchValue(value=os.getenv('T','')))])))
print()
print('Qdrant has NO RLS. This filter is the ONLY isolation, and the')
print('application must remember it. Postgres fails closed; this fails OPEN.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/ingestion/qdrant_writer.py`:

1. `_metadata_to_payload` stores the full chunk text. Name one thing that buys at
   query time, and one thing it costs.
2. Why are `chunk_id` and `doc_id` explicitly `str()`-cast?
3. `_get_client` uses `timeout=60`; `retriever.py` uses `10`. Same store. Why
   different?
4. `create_payload_indexes` is described as idempotent. What does that let the
   caller skip?
5. Find `verify_collection`. Read audit finding F8 in
   `docs/audit/repo_audit_20260811.md`. State what the gate asked, and why the
   answer was always yes.

Then compare with the textbook:

6. Read Part 8.1 (`faiss.IndexFlatL2(1536)`) and 8.2 (ChromaDB `collection.add`).
   List three things `_build_point` does that neither example can.

---

## 12. Self-check questions

**Basic**
1. What does a vector database store per entry?
2. What does HNSW trade away?
3. What is a named vector?
4. What is the collection called?
5. What is the Qdrant point ID here?

**Code**
6. What is `UPSERT_BATCH_SIZE`, and what limit is it about?
7. What does `wait=True` do?
8. How many payload indexes, and what are the three types?
9. Why is `filing_date` indexed as `KEYWORD` and not a date?
10. What raises if `QDRANT_URL` is unset?

**Why**
11. Why can a relational database not do vector search?
12. Why is Qdrant here, given brute force would be fast enough at 2,300 chunks?
13. Why store the chunk text in the payload?
14. Why `upsert` rather than `insert`?
15. Why does the write client have a longer timeout than the read client?

**Debugging**
16. `Api key is used with an insecure connection`. What is happening, and what
    does it mean for anything you measured this session?
17. Citations resolve to no document. Which caveat, and what caused it?
18. An ingest reports success and indexed nothing. Which finding, and what was
    the gate actually asking?

**System design**
19. You change the chunker's target size. Trace the consequences through to
    Qdrant, and say what must be run.
20. Qdrant has no RLS and no purge. Design a safe orphan-removal procedure,
    given `CAVEAT-016` and the 139-chunk incident.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **Buys:** retrieval returns the text *with* the hit, so `hybrid_search` builds
   a `ChunkResult` directly from the payload — no second lookup, no Postgres
   join, and the reranker has the text it needs without a round trip per
   candidate. **Costs:** the corpus text is duplicated in a third-party cloud
   store — trivial at 2,300 chunks, a real storage decision at a million, and an
   unaddressed data-residency question for client filings.
2. Because they may be Python `UUID` objects and **JSON has no UUID type**
   (Day 5). Without the cast the client would either fail to serialise or coerce
   unpredictably — and the payload is what every downstream consumer reads.
3. Because the right timeout is a property of the **caller**, not the service.
   The writer sends batches of 100 points, each carrying two vectors and the full
   chunk text — a large body over a link observed to flap. The reader is on a
   **user's request path**, where 10 s is ~25× the measured 0.36–0.41 s warm
   latency and converts a stall into a catchable exception (Day 19's argument).
4. A "have I already created these?" check. Because creating an existing index is
   a no-op, the caller can run it unconditionally on every ingest — which means
   the indexes cannot be missing because someone forgot a setup step.
5. It asked **"does this tenant have more than 100 chunks?"** — via
   `verify_collection(ALPHA_TENANT)["total_points"]`, a **tenant-wide** count.
   ETERNAL alone holds 2,268 chunks, so the answer was yes **before the ingest
   started**. The gate passed unconditionally on any ingest into a non-empty
   tenant, including one that indexed zero chunks. **It could not fail.**
6. **(a)** Two vectors on one point (`{"dense": ..., "sparse": ...}`) — FAISS
   stores one vector array with no names, Chroma one embedding per entry. **(b)** A
   rich, *indexed* payload used for pre-filtering inside the query — FAISS has no
   metadata at all; Chroma's filtering is basic and not integrated with fusion.
   **(c)** A **deterministic, content-derived point ID** enabling idempotent
   upsert — FAISS IDs are array positions, and re-adding duplicates.
   (Also acceptable: persistence without an explicit `write_index` step; and
   server-side RRF fusion, which neither library offers.)

### §12 — Basic

1. The vector, an ID, and a payload of arbitrary metadata.
2. **Exactness.** It is approximate nearest-neighbour: O(log n) instead of O(n),
   without guaranteeing the true top-K.
3. A vector stored under a name on a point, so one point can carry several
   vectors — here `"dense"` and `"sparse"`.
4. `ledgermind_chunks`.
5. The deterministic `chunk_id`:
   `uuid(md5(f"{doc_id}:{page_number}:{position}:{text[:100]}"))`.

### §12 — Code

6. `100`. A **payload size / network reliability** limit, not a memory one — each
   point carries two vectors plus the full chunk text.
7. Blocks until Qdrant has indexed the batch, so a verification step immediately
   afterwards is not reading a mid-index collection.
8. **Ten.** `KEYWORD` (8 fields), `BOOL` (`is_latest`), `INTEGER` (`page_number`).
9. Because it is only ever matched **exactly**, never ranged. A string index is
   sufficient and cheaper than a date type.
10. `RuntimeError`, with a message showing the expected shape
    (`https://your-cluster.qdrant.io`) — deliberately, because a default pointing
    at local Docker Qdrant would search the wrong index and answer confidently.

### §12 — Why

11. Because a B-tree indexes an **ordering**, and "closest in 384 dimensions" has
    no ordering — a vector's nearest neighbour is not adjacent in any single
    dimension. Every comparison is a matter of degree, not an exact match.
12. For **features, not speed**: hybrid dense+sparse in one query, native RRF
    fusion, and payload pre-filtering **inside each prefetch leg** (Day 27). At
    2,300 chunks brute force would be milliseconds; the vector database earns its
    place on capability.
13. So retrieval and reranking need no second lookup. It is the difference
    between one round trip and one-plus-N.
14. Because the point ID is deterministic, so `upsert` **overwrites in place** on
    re-ingest. `insert` would raise on a duplicate key and abort the batch.
15. See §11 Q3.

### §12 — Debugging

16. You are connected to **local Docker Qdrant**, not Qdrant Cloud — the warning
    fires because an API key is being sent over plain HTTP. It means the
    collection you are searching is not the one holding the corpus, so **every
    measurement in that session is invalid** and must be discarded, not
    interpreted. Fix: check `printenv QDRANT_URL` (Day 1 pre-flight).
17. **`CAVEAT-016`.** Qdrant holds chunks whose `doc_id` has no row in
    `documents`. Caused by the two-database `doc_id` divergence (Day 16) and by
    orphans left when chunk boundaries changed — Qdrant has no purge and no
    foreign keys, so nothing removes them automatically.
18. **Audit finding F8.** The gate asked whether the *tenant* had more than 100
    chunks, when ETERNAL alone had 2,268 — so the threshold was met before the run
    began. Fixed by scoping the gate to **this run's `doc_ids`**.

### §12 — System design

19. `chunk_id = uuid(md5(f"{doc_id}:{page}:{position}:{text[:100]}"))`. Changing
    the target size changes where splits fall, so **`position` and `text[:100]`
    both change** — therefore **every chunk ID changes**. Consequences: the new
    points are inserted alongside the old ones (upsert cannot overwrite an ID it
    no longer generates), so the collection **doubles** and stale chunks remain
    retrievable; and the embedding-space distribution shifts, which the textbook's
    "chunk size trap" (15B) warns produces subtly degraded retrieval that looks
    like a prompt problem. **What must be run:** delete the affected document's
    points by `doc_id` filter, then a full re-ingest — and per `CLAUDE.md` §9,
    `regression_check` first, and `purge_orphaned_metrics` (dry run) if extraction
    changed too. Treat it as a schema migration, not a config change.
20. **The constraint from the incident:** 139 chunks were deleted as "orphans" on
    the strength of a lookup against **one of two databases** — they were
    production's Paytm and Titan corpus. So the procedure must be:
    **(a)** Enumerate candidate `doc_id`s from Qdrant, not from Postgres.
    **(b)** Check each against **every** database that shares the collection —
    and if a database is unreachable, **abort**, because absence of evidence from
    an unqueried store is not evidence of absence.
    **(c)** Dry run first, printing the full candidate list with counts per
    `doc_id`, and stop (`CLAUDE.md` §1, rule 2).
    **(d)** Before any deletion, verify each candidate is genuinely unreferenced —
    not merely unmatched by a query you might have scoped wrongly.
    **(e)** Delete by explicit `doc_id` filter, never by "everything not in this
    list".
    The structural fix is upstream: give each database its **own collection**, so
    the question "is this chunk an orphan?" has a single-store answer (Day 16).

---

## 14. MUST REMEMBER

```text
- Collection: ledgermind_chunks. TWO named vectors per point: "dense", "sparse"
- The Qdrant point ID IS the deterministic chunk_id → idempotent upsert
- The FULL CHUNK TEXT lives in the payload → no second lookup at query time
- 10 payload indexes. company is KEYWORD, which is what MatchAny needs
- UPSERT_BATCH_SIZE = 100 (network/payload), wait=True
- Write client timeout 60s; READ client 10s. Different callers, different bounds
- HNSW is APPROXIMATE — a chunk missing from top-20 is not proof of absence
- Qdrant has NO RLS: the tenant filter is the only isolation, and it fails OPEN
- "Api key is used with an insecure connection" → local Qdrant → measurements void
```

## 15. MUST UNDERSTAND

```text
- Why named vectors are what make single-query hybrid search possible
- Why Qdrant is here for FEATURES, not speed, at this corpus size — and why
  saying so honestly matters
- Why the vector database cannot compensate for a bad embedding model
- Why deterministic IDs only help WHILE CHUNK BOUNDARIES HOLD
- Why F8 is the third instance of "a checker that could not observe the thing
  it certified"
```

---

## 16. This connects to

```text
Day 20 — embeddings
   ↓
Day 21 — where they live and how they are indexed     ← you are here
   ↓
Day 22 — going back to the start: PDF → PageBlock
```

Forward references:

- `_build_filter` and payload conditions → **Day 27**
- `Prefetch` + `FusionQuery(RRF)` → **Day 27**
- `chunk_id` construction in full → **Day 24**
- `CAVEAT-016` orphans → **Day 43**
- Audit **F8** and gates that cannot fail → **Day 43**
