# Day 08 — JWT and Dependency Injection

**Phase 2 · Weight: H (~120 min) · Prerequisites: Day 7**

---

## 1. Today's goal

By tonight you can:

- Decode a JWT by hand and explain each of its three parts.
- Explain — and demonstrate — that **a JWT is signed, not encrypted**, and say
  what follows for what you may put in one.
- Explain FastAPI's dependency injection: what `Depends` does, *when* it runs,
  and what happens when a dependency raises.
- Explain why authentication is a **dependency** here and CORS is **middleware**,
  and why that is not arbitrary.

---

## 2. Why now

Day 7 issued a token. Today it is carried and verified. This is also the day
`Depends(get_current_user)` — which you have now seen on four endpoints without
explanation — finally means something.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Headers | Day 4 | The token rides in `Authorization` |
| Dependencies run before handlers | Day 4 | Today is *why* |
| `create_access_token` | Day 7 | The token being verified |
| 401 vs 403 | Day 4 | Both appear today |

---

## 4. Concept lesson

### 4.1 The problem: request 2 does not remember request 1

HTTP is **stateless**. The server that just authenticated you has, by the next
request, no idea who you are. Something must be carried.

**Approach 1 — send credentials every time.** The password crosses the network on
every request, and the server runs a ~250 ms bcrypt check every time (Day 7).
Both are unacceptable.

**Approach 2 — server-side sessions.** On login, generate a random session id,
store `{session_id → user}` server-side, send the id in a cookie.

That is a good design and is still the right answer for many systems. Its costs:

- The server must **store** state, and every request reads it.
- With more than one server instance, that store must be **shared** — Redis, or
  sticky sessions.
- A restart with an in-memory store logs everyone out.

**Approach 3 — a signed token.** Put the facts *in* the token, and sign it so it
cannot be altered. The server stores nothing; it **verifies**.

**Mental model.** A session id is **a cloakroom ticket** — meaningless by itself;
the cloakroom holds the coat. A JWT is **a signed ID card** — it carries the
facts, and the signature is what makes them trustworthy.

---

### 4.2 What a JWT is, exactly

Three base64url-encoded parts joined by dots:

```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 . eyJzdWIiOiJhYmMiLCJyb2xlIjoiYWRtaW4ifQ . 4Xy7_gK...
└──────────── HEADER ────────────────┘ └────────── PAYLOAD ──────────────────┘ └ SIGNATURE ┘
```

**Header** — the algorithm:

```json
{"alg": "HS256", "typ": "JWT"}
```

**Payload** — the claims. LedgerMind's:

```json
{"sub": "9f3c...", "tenant_id": "1a2b...", "role": "admin",
 "iat": 1755000000, "exp": 1755007200}
```

`sub` (subject) and `iat`/`exp` are standard claims; `tenant_id` and `role` are
this application's own.

**Signature** — `HMAC-SHA256(base64(header) + "." + base64(payload), JWT_SECRET)`.

---

### 4.3 The sentence to memorise

> **A JWT is signed, not encrypted.**

Base64 is an **encoding**, not encryption. Anyone holding the token can decode
the payload and read every claim. What they cannot do is **change** a claim,
because any change invalidates the signature and they cannot produce a new one
without `JWT_SECRET`.

**What follows, practically:**

- **Never put a secret in a claim.** No passwords, no API keys, no PII you would
  not print.
- The claims are **assertions the server made**, and verifying the signature is
  what makes them trustworthy — not the fact that they are in the token.
- The token is a **bearer** credential: whoever holds it *is* you. Hence a short
  lifetime, and hence `CAVEAT-011` about `localStorage`.

---

### 4.4 Expiry, and the price of statelessness

```python
ACCESS_TOKEN_EXPIRE_HOURS = 2
```

**The cost of statelessness: you cannot revoke a token.** There is no server-side
record to delete. A stolen token works until `exp` passes, and nothing you do —
changing the password, disabling the account — stops it.

Two hours is the **mitigation**, and it is a genuine trade-off:

- **Shorter** → smaller stolen-token window, more frequent re-login. With no
  refresh-token mechanism (there is none here), that is real friction.
- **Longer** → better usability, bigger window.

Two hours suits a research tool used in working sessions. A consumer product
would use a short access token plus a refresh token, and accept the server-side
state that requires.

