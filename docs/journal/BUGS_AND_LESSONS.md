# LedgerMind — Bugs and the Lessons Behind Them

Each entry: **symptom → expected → root cause → investigation → fix → why the
fix works → lesson → prevention.**

Bugs 001–012 are reconstructed from the record — code comments,
`docs/IMPLEMENTATION_DELTAS.md`, and `docs/audit/repo_audit_20260811.md`. Each
cites its source so the evidence can be re-read. Entries from 013 on are found
during this documentation pass.

Read this file before proposing a fix in this codebase. Several of the entries
below are traps that were fallen into **more than once**.

---

## BUG-001 — Every semantic query classified "high confidence"

**Symptom.** `confidence_tier` came back `high` for essentially every semantic
query, including ones whose retrieved chunks were plainly unrelated to the
question. The same query occasionally returned `medium` instead — apparently at
random, run to run.

**Expected.** Weak retrieval should score `low` and refuse.

**Root cause.** A single threshold pair (`-4.5` / `-7.5`) was applied to scores
from **either** reranker backend. Those numbers were calibrated for the local
ONNX cross-encoder's raw logits (≈ −12…+2). **Cohere's relevance score is 0–1,
which is always ≥ −4.5.** So any Cohere-scored query was classified `high`
unconditionally. The run-to-run variation was the *correct* behaviour leaking
through whenever a Cohere API hiccup sent the query to the local fallback.

**Investigation.** Debug logging (`COHERE_CALIBRATION`, since removed) recorded
real production scores across all 83 golden-dataset questions. Every genuine
"high" scored ≥ 0.88; the one genuine "medium" — Q031, an ambiguous cross-period
question — scored 0.4656, correctly below 0.5.

**Fix.** Two threshold pairs, selected by the chunk's own
`reranker_backend` tag (`semantic_engine.py:36-59`). Cohere: 0.5 / 0.15. Local:
−4.5 / −7.5.

**Why it works.** The tier decision now uses the threshold pair belonging to the
scale the score is actually on.

**Lesson.**
> A threshold is meaningless without the scale it was calibrated against. If two
> implementations of the same interface return different units, the *unit* has to
> travel with the value.

**Prevention.** `reranker_backend` is now a field on `ChunkResult`, exposed at
admin tier, and `CLAUDE.md` carries a standing rule: *a score without its backend
is meaningless.*

**Source.** `semantic_engine.py:36-59`, `IMPLEMENTATION_DELTAS.md` §13.

---

## BUG-002 — A single query took 120 seconds and returned 200 OK

**Symptom.** The same query, measured three times: 3.07 s / **120.0 s** / 3.00 s.
Everything downstream of the LLM call completed in under a second. The request
returned 200 and looked entirely normal in the audit log.

**Expected.** A bounded response time, or a visible failure.

**Root cause.** **No Gemini call site set a timeout.** The 120 s was the client
giving up; the call was still running server-side.

**Investigation.** Render logs showed a **single** call — "AFC remote call 1 is
done" 78 s after "AFC is enabled" — ruling out SDK retry as the explanation.

**Fix.** One shared LLM client with explicit per-entry-point timeouts, plus a
Groq fallback (`app/llm/client.py`).

**Why it works — and why the two fixes are inseparable.** From the module
docstring:

> *"A timeout converts an unbounded hang into a catchable exception at a bound
> we choose, and only then is there anything for a fallback to catch. A fallback
> keyed on exceptions would never have fired on defect 1."*

The blueprint had promised the fallback (§17) since the beginning. It had **zero
call sites**: `config.py` declared a `groq_api_key` field that nothing read.

**Lesson.**
> An unbounded external call in a request path is a latent outage. And a
> resilience feature that has never been exercised is not a feature — check for
> call sites, not config fields.

**Prevention.** Three call sites would have meant three fallback ladders that
drift apart — the same failure class as this project's two formula copies and
three metric registries. One module, two entry points.

