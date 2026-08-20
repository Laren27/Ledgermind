# CLAUDE.md — LedgerMind

Deterministic multi-tenant financial intelligence platform for Indian SEBI filings.
Corpus: ETERNAL (formerly Zomato), TITAN, PAYTM.

The objective is **not** feature count. It is correctness, reliability, explainability,
and auditability. A wrong answer with a ✓ tick is worse than a refusal.

Detailed context lives in `docs/IMPLEMENTATION_DELTAS.md` (every divergence from the
blueprint), `docs/RUNBOOK.md` (stack startup, script invocation, quota procedure), and
`docs/audit/repo_audit_20260811.md` (13 findings ranked by blast radius, F1–F13 —
referenced by number throughout this file and in the test suite).

**Audit progress.** Closed: F1, F2, F8, F11, F12, F13. Open, in order: **F3** (unit scale detection — the blocker for arbitrary documents), **F14**
(multi-entity queries — `RouterResponse.company` is single-valued, so a two-issuer
query either nulls it or collapses to one issuer and asserts the other is absent from
the corpus; observed live returning "no company named Eternal" when ETERNAL is 732 rows.
Same class as F3: a confident wrong claim, not a visible failure. Fix shape is
`companies: list[str]` plus an IN-style filter, not a patch to the single field),
F7 (split chunk `financial_type='unknown'` into narrative vs undetermined), F4 + F9
(metadata validation and the gate scan window, designed together). Recorded but not
tasks: F5, F6.

**F2 closed 2026-08-12** across three steps. `route_after_router` now returns `refused`
on `error_node == "router"` and `graph.py` maps it to `audit_writer`, mirroring the
`blocked` edge — deliberately skipping the confidence tail, which would otherwise
rescore a refusal. `RouterResponse` gained `company_mentioned` (best-effort, **no prompt
block** — the model volunteers it, and an instruction was written, shipped and removed
for no measured loss). **"No prompt block" is not "invisible to the model."** The
response schema is sent on both providers, so declaring the field was itself an input
change; removing the instruction did not take it back out. Read
`docs/IMPLEMENTATION_DELTAS.md` section D, "The response schema is part of the prompt",
before treating any schema field as model-invisible. `_resolve_mentioned_issuers` gates the refusal on the resolved
list being empty rather than on `company is None`, because a multi-entity query nulls
`company` even when every issuer resolves. Verified on prod: a Reliance query returned
`company_not_in_corpus`, 0 citations, tier=low @ 0.0 — it previously returned 5
citations from TITAN/ZOMATO pages at tier=high @ 0.7095. Q051 unchanged at
`path=quantitative`, `sql_verified=true`, confidence 1.0.

**Open from that session, with no established before-state:** TQ008 routes `cross` where
its golden expects `semantic`, stable across three Gemini runs. A prompt-block cause was
suspected and DISPROVED — it still routes `cross` with the block removed. Needs a genuine
pre-`d365f4b` baseline (stash, checkout prior commit, three-call classify, restore)
before anyone calls it a regression.

Two facts F2 must account for: `_KNOWN_TICKERS` is larger than the corpus (SWIGGY,
NYKAA, DELHIVERY, POLICYBAZAAR resolve with zero documents), so "unknown company" and
"known company, no documents" are different refusals; and **no golden question carries
no company** — all 91 name theirs in the text, so do not build a mentioned-vs-omitted
distinction that has no caller.
Read those before proposing anything structural. Do not restate them here.

---

## 1. STOP-AND-ASK — never do these without explicit approval

These are not style preferences. Each one is a case where a green result does not
prove correctness.

1. **Migrations.** You cannot apply them — `ledgermind_app` is NOSUPERUSER and
   `psql "$DATABASE_URL"` fails with "must be owner of table". Write the `.sql` file
   wrapped in `BEGIN;`/`COMMIT;`, then stop. The user applies it by hand in the
   Supabase SQL editor. Afterwards verify **both** `schema_migrations` and
   `information_schema`, and state which database you queried.
2. **Destructive data operations.** `purge_orphaned_metrics --apply`,
   `purge_qdrant_company`, any re-ingest, any `backfill_financials`. Dry runs are
   free — run them, print the full candidate list, stop. Before any deletion, every
   candidate must be verified as either paired at an identical value with a
   surviving row, or a component summing into a preserved total.
3. **Measured constants.** Do not modify: `COHERE_HIGH` (0.5), `COHERE_MEDIUM` (0.15),
   near-duplicate threshold (0.70), alias coverage floor (0.5), `OVERLAP_TOKENS`
   (150), `BATCH_SIZE` (8). Each encodes a measurement that is not derivable from the
   code. Propose and stop. The **citation relevance floor (0.05) was deliberately
   removed** 2026-08-08 — it made a real figure untraceable rather than preventing an
   unsupported claim. Do not reintroduce it; read `semantic_engine.py:63` first.
   `COHERE_MEDIUM` (0.15) is the refuse-vs-answer boundary and has **never been
   exercised by a real query** (`semantic_engine.py:41-47`) — it is unvalidated, not
   validated, and that is why it must not be tuned casually.
