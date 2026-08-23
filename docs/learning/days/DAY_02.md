# Day 02 — Reading a Repository: Git as Evidence

**Phase 0 — Ground · Weight: L (~60 min) · Prerequisites: Day 1**

---

## 1. Today's goal

By tonight you can:

- Use `git log`, `git show` and `git diff` to answer a question about **why**
  code looks the way it does — and know when they cannot.
- Explain this project's one-commit-per-file discipline and what it buys.
- State the difference between what history **establishes** and what it only
  **suggests**.
- Run the "environment before code" check that this project mandates before any
  theory about a discrepancy.

This is a short day on purpose. It is a tool day, and the tool is used every
day after it.

---

## 2. Why now

Yesterday you proved *which code* is running. Today you learn to ask *why that
code exists*, because from Day 3 onward almost every explanation in this course
is sourced from one of three places: a code comment, a document, or the commit
that introduced the line.

There is a project-specific reason too. `CLAUDE.md` §8 makes git a **diagnostic
instrument**, not a backup tool:

> When local and prod disagree, run `git status --short` and
> `git log --oneline origin/main -1` before forming any code theory. Every
> "works locally, not in prod" has traced to an unpushed file, never a code
> defect.

That is a claim about *this repository's history*, and today you learn to check
claims like it.

---

## 3. Concepts you must know first

From **Day 1**: files, processes, the working tree, and the fact that
`./backend` and `/app` are the same directory.

---

## 4. Concept lesson

### 4.1 What a commit actually is

**What is it?** A **full snapshot** of every tracked file, plus a pointer to its
parent, plus a message and author.

**Not a diff.** This surprises people. Git stores snapshots; the diffs you see
are *computed on demand* by comparing a commit to its parent. That is why
`git show` can render a diff for any commit instantly, and why renames are
*detected* rather than recorded (git compares content and infers it).

**What problem does it solve?** Answering "what did this look like at that
moment, and what changed to get here".

**What existed before?** Copied folders (`final_v2_REAL`). Then centralised
systems like SVN, where history lived on a server, branching was expensive, and
you could not commit offline.

**Mental model.** A commit is a **numbered photograph of the whole project**,
with a note saying which photograph came before it and why you took this one.

---

### 4.2 The three places a file can be

```
   working tree   ──git add──►   staging area   ──git commit──►   history
   (your editor)                  ("the index")                   (permanent)
```

`git status --short` shows the first two columns:

```
 M file    modified in working tree, NOT staged
M  file    staged
MM file    staged, then modified again
?? file    untracked
```

**Why this matters here, concretely.** From `CLAUDE.md` §3:

> `git add` on an unmodified file stages nothing and the commit is a silent
> no-op. Run `git diff --stat` before every commit, every time.

If you believe you edited a file, `git add` it, and commit — and the edit never
actually landed — git will happily create an empty commit and tell you nothing
useful. The mandated `git diff --stat` first is a **cheap check against your own
belief**.

---

### 4.3 History establishes *what*, not *why*

This is the single most important idea today.

| Git can tell you | Git cannot tell you |
|---|---|
| What changed | Why it was necessary |
| When, and in what order | What alternatives were considered |
| What changed **alongside** it | Whether the author was confident |
| That a file has not been touched since date X | Whether that is deliberate or forgotten |

A commit message can supply the *why*, and in this repository it usually does —
which is exactly why the messages are long. But **the absence of a stated reason
is not evidence of an absent reason.**

`KNOWN_UNKNOWNS.md` `KU-004` is the worked example. Three frontend components
are imported by nothing. Git proves that. Git does **not** say whether they were
deliberately retained or forgotten, and no document does either. So the caveat
records the evidence and explicitly declines to assert a cause. You will do this
exercise properly on **Day 40**.

---

### 4.4 Commit granularity as a design decision

From `CLAUDE.md` §3:

> **One commit per file**, never batched, never skipped.

**What problem does it solve?** Attribution. If a regression appears, you want to
bisect to a single change to a single file. A commit touching nine files can only
tell you "somewhere in here".

