# Day 46 — The Master Trace, From Memory

**Phase 13 · Weight: H (~120 min) · Prerequisites: everything**

**Textbook: Part 17 — CONFIRMS.** The textbook closes with a generic master
flow: cache → retrieve → rerank → generate → return. Today you write
LedgerMind's, **then account for every difference.** There are more than you
expect, and one of them is that step 1 does not exist here.

---

## 0. What today is

**Today is not a lesson. It is a deliverable.**

You write `docs/architecture/MASTER_REQUEST_TRACE.md` — browser to rendered
answer, every arrow carrying its file, its function, its input, its output, why
it exists, and how it fails.

**Written with the repository closed.** Then checked against the code, with every
gap marked and fed into `LEARNING_PROGRESS.md` Part 3.

**The document is the proof.** Not the reading of this page.

> **Read §1 and §2, then close this file too.** Everything after §3 is for the
> checking pass, and reading it first turns a recall exercise into a copying
> exercise. That is not a rule about honesty — it is that **a trace you copied
> tells you nothing about what you know**, and the entire value of today is the
> list of gaps it produces.

---

## 0.5 Why now

**Because recall and recognition are different, and only one of them is what you
came for.**

Forty-five days of reading produce a powerful illusion: every file is familiar,
every term lands, nothing surprises you. **That is recognition.** It is real and
it is not the same as being able to produce the system from nothing — and the
only instrument that separates them is a blank page.

There is a second reason, specific to this course. Every day so far has been
*given* to you in an order chosen for you. Today you supply the order. **A trace
is a claim about what depends on what**, and you have been told the dependency
graph twelve times without ever having to construct it.

And a third, which is operational rather than pedagogical: **Day 47's material is
selected by today's output.** `LEARNING_PROGRESS.md` Part 4 says tomorrow covers
*"whatever Day 46 exposed as thin."* If §10 of your document is empty, tomorrow
has nothing to work on.

---

## 1. The protocol

Four passes, in this order. **Do not merge them.**

```
PASS 1  — CLOSED BOOK.        Write the whole trace. ~60 min.
             Repository closed. This document closed. No greps.
             Where you cannot recall a name, write [?] and KEEP GOING.

PASS 2  — MARK YOUR OWN GAPS.  ~5 min.
             Before opening anything, go back and mark every [?],
             every "something like", every arrow you drew but could not
             name. THIS LIST IS THE ACTUAL OUTPUT OF TODAY.

PASS 3  — CHECK AGAINST CODE.  ~40 min.
             Now open the repository. Fill the gaps, and — more
             importantly — find the places you were CONFIDENT AND WRONG.
             Mark corrections visibly. Do not silently overwrite.

PASS 4  — FEED BACK.           ~15 min.
             Every gap and every correction becomes a row in
             LEARNING_PROGRESS.md Part 3, with the day it was raised.
             Then compare against the textbook's Part 17 master flow.
```

**Pass 2 is the one people skip, and it is the one that matters.** After Pass 3
you will no longer be able to reconstruct what you did not know, because you will
know it. **The gap list only exists between Pass 1 and Pass 3.**

**On `[?]`:** write it and move on. Stopping to retrieve one function name costs
the momentum that carries you through the next five arrows, and the fact that you
could not recall it *is data*.

---

## 2. The specification

Write it to `docs/architecture/MASTER_REQUEST_TRACE.md`.

### 2.1 Every arrow carries six things

```
FROM → TO
  file:        the actual path
  function:    the entry point that runs
  in:          what it receives — the field names, not "the state"
  out:         what it produces or mutates
  why:         why this step exists at all
  fails:       what goes wrong here, and how it presents
```

**`why` and `fails` are the two that cannot be faked by careful reading.** If an
arrow's `why` reads *"to process the query"*, you have not got it. Every step in
this system exists to prevent a **specific** wrong answer, and you have been told
what, on the day you met it.

### 2.2 The three traces

Trace **one** request all the way through for each path:

| Trace | Question | Ends at |
|---|---|---|
| **A — quantitative** | *"What was Titan's standalone revenue for Q1FY26?"* | a ledger table and a ✓ |
| **B — semantic** | *"What risk factors does Eternal disclose?"* | cited prose |
| **C — refusal** | *"Should I buy Titan stock?"* | a policy block |

**Trace C is short and is not optional.** It is the one that proves you
understand the graph's edges rather than its nodes, and it is where most people
discover they cannot say what happens to `confidence_tier`.

