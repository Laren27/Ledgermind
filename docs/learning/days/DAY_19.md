# Day 19 — The Shared LLM Client: Timeouts, Failover, Attribution

**Phase 5 · Weight: H (~120 min) · Prerequisites: Days 12, 18**

**Textbook: 10.6 "Async / Parallel Execution" — EXTENDS.** The textbook mentions
retries and timeouts in passing. This file is 444 lines of them, every one
traceable to a measured production failure.

---

## 1. Today's goal

By tonight you can:

- Explain why **a timeout is a precondition for a fallback**, and why fixing the
  two together was not a coincidence.
- Distinguish **transport-class** from **provider-class** failure, and say why
  transport is checked first.
- Explain "one attempt plus exactly one retry — never a ladder", and why a ladder
  is the wrong shape.
- Explain provider attribution by **precedence**, and why last-writer-wins
  under-reported degradation in a real eval gate.

---

## 2. Why now

Days 17 and 18 covered what the model is for and how it is asked. Today: what
happens when it does not answer. This closes Phase 5, and it is the last file
before retrieval (Days 20–29).

`llm/client.py` is also the best-documented file in the codebase. Read today as
much for *how it records reasoning* as for what it does.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Lazy singletons, no-default rule | Day 12 | `_resolve_gemini_model` |
| `record_llm_call` precedence | Day 3 | Today is the full argument |
| Structured output asymmetry | Day 18 | The fallback must re-validate |
| 500 vs 503 | Day 4 | Same distinction, different layer |

---

## 4. Concept lesson

### 4.1 The two founding defects

The module docstring opens by naming them:

> **1. UNBOUNDED TAIL LATENCY.** No Gemini call site set a timeout. Measured the
> same query three times: **3.07s / 120.0s (client gave up, call still running) /
> 3.00s.** Render logs confirmed a SINGLE call — "AFC remote call 1 is done" 78s
> after "AFC is enabled" — not SDK retry. Everything downstream completed in
> under a second. The request returned **200 and looked normal in the audit log**,
> which is the same silent-degradation class as the old `user_id="anonymous"` bug.
>
> **2. NO FALLBACK.** Blueprint §17 promises "Gemini rate-limited → route to
> Groq". Confirmed never implemented: `config.py` had a `groq_api_key` field and
> **zero call sites**.

**Then the sentence that makes this a design lesson rather than a bug list:**

> The two are causally linked, which is why they are fixed together: **a timeout
> converts an unbounded hang into a catchable exception at a bound we choose, and
> only then is there anything for a fallback to catch.** A fallback keyed on
> exceptions would never have fired on defect 1.

**Read that again.** Defect 2 was *unfixable* while defect 1 existed. You can
write a perfect `except` block and it will never execute against a call that
never returns. The order of repair was forced by the mechanism.

**Mental model.** A timeout is **the doorbell**. Without one, the visitor stands
outside forever and nobody in the house ever learns there is someone at the door
— so the plan for "what to do when someone arrives" is dead code.

---

### 4.2 Why one module and not three call sites

> Three call sites rather than one wrapper would mean three fallback ladders that
> drift apart — the same failure class as this project's **two formula copies and
> three metric registries**. One module, two entry points.

The same argument you met on Day 10 (the registry) and Day 15 (`classify_upsert`).
It recurs because it is this codebase's central structural principle:

> **When one fact or one decision must be identical in several places, do not
> copy it. Make one copy and give it several callers.**

---

### 4.3 The fallback trigger is deliberately narrow

> **FALLBACK TRIGGER IS DELIBERATELY NARROW.** Timeout, 429, and 5xx only. A
> parse error or empty response is **model behaviour Groq will likely reproduce**;
> retrying those just doubles latency before the same failure. One attempt per
> provider, no backoff — if Gemini's daily bucket is exhausted, sleeping inside a
> request cannot help.

Two distinct principles:

**(a) Fall back only on failures the *other* provider might not share.** A network
timeout is about the link. An empty response is about the *request* — and a second
model handed the same request will likely produce the same nothing.

**(b) Sleeping inside a request cannot fix a daily quota.** A per-minute limit
refills in seconds; a daily one does not refill at all. Blocking a user's request
to wait for a bucket that will not refill for hours is worse than failing.

**And the exclusion that matters most:**

```python
# Deliberately NOT here: 401/403/invalid-argument. A bad key or a
# malformed request is a config error, and serving those from the
# fallback would hide the real fault
```

