# Day 30 — The Semantic Path, Whole

**Phase 8 · Weight: H (~120 min) · Prerequisites: Days 18, 19, 29**

**Textbook: Part 7 "The Complete RAG Pipeline" and Part 17 "Final Master Flow" —
CONFIRMS · 15B "Retrieval Looks Good But The Answer Is Wrong" — CONFIRMS ·
15B "Lost in the Middle" / sandwich ordering — DIVERGES (not implemented).**

---

## 1. Today's goal

By tonight you can **explain every arrow of the semantic pipeline**, from typed
question to rendered citation. Specifically:

- How the five chunks become a prompt, and what the numbered-excerpt format buys.
- Why `SynthesisOutcome` carries a **status** rather than relying on
  `provider is None` — and the outage that was indistinguishable from a real
  answer.
- What the **synthesis floor** is, and why `clear_llm_attribution` fires with it.
- Post-generation refusal detection: why retrieval confidence is not answer
  confidence, and why the guard is a position test rather than a length test.
- The single-source caveat, and why it downgrades confidence.

---

## 2. Why now

Days 25–29 produced five scored, deduplicated, confidence-tiered chunks. Today
they become an answer. This is the day the whole of Phase 6 and Phase 7 pays off,
and it is the last piece before the quantitative path.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `generate_text`, `LLMUnavailable` | Day 19 | Synthesis calls it |
| `record_llm_call` / `clear_llm_attribution` | Days 3, 19 | Both fire here |
| Grounding instructions | Day 18 | `SYNTHESIS_SYSTEM_PROMPT` |
| `confidence_tier` from retrieval only | Day 29 | Today's correction |
| `Citation` fields | Days 10, 29 | The Sources block |

---

## 4. Concept lesson

### 4.1 Three response strategies

`response_generator.py`'s docstring:

> **quantitative** — TEMPLATED, not generative. The SQL value is already
> verified; wrapping it in an LLM-generated sentence adds hallucination risk for
> zero benefit.
>
> **semantic** — GENERATIVE. Gemini synthesises an answer from the top retrieved
> chunks, with citations appended. This is the one place an LLM "explains"
> something, because there's no ground truth number to protect — only retrieved
> text to summarise faithfully.
>
> **cross** — GENERATIVE + contradiction disclosure.

**The rule underneath:** generation is permitted exactly where there is **no
verified value to protect**. Day 17's principle, applied at the last layer.

---

### 4.2 The prompt

```python
def _format_chunks_for_prompt(chunks) -> str:
    blocks = []
    for i, chunk in enumerate(chunks, 1):
        blocks.append(
            f"[Excerpt {i} — page {chunk['page_number']}, "
            f"{chunk['financial_type']} {chunk['fiscal_year']}]\n{chunk['text']}"
        )
    return "\n\n".join(blocks)
```

Producing:

```
Question: What risks does Eternal disclose?

Retrieved excerpts:

[Excerpt 1 — page 23, consolidated FY26]
Forward-looking statements in this document...

[Excerpt 2 — page 31, unknown FY26]
The Company faces competition in quick commerce...
```

**Each excerpt is labelled with page, financial type and fiscal year.** Not for
the model to cite — the prompt explicitly forbids that:

> Do not repeat citation details (page numbers, chunk IDs) in your prose —
> citations are appended separately by the system.

**So why label them?** Because the labels give the model *context for
disambiguation*. Two excerpts about revenue from different fiscal years are
distinguishable; without labels they are contradictory. **Metadata for reasoning,
not for attribution.**

**And the divergence.** Textbook 15B recommends *sandwich ordering* — best chunk
first, second-best last, the rest in the middle, to counter "lost in the middle".
`_format_chunks_for_prompt` does not do this: it emits chunks in rank order.

With five chunks the effect is likely small, and **it is unmeasured**. It is a
genuine, unclaimed improvement, recorded in `KNOWN_UNKNOWNS.md` rather than
silently "fixed".

---

### 4.3 `SynthesisOutcome` — a status, because `None` meant two things

```python
class SynthesisOutcome(NamedTuple):
    text: str
    result: Optional[object]
    status: str          # "ok" | "no_chunks" | "unavailable"
```

The docstring is the most valuable thing in the file:

> `status` is carried separately because `provider is None` **conflated two
> situations that must be reported to the user in opposite ways**:
>
> **no_chunks** — retrieval returned nothing. No LLM was called. **The system
> worked; the corpus did not hold the answer.**
>
> **unavailable** — BOTH providers failed and the raw-excerpt floor fired. **The
> system did not work**, and the user is holding an excerpt dump rather than an
> answer.
>
> Measured 2026-07-31: because both cases returned `None` and neither set `error`,
> **A TOTAL LLM OUTAGE WAS INDISTINGUISHABLE FROM A REAL ANSWER** —
> `confidence_tier` stayed `"high"` (honest about retrieval, silent about
> synthesis) and `llm_provider` kept whatever the router had already set.

