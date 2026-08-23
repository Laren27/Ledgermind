# Day 28 — Reranking, and Two Incompatible Score Scales

**Phase 7 · Weight: H (~120 min) · Prerequisites: Day 27**

**Textbook: Part 6 "Reranking" — EXTENDS.**

> **This is the most important day in the course.** If you take one idea from
> LedgerMind, take this one: **a number is meaningless without knowing which
> instrument produced it.**

---

## 1. Today's goal

By tonight you can:

- Explain bi-encoders versus cross-encoders, and why cross-encoders **cannot** do
  first-pass retrieval.
- Explain why LedgerMind has **two** rerankers, and why that is a memory decision
  with a correctness consequence.
- Reconstruct **BUG-001** from first principles: one threshold pair, two scales,
  and every Cohere-served query classified `high`.
- Explain why `reranker_backend` travels on the wire to admins, and what wrong
  conclusion it exists to prevent.
- Explain `_cohere_with_retry` — one retry, never a ladder — and the measured
  event behind it.

---

## 2. Why now

Day 27 produced 20 candidates ranked by RRF and warned you that RRF is a *third*
incompatible scale. Today adds a fourth and a fifth, and shows what happened when
two of them were mixed.

The ordering is strict for a reason: "the score scale is incomparable" is
unteachable before you have seen two scales fused (Day 27).

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Embeddings are bi-encoders | Day 20 | The contrast |
| RRF is a third scale | Day 27 | Today adds two more |
| `reranker_score = -inf`, `reranker_backend = "none"` | Days 25, 27 | The sentinels |
| One retry, never a ladder | Day 19 | The same shape, different provider |
| `reranker_backend` on the admin response | Day 9 | Today is why |

---

## 4. Concept lesson

### 4.1 Why retrieval alone is not enough

Textbook 6.1:

> Retrieval methods (dense and sparse) are optimized for **speed** across
> millions of candidates — they use approximations (HNSW) and simplified scoring
> (BM25, cosine similarity) precisely because exact, deep comparison across
> millions of items would be too slow. This speed comes at the cost of precision:
> the top-20 results from retrieval are a reasonably good shortlist, but **not
> necessarily perfectly ordered** by true relevance.

**Retrieval optimises recall. Reranking optimises precision.** Day 25's `20 → 5`
is exactly this split.

---

### 4.2 Bi-encoder versus cross-encoder

**Bi-encoder** (what every embedding model is): encodes query and document
**independently**, then compares the two vectors.

```
query    ──► [encoder] ──► vector_q  ┐
                                      ├─► cosine
document ──► [encoder] ──► vector_d  ┘
```

**The query and the document never meet inside the model.** That independence is
exactly what allows document vectors to be **pre-computed once** and stored
(Day 20) — and it is also the source of the imprecision.

**Cross-encoder:** takes query **and** document **together**, as one input, and
emits a single relevance score.

```
[query] [SEP] [document] ──► [encoder] ──► 0.87
```

Textbook 6.2:

> Because the model processes both texts jointly, it can capture **fine-grained
> interactions** between them — for example, recognizing that a document discusses
> refund timelines for **international** orders specifically, not just refunds in
> general — nuance a bi-encoder's independent encoding tends to miss.

**And why it cannot do retrieval:**

> Since query and document must be processed together, **nothing can be
> pre-computed in advance.** Every single query would require running the
> cross-encoder against every single document in the collection, in real time —
> computationally infeasible at any meaningful scale.

**Mental model.** A bi-encoder describes two people separately and compares the
descriptions. A cross-encoder **puts them in a room together**. The second is
better and does not scale — so you use the first to pick 20 people, and the second
to rank those 20.

---

### 4.3 LedgerMind has two rerankers

```python
RERANKER_MODEL_NAME = "Xenova/ms-marco-MiniLM-L-6-v2"   # local ONNX, fallback
# and, primary:                     Cohere "rerank-english-v3.0"
```

| | Cohere (primary) | Local ONNX (fallback) |
|---|---|---|
| Where | Cohere's API | in-process |
| RAM | **0 MB** | model resident |
| Score | `relevance_score` ∈ **[0, 1]** | raw **logits**, ~`[-12, +2]` |
| Cost | network hop | CPU |
| Loaded | never (no model) | **lazily, only if used** (Day 12) |

**Why two.** The 512 MB ceiling. `retriever.py` logs it explicitly:

```python
logger.info("Initializing Cohere client for cloud reranking (0MB RAM)")
```

Cohere is primary **because it costs no memory**. The local model exists so the
system still works when Cohere does not.

**And here is the consequence that defines this day.** The two produce
**numerically incompatible scores**:

```
Cohere:  0.92, 0.47, 0.03      — probabilities, higher is better, bounded [0,1]
ONNX:   -2.31, -5.88, -9.14    — logits, higher is better, unbounded, mostly negative
```