**If a bad API key silently fell back to Groq, every answer would come from the
fallback and nothing would say why.** The system would work, degraded, forever.
Excluding auth errors makes a misconfiguration *loud*.

---

### 4.4 Transport-class versus provider-class

Added 2026-08-22, and the docstring names the evidence:

```python
_TRANSPORT_MARKERS = (
    "timeout", "timed out", "deadline", "connection", "connecterror",
    "network", "unreachable", "max retries", "remote end closed",
    "resolution",
)
_PROVIDER_MARKERS = (
    "429", "resource_exhausted", "rate limit", "rate_limit",
    "500", "502", "503", "504", "unavailable", "internal server error",
)
```

| Class | Meaning | Retry? |
|---|---|---|
| **transport** | the bound was hit, or the socket never opened | **yes, once** |
| **provider** | the server said something | **no** — trigger 1 covers the wait case |

**Why transport is checked first:**

```python
def _marker_class(exc):
    """
    Transport is checked FIRST. A message carrying both (an HTTPSConnection
    error that also names a status) is a transport failure that mentions a
    status, and the retry decision follows transport.
    """
```

**Why the distinction exists at all** — three measured events in one day:

```
PQ016  Gemini -> Groq   NameResolutionError [Errno -3]
PQ008  Gemini -> Groq   Read timed out (read timeout=20.0)
PQ020  Cohere -> ONNX   [Errno 111] Connection refused
```

Each a **single** transport failure with no retry, immediately switching provider
and **withholding a whole sweep on an integrity gate**.

And the exclusion is stated positively:

> **Rate limiting was POSITIVELY EXCLUDED, not merely unobserved:** zero 429s, no
> `resource_exhausted`, no `retryDelay` in any error, spacing ≥45s against a
> 5 RPM ceiling, and failures at scattered positions (16/20 and 8/20) with the
> provider serving normally either side.

**"Positively excluded, not merely unobserved."** Absence of evidence was not
treated as evidence of absence — five independent checks were run to rule out the
competing hypothesis.

**And the failure that `"resolution"` fixes:**

> Added 2026-08-22: without it a `NameResolutionError` stripped of its urllib3
> wrapper matched **NOTHING** and so neither retried nor fell back. PQ016
> matched only on "connection" (HTTPSConnectionPool) and "max retries" from that
> wrapper — **the fallback path was depending on an exception's incidental
> packaging.**

A whole failure mode hinged on a library's exception wrapper text.

---

### 4.5 One retry, never a ladder

```python
def _call_gemini(fn):
    try:
        return fn()
    except Exception as e:
        delay = _short_retry_delay(e)
        if delay is not None:
            logger.info("Gemini rate-limited — honouring server retryDelay %.1fs", delay)
            time.sleep(delay + 0.2)
            return fn()
        if _marker_class(e) == "transport":
            logger.warning("Gemini transport failure (%s: %s) — retrying once in %.1fs", ...)
            time.sleep(TRANSPORT_RETRY_BACKOFF_S)
            return fn()
        raise
```

> **STILL NOT A LADDER.** A second failure means the condition is not transient
> and the caller should fall through to Groq rather than keep a user waiting.
> What changed is only that a transport failure now gets the one retry a
> rate-limit already had.

**Why not exponential backoff?** Because this is **inside a user's request**. An
exponential ladder means a user waiting 1 s, then 2 s, then 4 s while a
*perfectly good fallback provider* sits idle. Ladders are right for background
jobs, wrong on a request path.

**`TRANSPORT_RETRY_BACKOFF_S = 1.0`**, and the comment refuses to overclaim:

> Fixed, short, and deliberately not tunable per call … **Not derived from any
> measurement** — see the docstring on `_call_gemini` for what was measured.

A constant that says "this number is not measured" is more honest than one that
implies it is.

---

### 4.6 RPM versus daily quota

```python
# Gemini enforces BOTH a per-minute and a per-day quota, and returns 429 for
# either. They need opposite handling: an RPM limit refills in seconds and the
# server tells us exactly how long to wait, so falling straight to Groq there
# abandons the better model — and a Groq-served answer is not eval-comparable
# to a Gemini one. A daily limit does not refill, so sleeping is pointless.
#
# Confirmed live 2026-07-29: a 429 arrived with retryDelay 2s while the daily
# counter stood at 277/500. Google's error body labels the quotaId
# "...PerDay..." in BOTH cases, so the id cannot be trusted to tell them
# apart -- the retry delay can.
```