**Read the parenthesis.** `confidence_tier` was *honest about retrieval* — the
chunks really were good. It was **silent about synthesis**, which had not
happened. Every individual value was defensible; the composition lied.

**This is the same failure shape as Day 3's `record_llm_call`** and Day 9's
`confidence_tier` on a blocked query: *a field that is correct about the thing it
measures, reported as though it measured something else.*

---

### 4.4 The synthesis floor

```python
except LLMUnavailable as e:
    logger.error("Response synthesis unavailable on ALL providers: %s", e)
    return SynthesisOutcome(
        f"Unable to synthesise a summary due to a temporary error. "
        f"Top matching excerpt (page {chunks[0]['page_number']}): "
        f"{chunks[0]['text'][:300].strip()}",
        None,
        "unavailable",
    )
```

Both providers failed (Day 19), so the user gets **the raw top excerpt** — real,
retrieved, citable text, clearly labelled as a fallback.

**Why give anything at all?** Because retrieval succeeded. The correct chunk was
found; only the writing failed. Returning nothing would discard work the user
could still use.

**And the docstring's boundary:** *"The raw-excerpt floor is the last resort
AFTER the Groq fallback, not instead of it."* Three levels: Gemini → Groq →
excerpts.

**What the node does with it:**

```python
if synth.status == "unavailable":
    clear_llm_attribution(state)
    state["confidence_tier"] = "low"
    state["error"] = "synthesis_unavailable"
    state["error_node"] = "response_generator"
```

Four writes, each closing a way the record could overstate:

| Write | Prevents |
|---|---|
| `clear_llm_attribution` | The router's `"gemini"` making an outage look served |
| `confidence_tier = "low"` | Retrieval confidence standing in for answer confidence |
| `error = "synthesis_unavailable"` | The failure being invisible |
| `error_node` | Not knowing which stage failed |

And the comment: *"low can never RAISE the tier, so cap-never-raise holds
trivially"* — `confidence_node` (§4.7) only lowers, so setting `low` here is safe
regardless of what runs after.

---

### 4.5 The Sources block

```python
def _format_citations_block(citations) -> str:
    if not citations:
        return ""
    lines = ["\n\nSources:"]
    for i, c in enumerate(citations, 1):
        ftype = c.get('financial_type')
        type_part = f" ({ftype})" if ftype and ftype != "unknown" else ""
        lines.append(
            f"  [{i}] {c['company']} {c['fiscal_year']}{type_part} — "
            f"page {c['page_number']}, filed {c['filing_date']} "
            f"(relevance score: {c['reranker_score']:.2f})"
        )
    return "\n".join(lines)
```

**Built by code, not by the model.** The model is forbidden from writing citation
details, so a citation can only exist if a chunk was actually retrieved.
**Fabricating one is structurally impossible.**

**The `"unknown"` omission**, and its comment:

> `section_classifier` assigns UNKNOWN to every non-FINANCIAL_STATEMENT block **BY
> DESIGN**, so "(unknown)" is not missing data — it is a category that simply does
> not apply to narrative text. **Omission, not substitution.** … The frontend's
> `buildCitationItems` was fixed earlier; **this is the same bug in the plain-text
> Sources block.**

**The same defect in two renderers**, fixed at different times. Day 24's metadata
decision, surfacing for the third time.

**`relevance_score: {:.2f}` is printed raw** — and Day 28 established it is
meaningless without its backend, which this block does not show. On the local
scale a score renders as `-3.39`, which reads like an error. The admin API
carries `reranker_backend`; this plain-text block does not.

---

### 4.6 Post-generation refusal detection

**The problem:** `confidence_tier` measures **retrieval**, computed before any
answer text exists (Day 29). A perfect retrieval can still produce:

> "The provided excerpts do not contain information about risk factors."

High confidence, and a refusal.

```python
REFUSAL_PATTERNS = [_re.compile(p, _re.IGNORECASE) for p in [
    r"do(?:es)?\s+not\s+contain",
    r"documents?\s+(?:do|does)\s+not\s+(?:cover|contain|mention|include)",
    r"is\s+not\s+mentioned\s+in\s+the",
    r"no\s+information\s+(?:is\s+)?(?:available\s+|found\s+)?(?:about|regarding|on|concerning)",
    r"(?:was|were)\s+not\s+(?:found|located)\s+in\s+the\s+(?:provided\s+|retrieved\s+)?(?:excerpts|documents)",
    r"insufficient\s+information",
    r"excerpts?\s+(?:do|does)\s+not\s+(?:fully\s+)?answer",
]]
```

