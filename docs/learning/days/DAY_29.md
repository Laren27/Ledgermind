# Day 29 — Dedup, Confidence Scoring, and the CRAG Ladder

**Phase 7 · Weight: H (~120 min) · Prerequisites: Day 28**

**Textbook: 10.x "Corrective RAG" — EXTENDS.**

---

## 1. Today's goal

By tonight you can:

- Explain near-duplicate suppression: the threshold, why the denominator is the
  **smaller** chunk, and why this — not less overlap — was the fix.
- Explain `_score_confidence`: normalisation, the gap bonus, and the **loud
  error** that fires on unscored chunks.
- Explain CRAG as a **filter ladder**, and the `break`-vs-`continue` bug that
  silently removed it from every annual query.
- Explain what `crag_count` actually counts.
- Explain why measured constants in this codebase are frozen.

---

## 2. Why now

Day 28 produced scored, tagged chunks. Today is everything between scoring and
answering: removing redundancy, deciding whether the evidence is good enough, and
what to do when it is not. This closes Phase 7.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Two score scales, two threshold pairs | Day 28 | `_score_confidence` branches on them |
| `reranker_score = -inf` sentinel | Days 25, 27 | Today's loud error |
| `OVERLAP_TOKENS = 150` and its side-effect | Day 24 | Today is the fix |
| `_build_filter`'s conditions | Day 27 | CRAG drops them one at a time |

---

## 4. Concept lesson

### 4.1 The problem overlap created

Day 24: adjacent chunks share ~600 characters **by design**, and that overlap is
load-bearing — it stopped Paytm's PPBL impairment fact being orphaned.

The side-effect, measured live 2026-07-30:

> chunks `0b035c3c…` and `387d1a8c…` are both page 23, both exactly 705 chars,
> offset by ~90 chars, **87.8% token overlap**. They consumed **2 of 5 slots** with
> identical forward-looking-statements boilerplate, while the management
> commentary chunk that actually addressed the question sat at rank 2. An earlier
> scroll showed **page 9 appearing four times in one top-10.**

**Two of five slots to one passage.** The LLM receives five chunks (Day 17), so
40% of its evidence was a duplicate of another 20%.

**And the framing that decides the fix:**

> The bug is **not** that overlapping chunks exist — it is that **two windows over
> the same text can both occupy final top-5 slots.**

Reducing overlap would fix the symptom and reintroduce the orphaning. **Fix the
symptom where the symptom occurs**: at ranking.

---

### 4.2 Token-set containment, not cosine

```python
NEAR_DUPLICATE_THRESHOLD = 0.70

def _token_overlap(a: str, b: str) -> float:
    """Jaccard-style containment: |A∩B| / min(|A|,|B|)."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))
```

**Why not embedding cosine?**

> Deliberately token-set overlap, not embedding cosine: **the text is already in
> hand**, and a second model invocation to answer what a set intersection answers
> is cost for nothing.

