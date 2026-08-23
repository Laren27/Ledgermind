# Day 34 — The Guards, and What `sql_verified` Does Not Guarantee

**Phase 9 · Weight: H (~120 min) · Prerequisites: Day 33**

**Textbook: 15B "The Metadata Filter That Silently Returns Zero Results" —
EXTENDS.** The textbook warns against letting an empty result reach the LLM.
LedgerMind's harder problem is an empty *question* reaching the DSL.

---

## 1. Today's goal

By tonight you can:

- Explain why three regex guards run over the **raw query**, before any LLM call.
- Explain Stage 0 (derived) and Stage 0b (unqueryable), what each caught, and why
  0b records a `dsl_object` while 0a does not.
- Explain the **period-assumption guard**: why it requires *two* conditions, and
  why `period_assumed` is disclosed rather than hidden.
- Explain row-count verification and `ambiguous_result`.
- State precisely what `sql_verified = True` guarantees — **and what it does
  not.**

---

## 2. Why now

Day 32 gave you the DSL and `CAVEAT-004`: required fields force the model to
invent. Day 33 showed the compiler faithfully executing whatever it is handed.
Today is everything that stands between a bad request and a ticked answer.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `CAVEAT-004` — required fields | Day 32 | What the guards exist for |
| `derived_metric_aliases`, `unqueryable_metric_aliases` | Day 31 | The guards' vocabularies |
| The five compilers, and `_execute_sql` | Day 33 | What runs after the guards |
| "Assume, and record the assumption" | Days 16, 23 | `period_assumed` |

---

## 4. Concept lesson

### 4.1 Why the raw query

`quant_engine.py` states it:

> The only place the user's real intent still exists is **the raw query text**, so
> it is checked here, **before Gemini is called at all.**

**Follow the information flow.** By the time the DSL exists, the model has already
mapped the question onto eight fields — and if the question named something the
DSL cannot express (Day 32), that mapping **destroyed the evidence**. The
substituted metric is valid DSL; nothing downstream records what was asked.

**So the guards must run first, and they must read the original words.**

**Mental model.** The DSL is a translation. Once you have only the translation,
you cannot tell whether the original said something untranslatable. **Check the
original.**

---

### 4.2 Stage 0 — derived metrics

```python
_DERIVED_ALIASES = derived_metric_aliases()

# Longest-first so "ebitda margin" wins over "ebitda".
_DERIVED_ALIAS_RE = re.compile(
    "|".join(rf"\b{re.escape(a)}\b" for a in sorted(_DERIVED_ALIASES, key=len, reverse=True)),
    re.IGNORECASE,
)

def _query_names_derived_metric(query: str) -> Optional[str]:
    m = _DERIVED_ALIAS_RE.search(query or "")
    return _DERIVED_ALIASES.get(m.group(0).lower().strip()) if m else None
```

**`sorted(..., key=len, reverse=True)`** — Python's `re` alternation is
**first-match-wins**, not longest-match. Without the sort, `"ebitda"` would match
inside `"ebitda margin"` and the guard would name the wrong metric.

**The refusal:**

```python
derived = _query_names_derived_metric(query)
if derived:
    d = get_metric(derived)
    state["error"] = "metric_not_computable"
    state["error_node"] = "quant_engine"
    state["sql_verified"] = False
    state["confidence_score"] = 0.0
    state["confidence_tier"] = "low"
    state["response_text"] = (
        f"LedgerMind cannot currently compute {label}. It is a derived "
        f"metric" + (f" ({formula})" if formula else "") + ", and the "
        f"deterministic SQL compiler does not yet support deriving metrics "
        f"from their components. Rather than return an approximation, the "
        f"system declines. You can query the component line items directly "
        f"if they were extracted for this filing."
    )
    return state
```

**The message does four things:** names the metric, states *why* (no compiler for
derived metrics), says the decline is deliberate, and **offers the next step**.

Compare with a bare *"metric not available"*. The user cannot tell whether the
metric is unknown, unsupported, or absent from the corpus — three different
situations with three different responses.

**And the failure it prevents**, from `derived_metric_aliases`:

> Observed live 2026-07-29: *"What was Paytm's EBITDA for FY26?"* returned
> `total_expenses` (₹8,523 Cr) **with `sql_verified=True`.**

---

### 4.3 Stage 0b — registered but unqueryable

```python
# Deliberately AFTER the derived guard: a metric can only be in one class,
# but derived carries the more specific explanation, so it wins on order.
unqueryable = _query_names_unqueryable_metric(query)
```

**Ordering by explanation quality**, not by precedence — a metric cannot be both,
so the order only matters for which *message* the user gets.

**And here is the interesting part.** Unlike Stage 0, Stage 0b **records a
`dsl_object`**:

```python
state["dsl_object"] = {
    "metric": unqueryable,
    "entity": companies[0] if len(companies) == 1 else None,
    "fiscal_year": fiscal_year,
    "quarter": quarter,
    "financial_type": financial_type,
    "operation": None,
    "comparison_entity": None,
    "comparison_period": None,
    "guard_refused": "metric_not_queryable",
}
```

The comment explains why:

> **Record WHAT was refused.** Returning before Stage 1 means no DSL is ever
> generated, so `dsl_object` stays `None` — and `response_generator`'s cross
> reconciliation **reads `dsl_object` presence** to distinguish "a metric was
> identified but produced no verified figure" (disclose the gap) from "this query
> never asked for a figure" (say nothing). Without this, a query the guard refused
> on looks identical to a purely qualitative one, and the user is never told the
> system declined to verify the figure they named. **Metric resolution DID happen
> here** — precisely enough to refuse on — so recording it is accurate, not a
> placeholder.

**A guard writing a partial `dsl_object` for a downstream consumer's benefit.**
`guard_refused` is a non-standard key, and its presence is the signal.

**Note `len(companies) == 1`** — the same F14 rule as everywhere else (Day 32).

**And the failure it prevents:**

> *"Paytm … the 207 crore impairment of loans and investments in associates
> recorded in FY26?"* produced `metric="exceptional_items"` and appended *"PAYTM's
> consolidated Exceptional Items for FY26 was ₹−186 Cr"* — **a ticked,
> `sql_verified` figure for a metric nobody asked about.**

---

### 4.4 The period-assumption guard — two conditions

```python
_PERIOD_TOKEN_RE = re.compile(
    r"\bFY\s?\d{2,4}\b"                      # FY26, FY 26, FY2026
    r"|\b20\d{2}\b"                          # 2026
    r"|\bQ[1-4]\b"                           # Q1..Q4
    r"|\bfiscal\s+(?:year\s+)?\d{2,4}\b",    # fiscal year 2026
    re.IGNORECASE,
)

if state.get("fiscal_year") is None and not _query_names_period(query):
    latest = _latest_fiscal_year(dsl["entity"], dsl["metric"], dsl["financial_type"], tenant_id)
    if latest:
        if latest != dsl["fiscal_year"]:
            logger.info("No period in query — overriding invented fiscal_year %s with corpus latest %s ...", ...)
            dsl["fiscal_year"] = latest
            dsl["period"] = latest
        state["period_assumed"] = True
```

**Two conditions, and the second is the interesting one:**

> Overriding on `state["fiscal_year"] is None` **ALONE would be unsafe**: if
> `entity_resolver` misses a year that Gemini correctly read from the query text,
> that would **replace a right answer with a wrong one**. So the override
> additionally requires that **no period token appears in the raw query** — only
> then is Gemini's value provably invented rather than extracted.

**Read the logic:**

| Router found a year? | Query contains a period token? | Conclusion |
|---|---|---|
| yes | — | use it |
| no | **yes** | the model may have read it correctly — **do not override** |
| no | **no** | the value is **provably invented** — override |

**"Provably invented"** is the standard. The guard only fires when it can *prove*
the model made the value up, because the alternative is corrupting a correct
answer.

**And the substitute is derived, never hardcoded:**

```python
def _latest_fiscal_year(entity, metric, financial_type, tenant_id) -> Optional[str]:
    """
    Latest fiscal_year actually present in the corpus for this entity/metric.

    Derived, never hardcoded -- writing "default to FY26" into the prompt is
    the same class of multi-tenant time bomb as the old available_in_corpus
    flag, and would silently go stale the day FY27 lands.

    NOTE: fiscal_year is TEXT, so MAX() is lexical. Correct for FY23..FY99;
    would break at FY100. Acceptable for the next 74 years.
    """
```

**Schema versus state again** (Day 31): "the latest period" is *state*, so it is
queried, not written down. And the lexical-`MAX` limitation is named with its
expiry.

**Then the disclosure**, in `state.py`:

```python
# True when the query named no reporting period and quant_engine
# substituted the latest period present in the corpus. The figure is
# still SQL-verified; what is unverified is that it is the period the
# user meant — so it must be disclosed, not hidden behind a tick.
period_assumed: bool
```

**"The figure is still SQL-verified; what is unverified is that it is the period
the user meant."** That sentence is the whole day. Verification has a **scope**,
and the scope must be stated.

Rendered by `_period_assumption_note` (Day 30) — **one formatter, two paths**:

```
No reporting period was specified in the question — this figure is for FY26,
the most recent period available in the corpus.
```

---

### 4.5 Row-count verification

