# Day 05 — The Contract: JSON, Pydantic, Status Codes, CORS

**Phase 1 · Weight: M (~90 min) · Prerequisites: Day 4**

---

## 1. Today's goal

By tonight you can:

- Explain what JSON is, what it can and cannot represent, and why that shapes
  every boundary in this system.
- Read a Pydantic model as an **executable contract** and predict exactly what a
  malformed request produces.
- Explain the difference between *validated* and *correct* — the single most
  important idea in this day, and the reason three regex guards exist in
  `quant_engine.py`.
- Find the one field in `QueryRequest` that should not be there, and explain
  why it is `CAVEAT-001`.

---

## 2. Why now

Day 4 got a request to the door. Today is what the request must *look like* to
be let in. This is also the first day you meet Pydantic, which reappears in a
completely different role on Day 18 — as the schema handed to an LLM — and that
second role is impossible to understand without the first.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Request body, headers, status codes | Day 4 | The contract is about the body |
| Dependencies run before handlers | Day 4 | Explains 401-before-422 ordering |
| `QueryState` | Day 3 | `QueryRequest` becomes one |

---

## 4. Concept lesson

### 4.1 JSON, and what it costs you

**What it is.** A text format with six types: object (`{}`), array (`[]`),
string, number, boolean, null.

**What problem it solves.** Two programs in different languages need to exchange
structured data. Before JSON: XML (verbose, needs a parser and a schema
language), or bespoke binary formats (fast, unreadable, and unusable from a
browser).

**What JSON cannot represent** — and every one of these bites this codebase:

| Missing | Consequence here |
|---|---|
| **Dates** | `filing_date` travels as `"2026-05-15"`, a *string*. Something must parse it |
| **Decimals** | JSON numbers are IEEE floats. `financials.value` is `NUMERIC` in Postgres — exact — and `audit_writer._safe_json` uses `default=str` to avoid silently degrading a Decimal |
| **Sets** | Everything is an array; uniqueness must be enforced in code |
| **Comments** | Which is why `.env.example` exists as a commented file and `.env` is not JSON |
| **Trailing commas** | A common source of 422s when hand-writing a `curl` body |

**Mental model.** JSON is a **postcard**. Everything must be written in
characters both sides can read, so anything richer than six types has to be
*encoded* into one of them — and decoded on arrival.

---

### 4.2 Serialisation and its two failure points

```
Python object  ──serialise──►  JSON text  ──parse──►  Python object
   (rich)                       (6 types)              (rich again?)
```

Two places to lose information:

1. **Serialising** a type JSON has no equivalent for. A `Decimal("54364.00")`
   becomes `54364.0` — a float — unless you intervene.
2. **Parsing**, where `"54364.00"` is just a string until something says
   otherwise.

`audit_writer.py` shows the intervention:

```python
def _safe_json(value):
    """psycopg2 JSONB adapter needs plain JSON-serialisable values.
    Decimal types from SQL results need explicit float conversion."""
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None
```

`default=str` says: anything you cannot serialise, stringify. A `Decimal`
survives as `"54364.00"` rather than being silently rounded — and, importantly,
the audit row records what the system *actually computed*.

---

### 4.3 Pydantic — the contract as code

**The problem.** Data arriving over a network is untrusted. Validating by hand
is verbose, inconsistent between endpoints, and always incomplete.

**What existed before.** Hand-written checks:

```python
if "query" not in body: return 400, "missing query"
if not isinstance(body["query"], str): return 400, "query must be a string"
```

Written per endpoint, drifting immediately.

**What Pydantic does.**

```python
class QueryRequest(BaseModel):
    query: str
    tenant_id: Optional[str] = None
    execution_context: Optional[Dict[str, Any]] = None
```

Three lines that mean: `query` is **required** and must be a string;
`tenant_id` is optional and defaults to `None`; `execution_context` is an
optional dictionary of anything. FastAPI enforces it and returns **422** with
the offending field named, before your handler runs.

**Mental model.** A Pydantic model is **a form with required fields**. FastAPI is
the clerk who refuses the form and points at the empty box.

---

### 4.4 The idea that matters most today

> **Validated is not correct.**

