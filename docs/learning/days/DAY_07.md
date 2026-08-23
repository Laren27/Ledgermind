# Day 07 — Authentication: Hashing, bcrypt, and Not-passlib

**Phase 2 — Identity and permission · Weight: M (~90 min) · Prerequisites: Day 5**

---

## 1. Today's goal

By tonight you can:

- Explain hashing versus encryption, and why a password store must use the first.
- Explain what a salt is, what a cost factor is, and why bcrypt is *deliberately
  slow*.
- Read `auth/service.py` and explain the **one place in this application that
  queries the database with no tenant scope**, and the policy that makes it safe.
- Explain why a database failure during login returns **503, not 500**, and who
  acts on that distinction.

---

## 2. Why now

You can now get a request to a handler (Day 4) with a validated body (Day 5).
Everything after this point in the API is gated on identity. Day 8 needs a token
to verify; today is where the token is issued.

There is also a Day 5 thread to pull: `LoginRequest` was the first Pydantic model
you read. Today you follow what happens to its two fields.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| Request body → Pydantic model | Day 5 | `LoginRequest` |
| `response_model` validates output | Day 5 | `TokenResponse` |
| 401 vs 403, 500 vs 503 | Day 4 | Both distinctions appear today |

---

## 4. Concept lesson

### 4.1 The problem: you must check a password without storing one

**The naive approach.** Store passwords in a column. Compare on login.

**Why it fails.** Any database read — a backup, a leaked dump, a SQL injection,
a curious employee — yields every user's password. And because people reuse
passwords, you have leaked credentials for systems you have never heard of.

**Second attempt: encryption.** Encrypt the password, decrypt to compare.
**Still wrong**, and it is worth being precise about why: encryption is
*reversible by design*, so the key must exist somewhere the application can
reach. Compromise the application, get the key, decrypt everything. You have
added a step, not a defence.

**The insight.** You do not need the password. You need to know *whether a
presented password is the same one*. That is a strictly weaker requirement, and
it can be met with a **one-way** function.

---

### 4.2 Hashing

**What it is.** A function that maps input to a fixed-size output such that:

- the same input always gives the same output,
- a different input almost certainly gives a different output,
- **you cannot go backwards.**

**Mental model.** Hashing is **grinding coffee**. Same beans, same grounds. You
can grind a second batch and compare. You cannot reconstitute the beans.

**Third attempt: SHA-256.** Fast, one-way. Better — and still broken, for two
reasons.

**Problem 1: no salt.** Identical passwords produce identical hashes. An attacker
with a dump sees which users share a password, and can precompute a table of
common passwords once and match against every user at no extra cost.

**Problem 2: speed.** SHA-256 is *designed* to be fast — that is a virtue for
file integrity and a catastrophe for passwords. A GPU computes billions per
second, so guessing is cheap.

---

### 4.3 bcrypt: salt and cost, built in

**Salt.** A random value generated per password and mixed into the hash. Two
users with the same password get different hashes. Precomputed tables become
useless — an attacker must attack each hash separately.

The salt is not a secret. It is **stored inside the hash string**:

```
$2b$12$N9qo8uLOickgx2ZMRZoMye  IjZAgcfl7p92ldGxad68LJZdL17lhWy
└┬┘└┬┘ └──────────┬──────────┘ └──────────────┬──────────────┘
 │   │             │                           │
 │   │             salt (22 chars)             the hash
 │   cost factor: 2^12 = 4096 rounds
 algorithm
```

Everything needed to verify is in that one string, which is why the `users` table
needs only a `password_hash` column and no separate salt column.

**Cost factor.** bcrypt runs its key-derivation `2^cost` times. Higher cost =
slower = more expensive to guess. `bcrypt.gensalt()` defaults to 12 (4,096
rounds), which takes roughly 200–300 ms.

**That slowness is the feature.** A user logging in waits 250 ms once. An
attacker guessing needs 250 ms *per guess per hash*, and the cost is tunable
upward as hardware improves — without changing any stored hash, because the cost
is recorded in the hash itself.

---

