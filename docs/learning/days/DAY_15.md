# Day 15 — Indexes, Locking, and the Restatement Model

**Phase 4 · Weight: H (~120 min) · Prerequisites: Day 14**

---

## 1. Today's goal

By tonight you can:

- Explain what an index is, and what a **partial unique index** enforces that a
  plain unique constraint cannot.
- Explain `SELECT … FOR UPDATE` and the exact race it prevents.
- Explain `IS NOT DISTINCT FROM` and why `quarter = NULL` silently matches
  nothing.
- Explain the difference between a **restatement** (the issuer revised a figure)
  and a **parser correction** (our reading of an unchanged filing was wrong) —
  and why conflating them would manufacture a filing history that never happened.
- Explain why `classify_upsert` is a **pure function**, and what the alternative
  cost.

---

## 2. Why now

Day 14 covered who may see a row. Today: how a row gets *written*, and how the
system records that a number changed without ever losing the old one. This is
the last piece of the data model before migrations (Day 16) and the whole
quantitative path (Days 31–34).

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `financials` columns, `is_latest` | Day 13 | Today is what `is_latest` is for |
| Transactions, `SET LOCAL` | Days 11, 14 | The upsert is one transaction |
| `Decimal` vs `float` comparison | Day 13 | `_stored_value_differs` |

---

## 4. Concept lesson

### 4.1 Indexes

**The problem.** `SELECT ... WHERE company='ETERNAL' AND metric='revenue'` on a
table with no index means a **sequential scan** — read every row. Fine at 1,400
rows; fatal at 10 million.

**An index** is a sorted structure (a B-tree) mapping column values to row
locations. Lookup goes from O(n) to O(log n).

**The cost, which is why you do not index everything:** every index must be
updated on every `INSERT`, `UPDATE` and `DELETE`, and each occupies disk. An
index is a **read optimisation paid for on writes**.

`init.sql` defines three:

```sql
CREATE INDEX idx_financials_lookup
    ON financials (company, fiscal_year, financial_type, metric, is_latest);
CREATE INDEX idx_documents_company
    ON documents (company, fiscal_year, financial_type, is_latest);
CREATE INDEX idx_audit_tenant_time
    ON audit_log (tenant_id, created_at DESC);
```

**Column order in a composite index is not arbitrary.** A B-tree on
`(a, b, c)` can serve `WHERE a=…`, `WHERE a=… AND b=…`, and
`WHERE a=… AND b=… AND c=…` — but **not** `WHERE b=…` alone. It is a phone book
sorted by surname then first name: useless for finding everyone called "James".

`idx_financials_lookup`'s order mirrors `_base_select`'s `WHERE` clause exactly
(Day 33). That is not coincidence — the index was built for that query.

`idx_audit_tenant_time` has `created_at DESC` because every audit query is
"the most recent N for this tenant".

---

### 4.2 The partial unique index — the invariant that holds this system together

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_financials_latest
    ON financials (tenant_id, company, fiscal_year, quarter, financial_type, metric)
    WHERE is_latest = TRUE;
```

**Read the `WHERE`.** The uniqueness applies **only to rows where
`is_latest = TRUE`**.

**What it enforces:** *at most one current value per business key.* You may store
a hundred historical revisions of ETERNAL FY26 revenue; **exactly one** may be
current.

**Why a plain `UNIQUE` cannot do this.** A plain unique constraint on those six
columns would forbid ever storing a second row for the same key — no history at
all. The system's whole restatement model depends on keeping the old rows.

**Mental model.** A **noticeboard with one pin per topic**. Old notices are
archived, never destroyed; only one may be pinned up.

**The failure it prevents is not theoretical.** From `classify_upsert`:

> without retirement duplicate `is_latest=TRUE` rows accumulate silently
> (root cause of a **142-row duplicate incident** during Phase 3 finalization
> testing).

And the downstream symptom is `quant_engine`'s `ambiguous_result` (Day 34):
`point_in_time` expects exactly one row, and two means the filter or the data is
broken.

**There is a second partial index**, referenced in `db_loader`'s SQL:
`uq_financials_per_doc_coalesce`, on `(doc_id, metric, fiscal_year,
financial_type, quarter)`. That one makes **re-ingesting the same document**
idempotent — the `ON CONFLICT DO NOTHING` target.

Two indexes, two different invariants:

| Index | Enforces |
|---|---|
| `uq_financials_latest` (WHERE is_latest) | one **current** value per business key |
| `uq_financials_per_doc_coalesce` | one row per **metric per document** |

---

### 4.3 `IS NOT DISTINCT FROM`

In SQL, `NULL` means *unknown*, and comparisons with unknown are unknown:

```sql
SELECT NULL = NULL;              -- NULL, not TRUE
SELECT 'Q1' = NULL;              -- NULL
```

A `WHERE` clause treats `NULL` as false, so `WHERE quarter = NULL` **matches
nothing, ever** — silently.

`financials.quarter` is `NULL` for annual figures (Day 13). So the lookup that
finds an existing annual row must use:

```sql
AND quarter IS NOT DISTINCT FROM %(quarter)s
```

`IS NOT DISTINCT FROM` is `=` that treats two `NULL`s as equal.

**What breaks with plain `=`.** `_SQL_LOCK_LATEST` finds no existing row for any
annual metric. `classify_upsert` sees `existing_doc_id is None` and returns
`"inserted"`. A **new** `is_latest=TRUE` row is written beside the old one — and
the partial unique index rejects it. In the best case you get a constraint
violation; in a version without the index, you get the 142-row incident.

**Note the asymmetry with `_base_select`** (Day 33), which writes:

```python
if dsl["quarter"] is not None:
    sql += "  AND quarter = %s\n"
