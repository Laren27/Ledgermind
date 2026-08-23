# Day 22 — PDF → PageBlock: Parsing, Tables, OCR Damage

**Phase 6 · Weight: H (~120 min) · Prerequisites: Day 10**

**Textbook: 13.3–13.4 "Multimodal RAG — tables" — DIVERGES.** The textbook
recommends captioning tables into text, calling it "most common in production".
LedgerMind does the opposite, and today is why.

---

## 1. Today's goal

By tonight you can:

- Explain why a PDF is a *picture of a document*, not a document, and what that
  costs.
- Explain why tables are extracted **before** text, and what masking does.
- Explain **positional extraction**: recovering "this number belongs to that row
  and that period column" from x/y coordinates.
- Explain why LedgerMind rejects table captioning, and what that choice buys and
  costs.
- Explain `CAVEAT-003` — a page whose column layout fails to parse is skipped
  **silently** — and why that is the day's most important defect.

---

## 2. Why now

Days 20–21 covered vectors. Today goes back to the beginning: how a PDF becomes
anything at all. Days 23–24 then classify and chunk what this produces, and
Day 31 turns its tables into `financials` rows.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `PageBlock`, `RawTable` dataclasses | Day 10 | The output types |
| "never pass raw dicts between stages" | Day 10 | Why these are dataclasses |
| Parse once, reuse | Day 1 | Parsing twice exhausts WSL RAM |

---

## 4. Concept lesson

### 4.1 A PDF is a picture of a document

A PDF does not contain paragraphs, tables or rows. It contains **glyphs at
coordinates**: "draw the character '5' at x=412.3, y=688.1 in this font".

Everything else — that these glyphs form a word, that this word is a row label,
that this number belongs to the FY26 column — is **inferred from geometry**.

**Mental model.** A PDF is **a photograph of a spreadsheet**. The numbers are
visible; the grid is not stored, only drawn.

**What follows immediately:**

- "Extract the table" is a **layout reconstruction** problem, not a parsing one.
- Two visually identical PDFs can have entirely different internal structure.
- A number's meaning depends on **where it is**, and position is the only signal.

---

### 4.2 Tables before text, and masking

`pdf_parser.py`'s docstring states the sequence:

> - Tables extracted via pdfplumber's `extract_tables()`
> - Text extracted via `extract_text()` with layout preservation
> - Each page produces N `PageBlock`s: one per table + one text block for
>   remaining text
> - **Tables are extracted first; text blocks have table regions masked out**

**Why the order matters.** Without masking, every number in a table appears
**twice**: once inside the structured `RawTable`, and once in the page's flowing
text. Downstream that means:

- the chunker embeds table numbers as prose, producing chunks that retrieve on
  numeric queries and carry no structure;
- `financial_extractor` could see the same figure from two paths;
- and **`contradiction.py` would compare a table's own number against itself** —
  which is precisely the circularity BUG-003 suffered from (Day 37), where the
  engine flagged disagreement between a verified value and its own source.

**One page → N blocks:** one `PageBlock` per table (`block_type=TABLE`), plus one
for the remaining text (`block_type=TEXT`). Classification into
`FINANCIAL_STATEMENT`, `RISK_DISCLOSURE` and so on happens on Day 23.

---

### 4.3 Positional extraction

`extract_tables()` works when a PDF draws visible rules. Indian financial
statements frequently do not — columns are separated by **whitespace alignment
only**.

So `pdf_parser.py` has a second path:

```python
def extract_financials_positional(pdf_path, page_index, column_centers, tolerance=None)
def find_fully_populated_row_centers(pdf_path, page_index, num_cols, below_top=0.0, ...)
def detect_column_layout(pdf_path, page_idx)          # in financial_extractor.py
```

**The idea.** Every word has an x-centre. A financial table's period columns are
vertically aligned, so words belonging to the same column share an x-centre
within a tolerance. Find the centres, assign each number to its nearest centre,
and you have recovered the grid.

