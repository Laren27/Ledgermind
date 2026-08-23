# Day 40 — The Render Boundary, and Dead Code

**Phase 11 · Weight: H (~120 min) · Prerequisites: Days 39, 30, 34**

**Textbook: no citation.** Today has two halves. The first is the invariant that
holds the frontend together. The second is a **method** — how to establish that
code is unused, and how to keep what the repository *proves* separate from what
you have *inferred*. The second half is the one that transfers.

---

## 1. Today's goal

By tonight you can:

- Read `composeDocumentBody()` and name **all five** branches, in evaluation
  order, and the field each tests.
- Explain the **Zero UI-Hallucination Mandate** and give four places where this
  UI **omits** rather than substitutes — and say what each substitute would have
  asserted.
- Establish by evidence that three components are unreachable, and state the
  argument in a form someone else could check.
- Separate, explicitly and out loud: **FACT · EVIDENCE · INFERENCE · UNKNOWN.**
- Say what `git log`, `git log -S` and `tsc` each **can** and **cannot** establish
  about dead code.
- Decide whether deleting the three would be safe, and list what you would check
  first — *without inventing a reason they were kept.*
- Name the documented invariants that the code no longer satisfies — **there
  are three instances, and one of them is inside today's own subject** — and
  explain why the correct action is to **record** the drift, not to fix it.

---

## 2. Why now

Day 39 delivered a `QueryResponse` into `pages[]`. Today it becomes markup, and
the conversion happens in **one** function.

That function is unreadable without Days 30 and 34. It branches on
`data.sql_result?.[0]` having an `entity_a` key, or an `entity1` key, or a
`current_fy` key — and those three shapes are the compute functions' return
types from Day 33. If you do not already know that `point_in_time` returns a raw
row while `growth_comparison` returns a computed dict, this reads as arbitrary
key-sniffing.

The second half needs Day 2 (git as evidence) and Day 38 (there is exactly one
route). It also needs a habit the whole course has been building: **stop before
the story.**

---

## 3. Concepts you must know first

| Concept | From | Why it is needed today |
|---|---|---|
| `sql_result` row shapes per operation | Day 33 | The three key-sniffing branches |
| `sql_verified` and what it guarantees | Day 34 | The `verified` vs `estimated` glyph |
| Citations, `reranker_score` | Days 29–30 | `buildCitationItems`, `EvidenceList` |
| `role_filtered_response` strips fields | Day 9 | Why `relevance` and `latencyMs` are optional |
| `financial_type` is `unknown` by design | Day 23 | The omitted citation tag |
| `companies: string[]`, `[]` is legal | Day 36 (F14) | `issuerLabel`, `WorkingPaperHeader` |
| `frontend/app/` has three files | Day 38 | Half of the unreachability proof |
| Git history as evidence, not testimony | Day 2 | The whole second half |

---

# PART A — THE RENDER BOUNDARY

## 4. Concept lesson

### 4.1 The invariant, and the cost it is paying for

`CLAUDE.md` §6:

> **Frontend document components must never know which engine produced the
> data.** `composeDocumentBody()` in `app/page.tsx` is the only function aware of
> path/engine internals.

**ED-024** states the payoff: *"adding a fourth engine path touches one function,
not twenty components."*

Verify the invariant rather than believing it:

```bash
cd frontend
grep -rn "\.path\b\|sql_result\|sql_verified\|is_blocked\|\.error\b" components/ | grep -v node_modules
```

You should find **nothing** in `components/document/`. The document components
take `label`, `value`, `rows`, `count`, `items`, `paragraphs` — vocabulary from
the *page*, not from the *pipeline*.

**Now notice what this costs.** `composeDocumentBody` is ~150 lines of branching
in a 584-line file, it uses `any` four times, and it is the only place a change
to a `sql_result` shape has to be tracked. The invariant does not remove the
complexity; it **localises** it. That is the trade, and it is the right one for
the same reason the metric registry is (Day 31): one place to be wrong beats
twenty places to drift.

---

### 4.2 The five branches, in order

```tsx
function composeDocumentBody(data: QueryResponse) {
  if (data.is_blocked) { … }                                    // 1
  if (data.error) { … }                                         // 2
  const isComparativeResult      = data.sql_result?.[0] && "entity_a" in data.sql_result[0];
  const isPlainComparisonResult  = data.sql_result?.[0] && "entity1"  in data.sql_result[0];
  if (isPlainComparisonResult) { … }                            // 3
  if (isComparativeResult) { … }                                // 4
  if (data.path === "quantitative" && data.sql_result?.[0]) { … }// 5
  return ( … narrative … );                                     // default
}
```

| # | Test | Renders | Backend origin |
|---:|---|---|---|
| 1 | `is_blocked` | "Not Permitted / Policy Block" + the cleaned reason | `prompt_shield_node` (Day 42) |
| 2 | `error` | the error name as a callout, plus citations if any | any node's `error` (Days 30, 34, 36) |
| 3 | `"entity1" in sql_result[0]` | two-entity value comparison | `comparison` compute (Day 33) |
| 4 | `"entity_a" in sql_result[0]` | two-entity **growth** comparison | `growth_comparison` compute (Day 33) |
| 5 | `path === "quantitative"` | ledger table + verified callout | `point_in_time` / `yoy` (Day 33) |
| — | fallthrough | cited narrative | semantic and cross (Days 30, 37) |

**Order is semantics.** A blocked query may also carry an `error`; testing
`error` first would render a refusal where a policy block belongs. A comparison
result also has `path === "quantitative"`; testing branch 5 first would render a
single-value ledger table for a two-entity answer.

**Read one more time what branch 3 and 4 are doing.** They are **duck-typing a
SQL result** — `"entity1" in row` versus `"entity_a" in row`. There is no
discriminator field. Day 37 met the same pattern inside `cross_engine`:

> **`"value" in result_row` versus `"yoy_pct" in result_row`** — shape-sniffing …
> **An implicit contract between the compute functions and this consumer**,
> enforced by nothing.

**Here it is again, across a language boundary and an HTTP hop.** Rename
`entity1` to `entity_1` in `dsl_compiler.py` and this frontend silently falls
through to the narrative branch. No type error — `sql_result` is
`Record<string, unknown>[]`. No test — the frontend has no test runner
(CAVEAT-022). **The failure is a wrong-looking page, discovered by a human.**

**That is the most fragile seam in this system, and it is worth naming as such.**

---

### 4.3 Omit rather than substitute — four worked examples

The mandate, from `CLAUDE.md` §6:

> **Zero UI-hallucination mandate.** No badge, count, stat, or citation number
> may exist as static copy; every one is wired to a real backend field. Omit
> rather than substitute.

**"Omit rather than substitute" is the operative half.** The interesting cases
are not "don't hard-code a number" — they are "a field is missing; what now?"

**(1) The citation's `financial_type` tag.**

```tsx
// financial_type is UNKNOWN for every non-FINANCIAL_STATEMENT chunk by
// design (see section_classifier.py) — risk disclosures and MD&A are not
// scoped to standalone or consolidated. Rendering "(unknown)" reads as a
// classification failure when it is a correct N/A, so the tag is omitted
// entirely. Its presence then genuinely means "these are that entity's
// numbers," rather than being noise on every citation.
const ft = c.financial_type;
const hasFinancialType = !!ft && ft.toLowerCase() !== "unknown";
```

