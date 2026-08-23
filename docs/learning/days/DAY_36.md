# Day 36 — The Router: Classification, Entity Resolution, Refusal

**Phase 10 · Weight: H (~120 min) · Prerequisites: Days 19, 31, 35**

**Textbook: 14.2 "Query Classifier" — EXTENDS.** The case study routes between
two engines. This router also extracts entities, resolves them against a
registry, and can refuse.

---

## 1. Today's goal

By tonight you can:

- Explain what the router does beyond classification, and why extraction and
  classification share one call.
- Explain `resolve_ticker`'s surprising contract — **it never returns `None`** —
  and where an unknown company is actually detected.
- Explain **F2 and F14 as one defect class**: a single-valued field overloading
  `null` with two incompatible meanings.
- Explain why F2 is "partial by construction", and what would close it.
- Explain `CAVEAT-018` — the registry is larger than the corpus — and the two
  different refusals that implies.

---

## 2. Why now

Day 35 showed `route_after_router` consulting a `path` the router wrote. Today is
where that value comes from, and where the refusal that bypasses the engines is
written. Day 37 then covers the third path this router can choose.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `generate_structured`, `LLMUnavailable` | Day 19 | The one call |
| The schema is prompt input | Day 18 | F14's unmeasured classifier |
| `companies: list[str]`, empty is legal | Day 3 | The F14 shape |
| `_build_filter` drops the company condition | Day 27 | What the refusal prevents |
| `route_after_router` | Day 35 | Where the refusal terminates |

---

## 4. Concept lesson

### 4.1 Four jobs, one call

`router_node` does more than its name suggests:

| Job | Output |
|---|---|
| **Classify** the path | `path` ∈ {semantic, quantitative, cross} |
| **Extract** entities | `companies[]`, `fiscal_year`, `quarter`, `financial_type` |
| **Resolve** names to tickers | via `entity_resolver.resolve_ticker` |
| **Refuse**, when nothing resolves | `error`, `response_text`, `tier=low` |

**Why one LLM call and not two.** Quota (Day 17): a semantic query already makes
two calls (router + synthesis) against 500/day. Splitting extraction from
classification would make it three.

**And the cost of sharing**, recorded in `router.py` itself:

> if a probe ever shows omissions, **split extraction and classification into two
> calls** rather than adding another prompt line.

The fields the model must extract and the rules it must classify by live in **one
prompt**, so a change to either can perturb the other. That is why adding a
`company_mentioned` instruction was considered risky *"among the entity fields
the PATH CLASSIFICATION rules read"* (Day 18).

---

### 4.2 `RouterResponse`, field by field

```python
class RouterResponse(BaseModel):
    companies: list[str]
    company_mentioned: Optional[str]
    fiscal_year: Optional[str]
    quarter: Optional[str]
    financial_type: str
    path: Literal["semantic", "quantitative", "cross"]
    route_reason: str
```

**`companies: list[str]` — required, no default.** The comment:

> **REQUIRED WITH NO DEFAULT, deliberately.** The empty list is the no-issuer case
> and the model must emit it explicitly; a default would let an omission and a
> genuine "no issuer" produce the same value, **which is the exact overloading
> this field exists to remove.**

**Read that.** F14 removed one overload (`null` meaning both "none" and "several")
and the design refuses to reintroduce a different one (a default meaning both
"none" and "the model did not answer").

**`route_reason: str`** — the model's own explanation. Recorded in state and the
audit log. **It is also how a defect was found:** on 2026-08-11 the model
explained in `route_reason` that the query named *"a company not in the supported
list"* — the observation the schema had nowhere to express (§4.5).

**`path: Literal[...]`** — a closed set in the schema itself, so Gemini's
constrained decoding cannot emit a fourth value.

---

### 4.3 `resolve_ticker` never returns `None`

```python
def resolve_ticker(raw_name: str) -> str: ...
```

**It uppercases its input** when nothing matches. So:

```python
resolve_ticker("Reliance Industries")  →  "RELIANCE INDUSTRIES"
```

Not `None`. Not an error. A string that *looks like* a ticker.

**Which means the check cannot be `if resolved is None`:**

```python
# F2: resolve_ticker NEVER returns None -- it uppercases its input --
# so this gate, not resolve_ticker, is where an unknown company is
# actually detected.
if _resolved in _KNOWN_TICKERS:
    if _resolved not in companies:
        companies.append(_resolved)
else:
    unresolved_names.append(str(_raw))
```

**The membership test against `_KNOWN_TICKERS` is the real gate.** A reader
assuming `resolve_ticker` signals failure would write a check that never fires —
and the comment exists because that is exactly what happened.

**`_KNOWN_TICKERS` itself:**

