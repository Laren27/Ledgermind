# Day 37 — Cross-Examination and Contradiction Detection

**Phase 10 · Weight: H (~120 min) · Prerequisites: Days 30, 34, 36**

**Textbook: Part 14 — EXTENDS.** The case study's synthesiser merges two engines'
results. It has no concept of the two halves *disagreeing*, which is what this
day is about.

---

## 1. Today's goal

By tonight you can:

- Explain why quant runs **before** semantic on this path, and why two earlier
  fixes failed.
- Explain the three claim-eligibility rules and the eleven false contradictions
  that produced them.
- Explain **why a false contradiction is worse than a missed one**, for this
  system specifically.
- Explain `_reconcile_cross`'s **authority rule** and the four quadrants it
  handles.
- Explain Stage 0c and why it is scoped by *placement* rather than a conditional.

---

## 2. Why now

Days 30 and 34 gave you both engines complete. `cross_engine_node` **calls them
both directly**, so it was unreadable before today. This closes Phase 10.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `semantic_engine_node`, citations | Day 30 | One half |
| `quant_engine_node`, `sql_verified` | Day 34 | The other |
| `speaker_role` from transcript chunking | Day 24 | Claim eligibility |
| `metric_anchor_phrases()` | Day 31 | Stage 0c |
| `confidence_node` caps only | Day 30 | The contradiction penalty |

---

## 4. Concept lesson

### 4.1 What cross-examination is for

Questions like:

> *"Does management's commentary on Blinkit align with consolidated revenue?"*
> *"Is what the CEO said about profitability consistent with the actual numbers?"*

**Neither path alone answers these.** Semantic retrieves the commentary and has
nothing to check it against. Quantitative returns a figure and cannot read the
commentary.

**So cross runs both, and then compares.**

**And the comparison is the novel part.** The textbook's synthesiser *merges*
results; this one looks for **disagreement** — and disagreement is a claim about
a company, which is why the bar for making it is set so high.

---

### 4.2 Quant first — and why two fixes failed before this one

```python
# QUANT RUNS FIRST — deliberate, and the fix for the cross-path
# self-contradiction. Previously semantic ran first and the two halves
# were assembled independently, so the semantic half wrote its answer
# from narrative chunks alone. Asked "does commentary align with FY26
# PAT?", it correctly reported that the excerpts contain no PAT figure —
# a true statement about ITS context window — and the quant template then
# appended "PAT was ₹366 Cr" directly underneath it.
```

**The output contradicted itself**, in two adjacent paragraphs.

**The two failed fixes are the lesson:**

> Two earlier attempts failed because both tried to **SUPPRESS** that sentence
> (post-hoc rewriting in `response_generator`, then a prompt instruction not to say
> it). Neither worked, because **the model was being asked to withhold something
> true about the evidence it was given.** The working fix is to **make it false**:
> run quant first and inject the verified figure into the synthesis context as an
> established fact, so the model writes one coherent answer with nothing left to
> contradict.

**Read that carefully.** The model was *correct*: the excerpts genuinely did not
contain the PAT figure. Instructing it to conceal a true observation put a general
instruction against a specific one, and lost — the same shape as the appended-rule
failures on Day 18.

**The fix changes the premise rather than the instruction.** Put the figure *in
context*, and the statement "the excerpts do not contain it" stops being true.

**And the reordering is justified as safe:**

> Safe to reorder: `quant_engine` reads only the DSL-relevant state fields
> (company / fiscal_year / quarter / financial_type / query) and **never touches
> `retrieved_chunks` or `citations`**. The two sub-engines are genuinely
> independent; **only the ASSEMBLY was coupled.**

---

### 4.3 Stage 0c — no metric anchor

```python
_ANCHOR_RE = re.compile(
    "|".join(rf"(?<!\w){re.escape(p)}(?!\w)"
             for p in sorted(metric_anchor_phrases(), key=len, reverse=True)),
    re.IGNORECASE,
)

def _query_lacks_metric_anchor(query: str) -> bool:
    """True if the raw query names no known metric in any vocabulary."""
    return not _ANCHOR_RE.search(query or "")
```

**Why it exists:**

> `GeminiDSLResponse.metric` is a **REQUIRED** field. A cross-routed query that
> names no metric therefore cannot produce "no metric" — the model **invents one**,
> it compiles, it executes, and it is appended with `sql_verified=True`.

`CAVEAT-004` again (Day 32), on the third path. The measured case is **PQ012**:
*"financial exposure to Paytm Payments Bank"* → `exceptional_items` → a ticked
₹−186 Cr, **stable across five runs**.

**And two implementation details worth extracting.**

**Word boundaries are `(?<!\w)...(?!\w)`, not `\b`:**