**Anchored to the prompt's own vocabulary:**

> These patterns are anchored to phrasing `SYNTHESIS_SYSTEM_PROMPT` itself
> explicitly asks Gemini to use when excerpts don't answer the question — narrow
> and deliberate, **not a broad keyword scan** (same discipline as the blueprint's
> Trap 7 fix).

You control the prompt, so you know the phrasing to expect. That is what makes
narrow patterns viable.

**And the guard, which is the interesting part:**

```python
def _is_refusal_text(text: str) -> bool:
    for pattern in REFUSAL_PATTERNS:
        match = pattern.search(text)
        if match:
            if match.start() >= len(text) * 0.4:
                continue
            tail = text[match.end():].strip()
            if len(tail) < 120:
                return True
    return False
```

**Two tests: position, then tail.**

> A genuine refusal is short and **LEADS** with the refusal. A substantive answer
> can legitimately use the same phrasing to flag ONE limitation after several
> paragraphs of real content (e.g. "…do not contain the final audit opinion" as
> the last sentence of an otherwise complete answer) — **that is a caveat, not a
> refusal.**

**And the version that failed:**

> The previous version used `len(text) < 300` as an OR-branch escape hatch. That
> **short-circuited the position test entirely** for brief answers: confirmed live
> 2026-07-29 that **Q030 (260 chars, match at 48%)** and **Q038 (~300 chars, match
> mid-sentence)** were both capped to low confidence despite being correct,
> substantive answers that merely flagged one limitation. **Length is not the
> signal** — a genuine refusal has no real content BESIDES the refusal, however
> long it happens to be. So the test is now **what remains after the match**, not
> how many characters precede it.

**"Length is not the signal."** The first heuristic correlated with the target and
was not the target. Two false positives, with question ids and measurements.

**And `response_text` is deliberately left untouched:**

> `response_text` is intentionally left untouched so the frontend can render
> Gemini's exact explanation inside the refusal card.

The tier is capped; the model's own words survive, because "the documents do not
cover the auditor's opinion" is more useful than a generic refusal.

---

### 4.7 The single-source caveat

```python
distinct_sources = {c.get("chunk_id", i) for i, c in enumerate(citations)}

if len(distinct_sources) == 1 and path in ("semantic", "cross"):
    if state.get("confidence_tier") == "high":
        state["confidence_tier"] = "medium"
    single_source_caveat = (
        "This answer is based on a single source passage — "
        "related disclosures elsewhere in the filing may not have been retrieved."
    )
    state["response_text"] += f"\n\n_{single_source_caveat}_"
```

**One citation means one passage supported the answer.** That can happen because
the filing says it once — or because retrieval found one of several relevant
passages. **The system cannot tell which**, so it discloses.

**Only downgrades `high` → `medium`.** Never raises, and never touches `low`.

**`c.get("chunk_id", i)`** uses the loop index as a fallback so a citation missing
`chunk_id` counts as distinct rather than collapsing into another — failing toward
*more* sources, i.e. toward **not** firing the caveat. Arguably the wrong
direction for a disclosure, and it only matters if `chunk_id` is ever absent,
which `_build_citations` guarantees it is not.

---

### 4.8 `confidence_node` — caps only

Between `semantic_engine` and `response_generator` sits a node whose entire
contract is *never raise*:

```python
"""
This module does NOT compute confidence from scratch — it only adjusts
what the path engines already set. Never raises confidence, only lowers it.
"""
```

Two cross-cutting caps: **high-severity contradictions** → cap at `medium`
(Day 37), and **restatement disclosure** → cap at `medium`.

**Why cross-cutting caps belong in their own node.** Each path computes confidence
its own way — semantic from reranker scores, quant from `sql_verified`. A
contradiction penalty applies regardless of path, and implementing it in three
engines would be three copies of one rule.

**And the restatement penalty has no producer.** `CAVEAT-008` / audit **F5**:
nothing sets `restatement_disclosed`, so that cap never fires. **A mechanism with
no trigger** — recorded, not deleted.

---

## 5. The actual LedgerMind file

```
File:        backend/app/engines/response_generator.py (709 lines)
Purpose:     Assemble response_text from whatever the active path produced
Who imports: engines/graph.py
What it imports: state (Chunk/Citation/Contradiction types, record_llm_call,
                 clear_llm_attribution), llm/client (generate_text,
                 LLMUnavailable), metrics/registry (display_label)
Entry point: response_generator_node(state) -> QueryState
Data in:     a populated QueryState
Data out:    state["response_text"], possibly a capped confidence_tier
Skips:       entirely, if response_text is already set (blocked, or an
             engine already wrote a refusal)
```

