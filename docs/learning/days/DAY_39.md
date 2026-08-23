# Day 39 — State, Effects, and the SSE Consumer

**Phase 11 · Weight: H (~120 min) · Prerequisites: Days 38, 6**

**Textbook: no citation.** The textbook has no client chapter. It also has no
equivalent of today's central lesson, which is that **a client retry is a
second full pipeline run**, and that fact is what decides the retry policy.

---

## 1. Today's goal

By tonight you can:

- Name **all thirteen** pieces of state in `page.tsx` and say why each is there
  rather than inside a child.
- Explain **state lifting**, using the one case where the repository documents
  its own reason for lifting.
- Explain why `handleSubmit` collects trace events into a **local array** as well
  as into state, and what breaks without it.
- Read `submitQueryStreaming` line by line: why `fetch` + `ReadableStream` rather
  than `EventSource`, how SSE frames are re-assembled across chunk boundaries,
  and how the four error classes are decided.
- State the retry policy in one sentence and **justify it in cost**, not in
  taste.
- Explain what `api.retry.guard.ts` is, why it exists in a repository with no
  test runner, and what its **inline negative controls** are guarding against.
- Read `ExecutionTrace` and explain how it distinguishes *pending* from
  *skipped*.

---

## 2. Why now

Two prerequisites, and both are load-bearing.

**Day 38** gave you components, props and the client boundary. Every hook below
is only legal inside that boundary, and `page.tsx` is where it starts.

**Day 6** gave you the *server* side of SSE: `api/query.py`'s `event_stream()`,
the 15-second heartbeat, the unbounded queue, and the deliberate decision never
to cancel the graph task on client disconnect. **Today is the other end of that
same socket** — and that one decision from Day 6 is what makes today's retry
policy a cost question rather than a style question:

```python
finally:
    # Never cancel `task`. If the client vanished mid-query the
    # pipeline must still finish so audit_writer_node writes its row.
```

The pipeline does not stop when you stop listening. So a client that retries has
not "tried again" — it has started a **second** pipeline.

---

## 3. Concepts you must know first

| Concept | From | Why it is needed today |
|---|---|---|
| Client boundary, `"use client"` | Day 38 | Hooks are illegal above it |
| Props flow down, one direction | Day 38 | State lifting is the answer to "then how does it flow up?" |
| SSE frames, `event:` / `data:` | Day 6 | The parser below assumes you know the wire format |
| The graph task is never cancelled | Day 6 | The entire retry argument rests on this |
| Gemini's 500/day, two calls per semantic question | Day 19, `CLAUDE.md` §5 | The unit the retry cost is denominated in |
| `audit_log` is append-only | Day 3, 44 | Why a duplicate run leaves a permanent duplicate row |

---

## 4. Concept lesson

### 4.1 State — the problem, before the API

A component is a function of its props (Day 38). Call it again with the same
props and you get the same markup. That is a good property and it is also,
by itself, useless: nothing about a pure function remembers that a query is
in flight, or that you are on page 3.

**State is the value that survives a re-render and causes one when it changes.**

```tsx
const [isLoading, setIsLoading] = useState(false);
```

Three things happen on that line:

1. `isLoading` is the value **for this render**. It never changes mid-render.
2. `setIsLoading` is a request: *"make the next render use this value."*
3. React schedules that render.

**The consequence people get wrong**, and which this repository has a comment
about: `setIsLoading(true)` does **not** change `isLoading` on the following
line. The current render's `isLoading` is a fixed value. You will see this
matter in §4.4.

---

### 4.2 The thirteen states of `page.tsx`

```bash
grep -n "useState" frontend/app/page.tsx
```

| # | State | Type | Why it is here and not in a child |
|---:|---|---|---|
| 1 | `session` | `StoredSession \| null` | Decides login vs app. Nothing below can decide that |
| 2 | `sessionChecked` | `boolean` | Distinguishes "not logged in" from "have not looked yet" (§4.3) |
| 3 | `pages` | `Page[]` | The stack of working papers. Every answer ever produced this session |
| 4 | `currentPageIndex` | `number` | Which sheet is on top |
| 5 | `error` | `string \| null` | A failed submit, rendered into the sheet |
| 6 | `isLoading` | `boolean` | Read by `QueryDock` (button label) **and** the sheet (trace vs body) |
| 7 | `traceEvents` | `TraceEvent[]` | The **live** stream for the in-flight query |
| 8 | `revisions` | `Record<string, number>` | How many times each query text has been asked → the header's `REV: 02` |
| 9 | `activeView` | `ActiveView` | The five views (Day 38) |
| 10 | `shiftPhase` | `ShiftPhase` | Which stage of the page-turn animation (Day 38 §4.8) |
| 11 | `pendingPageIndex` | `number \| null` | Where the turn is going, held until the exit animation finishes |
| 12 | `pending` | `PendingUpload[]` | Upload rows — **the documented lift** (§4.5) |
| 13 | `loadingPending` | `boolean` | Refresh spinner for #12 |

**Read the pattern.** Every one of these is consumed by **two or more** children,
or decides which subtree exists at all. Compare with what stayed local:

| Local state | Where | Why it did not lift |
|---|---|---|
| `isHovered` | `DocumentPage` | Only the sheet's own shadow reads it |
| `query`, `selectedEntities` | `QueryDock` | Only the dock reads them; the parent gets the final string via `onSubmit` |
| `expanded` | `ExecutionTrace` | Only the trace's own collapse |
| `search`, `statusFilter` | `UploadHistoryTable` | Only that table filters |
| `file`, `company`, `ticker`, … | `UploadPanel` | The form's own draft; the parent gets the result via `onRefresh` |

**The rule this repository follows: state lives at the lowest common ancestor of
everything that reads it.** Not higher. `QueryDock`'s input text is not in
`page.tsx`, and that is correct.

---

### 4.3 Two states for one question — `session` and `sessionChecked`

```tsx
const [session, setSession] = useState<ReturnType<typeof getSession>>(null);
const [sessionChecked, setSessionChecked] = useState(false);

useEffect(() => {
  setSession(getSession());
  setSessionChecked(true);
}, []);
```

and later:

```tsx
if (!sessionChecked) return null;
if (!session) return <LoginForm onSuccess={() => setSession(getSession())} />;
```

**Why two booleans' worth of state for one question?**

`getSession()` reads `localStorage` (Day 41), which **does not exist during the
server render**. `lib/auth.ts` guards it:

