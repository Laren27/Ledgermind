# Day 33 — SQL Compilation and Python Arithmetic

**Phase 9 · Weight: H (~120 min) · Prerequisites: Days 15, 32**

---

## 1. Today's goal

By tonight you can:

- Explain what a compiler is in this sense, and why one exists between the DSL and
  the database.
- Read `_base_select` and account for **every clause**, including the two that
  are unconditional and the one that branches on `NULL`.
- Explain why `growth_comparison` needs **four** queries and how `CompileResult`
  carries them.
- Explain parameterised queries as the *structural* injection defence, and why
  the LLM never touching SQL makes it complete.
- Explain why every derived metric is computed in **Python**, and read each
  compute function's failure handling.

---

## 2. Why now

Day 32 produced a validated eight-field object. Today it becomes SQL and a
number. Day 34 wraps both in guards and verification.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `financials` columns, `is_latest` | Days 13, 15 | What is queried |
| `SET LOCAL app.tenant_id`, RLS | Day 14 | `_execute_sql` |
| `quarter IS NULL` means annual | Day 13 | The one branching clause |
| `DSLObject`'s eight fields | Day 32 | The compiler's input |

---

## 4. Concept lesson

### 4.1 What "compiler" means here

Not a language compiler. A **translator from one structured representation to
another**: `DSLObject` → parameterised SQL.

```
DSLObject                                   SQL + params
{metric: "revenue",              ──────►    SELECT value, metric, ...
 entity: "ETERNAL",                         FROM financials
 fiscal_year: "FY26",                       WHERE tenant_id = %s
 quarter: None,                               AND company = %s
 financial_type: "consolidated",              AND metric = %s
 operation: "point_in_time"}                  AND fiscal_year = %s
                                              AND financial_type = %s
                                              AND is_latest = TRUE
                                              AND quarter IS NULL
                                            params = (tenant, 'ETERNAL',
                                                      'revenue', 'FY26',
                                                      'consolidated')
```

**Deterministic.** The same DSL always produces the same SQL. No sampling, no
temperature, no provider.

**Mental model.** The DSL is **an order form**; the compiler is **the clerk who
turns it into a warehouse instruction**. The clerk never improvises.

---

### 4.2 `_base_select` — every clause accounted for

```python
def _base_select(self, dsl: DSLObject, tenant_id: str) -> Tuple[str, List]:
    sql = """
        SELECT value, metric, fiscal_year, quarter, financial_type, filing_date, unit, doc_id
        FROM financials
        WHERE tenant_id = %s AND company = %s AND metric = %s
          AND fiscal_year = %s AND financial_type = %s AND is_latest = TRUE
    """
    params = [tenant_id, dsl["entity"], dsl["metric"], dsl["fiscal_year"], dsl["financial_type"]]
    if dsl["quarter"] is not None:
        sql += "  AND quarter = %s\n"
        params.append(dsl["quarter"])
    else:
        sql += "  AND quarter IS NULL\n"
    return sql.strip(), params
```

**Every one of the five operations builds on this.** One place where the WHERE
clause lives.

**The selected columns are not arbitrary:**

| Column | Consumed by |
|---|---|
| `value` | every compute function |
| `metric` | `_compute_*`'s `metric` field, and `display_label` (Day 31) |
| `fiscal_year`, `quarter` | the response template's period label (Day 34) |
| `financial_type` | disclosure |
| `filing_date` | provenance |
| `unit` | rendering — and audit **F3**'s asserted `crore_inr` |
| `doc_id` | **the provenance link back to `documents`** (Day 13) |

**`doc_id` is why `sql_verified` means something.** The number traces to a
document.

**Two unconditional clauses.**

`tenant_id = %s` — belt and braces. RLS already filters by tenant (Day 14), so
this is redundant *while RLS is enabled*. Redundant, and it means a policy
misconfiguration degrades to "no rows" rather than "another tenant's rows".

`is_latest = TRUE` — never query a restated figure (Day 15). Not optional
anywhere in the request path.

**And the one branching clause:**

```python
if dsl["quarter"] is not None:
    sql += "  AND quarter = %s\n"
else:
    sql += "  AND quarter IS NULL\n"
```

**`quarter = NULL` matches nothing, silently** (Day 15). The compiler **branches
and emits `IS NULL` literally** rather than binding a parameter.

**Compare with `db_loader`**, which solves the same NULL problem with
`IS NOT DISTINCT FROM`. Two solutions, both correct:

| | `db_loader` | `_base_select` |
|---|---|---|
| Approach | `quarter IS NOT DISTINCT FROM %(quarter)s` | branch, emit `IS NULL` |
| Why | one statement covers either case | it is **building SQL text anyway**, and an explicit `IS NULL` is readable in a logged query |

`state["sql_query"]` reaches analysts (Day 9). `AND quarter IS NULL` reads
plainly; `IS NOT DISTINCT FROM NULL` invites a second look.

