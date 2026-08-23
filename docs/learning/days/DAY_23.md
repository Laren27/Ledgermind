# Day 23 — Classification by Three-Signal Intersection

**Phase 6 · Weight: M (~90 min) · Prerequisites: Day 22**

**Textbook: 10.1 "Metadata Filtering" — EXTENDS.** The textbook explains why
metadata matters at query time. Today is where the metadata *comes from* — and
it is not supplied by a user.

---

## 1. Today's goal

By tonight you can:

- Explain why **one PDF produces two `documents` rows**, and how their checksums
  stay distinct.
- Explain the **three-signal intersection** — structure ∧ location ∧ content —
  and give a concrete false positive that each signal alone would produce.
- Explain why `financial_type` is detected from **content**, never from a
  filename or a form field, and which blueprint trap that closes.
- Explain what happens when no standalone marker is found, and why the answer is
  `needs_review` rather than a default.

---

## 2. Why now

Day 22 produced `PageBlock`s tagged only `TEXT` or `TABLE`. Those two labels are
not enough: the chunker needs to know a risk disclosure from a balance sheet
(Day 24), and `financial_extractor` needs to target statement tables specifically
(Day 31). Today assigns the real labels.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `PageBlock`, `DocSection`, `BlockType` | Days 10, 22 | The types being refined |
| Blocks are refined **in place** | Day 10 | `classify_blocks` mutates the list |
| `sha256_checksum UNIQUE` | Day 13 | Two rows, one PDF |
| `derive_doc_id(checksum)` | Day 16 | Deterministic ids |

---

## 4. Concept lesson

### 4.1 Two documents inside one PDF

An Indian quarterly result publishes **both**:

- **Consolidated** — the parent plus every subsidiary.
- **Standalone** — the parent alone.

They are **different numbers for the same metric and period**, printed in the
same PDF, usually one after the other.

If you ingest that PDF as one document, a query for "Eternal FY26 revenue"
retrieves both and has no way to tell them apart. Worse, `financials` would hold
two `is_latest = TRUE` rows for one business key — which the partial unique index
(Day 15) would reject, or which would produce `ambiguous_result` (Day 34).

**So one PDF becomes two `documents` rows**, each with a page range and a
`financial_type`. `document_classifier.py`'s docstring:

> **Responsibilities:** … Emit two `DocSection` objects with correct page ranges
> … Write two rows to `documents` table (one per section)

**And the checksum problem this creates.** `documents.sha256_checksum` is `UNIQUE`
(Day 13), and both rows come from one file. The resolution:

```python
def section_checksum(file_sha256: str, financial_type: str) -> str:
    ...
```

> SHA256 stored as `{file_sha256}_{financial_type}` to allow two `documents` rows
> from one PDF **while still catching duplicate uploads**.

Both properties preserved: two rows are legal, and re-uploading the same PDF still
collides on both.

---

### 4.2 Trap 1 — never classify from a filename

`api/documents.py`'s docstring states it:

> `financial_type` is **NOT** collected here — it is auto-detected per-section
> from document content inside `pipeline._run_ingestion` … per the **Trap 1 fix
> (classify from content, never from filename or user input)**.

**Why.** A filename is a claim by whoever uploaded the file. Content is evidence.
If an admin uploads `eternal_q4_consolidated.pdf` and the file actually contains
both sections — or the standalone one — every downstream figure is misattributed,
silently, and looks correct.

Notice the upload form (Day 41) *does* collect `company`, `ticker`,
`fiscal_year`, `doc_type` and `filing_date` from the user. **`financial_type` is
the one field deliberately withheld** — because it is the one that changes what
a number *means*.

That asymmetry is itself worth noticing: it is not "never trust the user", it is
"do not let a user assert the field that silently reinterprets every value". The
other fields are recorded and are also unchecked — which is audit finding **F4**
(metadata is caller-asserted and never verified against the document), still open.

---

### 4.3 The markers

```python
STANDALONE_MARKERS = [...]
CONSOLIDATED_MARKERS = [...]
```

with the comment:

> Strict SEBI markers to avoid triggering on Press Release prose. We demand
> "statement of" or auditor report language to ensure we are …

**The failure being avoided:** a press release saying *"our standalone performance
was strong"* is prose, not a section boundary. A loose marker would split the
document at the wrong page and misattribute every figure after it.