else:
    sql += "  AND quarter IS NULL\n"
```

Two different solutions to the same NULL problem: the loader uses
`IS NOT DISTINCT FROM` (one statement, either case); the compiler **branches** and
emits `IS NULL` literally. Both correct. The compiler branches because it is
building SQL text anyway and an explicit `IS NULL` is easier to read in a logged
query.

---

### 4.4 `SELECT … FOR UPDATE`

```sql
SELECT id, filing_date, doc_id, value
FROM financials
WHERE ... AND is_latest = TRUE
FOR UPDATE
```

`FOR UPDATE` takes a **row-level write lock**. Another transaction attempting to
lock the same row **blocks** until this one commits or rolls back.

**The race without it:**

```
T1: SELECT existing is_latest row  → finds row X
T2: SELECT existing is_latest row  → finds row X          (same row!)
T1: UPDATE X SET is_latest = FALSE
T2: UPDATE X SET is_latest = FALSE                        (no-op)
T1: INSERT new row (is_latest=TRUE)
T2: INSERT new row (is_latest=TRUE)   ← TWO current rows
```

With `FOR UPDATE`, T2 blocks at its `SELECT` until T1 commits, then sees the
already-retired row and takes a different branch.

**Mental model.** `FOR UPDATE` is **taking the book off the shelf**. Anyone else
who wants it waits at the desk rather than reading a copy that is about to
change.

**When can this actually happen here?** Two Celery workers ingesting documents
that share a business key, or an ingest racing a backfill. Rare — and the cost of
being wrong is a silent duplicate, so the lock is cheap insurance.

---

### 4.5 Restatement versus parser correction

**This is the conceptual heart of the day.**

| | Restatement | Parser correction |
|---|---|---|
| What happened | **The issuer** published a revised figure | **We** misread an unchanged filing |
| The document | A *new* filing, new `doc_id` | The *same* filing, same `doc_id` |
| What changed | The world | Our software |
| Correct record | Retire the old row, insert a new one | **Update the value in place** |
| `is_latest` | moves | **untouched** |
| Opt-in? | no — always | **yes**, `correct_values=True` |

`_SQL_CORRECT_VALUE`'s comment makes the argument:

> **WHY THIS IS AN UPDATE AND NOT A RESTATEMENT.** `is_latest` / retirement / a
> new row all encode a claim about the **FILING's** history: the issuer published
> a revised figure. Nothing of the sort happened here. The filing never changed;
> our READING of it did, because the parser was fixed. Recording a parser
> correction through the restatement machinery would **manufacture a filing
> history that does not exist** — a retired "original" row the issuer never
> filed, sitting in the audit trail as though it had. So: value only. `is_latest`,
> `doc_id`, `filing_date`, version lineage and `created_at` are all left
> untouched.

**"Manufacture a filing history that does not exist."** In a system whose product
is an auditable record, writing a row implying the issuer revised something they
never revised is not a cosmetic error — it is a false statement in the permanent
record.

**And why it is opt-in.** Overwriting a stored value is destructive. The default
path (`skipped`) is safe: a re-ingest of the same document changes nothing.
`correct_values=True` is reserved for the deliberate case *"I fixed the parser and
I want the corpus re-read."*

**The failure that motivated it**, from `classify_upsert`:

> without `correct_values` that difference is discarded by `ON CONFLICT DO
> NOTHING`, which is how a misread **7,292** survived an `--apply` backfill
> against a corrected parser.

That is the README's ₹10,000 Cr OCR case (Day 2's history reading): the parser
was fixed, the backfill ran green, and the wrong figure stayed — because
"same document, nothing to do" was true of the *document* and false of *our
reading of it*.

---

## 5. The actual LedgerMind file

### `backend/app/ingestion/db_loader.py`

```
File:        backend/app/ingestion/db_loader.py (743 lines)
Purpose:     Write FinancialRecord objects to `financials` with correct
             restatement semantics
