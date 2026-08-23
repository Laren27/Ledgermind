# Day 31 — The Metric Registry, and How a Number Becomes a Row

**Phase 9 — The quantitative path · Weight: H (~120 min) · Prerequisites: Days 13, 24**

**Textbook: 14.3–14.5 "Case study — why the SQL path needs its own evaluation" —
CONFIRMS.**

---

## 1. Today's goal

By tonight you can:

- Explain why one registry replaced three, and name the three shipped bugs the
  split caused.
- Explain the six **derived views** the registry exposes, and why each consumer
  gets a projection rather than the raw table.
- Explain how a printed figure becomes a `FinancialRecord`: label normalisation,
  the regex ordering constraint, and derived totals.
- Explain `_compute_derived_totals` and `validate_financial_identities` — **two
  independent formula copies that must be updated together** — and why that
  duplication was not removed.
- Explain audit **F3** (unit) and **F6** (unanchored metrics) as the two open
  gaps here.

---

## 2. Why now

Days 25–30 covered the semantic path end to end. Today starts the other half.
Before the DSL (Day 32) can name a metric, something must have put that metric in
the database — and something must define what the name *means*.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `financials` columns, `metric` has no `CHECK` | Day 13 | The registry is the closed set instead |
| `FinancialRecord`, frozen dataclasses | Days 10, 15 | The types |
| `block_type == FINANCIAL_STATEMENT` | Day 23 | What extraction targets |
| Positional extraction, `NOT_PRINTED` | Day 22 | Where the numbers come from |

---

## 4. Concept lesson

### 4.1 Three registries, three shipped bugs

`registry.py`'s docstring:

> Prior to this refactor, metric definitions were split across **three
> independently hand-maintained dicts**:
> - `entity_resolver.py` `METRIC_ALIASES` (ingestion-side: label text → canonical)
> - `dsl_compiler.py` `METRIC_REGISTRY` (query-side: which metrics are DSL-queryable)
> - `quant_engine.py` `ALIASES` (prompt-side: prose aliases for Gemini)
>
> **Every one of the following real, shipped bugs was a direct consequence:**
> - `profit_before_tax` was **entirely absent** from `dsl_compiler`'s registry, so
>   Gemini had no correct option and **silently substituted "pat"** instead.
> - `exceptional_items` collapsed three distinct line items (OCI FX translation,
>   OCI remeasurement of defined benefit plans, PPE disposal gain/loss) into one
>   canonical name in `entity_resolver`, causing a **genuinely-blank cell to be
>   silently backfilled by an unrelated row's value.**
> - Titan's segment revenue had **no canonical home in any registry** and fell
>   through as unmapped.

**Three different failure shapes from one cause:**

| Bug | Shape |
|---|---|
| PBT absent from the query-side dict | The model had no correct option, so it picked a wrong one |
| `exceptional_items` over-collapsed | Three facts became one; a blank cell inherited a value |
| Titan segments unmapped | A real figure had nowhere to go |

**The fix was not "keep them in sync".** It was to make the second and third
copies **derived**:

> This file is the only place a metric is defined. All three consumers above now
> **import from here and derive their own view** of the data instead of
> maintaining a parallel copy.

---

### 4.2 Schema versus state

> This registry defines **semantics**: canonical name, known aliases (including
> OCR-mangled variants), whether a metric is a direct line item or must be
> computed, and whether it's queryable via the DSL/SQL path at all …
>
> This file deliberately does **NOT** track whether a given metric has actually
> been extracted for a given company/period. **That is data state, not schema**,
> and belongs at query time.

**The line is worth internalising.** *"Does `revenue` mean turnover?"* is schema —
constant, versioned with the code. *"Do we have Eternal's FY26 revenue?"* is
state — changes with every ingest.

