# Day 32 — The DSL: Schema, Validation, Repair

**Phase 9 · Weight: H (~120 min) · Prerequisites: Days 18, 31**

---

## 1. Today's goal

By tonight you can:

- Explain what a domain-specific language is here, and why eight fields beat
  free-form SQL.
- Read `DSLValidator.validate` and explain every rejection it makes.
- Explain the **repair hint** and the bounded self-healing loop — and why
  `LLMUnavailable` **breaks** rather than retries.
- Explain `CAVEAT-004`: the schema cannot express *"the user named no metric"* or
  *"no period"*, and what that forces the model to do.
- Explain `CAVEAT-002`: `preferred_operation` is a "load-bearing guardrail" that
  **can never fire**.

---

## 2. Why now

Day 31 established what a metric *is*. Today is how a question becomes a request
for one. Day 33 compiles that request to SQL; Day 34 guards it.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Structured output; schema-as-prompt | Day 18 | `GeminiDSLResponse` |
| The registry's six views | Day 31 | Validation reads two of them |
| "Validated is not correct" | Day 5 | Today's central limitation |
| `LLMUnavailable`, callers must not retry | Day 19 | The `break` |

---

## 4. Concept lesson

### 4.1 What the DSL is

**Eight fields.** That is the entire vocabulary the model has for a numeric
question:

```python
class GeminiDSLResponse(BaseModel):
    metric: str
    entity: str
    fiscal_year: str
    quarter: Optional[str]
    financial_type: str
    operation: str
    comparison_entity: Optional[str]
    comparison_period: Optional[str]
```

