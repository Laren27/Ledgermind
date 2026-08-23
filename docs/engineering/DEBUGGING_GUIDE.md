# How to Debug LedgerMind

Written from the incidents recorded in `CLAUDE.md`, `docs/RUNBOOK.md` and
`docs/IMPLEMENTATION_DELTAS.md`. Every "signature" below is a real observation,
not a hypothetical.

---

## 0. The four rules that come before any debugging

These exist because environment-vs-code confusion has cost more time on this
project than application defects have.

1. **Never patch blind.** Diagnose from real output before writing any fix.
2. **When a diagnostic contradicts a stated prediction, stop.** Do not continue
   past it.
3. **When a fix does not work, stop tuning the number and go measure.**
4. **Cause cannot be assigned from a single before/after pair.** This was
   attempted three times in one session and was wrong every time. The instrument
   that settled each: *three runs, with provider and model printed per run.*

And one corollary specific to this codebase: **an empty candidate set is a
network signature; a low-scoring one is a retrieval signature.** Establish which
you have before theorising.

---

## 1. Pre-flight — run this before trusting any measurement

```bash
# (a) which code is actually running?
docker compose exec -T backend python -c "import app.engines.retriever as m, inspect; print(m.__file__)"

# (b) which environment?
docker compose exec -T backend printenv GEMINI_MODEL
docker compose exec -T backend printenv QDRANT_URL     # MUST be the https Cloud URL
docker compose exec -T backend printenv DATABASE_URL   # local Docker, NOT Supabase (see below)

# (c) warm the process — a fresh exec costs ~4s cold; loop 5 and read the later ones
```

**Known-bad signatures:**

| What you see | What it means |
|---|---|
| `UserWarning: Api key is used with an insecure connection` | You are on **local Docker Qdrant**, not Cloud. Every measurement is invalid. |
| `UserWarning: Failed to obtain server version` | `qdrant_client` failed its construction-time probe. The next query in that process will die. |
| `QDRANT_URL=http://qdrant:6333` | Same as above — small local collection. |
| A local semantic failure on a **cold** process | Not a defect until it reproduces warm. A fresh `docker compose exec` carries ~30 s of fastembed/ONNX load; warm calls are 0.36–0.41 s. |

**Always state which database a measurement came from.** `docker-compose.yml:51`
overrides `DATABASE_URL` to the local Postgres, so the running stack does **not**
read the Supabase URL in `.env`. They are different databases with different
document counts.

---

## 2. Tracing one request end to end

The fastest path is the **SSE trace**, because it is a byproduct of real
execution — a node cannot forget to report itself.

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"...","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
echo ${#TOKEN}          # an empty token must fail loudly, not silently

curl -N -X POST localhost:8000/api/query/stream \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"What was ETERNAL'\''s consolidated revenue for FY26?"}'
```

You will see one `node` event per completed graph node, then one `complete`
event with the full role-filtered response. **Log in as `admin`** — `latency_ms`,
per-node `duration_ms`, `llm_provider`, `llm_model` and `reranker_backend` are
admin-tier only.

Then read the logs for the same request:

```bash
docker compose logs -f backend | grep -E "SemanticEngine|QuantEngine|CrossEngine|Router|Audit log written"
```

`audit_writer` logs one summary line per request with `request_id`, `path`,
`latency_ms`, `confidence`, `provider` and `model`.

---

## 3. "The answer is wrong" — decide which kind of wrong first

This is the most important triage in the system, because the three kinds have
nothing in common.

```text
Is there a number in the answer?
├── YES, and it came with a ✓ (sql_verified)
│     → WRONG NUMBER: the extraction or the DSL is at fault. Go to §4.
├── YES, but sql_verified is false
│     → The number came from PROSE, not the database. Go to §5.
└── NO (it is a text answer)
      → WRONG TEXT: retrieval or synthesis. Go to §5.
```

Read `path`, `sql_verified`, `dsl_object`, `confidence_tier` and `error` from
the response before forming any theory. As an analyst or admin you get all of
them.

---

## 4. Wrong number (quantitative path)

**Step 1 — is the DSL right?** Look at `dsl_object` in the response.

| Symptom | Likely cause |
|---|---|
| `metric` is not what you asked for | Metric substitution — the model was forced to pick something (CAVEAT-004). Check whether a Stage 0/0b guard *should* have fired. |
| `fiscal_year` you never mentioned | Period invention. If `period_assumed: true`, the system detected it and disclosed. If not, the guard missed. |
| `operation: point_in_time` on a "did X decline?" question | The DSL prompt has a rule for this; it lost. Prompt edits need approval (`CLAUDE.md` §1.5). |
| `entity` / `comparison_entity` swapped | Router extracts one company; comparison ops preserve the model's own pairing (`quant_engine.py:239-244`). |

**Step 2 — is the SQL right?** `sql_query` is in the response verbatim. Run it
yourself — but **`SET app.tenant_id` first**, or RLS silently returns 0 rows:

```sql
SET app.tenant_id = 'a0000000-0000-0000-0000-000000000001';
SELECT value, metric, fiscal_year, quarter, financial_type, filing_date, unit, doc_id
FROM financials
WHERE tenant_id = %s AND company = 'ETERNAL' AND metric = 'revenue'
  AND fiscal_year = 'FY26' AND financial_type = 'consolidated'
  AND is_latest = TRUE AND quarter IS NULL;