Pydantic guarantees `query` is a string. It cannot tell you the string is a
sensible question, that the `tenant_id` belongs to you, or that
`execution_context` contains anything meaningful.

This distinction runs through the whole system:

| Layer | Guarantees | Does **not** guarantee |
|---|---|---|
| Pydantic on the HTTP body | shape | meaning |
| Gemini `response_schema` | shape of the DSL | the *right* metric |
| SQL row-count verification | one row returned | the right period |

That middle row is why `quant_engine.py` has **three regex guards running over
the raw query before any LLM call**. A perfectly valid DSL object naming a metric
the user never mentioned passes every schema check in the system. Shape
validation cannot catch it. You will see this properly on **Day 34** — today,
just plant the idea.

---

### 4.5 Status codes for validation

| Code | When | Who decides |
|---|---|---|
| **400** | Body is not parseable as JSON at all | The framework |
| **422** | Valid JSON, wrong shape | Pydantic |
| **401** | No/invalid token | Your dependency — **runs first** |
| **413** | Payload too large | Your handler (`documents.py`, 50 MB) |

**The ordering is observable.** Send a malformed body *without* a token and you
get **401**, not 422 — because dependencies run before body validation. That is
a security property: an unauthenticated caller learns nothing about your schema.

---

## 5. The actual LedgerMind files

### `backend/app/api/query.py` — the request model

```python
class QueryRequest(BaseModel):
    query: str
    tenant_id: Optional[str] = None
    execution_context: Optional[Dict[str, Any]] = None
```

```
File:        backend/app/api/query.py (233 lines) — today, only the top
Purpose:     The two query endpoints and their request contract
Data in:     JSON body + Authorization header
Data out:    a role-filtered JSON response (Day 9), or an SSE stream (Day 6)
```

**Field by field.**

**`query: str`** — required, no default. The only genuinely required field.

**`tenant_id: Optional[str] = None`** — **this is `CAVEAT-001`.** In the handler:

```python
tenant_id = payload.tenant_id or current_user.get("tenant_id", "default")
```

The value from the **request body** is preferred over the value in the
**verified JWT**. That `tenant_id` then flows into `QueryState["tenant_id"]` →
`SET LOCAL app.tenant_id` (the RLS scope, Day 14) → the Qdrant filter (Day 27) →
the audit row (Day 44).

So an authenticated user of tenant A can post `{"query": "...", "tenant_id":
"<tenant-B-uuid>"}` and **every defence works exactly as designed** — while being
told the wrong tenant by the layer above them.

Read the caveat's own framing:

> Every defence works exactly as designed — they are all being told the wrong
> tenant by the layer above them.

With one seeded tenant this is unexploitable in practice. The moment a second
tenant holds data it is a full cross-tenant read. It is **Critical** as a
multi-tenant product, Low as a single-tenant demo, and it is recorded rather
than quietly fixed because fixing it is a functional change.

Note also that `db/session.py`'s docstring asserts the opposite as an
assumption:

> As long as `state["tenant_id"]` is sourced from the verified JWT (see
> api/query.py), those per-call connections are already RLS-correct.

**That assumption is what `api/query.py` breaks.** A docstring and the code it
describes disagreeing, in a security-relevant place, is exactly the drift class
Day 2 taught you to look for.

**`execution_context: Optional[Dict[str, Any]] = None`** — a deliberately
untyped escape hatch. The frontend's peer-comparison view sends
`{"enforce_path": true, "intended_path": "quantitative", "intended_operation":
"growth_comparison"}`. `Dict[str, Any]` means Pydantic validates *nothing* about
its contents.

The consequence is `CAVEAT-002`: `intended_operation` is written into
`state["preferred_operation"]` and **never read by anything**. `validate_dsl` has
a `preferred_operation` parameter, commented "Load-Bearing Guardrail", and the
single call site passes no second argument. The peer view still usually works —
because the *prompt* has a rule for it. A deterministic guardrail is absent and a
probabilistic one is doing its job, which is the inverse of this project's stated
preference.

---

### `backend/app/auth/schemas.py` — a contract in both directions