**Source.** `app/llm/client.py:1-49`.

---

## BUG-003 — Eleven false "high severity" contradictions in one answer

**Symptom.** *"Does ETERNAL's management commentary on profitability align with
its actual PAT for FY26?"* produced **eleven** `severity: high` contradictions
against PAT = ₹366 Cr, including +4730.6%, +7244.3% and −99.7%.

**Expected.** Zero, or at most one genuine disagreement.

**Root cause.** Two compounding errors.
1. **Every crore figure in every retrieved chunk was treated as a claim about
   the queried metric.** The flagged numbers were cash-flow lines, Adjusted
   EBITDA and other line items that merely shared a chunk.
2. **The top-cited chunk was page 33 — the Consolidated Statement of Cash
   Flows**, part of the *same document* the `financials` row was extracted from.
   The engine was flagging disagreement between a verified value and **its own
   source**. Circular by construction.

**Investigation.** Reading the flagged claims one by one against their chunks.

**Fix.** Three constraints (`contradiction.py:36-52`):
- **A.** Narrative chunk types only. `FINANCIAL_STATEMENT` and `TABLE` are the
  extraction source, not independent claims about it.
- **B.** Metric proximity — a figure is a claim about metric M only if an alias
  of M appears within `PROXIMITY_WINDOW = 120` characters.
- **C.** ±5% tolerance (blueprint Trap 7).

**Why it works.** Constraint A removes the circularity; B removes the
category error of differencing unrelated line items.

**Lesson — and this one generalises far beyond contradiction detection.**
> *"A FALSE contradiction is worse than a missed one. This system's stated value
> is surfacing disagreement instead of fabricating certainty; fabricating
> disagreement is the one failure that directly inverts that claim."*

Note also that the blueprint's Trap 7 had **anticipated** the tolerance problem
and prescribed exactly constraint C. Tolerance was necessary and **never
sufficient** — the real defect was comparing numbers that were not about the
metric at all.

**Prevention.** The rules are deliberately strict and will miss real
contradictions phrased at a distance from the metric name. *"That trade is
intentional."*

**Source.** `contradiction.py:15-53`, `IMPLEMENTATION_DELTAS.md` §C.

---

## BUG-004 — The system answered confidently about a company it does not hold

**Symptom.** *"What were Reliance Industries' revenue drivers in FY26?"*
returned **5 citations from TITAN/ZOMATO pages** at `tier=high`, score 0.7095.

**Expected.** A refusal.

**Root cause.** A chain of three, each individually reasonable:
1. `_build_filter` appends the company condition only `if company:` — so a null
   company silently widens the search to the **entire tenant**.
2. `ROUTER_SYSTEM_PROMPT` offered the model only two options: normalise to a
   ticker from the list, or return null if no company is mentioned. Having seen
   a company it could not normalise, the model **had no field in which to say
   so** — it explained the situation in `route_reason` prose and returned null,
   the only exit the schema allowed.
3. Nothing downstream distinguished "no company mentioned" from "named a company
   we do not hold".

**Investigation.** Measured 2026-08-11: `company=None`,
`company_unresolved=None`, and `route_reason` reading *"a company not in the
supported list"*. The model had **observed** the condition and had nowhere to put
it.

**Fix (F2, closed 2026-08-12, three steps).**
1. `RouterResponse` gained `company_mentioned` — the raw issuer name **as seen**,
   independent of resolvability.
2. `router_node` writes a refusal when nothing mentioned resolves.
3. `route_after_router` returns `"refused"` and `graph.py` maps it **directly to
   `audit_writer`**.

**Why step 3 was necessary.** Steps 1–2 wrote a refusal into the state — and the
query **still ran**, because `route_after_router` reads `path` and never looked
at `error`. **Writing a refusal is not the same as terminating.**

And it bypasses the tail because `confidence_node` *rescores*: measured, a
refused state reaching the tail came out `tier=high` at **0.7095**, putting a
confidence badge next to a refusal.