**So the markers demand structural language** — "Statement of Standalone Financial
Results", auditor's report phrasing — not the bare word "standalone".

**Mental model.** The marker is **a chapter heading**, not a mention of the
chapter's topic.

---

### 4.4 The three-signal intersection

This is the day's central idea, and `section_classifier.py`'s docstring makes the
argument better than a paraphrase:

> **Why intersection matters:**
> - "Revenue from operations" appears in MD&A prose (**content alone → false
>   positive**)
> - A table on page 7 contains financial-ish words (**content + structure → still
>   wrong**)
> - Only a **TABLE** block, **inside a DocSection page range**, with **financial
>   keywords** is unambiguously a financial statement. **All three must align.**

| Signal | Source | Alone, it gets wrong |
|---|---|---|
| **Structure** | `block_type == TABLE` from pdfplumber | A segment table, a shareholding table |
| **Location** | the page falls in a `DocSection` range | Any table anywhere |
| **Content** | ≥2 financial keywords | MD&A prose discussing revenue |

**Why intersection and not a score.** A weighted score would let a strong content
signal outvote a wrong structure signal — and produce a `FINANCIAL_STATEMENT`
label on a paragraph. Intersection makes each signal a **veto**, and a veto cannot
be outvoted.

**And the consequence for `financial_extractor`**, from the docstring:

> This structural tagging is what makes `financial_extractor.py` robust — it
> targets `block_type == FINANCIAL_STATEMENT`, **not floating text anchors.**

The extractor never searches for the words "Revenue from operations". It asks for
blocks already proven to be statement tables. **The classification is what makes
the extraction targeted rather than heuristic.**

---

### 4.5 The thresholds, and why they differ

```python
FINANCIAL_STATEMENT_MIN_KEYWORDS = 2
RISK_MIN_KEYWORDS                = 2
MANAGEMENT_DISCUSSION_MIN_KEYWORDS = 2
AUDITOR_MIN_KEYWORDS             = 1        # ← different
```

**Why 2 for financial statements**, from the comment:

> Using 2 prevents false positives from related-party or segment tables that
> contain one financial word but are not P&L / balance sheet tables.

**Why 1 for auditor reports.** An auditor's report has highly distinctive
vocabulary — "unmodified opinion", "Companies Act, 2013", "ICAI" — where a single
hit is already strong evidence. Requiring two would miss short reports.

**The general shape:** the threshold is set by how *discriminating* the vocabulary
is, not by a uniform rule.

**And the exclusion lists:**

```python
NOTES_EXCLUSION_PHRASES = {...}
AUDITOR_REPORT_EXCLUSION_PHRASES = {...}
```

Positive keywords say what a thing *is*; exclusion phrases say what it is
**not**. Notes to accounts are full of financial vocabulary and are not
statements. A **positive-only** classifier would swallow them.

**Continuation windows:**

```python
ANCHOR_HEADING_CHARS = 400
CONTINUATION_MAX_PAGES = 4
AUDITOR_CONTINUATION_MAX_PAGES = 6   # auditor reports commonly run 3-8 pages
```

A statement's title appears **once**, on its first page; pages 2 and 3 carry the
table with no heading. So once an anchor is found, the classifier propagates the
label forward for a bounded number of pages.

**Why bounded.** Unbounded propagation would label the rest of the document as a
financial statement. The bound is different per type because auditor reports are
genuinely longer — again, a constant set by the domain rather than by symmetry.

**These are audit finding F9** — constants fitted to the current corpus. They work
for these five documents; a filing with a seven-page balance-sheet section would
break the four-page window silently.

---

### 4.6 The failure that is *not* a default

> If no marker found: logs a warning, creates **ONE consolidated-only section**
> and sets `needs_review=True` — **never silently defaults to wrong
> `financial_type`.**

Three separate behaviours in one sentence:

1. **Log** — the event is observable.
2. **Assume consolidated** — the more common case, so the document is still usable.
3. **`needs_review=True`** — the assumption is *recorded as an assumption*.

**This is the same pattern as `period_assumed`** in `quant_engine` (Day 34): when
the system substitutes a value the user did not supply, it **marks the
substitution** rather than hiding it. And the same pattern as `applied_at = NULL`
in `schema_migrations` (Day 16): do not assert what you did not observe.