```python
if operation == "point_in_time":
    if len(rows) == 0:
        state["error"] = "no_data_found"
        state["response_text"] = (
            f"No data found for {dsl['entity']} {dsl['metric']} "
            f"{dsl['fiscal_year']} {dsl['financial_type']}. "
            f"This period may not yet be indexed."
        )
        return state

    if len(rows) > 1:
        # Trap 2 from blueprint: multiple rows means financial_type filter may have failed
        logger.error("point_in_time returned %d rows — expected 1. "
                     "Check financial_type filter. Rows: %s", len(rows), rows)
        state["error"] = "ambiguous_result"
        state["response_text"] = (
            f"Ambiguous result: {len(rows)} rows returned for a single-value query. "
            f"Please specify 'consolidated' or 'standalone' explicitly."
        )
        return state

    state["sql_result"] = rows
    state["sql_verified"] = True
    state["confidence_score"] = 1.0
    state["confidence_tier"] = "high"
```

**Exactly one row. Zero and two are both errors, for different reasons.**

**Zero rows** is *usually* legitimate — the period is not indexed. It is also what
RLS returns when `app.tenant_id` is unset (Day 14), and the message cannot tell
you which. **The same user-facing text for a data gap and a configuration error.**

**Two rows should be impossible.** `uq_financials_latest` (Day 15) enforces one
`is_latest` row per business key including `financial_type` — so two rows means
either the filter dropped `financial_type`, or duplicate `is_latest` rows exist.
**Logged at ERROR with the rows**, because it indicates a defect, and refused
rather than picking one.

**Refusing to pick is the point.** Returning the first row would produce a
`sql_verified` figure that is arbitrarily one of two valid answers.

**And note the asymmetry:** the other four operations set
`sql_verified = computed.get("error") is None` and never check row counts
directly — because the compute functions already return an error dict on missing
data (Day 33). `point_in_time` has no compute function, so its verification is
here.

---

### 4.6 What `sql_verified = True` means

**It guarantees:**

- The DSL validated against the registry and the operation registry (Day 32).
- The SQL was compiled deterministically by Python (Day 33).
- The query ran with RLS scoped to the tenant (Day 14).
- It returned the expected row count.
- Any arithmetic was Python over values from the database.
- Every row traces to a `doc_id`, which traces to a filing.

**It does NOT guarantee:**

| Not guaranteed | Which mechanism covers it |
|---|---|
| The metric is the one the user asked for | Stage 0 / 0b guards — **partial** |
| The period is the one the user meant | `period_assumed` — **disclosed, not guaranteed** |
| The stored value is correctly extracted | Day 31's identity checks — at ingest |
| The value's **unit** is crore | **Nothing.** Audit **F3** |
| The metric name is registry-anchored | **Nothing.** Audit **F6** |
| The question was answerable at all | The three guards |

**`sql_verified` is a statement about the pipeline, not about the world.** It says
*"this number came out of the database by a deterministic path"* — which is a
strong and bounded claim.

---

### 4.7 The response template

```python
def _format_quant_response(state: QueryState) -> str:
```

**Templated, not generated** (Day 30). Branches on `operation` and renders with
`_fmt_money` and `display_label` — **one formatter each**, both of which exist
because there were previously three (Day 31).

**And the quantitative path appends no citations block** (Day 30) — SQL is the
source of truth, and provenance is `doc_id` plus the DSL and SQL on the analyst
response (Day 9).

---

## 5. The actual LedgerMind file

```
File:  backend/app/engines/quant_engine.py (915 lines) — today, lines 300-915
Entry: quant_engine_node(state) -> QueryState
Stages:
  0   _query_names_derived_metric      → metric_not_computable
  0b  _query_names_unqueryable_metric  → metric_not_queryable (+ dsl_object!)
  1   _generate_dsl                    → dsl_generation_failed      (Day 32)
      period-assumption guard          → period_assumed = True
  2   compile_dsl                      → sql_compilation_failed     (Day 33)
  3   _execute_sql                     → sql_execution_failed
  4/5 verify + compute                 → no_data_found / ambiguous_result
Sets:  dsl_object · dsl_valid · dsl_attempts · sql_query · sql_result
       sql_row_count · sql_verified · period_assumed · confidence_*
```

**Seven distinct error codes.** Each names a *stage*, so `error_node` plus `error`
locates a failure exactly.

---

## 6. Deep walkthrough — `quant_engine_node`

**STATE BEFORE.** Post-router: `companies`, `fiscal_year`, `quarter`,
`financial_type`, `path="quantitative"`.

**Stage 0 and 0b** — regex over the raw query, **no LLM call yet**. Either
returns a refusal.

**Stage 1** — `_generate_dsl` (Day 32).

```python
state["dsl_attempts"] = attempts
if dsl_llm is not None:
    record_llm_call(state, dsl_llm)
```

**Attribution recorded even when the DSL failed** — because a call was still made
and a provider still served it (Day 19).

**The failure message distinguishes two cases:**