**A substitute would have asserted:** *"we tried to classify this and failed."*
The truth is *"this question does not apply here."* And the second sentence is
the real payoff: **omission makes the tag informative when it does appear.**

**(2) The issuer label.**

```tsx
/**
 * Returns null for the empty list rather than a stand-in string. Empty means no
 * issuer resolved and retrieval ran unfiltered across the tenant -- the caller
 * must omit the label, not print a name the system never produced.
 */
function issuerLabel(companies: string[] | null | undefined): string | null {
  if (!companies || companies.length === 0) return null;
  return companies.join(" / ");
}
```

and at the call site:

```tsx
{[issuerLabel(data.companies), data.fiscal_year ?? "Period"].filter(Boolean).join(" — ")}
```

**`.filter(Boolean)` is the mandate in one call.** A `null` label disappears from
the heading rather than becoming `"null — FY26"` or `"CORPUS — FY26"`.

**(3) The working-paper header's three states.**

```tsx
const entityName =
  companies == null            ? "NO QUERY EXECUTED"
  : companies.length === 0     ? "NO ISSUER RESOLVED — SEARCH UNFILTERED"
  : companies.length === 1     ? `${companies[0].toUpperCase()} LIMITED`
  // Plural: no "LIMITED" suffix. It is one legal entity's suffix and
  // appending it to a set of issuers would be a claim about none of them.
  : companies.map((c) => c.toUpperCase()).join(" · ");
```

**This is the most instructive one in the repository**, and its comment records
the incident:

> The previous fallback stood in for a falsy scalar company. After F14 that
> field stopped existing, so the fallback became the label on EVERY working
> paper — a corpus name asserted even for answers that named one issuer.
> It is not a safe default: an empty issuer list means the search ran
> unfiltered, which is a fact about SCOPE and must read as one, never as the
> name of a thing in the archive.

**Three separate lessons in one block.**

- **A "safe default" is a claim.** `"GENERAL CORPUS ARCHIVE"` looked harmless and
  asserted a scope the system had not established.
- **A schema change can weaponise a fallback.** The fallback was correct while
  `company` existed. F14 removed the field, every read became falsy, and the
  fallback fired on **100 %** of renders. Nothing about the fallback changed.
- **`LIMITED` is dropped for the plural case.** A legal-entity suffix appended to
  a set of issuers is a claim about none of them. That is a level of care worth
  copying.

**(4) The em-dash idiom.**

```tsx
{/* Same em-dash idiom as the admin-only Latency column
    below: a field that is not available reads as absent,
    never as a value. A bare {undefined} renders an empty
    cell, which looks like a rendering fault. */}
{e.confidenceTier ?? "—"}
```

```tsx
{e.latencyMs != null ? `${e.latencyMs}ms` : "—"}
```

**Two different reasons for absence, one visual treatment.**
`confidenceTier` is absent because a **blocked query never scored one**
(`response_shaping.py` pops the key). `latencyMs` is absent because the
**viewer/analyst role does not receive it**. Both are "not available", and both
render as `—`.

**And note the third state the comment names:** a bare `{undefined}` renders an
*empty cell*, which reads as a rendering bug. **Omission still needs a glyph.**
"Omit rather than substitute" means omit the *claim*, not the *cell*.

**(5) `EvidenceList`, one more time, from the props:**

```tsx
interface EvidenceItem {
  index: number;
  label: string;
  page: number;
  // Analyst+ only -- role_filtered_response strips reranker_score for
  // viewers, so this component must render without it.
  relevance?: number | null;
  id: string;
}
...
{item.relevance != null && `, relevance ${item.relevance.toFixed(2)}`}
```

**A presentational component carrying a comment about RBAC.** It does not know
about roles; it knows the field is optional and why. That is the right amount of
coupling: the *reason* is documented, the *logic* is not duplicated.

---

### 4.4 Three documented invariants the code no longer satisfies

**The course's rule is that the code is the authority.** Today that rule bites
three times, and in every case the correct action is to **record the drift, not
fix it** (`CODE_DOCUMENTATION_LOG.md` rule 5: *anything that looks wrong gets
DOCUMENTED, not fixed*).

#### Drift 1 — the glass/blur invariant is inverted

`CLAUDE.md` §6 says:

> Glass/blur is permitted in exactly one component: `QueryDock`.

Measure it:

```bash
grep -rn "backdrop-blur\|backdropFilter\|blur(" frontend/app frontend/components
```

```
components/document/Sidebar.tsx:47:        backdropFilter: "blur(12px)",
components/document/Sidebar.tsx:48:        WebkitBackdropFilter: "blur(12px)",
components/document/PageNavigator.tsx:23:          backdropFilter: "blur(8px)",
```

**`QueryDock.tsx` has none.** The one component the invariant names is the one
component without it, and two components it does not name have it.

**And the history explains how, precisely:**

```bash
git log --oneline -S "backdropFilter" -- frontend
```

| Commit | Date | What |
|---|---|---|
| `2532d1a` | 2026-07-24 | *"transform QueryDock into an embedded archival paper input"* — **removed** `backdropFilter: "blur(14px)"` from QueryDock |
| `ff1dcbc` | 2026-07-25 | *"anchor pagination to desk as an engraved … tray control"* — **added** `blur(8px)` to PageNavigator |
| `0c07da9` | 2026-07-25 | *"upgrade library archive aesthetic…"* — **added** `blur(12px)` to Sidebar |

**FACT:** the invariant was true before 2026-07-24 and false after 2026-07-25.
**FACT:** three styling commits moved it, none of which mentions the invariant.
**INFERENCE:** the design language changed — glass moved from the *paper* to the
*furniture* (sidebar, desk tray), which is coherent with the working-paper
metaphor. **UNKNOWN:** whether that was a decision about the invariant or an
aesthetic change that nobody checked the invariant against. No commit message,
comment or document says.

**Recorded as CAVEAT-027.** Not fixed: fixing means either editing `CLAUDE.md`
(a claim about intent that only the author can make) or editing three components
(a functional change). Both need approval.

#### Drift 2 — a hardcoded date on every working paper

```tsx
<div>Generated: <span style={{ color: "var(--ink-metadata)" }}>2026-07-25</span></div>
```

`WorkingPaperHeader.tsx`, live, rendered on **every** sheet.

**This is a stat as static copy — precisely what the mandate forbids.** And it is
not merely unwired: it is *wrong*, because it is a date, and dates are read as
claims about when.

Two neighbours in the same file are wired correctly — `wpRef` from
`request_id`, `revision` from the `revisions` map, `preparer` from
`session.role`. **One field out of four is fabricated, and it sits in the same
visual group as the three that are not**, which is what makes it credible.

**Also recorded in CAVEAT-027.** The fix is a backend field, which is a
functional change.

#### Drift 3 — a `Source Table:` that names a table which does not exist

This one lives inside today's own subject, `composeDocumentBody`, at **three**
call sites:

```bash
grep -n "sourceTable" frontend/app/page.tsx
```

```
140:        <SectionHeading sourceTable="audited_financials">
174:        <SectionHeading sourceTable="audited_financials">
217:        <SectionHeading sourceTable="audited_financials">
```

`SectionHeading` renders that as **`Source Table: audited_financials`**, in the
heading immediately above a SQL-verified figure.

**Now measure the claim:**

```bash
grep -n "CREATE TABLE" sql/init.sql
grep -rn "audited_financials" backend/ sql/ docs/ CLAUDE.md
```

