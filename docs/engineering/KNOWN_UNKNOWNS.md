# LedgerMind — Known Unknowns

Questions this repository **cannot currently answer**, recorded so that nobody
answers them by assumption.

This file exists because of a specific failure this project has had more than
once: an inference, written down once without a label, is read six weeks later
as an established fact. `docs/IMPLEMENTATION_DELTAS.md` records what was
*measured*. `CAVEATS.md` records what is *wrong*. This file records what is
**not known**, which is a third thing and the easiest one to lose.

Opened: 2026-08-23.

---

## How to read an entry

| Field | Meaning |
|---|---|
| **Question** | Stated as a question, never as a hedged claim |
| **Repository evidence** | What the code, the git history or a measurement actually shows |
| **Current hypothesis** | The best available guess, **explicitly labelled as a guess** |
| **Confidence** | Low / Medium / High — in the *hypothesis*, not in the evidence |
| **Why it matters** | What a wrong assumption here would cost |
| **How to verify** | A concrete procedure, not "investigate further" |
| **Status** | Open / Answered (with the date and the answer) |

**Confidence is about the hypothesis.** "High confidence" never means "so we can
treat it as true" — it means "if you are going to act on a guess, this is the
guess". An entry is only closed by a measurement, never by the hypothesis
starting to feel obvious.

---

## [KU-001] Why does TQ008 route `cross` where its golden expects `semantic`?

**Question.** TQ008 has routed `cross` across three consecutive Gemini runs. Its
golden `expected_path` was `semantic` until `f60a88a` changed it to `cross` per
the two-arm router probe. Is the observed routing a regression, or was the
golden simply wrong from the start?

**Repository evidence.**
- Stable across three runs, so not sampling noise.
- A prompt-block cause was suspected and **disproved**: TQ008 still routes
  `cross` with the block removed. This is recorded in `CLAUDE.md` and in
  `router.py`'s `company_mentioned` comment, both of which say so explicitly.
- No pre-`d365f4b` baseline exists. Nobody measured the before-state.

**Current hypothesis.** *(Guess.)* The golden was authored from intent rather
than from a measured run, and `cross` may be the correct classification for a
question that asks the model to reconcile commentary against a figure.

**Confidence.** Low. The alternative — that some earlier change moved it — has
not been excluded, only left unmeasured.

**Why it matters.** `CLAUDE.md` warns directly against calling this a regression
without a baseline. Treating it as one would attribute a defect to a commit that
may have nothing to do with it, which this project has done wrongly three times
in a single session.

**How to verify.**
```bash
git stash
git checkout <commit immediately before d365f4b>
# three classify calls on TQ008's exact question text, provider + model printed per run
git checkout main
git stash pop
```
Three runs, not one. Print provider and model on each — a Groq-served
classification is not comparable to a Gemini-served one.

**Status:** Open.

---

## [KU-002] Did the F14 schema change move the router's classifications?

**Question.** F14 changed `RouterResponse.company: Optional[str]` to
`companies: list[str]`. The response schema is sent to the model on both
providers. Did that change how queries are classified?

**Repository evidence.**
- Shipped **without a router probe**, on instruction. `CLAUDE.md` and
  `IMPLEMENTATION_DELTAS.md` both state this plainly rather than eliding it.
- The schema is measurably different: Gemini **+32 bytes** and the node loses
  its `nullable` flag under a list type; Groq **−39 bytes**.
- `IMPLEMENTATION_DELTAS.md` §D — *"The response schema is part of the prompt"*
  — establishes that a schema-only change is a model-input change, from a
  separate measured case.
- Q051's continued passing is argued **from code paths and unit tests, not from
  a run**. That is stated in `CLAUDE.md` in those words.

**Current hypothesis.** *(Guess.)* The classifier is unchanged for
single-issuer queries, since the prompt's per-field instructions are otherwise
identical. Multi-issuer queries are genuinely new behaviour and have no
before-state to be compared against.

**Confidence.** Low. This is precisely the kind of claim §D exists to warn
against.

**Why it matters.** Any route difference observed after 2026-08-22 could
originate here. Without a probe, F14 will absorb the blame for anything that
moves — or, worse, will be exonerated by assumption.

**How to verify.** Two-arm probe over the golden set, three runs per arm, schema
bytes and provider/model printed per run. `scripts/router_probe.py` already does
this shape of measurement.

**Status:** Open.

---

## [KU-003] Is `COHERE_MEDIUM = 0.15` a correct boundary?

**Question.** `COHERE_MEDIUM_CONFIDENCE_THRESHOLD = 0.15` is the
refuse-versus-answer boundary on the Cohere scale. Is it in the right place?

