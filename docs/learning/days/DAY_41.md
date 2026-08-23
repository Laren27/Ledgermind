# Day 41 — Auth State, Upload, and the Admin Surface

**Phase 11 · Weight: M (~90 min) · Prerequisites: Days 40, 9, 7**

**Textbook: no citation.** The textbook's ingestion chapters assume a file is
already on disk. Everything today is about the twenty seconds before that is
true — and about a lifecycle that **deliberately stops** rather than continuing
automatically.

---

## 1. Today's goal

By tonight you can:

- Explain how a session is stored, when it expires, and **who computes the
  expiry** — and why that last question matters.
- State the `localStorage`-versus-`httpOnly`-cookie trade-off **as the code
  states it** (CAVEAT-011), including the condition the comment attaches to it.
- Trace `require_role("admin")` from the JWT claim to a 403, and say where the
  **second**, field-level enforcement happens.
- Explain role gating in the UI, and why it is **not** a security boundary.
- Trace an upload end to end: multipart → size-guarded write → gate → Supabase
  Storage → `pending_uploads` row → **stop**.
- Explain why the gate is deterministic keyword scoring rather than an LLM call,
  and why it uses **two** thresholds rather than one.
- Explain why ingestion is not auto-triggered (**ED-016**), naming the measured
  failure that forced it.
- Explain what the "Audit Trail" view actually shows, and what it does not
  (CAVEAT-027(d)).

---

## 2. Why now

Day 40 closed the read path: response → `composeDocumentBody` → sheet. Today is
the **write** path and the **privileged** surface, and it needs three earlier
days at once.

**Day 7** gave you bcrypt and `authenticate_user`. **Day 9** gave you
`require_role` and `role_filtered_response` — route-level and field-level RBAC,
failing closed. Today you see both from the **client's** side, and you see the
one thing the client adds: a **third** gate that is not a security control at all
(§4.5), and knowing why is the point.

It also closes Phase 11. After today the whole frontend is accounted for: 28
components, 3 library modules, one route.

---

## 3. Concepts you must know first

| Concept | From | Why it is needed today |
|---|---|---|
| JWT is **signed, not encrypted** | Day 8 | Why `localStorage` is a theft risk and not a secrecy one |
| `require_role`, the rank ladder | Day 9 | The upload and pending routes |
| `role_filtered_response` fails closed | Day 9 | The second enforcement point |
| `db_transaction(tenant_id)` sets the GUC | Day 14 | Why the pending list is tenant-scoped without a `WHERE` |
| Offline ingestion, `Exited with status 137` | Day 1, ED-016 | Why upload stops at `pending` |
| Classification from **content**, never filename | Day 23 (Trap 1) | Why `financial_type` is not a form field |
| Omit rather than substitute | Day 40 | `ArchiveStamp`'s refusal to invent stages |

---

## 4. Concept lesson

### 4.1 A session, and who decides when it ends

[`lib/auth.ts`](../../../frontend/lib/auth.ts) is 75 lines and has three
functions. The whole client-side identity model is here.

```ts
export interface StoredSession extends AuthUser {
  accessToken: string;
  expiresAt: number; // epoch ms
}

export async function login(email: string, password: string): Promise<StoredSession> {
  const res = await fetch(`${API_URL}/auth/login`, { … });
  …
  const data = await res.json();
  // TokenResponse: { access_token, token_type, role, tenant_id, expires_in_hours }
  const session: StoredSession = {
    accessToken: data.access_token,
    role: data.role,
    tenantId: data.tenant_id,
    expiresAt: Date.now() + data.expires_in_hours * 60 * 60 * 1000,
  };
  if (typeof window !== "undefined") {
    localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
  }
  return session;
}
```

**Read what is stored and what is derived.**

- `accessToken` — the JWT. Opaque to this file; **never decoded here.**
- `role`, `tenantId` — sent **beside** the token by `/auth/login`, not read out
  of it.
- `expiresAt` — **computed on the client**, as `Date.now() + expires_in_hours`.

**That third line is the one to think about.** The token carries its own `exp`
claim (Day 8), and the *server* enforces that claim on every request. The client
maintains a **separate, parallel** expiry derived from the browser's clock.

**What that means, precisely:**

| | Client `expiresAt` | Token `exp` |
|---|---|---|
| Computed by | `Date.now()` in the browser | the server, at issue |
| Enforced by | `getSession()` returning `null` | `decode_access_token`, → 401 |
| If the browser clock is wrong | **wrong** | unaffected |

A browser clock two hours slow keeps a dead token in storage; the client thinks
it is valid and the **server rejects it**, which surfaces as
`UnauthorizedError` (Day 39) and logs the user out anyway. A clock two hours fast
logs the user out early, from a token that is still good.

**So the client's expiry is a convenience, not a control.** It exists to avoid
sending a request that is certain to 401. The **actual** boundary is the
signature check on the server, and it does not consult the browser at all.

**That is the general shape worth keeping:** a client-side check that mirrors a
server-side one is UX, not enforcement. It may be wrong without being a
vulnerability — but only because the server never trusts it.

**And `getSession` does the check on every read:**

```ts
export function getSession(): StoredSession | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(TOKEN_KEY);
  if (!raw) return null;
  try {
    const session: StoredSession = JSON.parse(raw);
    if (Date.now() >= session.expiresAt) {
      localStorage.removeItem(TOKEN_KEY);
      return null;
    }
    return session;
  } catch {
    return null;
  }
}
```

Three ways to get `null`, and each is correct: **no browser** (the server render,
Day 39 §4.3), **expired** (and it *removes* the row, so the check is
self-cleaning), and **unparseable** (a corrupted or hand-edited entry is treated
as absent rather than crashing the app).

---

### 4.2 CAVEAT-011 — read the comment, not the summary

```ts
// Auth for the LedgerMind frontend.
//
// Token is stored in localStorage — this is a real standalone Next.js app
// (not a claude.ai artifact, where localStorage is disallowed), so this is
// fine for a solo-user portfolio project. If this ever needs to be
// hardened (multi-user, production), move to an httpOnly cookie set by
// the backend instead — localStorage is readable by any script on the
// page, which is a real XSS exposure surface you'd want to close before
// this has real users.
```

**Notice the structure of that comment**, because it is the model for how a
trade-off should be recorded:

1. **The decision** — `localStorage`.
2. **The condition under which it is acceptable** — solo-user portfolio project.
3. **The trigger that ends that condition** — multi-user, production.
4. **The specific replacement** — an `httpOnly` cookie set by the backend.
5. **The mechanism of the risk** — readable by any script on the page.

**A comment that says only "TODO: use cookies" carries none of that.** This one
tells a future reader when to act and what to do.

**The threat, stated exactly.** A JWT is **signed, not encrypted** (Day 8). Its
claims are already public — anyone holding it can base64-decode `sub`,
`tenant_id`, `role`. So the exposure is **not** secrecy; it is **theft**. Any
script running on the page — an XSS payload, a compromised dependency, a
malicious browser extension — can read `localStorage` and exfiltrate the token.

**And what makes theft costly here is a different decision**, recorded in
`SECURITY_MODEL.md` §1:

> **No refresh tokens, no revocation, no logout server-side.** A stolen token is
> valid until `exp`. Logout is `localStorage.removeItem`.

