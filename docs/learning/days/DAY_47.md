# Day 47 — Failure Drills, Roads Not Taken, Viva

**Phase 13 · Weight: H (~120 min) · Prerequisites: Day 46**

**Textbook: Parts 11, 12 and 13 — DIVERGES, all three.** Agentic RAG, Graph RAG
and multimodal. LedgerMind built none of them, and each refusal has a stated
reason. Part 2 is where you argue both sides.

---

## 0. What today is

**The last day, and it is an examination of the stack as one mental model.**

Three parts, and they are different in kind:

```
PART 1  — BACKWARDS REASONING.   Symptom → layer → file → check.  ~40 min
             Real symptoms, from BUGS_AND_LESSONS.md, answers hidden.

PART 2  — ROADS NOT TAKEN.       ~30 min
             Three things deliberately not built. What each would
             have bought, and what it would have cost.

PART 3  — DESIGN THE NEXT ONE.   ~30 min
             Keep · change · measure first.

VIVA    — THE FULL STACK, OUT LOUD, WITH NOTHING OPEN.  ~20 min
             Machine → terminal → process → network → HTTP → API →
             auth → backend → database → routing → RAG → retrieval →
             verification → LLM → response.
```

**Yesterday chose part of today's material.** `LEARNING_PROGRESS.md` Part 4:
*"Day 47 — whatever Day 46 exposed as thin."* **Read your Part 3 rows before
starting**, and spend the extra time there.

---

## 1. Why now

Day 46 proved you can trace the system forwards. **Today runs it backwards**,
which is the direction real work arrives in: nobody hands you a request and asks
where it goes. They hand you *"the answer is wrong"*.

And the roads-not-taken half needs everything: you cannot argue that Graph RAG
was correctly declined until you know what flat retrieval currently does, what it
costs, and where it is actually weak.

---

# PART 1 — BACKWARDS REASONING

## 2. The method

```
SYMPTOM
   ▼  WHICH KIND OF WRONG?      (number vs text vs slow vs intermittent)
   ▼  WHICH LAYER?              name it BEFORE opening anything
   ▼  WHICH FILE / FIELD?
   ▼  WHICH CHECK?              a command, not a theory
   ▼  WHAT WOULD DISPROVE ME?
```

**The fifth line is the discipline.** `CLAUDE.md` §8: *"Forming theories is
cheap; killing them with output is the discipline. Four wrong theories at one
command each is the right ratio. Defending one is not."*

**And the rule that comes before all of it** — *"an empty candidate set is a
network signature; a low-scoring one is a retrieval signature"* — because it
splits the largest category in two before you have spent anything.

---

## 3. The drills

> **Answer each one fully — layer, file, field, command, disproof — before
> reading §4.** Write them down. An answer you did not write is an answer you
> can retro-fit.

**D1.** Every semantic query returns `confidence_tier: "high"`, including ones
whose retrieved chunks are plainly unrelated. Occasionally the same query returns
`medium` instead, apparently at random.

**D2.** One query took **120 seconds** and returned **200 OK**. The same query
returned in 3.07 s before it and 3.00 s after.

**D3.** *"Does ETERNAL's management commentary on profitability align with its
actual PAT for FY26?"* returns **eleven** contradictions at `severity: high`,
including **+4730.6 %** and **−99.7 %**.

**D4.** A user asks about **Reliance**, which is not in the corpus. The system
returns **five citations** from TITAN and ZOMATO pages, at `tier=high @ 0.7095`.

**D5.** The answer says *"the excerpts do not contain a PAT figure"* — and the
next paragraph states *"PAT was ₹366 Cr"*.

**D6.** Every answer for an hour was low-confidence and read like raw excerpts.
Nothing errored. `llm_provider` on those rows is **NULL**.

**D7.** A real, correctly extracted figure became **untraceable** — the number is
right, and the citation that supports it is gone.

**D8.** A metric held a **completely different line item's value**, for weeks,
with `sql_verified: true` throughout.

**D9.** Login fails **intermittently**. The same credentials work on retry.

**D10.** An eval sweep prints `providers={'gemini': 48, 'groq': 7}` and withholds
the score. Everything before question 41 passed.

**D11.** A user's question is blocked with *"This request cannot be processed."*
They insist it was an ordinary question about a filing.

**D12.** `GET /api/documents/pending` returns `{"pending_uploads": []}` for an
admin who uploaded three files this morning. **No error.**

**D13.** The same query returns `tier=medium` on one run and `tier=high` on the
next. Both runs are Gemini-served, same model.

**D14.** A quantitative answer is ticked ✓ and the heading above it reads
`Source Table: audited_financials`. A colleague cannot find that table.