---

### 4.5 Dependency injection

**The problem.** Several endpoints need the same preparatory work — verify a
token, produce a user. Options:

**Call it manually in each handler.** Works, and can be *forgotten*. A new
endpoint that omits the call is silently unauthenticated. The failure is
invisible.

**Middleware.** Runs on every request automatically. But it cannot easily run
*selectively* (`/health` and `/auth/login` must stay open), and its result cannot
be **passed into** the handler as a typed value — it must be smuggled through
`request.state`.

**Dependency injection.** Declare what you need in the signature:

```python
async def execute_query(
    payload: QueryRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
```

FastAPI resolves `get_current_user` **before** the handler body, and passes its
return value in. Selective, typed, and visible in the signature.

**Mental model.** `Depends(f)` means: **"run `f` first, and if it raises, my
function never happens."**

**Why this beats manual calls.** Forgetting a dependency is *visible* — the
handler simply has no `current_user` parameter, and any code using it fails
immediately at import or first call. Forgetting a manual call is *invisible*.

---

## 5. The actual LedgerMind files

### `backend/app/core/security.py` — the JWT half

```python
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 2

def create_access_token(user_id: str, tenant_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    # Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError on failure --
    # caller (dependencies.py) is responsible for turning these into HTTP 401.
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
```

**`datetime.now(timezone.utc)`, not `datetime.now()`.** A naive local datetime
would encode a wall-clock time with no zone, and expiry would be wrong by the
UTC offset — silently, and only for deployments outside UTC. `CLAUDE.md` §7 notes
that Render logs are UTC and the shell is IST: **a 5.5-hour discrepancy is
exactly the size of bug this prevents.**

**`algorithms=[ALGORITHM]` on decode — a list, and it must be.** This is the
defence against the classic JWT attack: an attacker crafts a token whose header
says `"alg": "none"`, and a decoder that trusts the *token's own header* accepts
it unsigned. Passing an explicit allow-list means the header's claim is checked
against what you permit, not obeyed.

**The comment about who raises what** is the module boundary being stated
explicitly: this module knows nothing about HTTP. It raises library exceptions;
`dependencies.py` maps them to status codes. `security.py` is reusable from a
script or a test with no FastAPI in sight.

---

### `backend/app/auth/dependencies.py`

```
File:        backend/app/auth/dependencies.py (56 lines)
Purpose:     Turn an Authorization header into a verified user dict,
             and enforce role minimums
Who imports it: api/query.py, api/documents.py, api/metrics.py
What it imports: jwt, FastAPI bits, decode_access_token
Entry points: get_current_user, require_role(minimum)
Boundary:    Establishes IDENTITY (get_current_user) and enforces a
             ROLE MINIMUM (require_role). Field-level filtering is Day 9.
```

---

## 6. Deep code walkthrough

### 6.1 `get_current_user`

```python
bearer_scheme = HTTPBearer()

def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Decodes and validates the JWT, attaches the payload to request.state.user
    so downstream dependencies (get_db_conn) can read tenant_id without
    re-decoding the token. This dependency must run before get_db_conn on
    every protected route.
    """
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired, please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

    user = {"user_id": payload["sub"],
            "tenant_id": payload["tenant_id"],
            "role": payload["role"]}
    request.state.user = user
    return user
```

**STATE BEFORE.** An HTTP request with (perhaps) an `Authorization` header.

**Step 1 — `Depends(bearer_scheme)`.** A dependency **inside** a dependency.
`HTTPBearer()` parses the header, requires the `Bearer ` prefix, and raises 403
if the header is missing entirely. It also registers the scheme in the OpenAPI
document, which is what puts the **Authorize** button on `/docs`.

**Step 2 — decode and verify.** `jwt.decode` recomputes the signature over
header+payload with `JWT_SECRET` and compares. Mismatch → `InvalidSignatureError`
(a subclass of `InvalidTokenError`). It also checks `exp` automatically.

**Step 3 — two exceptions, two messages:**

| Exception | Message |
|---|---|
| `ExpiredSignatureError` | "Token expired, please log in again" — **actionable** |
| `InvalidTokenError` | "Invalid token" — deliberately vague |

