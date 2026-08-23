# LedgerMind — Code Documentation Log

One row per file that receives educational comments during the 47-day course.
The point is not bookkeeping: it is to make visible *which* parts of the
codebase were hard to read, and *why*, so the pattern is legible in aggregate.

Opened: 2026-08-23. Empty by design — the first row lands on Day 1.

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
| | | | | | | | | |

*(first row lands on Day 1)*

---

## Files identified as needing comments

From the 2026-08-23 audit. Tiers are by need, not by order of work — the course
day that touches a file is when it gets commented.

### Tier 1 — no module docstring, and non-trivial

| File | LOC | Day | Note |
|---|---:|---:|---|
| `backend/app/ingestion/financial_extractor.py` | 908 | 31 | Largest undocumented file in the repo. Positional column detection, OCR repair, derived totals, identity validation — no file-level orientation at all |
| `backend/app/ingestion/entity_resolver.py` | 336 | 31 / 36 | 20+ regexes with ordering constraints that are load-bearing: `SPLIT_INITIAL_RE` **must** run before `PREFIX_RE`, and only an inline comment says so |
| `backend/app/api/query.py` | 233 | 6 | Rich inline comments; no file header |
| `backend/app/api/metrics.py` | 131 | 44 | Almost no prose |
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

| File | LOC | Day |
|---|---:|---:|
| `frontend/app/layout.tsx` | 40 | 38 |
| `frontend/components/document/DocumentPage.tsx` | 277 | 38 |
| `frontend/components/document/QueryDock.tsx` | 128 | 39 |
| `frontend/components/document/ExecutionTrace.tsx` | 179 | 39 |
| `frontend/components/document/Sidebar.tsx` | 156 | 40 |
| `frontend/components/document/LedgerTable.tsx` | 47 | 40 |
| `frontend/components/document/EntityComparisonTable.tsx` | 129 | 40 |
| `frontend/components/document/AuditLogTable.tsx` | 97 | 41 |
| `frontend/components/document/UploadPanel.tsx` | 418 | 41 |

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
