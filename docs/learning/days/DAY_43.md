# Day 43 — Evaluation

**Phase 12 · Weight: H (~120 min) · Prerequisites: Days 30, 34, 37**

**Textbook: 10.8 — DIVERGES.** The textbook evaluates RAG with RAGAS: a 0–1
faithfulness score from an LLM judge. LedgerMind rejects that outright and
asserts **exact values, pass/fail**. Today is about why, and about the three
gates that decide whether a number may be published at all.

> **NO SWEEP TODAY.** Nothing in this day runs `eval_runner.py`. Every command
> below is zero-LLM-call. `CLAUDE.md` §5: *"Never run `eval_runner.py` without
> explicit per-run approval."* Reading this day is not approval.

---

## 1. Today's goal

By tonight you can:

- Explain why RAGAS was rejected, in terms of what this system promises.
- Describe the golden dataset: 4 files, 91 questions, 12 categories, and the
  **shape of an assertion** in each.
- Run the pytest suite and know **before** you run it that it is not green, and
  why (CAVEAT-025).
- Read an `eval_results/*.json` file **correctly** — row count, pass count,
  provider set, reranker set, mtime — **before** looking at the score.
- Name the three integrity gates and say why they are separate rather than
  merged.
- Distinguish a **quota signature** from a **real defect** by shape alone.
- Apply the golden keyword rule: what may be asserted on, and what may not.
- Explain why the conftest patches `psycopg2.connect` by name as well as
  `socket`.
- **Report, do not interpret.**

---

## 2. Why now

Evaluation asserts across all three paths, so it cannot come before all three
exist. Days 30, 34 and 37 closed them.

It also needs Day 42's shield, for a reason that is not obvious: **blocked
queries make no LLM call**, and that fact broke the provider gate and withheld
three clean scores on 2026-07-29. The evaluation code carries the fix and the
explanation.

And it needs the habit built since Day 2: **read the record, with its date, to
the end.** Today applies that to result files, and it is where the previous
session's own error on the cross path was caught (§4.6).

---

## 3. Concepts you must know first

| Concept | From | Why it is needed today |
|---|---|---|
| `sql_verified`, and what it guarantees | Day 34 | Every quantitative assertion reads it |
| `confidence_tier`, and what it does *not* measure | Day 29 | The known calibration issue in the runner's docstring |
| Cohere vs local ONNX score scales | Day 28 | The third integrity gate |
| Gemini → Groq failover | Day 19 | The first integrity gate |
| Blocked queries make no LLM call | Day 42 | Why they are excluded from that gate |
| `_reconcile_cross`'s quadrants | Day 37 | The `cross_examination` scorer |
| 5 RPM / 500 per day | Day 19, `CLAUDE.md` §5 | Why `--delay 25` and why sweeps need approval |

---

## 4. Concept lesson

### 4.1 The problem: what does "correct" mean for a RAG answer?

Two families of answer, and they are not evaluable the same way.

> *"What was Titan's standalone revenue for Q1FY26?"*

There is exactly one right answer: **₹13,040 Cr**. Any other number is wrong,
and 13,041 is not "0.99 correct".

> *"What risk factors does Eternal disclose?"*

There is no single right string. Two correct answers can share no sentence.

**The textbook's answer to the second case is RAGAS**: an LLM judge scores
faithfulness, answer relevance and context precision on 0–1. **LedgerMind
rejects it** — divergence **D6** in the master course.

**Why, stated in terms of the product.** From `00_LEARNING_MAP.md`:

> A wrong answer with a ✓ tick is worse than a refusal.

**Two objections, and the second is the serious one.**

**(1) A score is not a decision.** RAGAS 0.82 does not tell you to ship. It does
not name a failing question. It cannot be a gate.

**(2) An LLM judge is the component under test.** Grading a generative pipeline
with a generative model makes the grader share the failure modes of the graded.
It is the same argument as Day 41's gate and Day 42's shield, third instance:
**do not put a probabilistic component in the path that decides correctness.**

**So the assertion is exact-value, pass/fail.** From `q_titan.json`:

```json
{
  "id": "TQ001",
  "category": "quantitative_point",
  "question": "What was Titan's standalone revenue for Q1FY26?",
  "expected_path": "quantitative",
  "expected_sql_verified": true,
  "expected_company": "TITAN",
  "expected_financial_type": "standalone",
  "expected_metric": "revenue",
  "expected_value": 13040.0,
  "expected_unit": "crore_inr",
  "notes": "Verified live; confirmed across multiple regression_check runs this session."
}
```

**Note `notes`.** Every expectation records **how it was established**. An
expected value with no provenance is a guess with a schema.

**And the honest limitation**: the semantic categories fall back to keyword
matching, which `CLAUDE.md` §5 calls *"the fragile part."* §4.7 is the rule that
makes it survivable.

---

### 4.2 The dataset, measured

```bash
python3 - <<'PY'
import json, glob
from collections import Counter
tot = Counter()
for f in sorted(glob.glob('golden_dataset/*.json')):
    qs = json.load(open(f))
    print(f'{f:44} {len(qs):3}')
    tot.update(q['category'] for q in qs)
print('\nTOTAL', sum(tot.values()))
for k, v in sorted(tot.items()):
    print(f'  {k:34} {v}')
PY
```

| File | Questions |
|---|---:|
| `q4fy26_eternal.json` | 55 |
| `q_paytm.json` | 20 |
| `q_titan.json` | 15 |
| `q_eternal_transcript.json` | 1 |
| **Total** | **91** |

| Category | n | Assertion shape |
|---|---:|---|
| `quantitative_point` | 27 | `sql_verified` **and** exact value |
| `quantitative_yoy` | 8 | `sql_verified` **and** `yoy_pct` within ±0.5 |
| `quantitative_standalone` | 7 | as point — tests `financial_type` isolation |
| `quantitative_growth_comparison` | 1 | faster entity **and** both growth percentages |
| `adversarial` | 11 | `is_blocked == True` |
| `semantic_management` | 8 | tier ≠ low **and** all keywords present |
| `semantic_audit` | 6 | as above |
| `semantic_business` | 5 | as above |
| `semantic_risk` | 4 | as above |
| `semantic_honest_refusal` | 2 | the response **states** nothing relevant was found |
| `out_of_corpus` | 6 | **not** `sql_verified` **and** (expected error **or** tier low) |
| `cross_examination` | 6 | contradiction count · `sql_verified` · tier · keywords |

**Three things to notice.**

**Categories exist with a scorer and zero questions.** `score_result` handles
`quantitative_comparison`, `quantitative_cross_period_refusal` and
`quantitative_restatement`; the current dataset has none. **Scoring code is
not coverage** — a distinction Day 40 made about `tsc`, in a different costume.

**`adversarial` is 11 of 91 — 12 %.** Refusal is a first-class outcome, and it is
tested at a rate that says so.

**The 88/90 baseline is not comparable.** `CLAUDE.md` §7:

> The 88/90 baseline predates the transcript question, so it is not directly
> comparable — the next sweep is a **new baseline**, not a continuation.

**A dataset change resets the baseline.** Not "roughly comparable". Reset.

---

### 4.3 The golden keyword rule