```ts
export function getSession(): StoredSession | null {
  if (typeof window === "undefined") return null;
```

So on the first render — server-side, or the client's first pass before effects
run — `session` is `null`. Without `sessionChecked`, that is indistinguishable
from "logged out", and **every reload would flash the login form** for a frame
before the effect replaced it.

`sessionChecked` splits one `null` into two meanings:

| `sessionChecked` | `session` | Meaning | Render |
|---|---|---|---|
| `false` | `null` | have not looked yet | `null` — nothing |
| `true` | `null` | genuinely logged out | `<LoginForm>` |
| `true` | object | logged in | the app |

**This is the same defect class you have met four times already.** F14: a scalar
`company` overloading `null` with "none" and "several". CAVEAT-004: a required
DSL `metric` that cannot express "no metric". `confidence_tier`'s default `low`
being indistinguishable from a measured `low`. **A single value carrying two
incompatible meanings, fixed by adding the field that separates them.**

Here it was fixed correctly and cheaply, in the frontend, with a boolean. Notice
that this is the **same shape** as the fix Day 36 said F2 and F14 required.

**And `useEffect(…, [])`.** The empty dependency array means *run once, after the
first render*. That is where "read the browser's storage" belongs: not during
render (there is no browser during the server render), but immediately after.

---

### 4.4 The local array beside the state array

```tsx
// Collected locally as well as in state: setState is async, so the array
// attached to the finished page must not depend on a flush having happened.
const collected: TraceEvent[] = [];
setTraceEvents([]);

try {
  const result = await submitQueryStreaming(
    query,
    (ev) => {
      collected.push(ev);
      setTraceEvents([...collected]);
    },
    executionContext as any
  );
  setPages((prev) => {
    const next = [...prev, { response: result, originView: ..., trace: collected }];
    setCurrentPageIndex(next.length);
    return next;
  });
```

**Two sinks for the same events, and they are not redundant.**

- `setTraceEvents([...collected])` drives the **live** display. It must be a
  *new array* each time — `collected.push()` mutates in place, and React compares
  by identity, so pushing without copying would change nothing on screen.
- `collected` is the **archive**. When the stream finishes, this exact array is
  attached to the completed page.

**Why not just read `traceEvents` when building the page?** Because
`traceEvents` inside `handleSubmit` is captured from the render in which
`handleSubmit` was created. It is `[]` for the whole function body, no matter how
many times `setTraceEvents` has been called — that is §4.1's point, and it is
what the comment means by *"must not depend on a flush having happened."*

**What would break.** Every finished page would carry `trace: []`, so paging back
to an earlier answer would show no execution trace at all — while the live one,
driven by the state copy, worked perfectly. **A bug that is invisible during the
query and only appears when you page back.**

**And note the functional update:**

```tsx
setPages((prev) => { ... });
```

`prev` is React's current value, not the closure's. Two answers arriving close
together cannot clobber each other. `setPages([...pages, newPage])` — reading
`pages` from the closure — would.

---

### 4.5 The lift that documents itself

```tsx
// Upload state lifted here (was previously local to UploadPanel) so both
// Archive Intake's capped preview and the new Upload History page read
// from the same fetch — no duplicate requests, no drift between the two.
const [pending, setPending] = useState<PendingUpload[]>([]);
const [loadingPending, setLoadingPending] = useState(false);
```

**This is state lifting with its reason attached, and it is worth reading as a
worked example.**

*Before:* `UploadPanel` owned `pending`, fetched it, displayed three rows.
*Then:* a second consumer appeared — the full Upload History view.
*Two consumers, two possible owners.* Either both fetch (two requests, and two
copies that drift the moment one refreshes), or the state moves up to the lowest
common ancestor of both.

It moved up. `UploadPanel` and `UploadHistoryTable` now both take `pending`,
`loadingPending` and `onRefresh` as **props**, and neither owns the data.

**And `useCallback` is why that works cleanly:**

```tsx
const loadPending = useCallback(async () => { ... }, []);

useEffect(() => {
  if (session) loadPending();
}, [session, loadPending]);
```

`useCallback` returns the **same function identity** across renders while its
dependencies (`[]`) are unchanged. Without it, `loadPending` would be a new
function every render, `[session, loadPending]` would differ every render, and
the effect would re-fetch on **every** render — an infinite loop of requests.

**That is the practical reason `useCallback` exists.** Not performance. Identity
stability for dependency arrays.

**One more line worth reading:**

```tsx
} catch (err) {
  if (err instanceof UnauthorizedError) {
    setSession(null);
  }
  // otherwise silent — this list is a convenience view, not critical path
}
```

**An explicitly-justified swallow.** A 401 logs you out because that is a real
state change; anything else is silently dropped **and says why**. Compare with
the router exception-swallow that `SESSION_LOG.md` records as a silent
degradation defect: the difference is not "swallowing is bad", it is whether the
swallow is reasoned about and written down.

---

### 4.6 Consuming SSE — and why not `EventSource`

Browsers ship a purpose-built SSE client, `EventSource`. This repository does not
use it, and `lib/api.ts` says why:

```ts
/**
 * Uses fetch + ReadableStream rather than EventSource: EventSource is GET-only
 * and cannot set an Authorization header, and moving the JWT into a query
 * string would put it in server access logs and browser history.
 */
```

**Two disqualifications, and the second is a security one.**

1. `EventSource` issues a **GET**. The query is a POST body (`{query,
   execution_context}`). A GET would put the question in the URL.
2. `EventSource` **cannot set headers.** No `Authorization: Bearer …`. The only
   way to authenticate would be `?token=…` — and a JWT in a query string lands in
   nginx access logs, Render's request logs, browser history, and any `Referer`
   header the page later sends.

So the stream is consumed by hand. That is more code, and the code is the rest of
this section.

---

### 4.7 Re-assembling SSE frames

```ts
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });
  const frames = buffer.split("\n\n");
  buffer = frames.pop() ?? "";
  ...
}
```

**Four lines, four separate facts.**

**`reader.read()` returns bytes, not frames.** A TCP chunk boundary lands
wherever the network puts it — mid-word, mid-JSON, mid-`data:` line. Nothing
aligns it to anything.

**`decoder.decode(value, { stream: true })`.** The `stream: true` flag tells the
decoder to **hold an incomplete multi-byte UTF-8 sequence** until the next chunk.
Without it, a chunk boundary splitting a multi-byte character (the `→` in
`"DSL → SQL"`, or `₹`) produces a replacement character. The trace labels in
`api/query.py` contain `→` deliberately, so this is not hypothetical.