> many phrases end or begin with non-word characters ("d&a", "impairment of
> loans/investment in associates") where **`\b` asserts the opposite of what is
> wanted.**

`\b` is a boundary *between* a word and a non-word character — so at the end of
`"d&a"`, `\b` requires a word character next, which fails. The lookarounds assert
"not preceded/followed by a word character" directly.

**Scoped by placement, not a conditional:**

> **SCOPED TO THE CROSS PATH BY PLACEMENT, NOT BY A CONDITIONAL.** On
> path=quantitative the router has already asserted the user wants a number, and
> refusing there would risk legitimate queries phrased outside registry
> vocabulary. Here the quant half is an ADJUNCT to a qualitative answer, so
> suppressing it degrades to qualitative-only … **Living in this module means Path
> 2 is untouched by construction rather than by a check someone could later
> move.**

**A guard whose scope is enforced by which file it lives in.** A conditional could
be edited to apply elsewhere; a module boundary cannot be edited by accident.

**And what it does *not* do:**

```python
quant_result: dict = {}
quant_succeeded = False
```

It sets `quant_result` to `{}` so `dsl_object` copies as `None` — which tells
`_reconcile_cross` this query **never asked for a figure** (§4.7), rather than
that a metric was identified and could not be verified.

---

### 4.4 The eleven false contradictions

`contradiction.py`'s docstring:

> The first shipped version treated **EVERY crore figure in EVERY retrieved chunk**
> as a claim about the queried metric. Confirmed live 2026-07-30: the question
> *"Does ETERNAL's management commentary on profitability align with its actual PAT
> for FY26?"* produced **ELEVEN "severity: high" contradictions** against
> PAT = ₹366 Cr, including **+4730.6%, +7244.3% and −99.7%**. None were
> contradictions. They were cash-flow lines, Adjusted EBITDA and other line items
> that happened to share a chunk, differenced against an unrelated metric.
>
> Worse, the top-cited chunk was **page 33 — the Consolidated Statement of Cash
> Flows, which is part of the SAME document the `financials` row was extracted
> from.** The engine was flagging disagreement between a verified value **and its
> own source. Circular by construction.**

**Two distinct failures:**

1. **Every number treated as a claim** about the queried metric.
2. **The source compared against itself** — page 33 is where the number came from.

**And the blueprint's anticipated fix was insufficient:**

> Blueprint §25B's Trap 7 anticipated a narrower failure (an approximation like
> "approximately INR 12,000 crore" flagged against an exact INR 12,114 crore) and
> prescribed a tolerance threshold. **Tolerance is necessary but was never
> sufficient:** the real defect is comparing numbers that are **not about the metric
> at all.**

**A tolerance makes a wrong comparison *quieter*, not *right*.**

---

### 4.5 The three rules

**A. Narrative chunks only.**

```python
NARRATIVE_CHUNK_TYPES = frozenset({...})

def _is_narrative(chunk) -> bool: ...
```

> **FINANCIAL_STATEMENT and TABLE chunks are the extraction source, not
> independent claims about it.** A table of figures also has no prose tying any
> metric name to any number, so proximity anchoring below cannot work on it either.

**Two reasons in one rule:** circularity (a statement cannot contradict itself),
and mechanics (proximity anchoring needs prose).

**B. Metric proximity.**

```python
PROXIMITY_WINDOW = 120

def _metric_alias_pattern(sql_metric: str) -> Optional[re.Pattern]: ...
def _near(text: str, start: int, end: int, pattern: re.Pattern) -> bool: ...
```

> A figure is a claim about PAT **only if a PAT alias appears within
> `PROXIMITY_WINDOW` characters of it** ("PAT of INR 366 crore", "profit after tax
> was INR 366 crore"). **Aliases come from the shared registry … never a second
> hand-maintained list** — three parallel metric dicts is the exact split that file
> was created to end.

**C. Speaker eligibility.**

```python
CLAIMANT_SPEAKER_ROLES = frozenset({"management"})

def _speaker_permits_claim(chunk) -> bool: ...
def _is_claim_eligible(chunk) -> bool: ...
```

**Day 24's speaker-turn chunking, consumed here.** Only a **management** turn may
carry a company claim.

**Why:** an analyst's premise is not the company's position — and in the ETERNAL
transcript *"several such premises are DENIED by management in the next turn"*
(inventory days p10, A&P flat p9, orders per customer p8). Flagging an analyst's
wrong premise as a company contradiction would be a false claim about the company.

**Non-transcript chunks have `speaker_role="unknown"`** (Day 3), so this rule must
admit them — otherwise no filing chunk could ever carry a claim.

---

### 4.6 Why a false contradiction is worse than a missed one

```python
# A FALSE contradiction is worse than a missed one. This system's stated value
# is surfacing disagreement instead of fabricating certainty; fabricating
# disagreement is the one failure that directly inverts that claim. These rules
# are deliberately strict and will miss real contradictions phrased at a
# distance from the metric name. That trade is intentional.
```

**The argument is about *this* system, not about precision and recall in general.**

LedgerMind's claim is: *we surface disagreement rather than fabricating
certainty.* A false contradiction **fabricates disagreement** — the exact inverse
of the claim, done in the system's own voice, about a real company.

**A missed contradiction** leaves the user where they would be without the
feature.

**And the cost is stated rather than hidden:** the rules *"will miss real
contradictions phrased at a distance from the metric name. That trade is
intentional."*

---

### 4.7 `_reconcile_cross` — the authority rule and four quadrants

```python
# AUTHORITY RULE: for path="cross", THIS MODULE is the final word on
# confidence_tier / error / error_node. cross_engine.py's assignments are an
# INPUT to _reconcile_cross(), not a competing decision. Do not add a second
# copy of this rule to cross_engine — that is the _compute_derived_totals /
# validate_financial_identities failure class, and it has already cost this
# project real extraction bugs.
```

**And `cross_engine.py` says the same from its side:**

> **AUTHORITY:** the `confidence_tier` / `error` values set here are an INPUT to
> `response_generator._reconcile_cross()`, which is the final word for this path.
> Step 4's error-clearing below runs BEFORE `response_generator` and **was
> previously undone by it.** Do not re-implement reconciliation here.

**Two modules, each pointing at the other, agreeing on who decides.** That
redundancy is deliberate — a reader arriving at either file learns the rule.

**The four quadrants:**

| | quant verified | quant absent |
|---|---|---|
| **narrative discusses it** | both halves agree or contradict | qualitative only |
| **narrative silent** | `CROSS_NARRATIVE_SCOPE_NOTE` | Quadrant 3 — say nothing |

**And the notes, each for one quadrant:**

```python
CROSS_NARRATIVE_SCOPE_NOTE = (
    "The retrieved narrative excerpts do not discuss this metric directly. "
    "The figure below is drawn from the extracted financial statements.")

CROSS_NO_VERIFIED_FIGURE_NOTE = (
    "No SQL-verified figure is available for the metric identified in this "
    "question, so the above reflects narrative disclosure only. Any figures "
    "appearing in it are quoted from the excerpts and have not been verified "
    "against the extracted financial statements.")

CROSS_SYNTHESIS_UNAVAILABLE_NOTE = (
    "The qualitative half of this cross-examination could not be produced — "
    "language model synthesis was unavailable on all providers. The verified "
    "figure below is unaffected: it comes directly from the extracted "
    "financial statements and required no language model.")
```

**Each names precisely which half is missing and what the remaining half is worth.**
The third is the sharpest: an LLM outage does not weaken a SQL-verified figure,
and it says so.

**And this is why Stage 0b writes a partial `dsl_object`** (Day 34):

> `dsl_object` presence is how `response_generator`'s cross reconciliation tells "a
> metric was identified but produced no verified figure" (**a real gap worth
> disclosing**) apart from "this query never asked for a figure" (**nothing to
> disclose — emitting a gap note there is noise**).