### 2.3 Required sections

```
1.  Topology            — the deployment picture. What runs where.
2.  Trace A             — quantitative, browser to rendered ✓
3.  Trace B             — semantic, browser to cited prose
4.  Trace C             — refusal, browser to policy block
5.  Where the paths diverge, and where they rejoin
6.  Every boundary crossed, and what changes shape at it
7.  The invariants — what is true at every step, and what enforces it
8.  Failure modes, by layer
9.  What the textbook's Part 17 does that this does not, and vice versa
10. My gaps  ← Pass 2's output, kept in the document, dated
```

**§10 stays in the finished document.** A trace with no gap section is either
untrue or was written with the code open.

### 2.4 What "browser to rendered answer" means

Start at the keypress. End at the pixels. **Do not start at the API.**

The frontend half is three days old (38–41) and is the half people skip. It is
also where two of this course's sharpest lessons live: the retry that is a second
pipeline, and the render boundary that keeps twenty components ignorant of the
engine.

---

## 3. Scaffolding — the arrow count, and nothing else

> **STOP unless Pass 1 and Pass 2 are done.**

Below is a **completeness check only**: how many arrows each trace should have,
and the boundaries each must cross. **No names, no order, no content.** If your
trace has six arrows in a stage that should have nine, you know where to look —
without being told what is missing.

| Stage | Trace A | Trace B | Trace C |
|---|---:|---:|---:|
| Browser → HTTP | 4 | 4 | 4 |
| HTTP → graph entry | 5 | 5 | 5 |
| Graph traversal | 6 | 7 | **2** |
| Engine internals | 7 | 11 | 0 |
| Response shaping → render | 6 | 7 | 5 |
| **Total** | **28** | **34** | **16** |

**Boundaries every trace must cross**, and name what changes shape at each:

```
1. keypress            → JavaScript event
2. TypeScript object   → JSON over HTTPS
3. JSON                → Pydantic model
4. JWT string          → verified claims dict
5. request fields      → QueryState (TypedDict)
6. QueryState          → node input / mutated output      × N
7. QueryState          → filtered response dict            (role)
8. Python dict         → JSON
9. JSON                → TypeScript QueryResponse
10. QueryResponse      → JSX
```

**Ten boundaries. Trace C crosses all ten too** — which is part of why it is
required.

---

## 4. The marking scheme

> **Pass 3.** Score yourself honestly; the number is not the point, the pattern
> in what you missed is.

**Per arrow: 6 points, one per field.** Then, per trace, these **structural**
questions — each worth more than any single arrow, because each is a place people
produce a *fluent* wrong answer.

### Trace A — quantitative

| # | Check | Common wrong version |
|---|---|---|
| A1 | Did you have the LLM emit a **structured object**, not SQL? | "the LLM generates the query" |
| A2 | Did **Python** compile the SQL, from fixed literals? | "the DSL is turned into SQL" — by what? |
| A3 | Did you set the tenant GUC **before** the SELECT? | omitted entirely |
| A4 | Did **arithmetic** happen in Python, over fetched values? | "the SQL computes growth" |
| A5 | Did you name **all three** pre-LLM guards? | naming one, or none |
| A6 | Did the repair loop have a **bound**, and repair **schema, not strategy**? | "it retries until it works" |
| A7 | Did `sql_verified` get set by something that **ran SQL**? | set by the model, or by the router |
| A8 | Did the answer text come from a **template**, not generation? | "the LLM writes the answer" |

### Trace B — semantic

| # | Check | Common wrong version |
|---|---|---|
| B1 | **Two** retrieval signals, produced how? | one vector search |
| B2 | Fused by a method that **ignores raw scores**? | "the scores are averaged" |
| B3 | Where does the tenant filter go — and why **there**? | "applied to the results" |
| B4 | Reranking is a **different model class** from embedding? | "re-sorted by score" |
| B5 | Two **threshold pairs**, chosen by **which field**? | one threshold |
| B6 | Dedup, and the denominator is the **smaller** chunk? | omitted |
| B7 | CRAG as a **filter ladder**, bounded? | "it retries retrieval" |
| B8 | Citations built from **retrieved chunks**, not parsed from prose? | "the model cites its sources" |
| B9 | Confidence measures **willingness to answer**, not correctness? | "confidence means it is right" |

### Trace C — refusal