**`buffer.split("\n\n")`.** SSE frames are separated by a blank line.

**`buffer = frames.pop() ?? ""`.** This is the important one. The **last** element
of a split is either an empty string (the buffer ended exactly on a separator) or
a **partial frame**. It is put back into the buffer and completed by the next
read. The code's own comment:

```ts
// SSE frames are separated by a blank line. Partial frames stay in the
// buffer until their terminator arrives -- a chunk boundary can land
// anywhere, including mid-JSON.
```

**Then each complete frame is parsed:**

```ts
for (const line of frame.split("\n")) {
  if (line.startsWith(":")) continue;            // heartbeat comment
  if (line.startsWith("event:")) eventName = line.slice(6).trim();
  else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
}
```

`: heartbeat` — the server's 15-second keepalive from Day 6 — is a **comment
line** in SSE and is skipped here. That is the client half of the mechanism that
stops an idle proxy closing the connection during a slow LLM call.

**And one defensive line:**

```ts
try {
  payload = JSON.parse(dataLines.join("\n"));
} catch {
  continue; // malformed frame: skip it rather than kill the stream
}
```

**Skip the frame, keep the stream.** One unparseable trace event should not lose
the answer that is still coming.

**Three event names, three destinations:**

```ts
if (eventName === "node") onNode(payload as TraceEvent);
else if (eventName === "complete") result = payload as QueryResponse;
else if (eventName === "error") streamError = payload?.message ?? "Pipeline error";
```

`start` is received and ignored — it carries only `request_id`, which the
`complete` payload also carries.

---

### 4.8 The four error classes

```ts
export class UnauthorizedError extends Error {}

/**
 * The pipeline RAN and reported its own failure: the server emitted an SSE
 * `error` event. Re-running reproduces it at double cost, so this never
 * retries.
 */
export class PipelineError extends Error {}

/** HTTP-level failure BEFORE the stream began (non-2xx, or a 2xx with no body). */
export class RequestFailedError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

/** The connection never established, or failed before a single byte arrived. */
export class TransportError extends Error {}
```

**Four classes, and the taxonomy is the design.** Each answers a different
question about *where* the failure happened:

| Class | Where | Retryable? | Why |
|---|---|---|---|
| `UnauthorizedError` | Auth, before anything | **No** | The token is dead. `submitQuery` would 401 identically |
| `RequestFailedError` | HTTP, before the stream | **No** | The server rejected the request. Re-sending reproduces it |
| `PipelineError` | Inside the graph, mid-stream | **No** | The pipeline **ran** and failed. Re-running spends a second LLM budget to fail again |
| `TransportError` | Socket, before any byte | **No** | DNS / refused / TLS. `submitQuery` hits the same wall |

**And the one case that is retried has no class**, because it is not an error —
it is an absence:

```ts
// Reader drained with no `complete`: the connection ended mid-pipeline.
// THE ONE RETRYABLE CASE -- nothing reported a failure, the bytes just
// stopped arriving.
outcome = result ?? "retry";
```

**Read the asymmetry.** Three named failures, none retried; one *unnamed*
outcome, retried. The signal for "safe to retry" is precisely that **nothing
reported a failure**.

---

### 4.9 Why retrying everything was a real cost

The docstring records what the previous version did:

```
 * The previous version retried every failure, and the cost was not
 * theoretical. api/query.py deliberately never cancels the graph task on
 * client disconnect ("the pipeline must still finish so audit_writer_node
 * writes its row"), so one user question became two full pipeline runs, two
 * LLM spends against a 500/day ceiling, and two audit_log rows with nothing
 * marking either as a retry.
```

**Price it out.** A semantic question is **two** Gemini calls (router +
synthesis). A cross question is three. The free tier is **500 per day**. A retry
policy that fires on every failure doubles that consumption on exactly the days
the system is already failing — which are the days it is already rate-limited.

**And the audit consequence is worse than the money.** `audit_log` is
append-only by grant (Day 44): the `ledgermind_app` role has no DELETE. Two rows
land, both look like genuine user questions, and **nothing distinguishes the
retry**. Every metric computed over that table — `total_queries`,
`avg_latency_ms`, `refusal_rate_pct` — is now measuring an artifact of the
client's error handling.

**That is the argument.** Not "retries are bad". *In this system*, a retry is a
second pipeline execution, a second billed LLM call, and a permanent second row
in an append-only ledger that has no way to mark it as duplicate.

**The fix is in `d3d3caa`**, "fix(frontend): retry only a dropped socket, never a
pipeline failure".

---

### 4.10 The retry, and where it is placed

```ts
// Outside the try on purpose: a throw from this call must reach the caller,
// not be caught by the block above and retried a second time.
if (outcome === "retry") return submitQuery(question, executionContext);
return outcome;
```

**Placement as the guarantee**, exactly as Day 37's Stage 0c was scoped by living
in `cross_engine.py`. Inside the `try`, a failure of the fallback would be caught
by the same `catch`, set `outcome = "retry"` again, and — depending on flow —
risk a second fallback. Outside it, the fallback's failure is the caller's
problem, and **exactly one** retry is structurally possible.

Note also that the fallback is to `submitQuery` — the **blocking** endpoint
(`POST /api/query`), not the stream. Same pipeline, different transport (Day 6).
The retry gives up the trace and keeps the answer.

---

### 4.11 `api.retry.guard.ts` — a test suite in a project with no test runner

```ts
/**
 * GUARD — submitQueryStreaming's retry policy.
 *
 * There is no test runner in this project (package.json has no test script and
 * no vitest/jest dependency), and adding one is outside this change. This file
 * is a standalone executable guard instead:
 *
 *   docker compose exec -T -w /app frontend \
 *     node_modules/.bin/sucrase-node lib/api.retry.guard.ts
 */
```

Verify the premise yourself:

```bash
grep -n '"test"\|vitest\|jest' frontend/package.json    # nothing
```

That is **CAVEAT-022** ("No CI, no frontend tests") met in the wild. And the
response to it is instructive: rather than adding a test framework as a side
effect of a bug fix, the fix ships with a **single executable file** that pins
the behaviour.

**And its central technique is worth more than the file.**

```ts
let positives = 0;
let controls = 0;

function assertEq(actual, expected, label) { positives++; eq(actual, expected, label); }

/** The inverted claim. Fails loudly if it does NOT throw. */
function control(fn, label) {
  controls++;
  let threw = false;
  try { fn(); } catch { threw = true; }
  if (!threw) throw new Error(`NEGATIVE CONTROL DID NOT FIRE: ${label}`);
}
```