**What it costs.** A longer history, and more typing.

**Look at the evidence in this repo.** The F14 change — renaming one state field
end to end — is *eleven* commits:

```
1c23b63 F14(state):     QueryState.company -> companies: list[str]
0a24aa1 F14(router):    RouterResponse.companies is a required list
8c93fdf F14(retriever): the company filter becomes an any-of
c20c9bc F14(semantic):  pass the issuer list through to retrieval
a9ece65 F14(quant):     override the DSL entity only when exactly one issuer
39a18d4 F14(cross):     resolve every named issuer to its parent
6764d03 F14(api):       omit the scalar top-level company
51191f6 F14(tests):     the filter tests move to the list interface
214f092 F14(tests):     a two-issuer query must not refuse anywhere
62bd4df F14(frontend):  QueryResponse.company -> companies: string[]
...
```

One change, eleven commits, each naming its layer. You can read the **blast
radius of a single type change** straight off the log. That is the payoff.

---

## 5. The actual LedgerMind files

Today's "file" is the history itself, plus two documents that encode how to read
it:

```
File:        CLAUDE.md §3 "Editing and committing"
Purpose:     The commit discipline, and why each rule exists
Why:         Each rule is a scar. "ABORT: found 0 is information, not a no-op."

File:        docs/journal/PROJECT_TIMELINE.md
Purpose:     985 commits, reconstructed into six phases
Why:         Shows the SHAPE of the history — where effort actually went
```

---

## 6. Deep walkthrough — reading one commit properly

Take `7d580df`, a real commit from two days before the course began.

**STATE BEFORE.** You know nothing about it except the hash.

**Step 1 — the headline.**

```bash
git show --stat --oneline 7d580df
```

```
7d580df fix(api): a blocked query stops reporting a confidence tier it never computed
 backend/app/api/response_shaping.py | ...
```

Already you know: one file, the API layer, and the message states a *behaviour*
and a *reason* rather than a change ("stops reporting X it never computed" — not
"update response_shaping").

**Step 2 — what changed.**

```bash
git show 7d580df
```

You will find a `base.pop("confidence_tier", None)` guarded by
`if response["is_blocked"]`, and — much more valuable — a long comment block
explaining that a Prompt Shield block routes straight to `audit_writer`, so
`confidence_node` never runs, so the tier that reached the client was
`make_initial_state`'s default `"low"` — indistinguishable on the wire from a
tier that was computed and came out low.

**Step 3 — what changed alongside it.**

```bash
git log --oneline 7d580df~1..HEAD | tail -5
```

You find `b4fe8fd test(api): pin blocked-vs-measured-low as distinguishable`
immediately after. **A fix and its test, as separate commits, adjacent.** That
adjacency is itself information: the author considered the behaviour worth
pinning.

**Step 4 — what the history does *not* say.** It does not say who noticed, or
how. For that you would read `docs/IMPLEMENTATION_DELTAS.md`, which records
measurements. History and documents answer different questions.

**STATE AFTER.** You can explain a change you did not make, and you know which
part of your explanation is evidence and which is inference.

---

## 7. Data flow — how a question becomes an answer, using git

```
"Why is this line here?"
   │
   ▼
git log -- <path>              which commits touched this file at all?
   │
   ▼
git log -S'<exact string>'     which commit INTRODUCED or REMOVED this text?
   │
   ▼
git show <hash>                the full change, and the message
   │
   ├─► message explains it  ──► you have EVIDENCE. Cite the hash.
   │
   └─► message does not     ──► check the code comment; then
                                 IMPLEMENTATION_DELTAS.md; then CAVEATS.md
                                 │
                                 └─► still nothing?
                                     You have an INFERENCE. Label it,
                                     and consider a KNOWN_UNKNOWNS entry.
```

`git log -S'string'` (the "pickaxe") is the one most people never learn and is
the most useful of the four. It searches for commits where the **number of
occurrences of a string changed** — i.e. where it was added or deleted.