```python
# Copied UNCONDITIONALLY.
state["dsl_object"] = quant_result.get("dsl_object")
```

**Unconditional, even on failure**, because absence is the signal.

---

### 4.8 `CROSS_SCOPE_INSTRUCTION` — rewritten after failing live

```python
# Rewritten after the first version failed live. That version told the model
# not to say the documents lack the figure — an instruction to withhold
# something TRUE about its own context, competing against SYNTHESIS_SYSTEM_
# PROMPT's earlier and more concrete "say what is and isn't covered". The
# earlier, more specific rule won, exactly as in the EBITDA silent-
# substitution bug. This version doesn't ask for a withholding: the figure is
# now IN context, so there is nothing to conceal.
```

**"The earlier, more specific rule won."** Day 18's principle, with a third
instance.

The current instruction:

```
CROSS-EXAMINATION CONTEXT: the excerpts are narrative disclosure text. Any
verified figures appear in a separate block at the end of the user message; they
come from extracted financial statements and are already verified. Use them as
established fact — do not question their availability ... Refer to a verified
figure qualitatively (e.g. "the reported profit", "the figure below") rather than
restating the numeral — the system appends the exact figure verbatim after your
answer, and a restatement risks transcription drift.
```

**"A restatement risks transcription drift."** The model is told to refer to the
figure *qualitatively*, because the system appends the exact number itself. **The
LLM never transcribes a verified figure** — a fourth application of "LLMs never do
math", extended to "LLMs never re-type a verified number."

**And the injection**, from `_generate_semantic_response` (Day 30):

```python
# Verified facts are supplied BEFORE the model writes, not reconciled
# after. Placed last so they are the most recent thing in context. This is
# not a violation of "LLMs never do math" — the figure is a constant the
# SQL layer already computed and verified; the model reports it, it does
# not derive it, and it is instructed below not to restate the numeral.
```

**And one formatter, reused:**

```python
verified_facts = ""
if state.get("sql_verified") and state.get("sql_result"):
    verified_facts = _format_quant_response(state)
```

> Deliberately **ONE formatter**: if the injected fact and the appended line came
> from separate code paths they could drift, which is the
> `_compute_derived_totals` / `validate_financial_identities` failure class this
> project has already paid for once.

---

### 4.9 Subsidiary resolution

```python
SUBSIDIARY_TO_PARENT = {"BLINKIT": "ETERNAL", "HYPERPURE": "ETERNAL"}

def resolve_parent_entity(entity): ...
def resolve_parent_entities(entities: list) -> list:
    """
    F14: the list form. Maps every named issuer through the subsidiary table,
    preserving order and dropping duplicates -- two subsidiaries of one parent
    must not produce that parent twice in an any-of filter.
    """
```

**Blinkit has no standalone filing** — its data lives inside ETERNAL's
consolidated statements. So the entity is mapped to its parent **for retrieval and
SQL**, while the original name is preserved for the answer.

**Deduplication matters:** "Blinkit and Hyperpure" would otherwise produce
`["ETERNAL", "ETERNAL"]` in a `MatchAny` filter (Day 27).

**And note the scalar version is kept:**

> The scalar version above is kept and unchanged: it is the single-entity contract
> the rest of this module's DSL handling still speaks.

---

## 5. The actual LedgerMind files

```
File:  backend/app/engines/cross_engine.py (294 lines)
Entry: cross_engine_node(state) -> QueryState
       resolve_parent_entity / resolve_parent_entities
       _query_lacks_metric_anchor  (Stage 0c)
Calls: quant_engine_node FIRST, then semantic_engine_node
Note:  reuses both nodes directly — "any fix to those modules automatically
       benefits Path 3"

File:  backend/app/engines/contradiction.py (456 lines)
Entry: detect_contradictions(chunks, sql_value, sql_metric, yoy_pct)
       detect_magnitude_contradictions · detect_directional_contradictions
       _is_claim_eligible · _is_narrative · _speaker_permits_claim
       extract_numeric_claims · extract_direction · has_approximation_language
Consts: MAGNITUDE_TOLERANCE_PCT 5.0 · SEVERITY_MEDIUM_PCT 10.0
        PROXIMITY_WINDOW 120 · CLAIMANT_SPEAKER_ROLES {"management"}
No LLM. Pure regex + arithmetic.

File:  backend/app/engines/response_generator.py — _reconcile_cross + 4 notes
```

---

## 6. Deep walkthrough — `cross_engine_node`

**STATE BEFORE.** Post-router, `path="cross"`.

**Step 0 — subsidiary resolution.**

```python
original_entities = state.get("companies") or []
resolved_entities = resolve_parent_entities(original_entities)
```

**Step 1 — Stage 0c, then quant.**