The tables are `tenants`, `users`, `documents`, **`financials`**, `audit_log`.
The string `audited_financials` appears **nowhere** outside those three frontend
literals.

**This is the sharpest instance of the mandate in the whole repository, and it is
the one that is violated.** Everything else on that sheet is earned: the ✓ comes
from `sql_verified` (Day 34), the figure comes from a compiled parameterised
query (Day 33), the period comes from the DSL. **The one line that says where the
number came from is invented** — and it is *more* trustworthy-looking than the
rest, because it names a relation, in monospace, like a citation.

**Be precise about what is wrong.** The figure is real. The verification is real.
It genuinely came from an extracted financial statement in Postgres. **Only the
identifier is fictional.** That is what makes it a mandate violation rather than
a correctness bug: nothing computed is wrong, and something asserted is.

**Recorded as CAVEAT-027(c), severity Medium** — the highest in that entry.
And note the fix the caveat recommends: **not** `sourceTable="financials"`, but
**omitting the prop**. Correcting the string swaps one hand-maintained literal
for another; omitting it asserts nothing, which is what the mandate actually
asks for. Deriving it properly would need a field `QueryResponse` does not carry.

> **Note for the reader who wants to be fair to the author.** Neither drift is
> incompetence. The first is what happens when a design language evolves and an
> invariant lives in a different file. The second is what happens when a
> mock-up's placeholder survives into production because it looks like the three
> real fields beside it. **Both are ordinary. Recording them is the discipline.**

---

# PART B — DEAD CODE

## 5. The method, before the case

This is the transferable half. Learn the **order of operations**, then apply it.

### 5.1 Four categories, kept apart

| Category | Definition | Test |
|---|---|---|
| **FACT** | Something you can re-derive right now with a command | *"What command reproduces this?"* |
| **EVIDENCE** | A fact that bears on the question | *"Which claim does this support or weaken?"* |
| **INFERENCE** | A conclusion you drew from evidence | *"What would have to be true for this to be wrong?"* |
| **UNKNOWN** | A question the repository cannot answer | *"Which person or artefact outside the repo holds this?"* |

**The failure mode is not being wrong. It is category slippage** — an inference
written in the grammar of a fact, so the next reader inherits a conclusion with
its uncertainty stripped off.

You have already seen this handled well twice: ED-023's *"Likely rationale —
inferred"*, and CAVEAT-001's *"The repository does not state why. **Likely
rationale — inferred:**"*. **Steal that formatting.** A labelled inference is
useful; an unlabelled one is a rumour.

---

### 5.2 What is dead code?

**Working definition:** code that cannot be reached by any execution of the
shipped program.

Distinguish three neighbours that are *not* dead code:

| Not dead code | Why |
|---|---|
| **Unused arguments / fields** | Reached; simply unread. `CorpusStatus.documents` (§6.4) |
| **Feature-flagged code** | Reachable under a configuration |
| **A metric with no producer** | Executes on every request and returns 0.0 — `cache_hit_rate_pct` (CAVEAT-009). **This is live code with no input**, an entirely different problem |

**An orphaned component** is the frontend's specific case: a component that
exports correctly, compiles, and is imported by nothing that the entry point can
reach.

---

### 5.3 How to establish it — five checks, in order

**1. Static references.**

```bash
grep -rn "AnswerCard\|CorpusPanel\|ConfidenceBadge" frontend/app frontend/components frontend/lib
```

**2. Dynamic references** — the check that grep for a name will miss.

```bash
grep -rn "dynamic(\|React.lazy\|await import(\|require(" frontend/app frontend/components frontend/lib
```

A string-keyed component map (`COMPONENTS[data.path]`) or `next/dynamic` would
mount a component whose name appears nowhere near a JSX tag.

**3. Entry points.** Is there another root that could mount it?

```bash
find frontend/app -type f
```

**4. Other refs** — branches, stashes, submodules, and anything outside the
working tree.

```bash
git branch -a
git stash list
git log --all --oneline -S "AnswerCard" -- frontend/app
```

**5. Build output.** Does it ship?

```bash
docker compose exec -T -w /app frontend node_modules/.bin/tsc --noEmit
```

**And know what step 5 does *not* prove.** `tsc --noEmit` type-checks every file
under `include`, whether or not it is reachable. **A clean typecheck is
compatible with a component that no user will ever see.** That asymmetry — the
compiler validates unreachable code as carefully as reachable code — is the
single reason dead frontend code survives.

---

## 6. The case: `AnswerCard`, `ConfidenceBadge`, `CorpusPanel`

### 6.1 FACTS — reproducible right now

**F-1. No importer.**

```bash
grep -rn "AnswerCard\|CorpusPanel\|ConfidenceBadge" frontend/app frontend/components frontend/lib
```

```
components/ConfidenceBadge.tsx:1:interface ConfidenceBadgeProps {
components/ConfidenceBadge.tsx:18:export default function ConfidenceBadge(...)
components/CorpusPanel.tsx:10:export default function CorpusPanel(...)
components/AnswerCard.tsx:1:import ConfidenceBadge from "./ConfidenceBadge";
components/AnswerCard.tsx:17:export default function AnswerCard(...)
components/AnswerCard.tsx:56:  … ConfidenceBadge, whose TIER_STYLES lookup …
components/AnswerCard.tsx:61:  <ConfidenceBadge tier={…} verified={…} />
```

**Self-references only.** The one import of `ConfidenceBadge` is from
`AnswerCard`, which nothing imports.

**F-2. No dynamic imports anywhere in the frontend.** The step-2 grep returns
zero lines across `app/`, `components/` and `lib/`.

**F-3. One entry point.** `frontend/app/` holds `page.tsx`, `layout.tsx`,
`globals.css` (Day 38). There is no second page.

**F-4. One branch, no stashes.** `git branch -a` → `main` and its remote.
`git stash list` → empty.

**F-5. The exact commit that orphaned them.**

```bash
git log --oneline -S "AnswerCard" -- frontend/app/page.tsx
```

```
9ce004a  connected page design
6b45b8c  Create page.tsx
```

Two commits: the one that introduced the reference, and the one that removed it.

```bash
git show 9ce004a -- frontend/app/page.tsx | grep -E "^[-+]import"
```

```
-import SearchBar from "@/components/SearchBar";
-import AnswerCard from "@/components/AnswerCard";
-import PipelineTrack from "@/components/PipelineTrack";
-import CorpusPanel from "@/components/CorpusPanel";
+import { DocumentEnvironment } from "@/components/document/DocumentEnvironment";
+import { DocumentPage } from "@/components/document/DocumentPage";
+import { WorkingPaperHeader } from "@/components/document/WorkingPaperHeader";
…
```

**`9ce004a`, 2026-07-22 12:26 IST, "connected page design".** One commit removed
**four** component imports and added the `components/document/*` set.

**F-6. Two of those four were later deleted; two were not.**

| Component | Orphaned | Deleted | Commit |
|---|---|---|---|
| `SearchBar` | `9ce004a`, 07-22 | **yes**, 07-25 | `c464bd2` *chore: remove dead SearchBar.tsx component* |
| `PipelineTrack` | `9ce004a`, 07-22 | **yes**, 07-28 | `3b0306d` *chore(frontend): remove dead PipelineTrack.tsx — orphaned, never imported, pre-redesign token set* |
| `AnswerCard` | `9ce004a`, 07-22 | **no** | — |
| `CorpusPanel` | `9ce004a`, 07-22 | **no** | — |