Two arguments: the text is present (Day 21's payload decision), and cosine would
answer a *different* question. Two chunks can be semantically similar without
being the same text — and semantic similarity is what retrieval is *for*. What
you want to detect here is **literal redundancy**.

**Why `min(|A|, |B|)` and not `|A ∪ B|`?**

> Denominator is the **SMALLER** chunk, so a short chunk fully contained in a
> longer one scores **1.0** rather than being diluted by the longer one's length.

Work it through. A 100-token chunk entirely inside a 700-token chunk:

| Denominator | Score | Verdict |
|---|---|---|
| union (true Jaccard) | 100/700 = **0.14** | "not duplicates" — **wrong** |
| min | 100/100 = **1.00** | "one contains the other" — **right** |

True Jaccard measures *mutual* similarity. Containment measures *"does this add
anything?"* — which is the actual question when deciding whether a chunk earns a
slot.

**And `0.70` is a starting point, honestly labelled:**

> Logged at INFO with the real ratio so the eval sweep yields the actual overlap
> distribution — the threshold above is a starting point **calibrated on ONE
> measured pair**, not a settled constant.

The code **instruments its own uncertainty**: every suppression logs its real
ratio, so the distribution accumulates and the constant can eventually be set from
data rather than from one observation.

---

### 4.3 The sorted-input contract

```python
def _deduplicate_near_identical(chunks, threshold=NEAR_DUPLICATE_THRESHOLD):
    """
    MUST be called on a list already sorted by reranker_score descending --
    "keep the first occurrence" is only "keep the best occurrence" if the
    input is sorted. Both call sites sort immediately before.

    O(n^2) over ~20 candidates is ~190 set intersections; negligible against
    a network rerank call.
    """
    kept = []
    for chunk in chunks:
        duplicate_of = None
        for existing in kept:
            ratio = _token_overlap(chunk["text"], existing["text"])
            if ratio >= threshold:
                duplicate_of = (existing, ratio)
                break
        if duplicate_of is None:
            kept.append(chunk)
        else:
            existing, ratio = duplicate_of
            logger.info("Near-duplicate suppressed | page=%s score=%.4f overlap=%.1f%% "
                        "with page=%s score=%.4f", ...)
    return kept
```

**The precondition is stated, and enforced at both call sites** (Day 28's
defensive `sort()`). A function with a precondition its callers must uphold is
fragile; stating it in the docstring **and** satisfying it at every call site is
the mitigation.

**O(n²) is justified rather than ignored** — 20 candidates, ~190 set
intersections, against a network call. Naming the cost is what makes it a
decision.

**Every suppression is logged with its real ratio**, at INFO. The log line names
both pages and both scores, so you can reconstruct exactly what was dropped and
what it duplicated.

---

### 4.4 `_score_confidence`, and the loud error

```python
def _score_confidence(chunks) -> Tuple[float, str]:
    if not chunks:
        return 0.0, "low"

    top_score = chunks[0]["reranker_score"]

    if chunks[0].get("reranker_backend") == "none" or top_score == float("-inf"):
        logger.error(
            "Unscored chunks reached _score_confidence (backend=%s score=%s) — "
            "rerank() did not run. This is a bug, not a low-confidence result.",
            chunks[0].get("reranker_backend"), top_score,
        )
        return 0.0, "low"
```

**This check exists because of `DISABLE_LOCAL_RERANKER`** (Day 27):

> Comparing `-inf` against ANY threshold yields "low", so this would **silently
> look like a legitimate refusal rather than a broken pipeline.** Fail loudly
> instead — this is a code defect, not a retrieval outcome.

**The sentinels worked exactly as designed** — `-inf` propagated faithfully. What
was missing was a **check that distinguishes "we scored this and it was bad" from
"we never scored this"**. Both produce `low`; only one is a bug.

**Then the scale selection** (Day 28):

```python
backend = chunks[0].get("reranker_backend", "local")   # safety default
if backend == "cohere":
    high_threshold, medium_threshold = COHERE_HIGH, COHERE_MEDIUM
    EMPIRICAL_MIN, EMPIRICAL_MAX = 0.0, 1.0
else:
    high_threshold, medium_threshold = LOCAL_HIGH, LOCAL_MEDIUM
    EMPIRICAL_MIN, EMPIRICAL_MAX = -12.0, -2.0
```

**Normalisation, for the audit log only:**

```python
normalised = (top_score - EMPIRICAL_MIN) / (EMPIRICAL_MAX - EMPIRICAL_MIN)
normalised = max(0.0, min(1.0, normalised))

gap = abs(top_score - bottom_score) if len(chunks) > 1 else 0.0
gap_bonus = min(0.05, gap * 0.005)      # max 5% bonus
final_score = min(1.0, normalised + gap_bonus)
```

**The gap bonus** rewards separation: if the top chunk is clearly better than the
rest, that is a stronger signal than five chunks scoring alike. Capped at 0.05
so it can nudge but never move a tier.

**And the crucial separation:**

```python
# Tier decision based on raw top score (not normalised)
if top_score >= high_threshold:   tier = "high"
elif top_score >= medium_threshold: tier = "medium"
else:                              tier = "low"
```

**The tier comes from the RAW score. The normalised score is for the audit log
only.** The docstring says so: *"The normalised score is for the audit log — the
tier drives routing logic."*

Why that matters: normalisation depends on `EMPIRICAL_MIN`/`MAX`, which are
estimates. Letting an estimate decide routing would make a refusal depend on a
guessed range. The raw comparison depends only on the calibrated thresholds.

---

### 4.5 CRAG as a filter ladder

Textbook 10.x describes Corrective RAG as re-retrieving when confidence is low —
typically by **rewriting the query**.

**LedgerMind does something different: it drops filters, one at a time.**

```python
def _broaden_retrieval(query, tenant_id, companies, fiscal_year, quarter,
                       financial_type, crag_count):
    """
    Retry 1 (crag_count=1): drop quarter constraint
    Retry 2 (crag_count=2): drop quarter AND fiscal_year constraints

    The most common cause of LOW/MEDIUM retrieval on a small corpus is
    over-specific metadata filters excluding relevant chunks.
    """
```

**Why filters and not the query.** Because the diagnosis is different. The
textbook assumes the *query* is the problem. Here the diagnosis is that the
**metadata filter is too narrow** — the router extracted `Q4` from a question
whose answer lives in the annual section, so the right chunk was excluded before
ranking ever happened. Rewriting the query cannot recover a chunk the filter
removed.

It is also **cheaper**: no LLM call, no second embedding. Two more Qdrant queries
at ~0.4 s each.

**The rung ordering is deliberate:** `quarter` first (most specific, most likely
wrong), then `fiscal_year`. **`company` is never dropped** — that would be F2 by
another route (Day 27).

---

### 4.6 The no-op rung, and the `break`/`continue` bug

```python
if crag_count == 1 and quarter is None:
    logger.info("CRAG retry 1 skipped: quarter filter was already unset")
    return None
if crag_count == 2 and fiscal_year is None:
    logger.info("CRAG retry 2 skipped: fiscal_year filter was already unset")
    return None
```

**Why `None` rather than an empty list.** Because "dropping a filter that was
never set" re-issues the **identical** query. Confirmed live 2026-07-29:

> a query with no period extracted (`fiscal_year=None`, `quarter=None`) ran
> **three retrievals returning byte-identical reranker scores** (0.1364/0.0633)
> before refusing.

`None` signals *"this rung was a no-op"* so the caller can act on it.

**And here is the bug that makes this day worth a full session:**

```python
if broadened is None:
    # This RUNG was a no-op (the filter it drops was already unset) —
    # advance to the next rung rather than abandoning the ladder.
    # Original bug: this used `break`, so any query with quarter=None
    # (i.e. every annual query) skipped rung 2 as well, which drops
    # fiscal_year and is real broadening. That silently removed CRAG
    # recovery from most semantic queries. crag_count is the RUNG
    # INDEX reached, not the number of retrievals actually performed.
    logger.info("CRAG rung %d was a no-op — advancing to next rung", crag_count)
    continue
```

**Trace it.** A question about an annual figure has `quarter = None`.

| | With `break` (the bug) | With `continue` (the fix) |
|---|---|---|
| Rung 1 | no-op → **abandon the ladder** | no-op → advance |
| Rung 2 | never reached | drops `fiscal_year` — **real broadening** |
| Result | refuse | may recover |

**Every annual query lost its recovery path**, and nothing raised. `crag_triggered`
was even set to `True` — so the audit log recorded a CRAG attempt that never
happened.

**And the definition the comment insists on:**

> `crag_count` is the **RUNG INDEX reached**, not the number of retrievals
> actually performed.

Those differ whenever a rung is a no-op. `crag_count = 2` can mean two retrievals
or one.

---

### 4.7 The loop, and the MEDIUM policy

```python
while confidence_tier in ("low", "medium") and crag_count < MAX_CRAG_RETRIES:
    if confidence_tier == "medium" and crag_count >= 1:
        logger.info("CRAG: MEDIUM after retry %d — accepting with disclaimer", crag_count)
        break
    crag_count += 1
    state["crag_triggered"] = True
    state["crag_count"] = crag_count
    broadened = _broaden_retrieval(..., crag_count=crag_count)
    if broadened is None:
        continue
    chunks = broadened
    confidence_score, confidence_tier = _score_confidence(chunks)
```

**Two different policies:**

- **LOW** → retry both rungs. Nothing usable was found.
- **MEDIUM** → retry **once**, then accept. Something usable was found, and
  broadening further risks trading a mediocre-but-relevant answer for a
  wider-but-noisier one.

**Then the refusal:**

```python
if confidence_tier == "low" or len(chunks) < MIN_CHUNKS_FOR_ANSWER:
    state["confidence_tier"] = "low"
    state["retrieved_chunks"] = []
    state["citations"] = []
    state["response_text"] = ("Insufficient information found ...")
    state["error"] = "low_confidence_refusal"
    state["error_node"] = "semantic_engine"
    return state
```

**`retrieved_chunks` and `citations` are cleared.** A refusal must not ship
evidence — otherwise the UI would render citations beside "we could not find
anything", which is worse than either alone.

---

### 4.8 Why the constants are frozen

`CLAUDE.md` §3:

> **Measured constants.** Do not modify: `COHERE_HIGH` (0.5), `COHERE_MEDIUM`
> (0.15), near-duplicate threshold (**0.70**), alias coverage floor (0.5),
> `OVERLAP_TOKENS` (150), `BATCH_SIZE` (8). **Each encodes a measurement that is
> not derivable from the code.** Propose and stop.

**"Not derivable from the code" is the criterion.** You cannot read
`_token_overlap` and work out that 0.70 is right. It came from observing a
specific pair at 87.8%. The number is **evidence compressed into a literal**, and
changing it discards the evidence.

Which is why each one carries its measurement in a comment beside it. The comment
is not documentation of the code — it is **the data the code was fitted to**.

---

## 5. The actual LedgerMind files

```
File:  backend/app/engines/retriever.py — _token_overlap,
                                          _deduplicate_near_identical
       NEAR_DUPLICATE_THRESHOLD = 0.70

File:  backend/app/engines/semantic_engine.py (388 lines)
Entry: semantic_engine_node(state) -> QueryState
       _score_confidence(chunks) -> (score, tier)
       _broaden_retrieval(...) -> list | None       ← None means "no-op rung"
       _build_citations(chunks) -> list[Citation]
Consts: LOCAL_HIGH -4.5 / LOCAL_MEDIUM -7.5
        COHERE_HIGH 0.5 / COHERE_MEDIUM 0.15
        MIN_CHUNKS_FOR_ANSWER = 1 · MAX_CRAG_RETRIES = 2
Note:  "This module does NOT call Gemini." Generation is Day 30.
```

---

## 6. Deep walkthrough — `semantic_engine_node`

**STATE BEFORE.** `QueryState` after the router: `resolved_query`, `companies`,
`fiscal_year`, `quarter`, `financial_type`.

**Step 1 — initial retrieval.**

```python
query = state.get("resolved_query") or state["query"]
chunks = retrieve_and_rerank(query=query, tenant_id=..., companies=..., ...)
confidence_score, confidence_tier = _score_confidence(chunks)
crag_count = 0
```

`resolved_query` with a fallback to `query` — Day 25's entity prefix, defensively.

**Step 2 — the ladder.** §4.7.

**Step 3 — refuse or proceed.**

**Step 4 — citations.**

```python
def _build_citations(chunks) -> List[Citation]:
    citations.append(Citation(
        chunk_id=..., doc_id=..., page_number=..., company=...,
        fiscal_year=..., financial_type=..., filing_date=...,
        reranker_score=chunk["reranker_score"],
        text_preview=chunk["text"][:200].strip(),
    ))
```

The 16-field `ChunkResult` narrows to a 9-field `Citation` (Day 10) —
`text_preview` at 200 characters, *"enough for a snippet, not the full chunk"*.

**Step 5 — write state.**

```python
state["retrieved_chunks"] = list(chunks)
state["citations"]        = citations
state["confidence_score"] = confidence_score
state["confidence_tier"]  = confidence_tier
state["crag_count"]       = crag_count
```

`list(chunks)` — a shallow copy, so the state holds its own list object
(Day 6's `accumulated.update(partial)` relies on values being replaced, not
mutated).

**STATE AFTER.** Up to five chunks, matching citations, a tier, and a CRAG count.
**No answer text** — that is Day 30.

---

### 6.1 The removed citation floor — read this before touching anything nearby

```python
# CITATION_RELEVANCE_FLOOR REMOVED 2026-08-08. Do not reintroduce without
# reading this.
#
# The floor dropped sub-0.05 chunks from `citations` while leaving them in
# `retrieved_chunks`, on the documented premise that "a weak chunk in the
# model's context is harmless and occasionally useful; the defect is presenting
# it as evidence". THAT PREMISE IS FALSE, and the counterexample is a live
# answer.
#
#   Citation floor: dropped 4 of 5 below 0.05 |
#     scores=[0.0419, 0.0219, 0.0165, 0.0094] pages=[31, 4, 19, 4]
#
# Page 19 at 0.0165 is ZOMATO FY24 AR p19, "Warehouse capacity # million square
# feet ... 4.8 ... Mar-24". The generated answer stated "warehousing capacity
# was 4.8 million square feet in FY24" and carried ONE citation -- a transcript
# page containing no such figure. The number was real, correctly extracted, and
# UNTRACEABLE. Deterministic across two runs, at confidence_score 0.9969.
#
# THE 0.05 CONSTANT WAS NOT WRONG. ... What was wrong is that
# `retrieved_chunks` and `citations` were allowed to diverge at all. The floor
# did not prevent an unsupported claim; it guaranteed the claim could not be
# checked.
```

**The lesson generalises well beyond this codebase.** The measurement behind the
constant was sound — two score clusters with an empty band between them. The
**design** built on it was wrong: allowing the model's context and the user's
evidence list to differ means the answer can cite something it did not use, and
use something it did not cite.

**And the rejected alternative is recorded too:**

> Rejected alternative: apply the floor to `retrieved_chunks` too. It closes the
> hole by narrowing what the model reads on EVERY semantic and cross query —
> altering retrieval to fix an evidence-list problem, with a blast radius far
> larger than the defect. **Three of the five chunks behind the Hyperpure answer
> scored below 0.05.**

**The noise it was built to suppress is real**, and the conclusion is that it is a
**display-weight** problem: *"render a 0.0165 citation differently, do not hide
it."*

---

## 7. Data flow

```
20 candidates (RRF-ranked)                                  Day 27
        ▼  rerank()                                          Day 28
20 scored, tagged with reranker_backend
        ▼  sort DESC  (defensive, both paths)
        ▼  _deduplicate_near_identical(threshold=0.70)
        │     containment = |A∩B| / min(|A|,|B|)
        │     keep first (= best, because sorted)
        │     log every suppression with its real ratio
        ▼  [:5]
5 chunks
        ▼  _score_confidence()
        ├─ backend == "none" or score == -inf?
        │     └─► logger.error("This is a bug, not a low-confidence result")
        ├─ select threshold pair by backend  ("local" is the SAFETY default)
        ├─ normalise → for the AUDIT LOG only
        ├─ gap bonus, capped at 0.05
        └─ tier from the RAW score
        ▼
   ┌────┴────────────────────────────────┐
   │ tier == high                        │──► proceed
   │ tier == medium, crag_count >= 1     │──► accept with disclaimer
   │ tier in (low, medium), rungs remain │──► CRAG
   └────┬────────────────────────────────┘
        ▼
  _broaden_retrieval(crag_count)
    rung 1: drop quarter      ──► None if quarter was already unset → CONTINUE
    rung 2: drop fiscal_year  ──► None if fiscal_year already unset → CONTINUE
    company is NEVER dropped
        ▼  re-score
   ┌────┴────────────────────────┐
   │ still low after both rungs  │──► REFUSE
   │                             │    retrieved_chunks = []
   │                             │    citations = []
   │                             │    error = "low_confidence_refusal"
   └────┬────────────────────────┘
        ▼
  _build_citations()   ChunkResult(16) → Citation(9), text_preview[:200]
        ▼
   QueryState: retrieved_chunks · citations · confidence_* · crag_count
        ▼
        Day 30 — response_generator
```

---

## 8. Engineering decision — suppress duplicates, ladder the filters

**Problem.** Overlap wastes context slots; over-specific filters exclude the right
chunk; and both failures look like "we could not find anything".

**Decision.** Token-set containment suppression at 0.70 after reranking, plus a
two-rung filter-dropping ladder, plus a loud error on unscored chunks.

`ENGINEERING_DECISIONS.md` **ED-005** (suppression over less overlap),
**ED-015** (CRAG as a filter ladder).

| Alternative | Why not |
|---|---|
| **Reduce `OVERLAP_TOKENS`** | Reintroduces the orphaned-PPBL failure. Fix the symptom where it occurs |
| **Embedding cosine for dedup** | A second model call to answer what a set intersection answers — and it measures a different thing |
| **True Jaccard (union denominator)** | A short chunk inside a long one scores 0.14 and survives |
| **Query rewriting for CRAG** (textbook) | An LLM call per retry against 500/day — and the diagnosis here is filters, not phrasing |
| **Drop the company filter as a rung** | That is F2 by another route |
| **Unlimited rungs** | Each is a retrieval; the ladder must terminate |

**Trade-offs accepted.**

- **0.70 is calibrated on one pair** — instrumented, not yet re-derived.
- **O(n²)**, justified at 20 candidates and stated.
- **A precondition (sorted input)** upheld by callers, not by the function.
- **`MIN_CHUNKS_FOR_ANSWER = 1`** — a single chunk can support an answer, which
  is permissive.
- **`COHERE_MEDIUM = 0.15` has never fired** (`KU-003`, Day 28), so the
  refuse-vs-answer boundary on the primary path is unexercised.

**Current validity.** Sound, with two open calibration questions.

**At 10×.** O(n²) is fine at 20 and would need attention at 200. The ladder's
assumption — that over-specific filters are the common failure — is a property of
a *small* corpus; on a large one the common failure becomes genuine absence, and
broadening would return noise rather than the missing chunk.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| Two near-identical chunks in the answer | Threshold too high, or input not sorted |
| A distinct chunk suppressed | Threshold too low, or genuinely repetitive boilerplate |
| `Unscored chunks reached _score_confidence` | **A bug.** `rerank()` did not run |
| Every semantic query refuses | Unscored chunks + no error check — the historic `DISABLE_LOCAL_RERANKER` failure |
| Three identical retrievals then a refusal | Historic: no-op rungs re-issuing the same query |
| Annual queries never recover | Historic: `break` instead of `continue` |
| A figure in the answer with no citation | Historic: the citation floor |
| `crag_count = 2` but only one retrieval | **Correct.** It is the rung index |
| Tier changes run to run | `reranker_backend` swapped (Day 28) |

---

## 10. Hands-on experiment

### Experiment 1 — containment vs Jaccard

```bash
docker compose exec -T backend python -c "
from app.engines.retriever import _token_overlap, NEAR_DUPLICATE_THRESHOLD
print('threshold:', NEAR_DUPLICATE_THRESHOLD)
long_ = 'the company reported revenue from operations of INR 54,364 crore for the year ended March 31 2026 driven by quick commerce'
short = 'revenue from operations of INR 54,364 crore'
other = 'the auditor issued an unmodified opinion on the consolidated financial statements'
def jaccard(a,b):
    sa,sb=set(a.split()),set(b.split()); return len(sa&sb)/len(sa|sb)
for label,a,b in [('short INSIDE long', short, long_), ('unrelated', long_, other)]:
    print(f'  {label:20} containment={_token_overlap(a,b):.3f}  jaccard={jaccard(a,b):.3f}')
print()
print('Containment says the short chunk ADDS NOTHING. Jaccard says they differ.')
print('Containment is the right question for \"does this earn a slot?\"')
"
```

### Experiment 2 — suppression on real chunks

```bash
docker compose exec -T backend python -c "
import logging, os
logging.basicConfig(level=logging.INFO, force=True)
from app.engines.retriever import hybrid_search, rerank
c = hybrid_search(query='ETERNAL FY26 forward looking statements and risk factors',
                  tenant_id=os.getenv('T',''), companies=['ETERNAL'], top_k=20)
print('candidates:', len(c))
out = rerank(query='forward looking statements and risk factors', chunks=c, top_k=5)
print('after rerank + dedup:', len(out))
for x in out:
    print(f\"  {x['reranker_score']:8.4f} [{x['reranker_backend']}] p{x['page_number']}\")
print()
print('Look above for any \"Near-duplicate suppressed\" INFO lines and their ratios.')
"
```

### Experiment 3 — the loud error

```bash
docker compose exec -T backend python -c "
import logging; logging.basicConfig(level=logging.INFO, force=True)
from app.engines.semantic_engine import _score_confidence
unscored = [{'reranker_score': float('-inf'), 'reranker_backend': 'none', 'text': 'x'}]
print('scoring an UNSCORED chunk:')
print(' ->', _score_confidence(unscored))
print()
print('It returns (0.0, low) AND logs an ERROR saying it is a bug.')
print('Without that error this is indistinguishable from a real refusal.')
"
```

### Experiment 4 — tier from raw, score from normalised

```bash
docker compose exec -T backend python -c "
from app.engines.semantic_engine import _score_confidence
def ch(s, b='cohere'): return {'reranker_score': s, 'reranker_backend': b, 'text': 'x'*100}
print('cohere scale:')
for s in (0.95, 0.52, 0.49, 0.20, 0.14, 0.01):
    sc, t = _score_confidence([ch(s)])
    print(f'  raw={s:5.2f}  normalised={sc:6.4f}  tier={t}')
print()
print('local scale:')
for s in (-2.0, -4.4, -4.6, -7.4, -7.6, -11.0):
    sc, t = _score_confidence([ch(s,'local')])
    print(f'  raw={s:6.2f}  normalised={sc:6.4f}  tier={t}')
print()
print('The TIER comes from the RAW score. Normalised is for the audit log.')
"
```

### Experiment 5 — the gap bonus

```bash
docker compose exec -T backend python -c "
from app.engines.semantic_engine import _score_confidence
def ch(s): return {'reranker_score': s, 'reranker_backend':'cohere', 'text':'x'*100}
tight  = [ch(0.90), ch(0.88), ch(0.87)]
spread = [ch(0.90), ch(0.30), ch(0.05)]
print('tight cluster :', _score_confidence(tight))
print('wide spread   :', _score_confidence(spread))
print()
print('Same top score. The spread earns a small bonus — capped at 0.05 so it')
print('can never move a tier.')
"
```

### Experiment 6 — the no-op rung

```bash
docker compose exec -T backend python -c "
import logging, os; logging.basicConfig(level=logging.INFO, force=True)
from app.engines.semantic_engine import _broaden_retrieval
r = _broaden_retrieval(query='revenue drivers', tenant_id=os.getenv('T',''),
                       companies=['ETERNAL'], fiscal_year='FY26', quarter=None,
                       financial_type='consolidated', crag_count=1)
print('rung 1 with quarter already None ->', r, ' <- None means NO-OP')
print()
print('The caller must CONTINUE here, not BREAK. With break, every annual query')
print('(quarter=None) also skipped rung 2, which drops fiscal_year and is REAL')
print('broadening. That silently removed CRAG from most semantic queries.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/retriever.py` (dedup block) and
`backend/app/engines/semantic_engine.py`:

1. Why is the denominator `min(|A|, |B|)`? Work an example where union gives the
   wrong answer.
2. `_deduplicate_near_identical` has a precondition. What is it, who upholds it,
   and what breaks if they stop?
3. `_score_confidence` logs an ERROR on unscored chunks and still returns
   `(0.0, "low")`. Why both?
4. `_broaden_retrieval` returns `None` for a no-op rung. What did the caller
   originally do with that, and what was the consequence?
5. Read the removed-citation-floor comment. The measurement was correct — what
   was wrong, and what is the rejected alternative?

---

## 12. Self-check questions

**Basic**
1. What is the near-duplicate threshold?
2. What does the denominator being `min` achieve?
3. What are `MAX_CRAG_RETRIES` and `MIN_CHUNKS_FOR_ANSWER`?
4. What does CRAG drop, and in what order?
5. What does `crag_count` count?

**Code**
6. What must be true of `_deduplicate_near_identical`'s input?
7. What triggers the loud error in `_score_confidence`?
8. What is the gap bonus and its cap?
9. What does `_broaden_retrieval` return for a no-op rung?
10. What does the refusal path do to `retrieved_chunks` and `citations`?

**Why**
11. Why suppression rather than less overlap?
12. Why token-set overlap rather than embedding cosine?
13. Why is the tier from the raw score and not the normalised one?
14. Why does MEDIUM get one retry and LOW two?
15. Why is `company` never dropped as a rung?

**Debugging**
16. Every semantic query refuses at `confidence_score = 0.0`, no error visible.
    What class of bug, and what should you find in the logs?
17. An annual query refuses without ever broadening. What is wrong?
18. An answer states a figure with no citation supporting it. Which fix, and what
    was the premise it disproved?

**System design**
19. `0.70` was calibrated on one pair. Design its re-derivation from data the
    system already logs.
20. The CRAG ladder assumes over-specific filters are the common failure. State
    when that stops being true, and what you would replace it with.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Because the question is *"does this chunk add anything?"*, not *"are these
   mutually similar?"* **Example:** a 100-token chunk entirely contained in a
   700-token chunk. Union denominator → 100/700 = **0.14**, below any sensible
   threshold, so the redundant chunk **survives and takes a slot**. Min
   denominator → 100/100 = **1.00**, correctly identified as adding nothing.
2. **Precondition:** the input must be sorted by `reranker_score` descending,
   because "keep the first occurrence" is only "keep the *best* occurrence" if it
   is. **Upheld by:** both call sites in `rerank()`, which sort immediately before
   — including the Cohere path, defensively, even though Cohere already returns
   sorted results. **If they stop:** suppression keeps the *worse* member of each
   duplicate pair, silently, and nothing raises.
3. **The error** because an unscored chunk means `rerank()` did not run — a code
   defect, and without the log it is indistinguishable from a legitimate
   low-confidence refusal. **Still returns `(0.0, "low")`** because the function
   must return *something* and refusing is the safe outcome; raising would turn a
   pipeline bug into a 500 for the user, when a refusal plus a logged error gives
   the user a sane response and the operator the diagnosis.
4. It used **`break`** — abandoning the whole ladder. Consequence: any query with
   `quarter=None` — **every annual query** — never reached rung 2, which drops
   `fiscal_year` and is real broadening. CRAG recovery was silently removed from
   most semantic queries, while `crag_triggered=True` was still written, so the
   audit log recorded an attempt that never happened.
5. **What was wrong:** allowing `retrieved_chunks` and `citations` to **diverge at
   all**. The floor did not prevent an unsupported claim; it guaranteed the claim
   **could not be checked** — an answer stating "4.8 million square feet" from a
   chunk scoring 0.0165 that had been removed from the citations, carrying one
   citation to a page containing no such figure. **Rejected alternative:** apply
   the floor to `retrieved_chunks` too — rejected because it narrows what the
   model reads on *every* semantic and cross query to fix an evidence-list
   problem, and three of the five chunks behind the Hyperpure answer scored below
   0.05. The real fix is display weight: *render a 0.0165 citation differently, do
   not hide it.*

### §12 — Basic

1. `NEAR_DUPLICATE_THRESHOLD = 0.70`.
2. A short chunk fully contained in a longer one scores 1.0 instead of being
   diluted by the longer one's length.
3. `MAX_CRAG_RETRIES = 2`; `MIN_CHUNKS_FOR_ANSWER = 1`.
4. Metadata filters: **rung 1 drops `quarter`**, **rung 2 drops `quarter` and
   `fiscal_year`**. `company` is never dropped.
5. The **rung index reached**, not the number of retrievals performed. They differ
   whenever a rung is a no-op.

### §12 — Code

6. Sorted by `reranker_score` descending.
7. `reranker_backend == "none"` **or** `reranker_score == float("-inf")`.
8. `min(0.05, gap * 0.005)` where `gap = |top − bottom|` — a maximum 5% bonus,
   small enough never to move a tier.
9. `None`.
10. Sets both to `[]`, so a refusal ships no evidence.

### §12 — Why

11. Because overlap is **load-bearing** — it is what stopped the PPBL fact being
    orphaned. The defect was not that duplicates exist but that two windows over
    the same text both reached the final five, which is a ranking problem and is
    fixed at ranking.
12. Because the text is already in hand (the Qdrant payload), so a second model
    call would be cost for nothing — **and** because cosine measures semantic
    similarity, which is what retrieval is *for*. What must be detected here is
    **literal redundancy**, which a set intersection answers exactly.
13. Because normalisation depends on `EMPIRICAL_MIN`/`MAX`, which are **estimates**.
    Letting an estimate decide routing would make a refusal depend on a guessed
    range; the raw comparison depends only on the calibrated thresholds. The
    normalised value exists for the audit log.
14. **LOW** means nothing usable was found, so both rungs are worth spending.
    **MEDIUM** means something usable *was* found; broadening further risks
    trading a mediocre-but-relevant answer for a wider, noisier set — so it
    retries once and then accepts with a disclaimer.
15. Because dropping it would produce an **unfiltered whole-tenant search** — F2
    by another route (Day 27). The reranker cannot rescue that, because a
    competitor's chunk is genuinely on-topic.

### §12 — Debugging

16. **Unscored chunks reaching `_score_confidence`** — `reranker_score = -inf`
    compares below every threshold, so everything is `low`. **In the logs you
    should find** `Unscored chunks reached _score_confidence (backend=none
    score=-inf) — rerank() did not run. This is a bug, not a low-confidence
    result.` If that line is absent, the check has been removed — which is exactly
    the state the `DISABLE_LOCAL_RERANKER` incident occurred in, where nothing
    surfaced an error and `confidence_score` simply read `0.0`.
17. The caller **`break`s** on a no-op rung instead of **`continue`ing**. An annual
    query has `quarter=None`, so rung 1 is a no-op; with `break` the ladder is
    abandoned before rung 2, which drops `fiscal_year` and is the rung that would
    have helped.
18. The **citation relevance floor** (removed 2026-08-08). It dropped sub-0.05
    chunks from `citations` while leaving them in `retrieved_chunks`, so the model
    read a chunk the user could not see. **The premise it disproved:** *"a weak
    chunk in the model's context is harmless and occasionally useful; the defect
    is presenting it as evidence."* The counterexample was a real, correctly
    extracted figure — 4.8 million square feet — that became **untraceable**.

### §12 — System design

19. The data already exists: every suppression logs
    `Near-duplicate suppressed | page=… score=… overlap=…% with page=… score=…` at
    INFO, and the comment says this is deliberate — *"so the eval sweep yields the
    actual overlap distribution."* **Procedure:** run a full eval sweep (with
    approval, `--delay 45`), collect every logged ratio, and plot the
    distribution. If it is bimodal — a cluster near 1.0 for true duplicates and a
    cluster well below for distinct chunks — set the threshold in the empty band,
    exactly as the Cohere thresholds were placed. **The harder half:** the log
    only records pairs that were **suppressed**, i.e. those already above 0.70, so
    the sweep is censored at the threshold. To see the full distribution you would
    need to log *every* computed ratio, not just the suppressing ones — a one-line
    change, and the reason this has not already been done from existing data.
    Then record the measurement beside the constant, as every other frozen
    constant does.
