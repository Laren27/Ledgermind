# Day 24 — Chunking and Embedding

**Phase 6 · Weight: H (~120 min) · Prerequisites: Days 20, 23**

**Textbook: Part 4 (all strategies) — EXTENDS · 4.6 parent-child — DIVERGES
(not built) · 15B "The Chunk Size Trap" — CONFIRMS · 15B "Embedding Batch Size
Memory Crash" — CONFIRMS.**

---

## 1. Today's goal

By tonight you can:

- Explain why chunking exists, and the size trade-off in both directions.
- Explain LedgerMind's **per-block-type** targets and why tables are never split.
- Explain `OVERLAP_TOKENS = 150`: what it fixed, why it is frozen, and why the
  fix for its side-effect was *suppression* rather than reduction.
- Explain **deterministic chunk IDs** and the exact condition under which they
  stop helping.
- Explain **speaker-turn chunking** — and why it is a correctness fix for
  contradiction detection, not a formatting nicety.

---

## 2. Why now

Day 22 produced blocks; Day 23 labelled them. Today they become the units that
get embedded (Day 20) and stored (Day 21). This closes Phase 6: after today the
whole ingest path is known, and Days 25–29 query what it built.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `block_type` labels | Day 23 | The chunker branches on them |
| `Chunk`, `ChunkMetadata`, `EmbeddedChunk` | Day 10 | The output types |
| `chunk_id` is the Qdrant point ID | Day 21 | Why determinism matters |
| Context window is a ceiling | Day 17 | Why chunks are small |

---

## 4. Concept lesson

### 4.1 Why chunk at all

Two independent reasons (textbook 4.1):

**The hard limit.** Embedding models have input limits. A 200-page annual report
cannot become one vector.

**The precision problem, which matters more.** Even if it fit, one vector for
fifty pages must represent everything at once. A question about one paragraph
retrieves the whole document — and Day 17 established that irrelevant context
degrades the answer.

> Chunking solves both problems: it keeps each piece within technical limits, and
> it allows retrieval to be **surgical rather than blunt**.

**The trade-off, in both directions** (textbook 4.2):

| Smaller chunks | Larger chunks |
|---|---|
| More precise retrieval | More surrounding context preserved |
| Risk losing context around a sentence | Less precise retrieval |
| More vectors: storage, embedding cost | Risk exceeding model limits |

**There is no universally correct size.** LedgerMind's answer is *there is no
single size at all* — the target depends on what kind of content the block holds.

---

### 4.2 Per-block-type targets

```python
CHARS_PER_TOKEN = 4

TARGET_TOKENS = {
    BlockType.TEXT:                  200,
    BlockType.RISK_DISCLOSURE:       250,
    BlockType.MANAGEMENT_DISCUSSION: 200,
    BlockType.FINANCIAL_STATEMENT:   None,   # ← never split
    BlockType.TABLE:                 None,   # ← never split
    BlockType.UNKNOWN:               200,
}
```

**Day 23's labels are what make this possible.** Without classification, every
block would get one target.

**Why `None` for tables.** A financial statement table is a **grid**: row labels
on the left, period columns across. Split it and you get half a table with no
header, or a header with no rows. Either fragment is worse than useless — it
retrieves on numeric queries and carries no interpretable structure.

The docstring states it as a rule: *"FINANCIAL_STATEMENT / TABLE → whole block =
one chunk (**never split tables**)"*.

**Why 250 for risk disclosures.** Risk factors are self-contained paragraphs
("Risk: our business depends on… Mitigation: …"). A slightly larger window keeps
a risk with its mitigation.

**`CHARS_PER_TOKEN = 4`** — Day 17's approximation. The target is a budget, not a
hard limit, and running a real tokeniser at every candidate split would cost far
more than the precision is worth.

---

### 4.3 Overlap: what it fixed, and what it broke

```python
OVERLAP_TOKENS = 150
OVERLAP_CHARS  = OVERLAP_TOKENS * CHARS_PER_TOKEN   # 600
```

**The problem overlap solves** (textbook, "Chunking" error entry): a sentence
split exactly at a boundary leaves both chunks holding a broken fragment.

**What raising it from 50 to 150 fixed**, from `retriever.py`'s dedup comment:

> `OVERLAP_TOKENS=150` in `chunker.py` … was raised from 50 specifically to fix a
> mid-sentence split that **orphaned Paytm's PPBL impairment fact** in an
> unretrieved chunk.

A real fact, in the corpus, unreachable — because it sat across a boundary.

**And what it broke.** 150 tokens of overlap means adjacent chunks share ~600
characters. Measured live 2026-07-30:

> chunks `0b035c3c…` and `387d1a8c…` are both page 23, both exactly 705 chars,
> offset by ~90 chars, **87.8% token overlap**. They consumed **2 of 5 slots**
> with identical forward-looking-statements boilerplate, while the management
> commentary chunk that actually addressed the question sat at rank 2.

**The fix was NOT to reduce overlap.** From the same comment:

> The bug is not that overlapping chunks exist — it is that **two windows over the
> same text can both occupy final top-5 slots.**

So the fix is **near-duplicate suppression at rerank time** (Day 29), not smaller
overlap. Overlap is load-bearing; the symptom is fixed where the symptom occurs.

**And the constant is frozen.** `CLAUDE.md` §3 lists `OVERLAP_TOKENS` among the
measured constants: *"Each encodes a measurement that is not derivable from the
code. Propose and stop."*

**The textbook agrees, from the other direction** (15B, "The Chunk Size Trap"):

> Chunk size is a **system-wide constant**. Changing it requires rebuilding the
> entire index from scratch. **Treat it like a database schema migration — not a
> config parameter.**

---

### 4.4 Recursive splitting

```python
SPLIT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
MIN_CHUNK_CHARS = 50
```

Try separators **in order of semantic strength**: paragraph break, line break,
sentence end, word break, and finally raw characters.

`_recursive_split` accumulates parts until adding the next would exceed
`max_chars`, emits the chunk, then **seeds the next chunk with the overlap tail**:

```python
overlap_text = current[-overlap_chars:] if overlap_chars else ""
current = overlap_text + (sep if overlap_text else "") + part
```

If any resulting chunk is still too large, it recurses with the **remaining**
separators:

```python
remaining_separators = separators[separators.index(sep) + 1:]
```

The `""` separator is the base case — a hard character window that always
terminates.

**No LangChain.** The docstring: *"No LangChain dependency — recursive splitter
implemented in ~30 lines."* Consistent with the rest of this codebase: a
dependency must earn itself.

---

### 4.5 Deterministic chunk IDs

```python
def _make_chunk_id(doc_id, page_number, position, text) -> str:
    fingerprint = f"{doc_id}:{page_number}:{position}:{text[:100]}"
    return str(uuid.UUID(hashlib.md5(fingerprint.encode()).hexdigest()))
```

Four components, each doing a job:

| Component | Job |
|---|---|
| `doc_id` | scopes to this document *version* |
| `page_number` | scopes to the page |
| `position` | index within the page's splits |
| `text[:100]` | content fingerprint — guards against position collisions |

**What it buys:** re-ingest the same PDF → same IDs → Qdrant `upsert` overwrites
in place. No duplicates, no delete-first step (Day 21).

**And the exact condition under which it stops helping**, from
`IMPLEMENTATION_DELTAS.md` §D:

> Orphaned vector rows — Qdrant has no purge, and **deterministic IDs only help
> while boundaries hold.**

Change `TARGET_TOKENS` or `OVERLAP_TOKENS` and every split moves — so `position`
and `text[:100]` change, so **every ID changes**. The old points are not
overwritten; they remain, orphaned, and still retrievable. That is `CAVEAT-016`,
and it is why §4.3's "treat it like a schema migration" is literal.

**`md5` here is not a security choice.** It is a fast, stable hash for identity.
The security-relevant hash in this system is `sha256` on document content
(Day 23).

---

### 4.6 Speaker-turn chunking — a correctness fix

**This is the most interesting thing in the file**, and its comment is worth
reading in full:

> A transcript's natural unit is the **speaker turn**, not an arbitrary character
> window. Measured 2026-08-08 on ETERNAL Q4FY26: the generic TEXT path produced
> **187 chunks from 17 pages**, and **chunk 92 opened mid-sentence on an ANALYST'S
> premise** ("your advertising promotion cost … was flat sequentially") **with no
> attribution**, while carrying a different speaker's name later in the same
> chunk. That is the **false-contradiction generator for Path 3**: an analyst's
> assertion, unowned, reads as a company claim — and in this document several
> such premises are **DENIED by management in the next turn** (inventory days p10,
> A&P flat p9, orders per customer p8).

**Follow the failure chain, because it crosses three subsystems:**

```
generic character-window chunking
        ↓
a chunk opens mid-sentence on an analyst's premise, with no attribution
        ↓
retrieval returns it as evidence
        ↓
contradiction.py reads a numeric claim in narrative text     (Day 37)
        ↓
compares it against the SQL-verified figure
        ↓
they disagree — because the analyst's premise was WRONG,
and management corrected it in the next turn
        ↓
the system reports a HIGH-SEVERITY CONTRADICTION
        ↓
which INVERTS its own stated value: "a false contradiction is worse
than a missed one"
```