**Put the two together.** `logout()` deletes the local copy; it does not tell the
server anything. A stolen token remains valid for the remainder of its **2-hour**
window regardless of what the user clicks. **The window *is* the mitigation** —
which is why the 2-hour figure is in `MUST_KNOW.md`'s numbers table with the note
*"stateless auth cannot revoke; the window is the mitigation."*

**An `httpOnly` cookie would fix the read**, because JavaScript cannot see such a
cookie. It would introduce CSRF as a new concern (a cookie is sent
automatically), needing `SameSite` and possibly a CSRF token — which is precisely
why "just use cookies" is a change with its own homework, not a free upgrade.

---

### 4.3 The login form, and the one thing it does not do

```tsx
try {
  await login(email, password);
  onSuccess();
} catch (err) {
  setError(err instanceof Error ? err.message : "Login failed");
}
```

**It does not distinguish a wrong email from a wrong password**, because the
backend does not:

> Wrong email and wrong password return the **same** message ("Invalid email or
> password") — no user enumeration.

`lib/auth.ts` preserves that:

```ts
if (res.status === 401) throw new Error("Invalid email or password.");
```

**A frontend that "helpfully" said "no account with that email" would undo a
backend security property from the other side.** The client is not adding
security here; it is *refraining from removing it*.

**And note `onSuccess()` rather than a returned session.** `page.tsx` passes
`onSuccess={() => setSession(getSession())}` — the parent re-reads from storage
rather than receiving the object. **One source of truth**: `localStorage`, read
through `getSession`.

---

### 4.4 What is missing from the login path

Worth stating plainly, because "there is a login form" invites the assumption
that the usual protections exist. From `SECURITY_MODEL.md` §1:

- **No rate limiting on `/auth/login`.** Online password guessing is unthrottled.
  Blueprint §5/§14 specified per-tenant rate limiting; `IMPLEMENTATION_DELTAS.md`
  §A records it as **not built**.
- **No password policy, no lockout, no MFA.**
- **No self-service registration** — users are seeded by migration
  (`sql/migrations/007_seed_users.sql`). *That absence is the main thing limiting
  the exposure*, and it is an accident of scope rather than a control.
- **The secret is symmetric.** Anything that can read `JWT_SECRET` can mint an
  admin token for any tenant.

**Do not read this as a list of bugs.** It is the honest perimeter of a portfolio
project, recorded so nobody deploys it believing otherwise. `SECURITY_MODEL.md`'s
framing rule — *"a mechanism existing is not the same as a threat being
closed"* — is exactly this.

---

### 4.5 Three gates, and only two of them are security

An admin-only feature is gated **three** times in this system. They are not
equivalent, and confusing them is the classic frontend security mistake.

**Gate 1 — the UI does not offer it.**

```tsx
const views: SidebarView[] =
  userRole === "admin"
    ? ["workbench", "peer", "audit", "upload"]
    : ["workbench", "peer", "audit"];
```

**This is not a security control.** `userRole` comes from `session.role`, which
came from a JSON field the client stored and **can trivially edit** — open
DevTools, change `"role":"viewer"` to `"role":"admin"` in `localStorage`,
reload, and the Upload Filing entry appears.

**It is a usability control**, and it is worth having as one: showing a button
that always 403s is worse than not showing it.

**Gate 2 — the route refuses.**

```python
user: dict = Depends(require_role("admin")),  # upload is admin-only per RBAC table
```

```python
def checker(user: dict = Depends(get_current_user)) -> dict:
    if ROLE_RANK[user["role"]] < ROLE_RANK[minimum_role]:
        raise HTTPException(status_code=403, detail=f"Requires role '{minimum_role}' or higher")
    return user
```

**This is the security control.** `user["role"]` comes from
`decode_access_token(token)` — a **signature-verified** claim. The edited
`localStorage` copy never reaches this code; the token does, and its `role` claim
cannot be changed without the secret.

**Try the DevTools edit and then click Upload:** the panel renders, the request
fires, and the server returns **403**. The forged role got you a button and
nothing else. *That* is the demonstration worth doing once.

**Gate 3 — the response is filtered by field.**

`role_filtered_response` (Day 9) strips `reranker_score`, `sql_query`,
`latency_ms`, `llm_provider` and more by tier, **and fails closed**:

```python
# Fail closed. Any role that isn't explicitly recognised -- a typo, a null,
# a future role added to the DB but not here -- gets the most restrictive
# payload, never the least.
if role not in _KNOWN_ROLES or role == "viewer":
    return base
```

**The pattern to keep: the client's role decides what is *shown*; the token's
role decides what is *served*.** They are the same value only when nobody is
lying.

---

### 4.6 The upload lifecycle, and the deliberate stop

```
browser                 FastAPI (Render)              Supabase        Postgres
   │  multipart POST        │                             │              │
   ├───────────────────────►│ require_role("admin")       │              │
   │                        │  ↓ 403 if not               │              │
   │                        │ .pdf extension check        │              │
   │                        │  ↓ 400 if not               │              │
   │                        │ streamed write to /tmp,     │              │
   │                        │ 1 MB at a time, 50 MB cap   │              │
   │                        │  ↓ 413 mid-write            │              │
   │                        │ extract first 2 pages       │              │
   │                        │  ↓ 400 if unreadable        │              │
   │                        │ check_is_financial_filing() │              │
   │                        │  ↓ 400 if REJECT            │              │
   │                        ├────────────────────────────►│ PUT object   │
   │                        │  ↓ 502 on failure           │              │
   │                        │ unlink /tmp (finally)       │              │
   │                        ├────────────────────────────────────────────►│
   │◄───────────────────────┤ INSERT pending_uploads … status 'pending'   │
   │  {doc_id, pending_id,  │                             │              │
   │   gate_score, message} │                             │              │
   │                        │                             │              │
   │                    ✋ STOPS HERE. No ingestion is triggered.         │
   │                                                                     │
   └── later, on a laptop:  python -m scripts.process_pending_uploads ────┘
```

**Every arrow is a real line in
[`api/documents.py`](../../../backend/app/api/documents.py).** Three of them are
worth pulling out.

**(1) The size cap is enforced *during* the write:**

```python
written = 0
with temp_path.open("wb") as f:
    while chunk := await file.read(1024 * 1024):
        written += len(chunk)
        if written > MAX_UPLOAD_BYTES:
            f.close()
            temp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail="File exceeds 50MB limit.")
        f.write(chunk)
```

**Not from `Content-Length`** — a client-supplied header that can lie. The count
is of bytes actually received, so a 4 GB upload is aborted at 50 MB **plus one
chunk**, and the partial file is deleted before the exception propagates.
`SECURITY_MODEL.md` §10 lists it as an availability control: *"Upload size cap
enforced during the write."*

**(2) The temp file is deleted in a `finally`:**

```python
try:
    await upload_file_to_storage(str(temp_path), storage_key)
except Exception as e:
    raise HTTPException(status_code=502, detail="Could not store uploaded file.")
finally:
    temp_path.unlink(missing_ok=True)
```

**Every exit path unlinks** — success, storage failure, and the two earlier
`raise` sites which unlink explicitly. `/tmp` on Render is small, and a leaked
50 MB file per rejected upload would fill it.

> **A related detail, already recorded: CAVEAT-024.** `UPLOAD_DIR.mkdir()` runs at
> **import time**, so importing this module has a filesystem side effect. Not a
> defect today; it is the class of thing that makes a module untestable later.

**(3) The response tells the truth about what happened:**

```python
return {
    "doc_id": …, "pending_id": …, "status": "pending",
    "gate_score": gate_result.score,
    "message": "File stored. Run process_pending_uploads.py to ingest.",
}
```

**`status: "pending"`, and a message naming the script.** Not "processing", not
"your document is being indexed". The UI repeats it:

```tsx
Filings are stored immediately but require a local ingestion step, run by the
developer, before becoming queryable.
```

**The mandate applied to a workflow, not just a number.** The most tempting lie
in an upload form is "we're working on it".

---

### 4.7 Why ingestion is not triggered — ED-016

The module docstring records the measurement:

```python
# Why no auto-trigger: loading the bge-small-en-v1.5 embedding model
# in-process OOM-killed Render's 512MB free-tier web service (confirmed
# via repeated "Exited with status 137" events). Running that step inside
# the same process that serves live queries is unsafe on this tier
# regardless of whether it's triggered via Celery or BackgroundTasks.
```

**Read the last clause carefully.** It is not "Celery would fix this." The
constraint is **RAM in one container**, so the *trigger mechanism* is irrelevant:
a `BackgroundTask` and a Celery task on the same box both load the model into the
same 512 MB.

**Exit 137 is `128 + 9` — SIGKILL.** The OOM killer, not an application error.
There is no traceback and no log line from the app, which is why the docstring
says "confirmed via repeated events" rather than "confirmed from the logs".

**And the honest framing** is the one `00_LEARNING_MAP.md` uses: *"Not a stylistic
choice."* Day 45 collects every architectural decision traceable to 512 MB;
this is the largest.

---

### 4.8 The gate — deterministic, two-dimensional, cheap

[`ingestion/gate.py`](../../../backend/app/ingestion/gate.py):

```python
SIGNAL_CATEGORIES: dict[str, list[tuple[str, int]]] = {
    "regulatory_citation": [(r"\bSEBI\b", 2), (r"Regulation\s+3[03]", 3), …],
    "statement_type":      [(r"Financial Results", 3), (r"Balance Sheet", 2), …],
    "audit_and_accounting":[(r"Ind\s?AS[\s-]?\d*", 2), (r"Auditor'?s Report", 3), …],
    "financial_type":      [(r"\bConsolidated\b", 1), (r"\bStandalone\b", 1), …],
}

MIN_SCORE = 6
MIN_CATEGORIES = 2
SCAN_CHAR_LIMIT = 6000
```

**Two thresholds, and the second is the interesting one.**

```python
passes_score      = total_score >= MIN_SCORE
passes_categories = len(matched_categories) >= MIN_CATEGORIES
if passes_score and passes_categories:
```

**Why not score alone?** Because score is gameable by repetition *within one
dimension*. A document that says "Consolidated" and "Standalone" and "Total
income" repeatedly accumulates points from **one** category. Requiring **two
distinct categories** demands that the document look like a filing along more
than one axis — a regulatory citation *and* a statement type, say.

**This is the same three-signal-intersection idea as Day 23's document
classifier**, applied at a coarser grain and to a different question ("is this a
filing at all?" rather than "which section is this?").

**Why regex and not an LLM?** Four reasons, in the order they matter:

1. **Cost.** The gate runs on every upload, before anything is stored. An LLM
   call is the most expensive thing in the system.
2. **Latency.** It runs **synchronously inside the request**. `SCAN_CHAR_LIMIT =
   6000` bounds it to roughly two pages regardless of a 400-page annual report.
3. **Determinism.** The same PDF gets the same verdict, and the verdict is
   explainable: `GateResult` carries `score`, `matched_categories` and
   `matched_signals`.
4. **It is a security boundary of sorts.** `SECURITY_MODEL.md` §4:
   *"The ingestion gate (`gate.py`) filters for filing-shaped documents, which
   raises the bar."* Putting an LLM in front of an untrusted PDF, to decide
   whether that PDF may enter the corpus, would mean **prompt-injecting the
   gatekeeper with the document you are trying to smuggle in.**

**Reason 4 is the one people miss.** It is the same argument
`SECURITY_MODEL.md` §5 makes about the Prompt Shield: *"There is no LLM-based
classifier behind the regex, deliberately — that would put a probabilistic
component in the compliance path."*

**And the rejection message is diagnostic:**

```python
reason_parts.append(f"score {total_score} < required {MIN_SCORE}")
reason_parts.append(f"matched {len(matched_categories)} categories ({matched_categories}) < required {MIN_CATEGORIES}")
```

The user is told **which** threshold failed and by how much. Compare with the
Prompt Shield's injection blocks, which are deliberately uninformative (Day 42).
**Different threats, opposite disclosure policies** — and both are right: a user
uploading the wrong PDF is making a mistake, while a user probing an injection
filter is not.

---

### 4.9 What the form does *not* collect

```python
# financial_type is NOT collected here — it is auto-detected per-section
# from document content inside pipeline._run_ingestion (detect_sections /
# register_sections), per the Trap 1 fix (classify from content, never
# from filename or user input).
```

**A deliberately absent form field.** A single annual report contains *both*
standalone and consolidated statements, often in different sections — so
`financial_type` is not a property of the **document** at all. It is a property of
each **section**, and only content can decide it.

**Now the counterweight, and it is a real one.** `SECURITY_MODEL.md` §7 records
what the form *does* accept:

> `company`, `fiscal_year`, `quarter`, `filing_date` are **caller-asserted and
> never checked against the document** — audit **F4**. A misfiled document is
> invisible; **it has already happened twice.**

**So one metadata field is derived from content on principle, and four beside it
are taken on trust.** That inconsistency is not hypothetical debt — F4 is an open
audit finding, and `CLAUDE.md` lists it as scheduled work ("F4 + F9, metadata
validation and the gate scan window, designed together").

**Hold both.** The Trap 1 fix is correct *and* incomplete. Extending the same
principle to the other four fields is exactly what F4 is.

---

### 4.10 `ArchiveStamp` — a component that refuses to animate

```tsx
// Maps 1:1 to real pending_uploads.status values from the backend.
// No fabricated intermediate stages (OCR/chunking/embedding/etc.) —
// the backend does not emit that granularity today, and inventing a
// progress animation the system can't actually report would violate
// this project's Zero UI-Hallucination Mandate.
const STAMP_CONFIG: Record<RealStatus, { label: string; color: string }> = {
  pending:    { label: "RECEIVED",  color: "#8B7355" },
  processing: { label: "INDEXING",  color: "#B58A3C" },
  done:       { label: "AVAILABLE", color: "#2E6B4A" },
  failed:     { label: "FAILED",    color: "#B0453A" },
};
```

**Four states, because the column has four values.** `pending_uploads.status` is
`pending | processing | done | failed`; the stamp has exactly four entries.

**The temptation this resists is specific.** A staged progress bar — *Parsing…
Chunking… Embedding… Indexing…* — would look far better and would be **entirely
invented**, because the ingestion script does not report per-stage progress to
this table. The comment names the exact stage list it declined to fabricate.

**And the animation it *does* have is honest:** a 30 ms mount delay, then a scale
from 1.6 to 1.0 — a stamp coming down. It animates **on status change** (the
effect's dependency is `[status]`), so it fires when something real happened.
**Motion tied to a state transition, not to a timer.**

Compare Day 39's `ExecutionTrace`, whose docstring says the same thing —
*"nothing is inferred, estimated, or animated on a timer"* — and note that the
two components were written to obey one rule.

---

### 4.11 The Audit Trail view, and what it actually shows

`page.tsx`:

```tsx
<AuditLogTable
  entries={pages.map((p, i) => ({
    pageNumber: i + 1,
    query: p.response.query,
    path: p.response.path,
    confidenceTier: p.response.confidence_tier,
    latencyMs: p.response.latency_ms,
    isSuccess: !p.response.error && !p.response.is_blocked,
  }))}
  onJump={(n) => { setCurrentPageIndex(n); setActiveView("workbench"); }}
/>
```

**Its rows come from `pages` — React state (Day 39).** Not from an endpoint.

**Prove there is no endpoint:**

```bash
grep -rn '@router\.\(get\|post\)' backend/app/api/*.py backend/app/auth/router.py
```

Six routes: `/auth/login`, `/api/query`, `/api/query/stream`,
`/api/documents/upload`, `/api/documents/pending`, `/api/metrics`. **Nothing
reads `audit_log`.**

**So this view is a session log**, and every row in it is real — each field is
read from a genuine `QueryResponse`. Nothing is fabricated. It also does the
right thing per-field: `confidenceTier ?? "—"` and `latencyMs != null ? … : "—"`
(Day 40 §4.3).

**What is wrong is one line of static copy.** The subtitle reads **"Immutable
System Log"**, over data that is cleared on sign-out and does not survive a
reload. Meanwhile the empty state — *"No audit entries logged in the current
workspace session"* — is accurate and correctly scoped.

**One panel, two claims about itself, disagreeing.** Recorded as
**CAVEAT-027(d)**.

**And the durable thing is real and is elsewhere.** `audit_log` is append-only by
grant, written by `audit_writer_node` on **every** query including blocks and
refusals (Day 44). It exists; it simply has no reader. The honest fix is
`GET /api/audit`, admin-only, RLS-scoped — which is a backend change, which is
why the caveat is open rather than fixed.

---

## 5. The actual LedgerMind files

```
File:  frontend/lib/auth.ts (75 lines)                      Tier 4 — read, don't rewrite
Entry: login(email, password) · getSession() · logout()
Store: localStorage["ledgermind_token"] = JSON of StoredSession
Note:  expiresAt is computed CLIENT-SIDE from expires_in_hours. The token's
       own `exp` is what the server enforces. CAVEAT-011.

File:  frontend/components/LoginForm.tsx (72)
In:    onSuccess callback        Out: the form, or an error string
Note:  preserves the backend's no-enumeration property

File:  frontend/components/document/UploadPanel.tsx (418)   Tier 3
State: 7 form fields + submitting/submitError/lastPendingId
Props: pending, loadingPending, onRefresh, onViewHistory   ← lifted, Day 39
Note:  the root div deliberately has NO space-y-5; ArchiveStamp is absolutely
       positioned but conditionally present, and Tailwind's space-y-* would
       shift its margin onto the heading and grow the panel by 20px.

File:  frontend/components/document/ArchiveStamp.tsx (62)
In:    status: "pending"|"processing"|"done"|"failed"|null
Note:  four states because the column has four values. No invented stages.

File:  frontend/components/document/UploadHistoryTable.tsx (180)
In:    uploads, loading, onRefresh, onBack
Note:  search + status filter are useMemo'd; the table body scrolls inside a
       max-height with a sticky header — "unbounded DATA doesn't require an
       unbounded physical page"

File:  frontend/components/document/AuditLogTable.tsx (97)  Tier 3
In:    entries (from `pages` state), onJump
Note:  CAVEAT-027(d) — "Immutable System Log" over React state

File:  backend/app/api/documents.py (~180)                  Tier 4
Entry: POST /api/documents/upload   (admin)
       GET  /api/documents/pending  (admin)
Const: MAX_UPLOAD_BYTES = 50 MB · UPLOAD_DIR = /tmp/ledgermind_uploads

File:  backend/app/ingestion/gate.py (~130)                 Tier 4
Entry: check_is_financial_filing(first_pages_text) -> GateResult
Const: MIN_SCORE 6 · MIN_CATEGORIES 2 · SCAN_CHAR_LIMIT 6000
Tests: backend/tests/test_gate.py — 13 pure-function tests

File:  backend/app/ingestion/pdf_text.py (~25)
Entry: extract_first_n_pages_text(path, n=2)
Note:  plain extract_text(), NOT layout=True — the gate scores keywords, it
       does not reconstruct tables. Kept separate so the gate is not coupled
       to parser changes.

File:  backend/app/ingestion/storage.py (~90)               Tier 4
Entry: upload_file_to_storage(local_path, storage_key)
Why:   web and ingestion do not reliably share a filesystem
```

---

## 6. Deep walkthrough — one upload

**STATE BEFORE.** Admin logged in, `activeView === "upload"`, `pending` holds
prior rows, the form is filled, a PDF is selected.

**Step 1 — the client guard.**

```tsx
if (!file || !company || !ticker || !fiscalYear || !filingDate) return;
```

Five required fields; `quarter` is optional (blank = annual). The submit button
carries the **same** condition in `disabled`, so the guard is belt-and-braces
against a form submitted by Enter.

**Step 2 — multipart, built by hand.**

```tsx
const formData = new FormData();
formData.append("file", params.file);
formData.append("fiscal_year", params.fiscalYear);
…
const res = await fetch(`${API_URL}/api/documents/upload`, {
  method: "POST",
  headers: { Authorization: `Bearer ${session.accessToken}` },
  body: formData,
});
```

**Note the absent `Content-Type`.** Setting it by hand would omit the multipart
**boundary** parameter the browser generates, and the server would fail to parse
the body. Letting `fetch` see a `FormData` body makes it set
`multipart/form-data; boundary=…` itself.

**Note the camelCase→snake_case rename** at the boundary: `fiscalYear` →
`fiscal_year`. TypeScript convention on one side, Python's `Form(...)` names on
the other, translated in exactly one place.

**Step 3 — role, then extension.**

```python
user: dict = Depends(require_role("admin")),
...
if not file.filename.lower().endswith(".pdf"):
    raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")
```

**Extension only — no magic-byte check.** `SECURITY_MODEL.md` §7 records the gap
and the mitigation: *"`pdfplumber` will reject a non-PDF later"* — which happens
two steps down, at `extract_first_n_pages_text`, and surfaces as a 400.

**Step 4 — the streamed, capped write** (§4.6).

**Step 5 — two pages of text, then the gate.**

```python
try:
    first_pages_text = extract_first_n_pages_text(str(temp_path), n=2)
except Exception as e:
    temp_path.unlink(missing_ok=True)
    raise HTTPException(status_code=400, detail=f"Could not read PDF: {e}")

gate_result = check_is_financial_filing(first_pages_text)

logger.info(
    "ingestion_gate doc_id=%s filename=%s decision=%s score=%d categories=%s",
    doc_id, file.filename, gate_result.decision.value,
    gate_result.score, gate_result.matched_categories,
)
```

**Single-line log, structured, with the score and the categories.** Render
truncates multi-line tracebacks (`CLAUDE.md` §7), and this is what lets you
diagnose a rejection from logs alone.

**Step 6 — storage, keyed by tenant.**

```python
tenant_id = user["tenant_id"]
storage_key = f"{tenant_id}/{doc_id}.pdf"
```

**The tenant id is a path prefix**, so objects are namespaced in the bucket.
**Note what this is and is not:** it is *organisation*, not *isolation* — Supabase
Storage is reached with the service key, which is not tenant-scoped. The real
isolation is Postgres RLS on `pending_uploads`, one step later. **Do not read the
prefix as a boundary.**

**Step 7 — the row, inside a tenant transaction.**

```python
with db_transaction(tenant_id) as conn:
    with conn.cursor() as cur:
        cur.execute(_SQL_INSERT_PENDING, (tenant_id, storage_key, company, …))
        pending_id = cur.fetchone()[0]
```

`db_transaction` yields a **connection**, not a cursor (`CLAUDE.md` §7), and sets
`SET LOCAL app.tenant_id` (Day 14). Migration `008` puts RLS on this table, which
is why the read query is simply:

```sql
SELECT … FROM pending_uploads ORDER BY created_at DESC LIMIT 50
```

**No `WHERE tenant_id = …`.** The policy supplies it. **And the failure mode
follows from that** — forget the GUC and you get **zero rows, not an error**
(Day 14). A pending list that renders "No filings registered yet" is
indistinguishable from a genuinely empty one.

**Step 8 — back in the client.**

```tsx
const result = await uploadDocument({ … });
await onRefresh();                 // re-fetch pending BEFORE marking
setLastPendingId(result.pending_id);
resetForm();
```

**`await onRefresh()` precedes `setLastPendingId`**, and the order matters:

```tsx
const lastStatus = lastPendingId
  ? pending.find((p) => p.id === lastPendingId)?.status ?? "pending"
  : null;
```

`lastStatus` is looked up **in the refreshed list**. Setting the id first would
render the stamp from a `pending` array that does not yet contain the new row,
falling back to `?? "pending"` — right by luck, and wrong the moment the default
changes.

**Step 9 — `resetForm`, and the file input's key.**

```tsx
const [fileInputKey, setFileInputKey] = useState(0);
…
setFileInputKey((k) => k + 1);
…
<input key={fileInputKey} type="file" … />
```

**A file input's value cannot be cleared programmatically** — browsers forbid
setting `input.value` on `type="file"` for security reasons. The idiom is to
**force React to unmount and remount it** by changing its `key`, which yields a
fresh empty element.

**A React key used as a reset button.** Worth knowing; it is the standard escape
hatch for any uncontrolled DOM state.

**STATE AFTER.** A PDF in Supabase Storage under `<tenant>/<doc_id>.pdf`; one
`pending_uploads` row with `status='pending'`; the stamp reading **RECEIVED**;
the form cleared. **Nothing has been parsed, chunked, embedded or indexed. The
document is not queryable.**

---

## 7. Data flow

```
LOGIN
  LoginForm → lib/auth.login()
      POST /auth/login  {email, password}
      ← {access_token, role, tenant_id, expires_in_hours}
      localStorage["ledgermind_token"] = {accessToken, role, tenantId, expiresAt}
                                                        ▲ computed client-side
  page.tsx: setSession(getSession())
      │
      ├─ role → Sidebar's view list                GATE 1 — usability only
      ├─ accessToken → every fetch's Authorization header
      └─ tenantId → displayed in the sidebar chrome

UPLOAD (admin)
  UploadPanel form state
      → lib/api.uploadDocument()  FormData, no manual Content-Type
      → POST /api/documents/upload
             require_role("admin")           GATE 2 — THE security control
             .pdf extension                  400
             streamed write, 50 MB cap       413 mid-write
             extract_first_n_pages_text(2)   400 if unreadable
             check_is_financial_filing()     400 if REJECT
                score >= 6  AND  categories >= 2
             upload_file_to_storage()        502
             finally: unlink /tmp
             INSERT pending_uploads (RLS)    status='pending'
      ← {doc_id, pending_id, status:'pending', gate_score, message}
      → onRefresh() → GET /api/documents/pending  (admin, RLS, no WHERE)
      → setLastPendingId → ArchiveStamp reads the REFRESHED list
                                    ✋ ingestion is NOT triggered

LATER, off the request path
  python -m scripts.process_pending_uploads
      → download from Storage → pipeline → Qdrant + Postgres
      → pending_uploads.status = 'done'

READ-BACK
  page.tsx `pending` (lifted, Day 39)
      ├─ UploadPanel     first 3 rows + count
      └─ UploadHistoryTable  all rows, search + status filter

AUDIT VIEW
  page.tsx `pages` (React state)  →  AuditLogTable
      NOT audit_log. No endpoint reads audit_log.       CAVEAT-027(d)

EVERY RESPONSE
  role_filtered_response(state, role)                GATE 3 — field level,
      viewer < analyst < admin, unknown role → most restrictive
```

---

## 8. Engineering decision — store the file, record the intent, stop

**Problem.** Accept a filing from an admin on a 512 MB web tier, without letting
ingestion touch the process that serves queries.

**Decision.** Gate synchronously (cheap), store durably (Supabase), record a
`pending_uploads` row, **return**. A separate operator-run script does the work.
**ED-016.**

| Alternative | Why not |
|---|---|
| **Ingest inline in the request** | The embedding model OOM-kills a 512 MB container. `Exited with status 137`, repeatedly |
| **FastAPI `BackgroundTasks`** | Same process, same RAM. The trigger is not the constraint |
| **Celery on the same host** | Same box, same ceiling. Celery *is* deployed — as a broker-backed worker for other work — and moving ingestion there does not change the arithmetic |
| **A bigger Render tier** | Money. And the project's stated constraint is the free tier |
| **Skip the gate** | Parsing, chunking and embedding a non-filing costs the expensive resource to discover something two pages of regex answer |
| **An LLM gate** | Cost, latency, non-determinism — and it puts a probabilistic component in front of an untrusted PDF, i.e. lets the document being screened talk to the screener |
| **Auto-trigger and lie about it** | The response would say "processing" while nothing was. Directly against the mandate |

**Trade-offs accepted.**

- **The lifecycle needs a human.** A filing is stored and unqueryable until
  someone runs a script. The UI says so, twice.
- **F4 is open.** `company`, `fiscal_year`, `quarter`, `filing_date` are
  caller-asserted and never checked against the document. **Two misfilings have
  already happened.**
- **Extension check only**, no magic bytes (`SECURITY_MODEL.md` §7).
- **The gate is a keyword scorer**, so a filing that phrases things unusually can
  be rejected. Recall is traded for determinism, exactly as in the Prompt Shield.
- **`localStorage` (CAVEAT-011)**, no revocation, 2-hour window.
- **`UPLOAD_DIR.mkdir()` at import time** (CAVEAT-024).
- **No progress reporting**, because there is no producer for it — and the UI
  refuses to invent one.

**Current validity.** Correct for the constraint. Every part of it is legible.

**At 10×.** Ingestion moves to its own service with its own memory budget, the
`pending_uploads` row becomes a real queue item, and `status` gains stages —
**at which point `ArchiveStamp`'s invented progress bar becomes honest**, because
there is finally something producing the stages. **The component was written for
that day and refuses to pretend until it comes.**

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| Upload button visible to a viewer | `session.role` was edited in `localStorage`. **Not a breach** — Gate 2 returns 403 |
| 403 on upload for a real admin | The token predates the role change. Log out and back in — the claim is baked at issue |
| Login form reappears on every reload | `sessionChecked` (Day 39) or `expiresAt` arithmetic |
| Logged out early / never | Browser clock skew against the client-side `expiresAt`. The server is unaffected |
| "No filings registered yet" with rows in the table | RLS: `app.tenant_id` unset ⇒ **zero rows, not an error** |
| 413 on a small file | The cap counts **received bytes**; check for a proxy re-encoding |
| 400 "Could not read PDF" | `pdfplumber` rejected it — often a `.pdf` that is not a PDF |
| A real filing rejected by the gate | Score < 6 **or** fewer than 2 categories. The reason names which |
| Every upload rejected | `SCAN_CHAR_LIMIT` reached before any signal — a scanned PDF with no text layer yields nothing to score |
| Stamp shows RECEIVED forever | Correct. Nothing runs ingestion until someone does |
| Stamp flashes the wrong status | `setLastPendingId` before `await onRefresh()` |
| The file input keeps the old filename | `fileInputKey` no longer bumped |
| The upload panel overflows the photographed paper | `space-y-5` moved onto the root div — `ArchiveStamp` becomes child #1 and takes the margin |
| The audit view empties on reload | **Working as built.** It reads React state, not `audit_log` |

---

## 10. Hands-on experiment

### Experiment 1 — read your own session

In the browser console, logged in:

```js
JSON.parse(localStorage.getItem("ledgermind_token"))
```

You see `accessToken`, `role`, `tenantId`, `expiresAt`. Then decode the token's
own claims — **it is signed, not encrypted**:

```js
JSON.parse(atob(JSON.parse(localStorage.getItem("ledgermind_token")).accessToken.split(".")[1]))
```

**Compare `exp * 1000` with `expiresAt`.** They should be close and are computed
by different machines. Now say which one the server obeys.

### Experiment 2 — forge a role, and watch Gate 2 hold

**Do this once. It is the demonstration that makes §4.5 concrete.**

Logged in as a **non-admin** (if you have one seeded), in the console:

```js
const s = JSON.parse(localStorage.getItem("ledgermind_token"));
s.role = "admin";
localStorage.setItem("ledgermind_token", JSON.stringify(s));
location.reload();
```

**"Upload Filing" appears in the sidebar.** Now submit an upload and watch the
network panel: **403**, `Requires role 'admin' or higher`.

**Gate 1 was decoration. Gate 2 read the signed claim.** Then:

```js
localStorage.removeItem("ledgermind_token"); location.reload();
```

### Experiment 3 — score a real filing through the gate

```bash
docker compose exec -T backend python -c "
from app.ingestion.gate import check_is_financial_filing, MIN_SCORE, MIN_CATEGORIES, SIGNAL_CATEGORIES
from app.ingestion.pdf_text import extract_first_n_pages_text
import glob
print('MIN_SCORE', MIN_SCORE, '| MIN_CATEGORIES', MIN_CATEGORIES)
print('categories:', list(SIGNAL_CATEGORIES))
print()
for p in sorted(glob.glob('/app/docs/raw/*.pdf'))[:5]:
    txt = extract_first_n_pages_text(p, n=2)
    r = check_is_financial_filing(txt)
    print(f'{p.split(\"/\")[-1][:44]:46} {r.decision.value:7} score={r.score:3}  {r.matched_categories}')
"
```

**Read the spread**, not just the pass/fail. How far above 6 is a genuine filing?
That margin is what the threshold is buying.

### Experiment 4 — synthesise the two failure modes

```bash
docker compose exec -T backend python -c "
from app.ingestion.gate import check_is_financial_filing

cases = {
 'one category, high score': 'Consolidated Standalone Total income Consolidated Standalone Total Income',
 'two categories, low score': 'SEBI. Balance Sheet.',
 'genuine-looking':          'Statement of Consolidated Financial Results ... SEBI (Listing Obligations and Disclosure Requirements) Regulation 33 ... Ind AS 34 ... Chartered Accountants ... Total income',
 'a novel':                  'It was the best of times, it was the worst of times.',
}
for name, text in cases.items():
    r = check_is_financial_filing(text)
    print(f'{name:28} {r.decision.value:7} score={r.score:3} cats={r.matched_categories}')
    if r.decision.value == 'reject':
        print(f'{\"\":28} reason: {r.reason[-110:]}')
"
```

**Row 1 is the point of `MIN_CATEGORIES`.** Repetition inside one dimension does
not buy entry.

### Experiment 5 — the gate's own tests

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend \
  env PYTHONPATH=/app python -m pytest tests/test_gate.py -q
```

Thirteen tests, all pure. Then read one:

```bash
sed -n '1,40p' backend/tests/test_gate.py
```

### Experiment 6 — upload a non-filing and read the rejection

Take any non-financial PDF and upload it through the admin UI. Expect a 400 with
the score and the category count. Then find the log line:

```bash
docker compose logs --tail 200 backend | grep ingestion_gate
```

**One structured line, with `decision`, `score` and `categories`.** That is what
"log single-line" buys you.

### Experiment 7 — the pending list, with and without the GUC

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -c \
  "SELECT count(*) AS without_guc FROM pending_uploads;"

docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   SELECT count(*) AS with_guc FROM pending_uploads;"
```

**Zero, then n.** No error either time. **That silence is the failure mode**
(Day 14), and it is why the API always goes through `db_transaction(tenant_id)`.

### Experiment 8 — prove the audit view is session-local

```bash
grep -rn '@router\.\(get\|post\)' backend/app/api/*.py backend/app/auth/router.py
```

Six routes. None reads `audit_log`. Then, in the browser: run two queries, open
Audit Trail, **reload the page**, and open it again. Empty. Finally:

```bash
docker compose exec -T postgres psql -U ledgermind_app -d ledgermind -c \
  "SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
   SELECT count(*) FROM audit_log;"
```

**The durable rows exist. Nothing serves them.** CAVEAT-027(d).

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `frontend/lib/auth.ts`, `backend/app/api/documents.py` and
`backend/app/ingestion/gate.py`:

1. Who computes `expiresAt`? Name **two** ways it can disagree with the token's
   `exp`, and say what happens in each.
2. Find CAVEAT-011's comment. List the five things it records, and say which one
   a "TODO: use cookies" would have lost.
3. Name the three gates on an admin feature. Which is security, which is
   usability, and how would you demonstrate the difference in sixty seconds?
4. Follow the upload from `require_role` to the `INSERT`. Name every place the
   temp file is deleted, and every status code that can be returned.
5. Why does the gate need `MIN_CATEGORIES` as well as `MIN_SCORE`? Construct a
   document that passes one and fails the other.

---

## 12. Self-check questions

**Basic**

1. Where is the token stored, and under what key?
2. How long is a JWT valid, and what ends a session early?
3. Which two routes are admin-only?
4. What are the four `pending_uploads` statuses?
5. What are `MIN_SCORE` and `MIN_CATEGORIES`?

**Code**

6. Why is the upload size cap enforced during the write?
7. Why does `uploadDocument` not set `Content-Type`?
8. Why is `fileInputKey` bumped on reset?
9. Why does `GET /api/documents/pending` have no `WHERE tenant_id`?
10. Why does `await onRefresh()` come before `setLastPendingId`?

**Why**

11. Why is `financial_type` not a form field?
12. Why is ingestion not auto-triggered, and why would Celery not have helped?
13. Why is the gate regex rather than an LLM? Give the security reason, not just
    the cost one.
14. Why does the gate explain its rejection while the Prompt Shield does not?
15. Why is the sidebar's role check not a security control?

**Debugging**

16. An admin sees "No filings registered yet" but rows exist in the table. Walk
    the diagnosis.
17. A genuine quarterly result is rejected by the gate. What do you check, in
    order?
18. A user reports the Audit Trail "loses history". Is this a bug? Answer
    precisely.

**System design**

19. Close F4: validate `company`, `fiscal_year`, `quarter` and `filing_date`
    against the document. Where does the check go, what does it do on
    disagreement, and what does it cost?
20. Move from `localStorage` to `httpOnly` cookies. Name every file that changes
    and every **new** problem you have created.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **The client**, as `Date.now() + expires_in_hours * 3600000`. **Disagreement
   (a):** browser clock skew — a slow clock keeps a dead token, so the client
   sends it and the server 401s (surfacing as `UnauthorizedError`, which logs the
   user out anyway); a fast clock logs the user out early from a still-valid
   token. **Disagreement (b):** the server's `expires_in_hours` and the actual
   `exp` claim could diverge if one were changed without the other — the client
   would then trust a number the token does not encode. **In both cases the
   server is unaffected**, because it reads the signed `exp`.
2. **(i)** the decision — `localStorage`; **(ii)** the condition making it
   acceptable — solo-user portfolio project; **(iii)** the trigger that ends it —
   multi-user, production; **(iv)** the specific replacement — an `httpOnly`
   cookie set by the backend; **(v)** the mechanism of the risk — readable by any
   script on the page. **A "TODO: use cookies" loses (ii), (iii) and (v)** — the
   condition, the trigger and the reason — which is everything a future reader
   needs to decide whether to act.
3. **Gate 1**, the sidebar's `views` array — **usability**, because `session.role`
   is client-editable. **Gate 2**, `require_role("admin")` — **security**,
   because the role is a signature-verified JWT claim. **Gate 3**,
   `role_filtered_response` — **security**, field-level, failing closed.
   **The sixty-second demo:** edit `role` to `"admin"` in `localStorage`, reload,
   watch the Upload entry appear, submit, and read the **403**.
4. **Deletions:** on the 413 (explicit `unlink` before raising); on the PDF-read
   failure (explicit `unlink` before raising); and in the `finally` around the
   storage upload — which covers both success and the 502. **Status codes:**
   403 (role), 400 (not `.pdf`), 413 (over 50 MB), 400 (unreadable PDF), 400
   (gate REJECT), 502 (storage failed), 200 (stored, pending).
5. Because score alone is gameable **within one dimension**: repeating
   "Consolidated / Standalone / Total income" accumulates points from the single
   `financial_type` category. `MIN_CATEGORIES = 2` demands the document look like
   a filing along more than one axis. **Passes score, fails categories:** the
   repetition above. **Passes categories, fails score:** `"SEBI. Balance Sheet."`
   — two categories, 2 + 2 = 4 < 6.

### §12 — Basic

1. `localStorage`, key `"ledgermind_token"`, as JSON of a `StoredSession`.
2. **2 hours** (HS256, `exp`). Early ends: the client-side `expiresAt` check in
   `getSession`, an explicit `logout()`, or a 401 that clears the session.
   **Nothing revokes server-side.**
3. `POST /api/documents/upload` and `GET /api/documents/pending`. (`GET
   /api/metrics` is the third admin-only route — Day 44.)
4. `pending`, `processing`, `done`, `failed`.
5. `MIN_SCORE = 6`, `MIN_CATEGORIES = 2`. (`SCAN_CHAR_LIMIT = 6000`.)

### §12 — Code

6. Because `Content-Length` is client-supplied and can lie. Counting received
   bytes aborts a 4 GB upload at 50 MB + one chunk and deletes the partial file.
7. Because the browser must generate the multipart **boundary** parameter.
   Setting the header by hand omits it and the server cannot parse the body.
8. Because a file input's `value` cannot be set programmatically. Changing the
   `key` forces React to unmount and remount it, producing a fresh empty element.
9. Because RLS on `pending_uploads` (migration `008`) supplies the predicate from
   `app.tenant_id`, which `db_transaction(tenant_id)` sets with `SET LOCAL`.
10. Because `lastStatus` is looked up **in `pending`**. Setting the id first
    renders the stamp against a list that does not yet contain the new row, so it
    falls through to `?? "pending"` — correct by accident.

### §12 — Why

11. Because it is **not a property of the document**. One annual report contains
    both standalone and consolidated statements in different sections, so only
    per-section content can decide it — the Trap 1 rule: classify from content,
    never from filename or user input.
12. Because loading `bge-small-en-v1.5` in-process **OOM-killed** Render's 512 MB
    tier (`Exited with status 137`, repeatedly). **Celery does not help** because
    the constraint is RAM in one container, not the trigger mechanism: a
    `BackgroundTask` and a Celery worker on the same box load the same model into
    the same 512 MB.
13. **Security reason:** an LLM gate would let the untrusted PDF *talk to the
    thing screening it* — prompt-injecting the gatekeeper with the document you
    are trying to smuggle in. Same argument as the Prompt Shield's: no
    probabilistic component in the compliance path. (Cost, latency and
    determinism are the other three.)
14. **Different threats.** A user uploading the wrong PDF is making a mistake and
    is helped by knowing which threshold failed. A user probing an injection
    filter is not making a mistake, and any feedback is a signal for tuning the
    next attempt.
15. Because `userRole` comes from `session.role` in `localStorage`, which the
    client can edit. It decides what is **shown**; the token's signed claim
    decides what is **served**.

### §12 — Debugging

16. **(1)** Is the user actually admin? A non-admin gets 403, not an empty list.
    **(2)** Network panel: is `/api/documents/pending` returning `200` with
    `{"pending_uploads": []}`, or failing? **(3)** If 200-and-empty, this is the
    **RLS zero-rows** signature — confirm by querying with and without
    `SET app.tenant_id` (Experiment 7). **(4)** If rows appear only with the GUC,
    the request path lost `db_transaction(tenant_id)`. **(5)** Also check the
    obvious: `CLAUDE.md` §7 — **which database?** compose overrides
    `DATABASE_URL` to the local Postgres, and rows inserted against Supabase are
    not there. **State which one you queried.**
17. **(1)** Run it through `check_is_financial_filing` directly and read `score`
    and `matched_categories` — the reason string names which threshold failed.
    **(2)** If score is 0, check whether `extract_first_n_pages_text` returned
    anything: a scanned PDF with no text layer scores nothing, and that is a
    **parsing** problem, not a gate problem. **(3)** If text exists but the score
    is low, check whether the signals are on pages 3+ — `n=2` and
    `SCAN_CHAR_LIMIT = 6000` are the window, and the gate scan window is part of
    open audit finding **F9**. **(4)** Only then consider the patterns.
18. **Not a bug — it is working as built, and the label is wrong.** The view
    renders `pages`, which is React state; it is cleared on sign-out and does not
    survive a reload, and the empty state says so ("in the current workspace
    session"). **What is wrong is one line of static copy**: the subtitle
    "Immutable System Log" asserts a durability property the data does not have —
    **CAVEAT-027(d)**. The durable `audit_log` exists, is append-only by grant,
    and **has no read endpoint**, so the frontend cannot show it today.

### §12 — System design

19. **Where.** In `api/documents.py`, immediately after the gate — the text is
    already extracted and no expensive work has happened yet.
    **What it does.** Extend `gate.py` with a `check_metadata_agreement(text,
    asserted)` returning per-field `agree | disagree | not_found`: search the
    first two pages for the company name via `entity_resolver`'s alias table
    (Day 31's single source, not a second list), for the fiscal-year and quarter
    patterns `entity_resolver` already parses, and for a date near "filing" or
    "board meeting".
    **On disagreement.** **Reject with the specific mismatch** — the same
    disclosure policy as the gate itself, and for the same reason: this is a user
    mistake. **On `not_found`, accept**: absence is not disagreement, and
    refusing on absence would reject every filing whose cover page is a scan.
    **Cost.** Recall. A legitimately named entity the alias table does not carry
    would be rejected — which is why `not_found` must not be treated as
    disagreement, and why the alias coverage floor (0.5) exists elsewhere in this
    system.
    **What it does not fix.** Two documents already misfiled. F4 is prevention;
    the existing rows need `purge_orphaned_metrics`-style reconciliation, which
    is a separate, approval-gated operation.
20. **Files that change. Backend:** `auth/router.py` sets the cookie via
    `Response.set_cookie(httponly=True, secure=True, samesite="lax")` instead of
    returning the token in the body; `auth/dependencies.py` reads the cookie
    instead of `HTTPBearer` — and note that this **breaks `Depends(bearer_scheme)`
    for every route at once**; `main.py`'s CORS needs
    `allow_credentials=True` with an explicit origin list, since `*` is illegal
    with credentials. **Frontend:** `lib/auth.ts` loses `accessToken` from
    `StoredSession` entirely; every `fetch` drops its `Authorization` header and
    adds `credentials: "include"`; `logout()` becomes a **server call**, because
    the client can no longer delete the cookie.
    **New problems created.** **(1) CSRF** — a cookie is sent automatically, so
    `POST /api/query` becomes forgeable from another origin without `SameSite`
    and probably a CSRF token. **(2)** `role` and `tenantId` are still needed by
    the UI and can no longer ride in the same store, so they need a
    `GET /auth/me`. **(3) `CAVEAT-012`** — CORS currently allows every
    `*.vercel.app` origin, and `allow_credentials=True` with a wildcard-ish origin
    list is a much worse combination than it is today. **(4)** Local development
    across `localhost:3000` → `localhost:8000` needs `secure` relaxed, so the dev
    and prod paths diverge. **(5)** `eval_runner.py` and every script authenticate
    with a bearer token and would all need changing.
    **The honest summary:** it closes token theft by XSS and opens CSRF, and the
    total work is larger than the caveat's one-line phrasing suggests. **That is
    why the comment records a trigger rather than a TODO.**

---

## 14. MUST REMEMBER

```text
- The token lives in localStorage["ledgermind_token"] — CAVEAT-011
- expiresAt is computed CLIENT-SIDE; the server enforces the token's own `exp`.
  The client check is UX, not a control
- A JWT is SIGNED, NOT ENCRYPTED. The exposure is THEFT, not secrecy
- No revocation, no refresh, no server-side logout. The 2-HOUR WINDOW is the
  mitigation
- THREE gates: sidebar (usability) · require_role (SECURITY) ·
  role_filtered_response (SECURITY, field-level, fails closed)
- The sidebar's role check is trivially bypassed and that is FINE
- Upload is admin-only. 50 MB cap enforced DURING the write, not from
  Content-Length. Temp file unlinked on EVERY exit path
- The gate is DETERMINISTIC REGEX: MIN_SCORE 6 AND MIN_CATEGORIES 2, over
  SCAN_CHAR_LIMIT 6000 chars (~2 pages)
- TWO thresholds, because score alone is gameable within one category
- The gate EXPLAINS its rejection; the Prompt Shield does NOT. Different
  threats, opposite disclosure policies
- financial_type is NOT a form field — content decides it (Trap 1).
  company / fiscal_year / quarter / filing_date ARE caller-asserted and
  unchecked — audit F4, and it has misfiled a document twice
- Upload STOPS at `pending`. ED-016: the embedding model OOM-killed a 512 MB
  container (exit 137 = SIGKILL). Celery would not have helped — the
  constraint is RAM, not the trigger
- pending_uploads is RLS-scoped, so the SELECT has no WHERE. Forget the GUC
  and you get ZERO ROWS, NOT AN ERROR
- ArchiveStamp has FOUR states because the column has four values. It refuses
  to invent stages the backend cannot report
- The Audit Trail view renders REACT STATE, not audit_log. No endpoint reads
  audit_log. CAVEAT-027(d)
```

## 15. MUST UNDERSTAND

```text
- Why a client-side expiry may be WRONG without being a VULNERABILITY, and
  what makes that true (the server never trusts it)
- Why the localStorage comment is a model for recording a trade-off: decision,
  condition, trigger, replacement, mechanism
- Why "the client decides what is SHOWN, the token decides what is SERVED" is
  the whole of frontend authorization
- Why refusing to distinguish a wrong email from a wrong password is the
  frontend REFRAINING from undoing a backend property
- Why an LLM gate in front of an untrusted PDF is a security decision, not a
  cost one
- Why rejecting on `not_found` would be wrong when validating metadata, and
  why disagreement and absence must be different outcomes
- Why a lifecycle that stops and SAYS SO is more honest than one that says
  "processing" while nothing runs
- Why ArchiveStamp's missing progress bar is the mandate applied to TIME
```

---

## 16. This connects to

```text
Day  7 — bcrypt, authenticate_user
Day  9 — require_role, role_filtered_response, failing closed
Day 14 — SET LOCAL, RLS, and zero-rows-not-an-error
Day 40 — the render boundary, and dead code
   ↓
Day 41 — auth state, upload, admin              ← END OF PHASE 11
   ↓
Day 42 — the Prompt Shield and the security model   ← PHASE 12 BEGINS
```

Forward references:

- The **other** regex gate, and why it discloses nothing → **Day 42**
- Indirect injection via corpus content, which this gate raises the bar against
  and does not close → **Day 42**
- `tests/test_gate.py` among the 218 → **Day 43**
- `audit_log`, the durable thing the Audit Trail view is not → **Day 44**
- `Exited with status 137`, and every decision traceable to 512 MB → **Day 45**