---

## 8. Engineering decision — why this discipline?

**The problem.** In a project whose entire claim is that its numbers are
correct, you must be able to attribute any change in a number to a change in the
code.

**The decision.** One commit per file; a message stating the *behaviour and its
reason*, not the mechanics; `git diff --stat` before every commit; push only
after all commits.

**Alternatives:**

| Alternative | Why not here |
|---|---|
| Squash-merge feature branches | You lose per-file attribution — the exact thing this is for |
| Conventional-commits only (`fix:`, `feat:`) | Fine, and used here as a *prefix*. But a prefix is not a reason |
| Long-lived branches | A solo developer gains nothing and loses the ability to bisect linearly |

**Trade-off accepted.** A verbose history. `git log --oneline` is less scannable;
`git log --oneline -- <path>` is far *more* useful.

**Current validity.** Strong. `PROJECT_TIMELINE.md` was reconstructed from this
history precisely because the granularity made it possible.

**At 10× / with a team.** You would add CI and PR review, and probably keep the
granularity. `CAVEAT-022` records that there is **no CI** — and Day 43 shows what
that cost: `0cf7e7c` broke 25 tests and nothing noticed for a day.

---

## 9. Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Commit created, nothing changed | `git add` on an unmodified file | `git diff --stat` **first**, every time |
| "Works locally, not in prod" | An unpushed file | `git status --short` and `git log --oneline origin/main -1` **before** any code theory |
| Cannot find when a line appeared | Looking with `git log -- <path>` alone | Use `git log -S'<string>'` |
| A rename looks like delete + add | Content changed too much for rename detection | Commit the pure rename **first**, then the edit — as was done for `LEARNING_CHECKLIST.md` → `LEARNING_PROGRESS.md` |
| History "proves" an intent | It does not | Separate evidence from inference. See `KU-004` |

---

## 10. Hands-on experiment

### Experiment 1 — the shape of the history

```bash
git rev-list --count HEAD
git log --date=format:'%Y-%m' --pretty=format:'%ad' | sort | uniq -c
```

Two-thirds of ~985 commits fall in July. Now ask *what kind* of work that was:

```bash
git log --oneline --since=2026-07-20 --until=2026-08-10 | head -30
```

Read the verbs. `fix`, `correct`, `guard`, `probe`, `purge`. Almost no `feat`.
**The architecture was built in four days; everything else is correctness work.**
That is the single most informative fact in this repository's history.

### Experiment 2 — the blast radius of one type change

```bash
git log --oneline --grep='^F14' --reverse
```

Eleven commits. Read the layer names in order — `state`, `router`, `retriever`,
`semantic`, `quant`, `cross`, `api`, `tests`, `frontend`. You are looking at a
map of the system's dependency structure, derived from a rename.

### Experiment 3 — the pickaxe

Find where a constant was introduced:

```bash
git log -S'NEAR_DUPLICATE_THRESHOLD' --oneline -- backend/app/engines/retriever.py
```

Then read the introducing commit in full. The comment above that constant
records a live measurement (two chunks, page 23, 87.8% token overlap). You will
meet it properly on **Day 29**.

### Experiment 4 — a file that stopped changing

```bash
git log --oneline -- backend/tests/conftest.py
```

One commit, 2026-08-11. Now:

```bash
git log --oneline --since=2026-08-11 -- backend/scripts/eval_runner.py
```

`eval_runner.py` kept changing; its test fixture did not. **That gap is
`CAVEAT-025`** — 25 tests have errored since 2026-08-22 because `--api-base`
became required and the fixture was never updated. You just found a live defect
using nothing but `git log`.

### Experiment 5 — evidence versus inference

```bash
git log --oneline -- frontend/components/AnswerCard.tsx
git show --stat 945b7d4
grep -rn "AnswerCard" frontend/app frontend/components frontend/lib
```

Now write down two lists:

- **What this proves.** (Nothing imports it. A recent commit modified it.)
- **What it suggests but does not prove.** (Why it is unreferenced.)