**D15.** After `docker compose up -d --force-recreate backend`, **every**
`docker compose exec` fails with *"possible container breakout detected"*.

---

## 4. Answer key — Part 1

> **Only after writing all fifteen.**

**D1 — BUG-001. Layer: confidence scoring, caused by reranking.**
**Root cause:** one threshold pair (`-4.5`/`-7.5`) applied to **either** backend.
Those numbers were calibrated for ONNX logits (≈ −12…+2); **Cohere returns 0–1,
which is always ≥ −4.5**, so every Cohere-scored query classified `high`
unconditionally. **The random `medium` was the correct behaviour leaking
through** whenever a Cohere hiccup sent the query to the local fallback.
**File/field:** `semantic_engine._score_confidence`, keyed on `reranker_backend`.
**Check:** read `reranker_backend` from the same response as the score.
**Disproof:** if `medium` runs are also Cohere-served, the theory is wrong.
**Class:** *incompatible scales read as one.*

**D2 — BUG-002. Layer: the LLM provider, not the pipeline.**
**Root cause:** no Gemini call site set a timeout, so a slow provider blocked the
request with **no ceiling**. Confirmed from Render logs as **one** call — `AFC is
enabled` → `AFC remote call 1 is done`, 78 s — **not** an SDK retry; everything
downstream took under a second. **Check:** the provider's own log lines, to
establish one call versus several. **Fix, in order:** (1) an explicit per-call
timeout, converting an unbounded hang into a catchable exception; (2) the Groq
fallback. **The order is not a preference — a fallback keyed on exceptions can
never fire against a hang, because a hang throws nothing.**
**And note the shape:** it returned **200 OK** and looked normal in the audit
log. Same silent-degradation class as `user_id="anonymous"`.

**D3 — BUG-003. Layer: contradiction detection.**
**Root cause:** every crore figure in every retrieved chunk was treated as a
claim about the queried metric. The flagged numbers were cash-flow lines and
Adjusted EBITDA that happened to share a chunk. **Worse: the top-cited chunk was
page 33, the Consolidated Statement of Cash Flows — part of the same document the
`financials` row was extracted from.** The engine flagged disagreement between a
verified value **and its own source. Circular by construction.**
**Fix:** three eligibility rules — narrative chunks only, a management speaker, a
metric alias within `PROXIMITY_WINDOW = 120` chars. **Tolerance alone was
necessary but never sufficient**: it makes a wrong comparison quieter, not right.

**D4 — BUG-004, audit F2. Layer: routing and entity resolution.**
**Root cause:** the router's failure to resolve a company did not stop the
pipeline. **`resolve_ticker` never returns `None`** — it uppercases its input —
so a check written against `None` would never fire; the real gate is
`_KNOWN_TICKERS` membership. Retrieval then dropped the company filter and
searched the whole tenant.
**Fix:** a `refused` edge from `router` straight to `audit_writer`, mirroring
`blocked`, **deliberately skipping the confidence tail** — which had been
rescoring the refusal at `tier=high @ 0.7095`.
**Class:** *writing a flag ≠ changing behaviour*, plus *a field overloading
`null`*.

**D5 — BUG-006. Layer: cross-path assembly.**
**Root cause:** semantic ran **first**, so the semantic half wrote its answer
from narrative chunks alone and **correctly** reported that they contain no PAT
figure — a true statement about its own context window. The quant template then
appended the figure underneath.
**Two fixes failed first**, both trying to **suppress** the sentence: post-hoc
rewriting, then a prompt instruction. Both lost, because **the model was being
asked to withhold something true about the evidence it was given** — and a
general instruction to withhold loses to `SYNTHESIS_SYSTEM_PROMPT`'s earlier,
more concrete *"say what is and isn't covered."*
**The working fix changed the premise, not the instruction:** run quant first and
inject the verified figure into context as established fact. **Then the sentence
is false rather than suppressed.**

**D6 — BUG-007. Layer: LLM providers, both of them.**
**Root cause:** a total synthesis outage served a raw-excerpt floor that was
**indistinguishable from a normal low-confidence answer.**
**Fix:** `error="synthesis_unavailable"`, the tier capped to low, and
**attribution cleared** — `llm_provider` NULL, because *"NULL is a real state,
not missing data."*
**And the downstream consequence is the part to know:** `eval_runner` must
**exclude** those rows from scoring, because the tier cap that is correct for a
user is **evidence for `out_of_corpus`'s pass condition** — an outage would
otherwise inflate the score on the day the system was down.