**Repository evidence.**
- `semantic_engine.py` states it directly: no query in the calibration run fell
  between 0.15 and 0.5, or below 0.15. The band is **unstressed by real data**.
- The `HIGH` boundary at 0.5 *was* validated: every "high" result scored ≥ 0.88,
  and the one genuine "medium" (Q031) scored 0.4656, correctly below 0.5.
- `CLAUDE.md` §3 lists it as a measured constant that must not be tuned.

**Current hypothesis.** *(Guess.)* It is a reasonable placeholder, and it has
never been exercised, which means it is **unvalidated, not validated**. Those
are different states and the distinction is the whole point of this entry.

**Confidence.** N/A — this is an absence of evidence, not a competing
explanation. There is nothing to be confident about.

**Why it matters.** It is the boundary between answering and refusing. A
constant that has never fired cannot be said to work; it can only be said not to
have failed yet.

**How to verify.** Cannot be synthesised — it needs a real query whose top
Cohere relevance genuinely lands in 0.15–0.5. Candidate source: the
`docs/measurements/cohere_*.json` score dumps, scanned for near-band scores. If
none exist, the honest answer is that the boundary remains untested.

**Status:** Open. Do not tune.

---

## [KU-004] Why are `AnswerCard`, `ConfidenceBadge` and `CorpusPanel` unreferenced?

**Question.** Three frontend components are imported by nothing (CAVEAT-026).
Were they deliberately retained, or orphaned and forgotten?

**Repository evidence.**
- `grep` over `frontend/app`, `frontend/components`, `frontend/lib` finds only
  self-references.
- `frontend/app/` holds exactly three files (`page.tsx`, `layout.tsx`,
  `globals.css`), so there is no second entry point. *(Corrected 2026-08-23: the
  original entry said two, omitting `globals.css`. The conclusion is unchanged —
  one `page.tsx` means one route.)*
- Commit `945b7d4` (2026-08-22) applied a correctness fix to `AnswerCard.tsx`.
- **No commit message, comment or document states a reason.** Searched.

**UPDATE 2026-08-23 — the supersession half is no longer a guess.**

Found while writing Day 40 of the course, using `git log -S` rather than
`git log --`. The pickaxe finds the commit where a *reference* changed, in a
file that was never itself deleted:

```
$ git log --oneline -S "AnswerCard" -- frontend/app/page.tsx
9ce004a  connected page design
6b45b8c  Create page.tsx
```

Two commits: the one that introduced the reference, and the one that removed it.

```
$ git show 9ce004a -- frontend/app/page.tsx | grep -E "^[-+]import"
-import SearchBar from "@/components/SearchBar";
-import AnswerCard from "@/components/AnswerCard";
-import PipelineTrack from "@/components/PipelineTrack";
-import CorpusPanel from "@/components/CorpusPanel";
+import { DocumentEnvironment } from "@/components/document/DocumentEnvironment";
+import { DocumentPage } from "@/components/document/DocumentPage";
...
```

**`9ce004a`, 2026-07-22 12:26 IST, "connected page design"** — one commit removed
**four** old component imports and added the `components/document/*` working-paper
set in the same diff.

**And two of those four were subsequently deleted:**

| Component | Orphaned | Deleted |
|---|---|---|
| `SearchBar` | `9ce004a`, 07-22 | **yes** — `c464bd2`, 07-25, *"chore: remove dead SearchBar.tsx component"* |
| `PipelineTrack` | `9ce004a`, 07-22 | **yes** — `3b0306d`, 07-28, *"chore(frontend): remove dead PipelineTrack.tsx — orphaned, never imported, pre-redesign token set"* |
| `AnswerCard` | `9ce004a`, 07-22 | **no** |
| `CorpusPanel` | `9ce004a`, 07-22 | **no** |

Also checked, per "How to verify" below: `git branch -a` shows only `main` and
its remote; `git stash list` is empty. **There is no other ref.**

**What this settles, and what it does not.**

- **SETTLED — supersession.** "Superseded by the `components/document/` UI" was
  recorded here as a guess. It is now evidenced: a single commit swapped one
  component set for the other. Confidence: **high**.
- **WEAKENED — "they were not known to be unreachable".** This entry reasoned
  that `945b7d4` suggests nobody knew. But the project **did** find orphans from
  this exact commit, twice, and deleted them with commit messages that say
  "dead" and "orphaned, never imported". That makes "nobody noticed" a weaker
  reading than it looked.
- **NOT SETTLED — and it is now a sharper question.** Not *"were they forgotten?"*
  but: **why were these three kept when two siblings orphaned by the same commit
  were deleted?** Nothing states it. `945b7d4` and `63d7f40` remain real
  correctness commits applied to a non-rendering component, and both readings —
  an unchecked sweep, or a deliberate keep-it-current — still survive.