**Three subsystems, one principle.**

---

## 5. The actual LedgerMind files

```
File:        backend/app/ingestion/document_classifier.py (383 lines)
Purpose:     Find section boundaries; register two documents rows
Entry points: detect_sections(blocks) -> list[DocSection]      ← PURE
             register_sections(...)                            ← owns all DB writes
             classify_and_register(...)
             compute_pdf_checksum(path) · derive_doc_id(checksum)
             section_checksum(file_sha256, financial_type)
Data in:     list[PageBlock]
Data out:    list[DocSection] with doc_ids populated

File:        backend/app/ingestion/section_classifier.py (576 lines)
Purpose:     Refine PageBlock.block_type using structure ∧ location ∧ content
Entry points: classify_blocks(blocks, sections) -> same list, refined IN PLACE
             get_blocks_by_type(blocks, block_type)
Data in:     list[PageBlock] (TEXT/TABLE) + list[DocSection]
Data out:    the SAME list, block_type refined
Pure:        no DB, no file I/O — "fully testable without infrastructure"
```

**Note the split in `document_classifier`:**

> `detect_sections()` is **pure** — no DB, fully testable without a connection.
> `register_sections()` owns **all** DB writes.

The same decide/act separation as `classify_upsert` (Day 15). It is why
`conftest.py` has a `make_block` fixture and `test_document_classifier.py` can
run in the zero-network suite.

---

## 6. Deep walkthrough

### 6.1 `detect_sections` — pure, and testable

```python
def detect_sections(blocks: list[PageBlock]) -> list[DocSection]:
```

**STATE BEFORE.** A flat list of `PageBlock`s, `block_type` ∈ {TEXT, TABLE}.

**Step 1 — scan for a standalone marker.** Walk blocks in page order, testing
each against `STANDALONE_MARKERS`.

**Step 2 — split.**

| Found? | Result |
|---|---|
| Yes, at page *N* | Two sections: consolidated `[1, N-1]`, standalone `[N, end]` |
| No | One consolidated section over the whole document, `needs_review=True` |

**Step 3 — return `DocSection` objects** with `financial_type`, `page_start`,
`page_end`, and `doc_id` still empty.

**STATE AFTER.** One or two `DocSection`s. **Nothing written anywhere.**

**Why pure matters here.** `conftest.py`'s `make_block` fixture builds
`PageBlock`s from literals:

> `PageBlock` is a plain dataclass — `page_number`, `content`, `block_type` — so a
> document layout can be expressed as a literal without parsing a PDF. **This is
> what makes `detect_sections` testable here at all**; the rest of the ingestion
> path takes parser output and does not have that property.

You can write a thirteen-block synthetic document and assert the split. You cannot
do that for `financial_extractor`.

---

### 6.2 `register_sections` — the DB half

```python
_SQL_SET_TENANT = "SET app.tenant_id = %s"
_SQL_INSERT_DOCUMENT = """..."""

def register_sections(...) -> list[DocSection]:
```

**`SET app.tenant_id` — a plain `SET`, not `SET LOCAL`.** Day 11's rule: this is
a batch job owning its connection, never pooled.

**One `INSERT` per section**, each with:

- `doc_id = derive_doc_id(section_checksum(file_sha256, financial_type))` —
  **deterministic** (Day 16), so re-ingesting the same PDF yields the same ids;
- `sha256_checksum = section_checksum(...)` — distinct per section, so `UNIQUE`
  holds;
- `ingestion_state = 'uploaded'`, moved later by the pipeline (Day 13).

**STATE AFTER.** Two `documents` rows, and the `DocSection` objects now carry
their `doc_id` — which every chunk and every `FinancialRecord` will reference.

---

### 6.3 `classify_blocks` — the intersection, implemented

```python
def _build_page_to_section(sections: list[DocSection]) -> dict[int, DocSection]:
    """Page number → the DocSection containing it."""

def _classify_table_block(block, section, ...) -> str:
    # STRUCTURE: it is already a TABLE (caller guarantees)
    # LOCATION : `section` is not None
    # CONTENT  : _count_keyword_matches(content_lower, FINANCIAL_KEYWORDS)
    #            >= FINANCIAL_STATEMENT_MIN_KEYWORDS
    #            AND no NOTES_EXCLUSION_PHRASES hit

def _classify_text_block(block) -> str:
    # RISK_DISCLOSURE / MANAGEMENT_DISCUSSION / AUDITOR_REPORT / TEXT
```