```python
if _query_lacks_metric_anchor(state["query"]):
    quant_result: dict = {}
    quant_succeeded = False
else:
    quant_state = dict(state)
    quant_state["companies"] = resolved_entities
    quant_result = quant_engine_node(QueryState(**quant_state))
    quant_succeeded = quant_result.get("error") is None and quant_result.get("sql_verified")
```

**`dict(state)` then `QueryState(**quant_state)`** — a copy with the resolved
entities substituted, so the sub-engine's mutations do not touch the caller's
state (Day 3: nodes mutate in place).

**`quant_succeeded` requires both** no error **and** `sql_verified` — because
`sql_verified` can be `False` without an error (a compute function returning an
error dict, Day 33).

**Step 2 — copy `dsl_object` unconditionally** (§4.7).

**Step 3 — semantic, after quant.**

```python
semantic_state = dict(state)
semantic_state["companies"] = resolved_entities
semantic_result = semantic_engine_node(QueryState(**semantic_state))

state["retrieved_chunks"] = semantic_result["retrieved_chunks"]
state["citations"]        = semantic_result["citations"]
qual_confidence_score     = semantic_result["confidence_score"]
qual_confidence_tier      = semantic_result["confidence_tier"]
```

**The semantic confidence is held in local variables**, not written to state —
because §4.7's reconciliation decides the final tier.

```python
if semantic_result.get("error") == "low_confidence_refusal":
    logger.warning("CrossEngine: semantic side returned low confidence")
    # Don't hard-fail yet — quant side might still produce a usable answer.
```

**Step 4 — contradiction detection.**

```python
sql_value = yoy_pct = None
metric_label = ""
if quant_succeeded and state["sql_result"]:
    result_row = state["sql_result"][0]
    if "value" in result_row:
        sql_value = float(result_row["value"]); metric_label = result_row.get("metric", "")
    elif "yoy_pct" in result_row:
        yoy_pct = result_row.get("yoy_pct"); sql_value = result_row.get("current_value")
        metric_label = result_row.get("metric", "")

contradictions = []
if state["retrieved_chunks"] and (sql_value is not None or yoy_pct is not None):
    contradictions = detect_contradictions(chunks=..., sql_value=..., sql_metric=..., yoy_pct=...)
else:
    logger.info("CrossEngine: skipping contradiction detection — insufficient data ...")
```

**`"value" in result_row` versus `"yoy_pct" in result_row`** — shape-sniffing,
because `point_in_time` returns raw rows while the others return computed dicts
(Day 33). **An implicit contract between the compute functions and this
consumer**, enforced by nothing.

**Step 5 — combined confidence.**

```python
if quant_succeeded:
    combined_score = min(qual_confidence_score, 1.0)
    combined_tier = qual_confidence_tier   # quant side is always "high" when verified
else:
    # Quant unavailable — fall back entirely to qualitative confidence,
    # but cap at medium since cross-examination promised both sides.
    combined_score = min(qual_confidence_score, 0.75)
    combined_tier = "medium" if qual_confidence_tier == "high" else qual_confidence_tier
```

**"Cross-examination promised both sides."** Delivering one half is a partial
answer, and the tier says so.

**Step 6 — clear the sub-engines' errors.**

```python
if state["retrieved_chunks"] or quant_succeeded:
    state["error"] = None
    state["error_node"] = None
```

**A sub-engine's refusal is not the cross path's refusal** — if either half
produced something, the path succeeded partially. And per the authority rule,
`_reconcile_cross` may still set an error afterwards.

---

### 6.1 `detect_contradictions`

```python
def detect_contradictions(chunks, sql_value, sql_metric, yoy_pct) -> List[ContradictionFlag]:
```

**Magnitude:** for each eligible chunk, extract crore figures **anchored near a
metric alias**, compare with `sql_value`, and flag beyond
`MAGNITUDE_TOLERANCE_PCT = 5.0`.

```python
_CRORE_PATTERN = re.compile(...)
_APPROXIMATION_SIGNAL = re.compile(...)

def has_approximation_language(text: str) -> bool: ...
```

**Approximation language widens the tolerance** — "approximately ₹12,000 crore"
against ₹12,114 crore is agreement, not disagreement. Blueprint Trap 7's fix,
retained as one rule among three.

**Directional:** compare directional language against the **sign** of `yoy_pct`.

```python
_POSITIVE_DIRECTION = re.compile(...)
_NEGATIVE_DIRECTION = re.compile(...)

def extract_direction(text, anchor=None) -> Optional[str]: ...
```

**Both take an `anchor`** — the metric alias pattern — so direction words are only
read near the metric they describe.

**Severity:**

```python
SEVERITY_MEDIUM_PCT = 10.0   # 10-20% off → medium severity

def _classify_severity(delta_pct: float) -> str: ...
```

**And severity drives `confidence_node`** (Day 30): a **high**-severity
contradiction caps the tier at `medium`.

---

## 7. Data flow