**The provider's own error metadata is wrong**, and the code says so with the
measurement that proves it. The workaround uses the one field that *is*
discriminating:

```python
_RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'|retry in (\d+(?:\.\d+)?)s", re.I)
MAX_RPM_RETRY_WAIT_S = 5.0

def _short_retry_delay(exc):
    """Server-advised wait, if it is short enough to be worth honouring."""
```

A short advised delay → per-minute → wait. A long one or none → treat as
exhausted → fall back.

---

### 4.7 The timeout, and the number that was wrong once

```python
TIMEOUT_STRUCTURED_MS = int(os.getenv("TIMEOUT_STRUCTURED_MS", "20000"))
```

The comment is a model of how to record a constant:

> 2026-08-13: structured raised 8_000 → 20_000. **The claim is about the TAIL,
> not the median** — two eight-call samples an hour apart gave medians of ~5.7s
> and ~2.9s, so neither is a p50 and the distribution is wide and unstable. What
> both samples agree on: **calls routinely exceed 8s.** Sample A (bound at 8s):
> 3 of 8 timed out at the ceiling. Sample B (bound at 20s): 8 of 8 served,
> including calls at 9511 and 9555 ms that the old bound would have killed. A
> sweep between the two **lost 15 of 48 answers to the fallback** and was withheld
> on the provider gate.
>
> **The tight bound was also SLOWER than a generous one:** a timeout costs the
> full 8s and then a Groq call (~8.8s observed) versus ~5.7s served correctly.
>
> Env-overridable because **this constant has been wrong once** and the right
> value is empirical. Default lives HERE, not in compose — a deploy without the
> var must get the correct value, and two answers to "what is the timeout" is the
> defect this module criticises in `GEMINI_MODEL`.

Five things at once: what changed, the evidence, the honest admission that
neither sample is a p50, the counter-intuitive finding (**tighter was slower**),
and the placement rule.

---

### 4.8 `GEMINI_MODEL` has no default

```python
def _resolve_gemini_model() -> str:
    model = os.getenv("GEMINI_MODEL")
    if not model:
        raise RuntimeError(
            "GEMINI_MODEL environment variable not set. Refusing to guess: the "
            "model that served a call is recorded as evidence on every audit "
            "row and asserted by the eval gate, so a defaulted value would "
            "silently misattribute every downstream number."
        )
    return model
```

Day 12's rule, with its receipt: two full eval sweeps were reported under a model
that never served a single call. **The crash costs five minutes; the wrong default
cost ~60 calls and two unusable result files.**

**And note it is resolved at *call* time, not import time** — so a missing
variable fails in the entry points that actually make LLM calls, rather than
crashing the Celery worker that merely imports the module transitively.

---

## 5. The actual LedgerMind file

```
File:        backend/app/llm/client.py (444 lines)
Purpose:     The ONE place an LLM call is made
Why:         Three call sites would mean three fallback ladders that drift
Who imports: engines/router.py, engines/quant_engine.py,
             engines/response_generator.py
Entry points: generate_text(...)  → LLMResult      (prose, temp 0.2)
             generate_structured(...) → LLMResult  (JSON, temp 0.0)
Data in:     system prompt, user message, optionally a Pydantic schema
Data out:    LLMResult(text, provider, model)
Raises:      LLMUnavailable — both providers failed. Callers MUST NOT retry
```

---

## 6. Deep walkthrough — `generate_structured`

```python
def generate_structured(system, user, schema, temperature=0.0,
                        max_tokens=200, timeout_ms=TIMEOUT_STRUCTURED_MS) -> LLMResult:
    try:
        gemini_model = _resolve_gemini_model()
        client = _get_gemini(timeout_ms)
        resp = _call_gemini(lambda: client.models.generate_content(
            model=gemini_model, contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=temperature,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        ))
        return LLMResult(resp.text.strip(), "gemini", gemini_model)
    except Exception as e:
        if not _should_fall_back(e):
            logger.error("Gemini structured call failed (no fallback): %s", e)
            raise LLMUnavailable(str(e)) from e
        logger.warning("Gemini structured call failed [class=%s] (%s) — falling back to Groq",
                       _marker_class(e), e)
    # ... Groq path (Day 18) ...
```

**STATE BEFORE.** A prompt, a user message, a schema.

**`_resolve_gemini_model()`** — raises if unset. Inside the `try`, so the raise
becomes `LLMUnavailable` via the no-fallback branch. A configuration error does
not silently reach Groq.