Keep both lists. **Day 40** is built on exactly this distinction.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Using only git commands, answer:

1. What was the **very first** commit, and on what date?
2. Which single file has the most commits touching it? (Hint: `git log
   --name-only --pretty=format: | sort | uniq -c | sort -rn | head`)
3. Find the commit that introduced `_deduplicate_near_identical`. What does its
   message say the function is for?
4. `backend/app/llm/client.py` did not exist for the first five weeks. Find the
   commit that created it and read its message. What two problems does it say it
   solves, and why were they fixed together?
5. Is there any commit whose message admits the change was **shipped without a
   measurement**? Find it. (Hint: search the log for `probe`.)

---

## 12. Self-check questions

**Basic**
1. Is a commit a snapshot or a diff?
2. What are the three places a tracked file can be?
3. What does `??` mean in `git status --short`?
4. What does `git diff --stat` show that `git diff --cached --stat` does not?
5. What does this project's commit discipline require, in one sentence?

**Code**
6. What does `git log -S'foo'` find that `git log --grep='foo'` does not?
7. How do you see only the commits that touched one file?
8. Why is a rename committed separately from the edit that follows it?
9. What does `git show --stat <hash>` give you that `git show <hash>` buries?
10. How do you count commits between two refs?

**Why**
11. Why does this project use one commit per file?
12. Why is `git diff --stat` mandated *before* every commit?
13. Why are the commit messages here unusually long?
14. Why does `CLAUDE.md` say to check git **before** forming a code theory?
15. Why is "the message does not state a reason" not evidence that there was no
    reason?

**Debugging**
16. You are certain you edited a file; the commit is empty. What happened?
17. A behaviour differs between local and production. Name the two commands you
    run first, and why those and not a debugger.
18. You need to know when a magic number entered the codebase. Which command?

**System design**
19. This repository has 985 commits and no CI. Name one defect that slipped
    through as a direct result, and say what CI would have caught.
20. If a team of five joined tomorrow, name one thing about this commit
    discipline you would keep and one you would change, with reasons.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. `83fc5ba`, "Initial commit", **2026-06-23**.
   `git log --reverse --date=short --pretty='%ad %h %s' | head -1`
2. Expect `docs/IMPLEMENTATION_DELTAS.md` or one of the large engine files —
   the exact answer matters less than the method. The interesting observation is
   that a *document* competes with source files for the top spot.
3. `git log -S'_deduplicate_near_identical' --oneline`. The message and the code
   comment together explain that adjacent chunks share ~150 tokens **by design**,
   and the defect is that two windows over the same text can both occupy final
   top-5 slots.
4. `b937b8e`, 2026-07-29. The docstring names **(1) unbounded tail latency** —
   the same query measured 3.07 s / 120.0 s / 3.00 s with no timeout anywhere —
   and **(2) no fallback** — a documented Gemini→Groq failover that had "a
   `groq_api_key` field and zero call sites". They were fixed together because
   *a timeout converts an unbounded hang into a catchable exception, and only
   then is there anything for a fallback to catch.* A fallback keyed on
   exceptions would never have fired on defect 1.
5. Yes — the F14 series. `06176e0 docs(deltas): F14 closed 2026-08-22 — and
   shipped without a probe, stated as such`. That commit exists to record the
   absence of a measurement. It is the honesty this project runs on, and it is
   now `KU-002`.

### §12 — Basic

1. A **snapshot** of all tracked files. Diffs are computed on demand.
2. Working tree → staging area (index) → history.
3. Untracked — git is not following the file at all.
4. `git diff --stat` shows **unstaged** changes (working tree vs index).
   `git diff --cached --stat` shows **staged** changes (index vs HEAD).
5. One commit per file, message stating the behaviour and its reason,
   `git diff --stat` first, push only after all commits.

### §12 — Code

6. `-S` searches for commits where the **number of occurrences of the string
   changed** — i.e. where it was added or removed. `--grep` searches commit
   **messages**. To find where a line came from, you want `-S`.