**Two facts added on the same pass** (both from Day 40, both reproducible):

- `CorpusPanel` reads `s.chunksIndexed` and `s.lastIngestedLabel`, **neither of
  which is a declared field of `CorpusStatus`**. It type-checks only because that
  interface ends with `[key: string]: any`. Rendered with a real API-shaped
  object it would throw on `.toLocaleString()`.
- **Nothing produces a `CorpusStatus`.** No function in `lib/api.ts` returns one,
  and the backend registers no corpus-status endpoint. Deleting the three
  components without deleting the type leaves a dead type with a live-looking
  declaration.

**Current hypothesis.** *(Still a guess, and now only about the retention.)* The
three were left in place when their two siblings were removed. Whether that was
a decision or an omission is not recorded.

**Confidence.** **High** on "superseded by the document UI" (was: medium — the
`9ce004a` evidence closed it). **Low**, unchanged, on whether retention was a
decision or an oversight. Only the author can answer the second.

**Why it matters.** It decides whether deletion is a cleanup or a loss. It is
also the only entry here answerable by a person rather than a measurement.

**How to verify.** **Ask the author** — specifically: *"you deleted SearchBar and
PipelineTrack after `9ce004a`; why not AnswerCard and CorpusPanel?"* The
branch/stash check is now done (none). The "did the document UI land after they
were last edited" check is also now done, and the answer is **yes for
supersession and no for editing** — `AnswerCard` was edited on 2026-08-23, a
month after being orphaned.

**Status:** Open — **narrowed, not closed** (2026-08-23). The supersession half is
answered from the repository; the retention half is not answerable from it at
all. Components retained untouched by decision.

---

## [KU-005] Why Cohere rather than a self-hosted cross-encoder, originally?

**Question.** Cohere Rerank is the primary reranker, with local ONNX as
fallback. Was Cohere chosen for the RAM constraint, or for quality, or both?

**Repository evidence.**
- The RAM constraint is documented and real: Render's 512 MB tier, torch does
  not fit, `retriever.py` notes Cohere as "0MB RAM".
- The local ONNX cross-encoder **also** fits in that budget and is present as
  the fallback — so RAM alone does not explain the ordering.
- No ADR or commit message states a quality comparison between them.

**Current hypothesis.** *(Guess.)* RAM headroom was the driver — the fallback
fits, but running it as primary competes with fastembed and the request path in
the same 512 MB. A quality preference may also exist but is not recorded.

**Confidence.** Medium.

**Why it matters.** It decides whether the ONNX fallback is an acceptable
long-term primary if Cohere's free tier ends. If the reason was purely RAM, a
larger instance changes the answer; if it was quality, it does not.

**How to verify.** Score the same golden set through both backends and compare —
noting that the two are on **incompatible scales**, so compare *rankings and
pass/fail*, never raw scores. `scripts/cohere_score_dump.py` has a hard abort for
exactly this mistake.

**Status:** Open.

---

## [KU-006] What is the true distribution of `response_text` lengths?

**Question.** How long are LedgerMind's answers, actually?

**Repository evidence.**
- `audit_writer.py` previously stored a **500-character prefix** under a
  variable named for a summary. Commit `5bff364` fixed it to bind in full.
- **1,516 of 4,168 stored rows (36.4%)** are unmarked prefixes.
- The column is unbounded `TEXT` and always could have held the whole thing —
  the writer never offered it.
- Nothing distinguishes a truncated row from a genuinely short one.

**Current hypothesis.** *(Guess.)* Every stored value above 486 characters is a
prefix, so the true distribution above that point has never been observed by
anything, including the code that wrote it.

**Confidence.** High that the historical data is unrecoverable. Nothing at all
about the shape of the real distribution.

**Why it matters.** Any aggregate over historical `response_text` length is
meaningless, and the temptation after a fix like this is to reason about the
repaired data as though it were always correct. It was not.

**How to verify.** Only forward-looking. `audit_writer.py` now logs
`Audit response length` per request; the distribution becomes knowable from rows
written after `5bff364`, and only those. Deliberately **no cap and no
warn-above-N** was added — as the code comment says, a threshold warning is a cap
that has not fired yet.

**Status:** Open, and partly unanswerable by construction.

---

## Adding an entry

Add one whenever you catch yourself writing "presumably", "it seems", "probably
because", or "I think the reason is" in any other document. That sentence
belongs here, as a question, with its evidence — not there, as prose.

Close an entry only with a measurement, and record the measurement, not just its
conclusion.