```python
is_unavailable = dsl_error and "not yet in corpus" in dsl_error
state["response_text"] = (
    f"The metric you asked about ({dsl_error}) is registered in LedgerMind "
    f"but has not yet been extracted for this company. "
    f"Currently available metrics: {_AVAILABLE_METRICS}."
    if is_unavailable else
    f"Could not interpret your financial query as a structured data request. "
    f"Please rephrase using specific metric names like 'revenue', 'total income'. "
    f"Error: {dsl_error}"
)
```

**A substring check on an error message** — fragile, and the alternative would be
a typed error from the validator. Worth noticing as a small piece of debt.

**The period-assumption guard**, then **Stage 2** (compile), **Stage 3**
(execute), **Stages 4/5** (verify and compute).

**STATE AFTER, on success.** `sql_result`, `sql_verified=True`,
`confidence_score=1.0`, `confidence_tier="high"` — and possibly
`period_assumed=True`.

**Note `confidence_score = 1.0`.** The quantitative path does not *score*
confidence; it asserts it. Which is defensible **exactly to the extent that
`sql_verified`'s scope is understood** (§4.6) — and why `period_assumed` must be
disclosed.

**And `confidence_node` can still lower it** (Day 30): a contradiction on the
cross path caps a `sql_verified` figure at `medium`.

---

### 6.1 Guard construction — a detail worth copying

```python
_UNQUERYABLE_ALIAS_RE = re.compile(
    "|".join(rf"\b{re.escape(a)}\b" for a in sorted(_UNQUERYABLE_ALIASES, key=len, reverse=True)),
    re.IGNORECASE,
) if _UNQUERYABLE_ALIASES else None

def _query_names_unqueryable_metric(query: str) -> Optional[str]:
    if _UNQUERYABLE_ALIAS_RE is None:
        return None
    ...
```

**The `if ... else None` guards against an empty alias set.**
`"|".join([])` is `""`, and `re.compile("")` matches **everywhere** — so an empty
registry would make the guard refuse every query.

**A one-line defence against a failure mode that only appears if the registry
changes.** And `re.escape` on every alias, because aliases contain `/`, `&` and
parentheses.

**Contrast with `cross_engine`'s Stage 0c** (Day 37), which uses
`(?<!\w)...(?!\w)` instead of `\b`:

> Word boundaries are `(?<!\w)...(?!\w)`, NOT `\b`: many phrases end or begin with
> non-word characters ("d&a", "impairment of loans/investment in associates")
> where `\b` asserts the opposite of what is wanted.

**Two guards, two boundary strategies**, because their vocabularies differ.
`IMPLEMENTATION_DELTAS.md` §D records that `metric_anchor_phrases()` matches
substrings rather than words — a latent risk in the third guard.

---

## 7. Data flow

```
"What was Paytm's EBITDA for FY26?"
        │
        ▼ router_node → path="quantitative"                    (Day 36)
        ▼ quant_engine_node
        │
   ┌────▼──────────────────────────────────────── STAGE 0 ────┐
   │ _query_names_derived_metric(RAW QUERY)                   │
   │   regex over derived_metric_aliases(), longest-first     │
   │   HIT → metric_not_computable · sql_verified=False       │
   │         tier=low · a message naming the metric AND the   │
   │         reason AND the next step.  NO LLM CALL MADE.     │
   └────┬─────────────────────────────────────────────────────┘
        │ miss
   ┌────▼──────────────────────────────────────── STAGE 0b ───┐
   │ _query_names_unqueryable_metric(RAW QUERY)               │
   │   min 4 words · canonical names included                 │
   │   HIT → metric_not_queryable                             │
   │         + WRITES a partial dsl_object with guard_refused │
   │           so _reconcile_cross can tell "identified but   │
   │           unverifiable" from "never asked"      (Day 37) │
   └────┬─────────────────────────────────────────────────────┘
        │ miss
        ▼ STAGE 1  _generate_dsl  ← FIRST LLM CALL             (Day 32)
        │   record_llm_call even on failure
        │
   ┌────▼────────────────────── PERIOD-ASSUMPTION GUARD ──────┐
   │ router found no year   AND   no period token in the raw  │
   │ query  →  the model's value is PROVABLY INVENTED         │
   │   _latest_fiscal_year(...)  ← DERIVED, never hardcoded   │
   │   dsl["fiscal_year"] = latest                            │
   │   state["period_assumed"] = True   → DISCLOSED (Day 30)  │
   └────┬─────────────────────────────────────────────────────┘
        ▼ STAGE 2  compile_dsl                                 (Day 33)
        ▼ STAGE 3  _execute_sql   SET LOCAL app.tenant_id      (Day 14)
        │
   ┌────▼────────────────────────────────── STAGES 4 / 5 ─────┐
   │ point_in_time: 0 rows → no_data_found                    │
   │                >1 rows → ambiguous_result (LOG AT ERROR)  │
   │                exactly 1 → sql_verified=True, conf 1.0    │
   │ others: _compute_*() → error dict or values               │
   │         sql_verified = computed.get("error") is None      │
   └────┬─────────────────────────────────────────────────────┘
        ▼ confidence_node — MAY LOWER, never raise             (Day 30)
        ▼ _format_quant_response + _period_assumption_note
        ▼ audit_writer                                          (Day 44)
```