**A chunking decision, three layers away, produces a false claim about a
company.**

**The pattern, and how it was validated:**

```python
SPEAKER_LINE_RE = re.compile(...)   # line-initial, ≤5 capitalised words, then ":"
TRANSCRIPT_DOC_TYPE = "earnings_transcript"
ROSTER_ANCHOR = "Management representatives:"
MODERATOR_SPEAKER = "Moderator"
TRANSCRIPT_TURN_OVERLAP_CHARS = 0
```

> The pattern is deliberately bounded: line-initial, at most 5 capitalised words
> before the colon. **Validated across all 532 lines of the transcript — 127
> matches, 17 distinct names (3 management + Moderator + 13 analysts), ZERO
> spurious hits inside prose.**

Not "it looks right" — a count, over the whole document, with the false-positive
rate stated.

**`_parse_management_roster`** reads the document's own
`"Management representatives:"` list, so **the roster is parsed from the
document, not hardcoded**. `_classify_speaker` then labels each turn
`management` / `analyst` / `moderator`, and that becomes
`ChunkResult.speaker_role` (Day 3) — which `contradiction.py` uses to decide
whether a chunk is even *eligible* to carry a company claim.

**`TRANSCRIPT_TURN_OVERLAP_CHARS = 0`.** Zero overlap, deliberately: a speaker
turn is a complete unit, and bleeding the previous speaker's words into it would
**reintroduce the exact attribution problem** this exists to fix.

**And the Moderator is treated as a speaker** because "its turns carry the
analyst's name and firm, which is the attribution".

---

## 5. The actual LedgerMind files

```
File:        backend/app/ingestion/chunker.py (687 lines)
Purpose:     Classified PageBlocks → Chunk objects with full metadata
Who imports: ingestion/pipeline.py, regression_check
Entry point: chunk_blocks(blocks, doc_metadata, ...) -> list[Chunk]
Data in:     list[PageBlock] with block_type refined (Day 23)
Data out:    list[Chunk] ready for embedder.py
Deferred:    parent-child chunking — "deferred to Phase 7" (textbook 4.6)

File:        backend/app/ingestion/embedder.py (289 lines)   ← Day 20
```

---

## 6. Deep walkthrough

### 6.1 `chunk_blocks` — the dispatch

```python
def chunk_blocks(blocks, ...) -> list[Chunk]:
    if doc_type == TRANSCRIPT_DOC_TYPE:
        roster = _parse_management_roster(blocks)
        ...  # speaker-turn path
    for block in blocks:
        target = TARGET_TOKENS.get(block.block_type, 200)
        if target is None:
            chunks += _chunk_unsplittable_block(block, ...)   # tables
        else:
            chunks += _chunk_text_block(block, target, ...)
```

**STATE BEFORE.** Classified `PageBlock`s and document metadata (company,
fiscal_year, doc_type, filing_date, tenant_id, doc_id).

**Three paths, chosen by type:**

| Path | Applies to | Behaviour |
|---|---|---|
| Speaker turns | `doc_type == "earnings_transcript"` | Split on `SPEAKER_LINE_RE`, zero overlap |
| Unsplittable | `TARGET_TOKENS[...] is None` | Whole block = one chunk |
| Recursive | everything else | `_recursive_split` with the type's target |

**STATE AFTER.** A flat `list[Chunk]`, each with a deterministic `chunk_id` and a
complete `ChunkMetadata`.

---

### 6.2 `_build_metadata` — every filter's source

```python
def _build_metadata(...) -> ChunkMetadata:
```

Every field a query can filter on (Day 27) is stamped here:

```
tenant_id · doc_id · company · fiscal_year · quarter · financial_type
chunk_type · page_number · filing_date · is_latest · speaker_role · section_label
```

**`SECTION_LABELS`** maps a `block_type` to a human-readable name:

```python
SECTION_LABELS = {
    BlockType.FINANCIAL_STATEMENT:   "Financial Statements",
    BlockType.TABLE:                 "Tables",
    BlockType.RISK_DISCLOSURE:       "Risk Disclosures",
    BlockType.MANAGEMENT_DISCUSSION: "Management Discussion",
    BlockType.TEXT:                  "General",
    BlockType.UNKNOWN:               "General",
}
```

Note `TEXT` and `UNKNOWN` both map to `"General"` — the display label is
deliberately coarser than the internal type, because "we could not classify this"
is not something to show a user as a category.

**And the field with a downstream consequence.** `financial_type` is stamped on
every chunk — including risk disclosures and MD&A, which are **not** scoped to
consolidated or standalone. Those get `"unknown"`.