**F-7. `AnswerCard` received two correctness commits after being orphaned**, both
on 2026-08-23, thirteen months of project-time later:

- `63d7f40` *"F14(frontend): AnswerCard reads companies, not the removed scalar"*
- `945b7d4` *"fix(frontend): AnswerCard does not hand ConfidenceBadge a tier that
  was never computed"*

Its sibling commits in the same series — `6b4fc17` (`AuditLogTable.tsx`) and
`017d97e` (`lib/api.ts`) — both touched **live** code and were correct.

**F-8. `tsc --noEmit` passes.** All three files type-check cleanly.

**F-9. `CorpusPanel` reads fields no backend response contains.**

```tsx
const FALLBACK: CorpusStatus = {
  companies: 4,
  filings: 5,
  chunksIndexed: 1021,
  lastIngestedLabel: "2h ago",
};
…
[s.chunksIndexed.toLocaleString(), "Chunks indexed"],
[s.lastIngestedLabel, "Last ingested"],
```

But `CorpusStatus` in `lib/api.ts` declares `total_chunks?`, `chunks?`,
`last_updated?` — **not** `chunksIndexed` or `lastIngestedLabel`. It compiles
only because of the escape hatch on the last line of the interface:

```ts
export interface CorpusStatus {
  companies: number;
  filings?: number;
  documents?: number;
  total_chunks?: number;
  chunks?: number;
  last_updated?: string;
  status?: string;
  [key: string]: any;      // ← this is why F-8 is true
}
```

**F-10. Nothing produces a `CorpusStatus`.** No function in `lib/api.ts` returns
one; `grep -rn "CorpusStatus"` finds the declaration and `CorpusPanel` only; the
backend registers four routers (`auth`, `query`, `metrics`, `documents`) plus
`/health`, and none serves a corpus-status shape.

---

### 6.2 EVIDENCE — what those facts bear on

**Unreachability is settled.** F-1 through F-4 close it: no static reference, no
dynamic reference, no second entry point, no other branch. This is not a
judgement; it is a finite search that returned empty.

**"Superseded by the document UI" is now evidenced, not guessed.** F-5 is the
key. `KU-004` recorded this as *"Current hypothesis. (Guess.)"* — because at the
time it was one. **It is no longer a guess:** a single commit removed all four
old imports and added the working-paper set in the same diff. The repository
proves the **what** and the **when**.

**"They were not known to be unreachable" is weakened, not strengthened.** KU-004
reasoned that `945b7d4` *"suggests they were not known to be unreachable at the
time."* F-6 pulls the other way: two siblings orphaned by the same commit **were
found and deleted**, with commit messages that say "dead" and "orphaned, never
imported". So the project *did* notice orphans from this exact commit — twice —
and acted. That makes "nobody knew" a weaker reading of `945b7d4` than it looked.

**And it does not settle it either.** `945b7d4` remains a real correctness fix
applied to a non-rendering component. Both readings survive: a sweep that
touched every `confidence_tier` consumer without checking reachability, or a
deliberate keep-it-current decision. **The evidence narrows the question. It does
not answer it.**

---

### 6.3 INFERENCE — labelled as such

> **INFERENCE (high confidence).** All three were superseded by the
> `components/document/` working-paper UI introduced in `9ce004a`. *Basis:* F-5,
> a single commit swapping one component set for the other. *What would falsify
> it:* an importer outside the working tree, or evidence that the document UI was
> intended to coexist with them.

> **INFERENCE (medium confidence).** `ConfidenceBadge` is orphaned *indirectly* —
> it has no defect of its own; it is unreachable purely because its only importer
> is. *Basis:* F-1. *Consequence:* it is the one of the three most likely to be
> genuinely reusable.

> **INFERENCE (low confidence).** The `945b7d4` and `63d7f40` fixes were applied
> without checking reachability, as part of a sweep over every
> `confidence_tier` / `companies` consumer. *Basis:* their sibling commits in the
> same series both touched live code. **This is a guess about a person's process
> and is exactly the kind of claim to leave labelled.**

---

### 6.4 UNKNOWN — and it is one question, not three

**The question the repository cannot answer:**

> Why were `AnswerCard`, `ConfidenceBadge` and `CorpusPanel` *kept*, when
> `SearchBar` and `PipelineTrack` — orphaned by the same commit — were deleted?

Searched and absent: no commit message, no code comment, no entry in
`ENGINEERING_DECISIONS.md`, `IMPLEMENTATION_DELTAS.md`, `SESSION_LOG.md` or
`docs/journal/`.

**Do not manufacture an answer.** Every plausible story — "kept for reference",
"planned to bring back", "just missed" — is a story about a person's intent, and
**the repository contains no intent, only artefacts**. `CAVEAT-026` says this
explicitly:

> **What the evidence does *not* establish.** Nothing in the git history or in
> any document states *why* these three are unreferenced … **Do not assert a
> reason.** The repository does not contain one.

**Only the author can close this.** That is why `KU-004`'s "How to verify" reads
*"Ask the author."*

---

### 6.5 Would deletion be safe?

**Technically: yes, and the search that establishes it is complete.**
F-1 (no static ref) + F-2 (no dynamic ref) + F-3 (one entry point) + F-4 (one
branch, no stashes) exhausts the ways a React component can be reached in this
project.

**And they ship nothing today.** Next.js bundles from the import graph rooted at
`app/`. Three unimported modules are not in any chunk. The cost is not bytes.

**The cost is attention**, and CAVEAT-026 prices it:

> Real as a maintenance cost: it consumed one commit of correctness work, and a
> reader looking for "where is the answer rendered" finds a plausible wrong
> answer first.

**Both halves of that are measurable.** One commit was spent (F-7). And
`AnswerCard` is a *convincing* wrong answer: 139 lines, correct-looking, with a
comment explaining a subtle `confidence_tier` guard. A newcomer grepping for
`crag_triggered` finds it there and nowhere else.

**But the decision is not being made today, and the reason is not technical.**

```
Status: Open by decision (2026-08-23). Not to be deleted or modified.
```

**Two reasons, and they are different.** First, the *why-kept* question (§6.4) is
open, and deleting closes it permanently — `git revert` recovers the file, but
nobody asks a question they no longer see. Second, and more concretely: these
three are **the course's worked example**. Deleting them would delete this
lesson.

**Before deleting — the checklist**, from CAVEAT-026 plus what today added:

1. `grep` for static references — `app/`, `components/`, `lib/`.
2. `grep` for `dynamic(`, `React.lazy`, `await import(`, `require(`.
3. Confirm the entry points: `find app -type f`.
4. `git branch -a`, `git stash list`, and any unpushed work.
5. **Ask the author** — the only step that resolves KU-004.
6. Delete `CorpusStatus` in the same commit, or it becomes a dead type with a
   live-looking declaration (F-10).
7. Record the decision, with the evidence, in the commit message. `3b0306d` is
   the model: *"orphaned, never imported, pre-redesign token set."*
8. Re-run `tsc --noEmit` — knowing it proves compilation, not reachability.

---

### 6.6 What each tool can and cannot establish

**Internalise this table.** It is the day's transferable content.