```python
class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: str
    expires_in_hours: int = 2

class TokenPayload(BaseModel):
    sub: str          # user_id
    tenant_id: str
    role: str
```

**`LoginRequest`** validates input. **`TokenResponse`** is declared as
`response_model=TokenResponse` on the route, so FastAPI validates the *output*
too — a handler returning the wrong shape fails at the boundary rather than
sending a malformed body.

**`TokenPayload`** is documentation. Nothing constructs it;
`core/security.py` builds the JWT payload as a plain dict. It exists so a reader
can see the claim shape in one place. Harmless — but worth noticing that a
declared model with no producer is the same *shape* of thing as
`preferred_operation` with no consumer.

**`email: str`, not `EmailStr`.** Pydantic offers `EmailStr` and it is not used.
Consequence: `"notanemail"` passes validation and fails at the database lookup
with a 401. Since a bad email must produce a 401 anyway — you must never reveal
whether an address exists — the extra validation would buy little. Defensible;
just know it is a choice and not an oversight.

---

## 6. Deep walkthrough — a body's journey

**STATE BEFORE.** Raw bytes on a socket.

```
b'{"query": "What was Eternal FY26 revenue?"}'
```

**Step 1 — routing.** `(POST, /api/query)` matches. No match → 404; wrong method
→ 405. Handler not called.

**Step 2 — dependencies.** `Depends(get_current_user)` runs. Missing or invalid
token → **401**, and *the body is never examined*.

**Step 3 — parse.** Bytes → `dict`. Malformed JSON → **400**.

**Step 4 — validate.** `dict` → `QueryRequest`. Missing `query` → **422**:

```json
{"detail": [{"type": "missing", "loc": ["body", "query"],
             "msg": "Field required"}]}
```

`loc` is the path to the offending field. That is the part worth reading.

**Step 5 — coerce.** Absent optionals become `None`. `payload` is now a typed
Python object with three attributes.

**STATE AFTER (inside the handler):**

```python
payload.query             # "What was Eternal FY26 revenue?"
payload.tenant_id         # None
payload.execution_context # None
current_user              # {"user_id": "...", "tenant_id": "...", "role": "..."}
```

**Step 6 — the transformation that matters.**

```python
request_id = str(uuid.uuid4())
tenant_id = payload.tenant_id or current_user.get("tenant_id", "default")
user_id = str(current_user["user_id"])

initial_state = make_initial_state(
    query=payload.query, tenant_id=tenant_id, user_id=user_id,
    request_id=request_id, execution_context=payload.execution_context,
)
```

Three lines of interest:

- `request_id` is minted **here**, not by the client. It flows into every log
  line and the audit row, so a client cannot forge or collide one.
- `tenant_id` — the caveat, in one line.
- `user_id` — read only from `current_user`, never from the body. Note that
  `tenant_id` had that property once too; the body override is the anomaly.

**What breaks if `QueryRequest` were a plain dict?** Nothing immediately —
`payload["query"]` works. But: no 422 (a missing key becomes a `KeyError` → 500),
no `/docs` schema, and no single place stating what a request looks like. You
would be back to hand-written checks, drifting per endpoint.

---

## 7. Data flow across boundaries

```
browser JS object     {query: "..."}
   │  JSON.stringify
   ▼
JSON text             '{"query":"..."}'
   │  network
   ▼
Python dict           {"query": "..."}
   │  Pydantic
   ▼
QueryRequest          payload.query
   │  make_initial_state
   ▼
QueryState (dict)     {"query": "...", "tenant_id": "...", ...}
   │  the whole graph
   ▼
QueryState (populated)
   │  role_filtered_response          (Day 9)
   ▼
dict → JSON → browser
```

**Six representations of the same request.** Each boundary is a chance to lose
or corrupt something, which is why each one has an explicit shape.

---

## 8. Engineering decision — typed contract at the boundary

**Problem.** Untrusted input must not reach business logic unshaped, and every
endpoint must fail the same way.

**Decision.** Pydantic models on request bodies, `response_model` where the shape
is fixed.

