# Day 18 — Prompting, Structured Output, and the Schema as Prompt

**Phase 5 · Weight: H (~120 min) · Prerequisites: Days 10, 17**

**Textbook: Part 15 "Prompting" — EXTENDS.** The textbook covers the grounding
instruction. LedgerMind goes considerably further, and today is that further.

---

## 1. Today's goal

By tonight you can:

- Explain system versus user messages, temperature, and why every call here uses
  `temperature=0.0`.
- Explain structured output: what Gemini's `response_schema` guarantees, what
  Groq's `json_object` does not, and how the client closes the gap.
- Explain the discovery this project made the hard way: **the response schema is
  part of the prompt**. Declaring a Pydantic field is a model-input change even
  when no prompt text mentions it.
- Explain why appended instructions have lost to earlier, more concrete rules
  **three times**, and why prompt edits are a STOP-AND-ASK item.

---

## 2. Why now

Day 17 established what the model is for. Day 10 gave you Pydantic as a
validation tool. Today those two collide: the same Pydantic model that validates
LLM output is also **sent to the model** — so a "type declaration" is also a
prompt edit.

This is the least obvious idea in the codebase and it changes how you read every
schema from here on.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Pydantic validates and emits JSON Schema | Day 10 | Today, the schema is *sent* |
| "Validated is not correct" | Day 5 | Today, the proof |
| The three LLM call sites | Day 17 | All three send a schema or a prompt |

---

## 4. Concept lesson

### 4.1 System versus user message

```python
resp = client.models.generate_content(
    model=gemini_model,
    contents=user,                       # ← the user message
    config=types.GenerateContentConfig(
        system_instruction=system,       # ← the system message
        temperature=temperature,
        ...
    ),
)
```

- **System message** — the standing instructions. Who you are, what you may do,
  what format to produce. Constant across calls.
- **User message** — this specific request. Varies per call.

**Mental model.** The system message is **the job description**; the user message
is **today's task**.

In LedgerMind the system prompts are module-level constants
(`ROUTER_SYSTEM_PROMPT`, `DSL_SYSTEM_PROMPT`, `SYNTHESIS_SYSTEM_PROMPT`) —
built once at import (Day 12) and never varied per request. That is deliberate:
a prompt that varies per request cannot be reasoned about across an eval sweep.

---

### 4.2 Temperature, and why it is always zero here

Temperature controls sampling randomness. `0.0` means: always take the most
likely next token.

Every structured call in this system passes `temperature=0.0`:

```python
def generate_structured(system, user, schema, temperature=0.0, max_tokens=200, ...)
```

**Why.** For classification and DSL generation you want the **same question to
produce the same answer**. Without that:

- an eval sweep measures sampling noise, not the system;
- a bug reproduces intermittently;
- "cause cannot be assigned from a single before/after pair" (`CLAUDE.md` §8)
  becomes even harder.

**And note the honest caveat:** `temperature=0.0` reduces variance; it does not
eliminate it. Providers batch and route requests differently, and floating-point
non-determinism on the serving side means identical inputs can still differ. That
is precisely why `CLAUDE.md` mandates **three runs with provider and model
printed**, not one.

`generate_text` defaults to `temperature=0.2` — synthesis is prose, where a
little variation is harmless and rigid repetition reads badly.

---

### 4.3 Structured output, and the asymmetry between providers

**The problem.** You need JSON matching an exact shape. Asking politely in the
prompt gets you JSON *most of the time*, wrapped in markdown fences *some of the
time*, and prose *occasionally*.

**Gemini's answer** — constrained decoding:

```python
config=types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=schema,          # a Pydantic model
)
```

The model is constrained *during generation* so the output **parses into the
model**. Not a request — a guarantee.

**Groq's answer** — `response_format={"type": "json_object"}`. Guarantees **valid
JSON**. Says nothing about the shape.

`llm/client.py`'s docstring names the asymmetry and the response to it:

> **STRUCTURED OUTPUT IS NOT SYMMETRIC.** Gemini's `response_schema` guarantees
> the output parses into the Pydantic model. Groq offers only
> `response_format={"type":"json_object"}`, which guarantees valid JSON — not the
> requested shape. So the Groq path serialises the schema into the system prompt
> and validates the result against the model itself. **A schema miss on Groq is
> treated as a PROVIDER FAILURE, not a parse error**: it raises rather than
> handing a malformed DSL to `validate_dsl`.

The code:

```python
schema_json = json.dumps(schema.model_json_schema(), indent=2)
groq_system = (
    f"{system}\n\n"
    f"Return ONLY a JSON object conforming to this schema. "
    f"No markdown, no commentary.\n\n{schema_json}"
)
...
text = (resp.choices[0].message.content or "").strip()
schema.model_validate_json(text)   # shape guarantee Gemini gives for free
```

**"A schema miss is a provider failure, not a parse error."** That classification
decides behaviour: a parse error might be retried or repaired; a provider failure
raises `LLMUnavailable` and the caller refuses. Off-shape output never reaches
the validator.

---

### 4.4 The discovery: the response schema is part of the prompt

**This is the day's central idea.**

The instinct is that `response_schema=RouterResponse` is a *client-side* type
annotation — something the SDK uses to parse the reply. It is not.

**The schema is serialised and sent to the model.** On Gemini it becomes part of
the constrained-decoding grammar; on Groq it is literally pasted into the system
prompt (above). Either way, **the model sees it**.

Therefore:

> **Adding a field to a Pydantic model is a prompt change.**

`IMPLEMENTATION_DELTAS.md` §D — *"The response schema is part of the prompt"* —
records the measurement. When F14 changed `company: Optional[str]` to
`companies: list[str]`:

- Gemini's serialised schema grew by **+32 bytes**, and the node **lost its
  `nullable` flag** under a list type;
- Groq's shrank by **−39 bytes**.

`CLAUDE.md` states the consequence plainly:

> SHIPPED WITHOUT A ROUTER PROBE on instruction — it is a schema change, the
> schema is model input on both providers … and the classifier is therefore
> **UNMEASURED** across it. Treat any later route difference as possibly
> originating here.

That is `KU-002` in `KNOWN_UNKNOWNS.md`.

---

### 4.5 The corollary: "no prompt block" is not "invisible to the model"

`RouterResponse.company_mentioned` carries this comment:

```python
# NO prompt block instructs this field, deliberately. One was written and
# then removed: the model populates the field readily without it
# (measured 2026-08-12, 'Reliance Industries' returned three times out of
# three on gemini with no instruction present), so the instruction bought
# a coverage guarantee that was never needed while adding a prompt block
# among the entity fields the PATH CLASSIFICATION rules read.
```

And `CLAUDE.md`:

> **"No prompt block" is not "invisible to the model."** The response schema is
> sent on both providers, so declaring the field was itself an input change;
> removing the instruction did not take it back out.

**Read that twice.** Someone added a field, added an instruction, measured that
the instruction was unnecessary, and removed it — while correctly noting that
*the field itself is still an input*. Removing the instruction narrowed the
change; it did not undo it.

---

### 4.6 Why prompt edits are STOP-AND-ASK

`CLAUDE.md` §1, rule 5:

> **Prompt edits.** `SYNTHESIS_SYSTEM_PROMPT`, the DSL prompt, the router prompt.
> **Appended instructions have lost to earlier, more concrete rules in the same
> prompt three separate times.** These need reading, not testing.

**"Lost to"** means: the model followed the earlier, more specific rule and
ignored the appended one. A prompt is not a list of equally-weighted constraints
— placement and specificity matter, and a general instruction appended after a
concrete one is frequently dead text.

**"Need reading, not testing"** is the sharper claim. A prompt edit's effect is
distributed across every query; a smoke test on three questions cannot detect a
2 % shift in classification. Reading the prompt as a whole — asking *where does
this land relative to the rules already there?* — catches more than a small
sample does.

**The worked example**, from the F2 comment in `router.py`:

> Do not "fix" this by appending an instruction that contradicts the normalise
> rule two lines above it; **that is the shape that lost three times already.**

---

## 5. The actual LedgerMind files