---

## 8. Engineering decision — refuse before generating

**Problem.** A required-field schema forces the model to invent, and an invented
value validates, compiles, executes and gets a tick.

**Decision.** Three deterministic regex guards over the **raw query**, before any
LLM call, each refusing with a specific message.

`ENGINEERING_DECISIONS.md` **ED-001** (the invariant), and the guards themselves.

| Alternative | Why not |
|---|---|
| **Make DSL fields optional** | Moves the problem into the validator; the model still has to choose (Day 32) |
| **Validate the DSL harder** | A substituted metric is **valid DSL**. The validator cannot see what was asked |
| **Ask the model whether it substituted** | A second call, and self-report is not evidence |
| **Post-hoc check: does the answer mention the metric?** | Checks the *answer*, not the *question*; and the answer is templated from the substituted metric |
| **A broad keyword scan** | Would fire on "cash", "others", "india". Hence `UNQUERYABLE_MIN_WORDS = 4` |

**Trade-offs accepted.**

- **Regex over natural language** — misses paraphrases the alias list does not
  contain. **Fails toward not firing** (Day 31), so a miss is the status quo.
- **Three guards in two files**, with different boundary strategies.
- **Stage 0b writes a partial `dsl_object`** for a consumer two modules away — a
  real coupling, documented at both ends.
- **`sql_verified` remains a pipeline claim**, not a world claim. Understanding
  its scope is required to use it correctly, and that understanding is documented
  rather than enforced.

**Current validity.** Strong, and explicitly partial. Each guard names the live
query that motivated it.

**At 10×** — more metrics and more phrasings. The alias-driven design scales with
the registry; the risk is `metric_anchor_phrases()`'s substring matching, already
recorded as latent.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| A verified figure for a metric nobody asked about | A guard missed it. `CAVEAT-004` |
| `metric_not_computable` on a legitimate question | Stage 0 fired on an alias in passing |
| Every query refused by a guard | An empty alias set → `re.compile("")` matches everywhere |
| `"ebitda"` matched inside `"ebitda margin"` | Aliases not sorted longest-first |
| A figure for a period the user did not mean | `period_assumed` — **disclosed**, not prevented |
| A right answer overridden by the corpus latest | Would be the period guard firing on one condition |
| `ambiguous_result` | Two `is_latest` rows, or the `financial_type` filter dropped |
| `no_data_found` when data exists | **RLS unset** (Day 14) — same message as a real gap |
| A figure wrong by 10× | Audit **F3**. `sql_verified` says nothing about units |

---

## 10. Hands-on experiment

### Experiment 1 — the guards, without an LLM

```bash
docker compose exec -T backend python -c "
from app.engines.quant_engine import (_query_names_derived_metric,
    _query_names_unqueryable_metric, _query_names_period)
qs = [
 'What was Paytm EBITDA for FY26?',
 'What was Eternal ebitda margin in FY26?',
 'What was Eternal revenue in FY26?',
 'What is Paytm financial exposure to Paytm Payments Bank?',
 'Tell me about the impairment of loans and investments in associates',
 'What were the revenue drivers?',
 'Revenue for fiscal year 2026',
 'Revenue in Q4',
]
for q in qs:
    print(f'{q!r}')
    print(f'   derived={_query_names_derived_metric(q)!r:22} '
          f'unqueryable={_query_names_unqueryable_metric(q)!r:22} '
          f'period={_query_names_period(q)}')
"
```

**Zero LLM calls.** That is the point of doing this first.

### Experiment 2 — longest-first alternation

```bash
docker compose exec -T backend python -c "
import re
from app.engines.quant_engine import _DERIVED_ALIASES
q = 'What was the ebitda margin in FY26?'
naive  = re.compile('|'.join(rf'\b{re.escape(a)}\b' for a in _DERIVED_ALIASES), re.I)
sorted_ = re.compile('|'.join(rf'\b{re.escape(a)}\b'
                     for a in sorted(_DERIVED_ALIASES, key=len, reverse=True)), re.I)
print('unsorted match     :', (naive.search(q)  or [None]) and naive.search(q).group(0))
print('longest-first match:', (sorted_.search(q) or [None]) and sorted_.search(q).group(0))
print()
print(\"Python's re alternation is FIRST-match-wins, not longest-match.\")
"
```

### Experiment 3 — the empty-set trap

```bash
docker compose exec -T backend python -c "
import re
empty = re.compile('|'.join([]))
print('re.compile(\"\") matches empty string:', bool(empty.search('anything at all')))
print('  span:', empty.search('anything at all').span())
print()
print('An empty alias set would make the guard refuse EVERY query.')
print('Hence: _UNQUERYABLE_ALIAS_RE = ... if _UNQUERYABLE_ALIASES else None')
"
```