Why it exists: The retire/insert/skip/correct decision is subtle and must
             happen in exactly one place
Who imports it: ingestion/pipeline.py, and several scripts/
What it imports: psycopg2, FinancialRecord
Entry points: load_financial_records(), classify_upsert(), verify_financials()
Data in:     a list of FinancialRecord + a connection
Data out:    a summary dict: inserted / restated / reingested / corrected /
             skipped / errors
Connection:  the CALLER owns the lifecycle. db_loader does not open or close.
```

---

## 6. Deep walkthrough

### 6.1 `classify_upsert` — one decision, in one place

```python
def classify_upsert(*, existing_doc_id, existing_value, existing_filing_date,
                    record: FinancialRecord, correct_values: bool = False) -> str:
    """THE single decision. Returns what a write WOULD do, and writes nothing.

    Pure: no cursor, no I/O, no side effects.
    """
    if existing_doc_id is None:
        return "inserted"

    try:
        new_date = date.fromisoformat(record.filing_date)
    except (ValueError, TypeError):
        return "skipped"                      # refuse rather than guess an ordering

    if str(existing_doc_id) == str(record.doc_id):
        if correct_values and _stored_value_differs(existing_value, record.value):
            return "corrected"
        return "skipped"

    if new_date < existing_filing_date:
        return "skipped"                      # would regress to a stale filing

    return "restated" if new_date > existing_filing_date else "reingested"
```

**Five branches. Walk each:**

| Condition | Label | Meaning |
|---|---|---|
| no existing `is_latest` row | `inserted` | first time this business key is seen |
| unparseable `filing_date` | `skipped` | **refuse rather than guess an ordering** |
| same `doc_id`, value unchanged (or not opted in) | `skipped` | the same document replayed |
| same `doc_id`, value differs, `correct_values` | `corrected` | the parser was fixed |
| different `doc_id`, older date | `skipped` | would regress to a stale filing |
| different `doc_id`, newer date | `restated` | the issuer revised |
| different `doc_id`, same date | `reingested` | both retire the old row |

**Why `restated` and `reingested` are distinct labels for the same action.**
Both retire and insert. They are separated so the **summary** tells the truth: a
run reporting "12 restated" means twelve issuer revisions, which is a real event
worth noticing; "12 reingested" means a re-run.

**Why the docstring insists on purity** — this is the design argument:

> `_upsert_one` used to decide and act in the same pass, so anything that wanted
> to know what a run WOULD do had to re-implement the branch order by hand. A
> hand-written mirror is a copy that drifts silently: it agrees on the day it is
> written and diverges at the first change to either side, and the whole value of
> a preview is that it tells the truth about the writer. … There is one decision,
> in one place, exercised by both.
>
> **Adding a branch here changes the writer and the preview together. That is the
> point.**

Same principle as the metric registry (Day 10): the fix for two copies is not
"keep them in sync", it is **one copy with two callers**.

And purity has a second payoff: `classify_upsert` is testable in the
zero-network pytest suite (Day 43). A function that needed a cursor could not be.

---

### 6.2 `_stored_value_differs` — the Decimal trap, again

```python
def _stored_value_differs(existing, new) -> bool:
    """Compared as FLOATS, deliberately, and not as Decimals. ...
    Decimal.__eq__ converts the float to its EXACT binary expansion, so
    Decimal("33.33") == 33.33 is False -- the stored figure and the one that
    produced it would compare as different, and every such row would be
    "corrected" to itself on every run.
    """
    if existing is None or new is None:
        return (existing is None) != (new is None)
    return float(existing) != float(new)
```

**The question this function answers** is not "are these mathematically equal?"
but **"would writing this record change what is stored?"** Framed that way, float
comparison is not a compromise — it is the correct comparison, because `float()`
of the stored `Decimal` round-trips to the same float the parser produced.

`(existing is None) != (new is None)` is XOR: differ if exactly one is `None`.

---

### 6.3 `_upsert_one` — acting on the label

```python
def _upsert_one(cursor, record, correct_values=False) -> str:
    cursor.execute(_SQL_LOCK_LATEST, {...})       # FOR UPDATE
    row = cursor.fetchone()

    existing_id = existing_doc_id = existing_value = existing_filing_date = None
    if row:
        existing_id, existing_filing_date, existing_doc_id, existing_value = row

    action = classify_upsert(                     # ← DECIDE
        existing_doc_id=existing_doc_id,
        existing_value=existing_value,
        existing_filing_date=existing_filing_date,
        record=record, correct_values=correct_values,
    )

    if action == "skipped":                       # ← ACT
        return "skipped"
    if action == "corrected":
        cursor.execute(_SQL_CORRECT_VALUE, {...})
        return "corrected"
    if action in ("restated", "reingested"):
        cursor.execute(_SQL_RETIRE_LATEST, {"existing_id": existing_id})
    cursor.execute(_SQL_INSERT_SAFE, {...})
    return action