used as:

```ts
assertEq(r.retries, 0, "error event MUST NOT retry");
control(() => eq(r.retries, 1, "inverted"), "error event MUST NOT retry [inverted]");
```

**Every assertion is immediately followed by the same claim inverted, wrapped so
it must throw.** The docstring states the reasoning:

> An assertion that cannot fail is not evidence, and a control living in a
> separate block can drift away from the assertion it is supposed to be
> guarding.

And then:

```ts
eq(positives, controls, "assertion count vs negative-control count");
if (positives === 0) throw new Error("no assertions ran");
```

**The counts are themselves asserted.** Delete a control and the file fails.
Delete every assertion and the file fails.

**This is `CLAUDE.md` §8 in executable form:** *"A test that cannot observe the
failure mode is not evidence."* The 20-iteration hostname loop that counted
successes without timing them reported 20/20 clean while lookups took 8 seconds.
An assertion with no live negative control is that same loop.

**The four cases it pins:**

| Case | Setup | Asserted |
|---|---|---|
| 1 | server emits `event: error` | 0 retries, surfaces as `PipelineError` |
| 2 | `!res.ok` (500) | 0 retries, `RequestFailedError` carrying `status === 500` |
| 3 | `start` frame then stream ends — **twice**: reader drains cleanly, and reader throws mid-read | **exactly 1** retry each, resolves with the fallback payload |
| 4 | 401 | 0 retries, `UnauthorizedError` |

**Case 3 covering both shapes is the detail to notice.** "Socket dropped" reaches
the code two different ways — `done: true` with no `complete`, and a `TypeError`
out of `reader.read()` — and they take **different branches**. One assertion
would have covered one branch.

---

### 4.12 `ExecutionTrace` — rendering a stream that may skip stages

179 lines, and its docstring states the constraint:

```tsx
/**
 * Every stage shown here corresponds to an actual node in app/engines/graph.py
 * and every detail line is a value read out of real backend state -- nothing
 * is inferred, estimated, or animated on a timer (Zero UI-Hallucination
 * Mandate). Timing appears only when the backend actually sent it, which it
 * does for admin only.
 */
```

**Six slots for eight nodes:**

```tsx
const SLOTS: Slot[] = [
  { key: "prompt_shield",      label: "PROMPT SHIELD" },
  { key: "router",             label: "ROUTER" },
  { key: "__engine__",         label: null },
  { key: "confidence",         label: "CONFIDENCE" },
  { key: "response_generator", label: "RESPONSE" },
  { key: "audit_writer",       label: "AUDIT" },
];

const ENGINE_NODES = new Set(["semantic_engine", "quant_engine", "cross_engine"]);
```

> Mirrors the real graph topology. The engine slot is deliberately one slot,
> not three: semantic / quantitative / cross are mutually exclusive.

**A UI shape derived from a graph property** (Day 35: the three engine edges are
alternatives, never concurrent).

**The label resolves early:**

```tsx
const PATH_LABELS: Record<string, string> = {
  QUANTITATIVE: "DSL → SQL",
  SEMANTIC:     "SEMANTIC RETRIEVAL",
  CROSS:        "CROSS-EXAMINATION",
};
```

> The router's own detail already names the path it chose, so the engine slot
> can be labelled the moment ROUTER completes — before the engine itself has
> reported. Without this the slot reads "RESOLVING ROUTE" while the line above
> it plainly says QUANTITATIVE, which shows uncertainty the backend has
> already dispelled.

**"Uncertainty the backend has already dispelled."** That is the mandate again,
from the other direction: not only must the UI not assert what it does not know,
it must not *withhold* what it does.

**And the hardest bit — pending versus skipped:**

```tsx
const auditDone = byKey.has("audit_writer");
const lastDoneIndex = SLOTS.reduce((acc, s, i) => (byKey.has(s.key) ? i : acc), -1);
...
const done    = event !== undefined;
const skipped = !done && auditDone;
const active  = !done && !skipped && i === lastDoneIndex + 1;
```

> A blocked query goes prompt_shield -> audit_writer directly, so the four
> middle slots never fire. Once AUDIT lands, anything still unseen was
> genuinely skipped, not pending.

**`audit_writer` is the terminal node of every path** (Day 35), so its arrival is
proof that nothing else is coming. Three visual states — `✓` done, `—` skipped
("not executed"), `›` active ("working…") — and the third is only claimed for
the **single** slot immediately after the last completed one.

---

## 5. The actual LedgerMind files

```
File:  frontend/lib/api.ts (387 lines)
Entry: submitQueryStreaming(question, onNode, executionContext?) -> QueryResponse
       submitQuery(...)          the blocking fallback, POST /api/query
       uploadDocument(...)       Day 41
       fetchPendingUploads()     Day 41
Types: QueryResponse · CitationResponse · ContradictionResponse ·
       TraceEvent · PendingUpload · CorpusStatus (unused — Day 40)
Errors: UnauthorizedError · PipelineError · RequestFailedError · TransportError
Note:  Tier 4 in CODE_DOCUMENTATION_LOG — read as teaching material, not
       rewritten. Its comments carry the measurements.

File:  frontend/lib/api.retry.guard.ts (228 lines)
Run:   docker compose exec -T -w /app frontend \
         node_modules/.bin/sucrase-node lib/api.retry.guard.ts
Pins:  four cases; every assertion carries an inline inverted control; the
       two counts are compared at the end.

File:  frontend/app/page.tsx — the state half (Day 40 takes the render half)
State: 13 useState · 2 useEffect · 1 useCallback
Entry: handleSubmit(query), loadPending(), handleNavigate(n),
       handleSheetTransitionEnd()

File:  frontend/components/document/ExecutionTrace.tsx (179 lines)
In:    events: TraceEvent[], isComplete: boolean
Out:   six slots, or a one-line collapsed summary once complete
Note:  6 slots for 8 nodes — the three engines share one, deliberately.
```

---

## 6. Deep walkthrough — one query, from keypress to a page in the stack

**STATE BEFORE.** `session` set, `pages = []`, `currentPageIndex = 0`,
`isLoading = false`, `activeView = "workbench"`.

**Step 1 — `QueryDock` owns the text, and hands over a string.**