**`_get_gemini(timeout_ms)` rebuilds the client every call**, and the comment
explains:

> Timeout is a per-client property in this SDK, and the two entry points need
> different bounds, so the client is rebuilt when the bound changes. Construction
> is cheap (no connection is opened here).

This is also where `CAVEAT-020` lives — `global _gemini_client` is declared and
never assigned, so the "singleton" is dead. Harmless, because construction opens
no socket, and misleading enough to be recorded.

**`_call_gemini(lambda: ...)`** — the call is wrapped in a closure so the retry
can invoke it again identically. Not a partial re-run: the same `fn()`.

**`resp.text.strip()`** — the raw string. Parsing is the caller's job.

**The `except` block's structure is the whole design:**

```python
if not _should_fall_back(e):        # config error, parse error, empty
    raise LLMUnavailable(...)       # ← do NOT try Groq
logger.warning("... [class=%s] ...", _marker_class(e), e)
# ← fall through to Groq
```

**`_marker_class(e)` in the log line** exists because of this:

```python
"""
Exists because _should_fall_back substring-matches a stringified
exception, so a 429 and a DNS failure produced an IDENTICAL fallback log
line. On 2026-08-21 PQ016 was distinguishable from PQ008 only because
their exception text happened to differ; nothing recorded the class.
"""
```

**Two different failures produced identical log lines**, and the only reason an
investigator could tell them apart was an accident of wording. Now the class is
logged explicitly.

**STATE AFTER (success).** `LLMResult(text, "gemini", "gemini-3.1-flash-lite")`.

**STATE AFTER (both failed).** `LLMUnavailable` — and the docstring is emphatic:

```python
class LLMUnavailable(RuntimeError):
    """Both providers failed. Callers must not retry — see module docstring."""
```

**Why callers must not retry.** Both providers have been tried; a third attempt
costs a user's latency and two quota units for the same failure. `quant_engine`
obeys it explicitly:

```python
except LLMUnavailable as e:
    # BREAK, not continue. The self-healing loop exists to repair BAD
    # DSL; "no provider answered" is not a DSL defect, and a repair
    # hint cannot fix it. Retrying here burns the single remaining
    # attempt on a call that will fail identically -- the same
    # conflation as the CRAG break/continue bug, inverted.
    return None, attempts, f"No LLM provider was available: {e}", None
```

---

### 6.1 Attribution — the second half of the story

`state.py`'s `record_llm_call` (Day 3) is the consumer of `LLMResult`, and its
header records two production failures:

> - `llm_provider` was set by whichever call **last SUCCEEDED**. The synthesis
>   floor returns `provider=None`, which overwrote nothing, so floor responses
>   logged as "gemini". Measured 2026-07-31: the eval provider gate reported
>   **11/45 non-Gemini when the true figure was ≥13**.
> - `--model` was only ever a **label**. Nothing in the pipeline recorded what
>   actually served the call.

```python
_PROVIDER_TAINT = {"gemini": 0, "groq": 1}
```

> **PRECEDENCE, NOT LAST-WRITER-WINS.** A semantic query makes two calls (router,
> synthesis). If either is served by the fallback, the ANSWER is a fallback
> artifact regardless of call order. So attribution only ever moves in the
> direction of "more degraded" within a single query.

**And `clear_llm_attribution`** for the synthesis floor:

> Leaving the router's earlier attribution in place would report a **total
> outage** as a normally-served answer.

**Three mechanisms, one principle: the record must never overstate what
happened.**

---

## 7. Data flow

```
caller: generate_structured(system, user, schema)
   │
   ├─ _resolve_gemini_model()      unset → RuntimeError → LLMUnavailable
   ├─ _get_gemini(timeout_ms)      client rebuilt (cheap, no socket)
   │
   ▼
_call_gemini(fn)
   ├─ fn() ──────────────────────────────────► SUCCESS
   └─ Exception
        ├─ _short_retry_delay(e) ≤ 5s?  → sleep(delay+0.2), fn() once
        ├─ _marker_class(e)=="transport"? → sleep(1.0), fn() once
        └─ else raise
   │
   ├─ SUCCESS ──► LLMResult(text, "gemini", model)
   │
   └─ FAILURE
        ├─ _should_fall_back(e) FALSE  (401/403/parse/empty)
        │     └─► LLMUnavailable — NO Groq attempt
        │
        └─ TRUE → log with [class=transport|provider]
              │
              ▼
        GROQ PATH  (Day 18)
              ├─ schema pasted into system prompt
              ├─ json_object mode
              ├─ model_validate_json(text)
              │     ├─ ok    → LLMResult(text, "groq", GROQ_MODEL)
              │     └─ fails → LLMUnavailable
              └─ any other error → LLMUnavailable
   │
   ▼
caller: record_llm_call(state, result)
   └─ _PROVIDER_TAINT: worst provider seen WINS
        │
        ▼
   audit_log.llm_provider / llm_model     (migration 014)
        │
        ▼
   admin response + eval provider gate
```