```

**STATE BEFORE.** Inside a transaction with `app.tenant_id` set. Possibly an
existing `is_latest` row.

**Step 1 — lock.** `_SQL_LOCK_LATEST` with `FOR UPDATE`. From this moment no
other transaction can touch that row.

**Step 2 — decide.** A pure call. No I/O.

**Step 3 — act.** Note `restated` and `reingested` **fall through** to the shared
`INSERT` after retiring. Only `skipped` and `corrected` return early.

**STATE AFTER.** Exactly one `is_latest=TRUE` row for the business key, and the
partial unique index has verified it.

**Why the decide/act split matters operationally.** `purge_orphaned_metrics.py`
and the dry-run previews call `classify_upsert` with rows read by a plain
`SELECT` (`_SQL_PEEK_LATEST` — the same predicate **without** `FOR UPDATE`, kept
beside its twin "so the two predicates cannot drift"). The preview and the writer
cannot disagree, because they are the same function.

---

### 6.4 `ON CONFLICT DO NOTHING` — the last line of defence

```sql
INSERT INTO financials (...) VALUES (...)
ON CONFLICT DO NOTHING
RETURNING id
```

Without it, re-ingesting a document raises a unique violation and **aborts the
whole transaction** — losing every record in the batch.

With it, a duplicate insert is a silent no-op and the batch continues.

**`RETURNING id`** is how the caller learns which happened: a row means inserted,
`None` means the conflict fired.

**And this is exactly where the ₹10,000 Cr bug hid.** `ON CONFLICT DO NOTHING` is
correct for "the same document replayed" and **wrong** for "the same document,
re-read by a fixed parser" — because in the second case the value genuinely
changed and the conflict silently discards it. `correct_values` exists to reach
past it.

---

### 6.5 `load_financial_records` — the batch, and its transaction shape

The loader's docstring states the sequence:

```
1. Set app.tenant_id on the connection before any DML (RLS enforcement)
2. For each record, run a transaction that:
   a. Locks existing is_latest=TRUE rows (SELECT FOR UPDATE)
   b. If a newer filing exists already: skip insert
   c. If an older filing exists: flip it to is_latest=FALSE
   d. Insert new row ON CONFLICT DO NOTHING
3. Return a summary dict
```

**Per record, not per batch.** One failing record does not roll back the other
1,400. The trade: a partially-loaded corpus is possible, and the summary's
`errors` count is what tells you.

**`SET app.tenant_id` here is a plain `SET`, not `SET LOCAL`** — deliberate
(Day 11). This is a batch job that owns its connection for its whole life and is
never pooled, so session scope is correct and avoids re-setting per record.

---

## 7. Data flow — one record

```
FinancialRecord(company='ETERNAL', metric='revenue', fiscal_year='FY26',
                quarter=None, financial_type='consolidated',
                value=54364.0, doc_id=<uuid>, filing_date='2026-05-15')
        │
        ▼
_SQL_LOCK_LATEST  … quarter IS NOT DISTINCT FROM NULL … FOR UPDATE
        │
        ├─ no row ────────────────────────► classify → "inserted"
        │
        └─ row (id, filing_date, doc_id, value)
                 │
                 ▼
           classify_upsert()   ← PURE. Also called by the dry-run preview.
                 │
     ┌───────────┼────────────┬──────────────┬──────────────┐
     ▼           ▼            ▼              ▼              ▼
 "skipped"  "corrected"  "restated"    "reingested"    "inserted"
     │           │            │              │              │
   return    UPDATE value  RETIRE old     RETIRE old        │
             (is_latest     ─────┬────────────┘             │
              untouched)         ▼                          │
                          INSERT new (is_latest=TRUE) ◄──────┘
                                 │
                                 ▼
                    uq_financials_latest verifies:
                    exactly ONE current row per business key