4. **Golden dataset edits.** Adding questions, changing keywords, changing
   `expected_path` or any `expected_*` field. PQ012 carries a
   `known_deliberate_failure` field — do not "fix" it by editing its expectation.
5. **Prompt edits.** `SYNTHESIS_SYSTEM_PROMPT`, the DSL prompt, the router prompt.
   Appended instructions have lost to earlier, more concrete rules in the same prompt
   three separate times. These need reading, not testing.
6. **Any eval sweep.** See §5.

Everything else — file edits, greps, per-file commits, `regression_check`, dry runs,
`check_migrations`, docker lifecycle — is yours to run.

---

## 2. Working agreements

- **Never edit a file whose current contents are not in this session.** Ask for it.
  Memory of export names and field shapes has been wrong before (`UnauthorizedError`
  is in `lib/api.ts`, not `lib/auth.ts`; `StoredSession` has no `.email` field).
- **When a working call site already exists, copy it verbatim** rather than rebuilding
  from the import list. `purge_orphaned_metrics.build_produced_sets()` has the exact
  extraction sequence; `financial_extractor`'s own loop shows how to read a `PageBlock`.
- Explain architecture first, file structure second, code last. Do not generate large
  codebases unless asked.
- Distinguish **Must Have / Nice to Have / Future Phase** on every recommendation.
- Check whether an existing component solves it before proposing a new one.
- Prefer deterministic over agentic, maintainable over novel, operationally reliable
  over architecturally elegant. Justify any added complexity.
- Treat established decisions as final unless a genuine technical issue is found.
  `docs/IMPLEMENTATION_DELTAS.md` records decisions already rejected with reasons.

---

## 3. Editing and committing

- Provide edits as a `cat > path << 'EOF' … EOF` block or a python heredoc doing
  exact-string replacement with a `count != 1` guard. For files over ~1000 lines, ask
  the user to paste in VS Code instead — heredoc placeholders get pasted literally.
- **A patch guard must be evaluated against pre-splice text**, or match on something
  that cannot appear in the replacement. A patch whose new string contains its old
  string is not idempotent.
- **Verify every edit with `grep -n` immediately.** AST parsing proves a file loads,
  not that an edit landed — and it does not compile regexes, so use
  `python -c "import <module>"` for any file containing them.
- `git add` on an unmodified file stages nothing and the commit is a silent no-op.
  Run `git diff --stat` before every commit, every time.
- **One commit per file**, never batched, never skipped. `git add <file> && git commit`,
  then `git push origin main` after all commits.
- An "ABORT: found 0" is information, not a no-op. Read the file before offering a
  replacement patch.

---

## 4. Pre-flight — before any measurement, sweep, or result you intend to trust

Environment-vs-code confusion has caused more lost time this month than application
defects. Verify all four:

```bash
# (a) which code is actually running
docker compose exec -T backend python -c "import app.engines.retriever as m, inspect; print(m.__file__)"
# (b) environment
docker compose exec -T backend printenv GEMINI_MODEL
docker compose exec -T backend printenv QDRANT_URL   # must be the Cloud https URL
# (c) DNS, WARM — a fresh `exec` costs ~4s cold; loop 5 and read the later ones
```

(d) For anything reading `reranker_score`: **read `reranker_backend` from the same
response.** Cohere scores (0–1) and local ONNX logits are incompatible scales and the
fallback fires at random on WSL2 network flap. A score without its backend is meaningless.

Further signatures:
- `UserWarning: Api key is used with an insecure connection` → you are on local Docker
  Qdrant, not Cloud. Every measurement is invalid.
- `UserWarning: Failed to obtain server version` → qdrant_client failed its
  construction-time probe; the next query in that process will die.
- **An empty candidate set is a network signature. A low-scoring one is a retrieval
  signature.** Check which before theorising.
- **A local semantic failure is not a defect until it reproduces on a warm process.**
  Fresh `docker compose exec` carries ~30s of cold fastembed/ONNX load. Warm calls are
  0.36–0.41s.

---

## 5. Evals and quota

Gemini free tier: **5 RPM, 500 requests/day per model.** A semantic question makes two
calls (router + synthesis). This budget is shared with everything else that day.

- `regression_check.py` makes **zero** LLM calls. Run it after every extraction change,
  not batched — batching means a failure cannot be attributed to one change.
- **Never run `eval_runner.py` without explicit per-run approval.** One Eternal sweep
  is ~100 calls; a full three-dataset sweep is ~165.