| Alternative | Why not |
|---|---|
| Manual `if` checks | Drifts per endpoint; no generated docs; inconsistent error shape |
| JSON Schema files | Same guarantees, another artefact to keep in sync with Python types |
| Trust the client | The frontend is not the only client — `eval_runner.py` and `curl` both call this API |
| Validate deep in the pipeline | The further from the boundary, the more code has already run on bad data |

**Trade-offs accepted.** A dependency, and an error format you do not control
(nested `detail` arrays are not the friendliest). Both cheap.

**Where the contract is deliberately *not* enforced:** `execution_context:
Dict[str, Any]`. Flexibility at the cost of `CAVEAT-002` — an unvalidated field
whose consumer was never wired, and nothing detected it.

**At 10× / multi-tenant.** `tenant_id` must be removed from `QueryRequest`
(`CAVEAT-001`). If an override is genuinely needed for evals, it should be gated
behind `require_role("admin")` **and** an environment flag, **and recorded in the
audit row** — so an overridden query is never indistinguishable from a normal one.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| 422 with `loc: ["body","query"]` | `query` missing or wrong type |
| 400 | Body is not JSON — usually a trailing comma or unquoted key |
| 401 on a body you know is bad | Dependencies run **first**; you never reached validation |
| 500 mentioning `KeyError` | Something read a dict key without validation |
| A Decimal arrives as a rounded float | Serialised without `default=str` |
| "blocked by CORS policy" | The response arrived; the browser withheld it (Day 4) |
| `execution_context` ignored | `CAVEAT-002` — `preferred_operation` has no consumer |
| An answer scoped to the wrong tenant | `CAVEAT-001` — body overrode the JWT |

---

## 10. Hands-on experiment