**`_build_page_to_section` first.** A dict lookup per block instead of a linear
scan of sections — the **location** signal made O(1).

**Tables and text take different paths**, because the signals differ. A text block
can never be a `FINANCIAL_STATEMENT` (it is not a table); a table is rarely a
`RISK_DISCLOSURE`.

**`ALL_FINANCIAL_SIGNALS = FINANCIAL_KEYWORDS | FINANCIAL_KEYWORD_TYPOS`** — the
keyword set **includes its own OCR-damaged variants**. Day 22's `TYPO_MAP` fixes
what it knows; this covers what slipped through. Two layers against the same
noise, at different stages.

**The MD&A special cases:**

```python
MD_LETTERED_HEADER_RE   = re.compile(...)   # "A. Financial performance"
MD_VARIANCE_NARRATIVE_RE = re.compile(...)  # "increased by 12% primarily due to"
MD_VARIANCE_SUPPORT_KEYWORDS = {"revenue", "expenses", "income", "results", "cost"}
```

Management discussion has **recognisable prose shapes** — lettered headings, and
variance narrative ("X increased by Y% primarily due to Z"). Structural patterns,
not vocabulary. And `MD_VARIANCE_SUPPORT_KEYWORDS` means a variance sentence must
*also* be about a financial subject — **another intersection**, at a smaller
scale.

**In place, by design:**

> Same list with `block_type` and metadata refined in-place. No new objects
> created — downstream modules read the updated list.

Day 10 covered why `PageBlock` is not frozen: freezing would force a copy of
every block on every refinement, for thousands of blocks, on a 512 MB tier.

---

## 7. Data flow

```
list[PageBlock]  (TEXT | TABLE)          ← Day 22
        │
        ├──────────────────────────────────────┐
        ▼                                      │
detect_sections(blocks)          PURE          │
        │  scan for STANDALONE_MARKERS         │
        ▼                                      │
  ┌─ found at page N ─► [DocSection(consolidated, 1..N-1),
  │                      DocSection(standalone,  N..end)]
  └─ not found ───────► [DocSection(consolidated, 1..end, needs_review=True)]
        │                                      │
        ▼                                      │
compute_pdf_checksum(path) ─► section_checksum(sha, ftype)                       
        │                       ─► derive_doc_id(...)                            
        ▼                                      │
register_sections()   DB WRITES ONLY           │
        │  SET app.tenant_id                   │
        │  INSERT INTO documents × 2           │
        ▼                                      │
[DocSection with doc_id populated] ────────────┤
                                               ▼
                        classify_blocks(blocks, sections)
                                               │
                        page → section  (location)
                        block_type      (structure)
                        keyword counts  (content)
                                ALL THREE
                                               │
                                               ▼
                     the SAME list, block_type refined IN PLACE
                     FINANCIAL_STATEMENT | RISK_DISCLOSURE |
                     MANAGEMENT_DISCUSSION | AUDITOR_REPORT | TABLE | TEXT
                                               │
                        ┌──────────────────────┴──────────────────┐
                        ▼                                         ▼
              chunker (Day 24)                    financial_extractor (Day 31)
              per-block-type targets              targets FINANCIAL_STATEMENT only
```

---

## 8. Engineering decision — intersection, not scoring

**Problem.** Label blocks reliably enough that extraction can target statement
tables and chunking can size by content type.

**Decision.** Require structure ∧ location ∧ content simultaneously, with
per-type thresholds and exclusion lists.

| Alternative | Why not |
|---|---|
| **Content keywords only** | "Revenue from operations" appears in MD&A prose. The docstring's first named false positive |
| **A weighted score** | A strong content signal could outvote a wrong structure signal. Intersection makes each a **veto** |
| **An ML classifier** | Training data would be these five documents; a model fitted to five documents is F9 with extra steps, and unexplainable |
| **An LLM per block** | Thousands of blocks per document × a 500/day quota. And it would be non-deterministic where determinism is available |
| **Trust the uploader** | Trap 1. A filename is a claim; content is evidence |