Note the asymmetry with Day 7's login, where both failures shared one message.
Here it is safe to distinguish: "expired" reveals nothing an attacker did not
already know (they hold the token and can read its `exp` themselves), and it
tells a legitimate user exactly what to do. Nothing is leaked; usability is
gained.

`ExpiredSignatureError` is a **subclass** of `InvalidTokenError`, so the order of
the `except` blocks matters. Reverse them and expiry is swallowed by the generic
branch, and every expired session reports "Invalid token" — a real usability
regression from a one-line ordering mistake.

**Step 4 — `request.state.user = user`.** `request.state` is a scratchpad living
for one request. This lets a *later* dependency read `tenant_id` without
re-decoding. **Read the docstring carefully, though:**

> so downstream dependencies (**get_db_conn**) can read tenant_id without
> re-decoding the token

**There is no `get_db_conn` in this codebase.** Grep for it: nothing. The
docstring describes an intended design that was not built — `quant_engine` and
`audit_writer` open their own connections and set the tenant themselves. So
`request.state.user` is written and, on the query path, never read.

That is not a bug. It is **documentation drift**, and it is the same class you
met on Day 5 (`db/session.py`'s docstring asserting an assumption
`api/query.py` breaks). Day 2's rule applies: *the code is the authority*.

**STATE AFTER.** The handler receives a three-key dict:
`{"user_id", "tenant_id", "role"}`.

**What breaks if the dependency is removed?** The endpoint becomes public. No
error, no warning — the handler simply has no `current_user` and any use of it
raises a `NameError` at call time. If the handler happened not to use it (a
metrics endpoint, say), it would silently serve anonymous traffic. This is why
`require_role` composes `get_current_user` rather than duplicating it.

---

### 6.2 `require_role` — a dependency **factory**

```python
ROLE_RANK = {"viewer": 0, "analyst": 1, "admin": 2}

def require_role(minimum_role: str):
    """
    Route-level RBAC. Usage: Depends(require_role("analyst"))
    Role hierarchy: viewer(0) < analyst(1) < admin(2) -- higher roles pass
    checks for lower minimums.
    """
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if ROLE_RANK[user["role"]] < ROLE_RANK[minimum_role]:
            raise HTTPException(403, f"Requires role '{minimum_role}' or higher")
        return user
    return checker
```

**`require_role` is not a dependency.** It is a function that *returns* one. That
is why the usage is `Depends(require_role("admin"))` — with the call — and not
`Depends(require_role)`.

**Why a factory?** `Depends` takes a callable and calls it with no arguments you
control. To parameterise it, you close over the parameter — `minimum_role` is
captured by `checker` and fixed at import time, when the route is declared.

**Composition, not duplication.** `checker` itself declares
`Depends(get_current_user)`. FastAPI resolves the whole chain:

```
route handler
  └─ Depends(require_role("admin")) → checker
       └─ Depends(get_current_user)
            └─ Depends(bearer_scheme)  → HTTPBearer
```

So `require_role` **cannot** be used without authentication. There is no way to
call the role check on an unverified user, because the role comes from a verified
token by construction.

**A hierarchy, not a set.** `<` rather than `!=` means an admin passes an
`analyst` minimum. The alternative — exact-match roles — would require listing
every acceptable role at every endpoint, and adding a role would mean editing
every route.

**The sharp edge.** `ROLE_RANK[user["role"]]` is an unguarded dictionary access.
A role in the database that is not in this dict raises `KeyError` → **500**.
Is that a bug? Consider the alternative: `.get(user["role"], 0)` would treat an
unknown role as `viewer` and let the request through. **A 500 is the safer
failure** — it fails closed, loudly.

But note the *inconsistency*: `role_filtered_response` (Day 9) handles the same
situation by returning the most restrictive payload rather than crashing. Two
layers, two different answers to "unknown role". Both fail closed; only one is
graceful.

---

## 7. Data flow