**D7 — BUG-005. Layer: citation assembly.**
**Root cause:** a citation **relevance floor of 0.05**, which dropped a chunk
whose figure the answer still used. **The measurement behind the constant was
correct; the effect was not** — it made a real figure untraceable rather than
preventing an unsupported claim.
**Fix:** the floor was **removed** on 2026-08-08, and `CLAUDE.md` §3 forbids
reintroducing it. **Read `semantic_engine.py:63` first.** The real defect was
allowing `retrieved_chunks` and `citations` to diverge at all.
**The lesson to carry:** a correct measurement can justify a wrong constant.

**D8 — BUG-009. Layer: extraction, not the query path.**
**Root cause:** a silent first-wins collision — `seen_keys` and an alias tie
meant one metric captured another line item's value, and everything downstream
was faithful to it. **`sql_verified: true` throughout, because it was true:**
the SQL ran and returned the stored value.
**This is the most important thing `sql_verified` does *not* guarantee.** It
verifies the pipeline, not the document.
**Check:** `regression_check.py` across all five documents, then
`purge_orphaned_metrics --dry-run`.
**Class:** *silent first-wins on a collision* — *a choice was made and nothing
recorded it.*

**D9 — Layer: auth, and "intermittently" is the diagnosis.**
**Two candidates, and you should name both.** (a) **Token expiry**: the 2-hour
JWT, plus a client-side `expiresAt` computed from the browser clock, so skew
makes the boundary fuzzy. (b) **The more interesting one:**
`authenticate_user` runs with `tenant_id=None`, relying on the
`auth_bootstrap_lookup` policy (migration 006) that permits `SELECT` on `users`
**only when `app.tenant_id` is unset**. A connection carrying a **leaked** GUC
from a previous request fails that policy — **and fails intermittently by
construction**, depending on which pooled connection you get.
**Check:** that every site uses `SET LOCAL`, never `SET`. **`SET LOCAL` is
transaction-scoped and cannot leak.**

**D10 — Layer: none. This is a quota signature.**
`groq` present means the failover fired on a 429 or timeout. **The tell is
positional:** everything before question 41 passed, and 41 is wherever the budget
ran out — an accident of how many calls the day had already spent. **A real
defect fails by category.**
**Do:** wait for quota and re-run; confirm `--delay` is 25 and not 15.
**Do not:** publish the raw tally. It prints as *"DO NOT publish"* precisely
because a number under a caveat ends up in a README.

**D11 — CAVEAT-021. Layer: the Prompt Shield.**
**Root cause:** `\bDAN\b` blocks any query containing "DAN"/"Dan" as a standalone
word, and `\bsystem\s*prompt\b` blocks legitimate questions **about** system
prompts.
**And the compounding factor is a second correct decision:** injection blocks
return the minimal `INJECTION_RESPONSE`, which deliberately explains nothing — so
**the user cannot tell a false positive from a real block.**
**Check:** run `check_query` on their exact text and read `block_reason`. Status:
*open, accepted trade for keeping injection blocks uninformative.*

**D12 — Layer: the database, and the absence of an error is the signal.**
**RLS returns zero rows, not an error, when `app.tenant_id` is unset.** So
"no data" and "no permission to see this data" are the same observation.
**Check:** the same `SELECT` with and without `SET app.tenant_id`.
**And the second candidate, which must be named:** **which database?**
`docker-compose.yml`'s `environment:` block overrides `DATABASE_URL` to local
Postgres, so rows written against Supabase are not there. **State which one you
queried** before concluding anything.

**D13 — BUG-001's sibling, and the trap is that "same model" rules out the
wrong thing.** The provider being identical says nothing about the **reranker**.
**Check `reranker_backend`.** Cohere flapping on WSL2 was measured at **5 of 8**
raw socket connects succeeding, at random — so the same query was scored by two
different systems, on two incompatible scales, and the tier moved.

**D14 — CAVEAT-027(c). Layer: the frontend, and nothing computed is wrong.**
`composeDocumentBody` renders `<SectionHeading sourceTable="audited_financials">`
at three call sites. **The table is `financials`**; the string appears nowhere in
`backend/`, `sql/` or `docs/`. **The figure is real, the verification is real,
the Postgres origin is real — only the identifier is invented.**
**That is what makes it a mandate violation rather than a correctness bug**, and
it is the highest-severity item in that caveat because it is a **provenance
claim attached to a verified number**.

**D15 — Not a security event. Layer: Docker.**
A **stale mount namespace**, usually after `--force-recreate`. **`-w /app` does
not help and no `cd` helps, because every exec fails — including
`docker compose exec -T backend echo alive`.** Run that one-liner to confirm,
then `docker compose up -d --force-recreate backend` and poll `/health`.
**The lesson: a message can name a threat and describe a different cause.**

---

## 5. Score yourself by class, not by count