---

## 6. Deep walkthrough — `response_generator_node`

**STATE BEFORE.** Post-`confidence_node`. Either `response_text` is set (blocked
or refused), or the path's outputs are populated.

**Step 0 — the early exit.**

```python
if state.get("response_text"):
    return state
```

**One line, and it is the whole error-path contract.** Every refusal — prompt
shield, router, low confidence, DSL failure, SQL failure, the guards — writes
`response_text` at the point of failure. This node then does nothing, so an error
message can never be overwritten by generation.

**Step 1 — build the blocks.**

```python
citations_block = _format_citations_block(state.get("citations", []))
contradiction_block = _format_contradiction_block(state.get("contradictions", []))
```

Both computed unconditionally, both empty-string when there is nothing. Appending
`""` is a no-op, so the branches below need no null checks.

**Step 2 — dispatch on path.**

```python
if path == "quantitative":
    body = _format_quant_response(state) + _period_assumption_note(state)
    state["response_text"] = body   # no citations block — SQL is the source of truth
```

**No citations on the quantitative path**, and the comment says why: SQL is the
source of truth. Provenance is the `doc_id` on the `financials` row (Day 13), and
the DSL and SQL are on the analyst response (Day 9). Different evidence, different
channel.

```python
elif path == "semantic":
    synth = _generate_semantic_response(query=state["query"],
                                        chunks=state.get("retrieved_chunks", []))
    if synth.result is not None:
        record_llm_call(state, synth.result)
    state["response_text"] = synth.text + citations_block
    qualitative_text_for_refusal_check = synth.text
```

**`state["query"]`, not `resolved_query`.** Retrieval searched with the prefixed
version (Day 25); synthesis reads the **user's actual words**. Feeding the model
`"ETERNAL FY26 Q4 consolidated What were the revenue drivers?"` would have it
answer a slightly different, machine-shaped question.

**`qualitative_text_for_refusal_check = synth.text`** — captured *before* the
citations block is appended, so the refusal patterns never match text the system
generated itself.

**Step 3 — the caveat and the refusal check** (§4.6–4.7).

**STATE AFTER.** `response_text` set; possibly `confidence_tier` capped and an
`error` recorded.

---

### 6.1 `_period_assumption_note` — one formatter, two paths

```python
def _period_assumption_note(state) -> str:
    """
    Disclosure for a substituted period. Shared by the quantitative and cross
    paths so the two can never drift — this project has repeatedly been bitten
    by the same rule living in two places.
    """
```

Day 34's `period_assumed` flag, rendered. **One function, two callers** — the
recurring structural principle.

---

### 6.2 `_fmt_money` — a formatter with a bug history

```python
def _fmt_money(value) -> str:
    """
    Render a currency figure the way a filing does: no trailing ".0" on whole
    numbers, two decimals only when the fraction is real. The frontend's
    LedgerTable renders the same figures via toLocaleString() (no decimals),
    so a hardcoded .1f here made one page show "54,364" and "54,364.0" for
    the same value. Single formatter so the convention lives in one place.
    """
```

**Two renderers, one page, two spellings of one number.** Not a correctness bug
and a credibility one: a user seeing `54,364` and `54,364.0` on the same screen
reasonably wonders which is right.

---

## 7. Data flow — the complete semantic path

```
USER: "What risks does Eternal disclose in Q4 FY26?"
   │
   ▼ POST /api/query, JWT verified                       Days 4-8
   ▼ make_initial_state()                                Day 3
   ▼ prompt_shield_node — passes                         Day 42
   ▼ router_node
   │    companies=["ETERNAL"] fiscal_year="FY26" quarter="Q4"
   │    path="semantic"
   │    resolved_query="ETERNAL FY26 Q4 consolidated What risks..."
   ▼                                                     Day 36
semantic_engine_node
   ▼ _encode_dense(resolved_query)      384 floats       Day 25
   ▼ _encode_sparse(resolved_query)     SparseVector     Day 26
   ▼ _build_filter(...)                 tenant · is_latest · company · FY · Q
   ▼ query_points(prefetch=[dense, sparse], FusionQuery(RRF))
   │                                    20 candidates    Day 27
   ▼ rerank() → Cohere or ONNX          scored + TAGGED  Day 28
   ▼ _deduplicate_near_identical(0.70)                   Day 29
   ▼ [:5]
   ▼ _score_confidence()                tier by backend  Day 29
   ▼ CRAG ladder if low/medium
   ▼ _build_citations()                 ChunkResult(16) → Citation(9)
   │
   ▼ confidence_node                    CAPS ONLY, never raises
   │
   ▼ response_generator_node                             ◄── TODAY
   │    _format_chunks_for_prompt()     [Excerpt i — page N, type FY]
   │    generate_text(SYNTHESIS_SYSTEM_PROMPT, ...)      Days 18-19
   │      ├─ Gemini ok        → status "ok"
   │      ├─ Groq fallback    → status "ok", provider "groq"
   │      └─ BOTH failed      → raw excerpt, status "unavailable"
   │                             → clear_llm_attribution
   │                             → tier=low, error=synthesis_unavailable
   │    record_llm_call()               worst provider wins
   │    _format_citations_block()       BUILT BY CODE, not the model
   │    single-source caveat            high → medium
   │    _is_refusal_text()              position + tail, not length
   │
   ▼ audit_writer_node                  the full record  Day 44
   ▼ role_filtered_response(state, role)                 Day 9
   ▼ SSE "complete" / JSON
   ▼ composeDocumentBody()                               Day 40
   ▼ rendered answer with numbered citations
```