---

## 8. Engineering decision — ED-011 and ED-012

**Problem.** A free-tier LLM that hangs, rate-limits and fails, on a request path,
with a second provider available — and a record that must not overstate what
happened.

**Decision.** One client module; timeouts on every call; a narrow
exception-keyed fallback; one retry, never a ladder; attribution by precedence.

| Alternative | Why not |
|---|---|
| **No fallback, just fail** | Blueprint §17 promised one, and a free tier exhausts predictably |
| **Retry the same provider with backoff** | Cannot fix a daily quota, and keeps a user waiting while a good provider idles |
| **Fall back on every error** | A bad API key would silently serve from the fallback forever |
| **A general retry library (tenacity)** | Would give you a ladder by default, which is the wrong shape here |
| **Last-writer-wins attribution** | Measured to under-report degradation in a real eval gate |

**Trade-offs accepted.**

- **A Groq answer is not a Gemini answer.** Different model, different
  constraints. Hence attribution and the eval provider gate.
- **20s per call before falling back** during a genuine Gemini outage. Explicitly
  accepted: *"Correctness over latency; outages are rare."*
- **Substring matching on stringified exceptions** — fragile, and the code says
  so: *"a missed match degrades to 'no fallback' (the current behaviour) rather
  than to a wrong answer."* It fails toward the old behaviour.

**Current validity.** Strong. The residual risk is the marker lists: `"resolution"`
had to be added after a real miss, so the lists are **empirically grown**, not
exhaustive.

**At 10×.** A per-tenant rate limiter (specified in the blueprint, **not built**),
a circuit breaker so a sustained Gemini outage stops paying 20s per call, and a
paid tier removing the daily ceiling.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `RuntimeError: GEMINI_MODEL not set` | Deliberate. A default would misattribute every number |
| Every answer served by Groq | A bad `GEMINI_API_KEY` — and 401 is **excluded** from fallback, so it raises rather than hiding |
| `LLMUnavailable: Both providers failed` | Genuine double outage. Callers must not retry |
| Route differs between runs | Check `llm_provider` first — a Groq classification is not comparable |
| Sweep withheld on the provider gate | Mixed providers within one run |
| An answer logged "gemini" but degraded | Historic: last-writer-wins, or the floor not clearing attribution |
| A single dropped packet dropped a whole row | Historic: no transport retry (PQ016/PQ008/PQ020) |
| A fallback log line that does not say why | Historic: `_marker_class` not logged |

---

## 10. Hands-on experiment

> **Quota:** several of these make real calls. 5 RPM / 500 per day, shared. Run
> once, read carefully.

### Experiment 1 — the classifier, and why order matters

```bash
docker compose exec -T backend python -c "
from app.llm.client import _marker_class, _should_fall_back
cases = [
  TimeoutError('Read timed out (read timeout=20.0)'),
  Exception('[Errno -3] Temporary failure in name resolution'),
  Exception('HTTPSConnectionPool: Max retries exceeded'),
  Exception('429 RESOURCE_EXHAUSTED'),
  Exception('503 Service Unavailable'),
  Exception('401 Unauthorized: API key not valid'),
  Exception('Expecting value: line 1 column 1'),
  Exception('HTTPSConnectionPool ... 503 from upstream'),
]
for e in cases:
    print(f'{_marker_class(e) or \"none\":9} fallback={str(_should_fall_back(e)):5}  {e}')
print()
print('Last row carries BOTH markers -> classified transport, because')
print('transport is checked first and the retry decision follows transport.')
"
```

### Experiment 2 — the no-default rule

```bash
docker compose exec -T -e GEMINI_MODEL= backend python -c "
from app.llm.client import _resolve_gemini_model
try:
    _resolve_gemini_model()
except RuntimeError as e:
    print('RAISED:'); print(' ', e)
"
```

Read the message. It explains **why** refusing beats guessing.

