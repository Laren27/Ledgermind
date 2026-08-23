# LedgerMind — Project Timeline

Reconstructed from git history on 2026-08-23: **985 commits**, `83fc5ba`
(2026-06-23) through `017d97e` (2026-08-22).

**Sourcing rule.** Dates and commit hashes are **repository evidence** — they
come from `git log` and can be re-derived. Anything about *why* a thing happened
is labelled. Where the repository states a reason (a commit message, a code
comment, `IMPLEMENTATION_DELTAS.md`), it is cited. Where it does not, the entry
says **[inferred]** and the inference is kept short.

---

## Activity, by week

```
2026-W26  ██                       22    repo, schema
2026-W27  █████████                99    API, auth, engines, ingestion
2026-W28  ███                      33
2026-W29  ██████████               112   the LLM client, extraction work begins
2026-W30  ████████████████████     222   ← peak: extraction correctness
2026-W31  ████████████████████████ 271   ← peak: registry, evals, calibration
2026-W32  ██████████               111   frontend, audit
2026-W33  ████                     51    the F1–F13 audit, the test suite
2026-W34  ███████                  82    F14, transport retries, documentation
```

Two-thirds of all commits fall in a five-week band (W29–W33), and almost none of
that work is feature work. It is extraction correctness, measurement and
consolidation. That shape is the most informative thing in this file.

---

## Phase 1 — Foundations (2026-06-23 → 2026-07-01)

| Date | Commit | Event |
|---|---|---|
| 06-23 | `83fc5ba` | Initial commit |
| 06-24 | `cdb2714` | `sql/init.sql` — the schema, RLS policies and two-role model exist from the very beginning |
| 07-01 | `8891153` | `scripts/eval_runner.py` — the evaluator predates most of what it evaluates |