**The refinement**, from `financial_extractor._refine_centers_with_data_row`:

> Override header-derived centres with measurements from a real, **fully-populated
> data row**, when one exists on this page.

Header text ("Year ended March 31, 2026") is often wider than the numbers beneath
it, so a centre derived from the header can sit off to one side. A row where
**every** column has a value gives the true centres. **Measure the data, not the
label.**

---

### 4.4 Why not caption the tables?

The textbook (13.3) recommends:

> Strategy 2: Caption Everything to Text (Most Common in Production)

i.e. turn the table into a sentence — *"Revenue from operations was ₹54,364 crore
in FY26 and ₹20,243 crore in FY25"* — embed the sentence, retrieve it, and let the
LLM read the numbers back.

**LedgerMind rejects this**, and the reason is the system's core claim.

| Captioning | Positional extraction |
|---|---|
| The number becomes **text in a chunk** | The number becomes a **typed row in `financials`** |
| Retrieved and read by an LLM | Fetched by SQL and computed in Python |
| Verifiable only by a human re-reading the source | `sql_verified = True` means something |
| Robust to layout weirdness | Fragile — `CAVEAT-003` |
| One pipeline | Two, and a third path to reconcile them (Day 37) |

**The trade, stated plainly:** captioning is more robust and gives up exactness.
LedgerMind chose exactness and pays for it in extraction-correctness work —
roughly **two-thirds of all commits** (Day 2's history reading).

**And it is not either/or.** The prose *is* still chunked and embedded — Day 24 —
so narrative discussion of a figure is retrievable. What is not captioned is the
**statement table itself**, because that is where the authoritative number lives.

---

### 4.5 OCR damage is real, and the map is the evidence

```python
TYPO_MAP = {
    "Ill": "III", "ll": "II", "l": "I",
    "COSIS": "costs", "ofs tock": "of stock",
    "amonisation": "amortisation", "benefi1s": "benefits",
    "incomc": "income", "TotaI": "Total", "EmpIoyee": "Employee",
    "DeIivery": "Delivery", "reIated": "related", "saIes": "sales",
    "Advcniscmcnt": "Advertisement",
}
```

Every entry is a real string observed in a real filing. Three families:

| Family | Examples | Cause |
|---|---|---|
| **`l` ↔ `I` ↔ `1`** | `TotaI`, `EmpIoyee`, `benefi1s` | Visually near-identical glyphs |
| **`c` ↔ `e` ↔ `o`** | `incomc`, `COSIS`, `Advcniscmcnt` | Low-quality scan |
| **Spurious spaces** | `ofs tock` | Word-spacing misread |

**And the one that shows how deep this goes** — from `entity_resolver.py`:

```python
# Rejoins a first letter that the PDF typeset as its own text run.
# ZOMATO FY24's cash-flow and OCI tables render as "I nterest expense",
# "L oan given", "P ayment of principal portion" — pdfplumber faithfully
# reports the space. This MUST run before PREFIX_RE: after casefolding,
# a bare leading "i " or "l " is a legal roman numeral, so PREFIX_RE
# stripped it and produced metrics named `nterest_expense` / `oan_given`.
```

**A regex ordering constraint whose violation silently renamed metrics.** The
first letter was typeset as a separate text run; pdfplumber reported it
faithfully; a later cleanup rule mistook it for a roman-numeral list marker.

**This is the single best illustration of why extraction is hard here:** every
layer is individually correct, and the composition is wrong.

---

### 4.6 `clean_financial_number` and the comma problem

```python
def clean_financial_number(val): ...
```

Financial PDFs write numbers in ways a naive `float()` rejects:

| As printed | Means |
|---|---|
| `1,234.56` | 1234.56 — comma as thousands separator |
| `(710)` | **−710** — accounting negative |
| `-` | not applicable, **not zero** |
| `54,364` | 54364 |

The `-` case matters: a dash means *"this line item does not apply"*, and storing
`0.0` would be a claim the filing never made. `NOT_PRINTED` (a `_NotPrinted`
sentinel) exists to represent it.

**And the trap that is still open.** `CAVEAT-005` / audit **F3**:

> `unit` is hardcoded to crore, and **the number cleaner is calibrated to crore
> too.**

The comma-as-decimal rule assumes crore-scale magnitudes. A filing in millions
would be misread — and Day 13 established there is no negative case in the corpus
to test against.

---

## 5. The actual LedgerMind file

```
File:        backend/app/ingestion/pdf_parser.py (677 lines)
Purpose:     Extract raw content from a PDF → list[PageBlock]. Nothing else.
Why:         "No chunking, no classification, no metadata injection"
Who imports: section_classifier (via pipeline), financial_extractor,
             regression_check
Entry points: parse_pdf(path) -> list[PageBlock]
             extract_financials(pdf_path, page_index)
             extract_financials_positional(pdf_path, page_index, column_centers)
             find_fully_populated_row_centers(...)
             clean_financial_number(val)
Data in:     a PDF path
Data out:    list[PageBlock] — one per table, plus one text block per page
Downstream:  document_classifier reads .content
             section_classifier reads/writes .block_type
             financial_extractor reads .table
```

**Note the docstring's discipline:** *"Responsibility: extract raw content from a
PDF and return `List[PageBlock]`. **Nothing else.**"* Every other ingestion module
gets its own file. This is why the pipeline is readable at all.

---

## 6. Deep walkthrough

### 6.1 `parse_pdf`

```python
def parse_pdf(pdf_path: str) -> list:
    ...
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for t in tables:
                blocks.append(PageBlock(page_number=i+1, block_type=BlockType.TABLE,
                                        table=RawTable(rows=t), content=...))
            text = page.extract_text(...)
            blocks.append(PageBlock(page_number=i+1, block_type=BlockType.TEXT,
                                    content=text))
```

**STATE BEFORE.** A path. Nothing in memory.

**`with pdfplumber.open(...)`** — a context manager (Day 11), because a PDF holds
an open file handle and page objects hold references into it.

**Page numbers are 1-based** (`i+1`), because citations show them to users. Every
downstream consumer — `Citation.page_number`, the frontend's evidence list —
expects the number printed on the page.

**Encrypted or corrupt PDFs return an empty list with a clear error logged**, per
the docstring. **Not an exception** — one bad document should not abort a batch.
The consequence is that a zero-block parse looks identical to an empty document,
which is the same silent-failure shape as Day 14's zero rows.

**STATE AFTER.** A flat `list[PageBlock]`, ordered by page, tables first within
each page.

**And the rule from `CLAUDE.md` §7:**

> **Any script that parses a corpus PDF must parse it once and reuse the result.**
> Parsing twice exhausts WSL RAM and restarts the distro. Run `regression_check`
> once, tee to `/tmp`, grep the file.

Not a style note. It restarts your machine.

---

### 6.2 `_apply_typo_map` and ordering

```python
_VALUE_TOKEN_RE = re.compile(r"^[(/]?-?[\d,]*\.?\d+[)\\]?$|^-$")
MIN_VALUE_COLUMNS = 2  # a real financial data row always has at least 2 periods

def _apply_typo_map(text: str) -> str: ...
```

**`MIN_VALUE_COLUMNS = 2`** is a **semantic** filter, not a syntactic one. A real
financial statement row shows the current period *and* the comparative. A line
with one number is prose containing a figure, not a data row.

This is the same class of check as `check_balance_invariants.py` (Day 13): *"a
balance-sheet stock cannot be negative"*. **A claim about the domain doing work no
arithmetic guard could do.**

**`_VALUE_TOKEN_RE`** accepts: optional `(` or `/`, optional `-`, digits with
commas, optional decimal, optional `)` or `\` — **or** a bare `-`. The stray `/`
and `\` are OCR artefacts of parentheses.

---

### 6.3 `NOT_PRINTED` — a sentinel that is not `None`

```python
class _NotPrinted:
    ...
NOT_PRINTED = _NotPrinted()
```

Three states must be distinguishable:

| State | Meaning |
|---|---|
| `54364.0` | a value |
| `NOT_PRINTED` | the filing printed `-` — **not applicable** |
| `None` | we failed to read this cell |

**Why not `None` for both?** Because "the company reported nothing here" and "our
parser could not read this" are different facts, and only the second is a bug.
Collapsing them would make extraction defects invisible — you could never
distinguish a blank line item from a failure.

**Why not `0.0`?** Because zero is a claim the filing did not make. The
₹10,000 Cr incident (Day 2) is what happens when a fabricated value enters
derivation arithmetic.

---

### 6.4 `detect_column_layout` and the silent skip

The column-detection chain:

```
find_fully_populated_row_centers(pdf, page, num_cols)
        ↓  x-centres from a row where EVERY column has a value
_refine_centers_with_data_row(...)
        ↓  override header-derived centres
extract_financials_positional(pdf, page, column_centers, tolerance)
        ↓  assign each word to its nearest centre
rows: [(description, [values...]), ...]
```

**And here is `CAVEAT-003`**, in `financial_extractor.py`:

```python
try:
    column_map, column_centers = detect_column_layout(pdf_path, page_idx)
except Exception as e:
    continue          # `e` is bound and never used; nothing is logged
```

**A page of a financial statement that raises during column detection produces no
records and no log line.** The ingest completes, all gates pass, and the missing
rows look identical to rows the document never contained.

From the caveat:

> **Current impact:** Unknown by construction — **there is no signal to count.**
> This is a plausible contributor to audit **F6** (686 of 1437 rows unanchored)
> and to any "why is this metric missing?" investigation.
>
> **Proper solution:** `logger.warning("Column layout failed on page %s: %s",
> page_number, e)` before the `continue`. **One line; no behaviour change.**

**One line.** It is not fixed because this pass is documentation-only, and
because even a log line is a functional change requiring approval. But note the
shape: `e` is *bound and never used* — the author caught the exception intending
to handle it and stopped one line short.

---

## 7. Data flow

```
ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf
        │
        ▼  pdfplumber.open()
   pages: glyphs at coordinates
        │
        ├─── page.extract_tables()  ──► RawTable(rows=[[...], ...])
        │         │                         │
        │         ▼                         ▼
        │    PageBlock(TABLE, table=..., page_number=1-based)
        │
        └─── page.extract_text()  ──► text with table regions MASKED
                  │
                  ▼
             PageBlock(TEXT, content=...)
        │
        ▼  the SECOND path, for tables with no drawn rules
   find_fully_populated_row_centers()  →  x-centres
        │
        ▼  _refine_centers_with_data_row()   measure the DATA, not the header
   extract_financials_positional(centers, tolerance)
        │
        ├─ raises → `continue`  ← CAVEAT-003: NO LOG, NO RECORD, NO SIGNAL
        │
        ▼
   [(description, [v1, v2, ...]), ...]
        │
        ▼  clean_financial_number  +  _apply_typo_map
   1,234.56 → 1234.56 · (710) → -710.0 · "-" → NOT_PRINTED · TotaI → Total
        │
        ├──────────────► Day 23: classify the blocks
        └──────────────► Day 31: rows → FinancialRecord → financials
```

---

## 8. Engineering decision — positional extraction over captioning

**Problem.** Recover exact figures from statement tables in PDFs that frequently
have no drawn grid.

**Decision.** pdfplumber with word-level positions; tables first with text
masked; column centres measured from a fully-populated data row.

| Alternative | Why not |
|---|---|
| **Caption tables to text** (textbook 13.3) | Robust, and destroys the exact-value guarantee. The number becomes prose an LLM reads back |
| **Multimodal embeddings (CLIP)** (13.2) | Embeds the table as an image. Retrievable, not *queryable* — no SQL |
| **Commercial table extraction (AWS Textract, Azure Form Recogniser)** | Better accuracy; a paid API and a data-residency question on client filings |
| **`camelot` / `tabula`** | Both assume drawn rules or need Java. The no-rules case is the hard one here |
| **Hand-entered figures** | Accurate, does not scale, and defeats the point |

**Trade-offs accepted.**

- **Fragility.** Positional extraction breaks on unusual layouts, and
  `CAVEAT-003` means it breaks *silently*.
- **Cost.** Two-thirds of all commits are extraction correctness.
- **Corpus-specific calibration.** `TYPO_MAP` and the crore-calibrated number
  cleaner are tuned to these filings — audit **F3** is the named blocker for
  arbitrary documents.

**Current validity.** Correct for the stated goal. The immediate improvements are
one log line (`CAVEAT-003`) and unit detection (**F3**).

**At 10×.** Extraction is offline, so throughput is not the pressure. The pressure
is **layout diversity**: more issuers means more layouts, and the silent-skip
rate becomes the thing you cannot measure — which is why the log line matters
more than it looks.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| **A metric silently missing** | `CAVEAT-003` — column detection raised and `continue`d with no log |
| A metric named `nterest_expense` | `SPLIT_INITIAL_RE` ran after `PREFIX_RE` — ordering violation |
| A figure wrong by 10× | Audit **F3** — unit asserted as crore |
| A blank line item stored as `0.0` | `NOT_PRINTED` collapsed to zero |
| Values assigned to the wrong period | Column centres derived from the header, not a data row |
| A table's numbers appearing as prose | Masking failed — and `contradiction.py` may then compare a figure with itself |
| WSL restarts during a script | A PDF parsed twice |
| Zero blocks from a real PDF | Encrypted or corrupt — logged, returns `[]`, does not raise |

---

## 10. Hands-on experiment

> **Parse once per script.** `CLAUDE.md` §7.

### Experiment 1 — what a page actually contains

```bash
docker compose exec -T backend python -c "
from app.ingestion.pdf_parser import parse_pdf
from collections import Counter
blocks = parse_pdf('/app/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf')
print('total blocks:', len(blocks))
print('by type     :', Counter(b.block_type for b in blocks))
print('pages       :', len({b.page_number for b in blocks}))
print()
for b in blocks[:4]:
    print(f'p{b.page_number} {b.block_type:8} table={b.table is not None} '
          f'content={(b.content or \"\")[:70]!r}')
"
```

### Experiment 2 — a real table

```bash
docker compose exec -T backend python -c "
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.models import BlockType
blocks = parse_pdf('/app/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf')
tables = [b for b in blocks if b.block_type == BlockType.TABLE and b.table]
print('tables found:', len(tables))
t = tables[0]
print(f'page {t.page_number}, {len(t.table.rows)} rows')
for row in t.table.rows[:8]:
    print('  ', row)
"
```

### Experiment 3 — the number cleaner

```bash
docker compose exec -T backend python -c "
from app.ingestion.pdf_parser import clean_financial_number, NOT_PRINTED
for raw in ['1,234.56', '54,364', '(710)', '-', '', '12.5', '(1,234.56)', 'n/a']:
    v = clean_financial_number(raw)
    tag = 'NOT_PRINTED' if v is NOT_PRINTED else repr(v)
    print(f'  {raw!r:14} -> {tag}')
print()
print('\"-\" is NOT_PRINTED, not 0.0. Zero would be a claim the filing never made.')
"
```

### Experiment 4 — OCR damage, in the wild

```bash
docker compose exec -T backend python -c "
from app.ingestion.pdf_parser import TYPO_MAP, _apply_typo_map
print('TYPO_MAP entries:', len(TYPO_MAP))
for k, v in TYPO_MAP.items(): print(f'  {k!r:16} -> {v!r}')
print()
for s in ['TotaI incomc', 'EmpIoyee benefi1s expense', 'Advcniscmcnt and saIes promotion']:
    print(f'  {s!r}')
    print(f'    -> {_apply_typo_map(s)!r}')
"
```

Then find the deeper one:

```bash
docker compose exec -T backend python -c "
from app.ingestion.entity_resolver import SPLIT_INITIAL_RE, PREFIX_RE, normalize_metric_label
for raw in ['I nterest expense', 'L oan given', 'P ayment of principal portion']:
    print(f'{raw!r:34} -> {normalize_metric_label(raw)!r}')
print()
print('If PREFIX_RE ran first, a leading \"i \" reads as a roman numeral and')
print('these become nterest_expense / oan_given. The ORDER is load-bearing.')
"
```

### Experiment 5 — see CAVEAT-003's blind spot

```bash
docker compose exec -T backend sh -c "grep -n -B4 -A4 'column_map, column_centers = detect_column_layout' /app/app/ingestion/financial_extractor.py"
```

Look at the `except Exception as e: continue`. **`e` is bound and never used.**
Now ask: if this fired on ten pages of a filing, what in the ingest output would
tell you?

Nothing. That is the caveat.

### Experiment 6 — column centres, header vs data row

```bash
docker compose exec -T backend python -c "
from app.ingestion.pdf_parser import find_fully_populated_row_centers
p = '/app/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf'
for page in range(0, 6):
    try:
        c = find_fully_populated_row_centers(p, page, num_cols=2)
        if c: print(f'page {page+1}: centres {[round(x,1) for x in c]}')
    except Exception as e:
        print(f'page {page+1}: {type(e).__name__}: {e}')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/ingestion/pdf_parser.py`:

1. The docstring says the module's responsibility is extraction and "nothing
   else". Name three things it deliberately does **not** do, and which module does
   each.
2. Why are tables extracted before text, and what does masking prevent? Name a
   downstream engine that would be corrupted without it.
3. `MIN_VALUE_COLUMNS = 2`. What claim about the world does that encode?
4. Find `NOT_PRINTED`. Why is it not `None`, and why not `0.0`?
5. Find `_refine_centers_with_data_row` in `financial_extractor.py`. Why prefer a
   data row's centres over the header's?

---

## 12. Self-check questions

**Basic**
1. What does a PDF actually contain?
2. What does `parse_pdf` return?
3. Are page numbers 0- or 1-based, and why?
4. What is `TYPO_MAP` for?
5. What does a `-` in a financial table mean here?

**Code**
6. Why `with pdfplumber.open(...)`?
7. What happens on an encrypted PDF?
8. What does `_VALUE_TOKEN_RE` accept?
9. What are the three distinguishable cell states?
10. Which function finds column centres, and from what?

**Why**
11. Why tables before text?
12. Why reject captioning?
13. Why measure column centres from a data row rather than the header?
14. Why must a PDF be parsed only once per script?
15. Why is `MIN_VALUE_COLUMNS` a semantic rather than a syntactic filter?

**Debugging**
16. A metric present in the filing is absent from `financials`, with a green
    ingest. What happened, and what would tell you?
17. A stored metric is named `oan_given`. What went wrong?
18. A figure is exactly 10× too large. Which finding, and why is it untestable
    today?

**System design**
19. Write the fix for `CAVEAT-003` and say what it would let you measure.
20. A new issuer's filings use a layout the column detector cannot read. Options:
    caption those tables, or refuse the document. Argue one.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **No chunking** (`chunker.py`), **no classification** (`document_classifier.py`
   for sections, `section_classifier.py` for block types), **no metadata
   injection** (`chunker._build_metadata`). Also: no embedding, no database
   writes. Single responsibility is why the pipeline is readable.
2. Because `extract_text()` would otherwise return the table's numbers **as
   prose**, duplicating every figure. Masking removes the table regions from the
   text block. **Without it, `contradiction.py` (Day 37) would compare a
   statement's own number against itself** — the circularity BUG-003 suffered,
   where the top-cited chunk was the cash-flow statement the `financials` row was
   extracted from. Also: the chunker would embed table numbers as narrative,
   producing chunks that retrieve on numeric queries and carry no structure.
3. That **a real financial statement row shows at least two periods** — the
   current one and its comparative. A line with a single number is prose that
   happens to contain a figure. It is a claim about how financial statements are
   written, doing work no syntactic check could do — the same class as
   `check_balance_invariants.py`'s "a balance-sheet stock cannot be negative".
4. **Not `None`** because "the filing printed a dash — not applicable" and "our
   parser failed to read this cell" are different facts, and only the second is a
   defect. Collapsing them makes extraction failures invisible. **Not `0.0`**
   because zero is a claim the filing never made, and a fabricated value entering
   derivation arithmetic is exactly the ₹10,000 Cr incident.
5. Because header text ("Year ended March 31, 2026") is typically **wider** than
   the numbers beneath it, so a centre derived from the header sits off to one
   side and numbers get assigned to the wrong column. A row where every column has
   a value gives the true centres. **Measure the data, not the label.**

### §12 — Basic

1. Glyphs at coordinates, plus drawing instructions. **No paragraphs, no tables,
   no rows** — those are inferred from geometry.
2. `list[PageBlock]` — one per table plus one text block per page, ordered by
   page.
3. **1-based** (`page_number=i+1`), because citations show them to users and must
   match the number printed on the page.
4. Correcting consistent OCR artefacts observed in real filings — `l`/`I`/`1`
   confusion, `c`/`e`/`o` confusion, and spurious spaces.
5. **Not applicable** — represented by `NOT_PRINTED`, never `0.0`.

### §12 — Code

6. Because a PDF holds an open file handle and page objects reference into it;
   the context manager guarantees release however the block exits (Day 11).
7. It logs a clear error and returns an **empty list** — it does not raise, so one
   bad document cannot abort a batch. The cost is that a zero-block parse looks
   like an empty document.
8. Optional leading `(` or `/`, optional `-`, digits with commas, optional
   decimal, optional trailing `)` or `\` — **or** a bare `-`. The stray slashes
   are OCR artefacts of parentheses.
9. A number; `NOT_PRINTED` (the filing showed `-`); `None` (we failed to read it).
10. `find_fully_populated_row_centers` — from the **x-centres of words in a row
    where every column has a value**, refined by `_refine_centers_with_data_row`.

### §12 — Why

11. So the text block can have the table regions **masked out**, preventing every
    figure from appearing twice. See §11 Q2 for what that would corrupt.
12. Because captioning turns an authoritative figure into **prose an LLM reads
    back**, which destroys the exact-value guarantee. `sql_verified = True` means
    something only because a typed row was produced by a deterministic path.
13. See §11 Q5.
14. Because parsing twice **exhausts WSL RAM and restarts the distro**
    (`CLAUDE.md` §7). Run `regression_check` once, tee to `/tmp`, grep the file.
15. Because syntax cannot distinguish "a row of a financial statement" from "a
    sentence containing a number". Only a claim about the domain can — and that
    claim is that a statement row always carries a comparative period.

### §12 — Debugging

16. **`CAVEAT-003`**: `detect_column_layout` raised on that page and the handler
    `continue`d **without logging**. The ingest completes, gates pass, and the
    missing rows are indistinguishable from rows the document never contained.
    **What would tell you: nothing** — there is no signal to count. That is
    precisely why the caveat exists, and why the one-line fix matters more than
    its size suggests.
17. The PDF typeset the initial `L` of "Loan given" as a separate text run, so
    pdfplumber reported `"L oan given"`. `PREFIX_RE` ran **before**
    `SPLIT_INITIAL_RE` and treated the bare leading `l ` as a roman-numeral list
    marker, stripping it. The ordering constraint in `entity_resolver.py` exists
    for this, and it is stated only in a comment.
18. **Audit F3 / `CAVEAT-005`** — `unit` defaults to `'crore_inr'` and nothing
    detects scale, so a filing reporting in millions is stored as crore.
    **Untestable today** because every document in the corpus reports in crore, so
    there is no negative case: any detector would pass trivially and you would
    learn nothing (Day 13).

### §12 — System design

19. ```python
    except Exception as e:
        logger.warning(
            "Column layout detection failed | doc_id=%s page=%s error=%s: %s",
            doc_id, page_idx + 1, type(e).__name__, e,
        )
        continue
    ```
    **What it lets you measure:** a *rate*. Today the silent-skip count is
    unknown by construction; with the log you can count skips per document, per
    issuer and per layout, and answer "is extraction degrading as we add
    issuers?" — which is the question that becomes urgent at 10×. It also turns
    "why is this metric missing?" from an open-ended hunt into a grep. Note this
    is still a **functional change** (a new log line) and needs approval; and per
    `CLAUDE.md`, log **single-line with the identifiers**, because Render
    truncates multi-line output.
20. **Refuse — and say so.** Captioning that issuer's tables would put
    non-exact figures into the same `financials` table as exact ones, with
    nothing on the row distinguishing them, so `sql_verified = True` would stop
    meaning one thing. That is the failure this system exists to prevent: *a
    wrong answer with a ✓ tick is worse than a refusal.* The honest path is the
    one `quant_engine`'s guards already take (Day 34) — accept the document for
    the **semantic** path (its prose is still chunked and retrievable) and refuse
    the **quantitative** path for it, with a message naming the reason. If
    captioning is ever wanted, it must carry a `unit`/`provenance` marker on the
    row and a different verification claim — a schema change, not a parser
    change. (Counter-argument worth acknowledging: a captioned figure retrievable
    at low confidence may beat nothing at all — but only if the *response* says
    which it is, and today it could not.)

---

## 14. MUST REMEMBER

```text
- A PDF is a PICTURE of a document — glyphs at coordinates, no rows, no tables
- Tables are extracted FIRST; the text block has table regions MASKED OUT
- Page numbers are 1-BASED, because citations show them
- Column centres come from a FULLY-POPULATED DATA ROW, not the header
- "-" is NOT_PRINTED. Never None, never 0.0
- MIN_VALUE_COLUMNS = 2: a real statement row always shows a comparative
- SPLIT_INITIAL_RE MUST run before PREFIX_RE, or metrics get renamed silently
- PARSE ONCE PER SCRIPT — twice exhausts WSL RAM and restarts the distro
- CAVEAT-003: a page whose layout fails to parse is skipped with NO LOG
```

## 15. MUST UNDERSTAND

```text
- Why table extraction is layout RECONSTRUCTION, not parsing
- Why LedgerMind rejects the textbook's captioning advice, and exactly what
  that trade costs — two-thirds of all commits
- Why three cell states must stay distinguishable, and what collapsing them hides
- Why every layer can be individually correct and the composition still wrong
  ("I nterest expense" → nterest_expense)
- Why a missing log line is the day's most important defect: without it, the
  failure rate is unknown BY CONSTRUCTION
```

---

## 16. This connects to

```text
Day 21 — where vectors live
   ↓
Day 22 — how a PDF becomes anything at all       ← you are here
   ↓
Day 23 — classifying what came out: sections and block types
```

Forward references:

- `block_type` refinement → **Day 23**
- Chunking the text blocks → **Day 24**
- Tables → `FinancialRecord` → `financials` → **Day 31**
- `normalize_metric_label` and the regex ordering → **Day 31**
- Why masking protects contradiction detection → **Day 37**
- Audit **F3** (unit) and **F6** (unanchored metrics) → **Days 31, 43**
