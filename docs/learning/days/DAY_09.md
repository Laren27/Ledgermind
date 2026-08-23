# Day 09 — Authorization: Roles, Field Filtering, Failing Closed

**Phase 2 · Weight: M (~90 min) · Prerequisites: Day 8**

---

## 1. Today's goal

By tonight you can:

- State the difference between authentication and authorization in one sentence
  each, and point at the file that does each.
- Explain LedgerMind's **two** enforcement points and why one is not enough.
- Read `role_filtered_response` and explain the **fail-closed** guard — what it
  prevents, and what happens if you delete it.
- Explain why `confidence_tier` is **omitted** on a blocked query while
  `confidence_score` is deliberately left at `0.0` — a distinction that was
  *measured*, not chosen by taste.

---

## 2. Why now

Day 8 established *who you are*. Today is *what you may see*. This closes
Phase 2: after today you can trace a request from `curl` through identity and
permission to a shaped response, and only the pipeline in the middle remains
opaque.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `get_current_user`, `require_role` | Day 8 | The first enforcement point |
| `QueryState` field names | Day 3 | The filter reads them directly |
| SSE `_trace_detail` role branch | Day 6 | A third place the same rule appears |

---

## 4. Concept lesson

### 4.1 Authentication vs authorization

- **Authentication** — *"Who are you?"* Answered by a signature over claims
  (Day 8).
- **Authorization** — *"What are you allowed to do?"* Answered by comparing your
  claims against a policy.

**Mental model.** Authentication is **the ID check at the door**. Authorization
is **which rooms your badge opens once you are inside**.

They are separate because the answers change independently: your identity is
stable, your permissions are not.

---

### 4.2 Two enforcement points, and why one is insufficient

| Point | Question | Granularity | Where |
|---|---|---|---|
| **Route-level** | May you call this endpoint at all? | whole endpoint | `require_role` (Day 8) |
| **Field-level** | Which parts of the answer may you see? | per field | `role_filtered_response` |

**Why route-level alone fails.** Every role may ask a question — a viewer needs
answers as much as an admin does. But the *answer object* contains the compiled
SQL, the DSL, raw reranker scores, latency, token counts and which LLM provider
served it. A viewer should get a correct, cited answer; they should not get the
machinery.

`/api/query` therefore requires only `get_current_user` — **any** authenticated
role. The differentiation happens on the way out.

**Why field-level alone fails.** Uploading a document is not a question of *which
fields*; it is a question of *whether at all*. `POST /api/documents/upload` uses
`Depends(require_role("admin"))` and never reaches a shaping step.

Two questions, two mechanisms.

---

### 4.3 Fail closed

**Fail open:** on an unexpected condition, allow. **Fail closed:** on an
unexpected condition, deny.

Consider a role that is not `viewer`, `analyst` or `admin` — a typo in a seed
migration, a `null`, a role added to the database's `CHECK` constraint but not to
the Python code. Without an explicit guard:

```python
def role_filtered_response(response, role):
    base = {...}
    if role == "viewer":
        return base
    base.update({...analyst fields...})
    if role == "analyst":
        return base
    base.update({...admin fields...})
    return base           # ← falls through here
```

An unknown role **falls through every branch** and receives the **full admin
payload**. The most privileged output, given to the least understood input.

The guard:

```python
_KNOWN_ROLES = frozenset({"viewer", "analyst", "admin"})

# Fail closed. Any role that isn't explicitly recognised -- a typo, a null,
# a future role added to the DB but not here -- gets the most restrictive
# payload, never the least. Without this the function falls through every
# `if` and returns the full admin response to unknown roles.
if role not in _KNOWN_ROLES or role == "viewer":
    return base
```

**Note the shape:** `role not in _KNOWN_ROLES` is checked **first**, with the
same branch as `viewer`. Unknown and least-privileged are treated identically.

**Contrast with Day 8.** `require_role` handles the same situation by raising
`KeyError` → 500. Both fail closed; one is graceful and one is loud. Two layers,
two answers to the same question. Worth noticing — it is the kind of
inconsistency that is harmless until someone "unifies" it in the wrong direction.