20. **It stops being true when the corpus is large enough that the right chunk is
    usually present but out-ranked, rather than filtered out.** On a five-document
    corpus, LOW confidence usually means "the filter excluded it"; on a
    ten-thousand-document corpus it usually means "we retrieved 20 things and none
    was the right one", and broadening the filter returns *more* noise, not the
    missing chunk — it can even lower confidence by admitting worse candidates.
    **What I would replace it with:** the textbook's actual CRAG — query rewriting
    or multi-query expansion — because at that scale the diagnosis shifts from
    *filters* to *phrasing*, plus a larger `TOP_K_RETRIEVAL` so reranking has more
    to work with. The signal for switching is measurable: log, on each CRAG
    recovery, whether the newly-recovered chunk was **excluded by a filter** or
    merely **out-ranked**. While the former dominates, the ladder is right.
    Note the current design already refuses to drop `company`, so the ladder can
    never broaden into the failure F2 describes — any replacement must preserve
    that.

---

## 14. MUST REMEMBER

```text
- NEAR_DUPLICATE_THRESHOLD = 0.70. FROZEN. Calibrated on ONE measured pair
- Containment = |A∩B| / min(|A|,|B|) — the SMALLER chunk is the denominator
- Dedup requires SORTED input; both call sites sort immediately before
- _score_confidence logs an ERROR on -inf / backend="none": a BUG, not a refusal
- The TIER comes from the RAW score. Normalised is for the audit log only
- Gap bonus is capped at 0.05 — it can nudge, never move a tier
- CRAG drops FILTERS, not queries: rung 1 quarter, rung 2 fiscal_year
- company is NEVER dropped
- A no-op rung returns None and the caller must CONTINUE, never BREAK
- crag_count is the RUNG INDEX, not the retrieval count
- A refusal clears retrieved_chunks AND citations
- The citation floor was REMOVED 2026-08-08. Do not reintroduce it
```

## 15. MUST UNDERSTAND

```text
- Why the fix for overlap's side-effect belongs at RANKING, not at chunking
- Why containment answers "does this add anything?" and Jaccard does not
- Why a correct measurement can support a wrong DESIGN — the citation floor
- Why letting retrieved_chunks and citations diverge guarantees a claim cannot
  be checked
- Why break-vs-continue silently removed recovery from every annual query, and
  why crag_triggered=True made the audit log lie about it
- Why a constant that "encodes a measurement not derivable from the code" must
  be proposed, not changed
```

---

## 16. This connects to

```text
Day 28 — reranking and two score scales
   ↓
Day 29 — dedup, confidence, and CRAG              ← END OF PHASE 7
   ↓
Day 30 — the complete semantic path: prompt, synthesis, citations
```

Forward references:

- `_format_chunks_for_prompt` and synthesis → **Day 30**
- The citation floor's display-weight fix, in the UI → **Day 40**
- `confidence_node`'s cross-cutting caps → **Day 30**
- `KU-003` — validating `COHERE_MEDIUM` → **Day 43**
- The eval's confidence-tier assertions → **Day 43**