**A score of `-3.39` is a plausible ONNX logit and an impossible Cohere
relevance.** Read it as the wrong one and every conclusion downstream is wrong.

---

### 4.4 The textbook gets this half-right

Textbook 6.2, under "What I learned running this on financial documents":

> Cross-encoder scores are **not calibrated probabilities** — they are raw
> logit-like scores that can range far outside [0, 1] depending on the model. A
> score of 8.3 does not mean "83% relevant." What matters is the relative
> ordering, not the absolute score. **However, the absolute score is still useful
> as a confidence threshold:** if your top-ranked chunk after reranking has a
> score below some minimum … the query likely has no good match in your corpus.

**Correct, and it stops one step short.** It assumes **one** model. LedgerMind has
two, and they **swap at runtime, silently, on network conditions**. So the
absolute score is useful as a threshold *only if you also know which model
produced it* — and the textbook has no concept of that.

**That gap is BUG-001.**

---

### 4.5 BUG-001, reconstructed

From `semantic_engine.py`'s comment block:

> Bug history: prior to this fix, a single fixed threshold pair (`-4.5` / `-7.5`,
> calibrated for the **LOCAL** reranker's logit scale) was applied to scores from
> **EITHER** backend. Cohere's 0-1 `relevance_score` is **always ≥ -4.5**, so any
> query that got Cohere-scored was **silently classified HIGH confidence
> regardless of actual relevance** — while the same query hitting the local
> fallback (e.g. on a Cohere API hiccup) was scored correctly. This produced
> `confidence_tier` results that **changed run-to-run for the same query**,
> depending purely on which reranker backend happened to serve that request.

**Follow the arithmetic yourself.** The thresholds were `high ≥ -4.5`,
`medium ≥ -7.5`.

| Cohere score | Meaning | `≥ -4.5`? | Tier assigned |
|---|---|---|---|
| 0.92 | excellent match | yes | high ✓ |
| 0.47 | mediocre | yes | **high ✗** |
| 0.03 | irrelevant | yes | **high ✗** |
| 0.001 | noise | yes | **high ✗** |

**Every possible Cohere score is ≥ −4.5.** The confidence tier was structurally
`high` on the Cohere path — a system-wide guarantee of high confidence, arrived at
by arithmetic.

**And the symptom was intermittent**, because the *same query* returned `medium`
when the ONNX fallback served it. Two runs, two answers, no code change. That is
the hardest kind of bug to chase, and this is why:

> `CLAUDE.md` §8: **Do not trust a single observation.** Verify across runs *and*
> across models.

**The fix — two threshold pairs, selected by backend:**

```python
LOCAL_HIGH_CONFIDENCE_THRESHOLD   = -4.5
LOCAL_MEDIUM_CONFIDENCE_THRESHOLD = -7.5

COHERE_HIGH_CONFIDENCE_THRESHOLD   = 0.5
COHERE_MEDIUM_CONFIDENCE_THRESHOLD = 0.15
```

```python
backend = chunks[0].get("reranker_backend", "local")  # default to stricter/local
if backend == "cohere":
    high_threshold, medium_threshold = COHERE_HIGH, COHERE_MEDIUM
    EMPIRICAL_MIN, EMPIRICAL_MAX = 0.0, 1.0
else:
    high_threshold, medium_threshold = LOCAL_HIGH, LOCAL_MEDIUM
    EMPIRICAL_MIN, EMPIRICAL_MAX = -12.0, -2.0
```

**Note the default is `"local"`** — the *stricter* scale. When unsure, assume the
harsher interpretation. Day 9 named this a **safety default**, distinct from
`_reranker_backend`'s refusal to default in the API response, which would be an
**observational** claim.

---

### 4.6 What the Cohere thresholds are, and are not

```python
# CALIBRATED 2026-07-27: validated against real production scores logged across
# all 83 golden-dataset questions (COHERE_CALIBRATION debug logging, since removed).
# Every "high" result scored >=0.88; the one genuine "medium" (Q031, ambiguous
# cross-period question) scored 0.4656, correctly below 0.5. No query in this
# run fell between 0.15-0.5 or below 0.15, so the MEDIUM/LOW boundary itself
# remains unstressed by real data -- revisit if a future query's tier looks wrong
# given its logged score. 0.5/0.15 held up against everything checked; keeping.
```

**Read this as two different claims:**

- **`0.5` is validated.** 83 questions; every high scored ≥ 0.88; one genuine
  medium at 0.4656, correctly below.
- **`0.15` is not.** *No query fell between 0.15 and 0.5, or below 0.15.*

`CLAUDE.md` §3 is blunt about it:

> `COHERE_MEDIUM` (0.15) is the refuse-vs-answer boundary and has **never been
> exercised by a real query** … it is **unvalidated, not validated**, and that is
> why it must not be tuned casually.

**"Unvalidated, not validated" is a distinction worth carrying.** The constant has
never fired. It has also never *failed*. Absence of failure is not evidence of
correctness, and treating it as such is how a placeholder hardens into a
"measured" value. `KU-003` records it.

---

### 4.7 The retry, and why it is not a ladder

```python
COHERE_RETRY_BACKOFF_S = 1.0

def _cohere_with_retry(fn):
    try:
        return fn()
    except Exception as e:
        logger.warning("Cohere rerank attempt failed (%s: %s) — retrying once in %.1fs", ...)
        time.sleep(COHERE_RETRY_BACKOFF_S)
        return fn()
```

The docstring names the measured event:

> 2026-08-21, **PQ020**: a single `[Errno 111] Connection refused` dropped one row
> of a 20-question sweep to the local ONNX reranker. That is not merely a slower
> path — **it is a DIFFERENT SCORING SCALE.** … PQ020 was then scored `tier=medium`
> against cohere-calibrated thresholds and FAILED its `expected_tier_low`
> assertion. On a clean re-run, cohere-served, it passed. **The failure was an
> artifact of the fallback, not a defect in the answer**, and the mixed backend
> withheld the whole sweep on the reranker integrity gate.

**One refused socket invalidated a 20-question evaluation.**

And the boundary is drawn precisely:

> The ONNX fallback **REMAINS** the second-failure path. It is the correct
> behaviour when Cohere is genuinely down; what it should not be is the response
> to **one refused socket**.

**And why it is a separate constant from the LLM client's:**

> Mirrors `TRANSPORT_RETRY_BACKOFF_S` in `app/llm/client.py` but is **deliberately
> a separate constant** — these are different providers on different links, and
> coupling them would tie one's tuning to the other's.

**Two constants with the same value, kept separate on purpose.** That is the
opposite of the single-registry rule (Day 10) — and it is right, because these are
**not the same fact**. One fact, one copy; two facts that happen to agree, two
constants.

---

## 5. The actual LedgerMind file

```
File:        backend/app/engines/retriever.py — rerank(), _cohere_with_retry,
                                                _get_reranker, _get_cohere_client
Entry point: rerank(query, chunks, top_k=5) -> list[ChunkResult]
Data in:     20 ChunkResult with reranker_score = -inf
Data out:    ≤5 ChunkResult, sorted, with reranker_score AND reranker_backend set
Constants:   RERANKER_MODEL_NAME, COHERE_RETRY_BACKOFF_S = 1.0
Thresholds:  live in semantic_engine.py, not here — this file SCORES,
             semantic_engine INTERPRETS
```

**That split matters.** `retriever.rerank` produces a score and **tags it with its
backend**. It makes no judgement. `semantic_engine._score_confidence` reads both
and interprets. Producing an untagged score would force the interpreter to guess —
which is what BUG-001 was.

---

## 6. Deep walkthrough — `rerank()`

### 6.1 Path A — Cohere

```python
cohere_client = _get_cohere_client()

if cohere_client is not None:
    try:
        doc_texts = [chunk["text"] for chunk in chunks]
        response = _cohere_with_retry(lambda: cohere_client.rerank(
            model="rerank-english-v3.0",
            query=query,
            documents=doc_texts,
            top_n=len(doc_texts),
        ))

        scored_chunks = []
        for hit in response.results:
            updated = dict(chunks[hit.index])
            updated["reranker_score"] = float(hit.relevance_score)
            updated["reranker_backend"] = "cohere"
            scored_chunks.append(ChunkResult(**updated))

        scored_chunks.sort(key=lambda c: c["reranker_score"], reverse=True)
        scored_chunks = _deduplicate_near_identical(scored_chunks)[:top_k]
        return scored_chunks
    except Exception as e:
        logger.error("Cohere API reranking failed (%s) — falling back to local reranker", e)
```

**STATE BEFORE.** 20 `ChunkResult`, `reranker_score = -inf`,
`reranker_backend = "none"`.

**`doc_texts = [chunk["text"] ...]`** — the text is already in hand, from the
Qdrant payload (Day 21). No lookup.

**`top_n=len(doc_texts)` — score ALL candidates, not just 5:**

> Cohere bills per **SEARCH** (not per document), so widening this is free, and
> dedup below needs a pool to backfill from — with `top_n=5`, dropping a
> near-duplicate left **4 chunks instead of swapping in the 6th-best.**

A billing detail turned into a correctness property.

**`hit.index`** — Cohere returns results by *index into the input list*, not by
content. `chunks[hit.index]` is how the score gets back to its chunk. Reorder the
input between the call and the read and you silently mis-assign every score.

**`dict(chunks[...])` then `ChunkResult(**updated)`** — copy, mutate the copy,
rebuild. `ChunkResult` is a `TypedDict` (Day 10), so this is dict manipulation
with a typed name.

**`reranker_backend = "cohere"` set at the point of scoring.** Not inferred later.
That is the whole fix: **the instrument tags its own output.**

**`sort(...)` — defensively**, and the comment says why:

> Cohere returns results sorted by relevance; sort defensively so
> `_deduplicate_near_identical`'s "keep first = keep best" holds regardless of
> provider ordering.

Day 29's suppression *depends* on sorted input. A provider changing its ordering
would break it silently. One line closes that.

**STATE AFTER.** ≤5 chunks, `reranker_score` on Cohere's scale,
`reranker_backend = "cohere"`.

---

### 6.2 Path B — local ONNX

```python
reranker = _get_reranker()
pairs = [(query, chunk["text"]) for chunk in chunks]
scores = list(reranker.rerank_pairs(pairs))

scored_chunks = []
for chunk, score in zip(chunks, scores):
    updated = dict(chunk)
    updated["reranker_score"] = float(score)
    updated["reranker_backend"] = "local"
    scored_chunks.append(ChunkResult(**updated))

scored_chunks.sort(key=lambda c: c["reranker_score"], reverse=True)
top_chunks = _deduplicate_near_identical(scored_chunks)[:top_k]
```

**`pairs = [(query, text), ...]`** — the cross-encoder's input format, and the
visible difference from a bi-encoder (Day 20's `_embed_dense(texts)` takes texts
alone).

**`_get_reranker()`** — the lazy singleton. **On a Cohere-configured system this
line may never execute in the process's lifetime**, so the model's RAM is never
spent (Day 12). Lazy loading is what makes having a fallback affordable.

**`zip(chunks, scores)`** — positional, because `rerank_pairs` returns scores in
input order. Same coupling as `hit.index`, different mechanism.

**`reranker_backend = "local"`.** Same discipline, other path.

---

### 6.3 The three-state fallback of `_get_cohere_client`

Day 12 covered this; here is what it means for *scores*:

| Condition | Returns | Score scale you get |
|---|---|---|
| No `COHERE_API_KEY` | `None`, **silently** | ONNX logits, consistently |
| Key set, package missing | `None`, **ERROR** | ONNX logits, and a misconfiguration |
| Key set, construction failed | `None`, **ERROR** | ONNX logits |
| Client OK, call fails twice | falls through | **ONNX logits — mid-session** |

**The last row is the dangerous one.** The first three are stable for the process:
every query gets the same scale. The fourth means **one query in a sweep gets a
different scale from its neighbours** — which is PQ020, and is why the eval has a
**reranker integrity gate**.

---

### 6.4 Where `reranker_backend` goes

```
retriever.rerank()
  └─ sets reranker_backend on EVERY chunk, at the point of scoring
       │
       ├──► semantic_engine._score_confidence()          (Day 29)
       │      backend = chunks[0].get("reranker_backend", "local")
       │      → selects the threshold pair
       │      → default "local" is a SAFETY default
       │
       ├──► api/response_shaping._reranker_backend()     (Day 9)
       │      → chunks[0].get("reranker_backend")
       │      → returns None when nothing was reranked
       │      → ADMIN ONLY, on the wire
       │
       └──► scripts/cohere_score_dump.py
              → has a HARD ABORT for exactly this mistake
```

**And the reason it is on the wire**, from `response_shaping.py`:

> This is not hypothetical. Cohere is primary with local ONNX as an automatic
> fallback on API failure, and on **2026-08-02** that fallback fired mid-session
> from WSL2 network flap (raw socket connects to `api.cohere.com` succeeded **5 of
> 8** attempts, failing at random). The same query returned `tier=medium` on one
> run and `tier=high` on another purely because a different backend scored it.
> Reading `-3.39` as a Cohere score rather than an ONNX logit then produced a
> **wrong conclusion about threshold calibration that reached this repo's
> documentation** before it was caught.

**A wrong conclusion reached the documentation.** The field exists so that never
happens again — and note that the *same value* is defaulted in one consumer
(safety) and refused in the other (observation). Day 9's distinction, in its
original habitat.

---

## 7. Data flow

```
20 ChunkResult   reranker_score = -inf   reranker_backend = "none"
        │
        ▼  _get_cohere_client()
        │
   ┌────┴─────────────────────────────────┐
   │ COHERE_API_KEY set and client built? │
   └────┬───────────────────────┬─────────┘
        │ YES                   │ NO (silent — a supported configuration)
        ▼                       │
  _cohere_with_retry(...)       │
    attempt 1                   │
      ├─ ok ──────────┐         │
      └─ fail         │         │
         sleep 1.0s   │         │
         attempt 2    │         │
           ├─ ok ─────┤         │
           └─ fail ───┼─────────┤  logger.error, fall through
                      │         │
                      ▼         ▼
        scores ∈ [0,1]      reranker.rerank_pairs([(q, text), ...])
        backend="cohere"    scores ≈ [-12, +2]  backend="local"
                      │         │
                      └────┬────┘
                           ▼
                   sort by score, DESC          (defensive on both paths)
                           ▼
                _deduplicate_near_identical()               (Day 29)
                           ▼
                        [:top_k]  → 5 chunks
                           ▼
        ┌──────────────────┴───────────────────┐
        ▼                                      ▼
 _score_confidence()                  role_filtered_response()
 picks a THRESHOLD PAIR                admin sees reranker_backend
 by backend                            → the score becomes interpretable
```

---

## 8. Engineering decision — cloud primary, local fallback, tagged scores

**Problem.** Rerank 20 candidates precisely, inside 512 MB, with a fallback that
does not silently corrupt confidence.

**Decision.** Cohere primary (0 MB), local ONNX fallback, **`reranker_backend`
recorded on every chunk**, two threshold pairs downstream.

`ENGINEERING_DECISIONS.md` **ED-004**.

| Alternative | Why not |
|---|---|
| **Local only** | Model resident in every process; competes with fastembed and the API inside 512 MB |
| **Cohere only** | A Cohere outage becomes a total retrieval-quality outage |
| **No reranking** | RRF ordering is approximate; the top-5 would be noticeably worse |
| **Normalise both to a common scale** | Requires a mapping between an unbounded logit distribution and `[0,1]`, calibrated per model — more assumptions, not fewer |
| **Only ever compare rankings, never absolute scores** | Principled, and gives up the refuse-vs-answer boundary that `COHERE_MEDIUM` provides |

**Trade-offs accepted.**

- **Two scales exist, permanently.** The mitigation is tagging, not unification.
- **A mid-session swap makes one query non-comparable to its neighbours** — hence
  the eval integrity gate.
- **`COHERE_MEDIUM = 0.15` is unvalidated** (`KU-003`).
- **A network hop on the request path**, bounded by the retry and the fallback.

**Current validity.** Strong. The design's real achievement is not the fallback —
it is that **the fallback cannot lie about itself**.

**At 10×.** The hop and the per-search billing both scale with query volume rather
than corpus size. The structural risk is unchanged: any *third* scoring source
would need its own threshold pair, and the code makes that obvious rather than
tempting you to reuse one.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| **Same query, two different tiers** | The backend swapped between runs. **Check `reranker_backend` first** |
| Everything scores `high` | One threshold pair against both scales — **BUG-001** |
| A sweep withheld on the reranker gate | Mixed backends within one run — PQ020 |
| `-3.39` read as a Cohere score | Missing backend context. This reached the documentation once |
| Dedup drops a chunk and leaves 4 | `top_n` set to 5 instead of all — no pool to backfill |
| Scores assigned to the wrong chunks | `hit.index` / `zip` order broken |
| Local reranker loaded unexpectedly | Cohere failed twice, or the key is unset |
| Suppression keeps the wrong duplicate | Input not sorted — the defensive `sort` prevents it |

---

## 10. Hands-on experiment

### Experiment 1 — the two scales, side by side

```bash
docker compose exec -T backend python -c "
import os
from app.engines.retriever import hybrid_search, rerank, _get_cohere_client
cands = hybrid_search(query='ETERNAL FY26 consolidated impairment and one-off charges',
                      tenant_id=os.getenv('T',''), companies=['ETERNAL'], top_k=10)
print('candidates:', len(cands))
print('cohere configured:', _get_cohere_client() is not None)
print()
out = rerank(query='impairment and one-off charges', chunks=cands, top_k=5)
for c in out:
    print(f\"  {c['reranker_score']:9.4f}  [{c['reranker_backend']}]  p{c['page_number']}  {c['text'][:52]!r}\")
"
```

Note the magnitude **and** the tag. One without the other is uninterpretable.

### Experiment 2 — force the local path, same query

```bash
docker compose exec -T -e COHERE_API_KEY= backend python -c "
import os
from app.engines.retriever import hybrid_search, rerank, _get_cohere_client
print('cohere configured:', _get_cohere_client() is not None, '<- key removed')
cands = hybrid_search(query='ETERNAL FY26 consolidated impairment and one-off charges',
                      tenant_id=os.getenv('T',''), companies=['ETERNAL'], top_k=10)
out = rerank(query='impairment and one-off charges', chunks=cands, top_k=5)
for c in out:
    print(f\"  {c['reranker_score']:9.4f}  [{c['reranker_backend']}]  p{c['page_number']}\")
"
```

**Compare with Experiment 1.** Same query, same candidates, **completely different
numbers**. Similar ordering, incomparable magnitudes.

### Experiment 3 — reproduce BUG-001

```bash
docker compose exec -T backend python -c "
LOCAL_HIGH, LOCAL_MED = -4.5, -7.5
cohere_scores = [0.92, 0.65, 0.47, 0.15, 0.03, 0.001]
print('Applying the LOCAL thresholds to COHERE scores:')
for s in cohere_scores:
    tier = 'high' if s >= LOCAL_HIGH else ('medium' if s >= LOCAL_MED else 'low')
    print(f'  cohere {s:6.3f}  ->  {tier}')
print()
print('EVERY possible Cohere score is >= -4.5.')
print('The tier was structurally \"high\" on the Cohere path. That is BUG-001.')
print()
from app.engines.semantic_engine import (COHERE_HIGH_CONFIDENCE_THRESHOLD as CH,
                                         COHERE_MEDIUM_CONFIDENCE_THRESHOLD as CM)
print('With the correct pair (%.2f / %.2f):' % (CH, CM))
for s in cohere_scores:
    tier = 'high' if s >= CH else ('medium' if s >= CM else 'low')
    print(f'  cohere {s:6.3f}  ->  {tier}')
"
```

### Experiment 4 — the safety default

```bash
docker compose exec -T backend python -c "
from app.engines.semantic_engine import _score_confidence
def chunk(score, backend):
    return {'reranker_score': score, 'reranker_backend': backend, 'text':'x'}
for score, backend in [(0.92,'cohere'), (0.92,'local'), (-3.39,'local'), (-3.39,'cohere')]:
    s, t = _score_confidence([chunk(score, backend)])
    print(f'  score={score:7.3f} backend={backend:7} -> tier={t:7} normalised={s}')
print()
print('The SAME number means different things. -3.39 is a fine ONNX logit and')
print('an impossible Cohere relevance.')
"
```

### Experiment 5 — the retry, and why it is not a ladder

```bash
docker compose exec -T backend python -c "
import time
from app.engines.retriever import _cohere_with_retry, COHERE_RETRY_BACKOFF_S
print('COHERE_RETRY_BACKOFF_S =', COHERE_RETRY_BACKOFF_S)
calls = {'n': 0}
def flaky():
    calls['n'] += 1
    if calls['n'] == 1: raise ConnectionRefusedError('[Errno 111] Connection refused')
    return 'ok'
t = time.perf_counter(); r = _cohere_with_retry(flaky)
print(f'transient failure -> {r!r} after {calls[\"n\"]} calls, {time.perf_counter()-t:.1f}s')
calls['n'] = 0
def always_fails():
    calls['n'] += 1; raise ConnectionRefusedError('[Errno 111]')
try: _cohere_with_retry(always_fails)
except Exception as e: print(f'persistent failure -> raised after {calls[\"n\"]} calls  <- NOT a ladder')
"
```

### Experiment 6 — the backend on the wire

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"query":"What risks does Eternal disclose in Q4 FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('reranker_backend :', d.get('reranker_backend'))
print('confidence_tier  :', d.get('confidence_tier'))
print('top citation     :', d['citations'][0].get('reranker_score'))
print()
print('cohere -> [0,1], thresholds 0.5 / 0.15')
print('local  -> logits ~[-12,+2], thresholds -4.5 / -7.5')
print()
print('Now a QUANTITATIVE query:')
"

curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"query":"What was Eternal revenue in FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('reranker_backend :', d.get('reranker_backend'), '<- None: nothing was reranked')
print('  Reporting \"local\" here would be an ASSUMPTION dressed as an observation.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/retriever.py::rerank` and
`backend/app/engines/semantic_engine.py` (lines 30–95):

1. Where exactly is `reranker_backend` set, and why *there* rather than inferred
   later?
2. `top_n=len(doc_texts)`. Find the comment. Name the billing fact and the
   correctness consequence.
3. Both paths call `sort()` before dedup. Why is that not redundant on the Cohere
   path?
4. `_score_confidence` defaults `reranker_backend` to `"local"`;
   `_reranker_backend` in `response_shaping.py` returns `None`. Same value, two
   behaviours. Justify both.
5. Find the `COHERE_MEDIUM` calibration comment. Which of the two thresholds is
   validated, and what exactly is the evidence for the other?

---

## 12. Self-check questions

**Basic**
1. Bi-encoder vs cross-encoder — one sentence each.
2. Why can a cross-encoder not do first-pass retrieval?
3. Which two rerankers, and which is primary?
4. What are the two score ranges?
5. What are the two threshold pairs?

**Code**
6. What does `top_n=len(doc_texts)` achieve?
7. How does a Cohere score get back to its chunk?
8. What is `pairs` on the local path?
9. Where is `reranker_backend` first set?
10. How many retries does `_cohere_with_retry` make?

**Why**
11. Why two rerankers?
12. Why does the same query sometimes return different tiers?
13. Why does `reranker_backend` reach admins on the wire?
14. Why does `_score_confidence` default to `"local"`?
15. Why is `COHERE_RETRY_BACKOFF_S` separate from the LLM client's constant?

**Debugging**
16. Every semantic query returns `high`. What is wrong?
17. A sweep is withheld on the reranker integrity gate. Is that a defect in the
    answers?
18. An analyst reports `reranker_score: -3.39` and concludes the thresholds are
    miscalibrated. What did they miss?

**System design**
19. Add a third reranker. What must be built alongside it?
20. `COHERE_MEDIUM = 0.15` has never fired. Design a way to validate it, and say
    why it is hard.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Inside `rerank()`, **at the moment the score is produced** —
   `updated["reranker_backend"] = "cohere"` in the Cohere loop and `"local"` in
   the ONNX loop. There, because the scoring code is the only place that *knows*
   with certainty. Inferring it later would mean reconstructing which path ran —
   from the environment, or from the score's magnitude — and both are guesses. The
   instrument tags its own output; that is the entire fix for BUG-001.
2. **Billing fact:** Cohere charges per **search**, not per document, so scoring
   20 costs the same as scoring 5. **Correctness consequence:** near-duplicate
   suppression needs a pool to backfill from — with `top_n=5`, dropping one
   duplicate leaves **4 chunks instead of promoting the 6th-best**. A pricing
   detail converted into a quality property.
3. Because `_deduplicate_near_identical`'s rule is *"keep the first occurrence"*,
   which is only *"keep the best occurrence"* if the input is sorted. Cohere
   currently returns results sorted by relevance — but that is a **provider
   behaviour, not a contract**. If it changed, suppression would silently keep the
   worse of each duplicate pair and nothing would raise. One line removes the
   dependency on someone else's undocumented ordering.
4. **`_score_confidence` defaults to `"local"`** because it is making a *safety*
   choice: the local thresholds are stricter, so an untagged score is interpreted
   conservatively and cannot inflate confidence. **`_reranker_backend` returns
   `None`** because it is making an *observation* to report to a human — and, as
   the comment says, *"reporting an assumption as an observation is how this went
   wrong in the first place."* `None` on a quantitative query is correct: nothing
   was reranked.
5. **`0.5` (HIGH) is validated:** 83 golden questions, every "high" scored ≥ 0.88,
   and the one genuine "medium" (Q031) scored 0.4656 — correctly below.
   **`0.15` (MEDIUM) is not.** The evidence for it is *nothing*: "No query in this
   run fell between 0.15-0.5 or below 0.15." It has never been exercised, so it is
   **unvalidated, not validated** — and it happens to be the refuse-vs-answer
   boundary. `KU-003`.

### §12 — Basic

1. **Bi-encoder:** encodes query and document independently, compares vectors —
   fast, pre-computable, less precise. **Cross-encoder:** processes query and
   document *together*, emits one relevance score — slower, not pre-computable,
   more precise.
2. Nothing can be pre-computed: every query would require running the model
   against every document in real time.
3. **Cohere `rerank-english-v3.0`** (primary, 0 MB RAM) and
   **`Xenova/ms-marco-MiniLM-L-6-v2`** ONNX (fallback, in-process).
4. Cohere: `[0, 1]` relevance. ONNX: raw logits, roughly `[-12, +2]`, mostly
   negative.
5. `COHERE_HIGH = 0.5` / `COHERE_MEDIUM = 0.15`; `LOCAL_HIGH = -4.5` /
   `LOCAL_MEDIUM = -7.5`.

### §12 — Code

6. All 20 candidates are scored rather than 5 — free under per-search billing, and
   it gives dedup a pool to backfill from.
7. `hit.index` — an index into the input `documents` list. `chunks[hit.index]`
   maps it back. Positional coupling: reordering the input between call and read
   would mis-assign every score.
8. `[(query, chunk["text"]) for chunk in chunks]` — the cross-encoder's joint
   input format, and the visible difference from a bi-encoder.
9. In `rerank()`, at the point of scoring, on each path.
10. **One.** One attempt plus exactly one retry, after a fixed 1.0 s backoff.
    Never a ladder.

### §12 — Why

11. Memory. Cohere costs **0 MB** locally, which matters inside a 512 MB ceiling;
    the local ONNX model exists so the system still functions when Cohere does
    not. Lazy loading means the fallback's RAM is never spent unless it is used.
12. Because the **backend swapped**. Cohere and ONNX produce incompatible scales,
    and before the two-pair fix, a Cohere-served query was classified `high`
    unconditionally while an ONNX-served one was scored correctly. Post-fix the
    tiers are right, but the *scores* still differ, and a mid-sweep swap makes one
    row non-comparable to its neighbours.
13. Because it **changes what the number beside it means**. On 2026-08-02 the
    fallback fired mid-session from WSL2 network flap (5 of 8 socket connects
    succeeded), the same query returned different tiers across runs, and reading
    `-3.39` as a Cohere score produced a wrong conclusion about calibration **that
    reached this repository's documentation**.
14. It is a **safety default**: the local thresholds are stricter, so an untagged
    score cannot inflate confidence.
15. Because they are **different providers on different links**, and coupling them
    would tie one's tuning to the other's. Two constants that happen to share a
    value are not one fact — the single-source rule applies to *facts*, not to
    coincidences.

### §12 — Debugging

16. **BUG-001**: one threshold pair applied to both scales. Every Cohere
    `relevance_score` is ≥ `-4.5`, so the local thresholds classify every
    Cohere-served query as `high` regardless of relevance. Check whether
    `_score_confidence` still branches on `reranker_backend`.
17. **Not necessarily.** PQ020's documented case was a **single refused socket**
    dropping one row to the ONNX reranker; that row was then scored against
    Cohere-calibrated thresholds and failed an assertion, and on a clean re-run it
    passed. The failure was **an artifact of the fallback, not a defect in the
    answer**. The gate withholds the *sweep* because mixed backends make the run
    non-comparable — it is not a verdict on the answers.
18. **`reranker_backend`.** `-3.39` is a plausible ONNX logit and an impossible
    Cohere relevance, so the interpretation depends entirely on which model
    produced it. It is on the admin response for exactly this reason, and
    `scripts/cohere_score_dump.py` has a hard abort for the same mistake.

### §12 — System design

19. **(a)** A third threshold pair with its own empirical min/max, and a branch in
    `_score_confidence` — never a reuse of an existing pair. **(b)** A distinct
    `reranker_backend` value set at the point of scoring. **(c)** A **calibration
    run** over the golden set to place the thresholds, with the evidence recorded
    beside the constants as the Cohere block does — including honestly stating
    which boundary is unexercised. **(d)** Its own retry/fallback constant, not a
    shared one. **(e)** The eval's reranker integrity gate must recognise it, or a
    mixed run would pass unnoticed. **(f)** `response_shaping._reranker_backend`
    needs nothing — it reports whatever is tagged, which is the point of the
    design. The code makes all of this obvious rather than tempting reuse, and
    that is the design working.
20. **How to validate:** you need a query whose top Cohere relevance genuinely
    lands in `0.15–0.5` — a *weak but non-empty* match. Candidate sources: scan the
    existing `docs/measurements/cohere_*.json` dumps for near-band scores; or
    construct queries that are on-topic for the corpus but ask about something
    genuinely absent (a metric no filing discusses, a period not held), which is
    the profile most likely to score weakly rather than at zero. Then confirm the
    tier and the resulting behaviour — CRAG retry, then refusal — match intent.
    **Why it is hard:** the band is unpopulated *because* retrieval is doing its
    job — filtered, hybrid, reranked candidates are usually either clearly
    relevant (≥ 0.88) or clearly not. You cannot synthesise a band-landing score
    without weakening retrieval, which changes the thing you are measuring. That
    is why `KU-003` records it as open with a stated verification method rather
    than as a task, and why `CLAUDE.md` §3 forbids tuning it casually: **an
    unexercised constant has neither succeeded nor failed.**

---

## 14. MUST REMEMBER

```text
- A NUMBER IS MEANINGLESS WITHOUT KNOWING WHICH INSTRUMENT PRODUCED IT
- Bi-encoder: independent, pre-computable, fast. Cross-encoder: joint, precise,
  cannot pre-compute — so it can only rerank a shortlist
- Cohere rerank-english-v3.0 (primary, 0 MB) → [0, 1]
- Xenova/ms-marco-MiniLM-L-6-v2 ONNX (fallback) → logits ~[-12, +2]
- TWO threshold pairs: COHERE 0.5/0.15, LOCAL -4.5/-7.5
- BUG-001: one pair, two scales → every Cohere score >= -4.5 → always "high"
- reranker_backend is set AT THE POINT OF SCORING, never inferred
- _score_confidence defaults to "local" (SAFETY); _reranker_backend returns
  None (OBSERVATION)
- top_n = ALL candidates: billing is per search, and dedup needs a pool
- One retry, never a ladder. COHERE_RETRY_BACKOFF_S is deliberately separate
- COHERE_MEDIUM = 0.15 has NEVER FIRED — unvalidated, not validated
```

## 15. MUST UNDERSTAND

```text
- Why the textbook's advice is correct for ONE model and insufficient for two
  that swap silently at runtime
- Why BUG-001 was structural, not a tuning error: EVERY Cohere score cleared
  the local threshold
- Why an intermittent, run-to-run symptom is the signature of an environment
  difference, not a code defect
- Why the fallback's real achievement is that IT CANNOT LIE ABOUT ITSELF
- The difference between a SAFETY default and an OBSERVATIONAL default, and why
  the same value takes both treatments in one codebase
- Why "unvalidated" and "validated" are different states, and why absence of
  failure is not evidence
```

---

## 16. This connects to

```text
Day 27 — RRF fusion, a third scale
   ↓
Day 28 — reranking, and two more scales           ← THE KEYSTONE OF PHASE 7
   ↓
Day 29 — dedup, confidence scoring, and the CRAG ladder
```

Forward references:

- `_deduplicate_near_identical`, called from both paths → **Day 29**
- `_score_confidence` reading `reranker_backend` → **Day 29**
- `reranker_backend` on the admin response → **Day 9** (already read)
- The eval's reranker integrity gate → **Day 43**
- `KU-003` — validating `COHERE_MEDIUM` → **Day 43**
- WSL2 network flap and the pre-flight check → **Day 44**