Map your misses onto `BUGS_AND_LESSONS.md`'s recurring classes. **A pattern in
what you missed is worth more than the total.**

| Class | Drills |
|---|---|
| **Incompatible scales read as one** | D1, D13 |
| **A field overloading `null`** | D4, D6 |
| **Writing a flag ≠ changing behaviour** | D4 |
| **Suppressing a true statement** | D5 |
| **Silent first-wins on a collision** | D8 |
| **A check satisfied by absence** | D12 |
| **One fact, several copies** | D14 |
| **The absence of an error is the signal** | D12, D15 |
| **Environment, not code** | D10, D12, D15 |

**If three or more of your misses share a class, that class is the gap** — not
the individual bugs. Put the **class** in `LEARNING_PROGRESS.md` Part 3.

---

# PART 2 — ROADS NOT TAKEN

## 6. Three refusals, argued both ways

**The exercise:** for each, argue **for** building it as strongly as you can,
then give LedgerMind's actual reason, then say what would have to change for the
answer to flip. **A refusal you can only defend is not understood.**

---

### 6.1 Agentic RAG — textbook Part 11

**What it is.** The model plans: decide what to retrieve, look, decide again,
possibly call a tool, iterate until satisfied. ReAct loops, tool-calling agents,
self-directed multi-hop.

**The case FOR, made properly.**

- Multi-hop questions genuinely need it. *"Did the metric management highlighted
  in Q1 improve by Q4?"* requires reading Q1, extracting a metric name, then
  querying Q4 — **and LedgerMind cannot answer it today.**
- An agent could recover from its own bad retrieval instead of refusing.
- It generalises: new question shapes need no new code path.

**LedgerMind's answer: deterministic, by decision.** The DSL repair loop is
bounded at **2** and repairs **schema, not strategy** — a malformed object is
retried; a wrong *approach* is not. CRAG is a **filter ladder**, not a query
rewrite. There is no planner anywhere.

**The reason, in this system's terms.** An agentic loop makes the number of LLM
calls **a function of the model's judgement**. Against 500/day that is a budget
you cannot forecast. Worse: it makes the *path* to an answer irreproducible, and
this system's product is an answer you can **defend**. *"The agent decided to
look again"* is not a lineage record. **`audit_log` has columns for a fixed
pipeline** — `dsl_generated`, `sql_executed`, one path — and an agent's trace does
not fit them.

**And the deepest reason:** the invariant is *the LLM never does arithmetic and
never chooses the query*. An agent that decides what to retrieve is choosing the
query. **You would be trading the guarantee for the capability.**

**What would flip it.** A paid tier removing the call budget, **plus** a
lineage format that can record a variable-length plan, **plus** a bounded agent —
"at most three retrievals, each logged, each with its own citation set" — so the
audit row stays complete. **Note that the third condition is the hard one**, and
that it is really a request for a *bounded* agent, i.e. a bigger state machine.

---

### 6.2 Graph RAG — textbook Part 12

**What it is.** Extract entities and relations into a knowledge graph (Neo4j),
traverse for multi-hop, retrieve over structure rather than similarity.

**The case FOR.**

- *"Which subsidiaries contributed to the revenue growth management attributed to
  quick commerce?"* is a **traversal**, and flat retrieval answers it badly.
- Subsidiary relationships are **already** in this system — as
  `SUBSIDIARY_TO_PARENT`, a **hand-maintained dict** in `cross_engine.py` with
  two entries. That is a graph with the graph part removed.
- Entity resolution already exists (`entity_resolver`), which is most of the
  extraction work.

**LedgerMind's answer**, and it is one sentence: *"flat retrieval is not yet the
bottleneck."*

**Take that seriously as a methodology claim, not a dismissal.** It says: **the
measured failures are not traversal failures.** Read the bug list — incompatible
score scales, an overloaded `null`, a suppressed true sentence, a first-wins
alias collision, a citation floor. **Not one of them would have been prevented by
a knowledge graph.**

**And the cost is not the graph, it is the extraction.** Entity and relation
extraction from filings is itself an LLM task, run over the corpus, with **its
own error rate** — and this system's whole posture is that an unverified
extraction is worse than a missing one. **You would be adding a probabilistic
layer beneath a system built to avoid exactly that.** Plus a fourth datastore
against a 512 MB, zero-cost deployment.

**What would flip it.** A golden category of traversal questions that flat
retrieval **measurably** fails — the evidence that does not exist today — plus a
way to verify extracted relations. **Note the order: the measurement first.** The
same rule as everywhere else in this project.

---

### 6.3 Multimodal — textbook Part 13

**What it is.** Send page images to a vision model; read charts, scanned tables,
and layout that text extraction loses.