**What this says.** Row-Level Security and the append-only audit table are in
the *first week*, not retrofitted. So is the evaluator. **[Inferred]** the
project was scoped around auditability and measurement before it was scoped
around features — which is consistent with `CLAUDE.md`'s stated objective
("correctness, reliability, explainability, and auditability… not feature
count") but the ordering is the evidence, not the sentence.

---

## Phase 2 — The system, built fast (2026-07-02 → 2026-07-05)

| Date | Commit | Event |
|---|---|---|
| 07-02 | `d68f3b1` | `app/main.py` — FastAPI entrypoint |
| 07-02 | `2ea9c83` | `auth/service.py` — login, bcrypt, JWT |
| 07-03 | `f58f923` | `engines/cross_engine.py` |
| 07-04 | `010a5e1` | `engines/graph.py` — LangGraph assembly |
| 07-04 | `449391b` | `engines/retriever.py` — hybrid search |
| 07-04 | `5404264` | `engines/quant_engine.py` — DSL → SQL |
| 07-05 | `1b79f31` | `ingestion/pipeline.py` — "Celery task that wires all Phase 3 ingestion modules" |

**Four days for the entire query pipeline and the ingestion chain.** Everything
after this point is correctness work on a structure that did not substantially
change again. The three-path design, the shared `QueryState`, and the
DSL-not-SQL rule all date from this week and none of them were revised.

---

## Phase 3 — The frontend and the first hard problems (2026-07-17 → 2026-07-29)

| Date | Commit | Event |
|---|---|---|
| 07-17 | `9ea7846` | `metrics/registry.py` — the single metric registry, replacing three hand-maintained dicts |
| 07-21 | `6b45b8c` | `frontend/app/page.tsx` — the Next.js working-paper UI |
| 07-24→25 | `856101c`, `f5e5b33` | Desk-background assets added, then reverted the next day |
| 07-29 | `b937b8e` | `app/llm/client.py` — the shared LLM client |

Three things worth reading closely:

**The registry (07-17)** is the first consolidation. Its own docstring names
three shipped bugs caused by the split it replaced — `profit_before_tax` absent
from one registry so Gemini substituted `pat`; `exceptional_items` collapsing
three distinct OCI lines; Titan's segment revenue with no canonical home.

**The revert (07-24 → 07-25)** is one of very few in the history.

**The LLM client (07-29)** exists because of two defects found the same day and
recorded in its module docstring: a single query measured at 3.07s / **120.0s** /
3.00s with no timeout anywhere, and a documented Gemini→Groq failover that had
"a `groq_api_key` field and zero call sites". The docstring explains why they
were fixed together: *a fallback keyed on exceptions would never have fired on
defect 1*.

---

## Phase 4 — Extraction correctness (2026-07-20 → 2026-08-10)

The two peak weeks. No new subsystems; the work is making the numbers right.
The five cases the README selects, each documented at length in
`IMPLEMENTATION_DELTAS.md`:

| What | Why it survived review |
|---|---|
| **A ₹10,000 Cr error laundered through arithmetic** — OCR split `17,292` into `I` and `7,292`; a rule kept the comma-bearing fragment. Derivation then recomputed total income and total expenses *from* the corrupted value | The stored column was internally **self-consistent**. The system had logged the disagreement for weeks in a list scanned by count rather than by magnitude |
| **A green gate that validated the producer, not the store** — `regression_check` passed 4/4 after every OCR fix, correctly, while the loader dropped every corrected value as `skipped` | The gate asserted on extraction output *in memory*. 28 stale figures, invisible to a green suite for three weeks |
| **A correct number in the wrong row** — Paytm `cash` stored as −710 Cr | Nothing about the digits was corrupt. Only a semantic claim — *a balance-sheet stock cannot be negative* — separates it. Now `scripts/check_balance_invariants.py`, which caught a second instance on its first run |
| **A citation floor that guaranteed untraceability** (removed 08-08) | The constant was correct. Letting `retrieved_chunks` and `citations` diverge was not |
| **One vector store, two databases, disjoint primary keys** — 139 Qdrant chunks deleted as "orphans" were production's Paytm and Titan corpus | *A checker that can structurally only inspect one of two stores passes having inspected nothing.* Resolved by deterministic doc_ids, migrations 018–019 |

**The recurring shape.** Four of the five are not wrong *values* — they are
**wrong instruments**: a self-consistent column, a gate reading the wrong layer,
a list scanned by the wrong statistic, a checker that could only see one store.
This is why `CLAUDE.md` §8 is about diagnostic discipline rather than about code.

---

## Phase 5 — Audit and testing (2026-08-11 → 2026-08-19)

| Date | Commit | Event |
|---|---|---|
| 08-11 | — | `docs/audit/repo_audit_20260811.md` — 13 findings, F1–F13, ranked by blast radius |
| 08-11 | `215224b` | `backend/tests/conftest.py` — "pytest unit suite over pure functions — 165 tests, 8 modules" |
| 08-11 | — | **F1 closed** — exact-alias-only company matching |
| 08-12 | — | **F2 closed** in three steps — the refusal edge to `audit_writer` |

The test suite arrives on **day 50 of 61**. Its conftest docstring states the
reason it looks unusual: several tests *assert known defects as current
behaviour*, naming the audit finding in the docstring, because "a test suite
whose purpose is to detect change must first describe the present accurately".

F2's closure is recorded honestly as **partial by construction** — it fires only
when the model returns an unresolvable name, not on the commoner case where the
schema gave it nowhere to say so.

---

## Phase 6 — F14 and the documentation pass (2026-08-20 → 2026-08-23)

| Date | Commit | Event |
|---|---|---|
| 08-20 | `d64e4e5`, `d03a17a`, `9e65624` | "The response schema is part of the prompt"; "log-only is a fact about the code path, not a bound on blast radius" |
| 08-20 | `500b6c1`, `f109e81` | `ENGINEERING_DECISIONS.md` and `BUGS_AND_LESSONS.md` first committed |
| 08-20 | — | Nine further documentation files written — and **left uncommitted for three days** |
| 08-21 | `3aedb1f` | Two-arm router probe: the field moves two routes, in opposite directions |
| 08-21→22 | `3cae191`, `e711e53`, `e1ca737` | Transport-class retry, for Gemini and for Cohere |
| 08-22 | `1c23b63`…`63d7f40` | **F14** — `company: Optional[str]` → `companies: list[str]`, end to end |
| 08-22 | `5bff364` | Audit `response_text` bound in full; 36.4% of stored rows were unmarked prefixes |
| 08-22 | `0cf7e7c` | `--api-base` required — **and 25 tests have errored ever since** (CAVEAT-025) |
| 08-23 | `87f1539`… | The nine 2026-08-20 documents committed; the 47-day course begun |

**F14's honesty is the notable thing.** It shipped *without a router probe*, on
instruction, and both `CLAUDE.md` and `IMPLEMENTATION_DELTAS.md` say so in those
words rather than eliding it. The classifier is recorded as **UNMEASURED** across
the change (KU-002), and Q051's continued passing is stated as "argued from code
paths and unit tests, not from a run".

**And the counterexample, in the same window.** `0cf7e7c` broke 25 tests and
nothing noticed for a day, because there is no CI (CAVEAT-022) and the documented
baseline said green. The discipline is real and it is not automatic.

---

## What the history shows, in four observations

**1. The architecture was right early and was not revised.** Four days in July
produced a three-path pipeline that 900 subsequent commits refined without
restructuring. Almost none of the effort went where a beginner would expect.

**2. The expensive failures were instrument failures, not logic failures.** A
self-consistent column, a gate reading memory instead of the database, a list
scanned by count instead of magnitude, a checker that could see one of two
stores. In each case the code was doing what it said; the *measurement* was
wrong.

**3. Consolidation was always a response to a bug, never to tidiness.** One
metric registry, one LLM client, one formatter — each has a docstring naming the
specific shipped defect that the duplication caused.

**4. The documentation is unusually candid, and that is deliberate.** "Shipped
without a probe." "Partial by construction." "Unvalidated, not validated."
`CLAUDE.md` states why: *a wrong answer with a ✓ tick is worse than a refusal* —
and that applies to the documentation as much as to the answers.

---

## Maintaining this file

Append a phase when the work changes character, not when a month ends. Cite the
commit. Keep **[inferred]** on anything the repository does not state — a
timeline is the easiest document in which an inference hardens into history.