**Why a DSL rather than SQL** (Day 17's ED-001, from the validation side):

| | Free-form SQL | An 8-field DSL |
|---|---|---|
| Is it valid? | parseable ≠ correct | **decidable** — check each field against a closed set |
| Wrong-but-valid | *"SELECT the right column for the wrong period"* is undetectable | a wrong `fiscal_year` is still a *field* you can inspect and log |
| Injection | requires sanitising generated SQL | the model never writes SQL |
| Schema exposure | the model must see the schema | it never does |
| Repairable | "your SQL is wrong" is not actionable | *"`operation='comparison'` requires `comparison_entity`"* is |

**Mental model.** SQL is **a blank sheet**. The DSL is **a form with eight
boxes**, each with a list of acceptable answers. You cannot validate a blank
sheet; you can validate a form.

---

### 4.2 The five operations

```python
OPERATION_REGISTRY: Dict[str, str] = {
    "point_in_time":     "Single period value for one entity",
    "yoy_growth":        "Year-over-year % change (requires fiscal_year in DSL)",
    "comparison":        "Two entities, same period (requires comparison_entity)",
    "cagr":              "Compound annual growth rate (requires multiple periods)",
    "growth_comparison": "Compares YoY growth rate between two entities ...",
}
```

**A closed set**, and — like the metric list — **interpolated into the prompt**
(Day 18), so the model is told exactly these five.

**`growth_comparison` is the interesting one.** It is not "comparison plus
yoy_growth" — it needs **four** SQL queries (Day 33) and answers *"who grew
faster"*, which neither of the others can. It exists because golden question Q051
asks exactly that.

---

### 4.3 Validation, rejection by rejection

```python
class DSLValidator:
    def validate(self, raw: dict, preferred_operation: Optional[str] = None) -> ValidationResult:
```

Returning:

```python
@dataclass
class ValidationResult:
    valid: bool
    dsl_object: Optional[DSLObject] = None
    error: Optional[str] = None
    repair_hint: Optional[str] = None
```

**`error` is for the log and the user; `repair_hint` is for the model.** And a
`repair_hint` of `None` means **do not retry** (§4.5).

**The checks, in order:**

**1. Required fields.**

```python
required = ["metric", "entity", "fiscal_year", "financial_type", "operation"]
missing = [f for f in required if not raw.get(f)]
```

Five of eight. `quarter`, `comparison_entity` and `comparison_period` are
legitimately optional.

**2. `financial_type` in a closed set.**

```python
VALID_FINANCIAL_TYPES = {"consolidated", "standalone"}
```

Mirrors the database `CHECK` (Day 13) — **a second copy of one fact**, in a
different language. Adding a third type means editing both, and only a migration
failure would reveal a miss.

**3. Metric resolution, through the registry.**

```python
raw_metric = raw["metric"].lower().strip()
resolved_metric = METRIC_ALIASES.get(raw_metric, raw_metric)
if resolved_metric not in METRIC_REGISTRY:
    return ValidationResult(valid=False,
        error=f"Unknown metric: '{raw_metric}'",
        repair_hint=f"Metric '{raw_metric}' not in registry. Available: {list(...)}.")
```

`METRIC_ALIASES` and `METRIC_REGISTRY` are `dsl_alias_pairs()` and
`dsl_registry()` — Day 31's projections, not local dicts.

**4. Registered but unavailable — and a `repair_hint` of `None`:**

```python
if not metric_def["available"]:
    return ValidationResult(valid=False,
        error=f"Metric '{resolved_metric}' is registered but not yet in corpus",
        repair_hint=None)          # ← non-recoverable. Do NOT retry.
```

**The model cannot fix this by trying again.** A repair hint would invite it to
substitute — the exact failure Stage 0 exists to prevent (Day 34).

**5. `point_in_time` with a distinct `comparison_period`:**

```python
if operation == "point_in_time":
    comp_period = raw.get("comparison_period")
    fy = raw.get("fiscal_year")
    if comp_period and comp_period.upper().strip() != fy.upper().strip():
        return ValidationResult(valid=False,
            error="operation='point_in_time' with distinct comparison_period spans two periods — use yoy_growth.",
            repair_hint="If comparing across two years for the same entity, use operation='yoy_growth'.")
```

**A single-value operation carrying two periods is incoherent.** Left unchecked,
`_compile_point_in_time` would ignore `comparison_period` and silently answer half
the question.

**6–8. Operation-specific requirements.** `comparison` needs
`comparison_entity`, and it must differ from `entity`. `yoy_growth` needs
`fiscal_year`. `growth_comparison` needs both, and two distinct entities.

**The same-entity check is worth naming:**

```python
if raw["entity"].upper().strip() == raw["comparison_entity"].upper().strip():
    return ValidationResult(valid=False,
        error="comparison_entity resolved to the same company as primary entity.",
        repair_hint="A comparison requires two different entities.")
```

**The eval has a category for this** — `quantitative_cross_period_refusal`, which
*"FAILS HARD if entities silently collapsed to same entity (bug #7 regression)"*.
A comparison of a company with itself returns a difference of zero, which is
arithmetically correct and answers nothing.

---

### 4.4 `CAVEAT-004` — what the schema cannot say

```python
class GeminiDSLResponse(BaseModel):
    metric: str          # REQUIRED
    fiscal_year: str     # REQUIRED
```

**Neither is `Optional`.** So a model asked about a question naming no metric —
or no period — **cannot answer "none"**. It must emit something.

From `CAVEAT-004`:

> A model constrained to emit a value cannot answer "the user named none" — it
> **invents one**, the invention validates cleanly, compiles to SQL, executes, and
> is stamped `sql_verified=True`.

**Four measured instances:**

| Query | Invented |
|---|---|
| *"What was Paytm's EBITDA for FY26?"* | `total_expenses` (₹8,523 Cr), verified |
| *"…the 207 crore impairment of loans and investments in associates…"* | `exceptional_items` (₹−186 Cr), verified |
| PQ012, *"financial exposure to Paytm Payments Bank"* — **names no metric at all** | `exceptional_items`, verified, **stable across five runs** |
| *"does management commentary align with its PAT decline?"* | `fiscal_year="FY25"` **invented from nothing** |

**Why not just make the fields optional?** The caveat answers it:

> Making the fields optional would **move the problem into the validator** rather
> than removing it, and Gemini's `response_schema` enforcement is what makes the
> structured path reliable in the first place.

An `Optional[str]` metric means the validator must handle `None` — and every
downstream consumer must too. And critically: **the model would still have to
choose** between `None` and a guess, so the failure mode does not disappear, it
relocates.

**So the mitigation is three regex guards over the raw query, before any LLM
call** (Day 34) — because *the raw query is the only place the user's real intent
still exists.*

**And note it is stable, not random.** PQ012 returned the same wrong metric five
consecutive times. **A deterministic wrong answer is harder to catch than a flaky
one**, because it survives re-runs.

---

### 4.5 The self-healing loop

```python
MAX_DSL_ATTEMPTS = 2

def _generate_dsl(query, companies, fiscal_year, quarter, financial_type):
    repair_hint = None
    attempts = 0
    last_error = None
    llm_result = None

    while attempts < MAX_DSL_ATTEMPTS:
        attempts += 1
        user_message = _build_dsl_user_message(..., repair_hint=repair_hint)
        try:
            llm = generate_structured(system=DSL_SYSTEM_PROMPT, user=user_message,
                                      schema=GeminiDSLResponse, temperature=0.0, max_tokens=200)
            llm_result = llm
            raw_dict = json.loads(llm.text)   # with a fence-strip fallback
            ...
        except LLMUnavailable as e:
            # BREAK, not continue.
            return None, attempts, f"No LLM provider was available: {e}", None
        except Exception as e:
            repair_hint = f"Previous call failed with error: {e}. Try again with valid JSON."
            continue

        validation = validate_dsl(raw_dict)
        if validation.valid:
            return validation.dsl_object, attempts, None, llm_result

        last_error = validation.error
        if validation.repair_hint is None:
            return None, attempts, validation.error, llm_result   # non-recoverable
        repair_hint = validation.repair_hint
```

**The repair hint is fed back into the *user* message:**

```python
if repair_hint:
    context += f"\nPREVIOUS ATTEMPT FAILED. Fix this issue:\n{repair_hint}\n"
```

**Why the user message and not the system prompt.** The system prompt is a
module-level constant (Day 18) — constant across calls, so it can be reasoned
about across an eval sweep. A per-call repair belongs in the per-call message.

**And the `break`, which is the day's sharpest comment:**

```python
except LLMUnavailable as e:
    # BREAK, not continue. The self-healing loop exists to repair BAD
    # DSL; "no provider answered" is not a DSL defect, and a repair
    # hint cannot fix it. Retrying here burns the single remaining
    # attempt on a call that will fail identically -- the same
    # conflation as the CRAG break/continue bug, inverted.
    return None, attempts, f"No LLM provider was available: {e}", None
```

**"The same conflation as the CRAG break/continue bug, inverted."**

| | CRAG (Day 29) | DSL loop |
|---|---|---|
| Bug | `break` where `continue` was right | `continue` would be wrong where `break` is right |
| Confused | "this rung is a no-op" with "the ladder is exhausted" | "the DSL is bad" with "no provider answered" |
| Cost | annual queries lost recovery | a wasted attempt on a call that fails identically |

**Same class: a control-flow decision that treats two different conditions as
one.** `LLMUnavailable` already means both providers failed (Day 19) — a third
attempt cannot help.

**Note also that `MAX_DSL_ATTEMPTS = 2` is two attempts, not two retries.** One
initial call plus one repair.

---

### 4.6 The entity override, and F14's `len(...) == 1`

```python
is_comparison = raw_dict.get("operation") == "comparison"

# F14 DECISION. Override ONLY when exactly one issuer was named.
# Pre-F14 this fired on any truthy `company`, and a two-issuer query
# nulled it, so it did not fire -- len(...) == 1 preserves that exactly
# while making the reason explicit instead of incidental. Handing one of
# two named issuers to `entity` would silently drop the other, which is
# the F14 defect itself.
if len(companies) == 1 and not is_comparison:
    raw_dict["entity"] = companies[0]
if fiscal_year and not raw_dict.get("fiscal_year"):
    raw_dict["fiscal_year"] = fiscal_year
if quarter is not None and not raw_dict.get("quarter"):
    raw_dict["quarter"] = quarter
raw_dict.setdefault("financial_type", financial_type)
```

**Router-extracted values override the model's**, on the grounds that the router
already did entity extraction with a dedicated prompt.

**Three different override strengths, and the differences are deliberate:**

| Field | Rule | Why |
|---|---|---|
| `entity` | override, but only if exactly one issuer **and not a comparison** | Handing one of two issuers to `entity` **drops the other** |
| `fiscal_year` | fill only if the model left it **empty** | The model may have read a year from the query text the router missed |
| `quarter` | same | Same |
| `financial_type` | `setdefault` | Weakest — a default, not an override |

**And `_build_dsl_user_message` mirrors it:**

```python
f"  entity: {companies[0] if len(companies) == 1 else 'unknown'}\n"
```

> ZERO OR SEVERAL both render 'unknown', which is byte-identical to what
> `company or 'unknown'` produced pre-F14 … **Q051 therefore sees the SAME DSL
> prompt it saw when it was measured passing**, and the model keeps producing
> `entity`/`comparison_entity` itself rather than being handed one issuer of the
> two as if it were the only one.

**A change designed to be byte-identical for the case that was already measured.**
That is how you make a refactor safe when you cannot afford to re-measure
everything.

---

### 4.7 `CAVEAT-002` — a guardrail that cannot fire

```python
# ── ⚡ PROGRAMMATIC OPERATION OVERRIDE (Load-Bearing Guardrail) ──
if preferred_operation and preferred_operation in OPERATION_REGISTRY:
    current_op = raw.get("operation", "").lower().strip()
    if current_op != preferred_operation:
        logger.info("⚡ DSL Validator: Overriding LLM chosen operation '%s' -> forcing preferred '%s'", ...)
        raw["operation"] = preferred_operation
```

**There is exactly one call site of `validate_dsl`:**

```python
validation = validate_dsl(raw_dict)          # no second argument
```

So `preferred_operation` is always `None`, and the block **never executes**.

Meanwhile `router_node` *does* write `state["preferred_operation"]` from the UI's
`execution_context.intended_operation` (Day 5's `CAVEAT-002`), and nothing reads
it.

**And the consequence, from the caveat:**

> The peer-comparison view still usually produces `growth_comparison`, but only
> because `DSL_SYSTEM_PROMPT` contains an explicit rule for "who grew revenue
> faster" — i.e. the **deterministic guardrail is absent and the probabilistic one
> is doing the work.** That is the inverse of this project's stated preference.

**A comment asserting "Load-Bearing" on dead code is worse than no comment.** It
tells a reader the mechanism is protected when it is not. `CAVEAT-002`'s
recommendation:

> pass `state.get("preferred_operation")` through `_generate_dsl` into
> `validate_dsl` — **or** delete the parameter and the UI field. Either is
> defensible; **having a guardrail that looks wired and is not is the worst of the
> three.**

---

## 5. The actual LedgerMind files

```
File:  backend/app/engines/dsl_compiler.py (303 lines)
       DOCSTRING IS A STUB — "LedgerMind — Phase 4: DSL Compiler" and nothing else,
       for a file holding the entire validation contract
       DSLValidator.validate(raw, preferred_operation=None) -> ValidationResult
       validate_dsl / compile_dsl / resolve_metric_alias  (module-level wrappers)
       METRIC_REGISTRY = dsl_registry()      ← Day 31
       METRIC_ALIASES  = dsl_alias_pairs()   ← Day 31
       OPERATION_REGISTRY, VALID_FINANCIAL_TYPES

File:  backend/app/engines/quant_engine.py — today, lines 55-300
       GeminiDSLResponse (Pydantic, sent to the model)
       _build_dsl_system_prompt() / _build_dsl_user_message()
       _generate_dsl(...) -> (dsl, attempts, error, llm_result)
       MAX_DSL_ATTEMPTS = 2
```

---

## 6. Deep walkthrough — `_generate_dsl`

**STATE BEFORE.** A raw query plus router-extracted entities.

**Attempt 1.**

```python
user_message = _build_dsl_user_message(query=query, companies=companies, ...)
```

```
Query: What was Eternal's revenue in FY26?
Already extracted from query:
  entity: ETERNAL
  fiscal_year: FY26
  quarter: null (annual)
  financial_type: consolidated
```

**Both the question and the router's extraction.** The model is not asked to
re-extract; it is asked to *classify the operation and name the metric* with the
entities supplied.

```python
llm = generate_structured(system=DSL_SYSTEM_PROMPT, user=user_message,
                          schema=GeminiDSLResponse, temperature=0.0, max_tokens=200)
```

Day 19's client: timeout, one retry, Groq fallback. Day 18's schema, **which is
itself prompt input**.

```python
try:
    raw_dict = json.loads(llm.text)
except json.JSONDecodeError:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", llm.text, flags=re.DOTALL).strip()
    raw_dict = json.loads(cleaned)
```

Fence stripping, despite the prompt forbidding fences (Day 18). Instruct **and**
handle.

**Override, then validate.**

**On success:** `return validation.dsl_object, attempts, None, llm_result`.

**On a recoverable failure:** `repair_hint` is set and the loop runs again — with
the hint appended to the user message.

**Attempt 2**, and then:

```python
return (None, attempts,
    f"Could not generate a valid DSL after {MAX_DSL_ATTEMPTS} attempts. "
    f"Last error: {last_error}" if last_error else
    f"Could not generate a valid DSL after {MAX_DSL_ATTEMPTS} attempts.",
    llm_result)
```

**`last_error` is captured on every iteration** — with a comment marking it as a
fix (`# NEW — capture it every iteration`), because previously the final message
lost the reason.

**STATE AFTER.** Either a validated `DSLObject`, or `None` plus an error the
caller turns into a user-facing message (Day 34).

**And the fourth return value.** `llm_result` is the whole `LLMResult`, not just
the provider string:

> The whole `LLMResult` is carried out, not just `.provider`, so the caller can
> record provider AND model in one attributed write.

Day 19's attribution, threaded through a function whose primary job is something
else.

---

## 7. Data flow

```
"What was Eternal's revenue in FY26?"
        │
        ▼ router_node                                        (Day 36)
   companies=["ETERNAL"] fiscal_year="FY26" quarter=None
   financial_type="consolidated" path="quantitative"
        │
        ▼ quant_engine_node → Stage 0 / 0b guards            (Day 34)
        │
        ▼ _generate_dsl
        │
        ├─ attempt 1 ─────────────────────────────────────┐
        │    _build_dsl_user_message(repair_hint=None)     │
        │    generate_structured(DSL_SYSTEM_PROMPT,        │
        │                        GeminiDSLResponse)  ──────┤ schema IS prompt input
        │      ├─ LLMUnavailable → BREAK, return           │        (Day 18)
        │      └─ other error → repair_hint, continue      │
        │    json.loads (+ fence-strip fallback)           │
        │    override: entity if len(companies)==1         │
        │              fiscal_year / quarter if empty      │
        │              financial_type setdefault           │
        │    validate_dsl(raw_dict)                        │
        │      ├─ valid → RETURN DSLObject                 │
        │      ├─ repair_hint is None → RETURN (no retry)  │
        │      └─ repair_hint set → loop                   │
        └──────────────────────────────────────────────────┘
        │
        ▼ attempt 2 (with "PREVIOUS ATTEMPT FAILED. Fix this issue: ...")
        │
        ▼ exhausted → (None, attempts, "Last error: ...", llm_result)
        │
        ▼ record_llm_call(state, llm_result)         provider AND model
        ▼ compile_dsl(dsl, tenant_id)                          (Day 33)
```

---

## 8. Engineering decision — a narrow DSL with bounded repair

**Problem.** Turn ambiguous natural language into a request that is *decidably*
valid, without letting a model write SQL.

**Decision.** Eight fields, five operations, a closed metric registry, and a
two-attempt repair loop with structured hints.

`ENGINEERING_DECISIONS.md` **ED-001**.

| Alternative | Why not |
|---|---|
| **Text-to-SQL** | The model needs the schema; generated SQL can be valid and semantically wrong, and "answers the right question" is not decidable |
| **Optional fields for "none"** | Moves the problem into the validator and weakens the schema guarantee; the model still has to choose |
| **Unbounded repair** | Each attempt is an LLM call against 500/day, and a model that fails twice usually fails identically |
| **No repair — refuse on first invalid** | A missing `comparison_entity` is genuinely repairable with one hint |
| **Function/tool calling** | Broadly equivalent, provider-specific, and lets the model choose arguments more freely |

**Trade-offs accepted.**

- **`CAVEAT-004`:** required fields mean the model must invent when the user named
  nothing. Mitigated by three raw-query guards (Day 34), not by the schema.
- **Coverage:** only questions expressible in eight fields and five operations
  reach SQL. Everything else refuses.
- **`VALID_FINANCIAL_TYPES`** duplicates the database `CHECK`.
- **`CAVEAT-002`:** the operation override is dead, and comments claim otherwise.
- **`max_tokens=200`** — a longer DSL would truncate, fail validation, and be
  treated as a provider failure. **Fails closed.**

**Current validity.** The design is sound; the two caveats are recorded and open.

**At 10×** — in *question variety*, not volume. Each new operation is a new
compiler branch (Day 33) and a new prompt rule; the closed set is what keeps
validation decidable, and widening it is the real cost.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| A verified figure for a metric nobody asked about | `CAVEAT-004` — required `metric`. Guards on Day 34 |
| An invented `fiscal_year` | Same, for the period. The period-assumption guard covers it |
| `dsl_generation_failed` after 2 attempts | Genuinely unparseable, or a repair hint the model cannot satisfy |
| A comparison of a company with itself | The same-entity check — the eval fails this hard |
| `point_in_time` silently answering half a question | The `comparison_period` check |
| `preferred_operation` ignored | `CAVEAT-002` — never passed to `validate_dsl` |
| Truncated JSON | `max_tokens=200` exceeded |
| An attempt wasted on an outage | Would be the missing `break` |
| Two issuers, one dropped | Would be the F14 defect — prevented by `len(...) == 1` |

---

## 10. Hands-on experiment

### Experiment 1 — the schema the model receives

```bash
docker compose exec -T backend python -c "
from app.engines.quant_engine import GeminiDSLResponse
import json
s = GeminiDSLResponse.model_json_schema()
print(json.dumps(s, indent=2))
print()
req = s.get('required', [])
print('REQUIRED:', req)
print()
print('metric and fiscal_year are REQUIRED. The model CANNOT say \"none\".')
print('That is CAVEAT-004, visible in the schema itself.')
"
```

### Experiment 2 — drive the validator directly

```bash
docker compose exec -T backend python -c "
from app.engines.dsl_compiler import validate_dsl
cases = [
 ('valid point_in_time', dict(metric='revenue', entity='ETERNAL', fiscal_year='FY26',
                              financial_type='consolidated', operation='point_in_time')),
 ('missing metric',      dict(entity='ETERNAL', fiscal_year='FY26',
                              financial_type='consolidated', operation='point_in_time')),
 ('bad financial_type',  dict(metric='revenue', entity='ETERNAL', fiscal_year='FY26',
                              financial_type='Consolidated ', operation='point_in_time')),
 ('unknown metric',      dict(metric='ebbitda', entity='ETERNAL', fiscal_year='FY26',
                              financial_type='consolidated', operation='point_in_time')),
 ('comparison, no 2nd',  dict(metric='revenue', entity='ETERNAL', fiscal_year='FY26',
                              financial_type='consolidated', operation='comparison')),
 ('comparison w/ SELF',  dict(metric='revenue', entity='ETERNAL', comparison_entity='eternal',
                              fiscal_year='FY26', financial_type='consolidated',
                              operation='comparison')),
 ('point_in_time + comp_period', dict(metric='revenue', entity='ETERNAL', fiscal_year='FY26',
                              comparison_period='FY25', financial_type='consolidated',
                              operation='point_in_time')),
 ('bogus operation',     dict(metric='revenue', entity='ETERNAL', fiscal_year='FY26',
                              financial_type='consolidated', operation='forecast')),
]
for label, raw in cases:
    r = validate_dsl(raw)
    print(f'  {label:30} valid={r.valid!s:5}')
    if not r.valid:
        print(f'      error : {r.error}')
        print(f'      hint  : {(r.repair_hint or \"<NONE — do not retry>\")[:88]}')
"
```

**Note which case returns `repair_hint=None`.** That is the non-recoverable
signal.

### Experiment 3 — alias resolution through the registry

```bash
docker compose exec -T backend python -c "
from app.engines.dsl_compiler import resolve_metric_alias, METRIC_REGISTRY, METRIC_ALIASES
print('DSL-queryable metrics:', len(METRIC_REGISTRY))
print('DSL aliases          :', len(METRIC_ALIASES))
print()
for raw in ['revenue', 'top line', 'turnover', 'net profit', 'profit after tax',
            'other income', 'total income', 'ebitda', 'nonsense']:
    print(f'  {raw!r:20} -> {resolve_metric_alias(raw)!r}')
print()
print('These come from dsl_alias_pairs() / dsl_registry() — Day 31 projections,')
print('never a local dict.')
"
```

### Experiment 4 — `CAVEAT-004`, live

```bash
docker compose exec -T backend python -c "
from app.engines.quant_engine import _generate_dsl
dsl, attempts, err, llm = _generate_dsl(
    query='What is Paytm financial exposure to Paytm Payments Bank?',
    companies=['PAYTM'], fiscal_year='FY26', quarter=None,
    financial_type='consolidated')
print('attempts:', attempts, '| error:', err)
print('dsl     :', dsl)
print('provider:', getattr(llm, 'provider', None), getattr(llm, 'model', None))
print()
print('The question names NO METRIC. The schema requires one, so the model')
print('must invent. PQ012 returned exceptional_items five times running —')
print('STABLE, not random, which is harder to catch than a flaky wrong answer.')
"
```

> **Quota:** one or two LLM calls. Note that Stage 0c would block this on the
> **cross** path (Day 37); here we are calling `_generate_dsl` directly.

### Experiment 5 — the repair loop

```bash
docker compose exec -T backend python -c "
from app.engines.quant_engine import _build_dsl_user_message
print('--- attempt 1 ---')
print(_build_dsl_user_message(query='Compare Eternal and Paytm revenue in FY26',
      companies=['ETERNAL','PAYTM'], fiscal_year='FY26', quarter=None,
      financial_type='consolidated'))
print('--- attempt 2, with a repair hint ---')
print(_build_dsl_user_message(query='Compare Eternal and Paytm revenue in FY26',
      companies=['ETERNAL','PAYTM'], fiscal_year='FY26', quarter=None,
      financial_type='consolidated',
      repair_hint=\"Provide 'comparison_entity' with the second company's ticker.\"))
print()
print('Two issuers render entity: unknown — byte-identical to pre-F14, so Q051')
print('sees the same prompt it was measured passing with.')
"
```

### Experiment 6 — `CAVEAT-002`, proven

```bash
docker compose exec -T backend sh -c "grep -rn 'validate_dsl(' /app/app/ | grep -v 'def validate_dsl'"
echo "--- and where preferred_operation is WRITTEN ---"
docker compose exec -T backend sh -c "grep -rn 'preferred_operation' /app/app/"
```

**One call site, one argument.** The override cannot fire. Now read the comment
above it and note it says "Load-Bearing Guardrail".

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/dsl_compiler.py` (lines 55–190) and
`backend/app/engines/quant_engine.py` (lines 55–300):

1. `ValidationResult` has both `error` and `repair_hint`. Who reads each, and what
   does `repair_hint=None` signal?
2. `GeminiDSLResponse.metric` is required. Name the caveat, and say why making it
   optional would not fix it.
3. `_generate_dsl` **breaks** on `LLMUnavailable`. Find the comment and explain
   which earlier bug it references.
4. The entity override is `if len(companies) == 1 and not is_comparison`. Why both
   conditions?
5. Find the "Load-Bearing Guardrail" comment. Grep for `validate_dsl(`. What do
   you conclude, and which of the three options in `CAVEAT-002` would you take?

---

## 12. Self-check questions

**Basic**
1. How many DSL fields, and which are required?
2. Name the five operations.
3. What is a repair hint?
4. What is `MAX_DSL_ATTEMPTS`?
5. Which registry projections does the validator use?

**Code**
6. What does `validate_dsl` return?
7. What happens on `repair_hint=None`?
8. Where does the repair hint go in the next call?
9. What does `_generate_dsl` return, and why four values?
10. Which override uses `setdefault` rather than assignment, and why?

**Why**
11. Why a DSL rather than generated SQL?
12. Why does `LLMUnavailable` break rather than continue?
13. Why is a `comparison` of a company with itself rejected?
14. Why does the entity override skip comparisons?
15. Why is `max_tokens=200` a fail-closed choice?

**Debugging**
16. A question naming no metric returns a ticked figure. Which caveat, and what
    mitigates it?
17. The peer-comparison view produces the wrong operation occasionally. Which
    caveat, and what is actually keeping it working?
18. `dsl_generation_failed` with "Last error: None". What was the historic bug?

**System design**
19. Add a `median` operation. List everything that changes.
20. `CAVEAT-002`: choose one of the three options and defend it.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **`error`** is for the log and, via `quant_engine`, the user-facing message.
   **`repair_hint`** is fed back **to the model** in the next attempt's user
   message. `repair_hint=None` means **non-recoverable — do not retry**: the loop
   returns immediately rather than spending its second attempt.
2. **`CAVEAT-004`.** Making it `Optional` would **move the problem into the
   validator rather than removing it**: the validator must then handle `None`, every
   downstream consumer must too, and — decisively — **the model still has to
   choose** between `None` and a guess, so the invent-a-metric failure relocates
   rather than disappearing. It would also weaken the `response_schema`
   enforcement that makes the structured path reliable.
3. *"BREAK, not continue. The self-healing loop exists to repair BAD DSL; 'no
   provider answered' is not a DSL defect, and a repair hint cannot fix it …
   **the same conflation as the CRAG break/continue bug, inverted.**"* CRAG used
   `break` where `continue` was right (confusing "this rung is a no-op" with "the
   ladder is exhausted"); here `continue` would be wrong where `break` is right
   (confusing "the DSL is bad" with "no provider answered"). Same class: a
   control-flow decision treating two different conditions as one.
4. **`len(companies) == 1`** because handing one of two named issuers to `entity`
   would **silently drop the other** — the F14 defect itself. **`not
   is_comparison`** because a comparison needs the model's own
   `entity`/`comparison_entity` pairing; the router extracts a *list*, not an
   ordered pair with roles. Together they preserve pre-F14 behaviour exactly while
   making the reason explicit rather than incidental.
5. **One call site**, `validate_dsl(raw_dict)`, with **no second argument** — so
   `preferred_operation` is always `None` and the block never executes.
   Meanwhile `router_node` writes `state["preferred_operation"]` and nothing reads
   it. **Which option:** wiring it through is the better answer *if* the
   deterministic override is genuinely wanted — and it is, because the peer view
   currently relies on a prompt rule, which is the probabilistic guardrail doing
   the deterministic one's job. But note it is a **functional change** requiring
   approval, and it needs a measurement: forcing `growth_comparison` could break a
   query the model currently classifies better than the UI does. The one option
   that is clearly wrong is leaving it: a comment asserting "Load-Bearing" on dead
   code tells the next reader the mechanism is protected when it is not.

### §12 — Basic

1. **Eight.** Required: `metric`, `entity`, `fiscal_year`, `financial_type`,
   `operation`. Optional: `quarter`, `comparison_entity`, `comparison_period`.
2. `point_in_time`, `yoy_growth`, `comparison`, `cagr`, `growth_comparison`.
3. A short, specific instruction returned by the validator and fed back to the
   model on the next attempt — e.g. *"Provide 'comparison_entity' with the second
   company's ticker."*
4. **2** — one initial attempt plus one repair.
5. `dsl_registry()` as `METRIC_REGISTRY` and `dsl_alias_pairs()` as
   `METRIC_ALIASES` (Day 31).
6. A `ValidationResult` dataclass: `valid`, `dsl_object`, `error`, `repair_hint`.
7. The loop returns immediately — non-recoverable, no second attempt.
8. Into the **user** message, as *"PREVIOUS ATTEMPT FAILED. Fix this issue: …"* —
   not the system prompt, which stays a module-level constant.
9. `(dsl_object, attempts, error_message, llm_result)`. Four because the caller
   needs the object, the attempt count for state, a user-facing error, **and the
   whole `LLMResult`** so it can record provider *and* model in one attributed
   write (Day 19).
10. `financial_type`, via `raw_dict.setdefault(...)` — the weakest form, a default
    rather than an override, because the model's own reading of "standalone" in
    the query text should win over the router's default of "consolidated".

### §12 — Why

11. Because validity is **decidable** for a form and not for a blank sheet. Each of
    eight fields can be checked against a closed set; "this SQL answers a different
    question than the one asked" cannot be checked at all. It also means the model
    never sees the schema and never writes SQL.
12. See §11 Q3.
13. Because it is arithmetically correct and answers nothing — a difference of
    zero. The eval has a category (`quantitative_cross_period_refusal`) that
    **fails hard** if entities silently collapse, because it was a real regression.
14. Because the router extracts an unordered *list* of issuers, while a comparison
    needs an ordered pair with roles (`entity` first-named, `comparison_entity`
    second). The model's own pairing carries that ordering; the router's list does
    not.
15. Because a DSL object longer than 200 tokens would be **truncated**, which
    produces invalid JSON, which fails schema validation, which
    `generate_structured` treats as a **provider failure** (Day 18) — so the
    system refuses rather than acting on a partial object.

### §12 — Debugging

16. **`CAVEAT-004`.** `GeminiDSLResponse.metric` is required, so a model asked
    about a question naming no metric must invent one — and the invention
    validates, compiles, executes and is stamped `sql_verified=True`. **Mitigated
    by three regex guards over the raw query, before any LLM call**: Stage 0
    (derived), Stage 0b (unqueryable) and Stage 0c (no metric anchor, cross path)
    — Days 34 and 37. Not by the schema.
17. **`CAVEAT-002`.** `preferred_operation` is written by `router_node` and never
    read; the "load-bearing" override never fires. **What is actually keeping it
    working** is a rule in `DSL_SYSTEM_PROMPT` — *"use this for questions like 'who
    grew revenue faster, X or Y'"* — i.e. the probabilistic guardrail is doing the
    deterministic one's job, which is the inverse of this project's stated
    preference.
18. `last_error` was **not captured on every iteration**, so when the loop
    exhausted its attempts the final message lost the reason for the last failure.
    Fixed by assigning `last_error = validation.error` inside the loop — marked in
    the source with `# NEW — capture it every iteration`.

### §12 — System design

19. `OPERATION_REGISTRY` (the entry, which flows into the prompt automatically);
    a validation branch in `DSLValidator.validate` for its requirements; a
    `_compile_median` method and a dispatch arm in `SQLCompiler.compile`
    (Day 33); a `_compute_median` if the arithmetic is Python-side, or a SQL
    aggregate if not; a formatting branch in `_format_quant_response` (Day 34); a
    rule in `DSL_SYSTEM_PROMPT` describing when to use it — **which is a prompt
    edit and therefore STOP-AND-ASK** (Day 18); and, if it is to be evaluated, a
    golden-dataset category and a scorer in `eval_runner.py` (Day 43). Nothing in
    the database or the registry changes — `median` is an operation over existing
    metrics, not a new metric.
20. **Wire it through** — `_generate_dsl` gains a `preferred_operation` parameter,
    threaded from `state.get("preferred_operation")`, and passes it to
    `validate_dsl`. **Defence:** the UI's peer-comparison view *knows* it wants
    `growth_comparison` — that is deterministic information the system currently
    throws away and then re-derives probabilistically from a prompt rule. This
    project's stated preference is deterministic over agentic, and here the
    deterministic path already exists and is simply unplugged. **What it must come
    with:** a measurement, because forcing an operation can be *wrong* — a user in
    the peer view asking a `point_in_time` question would have their operation
    overridden. So the override should apply only when the model's choice is one of
    a small set the view can legitimately override, and the eval's
    `quantitative_growth_comparison` category should be run before and after.
    **Why not delete instead:** deletion is cheaper and loses a real capability the
    UI already signals. Both beat the status quo, in which the comment lies.

---

## 14. MUST REMEMBER

```text
- EIGHT fields. FIVE operations. Both closed sets, both interpolated into the prompt
- Required: metric · entity · fiscal_year · financial_type · operation
- ValidationResult: error (for humans) + repair_hint (FOR THE MODEL)
- repair_hint = None means NON-RECOVERABLE. Do not retry
- MAX_DSL_ATTEMPTS = 2 — one attempt plus one repair
- The repair hint goes in the USER message; the system prompt stays constant
- LLMUnavailable BREAKS. "No provider answered" is not a DSL defect
- Entity override only when len(companies) == 1 AND not a comparison
- CAVEAT-004: metric and fiscal_year are REQUIRED, so the model must invent
- CAVEAT-002: the "Load-Bearing" operation override CAN NEVER FIRE
```

## 15. MUST UNDERSTAND

```text
- Why a form is validatable and a blank sheet is not
- Why making a required field optional RELOCATES the problem rather than
  removing it — the model still has to choose
- Why a STABLE wrong answer (PQ012, five runs) is harder to catch than a flaky one
- Why the same control-flow confusion produced opposite bugs in CRAG and here
- Why a refactor was made byte-identical for the case that was already measured
- Why a comment claiming "Load-Bearing" on dead code is worse than no comment
```

---

## 16. This connects to

```text
Day 31 — the registry, and how a number becomes a row
   ↓
Day 32 — the DSL: eight fields, validated                ← you are here
   ↓
Day 33 — compiling those fields to SQL, and doing the maths in Python
```

Forward references:

- `compile_dsl` and the five compilers → **Day 33**
- Stage 0 / 0b guards, and the period assumption → **Day 34**
- Stage 0c and `metric_anchor_phrases` → **Day 37**
- `DSL_SYSTEM_PROMPT` as prompt engineering → **Day 18** (already read)
- The eval's `quantitative_*` categories → **Day 43**