### Experiment 3 — the retry-delay parser

```bash
docker compose exec -T backend python -c "
from app.llm.client import _short_retry_delay, MAX_RPM_RETRY_WAIT_S
print('MAX_RPM_RETRY_WAIT_S =', MAX_RPM_RETRY_WAIT_S)
for msg in [\"429 ... {'retryDelay': '2s'} ...\",
            \"429 ... {'retryDelay': '38s'} ...\",
            '429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDay',
            'please retry in 3.5s']:
    print(f'  {str(_short_retry_delay(Exception(msg))):6}  <- {msg[:52]}')
print()
print('2s  -> per-minute, wait.   38s -> treat as exhausted, fall back.')
print('The quotaId says PerDay in BOTH cases. Only the delay discriminates.')
"
```

### Experiment 4 — a real call, attributed

```bash
docker compose exec -T backend python -c "
from app.llm.client import generate_text
r = generate_text(system='Reply with one word.', user='Say OK', max_tokens=10)
print('text    :', r.text.strip())
print('provider:', r.provider)
print('model   :', r.model)
"
```

### Experiment 5 — precedence, not last-writer-wins

```bash
docker compose exec -T backend python -c "
from app.engines.state import make_initial_state, record_llm_call, clear_llm_attribution
from types import SimpleNamespace
s = make_initial_state(query='q', tenant_id='t', user_id='u', request_id='r')

record_llm_call(s, SimpleNamespace(provider='gemini', model='flash-lite'))
print('after router (gemini)   :', s['llm_provider'], s['llm_model'])
record_llm_call(s, SimpleNamespace(provider='groq',   model='gpt-oss-120b'))
print('after synthesis (groq)  :', s['llm_provider'], s['llm_model'], '<- moved to WORSE')
record_llm_call(s, SimpleNamespace(provider='gemini', model='flash-lite'))
print('a later gemini call     :', s['llm_provider'], '<- did NOT move back')
clear_llm_attribution(s)
print('after synthesis floor   :', s['llm_provider'], '<- None: no model served this')
"
```

### Experiment 6 — force a fallback

```bash
docker compose exec -T -e GEMINI_API_KEY=deliberately-invalid backend python -c "
from app.llm.client import generate_text, LLMUnavailable
try:
    r = generate_text(system='Reply with one word.', user='Say OK', max_tokens=10)
    print('served by:', r.provider, r.model)
except LLMUnavailable as e:
    print('LLMUnavailable:', str(e)[:200])
    print()
    print('A 401 is DELIBERATELY excluded from the fallback trigger, so a bad')
    print('key surfaces as a failure instead of hiding behind Groq forever.')
"
```

Then watch the log line:

```bash
docker compose logs --tail 20 backend | grep -i "falling back\|no fallback"
```