### 4.4 The `verify` contract

```python
def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"),
                          password_hash.encode("utf-8"))
```

`checkpw` extracts the algorithm, cost and salt from the stored hash, hashes the
candidate with those parameters, and compares. The comparison is
**constant-time** — it does not return early on the first differing byte, so an
attacker cannot learn how much of a guess was right from timing.

---

## 5. The actual LedgerMind files

### `backend/app/core/security.py`

```
File:        backend/app/core/security.py (41 lines)
Purpose:     Password hashing and JWT creation/verification
Why it exists: Route handlers must not implement token or hash handling
Who imports it: auth/service.py, auth/dependencies.py
What it imports: bcrypt, jwt, datetime, settings
Entry points: verify_password, hash_password, create_access_token,
             decode_access_token
Boundary:    This module establishes IDENTITY. Authorization is elsewhere.
```

**The module docstring is a warning, and it is unusual enough to read in full:**

```python
"""
Password hashing and JWT helpers.

NOTE: We use the `bcrypt` library directly, NOT passlib. passlib's CryptContext
reads bcrypt.__about__.__version__ to detect the backend version, which was
removed in bcrypt>=4.1 -- this breaks passlib's bcrypt backend on any current
pip install. Calling bcrypt.hashpw/checkpw directly avoids the dependency
entirely. Do not re-add passlib for this.
"""
```

**Why this matters beyond the trivia.** passlib is the near-universal
recommendation in FastAPI tutorials. Someone reading this file and "improving" it
to match the tutorials would break login on the next clean install — and the
failure is an `AttributeError` deep inside a dependency, which reads like a
packaging problem rather than a decision. The docstring converts an invisible
constraint into a visible one. **That is what a good module docstring does.**

---

### `backend/app/auth/service.py`

```
File:        backend/app/auth/service.py (69 lines)
Purpose:     Authenticate an email/password pair and issue a token
Who imports it: auth/router.py
What it imports: db_transaction, verify_password, create_access_token
Data in:     email, password
Data out:    {access_token, role, tenant_id}, or raises 401 / 503
```

---

## 6. Deep code walkthrough

### 6.1 `authenticate_user`, and the exception in the RLS rule

```python
def authenticate_user(email: str, password: str) -> dict:
    """
    Looks up a seeded user by email and verifies password.

    Uses db_transaction(tenant_id=None) -- this is the ONE place in the app
    that queries with no RLS tenant context set. It relies on the
    auth_bootstrap_lookup policy (migration 006) which only permits SELECT
    when app.tenant_id is unset. Do not reuse this pattern elsewhere.
    """
```

**STATE BEFORE.** Two strings from a validated `LoginRequest`. **No identity
established. No tenant known.**

**And there is the chicken-and-egg problem.** Every other table in this system is
protected by Row-Level Security scoped to `app.tenant_id` (Day 14). But the
tenant id lives *in the users row you have not found yet*. You cannot scope the
lookup by a value the lookup is supposed to produce.

**The resolution.** Migration 006 defines a policy — `auth_bootstrap_lookup` —
that permits `SELECT` on `users` **only when `app.tenant_id` is unset**. That is
the precise inverse of every other policy in the schema, and it is deliberately
narrow: the moment a tenant context exists, this policy stops applying and normal
isolation resumes.

So `db_transaction(tenant_id=None)` is not a hole. It is a **named, single-purpose
exception with a matching database policy**, and the docstring's last sentence —
*"Do not reuse this pattern elsewhere"* — is the guard rail.

**Step 1 — the lookup:**

```python
with db_transaction(tenant_id=None) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, tenant_id, role, password_hash FROM users WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()
```

`%s` with a **parameter tuple** — not string formatting. psycopg2 sends the query
and the parameters separately, so `email` is never parsed as SQL. This is the
only defence against SQL injection that actually works, and it is used
consistently throughout this codebase (Day 33).

**Step 2 — the error handler, and why it is one line:**