| Tool | Establishes | Cannot establish |
|---|---|---|
| `grep -rn <Name>` | Static textual references | Dynamic/string-keyed references; references outside the searched paths |
| `grep dynamic(\|lazy\|import(` | Absence of the common dynamic patterns | A bespoke registry built some other way |
| `find app -type f` | The route surface | Anything about non-route entry points in another framework |
| `tsc --noEmit` | Types are consistent | **Whether anything is reachable.** It validates dead code just as carefully |
| `git log -- <file>` | Every commit that touched the file | Why |
| **`git log -S "<string>"`** | **The commits where a string's count changed** — i.e. when a reference appeared and disappeared | Why |
| `git show <c>` | The exact diff | The author's reasoning beyond the message |
| `git branch -a` / `stash list` | Other refs in this clone | Refs in a clone you do not have |
| Commit messages | What the author chose to write | What they chose not to |
| **A person** | **Intent** | — |

**`git log -S` is the one to remember.** It is what turned KU-004's guess into
evidence. `git log -- <file>` shows commits that touched a file; `git log -S
"<string>" -- <file>` shows commits where the number of occurrences of that
string **changed** — which is how you find the commit where a reference was
*removed*, in a file that was never itself deleted.

---

## 7. The actual LedgerMind files

```
File:  frontend/app/page.tsx  — the render half
Entry: composeDocumentBody(data: QueryResponse) -> JSX     THE ONLY PATH-AWARE FN
       buildCitationItems(data)      index · label · page · relevance · anchor id
       issuerLabel(companies)        null for [], never a stand-in
       cleanProseText(text)          strips the trailing "Sources:" block and **bold**
       cleanBlockReason(reason)      strips the "category: " prefix
Branches: is_blocked · error · "entity1" in row · "entity_a" in row ·
          path==="quantitative" · fallthrough (narrative)

File:  frontend/components/document/WorkingPaperHeader.tsx (91)   Tier 4
Note:  three-state `companies`, and ONE hardcoded field — CAVEAT-027

File:  frontend/components/document/EvidenceList.tsx (24)
Note:  `relevance?` optional because role_filtered_response strips it

File:  frontend/components/document/AuditLogTable.tsx (97)
Note:  the em-dash idiom, twice, for two different reasons

── the case study, RETAINED UNTOUCHED (CAVEAT-026) ──
frontend/components/AnswerCard.tsx      (139)  unreachable
frontend/components/ConfidenceBadge.tsx  (30)  unreachable via AnswerCard only
frontend/components/CorpusPanel.tsx      (33)  unreachable; reads fields no
                                               endpoint produces (F-9, F-10)
```

---

## 8. Deep walkthrough — one narrative answer becomes a page

**STATE BEFORE.** `data` is a `QueryResponse` with `path: "semantic"`,
`error: null`, `is_blocked: false`, `sql_result: null`, four citations.

**Step 1 — branches 1 and 2 miss.**

**Step 2 — the two duck-typed flags are computed before use.**

```tsx
const isComparativeResult     = data.sql_result?.[0] && "entity_a" in data.sql_result[0];
const isPlainComparisonResult = data.sql_result?.[0] && "entity1"  in data.sql_result[0];
```

`?.[0]` short-circuits on `null` **and** on `[]`. Both are `undefined` here.

**Step 3 — branch 5 misses** (`path !== "quantitative"`), so control reaches the
fallthrough.

**Step 4 — citations become display items.**

```tsx
const citationItems = buildCitationItems(data);
```

For each citation, in order:

```tsx
index: i + 1,                               // 1-based, display order
label: hasFinancialType
  ? `${c.company} ${c.fiscal_year} (${ft})` // tag present ⇒ meaningful
  : `${c.company} ${c.fiscal_year}`,        // omitted, not "(unknown)"
page: c.page_number,
relevance: c.reranker_score,                // undefined for a viewer
id: `cite-${c.chunk_id}`,                   // the DOM anchor (Day 38 §7)
```

**`index` is positional and nothing else.** It is not a chunk id, not a rank
score, not stable across queries. Its only contract is that the superscript and
the footnote agree — which they do because both are built from this one array.

**Step 5 — the narrative body.**

```tsx
<CitationSummary count={citationItems.length} />
<AnalysisSection
  paragraphs={[{
    text: cleanProseText(data.response_text ?? ""),
    citations: citationItems.map((c) => ({ index: c.index, anchorId: c.id })),
  }]}
/>
<EvidenceList items={citationItems} />
```

**Three things worth noticing.**

**(a) `cleanProseText` strips the model's own citation block:**

```tsx
.replace(/\n\nSources:[\s\S]*$/, "")
```

The backend appends a formatted `Sources:` block to `response_text` (Day 30).
The UI **removes it and rebuilds it** from the structured `citations` array.
**The rendered evidence list is never parsed out of model prose** — it is built
from data. That is the mandate applied to the single place it would be easiest
to violate.

**(b) One paragraph, all citations.** `paragraphs` is an array of one, and every
superscript is appended at the end of that block. So the superscripts do **not**
mark *which sentence* each source supports.

**Say that precisely, because it matters.** The claim the UI makes is *"these
sources support this answer"*, not *"source 2 supports this clause"*. **That is
the honest claim**, given that the backend does not produce sentence-level
attribution. A per-sentence mapping would be a *stronger* claim than the data
supports — the mandate again, in a place it is easy to miss because the weaker
rendering looks like a limitation rather than a decision.

**(c) `?? ""` on `response_text`.** A null becomes an empty paragraph, not the
string `"null"`.

**STATE AFTER.** A `<div style={{maxWidth: "74ch"}}>` — a measure chosen for
reading, not for the viewport — holding a count line, a justified paragraph with
superscripts, and numbered footnotes carrying `id="cite-<uuid>"`.

---

## 9. Data flow

```
QueryResponse (Day 39)
   │
   ▼ composeDocumentBody(data)          ← THE ONLY PATH-AWARE FUNCTION
   │
   ├─1 is_blocked ─────► MetricCallout("Not Permitted", "Policy Block", refused)
   │                     + AnalysisSection(cleanBlockReason(block_reason))
   │
   ├─2 error ──────────► MetricCallout(error.replace(/_/g," "), "—", refused)
   │                     + CitationSummary + AnalysisSection + EvidenceList
   │                     (citations still rendered — a refusal may cite)
   │
   ├─3 "entity1" in row ► EntityComparisonTable(entity1, entity2, one row)
   │                     + MetricCallout("Higher Reported Value", winner)
   │
   ├─4 "entity_a" in row► EntityComparisonTable(entity_a, entity_b, YoY row)
   │                     + MetricCallout("Faster Growing", faster_growing_entity)
   │
   ├─5 path==="quantitative"
   │                    ► SectionHeading(issuerLabel(companies) — fiscal_year)
   │                     + LedgerTable(prior / current, Δ YoY)
   │                     + MetricCallout("Result", ₹x Cr,
   │                          sql_verified ? "verified" : "estimated")   ← DAY 34
   │                     + AnalysisSection
   │
   └─default ──────────► CitationSummary + AnalysisSection + EvidenceList
                          cleanProseText strips the model's "Sources:" block;
                          the evidence list is rebuilt FROM DATA
```

**One arrow deserves its own note.** In branch 5:

```tsx
status={data.sql_verified ? "verified" : "estimated"}
```

**A single boolean from Day 34 chooses between a ✓ and a ~.** That is the entire
UI surface of the verification guarantee, and it is why `sql_verified` must never
be set optimistically. The frontend has no way to second-guess it.