```
backend/app/engines/router.py            ROUTER_SYSTEM_PROMPT + RouterResponse
backend/app/engines/quant_engine.py      DSL_SYSTEM_PROMPT (BUILT, not literal)
                                          + GeminiDSLResponse
backend/app/engines/response_generator.py SYNTHESIS_SYSTEM_PROMPT
backend/app/llm/client.py                 generate_structured / generate_text
docs/IMPLEMENTATION_DELTAS.md §D          the schema-is-prompt measurement
```

---

## 6. Deep walkthrough

### 6.1 `ROUTER_SYSTEM_PROMPT` — generated, not written

```python
_KNOWN_TICKERS = sorted({p.ticker for p in COMPANY_PROFILES})
_KNOWN_METRICS = sorted(METRIC_REGISTRY.keys())

ROUTER_SYSTEM_PROMPT = f"""You are the query router for LedgerMind...

companies:
  - Every Indian company whose OWN results the query asks about
  - Normalise each to a canonical ticker from this list: {_KNOWN_TICKERS}
  - Return a JSON array. One company -> one element. Two companies -> both,
    in the order named. No company -> []
  - NEVER null. The empty array is how "no company" is expressed
...
quantitative:
  - Query asks for a specific financial metric value
  - Known metrics: {_KNOWN_METRICS}
"""
```

**The ticker list and metric list are interpolated from the registries.** Add a
company to `COMPANY_REGISTRY` and the prompt updates itself.

**Why that matters.** A hand-typed list is a **second copy** of the registry —
exactly the drift that caused three shipped bugs (Day 10). Here the prompt is
*derived*, so it cannot disagree.

**And a trap it does not close** — `CAVEAT-018`:

> `_KNOWN_TICKERS` is larger than the corpus (SWIGGY, NYKAA, DELHIVERY,
> POLICYBAZAAR resolve with zero documents), so "unknown company" and "known
> company, no documents" are different refusals.

The prompt is consistent with the *registry*. The registry is not the *corpus*.

**Read the `companies` block as a piece of prompt engineering.** Four lines, and
each does work: what counts as a company ("whose OWN results"), how to normalise
(with the list), the shape (an array, ordered), and the null case stated twice —
positively (`No company -> []`) and negatively (`NEVER null`). That redundancy is
deliberate: the field is required with no default precisely so the model must
express "none" explicitly (Day 3).

---

### 6.2 `DSL_SYSTEM_PROMPT` — built by a function

```python
def _build_dsl_system_prompt() -> str:
    metric_lines = prompt_metric_lines()
    mapping_warnings = prompt_warnings()
    unavailable = {k: v for k, v in METRIC_REGISTRY.items() if not v["available"]}
    ...
DSL_SYSTEM_PROMPT = _build_dsl_system_prompt()
```

The comment states the reason:

> Metric lines and disambiguation warnings now come from the shared registry …
> instead of a hand-maintained ALIASES dict here — this was the exact spot where
> `profit_before_tax`'s PBT-vs-PAT warning previously had to be hand-kept in sync
> with `dsl_compiler.py`'s registry entry. Now both read from one place.

**Three sections of this prompt are worth reading as engineering:**

**(a) The unavailable-metric instruction — refusing beats substituting:**

```
- If the user asks for a metric in the UNAVAILABLE list, return that exact
  name. Do NOT substitute an available metric for it. Returning the
  unavailable name lets the system refuse honestly; substituting produces
  a confidently wrong answer to a question nobody asked.
```

The prompt explains *why* to the model. Whether that helps is unmeasured; what it
certainly does is tell the next human reader what the rule is for.

**(b) Direction words force `yoy_growth`:**

```
- If a query describes a metric CHANGING over time using direction words
  (declined, fell, dropped, decreased, grew, rose, increased, improved,
  worsened) you MUST use operation="yoy_growth", even when no year is named.
  A question about whether something "declined" asks about change between
  two periods — point_in_time returns a single value and cannot answer it.
```

An explicit vocabulary list plus the reasoning. This is a **concrete, early
rule** — the kind that, per §4.6, wins over appended general ones.

**(c) Comparison ordering:**