```tsx
const [query, setQuery] = useState("");
const handleSubmit = (e: React.FormEvent) => {
  e.preventDefault();
  if (!query.trim() || isLoading) return;
  onSubmit(query);
};
```

`e.preventDefault()` stops the browser's native form navigation. The guard
covers empty input **and** re-entry while a query is in flight — `isLoading`
arrives as a prop from `page.tsx`, so the dock cannot double-submit.

**Step 2 — `page.tsx handleSubmit`: flags first.**

```tsx
setIsLoading(true);
setError(null);
```

Clearing `error` matters: a previous failure's message must not sit under a
successful answer.

**Step 3 — the peer-comparison execution context.**

```tsx
const executionContext = activeView === "peer" ? {
  workspace_view: "peer_comparison",
  intended_path: "quantitative",
  intended_operation: "growth_comparison",
  enforce_path: true,
  financial_type: "consolidated"
} : undefined;
```

**This is client input steering the pipeline.** `SECURITY_MODEL.md` §7 records it
plainly:

> `execution_context` — **None.** `Dict[str, Any]`, straight into the router.
> `enforce_path` lets a client force a path. Placed *after* the F2 refusal
> deliberately, so an override cannot route past a failed entity resolution —
> but it is unvalidated client input steering the pipeline.

**Read what protects the system here, and what does not.** It is *not*
validation — there is none. It is **ordering**: the override is applied after the
refusal check, so it cannot smuggle a query past entity resolution. That is the
same "guard by placement" idea as Day 37's Stage 0c, and it is the whole
mitigation.

**Step 4 — two sinks (§4.4), then the stream.**

**Step 5 — inside `submitQueryStreaming`.**

```ts
let streamStarted = false;
let outcome: QueryResponse | "retry";
```

`streamStarted` is set **the moment a readable body is in hand**:

```ts
if (!res.ok || !res.body) throw new RequestFailedError(`Stream failed (${res.status})`, res.status);
streamStarted = true;
```

That single boolean is what separates "we never connected" from "the stream
started and then died". It is the same *split-the-overloaded-null* move as
`sessionChecked` in §4.3.

**Step 6 — the read loop** (§4.7), calling `onNode` per `node` event. Each call
pushes into `collected` and copies into `traceEvents`, and `ExecutionTrace`
re-renders with one more `✓`.

**Step 7 — classification in the `catch`.**

```ts
if (err instanceof UnauthorizedError)  throw err;
if (err instanceof PipelineError)      throw err;
if (err instanceof RequestFailedError) throw err;
if (!streamStarted) throw new TransportError(...);
outcome = "retry";
```

**Read it as a filter.** Three re-throws for failures that were *reported*; a
fourth for a connection that never existed; and only what falls through — a
throw **after** the stream started — becomes a retry.

**Step 8 — the retry, outside the `try`** (§4.10).

**Step 9 — back in `handleSubmit`, on success.**

```tsx
setPages((prev) => {
  const next = [...prev, { response: result, originView: ..., trace: collected }];
  setCurrentPageIndex(next.length);
  return next;
});
setRevisions((r) => ({ ...r, [query]: (r[query] ?? 0) + 1 }));
```

Both are functional updates. `setCurrentPageIndex(next.length)` is called
**inside** the `setPages` updater so it uses the length of the array actually
being committed.

> **Worth flagging honestly:** calling one setter inside another's updater
> function works here, but updater functions are expected to be pure, and React
> may invoke them more than once in development (Strict Mode). The equivalent
> `setPages(...); setCurrentPageIndex(pages.length + 1);` after the fact would
> be more conventional. The repository does not comment on this choice, and it
> has not produced an observed defect — record it as an observation, not a bug.

**Step 10 — 401 unwinds everything.**

```tsx
if (err instanceof UnauthorizedError) {
  setSession(null); setPages([]); setCurrentPageIndex(0); setError(null);
  return;
}
```

**A dead session clears the working papers.** Deliberate: the sheets hold another
user's answers as far as the next login is concerned.

**Step 11 — `finally { setIsLoading(false); }`.** Runs on success, on failure,
and on the 401 early return — because `finally` runs before the `return` takes
effect. **The dock can never be left disabled.**

**STATE AFTER.** `pages.length === 1`, `currentPageIndex === 1`,
`traceEvents` holds the finished stream, `isLoading === false`,
`revisions[query] === 1` → the header renders `REV: 01`.

---

## 7. Data flow

```
 keypress
    ▼ QueryDock local state: query
 submit ▼ onSubmit(query)
 page.tsx handleSubmit
    ├─ setIsLoading(true) · setError(null)
    ├─ executionContext  (peer view only — UNVALIDATED, ordering is the guard)
    ├─ collected: TraceEvent[] = []          ← LOCAL, the archive
    └─ submitQueryStreaming(query, onNode, ctx)
           │
           ▼ POST /api/query/stream   Authorization: Bearer <JWT>
           │
           ▼ res.body.getReader()      streamStarted = true
           │
           ▼ read() → bytes → decode(stream:true) → buffer
           │   split("\n\n") → frames; frames.pop() back into buffer
           │
           ├─ ": heartbeat"    → skipped (Day 6, 15 s)
           ├─ event: node      → onNode → collected.push + setTraceEvents([...])
           │                              → ExecutionTrace re-renders    ✓
           ├─ event: complete  → result
           └─ event: error     → PipelineError  ── NEVER RETRIED
           │
           ▼ drained with no `complete`  →  outcome = "retry"
           │        └─► submitQuery()  POST /api/query  (blocking, no trace)
           ▼
 page.tsx
    ├─ setPages(prev => [...prev, { response, originView, trace: collected }])
    ├─ setCurrentPageIndex(next.length)
    ├─ setRevisions(r => ({ ...r, [query]: (r[query] ?? 0) + 1 }))
    └─ finally: setIsLoading(false)
           ▼
 renderSheetContent(idx) → composeDocumentBody(data)          ← DAY 40
```

---

## 8. Engineering decision — retry exactly one failure mode

**Problem.** A streamed query can fail in four distinguishable ways. Which of
them may be retried automatically?

**Decision.** **Exactly one:** the socket dropping after the stream opened and
before `complete` arrived. Everything else surfaces as a typed error and stops.
Commit `d3d3caa`.