```

---

## 8. Engineering decision — retire, never overwrite

**Problem.** A figure can change for two different reasons, and the record must
tell them apart forever.

**Decision.** Restatements retire the old row and insert a new one; parser
corrections update the value in place, opt-in.

`ENGINEERING_DECISIONS.md` **ED-018**.

| Alternative | Why not |
|---|---|
| **`UPDATE` in place for everything** | Destroys history. "What did the filing say in May?" becomes unanswerable, and the audit trail loses its point |
| **A separate `financials_history` table** | Two tables to keep consistent; every query needs a union or a join |
| **Append-only with no `is_latest`** | Every read needs a correlated subquery for the max `filing_date`. `_base_select` becomes far more expensive, on the hot path |
| **Treat corrections as restatements** | Manufactures a filing history that never happened |

**Trade-offs accepted.**

- **The table grows.** Old rows are never removed — and there is no `DELETE`
  grant to remove them with (Day 13).
- **`is_latest` must be maintained correctly.** Get it wrong and you get zero
  current rows or two. Hence the partial index **and** `FOR UPDATE`.
- **`correct_values` is destructive.** Off by default; the previous value is not
  preserved anywhere.

**Current validity.** Sound, and well defended. The gap: a parser correction
leaves **no record that it happened** beyond the loader's summary. If you want to
know later why a stored figure differs from what the original ingest produced,
nothing tells you.

**At 10×.** Partitioning by `fiscal_year`, and a retention or archival policy for
retired rows — which, per Day 13, means detaching partitions rather than granting
`DELETE`.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `duplicate key ... uq_financials_latest` | Two `is_latest` rows attempted — the index doing its job |
| `ambiguous_result` from `quant_engine` | Two `is_latest` rows already exist |
| Every annual metric re-inserted | `quarter = NULL` instead of `IS NOT DISTINCT FROM` |
| A corrected parser's fix does not land | `correct_values=False` — `ON CONFLICT DO NOTHING` discarded it |
| Every row reports "corrected" every run | `Decimal` vs `float` compared directly |
| Two current rows after concurrent ingests | `FOR UPDATE` missing |
| A metric stuck `is_latest=TRUE` forever | Extraction stopped emitting that name — orphan, see below |
| A whole batch lost on one bad record | Transaction scoped per batch instead of per record |

**The orphan case is a standing maintenance obligation**, from `CLAUDE.md` §9:

> The loader retires rows by full business key **including `metric`**, so a name
> it stops emitting is never retired and stays `is_latest = TRUE` forever.
> Orphans are a maintenance obligation of extraction changes, not a loader bug.

Hence `purge_orphaned_metrics.py` after **any** extraction change — dry run
first, always.

---

## 10. Hands-on experiment

`export T=<alpha tenant uuid>` as on Day 14.

### Experiment 1 — `IS NOT DISTINCT FROM`

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); c.autocommit=True; cur=c.cursor()
for expr in ['NULL = NULL', 'NULL IS NOT DISTINCT FROM NULL',
             \"'Q1' = NULL\", \"'Q1' IS NOT DISTINCT FROM NULL\"]:
    cur.execute(f'SELECT {expr}')
    print(f'{expr:38} -> {cur.fetchone()[0]}')
print()
print('WHERE treats NULL as false. So `quarter = NULL` matches NOTHING.')
c.close()"
```

### Experiment 2 — the partial unique index refuses a second current row

```bash
docker compose exec -T -e T="$T" backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SET app.tenant_id = %s', (os.getenv('T'),))
cur.execute('''SELECT doc_id, company, fiscal_year, quarter, financial_type, metric, value
               FROM financials WHERE is_latest LIMIT 1''')
r = cur.fetchone(); print('existing current row:', r[1], r[5], r[2])
try:
    cur.execute('''INSERT INTO financials
        (tenant_id, doc_id, company, fiscal_year, quarter, financial_type,
         metric, value, unit, filing_date, is_latest)
        VALUES (%s,%s,%s,%s,%s,%s,%s,999,'crore_inr','2026-01-01',TRUE)''',
        (os.getenv('T'), r[0], r[1], r[2], r[3], r[4], r[5]))
except psycopg2.errors.UniqueViolation as e:
    print()
    print('REFUSED:', str(e).split(chr(10))[0])
    print('  <- at most ONE is_latest row per business key')
c.close()"
```

### Experiment 3 — `classify_upsert` is pure, so you can drive it directly