```python
except psycopg2.Error as e:
    # Single-line by design. Render's log stream truncates multi-line
    # tracebacks, which cost several rounds of debugging on 2026-07-30
    # while chasing intermittent login 500s under concurrent load.
    # pgcode names the Postgres error class exactly, with no traceback.
    logger.error(
        "LOGIN DB FAILURE pgcode=%s pgerror=%s exc=%s msg=%s",
        getattr(e, "pgcode", None),
        (getattr(e, "pgerror", "") or "").replace("\n", " ")[:300],
        type(e).__name__,
        str(e).replace("\n", " ")[:300],
    )
```

**Every element is a response to a measured failure.**

- **Single line.** Render's log viewer truncates multi-line output, so a
  traceback arrives as its first line — usually the least informative one.
- **`pgcode`.** The Postgres error class (`53300` = too many connections,
  `57P03` = cannot connect now). It names the failure *exactly*, without a
  traceback.
- **`.replace("\n", " ")`** on every interpolated value, so an embedded newline
  in the message cannot break the single-line property.
- **`[:300]`** so one enormous error cannot flood the log.

**Step 3 — 503, not 500:**

```python
    # 503, not 500: a transient database failure is retryable and is not
    # a defect in the request. The eval runner and the frontend can both
    # act on that distinction; a 500 tells them nothing.
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication temporarily unavailable. Please retry.",
    )
```

This is a **design decision about information**, not about politeness. A 500
means "this request is broken; retrying is pointless". A 503 means "the request
was fine; the system is momentarily not". Those lead to different client
behaviour, and collapsing them destroys something a caller could have used.

**Step 4 — one message for two different failures:**

```python
if row is None:
    raise HTTPException(401, "Invalid email or password")

user_id, tenant_id, role, password_hash = row

if not verify_password(password, password_hash):
    raise HTTPException(401, "Invalid email or password")
```

**The same message, deliberately.** "No such user" and "wrong password" are
different conditions with **identical responses**, because distinguishing them
tells an attacker which email addresses are registered — a *user enumeration*
vulnerability. It is also why `EmailStr` validation would buy nothing here
(Day 5): an invalid address must produce the same 401 as a valid unknown one.

**A residual leak worth naming honestly.** These two paths take different
*amounts of time*: the "no such user" branch skips `verify_password` and returns
in a few milliseconds, while the wrong-password branch spends ~250 ms in bcrypt.
A patient attacker can distinguish them by timing. The standard mitigation is to
hash against a dummy hash when the user is not found. **This codebase does not do
that.** It is not in `CAVEAT`s either — so it is a genuine, small, unrecorded
gap, and Day 42 is where you decide whether it is worth recording.

**Step 5 — issue the token:**

```python
token = create_access_token(user_id=str(user_id), tenant_id=str(tenant_id), role=role)
return {"access_token": token, "role": role, "tenant_id": str(tenant_id)}
```

**STATE AFTER.** The caller holds a signed token asserting who they are, which
tenant they belong to, and what role they hold. Everything from Day 8 onward
reads that token.

**Note what is NOT returned:** `password_hash`, and any other user column. The
`TokenResponse` model (Day 5) enforces this on the way out — a handler that
accidentally returned the hash would fail its own `response_model`.

---

### 6.2 `hash_password`, and where it is *not* used

```python
def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
```

`bcrypt.gensalt()` generates a fresh random salt at cost 12 **every call**. Hash
the same password twice, get two different strings — both of which verify.

**Where is it called from?** Nowhere in the request path. There is no
registration endpoint. Users are seeded by `sql/migrations/007_seed_users.sql`
with pre-computed hashes. That is a deliberate scope decision — this is a
research tool for a known set of analysts, not a consumer product — and it is why
`README.md` lists six seeded accounts across two tenants.

**The consequence:** there is no password *change* path either. Adding one means
adding an endpoint, a `require_role` decision, and a re-hash — and `hash_password`
already exists for exactly that day.

---

## 7. Data flow

