# LedgerMind — Code Documentation Log

One row per file that receives educational comments during the 47-day course.
The point is not bookkeeping: it is to make visible *which* parts of the
codebase were hard to read, and *why*, so the pattern is legible in aggregate.

Opened: 2026-08-23. **First five rows landed 2026-08-23**, from Days 38–44.
Days 1–37 added none — see the note under the log.

---

## The rules this log records compliance with

Every entry below was produced under these constraints. If a row cannot honestly
claim all of them, it does not get written — the work gets redone.

1. **Comments only.** No renames. No logic changes. No reordering. No
   dependency changes. No "cleaning up" adjacent code. No formatting churn
   beyond what the comment itself requires.
2. **Explain WHY, not WHAT.** `# Increment i` is noise. *"Move to the next chunk
   while preserving the configured overlap, so a fact near a boundary stays
   available to the next retrieval unit"* is the job.
3. **Do not comment what is already well commented.** Large parts of this
   codebase — `retriever.py`, `llm/client.py`, `state.py`, `contradiction.py`,
   `response_shaping.py`, `conftest.py` — already carry measurement-cited
   comments better than anything that would replace them. Adding to those is
   net-negative. The `Why it was hard to read` column must be answerable.
4. **Never invent a rationale.** If the reason a line exists cannot be
   established from the code, git history, or an existing document, the comment
   says so, or there is no comment. Inferences are labelled inline as
   `# [inferred]`.
5. **Anything that looks wrong gets DOCUMENTED, not fixed.** It goes to
   `CAVEATS.md` or `KNOWN_UNKNOWNS.md` and the work continues. Fixing requires
   explicit approval.
6. **Verified with `grep -n` immediately.** A file containing regexes is also
   checked with `python -c "import app.X.Y"` — an AST parse proves a file loads,
   not that a regex still compiles.
7. **The test suite is run after every file.** Compared against the current
   baseline of **218 passed / 25 errors** (CAVEAT-025), not against green.

---

## Log

| # | Date | Day | File | LOC | What was documented | Why it was hard to read | Architectural concept it demonstrates | Commit |
|---:|---|---:|---|---:|---|---|---|---|
| 1 | 2026-08-23 | 44 | `backend/app/api/metrics.py` | 131 | Module header: why it aggregates over `audit_log` rather than a purpose-built table; why the tenant `WHERE` is belt-and-braces beside RLS; and a per-metric note on what each number can and cannot be trusted for | No module docstring at all, and almost no prose, in the file producing every number an admin would quote. Four of its five summary metrics carry a caveat that is invisible from the SQL | **One fact, one place.** A second metrics store would be a second copy of `audit_log`'s facts — the failure class that produced three metric registries and two formula copies | `6c0bc23` |
| 2 | 2026-08-23 | 38 | `frontend/app/layout.tsx` | 44 | Why this is the only Server Component; why `metadata` and `next/font/google` depend on that; and that the three font variables are the file's purpose | Near-zero commenting, and it is the first frontend file a reader opens. Nothing in it says why no component names a font family | **A single source for a cross-cutting value.** One file decides the typefaces; every consumer reads a semantic token | `9f6d9b6` |
| 3 | 2026-08-23 | 40 | `frontend/components/document/Sidebar.tsx` | 156 | That the role check is a **usability** control and not a security boundary; why the view type has four values where the page has five; that `indexedFilings` is an unwired literal | The `userRole === "admin"` ternary reads exactly like an authorization check and is not one. Nothing nearby says what actually refuses | **The client decides what is shown; the token decides what is served.** Route-level `require_role` on a signature-verified claim is the boundary | `e7a0b49` |
| 4 | 2026-08-23 | 40 | `frontend/components/document/LedgerTable.tsx` | 47 | That `rule` is accounting notation — single marks a period comparison, double marks a total — not a border-weight preference | A three-value string prop that looks like styling and encodes domain meaning | **ED-024**: a presentational component that knows nothing about paths or engines and receives pre-formatted rows | `435db5a` |
| 5 | 2026-08-23 | 39 | `frontend/components/document/QueryDock.tsx` | 128 | Why `query` and `selectedEntities` are not lifted; how `isLoading` prevents double-submission; that the entity pills WRITE a query rather than bypassing the router; that `INDEX_TABS` are suggestions, not capabilities | Two apparently separate interactions that are one code path, and a literal list that reads as a capability declaration | **State lives at the lowest common ancestor of everything that reads it** — and the parent receives a finished value, not a draft | `a36b2b7` |