| Alternative | Why not |
|---|---|
| **Retry everything** (the previous behaviour) | Two pipeline runs, two LLM spends against 500/day, two audit rows with nothing marking the retry. This is what happened |
| **Retry nothing** | A dropped socket loses an answer that *did* complete server-side, for a failure the user did not cause. The pipeline ran and paid for itself either way |
| **Retry with backoff, n times** | Each attempt is a fresh pipeline; backoff spaces the cost, it does not reduce it |
| **Cancel the server task on disconnect, then retry freely** | Directly contradicts Day 6's decision — the pipeline must finish so `audit_writer_node` writes its row |
| **Idempotency key, server-side dedup** | The correct answer at scale. Requires a request-id round trip and a cache keyed on it. **Nothing like it exists** and it is not a client-only change |

**Trade-offs accepted.**

- **The retry silently downgrades to the blocking endpoint.** The user loses the
  execution trace and is told nothing. `ExecutionTrace` renders `trace: []`
  and simply shows no trace — omission, not a lie, but also not a notification.
- **A retried run still writes a second `audit_log` row.** Rarer now, but the
  underlying property is unchanged.
- **`TransportError` and `PipelineError` reach `page.tsx` as
  `err.message` and render as bare text.** No class-specific copy — a dropped
  network and a failed graph read identically to the user.
- **The guard is not run automatically.** No CI (CAVEAT-022). It is a file
  someone must remember to execute.

**Current validity.** The policy is right and the reasoning is recorded at both
ends (client docstring, server `finally` comment). The gap is enforcement, not
design.

**At 10×.** A server-side idempotency key on `request_id`, so a retry returns the
first run's stored result instead of executing again — which also fixes the
duplicate audit row, and is the only change that makes retrying genuinely cheap.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| One question, two `audit_log` rows | A retry fired. Confirm which class — only a dropped socket should |
| The live trace advances but a paged-back answer shows none | `trace: traceEvents` instead of `trace: collected` (§4.4) |
| The trace never updates on screen | `setTraceEvents(collected)` without the spread — same array identity, no re-render |
| Infinite refetch of `/api/documents/pending` | `loadPending` not wrapped in `useCallback`, so the effect's dependency changes every render |
| Login form flashes on every reload | `sessionChecked` removed, so "not looked yet" collapses into "logged out" |
| `→` renders as `�` in a trace label | `decoder.decode(value)` without `{ stream: true }` |
| The stream hangs, then dies at a proxy | Heartbeat comment lines not skipped, or the server's `X-Accel-Buffering: no` header lost |
| The submit button stays disabled forever | `setIsLoading(false)` moved out of `finally` |
| Middle trace slots stay grey after a policy block | `auditDone` not consulted, so skipped renders as pending |
| The engine slot reads "RESOLVING ROUTE" beside `QUANTITATIVE` | `PATH_LABELS` lookup removed — the router's `detail` is no longer consulted |

---

## 10. Hands-on experiment

### Experiment 1 — run the retry guard

```bash
docker compose exec -T -w /app frontend node_modules/.bin/sucrase-node lib/api.retry.guard.ts
```

Expect a single line: `GUARD PASS — N assertions, N inline negative controls,
counts equal`. **Note that the two numbers are equal, and that the file itself
asserts that.**

### Experiment 2 — break a negative control and watch it fire

Prove the guard can fail. Temporarily invert one **control**:

```bash
cd frontend
cp lib/api.retry.guard.ts /tmp/guard.bak
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("lib/api.retry.guard.ts")
s = p.read_text()
old = 'control(() => eq(r.retries, 1, "inverted"), "error event MUST NOT retry [inverted]");'
new = 'control(() => eq(r.retries, 0, "inverted"), "error event MUST NOT retry [inverted]");'
assert s.count(old) == 1, f"ABORT: found {s.count(old)}"
p.write_text(s.replace(old, new))
print("patched")
PY
docker compose exec -T -w /app frontend node_modules/.bin/sucrase-node lib/api.retry.guard.ts
```

You should see `GUARD FAIL — NEGATIVE CONTROL DID NOT FIRE: …`. **The control
now states something true, so it does not throw, so the guard rejects it.**
Restore:

```bash
cp /tmp/guard.bak lib/api.retry.guard.ts
git diff --stat frontend/     # must be empty
```

### Experiment 3 — watch the real stream, frame by frame

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@alpha.ledgermind.test","password":"demo1234"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "token length: ${#TOKEN}"

curl -N -s -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"What was Titan revenue in Q1FY26?"}' | head -40
```

**Read for three things:** the blank line between frames, whether any
`: heartbeat` appears, and whether `duration_ms` is present (it is — you
authenticated as admin).

### Experiment 4 — the same query as a viewer

```bash
VTOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"viewer@alpha.ledgermind.test","password":"demo1234"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
echo "viewer token length: ${#VTOKEN}"
```

If that token is non-empty, run Experiment 3's curl with it and **diff the node
events**. `duration_ms` should be absent — `api/query.py` gates it on
`role == "admin"`, the same restriction as `latency_ms` in
`role_filtered_response`. If the login fails, the viewer user is not seeded in
*this* database (`CLAUDE.md` §7: two divergent databases) — say which one you
queried rather than concluding the gate is broken.

### Experiment 5 — kill the backend mid-stream

**Terminal A:**

```bash
curl -N -s -X POST http://localhost:8000/api/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"Summarize Eternal management commentary on profitability"}'
```

**Terminal B, ~1 second later:**

```bash
docker compose restart backend
```

Terminal A ends without a `complete` frame. **That is the one retryable case**,
observed at the wire. Then poll before continuing:

```bash
until curl -sf http://localhost:8000/health >/dev/null; do :; done; echo "backend serving"
```

Now do it from the browser and watch the network panel: the failed
`/api/query/stream` is followed by exactly **one** `/api/query`.

### Experiment 6 — kill it *before* connect

```bash
docker compose stop backend
```

Submit from the browser. **No retry** — the failure is a `TransportError`,
because `streamStarted` was never set. Restart and poll `/health` as above.

### Experiment 7 — count the audit rows

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   SELECT query_text, COUNT(*), MIN(created_at), MAX(created_at)
   FROM audit_log GROUP BY query_text HAVING COUNT(*) > 1
   ORDER BY 2 DESC LIMIT 10;"
```

**Read carefully before concluding anything.** A count > 1 means the same text
was asked more than once — which is *normal*, since you ask golden questions
repeatedly. Only rows **seconds apart** are retry-shaped. And note the state this
proves: **there is no column that would tell you either way.** That is the debt
§8 names.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `frontend/app/page.tsx` and `frontend/lib/api.ts`:

1. List every `useState` in `page.tsx`. For each, name **at least two**
   consumers, or explain why it must live at the top even with one.
2. Find the comment about `setState` being async. Explain what would break, and
   say **when** the user would notice — during the query, or later?
3. Name the four error classes and, for each, say whether it is retried and why.
4. Find the retry call. Why is it **outside** the `try`?
5. `sessionChecked` exists to separate two meanings of one `null`. Name three
   other places in this system where a single value overloaded two meanings, and
   what each fix looked like.

---

## 12. Self-check questions

**Basic**

1. What does `useState` return?
2. What does an empty dependency array on `useEffect` mean?
3. What does `useCallback` preserve, and why does that matter?
4. What separates two SSE frames on the wire?
5. Which endpoint does the retry fall back to?

**Code**

6. Why is `setTraceEvents([...collected])` spread rather than passed directly?
7. What is `streamStarted` for, and at which exact line is it set?
8. What does `buffer = frames.pop() ?? ""` accomplish?
9. What does `{ stream: true }` do on `TextDecoder.decode`, and which character
   in this app makes it necessary?
10. How does `ExecutionTrace` distinguish a skipped stage from a pending one?

**Why**

11. Why not `EventSource`? Give both reasons.
12. Why is a retry expensive *in this system specifically*? Name three costs.
13. Why does a `PipelineError` never retry, when a dropped socket does?
14. Why does `page.tsx` hold `pending` when only `UploadPanel` originally used
    it?
15. Why does the retry guard pair every assertion with an inverted control, and
    then assert the two counts are equal?

**Debugging**

16. One user question produced two `audit_log` rows. Walk the diagnosis.
17. The live trace works; paging back shows none. One line is wrong — which?
18. `/api/documents/pending` is requested continuously in a loop. Cause?

**System design**

19. Design server-side retry safety. What does the client send, what does the
    server store, what does it return, and what does it fix that the current
    policy cannot?
20. The retry silently downgrades to the blocking transport and the user is not
    told. Design the disclosure — and say what makes this hard given the Zero
    UI-Hallucination Mandate.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Thirteen. The two-consumer test: `isLoading` → `QueryDock` (button label) and
   the sheet (trace vs. body); `pending` → `UploadPanel` preview and
   `UploadHistoryTable`; `pages` → the sheet, `AuditLogTable`, `PageNavigator`,
   `getDocId`. **The ones that pass on a different criterion** are `session` and
   `sessionChecked`, which decide *which subtree exists at all* — there is no
   lower common ancestor because one branch is `<LoginForm>` and the other is
   the entire application.
2. **What breaks:** `collected` is replaced by `traceEvents`, which is captured
   from the render `handleSubmit` was created in and is `[]` for the whole
   function body. Every completed page gets `trace: []`. **When it is noticed:**
   *not during the query* — the live trace is driven by the state copy and looks
   perfect. It appears only when you **page back** to an earlier answer and find
   its trace gone. A bug that hides behind the working case.
3. `UnauthorizedError` — no; the token is dead and the fallback would 401.
   `RequestFailedError` — no; the server rejected the request outright and would
   again. `PipelineError` — no; the pipeline **ran** and failed, so re-running
   reproduces the failure at double cost. `TransportError` — no; nothing ever
   connected, and `submitQuery` hits the same wall. **The retried case is not one
   of the four:** the reader drained (or threw) after the stream started, with
   nothing having reported a failure.
4. So a throw from `submitQuery` reaches the **caller**, rather than being caught
   by the same `catch` block, setting `outcome = "retry"` again, and risking a
   second fallback. Placement makes "exactly one retry" structural rather than
   conventional.
5. **(a)** F14 — `QueryState.company: str | None` overloading "no issuer" and
   "several"; fixed by a **schema change** to `companies: list[str]`.
   **(b)** CAVEAT-004 — a required DSL `metric` that cannot express "no metric",
   so the model invents one; **still open**, worked around by Stage 0c's
   placement (Day 37). **(c)** `confidence_tier` defaulting to `"low"` on a
   blocked query, indistinguishable from a measured low; fixed by **omitting the
   key** (`response_shaping.py`, and `lib/api.ts` made the field optional in
   `017d97e`). A fourth, if you found it: `reranker_score` without
   `reranker_backend` — two scales in one field, fixed by shipping the backend
   name beside it.

### §12 — Basic

1. A two-element array: the value for **this** render, and a setter that requests
   the next one.
2. Run once, after the first render, and never again.
3. **Function identity** across renders while its dependencies are unchanged.
   Matters because a function in another hook's dependency array would otherwise
   differ every render and re-trigger that hook forever.
4. A blank line — `\n\n`.
5. `POST /api/query` — the blocking endpoint, via `submitQuery`.

### §12 — Code

6. Because `collected.push()` mutates in place. React compares by identity, so
   handing back the same array reference schedules no visible change. The spread
   makes a new array.
7. To separate "the stream started and then died" (retryable) from "we never
   connected" (not). Set immediately after the `!res.ok || !res.body` check
   passes — the moment a readable body is in hand.
8. It removes the last element of the split — either `""` or a **partial frame**
   — and puts it back in the buffer to be completed by the next read. Without it,
   any frame straddling a chunk boundary is parsed as two broken halves.
9. It holds an **incomplete multi-byte UTF-8 sequence** until the next chunk
   arrives instead of emitting a replacement character. Necessary here because
   `api/query.py`'s label `"DSL → SQL"` contains `→`, a three-byte
   character; `₹` in answer text is the same hazard.
10. `audit_writer` is the terminal node of every path, so `byKey.has(
    "audit_writer")` proves nothing more is coming. A slot with no event is
    **skipped** if audit has landed, **active** if it is the single slot right
    after the last completed one, and **pending** otherwise.

### §12 — Why

11. **(a)** `EventSource` is GET-only and the query is a POST body. **(b)** It
    cannot set headers, so the JWT would have to travel in the query string —
    into server access logs, browser history and `Referer` headers.
12. **(1)** A second **full pipeline run**, because `api/query.py` never cancels
    the graph on disconnect. **(2)** A second LLM spend against a 500/day
    ceiling — two calls for a semantic question, three for cross. **(3)** A
    second `audit_log` row in an **append-only** table with **nothing marking it
    as a retry**, which corrupts every aggregate `api/metrics.py` computes.
13. Because a `PipelineError` means the pipeline **reported its own failure** —
    it ran, it was paid for, and re-running reproduces it. A dropped socket
    reported nothing: the bytes stopped arriving, which is a transport fact and
    not a statement about the pipeline.