**The case FOR, and it is the strongest of the three.**

- **CAVEAT-003 is exactly this failure**: a page whose column layout fails to
  parse is **skipped silently**. A vision model would read it.
- **CAVEAT-014's cousin, BUG-014**: a page that fails to parse leaves **no
  trace**.
- Financial filings are full of charts whose numbers appear nowhere in the text.
- **F3, the open blocker**: unit-scale detection (crore vs lakh vs million) is
  often a **layout** fact — a column header — and layout is what parsing loses.

**LedgerMind's answer: positional extraction into typed SQL rows.** Divergence
**D9**, and note that it is the *opposite* of the textbook's 13.3, which
recommends **captioning** tables into text.

**And the reason is the sharpest sentence in the divergence register:**
**captioning a balance sheet destroys the exact-value guarantee.** A caption is
prose. Prose is retrieved, embedded and paraphrased. **The moment a number is
prose, `sql_verified` cannot mean anything** — you have converted a typed row
into a sentence a model will re-type.

**So the refusal is not "vision is too expensive". It is that the textbook's
multimodal path terminates in text**, and this system's quantitative guarantee
requires the number to terminate in a **typed database column**.

**What would flip it — and this one has a real answer.** Vision used **at
ingestion**, not at query time, emitting **structured rows** rather than
captions: a vision model reads the page and produces `(metric, period, value,
unit)` tuples that go through the **same** validation, identity checks and
`is_latest` machinery as `financial_extractor`'s output. **Then the guarantee is
intact** — the extraction is probabilistic, as it already is, but the *storage
and verification* are unchanged.
**That is a genuinely open, unclaimed improvement**, and the honest framing is
that the objection is to captioning, **not** to vision.

---

## 7. Two more refusals, for completeness

**Parent-child chunking** (textbook 4.6, divergence **D5**). Not built. Instead:
per-block-type targets, `OVERLAP_TOKENS = 150`, and near-duplicate suppression at
0.70. **The 150 was raised from 50 after a mid-sentence split orphaned Paytm's
PPBL impairment** — a measured cause for a measured constant.

**Sandwich chunk ordering** (textbook 15B, divergence **D4**). *"Not
implemented. A genuine unclaimed improvement → KNOWN_UNKNOWNS, not silently
fixed."* **Notice what was done with it:** the textbook's suggestion was neither
adopted nor dismissed. It was **recorded as an open question**, because nobody
had measured whether it helps here. **That is the correct disposal of an
unevaluated good idea.**

---

# PART 3 — DESIGN THE NEXT ONE

## 8. Keep · change · measure first

> Write your own before reading §9. **Three lists, with a reason each. No item
> without a reason.**

**Keep** — what you would carry into a system built from scratch tomorrow.
**Change** — what you would do differently, knowing what it cost here.
**Measure first** — what you would refuse to decide without data, and the
measurement you would run.

**The constraint that makes this an exercise rather than a wish list:** for every
"change", state **what it costs** and **what it breaks**. A change with no cost
is one you have not thought about.

---

## 9. Answer key — Part 3

> **Only after writing yours.** Yours may differ; the *reasoning* is what is
> being examined.

### 9.1 Keep

| Keep | Because |
|---|---|
| **The LLM never does arithmetic** | The single load-bearing invariant. It produces the verification guarantee **and**, as a side effect, the containment of prompt injection |
| **Refusal as a first-class outcome** | Its own edge, its own audit row, its own tests, 12 % of the golden set. *"A wrong answer with a ✓ is worse than a refusal"* is only true if refusal is engineered |
| **Exact-value assertions over a judged score** | A score is not a decision, and an LLM judge shares the failure modes of what it grades |
| **One metric registry** | It replaced three drifting dicts that caused three shipped bugs |
| **Omit rather than substitute** | Every substitute is a claim. Six instances, each preventing a specific false assertion |
| **The measurement written beside the constant** | `OVERLAP_TOKENS = 150` is a conclusion; the comment is the evidence. Without it the number is folklore |
| **Recording debt instead of deleting it** | `cache_hit_rate_pct` at 0.0, marked at three layers, is a better artefact than a clean codebase with no memory |
| **A single path-aware boundary in the UI** | Twenty components ignorant of the pipeline; a fourth path touches one function |
| **Documents that state their limitations** | `SECURITY_MODEL.md`'s threat/defence/**limitation** shape — and note that today's course found two of its claims wrong, **which is the format working, not failing** |

### 9.2 Change