| # | Check | Common wrong version |
|---|---|---|
| C1 | Which node is the **entry point**? | the router |
| C2 | Which edge does a block take, and **what does it skip**? | "it returns early" |
| C3 | What happens to `confidence_tier`, and **why**? | "it is low" |
| C4 | How many LLM calls? | "one, to classify" |
| C5 | Is a row written? What is in `query_path`, `llm_provider`? | "blocks are not logged" |
| C6 | What does the client render, and from which branch? | the generic error branch |

### Cross-cutting — the ten that separate reading from understanding

| # | Question |
|---|---|
| X1 | Name **six** things that are **omitted rather than substituted**, and what each substitute would have asserted. |
| X2 | Name **five** instances of one value overloading two meanings, and the fix each got. |
| X3 | Where does the **same fact** exist in two places, and what keeps them in step? |
| X4 | Which guarantees are **enforced** and which hold **by convention**? Name three of each. |
| X5 | Name every point where the pipeline can **refuse**, and what each refusal is for. |
| X6 | Which decisions are traceable to **512 MB**? |
| X7 | Where does **client input steer the pipeline**, and what bounds it? |
| X8 | Which fields have **no producer**, and why do they still ship? |
| X9 | What does a **retry** cost, and which single failure is retried? |
| X10 | Where is the **single highest-priority security item**, and why is it currently unexploitable? |

**If you can answer X1 through X10 cold, you have the system.** Every one is a
pattern that recurs across at least three subsystems, which is why they are the
questions rather than "name the reranker".

---

## 5. Checking against the textbook — Part 17

The textbook's master flow, roughly:

```
query → CACHE CHECK → embed → vector search → rerank → build prompt
      → generate → return with citations
```

**Account for every difference.** Nine to find; here are the axes, not the
answers:

| # | Axis | Prompt |
|---|---|---|
| D1 | Step 1 | What does LedgerMind do instead, and what ships as a permanent 0.0 because of it? |
| — | Before step 1 | Two nodes run before anything the textbook lists. Name both. |
| — | Retrieval | The textbook has one signal. How many here, fused how, filtered where? |
| — | Reranking | One reranker there. How many here, and why does the **count** matter? |
| — | Generation | The textbook always generates. Name a path here that does not. |
| — | Arithmetic | Where does the textbook do maths, and where is it forbidden here? |
| — | Citations | Where do the textbook's citations come from, and where do these? |
| — | Disagreement | The textbook's synthesiser merges. What does this do that has no equivalent? |
| — | Refusal | Where is refusal in the textbook's flow? |

**The largest difference is not on that list**, and you should state it in your
own words: **the textbook's flow describes one path. This system chooses among
three, and the choice is itself a step with its own failure mode.**

---

## 6. Common failure patterns in this exercise

**Read these after Pass 2, before Pass 3.** They are the shapes a wrong trace
takes, and recognising yours saves the checking pass.

**1. The fluent middle.** Browser and database are precise; the middle is
*"then it retrieves relevant chunks and generates an answer."* **That sentence is
Days 25–30 compressed into eleven words**, and the compression is where the
knowledge is not.

**2. Nodes without edges.** Eight nodes named correctly, and no account of what
routes between them, which edges skip which nodes, or why two of them bypass the
confidence tail.

**3. Happy path only.** Every arrow's `fails` field reading "it errors". A trace
with no failure modes is a diagram, not a trace.

**4. The LLM does too much.** Any sentence where the model writes SQL, does
arithmetic, or decides `sql_verified` is a **structural** error, not a detail —
it inverts the system's central invariant.

**5. Missing the frontend.** Starting at `POST /api/query`. **The specification
says keypress to pixels.**

**6. Silent overwriting in Pass 3.** Correcting a wrong arrow without marking
that it was wrong. **A correction you cannot see is a gap you will repeat.**

**7. Confidence as correctness.** Writing that a high tier means the answer is
right. It measures **willingness to answer**, and `eval_runner`'s own docstring
records tier=high with reranker scores of −2.5 to −5.1 on unrelated chunks.

---

## 7. Hands-on — after Pass 3, verify three arrows against a live run

Pick the three arrows you were **least** sure about and verify them by
observation, not by reading.