Get a token first:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@alpha.ledgermind.test","password":"<password>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo ${#TOKEN}
```

`echo ${#TOKEN}` is from `CLAUDE.md` §6 — **so an empty token fails loudly**
rather than producing a confusing 401 later.

### Experiment 1 — a 422, read properly

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"quer": "typo in the field name"}' | python3 -m json.tool
```

Read `detail[0].loc`. It is a **path**: `["body", "query"]`. For a nested model it
would be `["body", "outer", "inner"]`.

### Experiment 2 — 400 vs 422

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "unclosed'
```

400 — not even parseable. Versus Experiment 1's 422 — parseable, wrong shape.

### Experiment 3 — ordering is observable

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" -d '{"garbage": true}'
```

**401, not 422.** The dependency rejected it before validation. An
unauthenticated caller learns nothing about your schema.

### Experiment 4 — see CAVEAT-001 for yourself

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What was Eternal FY26 revenue?","tenant_id":"00000000-0000-0000-0000-000000000000"}' \
  | python3 -m json.tool | head -20
```

The request is **accepted**. The pipeline runs scoped to a tenant you named in
the body. Because that tenant holds nothing, RLS returns zero rows and you get a
refusal — but note *what happened*: the body chose the scope.

### Experiment 5 — CORS is a browser rule

```bash
curl -s -i -X POST http://localhost:8000/api/query \
  -H "Origin: https://evil.example.com" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"test"}' | head -20
```

The request **succeeds**. `curl` is not a browser and does not enforce CORS. Now
look for `access-control-allow-origin` in the response headers — absent for a
disallowed origin. A browser would fetch this response and refuse to hand it to
your JavaScript.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/api/query.py` (top 40 lines) and `backend/app/auth/schemas.py`:

1. Which fields of `QueryRequest` are required and which optional? What decides?
2. `execution_context` is `Dict[str, Any]`. Name one thing Pydantic checks about
   its contents and one thing it does not.
3. Find where `request_id` is created. Why there and not from the client?
4. In `schemas.py`, which of the three models has no producer anywhere in the
   codebase? What is it for?
5. `TokenResponse` declares `expires_in_hours: int = 2`. Find the *other* place
   that number is written and say what would happen if they diverged.

---

## 12. Self-check questions

**Basic**
1. Name three things JSON cannot represent natively.
2. What does a Pydantic model guarantee?
3. What is the difference between 400 and 422?
4. Why does an unauthenticated malformed request return 401, not 422?
5. What is `response_model` for?

**Code**
6. What are the three fields of `QueryRequest`?
7. Where does `tenant_id` come from, and in what order of preference?
8. What does `_safe_json`'s `default=str` prevent?
9. Which model in `auth/schemas.py` has no producer?
10. Where is `request_id` generated?

**Why**
11. Why is `tenant_id` in the request body a Critical caveat for a multi-tenant
    product but Low today?
12. Why is `execution_context` deliberately untyped, and what did that cost?
13. Why is "validated" not "correct"? Give a concrete LedgerMind example.
14. Why validate at the boundary rather than deeper in the pipeline?
15. Why is `email: str` rather than `EmailStr` defensible here?

**Debugging**
16. A client reports "my `execution_context` is ignored". Where do you look?
17. A stored figure is `54364.0` where the source said `54364.00`. Which
    boundary, and what is the fix?
18. A query returns a refusal for a company you know is in the corpus, and the
    caller is authenticated. Name the request field you check first.

**System design**
19. Write the correct fix for `CAVEAT-001`, including what must be recorded.
20. `execution_context` is an untyped escape hatch that hid a dead guardrail for
    weeks. Propose a design that keeps the flexibility and makes the dead wiring
    detectable.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **Required:** `query` (no default). **Optional:** `tenant_id`,
   `execution_context` (both `Optional[...] = None`). The **presence of a
   default** is what makes a field optional in Pydantic — not the `Optional[]`
   annotation, which only widens the type.
2. **Checks:** that it is a dictionary (or `None`) with string keys. **Does not
   check:** any key name, any value type, or that `intended_operation` is one of
   the five legal operations. That last omission is `CAVEAT-002`'s hiding place.
3. `request_id = str(uuid.uuid4())` inside the handler. Server-side because it
   must be unique and unforgeable — it keys every log line and the audit row. A
   client-supplied id could collide with another user's, or be reused to make two
   different queries look like one in the audit trail.
4. **`TokenPayload`.** Nothing constructs it; `core/security.py` builds the JWT
   payload as a plain dict. It exists as documentation of the claim shape.
5. `ACCESS_TOKEN_EXPIRE_HOURS = 2` in `core/security.py`. If they diverged, the
   client would compute `expiresAt` (in `lib/auth.ts`) from the *response* value
   while the token's actual `exp` came from the *constant* — so the client would
   either log you out early or, worse, keep sending a token it believes is valid
   and receive 401s it does not expect. Two copies of one fact, which is the
   failure class behind this project's single metric registry.

### §12 — Basic

1. Dates, decimals (exact numerics), sets. Also acceptable: comments, binary
   data, cyclic references.
2. **Shape** — that required fields are present and every field is of the
   declared type, coercing where unambiguous. Nothing about meaning.
3. 400 — the body is not parseable as JSON at all. 422 — valid JSON, wrong
   shape.
4. Because dependencies run **before** body validation. It is also a security
   property: an unauthenticated caller learns nothing about the schema.
5. It validates the **response** shape on the way out, so a handler returning the
   wrong thing fails at the boundary rather than sending a malformed body — and
   it documents the response in `/docs`.

### §12 — Code

6. `query: str` (required), `tenant_id: Optional[str]`,
   `execution_context: Optional[Dict[str, Any]]`.
7. `payload.tenant_id or current_user.get("tenant_id", "default")` — **the
   request body first**, the verified JWT second. That preference order is
   `CAVEAT-001`.
8. Silent loss of precision on types JSON cannot represent — chiefly `Decimal`
   from psycopg2. Without it, a `Decimal("54364.00")` becomes a float; with it,
   it survives as the string `"54364.00"`.
9. `TokenPayload`.
10. In the handler, `request_id = str(uuid.uuid4())`.

### §12 — Why

11. The value flows into `SET LOCAL app.tenant_id` (the RLS scope), the Qdrant
    payload filter, and the audit row. So a user of tenant A can name tenant B
    and every defence obediently scopes to B. Today only one tenant holds data,
    so there is nothing to read; the moment a second does, it is a full
    cross-tenant read.
12. It is a UI escape hatch whose contents vary by view, and typing it would mean
    updating the model for every UI change. The cost: `CAVEAT-002` —
    `intended_operation` is written into `preferred_operation` and read by
    nothing, and the "load-bearing guardrail" it was meant to drive has never
    fired. Nothing detected that, because there is nothing to detect against.
13. Pydantic proves the *shape*. It cannot prove the *meaning*. Concrete example:
    Gemini's `response_schema` guarantees a `GeminiDSLResponse` with a `metric`
    string — and the model, required to emit *some* metric, returned
    `total_expenses` for a question about EBITDA. Perfectly valid, perfectly
    wrong. That is why three regex guards run over the raw query before any LLM
    call (Day 34).
14. Because the further from the boundary, the more code has already run on bad
    data — and each of those places must then defend itself independently.
    Validate once, at the edge, and everything downstream can assume the shape.
15. Because a nonexistent email must produce a **401** regardless (you must never
    reveal whether an address exists), so `EmailStr` would convert one 401 into a
    different 4xx without improving security. It is a defensible choice — just
    know it is a choice.

### §12 — Debugging

16. `router_node` writes `state["preferred_operation"]` from
    `execution_context["intended_operation"]`; then grep for
    `preferred_operation` and find that `validate_dsl`'s parameter is never
    passed at its single call site. `CAVEAT-002`. The `enforce_path` half **does**
    work — only the operation half is dead.
17. The **Python → JSON** boundary. psycopg2 returns `Decimal`; `json.dumps`
    cannot serialise it, and a naive `float()` conversion loses exactness. Fix:
    `json.dumps(value, default=str)`, as `_safe_json` does.
18. **`tenant_id` in the request body** (`CAVEAT-001`). If it was set to a tenant
    with no documents, RLS returns zero rows and the system correctly refuses —
    and the refusal looks identical to "we do not hold that company". Check the
    request before checking the corpus.

### §12 — System design

19. Remove `tenant_id` from `QueryRequest` entirely and read it only from
    `current_user`. If an override is genuinely required for evaluation: gate it
    behind `require_role("admin")` **and** an explicit environment flag, and
    **record in the audit row that an override was used** — otherwise an
    overridden query is indistinguishable from a normal one in the permanent
    record, which defeats the audit log's purpose. Note this is a functional
    change and requires explicit authorisation.
20. Keep `Dict[str, Any]` on the wire, but define a Pydantic model for the *known*
    keys and parse into it at the point of use, logging any key that is present
    and unconsumed. Alternatively: assert at startup that every key the frontend
    can send has a reader — a test that imports the frontend's constant list and
    greps the backend for each name. The general principle: **a field with no
    consumer should be loud, not silent**, exactly as `state.py` treats a dead
    field by refusing to make it plural.

---

## 14. MUST REMEMBER

```text
- JSON has six types. No dates, no decimals, no sets, no comments
- Pydantic guarantees SHAPE, never MEANING
- 400 = unparseable · 422 = parseable, wrong shape
- Dependencies run BEFORE validation → 401 beats 422
- A field is optional because it has a DEFAULT, not because of Optional[]
- CAVEAT-001: tenant_id in the request body overrides the verified JWT
- CAVEAT-002: preferred_operation is written and never read
- `echo ${#TOKEN}` after minting, so an empty token fails loudly
```

## 15. MUST UNDERSTAND

```text
- Why "validated" is not "correct", and where that gap is paid for later
- Why validating at the boundary beats validating deep in the pipeline
- Why a field with no consumer, and a model with no producer, are the same
  failure shape — and why one of them is Critical and the other harmless
- Why CORS failing in a browser and succeeding in curl is not a contradiction
- Why the request_id is minted server-side
```

---

## 16. This connects to

```text
Day 4 — how a request arrives
   ↓
Day 5 — what a valid request looks like            ← you are here
   ↓
Day 6 — the same request, streamed back node by node
```

Forward references:

- `Depends(get_current_user)` → **Day 8**
- `CAVEAT-001` and `SET LOCAL app.tenant_id` → **Day 14**
- `CAVEAT-002` and `validate_dsl` → **Day 32**
- Pydantic as an **LLM schema** → **Day 18**
- "Validated is not correct" → the three guards, **Day 34**
- `role_filtered_response` → **Day 9**