- When approved: `--delay 25`. Not 15 — the 15s runs that survived were luck; two calls
  per semantic question against 5 RPM is ~8 RPM, over budget by construction.
- Run the **largest dataset first as a gate**. Read in this order: `Providers:`, then
  `Models served:`, then the score. If either gate is unclean, stop — do not spend the
  remainder.
- **Report, do not interpret.** Paste those three lines verbatim and stop. A score is
  meaningless without a stated model, and a raw tally printed under a caveat ends up in
  a README. Withhold it; do not annotate it.
- Failure at a **fixed position** with everything before it passing is a quota
  signature. A real defect fails by **category**.
- Read the full response text via a direct authenticated API call, never `eval_runner`'s
  200-char preview — the preview truncates before the keyword in question.
- Hand-run queries must use the dataset's **exact** question text.

**Golden keyword rule.** Assert on: dates, proper nouns, contiguous phrases quoted from
the filing, acronyms that are the question's own subject. Never assert on: optional
acronym glosses the model may not introduce (PPBL, SCN, FEMA, LODR), verb inflection
(cancelled vs cancelling), or short strings a wrong answer would also satisfy.

---

## 6. Non-negotiable architecture invariants

- **LLMs never do math.** DSL → SQL only; derived metrics are Python-side arithmetic.
- Full audit lineage on every answer. Prompt Shield runs pre-router.
- Tenant isolation: Postgres RLS + Qdrant metadata + scoped Redis keys.
- **Always `SET app.tenant_id` before `financials`/`documents` SELECTs.** RLS silently
  returns 0 rows otherwise — that is not a data-missing signal.
- **`app/metrics/registry.py` is the single metric registry.** Three independent
  registries caused bugs across three sessions. Do not add a second one anywhere.
- **Both formula copies must be updated together**: `_compute_derived_totals()` and
  `validate_financial_identities()` are independent.
- Import root is always `app.X.Y`, never `backend.app`.
- `docker compose up -d --build` is the only correct way to run the stack. A
  backgrounded local uvicorn has caused multi-hour false-regression chases; check
  `lsof -i :8000`. Changed `env_file` values need `--force-recreate` — which also
  destroys files copied in with `docker compose cp`.
- `docker compose up -d` returns when the container starts, not when uvicorn serves.
  Poll `/health` before minting a token, and `echo ${#TOKEN}` so an empty token fails loudly.
- **`exec failed: current working directory is outside of container mount namespace
  root -- possible container breakout detected`** means the container's mount namespace
  went stale, usually after a `--force-recreate`. It is not a security event and not a
  cwd problem: `-w /app` does not help and no `cd` helps, because *every* exec fails,
  including `docker compose exec -T backend echo alive`. Run that one-liner first to
  confirm, then `docker compose up -d --force-recreate backend` and poll `/health`.
- `QDRANT_URL` and all cloud credentials flow purely through `env_file: .env`. Never
  override via an `environment:` block — that exact override invalidated every local
  measurement for a week. **`DATABASE_URL` currently has exactly that override**
  (`docker-compose.yml:51`): the running stack reads the **local** Docker Postgres, not
  the Supabase URL in `.env`. The two are different databases with different document
  counts (11 local vs 9 Supabase). Always state which one a measurement came from.
- **Frontend document components must never know which engine produced the data.**
  `composeDocumentBody()` in `app/page.tsx` is the only function aware of path/engine
  internals.
- **Zero UI-hallucination mandate.** No badge, count, stat, or citation number may exist
  as static copy; every one is wired to a real backend field. Omit rather than substitute.
- Glass/blur is permitted in exactly one component: `QueryDock`.

---

## 7. Environment facts — read these instead of guessing

Guessing the environment has cost time repeatedly. All of the below were verified.

- The backend image is a **Python container** — no `psql` inside it.
- The project uses **raw psycopg2**, not SQLAlchemy.
- `db_transaction()` yields a **connection**, not a cursor. Use `conn.cursor()`.
- `ChunkResult` is a **TypedDict** — use `chunk["text"]` / `.get()`, never `getattr`.
- `normalize_metric_label` lives in `entity_resolver`, not `financial_extractor`.
- psycopg2 adapts Python UUIDs as TEXT. Cast: `ANY(%s::uuid[])` with `[str(i) for i in ids]`.
- Scripts run as `python -m scripts.X`, not `python scripts/X.py`. `eval_runner` runs
  from the **host**, in `backend/`, with `../golden_dataset/` paths.
- **pytest suite** (177 tests, pure functions, zero network/DB/LLM, ~2s):
  `docker compose exec -T -w /app backend env PYTHONPATH=/app python -m pytest tests/ -q`
  The `-w /app` is load-bearing. Cheap enough to run on every change, unlike
  `regression_check`. Several tests assert **known defects as current behaviour** with
  the audit finding named in the docstring (F1, F2, F7, F9, F12b) — when one starts
  failing, that is the fix landing, and its assertion moves in the same commit.
  The conftest network guard patches `socket` *and* `psycopg2.connect` by name; libpq
  connects in C and bypasses Python sockets, so socket-patching alone does not cover it.