---

### 4.4 Omit versus null versus substitute

A field can be absent in three ways, and they are **not** equivalent:

| Form | On the wire | Means |
|---|---|---|
| **Omitted** | key absent | "this does not apply / was not computed" |
| **Null** | `"x": null` | "this applies and has no value" |
| **Substituted** | `"x": "low"` | "this is the value" — a claim |

The Zero UI-Hallucination Mandate (`CLAUDE.md` §6) says:

> Omit rather than substitute.

Today you will see all three used deliberately in one function, with a measured
reason for each.

---

## 5. The actual LedgerMind file

### `backend/app/api/response_shaping.py`

```
File:        backend/app/api/response_shaping.py (177 lines)
Purpose:     Shape the raw QueryState by requester role
Why it exists: The graph must always run in full and the audit log must always
             get the complete record. ONLY the HTTP response is filtered.
Who imports it: api/query.py — both endpoints
What it imports: nothing. Pure dict manipulation.
Entry point: role_filtered_response(response: dict, role: str) -> dict
Data in:     the final QueryState (a dict), and a role string
Data out:    a smaller dict
Boundary:    the last thing that touches an answer before it becomes JSON
```

**The docstring opens with a correction, which tells you something:**

```python
"""
Shapes the raw QueryState dict returned by the graph, based on the
requester's role. NOTE: there is no intermediate QueryResponse model —
api/query.py passes graph.ainvoke()'s final_state directly to this function,
so the keys read below are QueryState keys. An earlier version of this
docstring claimed otherwise and cost a wrong prediction about where a new
field had to be threaded (2026-07-31, llm_model).
...
Field names here must therefore track app/engines/state.py's QueryState
exactly. A key that does not exist there returns None silently — .get() will
not tell you the field was never populated.
"""
```

Two things:

1. **The docstring records its own past error**, and what that error cost. This
   is the same practice as `LEDGERMIND_LEARNING_JOURNAL.md`'s dated correction:
   a document that quietly matches reality stops being a record.
2. **The stated fragility.** `.get("some_typo")` returns `None`, indistinguishable
   from a field that exists and is genuinely `None`. There is no schema binding
   this function to `QueryState`. It is a **coupling maintained by discipline**,
   and the docstring says so rather than pretending otherwise.

---

## 6. Deep code walkthrough

### 6.1 The base payload — what everyone gets

```python
base = {
    "request_id": response["request_id"],
    "query": response["query"],
    "path": response.get("path"),
    "is_blocked": response["is_blocked"],
    "block_reason": response.get("block_reason"),
    "companies": response.get("companies") or [],
    "fiscal_year": response.get("fiscal_year"),
    "quarter": response.get("quarter"),
    "financial_type": response.get("financial_type"),
    "response_text": response.get("response_text"),
    "confidence_tier": response.get("confidence_tier"),
    "citations": _strip_citation_scores(response.get("citations", [])),
    "has_contradictions": bool(response.get("contradictions")),
    "contradictions": _strip_contradiction_values(response.get("contradictions", [])),
    "error": response.get("error"),
}
```

**`response["request_id"]` vs `response.get("path")`.** Direct indexing where the
field is *always* set by `make_initial_state`; `.get()` where a node may not have
run. Not stylistic — the direct index will `KeyError` if the contract breaks,
which is the loud failure you want on a field that must exist.

**`response.get("companies") or []`.** The `or []` handles `None` as well as a
missing key. And note the comment:

```python
# F14: OMITTED, not substituted. A multi-issuer result has no single
# correct value for a scalar "company", and the zero-UI-hallucination
# mandate says omit rather than pick one. `companies` carries the real
# answer; a frontend that wants to show issuers reads that.
```