---

## 10. Engineering decision — one path-aware function, and dead code retained

**Decision A — the render boundary.** `composeDocumentBody()` is the only
path-aware frontend function. **ED-024**, `CLAUDE.md` §6.

| Alternative | Why not |
|---|---|
| Each component reads `QueryResponse` | Twenty files change per new path; none is testable from props alone |
| A `path`-keyed component map | The real branches are `is_blocked`, `error`, two row shapes, **then** `path`. Four of five checks survive the refactor (Day 38 §8) |
| A discriminated union on `sql_result` | **Genuinely better**, and it needs a backend change: the compute functions would emit a `kind` field. Removes the duck-typing (§4.2) |
| Render the model's own `Sources:` prose | Evidence parsed out of generated text. Directly against the mandate |

**Decision B — retain the three unreachable components.**

| Alternative | Why not / why not yet |
|---|---|
| Delete now | The *why-kept* question closes with them, and they are this lesson's worked example |
| Delete after asking the author | **The correct sequence.** Step 5 of §6.5 |
| Restore them to use | Nothing needs them; the document UI covers every case |
| Fix `CorpusPanel`'s FALLBACK | It would be functional work on unreachable code — the exact thing `945b7d4` already did once |

**Trade-offs accepted.**

- **A convincing wrong answer sits in `components/`** for anyone grepping.
- **`CorpusStatus` is a dead type with a live-looking declaration** in
  `lib/api.ts` (F-10), and it will outlive the components unless deleted with
  them.
- **The duck-typed row shapes** cross a language boundary with nothing enforcing
  them.
- **Two documented invariants no longer hold** (§4.4), and are recorded rather
  than fixed.

**At 10×.** With ten paths and three products, the duck-typing is the first thing
to break, and a `kind` discriminator on `sql_result` is the fix — a backend
change first, a frontend simplification second.

---

## 11. Failure modes

| Symptom | Cause |
|---|---|
| A blocked query renders as an error | Branch order — `error` tested before `is_blocked` |
| A two-entity comparison renders as a single ledger row | Branch 5 tested before 3/4 |
| A comparison silently renders as narrative | A key renamed backend-side (`entity1` → `entity_1`). No type error, no test |
| `(unknown)` on every citation | The `financial_type` guard removed |
| `NO ISSUER RESOLVED — SEARCH UNFILTERED` on a normal answer | `companies` is `[]` — a **backend** signal, not a UI bug. `_build_filter` dropped the filter (Day 27) |
| Every header reads a corpus name | A "safe default" restored after F14 |
| `Source Table:` names a relation nobody can find | CAVEAT-027(c) — a hand-written literal, not a derived field |
| An empty confidence cell | `{undefined}` rendered directly instead of `?? "—"` |
| `relevance NaN` for a viewer | `.toFixed()` called without the `!= null` guard |
| The evidence list disagrees with the prose | `cleanProseText` no longer strips `Sources:`, so both are shown |
| Deleting a component breaks the build | It was **not** dead — step 2's dynamic grep was skipped |

---

## 12. Hands-on experiment

### Experiment 1 — verify the render boundary yourself

```bash
cd frontend
grep -rn "\.path\b\|sql_result\|sql_verified\|is_blocked" components/ | grep -v node_modules
echo "--- expected: nothing ---"
grep -c "data\." app/page.tsx
```

### Experiment 2 — trace one rendered value back to its producer

Run a quantitative query in the browser and find the ✓ beside the result. Then:

```bash
grep -n "sql_verified ? \"verified\"" app/page.tsx
```

Now trace backwards, naming a file at each hop: `MetricCallout` ← `page.tsx`
← `lib/api.ts` ← `role_filtered_response` ← `QueryState["sql_verified"]`
← `quant_engine_node` (Day 34). **Six hops, and every one is a real file.**

### Experiment 3 — find an omission and name what a substitute would assert

```bash
grep -n "hasFinancialType" -B 8 app/page.tsx
```

Answer in one sentence: *what would `(unknown)` have asserted about a risk
disclosure?*

### Experiment 4 — the unreachability proof, all five checks

```bash
cd frontend
echo "── 1. static references ──"
grep -rn "AnswerCard\|CorpusPanel\|ConfidenceBadge" app components lib
echo "── 2. dynamic references ──"
grep -rn "dynamic(\|React.lazy\|await import(\|require(" app components lib || echo "(none)"
echo "── 3. entry points ──"
find app -type f
echo "── 4. other refs ──"
cd .. && git branch -a && git stash list && echo "(stash list above; empty = nothing)"
echo "── 5. typecheck ──"
docker compose exec -T -w /app frontend node_modules/.bin/tsc --noEmit && echo "TYPECHECK CLEAN"
```

**Step 5 passes.** Write down, before reading on, what that does and does not
prove.

### Experiment 5 — find the commit that orphaned them

```bash
git log --oneline -S "AnswerCard" -- frontend/app/page.tsx
git show 9ce004a -- frontend/app/page.tsx | grep -E "^[-+]import"
git log -1 --format="%h %ad %s" --date=iso-strict 9ce004a
```

**Then the sibling components' fate:**

```bash
git log --all --oneline --diff-filter=D --name-only -- 'frontend/components/*' | head -20
```

Four imports removed by one commit; two of the four files deleted days later,
with commit messages that say so.

### Experiment 6 — prove the typecheck hole in `CorpusPanel`

```bash
cd frontend
grep -n "chunksIndexed\|lastIngestedLabel" components/CorpusPanel.tsx
grep -n "key: string" lib/api.ts
```

`chunksIndexed` is not a declared field of `CorpusStatus`. It compiles because of
`[key: string]: any`. **Now reason it through:** if `CorpusPanel` were rendered
with a real API-shaped object, `s.chunksIndexed` would be `undefined` and
`.toLocaleString()` would throw. **A typecheck-clean runtime crash, in a
component that cannot run.**

### Experiment 7 — the two drifts, measured

```bash
cd ..
echo "── invariant (CLAUDE.md §6) ──"
grep -n "Glass/blur" CLAUDE.md
echo "── reality ──"
grep -rn "backdropFilter\|backdrop-blur\|blur(" frontend/app frontend/components
echo "── how it moved ──"
git log --oneline -S "backdropFilter" -- frontend
echo "── the hardcoded date ──"
grep -n "Generated:" frontend/components/document/WorkingPaperHeader.tsx
echo "── the source table ──"
grep -n "sourceTable" frontend/app/page.tsx
grep -n "CREATE TABLE" sql/init.sql
grep -rn "audited_financials" backend/ sql/ docs/ CLAUDE.md || echo "(nowhere else)"
```

**Do not fix any of them.** Read CAVEAT-027, then re-read
`CODE_DOCUMENTATION_LOG.md` rule 5.

### Experiment 8 — write the four categories yourself

Take a blank file. For the glass/blur drift, write four sections — **FACT**,
**EVIDENCE**, **INFERENCE**, **UNKNOWN** — with at least one line each, and put a
reproducing command under every FACT. Then compare with §4.4.

**If you cannot put a command under a FACT, it is an inference.** That is the
test.

---

## 13. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `frontend/app/page.tsx` and `frontend/components/AnswerCard.tsx`:

1. List `composeDocumentBody`'s branches in order. For each, name the field it
   tests and the backend node that sets it. Then: **swap any two and say what
   renders wrongly.**