**You can now explain every arrow.** That was the goal of Phases 6–8.

---

## 8. Engineering decision — generate the prose, build the citations

**Problem.** Turn five retrieved passages into a faithful answer whose every claim
is checkable.

**Decision.** LLM writes prose from the excerpts only; **code builds the
citations**; refusals are detected after generation; failures degrade through
three levels.

`ENGINEERING_DECISIONS.md` **ED-014**.

| Alternative | Why not |
|---|---|
| **Let the model emit citations** | It can fabricate one. Code-built citations make that structurally impossible |
| **Template the semantic answer too** | Five passages cannot be templated into fluent prose; this is what LLMs are for |
| **Trust retrieval confidence as answer confidence** | Measured false: high-scoring retrieval produces refusal text |
| **Return nothing when synthesis fails** | Retrieval succeeded — discarding the top excerpt discards usable work |
| **A broad keyword scan for refusals** | Would catch caveats inside good answers. Q030 and Q038 are the measured cases |
| **Sandwich ordering** (textbook 15B) | Not implemented, unmeasured, recorded in `KNOWN_UNKNOWNS.md` |

**Trade-offs accepted.**

- **Refusal detection is regex over model output.** Anchored to the prompt's own
  vocabulary, so a prompt edit could silently desynchronise it. Another reason
  prompt edits are STOP-AND-ASK.
- **`reranker_score` is printed without its backend** in the plain-text Sources
  block — Day 28's lesson, half-applied.
- **No sandwich ordering.**
- **The restatement cap has no producer** (`CAVEAT-008`).

**Current validity.** Strong. The three-level degradation and the status-carrying
outcome are the parts worth copying elsewhere.

**At 10×.** Synthesis is one LLM call per semantic query, so cost scales linearly
against a hard daily ceiling. That, not quality, is the binding constraint — and
it is why the semantic cache was specified (textbook Part 17 step 1) and **never
built** (Day 44).

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| An answer with a figure and no supporting citation | Historic: the citation floor (Day 29) |
| A total LLM outage reported as a served answer | Historic: `provider is None` conflating two cases |
| A correct answer capped to `low` | Historic: the `len < 300` escape hatch. Q030, Q038 |
| A refusal rendered beside a ticked figure | Historic: the generic detector applied to the cross path |
| `54,364` and `54,364.0` on one page | Historic: two formatters |
| "(unknown)" in a citation | The same bug in whichever renderer was not fixed |
| A negative relevance score shown to a user | Local ONNX scale, printed without its backend |
| A caveat treated as a refusal | The position/tail guard is what prevents it |
| The restatement cap never fires | `CAVEAT-008` — no producer |

---

## 10. Hands-on experiment

### Experiment 1 — trace one question end to end

```bash
curl -N -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"query":"What risks does Eternal disclose in Q4 FY26?"}'
```

Watch the node events (Day 6). Then read the `complete` frame's `response_text`
and find the `Sources:` block. **Match each numbered citation to a node event you
just watched.**

### Experiment 2 — the prompt the model actually receives

```bash
docker compose exec -T backend python -c "
import os
from app.engines.retriever import retrieve_and_rerank
from app.engines.response_generator import _format_chunks_for_prompt, SYNTHESIS_SYSTEM_PROMPT
ch = retrieve_and_rerank(query='ETERNAL FY26 Q4 consolidated risk factors',
                         tenant_id=os.getenv('T',''), companies=['ETERNAL'], fiscal_year='FY26')
print('chunks:', len(ch)); print()
print('=== SYSTEM ==='); print(SYNTHESIS_SYSTEM_PROMPT[:700]); print('...')
print(); print('=== USER ===')
print(f'Question: What risks does Eternal disclose?')
print(); print('Retrieved excerpts:'); print()
print(_format_chunks_for_prompt(ch)[:1200]); print('...')
"
```