### Verification 1 — the SSE trace names its own nodes

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@alpha.ledgermind.test","password":"demo1234"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "token length: ${#TOKEN}"

curl -N -s -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What was Titan standalone revenue for Q1FY26?"}' \
  | grep -E "^event:|^data: \{\"node\"" | head -20
```

**Every `node` event is one arrow in your Trace A.** Count them against what you
wrote. The trace is a byproduct of real execution (Day 39), so it cannot lie
about which nodes ran.

### Verification 2 — the same, for a refusal

```bash
curl -N -s -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"Should I buy Titan stock?"}' \
  | grep -E "^event:" | head
```

**Two node events.** If your Trace C had more, find what you thought ran.

### Verification 3 — the shape the state takes at the boundary

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What was Titan standalone revenue for Q1FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('keys:', sorted(d))
print()
print('dsl_object :', json.dumps(d.get('dsl_object')))
print('sql_query  :', (d.get('sql_query') or '')[:120])
print('sql_result :', json.dumps(d.get('sql_result')))
print('verified   :', d.get('sql_verified'))
"
```

**Read `dsl_object` and `sql_query` side by side.** The first is what the model
produced; the second is what Python built from it. **If your trace had the model
producing the second, that is the correction to mark.**

### Verification 4 — the audit row as an independent record

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -x -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   SELECT query_path, llm_provider, llm_model, confidence_score, latency_ms,
          array_length(retrieved_chunk_ids,1) AS n_chunks
   FROM audit_log ORDER BY created_at DESC LIMIT 3;"
```

**Three rows: two queries and one block.** Reconcile each against the trace you
wrote for it.

---

## 8. Feeding back — Pass 4

**Every gap and every correction becomes a row** in `LEARNING_PROGRESS.md`
Part 3:

```markdown
| Opened | Concept | The actual question | Day raised | Resolved | How |
|---|---|---|---|---|---|
| 2026-08-23 | RRF | Why does k=60 damp the top ranks? | 27 | | |
| 2026-08-23 | CRAG | What exactly triggers a retry vs a refusal? | 29 | | |
```

**Write the actual question, not the topic.** *"RRF"* is not a question and
cannot be closed. *"Why does k=60 damp the top ranks?"* can.

**Then update Part 2's concept blocks.** A concept you could not trace today does
not have `trace` ticked, whatever you marked on the day.

**And Part 4 says Day 47 covers "whatever Day 46 exposed as thin."** So Part 3's
rows are literally tomorrow's syllabus. **This is the mechanism by which the
course adapts to you**, and it only works if §10 of your document is honest.

---

## 9. What "done" looks like

`docs/architecture/MASTER_REQUEST_TRACE.md` exists and:

```text
[ ] Traces A, B and C are complete, keypress to pixels
[ ] Every arrow carries file · function · in · out · why · fails
[ ] No `why` field reads "to process the query"
[ ] All ten boundaries appear, with what changes shape at each
[ ] The divergence/rejoin section names both bypass edges
[ ] The invariants section distinguishes ENFORCED from BY CONVENTION
[ ] The Part 17 comparison accounts for every difference
[ ] §10 "My gaps" is present, dated, and not empty
[ ] Corrections from Pass 3 are VISIBLE, not silently applied
[ ] LEARNING_PROGRESS.md Part 3 has a row per gap
```

**The ninth line is the one to be strict about.** A document that hides its
corrections is optimised for looking finished, and this one is not for anyone
else to read.

---

## 10. Self-check questions

> These are **not** the trace. Answer them after Pass 4, cold.

**Basic**

1. Name the eight graph nodes.
2. Name the three paths and who chooses.
3. Name the two retrieval signals.
4. What are the two reranker backends, and why does that matter?
5. Which node writes the audit row, and on which paths?

**Code**

6. What exactly does the LLM emit on the quantitative path?
7. Which function compiles SQL, and from what?
8. Where is the tenant filter applied in Qdrant, and where in Postgres?
9. Which two edges skip `confidence_node`?
10. Which single frontend function is path-aware?

**Why**

11. Why is arithmetic forbidden to the model?
12. Why are there two threshold pairs?
13. Why does a refusal need its own edge?
14. Why is a false contradiction worse than a missed one *here*?
15. Why does `cache_hit_rate_pct` ship at 0.0?

**Debugging**

16. Empty citations. First check?
17. Same query, two tiers. First check?
18. A number is wrong and `sql_verified` is true. Which layer?

**System design**

19. Add a fourth path. Name every file, table and document that changes.
20. Your trace disagrees with the code in one place. How do you decide which is
    wrong?

---

## 11. Answer key

> **Only after Pass 4.**

### §10 — Basic

1. `prompt_shield` · `router` · `semantic_engine` · `quant_engine` ·
   `cross_engine` · `confidence` · `response_generator` · `audit_writer`.
2. `semantic`, `quantitative`, `cross`. Chosen by `router_node` — an LLM
   classification returning a structured `RouterResponse`, **overridable by
   `execution_context.enforce_path`**, which is unvalidated client input placed
   *after* the F2 refusal so it cannot route past a failed entity resolution.
3. Dense (`bge-small-en-v1.5`, 384-dim, fastembed ONNX) and sparse
   (`Qdrant/bm25`), as **named vectors in one Qdrant collection**.
4. Cohere `rerank-english-v3.0` (primary, `[0,1]`) and `ms-marco-MiniLM-L-6-v2`
   ONNX (fallback, logits ≈ `[-12,+2]`). **It matters because the scales are
   incompatible, the fallback fires on network flap, and a score without its
   backend is meaningless** — so the confidence thresholds are split per backend
   and `reranker_backend` ships beside the score.
5. `audit_writer_node`, on **every** path — including blocked and refused, which
   reach it on their own edges.

### §10 — Code

6. An **eight-field DSL object** (`GeminiDSLResponse`), validated by
   `DSLValidator`. **Never SQL**, and it never sees the schema.
7. `SQLCompiler` in `engines/dsl_compiler.py`, from **fixed string literals** with
   `%s` placeholders, driven by the validated DSL object. No model output is ever
   interpolated into SQL text.
8. **Qdrant:** inside **each prefetch leg** — `tenant_id` and `is_latest` as
   `must` conditions before any optional filter, so both the dense and the sparse
   candidate sets are filtered *before* fusion. **Postgres:** by **RLS**, driven
   by `SET LOCAL app.tenant_id`, not by a `WHERE` clause in application code.
9. `blocked` (from `prompt_shield`) and `refused` (from `router`, audit F2). Both
   go straight to `audit_writer`, **because the confidence tail would rescore
   something that was never produced** — measured at tier=high @ 0.7095 on a
   refused query.
10. `composeDocumentBody()` in `frontend/app/page.tsx` (ED-024).

### §10 — Why

11. Because LLM arithmetic is **fluent and unverifiable**. The system's claim is
    that a ticked number is verified; a model-computed number could not carry that
    claim. So the model emits a structured object, Python compiles SQL, Postgres
    returns values, and **Python does the arithmetic** — the invariant that also
    makes prompt injection unable to forge a verified figure (Day 42).
12. Because the two rerankers return **incompatible scales**: Cohere
    probabilities in `[0,1]`, local ONNX logits around `[-12,+2]`. One threshold
    pair would be correct for one backend and meaningless for the other. So
    `COHERE_HIGH/MEDIUM = 0.5/0.15` and the local pair `-4.5/-7.5`, selected by
    `reranker_backend`.
13. Because otherwise it enters `confidence_node`, which **scores a refusal** —
    and the measured result was `tier=high @ 0.7095` on a query with no valid
    company, i.e. the system reporting high confidence in a refusal. Refusal is a
    first-class outcome with its own edge, its own audit row and its own tests.
14. Because **this system's stated value is surfacing disagreement rather than
    fabricating certainty.** A false contradiction fabricates disagreement — the
    exact inverse of the claim, in the system's own voice, about a real company. A
    missed one leaves the user where they would be without the feature.
15. Because the semantic cache was never built, `cache_hit` has **no producer**,
    and **deleting the field would delete the record of the debt**. It is marked at
    three layers — the SQL comment, CAVEAT-009, and `lib/api.ts`'s
    `// Do not render` — and the frontend obeys.

### §10 — Debugging

16. **Whether the candidate set was empty or low-scoring** — *"an empty candidate
    set is a network signature; a low-scoring one is a retrieval signature."*
    Establish which before theorising. Then, if empty: `QDRANT_URL` and the
    `Api key is used with an insecure connection` warning. If low-scoring: the
    filter, then the query text.
17. **`reranker_backend`, read from the same response as the score.** Cohere and
    local ONNX are different scales; the fallback fires on network flap, so the
    same query was genuinely scored by two systems.
18. **Extraction, not the query path.** `sql_verified` means the SQL ran and
    returned a value — it guarantees the *pipeline*, not the *number in the
    document*. The tool is `regression_check.py`, and the surrounding obligation is
    `purge_orphaned_metrics --dry-run` after any extraction change.

### §10 — System design

19. **Backend, in dependency order.** `engines/state.py` — any new `QueryState`
    fields. A new `engines/<name>_engine.py`. `engines/graph.py` — register the
    node, add the edge to `confidence`, extend `route_after_router`'s mapping.
    `engines/router.py` — the path enum in `RouterResponse`, **and the prompt
    block that describes when to choose it**. `engines/response_generator.py` — a
    branch, and possibly a reconciliation rule with an **authority** comment at
    both ends (Day 37's lesson). `api/response_shaping.py` if the path exposes new
    fields.
    **Database — the one everyone forgets.** `audit_log.query_path` has a **CHECK
    constraint** listing the legal values, so a fourth path needs a **migration**,
    applied by hand to **both** databases, and verified in `schema_migrations`
    *and* `information_schema`.
    **Frontend.** `lib/api.ts` — the response contract. `page.tsx` —
    `composeDocumentBody`'s branch, **and only that function** (ED-024).
    `ExecutionTrace.tsx` — `ENGINE_NODES` and `PATH_LABELS`, since the engine slot
    is deliberately one slot for mutually-exclusive paths.
    **Evaluation.** A golden category, a `score_result` branch, and questions with
    known outcomes — **structural assertions, not keywords**, per Day 43.
    **Documents.** `IMPLEMENTATION_DELTAS.md` in the **same commit**;
    `ENGINEERING_DECISIONS.md` for the decision; `00_LEARNING_MAP.md`'s graph;
    `LEDGERMIND_ARCHITECTURE.md`.
    **The point of the exercise:** the code change is small and the **blast radius
    is not** — and the migration is the item that is invisible from the code.
20. **The code is the authority. But do not stop there** — the interesting
    question is *why* you believed the other thing. **The procedure:** (1) verify
    the code claim by **observation**, not by reading — a live run, a SQL query, a
    grep with output — because you can misread code the same way twice. (2) Check
    whether a **document** taught you the wrong version; this course has already
    found four (`CLAUDE.md`'s glass/blur invariant, `SECURITY_MODEL.md`'s
    `audit_log` UPDATE claim and its "no DELETE anywhere", and Day 37's
    "unmeasured" cross path). **If a document is wrong, that is a finding, and it
    goes to `CAVEATS.md` — not silently into your head.** (3) If no document
    taught it, the gap is yours and belongs in Part 3. **A disagreement between
    your trace and the code is evidence about one of two things, and you have to
    determine which.**

---

## 12. MUST REMEMBER

```text
- Today's output is a DOCUMENT, and §10 "My gaps" is the most valuable part
  of it
- FOUR PASSES: closed book · mark gaps · check · feed back. PASS 2 IS THE ONE
  PEOPLE SKIP, and the gap list only exists between Pass 1 and Pass 3
- Every arrow carries SIX fields. `why` and `fails` are the two that cannot
  be faked by careful reading
- THREE traces: quantitative · semantic · REFUSAL. The refusal is short and
  is not optional — it is where the edges live
- KEYPRESS TO PIXELS. Starting at POST /api/query is the most common
  incomplete answer
- TEN boundaries, and every trace crosses all ten
- Corrections in Pass 3 must be VISIBLE. A correction you cannot see is a gap
  you will repeat
- Part 3's rows are literally tomorrow's syllabus (Part 4: Day 47 covers
  "whatever Day 46 exposed as thin")
```

## 13. MUST UNDERSTAND

```text
- Why recall and recognition are different, and why closing the book is the
  whole method rather than a formality
- Why "then it retrieves relevant chunks and generates an answer" is six days
  of material compressed into eleven words, and why that compression is
  exactly where the knowledge is not
- Why a trace without failure modes is a diagram
- Why any sentence in which the model writes SQL, does arithmetic, or decides
  sql_verified is a STRUCTURAL error rather than a detail
- Why a disagreement between your trace and the code is evidence about one of
  TWO things — your understanding, or a document — and why you have to
  determine which
- Why the blast radius of a fourth path is much larger than its diff, and why
  the migration is the invisible part
```

---

## 14. This connects to

```text
Days 1–45 — all of it
   ↓
Day 46 — the master trace, from memory
   ↓
Day 47 — failure drills, roads not taken, viva
```

**Day 47 reads your §10.** Part 4 of `LEARNING_PROGRESS.md` says tomorrow covers
*"whatever Day 46 exposed as thin."* So the honesty of the gap list is not a
virtue exercise — **it selects tomorrow's material.**