```
"Does management's commentary on profitability align with FY26 PAT?"
        ▼ router → path="cross"                                (Day 36)
        ▼ cross_engine_node
        │
        ▼ resolve_parent_entities(["BLINKIT"]) → ["ETERNAL"]
        │
        ▼ STAGE 0c: _query_lacks_metric_anchor(RAW QUERY)
        │    no anchor → quant_result = {} · quant_succeeded = False
        │                dsl_object copies as None → "never asked"
        │
        ▼ QUANT FIRST                                          (Day 34)
        │    quant_engine_node(copy with resolved entities)
        │    → sql_verified, sql_result
        │
        ▼ state["dsl_object"] = quant_result.get("dsl_object")   UNCONDITIONAL
        │
        ▼ SEMANTIC SECOND                                      (Day 30)
        │    semantic_engine_node(copy with resolved entities)
        │    → retrieved_chunks, citations
        │    (its confidence held in LOCALS, not written)
        │
        ▼ detect_contradictions(chunks, sql_value, sql_metric, yoy_pct)
        │    ┌── ELIGIBILITY ────────────────────────────────┐
        │    │ narrative chunk?          not a statement/table│
        │    │ speaker permits a claim?  management only      │
        │    │ figure within 120 chars of a metric alias?     │
        │    └───────────────────────────────────────────────┘
        │    magnitude  : |claim - sql| / sql > 5%  (widened if approximate)
        │    directional: direction word vs sign(yoy_pct)
        │    severity   : >20% high · 10-20% medium
        │
        ▼ combined confidence
        │    quant ok → qualitative tier
        │    quant absent → cap at MEDIUM ("promised both sides")
        │
        ▼ clear sub-engine errors if either half produced something
        │
        ▼ confidence_node — high-severity contradiction caps at medium (Day 30)
        │
        ▼ response_generator, path == "cross"
        │    verified_facts = _format_quant_response(state)     ONE formatter
        │    _generate_semantic_response(..., extra_instructions=CROSS_SCOPE_INSTRUCTION,
        │                                     verified_facts=verified_facts)
        │    _reconcile_cross(...)   ← THE AUTHORITY for tier/error on this path
        │       quadrant → one of three notes, or none
        │    body + citations_block + contradiction_block
```

---

## 8. Engineering decision — reuse both engines, compare deterministically

**Problem.** Answer a question that needs narrative *and* a verified figure, and
report disagreement without fabricating it.

**Decision.** Call both nodes directly, quant first; detect contradictions with
regex and arithmetic under three eligibility rules; reconcile availability in one
place.

`ENGINEERING_DECISIONS.md` **ED-019**, **ED-020**.

| Alternative | Why not |
|---|---|
| **Duplicate the engines' logic** | Any fix would have to land twice. Reuse means *"any fix to those modules automatically benefits Path 3"* |
| **Semantic first** | Produced a self-contradicting answer; two suppression fixes failed |
| **Suppress the contradicting sentence** | Asking the model to withhold something **true** about its context. Lost to an earlier, more specific rule — twice |
| **An LLM to detect contradictions** | Non-deterministic, and a fabricated disagreement is the worst failure available here |
| **Tolerance alone** (blueprint Trap 7) | Makes a wrong comparison quieter, not right — eleven false flags |
| **Reconcile in `cross_engine`** | It runs before `response_generator`, which was **undoing** its decisions. One authority, one place |

**Trade-offs accepted.**

- **Deliberately strict**, and will miss real contradictions phrased at a distance
  from the metric name. **Stated, not hidden.**
- **`SUBSIDIARY_TO_PARENT` is hand-maintained** — a second registry-like structure,
  small and explicit.
- **`metric_anchor_phrases()` matches substrings, not words** —
  `IMPLEMENTATION_DELTAS.md` §D records it as a latent risk.
- **The result-shape sniffing** (`"value" in row`) is an unenforced contract.
- **~~`cross` is BUILT but UNMEASURED against the golden set~~** — **CORRECTED
  2026-08-23, while writing Day 43. This day read `IMPLEMENTATION_DELTAS.md`
  §C's HEADING and missed the correction directly beneath it**, which begins
  *"Superseded 2026-08-02. The heading is now wrong in both directions and is
  kept only so the correction has something to attach to."* The measured
  position: a `cross_examination` category exists with **6 questions** (Q053,
  Q054, PQ018, PQ019, PQ020, ETQ001), `eval_runner.score_result` has a
  **dedicated `cross_examination` branch**, and **6 questions assert
  `expected_path="cross"`**. What remains true is narrower and is stated in §C:
  **three quadrants are unassertable**, and *a genuine contradiction does not
  exist in this corpus* — closing that needs a **document**, not a cleverer
  question. See Day 43 §4.6, which reads §C properly.

**Current validity.** The rules are well-evidenced. The path's *coverage* is
partial — five of the eight reconciliation outcomes are asserted; a real
contradiction is not, because the corpus contains none.

**At 10×.** More subsidiaries means `SUBSIDIARY_TO_PARENT` grows by hand — the
same shape as `CAVEAT-019` (company onboarding requires a code edit).

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| Eleven high-severity contradictions | Historic: every figure treated as a claim |
| A figure contradicting its own source | Historic: statement chunks eligible |
| An analyst's premise flagged as a company claim | Speaker eligibility, or transcript chunking not applied (Day 24) |
| A self-contradicting answer | Historic: semantic ran first |
| A refusal beside a ticked figure | Historic: the generic refusal detector applied to cross (Day 30) |
| A gap note on a purely qualitative question | `dsl_object` copied conditionally |
| An invented metric verified on the cross path | Stage 0c missed it |
| `["ETERNAL","ETERNAL"]` in a filter | Subsidiary dedup removed |
| Contradictions skipped silently | No chunks, or no `sql_value`/`yoy_pct` — logged at INFO |

---

## 10. Hands-on experiment

### Experiment 1 — Stage 0c and its boundaries

```bash
docker compose exec -T backend python -c "
from app.engines.cross_engine import _query_lacks_metric_anchor
from app.metrics.registry import metric_anchor_phrases
ph = metric_anchor_phrases()
print('anchor phrases:', len(ph))
print('samples       :', sorted(list(ph))[:8])
print()
for q in ['Does commentary align with FY26 PAT?',
          'What is Paytm financial exposure to Paytm Payments Bank?',
          'Tell me about d&a for Eternal',
          'impairment of loans/investment in associates',
          'Is management optimistic about the future?']:
    print(f'  lacks_anchor={_query_lacks_metric_anchor(q)!s:5}  {q!r}')
print()
print('Row 2 is PQ012 — no metric named, so the quant half is SKIPPED.')
"
```