```
- For comparison operations: "entity" MUST be the company named FIRST in the
  query, and "comparison_entity" MUST be the company named SECOND.
  Example: "Compare Eternal's and Paytm's revenue" → entity="ETERNAL",
  comparison_entity="PAYTM" — NOT the reverse, regardless of which company
  the question focuses on afterward.
```

Rule, example, and the anticipated failure ("NOT the reverse"). Note the shape:
**state the rule, show it, name the wrong answer.**

---

### 6.3 The Groq fallback path, line by line

```python
schema_json = json.dumps(schema.model_json_schema(), indent=2)
groq_system = (
    f"{system}\n\n"
    f"Return ONLY a JSON object conforming to this schema. "
    f"No markdown, no commentary.\n\n{schema_json}"
)

resp = _get_groq().chat.completions.create(
    model=GROQ_MODEL,
    messages=[{"role": "system", "content": groq_system},
              {"role": "user", "content": user}],
    temperature=temperature, max_tokens=max_tokens,
    response_format={"type": "json_object"},
)
text = (resp.choices[0].message.content or "").strip()
schema.model_validate_json(text)
logger.info("Groq fallback served structured call | model=%s", GROQ_MODEL)
return LLMResult(text, "groq", GROQ_MODEL)
```

**STATE BEFORE.** Gemini failed with a fallback-eligible error (Day 19).

**`schema.model_json_schema()`** — Pydantic emits JSON Schema. `model_validate_json`
then checks the reply against the *same* model. One definition, two uses.

**"No markdown, no commentary."** Models wrap JSON in ```` ```json ```` fences.
Both callers *also* strip fences defensively:

```python
except json.JSONDecodeError:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
    result = json.loads(cleaned)
```

Belt and braces: instruct against it, and handle it anyway.

**`schema.model_validate_json(text)` — discard the result.** The call is made for
its **exception**, not its value. If it passes, the raw text is returned and the
caller parses it. Slightly wasteful, and it keeps `LLMResult.text` a raw string on
both paths, so the caller cannot tell which provider served it without reading
`.provider`.

**On failure:**

```python
except (ValidationError, json.JSONDecodeError) as e:
    logger.error("Groq fallback returned off-schema JSON: %s", e)
    raise LLMUnavailable(f"Groq output failed schema validation: {e}") from e
```

`LLMUnavailable` — the same exception as a total outage. **Off-shape output is
treated as no output.**

---

## 7. Data flow — a prompt, both ways

```
                    Pydantic model (RouterResponse)
                             │
              ┌──────────────┴───────────────┐
              ▼                              ▼
        GEMINI PATH                     GROQ PATH
              │                              │
   response_schema=RouterResponse   model_json_schema() → JSON text
              │                              │
   sent as decoding CONSTRAINT      PASTED INTO THE SYSTEM PROMPT
              │                              │
              ▼                              ▼
   ┌──────────────────────┐      ┌──────────────────────────┐
   │ system_instruction   │      │ system + schema text     │
   │ + user message       │      │ + user message           │
   │ + schema (grammar)   │      │ + json_object mode       │
   └──────────┬───────────┘      └──────────┬───────────────┘
              ▼                              ▼
      shape GUARANTEED             valid JSON, shape NOT guaranteed
              │                              │
              │                    schema.model_validate_json(text)
              │                              ├─ ok    → LLMResult
              │                              └─ fails → LLMUnavailable
              ▼                              ▼
                    LLMResult(text, provider, model)
                             │
                             ▼
              caller: json.loads, fence-strip fallback,
                      then its own field normalisation