That is why `retriever._build_filter` has to write:

```python
Filter(should=[
    FieldCondition(key="financial_type", match=MatchValue(value=financial_type)),
    FieldCondition(key="financial_type", match=MatchValue(value="unknown")),
])
```

— an OR that matches the requested type **or** `"unknown"`. And *that* is audit
finding **F7**: because so many chunks are `"unknown"`, the filter is
**functionally inert** for narrative content (Day 27). The cause is here, in
metadata construction.

The frontend also handles it, in `page.tsx`:

```typescript
// financial_type is UNKNOWN for every non-FINANCIAL_STATEMENT chunk by
// design (see section_classifier.py) — risk disclosures and MD&A are not
// scoped to standalone or consolidated. Rendering "(unknown)" reads as a
// classification failure when it is a correct N/A, so the tag is omitted
```

**One metadata decision, visible in three layers.**

---

### 6.3 `_split_speaker_turns` and `_split_long_turn`

```python
def _split_speaker_turns(blocks, roster, ...) -> list[Chunk]:
def _split_long_turn(speaker: str, turn: str, max_chars: int) -> list[str]:
```

A speaker turn is the natural unit — but a CFO's opening remarks can run pages.
`_split_long_turn` splits an over-long turn **while preserving the speaker
label**, so every fragment stays attributed.

**That is the invariant:** never emit a chunk whose speaker is unknown. Splitting
is allowed; losing attribution is not.

---

### 6.4 What is deliberately not built

```python
# Parent-child chunking deferred to Phase 7
```

The textbook (4.6) recommends parent-child for dense documents: retrieve small
chunks for precision, send the **parent** to the LLM for context.

**LedgerMind does not build it**, and `IMPLEMENTATION_DELTAS.md` §B records it as
specified-and-unbuilt rather than quietly dropped.

**What stands in for it:** 150-token overlap (context across boundaries) plus
near-duplicate suppression (Day 29) to stop the overlap wasting slots. Cheaper,
and it does not solve the same problem as well — the textbook's "answer split
across multiple chunks" failure (15B) remains reachable.

**Honest position:** a real gap, recorded, with a partial mitigation.

---

## 7. Data flow

```
list[PageBlock]  (classified — Day 23)
        │
        ▼  chunk_blocks(blocks, doc_metadata)
        │
   doc_type == "earnings_transcript"?
        │
        ├─ YES ─► _parse_management_roster(blocks)     ← from the DOCUMENT
        │         │   "Management representatives:" anchor
        │         ▼
        │    _split_speaker_turns()  SPEAKER_LINE_RE
        │         │   overlap = 0
        │         ├─ turn too long? _split_long_turn() — keeps the speaker
        │         ▼
        │    _classify_speaker() → management | analyst | moderator
        │
        └─ NO ──► TARGET_TOKENS[block_type]
                  ├─ None → _chunk_unsplittable_block()   tables, whole
                  └─ N    → _recursive_split(max_chars=N*4, overlap=600,
                                             separators=["\n\n","\n",". "," ",""])
        │
        ▼  _make_chunk_id(doc_id, page, position, text[:100])
        ▼  _build_metadata(...)
   Chunk(chunk_id, text, metadata)
        │
        ▼  embedder.embed_chunks()   BATCH_SIZE=8        ← Day 20
   EmbeddedChunk(chunk, dense_vector[384], sparse_indices, sparse_values)
        │
        ▼  qdrant_writer.write_chunks()  batches of 100  ← Day 21
   Qdrant point: id=chunk_id, {"dense","sparse"}, payload=metadata+text
```

---

## 8. Engineering decision — per-type chunking with fixed overlap

**Problem.** Split heterogeneous filing content — statement tables, risk
paragraphs, MD&A prose, dialogue — into retrievable units without destroying
meaning.

**Decision.** Per-block-type targets; tables never split; 150-token overlap;
deterministic IDs; a separate speaker-turn path for transcripts.

`ENGINEERING_DECISIONS.md` **ED-005** (near-duplicate suppression instead of
reducing overlap), **ED-017** (deterministic chunk IDs), **ED-020** (speaker-turn
chunking).

| Alternative | Why not |
|---|---|
| **One fixed size for everything** (textbook 4.3) | Splits tables. The single worst outcome available |
| **Semantic chunking** (4.5) | An embedding call per candidate boundary, at ingest, on CPU. Cost with no measured benefit here |
| **Parent-child** (4.6) | The right answer for dense documents. **Not built** — recorded, not hidden |
| **Reduce overlap to stop duplicates** | Would reintroduce the orphaned-PPBL failure. Fix the symptom where it occurs |
| **LangChain's splitter** | ~30 lines of local code versus a dependency |
| **Character windows on transcripts** | Measured to produce unattributed analyst premises — a false-contradiction generator |