```
Browser: LoginForm.tsx
   │  {email, password}
   ▼
POST /auth/login                       ← no auth required; it IS the auth
   │
   ▼
LoginRequest (Pydantic)                ← 422 if malformed        (Day 5)
   │
   ▼
authenticate_user(email, password)
   │
   ├─ db_transaction(tenant_id=None)   ← THE ONE unscoped query
   │     └─ SELECT ... WHERE email = %s
   │           ├─ psycopg2.Error → log with pgcode → 503
   │           └─ row is None     → 401 "Invalid email or password"
   │
   ├─ verify_password(plain, hash)     ← bcrypt.checkpw, ~250 ms, constant-time
   │     └─ False → 401, SAME message
   │
   └─ create_access_token(user_id, tenant_id, role)
         │  HS256 over settings.JWT_SECRET               (Day 8)
         ▼
TokenResponse {access_token, token_type, role, tenant_id, expires_in_hours}
   │
   ▼
lib/auth.ts → localStorage["ledgermind_token"]           (CAVEAT-011, Day 41)
   │
   ▼
every later request: Authorization: Bearer <token>       (Day 8)
```

---

## 8. Engineering decision — bcrypt directly, and no registration

**Problem.** Verify a password without storing one, on a stack where a
dependency choice must survive a clean `pip install` in 2027.

**Decision.** `bcrypt` called directly; cost 12; users seeded by migration.

| Alternative | Why not |
|---|---|
| **passlib `CryptContext`** | The standard recommendation, and **broken**: it reads `bcrypt.__about__.__version__`, removed in bcrypt ≥ 4.1. Would fail on any current install |
| **Argon2** | Genuinely better (memory-hard, resists GPU attacks more strongly). Costs another dependency, and bcrypt at cost 12 is not the weak link here |
| **SHA-256 + manual salt** | You would be implementing bcrypt, badly. Wrong iteration count, wrong comparison, wrong salt handling |
| **An identity provider (Auth0, Cognito)** | Right answer for a real product. Overkill for six seeded accounts, and adds a paid external dependency to a ₹0 stack |

**Trade-offs accepted.**

- **No registration and no password change.** Correct for the scope; a real
  blocker for anything else. `hash_password` exists ready for that day.
- **Cost 12 is a latency choice.** ~250 ms per login. Raising it improves
  security and slows login; the value is not currently measured against anything.
- **A timing side-channel** between "unknown user" and "wrong password" —
  unmitigated and unrecorded.

**Current validity.** Appropriate. The weak link in this system's auth is not
bcrypt; it is `CAVEAT-001` (body-supplied `tenant_id`) and `CAVEAT-011`
(`localStorage`).

**At 10× / real users.** Registration, password reset, rate limiting on
`/auth/login` (there is none — Day 42), account lockout, and probably an
identity provider.

---

## 9. Failure modes

| Symptom | Cause | Note |
|---|---|---|
| 401 on correct credentials | Wrong tenant's account, or the seed migration did not run | Both 401 paths look identical **by design** |
| 503 from `/auth/login` | Transient DB failure | **Retryable.** Check `/health` and the `pgcode` in the logs |
| 500 from `/auth/login` | Something other than `psycopg2.Error` — e.g. a malformed stored hash | Not the retryable path |
| `AttributeError: __about__` | Someone re-added passlib | Read the module docstring |
| Login works, later requests 401 | Token expired (2 h) or `JWT_SECRET` changed | Day 8 |
| Login slow (~250 ms) | bcrypt cost 12 | **Working as intended** |
| Everything 401 after a redeploy | `JWT_SECRET` regenerated — all tokens invalid | Day 8 |

---

## 10. Hands-on experiment

### Experiment 1 — the same password, two hashes

```bash
docker compose exec -T backend python -c "
from app.core.security import hash_password, verify_password
a = hash_password('correct horse battery staple')
b = hash_password('correct horse battery staple')
print('hash A:', a)
print('hash B:', b)
print('identical?', a == b)
print('A verifies:', verify_password('correct horse battery staple', a))
print('B verifies:', verify_password('correct horse battery staple', b))
print('wrong pw  :', verify_password('wrong', a))
"
```

Different strings, both verify. That is the salt, and it is why a leaked dump
cannot be attacked with one precomputed table.