Note the `[class=...]` tag — the thing that did not exist on 2026-08-21.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/llm/client.py`:

1. The module docstring names two defects and says they were "causally linked".
   Explain the link, and why defect 2 could not be fixed alone.
2. Find `_TRANSPORT_MARKERS`. Why was `"resolution"` added, and what was the
   fallback path depending on before it?
3. `_marker_class` checks transport first. What kind of message forces that
   ordering to matter?
4. `TIMEOUT_STRUCTURED_MS` was raised from 8s to 20s. Give the counter-intuitive
   finding, and say why the default lives in the code rather than in compose.
5. `LLMUnavailable`'s docstring says callers must not retry. Find the caller that
   obeys this explicitly, and quote the reason it gives.

---

## 12. Self-check questions

**Basic**
1. What are the two entry points?
2. What is the fallback provider?
3. How many retries per provider?
4. What does `LLMUnavailable` mean?
5. What is `TIMEOUT_STRUCTURED_MS`?

**Code**
6. What does `_should_fall_back` match on, and why is that fragile-but-safe?
7. Which errors are deliberately excluded from the fallback trigger?
8. What does `_short_retry_delay` return, and what decides its bound?
9. Why is the Gemini client rebuilt per call?
10. What does `_PROVIDER_TAINT` encode?

**Why**
11. Why is a timeout a precondition for a fallback?
12. Why one module rather than three call sites?
13. Why is a ladder the wrong shape here?
14. Why exclude 401/403 from fallback?
15. Why precedence rather than last-writer-wins?

**Debugging**
16. Every answer is served by Groq. Two candidate causes, and how to tell them
    apart.
17. An eval sweep is withheld on the provider gate. What happened, and is it a
    defect in the answers?
18. A query's route differs between two runs. What do you check before forming a
    code theory?

**System design**
19. A sustained Gemini outage means every query pays 20s before falling back.
    Design the fix and name what it costs.
20. `_should_fall_back` substring-matches stringified exceptions and had to grow
    `"resolution"` after a real miss. Propose something more robust, and say why
    the current design is still defensible.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Without a timeout, a hung call **never returns and never raises** — so an
   exception-keyed fallback has nothing to catch and is dead code. A timeout
   converts an unbounded hang into a catchable exception at a bound you choose,
   and only then does a fallback become reachable. Defect 2 was unfixable while
   defect 1 existed.
2. Because a `NameResolutionError` **stripped of its urllib3 wrapper matched
   nothing**, so it neither retried nor fell back. Before the fix, PQ016 matched
   only on `"connection"` (from `HTTPSConnectionPool`) and `"max retries"` — i.e.
   **the fallback path was depending on an exception's incidental packaging**
   rather than on its actual class.
3. A message carrying **both** classes — an `HTTPSConnectionPool` error that also
   names a status code. Without an explicit order it could be classified either
   way; the code declares it a transport failure that mentions a status, so the
   retry decision follows transport (and it gets its one retry).
4. **The tighter bound was slower.** A timeout costs the full 8s *and then* a
   Groq call (~8.8s observed), versus ~5.7s served correctly — so the "safe" tight
   bound produced worse latency as well as worse answers. The default lives in the
   code because *"a deploy without the var must get the correct value, and two
   answers to 'what is the timeout' is the defect this module criticises in
   `GEMINI_MODEL`."*
5. `quant_engine._generate_dsl`: *"**BREAK, not continue.** The self-healing loop
   exists to repair BAD DSL; 'no provider answered' is not a DSL defect, and a
   repair hint cannot fix it. Retrying here burns the single remaining attempt on
   a call that will fail identically — the same conflation as the CRAG
   break/continue bug, inverted."*

### §12 — Basic

1. `generate_text` (prose, temp 0.2, 20s) and `generate_structured` (JSON, temp
   0.0, schema-constrained).
2. Groq, `openai/gpt-oss-120b` by default.
3. One attempt plus **exactly one** retry on Gemini (rate-limit or transport).
   Groq gets one attempt.
4. Both providers failed. Callers must not retry.
5. 20,000 ms, env-overridable, defaulted in the code.

### §12 — Code

6. On the **stringified exception** (`f"{type(exc).__name__}: {exc}".lower()`).
   Fragile because it depends on message text — and safe because *"a missed match
   degrades to 'no fallback' (the current behaviour) rather than to a wrong
   answer."* It fails toward the pre-existing behaviour.
7. 401 / 403 / invalid-argument, and anything not in the marker lists (parse
   errors, empty responses). A bad key or malformed request is a **config error**;
   serving it from the fallback would hide the real fault.
8. The server-advised wait in seconds, but **only if `0 < delay ≤ 5.0`**
   (`MAX_RPM_RETRY_WAIT_S`); otherwise `None`. The bound is what discriminates a
   per-minute limit (refills in seconds) from a daily one (does not refill).
9. The timeout is a **per-client property** in this SDK and the two entry points
   need different bounds, so the client is rebuilt when the bound changes.
   Construction is cheap — no connection is opened.
10. Degradation order: `{"gemini": 0, "groq": 1}`. Higher is worse, and
    attribution only ever moves toward worse.

### §12 — Why

11. See §11 Q1.
12. Because three call sites would mean **three fallback ladders that drift
    apart** — the same failure class as the two formula copies and three metric
    registries.
13. Because it runs **inside a user's request**. An exponential ladder makes the
    user wait 1s, 2s, 4s while a working fallback provider sits idle. A second
    failure means the condition is not transient, so falling through is both
    faster and more informative. Ladders are for background jobs.
14. Because a bad API key would then be served silently by Groq forever — the
    system would work, degraded, with nothing saying why. Excluding auth errors
    makes a misconfiguration loud.
15. Because a semantic query makes two calls, and if **either** is served by the
    fallback the answer is a fallback artifact regardless of order.
    Last-writer-wins was measured under-reporting degradation: the eval provider
    gate reported 11/45 non-Gemini when the true figure was ≥13.

### §12 — Debugging

16. **(a)** `GEMINI_API_KEY` is invalid — but note 401 is *excluded* from
    fallback, so this would raise `LLMUnavailable` rather than serve from Groq;
    if you are genuinely getting Groq answers it is not a 401. **(b)** Gemini is
    timing out or rate-limited on every call. **Tell them apart** with the
    backend log: the fallback line carries `[class=transport|provider]`, and the
    no-fallback line says "(no fallback)". Also check `printenv GEMINI_MODEL`
    (unset raises before either).
17. The run contained answers served by **different providers**, so the results
    are not comparable — a Groq-served answer is a materially different artifact
    from a Gemini one. **It is not necessarily a defect in the answers**: PQ020's
    documented case was a single refused socket that dropped one row to the ONNX
    reranker, and on a clean re-run it passed. The gate withholds the *sweep*, not
    a judgement on the answers.
18. **`llm_provider` and `llm_model`, from the same response** (admin tier). A
    Groq-served classification is not comparable to a Gemini one, and this is the
    single most common false-regression cause in this project. Only after those
    match do temperature, prompt or code changes become worth considering — and
    then it is **three runs**, not two, with provider and model printed per run.

### §12 — System design

19. A **circuit breaker**: after N consecutive Gemini failures within a window,
    mark it open and route directly to Groq for a cooldown, then probe with a
    single request. **Costs:** shared mutable state across requests (and, with
    multiple workers, *per worker* unless externalised — reintroducing the state
    JWTs and lazy singletons were chosen to avoid); a new failure mode where the
    breaker is stuck open after Gemini recovers; and an attribution wrinkle, since
    breaker-routed calls never attempt Gemini and must still record `groq`
    honestly. Given outages are rare and the module explicitly chooses
    "correctness over latency", this is a *nice to have*, not a must.
20. **More robust:** key on **exception types** (`httpx.TimeoutException`,
    `socket.gaierror`, `google.api_core.exceptions.ResourceExhausted`) rather than
    message text, with the substring list kept only as a last-resort fallback.
    **Why the current design is still defensible:** the module says it explicitly —
    *"the SDK surfaces status codes inconsistently across error types, and a
    missed match degrades to 'no fallback' (the current behaviour) rather than to
    a wrong answer."* Type-based matching couples you to two SDKs' exception
    hierarchies across two providers, and those change; a missed *type* would fail
    the same way a missed *string* does. The honest position is that the marker
    lists are **empirically grown, not exhaustive** — which is why `"resolution"`
    was added — and that the failure direction is safe.

---

## 14. MUST REMEMBER

```text
- A TIMEOUT IS A PRECONDITION FOR A FALLBACK. No timeout → the except never runs
- Timeouts: structured 20s (env-overridable), text 20s, Groq 20s
- ONE attempt plus EXACTLY ONE retry. Never a ladder — this is a request path
- Two retry triggers: server-advised retryDelay ≤5s, and transport-class failure
- Transport is checked FIRST when a message carries both classes
- 401/403 are DELIBERATELY excluded — a bad key must be loud
- LLMUnavailable = both providers failed. CALLERS MUST NOT RETRY
- Attribution is PRECEDENCE: the worst provider seen wins, never last-writer
- GEMINI_MODEL has NO default and raises at CALL time
```

## 15. MUST UNDERSTAND

```text
- Why two defects were causally linked and had to be fixed together
- Why an RPM limit and a daily limit need OPPOSITE handling, and why the
  provider's own quotaId cannot tell them apart
- Why "positively excluded, not merely unobserved" is a higher standard than
  "we didn't see any 429s"
- Why the tighter timeout was also SLOWER — and why that is only visible if you
  measure the tail rather than the median
- Why a constant that says "not derived from any measurement" is more honest
  than one that implies it is
- Why substring matching on exceptions is fragile AND safe: it fails toward
  the previous behaviour, never toward a wrong answer
```

---

## 16. This connects to

```text
Day 17 — what an LLM is
Day 18 — how we ask it
   ↓
Day 19 — what happens when it does not answer     ← END OF PHASE 5
   ↓
Day 20 — the other half: retrieval. Embeddings and cosine similarity
```

Forward references:

- `_cohere_with_retry` — the same shape, different provider → **Day 28**
- The synthesis floor and `clear_llm_attribution` → **Day 30**
- `LLMUnavailable` breaking the DSL loop → **Day 32**
- The eval provider gate → **Day 43**
- `llm_provider` / `llm_model` in the audit row → **Day 44**
- `CAVEAT-020` (`_get_gemini`'s unassigned global) → recorded, not fixed