**Trade-offs accepted.**

- **Overlap costs top-5 slots**, mitigated by suppression at rerank (Day 29).
- **Changing any chunk parameter invalidates every ID** — a full re-ingest, and
  orphans in Qdrant if you do not clean up (`CAVEAT-016`).
- **`CHARS_PER_TOKEN = 4` is approximate**, so targets are budgets.
- **Speaker-turn logic is transcript-specific** and validated on **one**
  transcript. 127 matches, zero false positives — on 532 lines of one document.

**Current validity.** Sound and well-evidenced. The parent-child gap is the
largest unbuilt item.

**At 10×.** More document *types* (not more documents) is the pressure: each new
type may need its own target and possibly its own splitting path, and the
speaker-turn pattern would need re-validating on transcripts from other issuers.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| A table retrieved as half a grid | Something split an unsplittable block |
| A fact orphaned across a boundary | Overlap too small — the pre-150 failure |
| Two near-identical chunks in top-5 | Overlap working as designed; suppression is the fix (Day 29) |
| Duplicate chunks after re-ingest | A chunk parameter changed → all IDs changed |
| Orphaned Qdrant points | Same cause. **No purge exists** — `CAVEAT-016` |
| A false high-severity contradiction | An unattributed analyst premise — the transcript failure |
| `financial_type` filter matching everything | Audit **F7** — most chunks are `"unknown"` |
| Ingest OOM | `BATCH_SIZE` above 8 |
| The answer is incomplete | Textbook 15B — the answer split across chunks. Parent-child would help; it is not built |

---

## 10. Hands-on experiment

### Experiment 1 — the constants

```bash
docker compose exec -T backend python -c "
import app.ingestion.chunker as ch
print('CHARS_PER_TOKEN :', ch.CHARS_PER_TOKEN)
print('OVERLAP_TOKENS  :', ch.OVERLAP_TOKENS, '->', ch.OVERLAP_CHARS, 'chars   (FROZEN)')
print('MIN_CHUNK_CHARS :', ch.MIN_CHUNK_CHARS)
print('SEPARATORS      :', ch.SPLIT_SEPARATORS)
print()
for k, v in ch.TARGET_TOKENS.items():
    print(f'  {k:22} {\"NEVER SPLIT\" if v is None else str(v)+\" tokens\"}')
"
```

### Experiment 2 — recursive splitting, and the overlap made visible

```bash
docker compose exec -T backend python -c "
from app.ingestion.chunker import _recursive_split
text = ('Revenue from operations grew substantially during the period. '
        'The growth was driven by quick commerce expansion. '
        'Blinkit contributed materially to consolidated revenue. '
        'Management expects the trend to continue into the next fiscal year. ') * 4
out = _recursive_split(text, max_chars=300, overlap_chars=100,
                       separators=['\n\n','\n','. ',' ',''])
print('chunks:', len(out))
for i, c in enumerate(out):
    print(f'  [{i}] {len(c):4d} chars | {c[:58]!r}')
print()
print('overlap check — tail of [0] vs head of [1]:')
print('  tail :', repr(out[0][-60:]))
print('  head :', repr(out[1][:60]))
"
```

### Experiment 3 — deterministic IDs, and when they stop helping

```bash
docker compose exec -T backend python -c "
from app.ingestion.chunker import _make_chunk_id
t = 'Revenue from operations grew to INR 54,364 crore'
print('same inputs twice :', _make_chunk_id('d1',5,0,t) == _make_chunk_id('d1',5,0,t))
print('position 0 -> 1   :', _make_chunk_id('d1',5,0,t) != _make_chunk_id('d1',5,1,t))
print('text changes      :', _make_chunk_id('d1',5,0,t) != _make_chunk_id('d1',5,0,t+' x'))
print()
print('Change TARGET_TOKENS or OVERLAP_TOKENS and BOTH position and text[:100]')
print('move for most chunks -> every id changes -> upsert cannot overwrite ->')
print('the old points remain, orphaned and still retrievable. CAVEAT-016.')
"
```

### Experiment 4 — chunk a real document

```bash
docker compose exec -T backend python -c "
from collections import Counter
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks
from app.ingestion.chunker import chunk_blocks
import inspect

p = '/app/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf'
blocks = parse_pdf(p)                       # parse ONCE
secs = detect_sections(blocks); classify_blocks(blocks, secs)
print('chunk_blocks signature:', inspect.signature(chunk_blocks))
print('block types:', Counter(b.block_type for b in blocks))
"
```