**Note the excerpt labels**, and note the prompt forbidding their restatement.

### Experiment 3 — the three statuses

```bash
docker compose exec -T backend python -c "
from app.engines.response_generator import _generate_semantic_response
out = _generate_semantic_response(query='anything', chunks=[])
print('no chunks ->', out.status, '|', out.text[:70])
print('  result  ->', out.result, ' <- no LLM was called')
"
```

Then force the outage:

```bash
docker compose exec -T -e GEMINI_API_KEY=bad -e GROQ_API_KEY=bad backend python -c "
import os
from app.engines.retriever import retrieve_and_rerank
from app.engines.response_generator import _generate_semantic_response
ch = retrieve_and_rerank(query='ETERNAL FY26 risk factors', tenant_id=os.getenv('T',''),
                         companies=['ETERNAL'])
out = _generate_semantic_response(query='What risks?', chunks=ch)
print('status:', out.status)
print('result:', out.result, ' <- None, and status says WHY')
print('text  :', out.text[:180])
print()
print('no_chunks: the system worked, the corpus did not hold it.')
print('unavailable: the system did not work. Opposite reports to the user.')
"
```

### Experiment 4 — refusal detection, and the guard

```bash
docker compose exec -T backend python -c "
from app.engines.response_generator import _is_refusal_text
cases = [
 ('genuine refusal',
  'The provided excerpts do not contain information about risk factors.'),
 ('answer with a caveat',
  'Eternal discloses several risks including intense competition in quick commerce, '
  'regulatory uncertainty around food delivery, and dependence on gig workers. '
  'Management notes mitigation through geographic diversification and a focus on '
  'unit economics. The excerpts do not contain the final audit opinion.'),
 ('short but substantive (Q030-shaped)',
  'Revenue grew 68% year on year to INR 54,364 crore, driven by quick commerce. '
  'The excerpts do not cover segment-level margins.'),
]
for label, t in cases:
    print(f'  {_is_refusal_text(t)!s:5}  {label}  ({len(t)} chars)')
print()
print('The third is ~150 chars. The OLD len<300 escape hatch flagged it.')
print('The position + tail test does not.')
"
```