Before F14 there was a scalar `company`. A two-issuer query had no correct value
for it. The choice was: pick one (a lie), send `null` (ambiguous — is it "no
issuer" or "several"?), or **remove the field**. F14 removed it.

**`_strip_citation_scores` — the viewer's citations:**

```python
# chunk_id is included deliberately: it carries no information (opaque UUID)
# but the frontend needs it as a stable DOM anchor id to tie inline
# superscripts to their numbered footnotes. Scores stay stripped.
_VIEWER_CITATION_FIELDS = {"chunk_id", "doc_id", "page_number", "company",
                           "fiscal_year", "financial_type"}
```

A viewer sees **where** a claim came from — company, year, page — and not
`reranker_score`. The comment justifies the one field that looks like a leak:
`chunk_id` is an opaque UUID carrying no information, needed as a DOM anchor.

**Why strip the score at all?** Because a bare `0.0165` is meaningless without
its scale, and the scale depends on which backend scored it (Day 28). Showing a
number the viewer cannot interpret is worse than showing none.

**`_strip_contradiction_values`:**

```python
# Viewer sees that a contradiction exists and its severity, not the
# underlying numbers/claims that produced it.
return [{"type": c.get("type"), "severity": c.get("severity")} for c in contradictions]
```

**`has_contradictions` as a separate boolean.** Redundant with a non-empty list —
until you notice a viewer's list is stripped to two fields. The boolean is an
unambiguous, role-independent signal.

---

### 6.2 The measured decision: omit `confidence_tier` on a block

```python
if response["is_blocked"]:
    base.pop("confidence_tier", None)
```

One line, and the comment above it is thirty. Read it in full, because it is the
best worked example in the codebase of *how a decision gets made here*:

```python
# OMITTED, NOT SUBSTITUTED -- the same rule `companies` follows six lines
# up, applied to the one field that was still asserting an unmeasured value.
#
# A Prompt Shield block goes prompt_shield -> audit_writer directly
# (graph.py's "blocked" edge), so confidence_node NEVER RUNS and nothing
# ever writes a tier. What reached the client was make_initial_state's
# default "low" -- indistinguishable on the wire from a tier that was
# computed and came out low. Confirmed live against Render 2026-08-22:
# a blocked query returned confidence_tier="low", confidence_score=0.0.
#
# OMITTED rather than nulled, and that is a MEASURED choice, not a taste
# one. scripts/eval_runner.py's out_of_corpus scorer reads the tier
# through `.get("confidence_tier", "low")` inside a PASS condition, so an
# absent key scores exactly as today while an explicit null flips that
# verdict from pass to fail. Verified by running score_result over all
# twelve golden categories with the field set three ways: absent and
# "low" agree everywhere; null diverges on out_of_corpus.
#
# `confidence_score` is deliberately LEFT AT 0.0. It is a stored
# audit_log column and metrics.py aggregates over it (refusal_rate_pct,
# confidence_distribution); making it null would retroactively change
# what those aggregates mean for every blocked row ever written. That is
# a stored-data change, not a response change, and belongs in its own
# decision.
```

**Three separate judgements, each with its own justification:**

1. **The defect.** A default reported as a measurement. The client could not tell
   "we scored this and it was low" from "we never scored this".
2. **Omit, not null — because it was measured.** Not "omitting feels cleaner".
   Someone ran the eval scorer across twelve categories with the field set three
   ways and found that `null` changes a verdict while absent does not.
3. **`confidence_score` stays `0.0` — because the blast radius is different.**
   It is a *stored column* that `api/metrics.py` aggregates over. Nulling it
   would retroactively change what `refusal_rate_pct` and
   `confidence_distribution` mean for every blocked row ever written. That is a
   **stored-data** change wearing the costume of a response change, and it was
   correctly refused as out of scope.

**This is the reasoning standard.** Not "is this cleaner?" but *"what does each
option change, for whom, and has anyone checked?"*

---

### 6.3 The fail-closed guard

```python
if role not in _KNOWN_ROLES or role == "viewer":
    return base
```

**STATE BEFORE.** `base` holds the safe fields.
**STATE AFTER.** For a viewer or an unknown role, that is the entire response.

Delete this line and an unknown role falls through to the admin block. **A typo
in a seed migration becomes a privilege escalation.**

---

### 6.4 Analyst — the machinery

```python
base.update({
    "confidence_score": response.get("confidence_score"),
    "crag_triggered": response.get("crag_triggered"),
    "crag_count": response.get("crag_count"),
    "citations": response.get("citations", []),            # full, with reranker_score
    "contradictions": response.get("contradictions", []),  # full detail
    "dsl_object": response.get("dsl_object"),
    "sql_query": response.get("sql_query"),
    "sql_result": response.get("sql_result"),
    "sql_verified": response.get("sql_verified"),
    "error_node": response.get("error_node"),
})

if role == "analyst":
    return base
```

Note `citations` and `contradictions` are **overwritten** with the unstripped
versions rather than merged. `.update()` replaces.

An analyst can now **check the work**: read the compiled SQL, see the DSL, see
raw scores, see which node failed. That is the difference between "here is an
answer" and "here is an answer you can audit".

---

### 6.5 Admin — operational truth

```python
base.update({
    "latency_ms": ..., "tokens_used": ..., "cache_hit": ...,
    "llm_provider": ...,
    "llm_model": ...,
    "reranker_backend": _reranker_backend(response),
})
```

Each has a comment. The `reranker_backend` one is the longest in the file and is
worth reading now, because it previews Day 28:

```python
# WHICH RERANKER SCORED THE CITATIONS. Admin-tier, same reasoning as
# llm_provider: an operational fact that must be visible SOMEWHERE
# because it changes what the numbers beside it MEAN.
#
# citations carry reranker_score with no unit. Cohere returns 0-1;
# the local ONNX cross-encoder returns raw logits (~-12 to +2). ...
#
# This is not hypothetical. ... on 2026-08-02 that fallback fired
# mid-session from WSL2 network flap (raw socket connects to
# api.cohere.com succeeded 5 of 8 attempts, failing at random). The
# same query returned tier=medium on one run and tier=high on another
# purely because a different backend scored it. Reading -3.39 as a
# Cohere score rather than an ONNX logit then produced a wrong
# conclusion about threshold calibration that reached this repo's
# documentation before it was caught.
```

**A wrong conclusion reached the documentation.** That is why this field is on
the wire.

And the derivation:

```python
def _reranker_backend(response: dict):
    """Backend that scored the citations, or None if nothing was reranked.

    One rerank call per query, so one backend for the whole set -- this is a
    response-level fact, not a per-citation one, and attaching it to each
    citation would imply a variability that does not exist.
    """
    chunks = response.get("retrieved_chunks") or []
    if not chunks:
        return None
    return chunks[0].get("reranker_backend")
```

Two more decisions in seven lines:

- **Derived, not recomputed.** `retriever.py` tags every chunk at the point of
  scoring. Recomputing here would be a second copy of a fact — the failure class
  behind the three metric registries.
- **`None` when nothing was reranked, and deliberately *not* defaulting to
  `"local"`.** `_score_confidence` defaults to `"local"` as a *safety* choice
  (assume the stricter scale when unsure). Here it would be an **observation**,
  and the comment says: *"reporting an assumption as an observation is how this
  went wrong in the first place."*

**The same value, two defaults, for two different reasons.** That distinction —
safety default versus observational default — is worth carrying forward.

---

## 7. Data flow

```
final QueryState  (~40 keys, everything the pipeline learned)
        │
        ▼
role_filtered_response(state, role)
        │
        ├── base: 15 fields, safe for everyone
        │     ├─ citations  → _strip_citation_scores  (6 fields, no score)
        │     └─ contradictions → _strip_contradiction_values (type + severity)
        │
        ├── if is_blocked → pop("confidence_tier")     ← measured decision
        │
        ├── role unknown OR viewer ──────────────────► return base    (15 fields)
        │
        ├── + analyst fields (10) ───► if analyst ───► return base    (25 fields)
        │
        └── + admin fields (6) ──────────────────────► return base    (31 fields)
                                                          │
                                                          ▼
                                                    JSON to the client

MEANWHILE, unfiltered:
   audit_log  ← the COMPLETE record, every role, every query, always
```

**The invariant to memorise:** the graph always runs in full and the audit log
always receives everything. **Only the HTTP response is filtered.** Filtering is
a presentation decision, never a data-collection one.

---

## 8. Engineering decision — filter the response, not the pipeline

**Problem.** Three roles need different amounts of the same answer.

**Decision.** Run the pipeline identically for everyone; filter once, at the
boundary, in a pure function.

| Alternative | Why not |
|---|---|
| **Run different pipelines per role** | A viewer's answer would be computed differently from an analyst's, so they could **disagree**. Fatal for a system whose claim is that answers are checkable |
| **Filter in the frontend** | The data is already on the wire. Trivially bypassed with `curl` |
| **Per-field permissions in the DB** | Enormous machinery for three roles and one response shape |
| **Separate response models per role** | Three Pydantic models drifting from `QueryState`; three places to update per new field |

**Trade-offs accepted.**

- **The coupling is by discipline, not by type.** A typo returns `None` silently
  (the docstring says so).
- **The role rule exists in three places:** here, `require_role`, and
  `_trace_detail` in `api/query.py`. Three copies of one policy — the exact
  failure class this project consolidated for metrics. Nobody has consolidated
  it here yet, and it is not in `CAVEAT`s.
- **Some computation is wasted** for viewers. Negligible next to an LLM call.

**Current validity.** Sound. The drift risk is real but small at three roles.

**At 10×.** With more roles or per-tenant policies, this becomes a table lookup
rather than a chain of `if`s. The bigger question is whether the audit log should
still receive everything for every role — currently yes, and it should stay that
way, because the audit trail's value is that it is unconditional.

---

## 9. Failure modes

| Symptom | Cause | Note |
|---|---|---|
| A viewer sees SQL | Fail-closed guard removed or reordered | The guard is the whole defence |
| A field is always `None` | Key name does not match `QueryState` | `.get()` will not tell you |
| An eval verdict flips after a "cleanup" | `confidence_tier` set to `null` instead of omitted | Measured across twelve categories |
| `refusal_rate_pct` changes meaning | Someone nulled `confidence_score` | Stored-data change in disguise |
| 500 on a valid token | Unknown role hits `require_role`'s `ROLE_RANK` | Day 8 — fails closed, loudly |
| A score misread by a factor of ten | `reranker_backend` ignored | The reason the field ships |
| Trace detail and response disagree on role | `_trace_detail`'s rule drifted | Three copies of one policy |

---

## 10. Hands-on experiment

Get three tokens:

```bash
mk() { curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$1@alpha.ledgermind.test\",\"password\":\"<password>\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"; }
VIEWER=$(mk viewer); ANALYST=$(mk analyst); ADMIN=$(mk admin)
for t in "$VIEWER" "$ANALYST" "$ADMIN"; do echo ${#t}; done
```

### Experiment 1 — count the fields

```bash
ask() { curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $1" -H "Content-Type: application/json" \
  -d '{"query":"What was Eternal revenue in FY26?"}'; }

for r in VIEWER ANALYST ADMIN; do
  eval "T=\$$r"
  echo -n "$r: "
  ask "$T" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d),'fields ->', sorted(d))"
  echo
done
```

15 → 25 → 31. Read the *added* keys at each step and connect each to a reason.

### Experiment 2 — the same citation, two ways

```bash
ask "$VIEWER"  | python3 -c "import sys,json; print('VIEWER :', json.load(sys.stdin)['citations'][0])"
ask "$ANALYST" | python3 -c "import sys,json; print('ANALYST:', json.load(sys.stdin)['citations'][0])"
```

Same citation. The analyst's has `reranker_score`, `filing_date` and
`text_preview`; the viewer's has six fields and no score.

### Experiment 3 — the omitted tier

```bash
blocked() { curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $1" -H "Content-Type: application/json" \
  -d '{"query":"Should I buy Zomato?"}'; }

blocked "$ADMIN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('is_blocked        :', d['is_blocked'])
print('confidence_tier   :', 'ABSENT' if 'confidence_tier' not in d else repr(d['confidence_tier']))
print('confidence_score  :', d.get('confidence_score'), '<- deliberately still 0.0')
"
```

The tier is **gone**; the score is **present and zero**. Two fields, two
decisions, both documented.

### Experiment 4 — the fail-closed guard, proven

```bash
docker compose exec -T backend python -c "
from app.api.response_shaping import role_filtered_response
state = {'request_id':'r','query':'q','is_blocked':False,'companies':['ETERNAL'],
         'sql_query':'SELECT secret FROM financials','llm_provider':'gemini',
         'latency_ms': 1234}
for role in ('viewer','analyst','admin','superuser','', None):
    out = role_filtered_response(dict(state), role)
    print(f'{str(role):10} -> {len(out):2d} fields   sql_query={\"sql_query\" in out}')
"
```

`superuser`, `''` and `None` all get **15 fields and no SQL** — the viewer
payload. Now mentally delete the `role not in _KNOWN_ROLES` clause and rerun the
logic in your head: all three fall through to admin.

### Experiment 5 — `reranker_backend` in the wild

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"query":"What risks does Eternal disclose in Q4 FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('reranker_backend:', d.get('reranker_backend'))
print('top score       :', d['citations'][0].get('reranker_score'))
print()
print('Read the score ONLY in light of the backend:')
print('  cohere -> [0,1] relevance    thresholds 0.5 / 0.15')
print('  local  -> logits ~[-12,+2]   thresholds -4.5 / -7.5')
"
```

Now run the same query on a **quantitative** question and watch
`reranker_backend` come back `None` — nothing was reranked, and reporting
`"local"` would be an assumption dressed as an observation.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/api/response_shaping.py`:

1. Which fields does an **analyst** get that a viewer does not? Group them into
   "check the retrieval" and "check the computation".
2. Why is `chunk_id` in the viewer's citation fields when it carries no
   information?
3. `confidence_tier` is popped on a block; `confidence_score` is not. State both
   reasons — they are different in kind.
4. `_reranker_backend` returns `None` when nothing was reranked, while
   `_score_confidence` defaults to `"local"`. Both are about the same value. Why
   the difference?
5. Find the docstring's warning about `.get()`. What class of bug does it
   describe, and why can no test catch it easily?

---

## 12. Self-check questions

**Basic**
1. Authentication vs authorization, one sentence each.
2. What are the two enforcement points here?
3. What does "fail closed" mean?
4. Which role sees `sql_query`?
5. What does a viewer's citation contain?

**Code**
6. What does `role_filtered_response` return for an unrecognised role?
7. Why `response["request_id"]` but `response.get("path")`?
8. What does `_strip_contradiction_values` keep?
9. How is `reranker_backend` derived?
10. Which endpoints use `require_role`, and at what minimum?

**Why**
11. Why is the graph run in full even for a viewer?
12. Why is `confidence_tier` omitted rather than nulled on a block?
13. Why is `confidence_score` left at `0.0` when the tier was removed?
14. Why does `reranker_backend` ship to admins at all?
15. Why does `has_contradictions` exist alongside the list?

**Debugging**
16. A field reads `None` for every role. Where do you look first?
17. An eval category flips from pass to fail after a response-shaping change that
    "only cleaned up nulls". What happened?
18. An admin reports `reranker_score: -3.39` and concludes the thresholds are
    wrong. What did they forget, and where would you have caught it?

**System design**
19. Add a `"compliance"` role that sees citations and the audit trail but not
    SQL. What changes, and what is the risk in the current design?
20. The role rule exists in three places. Propose a consolidation and name what
    it would cost.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **Check the retrieval:** `citations` (unstripped, with `reranker_score`),
   `contradictions` (full detail), `crag_triggered`, `crag_count`,
   `confidence_score`. **Check the computation:** `dsl_object`, `sql_query`,
   `sql_result`, `sql_verified`, `error_node`. Together they let an analyst
   reconstruct *how* the answer was produced on either path.
2. Because the frontend needs a **stable DOM anchor id** to tie an inline
   superscript to its numbered footnote. It is an opaque UUID carrying no
   information — the comment says exactly this, and then adds "Scores stay
   stripped", so the exception is bounded and justified.
3. **Tier:** it was never computed — `confidence_node` never runs on the blocked
   edge — so reporting the default `"low"` asserts an unmeasured value.
   **Score:** it is a *stored audit_log column* that `api/metrics.py` aggregates
   over (`refusal_rate_pct`, `confidence_distribution`); nulling it would
   retroactively change what those aggregates mean for every blocked row ever
   written. One is a response-shape change; the other is a stored-data change,
   and only the first was in scope.
4. Because they answer different questions. `_score_confidence` needs a **safety
   default**: when unsure which scale a score is on, assume the stricter one, so
   an unknown backend cannot inflate confidence. `_reranker_backend` is making an
   **observation** to report to a human — and, as the comment says, "reporting an
   assumption as an observation is how this went wrong in the first place."
5. `.get("typo")` returns `None`, **indistinguishable** from a field that exists
   and is genuinely `None`. A test is hard because the correct value for most of
   these fields on most queries *is* `None` — you would need a fixture where
   every field is non-null, which means a query exercising all three paths at
   once, which does not exist. The real fix is a schema binding this function to
   `QueryState`, which does not exist either.

### §12 — Basic

1. **Authentication:** who are you? **Authorization:** what are you allowed to
   do?
2. Route-level (`require_role`) and field-level (`role_filtered_response`).
3. On an unexpected condition, **deny** rather than allow.
4. Analyst and admin.
5. `chunk_id`, `doc_id`, `page_number`, `company`, `fiscal_year`,
   `financial_type` — six fields, no scores, no `text_preview`.

### §12 — Code

6. The **viewer** payload — the most restrictive. `role not in _KNOWN_ROLES` is
   checked in the same branch as `role == "viewer"`.
7. Direct indexing where `make_initial_state` guarantees the key exists, so a
   broken contract fails **loudly** with a `KeyError`. `.get()` where a node may
   legitimately not have run.
8. `type` and `severity` only — that a contradiction exists and how serious, not
   the numbers or claims that produced it.
9. `response["retrieved_chunks"][0]["reranker_backend"]` — read from the first
   chunk, because there is **one rerank call per query** so one backend for the
   whole set. `None` if no chunks.
10. `POST /api/documents/upload` and `GET /api/documents/pending` at `admin`;
    `GET /api/metrics` at `analyst`. `/api/query` and `/api/query/stream` require
    only authentication.

### §12 — Why

11. So that a viewer's answer and an analyst's answer to the same question are
    **the same answer**. Different pipelines could disagree, which is fatal for a
    system whose claim is that answers are checkable. It also means the audit log
    receives the complete record regardless of who asked.
12. Because it was **measured**. `eval_runner`'s `out_of_corpus` scorer reads the
    tier through `.get("confidence_tier", "low")` inside a PASS condition — an
    absent key behaves exactly as the old default, while an explicit `null` flips
    the verdict from pass to fail. Verified across all twelve golden categories
    with the field set three ways.
13. Because it is a stored column that metrics aggregate over, so changing it is
    a **stored-data** change with retroactive effect on every blocked row ever
    written — a different decision with a different blast radius, correctly
    deferred.
14. Because it **changes what the number beside it means**. `reranker_score`
    carries no unit; Cohere returns `[0,1]` and local ONNX returns logits around
    `[-12,+2]`. On 2026-08-02 the fallback fired mid-session from WSL2 network
    flap and the same query returned different tiers on different runs — and
    reading an ONNX logit as a Cohere score produced a wrong conclusion about
    threshold calibration **that reached this repository's documentation**.
15. Because a viewer's `contradictions` list is stripped to two fields, so
    "is the list non-empty and meaningful?" is not a question the client should
    have to reason about. The boolean is unambiguous and role-independent.

### §12 — Debugging

16. The **key name**. `role_filtered_response` reads `QueryState` keys directly
    with `.get()`, and a name that does not exist returns `None` silently. Check
    the spelling against `engines/state.py` — the docstring warns about exactly
    this and records that a previous version of the docstring itself caused a
    wrong prediction about where `llm_model` had to be threaded.
17. Someone changed an **omitted** field to an explicit `null`. The eval scorer's
    `.get("confidence_tier", "low")` fallback only fires when the key is
    **absent**; an explicit `null` is a present value, so the default never
    applies and the comparison changes. This is precisely the case the thirty-line
    comment was written to prevent.
18. They forgot **`reranker_backend`**. `-3.39` is a plausible ONNX logit and an
    impossible Cohere relevance score, so the interpretation depends entirely on
    which backend served that query. It would have been caught by reading
    `reranker_backend` from the same response — which is admin-tier for exactly
    this reason. `scripts/cohere_score_dump.py` has a hard abort for the same
    mistake; the query response previously had nothing.

### §12 — System design

19. Add `"compliance"` to `_KNOWN_ROLES`; add it to `ROLE_RANK` in
    `dependencies.py` (or the rank model breaks, since compliance is not simply
    "above analyst"); add a branch in `role_filtered_response` between viewer and
    analyst; update `_trace_detail`'s role check in `api/query.py`; and add the
    value to the `users.role` `CHECK` constraint **via a migration**. **The risk:**
    a strict hierarchy cannot express "sees citations but not SQL" — that is not
    a rank, it is a *set of permissions*. Forcing it into `ROLE_RANK` either
    grants too much or too little, and the honest fix is to replace the rank with
    a capability set, which is a larger change than it first appears.
20. Define the policy once as data — `{role: set_of_field_names}` — and have
    `role_filtered_response`, `require_role` and `_trace_detail` all read it.
    **Costs:** an indirection that makes the shaping function harder to read at a
    glance; a data structure that must itself be tested; and a migration risk,
    since `require_role` currently answers "may you call this at all" while the
    others answer "what may you see" — genuinely different questions that a
    single table might conflate. The consolidation is worth doing when a fourth
    role appears, and not before.

---

## 14. MUST REMEMBER

```text
- Authentication: who are you?  Authorization: what may you do?
- TWO enforcement points: require_role (route) + role_filtered_response (field)
- FAIL CLOSED: unknown role gets the VIEWER payload, never admin
- viewer 15 fields · analyst 25 · admin 31
- The graph always runs in full; the audit log always gets everything.
  ONLY the response is filtered
- confidence_tier is OMITTED on a block (measured); confidence_score stays 0.0
- reranker_backend ships to admins because it changes what the score MEANS
- Omit > null > substitute
```

## 15. MUST UNDERSTAND

```text
- Why filtering the RESPONSE beats running different pipelines: two pipelines
  can disagree, and disagreement is fatal to a checkable system
- Why "omit vs null" was settled by running a scorer, not by taste
- Why a response-shape change and a stored-data change have different blast
  radii even when they touch the same field
- The difference between a SAFETY default and an OBSERVATIONAL default —
  the same value, two correct answers, for two different reasons
- Why the role rule living in three places is a real, unrecorded drift risk
```

---

## 16. This connects to

```text
Day 7 — issuing identity
Day 8 — verifying it
   ↓
Day 9 — what it may see                      ← END OF PHASE 2
   ↓
Day 10 — the Python idioms this codebase actually uses
```

Forward references:

- `reranker_score`, `reranker_backend`, the two threshold pairs → **Day 28**
- `llm_provider` / `llm_model` → **Day 19**
- `dsl_object`, `sql_query`, `sql_verified` → **Days 32–34**
- `cache_hit` — always `false`, no producer → **Day 44**
- `_trace_detail`'s third copy of the role rule → **Day 39**
- Tenant isolation and the full threat model → **Days 14 and 42**