### Experiment 4 — the period guard's two conditions

```bash
docker compose exec -T backend python -c "
from app.engines.quant_engine import _query_names_period
cases = [
 ('router found FY26, query says FY26', 'FY26', 'What was revenue in FY26?'),
 ('router found nothing, query says 2026', None, 'What was revenue in 2026?'),
 ('router found nothing, query says fiscal year 2026', None, 'Revenue for fiscal year 2026'),
 ('router found nothing, query says nothing', None, 'What was revenue?'),
]
for label, router_fy, q in cases:
    tok = _query_names_period(q)
    fires = (router_fy is None) and not tok
    print(f'  {label:46} token={tok!s:5} OVERRIDE={fires}')
print()
print('It fires ONLY when the value is PROVABLY invented. Row 2 and 3 do not')
print('fire — the model may have read the period correctly from the text.')
"
```

### Experiment 5 — a guard refusing, end to end

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ANALYST" -H "Content-Type: application/json" \
  -d '{"query":"What was Paytm EBITDA for FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('error        :', d.get('error'))
print('error_node   :', d.get('error_node'))
print('sql_verified :', d.get('sql_verified'))
print('confidence   :', d.get('confidence_tier'))
print('dsl_object   :', d.get('dsl_object'), ' <- None: Stage 0 returns BEFORE any DSL')
print()
print(d.get('response_text'))
"
```

Then Stage 0b, and watch `dsl_object` behave differently:

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ANALYST" -H "Content-Type: application/json" \
  -d '{"query":"Tell me about the impairment of loans and investments in associates for Paytm"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('error     :', d.get('error'))
print('dsl_object:', json.dumps(d.get('dsl_object'), indent=2))
print()
print('Stage 0b RECORDS what was refused, including guard_refused —')
print('so _reconcile_cross can tell it from a query that never asked for a figure.')
"
```

### Experiment 6 — `period_assumed`, disclosed

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ANALYST" -H "Content-Type: application/json" \
  -d '{"query":"What was Eternal revenue?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('sql_verified :', d.get('sql_verified'))