### Experiment 2 — read the anatomy of a hash

```bash
docker compose exec -T backend python -c "
from app.core.security import hash_password
h = hash_password('x')
print('full  :', h)
print('algo  :', h[:4])
print('cost  :', h[4:6], '->', 2**int(h[4:6]), 'rounds')
print('salt  :', h[7:29])
print('digest:', h[29:])
"
```

Everything needed to verify lives in the string. Hence no salt column.

### Experiment 3 — feel the cost factor

```bash
docker compose exec -T backend python -c "
import time, bcrypt
for cost in (4, 8, 12, 14):
    s = bcrypt.gensalt(rounds=cost); t = time.perf_counter()
    bcrypt.hashpw(b'password', s)
    print(f'cost {cost:2d} ({2**cost:6d} rounds): {(time.perf_counter()-t)*1000:8.1f} ms')
"
```

Each step doubles. Cost 4 is instant and useless; cost 14 makes login sluggish.
12 is the compromise — and now you can defend it with numbers.

### Experiment 4 — both failures, one response

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"nobody@nowhere.test","password":"x"}'
echo
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@alpha.ledgermind.test","password":"definitely-wrong"}'
```

Byte-identical bodies. Now time them:

```bash
for e in nobody@nowhere.test admin@alpha.ledgermind.test; do
  curl -s -o /dev/null -w "$e  %{time_total}s\n" -X POST http://localhost:8000/auth/login \
    -H "Content-Type: application/json" -d "{\"email\":\"$e\",\"password\":\"wrong\"}"