14. Because a second consumer appeared (`UploadHistoryTable`). Two owners means
    two fetches and two copies that drift the moment one refreshes, so the state
    moved to the lowest common ancestor — and the comment in the code says
    exactly that.
15. Because **an assertion that cannot fail is not evidence**, and a control kept
    in a separate block drifts away from the assertion it guards. The count
    equality means a deleted or unreached control is itself a failure — the
    guard guards its own completeness.

### §12 — Debugging

16. **(1)** Are the two rows seconds apart, or minutes? Minutes = the question
    was asked twice. **(2)** Check `latency_ms` — a retry's two rows both carry a
    full pipeline latency, so it is not simply a double-write. **(3)** Reproduce
    with the browser network panel open and count requests to `/api/query/stream`
    vs `/api/query`. **(4)** Check which failure class the first attempt hit —
    only a drop *after* `streamStarted` may retry; anything else means the
    classification in the `catch` has regressed. **(5)** Run the guard. **And
    state the limit honestly:** nothing in `audit_log` records that a row was a
    retry, so the table cannot settle this — only the client can.
17. `trace: collected` was replaced by `trace: traceEvents` in the `setPages`
    updater.
18. `loadPending` is no longer wrapped in `useCallback`, so it is a new function
    identity every render; `useEffect(..., [session, loadPending])` therefore
    sees a changed dependency on every render, re-runs, sets state, re-renders.

### §12 — System design

19. **Client:** generate a UUID per *user question* (not per attempt) and send it
    as an `Idempotency-Key` header on both `/api/query/stream` and `/api/query`.
    **Server:** `api/query.py` looks the key up before building the initial
    state. Store `(tenant_id, idempotency_key) → request_id, final_state` with a
    short TTL — Redis is already deployed as the Celery broker and is the natural
    home, though nothing currently reads or writes a cache there (CAVEAT-009,
    and blueprint §15's cache was never built). On a hit, **return the stored
    result without executing the graph**; on a miss, execute and store on
    completion. **What it fixes that the current policy cannot:** a retry becomes
    genuinely free — no second LLM call, no second `audit_log` row, and it
    becomes safe to retry `TransportError` and even `RequestFailedError`, because
    a second attempt can no longer double-execute. **The awkward case to state:**
    a retry arriving *while the first run is still in flight* must block on it
    rather than miss the cache and start a second — so the stored value needs an
    in-progress state, not just a completed one.
20. **The design.** `submitQueryStreaming` already knows it fell back. Surface it
    on the result rather than in the trace: return `{ ...payload, degraded:
    "trace_unavailable" }`, and have `ExecutionTrace` render a single line —
    *"Execution trace unavailable: the connection dropped and the answer was
    re-fetched without streaming."*
    **Why this is harder than it looks under the mandate.** The mandate forbids
    asserting what the system did not measure. The tempting move — reconstructing
    a plausible trace from the response (it has `path`, so the engine is known;
    it has `citations`, so retrieval ran) — is **exactly** what the mandate
    forbids: a trace nobody observed, rendered as though observed. And the second
    temptation, showing the six slots greyed out, implies the stages did not run
    when they did. **The only honest render is a statement about the client's own
    knowledge**, which is why the disclosure has to be prose about the
    connection, not a depiction of the pipeline. That constraint is the whole
    answer.

---

## 14. MUST REMEMBER

```text
- THIRTEEN useState in page.tsx. State lives at the lowest common ancestor of
  everything that reads it — QueryDock's input text is NOT lifted
- setState is ASYNC. The value in scope is fixed for the whole render
- `collected` (local array) is the ARCHIVE; setTraceEvents is the LIVE view.
  Both, because the finished page must not wait on a flush
- Spread when setting from a mutated array — React compares by IDENTITY
- useCallback preserves FUNCTION IDENTITY for dependency arrays. Not perf
- sessionChecked splits one null into "not looked yet" vs "logged out" —
  the same defect class as F14, CAVEAT-004 and the blocked confidence_tier
- NOT EventSource: GET-only, and cannot set Authorization (a JWT in a query
  string lands in access logs and history)
- SSE frames split on "\n\n"; frames.pop() puts the PARTIAL frame back
- decode(value, { stream: true }) — a chunk boundary can split a multi-byte
  character, and "DSL → SQL" contains one
- FOUR error classes: UnauthorizedError · PipelineError · RequestFailedError ·
  TransportError. NONE is retried
- ONLY a dropped socket after stream start is retried — and it is not one of
  the four classes; it is the absence of any reported failure
- The retry call sits OUTSIDE the try, so exactly one is structurally possible
- A retry = a second pipeline + a second LLM spend against 500/day + a second
  append-only audit row with NOTHING marking it a retry
- api.retry.guard.ts: every assertion carries an INLINE inverted control, and
  the counts are asserted equal
- ExecutionTrace: 6 slots for 8 nodes; the three engines share one, because
  they are mutually exclusive. audit_writer landing proves "skipped", not
  "pending"
```

## 15. MUST UNDERSTAND

```text
- Why a client retry is an ARCHITECTURAL cost here and not a UX preference —
  it follows from api/query.py's decision never to cancel the graph
- Why "nothing reported a failure" is the correct signal for retryability,
  and why that case deliberately has no error class
- Why splitting an overloaded null is the same fix at every layer of this
  system, from a TS boolean to a Pydantic schema change
- Why an assertion without a live negative control is not evidence, and why
  the control must live beside it rather than in a separate block
- Why "the router already said QUANTITATIVE" makes "RESOLVING ROUTE" a
  UI-hallucination in the OTHER direction — withholding what is known
- Why the honest disclosure of a degraded retry is prose about the CLIENT,
  never a reconstructed depiction of the pipeline
```

---

## 16. This connects to

```text
Day 6  — SSE server side, and the graph task that is never cancelled
Day 38 — components, props, the client boundary
   ↓
Day 39 — state, effects, and the SSE consumer
   ↓
Day 40 — the render boundary, and dead code
```

Forward references:

- `composeDocumentBody(data)`, the function this whole flow feeds → **Day 40**
- `getSession` / `localStorage` / `expiresAt` in full → **Day 41**
- `uploadDocument`, `fetchPendingUploads` → **Day 41**
- No test runner (CAVEAT-022), and what the guard file substitutes for →
  **Day 43**
- The `audit_log` row a retry duplicates → **Day 44**