| Change | Cost, and what it breaks |
|---|---|
| **CAVEAT-001 first — the body `tenant_id` override** | Two lines. Breaks any pre-auth script that posts a tenant; **removing a Pydantic field makes an extra key ignored, not an error**, so a stale caller fails silently and correctly. **Do it before the second tenant exists** |
| **CI, even one job** | Hours. `pytest` + `tsc` + the retry guard, all zero-LLM. **And it must encode the 218/25 baseline** — a CI that is red on arrival gets ignored, which is conftest's own lesson |
| **A discriminator on `sql_result` rows** | Backend emits `kind`; frontend gets a discriminated union. Removes duck-typing across a language boundary. **Does not fix an unknown `kind`** — only a shared schema would |
| **A `parse_failure` record instead of a silent skip** | CAVEAT-003/BUG-014. A skipped page currently leaves **no trace**; a row per failure makes coverage measurable. Costs a table and a decision about what to do with the rows |
| **Vision at ingestion, emitting structured rows** | §6.3. **Not captioning.** Costs an ingestion-time model call per page and a new error mode; buys F3 and CAVEAT-003 |
| **`reranker_backend` as an `audit_log` column** | One column, one migration. Historic scores are already unrecoverable — this stops the loss going forward |
| **Retire rather than delete in the purge scripts** | Aligns them with ED-018's restatement model, and sidesteps CAVEAT-028's grant divergence entirely |
| **Company onboarding as data** | CAVEAT-019. Today a new issuer is a code edit and a deploy |

**And two I would *not* change, against instinct:**

- **The raw-psycopg2, no-ORM decision.** *"SQLAlchemy adds nothing for flat
  record inserts."* The SQL here is a security boundary; hiding it behind an
  abstraction makes the boundary harder to audit, and every parameterisation is
  visible today.
- **`page.tsx` at 584 lines.** It is the lowest common ancestor of state that is
  genuinely shared. Splitting it means a context provider — real machinery for a
  file that is long but not complex. **Long is not the same as tangled**, and
  routing (which *would* force the split) is a different change.

### 9.3 Measure first

| Question | The measurement | Why it must precede the decision |
|---|---|---|
| **Is `COHERE_MEDIUM = 0.15` right?** | KU-003. It is the **refuse-vs-answer boundary and has never been exercised by a real query.** Log the score distribution across a sweep first | It is **unvalidated, not validated**, and tuning it blind moves a refusal boundary nobody has observed |
| **Is a cache worth building?** | `SELECT query_text, count(*) FROM audit_log GROUP BY 1 HAVING count(*)>1` — **answerable today, offline, before writing any code** | If exact repeats are rare, the whole feature is unjustified |
| **What is the response-length distribution?** | KU-006. Already being logged pre-write, deliberately with **no** warn-above-N | *"A threshold warning is a cap that has not fired yet"* — and the old truncation destroyed the evidence |
| **Does sandwich ordering help here?** | Divergence D4. A scoped eval on the semantic categories, both orderings | An unevaluated good idea belongs in KNOWN_UNKNOWNS, not in the code |
| **Was TQ008's route a regression?** | KU-001. Three classify calls at the pre-`d365f4b` commit, provider and model printed per run | *"Cause cannot be assigned from a single before/after pair"* — attempted three times, wrong three times |
| **Does flat retrieval fail traversal questions?** | A golden category that does not exist | Without it, Graph RAG is a preference |
| **Why Cohere rather than the local cross-encoder?** | KU-005. A quality comparison nobody ran | The RAM argument alone does **not** explain the ordering, since the ONNX model also fits |

**Notice the shape of that table.** Six of seven are **already recorded as open
questions** by the project itself. **The discipline being examined is not
inventing measurements — it is refusing to decide without them**, and knowing
where the list is kept.

---

# THE VIVA

## 10. The full stack, out loud, nothing open

**Twenty minutes. Speak it.** Each rung: what it is here, why, and one way it
fails.

```
 1. MACHINE        512 MB, and the eight decisions that follow
 2. TERMINAL       docker compose up -d; up ≠ serving; poll /health
 3. PROCESS        one uvicorn worker; six thread limits; lazy singletons
 4. NETWORK        Render · Vercel · Supabase · Qdrant Cloud · three APIs
 5. HTTP           POST + Bearer; SSE as the second transport
 6. API            FastAPI, Pydantic contract, four routers, /health
 7. AUTHENTICATION bcrypt direct; HS256; 2 h; no revocation
 8. AUTHORIZATION  route-level rank ladder + field-level, failing closed
 9. BACKEND        ONE dict through eight nodes. No layers
10. DATABASE       RLS + FORCE; SET LOCAL; zero rows, not an error
11. ROUTING        classify → resolve → refuse. Three paths, two bypass edges
12. RAG            why it exists: parametric vs non-parametric memory
13. RETRIEVAL      dense + sparse → RRF → rerank → dedup → CRAG → confidence
14. VERIFICATION   DSL → SQL → Python arithmetic → sql_verified
15. LLM            structured output; schema is prompt; timeout then failover
16. RESPONSE       role filter → JSON → one path-aware function → JSX
```