Mixing them produces a flag that goes stale silently, which the file names as the
thing it replaced (`available_in_corpus`). Instead, a zero-row SQL result **is**
the answer (Day 34's `no_data_found`).

---

### 4.3 `MetricDefinition`

```python
@dataclass(frozen=True)
class MetricDefinition:
    canonical_name: str
    aliases: tuple[str, ...]
    metric_type: MetricType          # Literal["raw", "derived"]
    dsl_enabled: bool
    label: str
    prompt_aliases: str = ""
    prompt_warning: str | None = None
    derivation_formula: str | None = None
```

**Two independent booleans, and confusing them is the day's most common error:**

| | `metric_type` | `dsl_enabled` |
|---|---|---|
| Question | *Is it a printed line item, or computed from others?* | *May a user query it?* |
| `raw` + `True` | `revenue` — printed, queryable | |
| `derived` + `True` | `ebitda` — computable in principle, **no compiler yet** | |
| `raw` + `False` | OCI sub-lines — printed, exist only to give ingestion a dedup target | |

Two guards exist because there are two failure modes (§4.5).

**And the integrity check at import:**

```python
_BY_CANONICAL = {m.canonical_name: m for m in ALL_METRICS}
if len(_BY_CANONICAL) != len(ALL_METRICS):
    ...
    raise ValueError(f"Duplicate canonical_name(s) in metric registry: {dupes}")
```

A duplicate canonical name would silently shadow one definition. The check runs at
**import** (Day 12), so it fails at startup, not at query time — and it names the
duplicates rather than just failing.

---

### 4.4 Six views of one table

```python
all_alias_pairs()             # ingestion: every alias → canonical
dsl_registry()                # dsl_compiler: canonical → {label, available}
dsl_alias_pairs()             # dsl_compiler: alias → canonical, DSL-enabled only
prompt_metric_lines()         # quant_engine: prose lines for the LLM prompt
prompt_warnings()             # quant_engine: disambiguation notes
derived_metric_aliases()      # quant_engine Stage 0 guard
unqueryable_metric_aliases()  # quant_engine Stage 0b guard
metric_anchor_phrases()       # cross_engine Stage 0c guard
display_label()               # response_generator
```

**Each consumer gets a projection.** Nobody imports `ALL_METRICS` and filters it
themselves — because that filtering *is* the thing that used to drift.

**`display_label` deserves reading**, because it is the failure class in miniature:

> Exists because `_format_quant_response` carried **THREE conventions for the same
> job**: `.replace("_"," ").title()` in `point_in_time` (which rendered "pat" as
> "Pat"), the raw key in `yoy_growth`, and the raw key `.lower()`'d in
> `comparison`. **Same failure class as the three metric registries — one concept,
> several copies, drifting.**

Three renderings of one metric name **inside one function**. The fix is one
function with a documented fallback:

> Falls back to a title-cased key for metrics not in the registry — `financials`
> stores unmapped OCR line items as-is … so a row's metric column is **not
> guaranteed to be a registry key**.

**That fallback is audit F6 acknowledged in code.**

---

### 4.5 Two guards, two failure modes

**`derived_metric_aliases()` — Stage 0:**

> These metrics have no SQL formula compiler, so an LLM asked for one cannot emit
> a computable answer — and when forced to choose from the available list it
> **substitutes the nearest plausible metric rather than refusing.** Observed live
> 2026-07-29: *"What was Paytm's EBITDA for FY26?"* returned `total_expenses`
> (₹8,523 Cr) **with `sql_verified=True`.**
>
> Restricted to derived metrics **deliberately**: their aliases are distinctive.
> Scanning every registry alias would false-positive constantly on short aliases
> of non-queryable metrics ("others", "india", "cash", "equity").

**`unqueryable_metric_aliases()` — Stage 0b, the sibling:**

> Observed live 2026-07-30: *"Paytm … the 207 crore impairment of loans and
> investments in associates recorded in FY26?"* produced
> `metric="exceptional_items"` and appended *"PAYTM's consolidated Exceptional
> Items for FY26 was ₹−186 Cr"* — **a ticked, `sql_verified` figure for a metric
> nobody asked about.**

Two details in that function are worth extracting:

**Canonical names are included as matchable phrases:**

> **This is load-bearing, not tidiness:** every stored alias for the impairment
> metric uses a slash ("loans/investment"), while the query and the canonical name
> use "and". **Aliases alone would not have matched the very query that exposed
> the bug.**

**And the false-positive guard, `UNQUERYABLE_MIN_WORDS = 4`:**

> `dsl_enabled=False` covers aliases like "cash", "equity", "others", "orders",
> "india" — scanning those would fire on almost any query … A 4-word floor makes
> matches specific enough to be intentional. **Conservative on purpose: it fails
> toward NOT firing**, so behaviour is unchanged anywhere the phrase is not
> unmistakable.

**"Fails toward not firing."** A guard that fires wrongly refuses a legitimate
question; one that fails to fire falls back to the pre-existing behaviour. Given
a choice, degrade to the status quo.

---

### 4.6 From a printed figure to a row

```
FINANCIAL_STATEMENT blocks                                   (Day 23)
        ▼  detect_column_layout / find_fully_populated_row_centers   (Day 22)
   (description, [v1, v2, ...]) rows
        ▼  _should_skip_row(description, values)
        ▼  normalize_metric_label(description)          entity_resolver
        ▼  resolve_metric(...)  →  canonical, via all_alias_pairs()
        ▼  _rows_to_records()
   FinancialRecord(company, metric, fiscal_year, quarter,
                   financial_type, value, unit='crore_inr', doc_id, filing_date)
        ▼  _compute_derived_totals()
        ▼  validate_financial_identities()
        ▼  db_loader.load_financial_records()            (Day 15)
   financials rows
```

**`normalize_metric_label` is where OCR damage is repaired**, and the regex
ordering constraint you met on Day 22 lives here:

```python
SPLIT_INITIAL_RE  # rejoins "I nterest" → "Interest"     MUST RUN FIRST
PREFIX_RE         # strips "(i) ", "1. ", "a) " list markers
META_RE           # strips "(unaudited)", "(consolidated)", "(Rs. in crores)"
UNITS_OUTSIDE_PARENS_RE
TRAILING_PUNCT_RE / LEADING_PUNCT_RE / MULTISPACE_RE / SLASH_RE / ...
FOOTNOTE_RE       # strips "(1)" at the end
```

> This MUST run before `PREFIX_RE`: after casefolding, a bare leading "i " or
> "l " is a legal roman numeral, so `PREFIX_RE` stripped it and produced metrics
> named `nterest_expense` / `oan_given`.

**An ordering constraint stated only in a comment, whose violation silently
renames metrics.** Nothing enforces it.

---

### 4.7 Derived totals, and the overwrite guard

```python
DERIVED_OVERWRITE_MAX_DIVERGENCE = 0.05

def _derivation_within_tolerance(read_val, computed_val) -> bool: ...
def _compute_derived_totals(records) -> list[FinancialRecord]: ...
```

Some totals are **computed from components** — `total_income = revenue +
other_income` — because the printed total may be missing or misread.

**The guard:** if the computed total differs from the printed one by **more than
5%**, do not overwrite.

**And the reasoning behind that number is the README's headline incident**
(Day 2):

> **A 10,000 Cr error laundered through arithmetic.** OCR split `17,292` into `I`
> and `7,292`; a rule that treated any comma-bearing fragment as a complete number
> kept the second and discarded the first. **Derivation then recomputed total
> income and total expenses *from* the corrupted revenue, overwriting two rows OCR
> had read correctly.** The stored column was internally self-consistent — which is
> exactly why it survived review.

**Read that twice.** Derivation *propagated* a misread. The result was internally
consistent, so every arithmetic check passed. The standing rule that came out of
it:

> **A derivation overwrite whose magnitude is not rounding-scale is a misread
> component until proven otherwise.**

That is what `DERIVED_OVERWRITE_MAX_DIVERGENCE = 0.05` encodes.

---

### 4.8 Two formula copies, deliberately

```python
IDENTITY_TOLERANCE_PCT = 0.5

def validate_financial_identities(records) -> list[dict]: ...
```

Checks accounting identities — *total income = revenue + other income*,
*PBT = total income − total expenses* — and reports divergences.

**And `CLAUDE.md` §6:**

> **Both formula copies must be updated together:** `_compute_derived_totals()`
> and `validate_financial_identities()` are **independent**.

**Why two copies of one formula, in a codebase that consolidated three
registries?**

Because they answer **different questions**:

| | `_compute_derived_totals` | `validate_financial_identities` |
|---|---|---|
| Question | *What should this total be?* | *Do the stored figures agree?* |
| Effect | **writes** a value | **reports** a divergence |
| Tolerance | 5% (overwrite guard) | 0.5% (identity check) |

**If the validator called the computer, it could not detect an error in the
computer.** A check that shares an implementation with the thing it checks
validates nothing — which is exactly the "a checker narrower than the system it
reports on" family from Day 16.

**So this is not the registry situation inverted.** One *fact* gets one copy;
**one fact and its independent check get two implementations, on purpose.** The
`CLAUDE.md` rule exists because the duplication is deliberate and therefore
fragile.

**And the interaction between the two tolerances:**

> Why is `DERIVED_OVERWRITE_MAX_DIVERGENCE` deliberately allowed to **produce**
> identity failures?

Because the overwrite guard is permissive (5%) and the identity check is strict
(0.5%). A derivation that overwrites at 3% divergence will then **fail the
identity check** — and that is the design: the write is allowed, and the
disagreement is **reported** rather than suppressed.

---

### 4.9 The two open gaps

**Audit F3 — `unit`.** `CAVEAT-005`:

> Every stored value is asserted to be in crore … `unit` is hardcoded to crore,
> **and the number cleaner is calibrated to crore too.**

Day 13 established there is **no negative case in the corpus** to test a detector
against. F3 is the named blocker for arbitrary documents.

**Audit F6 — unanchored metrics.** 174 stored metric names have no registry
anchor, across **686 of 1,437 rows**. `financials.metric` has no `CHECK` (Day 13),
so an unmapped OCR line item is stored as-is — which is why `display_label` needs
a fallback, and why `resolve_metric` has an *"Unknown metric … storing as-is"*
path.

**Storing it as-is is defensible.** The alternative is discarding a real figure
because we could not name it. The cost is that the metric is unqueryable and
`display_label` renders a raw key.

---

## 5. The actual LedgerMind files

```
File:  backend/app/metrics/registry.py (768 lines)
       ALL_METRICS: tuple[MetricDefinition, ...]   ← THE single definition
       9 accessor functions, one per consumer
       Duplicate-name check at import

File:  backend/app/ingestion/financial_extractor.py (908 lines)
       Entry:  extract_all_financial_records(...) -> list[FinancialRecord]
       Key:    detect_column_layout · _should_skip_row · _rows_to_records
               _compute_derived_totals · validate_financial_identities
       Consts: DERIVED_OVERWRITE_MAX_DIVERGENCE = 0.05
               IDENTITY_TOLERANCE_PCT = 0.5
       NOTE:   NO MODULE DOCSTRING — the largest undocumented file here

File:  backend/app/ingestion/entity_resolver.py (336 lines) — METRIC half
       normalize_metric_label(raw) · resolve_metric(raw)
       20+ regexes with a LOAD-BEARING ordering constraint
       NOTE:   NO MODULE DOCSTRING
```

---

## 6. Deep walkthrough

### 6.1 `_should_skip_row`

```python
_SKIP_DESCRIPTIONS = {...}
_OCR_DUPLICATE_METRICS = {...}

def _should_skip_row(description: str, values: list) -> bool: ...
```

Not every extracted row is a metric. Section headings, "Notes", blank separators,
and OCR duplicates all appear as rows with plausible shape.

`_OCR_DUPLICATE_METRICS` is the interesting set: OCR sometimes emits the same line
twice with slightly different spellings, and storing both would create two
`is_latest` rows for one business key — which the partial unique index (Day 15)
would reject, aborting the batch.

**A parsing artefact defended at the semantic layer, because the storage layer
would fail loudly and unhelpfully.**

---

### 6.2 `normalize_metric_label`, and the ordering

**STATE BEFORE.** A raw description string, possibly OCR-damaged:
`"(i) Revenue from operations (unaudited) (Rs. in crores)"`.

The pipeline, in order:

1. `SPLIT_INITIAL_RE` — rejoin a split initial. **First, and load-bearing.**
2. `PREFIX_RE` — strip `(i) `, `1. `, `a) `.
3. `META_RE` — strip `(unaudited)`, `(consolidated)`, `(Rs. in crores)`.
4. `UNITS_OUTSIDE_PARENS_RE` — strip bare unit phrases.
5. `FOOTNOTE_RE` — strip trailing `(1)`.
6. Punctuation, whitespace, slash and hyphen normalisation.
7. `OCR_FIXES` — a substitution map.

**STATE AFTER.** `"revenue from operations"` → `resolve_metric` →
`"revenue"`.

**Why the order is a real risk.** It is documented in one comment, on one regex,
with no test asserting it. Reorder these lines and metrics get renamed **silently**
— and the resulting rows are stored as-is (F6), so they simply become unqueryable
rather than erroring.

---

### 6.3 `resolve_metric` and the coverage floor

```python
def resolve_metric(raw: str) -> str: ...
```

Normalises, then looks up `all_alias_pairs()`. On a miss, **stores as-is**.

`CLAUDE.md` §3 lists an **alias coverage floor (0.5)** among the frozen measured
constants — a guard that fires when fewer than half of a document's extracted
labels resolve to registry metrics, indicating the extraction or the registry has
gone wrong for that document rather than for one row.

And `CLAUDE.md` §8 records how a coverage-floor fix was nearly reverted:

> **Measure before reverting, not just before shipping.** A coverage-floor fix was
> one command from being reverted as a regression; the ninety-second measurement
> showed the shift was an improvement (**divergences 2212 Cr → 11 Cr**).

**A change that looked like a regression was an improvement**, and the only thing
that separated them was a measurement someone almost skipped.

---

### 6.4 `_compute_derived_totals`, state by state

**STATE BEFORE.** `FinancialRecord`s from one document, some totals missing or
misread.

**Execute.** For each derivable total, compute from components; compare with the
printed value if present; overwrite only if within 5%.

**STATE AFTER.** Some totals filled in; some printed values preserved because the
divergence was too large to be rounding.

**What breaks if you remove the tolerance check.** The ₹10,000 Cr incident. A
corrupted component propagates into every total derived from it, producing an
internally self-consistent column that passes every arithmetic check.

**What breaks if you set the tolerance too tight.** Legitimate rounding
differences — filings round to the nearest crore — would block valid derivations,
leaving totals missing.

---

### 6.5 `validate_financial_identities`

**STATE BEFORE.** Records, post-derivation.

**Execute.** Check each identity; report divergences above 0.5%.

**STATE AFTER.** A list of divergence dicts. **Reported, not corrected.**

**And this is where the ₹10,000 Cr error was visible for weeks:**

> The system had logged the disagreement on every run for weeks, in a list
> **scanned by count rather than by magnitude.**

**The check worked.** The output was read wrongly. A list of 30 divergences looks
the same whether they are 11 Cr or 2,212 Cr — and nobody sorted by magnitude.

**The lesson generalises:** a diagnostic that reports a *list* invites counting.
If magnitude is what matters, the diagnostic must surface magnitude.

---

## 7. Data flow

```
ONE DEFINITION
ALL_METRICS: tuple[MetricDefinition, ...]      frozen, duplicate-checked at import
        │
   ┌────┼──────────────┬──────────────┬───────────────┬──────────────┐
   ▼    ▼              ▼              ▼               ▼              ▼
all_alias   dsl_registry   prompt_metric   derived_    unqueryable_   metric_
_pairs()    dsl_alias      _lines()        metric_     metric_        anchor_
   │        _pairs()       prompt_         aliases()   aliases()      phrases()
   │            │          warnings()          │            │             │
   ▼            ▼              ▼               ▼            ▼             ▼
entity_     dsl_compiler   DSL_SYSTEM_     Stage 0      Stage 0b      Stage 0c
resolver    validation     PROMPT          guard        guard         guard
(ingest)    (Day 32)       (Day 18)        (Day 34)     (Day 34)      (Day 37)


INGEST PATH
FINANCIAL_STATEMENT blocks                              (Day 23)
        ▼ detect_column_layout                          (Day 22)
   (description, [values])
        ▼ _should_skip_row
        ▼ normalize_metric_label     SPLIT_INITIAL_RE FIRST — load-bearing
        ▼ resolve_metric → all_alias_pairs()
        │    ├─ hit  → canonical name
        │    └─ miss → STORED AS-IS        ← audit F6
        ▼ _rows_to_records
   FinancialRecord(unit='crore_inr' ASSERTED)   ← audit F3
        ▼ _compute_derived_totals      overwrite only within 5%
        ▼ validate_financial_identities  report above 0.5%   ← REPORT, NOT FIX
        ▼ db_loader.load_financial_records                    (Day 15)
   financials rows
        ▼
   SELECT ... WHERE metric = %s                            (Day 33)
```

---

## 8. Engineering decision — one registry, two independent formulas

**Problem.** A metric's identity must be consistent across ingestion, query
validation, prompt construction and three guards — while an accounting check must
remain independent of the arithmetic it checks.

**Decision.** One frozen registry with per-consumer projections; **two deliberate
formula implementations** with different tolerances.

`ENGINEERING_DECISIONS.md` **ED-013**.

| Alternative | Why not |
|---|---|
| **Keep three dicts, sync carefully** | Tried. Three shipped bugs |
| **A database table of metrics** | Metrics are schema, versioned with the code; a table would drift between environments (Day 16's two-database problem) |
| **A `CHECK` constraint on `financials.metric`** | A migration per metric, and it would reject unmapped OCR line items — discarding real figures |
| **One shared formula for compute and validate** | **A check sharing an implementation with the thing it checks validates nothing** |
| **Reject rows whose label does not resolve** | Discards real figures for a naming failure. F6 is the cost of not doing this |

**Trade-offs accepted.**

- **F6:** 686 of 1,437 rows carry unanchored metric names, unqueryable and
  rendered as raw keys.
- **F3:** `unit` asserted, not detected — and untestable with this corpus.
- **Two formula copies** must be updated together, enforced only by a note in
  `CLAUDE.md`.
- **The regex ordering constraint** is stated in one comment with no test.

**Current validity.** The consolidation is unambiguously right. The three open
items are all *recorded*.

**At 10×** — in issuers, not documents. More layouts means more unmapped labels
(F6 grows) and more chance of a non-crore filing (F3 becomes live). Both are
diversity exposures, not volume ones.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| A metric answers with a different metric's figure | The substitution class — Stage 0/0b guards exist for it |
| `nterest_expense`, `oan_given` | Regex ordering violated |
| A total wrong, column internally consistent | Derivation propagated a misread component |
| Identity divergences logged and ignored | Scanned by count, not magnitude |
| A figure stored but unqueryable | **F6** — unanchored metric name |
| A figure wrong by 10× | **F3** — unit asserted |
| A metric rendered "Pat" | Historic: three label conventions in one function |
| Duplicate `is_latest` rows on ingest | An OCR duplicate not in `_OCR_DUPLICATE_METRICS` |
| `ValueError: Duplicate canonical_name(s)` | Two registry entries share a name — **at import** |

---

## 10. Hands-on experiment

### Experiment 1 — the registry and its six views

```bash
docker compose exec -T backend python -c "
from app.metrics import registry as r
print('ALL_METRICS            :', len(r.ALL_METRICS))
print('  raw / derived        :',
      sum(1 for m in r.ALL_METRICS if m.metric_type=='raw'), '/',
      sum(1 for m in r.ALL_METRICS if m.metric_type=='derived'))
print('  dsl_enabled True/False:',
      sum(1 for m in r.ALL_METRICS if m.dsl_enabled), '/',
      sum(1 for m in r.ALL_METRICS if not m.dsl_enabled))
print()
for name, fn in [('all_alias_pairs', r.all_alias_pairs),
                 ('dsl_registry', r.dsl_registry),
                 ('dsl_alias_pairs', r.dsl_alias_pairs),
                 ('derived_metric_aliases', r.derived_metric_aliases),
                 ('unqueryable_metric_aliases', r.unqueryable_metric_aliases),
                 ('metric_anchor_phrases', r.metric_anchor_phrases)]:
    print(f'  {name:28} {len(fn())}')
print()
print('Six projections. One definition. Nobody filters ALL_METRICS themselves.')
"
```

### Experiment 2 — the two booleans

```bash
docker compose exec -T backend python -c "
from app.metrics.registry import ALL_METRICS
buckets = {}
for m in ALL_METRICS:
    buckets.setdefault((m.metric_type, m.dsl_enabled), []).append(m.canonical_name)
for (mt, de), names in sorted(buckets.items()):
    print(f'  metric_type={mt:8} dsl_enabled={de!s:5}  n={len(names):2}  {names[:4]}')
print()
print('metric_type answers: is it printed, or computed from others?')
print('dsl_enabled answers: may a user query it?')
print('They are INDEPENDENT, and the two guards exist because of that.')
"
```

### Experiment 3 — the guards, and why they are narrow

```bash
docker compose exec -T backend python -c "
from app.metrics.registry import derived_metric_aliases, unqueryable_metric_aliases, UNQUERYABLE_MIN_WORDS
d = derived_metric_aliases(); u = unqueryable_metric_aliases()
print('derived aliases (Stage 0):', len(d))
for k, v in list(d.items())[:6]: print(f'    {k!r:34} -> {v}')
print()
print(f'unqueryable phrases (Stage 0b), min_words={UNQUERYABLE_MIN_WORDS}:', len(u))
for k, v in list(u.items())[:6]: print(f'    {k!r:52} -> {v}')
print()
loose = unqueryable_metric_aliases(min_words=1)
print(f'with min_words=1: {len(loose)} phrases — including:')
short = [k for k in loose if len(k.split()) == 1][:10]
print('   ', short)
print()
print('THAT is why the 4-word floor exists. Those would fire on almost any query.')
"
```

### Experiment 4 — the regex ordering, made visible

```bash
docker compose exec -T backend python -c "
from app.ingestion.entity_resolver import normalize_metric_label, resolve_metric
cases = ['(i) Revenue from operations',
         'I nterest expense',
         'L oan given',
         'Total incomc (unaudited)',
         'EmpIoyee benefi1s expense',
         'Impairment of loans/investment in associates',
         'Revenue from operations (Rs. in crores) (1)']
for raw in cases:
    n = normalize_metric_label(raw)
    print(f'  {raw!r:48}')
    print(f'      normalised -> {n!r}')
    print(f'      resolved   -> {resolve_metric(raw)!r}')
"
```

### Experiment 5 — derived totals and the overwrite guard

```bash
docker compose exec -T backend python -c "
from app.ingestion.financial_extractor import (_derivation_within_tolerance,
        DERIVED_OVERWRITE_MAX_DIVERGENCE, IDENTITY_TOLERANCE_PCT)
print('DERIVED_OVERWRITE_MAX_DIVERGENCE:', DERIVED_OVERWRITE_MAX_DIVERGENCE, '(5%)')
print('IDENTITY_TOLERANCE_PCT          :', IDENTITY_TOLERANCE_PCT, '(0.5%)')
print()
for read, computed in [(54364.0, 54364.0), (54364.0, 54900.0),
                       (54364.0, 57000.0), (17292.0, 7292.0)]:
    ok = _derivation_within_tolerance(read, computed)
    div = abs(read-computed)/abs(read)*100
    print(f'  read={read:9.1f} computed={computed:9.1f}  divergence={div:6.2f}%  overwrite={ok}')
print()
print('The last row is the OCR split: 17,292 -> 7,292. 57.8% divergence.')
print('The guard REFUSES the overwrite. Before it existed, derivation')
print('propagated the misread into total income AND total expenses.')
"
```

### Experiment 6 — run the extraction, and read the divergences BY MAGNITUDE

```bash
docker compose exec -T backend python -c "
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks
from app.ingestion.financial_extractor import validate_financial_identities
import inspect
print(inspect.signature(validate_financial_identities))
print()
print('Run extraction once, then sort divergences by MAGNITUDE, not count.')
print('A list of 30 looks the same whether they are 11 Cr or 2,212 Cr —')
print('and that is exactly how the 10,000 Cr error survived for weeks.')
"
```

Then the real gate, **once**, teed:

```bash
docker compose exec -T -w /app backend env PYTHONPATH=/app python -m scripts.regression_check 2>&1 | tee /tmp/rc.txt | tail -30
grep -i "divergen\|identity" /tmp/rc.txt | head -20
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/metrics/registry.py` and
`backend/app/ingestion/financial_extractor.py`:

1. Name the three bugs the registry split caused, and say what kind of failure
   each was.
2. `metric_type` and `dsl_enabled` are independent. Give a metric for each of the
   three populated combinations, and say which guard covers which.
3. `unqueryable_metric_aliases` includes canonical names as matchable phrases. The
   comment calls this "load-bearing, not tidiness". Why?
4. `_compute_derived_totals` and `validate_financial_identities` implement the
   same formulas. Why was this duplication **not** removed?
5. `DERIVED_OVERWRITE_MAX_DIVERGENCE` is 5% and `IDENTITY_TOLERANCE_PCT` is 0.5%.
   Why is the guard looser than the check?

---

## 12. Self-check questions

**Basic**
1. Why does one registry exist?
2. What is the difference between `metric_type` and `dsl_enabled`?
3. What does `all_alias_pairs()` serve?
4. What happens to a label that resolves to nothing?
5. What are the two tolerance constants?

**Code**
6. What check runs at registry import, and why there?
7. What does `display_label` fall back to, and why is a fallback needed?
8. Which regex must run first in `normalize_metric_label`, and what breaks
   otherwise?
9. What does `_should_skip_row` prevent?
10. What does `validate_financial_identities` do with a divergence?

**Why**
11. Why is corpus availability *not* in the registry?
12. Why is `derived_metric_aliases` restricted to derived metrics?
13. Why is `UNQUERYABLE_MIN_WORDS = 4`?
14. Why are there two formula implementations?
15. Why store an unresolvable metric name as-is?

**Debugging**
16. A query for EBITDA returns a `sql_verified` figure for total expenses. Which
    guard, and what is the underlying cause?
17. A stored total is wrong but every arithmetic check passes. What happened?
18. A metric renders as a raw key in the UI. Which finding?

**System design**
19. Close F3 (unit detection). Sketch the change and name what makes it
    untestable today.
20. The regex ordering constraint is stated in a comment with no test. Write the
    test, and say where it belongs.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **(a)** `profit_before_tax` absent from `dsl_compiler`'s registry — *the model
   had no correct option, so it substituted `pat`*. **(b)** `exceptional_items`
   collapsing three distinct line items into one canonical name in
   `entity_resolver` — *three facts became one, and a genuinely blank cell was
   backfilled by an unrelated row's value*. **(c)** Titan's segment revenue with no
   canonical home anywhere — *a real figure had nowhere to go and fell through
   unmapped*. Three shapes: a missing option, an over-collapse, and an omission.
2. **`raw` + `dsl_enabled=True`:** `revenue` — a printed line item a user may
   query. **`derived` + `dsl_enabled=True`:** `ebitda` — computable in principle,
   **no SQL formula compiler exists**, so Stage 0's `derived_metric_aliases` guard
   refuses it. **`raw` + `dsl_enabled=False`:** OCI sub-lines — printed, but
   present only to give ingestion a dedup target; Stage 0b's
   `unqueryable_metric_aliases` guard covers these.
3. Because **every stored alias for the impairment metric uses a slash**
   ("loans/investment") while the query and the canonical name use "and". Matching
   on aliases alone **would not have matched the very query that exposed the
   bug** — PQ012's "impairment of loans and investments in associates". Including
   the canonical name with underscores expanded is what makes the guard fire on
   the phrasing users actually use.
4. Because **a check that shares an implementation with the thing it checks
   validates nothing.** If `validate_financial_identities` called
   `_compute_derived_totals`, an error in the derivation would be invisible — the
   validator would confirm the derivation agrees with itself. They answer
   different questions (*what should this be?* versus *do the stored figures
   agree?*) with different tolerances, and the independence is the point.
   `CLAUDE.md` §6 records the fragility this creates: both copies must be updated
   together.
5. Because they do different jobs. The **overwrite guard** must tolerate genuine
   rounding — filings round to the nearest crore, so a derived total can
   legitimately differ by a small amount, and a tight guard would block valid
   derivations and leave totals missing. The **identity check** is a *report*, not
   a write, so it can afford to be strict and surface small disagreements. And the
   asymmetry is deliberate: a derivation that overwrites at 3% will then **fail the
   0.5% identity check** — the write is allowed and the disagreement is reported
   rather than suppressed.

### §12 — Basic

1. Because three hand-maintained copies of one fact caused three shipped bugs. The
   fix was to make the other copies **derived** rather than maintained.
2. `metric_type` — is it a printed line item (`raw`) or computed from others
   (`derived`)? `dsl_enabled` — may a user query it through the DSL? Independent.
3. `entity_resolver.py`'s ingestion-time alias lookup — every alias for every
   metric, regardless of `dsl_enabled`.
4. It is **stored as-is**. That is audit **F6**: 174 such names across 686 of
   1,437 rows, unqueryable and rendered as raw keys.
5. `DERIVED_OVERWRITE_MAX_DIVERGENCE = 0.05` (5%) and
   `IDENTITY_TOLERANCE_PCT = 0.5` (0.5%).

### §12 — Code

6. A **duplicate `canonical_name` check** — building `_BY_CANONICAL` and comparing
   its length against `ALL_METRICS`, raising `ValueError` with the offending names.
   At import (Day 12) so it fails at startup rather than silently shadowing a
   definition at query time.
7. `canonical_name.replace("_", " ").title()`. A fallback is needed because
   `financials.metric` is **not guaranteed to be a registry key** — unmapped OCR
   line items are stored as-is (F6).
8. `SPLIT_INITIAL_RE`, which rejoins a first letter typeset as its own text run
   ("I nterest expense"). If `PREFIX_RE` ran first, a bare leading `i ` or `l `
   reads as a **roman-numeral list marker** and gets stripped, producing
   `nterest_expense` / `oan_given` — silently.
9. Section headings, "Notes", blank separators, and **OCR duplicates** being
   stored as metrics. The last matters most: a duplicated line would create two
   `is_latest` rows for one business key, which the partial unique index (Day 15)
   rejects, aborting the batch.
10. **Reports** it — returns a list of divergence dicts. It does not correct
    anything.

### §12 — Why

11. Because availability is **data state**, not schema — it changes with every
    ingest, while the registry is versioned with the code. A stored flag would go
    stale silently (the `available_in_corpus` flag it replaced). A zero-row SQL
    result answers the question instead.
12. Because derived metrics have **distinctive aliases**. Scanning every registry
    alias would false-positive constantly on short aliases of non-queryable
    metrics — "others", "india", "cash", "equity" — firing on almost any query.
13. Because `dsl_enabled=False` covers exactly those short, common aliases. A
    4-word floor makes a match specific enough to be intentional, and it **fails
    toward not firing** — so behaviour is unchanged wherever the phrase is not
    unmistakable.
14. See §11 Q4.
15. Because the alternative is **discarding a real figure** because we could not
    name it. The cost is F6: the row is unqueryable and renders as a raw key —
    which is worse than a clean mapping and better than losing the number.

### §12 — Debugging

16. **Stage 0** (`derived_metric_aliases`). Underlying cause: `GeminiDSLResponse.metric`
    is a **required** field, so a model asked for a metric with no compiler cannot
    return "none" — it substitutes the nearest available one, which is perfectly
    valid DSL, compiles, executes and is stamped `sql_verified=True`. The
    validator cannot catch it; only a regex over the **raw query**, before any LLM
    call, can (Day 34).
17. **Derivation propagated a misread component.** OCR corrupted `revenue`, and
    `_compute_derived_totals` recomputed `total_income` and `total_expenses` *from*
    the corrupted value — so the column became **internally self-consistent**,
    which is why every arithmetic check passed and why it survived review. The
    identity divergences *were* logged, every run, for weeks — in a list scanned by
    **count** rather than by **magnitude**.
18. **Audit F6.** The stored metric name has no registry anchor, so `display_label`
    falls back to `canonical_name.replace("_", " ").title()`.

### §12 — System design

19. **The change:** parse the unit declaration financial statements print in their
    header — "(₹ in crore)", "(Rs. in millions)", "(₹ in lakhs)" — during
    `section_classifier` (it already scans block content and already owns the
    three-signal intersection), attach it to the `DocSection`, thread it through
    `_rows_to_records` into `FinancialRecord.unit` instead of the hardcoded
    default, and add a magnitude sanity check as a second signal. **What makes it
    untestable today:** every document in the corpus reports in crore, so there is
    **no negative case** — a detector would pass trivially and you would learn
    nothing about whether it works (Day 13). It also interacts with F3's second
    half: `clean_financial_number` is itself calibrated to crore-scale magnitudes,
    so detection alone is insufficient. Testing requires ingesting a filing in a
    different unit, which is why F3 is the named blocker for arbitrary documents
    rather than a bug with a quick fix.
20. **The test:**
    ```python
    def test_split_initial_runs_before_prefix_stripping():
        # PDFs typeset a leading capital as its own text run; after casefolding,
        # a bare "i " or "l " is a legal roman numeral that PREFIX_RE would strip.
        assert normalize_metric_label("I nterest expense") == "interest expense"
        assert normalize_metric_label("L oan given")       == "loan given"
        assert normalize_metric_label("P ayment of principal portion") \
               == "payment of principal portion"
        # And the genuine list marker must still be stripped:
        assert normalize_metric_label("(i) Revenue from operations") \
               == "revenue from operations"
    ```
    **Where it belongs:** `backend/tests/test_entity_resolver.py`, in the
    zero-network pure-function suite — `normalize_metric_label` takes a string and
    returns a string, so it qualifies under `conftest.py`'s scope rule. **Why it
    matters more than it looks:** the constraint is currently enforced by a comment
    on one regex, and a violation renames metrics **silently** — the rows are still
    stored (as-is, per F6), so nothing errors and the figures simply become
    unqueryable. The last assertion is the important one: it pins that the fix did
    not break genuine prefix stripping, which is what makes the test a *guard*
    rather than a snapshot.

---

## 14. MUST REMEMBER

```text
- app/metrics/registry.py is THE single metric registry. Never add a second
- Three hand-maintained copies caused THREE shipped bugs
- Six derived views; nobody filters ALL_METRICS themselves
- metric_type (raw|derived) and dsl_enabled are INDEPENDENT booleans
- Duplicate canonical_name raises at IMPORT, naming the duplicates
- SPLIT_INITIAL_RE MUST run before PREFIX_RE. Comment only, no test
- An unresolvable label is STORED AS-IS → audit F6 (686 of 1437 rows)
- unit is ASSERTED as crore, never detected → audit F3
- DERIVED_OVERWRITE_MAX_DIVERGENCE = 0.05 · IDENTITY_TOLERANCE_PCT = 0.5
- BOTH formula copies must be updated together — they are independent ON PURPOSE
- UNQUERYABLE_MIN_WORDS = 4 — the guard fails toward NOT firing
```

## 15. MUST UNDERSTAND

```text
- Why the fix for three copies was DERIVATION, not discipline
- Why schema and state must not share a home, and how a stale flag hides
- Why a check that shares an implementation with what it checks validates
  nothing — and why that makes ONE duplication correct in a codebase that
  eliminated another
- Why a derivation can LAUNDER a misread into an internally consistent column
  that passes every arithmetic check
- Why a diagnostic that reports a LIST invites counting, and why magnitude had
  to be surfaced
- Why storing an unnameable figure beats discarding it, and what that costs
```

---

## 16. This connects to

```text
Day 30 — the semantic path, whole
   ↓
Day 31 — the registry, and how a number becomes a row   ← you are here
   ↓
Day 32 — the DSL: the eight fields the model may emit
```

Forward references:

- `dsl_registry()` / `dsl_alias_pairs()` in validation → **Day 32**
- `_base_select` querying `financials` → **Day 33**
- Stage 0 and Stage 0b guards firing → **Day 34**
- `metric_anchor_phrases()` and Stage 0c → **Day 37**
- `regression_check` and the coverage floor → **Day 43**
- `purge_orphaned_metrics` after any extraction change → **Day 43**