2. Find three places the UI omits a field. For each, write the sentence a
   substitute would have asserted.
3. Establish that `AnswerCard` is unreachable, using **five** checks. Say what
   each check would have caught that the others would not.
4. Find the commit that orphaned it — by command, not by memory — and say which
   two of its four siblings were subsequently deleted.
5. Write the FACT / EVIDENCE / INFERENCE / UNKNOWN split for "why were these
   three kept?" **Your UNKNOWN section must not be empty.**

---

## 14. Self-check questions

**Basic**

1. What is `composeDocumentBody` the only function aware of?
2. State the Zero UI-Hallucination Mandate in one sentence.
3. What does `issuerLabel` return for `[]`, and why not a string?
4. What is an orphaned component?
5. Which two of the four components orphaned by `9ce004a` were deleted?

**Code**

6. Why are the two comparison flags computed before either is tested?
7. What does `.filter(Boolean)` do in the quantitative `SectionHeading`?
8. Why does `AuditLogTable` render `—` rather than nothing?
9. Why does `cleanProseText` strip the `Sources:` block?
10. Why does `CorpusPanel` type-check despite reading undeclared fields?

**Why**

11. Why is branch order semantics rather than style?
12. Why is `"NO ISSUER RESOLVED — SEARCH UNFILTERED"` better than a corpus name?
13. Why does one paragraph carry every superscript, and why is that the honest
    rendering?
14. Why does `tsc --noEmit` passing say nothing about reachability?
15. Why must the reason these three were kept stay UNKNOWN?

**Debugging**

16. A two-entity comparison renders as plain narrative. Walk the diagnosis.
17. Every working paper reads `GENERAL CORPUS ARCHIVE`. What happened, and what
    class of change caused it?
18. A colleague deletes an "obviously unused" component and the build breaks.
    Which check did they skip, and what would it have shown?

**System design**

19. Design the removal of the duck-typed row shapes. Name every file, say what
    the backend emits, and state what the change does **not** fix.
20. Design a mechanism that would have caught these orphans automatically. State
    its false-positive mode and whether it belongs in this project **today**.

---

## 15. Answer key

> **Only read after attempting.**

### §13

1. **Order:** `is_blocked` (`prompt_shield_node`) → `error` (any node) →
   `"entity1" in sql_result[0]` (`comparison`) → `"entity_a" in sql_result[0]`
   (`growth_comparison`) → `path === "quantitative"` (`router_node` +
   `point_in_time`/`yoy`) → fallthrough (semantic/cross).
   **Swaps:** move `error` above `is_blocked` and a **policy block renders as a
   refusal** — the user is told the corpus lacked something when in fact the
   question was declined on compliance grounds, which is a materially different
   statement. Move branch 5 above 3/4 and a **two-entity comparison renders as a
   single-value ledger table**, because a comparison result is also
   `path === "quantitative"`.
2. Any three of: **(a)** `financial_type` tag omitted when `"unknown"` — a
   substitute asserts *"we tried to classify this and failed"* when the truth is
   *"this distinction does not apply to a risk disclosure"*. **(b)** `issuerLabel`
   returns `null` for `[]` — a substitute asserts *"this answer is about a named
   issuer"* when in fact **no issuer resolved and retrieval ran unfiltered**.
   **(c)** `confidence_tier` omitted on a block — a substitute (`"low"`) asserts
   *"we scored this and it came out low"* when nothing scored it. **(d)**
   `relevance` omitted for viewers — a substitute (`0.00`) asserts *"this
   citation is irrelevant"* when the score was withheld by role.
3. **(1)** static grep — direct JSX/import references; **(2)** dynamic grep —
   `next/dynamic`, `React.lazy`, string-keyed maps, which carry no textual
   reference near a tag; **(3)** entry points — a second `app/**/page.tsx` would
   be an independent root the first two greps would still miss if scoped wrongly;
   **(4)** branches/stashes — work outside the current tree, which no grep of the
   tree can see; **(5)** typecheck — that removal will not break compilation,
   which is about *validity*, not reachability. **No single check subsumes
   another.**
4. `git log --oneline -S "AnswerCard" -- frontend/app/page.tsx` → `9ce004a`
   *"connected page design"*, 2026-07-22. Its four siblings: `SearchBar`
   (**deleted** `c464bd2`, 07-25), `PipelineTrack` (**deleted** `3b0306d`,
   07-28), `AnswerCard` and `CorpusPanel` (**not deleted**).
5. **FACT:** unimported; orphaned by `9ce004a`; two siblings deleted; two
   correctness commits landed on `AnswerCard` on 2026-08-23; one branch, no
   stashes. **EVIDENCE:** the single-commit swap evidences supersession; the two
   deletions evidence that this project **does** find and remove orphans from
   this exact commit. **INFERENCE (high):** superseded by the document UI.
   **INFERENCE (low, labelled):** the August fixes were a sweep that did not
   check reachability. **UNKNOWN:** *why these three were kept when two siblings
   were deleted.* No commit message, comment or document states it; only the
   author can.

### §14 — Basic

1. Path and engine internals — `path`, `sql_result` shapes, `sql_verified`,
   `error`, `is_blocked`.
2. No badge, count, stat or citation number may exist as static copy; every one
   is wired to a real backend field, and where a field is absent the UI **omits**
   rather than substitutes.
3. `null` — because `[]` means **no issuer resolved and the search ran
   unfiltered across the tenant**, and printing any name would assert something
   the system never produced.
4. A component that exports correctly and compiles, but is imported by nothing
   reachable from the application's entry point.
5. `SearchBar` (`c464bd2`) and `PipelineTrack` (`3b0306d`).

### §14 — Code

6. Both are needed as booleans in a fixed evaluation order; computing them
   together keeps the two `sql_result?.[0]` guards adjacent and readable, and
   makes the mutual exclusivity of branches 3 and 4 visible at a glance.
7. Drops `null` (from `issuerLabel`) before the `join(" — ")`, so an unresolved
   issuer disappears from the heading instead of producing a dangling separator.
8. Because a bare `{undefined}` renders an **empty cell**, which reads as a
   rendering fault. Omission still needs a glyph: omit the *claim*, not the
   *cell*.
9. Because the evidence list is rebuilt **from the structured `citations`
   array**, not parsed out of model prose. Leaving both would show two evidence
   lists, one of which is generated text.
10. `CorpusStatus` ends with `[key: string]: any`, so any property name
    type-checks as `any`.

### §14 — Why

11. Because the categories overlap: a blocked query may carry an `error`, and a
    comparison result is also `path === "quantitative"`. The order encodes
    which classification is the more specific claim about the answer.
12. Because an empty `companies` is a fact about **scope**, not about identity.
    A corpus name reads as "this is the archive this answer came from", when the
    truth is "no filter was applied and this searched everything". After F14 that
    fallback fired on every render, which is how it was found.
13. Because the backend produces **answer-level** citations, not sentence-level
    attribution. Attaching a superscript to a specific clause would assert a
    mapping the data does not contain — a *stronger* claim than the evidence
    supports, which is precisely what the mandate forbids.
14. Because `tsc` checks every file matched by `include`, reachable or not. It
    validates dead code exactly as carefully as live code — which is the reason
    dead frontend code survives at all.
15. Because the repository contains **artefacts, not intent**. Every available
    story is a claim about a person's reasoning, and none is recorded anywhere.
    Asserting one converts a labelled unknown into an inherited false certainty.