(Read the signature, then call it with the metadata it needs — that discovery is
part of the exercise.)

### Experiment 5 — the speaker pattern, on the real transcript

```bash
docker compose exec -T backend python -c "
import re
from app.ingestion.chunker import SPEAKER_LINE_RE, ROSTER_ANCHOR, MODERATOR_SPEAKER, TRANSCRIPT_TURN_OVERLAP_CHARS
from app.ingestion.pdf_parser import parse_pdf
print('overlap for transcript turns:', TRANSCRIPT_TURN_OVERLAP_CHARS, '<- ZERO, deliberately')
print('roster anchor :', ROSTER_ANCHOR)
print()
blocks = parse_pdf('/app/docs/raw/Q4FY26-earnings-call-transcript.pdf')
lines = [l for b in blocks if b.content for l in b.content.split(chr(10))]
hits = [l for l in lines if SPEAKER_LINE_RE.match(l)]
names = sorted({SPEAKER_LINE_RE.match(l).group(1).strip() for l in hits})
print('lines        :', len(lines))
print('speaker hits :', len(hits))
print('distinct     :', len(names))
for n in names: print('   ', n)
"
```

Compare with the recorded validation: **532 lines, 127 matches, 17 distinct
names, zero spurious hits.**

### Experiment 6 — the roster comes from the document

```bash
docker compose exec -T backend python -c "
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.chunker import _parse_management_roster, _classify_speaker
blocks = parse_pdf('/app/docs/raw/Q4FY26-earnings-call-transcript.pdf')
roster = _parse_management_roster(blocks)
print('roster parsed FROM THE DOCUMENT:', sorted(roster))
print()
for name in list(roster)[:2] + ['Moderator', 'Some Analyst']:
    print(f'  {name:26} -> {_classify_speaker(name, roster)}')
print()
print('This becomes ChunkResult.speaker_role, which contradiction.py uses to')
print('decide whether a chunk is ELIGIBLE to carry a company claim. Day 37.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/ingestion/chunker.py`:

1. `TARGET_TOKENS[FINANCIAL_STATEMENT]` is `None`. What does that mean, and what
   would a split table look like to retrieval?
2. Find the `OVERLAP_TOKENS` history. What did raising it to 150 fix, what did it
   break, and why was the fix *not* to lower it again?
3. `_make_chunk_id` uses four components. What does `text[:100]` guard against
   that the other three do not?
4. Read the speaker-turn comment. Trace the failure from "character-window
   chunking" to "a false high-severity contradiction" — name every layer it
   passes through.
5. `TRANSCRIPT_TURN_OVERLAP_CHARS = 0` while everything else uses 600. Why zero
   here specifically?

---

## 12. Self-check questions

**Basic**
1. Why chunk at all — two reasons.
2. What is `OVERLAP_TOKENS`, and is it tunable?
3. Which block types are never split?
4. What makes a chunk ID deterministic?
5. What is a "speaker turn"?

**Code**
6. What is `CHARS_PER_TOKEN` and why an approximation?
7. What is `SPLIT_SEPARATORS`, and in what order?
8. What does `_split_long_turn` preserve?
9. Where is `speaker_role` set, and who consumes it?
10. Which fields does `_build_metadata` stamp?

**Why**
11. Why per-block-type targets rather than one size?
12. Why never split a table?
13. Why was near-duplicate suppression the fix rather than less overlap?
14. Why zero overlap on transcript turns?
15. Why is parent-child chunking recorded rather than quietly dropped?

**Debugging**
16. After a chunker change, Qdrant's point count nearly doubles. What happened?
17. A false high-severity contradiction appears on a transcript question. Where
    does the cause live?
18. The `financial_type` filter appears to have no effect. Which finding, and
    where was the cause introduced?

**System design**
19. You want to raise `TARGET_TOKENS[TEXT]` from 200 to 400. Write the full
    procedure, including what must be verified afterwards.
20. Parent-child chunking is not built. Design the smallest version that would
    address the textbook's "answer split across chunks" failure without
    invalidating every chunk ID.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **Never split.** The whole block becomes one chunk. A split table would be
   either rows with no header (numbers with no row labels or period columns) or a
   header with no rows — a fragment that still **retrieves on numeric queries**
   while carrying nothing interpretable. Worse than not retrieving at all,
   because it looks like evidence.
2. **Fixed:** a mid-sentence split that orphaned Paytm's PPBL impairment fact in
   an unretrieved chunk — a real fact in the corpus, unreachable. **Broke:**
   adjacent chunks now share ~600 characters, and two windows over the same text
   consumed **2 of 5** final slots with identical boilerplate (measured page 23,
   87.8% overlap) while the relevant chunk sat at rank 2. **Why not lower it:**
   *"The bug is not that overlapping chunks exist — it is that two windows over
   the same text can both occupy final top-5 slots."* Lowering overlap would
   reintroduce the orphaning. The symptom is fixed where the symptom occurs — at
   rerank (Day 29).