```

**Both paths carry the schema to the model.** That is what "the schema is part of
the prompt" means mechanically.

---

## 8. Engineering decision — constrained output plus local validation

**Problem.** Get structured data from a model reliably, across two providers with
different capabilities.

**Decision.** Gemini `response_schema` as primary; Groq schema-in-prompt plus
local validation; off-shape treated as provider failure.

| Alternative | Why not |
|---|---|
| **Ask for JSON in the prompt only** | Works most of the time. "Most of the time" is what this system exists to eliminate |
| **Regex the JSON out of prose** | A parser for something you can constrain at generation |
| **Retry until parseable** | Doubles latency and cost against a 500/day quota, for a failure the provider already told you about |
| **Repair off-shape JSON** | Repairing a malformed DSL means guessing the model's intent — the class of guessing this system forbids |
| **Function/tool calling** | Broadly equivalent, provider-specific, and less portable across the two providers here |

**Trade-offs accepted.**

- **The schema is prompt surface.** Every field is an input change. This is the
  cost that produced `KU-002`.
- **Gemini and Groq answers are not equivalent artifacts** — one constrained
  during decoding, one validated after. Hence provider attribution (Day 19).
- **`max_tokens=200`** bounds structured output. A longer DSL would truncate —
  which would fail validation and become a provider failure. Fails closed.

**Current validity.** Sound. The open item is that a schema change is a prompt
change **and there is no probe requirement enforcing a measurement**. `KU-002`
records the one instance where that was skipped deliberately.

**At 10×.** The prompts are already derived from registries, so scale in
companies and metrics is handled. The pressure is on `_KNOWN_TICKERS` growing
beyond a reasonable prompt size, and on `CAVEAT-018` — a registry larger than the
corpus makes the "no documents" refusal more common.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| JSON wrapped in ```` ``` ```` fences | Model ignored the instruction — the fence-strip fallback handles it |
| `LLMUnavailable: Groq output failed schema validation` | Off-shape reply. **Treated as no output, deliberately** |
| Truncated JSON | `max_tokens` too low for the schema |
| Classification changed after a "type-only" edit | **The schema is prompt input.** `KU-002` |
| An appended instruction has no effect | It lost to an earlier, more concrete rule. Three prior instances |
| Same query, different route across runs | Not necessarily temperature — check provider and model first |
| Prompt lists a company with no documents | `CAVEAT-018` — the registry is larger than the corpus |

---

## 10. Hands-on experiment

### Experiment 1 — see what the model actually receives

```bash
docker compose exec -T backend python -c "
from app.engines.router import RouterResponse
import json
s = RouterResponse.model_json_schema()
print(json.dumps(s, indent=2))
print()
print('bytes on the wire:', len(json.dumps(s)))
print()
print('THIS IS SENT TO THE MODEL. It is not a client-side annotation.')
"
```

### Experiment 2 — measure a schema change, exactly as F14 did

```bash
docker compose exec -T backend python -c "
from pydantic import BaseModel
from typing import Optional
import json

class Before(BaseModel):
    company: Optional[str]
    fiscal_year: Optional[str]
    path: str

class After(BaseModel):
    companies: list[str]
    fiscal_year: Optional[str]
    path: str

b = json.dumps(Before.model_json_schema())
a = json.dumps(After.model_json_schema())
print('before:', len(b), 'bytes')
print('after :', len(a), 'bytes')
print('delta :', len(a) - len(b))
print()
print('company node before:', json.dumps(Before.model_json_schema()['properties']['company']))
print('companies node after:', json.dumps(After.model_json_schema()['properties']['companies']))
print()
print('Note the list type has NO nullable flag. That is the F14 measurement.')
"
```

### Experiment 3 — read the generated prompts

```bash
docker compose exec -T backend python -c "
from app.engines.router import ROUTER_SYSTEM_PROMPT, _KNOWN_TICKERS, _KNOWN_METRICS
print('tickers interpolated:', _KNOWN_TICKERS)
print('metrics interpolated:', len(_KNOWN_METRICS))
print()
print(ROUTER_SYSTEM_PROMPT)
"
```

Then find the tickers with **zero documents** — `CAVEAT-018`:

```bash
docker compose exec -T backend python -c "
import psycopg2, os
from app.engines.router import _KNOWN_TICKERS
c = psycopg2.connect(os.getenv('DATABASE_URL')); cur=c.cursor()
cur.execute('SET app.tenant_id = %s', (os.getenv('T'),))
cur.execute('SELECT DISTINCT company FROM financials')
have = {r[0] for r in cur.fetchall()}
print('in prompt :', _KNOWN_TICKERS)
print('in corpus :', sorted(have))
print('PROMPTED BUT EMPTY:', sorted(set(_KNOWN_TICKERS) - have))
c.close()"
```