```

**Zero rows is not evidence that data is missing.** It is equally consistent
with a forgotten GUC.

**Step 3 — is the stored value right?** If the SQL is right and the value is
wrong, the defect is in extraction, and the tool is:

```bash
docker compose exec -T backend python -m scripts.regression_check 2>&1 | tee /tmp/regcheck.log | tail -12
grep -A 4 "Records extracted" /tmp/regcheck.log
grep "IDENTITY FAIL"   /tmp/regcheck.log
grep "DISCARDED ROW"   /tmp/regcheck.log
grep "METRIC TIE"      /tmp/regcheck.log
```

**Run it ONCE and tee.** Each run re-parses all five corpus PDFs including the
371-page Zomato annual report; re-running it to see a different slice is what
exhausted WSL2's RAM on 2026-08-02.

Three log lines are your highest-value signals:

- **`[DISCARDED ROW]`** — two distinct source rows resolved to one canonical
  metric and first-wins threw one away. *Real defect every time.* The fix is
  normally an alias in `registry.py` so the two stop colliding.
- **`[METRIC TIE]`** — two aliases matched at the same word count; the winner
  was chosen by registry declaration order. This is how PAYTM's `tax_expense`
  ended up holding the *deferred* tax figure for weeks.
- **`[IDENTITY FAIL]`** — a balance/P&L identity does not hold. Above 5% the
  ingest *refuses to load*.

The long `Unknown metric: … storing as-is` stream is the documented as-is
storage path, **not** errors.

**Step 4 — after any extraction change**, run the orphan check:

```bash
docker compose exec -T backend python -m scripts.purge_orphaned_metrics    # DRY RUN
```

The loader retires rows by full business key *including* `metric`, so a name the
extractor stops emitting is never retired and stays `is_latest = TRUE` forever.
Orphans are a maintenance obligation of extraction changes, not a loader bug.
**Never pass `--apply` without approval.**

---

## 5. Wrong text (semantic / cross path)

**Step 1 — did retrieval find the right pages?** Read `citations`. Each carries
`page_number`, `company`, `fiscal_year` and `reranker_score`.

**Read `reranker_backend` from the same response.** A score without its backend
is meaningless: Cohere returns 0–1, the local ONNX cross-encoder returns raw
logits (≈ −12…+2), and the fallback fires at random under WSL2 network flap.
The same query has returned `tier=medium` on one run and `tier=high` on another
purely because a different backend scored it.

| Symptom | Cause |
|---|---|
| **Zero** candidates | Network. Qdrant unreachable, or the filter matched nothing. Check `hybrid_search returned N points` in the logs. |
| Candidates, all low-scoring | Retrieval. The corpus may not hold the answer, or the filter was too narrow. |
| Right topic, wrong company | The company filter did not apply — `_build_filter` appends it only `if company:`. Check `company` in the response. |
| Same page appearing several times | Near-duplicate suppression under-firing. Grep `Near-duplicate suppressed` for the real overlap ratios. |
| `crag_triggered: true`, identical scores across retries | A CRAG rung dropped a filter that was already unset. Should log `CRAG rung N was a no-op`. |

**Step 2 — did the model use them?** Read the full `response_text` via a direct
authenticated API call — **never** `eval_runner`'s 200-character preview, which
truncates before the keyword in question.

| Symptom | Cause |
|---|---|
| Answer says "the documents do not contain…" but `citations` look right | Post-generation refusal (`response_generator.py:70`). The retrieval was fine; the model would not answer from it. |
| A refusal-shaped sentence **and** a ✓ number, side by side | Cross-path reconciliation. Check which quadrant fired — grep `Cross reconciliation Q2`. |
| "Unable to synthesise a summary due to a temporary error" + an excerpt | **Both LLM providers failed.** `error=synthesis_unavailable`, and `llm_provider` is correctly `NULL`. |
| Confidently answers about a company not in the corpus | The F2 refusal is partial by construction — it fires only when the model *returns* an unresolvable name (CAVEAT-007, `router.py:296-314`). |

---

## 6. LLM issues

```bash
docker compose logs backend | grep -E "falling back to Groq|Gemini rate-limited|LLMUnavailable|no fallback"
```

| Log line | Meaning |
|---|---|
| `Gemini rate-limited — honouring server retryDelay Ns` | An **RPM** limit. One retry, then Groq. |
| `Gemini structured call failed (…) — falling back to Groq` | Timeout / 429 / 5xx / transport. The narrow trigger fired. |
| `Gemini … failed (no fallback)` | A 401/403/invalid-argument — a **config error**, deliberately not masked by the fallback. |
| `Groq fallback returned off-schema JSON` | Groq has no `response_schema`; a shape miss is treated as a provider failure, not a parse error. |
| `RuntimeError: GEMINI_MODEL environment variable not set` | Working as intended. A wrong default once cost two full eval sweeps. |

**Rate limits.** Gemini free tier is **5 RPM, 500/day per model**. A semantic
question makes two calls. **Failure at a fixed position with everything before
it passing is a quota signature; a real defect fails by category.**

---

## 7. Auth issues

| Symptom | Where to look |
|---|---|
| 401 on every request | Token expired (2 h). `getSession()` returns `null` past `expiresAt` and the UI drops to the login form. |
| 401 immediately after login | `JWT_SECRET` differs between the process that signed and the process that verifies. |
| **503** on login | Deliberate: a transient DB failure is retryable and is not a defect in the request. Grep `LOGIN DB FAILURE pgcode=`. |
| 403 on `/api/metrics` or upload | `require_role("admin")`. |
| Analyst sees no `dsl_object` | Check the role in the token — `role_filtered_response` **fails closed**, so an unrecognised role silently gets the *viewer* payload. |

---

## 8. Database issues

| Symptom | Cause |
|---|---|
| **0 rows from a query you know has data** | `SET app.tenant_id` was not run. RLS returns 0 rows, not an error. |
| `must be owner of table` | `ledgermind_app` is `NOSUPERUSER`. **You cannot apply migrations.** Write the `.sql` wrapped in `BEGIN;/COMMIT;` and stop — the user applies it in the Supabase SQL editor. |
| Duplicate `is_latest = TRUE` rows | `uq_financials_latest` should make this impossible. If it happens, the index is missing on that database. |
| Counts disagree between local and prod | Two different databases — CAVEAT-015. Check `DATABASE_URL` on both sides before forming a code theory. |

```bash
docker compose exec -T backend python -m scripts.check_migrations
docker compose exec -T backend python -m scripts.check_duplicate_docs
docker compose exec -T backend python -m scripts.check_citation_integrity
```

---

## 9. Docker / stack issues

| Symptom | Fix |
|---|---|
| `ConnectionRefused` right after `up -d` | `up -d` returns when the container **starts**, not when uvicorn binds. Poll `/health`. |
| `exec failed: current working directory is outside of container mount namespace root -- possible container breakout detected` | **Not a security event.** A stale mount namespace, usually after `--force-recreate`. Confirm with `docker compose exec -T backend echo alive`, then `docker compose up -d --force-recreate backend` and poll `/health`. `-w /app` does not help; no `cd` helps. |
| Changed `.env` value has no effect | `--force-recreate` — which also destroys anything put in with `docker compose cp`. |
| A change to `backend/` does nothing | `./backend:/app` is a bind mount, so the container path **is** the working tree. Check `git status`; also check `lsof -i :8000` for a stray local uvicorn (this has caused multi-hour false-regression chases). |
| `docker compose cp` left untracked files in the repo | Same bind mount. Container scratch goes in `/tmp`: `docker compose exec -T backend sh -c 'cat > /tmp/x.py' < x.py`. |

---

## 10. Frontend issues

- The stream falls back to `POST /api/query` on **any** streaming failure
  (`lib/api.ts:239-244`), so a broken trace with a working answer means SSE
  failed, not the pipeline. Watch the Network tab for `/api/query/stream`
  followed by `/api/query`.
- Render/nginx buffer proxied responses by default. The backend sets
  `X-Accel-Buffering: no`; if events all arrive at once in production, that
  header is being stripped. It **must be verified live on Render**, not locally.
- A UI element showing nothing where you expected a value is usually **correct**:
  the Zero UI-Hallucination Mandate says omit rather than substitute.

---

## 11. Evaluation — the discipline

`CLAUDE.md` §5 governs this and it is not optional.

- `regression_check.py` makes **zero** LLM calls. Run it after every extraction
  change, **not batched** — batching means a failure cannot be attributed.
- **Never run `eval_runner.py` without explicit per-run approval.** One Eternal
  sweep is ~100 calls; a full three-dataset sweep is ~165, against 500/day.
- When approved: `--delay 25`, not 15. Two calls per semantic question against
  5 RPM is ~8 RPM — over budget by construction.
- Run the **largest dataset first as a gate**. Read in this order:
  `Providers:`, then `Models served:`, then the score. If either gate is
  unclean, **stop**.
- **Report, do not interpret.** Paste those three lines verbatim. A score is
  meaningless without a stated model.
- **`eval_results/*.json` are not baselines** — they are whatever ran last,
  including rolled-back experiments. Before reading any of them, print row
  count, pass count, provider set, reranker set and mtime. Three wrong
  conclusions in one session traced to skipping that header check. Baselines
  live in `docs/IMPLEMENTATION_DELTAS.md`, dated and with providers stated.

---

## 12. The cheap test that catches most things

```bash
docker compose exec -T -w /app backend env PYTHONPATH=/app python -m pytest tests/ -q
```

177 tests, pure functions, no network/DB/LLM, ~2 seconds. The `-w /app` is
load-bearing.

Note that several tests assert **known defects as current behaviour**, with the
audit finding named in the docstring (F1, F2, F7, F9, F12b). **When one of those
starts failing, that is the fix landing** — and its assertion moves in the same
commit.