3. **Position collisions.** `doc_id`, `page_number` and `position` describe
   *where* a chunk sits; if the same position were reached with different content
   — because upstream splitting changed, or two blocks on a page were processed in
   a different order — the first three components would collide while the text
   differs. `text[:100]` makes the ID depend on **what the chunk actually says**.
4. `character-window chunking` → a chunk opens mid-sentence on an analyst's
   premise, unattributed → **retrieval** returns it as evidence →
   **`contradiction.py`** extracts a numeric claim from narrative text →
   compares it against the **SQL-verified** figure → they disagree, because the
   analyst's premise was wrong and management corrected it in the next turn →
   the system reports a **high-severity contradiction** → which inverts its own
   stated value, since *"a false contradiction is worse than a missed one."*
   Layers: chunker → Qdrant/retriever → cross_engine → contradiction → response.
5. Because a speaker turn is a **complete unit**, and overlap would bleed the
   *previous speaker's words* into the next speaker's chunk — **reintroducing
   exactly the attribution problem** speaker-turn chunking exists to fix. Overlap
   solves boundary loss in continuous prose; in dialogue the boundary *is* the
   meaning.

### §12 — Basic

1. **(a)** Embedding models have hard input limits. **(b)** Precision — one vector
   for fifty pages cannot answer a question about one paragraph, and irrelevant
   context degrades the answer.
2. 150 tokens (600 characters) of shared text between adjacent chunks.
   **No — frozen.** `CLAUDE.md` §3: it encodes a measurement not derivable from
   the code. Propose and stop.
3. `FINANCIAL_STATEMENT` and `TABLE` (`TARGET_TOKENS` is `None`).
4. `md5(f"{doc_id}:{page_number}:{position}:{text[:100]}")` rendered as a UUID.
5. One speaker's contiguous contribution to a transcript, from their name-and-colon
   line to the next speaker's.

### §12 — Code

6. 4 characters per token. Approximate because the target is a **budget**, not a
   hard limit, and running a real tokeniser at every candidate split during
   recursive splitting would cost far more than the precision is worth — the split
   point is chosen by separator anyway.
7. `["\n\n", "\n", ". ", " ", ""]` — paragraph, line, sentence, word, raw
   character. **Descending semantic strength**, with `""` as the always-terminating
   base case.
8. The **speaker label**. An over-long turn is split, and every fragment stays
   attributed. Splitting is allowed; losing attribution is not.
9. Set in the chunker's transcript path via `_classify_speaker(speaker, roster)`,
   stored in `ChunkMetadata` → the Qdrant payload → `ChunkResult.speaker_role`.
   Consumed by `contradiction.py`'s `_speaker_permits_claim` /
   `CLAIMANT_SPEAKER_ROLES` to decide whether a chunk may carry a company claim
   (Day 37).
10. `tenant_id`, `doc_id`, `company`, `fiscal_year`, `quarter`, `financial_type`,
    `chunk_type`, `page_number`, `filing_date`, `is_latest`, `speaker_role`,
    `section_label` — i.e. every field a query can filter on.

### §12 — Why

11. Because the content types are genuinely different. A statement table is a grid
    that must stay whole; a risk factor is a self-contained paragraph; MD&A is
    flowing prose. Day 23's classification is what makes the distinction
    available.
12. See §11 Q1.
13. Because overlap is **load-bearing** — it is what stopped the PPBL fact being
    orphaned. The defect was not overlapping chunks existing but two windows over
    the same text both reaching the final top-5, which is a **ranking** problem and
    is fixed at ranking.
14. See §11 Q5.
15. Because `IMPLEMENTATION_DELTAS.md` §B is the register of things **specified
    and not built**, and a gap that is recorded can be reasoned about — whereas a
    gap that is quietly dropped becomes an assumption someone later relies on.
    The textbook's "answer split across multiple chunks" failure remains reachable
    here, and saying so is the point.

### §12 — Debugging

16. A chunk parameter changed — `TARGET_TOKENS`, `OVERLAP_TOKENS`, or the
    separators. Splits moved, so `position` and `text[:100]` changed, so **every
    chunk ID changed**. `upsert` inserted the new points **alongside** the old
    ones, because it cannot overwrite an ID it no longer generates. The old points
    are orphaned and still retrievable. `CAVEAT-016`, and there is no purge.