### Experiment 2 — `\\b` versus the lookarounds

```bash
docker compose exec -T backend python -c "
import re
for phrase in ['d&a', 'impairment of loans/investment in associates']:
    b   = re.compile(rf'\b{re.escape(phrase)}\b', re.I)
    look = re.compile(rf'(?<!\w){re.escape(phrase)}(?!\w)', re.I)
    text = f'Discussion of {phrase} for the period.'
    print(f'{phrase!r}')
    print(f'   \\\\b      match: {bool(b.search(text))}')
    print(f'   lookaround: {bool(look.search(text))}')
print()
print('\\\\b requires a WORD character adjacent. After \"a\" in \"d&a\" the next')
print('char is a space, so \\\\b fails. The lookarounds assert the opposite.')
"
```

### Experiment 3 — claim eligibility, rule by rule

```bash
docker compose exec -T backend python -c "
from app.engines.contradiction import (_is_narrative, _speaker_permits_claim,
    _is_claim_eligible, NARRATIVE_CHUNK_TYPES, CLAIMANT_SPEAKER_ROLES, PROXIMITY_WINDOW)
print('NARRATIVE_CHUNK_TYPES :', sorted(NARRATIVE_CHUNK_TYPES))
print('CLAIMANT_SPEAKER_ROLES:', sorted(CLAIMANT_SPEAKER_ROLES))
print('PROXIMITY_WINDOW      :', PROXIMITY_WINDOW)
print()
def ch(ct, sr): return {'chunk_type': ct, 'speaker_role': sr, 'text': 'PAT was INR 366 crore'}
for ct in ['MANAGEMENT_DISCUSSION', 'TEXT', 'FINANCIAL_STATEMENT', 'TABLE']:
    for sr in ['management', 'analyst', 'moderator', 'unknown']:
        c = ch(ct, sr)
        print(f'  {ct:22} {sr:10} narrative={_is_narrative(c)!s:5} '
              f'speaker={_speaker_permits_claim(c)!s:5} ELIGIBLE={_is_claim_eligible(c)}')
"
```

**Note that `unknown` must be admitted** — otherwise no filing chunk could ever
carry a claim.

### Experiment 4 — proximity anchoring

```bash
docker compose exec -T backend python -c "
from app.engines.contradiction import extract_numeric_claims, _metric_alias_pattern
anchor = _metric_alias_pattern('pat')
print('pat alias pattern:', anchor.pattern[:120] if anchor else None)
print()
texts = [
 'Profit after tax was INR 366 crore for the year.',
 'Revenue was INR 54,364 crore and cash flow from operations was INR 4,730 crore.',
 'PAT of INR 366 crore was reported. Separately, capex reached INR 1,200 crore.',
]
for t in texts:
    print(f'{t!r}')
    print(f'   all figures      : {extract_numeric_claims(t)}')
    print(f'   anchored to PAT  : {extract_numeric_claims(t, anchor=anchor)}')
print()
print('Row 2 has TWO crore figures and NEITHER is about PAT. Without anchoring,')
print('both would be differenced against PAT. That is how eleven appeared.')
"
```

### Experiment 5 — direction, tolerance and severity

```bash
docker compose exec -T backend python -c "
from app.engines.contradiction import (extract_direction, has_approximation_language,
    _classify_severity, MAGNITUDE_TOLERANCE_PCT, SEVERITY_MEDIUM_PCT)
print('MAGNITUDE_TOLERANCE_PCT:', MAGNITUDE_TOLERANCE_PCT)
print('SEVERITY_MEDIUM_PCT    :', SEVERITY_MEDIUM_PCT)
print()
for t in ['Profit after tax declined sharply during the year.',
          'PAT grew strongly, rising 68% year on year.',
          'Profit after tax was INR 366 crore.']:
    print(f'  direction={str(extract_direction(t)):9} approx={has_approximation_language(t)!s:5} {t!r}')
print()
for d in (3.0, 7.0, 12.0, 25.0, 4730.6):
    print(f'  delta {d:8.1f}%  -> severity {_classify_severity(d)}')
"
```

### Experiment 6 — a cross query, end to end

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ANALYST" -H "Content-Type: application/json" \
  -d '{"query":"Does Eternal management commentary on profitability align with its actual PAT for FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('path            :', d.get('path'))
print('sql_verified    :', d.get('sql_verified'))
print('citations       :', len(d.get('citations', [])))
print('contradictions  :', len(d.get('contradictions', [])))
for c in d.get('contradictions', []):
    print('   ', c.get('type'), c.get('severity'), c.get('delta_pct'))
print('confidence_tier :', d.get('confidence_tier'))
print()
print(d.get('response_text', '')[:900])
"
```

**This is the exact question that produced eleven false contradictions.** Count
them now.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/cross_engine.py` and
`backend/app/engines/contradiction.py`:

1. Why does quant run first? Name the two fixes that failed and say what was
   wrong with both.
2. Name the three eligibility rules. For each, give a false contradiction it
   prevents.
3. Find the sentence about a false contradiction versus a missed one. Why is that
   argument specific to *this* system?
4. Stage 0c is "scoped by placement, not by a conditional". What does that buy?
5. Both `cross_engine.py` and `response_generator.py` carry an authority comment.
   Who decides, and why is it written twice?

---

## 12. Self-check questions

**Basic**
1. What is cross-examination for?
2. Which engine runs first, and why?
3. What are the three eligibility rules?
4. What is `PROXIMITY_WINDOW`?
5. Which speaker roles may carry a claim?

**Code**
6. What does Stage 0c check, and what does it set when it fires?
7. Why is `dsl_object` copied unconditionally?
8. How does the code tell a `point_in_time` result from a computed one?
9. What does `resolve_parent_entities` do that the scalar version does not?
10. Where does the final `confidence_tier` for this path get decided?