### Experiment 5 — the single-source caveat

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"query":"What did management say about Hyperpure specifically in Q4 FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('citations      :', len(d.get('citations', [])))
print('confidence_tier:', d.get('confidence_tier'))
txt = d.get('response_text') or ''
print('caveat present :', 'single source passage' in txt)
"
```

A narrow question is more likely to retrieve one passage.

### Experiment 6 — citations are code-built

```bash
docker compose exec -T backend python -c "
from app.engines.response_generator import _format_citations_block
cits = [
 {'company':'ETERNAL','fiscal_year':'FY26','financial_type':'consolidated',
  'page_number':23,'filing_date':'2026-05-15','reranker_score':0.9214},
 {'company':'ETERNAL','fiscal_year':'FY26','financial_type':'unknown',
  'page_number':31,'filing_date':'2026-05-15','reranker_score':0.4127},
 {'company':'ETERNAL','fiscal_year':'FY26','financial_type':'unknown',
  'page_number':19,'filing_date':'2026-05-15','reranker_score':-3.3900},
]
print(_format_citations_block(cits))
print()
print('Row 2: \"(unknown)\" OMITTED, not printed.')
print('Row 3: a LOCAL ONNX logit rendered raw. Nothing here says which scale.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/response_generator.py`:

1. `response_generator_node` starts with `if state.get("response_text"): return`.
   Name three earlier places that rely on this, and what would break without it.
2. Why does the semantic branch pass `state["query"]` rather than
   `state["resolved_query"]`?
3. `SynthesisOutcome` has three fields. Why is `status` needed when `result`
   already distinguishes "an LLM ran" from "one did not"?
4. `_is_refusal_text` has two tests. Name both, and say what the previous version
   used instead and why it failed.
5. The quantitative branch appends no citations block. Where does provenance live
   for that path instead?

---

## 12. Self-check questions

**Basic**
1. Which path is templated and which generative?
2. What are the three `SynthesisOutcome` statuses?
3. What is the synthesis floor?
4. Who builds the citations?
5. What does the single-source caveat do to confidence?

**Code**
6. What does `_format_chunks_for_prompt` emit per chunk?
7. What four things happen on `status == "unavailable"`?
8. What does `_is_refusal_text` measure?
9. What does `_period_assumption_note` return, and who calls it?
10. What does `confidence_node` do, and what can it never do?

**Why**
11. Why is generation permitted on the semantic path and not the quantitative one?
12. Why does the model receive excerpt labels but is forbidden from citing them?
13. Why must the outage case be distinguishable from the empty-corpus case?
14. Why is `response_text` untouched when a refusal is detected?
15. Why is the refusal check applied to the semantic path but not the cross path?

**Debugging**
16. A user reports a confident answer whose figure appears in no citation. Which
    historic bug, and what was the fix?
17. A correct, short answer is rendered as a refusal. Which guard failed, and how?
18. The audit log shows `llm_provider = "gemini"` on an answer that is a raw
    excerpt dump. What went wrong, and what fixed it?

**System design**
19. Implement sandwich ordering. Where does it go, and how would you know whether
    it helped?
20. The plain-text Sources block prints `reranker_score` without its backend.
    Propose a fix consistent with this codebase's rules.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **(a)** `prompt_shield_node` sets `response_text` to a compliance message.
   **(b)** `router_node`'s F2 refusal sets `routing_unavailable` /
   `company_not_in_corpus` text. **(c)** `semantic_engine_node`'s
   `low_confidence_refusal`. **(d)** `quant_engine`'s guards and error paths.
   **Without it**, generation would run on a refused state and **overwrite the
   refusal message** with an LLM answer built from whatever chunks happened to be
   present — turning a refusal into a confident answer, which is the exact failure
   the whole system is built against.
2. Because retrieval searched with the machine-shaped prefixed string
   (`"ETERNAL FY26 Q4 consolidated What risks…"`), and the model should answer
   **the user's actual question**. Feeding it the prefixed version would have it
   answer a subtly different question, and would also put tokens in the prompt
   that the model might echo.
3. Because `result is None` is true in **both** the `no_chunks` and `unavailable`
   cases, and those must be reported to the user in **opposite** ways: one means
   "the system worked and the corpus does not hold this", the other means "the
   system failed and you are holding an excerpt dump". Measured 2026-07-31: with
   only `None` to go on, **a total LLM outage was indistinguishable from a real
   answer** — the tier stayed `high` and `llm_provider` kept the router's value.
4. **(a) Position:** the match must start within the first 40% of the text.
   **(b) Tail:** fewer than 120 characters may follow the match. The previous
   version used `len(text) < 300` as an OR-branch, which **short-circuited the
   position test for short answers** — Q030 (260 chars, match at 48%) and Q038
   were correct, substantive answers flagging one limitation, and both were capped
   to low. **Length is not the signal**: a genuine refusal has no real content
   besides the refusal, however long it is.
5. In the **`financials` row's `doc_id`** (Day 13), which links to the `documents`
   row carrying `filing_date` and `sha256_checksum` — and in the **DSL and
   compiled SQL**, exposed to analysts and admins by `role_filtered_response`
   (Day 9). Different evidence, different channel: the semantic path's evidence is
   *passages*, the quantitative path's is *a verified row and the query that
   produced it*.

### §12 — Basic

1. **Quantitative** is templated; **semantic** and **cross** are generative.
2. `"ok"`, `"no_chunks"`, `"unavailable"`.
3. The last resort after both providers fail: return the raw top excerpt (300
   chars) labelled as a temporary error, with `status="unavailable"`.
4. **Code** — `_format_citations_block`. The model is forbidden from writing
   citation details.
5. Downgrades `high` → `medium`. Never raises, never touches `low`.

### §12 — Code

6. `[Excerpt {i} — page {page_number}, {financial_type} {fiscal_year}]` followed by
   the chunk text, joined by blank lines.
7. `clear_llm_attribution(state)`; `confidence_tier = "low"`;
   `error = "synthesis_unavailable"`; `error_node = "response_generator"`.
8. Whether a refusal pattern matches **early** (within the first 40%) **and** is
   followed by fewer than 120 characters of substantive text.
9. A disclosure sentence when `period_assumed` is set, naming the substituted
   fiscal year. Called by **both** the quantitative and cross paths — one
   formatter, so they cannot drift.
10. Applies cross-cutting confidence **caps** — contradictions and restatement. It
    can only lower a tier, never raise one.

### §12 — Why

11. Because the quantitative value is **already verified by SQL**, so wrapping it
    in generated prose adds hallucination risk for zero benefit. The semantic path
    has no ground-truth number to protect — only retrieved text to summarise.
12. The labels give the model **context for disambiguation** — two excerpts about
    revenue from different years are distinguishable rather than contradictory.
    Citing is forbidden because the system appends citations built from real
    retrieved chunks, so a model-written citation could be fabricated.
13. Because they require opposite user-facing reports, and because conflating them
    made an outage look like a served answer, with `confidence_tier = "high"` and
    a stale `llm_provider`.
14. So the frontend can render **Gemini's exact explanation** inside the refusal
    card. "The documents do not cover the auditor's opinion" is more useful to the
    user than a generic refusal message.
15. Because it was **capping SQL-verified cross answers to `tier=low` with
    `error="low_confidence_refusal"`**, which the frontend renders as a "refused"
    callout **beside a ticked, correct figure**. The cross path has its own
    reconciliation (`_reconcile_cross`), which is declared the authority for that
    path's tier and error (Day 37).

### §12 — Debugging

16. The **citation relevance floor** (Day 29), removed 2026-08-08. It dropped
    sub-0.05 chunks from `citations` while leaving them in `retrieved_chunks`, so
    the model read a passage the user could not see — the "4.8 million square
    feet" case. **The fix was to delete the floor**, on the principle that
    `retrieved_chunks` and `citations` must never diverge; the noise it suppressed
    is a display-weight problem instead.
17. `_is_refusal_text`'s **position test was short-circuited** by the old
    `len(text) < 300` OR-branch, so a brief answer containing a caveat matched.
    Fixed by removing the length escape hatch and testing **what remains after the
    match** instead.
18. **`clear_llm_attribution` was not called** on the synthesis floor, so the
    router's earlier successful `record_llm_call` left `llm_provider = "gemini"` in
    place — reporting a total outage as a normally-served answer. Fixed by calling
    `clear_llm_attribution(state)` whenever `status == "unavailable"`, and by
    carrying the status at all so the caller knows to.

### §12 — System design

19. **Where:** `_format_chunks_for_prompt` — it is the only place chunk order
    becomes prompt order, and it receives an already-ranked list. The change is
    the textbook's four-line reorder: `[best] + rest + [second-best]`.
    **How you would know it helped:** you cannot tell from a single query, so run
    the golden dataset's semantic categories twice — once each ordering — with
    everything else fixed, three runs per arm, provider and model printed per run
    (`CLAUDE.md` §8). Score on **keyword presence**, which is what the semantic
    categories already assert, so the instrument exists. **The honest problem:**
    each arm is ~100 LLM calls against a 500/day ceiling, so this is a
    two-day measurement and needs explicit approval. And with only five chunks the
    effect may be within run-to-run variance — which is itself worth knowing, and
    is why it belongs in `KNOWN_UNKNOWNS.md` rather than being applied on the
    textbook's authority.
20. **The rule this codebase applies** (Day 28): a score is meaningless without
    its instrument, and an assumption must not be reported as an observation. So:
    **do not print the raw score in user-facing plain text at all.** The tier
    already conveys confidence, and the raw number is uninterpretable without a
    scale the user has no way to see. For analyst and admin consumers the score
    **and** `reranker_backend` are already on the JSON response, which is the right
    channel for it. If a number must appear in the plain-text block, print the
    **normalised** value (which `_score_confidence` already computes on a
    backend-aware 0–1 scale) rather than the raw one — but the simpler, more
    honest change is omission, which is the same rule `"(unknown)"` follows three
    lines above it in the same function.

---

## 14. MUST REMEMBER

```text
- quantitative = TEMPLATED · semantic and cross = GENERATIVE
- Generation is allowed only where there is NO verified value to protect
- CITATIONS ARE BUILT BY CODE. A fabricated citation is structurally impossible
- SynthesisOutcome carries a STATUS: ok | no_chunks | unavailable
- The floor is Gemini → Groq → RAW EXCERPT, in that order
- On "unavailable": clear attribution, tier=low, error, error_node — four writes
- Refusal detection is POSITION (first 40%) + TAIL (<120 chars). NOT length
- response_text is left untouched on a detected refusal
- Single citation → caveat + high→medium
- confidence_node CAPS ONLY. It can never raise
- Synthesis reads state["query"], not resolved_query
```

## 15. MUST UNDERSTAND

```text
- Why retrieval confidence is not answer confidence, and why the correction
  has to happen AFTER generation
- How three individually-honest values composed into a lie: a good tier, a
  stale provider, and a None that meant two things
- Why "length is not the signal" — the first heuristic correlated with the
  target and was not the target
- Why anchoring refusal patterns to your OWN prompt's vocabulary is what makes
  narrow patterns viable — and why that couples them to prompt edits
- Why one formatter with two callers keeps appearing as the answer
```

---

## 16. This connects to

```text
Days 25-29 — retrieval
   ↓
Day 30 — the complete semantic path              ← END OF PHASE 8
   ↓
Day 31 — the other half: how a number becomes a row
```

Forward references:

- `_format_quant_response` and the templates → **Day 34**
- `_reconcile_cross` and the cross path's authority rule → **Day 37**
- `period_assumed` → **Day 34**
- `CAVEAT-008` — the restatement cap with no producer → **Day 43**
- Sandwich ordering, in `KNOWN_UNKNOWNS.md` → **Day 43**
- `composeDocumentBody` rendering all of this → **Day 40**