```python
_KNOWN_TICKERS = sorted({p.ticker for p in COMPANY_PROFILES})
```

Derived from the registry (Day 31's principle), and **interpolated into the
prompt** (Day 18).

---

### 4.4 `CAVEAT-018` — the registry is larger than the corpus

`COMPANY_REGISTRY` holds seven profiles: ETERNAL, PAYTM, NYKAA, POLICYBAZAAR,
DELHIVERY, SWIGGY, TITAN.

**The corpus holds three:** ETERNAL, TITAN, PAYTM.

From `CLAUDE.md`:

> `_KNOWN_TICKERS` is larger than the corpus (SWIGGY, NYKAA, DELHIVERY,
> POLICYBAZAAR resolve with **zero documents**), so **"unknown company" and
> "known company, no documents" are different refusals.**

| Situation | Router | Outcome |
|---|---|---|
| "Reliance" | does not resolve | `company_not_in_corpus` refusal |
| "Swiggy" | **resolves cleanly** | passes; retrieval finds nothing; `no_data_found` |
| "Eternal" | resolves | answered |

**Two different truths, and the second gets a message about a *period* not being
indexed** — which is misleading, because the issue is the *company*.

**Why the registry is broader.** Because it is the **normalisation vocabulary**:
"bundl technologies" genuinely means SWIGGY, and knowing that is correct
independent of whether we hold Swiggy's filings. Schema versus state again
(Day 31) — and here the separation produces a user-facing wrinkle.

---

### 4.5 F2 and F14 as one defect class

**F14 (the state field).** `company: Optional[str]` held one issuer. A two-issuer
query nulled it — and null already meant "no issuer named":

> Measured 2026-08-21 on the groq fallback: the same query collapsed to `PAYTM`
> and the answer stated the documents contain **no company named Eternal**.
> ETERNAL is 732 rows.

**F2 (the refusal).** `company=None` conflated "no company mentioned" with "named
a company we do not hold":

> Measured 2026-08-11 on *"What were Reliance Industries revenue drivers in
> FY26?"*: `company=None`, `company_unresolved=None`, and `route_reason` reading
> *"a company not in the supported list"*. **The model OBSERVED the condition and
> had no field in which to express it**, so it explained itself in prose and took
> the only exit the schema allowed. That query still runs unfiltered over the whole
> tenant and answers at `tier=high`.

**One class: a single-valued field carrying two incompatible meanings in one
value.**

| | Overloaded value | Meanings conflated | Fix |
|---|---|---|---|
| **F14** | `company = None` | "no issuer" / "several issuers" | make it a **list** |
| **F2** | `company = None` | "no issuer" / "unknown issuer" | add `company_mentioned` |

**And the fix for each is a *schema* change**, not a check — because a check
cannot recover information the type could not carry.

---

### 4.6 `company_mentioned` — a field with no prompt block

```python
# F2: the raw issuer name AS SEEN, independent of resolvability. `company`
# is normalise-or-null, so when the model saw a company it could not
# normalise it had nowhere to say so and returned null ... Same shape as
# the DSL period-invention bug: a schema that cannot express what the
# model observed.
company_mentioned: Optional[str]
```

**"A schema that cannot express what the model observed."** The same sentence
describes `CAVEAT-004` (Day 32) — required `metric` and `fiscal_year` forcing
invention. **Two subsystems, one root cause: the schema is the model's
vocabulary, and a missing field is a thought it cannot have.**

**And the instruction that was written, shipped and removed** (Day 18): the model
populates the field readily without one, so the instruction bought a coverage
guarantee that was never needed while adding a prompt block among the rules that
drive classification.

**But:** *"'No prompt block' is not 'invisible to the model.'"* Declaring the
field is itself an input change.

---

### 4.7 `_resolve_mentioned_issuers` — splitting a free-text field

```python
def _resolve_mentioned_issuers(company_mentioned):
    """
    Split company_mentioned into (resolved, unresolved) ticker lists.

    F2 step 2. The refusal CANNOT key on `company is None`: RouterResponse
    holds ONE company, so a multi-entity query nulls it even when every
    issuer named is in the corpus. Measured 2026-08-12 on golden Q051 ("Who
    grew revenue faster in FY26, Eternal or Paytm?") -- company=None,
    company_mentioned='Eternal, Paytm', both resolvable. Refusing on
    nullness would have refused a question that passes today.
    """
    if not company_mentioned:
        return [], []
    parts = re.split(r",|\bvs\.?\b|\band\b|\bor\b", company_mentioned, flags=re.I)
    ...
```

**A regex splitting on `,`, `vs`, `and`, `or`** — because the model returns a free
string like `"Eternal, Paytm"` or `"Eternal vs Paytm"`.

**Fragile by nature.** A company whose name contains "and" — "Larsen and Toubro" —
would split wrongly. Not currently in the registry, and worth knowing.

**Note the docstring's own framing:**

> **Same defect class as F2 one level up:** a single-valued field overloading null
> with two incompatible meanings — "not in corpus" and "more than one". **This
> separates them.**

---

### 4.8 The refusal, and why it is "partial by construction"

```python
_refusal = None
if (result.get("route_reason") or "").startswith("FALLBACK_ERROR"):
    _refusal = ("routing_unavailable", "The query could not be classified ...")
elif result.get("company_unresolved"):
    _refusal = ("company_not_in_corpus", "This query names a company that is not present ...")

if _refusal is not None:
    state["error"], state["response_text"] = _refusal
    state["error_node"] = "router"
    state["path"] = result["path"]
    state["route_reason"] = result["route_reason"]
    state["confidence_tier"] = "low"
    state["confidence_score"] = 0.0
    return state
```

**Two refusals.** `routing_unavailable` when both providers failed;
`company_not_in_corpus` when named issuers all failed the ticker gate.

**`error_node = "router"`** is what `route_after_router` keys on (Day 35).

**And `path` is still written**, even on a refusal — so the audit row records what
the classifier *decided* alongside the refusal.

**The placement comment:**

> Placed **BEFORE** the UI workflow override deliberately: forcing a desk does not
> fix an entity that failed to resolve, so **an override must not be able to route
> past this.**

**A UI override cannot bypass a refusal.** Ordering as enforcement.

**And the honesty:**

> **PARTIAL BY CONSTRUCTION — READ BEFORE ASSUMING THIS CLOSES F2.**
> `company_not_in_corpus` fires only when the model RETURNS a name that fails the
> `_KNOWN_TICKERS` gate (a misspelling, a subsidiary, a renamed entity). It does
> **NOT** fire on the common case, because `ROUTER_SYSTEM_PROMPT` offers the model
> only two options — "normalise to canonical ticker from this list" or "if no
> company mentioned, return null".

**The prompt gives the model no way to say "I saw a company and it is not on your
list."** So the common case still produces an empty list, and `_build_filter`
drops the condition (Day 27).

**And the warning about the fix:**

> Do not "fix" this by appending an instruction that contradicts the normalise
> rule two lines above it; **that is the shape that lost three times already.**

---

### 4.9 `_build_resolved_query`

```python
def _build_resolved_query(original_query, companies, fiscal_year, quarter, financial_type) -> str:
    # Every named issuer joins the BM25 prefix. One issuer produces exactly
    # the pre-F14 string; none produces no prefix, also as before. Two now
    # contribute both tickers where previously the null contributed nothing.
    prefix_parts = [p for p in [*companies, fiscal_year, quarter, financial_type] if p]
    return f"{' '.join(prefix_parts)} {original_query}" if prefix_parts else original_query
```

Day 25 covered what this buys. Note the F14 comment's discipline: **one issuer
produces byte-identical output to before**, so the change cannot perturb a
single-issuer query that was already measured.

---

## 5. The actual LedgerMind files

```
File:  backend/app/engines/router.py (428 lines)
       DOCSTRING IS A STUB — four lines for a file holding the classifier,
       the refusal logic, and both routing functions
       RouterResponse (Pydantic — SENT TO THE MODEL)
       ROUTER_SYSTEM_PROMPT (interpolated from the registries)
       _classify_query · _resolve_mentioned_issuers · _build_resolved_query
       router_node · route_after_shield · route_after_router
       _KNOWN_TICKERS · _KNOWN_METRICS

File:  backend/app/ingestion/entity_resolver.py — COMPANY half
       NO MODULE DOCSTRING
       COMPANY_REGISTRY: list[CompanyProfile]   (7 profiles)
       _ALIAS_INDEX  ·  resolve_company()  ·  resolve_ticker()
```

**Note where `entity_resolver` lives: `app/ingestion/`.** A query-path module
imports from the ingestion package — because company aliases are one fact used by
both halves, and duplicating them is the failure class this codebase eliminated
(Day 31).

---

## 6. Deep walkthrough — `_classify_query`

**STATE BEFORE.** A raw query string.

**Step 1 — the LLM call.**

```python
llm = generate_structured(system=ROUTER_SYSTEM_PROMPT, user=query,
                          schema=RouterResponse, temperature=0.0, max_tokens=200)
llm_result = llm
raw_text = llm.text
```

**The whole `LLMResult` is kept**, not just `.provider`:

> so the caller can record provider AND model in one attributed write.

**Step 2 — parse, with the fence fallback** (Day 18).

**Step 3 — resolve every named issuer.**

```python
companies_raw = result.get("companies") or []
if isinstance(companies_raw, str):
    companies_raw = [companies_raw]
```

**A defensive unwrap:**

> A `str` here would be a model that ignored the array instruction; wrapping it is
> **cheaper than a parse failure** and keeps the single-issuer path identical to
> the pre-F14 behaviour.

Then, per name:

```python
for _raw in companies_raw:
    if _raw is None or str(_raw).strip() == "" or str(_raw).lower() == "null":
        continue
    _resolved = resolve_ticker(str(_raw))
    if _resolved in _KNOWN_TICKERS:
        if _resolved not in companies:
            companies.append(_resolved)
    else:
        unresolved_names.append(str(_raw))
```

**`str(_raw).lower() == "null"`** — the literal string `"null"`. Models emit it.
Filtered explicitly rather than trusted to be `None`.

**`if _resolved not in companies`** — deduplication preserving order. Two aliases
for one issuer must not produce two filter values (Day 27's `MatchAny`).

**Step 4 — the refusal gate.**

```python
# REFUSE ONLY WHEN NOTHING RESOLVED. For one issuer this is exactly
# the pre-F14 rule: a single unresolvable name refuses, as Reliance
# did. For several it is strictly weaker on purpose -- one unknown
# name alongside a known one must not refuse the known one.
if unresolved_names and not companies:
    company_unresolved = ", ".join(unresolved_names)
    logger.warning("Router named only unknown companies: %r ...", unresolved_names)
```

**"Strictly weaker on purpose."** *"Compare Eternal and Reliance"* resolves
ETERNAL and fails RELIANCE — and answers about Eternal rather than refusing both.

**Step 5 — the `company_mentioned` cross-check**, using
`_resolve_mentioned_issuers`, with the same "refuse only when nothing resolves"
rule.

**Step 6 — normalise the remaining fields.**

```python
path = result.get("path", "semantic").lower()
if path not in ("semantic", "quantitative", "cross"):
    path = "semantic"
```

**Defensive even though the schema is a `Literal`.** Gemini's constrained decoding
guarantees it; **Groq's does not** (Day 18) — the Groq path validates against the
model, but a value that passes Pydantic could still arrive in unexpected case.

```python
quarter = result.get("quarter")
if quarter and quarter.lower() == "null": quarter = None
if quarter:
    quarter = quarter.upper().strip()
    match = re.search(r"(Q[1-4])", quarter)
    quarter = match.group(1) if match else quarter
```

**Extracting `Q4` from whatever the model wrote** — `"Q4 FY26"`, `"Quarter 4"`.

**Step 7 — the fallback return.**

```python
except LLMUnavailable as e:
    logger.error("Router classification unavailable on ALL providers: %s", e)
except Exception as e:
    logger.error("Router classification failed: %s", e)

return {
    "companies": [], "ticker": None, "company_unresolved": None,
    "company_mentioned": None, "fiscal_year": None, "quarter": None,
    "financial_type": "consolidated", "path": "semantic",
    "route_reason": "FALLBACK_ERROR: classification failed on all providers",
    "llm_result": None,
}
```

**The sentinel in `route_reason`:**

> The audit trail must be able to tell this apart from a genuine semantic
> classification — **an error-masked-as-semantic route is indistinguishable
> otherwise**, which is the defect class that cost two sessions of investigation.

**`path="semantic"` is a *safe* default and a *dishonest* one** — so the
dishonesty is repaired by the marker, which `router_node` then turns into a
refusal.

---

### 6.1 The UI override

```python
if context.get("enforce_path") and context.get("intended_path"):
    intended_path = context["intended_path"]
    logger.info("⚡ UI Workflow Override: Forcing path '%s' (ignoring Gemini classification '%s')", ...)
    state["path"] = intended_path
    state["route_reason"] = f"UI Workflow Override: Routed directly to {intended_path} desk"
    if context.get("intended_operation"):
        state["preferred_operation"] = context["intended_operation"]
    return state
```

**After the refusal check** (§4.8). And `preferred_operation` is written here and
**read by nothing** — `CAVEAT-002` (Day 32).

---

## 7. Data flow

```
"Who grew revenue faster in FY26, Eternal or Paytm?"
        │
        ▼ router_node
        │  if state["is_blocked"]: return state      ← the shield already ran
        ▼ _classify_query(query)
        │
        ▼ generate_structured(ROUTER_SYSTEM_PROMPT, RouterResponse)
        │    schema IS prompt input                            (Day 18)
        │    ├─ LLMUnavailable → FALLBACK_ERROR sentinel
        │    └─ ok → {"companies": ["Eternal","Paytm"], "path": "quantitative", ...}
        │
        ▼ per name: resolve_ticker → in _KNOWN_TICKERS ?
        │    "Eternal" → ETERNAL ✓        "Paytm" → PAYTM ✓
        │    unresolved_names = []        companies = ["ETERNAL","PAYTM"]
        │
        ▼ refuse only if unresolved_names AND not companies
        │
        ▼ _resolve_mentioned_issuers(company_mentioned)   same rule
        │
        ▼ normalise fiscal_year / quarter / financial_type / path
        │
        ▼ back in router_node:
        │    state["companies"] = ["ETERNAL","PAYTM"]
        │    record_llm_call(state, llm_result)                (Day 19)
        │    state["resolved_query"] = "ETERNAL PAYTM FY26 consolidated Who grew..."
        │    state["company_unresolved"] = None
        │
        ▼ REFUSAL CHECK  ← before the UI override, deliberately
        │    FALLBACK_ERROR?      → routing_unavailable
        │    company_unresolved?  → company_not_in_corpus
        │       error_node = "router" · tier = low · score = 0.0
        │       RETURN
        │
        ▼ UI override, if enforce_path
        ▼ state["path"], state["route_reason"]
        │
        ▼ route_after_router(state)                            (Day 35)
             "refused" → audit_writer      "quant_engine" → ...
```

---

## 8. Engineering decision — one classification call, refuse on unresolved

**Problem.** Turn free text into a path plus entities, and refuse rather than
search unfiltered when the entities do not resolve.

**Decision.** One structured LLM call; resolve every name against the registry;
refuse only when **nothing** resolves; refuse **before** any UI override.

`ENGINEERING_DECISIONS.md` **ED-009**, **ED-010**.

| Alternative | Why not |
|---|---|
| **Two calls (extract, then classify)** | Doubles router cost against 500/day. Named as the fallback *if a probe shows omissions* |
| **Regex/keyword routing** | Cannot handle arbitrary phrasing; "who grew faster" is not a keyword problem |
| **Refuse on any unresolved name** | Would refuse *"Compare Eternal and Reliance"*, which can answer about Eternal |
| **Refuse on an empty `companies` list** | Would refuse **Q051**, which passes *because* retrieval runs unfiltered while the DSL carries both issuers (Day 27) |
| **Let the UI override skip the refusal** | Forcing a desk does not fix an entity that failed to resolve |

**Trade-offs accepted.**

- **F2 is partial by construction.** The prompt gives the model no way to say
  "I saw a company not on your list", so the common case still yields an empty
  list.
- **`CAVEAT-018`:** a registry broader than the corpus means "known but no
  documents" gets a message about a *period*.
- **`_resolve_mentioned_issuers` splits on "and"** — fragile for a company whose
  name contains it.
- **F14 shipped without a router probe** (`KU-002`), so the classifier is
  **unmeasured** across a change that altered the schema on both providers.
- **`TQ008` routes `cross` where its golden expects `semantic`**, cause unknown,
  a prompt-block explanation suspected and **disproved** (`KU-001`).

**Current validity.** The mechanism is right and the gaps are named. The
open item that matters most is `KU-002`.

**At 10×** — in issuers. `_KNOWN_TICKERS` interpolated into the prompt grows
linearly, and `CAVEAT-018`'s gap widens with every registry entry that has no
documents.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| A confident answer about a company not in the corpus | **F2, partial** — empty list, filter dropped (Day 27) |
| "No data found for this period" for a company we do not hold | `CAVEAT-018` — resolved cleanly, zero documents |
| Two issuers, one silently dropped | Historic **F14** |
| A refusal that still returns citations | `route_after_router` not keying on `error_node` (Day 35) |
| An outage classified as a normal semantic query | Would be the missing `FALLBACK_ERROR` sentinel |
| A UI override routing past a refusal | Would be the check placed after the override |
| A route differs run to run | **Check `llm_provider` first** (Day 19) |
| A company name containing "and" split wrongly | `_resolve_mentioned_issuers`'s regex |

---

## 10. Hands-on experiment

### Experiment 1 — the registry versus the corpus

```bash
docker compose exec -T backend python -c "
import os, psycopg2
from app.engines.router import _KNOWN_TICKERS
from app.ingestion.entity_resolver import COMPANY_REGISTRY
print('registry profiles:', len(COMPANY_REGISTRY))
for p in COMPANY_REGISTRY:
    print(f'  {p.ticker:14} {p.sector:16} aliases={len(p.aliases)}')
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SET app.tenant_id = %s', (os.getenv('T'),))
cur.execute('SELECT DISTINCT company FROM financials')
have = {r[0] for r in cur.fetchall()}; c.close()
print()
print('IN PROMPT :', _KNOWN_TICKERS)
print('IN CORPUS :', sorted(have))
print('RESOLVE BUT HAVE NO DOCUMENTS:', sorted(set(_KNOWN_TICKERS) - have))
print()
print('CAVEAT-018: those get \"no data for this period\" — about the PERIOD,')
print('when the real issue is the COMPANY.')
"
```

### Experiment 2 — `resolve_ticker` never returns `None`

```bash
docker compose exec -T backend python -c "
from app.ingestion.entity_resolver import resolve_ticker
from app.engines.router import _KNOWN_TICKERS
for raw in ['Eternal', 'zomato', 'Zomato Limited', 'One97 Communications',
            'bundl technologies', 'Reliance Industries', 'complete nonsense']:
    r = resolve_ticker(raw)
    print(f'  {raw!r:26} -> {r!r:26} in _KNOWN_TICKERS: {r in _KNOWN_TICKERS}')
print()
print('It UPPERCASES on a miss. The membership test is the real gate.')
print('A check written as `if resolve_ticker(x) is None` would NEVER fire.')
"
```

### Experiment 3 — the mentioned-issuer splitter

```bash
docker compose exec -T backend python -c "
from app.engines.router import _resolve_mentioned_issuers
for s in ['Eternal', 'Eternal, Paytm', 'Eternal vs Paytm', 'Eternal and Paytm',
          'Eternal or Reliance', 'Reliance Industries', 'Larsen and Toubro', None, '']:
    print(f'  {str(s)!r:26} -> resolved={_resolve_mentioned_issuers(s)[0]} '
          f'unresolved={_resolve_mentioned_issuers(s)[1]}')
print()
print('Row 7: a company name CONTAINING \"and\" splits wrongly. Not in the')
print('registry today; a real fragility.')
"
```

### Experiment 4 — classify, three runs

```bash
docker compose exec -T backend python -c "
from app.engines.router import _classify_query
q = 'Who grew revenue faster in FY26, Eternal or Paytm?'
for i in range(3):
    r = _classify_query(q)
    llm = r.get('llm_result')
    print(f'run {i+1}: path={r[\"path\"]:13} companies={r[\"companies\"]} '
          f'fy={r[\"fiscal_year\"]} provider={getattr(llm,\"provider\",None)} '
          f'model={getattr(llm,\"model\",None)}')
    print(f'        route_reason: {r[\"route_reason\"][:80]!r}')
print()
print('THREE runs with provider and model printed — CLAUDE.md §8.')
print('Cause cannot be assigned from a single before/after pair.')
"
```

> **Quota:** three calls.

### Experiment 5 — the refusal, and where it must sit

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ANALYST" -H "Content-Type: application/json" \
  -d '{"query":"What were Reliance Industries revenue drivers in FY26?"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('error       :', d.get('error'))
print('error_node  :', d.get('error_node'))
print('path        :', d.get('path'), ' <- still recorded, even on a refusal')
print('citations   :', len(d.get('citations', [])), ' <- 0: the refusal TERMINATED')
print('tier        :', d.get('confidence_tier'))
print()
print(d.get('response_text'))
"
```

Then the override, which must **not** route past it:

```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer $ANALYST" -H "Content-Type: application/json" \
  -d '{"query":"What were Reliance Industries revenue drivers in FY26?",
       "execution_context":{"enforce_path":true,"intended_path":"quantitative"}}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('error:', d.get('error'), '| path:', d.get('path'))
print()
print('The override is applied AFTER the refusal check, deliberately:')
print('forcing a desk does not fix an entity that failed to resolve.')
"
```

### Experiment 6 — the F2 gap, made visible

```bash
docker compose exec -T backend python -c "
import logging; logging.basicConfig(level=logging.INFO, force=True)
from app.engines.router import _classify_query
r = _classify_query('What were Reliance Industries revenue drivers in FY26?')
print('companies         :', r['companies'])
print('company_unresolved:', r['company_unresolved'])
print('company_mentioned :', r['company_mentioned'])
print('route_reason      :', r['route_reason'][:110])
print()
print('If companies==[] and company_unresolved is None, the refusal does NOT')
print('fire and _build_filter drops the company condition — audit F2, partial')
print('by construction. Check the logs for UNFILTERED WHOLE-TENANT SEARCH.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/router.py` and
`backend/app/ingestion/entity_resolver.py`:

1. `resolve_ticker` never returns `None`. Where is an unknown company actually
   detected, and what bug does the comment say this caused?
2. The refusal fires only when **nothing** resolves. Give a query where refusing
   on *any* unresolved name would be wrong.
3. Find the "PARTIAL BY CONSTRUCTION" comment. Why does
   `company_not_in_corpus` not fire on the common case?
4. Why is the refusal check placed **before** the UI workflow override?
5. `_classify_query`'s fallback returns `path="semantic"` with a
   `FALLBACK_ERROR` marker. Why both?

---

## 12. Self-check questions

**Basic**
1. What four jobs does the router do?
2. Which fields does `RouterResponse` carry?
3. What are the two refusals?
4. How many companies are in the registry, and how many in the corpus?
5. What is `resolved_query`?

**Code**
6. What does `resolve_ticker` return on a miss?
7. What is `_KNOWN_TICKERS` built from?
8. What does `_resolve_mentioned_issuers` split on?
9. What does `record_llm_call` receive here, and why the whole object?
10. What does the quarter normalisation extract?

**Why**
11. Why one LLM call rather than two?
12. Why is `companies` required with no default?
13. Why refuse only when nothing resolves?
14. Why is F2 partial by construction?
15. Why is `path` still written on a refusal?

**Debugging**
16. A confident answer cites the wrong company. What do you grep for, and which
    two findings are candidates?
17. "No data found for this period" for a company we do not hold. Which caveat?
18. A query routes differently on two runs. What do you check before anything
    else?

**System design**
19. Close F2 fully. What must change, and which project rule does it trip?
20. `CAVEAT-018`: fix the "known but no documents" message without narrowing the
    normalisation vocabulary.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. In the **`_resolved in _KNOWN_TICKERS` membership test**, not in
   `resolve_ticker`. The comment: *"`resolve_ticker` NEVER returns None — it
   uppercases its input — so **this gate, not resolve_ticker, is where an unknown
   company is actually detected.**"* The bug: a check written as `if resolved is
   None` would never fire, so an unknown company would pass through as a
   ticker-shaped string.
2. *"Compare Eternal and Reliance revenue in FY26."* ETERNAL resolves, RELIANCE
   does not. Refusing on any unresolved name would refuse a question that **can**
   be answered about Eternal. The comment calls the multi-issuer rule *"strictly
   weaker on purpose"*.
3. Because `ROUTER_SYSTEM_PROMPT` offers the model **only two options** —
   normalise to a canonical ticker from the list, or return an empty list for "no
   company". **There is no way for it to say "I saw a company and it is not on
   your list."** So a question about Reliance yields `companies=[]` and no
   unresolved name, the refusal does not fire, and `_build_filter` drops the
   company condition (Day 27).
4. Because *"forcing a desk does not fix an entity that failed to resolve, so an
   override must not be able to route past this."* If the override ran first, a
   UI-forced quantitative query about an unresolvable company would proceed to the
   DSL and the SQL, and the refusal would never be reached.
5. **`path="semantic"`** because the graph needs *some* path and semantic is the
   safest (it degrades to a refusal on low confidence). **The `FALLBACK_ERROR`
   marker** because that default is otherwise **dishonest**: an
   error-masked-as-semantic route is indistinguishable from a genuine
   classification in the audit trail — *"the defect class that cost two sessions of
   investigation."* `router_node` then converts the marker into an explicit
   `routing_unavailable` refusal.

### §12 — Basic

1. Classify the path; extract entities and period; resolve names to tickers;
   refuse when nothing resolves.
2. `companies`, `company_mentioned`, `fiscal_year`, `quarter`, `financial_type`,
   `path`, `route_reason`.
3. `routing_unavailable` (both providers failed) and `company_not_in_corpus`
   (every named issuer failed the ticker gate).
4. **Seven** in the registry; **three** in the corpus (ETERNAL, TITAN, PAYTM).
5. The query prefixed with the extracted tickers and period tokens, used for
   retrieval (Day 25).
6. The **uppercased input** — never `None`.
7. `sorted({p.ticker for p in COMPANY_PROFILES})` — derived from the registry, and
   interpolated into the prompt.
8. `,`, `vs`/`vs.`, `and`, `or` — case-insensitive.
9. The whole `LLMResult`, so provider **and** model are recorded in one attributed
   write (Day 19).
10. `Q1`–`Q4` via `re.search(r"(Q[1-4])", quarter)`, from whatever the model wrote
    (`"Q4 FY26"`, `"Quarter 4"`).

### §12 — Why

11. Quota. A semantic query already makes two calls against 500/day; splitting
    extraction from classification makes it three. The file names two calls as the
    **fallback plan** if a probe ever shows omissions.
12. So that "the model omitted the field" and "the model says there is no issuer"
    cannot produce the same value — *"the exact overloading this field exists to
    remove."*
13. Because one unknown name alongside a known one must not refuse the known one,
    and because refusing on an **empty** list would refuse Q051, which passes
    precisely because retrieval runs unfiltered while the DSL carries both issuers.
14. See §11 Q3.
15. So the audit row records what the classifier **decided**, alongside the
    refusal. Without it you could not tell whether a refused query had been
    classified quantitative or semantic — which matters when investigating whether
    the classification itself was the problem.

### §12 — Debugging

16. `grep "UNFILTERED WHOLE-TENANT SEARCH"` in the backend logs. Candidates:
    **F2** (the company condition was dropped because nothing resolved) and
    **F14** (historic — a two-issuer query collapsed to one). Check `companies`,
    `company_unresolved` and `route_reason` on the response; if `companies` is
    empty and `route_reason` mentions a company, that is F2's partial gap.
17. **`CAVEAT-018`.** The company resolved cleanly against the registry (which is
    broader than the corpus) but has zero documents, so retrieval finds nothing and
    the message blames the **period**.
18. **`llm_provider` and `llm_model`, from the same response.** A Groq-served
    classification is not comparable to a Gemini-served one, and this is the single
    most common false-regression cause in this project. Only after those match is a
    code or prompt explanation worth considering — and then it is **three runs**,
    not two (`KU-001`'s TQ008 is exactly this situation, still open).

### §12 — System design

19. **What must change:** the prompt must give the model a way to say *"I saw a
    company and it is not in your list."* Concretely: `company_mentioned` already
    exists as a field, but the prompt's `companies` block instructs only
    "normalise to this list" or "return `[]`" — so add an explicit instruction that
    a company seen but not on the list goes in `company_mentioned`. **The rule it
    trips:** `CLAUDE.md` §1 rule 5 — **prompt edits are STOP-AND-ASK**, and the
    router file warns specifically: *"Do not 'fix' this by appending an instruction
    that contradicts the normalise rule two lines above it; that is the shape that
    lost three times already."* So the edit must **modify the existing `companies`
    block** rather than append a contradicting rule, and it needs a **two-arm
    router probe, three runs per arm, provider and model printed** — which is also
    what `KU-002` says is owed for F14. The file's own alternative if that fails:
    **split extraction and classification into two calls.**
20. **Do not narrow the registry** — "bundl technologies means SWIGGY" is correct
    knowledge independent of whether we hold Swiggy's filings, and narrowing it
    would break normalisation for a future ingest. **Instead, add the corpus check
    where the state is known:** after resolution, query which of the resolved
    tickers actually have `is_latest` rows for this tenant (a cheap indexed
    `SELECT DISTINCT company`), and if a resolved issuer has **none**, refuse with a
    message naming the *company* rather than letting retrieval return nothing and
    the message blame the period. **Why it belongs here and not in the registry:**
    Day 31's schema-versus-state rule — corpus membership is *state*, so it is
    queried, never written down; a `has_documents` flag in the registry would be
    the `available_in_corpus` time bomb again. **What it costs:** one extra query
    per request on the router path, and a new refusal code that the eval's
    `out_of_corpus` category would need to accept. Note this is a functional
    change requiring approval.

---

## 14. MUST REMEMBER

```text
- The router does FOUR jobs: classify, extract, resolve, refuse
- companies is REQUIRED with NO DEFAULT — the empty list must be explicit
- resolve_ticker NEVER returns None. It UPPERCASES on a miss
- The _KNOWN_TICKERS membership test is where an unknown company is detected
- Refuse only when NOTHING resolves — one unknown name must not refuse a known one
- The refusal check runs BEFORE the UI override, deliberately
- error_node = "router" is what route_after_router keys on
- FALLBACK_ERROR marks a total provider outage, so it is not mistaken for
  a genuine semantic classification
- F2 is PARTIAL BY CONSTRUCTION — the prompt gives no way to say
  "I saw a company not on your list"
- CAVEAT-018: 7 registry profiles, 3 in the corpus. Two different refusals
- KU-002: F14 shipped without a router probe. The classifier is UNMEASURED
```

## 15. MUST UNDERSTAND

```text
- Why F2 and F14 are ONE defect class: a single-valued field overloading null
  with two incompatible meanings — and why the fix is a SCHEMA change, not a check
- Why "a schema that cannot express what the model observed" describes both this
  and CAVEAT-004: the schema is the model's vocabulary
- Why the model explaining a condition in route_reason prose was EVIDENCE that
  the schema was missing a field
- Why ordering (refusal before override) is a form of enforcement
- Why a safe default can still be dishonest, and how a marker repairs it
```

---

## 16. This connects to

```text
Day 35 — the graph
   ↓
Day 36 — the router: how a question becomes a path   ← you are here
   ↓
Day 37 — the third path: cross-examination
```

Forward references:

- `path="cross"` and what it triggers → **Day 37**
- `_build_filter` dropping the company condition → **Day 27** (already read)
- `ROUTER_SYSTEM_PROMPT` as prompt engineering → **Day 18** (already read)
- `route_after_router` terminating the refusal → **Day 35** (already read)
- `KU-001` (TQ008) and `KU-002` (the unmeasured classifier) → **Day 43**
- `scripts/router_probe.py` → **Day 43**