### §14 — Debugging

16. **(1)** Confirm `path === "quantitative"` and that `sql_verified` is true —
    if not, this is a backend refusal, not a rendering fault. **(2)** Read
    `sql_result[0]`'s **keys** from the API response directly (admin token; the
    field is analyst+). **(3)** Compare them against `"entity1"` and
    `"entity_a"`. **(4)** If neither is present, the compute function's row shape
    changed — `dsl_compiler.py` / the compare functions (Day 33) — and the
    duck-typing fell through to the narrative branch. **(5)** Note what did *not*
    help: `tsc` is clean, because `sql_result` is
    `Record<string, unknown>[]`; and there is no frontend test.
17. `companies` is `[]` **or** the header's fallback was restored. After F14 the
    scalar `company` stopped existing, so every read of it was falsy and the old
    fallback became the label on 100 % of renders. **Class:** a schema change
    weaponising an existing fallback — the fallback itself did not change.
18. **Check 2, the dynamic-reference grep.** A `next/dynamic` import or a
    string-keyed component map (`COMPONENTS[data.path]`) references a component
    with no textual mention near a JSX tag, so the static grep in check 1 comes
    back clean.

### §14 — System design

19. **Backend.** Every compute function in `dsl_compiler.py` adds a `kind` field
    to its result row: `"point_in_time"`, `"yoy"`, `"comparison"`,
    `"growth_comparison"`. It rides through `sql_result` untouched — no change to
    `quant_engine`, `response_shaping` or the audit write, since the row is
    already opaque to all three.
    **Frontend.** `lib/api.ts` declares a discriminated union
    (`type SqlRow = PointInTimeRow | YoyRow | ComparisonRow | GrowthComparisonRow`)
    keyed on `kind`. `composeDocumentBody` switches on `row.kind`, and TypeScript
    narrows the row type inside each branch — so `row.entity_a` is a compile error
    in the wrong branch, and the four `any` casts disappear.
    **Also changes:** `cross_engine.py`'s own shape-sniffing (`"value" in
    result_row`, Day 37) can read `kind` instead — the same defect, fixed once.
    And `tests/test_quant_dsl_binding.py` gains an assertion that every compute
    function emits its `kind`.
    **What it does NOT fix:** nothing *enforces* that the TS union matches the
    Python emitters. It converts a silent wrong render into a **compile error on
    the frontend when the frontend is wrong**, which is a large improvement — but
    a backend that emits an unknown `kind` still falls through. The only complete
    fix is generating the TS types from a shared schema, which is a build-system
    change this project does not have.
20. **The mechanism.** A dead-export check — `ts-prune`, `knip`, or ESLint's
    `import/no-unused-modules` — run over `frontend/`, reporting exported symbols
    with no importer, seeded from `app/**` as the entry points.
    **False-positive mode**, and it is the reason these tools are usually
    abandoned: entry points are **conventional**, not declarative. `app/page.tsx`
    and `app/layout.tsx` are roots because Next.js says so, not because anything
    imports them, so an unseeded run reports the entire application as dead. Add
    `next.config.js`, `middleware.ts`, `instrumentation.ts` or a route handler and
    each is a new root that must be configured in. **Every false positive
    pressures someone to add an ignore, and enough ignores make the tool
    ornamental.**
    **Does it belong here today?** **No — and the honest reason is CAVEAT-022.**
    There is no CI. An unrun check is not a check (`CLAUDE.md` §5's *"a check
    satisfied by absence is not a check"*). Adding a tool nobody executes trades a
    known, documented, three-file problem for an unknown, unenforced one. **The
    sequence is CI first, then this — and even then the payoff here is three
    files.**

---

## 16. MUST REMEMBER

```text
- composeDocumentBody() is the ONLY path-aware frontend function (ED-024)
- FIVE branches, in order: is_blocked · error · "entity1" · "entity_a" ·
  path==="quantitative" · then narrative. ORDER IS SEMANTICS
- Branches 3 and 4 DUCK-TYPE a SQL row across a language boundary. No type
  error, no test, if a backend key is renamed
- OMIT rather than substitute — but omission still needs a glyph. `—`, not
  an empty cell
- issuerLabel returns null for []; .filter(Boolean) removes it from the heading
- WorkingPaperHeader has THREE states: null / [] / [names]. `[]` means
  RETRIEVAL RAN UNFILTERED
- cleanProseText strips the model's "Sources:" block; the evidence list is
  rebuilt FROM DATA, never parsed from prose
- ONE paragraph carries every superscript, because the backend produces
  answer-level citations, not sentence-level attribution
- Five checks for dead code: static grep · dynamic grep · entry points ·
  branches/stashes · typecheck
- `tsc --noEmit` proves TYPES, never REACHABILITY. It validates dead code
  just as carefully
- `git log -S "<string>" -- <file>` finds when a REFERENCE was removed. That
  is the command that turned KU-004's guess into evidence
- 9ce004a (2026-07-22, "connected page design") orphaned FOUR components.
  SearchBar and PipelineTrack were deleted; AnswerCard and CorpusPanel were not
- AnswerCard · ConfidenceBadge · CorpusPanel: RETAINED UNTOUCHED (CAVEAT-026)
- CAVEAT-027, three instances, RECORDED and NOT fixed: the glass/blur
  invariant is INVERTED; WorkingPaperHeader hardcodes "Generated: 2026-07-25";
  and `Source Table: audited_financials` names a table that DOES NOT EXIST
  (it is `financials`) directly above a verified figure
```

## 17. MUST UNDERSTAND

```text
- Why localising path-awareness in one function beats distributing it, and
  why that is the SAME argument as the single metric registry
- Why a "safe default" is a claim, and how F14 turned a correct fallback into
  a 100%-firing falsehood without the fallback changing
- Why sentence-level superscripts would be a STRONGER claim than the data
  supports — the weaker rendering is the honest one
- Why FACT / EVIDENCE / INFERENCE / UNKNOWN must stay separated, and why the
  failure mode is grammar, not accuracy: an inference written as a fact
- Why "why were they kept" is unanswerable FROM THE REPOSITORY, and why the
  right move is to leave it labelled rather than fill it with a plausible story
- Why a documented invariant that the code violates is a finding to RECORD,
  not a bug to fix — fixing requires deciding which side was right, and that
  is the author's call
- Why an automated dead-export check does not belong here yet, and why the
  reason is CI rather than the tool
```

---

## 18. This connects to

```text
Day 30 — the semantic path (what the narrative branch renders)
Day 33 — compute row shapes (what branches 3-5 duck-type)
Day 34 — sql_verified (the one boolean behind ✓ vs ~)
Day 39 — state, effects, the SSE consumer
   ↓
Day 40 — the render boundary, and dead code
   ↓
Day 41 — auth state, upload, admin
```

Forward references:

- `localStorage`, `LoginForm`, `UploadPanel`, the gate → **Day 41**
- `is_blocked` and `block_reason`, from the producing side → **Day 42**
- CAVEAT-022 (no CI, no frontend tests) and why §14 Q20 defers → **Day 43**
- `cache_hit_rate_pct`: live code with no producer, the *opposite* of dead
  code → **Day 44**

Records opened today:

- **CAVEAT-027** — two documented invariants the code no longer satisfies.
- **KU-004** — updated with the `9ce004a` evidence and the sibling-deletion
  precedent. **The "why kept" question stays open.**