```
POST /auth/login                                        (Day 7)
   └─ create_access_token(user_id, tenant_id, role)
        │  payload = {sub, tenant_id, role, iat, exp}
        │  jwt.encode(payload, JWT_SECRET, HS256)
        ▼
   "eyJhbGci....eyJzdWIi....4Xy7_gK"
        │
        ▼  lib/auth.ts → localStorage                   (CAVEAT-011)
        │
        ▼  every later request
   Authorization: Bearer eyJhbGci....
        │
        ▼
   HTTPBearer                    header missing → 403
        │
        ▼
   decode_access_token           bad signature → 401 "Invalid token"
        │                        exp passed     → 401 "Token expired"
        ▼
   {"user_id", "tenant_id", "role"}
        │
        ├─► request.state.user   (written; not read on the query path)
        │
        ├─► require_role("admin")  → rank check → 403 if too low
        │
        └─► the handler
              ├─ tenant_id → QueryState → SET LOCAL app.tenant_id   (Day 14)
              ├─ tenant_id → Qdrant payload filter                  (Day 27)
              ├─ user_id   → audit_log row                          (Day 44)
              └─ role      → role_filtered_response                 (Day 9)
```

**The whole security model hangs off three claims** — and `tenant_id`'s journey
is the one that matters most, because of `CAVEAT-001`: the request body can
override it before any of that happens.

---

## 8. Engineering decision — stateless JWT with a 2-hour life

**Problem.** Recognise a caller on request 2 without storing session state, on a
platform where the process restarts freely.

**Decision.** HS256 JWT carrying `sub`, `tenant_id`, `role`; two-hour expiry;
verified by a FastAPI dependency.

| Alternative | Why not |
|---|---|
| **Server-side sessions** | Requires a shared store (Redis is present but is the Celery broker only) and survives no restart in-memory. Genuinely better for revocation — see below |
| **RS256 (asymmetric)** | Lets verifiers hold only a public key. Valuable when *several services* verify. Here one service both signs and verifies, so it is complexity with no consumer |
| **Access + refresh tokens** | The correct answer for a consumer product. Needs a stored refresh token, a rotation endpoint, and revocation — real infrastructure for a six-account tool |
| **API keys** | No expiry, no claims, no standard verification |

**Trade-offs accepted.**

- **No revocation.** This is the real cost, and it is unmitigated except by the
  two-hour window. Changing a password does not invalidate an issued token.
- **`localStorage` on the client** (`CAVEAT-011`) — readable by any script on
  the page, so an XSS becomes a session theft. `lib/auth.ts`'s own comment says
  an httpOnly cookie is the hardening path.
- **`JWT_SECRET` is a single point of compromise.** Leak it and anyone can mint
  an admin token for any tenant. It is the only value in `.env` with that
  property.

**Current validity.** Appropriate at this scope. Note that `Settings.JWT_SECRET`
has **no default** — `core/config.py` declares it bare, so the application
refuses to start without it. That is the same discipline as `GEMINI_MODEL`
(Day 19): a security-critical value must never have a plausible fallback.

**At 10×.** Refresh tokens, a revocation list (which reintroduces server state —
the trade-off inverts), per-tenant secrets, and RS256 if a second service ever
needs to verify.

---

## 9. Failure modes

| Symptom | Cause | Distinguishing feature |
|---|---|---|
| 403 with "Not authenticated" | Header missing entirely | Raised by `HTTPBearer`, before decode |
| 401 "Invalid token" | Bad signature, malformed, or wrong secret | Login itself still works |
| 401 "Token expired" | Past `exp` | Fixed at 2 h after issue |
| 403 "Requires role 'admin' or higher" | Valid token, insufficient role | Identity is fine |
| **All** requests 401 after redeploy | `JWT_SECRET` changed | **Login succeeds; everything after fails** |
| 500 on a valid token | Role in the DB not in `ROLE_RANK` | `KeyError` — fails closed, ungracefully |
| Expiry off by hours | Naive datetime | Prevented by `timezone.utc` |
| Every expired token says "Invalid token" | `except` blocks in the wrong order | `ExpiredSignatureError` is a subclass |

---