**Days 1–37 added no rows.** Those days were produced as documentation only; the
files they cover were read as teaching material and left unedited. The five rows
above are the first, and they came from Days 38–44. That is recorded rather than
tidied away — the log's job is to make visible *which* parts of the codebase were
hard to read, and an empty stretch is information about how the work actually
went.

**Nothing in Tier 4 was touched**, and two files this pass deliberately declined:
`ExecutionTrace.tsx` and `frontend/lib/api.ts` both carry measurement-cited
headers better than anything that would replace them (rule 3), even though the
tier list places the first in Tier 3.

---

## Files identified as needing comments

From the 2026-08-23 audit. Tiers are by need, not by order of work — the course
day that touches a file is when it gets commented.

### Tier 1 — no module docstring, and non-trivial

**✔ = a row exists in the log above.**

| File | LOC | Day | Note |
|---|---:|---:|---|
| `backend/app/ingestion/financial_extractor.py` | 908 | 31 | Largest undocumented file in the repo. Positional column detection, OCR repair, derived totals, identity validation — no file-level orientation at all |
| `backend/app/ingestion/entity_resolver.py` | 336 | 31 / 36 | 20+ regexes with ordering constraints that are load-bearing: `SPLIT_INITIAL_RE` **must** run before `PREFIX_RE`, and only an inline comment says so |
| `backend/app/api/query.py` | 233 | 6 | Rich inline comments; no file header |
| `backend/app/api/metrics.py` | 131 | 44 | **✔ done** — row 1 |
| `backend/app/auth/service.py` | 69 | 7 | |
| `backend/app/auth/dependencies.py` | 56 | 8 | |
| `backend/app/auth/router.py` | 10 | 7 | |
| `backend/app/auth/schemas.py` | 19 | 5 | The auth chain is the first thing a learner reads and has no map |
| `backend/app/core/config.py` | 19 | 12 | |
| `backend/app/main.py` | 108 | 4 | Opens with a comment block, not a docstring |
| `backend/app/worker.py` | 48 | 45 | Same |

### Tier 2 — stub docstring (a title, then nothing)

| File | LOC | Day | Note |
|---|---:|---:|---|
| `backend/app/engines/dsl_compiler.py` | 303 | 32–33 | Docstring is one line. The file holds the entire validation contract |
| `backend/app/engines/router.py` | 428 | 36 | Same. Inline comments are excellent; the header is absent |

### Tier 3 — frontend, near-zero commenting

| File | LOC | Day | Status |
|---|---:|---:|---|
| `frontend/app/layout.tsx` | 40 | 38 | **✔ done** — row 2 (measured 44 lines) |
| `frontend/components/document/DocumentPage.tsx` | 277 | 38 | |
| `frontend/components/document/QueryDock.tsx` | 128 | 39 | **✔ done** — row 5 |
| `frontend/components/document/ExecutionTrace.tsx` | 179 | 39 | **declined, rule 3** — its existing header is better |
| `frontend/components/document/Sidebar.tsx` | 156 | 40 | **✔ done** — row 3 |
| `frontend/components/document/LedgerTable.tsx` | 47 | 40 | **✔ done** — row 4 |
| `frontend/components/document/EntityComparisonTable.tsx` | 129 | 40 | |
| `frontend/components/document/AuditLogTable.tsx` | 97 | 41 | |
| `frontend/components/document/UploadPanel.tsx` | 418 | 41 | |

### Tier 4 — well commented already; **do not touch**

`retriever.py` · `llm/client.py` · `engines/state.py` · `contradiction.py` ·
`api/response_shaping.py` · `semantic_engine.py` · `prompt_shield.py` ·
`audit_writer.py` · `cross_engine.py` · `tests/conftest.py` ·
`ingestion/storage.py` · `ingestion/gate.py` · `api/documents.py` ·
`db/session.py` · `core/security.py` · `frontend/lib/api.ts` ·
`frontend/lib/auth.ts` · `frontend/components/document/WorkingPaperHeader.tsx` ·
`frontend/components/environment/PaperStack.tsx`

These are read as *teaching material* on their day, not rewritten. Several are
better than what would replace them.

### Special case — commented, but not edited

`frontend/components/AnswerCard.tsx`, `ConfidenceBadge.tsx`, `CorpusPanel.tsx`
are unreachable (CAVEAT-026) and are **retained untouched by decision**. Day 40
studies them; it does not modify them.