- **`eval_results/*.json` are not baselines.** They are whatever ran last, including
  rolled-back experiments and interrupted runs. `eval_q_titan.json` (2026-08-10) reads
  11/15 with providers `{None, 'gemini'}` — that is the `financial_type` propagation
  rollback artifact, not a result; the 08-08 sweep's TITAN 15/15 stands. **Before
  reading any eval JSON, print row count, pass count, provider set, reranker set and
  mtime.** Three wrong conclusions in one session traced to reading these files without
  that header check. Baselines live in `docs/IMPLEMENTATION_DELTAS.md`, dated and with
  providers stated.
- **Cause cannot be assigned from a single before/after pair.** Attempted three times in
  one session, wrong every time: a `cross` route blamed on a prompt edit was a Groq
  fallback; a second was disproved by removing the block; a "TITAN 14/15" correction came
  from a void run. The instrument that settled each: **three runs with provider and model
  printed per run**, and checking what produced an artifact before reading its numbers.
- `golden_dataset/` holds four `q*.json` inputs: q4fy26_eternal (55), q_titan (15),
  q_paytm (20), q_eternal_transcript (1) = **91 questions**. The 88/90 baseline predates
  the transcript question, so it is not directly comparable — the next sweep is a new
  baseline, not a continuation. Eval outputs go to `eval_results/` (gitignored).
- `--min-chunks` (pipeline.py, chunker.py) defaults to **100**. TITAN legitimately
  produces 24 chunks, so a fully successful TITAN ingest exits 1 on the post-write
  completion gate. Read the log, do not re-run. Use `--min-chunks 20` for TITAN.
- Render truncates multi-line tracebacks — log exceptions single-line with pgcode.
- Render logs are UTC; the shell is IST.
- **Any script that parses a corpus PDF must parse it once and reuse the result.**
  Parsing twice exhausts WSL RAM and restarts the distro. Run `regression_check` once,
  tee to `/tmp`, grep the file.
- **`docker compose cp <file> backend:/app/...` writes into the repo.** compose binds
  `./backend:/app`, so the container path and the working tree are the same path — a
  file "copied into the container" appears under `backend/` and shows up as untracked.
  Container scratch goes in `/tmp`, which is not mounted:
  `docker compose exec -T backend sh -c 'cat > /tmp/x.py' < x.py`. Same for anything
  the container writes to `/app`. Check `git status` after either.

---

## 8. Diagnostic discipline

- **Never patch blind.** Diagnose from real output before writing any fix.
- **When a diagnostic contradicts a stated prediction, stop.** Do not continue past it.
- **When a fix does not work, stop tuning the number and go measure.**
- **Measure before reverting, not just before shipping.** A coverage-floor fix was one
  command from being reverted as a regression; the ninety-second measurement showed the
  shift was an improvement (divergences 2212 Cr → 11 Cr).
- **Test through the real entry point**, not the underlying dict or a similar-looking
  function. Two separate false conclusions came from querying `all_alias_pairs()`
  directly and from verifying through `extract_text()` when the extractor uses
  `extract_financials_positional()`.
- **A test that cannot observe the failure mode is not evidence.** A 20-iteration
  hostname loop that counted successes without timing them reported 20/20 clean while
  lookups were taking 8s.
- **Do not trust a single observation.** Verify across runs *and* across models.
- Forming theories is cheap; killing them with output is the discipline. Four wrong
  theories at one command each is the right ratio. Defending one is not.
- When local and prod disagree, run `git status --short` and
  `git log --oneline origin/main -1` before forming any code theory. Every
  "works locally, not in prod" has traced to an unpushed file, never a code defect.
- **A false contradiction is worse than a missed one** — fabricating disagreement
  inverts the system's stated value.

---

## 9. Standing maintenance

- Run `regression_check.py` (5-doc gate: ETERNAL Q4FY26 / TITAN / PAYTM / ZOMATO FY24
  / ETERNAL transcript) after any
  change to `section_classifier.py` or `financial_extractor.py`, before touching
  `chunker` / `embedder` / `qdrant_writer` / `pipeline`.
- Run `purge_orphaned_metrics.py` (dry run) after **any** extraction change. The loader
  retires rows by full business key including `metric`, so a name it stops emitting is
  never retired and stays `is_latest = TRUE` forever. Orphans are a maintenance
  obligation of extraction changes, not a loader bug.
- Update `docs/IMPLEMENTATION_DELTAS.md` in the **same commit** as any change that makes
  a blueprint statement untrue.