done
```

**The second is measurably slower** — it reached bcrypt. That is the timing
side-channel from §6.1, observed in your own terminal.

### Experiment 5 — the bootstrap policy is real

```bash
docker compose exec -T backend python -c "
import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = c.cursor()
cur.execute(\"SELECT count(*) FROM users\")
print('with NO tenant set :', cur.fetchone()[0], '<- the bootstrap policy allows this')
cur.execute(\"SET app.tenant_id = '00000000-0000-0000-0000-000000000000'\")
cur.execute(\"SELECT count(*) FROM users\")
print('with a tenant set  :', cur.fetchone()[0], '<- normal isolation resumed')
c.close()
"
```

The count **drops** once a tenant context exists. The exception applies only in
the unset case, exactly as the docstring claims.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/auth/service.py` and `backend/app/core/security.py`:

1. Why does the login lookup use `db_transaction(tenant_id=None)` when every
   other query in the app passes a real tenant?
2. Find every place the error log line defends its single-line property. There
   are three. Why does each matter?
3. Both 401s use the same message. Name the vulnerability that prevents.
4. Why is `hash_password` never called by any endpoint? Where do the hashes come
   from?
5. `create_access_token` receives `str(user_id)` and `str(tenant_id)`. Why the
   explicit `str()` on values that came from a database?

---

## 12. Self-check questions

**Basic**
1. Hashing vs encryption — one sentence each.
2. What is a salt, and where is it stored?
3. What does the cost factor control?
4. Why does `/auth/login` require no authentication?
5. What three things does the login response return besides the token?

**Code**
6. Which function verifies a password, and what does it do internally?
7. What does `db_transaction(tenant_id=None)` permit, and what makes that safe?
8. Which exception type triggers the 503 branch?
9. What is `pgcode` and why log it?
10. Where does `ACCESS_TOKEN_EXPIRE_HOURS` live?

**Why**
11. Why bcrypt directly instead of passlib?
12. Why is bcrypt deliberately slow?
13. Why 503 rather than 500 on a database failure?
14. Why do both failure branches return the same message?
15. Why is there no registration endpoint?

**Debugging**
16. `/auth/login` returns 503 intermittently under load. Where do you look, and
    what specifically do you read?
17. All users get 401 after a redeploy, with correct passwords. Most likely
    cause?
18. `AttributeError: module 'bcrypt' has no attribute '__about__'`. What
    happened?

**System design**
19. Add a password-change endpoint. List everything that must be decided or
    built, including one thing that does not exist yet in this codebase.
20. `/auth/login` has no rate limiting. Describe the attack that enables, and
    say what the bcrypt cost factor does and does not do about it.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. Chicken and egg: the tenant id lives *in the users row the lookup has not
   found yet*, so the query cannot be scoped by a value it exists to produce.
   Migration 006's `auth_bootstrap_lookup` policy permits `SELECT` on `users`
   **only when `app.tenant_id` is unset** — the exact inverse of every other
   policy, and narrow enough that normal isolation resumes the moment a tenant
   context exists.
2. (a) `.replace("\n", " ")` on `pgerror`, (b) the same on `str(e)`, and (c)
   `[:300]` truncation on both. The first two stop an embedded newline in a
   database message from breaking the single-line property; the third stops one
   enormous error from flooding the log. All three exist because Render's log
   stream truncates multi-line output, so a traceback arrives as its least
   informative line.
3. **User enumeration.** Different messages would let an attacker discover which
   email addresses are registered by observing which produce "no such user".
4. There is no registration endpoint. Users are seeded by
   `sql/migrations/007_seed_users.sql` with pre-computed hashes — a deliberate
   scope decision for a tool serving a known set of analysts.
   `hash_password` exists ready for the day a change-password path is added.
5. psycopg2 returns `user_id` and `tenant_id` as Python `UUID` objects, not
   strings. `jwt.encode` must serialise the payload to JSON, and JSON has no
   UUID type (Day 5) — so the conversion has to happen somewhere. Doing it
   explicitly at the boundary is clearer than relying on a serialiser's
   behaviour.

### §12 — Basic

1. **Hashing** is one-way: you cannot recover the input. **Encryption** is
   reversible with a key — which means the key must exist somewhere reachable.
2. A per-password random value mixed into the hash so identical passwords produce
   different hashes. It is **not secret** and is stored inside the hash string
   itself.
3. The number of key-derivation rounds, `2^cost`. It makes hashing deliberately
   expensive, and it is recorded in each hash so it can be raised over time
   without invalidating existing hashes.
4. It is the endpoint that *establishes* authentication. Requiring a token to get
   a token is circular.
5. `token_type` (`"bearer"`), `role`, `tenant_id`, and `expires_in_hours` — five
   fields in total including the token.
6. `verify_password` → `bcrypt.checkpw`. It extracts algorithm, cost and salt
   from the stored hash, hashes the candidate with those exact parameters, and
   compares in **constant time**.
7. A `SELECT` on `users` with no tenant scope. Safe because migration 006's
   policy only permits it while `app.tenant_id` is unset, and because it is used
   in exactly one place with a docstring saying not to reuse it.
8. `psycopg2.Error` — and only that. Anything else propagates and becomes a 500,
   which is correct: a malformed stored hash is a defect, not a transient outage.
9. The Postgres error class code (e.g. `53300` too many connections, `57P03`
   cannot connect now). It names the failure exactly, in one token, with no
   traceback — which is what survives Render's log truncation.
10. `backend/app/core/security.py`, as `ACCESS_TOKEN_EXPIRE_HOURS = 2`. Note
    `TokenResponse.expires_in_hours` defaults to `2` **independently** — two
    copies of one fact (Day 5, §11 Q5).

### §12 — Why

11. passlib's `CryptContext` reads `bcrypt.__about__.__version__` to detect the
    backend version, and that attribute was **removed in bcrypt ≥ 4.1**. passlib's
    bcrypt backend therefore breaks on any current install. Calling
    `bcrypt.hashpw`/`checkpw` directly avoids the dependency entirely.
12. Because an attacker's cost is per-guess. A user waits 250 ms once; an
    attacker waits 250 ms for every guess against every hash. A fast hash gives
    the attacker billions of guesses per second on commodity GPUs.
13. Because a transient database failure is **retryable** and is not a defect in
    the request. The eval runner and the frontend behave differently on each; a
    500 destroys that information.
14. To prevent **user enumeration** — an attacker must not be able to learn which
    email addresses exist. (It is also why `EmailStr` would add nothing here.)
15. Scope. Six accounts across two tenants, seeded by migration, serving a
    single-developer research tool. A registration endpoint implies email
    verification, rate limiting, and abuse handling — none of which this system
    needs yet.

### §12 — Debugging

16. The **container logs**, for the single-line `LOGIN DB FAILURE` entry, and
    specifically its **`pgcode`**. `53300` means too many connections — which
    points at `CAVEAT-013` (a new psycopg2 connection is opened per statement,
    and one `growth_comparison` query opens four plus the audit write). `57P03`
    means the database is starting up or shutting down. Then `/health`.
17. **`JWT_SECRET` changed.** Every previously issued token now fails signature
    verification, so every request 401s while the passwords themselves are fine.
    Distinguishing feature: *login itself succeeds* and only subsequent requests
    fail (Day 8).
18. Someone re-added passlib — probably while "modernising" the auth module to
    match a FastAPI tutorial. The module docstring exists specifically to prevent
    this.

### §12 — System design

19. Decide: does the user supply the current password (yes — otherwise a stolen
    token becomes a permanent account takeover)? Build: a `POST /auth/password`
    endpoint; `Depends(get_current_user)` so only the authenticated user can
    change their own; verify the old password with `verify_password`; hash the
    new one with `hash_password`; `UPDATE users SET password_hash = %s` — which
    **is** granted (`SELECT, INSERT, UPDATE`). **The thing that does not exist
    yet:** a way to invalidate the tokens already issued. JWTs are stateless and
    cannot be revoked, so an attacker holding a token keeps access for up to two
    hours after the password change. Solving that needs a revocation list or a
    token version claim — genuinely new infrastructure.
20. **Credential stuffing / brute force.** An attacker can attempt unlimited
    logins against a known email. The bcrypt cost factor limits the attacker to
    roughly four attempts per second **per core of your server** — it makes the
    attack *expensive for your server* as much as for them, so it doubles as a
    denial-of-service vector. What it does **not** do is stop the attack, bound
    total attempts, or protect a weak password. Real mitigations: per-IP and
    per-account rate limiting, exponential backoff, account lockout, and
    monitoring. None exist here — see Day 42, and note that `IMPLEMENTATION_DELTAS.md`
    records per-tenant rate limiting as **specified and not built**.

---

## 14. MUST REMEMBER

```text
- Hashing is one-way; encryption is reversible. Passwords need the first
- bcrypt stores algorithm + cost + salt INSIDE the hash string
- Cost 12 = 4096 rounds ≈ 250 ms. The slowness IS the feature
- bcrypt is called DIRECTLY. Never re-add passlib (bcrypt.__about__ is gone)
- db_transaction(tenant_id=None) is used in EXACTLY ONE place: the login lookup
- Both 401 branches return the SAME message — user enumeration
- 503 not 500 on a DB failure: retryable vs defective
- Log single-line with pgcode — Render truncates multi-line tracebacks
```

## 15. MUST UNDERSTAND

```text
- Why encryption is the wrong tool even though it "works"
- Why the login lookup is a chicken-and-egg problem, and how a database POLICY
  resolves it rather than an application exception
- Why a status code is an information-preserving decision
- Why identical messages can still leak through TIMING — and that this codebase
  has that leak, unmitigated and unrecorded
- Why a module docstring that says "do not do X" is doing real work
```

---

## 16. This connects to

```text
Day 6 — the response, streamed
   ↓
Day 7 — issuing identity                       ← you are here
   ↓
Day 8 — carrying and verifying it: JWT + dependency injection
   ↓
Day 9 — what that identity is ALLOWED to see
```

Forward references:

- `create_access_token` in full → **Day 8**
- `db_transaction` and `SET LOCAL` → **Day 11**
- The `auth_bootstrap_lookup` policy → **Day 14**
- The `users` table and migration 006 → **Day 16**
- `localStorage` (`CAVEAT-011`) → **Day 41**
- Rate limiting, and the timing leak → **Day 42**
- `pgcode 53300` and per-statement connections (`CAVEAT-013`) → **Day 14**