**Two near-misses worth recording.**
- Refusing on `company is None` would have been wrong: a **multi-entity** query
  nulls `company` even when every issuer resolves. Golden Q051 ("Eternal or
  Paytm") would have been refused. Hence `_resolve_mentioned_issuers`.
- A prompt instruction telling the model to always populate the new field was
  written, shipped, and then **removed** — the model populated it readily
  without one (three of three runs), so the instruction bought a guarantee that
  was never needed while adding a prompt block among the fields the path rules
  read. **What the removal did not undo:** the field stayed in `RouterResponse`,
  and the response schema is sent to the model on both providers — so the
  declaration was an input change on its own, prompt line or no prompt line.
  Measured and recorded in `IMPLEMENTATION_DELTAS.md` section D,
  "The response schema is part of the prompt".

**Lesson.**
> When a model returns null, ask whether your schema gave it any other way to
> say what it saw. A field that overloads null with two incompatible meanings
> will be read as the wrong one.

**Prevention.** `tests/test_router_refusal.py` — 11 tests covering the refusal
edge and multi-entity resolution.

**Still open.** The fix is **partial by construction** and says so: it fires only
when the model *returns* a name that fails the ticker gate. See CAVEAT-007 /
audit F14.

**Source.** `router.py:279-345`, `graph.py:95-101`, `CLAUDE.md`.

---

## BUG-005 — A real, correctly extracted figure became untraceable

**Symptom.** An answer stated *"warehousing capacity was 4.8 million square feet
in FY24"* and carried **one** citation — a transcript page containing no such
figure. Deterministic across two runs, at `confidence_score` 0.9969.

**Expected.** The claim cites the page it came from.

**Root cause.** A citation relevance floor dropped chunks scoring below 0.05
from `citations` — **while leaving them in `retrieved_chunks`**. The source was
ZOMATO FY24 AR p19 at score 0.0165. The model read it; the user could not see it.

```text
Citation floor: dropped 4 of 5 below 0.05 |
  scores=[0.0419, 0.0219, 0.0165, 0.0094] pages=[31, 4, 19, 4]
```

**Investigation.** Tracing the stated figure back to its source and finding it in
a chunk that had been suppressed from the evidence list.

**Fix.** The floor was **removed** (2026-08-08).

**Why it works.** The premise the floor rested on — *"a weak chunk in the model's
context is harmless and occasionally useful; the defect is presenting it as
evidence"* — was false. The floor did not prevent an unsupported claim; it
guaranteed the claim **could not be checked**.

**The subtle part.** The 0.05 constant itself was **not wrong**. The measurement
behind it stands: two score clusters with an empty band between them. What was
wrong was allowing `retrieved_chunks` and `citations` to diverge at all.

**The rejected alternative, and why.** Apply the floor to `retrieved_chunks` too.
That closes the hole by narrowing what the model reads on **every** semantic and
cross query — altering retrieval to fix an evidence-list problem, with a blast
radius far larger than the defect. (Three of the five chunks behind the Hyperpure
answer scored below 0.05.)

**Lesson.**
> A correct measurement can support a wrong decision. Ask what the number is
> being *used for*.
>
> The invariant: **if the model reads it, the user must be able to check it.**

**Prevention.** A 30-line comment at `semantic_engine.py:61-93` explaining why
it must not be reintroduced, plus an entry in `CLAUDE.md` §1.3.

**Source.** `semantic_engine.py:61-93`, `IMPLEMENTATION_DELTAS.md` §9.

---

## BUG-006 — The answer contradicted itself, and two fixes failed before one worked

**Symptom.**

> "The retrieved documents do not contain the PAT figure for FY26.
>  ETERNAL's consolidated PAT for FY26 was ₹366 Cr."

**Expected.** One coherent answer.

**Root cause.** The cross branch was literally `qual_body + quant_body`, with
neither half aware of the other. The semantic engine's scope is the top-k
**narrative** chunks; line items rarely appear in narrative text, so *"the
documents do not contain PAT"* was a **true, scoped** statement about those
chunks — printed as a global claim, then immediately contradicted.

**Investigation — including two failed fixes.**
1. **Post-hoc rewriting** in `response_generator` to strip the sentence. Failed.
2. **A prompt instruction** telling the model not to say it. Failed — it lost to
   `SYNTHESIS_SYSTEM_PROMPT`'s earlier and more concrete *"say what is and isn't
   covered."*

**Fix.** Two parts.
- **Reorder:** run `quant_engine` **first**, inject the verified figure into the
  synthesis context as established fact.
- **Reconcile:** `_reconcile_cross()` classifies **availability** as a four-way
  quadrant with no judgment calls, *before* asking whether the halves disagree.

**Why it works.** *"Both tried to SUPPRESS that sentence… Neither worked, because
the model was being asked to withhold something TRUE about the evidence it was
given. The working fix is to make it false."*

**Lesson.**
> Do not instruct a model to withhold something true. Change what is true.
>
> And: **contradiction detection answers "do the halves disagree?" That question
> is only meaningful once "does each half have anything?" resolves.** Asking them
> in the wrong order is what produced BUG-003's eleven false flags.

**Prevention.** An **AUTHORITY RULE** comment states that `response_generator`
is the final word on `confidence_tier`/`error` for the cross path, and
`cross_engine`'s assignments are an *input*, not a competing decision. A second
copy of that rule in `cross_engine` is explicitly forbidden.

**Source.** `cross_engine.py:131-150`, `response_generator.py:419-560`.

---

## BUG-007 — A total LLM outage was indistinguishable from a normal answer

**Symptom.** Both providers failed. The user received a fallback string plus a
raw excerpt. `confidence_tier` still read **"high"**, and `llm_provider` still
read **"gemini"** — inherited from the router call that had succeeded earlier.

**Expected.** A visible failure.

**Root cause.** Two independent conflations.
1. `_generate_semantic_response` returned `provider=None` for **two different
   situations**: "retrieval found nothing, no LLM was called" (the system worked;
   the corpus did not hold the answer) and "both providers failed" (the system
   did not work). Neither set `error`.
2. `llm_provider` was set by whichever call last **succeeded**. The synthesis
   floor returned `None`, which overwrote nothing.

**Investigation.** Measured 2026-07-31 during an eval sweep: the provider gate
reported 11/45 non-Gemini when the true figure was ≥ 13.

**Fix.** Three changes.
- `SynthesisOutcome` carries a three-valued `status`: `"ok"` /
  `"no_chunks"` / `"unavailable"`.
- `clear_llm_attribution()` nulls provider **and** model when no LLM produced
  the text.
- On `status == "unavailable"`: `confidence_tier = "low"`,
  `error = "synthesis_unavailable"`.

**Why it works.** `confidence_tier` was honest about *retrieval* and silent about
*synthesis*. The status field separates the two questions instead of encoding
both in one nullable field.

**Lesson.**
> `None` is not a status. If two situations need opposite responses, they need
> two values — and a degraded system must **look** degraded, not merely survive.

**Prevention.** `state.py:219-271` forbids direct assignment to
`llm_provider`/`llm_model`; attribution moves only toward *more degraded*.

**Source.** `response_generator.py:264-296`, `state.py:219-271`.

---

## BUG-008 — CRAG silently stopped working for every annual query

**Symptom.** Semantic queries with no quarter in them refused after burning all
their retries — and the retries returned **byte-identical** reranker scores
(0.1364 / 0.0633), three times.

**Expected.** Broadening should change the result set.

**Root cause.** Two bugs stacked.
1. A retry that drops a filter which was **never set** re-issues an identical
   query and consumes a retry slot for nothing.
2. The fix for (1) returned `None` to signal "nothing to broaden" — and the
   caller handled `None` with **`break`**. So a query with `quarter=None` (i.e.
   **every annual query**) exited the ladder at rung 1 and never reached rung 2,
   which drops `fiscal_year` and is real broadening.

**Investigation.** Confirmed live 2026-07-29 by comparing scores across retries.
Identical scores are the signature of an identical query.

**Fix.** `break` → `continue`, with the clarifying comment:
*"crag_count is the RUNG INDEX reached, not the number of retrievals actually
performed."*

**Lesson.**
> `break` and `continue` encode completely different beliefs about whether the
> **remaining** work is still worth doing. A no-op step is not a stopping
> condition.

**Note the mirror image.** `_generate_dsl` has the *inverse* situation and uses
`break` deliberately: when `LLMUnavailable` fires, the self-healing loop must
**stop**, because that loop exists to repair bad DSL and "no provider answered"
is not a DSL defect. The comment names the connection: *"the same conflation as
the CRAG break/continue bug, inverted."*

**Prevention.** Both call sites now carry a comment explaining the choice.

**Source.** `semantic_engine.py:227-238, 334-345`, `quant_engine.py:254-261`.

---

## BUG-009 — A metric held a completely different line item's value for weeks

**Symptom.** PAYTM consolidated `tax_expense` held **10** for FY26 annual when
the true figure was **30**. Three PAT identity checks failed and stood failing
for weeks.

**Expected.** `tax_expense` holds total tax expense.

**Root cause.** A three-stage silent chain.
1. PAYTM's P&L prints `'Deferred tax expense/ (credit)'` — four tokens. Both
   `"deferred tax"` (→ `deferred_tax`) and `"tax expense"` (→ `tax_expense`) are
   two-word subsets at exactly **0.50** coverage, so both cleared the floor and
   **tied**.
2. The tie was broken by **dict insertion order** — i.e. declaration order in
   `ALL_METRICS`. `tax_expense` is declared earlier, so it won. Nothing recorded
   that a choice had been made.
3. The deferred row, now labelled `tax_expense`, appeared first (page 8) and
   claimed the slot in `seen_keys`. When the **genuine** `'Total Tax expense'`
   row arrived, first-wins discarded it **without a trace**.

**Investigation.** Chasing three standing identity failures back through the
extraction chain.

**Fix.** Three changes, and note what each does:
- A three-word alias `"deferred tax expense"` — which wins outright, resolving
  *this* collision.
- A **`[METRIC TIE]` warning** when tied aliases name *different* canonicals,
  naming the shared tokens.
- A **`[DISCARDED ROW]` warning** when a `seen_keys` collision has a *different*
  value.

**Why it works.** The alias fixes the instance. The two log lines make the
**class** visible: *"the next such collision deserves one log line rather than a
multi-session diagnosis."*

**Deliberately NOT changed.** Neither log changes the outcome. Which row is
correct is a per-document judgement, and the real fix is normally an alias edit
so the two stop colliding at all. *"This makes the collision VISIBLE; it does not
guess."*

Also recorded: a static scan found **189** same-length alias pairs mapping to
different canonicals and sharing a word. That is an upper bound, not a risk
count — which ties are *reachable* depends on the labels real documents contain,
which is exactly what the log measures and a static scan cannot.

**Lesson.**
> When two candidates tie and you must pick one, **log that you picked**. A tie
> broken by declaration order is arbitrary with respect to correctness, and
> silence turns a one-line diagnosis into a multi-session one.

**Prevention.** Both warnings appear in `regression_check` output; the debugging
guide lists them as the highest-value grep targets.

**Source.** `entity_resolver.py:264-300`, `financial_extractor.py:805-830`.

---

## BUG-010 — A one-word alias swallowed a seven-word label

**Symptom.** Four distinct cash-flow lines collapsed onto the canonical metrics
`cash` and `equity`, with wrong values, in **queryable** metrics.

**Expected.** `"net cash generated from/(used in) investing activities"` does not
resolve to `cash`.

**Root cause.** Token-set containment (`alias_words <= normalized_words`) is true
whenever the alias's words all appear in the label — including when the alias is
**one word of seven**. `"cash"` matched inside the line above; `"equity"` matched
inside `"proceeds from issue of equity shares"`.

**Investigation.** Measured across ZOMATO FY24 pages 169/170/176/292
(2026-08-01): every coincidental match scored **≤ 0.43** coverage; every genuine
paraphrase scored **≥ 0.60**. Raw ratios recorded in `docs/measurements/`.

**Fix.** A coverage floor of **0.5** — `len(alias_words)/len(normalized_words)`
must be ≥ 0.5 for the alias-inside-label direction.

**Why it works.** 0.5 sits in the **empty band** between the two clusters, with
~0.07 margin either side. It is not a guess; it is the midpoint of a measured
gap.

**Note the asymmetry.** The floor applies **only** to alias-inside-label. The
reverse direction (label is a fragment of a longer alias) has coverage > 1 by
construction and is a different, working case.

**Lesson.**
> Set a threshold by measuring both populations and finding the gap. If there is
> no gap, the threshold will not work and you need a different signal.

**Near-miss worth recording.** `CLAUDE.md` §8 notes this fix was *"one command
from being reverted as a regression"* — the ninety-second measurement showed the
shift was an improvement: divergences fell from **2212 Cr to 11 Cr**.
**Measure before reverting, not just before shipping.**

**Source.** `entity_resolver.py:233-250`, `CLAUDE.md` §8.

---

## BUG-011 — An analyst's question was about to be read as a company claim

**Symptom.** Generic chunking of the ETERNAL Q4FY26 earnings transcript produced
187 chunks from 17 pages. **Chunk 92 opened mid-sentence on an analyst's
premise** — *"your advertising promotion cost … was flat sequentially"* — with no
attribution, while carrying a different speaker's name later in the same chunk.
Ten further chunks lost attribution entirely at page boundaries.

**Expected.** A claim is attributable to whoever made it.

**Root cause.** Two structural facts colliding.
1. A transcript's natural unit is the **speaker turn**, not a character window.
2. `parse_pdf` emits **one block per page**, so a turn spanning a page break has
   its speaker line on the previous page and its remainder starts bare.

**Why this was urgent rather than cosmetic.** In this document, management
**denies** several analyst premises in the very next turn — inventory days (p10),
A&P flat sequentially (p9), orders per customer (p8). An unattributed analyst
assertion reads as a company claim, and feeding that to the contradiction
detector is how it manufactures disagreement (BUG-003's failure mode, arriving by
a different route).

**Investigation.** 2026-08-08: `SPEAKER_LINE_RE` validated across all 532 lines
of the transcript — 127 matches, 17 distinct names (3 management + Moderator + 13
analysts), **zero** spurious hits inside prose. Separately, 10 of 129 chunks
classified `unknown`, including Akshant's 3,000-store guidance (p3), Albinder on
unhealthy growth (p10) and Akshant on customer retention (p12) — real management
speech with attribution lost at a page boundary.

**Fix.** Four coordinated changes.
- Split on speaker turns; store `speaker_role` as chunk metadata.
- Thread the outgoing speaker across page blocks — including across **skipped**
  pages, because dropping it would hand the next page a stale speaker from two
  pages back, *"a wrong attribution, which is worse than none."*
- Parse the management roster from the document's **own** page-1 declaration; an
  empty roster is a **hard ingest failure** (it would classify every speaker as
  an analyst, suppressing every claim and producing a clean-looking "no
  contradictions" result for entirely the wrong reason).
- Prefix continuations `"<Speaker> (cont.): "`.

**Why the `(cont.)` marker is load-bearing.** The source does not repeat the
speaker's name. A continuation piece that reads as a fresh verbatim attribution
is *"text this system invented at the data-entry point."*

**Then a fifth change, and the order mattered.** Turn-to-turn overlap was set to
**zero**. The 600-char overlap against an 800-char max left only 200 chars of
forward progress, so `_recursive_split` overflowed repeatedly and fell through to
its character-slice branch — producing chunks opening *'usiness to work'*,
*'urav's previous question'*, *'een the principle'*.

> *"The sequence matters: dropping this before threading existed would have
> created the orphans it was protecting against."*

**Lesson.**
> Chunk on the document's own semantic unit, not a character count. And when
> removing a safety measure, verify that its replacement is **already in place** —
> the order of two correct changes can still be wrong.

**Source.** `chunker.py:163-345`, `models.py:139-152`.

---

## BUG-012 — Ingest completion gates that could not fail

**Symptom.** Every ingest passed every gate, including runs that indexed nothing.

**Expected.** A gate that fails when the run failed.

**Root cause.** **Three gates were scoped to the tenant, not to the run.**
- Gate 2 read `verify_collection(TENANT)["total_points"]` against a threshold of
  100. ETERNAL alone holds 2268 chunks, so the threshold was already satisfied
  **before the run started**.
- Gate 4's filter was `tenant_id + is_latest` with `limit=1` against a tenant
  already holding 2531 points, so `len(results) > 0` was true before the run
  started. It reported *"Semantic search: ✅"* as evidence about a document it had
  never queried.
- Gate 1 matched any previously-indexed document for the same company and year.

**Investigation.** Audit finding **F8**, 2026-08-11.

**Fix.** All three rescoped to **this run's `doc_ids`**. Gate 4 additionally
fetches payloads and asserts no **stray** doc_ids came back — because *"a filter
that silently failed to apply would otherwise be indistinguishable from one that
worked."*

**Why it works.** The gate now observes its own output.

**Lesson — the sharpest one in this file.**
> **A check that passes because of pre-existing state is not a check.** Ask what
> would have to be true for it to *fail*. If the answer is "an empty database",
> the test is measuring the wrong thing.

**Prevention.** `IMPLEMENTATION_DELTAS.md` §D names this as a recurring class:
*"A check satisfied by absence — three instances, one cause."*

**Source.** `pipeline.py:555-680`, `docs/audit/repo_audit_20260811.md` F8.

---

## BUG-013 — A dead guardrail that looks alive

*Found 2026-08-20 during documentation. Not previously recorded.*

**Symptom.** None yet — the peer-comparison view still usually produces the right
operation.

**Expected.** The UI's `intended_operation` deterministically forces the DSL
operation.

**Root cause.** `DSLValidator.validate()` takes a `preferred_operation` argument
and overrides the model's choice when it is present. The block is commented
`⚡ PROGRAMMATIC OPERATION OVERRIDE (Load-Bearing Guardrail)`.

There is exactly one call site:

```python
validation = validate_dsl(raw_dict)      # quant_engine.py:268 — no second arg
```

`state["preferred_operation"]` is written by `router_node` and **read by
nothing**.

**Investigation.** `grep -rn "preferred_operation" backend/app/` — five hits in
`dsl_compiler.py` (the implementation), two in `state.py` (the declaration), one
in `router.py` (the write). **Zero reads.**

**Fix.** Not yet applied — see CAVEAT-002.

**Why it has not surfaced.** `DSL_SYSTEM_PROMPT` contains an explicit rule for
"who grew revenue faster, X or Y" → `growth_comparison`. So the *probabilistic*
mechanism is doing the work the *deterministic* one was written to guarantee.
That is the inverse of this project's stated preference, and it will surface the
first time a peer question is phrased outside that prompt rule.

**Lesson.**
> A guardrail with no caller is worse than no guardrail: it invites you to rely
> on protection that does not exist. When you add a parameter, add the call that
> passes it **in the same commit**, or do not add it.

**Prevention.** Grep for reads, not just definitions, when reviewing a new
parameter. A default value makes an unpassed argument invisible.

---

## BUG-014 — A page that fails to parse leaves no trace

*Found 2026-08-20 during documentation. Not previously recorded.*

**Symptom.** Unknown by construction — there is no signal to count.

**Root cause.**

```python
try:
    column_map, column_centers = detect_column_layout(pdf_path, page_idx)
except Exception as e:
    continue                     # `e` bound, never used; nothing logged
```

`financial_extractor.py:785`. A financial-statement page whose column detection
raises produces **no records and no log line**. The ingest completes, all gates
pass, and the missing rows are indistinguishable from rows the document never
contained.

**Why it exists.** **Likely rationale — inferred:** defensive skipping so one
malformed page cannot abort an entire ingest. A reasonable goal, implemented
without the observability that makes it safe.

**Fix.** Not yet applied — see CAVEAT-003. One line:
`logger.warning("Column layout failed on page %s: %s", page_number, e)`.

**Lesson.**
> `except: continue` is a decision to lose data. That is sometimes correct — but
> it must be **logged**, or you have also decided never to find out. A bound-but-
> unused exception variable is a strong smell: someone intended to log it.

**Prevention.** Grep for `except.*as e` followed by a bare `continue`/`pass`.

---

## Recurring failure classes

The individual bugs matter less than the patterns. These are the shapes to
recognise.

| Class | Instances | The tell |
|---|---|---|
| **One fact, several copies** | 3 metric registries → `registry.py`; 3 label formatters → `display_label()`; writer vs dry-run → `classify_upsert()`; injected fact vs appended line → one formatter | Two places that must agree, with nothing forcing them to |
| **A field overloading `null` with two meanings** | `company` (BUG-004); `provider` (BUG-007); `NOT_PRINTED` vs `None` in the extractor | Two situations needing opposite responses share one value |
| **A check satisfied by absence** | Gates 1/2/4 (BUG-012); the DISABLE_LOCAL_RERANKER path; the 20/20 hostname loop that counted successes without timing them | Ask: what would have to be true for this to *fail*? |
| **Incompatible scales read as one** | Cohere vs ONNX (BUG-001); RRF as a third scale; crore vs lakh (F3, latent) | A number crossing a boundary without its unit |
| **Writing a flag ≠ changing behaviour** | The router refusal (BUG-004 step 3); `preferred_operation` (BUG-013) | State says X, control flow never reads X |
| **Suppressing a true statement** | The cross self-contradiction (BUG-006, twice) | You are instructing a model to lie about its own evidence |
| **Silent first-wins on a collision** | `seen_keys` (BUG-009); alias ties (BUG-009); tenant-wide gates | A choice was made and nothing recorded it |
| **Prompt-order effects** | Three separate instances, per `CLAUDE.md` §1.5 | An appended instruction losing to an earlier, more concrete rule |

---

## The meta-lessons

From `CLAUDE.md` §8, earned the hard way:

1. **Never patch blind.** Diagnose from real output before writing any fix.
2. **When a diagnostic contradicts a stated prediction, stop.**
3. **When a fix does not work, stop tuning the number and go measure.**
4. **Measure before reverting, not just before shipping.** (BUG-010 was one
   command from being reverted as a regression.)
5. **Test through the real entry point**, not the underlying dict or a
   similar-looking function. Two false conclusions came from querying
   `all_alias_pairs()` directly and from verifying through `extract_text()` when
   the extractor uses `extract_financials_positional()`.
6. **A test that cannot observe the failure mode is not evidence.**
7. **Do not trust a single observation.** Verify across runs *and* across models.
   *"Cause cannot be assigned from a single before/after pair"* — attempted three
   times in one session, wrong every time.
8. **Forming theories is cheap; killing them with output is the discipline.**
   Four wrong theories at one command each is the right ratio. Defending one is
   not.