```bash
docker compose exec -T backend python -c "
from datetime import date
from app.ingestion.db_loader import classify_upsert
from app.ingestion.models import FinancialRecord
import inspect

def rec(doc_id, value, filing_date):
    f = FinancialRecord.__dataclass_fields__
    kw = {k: None for k in f}
    kw.update(dict(company='ETERNAL', metric='revenue', fiscal_year='FY26',
                   quarter=None, financial_type='consolidated',
                   value=value, doc_id=doc_id, filing_date=filing_date))
    return FinancialRecord(**{k: v for k, v in kw.items() if k in f})

D1, D2 = 'doc-aaa', 'doc-bbb'
cases = [
  ('no existing row',            None, None,    None,                 rec(D1, 100.0,'2026-05-15'), False),
  ('same doc, same value',       D1,   100.0,   date(2026,5,15),      rec(D1, 100.0,'2026-05-15'), True),
  ('same doc, NEW value, opt-in',D1,   90.0,    date(2026,5,15),      rec(D1, 100.0,'2026-05-15'), True),
  ('same doc, NEW value, no opt',D1,   90.0,    date(2026,5,15),      rec(D1, 100.0,'2026-05-15'), False),
  ('new doc, NEWER filing',      D1,   90.0,    date(2026,5,15),      rec(D2, 100.0,'2026-08-01'), False),
  ('new doc, SAME date',         D1,   90.0,    date(2026,5,15),      rec(D2, 100.0,'2026-05-15'), False),
  ('new doc, OLDER filing',      D1,   90.0,    date(2026,8,1),       rec(D2, 100.0,'2026-05-15'), False),
  ('unparseable filing_date',    D1,   90.0,    date(2026,5,15),      rec(D2, 100.0,'not-a-date'), False),
]
for label, dq, val, fd, r, cv in cases:
    out = classify_upsert(existing_doc_id=dq, existing_value=val,
                          existing_filing_date=fd, record=r, correct_values=cv)
    print(f'{label:30} -> {out}')
" 2>&1 | tail -12
```

**Eight cases, no database.** That is what "pure" buys.

### Experiment 4 — the Decimal trap that would "correct" every row

```bash
docker compose exec -T backend python -c "
from decimal import Decimal
from app.ingestion.db_loader import _stored_value_differs
stored, parsed = Decimal('33.33'), 33.33
print('Decimal == float directly :', stored == parsed, ' <- naive, and WRONG')
print('_stored_value_differs     :', _stored_value_differs(stored, parsed))
print()
print('If this returned True, every such row would be corrected to itself,')
print('on every run, forever.')
"
```

### Experiment 5 — `FOR UPDATE` blocking, observed

Terminal A:

```bash
docker compose exec -T -e T="$T" backend python -c "
import psycopg2, os, time
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SET app.tenant_id = %s', (os.getenv('T'),))
cur.execute('SELECT id FROM financials WHERE is_latest LIMIT 1 FOR UPDATE')
print('A: locked', cur.fetchone()[0], '- holding for 10s')
time.sleep(10)
c.rollback(); c.close(); print('A: released')"
```

Terminal B, immediately:

```bash
docker compose exec -T -e T="$T" backend python -c "
import psycopg2, os, time
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SET app.tenant_id = %s', (os.getenv('T'),))
t=time.perf_counter()
cur.execute('SELECT id FROM financials WHERE is_latest LIMIT 1 FOR UPDATE')
print(f'B: acquired after {time.perf_counter()-t:.1f}s  <- it WAITED')
c.rollback(); c.close()"
```

### Experiment 6 — read the history a restatement leaves

```bash
docker compose exec -T -e T="$T" backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SET app.tenant_id = %s', (os.getenv('T'),))
cur.execute('''SELECT company, metric, fiscal_year, count(*) AS versions,
                      count(*) FILTER (WHERE is_latest) AS current
               FROM financials GROUP BY 1,2,3 HAVING count(*) > 1
               ORDER BY versions DESC LIMIT 10''')
rows = cur.fetchall()
if rows:
    for r in rows: print(f'  {r[0]:10} {r[1]:26} {r[2]:6} versions={r[3]} current={r[4]}')
else:
    print('  no multi-version rows in this database')
print()
print('current must be 1 for every row. The partial index guarantees it.')
c.close()"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/ingestion/db_loader.py`:

1. `_SQL_LOCK_LATEST` and `_SQL_PEEK_LATEST` are identical but for one clause.
   Which, and why do both exist *beside each other*?
2. `classify_upsert` returns `"skipped"` for an unparseable `filing_date`. Why
   not raise?
3. `restated` and `reingested` cause identical writes. Why are they separate
   labels?
4. Find the comment explaining why a parser correction is an `UPDATE`. State the
   argument in your own words.
5. `load_financial_records` uses a plain `SET app.tenant_id`, not `SET LOCAL`. Is
   that a bug? Justify from the function's usage.

---

## 12. Self-check questions

**Basic**
1. What does an index cost?
2. What does a partial unique index enforce here?
3. What does `IS NOT DISTINCT FROM` do?
4. What does `FOR UPDATE` do?
5. What are the five outcomes of `classify_upsert`?

**Code**
6. Which columns are in `uq_financials_latest`, and what is its `WHERE`?
7. What does `ON CONFLICT DO NOTHING` prevent, and what does it hide?
8. How does `_upsert_one` learn whether the insert happened?
9. Why compare values as floats?
10. What is the transaction scope in `load_financial_records`?