**Trade-offs accepted.**

- **Constants fitted to the current corpus** — audit **F9**. `CONTINUATION_MAX_PAGES
  = 4` works for these filings and would silently truncate a longer section.
- **Exclusion lists are enumerated**, so an unlisted phrase is not excluded.
- **Sections are assumed contiguous** — consolidated then standalone. A document
  interleaving them would be split wrongly.
- **`needs_review` is set and nothing consumes it.** It is recorded and there is
  no queue, no dashboard, no alert. The honest state is: *observable if you look,
  invisible if you do not.*

**Current validity.** Sound. The exposure is F9, and it grows with corpus
diversity rather than corpus size.

**At 10×** (in issuers, not documents): more layouts, more marker phrasings,
more continuation lengths. The right response is to **measure the classification
distribution per document** — which `regression_check` already does (Day 43),
checking "did a classifier change help one document while silently breaking
another?"

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| Standalone figures stored as consolidated | No marker found; defaulted to consolidated. **`needs_review=True` — if anyone looks** |
| Split at the wrong page | A marker matched press-release prose |
| A statement's later pages unclassified | Section longer than `CONTINUATION_MAX_PAGES` — **audit F9** |
| Notes to accounts labelled `FINANCIAL_STATEMENT` | An exclusion phrase not in the list |
| MD&A prose labelled financial | Content signal alone — the intersection prevents it |
| `financial_extractor` finds nothing | No block reached `FINANCIAL_STATEMENT` |
| A second `documents` row rejected | `section_checksum` not applied — plain file hash collides |
| Metadata wrong but plausible | Audit **F4** — caller-asserted, never verified |

---

## 10. Hands-on experiment

### Experiment 1 — `detect_sections` with no PDF at all

```bash
docker compose exec -T backend python -c "
from app.ingestion.models import PageBlock, BlockType
from app.ingestion.document_classifier import detect_sections

def blk(p, text, bt=BlockType.TEXT):
    return PageBlock(page_number=p, content=text, block_type=bt)

doc = [blk(1,'Consolidated Financial Results for the quarter ended March 31, 2026'),
       blk(2,'Revenue from operations 54,364'),
       blk(3,'Total expenses 51,200'),
       blk(4,'Statement of Standalone Financial Results for the quarter ended'),
       blk(5,'Revenue from operations 12,100')]
for s in detect_sections(doc):
    print(f'  {s.financial_type:14} pages {s.page_start}-{s.page_end} '
          f'needs_review={getattr(s, \"needs_review\", None)}')
print()
print('no standalone marker:')
for s in detect_sections(doc[:3]):
    print(f'  {s.financial_type:14} pages {s.page_start}-{s.page_end} '
          f'needs_review={getattr(s, \"needs_review\", None)}')
"
```

**A synthetic document, no PDF, no database.** That is what "pure" buys.

### Experiment 2 — the intersection, one signal at a time

```bash
docker compose exec -T backend python -c "
from app.ingestion.models import PageBlock, BlockType
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks
from collections import Counter

FIN = 'Revenue from operations 54,364 Total expenses 51,200 Profit before tax 3,164'
def blk(p, text, bt): return PageBlock(page_number=p, content=text, block_type=bt)

for label, b in [
    ('TABLE + in-section + keywords', blk(2, FIN, BlockType.TABLE)),
    ('TEXT  + in-section + keywords', blk(2, FIN, BlockType.TEXT)),
    ('TABLE + in-section + 1 keyword', blk(2, 'Segment revenue by geography', BlockType.TABLE)),
]:
    doc = [blk(1,'Consolidated Financial Results', BlockType.TEXT), b]
    secs = detect_sections(doc)
    classify_blocks(doc, secs)
    print(f'  {label:34} -> {doc[1].block_type}')
print()
print('Same content, three outcomes. Structure and count are VETOES.')
"
```

### Experiment 3 — real distribution

```bash
docker compose exec -T backend python -c "
from collections import Counter
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks
p = '/app/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf'
blocks = parse_pdf(p)                      # parse ONCE
print('before:', Counter(b.block_type for b in blocks))
secs = detect_sections(blocks)
for s in secs: print(f'  section {s.financial_type:14} pages {s.page_start}-{s.page_end}')
classify_blocks(blocks, secs)
print('after :', Counter(b.block_type for b in blocks))
"
```