### Experiment 4 — the DSL prompt is built, not written

```bash
docker compose exec -T backend python -c "
from app.engines.quant_engine import DSL_SYSTEM_PROMPT
print(DSL_SYSTEM_PROMPT[:2600])
"
```

Find the three rules from §6.2 and, for each, identify: the rule, the example,
and the named wrong answer.

### Experiment 5 — temperature 0 is not determinism

```bash
docker compose exec -T backend python -c "
from app.engines.router import _classify_query
q = 'Who grew revenue faster in FY26, Eternal or Paytm?'
for i in range(3):
    r = _classify_query(q)
    print(f'run {i+1}: path={r[\"path\"]:12} companies={r[\"companies\"]}')
print()
print('CLAUDE.md mandates three runs with provider and model printed,')
print('because temperature=0 reduces variance, it does not remove it.')
"
```

> **Quota:** three calls. Run once.

### Experiment 6 — off-shape output is a provider failure

```bash
docker compose exec -T backend python -c "
from app.engines.router import RouterResponse
from pydantic import ValidationError
bad = '{\"company\": \"ETERNAL\", \"path\": \"quantitative\"}'   # pre-F14 shape
try:
    RouterResponse.model_validate_json(bad)
except ValidationError as e:
    print('REJECTED:'); print(e)
    print()
    print('On the Groq path this raises LLMUnavailable — the same exception as a')
    print('total outage. Off-shape output never reaches validate_dsl.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/engines/router.py` and `backend/app/llm/client.py`:

1. `_KNOWN_TICKERS` and `_KNOWN_METRICS` are interpolated into the prompt. What
   drift does that prevent, and what does it *not* prevent?
2. Find `company_mentioned`'s comment. Why was an instruction written, shipped
   and then removed — and what did removing it **not** undo?
3. In `generate_structured`, what does the Groq path do that the Gemini path does
   not, and why is the difference a provider capability rather than a style
   choice?
4. `schema.model_validate_json(text)` discards its return value. Why is the call
   made at all?
5. Read `CLAUDE.md` §1 rule 5. What does "appended instructions have lost to
   earlier, more concrete rules" mean for how you would add a new rule?

---

## 12. Self-check questions

**Basic**
1. System vs user message?
2. What does `temperature=0.0` do — and not do?
3. What does Gemini's `response_schema` guarantee?
4. What does Groq's `json_object` guarantee?
5. Where do the three system prompts live?

**Code**
6. How is `ROUTER_SYSTEM_PROMPT`'s ticker list produced?
7. Why is `DSL_SYSTEM_PROMPT` built by a function?
8. What does the Groq path do with `model_json_schema()`?
9. What happens when Groq returns off-shape JSON?
10. What is the fence-strip fallback, and why does it exist when the prompt
    forbids fences?

**Why**
11. Why is the response schema part of the prompt?
12. Why is "no prompt block" not "invisible to the model"?
13. Why is off-shape output a provider failure rather than a parse error?
14. Why are prompt edits STOP-AND-ASK?
15. Why interpolate registries into prompts rather than typing the lists?

**Debugging**
16. A "type-only" change to a Pydantic model shifts classification on 3 % of
    queries. What happened?
17. You append a rule to the DSL prompt and it has no effect. Two hypotheses, and
    which does this project's history favour?
18. A route differs between two runs of the same query. What do you check before
    concluding anything?

**System design**
19. You must add a `confidence: float` field to `RouterResponse`. What must you do
    beyond writing the field?
20. `_KNOWN_TICKERS` is larger than the corpus. Describe the failure that creates
    and propose a fix that does not make the prompt lie.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **Prevents:** the prompt disagreeing with the registry — a hand-typed list is a
   second copy, and three shipped bugs came from exactly that split (Day 10).
   **Does not prevent:** the *registry* disagreeing with the **corpus**.
   `_KNOWN_TICKERS` includes SWIGGY, NYKAA, DELHIVERY and POLICYBAZAAR, which have
   zero documents — so the prompt truthfully lists companies the system cannot
   answer about. `CAVEAT-018`.