17. **In the chunker**, not in `contradiction.py`. If the transcript path did not
    run — wrong `doc_type`, or a document whose speaker lines do not match
    `SPEAKER_LINE_RE` — the generic character-window path produces unattributed
    analyst premises, and everything downstream behaves correctly on bad input.
    Check `speaker_role` on the offending chunk: `"unknown"` on a transcript chunk
    is the signature.
18. **Audit finding F7** — the `financial_type` retrieval filter is functionally
    inert. **The cause is here**, in `_build_metadata`: every chunk is stamped with
    a `financial_type`, and non-statement chunks (risk, MD&A) get `"unknown"`
    because they are genuinely not scoped to consolidated or standalone. The
    filter therefore has to match `requested OR "unknown"`, and since most
    narrative chunks are `"unknown"`, it excludes almost nothing.

### §12 — System design

19. **Treat it as a schema migration** (textbook 15B), because it is one.
    **(a)** Get approval — `TARGET_TOKENS` is not in `CLAUDE.md` §3's frozen list
    but `OVERLAP_TOKENS` is, and they interact.
    **(b)** Run `regression_check` **before** the change and tee it, to have a
    baseline block/chunk distribution.
    **(c)** Change the constant.
    **(d)** Re-run `regression_check` **once**, tee to `/tmp`, grep — never twice
    (WSL RAM).
    **(e)** **Delete the affected documents' Qdrant points by `doc_id` filter**,
    because every chunk ID will change and `upsert` will otherwise leave the old
    points orphaned.
    **(f)** Full re-ingest.
    **(g)** Verify: Qdrant point count against expectation (not a tenant-wide
    count — audit F8); a spot-check that no table was split; and re-check the
    near-duplicate suppression rate, since a larger window changes the overlap
    ratio and 0.70 was calibrated on one measured pair.
    Nothing in Postgres changes — `financials` is extracted from tables, not
    chunks.
20. **The smallest version that does not invalidate IDs:** keep chunking exactly as
    it is, and add a **parent pointer** — store `parent_page_text` (or a
    `parent_id` plus a separate parent store) in the Qdrant **payload**. Retrieval
    and reranking still operate on the small chunks, so scores, thresholds and
    near-duplicate suppression are untouched; only `_format_chunks_for_prompt`
    (Day 30) changes, to send the parent text for the top-K instead of the chunk
    text. **Why this preserves IDs:** `_make_chunk_id` reads `doc_id`,
    `page_number`, `position` and `text[:100]` — adding a payload field changes
    none of them, so `upsert` still overwrites in place. **What it costs:**
    payload size (the page text duplicated per chunk), and it reintroduces the
    "lost in the middle" pressure by making the context longer — which is why it
    should be measured against the golden set before shipping, not assumed
    better.

---

## 14. MUST REMEMBER

```text
- Per-block-type targets. TABLES AND FINANCIAL STATEMENTS ARE NEVER SPLIT
- OVERLAP_TOKENS = 150 (600 chars). FROZEN. Raised from 50 to fix an orphaned fact
- The overlap side-effect is fixed at RERANK (suppression), not by lowering it
- chunk_id = md5(doc_id:page:position:text[:100]) → idempotent upsert
- Deterministic IDs only help WHILE CHUNK BOUNDARIES HOLD
- Transcripts split by SPEAKER TURN, with ZERO overlap
- The roster is parsed FROM THE DOCUMENT, not hardcoded
- speaker_role → contradiction.py's claim eligibility
- Non-statement chunks carry financial_type="unknown" → audit F7
- Parent-child chunking is NOT BUILT — recorded in DELTAS §B
```

## 15. MUST UNDERSTAND

```text
- Why chunk size is a schema migration, not a config parameter
- Why the fix for a symptom belongs where the symptom occurs, not where the
  cause is convenient to change
- How a CHUNKING decision produces a FALSE CONTRADICTION three layers away
- Why "127 matches, 17 names, zero spurious hits across 532 lines" is a
  validation and "it looks right" is not
- Why zero overlap is correct for dialogue and wrong for prose
- Why one metadata decision (financial_type="unknown") is visible in the
  retriever, the audit findings, and the frontend
```

---

## 16. This connects to

```text
Day 23 — labelling the blocks
   ↓
Day 24 — splitting them into retrievable units    ← END OF PHASE 6
   ↓
Day 25 — the query side begins: dense retrieval
```

Forward references:

- Near-duplicate suppression, the fix for overlap's side-effect → **Day 29**
- `financial_type="unknown"` and audit **F7** → **Day 27**
- `speaker_role` and claim eligibility → **Day 37**
- `CAVEAT-016` orphaned points → **Day 43**
- Parent-child, and "the answer split across chunks" → **Day 30**