---

### 4.3 Parameterisation, and why it is structural here

```python
cur.execute(sql, params)
```

psycopg2 sends the statement and the values **separately**. The value is never
parsed as SQL. `'; DROP TABLE financials; --` is looked up as a company name and
found not to exist.

**Never** string formatting:

```python
# NEVER, anywhere in this codebase:
sql = f"WHERE company = '{dsl['entity']}'"
```

**And here the defence is complete rather than merely correct**, because of two
layers:

1. **The LLM never writes SQL** — it emits eight fields, which become
   *parameters*, never *statement text* (Day 17's ED-001).
2. **The statement text is built from the compiler's own literals** — the only
   thing that varies is which fixed clause is appended.

A text-to-SQL system has to sanitise generated SQL, which is a losing game. Here
the attack surface does not exist: **there is nowhere for user input to become
statement text.**

**One thing worth noticing:** `dsl["metric"]`, `dsl["entity"]` and
`dsl["fiscal_year"]` are parameters, so injection is impossible — but they are
also **model output**. Validation (Day 32) is what stops a *semantically* wrong
value; parameterisation is what stops a *syntactically* dangerous one. **Two
different defences against two different problems**, and confusing them is a
common error.

---

### 4.4 `CompileResult` and the `__post_init__`

```python
@dataclass
class CompileResult:
    success: bool
    error: Optional[str] = None
    operation: Optional[str] = None
    metric_label: Optional[str] = None
    queries: List[Tuple[str, tuple]] = field(default_factory=list)
    sql: Optional[str] = None
    params: Optional[tuple] = None
    sql2: Optional[str] = None
    params2: Optional[tuple] = None

    def __post_init__(self):
        if not self.queries:
            if self.sql and self.params is not None:
                self.queries.append((self.sql, self.params))
            if self.sql2 and self.params2 is not None:
                self.queries.append((self.sql2, self.params2))
```

**Two representations of the same thing**, and `__post_init__` reconciles them.

**Why both exist.** Four operations need one or two queries and use
`sql`/`sql2`; `growth_comparison` needs four and uses `queries`. Rather than
forcing every operation into a list, the dataclass accepts either and **normalises
into `queries`** on construction.

**`params is not None` rather than `if self.params`.** An empty tuple is falsy and
would be skipped — and while no current operation has zero parameters
(`tenant_id` is always one), the explicit check means a future one could not
silently lose its query. The same explicit-not-truthy discipline as
`_build_filter` (Day 27) and `db_transaction` (Day 11).

**`field(default_factory=list)`** — the mutable-default trap. A bare
`queries: List = []` would share one list across every instance.

---

### 4.5 The five compilers

```python
def compile(self, dsl: DSLObject, tenant_id: str) -> CompileResult:
    operation = dsl["operation"]
    if operation == "point_in_time":     return self._compile_point_in_time(dsl, tenant_id)
    elif operation == "yoy_growth":      return self._compile_yoy_growth(dsl, tenant_id)
    elif operation == "comparison":      return self._compile_comparison(dsl, tenant_id)
    elif operation == "cagr":            return self._compile_cagr(dsl, tenant_id)
    elif operation == "growth_comparison": return self._compile_growth_comparison(dsl, tenant_id)
    return CompileResult(success=False, error=f"No compiler for operation: {operation}")
```

| Operation | Queries | How the second (etc.) differs |
|---|---|---|
| `point_in_time` | 1 | — |
| `yoy_growth` | 2 | `fiscal_year` → prior year |
| `comparison` | 2 | `entity` → `comparison_entity` |
| `cagr` | 1 | different SQL: no `fiscal_year`, `ORDER BY fiscal_year ASC` |
| `growth_comparison` | **4** | entity × period, both dimensions |

**The prior-year inference:**

```python
try:
    year_num = int(dsl["fiscal_year"].replace("FY", ""))
    prior_fy = f"FY{year_num - 1}"
except ValueError:
    return CompileResult(success=False, error=f"Cannot infer prior year from: {dsl['fiscal_year']}")
```

**String arithmetic on a label.** `"FY26"` → `26` → `25` → `"FY25"`. It works
because Indian fiscal years are labelled this way, and it **fails closed** on
anything unparseable rather than guessing.

**The variant construction:**

```python
prior_dsl = dict(dsl)
prior_dsl["fiscal_year"] = prior_fy
sql2, params2 = self._base_select(DSLObject(**prior_dsl), tenant_id)
```

**Copy, modify the copy, rebuild.** `DSLObject` is a `TypedDict` (Day 10), so
`dict(dsl)` is a shallow copy and `DSLObject(**prior_dsl)` a typed reconstruction.
Mutating `dsl` in place would corrupt the object the caller still holds and the
one written to `state["dsl_object"]`.

**And `cagr` is the one that does not use `_base_select`:**

```python
sql = """
    SELECT value, fiscal_year, quarter, financial_type, filing_date, unit
    FROM financials
    WHERE tenant_id = %s AND company = %s AND metric = %s AND financial_type = %s
      AND is_latest = TRUE AND quarter IS NULL
    ORDER BY fiscal_year ASC
"""
```

**No `fiscal_year` filter** — CAGR spans all available years. **`quarter IS NULL`
hardcoded** — CAGR is annual by definition. **`ORDER BY fiscal_year ASC`** — and
that ordering is *lexical* on a TEXT column (Day 13), correct for FY23–FY99.

**Note it also drops `doc_id` from the SELECT.** CAGR spans several documents, so
one `doc_id` would be misleading — the provenance is the set of rows, not one row.

---

### 4.6 Python does the arithmetic

```python
def _compute_yoy_growth(current_rows, prior_rows, metric_label) -> Dict[str, Any]:
    if not current_rows or not prior_rows:
        return {"error": "Missing data for one or both periods",
                "current": None, "prior": None, "yoy_pct": None}

    current_val = float(current_rows[0]["value"])
    prior_val   = float(prior_rows[0]["value"])
    ...
    if prior_val == 0:
        yoy_pct = None
        yoy_note = "Prior year value is zero — growth % undefined"
    else:
        yoy_pct = round((current_val - prior_val) / abs(prior_val) * 100, 2)
        yoy_note = None
```

**`float(row["value"])`** — psycopg2 returns `Decimal` (Day 13). Converting at the
boundary because the result is JSON-serialised.

**`abs(prior_val)`** in the denominator. **This is not cosmetic.** With a negative
prior value — a prior-year loss — `(current − prior) / prior` flips the sign, so a
loss narrowing from −100 to −50 would report **−50%** growth when it improved.
`abs()` makes the sign of the result mean *direction of change*.

**`prior_val == 0` returns `yoy_pct = None` and a note**, rather than raising or
returning infinity. Growth from zero is genuinely undefined, and the note says so.

**And the two-line period label:**

```python
quarter = current_rows[0].get("quarter")
"current_fy": f"{quarter} {current_fy}" if quarter else current_fy,
```

A quarterly YoY renders `"Q4 FY26"`; an annual one renders `"FY26"`.

---

### 4.7 The other three

**`_compute_comparison`** — same shape, plus a signed difference:

```python
diff = round(v1 - v2, 2)
diff_pct = round((v1 - v2) / abs(v2) * 100, 2) if v2 != 0 else None
```

**`_compute_growth_comparison`** — computes **two** YoY figures and names a
winner:

```python
if a_prior_val == 0 or b_prior_val == 0:
    return {"error": "One or both entities' prior-year value is zero — growth % undefined", ...}

yoy_a_pct = round((a_curr_val - a_prior_val) / abs(a_prior_val) * 100, 2)
yoy_b_pct = round((b_curr_val - b_prior_val) / abs(b_prior_val) * 100, 2)
faster = entity_a if yoy_a_pct > yoy_b_pct else entity_b
```

**Four values in, one verdict out.** This is Q051 — *"Who grew revenue faster in
FY26, Eternal or Paytm?"* — measured at ETERNAL faster, 168.56 vs 22.28.

**`_compute_cagr`** — the only one over an arbitrary number of rows:

```python
if len(rows) < 2:
    return {"error": f"Need ≥2 data points for CAGR, found {len(rows)}", "cagr_pct": None}
...
n_years = len(rows) - 1
if v_start <= 0:
    return {"error": "Starting value ≤0, CAGR undefined", "cagr_pct": None}
cagr_pct = round(((v_end / v_start) ** (1 / n_years) - 1) * 100, 2)
```

**`n_years = len(rows) - 1`** — three data points span two years. Off by one and
every CAGR is wrong by a constant factor.

**`v_start <= 0`** — a fractional power of a negative number is complex, and of
zero is a division by zero. Refused with a reason.

**And it returns its own inputs:**

```python
"data_points": [{"fiscal_year": r["fiscal_year"], "value": float(r["value"])} for r in rows],
```

**The CAGR is checkable**, because the response carries the series it was computed
from.

---

### 4.8 The uniform failure shape

Every compute function returns a **dict with an `error` key** rather than raising:

```python
state["sql_verified"] = computed.get("error") is None
state["confidence_score"] = 1.0 if state["sql_verified"] else 0.4
state["confidence_tier"] = "high" if state["sql_verified"] else "low"
```

**One line decides `sql_verified` for every derived operation.** A computation that
could not complete is not verified, and the tier follows.

**Why dicts and not exceptions.** A missing prior year is not exceptional — it is
a *fact about the corpus*, and it must reach the user as an explanation. An
exception would have to be caught and translated at every call site.

---

## 5. The actual LedgerMind files

```
File:  backend/app/engines/dsl_compiler.py — SQLCompiler (lines ~190-300)
       compile(dsl, tenant_id) -> CompileResult
       _base_select · _compile_point_in_time · _compile_yoy_growth
       _compile_comparison · _compile_cagr · _compile_growth_comparison
       CompileResult with __post_init__ normalisation

File:  backend/app/engines/quant_engine.py (lines ~430-620)
       _get_db_connection · _execute_sql(sql, params, tenant_id)
       _compute_yoy_growth · _compute_comparison
       _compute_growth_comparison · _compute_cagr
```

---

## 6. Deep walkthrough — `_execute_sql`

```python
def _execute_sql(sql: str, params: tuple, tenant_id: str) -> List[Dict[str, Any]]:
    """
    Execute a single parameterised SQL query with RLS tenant isolation.
    SET LOCAL app.tenant_id scopes the RLS policy to this transaction only.
    Uses DictCursor so results are accessible by column name.
    """
    conn = None
    try:
        conn = _get_db_connection()
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SET LOCAL app.tenant_id = %s", (str(tenant_id),))
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(row) for row in rows]
    except psycopg2.Error as e:
        logger.error("SQL execution failed: %s | SQL: %s", e, sql[:200])
        raise
    finally:
        if conn:
            conn.close()
```

Day 14 covered this. Two additions from today's angle:

**`return [dict(row) for row in rows]`.** `RealDictCursor` returns
`RealDictRow` objects — dict-like, and not plain dicts. Converting means the rows
are JSON-serialisable and go straight into `state["sql_result"]` (Day 3) with no
further handling.

**`sql[:200]` in the error log.** Truncated for the same reason `audit_writer`
logs single-line (Day 7): Render's log stream. And note what is **not** logged —
`params`. A logged parameter could contain tenant data.

**And the multiplier.** `growth_comparison` calls this **four times**:

```python
all_rows = [_execute_sql(q_sql, q_params, tenant_id) for q_sql, q_params in compile_result.queries]
```

Four connections, four handshakes, four transactions — plus the audit write. That
is `CAVEAT-013` at its worst (Day 11).

---

### 6.1 `growth_comparison`'s separate path

```python
if operation == "growth_comparison":
    try:
        all_rows = [_execute_sql(q_sql, q_params, tenant_id) for q_sql, q_params in compile_result.queries]
    except Exception as e:
        state["error"] = "sql_execution_failed"
        ...
        return state

    a_curr, a_prior, b_curr, b_prior = all_rows
    state["sql_row_count"] = sum(len(r) for r in all_rows)
    computed = _compute_growth_comparison(a_curr, a_prior, b_curr, b_prior,
                                          dsl["entity"], dsl["comparison_entity"],
                                          compile_result.metric_label)
    state["sql_result"] = [computed]
    state["sql_verified"] = computed.get("error") is None
    ...
    return state
```

The comment marks it:

```python
# ── growth_comparison: 4-query operation, handled separately since it
# doesn't fit the single-query .sql/.params execution path below ──
```

**`a_curr, a_prior, b_curr, b_prior = all_rows`** — positional unpacking of a
four-element list, matching the order `_compile_growth_comparison` appended them.
**An ordering contract between two functions, enforced by nothing.** Reorder the
compiler's `queries` list and the computation silently compares the wrong pairs.

**`sum(len(r) for r in all_rows)`** — `sql_row_count` across all four, so a
missing period shows as a count below 4.

---

## 7. Data flow

```
DSLObject (validated)                                     Day 32
        ▼ compile_dsl(dsl, tenant_id)
        │
   ┌────┴─────────────────────────────────────────────────────┐
   │ point_in_time  → _base_select                → 1 query   │
   │ yoy_growth     → _base_select × 2 (FY, FY-1) → 2 queries │
   │ comparison     → _base_select × 2 (A, B)     → 2 queries │
   │ cagr           → custom SQL, ORDER BY FY ASC → 1 query   │
   │ growth_comparison → _base_select × 4          → 4 queries│
   └────┬─────────────────────────────────────────────────────┘
        ▼ CompileResult.__post_init__ normalises into .queries
        │
        ▼ _execute_sql(sql, params, tenant_id)     ONE CONNECTION EACH
        │    _get_db_connection()                  CAVEAT-013
        │    with conn:  BEGIN
        │      SET LOCAL app.tenant_id = %s        RLS scope   (Day 14)
        │      cur.execute(sql, params)            PARAMETERISED
        │      fetchall() → RealDictRow → dict
        │    COMMIT · close
        │
        ▼ rows: [{"value": Decimal("54364.00"), "metric": "revenue",
        │          "fiscal_year": "FY26", "unit": "crore_inr", "doc_id": ...}]
        │
        ▼ PYTHON ARITHMETIC — never SQL, never the LLM
        │    _compute_yoy_growth       (current − prior) / abs(prior) × 100
        │    _compute_comparison       v1 − v2, and a signed %
        │    _compute_growth_comparison  two YoY, then a winner
        │    _compute_cagr             (end/start)^(1/n) − 1
        │
        ▼ {"error": None, ...} or {"error": "Missing data ...", ...}
        │
        ▼ sql_verified = computed.get("error") is None
        ▼ confidence 1.0 / high  or  0.4 / low
        ▼ state["sql_result"] = [computed]
        │
        ▼ _format_quant_response(state)                       Day 34
```

---

## 8. Engineering decision — compile deterministically, compute in Python

**Problem.** Turn a validated request into an exact figure, with the arithmetic
auditable.

**Decision.** A hand-written compiler producing parameterised SQL; all derived
arithmetic in Python; failures returned as dicts.

`ENGINEERING_DECISIONS.md` **ED-001**, **ED-006**.

| Alternative | Why not |
|---|---|
| **SQL does the arithmetic** (`(a-b)/b*100` in the query) | Two more places for `NULL` and division-by-zero, expressed in a language with different semantics — and the intermediate values would not be in the response |
| **An ORM query builder** | Rejected project-wide (Day 13). The SQL *is* the artefact being reviewed |
| **One generic compiler with dynamic clauses** | Fewer lines, and every operation's requirements become runtime conditionals instead of separate, readable functions |
| **Raise on missing data** | A missing prior year is a fact about the corpus, not an exception; it must reach the user as an explanation |
| **String-formatted SQL** | Injection, and it would put model output into statement text |

**Trade-offs accepted.**

- **Five near-identical compile functions.** Deliberate: each is readable in
  isolation, and `_base_select` holds the shared part.
- **`CAVEAT-013`** — one connection per statement, four for
  `growth_comparison`.
- **The four-query ordering contract** is positional and unenforced.
- **Lexical `ORDER BY fiscal_year`** — correct to FY99.
- **`unit` is selected and rendered but never validated** — audit F3.

**Current validity.** Strong. The compiler is the part of this system that most
clearly earns `sql_verified`.

**At 10×.** Connection pooling becomes necessary (Day 11's audit), and
`idx_financials_lookup` (Day 15) already matches `_base_select`'s WHERE order, so
the query itself scales.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `Cannot infer prior year from: ...` | `fiscal_year` not `FY##`. **Fails closed** |
| `Missing data for one or both periods` | A prior year absent from the corpus |
| `Need ≥2 data points for CAGR` | Fewer than two annual rows |
| `Starting value ≤0, CAGR undefined` | Negative or zero base |
| A narrowing loss reported as negative growth | Would be a missing `abs()` in the denominator |
| Zero rows | RLS unset (Day 14), or genuinely no data (Day 34) |
| `pgcode 53300` | Connection exhaustion — `CAVEAT-013`, ×4 here |
| Wrong pairs compared in `growth_comparison` | The positional ordering contract broken |
| Every CAGR wrong by a constant factor | `n_years` off by one |

---

## 10. Hands-on experiment

`export T=<alpha tenant uuid>` as on Day 14.

### Experiment 1 — compile all five, and read the SQL

```bash
docker compose exec -T backend python -c "
import os
from app.engines.dsl_compiler import compile_dsl
T = os.getenv('T','')
base = dict(metric='revenue', entity='ETERNAL', period='FY26', fiscal_year='FY26',
            quarter=None, financial_type='consolidated', operation='point_in_time',
            comparison_entity=None, comparison_period=None)
for op, extra in [('point_in_time', {}), ('yoy_growth', {}),
                  ('comparison', {'comparison_entity':'PAYTM'}),
                  ('cagr', {}),
                  ('growth_comparison', {'comparison_entity':'PAYTM'})]:
    d = dict(base); d['operation'] = op; d.update(extra)
    r = compile_dsl(d, T)
    print(f'=== {op}  success={r.success}  queries={len(r.queries)}  label={r.metric_label!r}')
    for i,(sql,p) in enumerate(r.queries,1):
        print(f'  --- query {i} ---')
        print('  ' + sql.replace(chr(10), chr(10)+'  '))
        print('  params:', p)
    print()
"
```

**Read every WHERE clause.** Find `is_latest = TRUE` and `quarter IS NULL` in each.

### Experiment 2 — the `NULL` branch

```bash
docker compose exec -T backend python -c "
import os
from app.engines.dsl_compiler import compiler
T = os.getenv('T','')
base = dict(metric='revenue', entity='ETERNAL', period='FY26', fiscal_year='FY26',
            financial_type='consolidated', operation='point_in_time',
            comparison_entity=None, comparison_period=None)
for q in (None, 'Q4'):
    d = dict(base); d['quarter'] = q
    sql, params = compiler._base_select(d, T)
    print(f'quarter={q!r}:')
    print('  last clause:', sql.strip().splitlines()[-1].strip())
    print('  params     :', params)
print()
print('quarter=None emits the LITERAL \"AND quarter IS NULL\" — no parameter.')
print('Binding NULL as a parameter would match NOTHING, silently.')
"
```

### Experiment 3 — parameterisation, attacked

```bash
docker compose exec -T backend python -c "
import os
from app.engines.dsl_compiler import compiler
T = os.getenv('T','')
d = dict(metric='revenue', entity=\"ETERNAL'; DROP TABLE financials; --\",
         period='FY26', fiscal_year='FY26', quarter=None,
         financial_type='consolidated', operation='point_in_time',
         comparison_entity=None, comparison_period=None)
sql, params = compiler._base_select(d, T)
print('SQL TEXT (unchanged — no user input in it):')
print(sql)
print()
print('PARAMS:', params)
print()
print('The hostile string is a VALUE, looked up as a company name and not found.')
print('It never becomes statement text. And note it could never have got this far:')
print('the DSL validator (Day 32) checks entity against the corpus separately.')
"
```

### Experiment 4 — run one for real

```bash
docker compose exec -T -e T=\"$T\" backend python -c "
import os
from app.engines.dsl_compiler import compile_dsl
from app.engines.quant_engine import _execute_sql
T = os.getenv('T','')
d = dict(metric='revenue', entity='ETERNAL', period='FY26', fiscal_year='FY26',
         quarter=None, financial_type='consolidated', operation='point_in_time',
         comparison_entity=None, comparison_period=None)
r = compile_dsl(d, T)
rows = _execute_sql(r.sql, r.params, T)
print('rows:', len(rows))
for row in rows:
    for k, v in row.items():
        print(f'  {k:16} {v!r}  ({type(v).__name__})')
print()
print('Note value is a DECIMAL. doc_id is the provenance link.')
"
```

### Experiment 5 — the arithmetic, and the `abs()`

```bash
docker compose exec -T backend python -c "
from app.engines.quant_engine import _compute_yoy_growth, _compute_cagr, _compute_comparison
def rows(v, fy, q=None): return [{'value': v, 'fiscal_year': fy, 'quarter': q, 'unit':'crore_inr'}]

print('normal growth :', _compute_yoy_growth(rows(54364.0,'FY26'), rows(20243.0,'FY25'), 'Revenue')['yoy_pct'])
print('a NARROWING LOSS (prior negative):')
r = _compute_yoy_growth(rows(-50.0,'FY26'), rows(-100.0,'FY25'), 'PAT')
print('   with abs()   :', r['yoy_pct'], ' <- +50%, correctly an improvement')
print('   without abs():', round((-50.0 - -100.0) / -100.0 * 100, 2), ' <- -50%, WRONG DIRECTION')
print()
print('prior = 0     :', _compute_yoy_growth(rows(100.0,'FY26'), rows(0.0,'FY25'), 'X'))
print()
c = _compute_cagr([{'value':100.0,'fiscal_year':'FY24','unit':'crore_inr'},
                   {'value':150.0,'fiscal_year':'FY25','unit':'crore_inr'},
                   {'value':225.0,'fiscal_year':'FY26','unit':'crore_inr'}], 'Revenue', 'X')
print('cagr 3 points :', c['cagr_pct'], '%  n_years =', c['n_years'], ' <- 3 points span 2 years')
print('data_points returned:', len(c['data_points']), ' <- the CAGR is CHECKABLE')
"
```

### Experiment 6 — `growth_comparison`, four queries

```bash
docker compose exec -T -e T=\"$T\" backend python -c "
import os
from app.engines.dsl_compiler import compile_dsl
from app.engines.quant_engine import _execute_sql, _compute_growth_comparison
T = os.getenv('T','')
d = dict(metric='revenue', entity='ETERNAL', comparison_entity='PAYTM',
         period='FY26', fiscal_year='FY26', quarter=None,
         financial_type='consolidated', operation='growth_comparison',
         comparison_period=None)
r = compile_dsl(d, T)
print('queries:', len(r.queries))
for i,(sql,p) in enumerate(r.queries,1):
    print(f'  {i}: entity={p[1]:8} fiscal_year={p[3]}')
all_rows = [_execute_sql(s, p, T) for s, p in r.queries]
a_curr, a_prior, b_curr, b_prior = all_rows
out = _compute_growth_comparison(a_curr, a_prior, b_curr, b_prior, 'ETERNAL', 'PAYTM', 'Revenue')
print()
for k, v in out.items(): print(f'  {k:24} {v}')
print()
print('Q051. Four connections plus the audit write — CAVEAT-013 at its worst.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/dsl_compiler.py` (`SQLCompiler`) and
`backend/app/engines/quant_engine.py` (the `_compute_*` functions):

1. `_base_select` selects eight columns. Name the consumer of each, and say which
   one makes `sql_verified` meaningful.
2. Two clauses are unconditional. Which, and what does each protect against?
3. `_compute_yoy_growth` uses `abs(prior_val)` in the denominator. Construct the
   case where omitting it inverts the answer.
4. `CompileResult` has both `sql`/`sql2` and `queries`. Why both, and what does
   `__post_init__` do?
5. `_compile_cagr` does not use `_base_select` and drops `doc_id`. Give a reason
   for each.

---

## 12. Self-check questions

**Basic**
1. What does the compiler translate?
2. How many queries does each operation need?
3. What does `_base_select` always include?
4. Where does the arithmetic happen?
5. What does a compute function return on failure?

**Code**
6. What does `quarter is not None` decide?
7. Why `float(row["value"])`?
8. What is `n_years` in the CAGR, and why?
9. What does `RealDictCursor` give you?
10. How does `growth_comparison` execute its queries?

**Why**
11. Why not do the arithmetic in SQL?
12. Why is parameterisation *structurally* complete here?
13. Why does `_compile_cagr` hardcode `quarter IS NULL`?
14. Why return error dicts rather than raising?
15. Why does the compiler emit `IS NULL` literally instead of binding `NULL`?

**Debugging**
16. A CAGR is wrong by a constant factor across every query. What is wrong?
17. `growth_comparison` reports the wrong company as faster, and the individual
    values are right. What is wrong?
18. `pgcode 53300` appears only on peer-comparison queries. Why those?

**System design**
19. Add a `qoq_growth` operation. List every change.
20. `CAVEAT-013`: `growth_comparison` opens five connections. Fix it, and say what
    must be audited first.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `value` → every compute function. `metric` → the compute result's label and
   `display_label`. `fiscal_year`, `quarter` → the response template's period
   label. `financial_type` → disclosure. `filing_date` → provenance. `unit` →
   rendering (and audit F3's asserted crore). **`doc_id` → the provenance link
   back to `documents`, and it is what makes `sql_verified` meaningful**: the
   number traces to a specific filing rather than being asserted.
2. **`tenant_id = %s`** — belt and braces over RLS, so a policy misconfiguration
   degrades to *no rows* rather than *another tenant's rows*. **`is_latest =
   TRUE`** — so a restated, superseded figure can never be returned (Day 15).
3. Prior year **−100** (a loss), current year **−50** (a narrower loss). With
   `abs()`: `(−50 − −100) / 100 × 100 = +50%` — correctly an improvement. Without:
   `(−50 − −100) / −100 × 100 = −50%` — reported as a *decline* when the company
   improved. The `abs()` is what makes the result's sign mean **direction of
   change** rather than being inverted by the sign of the base.
4. Four operations produce one or two queries and use `sql`/`sql2`;
   `growth_comparison` produces four and uses `queries`. `__post_init__`
   **normalises** the pair form into `queries` at construction, so the executor
   has one interface while each compiler writes in whichever shape is natural.
   Note `params is not None` rather than truthiness — an empty tuple would
   otherwise be silently dropped.
5. **Does not use `_base_select`** because CAGR has no `fiscal_year` filter (it
   spans every available year) and needs `ORDER BY fiscal_year ASC`, so the shared
   WHERE clause does not fit. **Drops `doc_id`** because the result spans several
   documents — a single `doc_id` would be misleading, and the honest provenance is
   the returned `data_points` series.

### §12 — Basic

1. A validated `DSLObject` into parameterised SQL plus a parameter tuple.
2. `point_in_time` 1 · `yoy_growth` 2 · `comparison` 2 · `cagr` 1 ·
   `growth_comparison` **4**.
3. `tenant_id`, `company`, `metric`, `fiscal_year`, `financial_type`,
   `is_latest = TRUE`, and a quarter clause.
4. **In Python**, in the `_compute_*` functions — never in SQL, never in the LLM.
5. A dict with an `error` key set and the computed fields `None`.
6. Whether to emit `AND quarter = %s` with a bound parameter, or the literal
   `AND quarter IS NULL`.
7. psycopg2 returns `NUMERIC` as `Decimal`; the result is JSON-serialised, and
   JSON has no decimal type (Day 5).
8. `len(rows) - 1` — the number of *intervals*, not data points. Three annual
   figures span two years of growth.
9. Rows accessible by **column name** rather than position, so adding a column to
   the SELECT does not shift indexes.
10. A list comprehension over `compile_result.queries`, then positional unpacking
    `a_curr, a_prior, b_curr, b_prior = all_rows`.

### §12 — Why

11. Because `NULL` handling and division-by-zero would have to be expressed in a
    language with different semantics, in two more places; because the
    intermediate values (current, prior, both YoY figures) would not be available
    to the response; and because Python arithmetic is testable in the
    zero-network unit suite while SQL arithmetic is not.
12. Because there are **two layers**: the LLM never writes SQL (it emits eight
    fields that become *parameters*), and the statement text is built entirely
    from the compiler's own literals. There is **nowhere for user input to become
    statement text** — so unlike a text-to-SQL system, there is no generated SQL to
    sanitise.
13. Because CAGR is **annual by definition** — a compound annual growth rate over
    quarterly rows is meaningless, and mixing annual and quarterly rows in the
    series would silently produce a wrong figure.
14. Because a missing prior year is a **fact about the corpus**, not an
    exceptional condition. It must reach the user as an explanation, and an
    exception would have to be caught and translated at every call site.
15. Because `quarter = NULL` matches **nothing**, silently (Day 15). And because
    the compiler is building SQL text anyway, so an explicit `IS NULL` reads
    plainly in the logged `sql_query` that analysts see (Day 9).

### §12 — Debugging

16. **`n_years` is off by one** — using `len(rows)` instead of `len(rows) - 1`.
    Every CAGR would then be computed over one interval too many, producing a
    consistently understated rate.
17. **The positional ordering contract is broken.** `_compute_growth_comparison`
    unpacks `a_curr, a_prior, b_curr, b_prior` in the order
    `_compile_growth_comparison` appended them to `queries`. If that order changed,
    the function would compute A's growth from B's periods — each individual value
    correct, the pairing wrong. Nothing enforces the contract.
18. Because peer comparison uses `growth_comparison`, which is the **only
    four-query operation** — five connections including the audit write, against a
    `CAVEAT-013` design that opens one per statement. Concurrent peer queries
    exhaust the Postgres connection limit first.

### §12 — System design

19. `OPERATION_REGISTRY` in `dsl_compiler.py`; a validation branch requiring
    `quarter` and a way to name the prior quarter (which is harder than the year
    case — Q1's prior quarter is the **previous fiscal year's Q4**, so the
    inference is not simple decrement); `_compile_qoq_growth` producing two
    queries; a dispatch arm in `compile`; `_compute_qoq_growth` (or reuse
    `_compute_yoy_growth`, since the arithmetic is identical — only the period
    labelling differs); a formatting branch in `_format_quant_response` (Day 34);
    a rule in `DSL_SYSTEM_PROMPT` — **a prompt edit, STOP-AND-ASK** (Day 18); and a
    golden-dataset category plus a scorer if it is to be evaluated (Day 43).
    Nothing changes in the database or the registry.
20. **The fix:** a connection pool, or — cheaper and more targeted — execute the
    four queries **on one connection inside one transaction**, since they share a
    tenant and are read-only. `_execute_sql` would need a variant accepting a list
    of `(sql, params)` and an open connection, or `growth_comparison` would open
    the connection itself and pass a cursor down. **What must be audited first:**
    every `SET app.tenant_id` on the request path must be `SET LOCAL` (Day 11) —
    which `_execute_sql` already uses, so the request path is safe; the risk is
    that `ingestion/pipeline.py` and `db_loader.py` use a plain `SET`, and a
    pooler whose scope included them would leak a tenant setting between jobs. So:
    confirm the pooler's scope excludes the batch path, or convert those to `SET
    LOCAL`, **and** add the static test from Day 14 that fails if `SET
    app.tenant_id` appears without `LOCAL` on a request-path module.

---

## 14. MUST REMEMBER

```text
- The compiler is DETERMINISTIC: same DSL → same SQL, always
- _base_select ALWAYS includes tenant_id and is_latest = TRUE
- quarter=None emits the LITERAL "AND quarter IS NULL" — never a bound NULL
- Parameterised queries, everywhere. The LLM never writes SQL
- growth_comparison = FOUR queries, unpacked POSITIONALLY
- ALL arithmetic is Python: yoy · comparison · growth_comparison · cagr
- abs(prior) in the denominator — or a narrowing loss reads as a decline
- n_years = len(rows) - 1 — three points span two years
- Compute functions return an error DICT; they never raise
- sql_verified = computed.get("error") is None
- CAGR returns its own data_points, so the figure is CHECKABLE
```

## 15. MUST UNDERSTAND

```text
- Why the injection defence here is STRUCTURAL rather than defensive: there is
  nowhere for user input to become statement text
- Why validation and parameterisation defend against DIFFERENT problems
- Why doc_id in the SELECT is what makes sql_verified mean something
- Why a missing prior year is a fact about the corpus, not an exception
- Why five near-identical compile functions are preferred to one generic one
- Why an unenforced positional contract between two functions is a real risk
```

---

## 16. This connects to

```text
Day 32 — the DSL, validated
   ↓
Day 33 — compiled to SQL, computed in Python      ← you are here
   ↓
Day 34 — the guards, and what sql_verified does NOT guarantee
```

Forward references:

- `_format_quant_response` rendering these results → **Day 34**
- Row-count verification and `ambiguous_result` → **Day 34**
- `CAVEAT-013` and pooling → **Days 11, 45**
- The eval's `quantitative_*` scorers → **Day 43**
- `unit` and audit **F3** → **Day 31** (already read)