2. It was written to guarantee the field would be populated; measurement
   (2026-08-12) showed the model populates it readily without one — 'Reliance
   Industries' returned three times out of three. So the instruction bought a
   guarantee that was never needed while adding a prompt block **among the entity
   fields the PATH CLASSIFICATION rules read**, i.e. it risked disturbing
   classification for nothing. **Removing it did not undo declaring the field**:
   the schema is sent on both providers, so the field itself remains an input.
3. The Groq path **serialises the schema into the system prompt** and then
   **validates the reply locally**. It is a provider capability difference: Gemini
   constrains generation so the shape is guaranteed; Groq only guarantees valid
   JSON, so the shape must be requested in text and checked afterwards.
4. For its **exception**. It is the shape check. Passing means the reply conforms;
   failing raises `ValidationError`, which is converted to `LLMUnavailable`. The
   parsed object is discarded so that `LLMResult.text` stays a raw string on both
   paths and the caller's handling is identical.
5. That **placement and specificity matter more than presence**. A new rule
   appended after a concrete rule it contradicts is frequently dead text — that
   shape has lost three times. So: read the whole prompt, find where the new rule
   sits relative to existing ones, and if it conflicts, **edit the existing rule
   rather than appending a contradiction**. And treat it as needing reading, not a
   three-question smoke test.

### §12 — Basic

1. **System:** standing instructions, constant across calls. **User:** this
   specific request.
2. Always picks the most likely next token, minimising sampling randomness. It
   does **not** guarantee determinism — provider-side batching, routing and
   floating-point non-determinism can still vary the output.
3. That the output **parses into the supplied Pydantic model**. The shape is
   enforced during decoding.
4. **Valid JSON.** Nothing about the shape.
5. `ROUTER_SYSTEM_PROMPT` in `engines/router.py`; `DSL_SYSTEM_PROMPT` in
   `engines/quant_engine.py` (built by `_build_dsl_system_prompt()`);
   `SYNTHESIS_SYSTEM_PROMPT` in `engines/response_generator.py`. All module-level
   constants.

### §12 — Code

6. `_KNOWN_TICKERS = sorted({p.ticker for p in COMPANY_PROFILES})`, interpolated
   into an f-string at import.
7. Because its metric lines and disambiguation warnings come from the shared
   registry rather than a hand-maintained dict — the exact place where
   `profit_before_tax`'s PBT-vs-PAT warning previously had to be hand-synced with
   `dsl_compiler.py`.
8. Serialises it with `json.dumps(..., indent=2)` and appends it to the system
   prompt, prefixed by "Return ONLY a JSON object conforming to this schema. No
   markdown, no commentary."
9. `ValidationError` → caught → `raise LLMUnavailable(...)`. The same exception as
   a total outage, so the caller refuses rather than proceeding.
10. `re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL)` in both
    `router._classify_query` and `quant_engine._generate_dsl`. It exists because
    an instruction is a request, not a guarantee — models wrap JSON in fences
    anyway. Instruct against it *and* handle it.

### §12 — Why

11. Because it is **serialised and sent**: on Gemini as a decoding constraint, on
    Groq pasted into the system prompt. The model sees it either way, so declaring
    a field changes the model's input.
12. Because the block being absent only removes *instruction text*. The **field
    declaration** still travels in the schema. Removing the instruction narrowed
    the change; it did not undo it.
13. Because repairing a malformed DSL means guessing the model's intent, which is
    the class of guessing this system forbids — and because the classification
    decides behaviour: a parse error invites a retry, a provider failure makes the
    caller refuse. Off-shape output never reaches `validate_dsl`.
14. Because a prompt edit's effect is distributed across every query and cannot be
    detected by a small smoke test; because appended instructions have lost to
    earlier, more concrete rules three separate times; and because a full eval
    sweep costs a large fraction of the daily quota. So they need **reading**.
15. To avoid a second copy of a fact. A hand-typed list drifts from the registry
    silently, and the metric registry exists precisely because three such copies
    caused three shipped bugs.

### §12 — Debugging