**Then the five that tie it together:**

1. **Name the invariant** that appears at rungs 9, 14, 15 and 16 — and say what
   it buys at each.
2. **Where does the system refuse?** Five places. What is each for?
3. **Name three guarantees that are enforced and three that hold by
   convention.**
4. **Trace one number** from a PDF page to a rendered ✓, naming every
   transformation.
5. **Where is it weakest**, and what would you fix first?

---

## 11. Answer key — the viva's five

> **After speaking them.**

1. **The LLM never does arithmetic and never writes SQL.**
   **Rung 9:** it is why `QueryState` carries a `dsl_object` rather than a query
   string — the model's output is *data*, and the pipeline is the executor.
   **Rung 14:** it is what `sql_verified` means — a value returned by SQL that
   Python compiled and Python computed over.
   **Rung 15:** it is why the response schema matters more than the prompt text —
   the model's job is to fill a shape, not to reason to an answer.
   **Rung 16:** it is why a ✓ can be rendered at all. **And it is why a
   successful prompt injection can influence prose and cannot forge a verified
   figure** — a security property that falls out of a correctness decision.

2. **Five refusal points.**
   **(a) Prompt Shield** — SEBI advice and injection, before anything runs.
   **(b) The router** — `company_not_in_corpus`, F2's edge, because searching
   unfiltered answered confidently about a company not held.
   **(c) The quantitative guards** — three, pre-LLM, refusing rather than
   substituting a nearby metric (the EBITDA silent substitution).
   **(d) Confidence** — `low_confidence_refusal` when retrieval is too weak to
   support an answer.
   **(e) The ingestion gate** — before a document enters the corpus at all.
   **Each is "a wrong answer with a ✓ is worse than a refusal", at a different
   layer.**

3. **Enforced:** RLS with `FORCE` and a `NOSUPERUSER` role; **no `DELETE` on
   `audit_log`** (verified on both databases); parameterised SQL built from fixed
   literals, so model output cannot reach the query text; field-level RBAC that
   **fails closed** on an unknown role; `golden_dataset/` mounted read-only.
   **By convention:** `audit_log` content immutability — **`UPDATE` *is* granted**
   and nothing uses it (CAVEAT-028); `ADMIN_DATABASE_URL` bypassing RLS, whose
   *"only protection is that no request-path code reads it"*; the three parallel
   audit arrays staying aligned; `sql_result` row shapes matching what the
   frontend duck-types; the `cache_hit` field never being rendered.

4. **PDF page → rendered ✓.**
   `pdf_parser` (pdfplumber, **word-level positions**, tables before text) →
   `PageBlock` → `document_classifier` / `section_classifier`
   (three-signal intersection) → `financial_extractor`
   (**positional column detection**, not captioning) → `normalize_metric_label`
   via `entity_resolver` against **the single registry** → derived totals and
   `validate_financial_identities` → `db_loader`, retiring prior rows by full
   business key and setting `is_latest` under a **partial unique index** →
   *[the request begins]* → router → DSL object → `DSLValidator` → three guards
   → `SQLCompiler` builds parameterised SQL from fixed literals →
   `SET LOCAL app.tenant_id`, RLS → rows → **Python arithmetic** →
   `sql_verified = True` → `_format_quant_response` **template** →
   `role_filtered_response` → JSON → `lib/api.ts` → `composeDocumentBody`
   branch 5 → `MetricCallout status={sql_verified ? "verified" : "estimated"}`
   → **✓**.
   **Count the places the number could change and does not: it is copied,
   never re-derived, from `db_loader` onward — and the LLM never re-types it,
   because `CROSS_SCOPE_INSTRUCTION` says a restatement risks transcription
   drift.**

5. **Weakest, in order, and the first is not close.**
   **(1) CAVEAT-001** — the body-supplied `tenant_id` defeats the tenant boundary
   from **above**, so RLS, the vector filter and the audit row all work perfectly
   on the attacker's chosen tenant. Unexploitable **only because one tenant is
   seeded**, which is a property of the data and expires with no code change.
   **(2) No CI** — CAVEAT-022, and 25 errored tests went unnoticed for a day
   because the cheap check nobody notices is the one nobody runs.
   **(3) F3, unit scale** — every stored value is *asserted* to be in crore. It is
   the blocker for arbitrary documents.
   **(4) Indirect injection** — undefended, bounded today by admin-only upload and
   a three-company curated corpus, i.e. by scale.
   **(5) No rate limiting anywhere**, including login.
   **Fix order: (1) before a second tenant exists. Then (2), because everything
   else is safer to change once something is watching.**