**Why**
11. Why retire rather than overwrite?
12. Why is a parser correction not a restatement?
13. Why is `correct_values` opt-in?
14. Why is `classify_upsert` pure?
15. Why does the loader need `FOR UPDATE` at all?

**Debugging**
16. A corrected parser's fix does not appear in the database, and the run is
    green. What happened?
17. `ambiguous_result` on a `point_in_time` query. What is wrong in the data, and
    what should have prevented it?
18. After an extraction change, a metric nobody emits any more still answers
    queries. What is it, and what is the procedure?

**System design**
19. A parser correction leaves no record that it happened. Design the smallest
    change that fixes that without breaking the restatement model.
20. `financials` grows without bound and there is no `DELETE` grant. Design
    archival that preserves the audit chain.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `_SQL_LOCK_LATEST` has **`FOR UPDATE`**; `_SQL_PEEK_LATEST` does not. Both
   exist because the dry-run preview must classify what a run *would* do while
   **taking no row locks and holding no write transaction**. They are kept
   adjacent, with a comment saying so, "so the two predicates cannot drift" — if
   the peek's `WHERE` diverged from the lock's, the preview would classify a
   different row than the writer acts on.
2. Because an unparseable date makes the **ordering question unanswerable**, and
   every remaining branch depends on comparing dates. Raising would abort the
   record (and, if the transaction were batch-scoped, the batch). `"skipped"`
   refuses to guess, leaves the existing data untouched, and shows up in the
   summary where a human can see it.
3. So the **summary tells the truth**. Both retire and insert, but "12 restated"
   means twelve issuer revisions — a real event worth investigating — while "12
   reingested" means someone re-ran an ingest. Collapsing them would hide a
   signal in noise.
4. `is_latest`, retirement and a new row all encode a claim about **the filing's**
   history: that the issuer published a revision. A parser fix means the filing
   never changed — *our reading of it* did. Routing that through the restatement
   machinery would create a retired "original" row the issuer never filed, sitting
   in the audit trail as though it had: **a false statement in the permanent
   record** of a system whose product is an auditable record.
5. **Not a bug.** `load_financial_records` is a batch job that owns its
   connection for its entire life and is never pooled or handed to another
   request, so session scope is correct — and it avoids re-executing `SET LOCAL`
   for every one of ~1,400 records. The `SET LOCAL` rule applies where a
   connection could be **reused by a different request**, which is the HTTP path.

### §12 — Basic

1. Storage, plus an update on every `INSERT`/`UPDATE`/`DELETE`. It is a read
   optimisation paid for on writes.
2. At most **one `is_latest = TRUE` row** per `(tenant_id, company, fiscal_year,
   quarter, financial_type, metric)` — while allowing unlimited historical rows.
3. `=` that treats two `NULL`s as equal, so it matches annual rows where
   `quarter IS NULL`.
4. Takes a row-level write lock; another transaction trying to lock the same row
   blocks until this one commits or rolls back.
5. `inserted`, `corrected`, `skipped`, `restated`, `reingested`.

### §12 — Code

6. `(tenant_id, company, fiscal_year, quarter, financial_type, metric)`
   `WHERE is_latest = TRUE`.
7. **Prevents:** a unique violation aborting the whole transaction on a re-ingest.
   **Hides:** a genuinely changed value when the same document is re-read by a
   fixed parser — which is how a misread 7,292 survived an `--apply` backfill.
8. `RETURNING id` — a row means the insert happened, `None` means the conflict
   fired.
9. Because psycopg2 returns `NUMERIC` as `Decimal` while the parser produces a
   `float`, and `Decimal.__eq__` compares against the float's **exact binary
   expansion** — so `Decimal("33.33") == 33.33` is `False`. The question being
   asked is "would writing this change what is stored?", and `float()` of the
   stored `Decimal` round-trips to the same float.
10. **Per record**, not per batch — so one bad record does not roll back the other
    1,400. The trade is that a partial load is possible, which the summary's
    `errors` count surfaces.

### §12 — Why

11. Because overwriting destroys history, and "what did the filing say in May?"
    is a question this system must be able to answer. Retirement keeps every
    version while `is_latest` keeps reads cheap.
12. Because nothing about the **filing** changed — only our software. Recording it
    as a restatement manufactures a filing history that does not exist.
13. Because it is **destructive**: it overwrites a stored value with no record of
    the previous one. The default (`skipped`) is safe, and `correct_values=True`
    is reserved for the deliberate "I fixed the parser, re-read the corpus" case.
14. So the **writer and the dry-run preview are the same decision**. A
    hand-written preview is a copy that agrees on the day it is written and
    drifts at the first change. Purity also makes it testable in the
    zero-network unit suite.