`CLAUDE.md` §5, and it exists because keyword brittleness has cost real time:

> **Assert on:** dates, proper nouns, contiguous phrases quoted from the filing,
> acronyms that are the question's own subject.
>
> **Never assert on:** optional acronym glosses the model may not introduce
> (PPBL, SCN, FEMA, LODR), verb inflection (cancelled vs cancelling), or short
> strings a wrong answer would also satisfy.

**Each prohibition is a real failure.**

- **Optional glosses.** A correct answer may write *"Paytm Payments Bank"* and
  never *"(PPBL)"*. The gloss is the model's stylistic choice, not a fact about
  the filing.
- **Verb inflection.** "cancelled" vs "cancelling" is a tense, not a claim.
- **Short strings a wrong answer satisfies.** The fatal class: asserting on
  `"revenue"` in an answer about revenue passes whatever the number is. **A
  keyword that a wrong answer also contains is not an assertion.** (Same shape as
  Day 39's *"an assertion that cannot fail is not evidence."*)

**And the runner supports an alternatives form**, added for a measured reason:

```python
# The alternatives form exists because a correct answer may legitimately name
# the same thing two ways across runs -- Q039's answer alternates between
# "SEBI (Listing Obligations..." and "Securities and Exchange Board of India
# (...)". Both are right, and no single required substring covers both. That is
# a property of every acronym-bearing question, not of one question.
#
# This does NOT relax §5's golden keyword rule. Each alternative must still be
# an asserted-on string in its own right.
```

**A bare string is a one-element alternatives set**, so both paths are one path:

```python
def _keyword_alternatives(spec) -> list[str]:
    if isinstance(spec, str):
        return [spec.lower()]
    return [alt.lower() for alt in spec]
```

**And malformed entries fail at LOAD time**, not mid-sweep:

```python
def validate_expected_keywords(questions: list[dict]) -> None:
    """
    A malformed entry must never reach the matcher, because every malformed
    shape there fails OPEN: an empty list makes any() False -> permanently
    missing; a nested list makes the `in` test raise mid-sweep, after the
    Gemini calls have already been spent. Both are worse than refusing to
    start.
    """
```

**"After the Gemini calls have already been spent."** Under a 500/day ceiling,
*when* a validation fails is part of its cost.

---

### 4.4 Three integrity gates, deliberately not merged

Before any score is printed, three checks run. Each can **withhold** the number.

```python
scored, providers, models, backends = _integrity_counters(results)
contaminated   = {p for p in providers if p not in ("gemini",)}
model_mismatch = {m for m in models if m != model}
backend_mixed  = len(backends) > 1
```

**Gate 1 — provider.**

> `app/llm/client.py` falls back to Groq on 429/timeout/5xx. That is correct
> behaviour for a USER — the answer still arrives — but it is fatal for an EVAL:
> **a mixed-provider sweep produces a number that describes neither model.**
> Confirmed 2026-07-29 that a rate-limited run returned `llm_provider="groq"` on
> every query **while looking entirely normal.**

**"While looking entirely normal" is the whole problem.** The failover works. The
answers arrive. Nothing is red. The score describes a system that is half one
model and half another.

**Gate 2 — model.** Separate, and the comment says why:

> **SEPARATE from the provider gate above, not merged into it.** A
> mixed-provider run and a wrong-model run are different faults with different
> remedies (**wait for quota** vs. **fix the environment and re-run**), and one
> combined message means reading the wrong instruction at the worst possible
> moment.

**Gate 3 — reranker backend.** Same shape, one more scale problem:

```
*** SCORE WITHHELD — MIXED RERANKER BACKENDS ***
  Cohere scores are probabilities in [0,1];
  local ONNX scores are unbounded logits. A run spanning both
  is two systems, and the questions that flipped are the ones
  whose top-5 ordering differs between the scales.
```

**Day 28's lesson as a gate.** The fallback fires on WSL2 network flap; a sweep
spanning both backends measured two retrieval systems.

**And the withholding is total, not decorative:**

```python
print(f"  Raw tally (DO NOT publish): {tally}")
```

**A score printed with a warning above it still gets copied into a README.**
`CLAUDE.md` §5: *"Report, do not interpret … Withhold it; do not annotate it."*

**One arithmetic, one place:**

```python
def _integrity_counters(results):
    """
    Computed ONCE and shared by print_report and the meta block main() writes,
    so the recorded metadata and the printed gate cannot disagree. …
    two copies of that arithmetic drifting apart would mean a withheld run whose
    own JSON claims it was clean.
    """
```

**The `_compute_derived_totals` / `validate_financial_identities` failure class
again** (Days 31, 37) — this project's recurring lesson, in its third subsystem.

---

### 4.5 Two exclusions, both measured

**(a) Blocked queries are excluded from the provider gate.**

```python
# Blocked queries are excluded: prompt_shield blocks before router_node,
# which returns immediately on is_blocked, so NO LLM call is ever made and
# llm_provider is legitimately None. Counting those as "unknown" withheld
# three otherwise-clean scores on 2026-07-29 -- the unknown count matched
# the adversarial count exactly in all three datasets.
```

**The diagnosis was two equal counts.** `unknown == adversarial`, in all three
datasets — a coincidence too exact to be one. **That is a debugging technique
worth naming: when two independent tallies match exactly, they are not
independent.**

And the same paragraph handles `None` backends symmetrically:

> None is excluded from `backends` rather than counted: **refusal paths score no
> citations**, so `reranker_backend` is legitimately absent there, exactly as
> `llm_provider` is legitimately absent on blocked queries.

**Absence with a known cause is not contamination.**

**(b) Synthesis outages are excluded from the score entirely.**

```python
# response_generator sets error="synthesis_unavailable" when BOTH providers
# fail, serves a raw-excerpt floor, and caps the tier to low by design …
# That cap is correct and is not touched here -- but it feeds pass/fail
# conditions, not just messages, and every one of them reads the cap as
# evidence:
#   out_of_corpus passes on (not sql_verified) and tier == "low"
```

**Read the trap.** On a total LLM outage the tier is capped to `low` — correctly.
But `out_of_corpus` **passes** on `tier == "low"`. **So an outage would make
every out-of-corpus question pass**, for the wrong reason, and the sweep would
report an inflated score on a day the system was down.

**The fix is neither pass nor fail but EXCLUDED**, from both numerator and
denominator:

```python
tally = f"{passed}/{scored_total}" + (
    f" ({n_excluded} excluded: synthesis_unavailable)" if n_excluded else ""
)
```

**And the excluded IDs are listed, not just counted:**

```python
# The IDs, listed, not just a count. A count cannot distinguish exclusions
# clustered in one category -- which is a defect signature and a reason to
# look at the code -- from exclusions scattered across the run, which is a
# transient signature and a reason to wait for quota and re-run.
```

**Clustered versus scattered.** The same reasoning as §4.9's quota signature, at
a different grain.

---

### 4.6 Reading a record to the end — the cross path, corrected

**This day found an error in Day 37, and the error is instructive.**

Day 37 stated three times that the cross path is *"BUILT but UNMEASURED against
the golden set"*, citing `IMPLEMENTATION_DELTAS.md` §C. §C's **heading** does say
that. Its next paragraph says:

> **Superseded 2026-08-02.** The heading is now wrong in both directions and is
> kept only so the correction has something to attach to. **The path IS
> measured.**

**Measure it yourself:**

```bash
python3 - <<'PY'
import json, glob
for f in sorted(glob.glob('golden_dataset/*.json')):
    for q in json.load(open(f)):
        if q.get('category') == 'cross_examination' or q.get('expected_path') == 'cross':
            print(f"{q['id']:8} cat={q.get('category'):22} "
                  f"expected_path={str(q.get('expected_path')):10} "
                  f"contradictions={q.get('expected_contradictions')} "
                  f"tier_low={q.get('expected_tier_low')}")
PY
```

Six `cross_examination` questions; six with `expected_path="cross"`; the sets
overlap in five. And `score_result` has a dedicated branch for the category.

**What *is* true is narrower**, and §C states it:

> *A genuine contradiction does not exist in this corpus.* Three zero-quota
> retrieval probes looked for one. Every profitability-framed query returns
> financial statements … The narrative discusses NOV, order mix, store counts and
> category growth. **The two halves address different subjects, so there is
> nothing to disagree about.** Closing this needs a DOCUMENT containing a real
> disagreement … **not another question.** A manufactured contradiction would
> train the system to fire on approximation, which is **Trap 7 inverted and
> worse than no test at all.**

**Three lessons, and take all three.**

1. **A heading is not a record.** §C's heading is deliberately retained-and-wrong
   so the correction has an anchor. Day 37 quoted the title.
2. **"Unmeasured" and "measured but not exhaustively" are different claims** with
   different remedies.
3. **Some gaps cannot be closed by writing a better test.** No question produces
   a contradiction the corpus does not contain, and manufacturing one would
   train the wrong behaviour.

**And read the `cross_examination` scorer's own comment**, which is the same
discipline in miniature:

```python
# tier is asserted in ONE direction by default and the OTHER when the
# golden entry sets expected_tier_low. …
# But _reconcile_cross's Quadrant 4 (both halves empty, a genuine
# no-answer) returns tier="low" BY CONSTRUCTION, so an unconditional
# fail made that quadrant untestable: TQ015 was authored as a
# Quadrant 4 cross question on 2026-08-02 and had to be moved to
# semantic_honest_refusal purely because this branch could not express
# the expectation.
#
# Deliberately an INVERSION, not a skip. Skipping the check would let
# such a question pass at any tier, asserting nothing -- the flag has
# to buy a real assertion, not remove one.
```

**"The flag has to buy a real assertion, not remove one."** A skip would have
been one line shorter and would have made the question meaningless.

---

### 4.7 The pytest suite, and its stated red

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend \
  env PYTHONPATH=/app python -m pytest tests/ -q
```

**Expect `218 passed, 25 errors`. Know that before you run it.**

**CAVEAT-025**, and the second impact is worse than the first:

> 1. Twenty-five real tests over `eval_runner`'s scoring helpers have not run for
>    a day. **Those helpers decide whether a golden question passes.**
> 2. `CLAUDE.md` described the suite as "177 tests… ~2s" with no failure count,
>    so the documented baseline was *green*. Anyone running the suite and seeing
>    25 errors would reasonably read them as something they had just broken.
>    **A stated baseline that does not match the observed one is worse than no
>    baseline: it converts an existing defect into a false attribution.**

**The cause is one line.** `conftest.py`'s `eval_runner` fixture supplies:

```python
sys.argv = ["eval_runner.py", "--model", "unused-by-these-tests"]
```

and commit `0cf7e7c` made `--api-base` **required with no default**, so the
import raises `SystemExit(2)`.

**Why it is not fixed here.** The course's production contract is **comments
only** on source; `conftest.py` is source, and a fixture change is behaviour.
`CODE_DOCUMENTATION_LOG.md` rule 5: *anything that looks wrong gets DOCUMENTED,
not fixed.* **The fix is known, one line, and awaiting approval.**

**What the suite is, and what it deliberately is not:**

```
12 files · 194 test functions · 243 collected after parametrisation · ~5 s
```

Every test is a **pure function** test. No network, no DB, no LLM, no PDF.

> A function that needs any of those is OUT OF SCOPE here. It belongs in
> `regression_check.py` (integration, needs the corpus) or in a targeted script
> under `scripts/`.

**And the network guard is a measurement, not a hope:**

```python
monkeypatch.setattr(socket.socket, "connect", _blocked)
monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
monkeypatch.setattr(socket, "create_connection", _blocked)

# C-extension clients that bypass the socket module -- see docstring.
import psycopg2
monkeypatch.setattr(psycopg2, "connect", _blocked)
```

> Patching socket catches every client that opens its connection **through
> Python** … It does NOT catch a C extension that calls the OS directly.
> psycopg2 … connects via libpq and never touches Python's socket module.
> **Measured: with the socket patch alone, `psycopg2.connect` to the live
> database SUCCEEDED.** That is why `psycopg2.connect` is patched by name.
>
> The general lesson … **the socket patch is a broad net with a known hole, not a
> proof.** A new client needs its own line here, **and a probe confirming the
> line works.**

**"And a probe confirming the line works."** The guard was verified by attempting
a connection, not by reasoning about layers.

---

### 4.8 Tests that assert known defects

```python
# ASSERTIONS RECORD OBSERVED BEHAVIOUR, NOT DESIRED BEHAVIOUR.
#
# Where a defect exists the test asserts what the code CURRENTLY DOES and names
# the finding in its docstring. That is deliberate: a test suite whose purpose is
# to detect change must first describe the present accurately. A test asserting
# the fixed behaviour of an unfixed function is a failing test, and a suite that
# is red on arrival gets ignored.
#
# When one of those defects is fixed, the corresponding test SHOULD fail. That is
# the suite working. … update the assertion in the same commit as the fix.
```

**Read the inversion.** A failing test normally means a regression. Here, for
these specific tests, **a failure means the fix landed** — and the docstring
names the audit finding so you can tell which is which.

**And no `xfail`:**

> `xfail(strict=False)` would let a genuine regression pass silently, and every
> defect covered below is currently **stable and reproducible rather than
> flaky.**

**`xfail` says "this may fail." These tests say "this fails, exactly like
this."** The second is an assertion; the first is a shrug.

---

### 4.9 Quota signature versus real defect

`CLAUDE.md` §5:

> Failure at a **fixed position** with everything before it passing is a quota
> signature. A real defect fails **by category**.

**Why position is the tell.** Gemini's daily quota is consumed in order. Question
1 through *k* pass; *k+1* onward fail; and *k* is wherever the budget ran out —
**an accident of how many calls the day had already spent**, not of the
questions.

**A real defect has semantics.** Every `quantitative_standalone` question fails
and everything else passes ⇒ `financial_type` isolation. Every question about
one company fails ⇒ ingestion or entity resolution.

| | Quota | Defect |
|---|---|---|
| Shape | positional | categorical |
| Boundary | arbitrary index | a category edge |
| Providers | `groq` appears | unchanged |
| Re-run tomorrow | passes | fails identically |
| Remedy | wait | read the code |

**And the runner reports both dimensions**, which is what makes the distinction
readable: `By category:` with per-category bars, plus the excluded IDs listed
individually (§4.5).

---

### 4.10 `eval_results/*.json` are not baselines

`CLAUDE.md` §7, and this is the day's most practical rule:

> **`eval_results/*.json` are not baselines.** They are whatever ran last,
> including rolled-back experiments and interrupted runs. `eval_q_titan.json`
> (2026-08-10) reads 11/15 with providers `{None, 'gemini'}` — that is the
> `financial_type` propagation **rollback artifact**, not a result; the 08-08
> sweep's TITAN 15/15 stands. **Before reading any eval JSON, print row count,
> pass count, provider set, reranker set and mtime.** Three wrong conclusions in
> one session traced to reading these files without that header check.

**The directory holds 27 files.** Full sweeps, scoped sweeps, router probes,
single-question debugging runs, and artifacts of rolled-back experiments —
**with names that do not distinguish them.**

**So the header check is not caution, it is the read protocol:**

```bash
python3 - <<'PY'
import json, os, glob, datetime
from collections import Counter
for f in sorted(glob.glob('eval_results/*.json')):
    try:
        d = json.load(open(f))
    except Exception as e:
        print(f'{os.path.basename(f):42} UNREADABLE ({e})'); continue
    rows = d.get('results') if isinstance(d, dict) else d
    if not isinstance(rows, list):
        print(f'{os.path.basename(f):42} not a results file'); continue
    prov = Counter((r.get('api_response') or {}).get('llm_provider') or 'None' for r in rows)
    back = Counter((r.get('api_response') or {}).get('reranker_backend') or 'None' for r in rows)
    passed = sum(1 for r in rows if (r.get('score') or {}).get('pass'))
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d')
    print(f'{os.path.basename(f):42} rows={len(rows):3} pass={passed:3} '
          f'mtime={mt}  providers={dict(prov)} backends={dict(back)}')
PY
```

**Read the header, then decide whether the number is worth reading.**

**Baselines live in `docs/IMPLEMENTATION_DELTAS.md`**, dated, with providers
stated. That is the difference between an artifact and a record.

---

### 4.11 `regression_check.py` — the free gate

**Zero LLM calls. Zero network. Read-only.**

```python
"""
Regression Check — verifies section_classifier.py and financial_extractor.py
produce sane output across all reference documents in the corpus.

Checks two independent layers:
  1. Block-type distribution — did a classifier change help one document
     while silently breaking another?
  2. Extracted financial records — does the column-map/extraction chain
     produce plausible numbers, not just "some numbers"?
"""
```

**Layer 1 is the interesting one.** *"Help one document while silently breaking
another"* is the failure a single-document test cannot see, and it is why the
gate runs across **five** documents (ETERNAL Q4FY26, TITAN, PAYTM, ZOMATO FY24,
ETERNAL transcript).

**Run it after every extraction change, and not batched:**

> `regression_check.py` makes **zero** LLM calls. Run it after every extraction
> change, **not batched** — batching means a failure cannot be attributed to one
> change.

**And it has an operational hazard worth knowing:**

> **Any script that parses a corpus PDF must parse it once and reuse the
> result.** Parsing twice exhausts WSL RAM and restarts the distro. Run
> `regression_check` once, tee to `/tmp`, grep the file.

**Also `--min-chunks`** (CAVEAT-023): defaults to 100, TITAN legitimately
produces 24, so a **fully successful** TITAN ingest exits 1 on the post-write
completion gate. *"Read the log, do not re-run."* **An exit code that is not a
verdict.**

---

### 4.12 Quota discipline

| Rule | Value | Why |
|---|---|---|
| Rate limit | **5 RPM** | one call per 12 s |
| Daily | **500 per model** | shared with everything else that day |
| Calls per semantic question | **2** | router + synthesis |
| `--delay` | **25 s** | 2 × 12 s, rounded. **Not 15** |
| Largest dataset | **first**, as a gate | if it is unclean, stop |
| Approval | **per run** | `CLAUDE.md` §5 |

**On `--delay`:**

```python
# 5 RPM = one call per 12s, and a semantic question makes TWO Gemini calls
# (router classification, then response synthesis): 2 x 12s = 24s, rounded
# to 25s. The previous default of 15.0 assumed one call per question, giving
# ~8 RPM against a 5 RPM limit -- over budget by construction, and why the
# Groq fallback fired mid-sweep and withheld the score (2026-07-30, Paytm).
# Do not lower this to speed up a run; an invalid baseline costs a full re-run.
```

**"Over budget by construction."** Not unlucky. The arithmetic was wrong, and the
runs that survived at 15 s were survivorship.

**And read the report in this order** — `CLAUDE.md` §5:

```
1. Providers:
2. Models served:
3. the score
```

**If either gate is unclean, stop.** Do not spend the remaining questions.

**Then: report, do not interpret.** Paste those three lines verbatim and stop.

---

### 4.13 A calibration issue the runner states about itself

```python
# KNOWN CALIBRATION ISSUE (flagged, not fixed by this runner):
#   confidence_tier has been observed as "high" even when reranker_score is strongly
#   negative (-2.5 to -5.1) and retrieved chunks are unrelated to the question. This
#   means confidence_tier alone cannot be trusted to signal "did we find the right
#   content" — only whether the model was willing to answer. See semantic_honest_refusal
#   category, which checks response_text content instead of confidence_tier for this reason.
```

**A tool documenting a limitation of the thing it measures** — and then routing
around it: `semantic_honest_refusal` checks **content**, not tier, precisely
because tier cannot carry that signal.

**Day 29's lesson, from the evaluator's side:** confidence measures *willingness
to answer*, not *retrieval correctness*.

---

## 5. The actual LedgerMind files

```
Dir:   golden_dataset/  — 4 files, 91 questions, 12 categories
       q4fy26_eternal.json 55 · q_paytm.json 20 · q_titan.json 15 ·
       q_eternal_transcript.json 1
       Mounted READ-ONLY into the backend container, on purpose: ":ro means
       nothing running in this container can write an eval output beside them,
       which is the failure that once left 79 outputs against 3 inputs"

File:  backend/scripts/eval_runner.py (~1100 lines)      HOST-RUN, from backend/
Entry: main() · score_result(golden, result) · print_report(results, model)
       _integrity_counters(results) -> (scored, providers, models, backends)
       validate_expected_keywords(questions)   — fails at LOAD time
       _keyword_alternatives · _missing_keywords
Args:  --api-base REQUIRED · --model REQUIRED · --dataset · --out ·
       --delay 25.0 · --category · --categories (marks the run scoped=true)
Gates: provider · model · reranker backend — THREE, deliberately separate

File:  backend/scripts/regression_check.py               ZERO LLM CALLS
Entry: the 5-document gate. Block-type distribution + extracted records.
Note:  parse each PDF ONCE — twice exhausts WSL RAM and restarts the distro

Dir:   backend/tests/ — 12 files, 194 functions, 243 collected, ~5 s
       conftest.py: autouse network guard (socket AND psycopg2.connect),
                    eval_runner fixture, make_block fixture
       BASELINE: 218 passed / 25 errors  — CAVEAT-025, NOT green

Dir:   eval_results/  — 27 files, gitignored, NOT BASELINES
Baselines: docs/IMPLEMENTATION_DELTAS.md, dated, with providers stated
```

---

## 6. Deep walkthrough — one question scored

**TQ001**, `quantitative_point`, expecting `13040.0`.

**Step 1 — load and validate, before any call.**

```python
validate_expected_keywords(questions)
```

**Every malformed shape fails open at match time**, so it must fail closed at
load time — **before the Gemini budget is spent.**

**Step 2 — authenticate as admin.**

```python
parser.add_argument("--email", default="admin@alpha.ledgermind.test",
                    help="MUST be admin: llm_provider is admin-tier only "
                         "in response_shaping.py, and without it the provider guard "
                         "silently reads None for every question and concludes the "
                         "whole sweep was Gemini-served.")
```

**Read the failure that would produce.** As analyst, `llm_provider` is absent
(Day 9), so `providers` is `{"unknown": 91}` — which the gate *does* catch, as
contamination. **The help text's phrasing describes an older behaviour**;
today's gate withholds on `unknown` and prints a hint naming this exact cause.
**Both the trap and its guard are in the file.**

**Step 3 — the query.**

```python
def run_query(token: str, question: str) -> Optional[dict]:
```

Uses the dataset's **exact** question text (`CLAUDE.md` §5), against `--api-base`.

**Step 4 — score.**

```python
if not result.get("sql_verified"):
    return {"pass": False, "reason": "sql_verified is False", …}
```

**`sql_verified` first, always.** A right number that was not verified is not a
pass — the tick is the product (Day 34).

Then path, then value:

```python
expected_path = golden.get("expected_path")
if expected_path and actual_path != expected_path:
    return {"pass": False, "reason": f"Wrong path: expected={expected_path} actual={actual_path}", …}
```

then `_extract_point_value(sql_result)` compared against `expected_value`.

**Step 5 — sleep.**

```python
time.sleep(args.delay)     # 25 s
```

**Step 6 — the gates, then the report.** `_integrity_counters` once; three gates;
the tally withheld or printed.

**Step 7 — the JSON, with a meta block.**

```python
# CLAUDE.md section 7 requires a provider/reranker header check
"reranker_backends": dict(_backends),
```

**The file records what the header check needs**, so a future reader can perform
§4.10's protocol on it.

**STATE AFTER.** One row in `eval_results/eval_q_titan.json`, and a printed
report whose first three lines are `Providers:`, `Models served:`, the score —
**in that order, because that is the order they must be read in.**

---

## 7. Data flow

```
golden_dataset/*.json  (READ-ONLY mount)
        ▼ validate_expected_keywords()      FAILS AT LOAD, before any spend
        ▼ get_token(admin)                  llm_provider is admin-tier only
        │
   ┌────▼──────────────────── per question ─────────────────────────┐
   │  run_query(exact text) ──► POST /api/query                     │
   │        ▼                                                        │
   │  score_result(golden, result)                                   │
   │     adversarial            → is_blocked                         │
   │     out_of_corpus          → not sql_verified AND (err OR low)  │
   │     quantitative_*         → sql_verified AND exact value       │
   │     cross_examination      → contradictions · verified · tier   │
   │     semantic_*             → tier != low AND all keywords       │
   │     synthesis_unavailable  → EXCLUDED, neither pass nor fail    │
   │        ▼                                                        │
   │  time.sleep(25)            2 calls/question vs 5 RPM            │
   └────┬────────────────────────────────────────────────────────────┘
        ▼
   _integrity_counters(results)          ONE arithmetic, TWO consumers
        │  scored = rows where NOT is_blocked   (no LLM call is not "unknown")
        │  backends: None dropped               (a refusal reranks nothing)
        ▼
   ┌─ GATE 1 provider  ── any non-gemini ──► WITHHOLD  "wait for quota"
   ├─ GATE 2 model     ── any != --model ──► WITHHOLD  "fix the environment"
   └─ GATE 3 backend   ── mixed scales   ──► WITHHOLD  "check the Cohere key"
        ▼ all clean
   Providers: … │ Models served: … │ Total/Pass/Fail/Score
   By category: bars   ·   Excluded: IDs listed   ·   Failures: IDs + reasons
        ▼
   eval_results/eval_<dataset>.json   + meta{providers, models, backends, api_base}
        ▼
   NOT A BASELINE.  Baselines live in IMPLEMENTATION_DELTAS.md, dated.
```

---

## 8. Engineering decision — exact assertions over a judged score

**Problem.** Decide whether a change to a RAG system made it better or worse,
under a 500-call daily budget, for a product whose claim is verified numbers.

**Decision.** A hand-authored golden dataset of exact-value, pass/fail
assertions, plus three integrity gates that can withhold the number entirely.
**D6.**

| Alternative | Why not |
|---|---|
| **RAGAS / LLM-judged faithfulness** | A score is not a decision and names no failing question; and **the judge shares the failure modes of the judged** |
| **Human review of samples** | Not repeatable, not a gate, does not scale to 91 |
| **String equality on the whole answer** | Semantic answers legitimately vary in wording |
| **Embedding similarity to a reference** | A wrong number is embedding-similar to a right one. **Precisely the failure this system exists to prevent** |
| **Print the score with a warning** | *"A raw tally printed under a caveat ends up in a README"* |
| **One combined integrity gate** | Different faults, different remedies. One message means reading the wrong instruction at the worst moment |
| **CI on every push** | **Would be right, and does not exist** (CAVEAT-022). Each run costs real quota, so it needs a strategy, not a workflow file |

**Trade-offs accepted.**

- **Keyword matching for semantic answers** — fragile, mitigated by §4.3's rule
  and the alternatives form, and it is where most brittleness has appeared.
- **Hand-authored expectations** — each needs a `notes` provenance line, and a
  wrong expectation is invisible until someone re-derives it.
- **91 questions is small**, and coverage is uneven: 1 growth-comparison, 27
  point queries.
- **Three scored categories have zero questions.**
- **No genuine contradiction is asserted**, and no question can fix that (§4.6).
- **A sweep costs ~165 calls** of a 500/day budget, so it cannot be routine —
  which is exactly why it needs approval, and why `regression_check` exists as
  the free gate.
- **No CI** (CAVEAT-022), so **the suite is only as good as the discipline of
  running it** — which is how 25 errors went unnoticed for a day.

**Current validity.** The gates are the strongest part and are well-evidenced.
The weakest part is that nothing runs any of it automatically.

**At 10×.** More corpora means the dataset grows past what one sweep can afford,
so it splits into a per-PR smoke subset (zero-LLM: `regression_check` + pytest)
and a nightly full sweep on a paid tier. **The gates are unchanged** — they are
about attribution, not scale.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `218 passed, 25 errors` | **The baseline.** CAVEAT-025 — the `eval_runner` fixture predates `--api-base` |
| Score withheld, providers `{gemini, groq}` | Rate limit or timeout. **Wait for quota and re-run** |
| Score withheld, providers `{unknown: n}` | Not authenticated as admin — `llm_provider` is admin-tier |
| Score withheld, models mismatched | The environment is running a different `GEMINI_MODEL`. **Fix and re-run** |
| Score withheld, backends `{cohere, local}` | Cohere failed mid-sweep. Check the key and the network |
| `unknown` count exactly equals the adversarial count | Blocked rows counted in the provider gate. **Legitimately no provider** |
| Everything after question *k* fails | **Quota.** Positional, not categorical |
| Every question in one category fails | **A real defect.** Read the code |
| An out-of-corpus category at 100 % on a bad day | Synthesis outage capping tiers to low — must be **excluded**, not passed |
| A sweep overwrites another dataset's JSON | A single `--out` default. Fixed by per-dataset derivation |
| Eval outputs appear inside `golden_dataset/` | The `:ro` mount is what prevents this. 79 outputs against 3 inputs, once |
| TITAN ingest exits 1 while succeeding | `--min-chunks` 100 vs 24 (CAVEAT-023). **Read the log, do not re-run** |
| WSL restarts mid-`regression_check` | A PDF parsed twice. Parse once, tee, grep |

---

## 10. Hands-on experiment

> **Every command here is zero-LLM-call.**
>
> **`MSYS_NO_PATHCONV=1` is load-bearing on any command carrying `-w /app`**, when
> run from Git Bash on Windows. Without it the path is rewritten and the exec
> fails with `Cwd must be an absolute path` — verified while writing this day.
> `CLAUDE.md` §7 records the same for the pytest invocation. On Linux or macOS
> the prefix is harmless.

### Experiment 1 — the suite, against the stated baseline

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend \
  env PYTHONPATH=/app python -m pytest tests/ -q 2>&1 | tail -5
```

**Predict `218 passed, 25 errors` before you press enter.** Then:

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend \
  env PYTHONPATH=/app python -m pytest tests/test_eval_helpers.py -q 2>&1 | grep -A 4 "SystemExit\|error:" | head -12
```

**The error names `--api-base`.** One line in a fixture.

### Experiment 2 — measure the dataset

```bash
python3 - <<'PY'
import json, glob
from collections import Counter
tot = Counter(); paths = Counter()
for f in sorted(glob.glob('golden_dataset/*.json')):
    qs = json.load(open(f))
    print(f'{f:44} {len(qs):3}')
    tot.update(q['category'] for q in qs)
    paths.update(str(q.get('expected_path')) for q in qs)
print('\nTOTAL', sum(tot.values()))
for k, v in sorted(tot.items()): print(f'  {k:34} {v}')
print('\nexpected_path:', dict(paths))
PY
```

### Experiment 3 — the header protocol on every result file

Run the script in §4.10. **Then find `eval_q_titan.json`** and check its numbers
against `CLAUDE.md` §7's warning about the rollback artifact.

**Write down, before looking at any score, which files you would refuse to
read.**

### Experiment 4 — score a question with no API at all

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend python -c "
import sys, importlib.util
sys.argv = ['eval_runner.py', '--api-base', 'http://unused', '--model', 'unused']
spec = importlib.util.spec_from_file_location('er', '/app/scripts/eval_runner.py')
er = importlib.util.module_from_spec(spec); spec.loader.exec_module(er)

golden = {'id':'X','category':'quantitative_point','expected_sql_verified':True,
          'expected_path':'quantitative','expected_value':13040.0}
for name, resp in {
  'exact':        {'sql_verified':True,'path':'quantitative','sql_result':[{'value':13040.0}]},
  'off by one':   {'sql_verified':True,'path':'quantitative','sql_result':[{'value':13041.0}]},
  'unverified':   {'sql_verified':False,'path':'quantitative','sql_result':[{'value':13040.0}]},
  'wrong path':   {'sql_verified':True,'path':'semantic','sql_result':[{'value':13040.0}]},
}.items():
    s = er.score_result(golden, resp)
    print(f'{name:14} pass={str(s[\"pass\"]):5}  {s[\"reason\"]}')
"
```

**Note the argv substitution** — the same manoeuvre `conftest.py`'s fixture uses,
**with the argument the fixture is missing.** You have just performed
CAVEAT-025's one-line fix by hand, without editing the file.

**And note row 3:** the right number, unverified, **fails**.

### Experiment 5 — the keyword rule, demonstrated

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend python -c "
import sys, importlib.util
sys.argv = ['eval_runner.py', '--api-base', 'u', '--model', 'u']
spec = importlib.util.spec_from_file_location('er', '/app/scripts/eval_runner.py')
er = importlib.util.module_from_spec(spec); spec.loader.exec_module(er)

resp = 'paytm payments bank was affected by the rbi directive of january 2024.'
for kw in [['paytm payments bank'], ['ppbl'],
           [['ppbl','paytm payments bank']], ['january 2024'], ['revenue']]:
    print(f'{str(kw):46} missing={er._missing_keywords({\"expected_keywords\":kw}, resp)}')
"
```

Row 2 is the **optional gloss** the rule forbids. Row 3 is the alternatives form.
**Row 5 is the fatal class** — `revenue` would be satisfied by a wrong answer
too, so it asserts nothing.

### Experiment 6 — prove the network guard, and its hole

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend env PYTHONPATH=/app \
  python -m pytest tests/ -q -k "registry or gate" 2>&1 | tail -3
```

Then read why one line exists:

```bash
sed -n '/C-extension clients/,+3p' backend/tests/conftest.py
grep -n "psycopg2.connect to the live database SUCCEEDED" -B 4 backend/tests/conftest.py
```

**The guard was verified by attempting a connection.**

### Experiment 7 — find the tests that assert defects

```bash
grep -rn "F1\b\|F2\b\|F7\b\|F9\b\|F12" backend/tests/*.py | grep -i "docstring\|finding\|audit" | head
grep -rln "CURRENTLY DOES\|currently does\|known defect\|audit finding" backend/tests/
```

**Pick one and read its docstring.** When it starts failing, that is the fix
landing.

### Experiment 8 — the free gate, run once and tee'd

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend env PYTHONPATH=/app \
  python -m scripts.regression_check 2>&1 | tee /tmp/regcheck.txt | tail -25
grep -n "FAIL\|WARN\|min-chunks\|SKIP" /tmp/regcheck.txt | head -20
```

**Tee, then grep.** *"Any script that parses a corpus PDF must parse it once and
reuse the result."*

### Experiment 9 — pre-flight, before any measurement you intend to trust

```bash
docker compose exec -T backend python -c "import app.engines.retriever as m; print(m.__file__)"
docker compose exec -T backend printenv GEMINI_MODEL
docker compose exec -T backend printenv QDRANT_URL     # must be the https Cloud URL
docker compose exec -T backend printenv DATABASE_URL   # local Docker, NOT Supabase
```

**All four, every time.** `CLAUDE.md` §4: environment-vs-code confusion has cost
more time than application defects.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `golden_dataset/q_titan.json`, `backend/scripts/eval_runner.py` and
`backend/tests/conftest.py`:

1. Read TQ001. Name every field that must hold for a pass, and say which one is
   checked **first** and why.
2. Find the three integrity gates. For each: what triggers it, what it prints,
   and **what the operator should do next**. Why are they not one gate?
3. Find the two exclusions in `_integrity_counters`. For each, name the
   measurement that caused it.
4. Read the `conftest.py` network guard. What does patching `socket` cover, what
   does it miss, and how was the miss established?
5. Given a report where questions 1–34 pass and 35–55 fail: quota or defect?
   Name **three** things you would check before deciding.

---

## 12. Self-check questions

**Basic**

1. How many golden questions, in how many files?
2. What is the pytest baseline?
3. What is `--delay`, and why that number?
4. What are the three integrity gates?
5. How many `adversarial` questions, and what do they assert?

**Code**

6. Why does `validate_expected_keywords` run at load time?
7. Why is `_integrity_counters` shared by two consumers?
8. Why are blocked queries excluded from the provider gate?
9. Why is a synthesis outage excluded rather than failed?
10. Why does `conftest.py` patch `psycopg2.connect` by name?

**Why**

11. Why was RAGAS rejected? Give both objections.
12. Why is a score withheld rather than printed with a warning?
13. Why are the three gates separate?
14. Why do some tests assert known defects, and what does their failure mean?
15. Why is `eval_results/*.json` not a baseline, and where do baselines live?

**Debugging**

16. A sweep prints `providers={'gemini': 48, 'groq': 7}`. What happened, what do
    you do, and what do you report?
17. Everything after question 40 fails. Quota or defect? How do you confirm?
18. A colleague reports "the suite is broken, 25 errors". Respond.

**System design**

19. Design CI for this project. What runs per push, what runs nightly, and what
    never runs automatically?
20. Design an assertion for a genuine contradiction on the cross path. Say why
    no question alone can produce it, and what it would take.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `expected_sql_verified` (true), `expected_path` (`quantitative`), and
   `expected_value` (13040.0). **`sql_verified` is checked first**, because a
   right number that was not verified is not a pass — the verification, not the
   digits, is the product. (`expected_company`, `expected_financial_type`,
   `expected_metric` and `expected_unit` document the expectation; the point
   scorer asserts verification, path and value.)
2. **Provider** — any non-`gemini` in the served set; prints *"NOT A VALID
   BASELINE"* plus a hint per contaminant; **next: wait for quota and re-run**
   (`groq`) or re-run as admin (`unknown`). **Model** — any served
   `llm_model != --model`; prints the mismatch and the `printenv GEMINI_MODEL`
   command; **next: fix the environment and re-run.** **Reranker backend** — more
   than one backend; explains the incompatible scales; **next: check the Cohere
   key.** **Not one gate:** *"different faults with different remedies … one
   combined message means reading the wrong instruction at the worst possible
   moment."*
3. **(a)** Blocked queries excluded — `prompt_shield` blocks before
   `router_node`, so no LLM call is made and `llm_provider` is legitimately
   `None`. **Measurement:** three otherwise-clean scores withheld on 2026-07-29,
   with the `unknown` count matching the adversarial count **exactly** in all
   three datasets. **(b)** `None` backends dropped — refusal paths score no
   citations, so `reranker_backend` is legitimately absent.
4. **Covers:** every client that opens a connection through Python — httpx,
   qdrant_client, cohere, groq, google-genai, urllib. **Misses:** a C extension
   calling the OS directly; psycopg2 connects via libpq. **Established by
   measurement:** with the socket patch alone, `psycopg2.connect` to the live
   database **succeeded**. Hence the by-name patch — *"a broad net with a known
   hole, not a proof."*
5. **Almost certainly quota — but confirm, do not assume.** **(1) Providers:** if
   `groq` appears, the fallback fired, which is the rate-limit signature.
   **(2) Category distribution:** do the failures span every category after 34, or
   cluster in one? Positional = quota; categorical = defect. **(3) The failure
   reasons themselves:** an exhausted quota surfaces as API errors or
   `synthesis_unavailable` exclusions, not as wrong values. A fourth, if you have
   it: re-run tomorrow — quota passes, a defect fails identically.

### §12 — Basic

1. **91**, in **4** files: 55 Eternal, 20 Paytm, 15 Titan, 1 transcript.
2. **218 passed, 25 errors** (CAVEAT-025). **Not green.**
3. **25 s.** 5 RPM = one call per 12 s; a semantic question makes **two** calls;
   2 × 12 = 24, rounded up.
4. Provider, model, reranker backend.
5. **11.** They assert `is_blocked == True`.

### §12 — Code

6. Because every malformed shape **fails open** at match time — an empty list
   makes `any()` false (permanently "missing"), a nested list raises mid-sweep
   **after the Gemini calls have been spent.** Refusing to start is cheaper.
7. So the printed gate and the JSON `meta` block cannot disagree. *"Two copies of
   that arithmetic drifting apart would mean a withheld run whose own JSON claims
   it was clean."* — the `_compute_derived_totals` failure class.
8. Because they make **no LLM call**: `prompt_shield` blocks before
   `router_node`. `llm_provider = None` there is a correct record, not
   contamination.
9. Because the outage caps the tier to `low` by design, and **`out_of_corpus`
   passes on `tier == "low"`** — so failing them would be wrong and passing them
   would inflate the score. Excluded from **both** numerator and denominator, with
   the IDs listed so clustered and scattered exclusions are distinguishable.
10. Because psycopg2 connects through **libpq in C** and never touches Python's
    `socket` module. Measured: with the socket patch alone, a live connection
    **succeeded**.

### §12 — Why

11. **(1)** A 0–1 score **is not a decision** — it does not say ship or not, and
    names no failing question. **(2)** The judge is **an LLM**, i.e. the component
    under test, so it shares the failure modes of the thing it grades. For a
    system whose claim is *"a wrong answer with a ✓ is worse than a refusal"*,
    correctness must be checkable without a model's opinion.
12. Because *"a score printed with a warning above it still gets copied into a
    README."* Withholding is the only reliable way to stop an invalid number
    propagating.
13. Because they are **different faults with different remedies**: mixed provider
    → wait for quota; wrong model → fix the environment and re-run; mixed backend
    → check the Cohere key. Merging them means reading the wrong instruction at
    the worst moment.
14. Because **a suite whose purpose is to detect change must first describe the
    present accurately**, and a test asserting the fixed behaviour of an unfixed
    function is red on arrival and gets ignored. **A failure there means the fix
    landed** — the docstring names the audit finding, and the assertion moves in
    the same commit as the fix.
15. Because they are **whatever ran last**, including rolled-back experiments,
    scoped runs and interrupted sweeps, with names that do not distinguish them —
    `eval_q_titan.json` reads 11/15 and is a rollback artifact, not a result.
    **Baselines live in `docs/IMPLEMENTATION_DELTAS.md`**, dated and with
    providers stated.

### §12 — Debugging

16. **What happened:** the Groq fallback fired on 7 of 55 scored answers — a rate
    limit, a timeout or a 5xx from Gemini. **What you do:** nothing to the code.
    The sweep is void; wait for quota and re-run, and check `--delay` is 25 rather
    than 15 (the arithmetic that caused this before). **What you report:** the
    three lines verbatim — `Providers:`, `Models served:`, and the withholding
    line — **and not the raw tally.** It is printed as "DO NOT publish" precisely
    so it is not.
17. **Check three things.** **(1)** `Providers:` — `groq` present is the
    rate-limit signature. **(2)** Is the failure boundary at a **category edge** or
    at an arbitrary index? Positional = quota. **(3)** Are the failure *reasons*
    API errors and `synthesis_unavailable` exclusions, or wrong values? Wrong
    values are a defect. **Then confirm by re-running tomorrow** — quota passes, a
    defect reproduces exactly.
18. **"That is the baseline, not a break."** 218 passed / 25 errors since
    2026-08-22 — CAVEAT-025. Every error is `tests/test_eval_helpers.py` failing
    at fixture setup, because commit `0cf7e7c` made `--api-base` required and the
    fixture still supplies only `--model`, so the import raises `SystemExit(2)`.
    **The fix is one line and is not being applied here**, because the course's
    contract is comments-only on source and a fixture change is behaviour.
    **And say the second half**, because it is the more important one: the reason
    they would reasonably think they broke it is that `CLAUDE.md` used to state a
    *green* baseline. **A stated baseline that does not match the observed one
    converts an existing defect into a false attribution.** It now states 218/25.

### §12 — System design

19. **Per push — zero LLM calls, must be fast and free.**
    `pytest tests/ -q` (~5 s), the frontend `tsc --noEmit`, and
    `lib/api.retry.guard.ts` (Day 39). **And the baseline must be encoded**, not
    remembered: assert `218 passed / 25 errors` explicitly, or fix CAVEAT-025
    first and require green. **A CI that is red on arrival gets ignored** —
    conftest's own lesson.
    **Nightly — no LLM, needs the corpus.** `regression_check.py` across the five
    documents, and `purge_orphaned_metrics --dry-run` reporting candidates
    without applying anything.
    **Never automatic: `eval_runner.py`.** Every run spends real quota against a
    500/day ceiling shared with development. It stays manual and
    approval-gated — `CLAUDE.md` §5. **If it ever becomes automatic, it needs its
    own paid key**, or a nightly sweep silently starves the working day.
    **The honest note:** this is a portfolio project with no CI at all
    (CAVEAT-022), and the first two tiers are hours of work. The reason to do it
    is exactly the 25 errors: *"the suite is cheap enough to run on every change,
    and that is exactly why nobody notices when it is not run."*
20. **Why no question alone can produce it.** §C's three zero-quota retrieval
    probes established that **the corpus contains no genuine contradiction**:
    every profitability-framed query returns financial statements, because that is
    where profit lives in a results filing, while the narrative discusses NOV,
    order mix and store counts. **The two halves address different subjects, so
    there is nothing to disagree about.** No phrasing changes what is in the
    documents.
    **What it would take: a document.** An earnings-call transcript or an investor
    presentation in which management makes a **directional or magnitude claim**
    about a metric that also exists in `financials` — "margins expanded this
    quarter" against a computed contraction, or "roughly ₹12,000 crore" against a
    verified ₹13,040 Cr.
    **The assertion, once the document exists.** Structural, never on answer text:
    `len(contradictions) == 1`, `contradictions[0].type` (`magnitude` or
    `directional`), `severity`, and `confidence_tier == "medium"` — because a
    high-severity contradiction **caps** the tier (Day 30), which makes the cap
    itself testable for the first time.
    **The trap, and it is why this must not be rushed.** §C: *"a manufactured
    contradiction would train the system to fire on approximation, which is Trap 7
    inverted and worse than no test at all."* A synthetic document written to
    contradict would pass the test while teaching the wrong sensitivity. **The
    document must be real, and found rather than made.**
    **And a cheaper intermediate step**: `detect_contradictions` is pure — regex
    and arithmetic, no LLM, no network (Day 37). **A real contradiction can be
    asserted in `tests/test_contradiction.py` today**, at the unit level, with
    zero quota. That does not test the *path*, but it does test the *detector*,
    and it is available now rather than when a document turns up.

---

## 14. MUST REMEMBER

```text
- NEVER run eval_runner.py without explicit per-run approval
- --delay 25, NOT 15. 5 RPM, 500/day, TWO calls per semantic question
- Largest dataset FIRST, as a gate. If either gate is unclean, STOP
- Read in this order: Providers: → Models served: → the score
- REPORT, DO NOT INTERPRET. Paste those three lines verbatim
- A withheld score is WITHHELD, not annotated — a tally under a caveat ends
  up in a README
- 91 questions · 4 files · 12 categories. 11 adversarial (12%)
- THREE integrity gates, deliberately SEPARATE: provider · model · reranker
  backend. Different faults, different remedies
- Blocked queries are EXCLUDED from the provider gate — no LLM call is made,
  so None is a correct record. The tell was unknown == adversarial, exactly
- synthesis_unavailable is EXCLUDED from numerator AND denominator, and the
  IDs are LISTED (clustered = defect, scattered = transient)
- pytest baseline is 218 passed / 25 errors. NOT GREEN. CAVEAT-025
- conftest patches socket AND psycopg2.connect BY NAME — libpq bypasses
  Python sockets, and that was MEASURED, not assumed
- Some tests assert KNOWN DEFECTS. Their failure means THE FIX LANDED
- eval_results/*.json are NOT BASELINES. Print rows, passes, providers,
  backends, mtime BEFORE the score. Baselines are in IMPLEMENTATION_DELTAS.md
- Quota fails by POSITION. A real defect fails by CATEGORY
- regression_check.py is ZERO LLM calls — run it after EVERY extraction
  change, not batched. Parse each PDF ONCE
- golden_dataset/ is mounted :ro so nothing can write an eval output beside
  the inputs
```

## 15. MUST UNDERSTAND

```text
- Why an LLM judge cannot grade a generative pipeline: it shares the failure
  modes of what it grades — the third instance of "no probabilistic component
  in the deciding path"
- Why an exact value is the only assertion compatible with "a wrong answer
  with a tick is worse than a refusal"
- Why a keyword a WRONG answer also satisfies is not an assertion at all
- Why WHEN a validation fails is part of its cost under a daily budget
- Why absence with a known cause (no LLM call, no citations) is not
  contamination, and why proving that took noticing two counts were equal
- Why an outage must be EXCLUDED rather than failed: the tier cap that is
  correct for the user is EVIDENCE for a pass condition
- Why a suite that is red on arrival gets ignored, and why that argues for
  asserting present behaviour rather than desired behaviour
- Why a heading is not a record, and why a retained-and-wrong heading is a
  convention rather than a mistake
- Why some coverage gaps need a DOCUMENT rather than a better test, and why
  manufacturing the missing case would be worse than leaving it open
```

---

## 16. This connects to

```text
Day 29 — confidence, and what it does not measure
Day 30 · 34 · 37 — the three paths this asserts across
Day 42 — blocked queries make no LLM call
   ↓
Day 43 — evaluation
   ↓
Day 44 — observability, and debugging by layer
```

Forward references:

- `audit_log` as the other record of every run → **Day 44**
- `cache_hit_rate_pct`: a metric with no producer, shipped anyway → **Day 44**
- Why a nightly sweep needs its own key, and the 512 MB ceiling → **Day 45**
- The corrected cross-path coverage claim → **Day 37 §8**, corrected 2026-08-23