### Experiment 4 — the thresholds

```bash
docker compose exec -T backend python -c "
import app.ingestion.section_classifier as sc
print('FINANCIAL_STATEMENT_MIN_KEYWORDS  :', sc.FINANCIAL_STATEMENT_MIN_KEYWORDS)
print('RISK_MIN_KEYWORDS                 :', sc.RISK_MIN_KEYWORDS)
print('MANAGEMENT_DISCUSSION_MIN_KEYWORDS:', sc.MANAGEMENT_DISCUSSION_MIN_KEYWORDS)
print('AUDITOR_MIN_KEYWORDS              :', sc.AUDITOR_MIN_KEYWORDS, '  <- 1, not 2')
print('CONTINUATION_MAX_PAGES            :', sc.CONTINUATION_MAX_PAGES)
print('AUDITOR_CONTINUATION_MAX_PAGES    :', sc.AUDITOR_CONTINUATION_MAX_PAGES)
print()
print('financial keywords:', len(sc.FINANCIAL_KEYWORDS))
print('OCR typo variants :', len(sc.FINANCIAL_KEYWORD_TYPOS))
print('notes exclusions  :', len(sc.NOTES_EXCLUSION_PHRASES))
print()
print('Thresholds differ because vocabularies differ in DISCRIMINATING POWER.')
print('These are audit F9: fitted to the current corpus.')
"
```

### Experiment 5 — two rows, one file

```bash
docker compose exec -T backend python -c "
from app.ingestion.document_classifier import compute_pdf_checksum, section_checksum, derive_doc_id
p = '/app/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf'
sha = compute_pdf_checksum(p)
print('file sha256 :', sha[:32], '...')
for ft in ('consolidated', 'standalone'):
    ck = section_checksum(sha, ft)
    print(f'  {ft:14} checksum={ck[:40]}...  doc_id={derive_doc_id(ck)}')
print()
print('One file, two DISTINCT checksums -> UNIQUE holds, duplicates still caught.')
"
```

### Experiment 6 — exclusion phrases doing work

```bash
docker compose exec -T backend python -c "
import app.ingestion.section_classifier as sc
print('NOTES_EXCLUSION_PHRASES:')
for p in sorted(sc.NOTES_EXCLUSION_PHRASES): print('  ', p)
print()
print('AUDITOR_REPORT_EXCLUSION_PHRASES:')
for p in sorted(sc.AUDITOR_REPORT_EXCLUSION_PHRASES): print('  ', p)
print()
print('Positive keywords say what a thing IS. These say what it is NOT.')
print('A positive-only classifier would swallow the notes to accounts.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/ingestion/document_classifier.py` and
`backend/app/ingestion/section_classifier.py`:

1. `detect_sections` is pure and `register_sections` owns the DB writes. Name the
   other place in this codebase with the same split, and what both gain from it.
2. Why is `AUDITOR_MIN_KEYWORDS` 1 when the others are 2?
3. Find `ALL_FINANCIAL_SIGNALS`. Why does a keyword set contain OCR-damaged
   variants when Day 22 already has a `TYPO_MAP`?
4. What happens when no standalone marker is found? Name the three separate
   behaviours, and the other subsystem that uses the same pattern.
5. `classify_blocks` refines block types **in place**. Why not return a new list?

---

## 12. Self-check questions

**Basic**
1. Why does one PDF produce two `documents` rows?
2. What are the three signals?
3. Where does `financial_type` come from?
4. What is `needs_review` for?
5. What does `classify_blocks` return?

**Code**
6. What does `section_checksum` compute, and why?
7. What does `_build_page_to_section` provide?
8. Which block types can a TEXT block become?
9. What is `MD_VARIANCE_NARRATIVE_RE` for?
10. What are `CONTINUATION_MAX_PAGES` and its auditor variant?

**Why**
11. Why intersection rather than a weighted score?
12. Why must the markers be structural rather than the bare word "standalone"?
13. Why 2 keywords for financial statements?
14. Why are exclusion phrases needed at all?
15. Why is `financial_type` withheld from the upload form when other metadata is
    not?

**Debugging**
16. `financial_extractor` produces zero records from a filing with obvious tables.
    What do you check first?
17. Standalone figures are stored as consolidated. What happened, and what would
    have told you?