---

## 12. Where to go from here

**The course ends; the documents do not.**

| Document | Its job now |
|---|---|
| [`CAVEATS.md`](../../engineering/CAVEATS.md) | 28 entries. **The work queue** |
| [`KNOWN_UNKNOWNS.md`](../../engineering/KNOWN_UNKNOWNS.md) | 6 questions. **The measurement queue** |
| [`BUGS_AND_LESSONS.md`](../../journal/BUGS_AND_LESSONS.md) | 14 bugs, 8 recurring classes. **Read before proposing a fix** |
| [`DEBUGGING_GUIDE.md`](../../engineering/DEBUGGING_GUIDE.md) | 12 sections. **Read when something is wrong** |
| [`LEARNING_PROGRESS.md`](../LEARNING_PROGRESS.md) | Part 3 is now yours. Part 5 is the final exam |
| [`MASTER_REQUEST_TRACE.md`](../../architecture/MASTER_REQUEST_TRACE.md) | **Yours, from Day 46.** Re-check it after any structural change |

**And the standing obligations**, which are not optional and are not covered by
any of the above:

```text
- regression_check.py after ANY extraction change — not batched
- purge_orphaned_metrics --dry-run after ANY extraction change
- IMPLEMENTATION_DELTAS.md in the SAME COMMIT as anything that makes a
  blueprint statement untrue
- Never eval_runner.py without per-run approval
- One commit per file. git diff --stat before every commit
```

---

## 13. MUST REMEMBER

```text
- Backwards: SYMPTOM → kind of wrong → LAYER → file/field → a COMMAND →
  what would disprove me
- Name the layer BEFORE opening anything
- Empty candidate set = NETWORK. Low-scoring = RETRIEVAL
- "Intermittently" is a diagnosis: skew, flap, or a leaked GUC
- Positional failure = QUOTA. Categorical failure = DEFECT
- The absence of an error is a signal: RLS zero rows; a silently skipped page;
  a 200 OK that took 120 seconds
- A message can name a threat and describe a different cause ("container
  breakout" = a stale mount namespace)
- THREE roads not taken, each with a stated reason: agentic (determinism over
  agency) · graph (flat retrieval is not yet the bottleneck) · multimodal
  (captioning a balance sheet destroys the exact-value guarantee)
- The objection to multimodal is to CAPTIONING, not to vision. Vision at
  INGESTION emitting STRUCTURED ROWS keeps the guarantee intact
- A refusal you can only defend is not understood. Argue both sides
- Six of seven "measure first" items are ALREADY recorded as open questions —
  the discipline is refusing to decide without them, and knowing where the
  list is
```

## 14. MUST UNDERSTAND

```text
- Why patterns beat instances: score yourself by CLASS, because three misses
  sharing a class is one gap, not three
- Why a correct measurement can justify a WRONG constant (the 0.05 citation
  floor), and why "measure before reverting" is a separate rule from
  "measure before shipping"
- Why sql_verified guarantees the PIPELINE and not the DOCUMENT, and why
  BUG-009 is the proof
- Why an agentic loop trades a GUARANTEE for a CAPABILITY, and why the
  audit_log schema is part of that argument
- Why "flat retrieval is not yet the bottleneck" is a methodology claim: not
  one recorded bug would have been prevented by a knowledge graph
- Why adding a probabilistic extraction layer beneath a system built to avoid
  probabilistic answers is the real cost of Graph RAG here
- Why the strongest security property in this system is a SIDE EFFECT of a
  correctness decision
- Why a document that states its limitations is working when its claims are
  found wrong — and why the finding goes to CAVEATS.md rather than into your
  head
- Why the first thing to fix is the one that is currently unexploitable
```

---

## 15. This connects to

```text
Day 46 — the master trace, from memory
   ↓
Day 47 — failure drills, roads not taken, viva        ← END OF THE COURSE
   ↓
The caveat list · the unknown list · the standing obligations
```

**What you can now say, and it is the thing the course was for:**

> I understand why LedgerMind uses Qdrant here, what happens to a document
> before it reaches Qdrant, how the query reaches retrieval, why dense and
> sparse retrieval are both used, how the results are fused and reranked, how
> the context reaches the LLM, how authentication protects the request, how the
> structured path differs from the semantic path, what can fail at each stage,
> and why we made each architectural decision.

**And the two sentences under all of it:**

> **A wrong answer with a ✓ tick is worse than a refusal.**
>
> **When something looks over-engineered, ask what wrong answer it prevents.
> There is a documented answer every time.**