16. **The schema is prompt input.** The type change altered the serialised schema
    the model receives — on Gemini it can change the decoding grammar (a list type
    loses `nullable`), on Groq it changes the pasted text. This is exactly F14 and
    `KU-002`. The correct response is a two-arm router probe, three runs per arm,
    provider and model printed — not a code hunt.
17. **(a)** The rule is genuinely being ignored because it lost to an earlier,
    more concrete rule; **(b)** the change did not reach the model (stale
    container, wrong environment). Check (b) first with the Day 1 pre-flight —
    but this project's history favours **(a)**: three separate instances of an
    appended instruction losing to an earlier concrete one.
18. **Provider and model, from the same response.** A Groq-served classification
    is not comparable to a Gemini-served one, and `llm_provider` / `llm_model` are
    on the admin response for exactly this reason. Only after those match is
    temperature or a code change worth considering — and then it is three runs,
    not two.

### §12 — System design

19. Beyond the field: **(a)** recognise it is a **prompt change**, and decide
    whether it needs a prompt block — remembering that adding one places text
    among the rules the classifier already reads. **(b)** Run a **two-arm router
    probe**, three runs per arm, with provider, model and schema byte-size printed
    — because the classifier is otherwise unmeasured across the change. **(c)**
    Thread it through `_classify_query`'s normalisation, `QueryState` if it is to
    be kept, and `role_filtered_response` if it is to be surfaced. **(d)** If it
    reaches `audit_log`, a migration. **(e)** Record the measurement in
    `IMPLEMENTATION_DELTAS.md`. The field is the smallest part of the work.
20. **The failure:** the prompt tells the model these are valid canonical tickers,
    so a question about Swiggy resolves cleanly and then finds zero rows — the
    system refuses with "no data for this period" rather than "we do not hold this
    company". Two different truths, one message, and the user cannot tell which.
    **A fix that does not make the prompt lie:** keep the registry as the
    normalisation vocabulary (it is correct that "bundl technologies" means
    SWIGGY), but derive the *prompt's* list from **companies actually present in
    the corpus** for that tenant, and let anything resolvable-but-absent take the
    existing `company_not_in_corpus`-style refusal with an accurate message. That
    keeps normalisation broad and the prompt honest. Note this is a functional
    change and a prompt change — both STOP-AND-ASK.

---

## 14. MUST REMEMBER

```text
- THE RESPONSE SCHEMA IS PART OF THE PROMPT. Adding a field is a prompt change
- "No prompt block" is NOT "invisible to the model"
- Gemini response_schema guarantees SHAPE; Groq json_object guarantees only
  valid JSON — so the Groq path pastes the schema and validates locally
- Off-shape output on Groq = LLMUnavailable = the same as a total outage
- temperature=0.0 everywhere structured; 0.2 for prose synthesis
- Prompts are BUILT from the registries, never hand-typed
- Appended instructions have lost to earlier concrete rules THREE times
- Prompt edits are STOP-AND-ASK. They need reading, not testing
```

## 15. MUST UNDERSTAND

```text
- Why a Pydantic model is simultaneously a validator and a prompt
- Why F14 left the classifier UNMEASURED, and why that is recorded as an
  unknown rather than assumed harmless
- Why classifying off-shape output as a PROVIDER failure (not a parse error)
  decides what happens next
- Why placement and specificity in a prompt matter more than presence
- Why deriving prompts from registries closes one drift and leaves another
  (registry vs corpus) open
```

---

## 16. This connects to

```text
Day 17 — what an LLM is
   ↓
Day 18 — how we ask it, and what the asking includes    ← you are here
   ↓
Day 19 — what happens when the provider fails, and how we record who answered
```

Forward references:

- `ROUTER_SYSTEM_PROMPT` and classification in full → **Day 36**
- `DSL_SYSTEM_PROMPT` and the eight fields → **Day 32**
- `SYNTHESIS_SYSTEM_PROMPT` and grounding → **Day 30**
- `KU-002` — the unmeasured F14 classifier → **Day 43**
- `CAVEAT-018` — registry larger than corpus → **Day 36**
- Provider attribution → **Day 19**