**Why**
11. Why did suppressing the contradicting sentence fail, twice?
12. Why are statement and table chunks ineligible?
13. Why is a false contradiction worse than a missed one *here*?
14. Why cap at medium when the quant half is unavailable?
15. Why is the verified figure injected before generation rather than appended
    after?

**Debugging**
16. Eleven high-severity contradictions on one answer. Which rule is missing?
17. An answer says the excerpts lack a figure, then states it. What is wrong?
18. A gap note appears on a purely qualitative question. What broke?

**System design**
19. `SUBSIDIARY_TO_PARENT` is hand-maintained. Propose a better home and say what
    it costs.
20. **[PREMISE CORRECTED — see §8 and the key.]** The cross path *is* measured,
    by six golden questions. But **no golden question asserts a real
    contradiction**, because the corpus contains none. Design that measurement,
    and say why writing a cleverer question cannot produce it.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. So the verified figure is **in the synthesis context** when the model writes,
   making "the excerpts do not contain this figure" **false** rather than
   suppressed. **The two failed fixes:** post-hoc rewriting in
   `response_generator`, and a prompt instruction not to say it. Both were wrong
   for the same reason — **the model was being asked to withhold something true
   about the evidence it was given**, and a general instruction to withhold lost to
   `SYNTHESIS_SYSTEM_PROMPT`'s earlier, more concrete *"say what is and isn't
   covered."*
2. **(a) Narrative chunks only** — prevents a figure being differenced against the
   statement table it was extracted from (page 33, the cash-flow statement,
   *circular by construction*). **(b) Metric proximity** — prevents cash-flow lines
   and Adjusted EBITDA in the same chunk being differenced against PAT (+4730.6%,
   +7244.3%). **(c) Speaker eligibility** — prevents an analyst's premise, which
   management **denied in the next turn**, being reported as a company claim.
3. Because **this system's stated value is surfacing disagreement instead of
   fabricating certainty.** A false contradiction *fabricates disagreement* — the
   exact inverse of the claim, made in the system's own voice, about a real
   company. A missed one merely leaves the user where they would be without the
   feature. In a system whose product were recall, the trade would run the other
   way.
4. It means **Path 2 is untouched by construction rather than by a check someone
   could later move.** A conditional (`if path == "cross"`) inside `quant_engine`
   could be edited, inverted or made to apply elsewhere; a guard that lives in
   `cross_engine.py` cannot accidentally affect the quantitative path, because it
   is not on that path's code.
5. **`response_generator._reconcile_cross` decides** — it is the final word on
   `confidence_tier`, `error` and `error_node` for `path="cross"`.
   `cross_engine`'s assignments are an **input**. Written twice because a reader
   arriving at either file must learn the rule: `cross_engine` warns *"do not
   re-implement reconciliation here"* (its Step 4 error-clearing was previously
   being undone downstream), and `response_generator` warns *"do not add a second
   copy of this rule to cross_engine"* — naming the
   `_compute_derived_totals`/`validate_financial_identities` failure class.

### §12 — Basic

1. Questions needing **both** narrative and a verified figure — *"does the
   commentary align with the numbers?"*
2. **Quant.** So the verified figure is in the synthesis context as established
   fact.
3. Narrative chunks only; a management speaker; a figure within
   `PROXIMITY_WINDOW` of a metric alias.
4. **120 characters.**
5. `CLAIMANT_SPEAKER_ROLES = {"management"}` — plus non-transcript chunks, which
   carry `"unknown"` and must be admitted.
6. Whether the raw query names any known metric phrase. When it fires it sets
   `quant_result = {}` and `quant_succeeded = False`, so `dsl_object` copies as
   `None`.
7. Because **absence is the signal**: `dsl_object is None` tells `_reconcile_cross`
   the query *never asked for a figure*, versus a present object meaning a metric
   was identified but produced no verified figure.
8. Shape-sniffing: `"value" in result_row` (a raw `point_in_time` row) versus
   `"yoy_pct" in result_row` (a computed dict).
9. Maps **every** issuer through the subsidiary table, preserving order and
   **dropping duplicates** — two subsidiaries of one parent must not produce that
   parent twice in an any-of filter.
10. In `response_generator._reconcile_cross`.

### §12 — Why

11. See §11 Q1.
12. Because they are **the extraction source, not independent claims about it** —
    comparing a value against the table it came from is circular. And mechanically:
    a table has no prose tying a metric name to a number, so proximity anchoring
    cannot work on it.
13. See §11 Q3.
14. Because *"cross-examination promised both sides."* Delivering only the
    qualitative half is a partial answer, and the tier must say so rather than
    presenting it as a complete cross-examination.
15. So the model writes **one coherent answer** with nothing left to contradict.
    Appending afterwards is what produced the self-contradiction. And the model is
    instructed to refer to the figure **qualitatively** rather than restating the
    numeral, because *"a restatement risks transcription drift"* — the system
    appends the exact figure verbatim itself.

### §12 — Debugging

16. **Metric proximity** — every crore figure in every retrieved chunk is being
    treated as a claim about the queried metric. (And check narrative eligibility
    too: if a statement chunk is included, one of the eleven will be the source of
    the number itself.)
17. **Quant is not running before semantic**, or the verified figure is not
    reaching `verified_facts` — check `sql_verified` and `sql_result`, since
    `verified_facts` is only populated when both are truthy. If the figure is
    absent from context, the model correctly reports that the excerpts lack it, and
    the appended template then states it.
18. **`dsl_object` is being copied conditionally** (only on success) rather than
    unconditionally, so a query that never asked for a figure now looks identical
    to one where a metric was identified and could not be verified —
    and `_reconcile_cross` emits `CROSS_NO_VERIFIED_FIGURE_NOTE` where it should
    say nothing.