18. A statement's later pages are unclassified. Which finding, and why is it
    corpus-dependent?

**System design**
19. A new issuer publishes consolidated and standalone **interleaved** rather than
    sequentially. What breaks, and what would you change?
20. `needs_review` is set and nothing consumes it. Design the smallest change that
    makes it act, and say what it must not do.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **`classify_upsert` / `_upsert_one`** in `db_loader.py` (Day 15) — the pure
   decision separated from the acting writer. Both gain: **testability** (the pure
   half runs in the zero-network pytest suite with no database) and **one
   decision with two callers**, so a dry run or a synthetic test cannot drift from
   the real path.
2. Because auditor-report vocabulary is far more **discriminating** — "unmodified
   opinion", "Companies Act, 2013", "ICAI" essentially do not appear outside an
   auditor's report. A single hit is already strong evidence, and requiring two
   would miss short reports. The threshold is set by the vocabulary's power, not
   by a uniform rule.
3. Because `TYPO_MAP` fixes the artefacts it **knows about**, and classification
   runs on text that may still carry ones it does not. Two layers against the same
   noise at different stages: repair what you can name, and match defensively for
   what you cannot. Missing a `FINANCIAL_STATEMENT` label because the OCR
   mangled one keyword would silently drop a whole table from extraction.
4. **(a)** Log a warning — the event is observable. **(b)** Create one
   consolidated-only section — the more common case, so the document remains
   usable. **(c)** Set `needs_review=True` — the assumption is recorded *as* an
   assumption. The same pattern is `period_assumed` in `quant_engine` (Day 34),
   and `applied_at = NULL` in `schema_migrations` (Day 16): **never assert what
   you did not observe.**
5. Because a document produces thousands of blocks and copying the whole list on
   every refinement is real memory on a 512 MB tier. It is also why `PageBlock` is
   **not** a frozen dataclass while `MetricDefinition` is (Day 10) — data in
   flight versus schema.

### §12 — Basic

1. Because an Indian quarterly result publishes **consolidated** (parent +
   subsidiaries) and **standalone** (parent only) in the same PDF — different
   numbers for the same metric and period. One row would make them
   indistinguishable and would produce two `is_latest = TRUE` rows for one
   business key.
2. **Structure** (`block_type == TABLE`), **location** (the page falls inside a
   `DocSection` range), **content** (≥ N keywords, no exclusion phrase).
3. **Detected from document content** — never from a filename or a form field.
   Blueprint Trap 1.
4. Recording that a section boundary was **assumed** rather than found, so the
   assumption is visible instead of silent.
5. **The same list**, with `block_type` refined in place.

### §12 — Code

6. `{file_sha256}_{financial_type}` — so one PDF can produce two `documents` rows
   under a `UNIQUE` checksum column while a genuine duplicate upload still
   collides.
7. A `dict[int, DocSection]` — page number → containing section, making the
   **location** signal an O(1) lookup instead of a linear scan per block.
8. `RISK_DISCLOSURE`, `MANAGEMENT_DISCUSSION`, auditor-report, or remain `TEXT`.
   **Never `FINANCIAL_STATEMENT`** — that requires the structure signal.
9. Matching **variance narrative** — "X increased by 12% primarily due to Y" — a
   structural prose shape characteristic of MD&A. It is gated by
   `MD_VARIANCE_SUPPORT_KEYWORDS` so the sentence must also be about a financial
   subject: a smaller intersection inside the larger one.
10. Bounds on how many pages a label propagates forward from its anchor heading.
    4 for most sections, **6** for auditor reports, which "commonly run 3–8
    pages".

### §12 — Why

11. Because a weighted score lets a strong signal **outvote** a wrong one — a
    paragraph with heavy financial vocabulary could score as a statement.
    Intersection makes each signal a **veto**, and a veto cannot be outvoted.
12. Because a press release saying "our standalone performance was strong" is
    prose, not a section boundary. A loose marker would split the document at the
    wrong page and misattribute every figure after it.
13. To exclude related-party and segment tables, which contain **one** financial
    word without being P&L or balance-sheet tables.
14. Because notes to accounts are full of financial vocabulary and are not
    statements. Positive keywords say what a thing is; exclusions say what it is
    not, and a positive-only classifier swallows the notes.