7. `git log -- <path>` (the `--` separates paths from refs).
8. So git records it as a rename rather than a delete-plus-add. Rename detection
   compares content; a rename bundled with substantial edits often falls below
   the similarity threshold and the file's history appears to start over.
9. The **file list and line counts** — the blast radius — without the diff body
   burying it.
10. `git rev-list --count A..B`.

### §12 — Why

11. Attribution. If a number changes, you must be able to bisect to a single
    change to a single file. A nine-file commit tells you only "somewhere here".
12. Because `git add` on an unmodified file stages nothing and the commit is a
    **silent no-op**. The check costs a second and catches a belief that is wrong.
13. Because the message is where the *reason* lives, and this project treats
    "why" as the thing worth preserving. The code shows what; only the message
    and the comment show why.
14. Because **every** "works locally, not in prod" in this project's history has
    traced to an unpushed file, never a code defect. Environment before code, as
    on Day 1.
15. Because a reason can exist and simply not be written down. Asserting "there
    was no reason" from silence is manufacturing a conclusion. That is why
    `KNOWN_UNKNOWNS.md` exists as a third register alongside "what was measured"
    and "what is wrong".

### §12 — Debugging

16. Either the edit never landed (wrong file, wrong container path, editor did
    not save), or you edited a file that was already in that state. `git diff
    --stat` before committing would have shown zero changed files.
17. `git status --short` and `git log --oneline origin/main -1`. Because the
    cheapest hypothesis is that production is running different code, and a
    debugger will happily spend an hour confirming that the code you are reading
    behaves correctly — which is not the question.
18. `git log -S'<the number or constant name>' -- <path>`.

### §12 — System design

19. `0cf7e7c` made `eval_runner`'s `--api-base` required; `conftest.py`'s fixture
    still passed only `--model`; **25 tests have errored since 2026-08-22**
    (`CAVEAT-025`). CI would have failed the build on the same push that
    introduced the change, making it a two-minute fix instead of a defect found
    by an audit a day later. Also acceptable: the documented baseline said
    "green", which turned a pre-existing defect into a false attribution for the
    next person to run the suite.
20. **Keep:** the per-file granularity and the reason-stating messages — they are
    what made `PROJECT_TIMELINE.md` reconstructible and what makes bisecting
    meaningful. **Change:** add CI and require review, because the discipline is
    currently enforced only by one person remembering it, and `CAVEAT-025` is
    proof that it lapses. Also acceptable: adopt short-lived feature branches so
    a half-finished series is not sitting on `main`.

---

## 14. MUST REMEMBER

```text
- A commit is a SNAPSHOT; diffs are computed
- `git diff --stat` BEFORE every commit, every time
- `git add` on an unmodified file → silent no-op commit
- One commit per file, never batched
- `git log -S'string'` finds where text was ADDED or REMOVED
- "works locally, not in prod" → `git status --short` FIRST, before any theory
- Commit a rename ALONE, then edit
```

## 15. MUST UNDERSTAND

```text
- Why granularity is an attribution decision, not a style preference
- What history ESTABLISHES vs what it only SUGGESTS — and why conflating
  them manufactures conclusions
- Why the absence of a stated reason is not evidence of an absent reason
- Why the shape of a history (which weeks, which verbs) tells you more about
  a project than any single commit
```

---

## 16. This connects to

```text
Day 1 — you can prove which code is running
   ↓
Day 2 — you can ask why that code exists, and know the limits of the answer
   ↓
Day 3 — the system itself: three engines and one shared dictionary
```

Forward references:

- **Evidence vs inference**, applied to dead code → **Day 40**
- **`CAVEAT-025`**, which you found today with `git log` → **Day 43**
- **The F14 blast radius** you mapped today → **Day 36**
- **`NEAR_DUPLICATE_THRESHOLD`** and its measurement → **Day 29**
- **`llm/client.py`'s two founding defects** → **Day 19**