### §12 — System design

19. **Better home:** `app/ingestion/entity_resolver.py`, alongside
    `COMPANY_REGISTRY` — a subsidiary is an entity fact, and `CompanyProfile`
    could carry a `parent: Optional[str]` field, making the mapping *derived* from
    the same single source as aliases and tickers (Day 31's principle).
    `cross_engine` would then call an accessor rather than owning a dict.
    **What it costs:** `entity_resolver` lives in `app/ingestion/` and is already
    imported by the query path, so no new coupling — but it does mean a subsidiary
    is now visible to *ingestion*, which does not need it, and the registry gains a
    field that is `None` for most profiles. **What it does not fix:** onboarding
    still requires a code edit — the same shape as `CAVEAT-019`. Moving it makes
    the fact single-sourced; it does not make it data.
20. > **CORRECTION, 2026-08-23.** The original version of this answer opened
    > *"`IMPLEMENTATION_DELTAS.md` §C records the cross path as built but
    > unmeasured, and the reason is that there is no golden category for it."*
    > **Both halves are false**, and §C says so itself directly under the heading
    > this day quoted. **The design below was right; its premise was wrong**, and
    > it is kept rather than deleted because most of it is what the repository
    > actually built — which is the useful thing to notice.

    **What already exists** (measured 2026-08-23): a `cross_examination` category
    with **six** questions, a dedicated `score_result` branch asserting
    `expected_contradictions`, `expected_sql_verified` and `expected_tier_low`,
    and **six** questions carrying `expected_path="cross"`. The structural
    assertions the answer below proposed — `len(contradictions)`, which note
    appears, the tier — are exactly what that branch checks. **Assertions on
    structure rather than on answer text**, for the reason given below, and PQ019
    carries **no keywords at all by design**, because a scoped negative has no
    stable vocabulary.

    **What is genuinely missing, and why no question can supply it.** §C:
    *a genuine contradiction does not exist in this corpus.* Three zero-quota
    retrieval probes looked for one. Every profitability-framed query returns
    financial statements, because in a results filing that is where profit
    lives, while the narrative discusses NOV, order mix and store counts —
    **the two halves address different subjects, so there is nothing to disagree
    about.** Closing this needs a **document** containing a real disagreement (an
    earnings-call transcript, an investor presentation making directional
    claims), **not another question**. And §C names the trap: *"a manufactured
    contradiction would train the system to fire on approximation, which is
    Trap 7 inverted and worse than no test at all."*

    **The original design, retained as written:** add questions whose correct
    outcome is known — one where the commentary genuinely **agrees** (expect zero
    contradictions), one where it genuinely disagrees (expect one, with a stated
    severity), one naming no metric (expect Stage 0c to skip the quant half and
    emit no gap note), and one where the metric is identified but unverifiable
    (expect `CROSS_NO_VERIFIED_FIGURE_NOTE`). **What makes it hard:** the
    *correct* answer for a contradiction question is not a value but a
    **judgement**, so the assertions have to be on structure rather than on
    answer text — which is an advantage, since structure is checkable without
    keyword matching, the fragile part `CLAUDE.md` §5 warns about. **Cost:** each
    question is two LLM calls plus the DSL call, so ten questions is ~30 against
    500/day, and it needs approval per `CLAUDE.md` §5.

    **The lesson this correction actually teaches**, and the reason it is left
    visible: **a heading is not a record.** §C's heading has been wrong since
    2026-08-02 and is *deliberately retained* so the correction has something to
    attach to — a convention this day read past. **Read to the end of an entry
    before quoting its title.**

---

## 14. MUST REMEMBER

```text
- QUANT RUNS FIRST — so the verified figure is IN CONTEXT when the model writes
- Two earlier fixes failed by asking the model to WITHHOLD SOMETHING TRUE
- THREE eligibility rules: narrative chunks · management speaker ·
  metric alias within 120 chars
- Tolerance alone was NECESSARY BUT NEVER SUFFICIENT — eleven false flags
- A FALSE contradiction is worse than a missed one — it inverts the system's
  stated value
- Statement/table chunks are the SOURCE, not independent claims — circular
- _reconcile_cross is THE AUTHORITY for tier/error on path="cross"
- dsl_object is copied UNCONDITIONALLY — its ABSENCE is the signal
- Stage 0c is scoped BY PLACEMENT, not by a conditional
- Word boundaries use (?<!\w)...(?!\w), NOT \b — "d&a", "loans/investment"
- No LLM anywhere in contradiction.py. Pure regex and arithmetic
```

## 15. MUST UNDERSTAND

```text
- Why changing the PREMISE beat changing the INSTRUCTION: the model was right,
  and a general instruction to conceal lost to an earlier specific one
- Why comparing a figure against its own source is circular by construction
- Why the precision/recall trade here is decided by the system's STATED VALUE,
  not by a general preference
- Why one authority in one place, documented at both ends, is the answer to
  two modules that could each decide
- Why absence of a field can be a signal, and why that requires copying it
  unconditionally
- Why a guard's scope enforced by MODULE BOUNDARY is stronger than a conditional
```

---

## 16. This connects to

```text
Day 35 — the graph
Day 36 — the router
   ↓
Day 37 — cross-examination                        ← END OF PHASE 10
   ↓
Day 38 — the frontend begins
```

Forward references:

- `contradictions` rendered in the UI → **Day 40**
- Severity capping confidence → **Day 30** (already read)
- `speaker_role` produced by the chunker → **Day 24** (already read)
- `metric_anchor_phrases()`'s substring risk → **Day 43**
- The cross path's **actual** golden coverage, and the one outcome no question
  can assert → **Day 43**