## 10. Hands-on experiment

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@alpha.ledgermind.test","password":"<password>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo ${#TOKEN}
```

### Experiment 1 — decode it by hand

```bash
python3 - "$TOKEN" <<'PY'
import sys, base64, json
tok = sys.argv[1]
h, p, s = tok.split('.')
pad = lambda x: x + '=' * (-len(x) % 4)
print("HEADER :", json.dumps(json.loads(base64.urlsafe_b64decode(pad(h))), indent=2))
print("PAYLOAD:", json.dumps(json.loads(base64.urlsafe_b64decode(pad(p))), indent=2))
print("SIGNATURE (first 20):", s[:20], "...")
PY
```

**No secret was needed.** You just read every claim. Internalise that: a JWT is
signed, not encrypted.

### Experiment 2 — tamper with a claim

```bash
python3 - "$TOKEN" <<'PY'
import sys, base64, json
tok = sys.argv[1]
h, p, s = tok.split('.')
pad = lambda x: x + '=' * (-len(x) % 4)
payload = json.loads(base64.urlsafe_b64decode(pad(p)))
print("original role:", payload["role"])
payload["role"] = "admin"
payload["tenant_id"] = "00000000-0000-0000-0000-000000000000"
newp = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
print(f"{h}.{newp}.{s}")     # same signature, different payload
PY
```

Take the printed token and use it:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/metrics \
  -H "Authorization: Bearer <paste the forged token>"
```

**401.** You could *read* the claims and *change* them, and the signature no
longer matches. That is the entire security property, demonstrated.

### Experiment 3 — the `alg: none` attack, and why it fails

```bash
docker compose exec -T backend python -c "
import jwt, os
tok = jwt.encode({'sub':'x','tenant_id':'y','role':'admin'}, None, algorithm='none')
print('unsigned token minted:', tok[:60], '...')
from app.core.security import decode_access_token
try:
    decode_access_token(tok)
    print('ACCEPTED  <- would be a critical vulnerability')
except Exception as e:
    print('REJECTED :', type(e).__name__, e)
"
```

Rejected — because `decode_access_token` passes `algorithms=["HS256"]` as an
explicit allow-list rather than trusting the token's own header.

### Experiment 4 — expiry, without waiting two hours

```bash
docker compose exec -T backend python -c "
import jwt, time
from datetime import datetime, timedelta, timezone
from app.core.config import settings
from app.core.security import decode_access_token, ALGORITHM

now = datetime.now(timezone.utc)
expired = jwt.encode({'sub':'u','tenant_id':'t','role':'viewer',
                      'iat': now - timedelta(hours=3),
                      'exp': now - timedelta(hours=1)},
                     settings.JWT_SECRET, algorithm=ALGORITHM)
try:
    decode_access_token(expired)
except Exception as e:
    print('->', type(e).__name__, ':', e)
"
```

`ExpiredSignatureError`. Now note it is a **subclass** of `InvalidTokenError`:

```bash
docker compose exec -T backend python -c "
import jwt
print(jwt.ExpiredSignatureError.__mro__[:3])
"
```

That is why the `except` order in `dependencies.py` is load-bearing.

### Experiment 5 — the role hierarchy, end to end

Log in as `viewer@alpha.ledgermind.test` and try an admin endpoint:

```bash
VTOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"viewer@alpha.ledgermind.test","password":"<password>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -o /dev/null -w "viewer -> /api/documents/pending : %{http_code}\n" \
  http://localhost:8000/api/documents/pending -H "Authorization: Bearer $VTOKEN"
curl -s -o /dev/null -w "admin  -> /api/documents/pending : %{http_code}\n" \
  http://localhost:8000/api/documents/pending -H "Authorization: Bearer $TOKEN"
```

403 then 200. The identity was valid in both cases; only the **rank** differed.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/core/security.py` and `backend/app/auth/dependencies.py`:

1. Why is `algorithms=[ALGORITHM]` a **list**, and what attack does that shape
   prevent?
2. Why `datetime.now(timezone.utc)` rather than `datetime.now()`? Name the
   concrete discrepancy this project would otherwise hit.
3. `require_role` returns a function. Why can it not simply *be* the dependency?
4. `ROLE_RANK[user["role"]]` is unguarded. What happens on an unknown role, and
   why is that arguably the right choice?
5. Find the docstring claim about `request.state.user` and a downstream
   dependency. Grep for that dependency. What do you conclude?

---

## 12. Self-check questions

**Basic**
1. What are the three parts of a JWT?
2. Is a JWT encrypted?
3. What does `sub` mean, and what does LedgerMind put there?
4. How long does a token last?
5. What does `Depends(f)` do?

**Code**
6. What raises 403 when the `Authorization` header is missing entirely?
7. Which two exceptions does `get_current_user` catch, and why does their order
   matter?
8. What three keys are in the returned user dict?
9. Why is `require_role` a factory?
10. Where does `JWT_SECRET` come from, and what happens if it is unset?

**Why**
11. Why can a JWT not be revoked, and what mitigates that here?
12. Why is auth a dependency rather than middleware?
13. Why does `security.py` raise library exceptions instead of `HTTPException`?
14. Why is it safe to distinguish "expired" from "invalid" when Day 7 refused to
    distinguish its two failures?
15. Why is a role **hierarchy** better than exact-match roles here?

**Debugging**
16. Login succeeds; every subsequent request 401s. Two candidate causes, and how
    to tell them apart in one command.
17. A valid admin token returns 500 on a protected route. What is the most likely
    cause?
18. Users report being logged out "randomly, about every two hours". Bug?

**System design**
19. Add token revocation on password change. Describe the mechanism and name what
    it costs you.
20. A second service must verify these tokens. What changes, and why is the
    current choice fine until then?

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. So the **allow-list is yours**, not the token's. The classic JWT attack sends
   a token whose header claims `"alg": "none"`; a decoder that trusts the token's
   own header accepts it unsigned. Passing an explicit list means the header is
   *checked against* what you permit rather than obeyed. (It is a list because
   the API supports rotating between algorithms.)
2. `datetime.now()` returns a **naive** local time with no zone. `exp` would then
   encode a wall-clock instant that means different things in different
   deployments. Concretely: `CLAUDE.md` §7 records that **Render logs are UTC and
   the shell is IST** — a 5.5-hour offset, which is exactly the size of silent
   expiry bug this prevents.
3. Because `Depends` calls the callable with no arguments you control. To
   parameterise the check with a minimum role, you must close over it — so
   `require_role("admin")` is evaluated at import time, returning a zero-argument
   dependency with the minimum captured.
4. `KeyError` → **500**. Arguably right because the alternative,
   `.get(role, 0)`, treats an unknown role as `viewer` and **lets the request
   through**. A 500 fails closed and loudly. Worth noting the inconsistency
   though: `role_filtered_response` handles the same case gracefully by returning
   the most restrictive payload. Both fail closed; only one is graceful.
5. The docstring says `request.state.user` exists "so downstream dependencies
   (**get_db_conn**) can read tenant_id without re-decoding the token".
   **`get_db_conn` does not exist** — grep finds nothing. `quant_engine` and
   `audit_writer` open their own connections and set the tenant themselves. So
   the docstring describes an intended design that was not built, and
   `request.state.user` is written and never read on the query path. Conclusion:
   documentation drift, of the same class as `db/session.py`'s docstring from
   Day 5. The code is the authority.

### §12 — Basic

1. Header, payload, signature — base64url-encoded, dot-separated.
2. **No.** Signed. Anyone can decode and read it; nobody can alter it without the
   secret.
3. "Subject" — the standard claim for who the token is about. LedgerMind puts
   `user_id` there.
4. Two hours (`ACCESS_TOKEN_EXPIRE_HOURS = 2`).
5. Runs `f` before the handler and passes its return value in as that parameter.
   If `f` raises, the handler never runs.

### §12 — Code

6. `HTTPBearer()` — before any decoding happens.
7. `jwt.ExpiredSignatureError` then `jwt.InvalidTokenError`. Order matters because
   the first is a **subclass** of the second; reversed, the generic handler
   catches expiry and every expired session reports "Invalid token".
8. `user_id`, `tenant_id`, `role`.
9. Because `Depends` takes a callable it invokes with no arguments you control,
   so parameterisation must come from a closure.
10. `settings.JWT_SECRET`, via `pydantic-settings` reading `.env`. It is declared
    **with no default**, so the application refuses to start without it — the
    same discipline as `GEMINI_MODEL`.

### §12 — Why

11. Because there is no server-side record to delete — the token *is* the
    credential and verification is purely cryptographic. Mitigated only by the
    two-hour expiry. Changing a password does **not** invalidate issued tokens.
12. Because auth must be **selective** (`/health` and `/auth/login` are open) and
    its **result must be passed into the handler** as a typed value. Middleware
    can do neither cleanly — it would have to smuggle the user through
    `request.state`, which is exactly the pattern whose downstream consumer here
    turned out not to exist.
13. To keep the module free of HTTP concerns, so it is usable from a script or a
    test with no FastAPI present. The comment states the contract: the caller
    turns these into 401s.
14. Because "expired" reveals nothing an attacker does not already have — they
    hold the token and can read its `exp` themselves (Experiment 1). Meanwhile it
    tells a legitimate user precisely what to do. Nothing leaks; usability is
    gained. Day 7's case was different: distinguishing "no such user" from "wrong
    password" would reveal **which email addresses exist**.
15. Because with exact-match, every endpoint must list every acceptable role, and
    adding a role means editing every route. A hierarchy states a minimum once,
    and higher roles pass automatically.

### §12 — Debugging

16. (a) **`JWT_SECRET` changed** — a redeploy regenerated it, so previously
    issued tokens fail signature verification. (b) **Clock skew or expiry** — the
    token is genuinely past `exp`. Tell them apart in one command: decode the
    token client-side (Experiment 1) and read `exp`. If `exp` is in the future,
    it is the secret; if in the past, it is expiry. (The response messages also
    differ: "Invalid token" vs "Token expired".)
17. The `role` value stored in the database is not a key in `ROLE_RANK` — a
    `KeyError` in `require_role`. Would occur if a role were added to the `users`
    table's `CHECK` constraint without adding it to `ROLE_RANK`. Two copies of
    one fact, in two languages.
18. **No — that is the design.** `ACCESS_TOKEN_EXPIRE_HOURS = 2`, and there is no
    refresh mechanism, so a session ends exactly two hours after login. The
    frontend's `getSession()` proactively clears an expired token, which is why
    it presents as a logout rather than an error.

### §12 — System design

19. Add a `token_version` integer to the `users` row and include it as a claim.
    `get_current_user` then reads the current version from the database and
    rejects the token if the claim does not match; changing a password increments
    it. **What it costs:** a database read on **every authenticated request** —
    which reintroduces exactly the server-side state JWTs were chosen to avoid.
    The trade-off inverts. (A denylist of revoked token ids has the same cost
    plus a cleanup job.) Note also that `CAVEAT-013` already opens a connection
    per statement, so this would compound a known inefficiency.
20. Switch from **HS256 to RS256**. HS256 is symmetric — the same secret signs
    and verifies — so every verifying service would need the ability to *mint*
    tokens too. RS256 signs with a private key and verifies with a public one, so
    a second service can verify without being able to issue. The current choice
    is fine because one service does both, and RS256 would be key-management
    complexity with no consumer.

---

## 14. MUST REMEMBER

```text
- A JWT IS SIGNED, NOT ENCRYPTED. Anyone can read the claims
- Three parts: header . payload . signature
- Claims here: sub (user_id), tenant_id, role, iat, exp
- 2-hour expiry. There is NO revocation
- algorithms=[ALGORITHM] as an allow-list — defeats the alg:none attack
- datetime.now(timezone.utc), never naive
- ExpiredSignatureError is a SUBCLASS of InvalidTokenError — except order matters
- Depends(f) = "run f first; if it raises, my function never happens"
- require_role is a FACTORY; it composes get_current_user, never duplicates it
```

## 15. MUST UNDERSTAND

```text
- Why statelessness buys scale and costs revocation, and that the 2-hour
  window is the only thing standing in for revocation here
- Why a dependency beats both a manual call (forgettable, invisibly) and
  middleware (not selective, result not injectable)
- Why security.py raises library exceptions and dependencies.py maps them —
  a module boundary you can point at
- Why distinguishing failure messages is safe here and unsafe on Day 7
- Why an unguarded ROLE_RANK lookup failing with a 500 is the SAFER choice
```

---

## 16. This connects to

```text
Day 7 — issuing identity
   ↓
Day 8 — carrying and verifying it                ← you are here
   ↓
Day 9 — what that identity is ALLOWED to see, field by field
```

Forward references:

- `role` → `role_filtered_response` → **Day 9**
- `tenant_id` → `SET LOCAL app.tenant_id` and RLS → **Day 14**
- `tenant_id` → the Qdrant payload filter → **Day 27**
- `user_id` → the audit row → **Day 44**
- `CAVEAT-001` (the body overrides `tenant_id` before any of this) → **Day 14**
- `CAVEAT-011` (`localStorage`) → **Day 41**
- The full threat model → **Day 42**