15. To prevent two concurrent ingests both finding the same `is_latest` row,
    both retiring it, and both inserting — producing two current rows. Rare, and
    the failure is a silent duplicate, so the lock is cheap insurance.

### §12 — Debugging

16. `correct_values` was `False` (the default). `classify_upsert` saw the same
    `doc_id` and returned `"skipped"`; even had it inserted, `ON CONFLICT DO
    NOTHING` would have discarded it. The run is green because **nothing failed** —
    the loader did exactly what it was told. This is the ₹10,000 Cr case, and the
    deeper lesson is the README's: *a green gate that validates the producer, not
    the store*. `regression_check` asserts on extraction output **in memory** and
    passed 4/4 while 28 stored figures were stale.
17. **Two `is_latest = TRUE` rows** exist for the business key.
    `uq_financials_latest` should have prevented it — so either the index is
    missing in that database (check `pg_indexes`), or the rows predate it, or
    they were written by a path that bypassed the loader. `FOR UPDATE` prevents
    the concurrent-write route to it.
18. An **orphaned metric**. The loader retires by full business key *including
    `metric`*, so a name extraction stops emitting is never retired and stays
    `is_latest = TRUE` forever. Procedure (`CLAUDE.md` §9): run
    `purge_orphaned_metrics.py` **dry run** after any extraction change, print the
    full candidate list, and stop. Before any deletion, every candidate must be
    verified as either paired at an identical value with a surviving row, or a
    component summing into a preserved total. `--apply` is a STOP-AND-ASK
    operation.

### §12 — System design

19. Add a nullable `corrected_at TIMESTAMPTZ` and `corrected_from NUMERIC` to
    `financials`, written only by `_SQL_CORRECT_VALUE`. The restatement model is
    untouched — `is_latest`, `doc_id`, `filing_date` and lineage still mean
    exactly what they meant, and the new columns are `NULL` on every row that was
    not corrected, so they cannot be mistaken for filing history. Cost: a
    migration, and two columns that are `NULL` for the overwhelming majority of
    rows. **Why this shape rather than a separate table:** the fact belongs to the
    row it describes, and a side table would need the same join discipline the
    restatement model was designed to avoid.
20. Partition `financials` by `fiscal_year` (or by `is_latest`), and **detach**
    partitions of retired rows rather than deleting them — a detached partition
    can be archived to cold storage or dropped **by the owner role**, never by
    `ledgermind_app`. **What preserves the audit chain:** `audit_log` stores
    `retrieved_chunk_ids` and `sql_executed`, not `financials.id`, so archiving
    old *retired* rows does not orphan any audit reference — but archiving a row
    still referenced by a `documents` row would, so `documents` must be archived
    in step with it, or the foreign key retained. Never archive `is_latest` rows.
    And never solve this by granting `DELETE`: that would trade a bounded storage
    problem for an unbounded integrity one.

---

## 14. MUST REMEMBER

```text
- uq_financials_latest: UNIQUE (business key) WHERE is_latest = TRUE
  → at most ONE current value, unlimited history
- quarter IS NULL means annual → the loader needs IS NOT DISTINCT FROM
- `quarter = NULL` matches NOTHING, silently
- FOR UPDATE prevents two ingests both retiring and both inserting
- RESTATEMENT (issuer revised, new doc_id) → retire + insert
- PARSER CORRECTION (same doc_id, we misread) → UPDATE value only, OPT-IN
- classify_upsert is PURE: the writer and the dry-run preview share one decision
- Compare stored vs parsed as FLOATS, never Decimal vs float directly
- Orphaned metrics are a maintenance obligation of extraction changes
```

## 15. MUST UNDERSTAND

```text
- Why conflating a correction with a restatement manufactures a filing history
  that never happened — a false statement in the permanent record
- Why a partial index is the only shape that allows history AND one current row
- Why "same document, nothing to do" was true of the DOCUMENT and false of our
  READING of it — and cost ₹10,000 Cr
- Why purity is what makes a dry run trustworthy: one decision, two callers
- Why a green gate can validate the producer while the store stays wrong
```

---

## 16. This connects to

```text
Day 14 — who may see which rows
   ↓
Day 15 — how a row is written, and how history is kept   ← you are here
   ↓
Day 16 — how the schema itself changes
```

Forward references:

- `_base_select` reading `is_latest = TRUE` → **Day 33**
- `ambiguous_result` when two current rows exist → **Day 34**
- `FinancialRecord` production → **Day 31**
- `regression_check` vs the store → **Day 43**
- `purge_orphaned_metrics` → **Day 43**
- Restatement disclosure and the confidence cap → **Day 30**