print('confidence   :', d.get('confidence_tier'))
print('dsl fiscal_yr:', (d.get('dsl_object') or {}).get('fiscal_year'))
print()
print(d.get('response_text'))
print()
print('The figure IS verified. What is NOT verified is that FY26 is the period')
print('the user meant — so it is disclosed rather than hidden behind the tick.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/quant_engine.py` (lines 300–915):

1. Why do the guards read `state["query"]` rather than the DSL? What evidence is
   destroyed by the time the DSL exists?
2. Stage 0b writes a partial `dsl_object`; Stage 0 does not. Find the comment and
   name the downstream consumer.
3. The period guard has two conditions. What goes wrong with only the first?
4. `point_in_time` refuses on `len(rows) > 1` rather than taking the first. Why?
5. List three things `sql_verified = True` does **not** guarantee, and say which
   mechanism (if any) covers each.

---

## 12. Self-check questions

**Basic**
1. Name the three guards and where each lives.
2. Why do they read the raw query?
3. What does `period_assumed` mean?
4. How many rows must `point_in_time` return?
5. What are the seven error codes?

**Code**
6. Why are aliases sorted longest-first?
7. Why is `_UNQUERYABLE_ALIAS_RE` conditionally `None`?
8. What does `_latest_fiscal_year` query, and what is its stated limitation?
9. What non-standard key does Stage 0b write, and why?
10. What sets `sql_verified` for the non-`point_in_time` operations?

**Why**
11. Why refuse before generating rather than validating harder?
12. Why does the period guard need two conditions?
13. Why is `ambiguous_result` logged at ERROR?
14. Why is `_latest_fiscal_year` derived rather than a constant?
15. Why does the quantitative path assert `confidence_score = 1.0` instead of
    scoring?

**Debugging**
16. `no_data_found` for a figure you can see in the database. What do you check
    first?
17. Every quantitative query is refused by a guard. What changed?
18. `ambiguous_result` on a query that worked yesterday. Two hypotheses.

**System design**
19. Add a fourth guard for "the user asked for a ratio we do not compute". Where
    does it go and what must it not do?
20. `sql_verified` is a pipeline claim, not a world claim. Design a way to make
    the scope visible to a user without weakening the tick.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Because **the raw query is the only place the user's real intent still
   exists.** By the time the DSL exists, the model has already mapped the question
   onto eight required fields — and if the question named something the DSL cannot
   express, that mapping **destroyed the evidence**: the substituted metric is
   valid DSL, and nothing downstream records what was actually asked.
2. The comment says: *"`response_generator`'s cross reconciliation reads
   `dsl_object` presence to distinguish 'a metric was identified but produced no
   verified figure' (disclose the gap) from 'this query never asked for a figure'
   (say nothing)."* **The consumer is `_reconcile_cross`** in
   `response_generator.py` (Day 37). Without the record, a guard-refused query
   would look identical to a purely qualitative one and the user would never be
   told the system declined to verify the figure they named.
3. With only *"the router found no fiscal year"*, the guard would fire whenever
   `entity_resolver` **missed** a year that the model had **correctly read from the
   query text** — replacing a right answer with the corpus latest. The second
   condition (no period token in the raw query) is what makes the model's value
   **provably invented** rather than merely unconfirmed.
4. Because two rows means the system genuinely does not know which is correct —
   most likely the `financial_type` filter failed, so one row is consolidated and
   one standalone, **both valid**. Returning the first would produce a
   `sql_verified` figure that is arbitrarily one of two right answers, which is
   worse than refusing. It is also logged at ERROR because it indicates a defect
   (a partial-unique-index violation should have made it impossible).
5. **(a) The metric is the one asked for** — covered *partially* by Stage 0/0b.
   **(b) The period is the one meant** — covered by `period_assumed`, which
   *discloses* rather than guarantees. **(c) The unit is crore** — covered by
   **nothing**; audit F3. Also acceptable: the stored value was extracted
   correctly (covered at ingest by the identity checks, Day 31), or the metric name
   is registry-anchored (nothing; audit F6).

### §12 — Basic

1. **Stage 0** `_query_names_derived_metric` and **Stage 0b**
   `_query_names_unqueryable_metric`, both in `quant_engine.py`; **Stage 0c**
   `_query_lacks_metric_anchor` in `cross_engine.py` (Day 37).
2. See §11 Q1.
3. That the query named no reporting period and the engine substituted the latest
   period present in the corpus.
4. **Exactly one.** Zero is `no_data_found`; more than one is `ambiguous_result`.
5. `metric_not_computable`, `metric_not_queryable`, `dsl_generation_failed`,
   `sql_compilation_failed`, `sql_execution_failed`, `no_data_found`,
   `ambiguous_result` (plus `insufficient_data_for_cagr`).
6. Because Python's `re` alternation is **first-match-wins**, not longest-match —
   so `"ebitda"` would match inside `"ebitda margin"` and the guard would name the
   wrong metric.
7. Because `"|".join([])` is `""`, and `re.compile("")` **matches everywhere** —
   an empty alias set would make the guard refuse every query.
8. `SELECT MAX(fiscal_year) FROM financials WHERE tenant_id/company/metric/
   financial_type AND is_latest`. **Limitation:** `fiscal_year` is TEXT, so `MAX()`
   is **lexical** — correct for FY23–FY99, breaking at FY100.
9. `"guard_refused": "metric_not_queryable"` — a marker on an otherwise partial
   DSL object, so a downstream consumer can tell this was a guard refusal rather
   than a completed DSL.
10. `state["sql_verified"] = computed.get("error") is None` — the compute
    functions return an error dict on missing data, so one line covers all four.

### §12 — Why

11. Because **a substituted metric is valid DSL.** The validator checks the DSL
    against the registry and finds nothing wrong — the error is that the DSL
    describes a different question from the one asked, and only the raw query
    holds that evidence.
12. See §11 Q3.
13. Because it indicates a **defect**, not a data condition:
    `uq_financials_latest` should make two `is_latest` rows for one business key
    impossible (Day 15), so its occurrence means either the filter dropped
    `financial_type` or the index is missing/violated. The rows are logged so the
    defect can be diagnosed.
14. Because "the latest period in the corpus" is **state, not schema** (Day 31) —
    hardcoding "FY26" would go stale the day FY27 lands, silently, and would be
    wrong per-tenant. The comment calls it "the same class of multi-tenant time
    bomb as the old `available_in_corpus` flag".
15. Because there is nothing to score. The semantic path scores retrieval quality
    because relevance is a matter of degree; a SQL result either came out of the
    database by a deterministic path or it did not. The assertion is defensible
    **exactly to the extent that `sql_verified`'s scope is understood** — which is
    why `period_assumed` exists, and why `confidence_node` can still lower it.

### §12 — Debugging

16. **`app.tenant_id`.** RLS returns zero rows when the GUC is unset (Day 14), and
    `_execute_sql` sets it from `state["tenant_id"]` — which `CAVEAT-001` lets the
    **request body** override (Day 5). So: check the request for a `tenant_id`
    field, then confirm which tenant the query was scoped to. The user-facing
    message is identical for a genuine data gap and a scoping error, which is
    exactly why this is the first check.
17. Most likely **the alias registry emptied** — a change to `metric_type` or
    `dsl_enabled` across the registry could empty `_DERIVED_ALIASES` or
    `_UNQUERYABLE_ALIASES`, and while `_UNQUERYABLE_ALIAS_RE` guards against that
    with `if ... else None`, an empty set feeding an unguarded `"|".join` would
    produce a regex matching everywhere. Also possible: `UNQUERYABLE_MIN_WORDS`
    lowered, admitting short aliases like "cash" and "others" that fire on almost
    any query — which is precisely why the 4-word floor exists.
18. **(a)** Duplicate `is_latest` rows were created — a re-ingest that bypassed
    the loader, or the partial unique index missing in that database (Day 16's
    two-database problem). **(b)** The `financial_type` filter is no longer being
    applied, so consolidated and standalone rows both match. Check the logged rows
    in the ERROR line — if they differ only in `financial_type`, it is (b).

### §12 — System design

19. **Where:** a **Stage 0d** in `quant_engine.py`, after 0b, following the same
    shape — a module-level alias set from the registry, a longest-first regex with
    `re.escape` and an empty-set guard, and a `_query_names_*` helper returning the
    canonical name or `None`. **What it must not do:** fire loosely. Ratio words
    ("margin", "per", "rate", "ratio") appear constantly in financial prose, so a
    bare keyword scan would refuse legitimate questions — the same reason
    `derived_metric_aliases` is restricted to derived metrics and
    `unqueryable_metric_aliases` has a four-word floor. It must **fail toward not
    firing**, so a miss leaves current behaviour unchanged. It should also carry a
    message naming the ratio, the reason, and the components the user *can* query
    — and, like 0b, record a partial `dsl_object` if the cross path needs to
    distinguish it. Note that most ratios are already `metric_type="derived"`, so
    Stage 0 may cover them; the guard is only worth adding if a measured case shows
    otherwise.
20. **Do not weaken the tick — qualify it.** `sql_verified` already means something
    precise, so the fix is to surface the *scope* alongside it rather than
    softening the flag. Concretely: the response already carries `period_assumed`,
    and `_period_assumption_note` renders it — extend that pattern to the other
    unguaranteed dimensions. Add a `verification_scope` object to the analyst/admin
    response listing what was and was not established: `{"period": "assumed" |
    "stated", "metric": "named" | "inferred", "unit": "asserted"}` — with `unit`
    permanently `"asserted"` until F3 closes, which makes the open gap **visible in
    every response** rather than living only in a caveats file. **Why this and not a
    softer flag:** a boolean that sometimes means "mostly verified" is worse than
    one that means exactly one thing plus a scope; and the zero-hallucination
    mandate's rule — *omit rather than substitute* — argues for stating what was
    checked rather than hedging what was not. The frontend already omits fields it
    cannot substantiate (Day 40), so it has the pattern.

---

## 14. MUST REMEMBER

```text
- THREE guards, all regex over the RAW QUERY, all BEFORE any LLM call
  Stage 0  derived metrics        → metric_not_computable
  Stage 0b registered-unqueryable → metric_not_queryable (+ partial dsl_object)
  Stage 0c no metric anchor       → cross path only          (Day 37)
- Aliases sorted LONGEST-FIRST — re alternation is first-match-wins
- An empty alias set would make re.compile("") match EVERYWHERE. Guarded
- The period guard needs BOTH: router found nothing AND no period token
  → only then is the model's value PROVABLY invented
- _latest_fiscal_year is DERIVED, never hardcoded. MAX() is lexical (to FY99)
- period_assumed is DISCLOSED, not hidden behind the tick
- point_in_time: exactly ONE row. 0 = no_data_found, >1 = ambiguous_result (ERROR)
- sql_verified is a claim about THE PIPELINE, not about the world
- It says NOTHING about units (F3) or registry anchoring (F6)
```

## 15. MUST UNDERSTAND

```text
- Why a substituted metric is VALID DSL, and why that makes the validator blind
  to it — so the guard must read the original question
- Why "provably invented" is the standard for overriding a model's value
- Why refusing to pick between two valid rows is better than picking one
- Why verification has a SCOPE, and why the scope must be stated rather than
  implied by a tick
- Why a guard that fails toward NOT firing is the safe direction
- Why Stage 0b records what it refused, for a consumer two modules away
```

---

## 16. This connects to

```text
Day 33 — compiled and computed
   ↓
Day 34 — the guards, and the limits of the tick    ← END OF PHASE 9
   ↓
Day 35 — LangGraph: how the paths are wired together
```

Forward references:

- Stage 0c, and `metric_anchor_phrases`'s substring risk → **Day 37**
- `_reconcile_cross` reading `dsl_object` presence → **Day 37**
- `_format_quant_response` and `_period_assumption_note` → **Day 30** (already read)
- `confidence_node` lowering a verified tier → **Day 30** (already read)
- Audit **F3** (unit) and **F6** (anchoring) → **Day 31** (already read)
- The eval's `quantitative_*` categories → **Day 43**