15. Because `financial_type` is the one field that **silently reinterprets every
    value** in the document. The other fields are recorded facts about the filing;
    this one changes what a number *means*. (Note the others are also unverified —
    audit **F4**, still open.)

### §12 — Debugging

16. Whether any block reached `FINANCIAL_STATEMENT`. Run the Experiment 3
    distribution: if the count is zero, the failure is in **classification**, not
    extraction. Then check which of the three signals failed — most often
    location, because `detect_sections` split at the wrong page or produced one
    section covering everything.
17. **No standalone marker was found**, so `detect_sections` defaulted to a single
    consolidated section over the whole document. **What would have told you:**
    `needs_review=True` on the `DocSection`, and the warning logged at detection
    time — both of which exist and neither of which anything watches. That is the
    open half of this design.
18. **Audit finding F9** — constants fitted to the current corpus.
    `CONTINUATION_MAX_PAGES = 4` propagates the label four pages past its anchor;
    a longer statement section is silently truncated. Corpus-dependent because the
    right bound is a property of the *issuers' layouts*, and this corpus has five
    documents from three issuers.

### §12 — System design

19. **What breaks:** `detect_sections` assumes contiguity — it finds the first
    standalone marker and splits once, so interleaved sections produce two ranges
    that are both wrong, and every figure after the first boundary is
    misattributed. **What I would change:** scan for **all** marker occurrences,
    not the first, and build a list of `(page, financial_type)` boundaries; a
    `DocSection` then becomes a set of page ranges rather than a single
    `start..end`. That changes `DocSection`'s shape, `_build_page_to_section`, and
    every consumer that assumes a contiguous range. Given F4 (metadata unverified)
    is already open, I would also add an assertion that every page falls in exactly
    one section, and set `needs_review` when it does not — because a silently
    mis-split document is precisely the failure this whole day exists to prevent.
20. **Smallest change that makes it act:** surface it. `needs_review` is already
    on the `DocSection`; persist it to the `documents` row (a migration — Day 16)
    and expose it on the existing admin `/api/documents/pending` endpoint, which
    already lists ingestion state (Day 41). One column, one field in a response
    the frontend already renders. **What it must not do:** block ingestion. The
    current behaviour — assume consolidated, record the assumption, continue — is
    correct, because refusing a document because one marker was missing would make
    the system less useful without making it more truthful. The flag's job is to
    make the assumption **visible**, not to override it.

---

## 14. MUST REMEMBER

```text
- ONE PDF → TWO documents rows (consolidated + standalone)
- sha256 stored as "{file_sha256}_{financial_type}" — two rows, UNIQUE still holds
- financial_type is detected from CONTENT, never a filename or form field (Trap 1)
- THREE SIGNALS, ALL REQUIRED: structure ∧ location ∧ content
- Each signal is a VETO, not a vote
- Thresholds differ by vocabulary: 2 for statements, 1 for auditor reports
- Keyword sets include OCR-damaged variants — a second layer over TYPO_MAP
- No marker found → log + assume consolidated + needs_review=True
- detect_sections is PURE; register_sections owns all DB writes
- classify_blocks refines IN PLACE
```

## 15. MUST UNDERSTAND

```text
- Why intersection beats scoring: a veto cannot be outvoted
- Why classification is what makes EXTRACTION targeted rather than heuristic
- Why financial_type is the one metadata field withheld from the uploader
- Why "assume, and record the assumption" appears in three subsystems
- Why F9's constants are a corpus-diversity exposure, not a corpus-size one
- Why needs_review being set-but-unconsumed is honest-but-incomplete
```

---

## 16. This connects to

```text
Day 22 — PDF → PageBlock
   ↓
Day 23 — labelling the blocks                    ← you are here
   ↓
Day 24 — splitting them into retrievable chunks
```

Forward references:

- Per-block-type chunk targets → **Day 24**
- `get_blocks_by_type(FINANCIAL_STATEMENT)` in extraction → **Day 31**
- `financial_type` as a retrieval filter (and audit **F7**) → **Day 27**
- `period_assumed`, the same "record the assumption" pattern → **Day 34**
- `regression_check`'s block-distribution check → **Day 43**
- Audit **F4** (metadata unverified) and **F9** (fitted constants) → **Day 43**
