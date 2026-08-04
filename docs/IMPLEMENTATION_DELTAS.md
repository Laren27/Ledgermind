# LedgerMind — Implementation Deltas

Companion to `ARCHITECTURE.md`, which is preserved verbatim as the original
design record and is NOT edited in place. This file records every point where
the shipped system diverges from that spec.

**Why this file exists.** §17 of the blueprint specified a Groq fallback for
months. It named a model Groq had retired, and no fallback code existed at any
call site. Nothing caught it, because the spec lived outside the repository and
therefore never appeared in a diff. A design document that cannot be reviewed
alongside the code it describes will drift silently. Any change that makes a
blueprint statement untrue must add an entry here in the same commit.

Last verified: 2026-08-02.

---

## A. Corrected — the spec was wrong or has gone stale

### §17 — LLM failover
Blueprint: "Gemini Flash rate-limited → route to Groq llama-3.1-70b."

Two corrections. `llama-3.1-70b` is retired by Groq; the pinned model is
`llama-3.3-70b-versatile` (JSON mode, 128k context, free tier). And the
fallback did not exist in code until 2026-07-29 — `config.py` had a
`groq_api_key` field with zero call sites.

As shipped (`app/llm/client.py`, sole LLM entry point for all three callers):

- Timeouts: structured 8s, text 20s, Groq 20s. A timeout is what converts an
  unbounded hang into a catchable exception; without one, a fallback keyed on
  exceptions can never fire. Motivated by an observed 78s Gemini call that
  returned 200 and looked normal in the audit log.
- Fallback triggers: timeout, transport failure, 429, 5xx.
- Does NOT trigger on auth or invalid-argument errors — verified that an invalid
  `GEMINI_API_KEY` raises 400 INVALID_ARGUMENT and does not fall back, so a bad
  key cannot be masked as a provider outage.
- 429 handling: Google labels the quotaId `...PerDay...` for both the per-minute
  and per-day limit, so only `retryDelay` distinguishes them. <=5s sleeps once
  and retries Gemini; longer falls through to Groq. Both branches verified live.
- Groq has no `response_schema`, only `json_object`. `generate_structured`
  injects the schema into the prompt and validates with Pydantic; an off-schema
  response is treated as a provider failure and never reaches `validate_dsl`.
- `llm_provider` is recorded on `QueryState`, admin-tier only.

**The fallback preserves availability, not behaviour.** Added 2026-07-31 after
Gemini's daily quota was exhausted mid-eval. The router is itself an LLM call,
and Groq classifies differently: TQ006 ("How did Titan's Watches division
perform in Q1FY26?") routed to `quantitative` on every Groq-served run and to
`semantic` on all four historical Gemini runs — confirmed from `audit_log`, not
inferred. So a Groq-served answer is not equivalent to a Gemini-served one for
anything asserting `expected_path`, which is exactly why `eval_runner.py`
withholds the headline score rather than annotating it.

Two further observations from the same outage, both worth acting on:

- When BOTH providers fail, `_generate_semantic_response` returns its
  raw-excerpt floor and the pipeline reports `confidence_tier="high"` with
  `error=None`. The tier is honest about RETRIEVAL, which genuinely was
  high-confidence, but nothing in the state records that no LLM ran. A total
  outage is therefore indistinguishable from a real answer. Fix: set
  `error="synthesis_unavailable"` when the floor fires.
- `llm_provider` is set by whichever call last SUCCEEDED. The router sets it,
  then the floor returns `provider=None`, which never overwrites the stale
  value — so floor responses were logged as `provider="gemini"` or
  `provider="groq"`. Observed 2026-07-31: the gate reported 11/45 non-Gemini
  when the true figure was at least 13. The gate still fired, but it is less
  conservative than it appears.

Stacked free tiers do not compose into reliability. Groq's own daily token
limit (100k TPD) was exhausted within hours of Gemini's, leaving no provider at
all. The failover design is sound and was exercised end-to-end; the ceiling is
the free tier, not the architecture.

### §13 — Confidence thresholds are backend-dependent
Blueprint gives flat cutoffs (HIGH >0.8, LOW <0.5). Reranking runs on two
backends with incompatible score scales: Cohere Rerank returns 0–1, the local
ONNX cross-encoder returns raw logits (~-12 to +2). A single threshold set
silently classified every Cohere-scored query as HIGH.

As shipped: `ChunkResult.reranker_backend` tags each chunk, and
`_score_confidence()` selects LOCAL (-4.5 / -7.5) or COHERE (0.5 / 0.15)
thresholds accordingly. Cohere values reviewed against production reranker
scores 2026-07-29; no evidence of miscalibration, though the 0.15–0.5 band
remains unstressed by any observed query.

**Measured properly 2026-08-01** (`scripts/cohere_score_dump.py`, 10 queries,
raw JSON under `docs/measurements/`). The prior belief that Cohere scores were
"flat" was an artefact of only ever inspecting the post-dedup top-5 of
well-retrieving queries. Across full candidate sets the separation is two orders
of magnitude with ZERO overlap: good queries top out at 0.329–0.999, poor ones
at 0.003–0.032. `COHERE_HIGH=0.5` was read as correct and unmovable — no poor
query cleared it, the highest reaching 0.0323, fifteen times below.

**That margin is SUPERSEDED as of 2026-08-02** — and the supersession is
ITSELF CORRECTED 2026-08-03; read the correction block below before quoting
any number in the next two paragraphs. A second measurement (17
queries, `docs/measurements/cohere_band_stress_2026-08-02.json`) found a poor
query at **0.4834** — "How does Eternal's board compose its audit committee?",
a topic this corpus does not contain. `poor` now spans 0.0012–0.4834. The
margin under `COHERE_HIGH=0.5` is therefore **0.017, not fifteenfold**.

The threshold is not moved on this: one query at 0.4834 against nine below
0.03 is an outlier, not a distribution, and raising `COHERE_HIGH` would change
confidence on every semantic query in the corpus. But the recorded confidence
in that threshold was overstated and the number should not be quoted as
fifteenfold again. A third measurement targeting the 0.03–0.5 region
specifically is the way to settle it.

Also measured and read at the time as DISCONFIRMED in the same run — DOWNGRADED
TO UNTESTED 2026-08-03, see the correction block below: the hypothesis that cross-style
framing ("does X align with its financial exposure to Y") lifts scores by
matching the financial-statement frame rather than the subject. Seven such
queries on absent topics scored 0.0002–0.0725, and the three controlled pairs —
same topic, bare versus cross-framed — went the WRONG way (audit committee
0.4834 bare against 0.0725 cross-framed). Framing is not the separating
variable. Recorded so it is not re-attempted. A single
0.584 cover-letter datapoint had suggested the opposite; ten points overruled
it. Note the median candidate on a GOOD query still scores ~0.006–0.08: Cohere
pushes nearly everything to the floor and lifts only real matches, which is what
makes the citation floor in section A safe at 0.05.

**CORRECTION 2026-08-03 — the 0.4834 query was MISLABELLED.** "How does
Eternal's board compose its audit committee?" was recorded above as a `poor`
query on "a topic this corpus does not contain". That is wrong. The topic IS in
the corpus. Its three top-ranked chunks are all page 135, FY24 — the ZOMATO
FY24 annual report, whose statutory corporate-governance section the quarterly
filings do not carry — and the stored previews contain the disclosure verbatim:
"Brief terms of reference, composition of these committees", "I. Audit
Committee", "governance policies". 0.4834 is Cohere scoring genuinely relevant
retrieved text. It is a correct score on a present topic, not an absent-query
outlier. Found by reading the committed JSON, not by re-running anything.

Three consequences, all of which narrow what the earlier paragraphs claimed:

1. THE RECORDED ABSENT MAXIMUM FALLS TO 0.2195. With the audit-committee query
   removed from the absent population, the highest score any genuinely absent
   query has produced across both runs is 0.2195 — "How does Titan's board
   evaluate the performance of its independent directors?"
   (`cohere_specificity_2026-08-03.json`, label `absent`). That one was checked
   the same way rather than assumed: its top chunk is the AUDITOR'S LETTERHEAD
   ADDRESS BLOCK (BS R & Co. LLP, Embassy Golf Links, page 11, TABLE), which is
   unrelated to the question. It is a genuine absent-query score.

   The `poor` span of 0.0012–0.4834 above therefore does not describe absent
   queries. The remaining band_stress `poor` entries top out at 0.0288 and the
   seven `absent_cross` at 0.0725.

2. THE FRAMING HYPOTHESIS IS UNTESTED, NOT DISCONFIRMED. The disconfirmation
   rested on three controlled pairs, and the only pair that diverged was the
   audit-committee pair — 0.4834 bare against 0.0725 cross-framed. If the bare
   query was scoring real governance text while the cross-framed one was not,
   the two halves were never asking about the same available content, and the
   pair cannot separate framing from topic presence. The other two pairs (gold
   hedging 0.0012/0.0002, data privacy 0.0144/0.0102) did not diverge, which is
   consistent with the hypothesis being wrong but equally consistent with both
   halves simply being absent. Downgraded from DISCONFIRMED to UNTESTED. It may
   still be false; this run does not establish it either way.

3. `COHERE_MEDIUM=0.15` IS NOW MEASURED TO ADMIT A GENUINELY ABSENT QUERY. At
   0.2195, the Titan board-evaluation query clears 0.15 — scoring an auditor's
   address block. The 0.15–0.5 band is no longer unstressed: it has one
   genuinely absent occupant. Under `COHERE_HIGH=0.5` the margin against a
   genuine absent query is 0.2805 rather than the 0.017 stated above, since
   that 0.017 was computed from the mislabelled query.

**NO THRESHOLD WAS MOVED.** Not `COHERE_HIGH` (0.5), not `COHERE_MEDIUM`
(0.15), not the citation floor (0.05). This entry corrects the record only.
One absent query at 0.2195 is a single datapoint, and §1 requires proposing and
stopping on any of these constants. What it does establish is that the band
between MEDIUM and HIGH is reachable by a no-answer query, which the earlier
text said was unobserved.

#### Band probe 2026-08-03 — there is no score-only separator, and the calibration is abandoned as posed

TWELVE QUERIES, ALL RECORDED `unlabelled`, ground truth read from full chunk
text AFTER the run. Measured with `scripts/cohere_score_dump.py`
(PROBE_QUERIES), file `docs/measurements/cohere_band_probe_2026-08-03.json`.
Labels were withheld deliberately: the 0.4834 correction above was a MISLABEL,
assigned from a query's wording rather than from what retrieval returned, and
this set was designed so the same mistake could not be repeated.

THE RESULT, which settles the question the last three runs were circling:

```
corpus DOES answer          0.0099 .. 0.9667
corpus does NOT answer      0.0003 .. 0.6954
```

**The two ranges are not adjacent, not touching, and not separable. They are
fully interleaved, one nested inside the other.** No threshold anywhere on this
axis splits them, because the same score means both things at different points.

THE TWO BOUNDARY CASES, which are the entry.

- **Q1, top1 = 0.6954, corpus does NOT answer it.** "What succession plan does
  Eternal disclose for its chief executive?" The 0.6954 chunk is a **Regulation
  33 declaration letter** (p44, doc `4c024e0f`) — a compliance cover letter
  carrying scrip codes, an ISIN and a signatory block, and nothing whatever
  about succession. The genuinely relevant chunk is at **rank 2, score 0.0219**
  (p80, doc `bd300f21`): "We have a succession plan in place to ensure seamless
  leadership transitions." That is BRSR boilerplate about succession generally,
  not a CEO plan, so the question is still unanswered — but the ordering is
  inverted against relevance by a factor of thirty.
- **Q10, top1 = 0.0099, corpus DOES answer it.** "What does Eternal disclose
  about lease terminations?" Rank 1 is the same Regulation 33 letter. The real
  answer sits at **rank 9, score 0.0001** (p341, doc `e46f92d7`): "Gain on
  termination of lease contracts (1) (3)" in the standalone lease note. A
  printed figure, correctly retrieved into the pool, ranked ninth at one
  ten-thousandth of the top score.

So a no-answer reached 0.6954 and an answer sat at 0.0099. **0.6954 > 0.5 =
`COHERE_HIGH`.** Any cut placed to exclude Q1 discards Q10, Q12 and half of Q7.

THE MECHANISM: ENTITY MATCHING, not topic matching. Cohere is repeatedly scoring
the presence of the question's NAMED ENTITIES rather than whether the passage
addresses the question:

- **Q1** — "chief executive" matched a declaration letter's signatory block.
- **Q2** ("cybersecurity incidents") — top1 0.0535 on **workplace injury**
  disclosure: "injuries reported… primarily attributed to slip, trip, and fall
  incidents." Matched on *incidents*. The genuine ISO 27001 / ransomware /
  phishing passage is at rank 6, score 0.0004.
- **Q5** ("supplier audit programme") — top1 0.0799 on the **statutory
  auditor's** limited review report, BS R & Co. LLP. Matched on *audit*. No
  candidate in the pool mentions suppliers or vendors at all.

This extends, and is the same failure as, the frame-matching note recorded
earlier in this file — cover and boilerplate matter retrieving on FRAME while
scoring near zero on subject — and the observation there that ETERNAL's cross
query ranked forward-looking-statements boilerplate above genuine margin
commentary. Frame-matching and entity-matching are two faces of one behaviour:
the reranker is matching SURFACE FEATURES OF THE QUESTION against surface
features of the passage. Boilerplate is dense in exactly those features, which
is why the same handful of cover pages keeps surfacing across unrelated queries.

**`COHERE_MEDIUM` CALIBRATION IS ABANDONED AS POSED — NOT DEFERRED.** The
programme was: find the value in the 0.15–0.5 band that separates answerable
from unanswerable. That premise is now contradicted by measurement, not merely
unsupported by it. No value of `COHERE_MEDIUM`, and no value of `COHERE_HIGH`,
can perform that separation on this evidence, because the property being
thresholded is not the property the score carries. Further points on the same
axis will not change this; collecting them would be measuring harder in the
direction already shown to be wrong. Any future work here has to introduce a
signal the reranker score does not contain — subject-term presence in the chunk,
chunk_type, an entity-vs-topic distinction — rather than a better cut point.

**NO THRESHOLD WAS MOVED.** Not `COHERE_HIGH` (0.5), not `COHERE_MEDIUM`
(0.15), not the citation floor (0.05).

OPEN ARCHITECTURAL QUESTION, recorded as such and NOT as a defect with a fix.
`_score_confidence` reads `chunks[0]` — the single top-ranked chunk — to assign
a confidence tier. Q1 is a demonstrated counterexample: `chunks[0]` is a
Regulation 33 declaration letter at 0.6954, which would tier as high confidence
on a question the corpus does not answer, while the only on-topic chunk sits at
rank 2 with 0.0219. Q10 is the mirror image. This is not being changed here.
Reading more than one chunk, or weighting by subject presence, is a design
decision with consequences for every path that consumes a tier, and it needs
proposing on its own terms rather than being smuggled in as a fix to a
measurement entry.

**THIS RESTS ON TWELVE QUERIES.** Six chosen as subjects with neighbouring
content, six as present-but-peripheral, across three companies. It is enough to
contradict a separation claim — one interleaved pair does that, and there are
several — but it is NOT enough to characterise the score's behaviour in general,
to establish how often entity-matching dominates, or to support any positive
rule about what the score does mean. Do not cite this entry as evidence for
anything beyond the negative result.

#### Quadrant 4 is now exercised — the blocker was query selection, not COHERE_MEDIUM

`_reconcile_cross` Quadrant 4 (both halves empty, a genuine no-answer) had no
golden coverage. Two successive explanations were recorded and both were wrong:
first that `COHERE_MEDIUM=0.15` blocked it (RETRACTED — those were ONNX logits,
not Cohere scores), then that the blocker was UNKNOWN. **It was query
selection.**

Every earlier candidate was chosen by judging from the query's WORDING that its
topic was absent. Four were authored on 2026-08-02 and all four returned
tier=medium, because each topic scored above 0.15 and pulled a citation. The
band probe made topic absence MEASURABLE rather than assumed, and selecting from
its sub-0.15 results resolved this on the first attempt.

THREE PROBE QUERIES, anchor-checked then submitted live as admin 2026-08-03.
All three cleared `anchor_check.py` with no bare-substring match against the
217-phrase `metric_anchor_phrases()` set (positive control confirmed the check
fires), so Stage 0c set no partial `dsl_object` on any of them:

```
query                              path   tier    cites  dsl   sql_verified  reranker_backend
disaster recovery / outages        cross  low     0      None  False         None
political contributions / penalty  cross  medium  1      None  False         cohere
director remuneration benchmarking cross  low     0      None  False         None
```

All three: `llm_provider=gemini`, `llm_model=gemini-3.1-flash-lite`,
`is_blocked=false`, `crag_triggered=true`. Q1 and Q3 returned
`error=low_confidence_refusal` at `response_generator`; Q2 returned no error.

Q1 became **PQ020** in `q_paytm.json`, asserting `expected_path="cross"` and
`expected_tier_low=true`, with no keyword assertions — a scoped no-answer has no
stable vocabulary. Verified against the real scorer: `--categories
cross_examination`, 3/3 PASS, scoped=true, PQ020 passing at confidence=low.
Golden set 88 -> 89. **NO THRESHOLD WAS MOVED to make this pass.**

Topic selection is the transferable part. "Disaster recovery" measured
top1=0.0003 in `cohere_band_probe_2026-08-03.json` — the lowest of twelve, all
20 candidates being subsidiary name lists. Author future Quadrant 4 questions
from measured sub-0.15 topics, not from intuitions about what a filing omits.

#### Q2 as an instance of the `chunks[0]` question — one page as a generic attractor

Recorded against the open architectural question above, as EVIDENCE for it, not
as a defect with a fix.

Q2 ("political contributions… regulatory penalties") returned **tier=medium on a
single citation: ETERNAL FY26 page 44** — the Regulation 33 declaration letter.
The response text is correct and says the documents do not contain the
information. The TIER disagrees with the prose, and it disagrees because
`_score_confidence` reads `chunks[0]`, which is that letter.

The same page recurs across unrelated queries in the band probe:

- **0.6954** — false top-1 for "What succession plan does Eternal disclose for
  its chief executive?", where the genuine hit sat at rank 2 / 0.0219.
- **0.0099** — top-1 for "What does Eternal disclose about lease terminations?",
  where the genuine figure sat at rank 9 / 0.0001.
- **0.26** — sole citation behind Q2's medium tier here.

One boilerplate page — scrip codes, ISIN, a signatory block, a compliance
subject line — acting as a **generic attractor** across questions with nothing
in common. This is the entity-matching behaviour recorded in the band probe
entry seen from the consuming side: the page is dense in exactly the surface
features questions carry, so it ranks first on questions it cannot answer.

**DO NOT TUNE THIS.** Not the citation floor, not `COHERE_MEDIUM`, not a
page-44 filter — the naive fix (exclude a page number) is the per-document hack
this project rejects, and it would silently break the moment another filing's
cover page took the same role. The architectural question is whether a
confidence tier may be derived from one chunk at all. It needs proposing on its
own terms.

#### `reranker_backend` is None on refusal paths

Measured on Q1 and Q3 above: both returned `reranker_backend=None` — not
`"cohere"`, not `"local"`. Both are the queries that ended in
`low_confidence_refusal` at `response_generator` with zero citations. The
refusal short-circuits before a backend is recorded.

Consequence for §4(d), which requires reading `reranker_backend` from the same
response as any `reranker_score`: **that check cannot be applied on a refusal
path.** This is harmless in itself — a refusal carries no citations and
therefore no scores to misinterpret, so there is nothing for the incompatible-
scales trap to catch. But it is worth knowing before someone treats a `None`
backend as evidence of a fallback, or writes a guard that asserts the field is
populated. Absence of the field here means "no citations were scored", not "the
backend is unknown".

### §5 / §8 — Embedding runtime
`sentence-transformers` + `torch` replaced by `fastembed` ONNX
(`BAAI/bge-small-en-v1.5` dense, `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder).
Torch does not fit Render's 512MB tier. Both removed from `requirements.txt`.

Embedding `BATCH_SIZE` is 8, not the blueprint's 32. A 1999-chunk document
OOM-killed repeatedly at 32 even after raising the WSL2 ceiling to 8GB. Batch
size affects throughput only — per-chunk embeddings are mathematically
identical regardless of grouping.

### §5 / §9 — Reranking
Cohere Rerank is the PRIMARY reranker, not the "Phase 7 upgrade" the blueprint
describes. Local ONNX cross-encoder is the automatic fallback. Driven by the
same 512MB constraint. Measured side effect: semantic retrieval runs ~1283ms on
Render vs ~18233ms locally — a 14x gap. Never tune thresholds or judge latency
from local timings.

### §14 — RLS policies: AND is not a short-circuit operator
The blueprint's RLS example is `tenant_id = current_setting('app.tenant_id')::uuid`.
Shipped as written, this broke authentication in production from launch until
2026-07-30: roughly 95% of logins returned 500 whenever any other traffic was
running, while succeeding 10/10 when the service was idle.

Two stacked defects:

1. **Empty GUC, not NULL.** `DATABASE_URL` routes through Supabase's session
   pooler (port 5432),
   so a login gets handed a server connection that previously served a query and
   ran `SET LOCAL app.tenant_id`. The GUC stays defined on that connection and
   reverts to `''` — not NULL — when the transaction ends. `NULL::uuid` is legal;
   `''::uuid` raises 22P02. Idle service means fresh connections, which is why
   every low-traffic test passed.

2. **`AND` does not short-circuit.** The first fix guarded the cast with
   `coalesce(...) <> '' AND tenant_id = ...::uuid`. PostgreSQL does not guarantee
   left-to-right evaluation of boolean conjuncts — the planner orders them by
   estimated cost and may evaluate the cast first. Confirmed still failing with
   pgcode 22P02 after that guard shipped (migration 009).

As shipped: every policy is a single `CASE` expression, `CASE` being the only
construct that guarantees an untaken branch is never evaluated. `users`
(migration 010) yields true on empty context — the login-by-email bootstrap.
All other tables (migration 011) yield **false**: fail closed, since an empty
tenant context must return zero rows, not all rows.

Diagnosis note worth keeping: this took three attempts because Render's log
stream truncates multi-line tracebacks, hiding the exception type. It was
settled by logging `pgcode` on a single line in `auth/service.py`, which also
now returns 503 rather than 500 for transient DB failures.

### §5 / §14 — Per-tenant rate limiting — NOT BUILT
The blueprint specifies a Redis token bucket per API key at the FastAPI layer.
Verified 2026-07-30 by grep: no limiter exists anywhere in `app/`. Nothing
throttles per-tenant request volume, and no endpoint returns 429.

### §9 — DISABLE_LOCAL_RERANKER removed
An env flag added during the 512MB OOM crisis returned `hybrid_search`
candidates directly, skipping reranking. Those chunks carry
`reranker_score=float("-inf")` and `reranker_backend="none"`, so
`_score_confidence()` classified **every** semantic query as LOW and refused —
after burning all CRAG rungs on identical retrievals first. Never enabled in
production (the sweep returns `confidence=high` throughout, which is impossible
if it were set), so this was a loaded footgun rather than a live defect.

Removed rather than fixed: RRF scores (~0.016 at rank 1, k=60) are a third
incompatible scale needing their own calibrated thresholds — the exact mismatch
class that produced the Cohere-vs-local confidence bug. Cohere primary plus
local ONNX fallback already covers the RAM constraint. `_score_confidence()`
now logs an error if unscored chunks ever reach it, rather than reporting a
false refusal.

### §22 — Audit log path values
The blueprint writes `query_path: "cross_examination"`. The code uses `"cross"`
everywhere (Pydantic Literal, `route_after_router()`). The DB CHECK constraint
is `('semantic','quantitative','cross','blocked','unknown')` — matched to the
code rather than widening it to admit both spellings, which would have made two
names for one concept permanent schema drift. `'unknown'` is included because
`audit_writer_node` can emit it on a pre-router exception.

**Amended 2026-07-30.** The paragraph above described `init.sql`, not every
live database. The local Postgres volume predated that edit and still carried
the original four-value constraint admitting `cross_examination`. Because
`CREATE TABLE IF NOT EXISTS` makes re-running `init.sql` against an existing
volume a no-op, nothing ever corrected it. Every `path="cross"` query had its
audit row rejected for the lifetime of that database: 2646 rows, zero cross,
across the entire history of the table. `audit_writer` logged
`Audit log write FAILED (response still delivered)` at ERROR and returned the
answer anyway — correct for the user, and not silent, but never grepped for.
Repaired by migration 013. Supabase was verified as already correct, so this
affected local only.

Two things worth keeping. First, a delta recorded as "corrected" is a claim
about the code, not about every deployed schema; the two can diverge without
any diff showing it, which is the same failure mode this whole file exists to
prevent. Second, `check_migrations.py` structurally cannot catch this class —
it diffs migration filenames against `schema_migrations`, not schema contents
against the code that depends on them.

### §9 — Near-duplicate suppression after reranking
The blueprint's retrieval pipeline is top-20 hybrid → cross-encoder rerank →
top-5 to the LLM, with no diversity step. That assumes the 20 candidates are 20
distinct passages. They are not: `chunker.py` uses `OVERLAP_TOKENS=150`, so
adjacent chunks share roughly 150 tokens by design, and both windows over the
same text are independently retrievable points.

Measured live 2026-07-30 on an ETERNAL FY26 cross query: chunks `0b035c3c…` and
`387d1a8c…` were both page 23, both exactly 705 characters, offset by about 90
characters, 87.8% token overlap. They occupied 2 of the 5 slots with identical
forward-looking-statements boilerplate while the management-commentary chunk
that actually addressed the question sat at rank 2. The first run after the fix
suppressed **9 of 20** candidates, overlaps ranging 72.5%–90.3%, including one
cross-page catch (page 36 against page 25, a repeated auditor letterhead).

The overlap is not the bug and must stay. `OVERLAP_TOKENS` was raised from 50 to
150 specifically to fix a mid-sentence split that orphaned Paytm's PPBL
impairment fact in an unretrieved chunk. The bug is that two windows over the
same text could both survive to the final cut. So the fix sits in
`retriever.py`, after rerank and before the top-k slice, not in the chunker.

As shipped: `_deduplicate_near_identical()` compares token-set containment
(`|A∩B| / min(|A|,|B|)`, so a short chunk fully inside a longer one scores 1.0)
against a 0.70 threshold, keeping the higher-scored member of each pair. Token
sets rather than embedding cosine — the text is already in hand, and a second
model invocation to answer what a set intersection answers is cost for nothing.
Cohere is now asked to score the full candidate set rather than `top_n=5`; it
bills per search rather than per document, so this is free, and without it
dropping a duplicate left four chunks instead of promoting the sixth-best.

The threshold is calibrated on one measured distribution and every suppression
logs its real ratio, so the eval sweep accumulates evidence. Note the asymmetry:
those logs show only what was dropped. False positives live on the kept side and
would surface as a changed answer, not a log line. 83/83 held after the change,
which is evidence rather than proof.

Surfaced here, closed 2026-08-02 — see the citation relevance floor below.

### §9 — Citation relevance floor
With duplicates cleared, the trailing slots held chunks scoring 0.026 and 0.014:
noise presented to the user as numbered evidence. A citation is a CLAIM that a
passage supports the answer, so rendering a 0.02 chunk with the same visual
weight as a 1.00 match asserts support the score says is absent — a Zero
UI-Hallucination Mandate violation on the evidence list itself.

Measured 2026-08-02 across four live `semantic_risk` queries, 20 real citations.
Sorted, the scores fall into two clusters with nothing between them: noise at
0.0027–0.0290 (seven values) and genuine matches at 0.0883–0.9996 (thirteen).
`CITATION_RELEVANCE_FLOOR = 0.05` sits in a roughly threefold empty band, so it
is not a tuned constant — anything in 0.03–0.08 gives identical results on this
evidence. Consistent with the 2026-08-01 Cohere dump, where no 'poor' query
exceeded 0.0323.

Three properties are deliberate and load-bearing:

- **Display layer only.** The filter lives in `_build_citations()`, the single
  construction point, and does not touch `retrieved_chunks`. A weak chunk in the
  model's context is harmless and occasionally useful; the defect is presenting
  it as evidence. Filtering retrieval instead would change what the model sees on
  every semantic and cross query, risking the eval baseline for no gain.
- **Cannot move confidence.** `_score_confidence()` reads `chunks[0]` and
  `chunks[-1]` and runs BEFORE `_build_citations()` at every call site, so the
  tier cannot shift as a side effect of a display filter. Verified live: counts
  went 5→2, 5→5, 5→3, 5→3 across the four queries with every tier unchanged. If
  that call ordering ever changes, this guarantee changes with it.
- **Cohere scale only.** Local ONNX returns raw logits (thresholds -4.5/-7.5)
  where 0.05 sits above nearly every legitimate score and would drop everything.
  One threshold across two scales is the §13 bug rebuilt. No logit-scale floor
  exists because none has been measured, and inventing one for symmetry is the
  unmeasured-constant habit this project has already paid for.

It can never return an empty list: if every chunk falls below the floor the
top-scoring one is kept, since an answer with zero citations violates Principle 2
outright and one weak citation beats none.

The same asymmetry as near-duplicate suppression applies. The logs show only what
was dropped; a false positive — a genuinely relevant chunk scoring below 0.05 —
would surface as a missing citation, not a log line. Four queries found none,
which is evidence rather than proof. A suspiciously short citation list is the
tell, and this threshold is the first thing to check.

Incidental observation, not acted on: all seven chunks dropped across the three
affected queries came from Paytm pages 5–6, the same two pages recurring in every
query. Consistent with cover/boilerplate matter that retrieves on frame-matching
and scores near zero on subject-matching. Whether such pages should reach the
candidate pool at all is a chunker/classifier question, not a retrieval one; the
naive fix (filter by page number) is exactly the per-document hack this project
rejects.

### §10 — Stage 0 guards: refusing beats substituting
Blueprint §10's DSL self-healing loop assumes an invalid metric produces an
invalid DSL that the validator rejects. It does not. Constrained to emit some
metric string from the AVAILABLE list, the model substitutes the nearest
plausible neighbour, and the result is perfectly valid DSL — the validator has
nothing to catch. Nothing downstream records what the user actually asked, so
the only place that intent survives is the raw query text.

Two guards therefore run BEFORE any DSL is generated, both reading the raw
query against the shared registry:

- **Stage 0 — derived metrics** (`metric_type="derived"`). Observed live: "What
  was Paytm's EBITDA for FY26?" returned `total_expenses` ₹8,523 Cr with
  `sql_verified=True`.
- **Stage 0b — registered but not DSL-queryable** (`dsl_enabled=False`), added
  2026-07-31. Observed live: "...the 207 crore impairment of loans and
  investments in associates recorded in FY26?" returned
  `metric="exceptional_items"` and appended a ticked `₹-186 Cr` for a metric
  nobody asked about.
- **Stage 0c — no metric named at all**, added 2026-08-02. Lives in
  `cross_engine.py`, not `quant_engine.py`. `GeminiDSLResponse.metric` is a
  REQUIRED field, so a cross-routed query naming no metric cannot return "none"
  — the model manufactures one, which compiles, executes, and is stamped
  `sql_verified=True`. Observed live across five consecutive runs on
  gemini-3.1-flash-lite: PQ012, "Does Paytm have any financial exposure to Paytm
  Payments Bank following the license cancellation?", produced
  `metric="exceptional_items"` and appended a ticked `₹-186 Cr`. Stable
  classification, not non-determinism.

Stage 0b matches canonical names with underscores expanded, not only alias
tuples — load-bearing, since every stored alias for that metric uses a slash
("loans/investment") while both the query and the canonical name use "and".
A four-word floor is the false-positive guard: `dsl_enabled=False` covers
aliases like "cash", "equity", "others" and "india", and scanning those would
fire on nearly any query. 44 phrases survive the floor, all unambiguous
financial-statement language. It fails toward NOT firing.

Stage 0c inverts Stage 0b's polarity, and that is why it has NO word floor.
Stage 0b fires when it FINDS a phrase, so breadth makes it over-fire and it needs
`UNQUERYABLE_MIN_WORDS`. Stage 0c is consulted to find NOTHING, so breadth makes
it fire LESS: short aliases like "cash" and "india" are free safety rather than a
hazard. Failing to anchor leaves a query unguarded (the prior state, recoverable);
anchoring too eagerly would suppress a figure someone legitimately asked for (a
new defect). Its phrase set therefore unions aliases, underscore-expanded
canonical names, and `prompt_aliases` — the last being load-bearing, since
"delivery charges" and "employee benefits" exist only there, and a first pass
reading alias tuples alone left four quantitative golden questions unanchored.
Verified against all 84 golden questions: 28 have no anchor, every one of them
adversarial (blocked pre-router) or `semantic_*` (never invokes the quant half).

Stage 0c is scoped to the cross path BY PLACEMENT, not by a runtime conditional.
On `path=quantitative` the router has already asserted the user wants a number,
and refusing there would risk legitimate queries phrased outside registry
vocabulary. On cross the quant half is an adjunct to a qualitative answer, so
skipping it degrades to qualitative-only — a case `_reconcile_cross` already
handles as Quadrant 3. Living in `cross_engine.py` means Path 2 is untouched by
construction rather than by a check someone could later move. It deliberately
does NOT set `dsl_object`: leaving it None is what tells reconciliation this
query never asked for a figure, so `CROSS_NO_VERIFIED_FIGURE_NOTE` — which
asserts a metric was identified — stays correctly silent. No change to
`response_generator.py` was needed.

Note the router was NOT changed. Its cross rule ("verify or compare qualitative
claims against financial numbers") matches "financial exposure" defensibly, and
re-tuning a classification prompt used by every query to clear one test is the
prompt-versus-concrete-rule fight already lost twice here. With the guard in
place, routing cross costs nothing. PQ012's golden entry stays red on
`expected_path` as the single artifact recording that classification is imprecise
on this phrasing; it carries a `known_deliberate_failure` field saying so.

Stage 0b records a partial `dsl_object` before returning, so the cross path's
reconciliation can tell "a metric was identified but produced no verified
figure" from "no figure was ever requested" — without it, a refused query looks
identical to a purely qualitative one and the user is never told the system
declined to verify the figure they named.

### §8 — A guard that measures the raw label rejects real line items
`_should_skip_row()` drops rows whose description exceeds 80 characters or 12
words, on the reasoning that real P&L/BS line items are short LABELS and
anything longer is narrative prose that happened to parse as a table row. That
reasoning is sound. The implementation measured the RAW description while
`resolve_metric()` measured the NORMALISED one — two different strings, and the
guard ran first.

Bracketed note references, footnote markers and roman-numeral prefixes are not
part of a label, so counting them rejected genuine data:

- PAYTM standalone `Provision for impainnent ofloans/investments in
  subsidiary/associate [refer note 4]` — 83 raw characters, 69 normalised.
  Carries FY26 439.0 and FY25 37.0, resolves correctly to
  `impairment_of_loans_and_investments_in_associates`, and was dropped from
  every ingest after the guard landed 2026-07-31, while its 59-character
  CONSOLIDATED twin (207.0 / 30.0) passed. The standalone/consolidated
  asymmetry is what made it visible.
- TITAN `V. Profit before share of profit of an associate and tax (III -JV)` —
  13 raw words, 11 normalised. A real line item, the step between operating
  profit and PBT for a company with associate holdings.
- 69 further PAYTM cash-flow and OCI rows that had never entered the database
  at all: opening/closing cash balances, net cash from operating/investing/
  financing, FVTOCI fair-value changes. Internally consistent on inspection
  (FY25 closing 2072 = FY26 opening 2072).

Fixed by measuring the normalised label for the LENGTH tests only. The
remaining checks stay on `desc_lower` — their behaviour under normalisation has
not been measured, and this was one demonstrated defect, not a rewrite. Prose
is unaffected because it has no bracketed metadata to strip: the two known
cases measure 104->103 and 93->93 characters, both still caught.

HOW IT WAS FOUND, which is the part worth keeping. Adding PAYTM to
`regression_check.DOCUMENTS` extended `purge_orphaned_metrics.py` to its 395
previously-unevaluated rows. The first dry run flagged 46 candidates, and two
of them carried the standalone impairment figures with NO current-code
replacement anywhere. A blanket `--apply` would have deleted verified data and
nothing would have surfaced it until a standalone query returned empty. The
"verify raw/canonical pairs at identical values before deleting" rule from the
178-row purge is what stopped it.

Backfilled with `scripts/backfill_financials.py` (new): Stage 7 only, doc_ids
READ from the documents table rather than minted. A full pipeline run would
have re-chunked, re-embedded and re-upserted Qdrant, all producing byte-
identical output for an unchanged document — pure risk, and rewriting Qdrant
payloads is exactly what produced the PAYTM FY99 drift. `pipeline.py` has
`--skip-financials` for stages 1-6; there was no inverse.

Safe because `db_loader._SQL_LOCK_LATEST` matches on the BUSINESS KEY
(company, metric, fiscal_year, financial_type, quarter), not `doc_id`. PAYTM
395 -> 464 rows (69 inserted, 351 skipped as already-present, 0 errors), TITAN
+4. The purge then ran clean at 44, every candidate verified as either paired
to a replacement at identical values (`operating_profit` ->
`operating_profit/(loss)_before_working_capital_changes`, `equity` ->
`paid_up_equity_share_capital`, `payables` -> `increase/(_decrease)_in_trade_
payables`, and so on) or a component summing into a preserved total
(`deferred_tax` + `current_tax` + `adjustment_of_tax_relating_to_earlier_years`
= `tax_expense`, verified arithmetically for all four periods). 464 -> 420,
second run idempotent, every golden value unchanged.

TAX COMPOSITION EVALUABILITY, measured 2026-08-03 against live `is_latest` rows.
The premise that this identity "reconciles on every PAYTM period" is FALSE as a
statement about the data: 2 of 10 period groups carry all four rows. The formula
is not what is in question — both complete groups reconcile exactly (18+10+2=30,
6+(-3)+2=5, diff 0.00) — what is missing is components, not agreement.

Zero-coalescing those absences would have manufactured a FALSE FAILURE. FY25
annual consolidated holds `current_tax` 20.00 against `tax_expense` 18.00 with
`deferred_tax` ABSENT, which `.get(m, 0)` renders as an 11.11% identity breach
when the true statement is that a component was never extracted. That is why
`validate_financial_identities` check 4 asserts only on complete groups and
routes the rest to `[IDENTITY NOT EVALUATED]` — the same third outcome
`purge_orphaned_metrics` gives period groups no source document produces.

```
fy     q    type          current_tax deferred_tax  adjustment tax_expense  evaluable?
--------------------------------------------------------------------------------------
FY25   Q4   consolidated        1.00       2.00     ABSENT       3.00  NO   missing: adjustment_of_tax_relating_to_earlier_years
FY25   Q4   standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY25   —    consolidated       20.00     ABSENT     ABSENT      18.00  NO   missing: deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY25   —    standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY26   Q3   consolidated        6.00      -3.00       2.00       5.00  YES  sum=5.00 vs 5.00 diff=0.00
FY26   Q3   standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY26   Q4   consolidated       -2.00      13.00     ABSENT      11.00  NO   missing: adjustment_of_tax_relating_to_earlier_years
FY26   Q4   standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years
FY26   —    consolidated       18.00      10.00       2.00      30.00  YES  sum=30.00 vs 30.00 diff=0.00
FY26   —    standalone        ABSENT     ABSENT     ABSENT       0.00  NO   missing: current_tax, deferred_tax, adjustment_of_tax_relating_to_earlier_years

groups total: 10   fully evaluable: 2   not evaluable: 8
```

NOT RECONCILED with the "verified arithmetically for all four periods" claim
directly above. That verification ran at purge time against the 464-row table;
this measurement is against the 420-row post-purge state, and which four periods
were checked then was not recorded. The two are therefore not directly
comparable and the earlier claim is neither confirmed nor retracted here.
SETTLED 2026-08-03 by that dry run. The purge is excluded as a cause of tax
sparsity on two independent grounds, either of which is sufficient alone.

1. The database is now exactly the current extractor's output. The dry run
   reported ORPHANED: 0 with no unscoped-group section at all, so every live
   business key is in the produced set. Produced row keys 1377, live is_latest
   rows 1377, and DISTINCT live business keys also 1377 — equal cardinality on a
   subset relation forces set equality. Had a purge deleted a component row the
   extractor still produces, that key would now be produced-but-not-live and the
   two counts could not both be 1377. Reinforcing this, `financials` holds ZERO
   is_latest = FALSE rows: nothing retired, nothing residual.
2. `validate_financial_identities` runs over extraction RECORDS, not over
   database rows — it receives `records` and groups them in memory. A purge
   mutates only the table, so it is structurally incapable of changing that
   function's verdict. This ground holds regardless of what any row count says.

So every current NOT EVALUATED verdict means the component was not extracted.
The DB-side matrix above is a faithful reconstruction of extraction output
precisely because the two sets coincide; that coincidence is a present fact, not
a guarantee, and the reconstruction stops being faithful the moment they diverge.

What remains genuinely unrecoverable: the purge DID delete tax component rows
(deltas records them as "a component summing into a preserved total ... for all
four periods", which means all four rows were present for those periods at purge
time). They were hard-deleted and left no is_latest = FALSE trace, so WHICH four
periods cannot be recovered from the database. But those groups would not be
evaluable today even had the purge never run: the purge only removes rows the
extractor has already stopped producing, and a re-ingest now reproduces exactly
the current 1377 rows. The loss of evaluability traces to the extraction change.
The purge removed the stale evidence of it, not the capability.

§9 OBLIGATION DISCHARGED. The post-extraction-change `purge_orphaned_metrics`
dry run required by §9 ran clean on 2026-08-03: zero orphans, zero unscoped
groups, nothing to purge, nothing applied.

LATENT GAP, recorded because it is untested rather than verified. That same
"zero is_latest = FALSE rows" fact means Truth Resolution and restatement
handling have NEVER been exercised against live data. The retirement path --
`db_loader._SQL_LOCK_LATEST` flipping a prior row to is_latest = FALSE while
preserving it -- is the mechanism the whole restatement design rests on, and no
row in the live table has ever traversed it. Its correctness is currently an
argument from reading the SQL, not a measurement. Treat any first restatement as
an unproven code path and measure it, do not assume it.

SUPERSEDED SAME DAY, in the row counts only. The 1377 = 1377 set equality above
was true when measured and is now stale: the `(I)` -> `(1)` OCR fix in
`pdf_parser._ocr_one_to_digit` moved the produced-set to **1392** against 1377
live is_latest rows. Both reasoning grounds survive intact -- ground 2 never
depended on a row count, and ground 1's conclusion (live is a SUBSET of
produced, so no purge silently removed a still-producible row) is unchanged and
in fact reinforced: produced now EXCEEDS live by exactly the 15 recovered
values. What is no longer true is the stronger claim of set EQUALITY, and with
it the statement that a re-ingest "reproduces exactly the current 1377 rows".

Per-document: ETERNAL Q4FY26 442 -> 449, TITAN 269 -> 269, PAYTM 424 -> 432,
ZOMATO 242 -> 242. TITAN and ZOMATO are unmoved, which is the expected shape --
neither prints the wrapped-`I` form. The 15 are printed figures that
`clean_financial_number` previously converted to None after the column had
already been claimed.

CONSEQUENCE, now CLOSED. `backfill_financials` ran with explicit approval on
2026-08-03 and the 15-row gap is closed: ETERNAL 7 inserted / 442 skipped,
ZOMATO 0 inserted / 242 skipped, PAYTM 8 inserted / 424 skipped, and across all
three **0 restated, 0 reingested, 0 errors**. `financials` went 1377 -> 1392,
DISTINCT business keys 1392, and **live == produced is restored at 1392**.

Zero `is_latest = FALSE` rows survived the write, which is a property of the
loader and not luck: `backfill_financials` READS doc_ids from the documents
table rather than minting them, so every already-present record takes
db_loader's same-doc_id branch -- `ON CONFLICT DO NOTHING`, counted as
"skipped", with no retirement. Only genuinely new business keys insert. The
restatement path therefore STILL has never executed against live data, and the
latent gap recorded above stands untouched.

MEASURED, not assumed: `regression_check` 2026-08-03, 4/4 PASS, zero identity
failures, zero discarded rows, and identities NOT EVALUATED 10 / 8 / 7 / 4
(ETERNAL Q4FY26 / TITAN / PAYTM / ZOMATO) = 29, down from 30. The single group
that moved is the PAYTM one named above. Every other NOT EVALUATED verdict in
the corpus is still an absent `adjustment_of_tax_relating_to_earlier_years`,
which three of the four documents genuinely do not print -- TITAN prints only
Current/Deferred/Total, ZOMATO the same, and ETERNAL Q4FY26 prints no total-tax
row at all. Only PAYTM prints the adjustment line. The identity was NOT
reshaped; that remains an open proposal, and reshaping it now would paper over
extraction gaps rather than record them.

#### The `(I)` defect: a claimed column that then lost its value

WHAT MADE IT INVISIBLE, which is the part worth keeping. `(I)` did not fail to
match. `_is_numeric_word()` tests `text.strip("()")`, so `(I)` reduced to `I`,
matched `^I$` in `_NUMERIC_WORD_RE`, qualified as a numeric word, entered a
bucket and CLAIMED a value column. Only then did conversion fail:
`clean_financial_number("(I)")` strips the parens and calls `float("I")`, which
raises ValueError and returns None. The pre-existing `I` -> `1` repair sat at
the fragment join and tested `t == "I"` against the UNSTRIPPED token, so every
wrapped form slipped past it.

The consequence is the reason this rates an entry rather than a line in a commit
message: **a printed figure became indistinguishable from an absent one**. That
is the single failure mode this codebase is least equipped to notice, because
absence is a legitimate, frequently-correct reading everywhere else -- the `*`
negligible marker, blank columns, nil dashes. An unparseable token that had
already consumed its column produced exactly the same downstream signature as a
column that was never printed. It is strictly worse than a non-match, which
would at least have left the column free.

Fixed in `pdf_parser._ocr_one_to_digit`, deliberately EXACT-STRING rather than
regex: the call site is the per-bucket value loop, hot for every row of every
financial page in all four documents, so `_NUMERIC_WORD_RE`, `_VALUE_TOKEN_RE`
and `clean_financial_number`'s own regexes are untouched. The substitution fires
only when the token stripped of wrapper characters is EXACTLY "I", preserving
the wrapper so the negative sign survives ("(I)" -> "(1)" -> -1.0). Roman
numerals cannot be caught: "II", "III", "IV", "VI", "VII" all have cores that
are not "I", and none is a numeric word to begin with. The stage is late by
design -- a token reaches the function only after being classified numeric AND
assigned to a column by x-position -- so description text never passes through
it. Same lesson as `entity_resolver.TRAILING_INITIAL_RE`: get the STAGE wrong
and a fix aimed at one shape silently rewrites another.

15 RECOVERIES, each verified against the printed line before any write. Only TWO
are tax-breakdown rows; the other 13 are outside what the fix was reasoned about
and were reviewed individually on that basis.

```
ETERNAL  FY25 Q4  consol      -1.00  oci_fx_translation                    p31
ETERNAL  FY25 --  consol    -118.00  income_taxes_(paid)/refund_(net)      p33
ETERNAL  FY25 --  standalone   -1.00  gain_on_termination_of_lease_contracts p42
ETERNAL  FY25 --  standalone    0.00  other_interest_paid                   p42
ETERNAL  FY25 --  standalone   -1.00  profit_on_sale_of_ppe_(net)           p42
ETERNAL  FY26 Q4  standalone   -1.00  oci_remeasurement_defined_benefit     p40
ETERNAL  FY26 --  standalone   -1.00  other_interest_paid                   p42
PAYTM    FY25 Q4  consol       -1.00  share_of_oci_of_associates/jv         p8
PAYTM    FY25 --  consol       -1.00  adjustment_of_tax_relating_to_earlier_years p8
PAYTM    FY25 --  consol       -1.00  deferred_tax                          p8
PAYTM    FY25 --  consol       -1.00  share_of_oci_of_associates/jv         p8
PAYTM    FY25 --  standalone   -1.00  profit_on_sale_of_ppe_(net)           p18
PAYTM    FY26 Q3  consol       -1.00  share_of_profit/(loss)_of_associates  p8
PAYTM    FY26 Q4  consol       -1.00  non-controlling_interests             p8
PAYTM    FY26 --  consol       -1.00  non-controlling_interests             p8
```

THREE are confirmed by the filings' OWN printed subtotals, not merely cited:
associates FY26 Q3 (231 + (-1) = 230, the printed "Proft/(Loss) before
exceptional items and tax"); non-controlling interests, where owners-of-parent
plus NCI reproduces the printed total in all five columns (184 + (-1) = 183,
553 + (-1) = 552, and the FY25 pair via the same line); and the tax row
(20 + (-1) + (-1) = 18).

FIFTEEN RECOVERIES FROM FOURTEEN TOKENS. `ETERNAL FY25 standalone
other_interest_paid` carries no `(I)` of its own. Its row prints
"Other interest paid (I) (0)", so before the fix its values were [None, -0.0],
and `_should_skip_row` drops any row whose only non-None values are zero -- the
WHOLE row was discarded. Recovering the FY26 `(I)` resurrected the row, which
then emitted both columns. Worth remembering as a shape: a single recovered
token can restore records in columns that never contained the defect.

TITAN AND ZOMATO MEASURED, NOT INFERRED. The first report argued they were
unaffected from the fix being purely additive plus unchanged record counts.
That inference was then checked directly: both return gained=0, lost=0,
value-changed=0, TITAN additionally confirmed at token level (zero tokens
rewritten on its P&L page), and ZOMATO independently re-confirmed by the
backfill itself reporting 0 inserted / 242 skipped. Neither document prints the
wrapped-`I` form.

INSTRUMENT CAVEAT, recorded because the instrument is committed and will be
reused. `_recovered_value_dump.py`'s LAYER B matches old rows to new rows by
description string and takes the first match, so a page carrying SEVERAL rows
with an identical description will produce a spurious token-level "recovery"
(observed on TITAN page 14, which has three rows labelled
"-Non-controlling interesi-"). LAYER A is unaffected -- it keys on the full
business key -- and Layer A is the authority for what actually changed. Read
Layer B as provenance for a Layer A finding, never as a finding on its own.

#### The all-zero row guard: a printed nil is data, and it was discarded — RESOLVED

STATUS: RESOLVED 2026-08-03, measured and backfilled. The mechanism below is
kept as written because it is the reasoning that justified the change; the
RESOLUTION section at the end records what was actually done. This entry was
OPEN for one day and the tradeoff paragraph below was the live decision.

WHERE IT DIES. `_should_skip_row`'s final clause,
`financial_extractor.py:353`:

```python
if not [v for v in values if v is not None and v != 0.0]:
    return True
```

consumed at `financial_extractor.py:381` in `_rows_to_records`:

```python
if _should_skip_row(description, values):
    continue
```

Execution never reaches `financial_extractor.py:397`, where the
`records.append(FinancialRecord(...))` sits. **No FinancialRecord is ever
constructed, so `db_loader` never sees the value at all.** Every part of the
write path -- `_SQL_LOCK_LATEST`, the retirement flip, the ON CONFLICT guard --
is innocent here. Anyone debugging this from the loader end will find nothing,
because there is nothing to find: the row is gone two call frames earlier.

THE MECHANISM, traced end to end on ZOMATO AR 2023-24 p.285 standalone, which
prints `Deferred tax - -`:

1. `_NUMERIC_WORD_RE` (`pdf_parser.py:264`) accepts a bare `-` via its `^-$`
   alternative, so the dash qualifies as a numeric word, enters a bucket and
   CLAIMS its value column.
2. `clean_financial_number` (`pdf_parser.py:95`) maps it:
   `if not val or val == '-': return 0.0`. The nil dash becomes `0.0`.
3. `extract_financials_positional` returns `['Deferred tax', 0.0, 0.0]`.
4. `_rows_to_records` takes `values=[0.0, 0.0]` and the guard above reads the
   row as EMPTY. It is discarded whole.

Verified directly rather than by reading: `clean_financial_number('-')` is
`0.0`; `_should_skip_row('Deferred tax', [0.0, 0.0])` is `True`; the SAME label
with `[1.0, 1.0]` is `False`. The label passes every guard. Only the values
condemn it.

WHY THIS IS WRONG, stated precisely. **A printed nil asserts that the line item
IS zero for that period. That is not the same statement as the line not being
printed.** The filing is making a positive claim and the extractor is
converting it into an absence. This is structurally the SAME failure as the
`(I)` defect recorded directly above -- a printed figure becoming
indistinguishable from an absent one -- and it arrives by the same two-step
shape: the token is accepted well enough to claim a column, and is then lost
after the claim. The two defects are independent in code and identical in
consequence, which is why both are recorded here rather than as commit
footnotes.

`-0.0 == 0.0` is `True` in Python, so parenthesis-wrapped zeros (`(0)`, which
`clean_financial_number` negates to `-0.0`) are swept in by the same clause. The
loss is not limited to bare dashes.

SCOPE, measured across all four reference documents by
`scripts/_zero_row_loss_scan.py`:

```
document              rows dropped   gross candidates   DISTINCT keys lost
ETERNAL_Q4FY26                   5                 16                   16
TITAN_Q1FY26                     3                 10                    4
PAYTM_FY26                       0                  0                    0
ZOMATO_AR_2023-24               18                 36                   30
--------------------------------------------------------------------------
total                           26                 62                   50
```

GROSS exceeds DISTINCT where one page carries several identically-resolving
rows -- TITAN's three `-Non-controlling interesi-` and ZOMATO's three
`Non-controlling interest` each collapse to a single key set, and `seen_keys`
first-wins would collapse them anyway. **50 is the honest figure.** PAYTM is
clean at zero.

NOT every dropped row is a loss: `finance_costs` on ZOMATO p.291 is dropped by
this clause but the metric is produced from another page, so its business key
survives. It is excluded from the 50.

THE TRADEOFF, which is why this is open rather than fixed. Of the 19 distinct
metric names involved, only FOUR are in the registry -- `deferred_tax`,
`exceptional_items`, `inventory`, `changes_in_inventories`. The other fifteen
are not, and would be stored as raw. Several are OCR-damaged variants of each
other rather than distinct line items:
`non-controlling_interest` / `non-controlling_interesi` /
`non-controlling_int~re!_i`, and `income_tax_relating_to_above` /
`ncome_tax_relating_to_above` / `(_iii)_income_tax_relating_to_above` /
`(_iii)_ncome_tax_relating_to_above`. So removing the clause outright would add
~50 rows of which the majority are unregistered and a visible fraction are label
noise. **The guard is doing real work as well as causing this loss**, and any
fix has to separate "this row has no data" from "this row's data is zero"
without also readmitting the noise the guard currently absorbs. That is a design
question, not a one-line change.

Known consequence already visible elsewhere in this file: the two
`absent: deferred_tax` verdicts for ETERNAL FY24 and FY23 standalone in the
NOT EVALUATED tally are this clause, not an extraction gap in the usual sense --
the figure IS printed and IS read.

RESOLUTION, 2026-08-03
----------------------

THE FIX IS UPSTREAM OF THE GUARD, not in it. `pdf_parser.NOT_PRINTED` is a
sentinel returned when a bucket is EMPTY. Before it existed, None meant two
different things in a positional row and nothing could tell them apart: the
column carried no token at all, or a token WAS printed and
`clean_financial_number` could not parse it (its `except ValueError` path -- the
shape that hid the `(I)` defect). **`None` now means exactly one thing: a token
was printed and would not parse.** That single disambiguation is what let the
guard be rekeyed without guessing.

The sentinel is identity-compared with `is`, never `==`, and is deliberately a
bare class rather than an Enum or float subclass: anything with numeric
behaviour could be summed or `abs()`'d by accident on this hot path and silently
become a value. It is falsy, so a stray truthiness test reads as "no value
here". `_should_skip_row`'s magnitude guard and `_rows_to_records`' emit loop
both had to learn about it -- `abs(NOT_PRINTED)` raises TypeError.

The clause itself was KEPT, not deleted, rekeyed to
`if all(v is NOT_PRINTED for v in values)`: drop only when no column carried a
printed token.

THE CLAUSE IS NOW UNREACHABLE FOR POSITIONAL-PATH ROWS, and this is recorded
rather than discovered later. `extract_financials_positional` at
`pdf_parser.py:466` already refuses to emit a row with fewer than
MIN_VALUE_COLUMNS (2) non-empty buckets, so every row reaching the guard from
that path has at least two printed tokens and the new predicate cannot fire for
it. **It survives as a structural floor for the degenerate all-empty row, not as
a filter.** In this corpus it filters nothing. That is a deliberate weakening,
accepted knowingly: separating real line items from OCR label noise is the job
of the LABEL guards, not of a values test, and the tradeoff paragraph above was
resolved in favour of admitting the rows and reviewing them by hand.

WHAT WAS ADMITTED. 45 of the 50 rows, reviewed individually before any write.
**Every one of the 45 is a printed nil** -- each value is 0.0 or -0.0. They add
no magnitude, only the filing's positive assertion that the line is zero. That
is the whole point: the assertion is data, and its absence was previously
indistinguishable from the line not being printed.

THE ONE EXCLUSION. `non-controlling_int~re!_i`, all 5 rows. ETERNAL_Q4FY26 p31
prints the non-controlling-interest line twice and OCR renders the occurrences
differently; the damaged name covers the SAME five period groups as
`non-controlling_interest` with IDENTICAL values in all five. One printed line
under two readings. Excluded by name in `_OCR_DUPLICATE_METRICS`, keyed on the
RESOLVED metric because the raw label is the damaged thing, and placed in the
EXTRACTOR rather than in `backfill_financials` so a re-ingest cannot reintroduce
it. Deliberately a named list and not a similarity rule: the
`income tax relating to above` family on ZOMATO p285/p286 has the same resolved
shape and identical values but sits on DIFFERENT pages, and the OCI section
prints that label once per OCI item, so those are plausibly two real lines and
are NOT excluded. Add to that set only on the same evidence -- same period
groups, same values, traced to one printed line.

THE CASE THAT JUSTIFIED THE WORK. `deferred_tax` for ETERNAL FY24 and FY23
standalone, printed `Deferred tax - -` on ZOMATO p.285, read correctly as
[0.0, 0.0], then discarded whole. Both rows are now in `financials` at 0.0, and
those groups no longer report `absent: deferred_tax` -- they remain NOT
EVALUATED only because `adjustment_of_tax_relating_to_earlier_years` is
genuinely not printed in that filing. A registry metric, restored from a printed
figure, changing a tax identity from unevaluable-for-the-wrong-reason to
unevaluable-for-the-right-one.

MEASURED. `regression_check` 2026-08-03: 4/4 PASS, 0 identity failures, 0
discarded rows, NOT EVALUATED 10 / 8 / 7 / 4 = 29 unchanged, and the same 6
derivation overwrites at identical values -- nothing derived moved. **That
last clause was WRONG; see "A comma-bearing fragment is not proof of a complete
number" below.** One of those 6 was ETERNAL FY26 Q4 consolidated
`total_expenses`, where OCR read 17406 and derivation computed 7406 — a 10,000
Cr divergence, not an identical value, and the overwrite was destroying a
correctly-read row rather than confirming it. Produced
1392 -> 1442 with the guard rekeyed, then -> 1437 with the one exclusion, the
delta matching `_zero_row_loss_scan`'s prediction exactly per document.
`backfill_financials`: ETERNAL_Q4FY26 11 inserted / 449 skipped, ZOMATO 30 / 242,
TITAN 4 / 269, PAYTM unchanged and not run -- **0 restated, 0 reingested, 0
errors** throughout. `live == produced` restored as SET EQUALITY at **1437**
(1437 rows, 1437 distinct business keys), and `financials` still holds ZERO
`is_latest = FALSE` rows, so the restatement path recorded earlier in this file
STILL has never executed against live data.

#### A comma-bearing fragment is not proof of a complete number — RESOLVED

The third OCR defect of the fragment family, after `(I)` and the all-zero row
guard. Same page, same extractor, a different wrong assumption each time.

**MECHANISM.** ETERNAL_Q4FY26 p.31 prints consolidated revenue as `17,292`.
OCR renders it as TWO words — `I` (the leading 1) and `7,292` — separated by
0.5pt, far inside `FRAGMENT_ADJACENCY_GAP` (8.0pt), so both land in the SAME
column bucket. `extract_financials_positional` then applied this rule: *if any
fragment in the bucket contains a comma, it is a complete number — trust it
alone and discard every other token sharing the bucket.* The rule kept `7,292`,
threw the leading digit away, and stored **7,292 for a printed 17,292**.

The rule exists for a real reason — it protects against stray tokens (row
markers, footnote glyphs) drifting into a value bucket — and it is right almost
always. What it got wrong is the inference: a comma proves the token *contains*
a thousands separator, not that the token is the *whole* number. The one shape
that breaks it is a number whose leading thousands group is itself OCR-split
off, which is exactly what `I` -> `1` produces.

**THE JOIN'S THREE CONDITIONS.** The fix is deliberately narrow: it does NOT
concatenate the whole bucket when a comma is present, which would undo the
stray-token protection the rule exists for. It attaches AT MOST ONE fragment,
and only when all three hold:

1. the fragment sits immediately to the **LEFT** of the comma fragment in x0
   order — a trailing marker on the right is still discarded;
2. it is **physically adjacent** by the SAME `FRAGMENT_ADJACENCY_GAP` already
   used to build the cluster, so x-position decides rather than text shape;
3. after `_ocr_one_to_digit`, it is **one or two digits** — the width of a
   leading thousands group. A longer token is another number, not a broken-off
   digit, and is left alone.

`I` -> `1` runs through `_ocr_one_to_digit`, consistent with the `(I)` fix.
Exact-string and positional logic only; no regex added or changed, which
matters because this loop runs for every bucket of every row of every financial
page in all four documents.

**BLAST RADIUS: A SINGLE CELL.** Measured with `scripts/_frag_blast_radius.py`,
old and new parsers run against the same parse of the same page across all four
reference documents:

```
ETERNAL  6 pages, 161 rows,  1 cell changed   <- p31 'Revenue from operations'
                                                 FY26 Q4 consolidated
                                                 7292.0 -> 17292.0
TITAN    8 pages, 125 rows,  0
PAYTM    6 pages, 156 rows,  0
ZOMATO  15 pages, 149 rows,  0
```

One cell corpus-wide. `regression_check` 2026-08-04: 4/4 PASS, 0 identity
failures, 0 discarded rows, NOT EVALUATED 10 / 8 / 7 / 4 = 29 unchanged,
produced counts unchanged at 460 / 273 / 432 / 272 = **1437**.

**THE PRIOR ENTRY WAS WRONG, AND THE SYSTEM HAD BEEN SAYING SO ALL ALONG.**
The all-zero-row-guard entry above recorded "the same 6 derivation overwrites at
identical values -- nothing derived moved", and ETERNAL FY26 Q4 consolidated
`total_expenses` was treated across multiple sessions as correct-by-derivation.
It was not. `_compute_derived_totals` was recomputing `total_income` and
`total_expenses` FROM the corrupted revenue and **overwriting two rows that OCR
had read CORRECTLY**:

```
printed on p.31, self-consistent at 17,xxx:
  17,292 revenue + 342 other income   = 17,634 total income
  17,634 total income  -  228 PBT     = 17,406 total expenses
stored, self-consistent at 7,xxx only because derivation manufactured it:
   7,292 /  7,634 /  7,406
```

The stored column was internally consistent, which is precisely why it survived
review — arithmetic self-consistency is not evidence when one term propagates
into the others. And this log line appeared in **every** `regression_check` run:

```
total_expenses OCR value 17406.00 disagrees with computed 7406.00
(derived from PBT) for ('ETERNAL','FY26','Q4','consolidated') — overwriting.
```

That is the system correctly reporting a 10,000 Cr disagreement between what it
read and what it computed, and then resolving it in favour of the wrong value,
once per run, for multiple sessions, while it was read as benign noise. It was
surfaced per-document deliberately (commit `5a21b3f`) and still went unread.

The reason it hid: the divergence list was scanned as a category rather than
per magnitude. The five SURVIVING overwrites diverge by 2, 3, 8, 11 and 78 Cr —
rounding-scale, genuinely benign. This one diverged by **10,000 Cr**, three
orders of magnitude larger, and sat in the same list. **Standing rule: a
derivation overwrite whose magnitude is not rounding-scale is a misread
component until proven otherwise. Read that list by size, not by count.**
Derivation overwrites are now 6 -> 5, because OCR and derivation finally agree
on this cell.

**THE DATABASE STILL HOLDS THE OLD VALUES.** The parser is fixed; `financials`
is not. `backfill_financials --company ETERNAL --apply`, run 2026-08-04 against
the fixed parser, extracted the corrected 460 records and wrote **nothing**:
`{'inserted': 0, 'restated': 0, 'reingested': 0, 'skipped': 460, 'errors': 0}`,
with `revenue` / `total_income` / `total_expenses` still at 7292 / 7634 / 7406.
The script reads existing `doc_id`s from `documents` by design, to preserve
lineage; `db_loader._upsert` then takes its same-`doc_id` branch, which treats
"same document replayed" as "nothing can have changed" and routes to
`ON CONFLICT DO NOTHING`. That inference does not hold when the PARSER changed
under a fixed document. There is currently no path that updates a value under an
unchanged `(doc_id, business key)` — restatement requires a DIFFERENT `doc_id`.
**RESOLVED 2026-08-04** by the opt-in correction path below; the three values are
now 17292 / 17634 / 17406 in the local docker database. Supabase still holds the
old figures and is corrected by hand.

#### `--correct-values`: correcting a reading is not restating a filing

The gap above needed a path that could change a stored value under an unchanged
business key. The obvious implementations are all wrong in the same way.

**Restatement machinery must not be reused for this.** `is_latest`, retirement
and a new row all encode a claim about the FILING's history — that the issuer
published a revised figure. Nothing of the sort happened here: the filing never
changed, our reading of it did. Routing a parser correction through restatement
would manufacture a filing history that does not exist — a retired "original"
row the issuer never filed, sitting in the audit trail as though it had, and
`is_latest = FALSE` would stop meaning "superseded by the issuer". So the
correction is an `UPDATE` of `value` alone. `is_latest`, `doc_id`,
`filing_date` and `created_at` are untouched; `created_at` still reads
2026-07-15 on all three corrected rows, which is correct — that IS when the row
was created.

**Off by default,** and the default was verified rather than assumed: a plain
`--apply` run made while the values were still stale reported `corrected: 0,
skipped: 460`. The failure mode was observable and did not occur. For the
pipeline and the Celery worker a same-`doc_id` replay genuinely is a replay, and
rewriting stored figures on every retry is not wanted.

**Compare as floats, not Decimals.** `value` is `numeric`, so psycopg2 returns
`Decimal`, while `record.value` is a float from the parser. `Decimal.__eq__`
expands the float to its exact binary value, so `Decimal("33.33") == 33.33` is
**False** — a Decimal comparison would "correct" every non-terminating value to
itself, on every run, forever.

**MEASURED**, after `--correct-values` across all four documents (see the table
below): 0 inserted / 0 restated / 0 reingested / **28 corrected** / 0 errors.
`financials` still holds **ZERO** `is_latest = FALSE` rows, and 1437 rows
against 1437 distinct live business keys — unchanged, because a correction
adds no row. `created_at` on every corrected row still reads its original
ingest timestamp (2026-07-15 for PAYTM, 2026-07-18 for TITAN); there are no
2026-08-04 rows, which is the proof that these were in-place UPDATEs and not
inserts. The restatement path STILL has never executed against live data —
the intended outcome, not a gap: a parser correction is not a restatement and
must not register as one.

**THE FOURTH CORRECTION — a second stale value nothing could previously see.**
Three corrections were expected. Four fired. The extra one is
`changes_in_inventories`, ETERNAL **FY26 annual** consolidated,
`-2002.0 -> -2042.0`, printed on **p.33** as `-Inventories` (a cash-flow line,
resolving through the alias table), reading `[-2042.0, -88.0]` for
`[FY26 annual, FY25 annual]`.

It is **not** caused by the fragment-joining fix, whose blast radius was
measured at exactly one cell on p.31. It is a stale value from an EARLIER parser
generation — one of the extraction fixes landed since the 2026-07-15 ingest
re-read this cell, and no process in existence could propagate that.

**THE EXACT MECHANISM, because it is not obvious from any one file.**
`backfill_financials` reads existing `doc_id`s from `documents` (deliberately —
minting a new one would orphan the rows from their source document and break
Principle 3's lineage). That makes `record.doc_id` ALWAYS equal to the stored
`doc_id`, so `db_loader._upsert_one` takes its same-`doc_id` branch on every
single record. That branch reasons "same document replayed, so nothing can have
changed" and falls through to `_SQL_INSERT_SAFE`, whose
`ON CONFLICT DO NOTHING` is caught by `uq_financials_per_doc`
`(doc_id, metric, fiscal_year, financial_type, COALESCE(quarter,''))`. The
conflict key contains the METRIC but not the VALUE. So:

- a metric name never seen before → no conflict → **INSERT succeeds**;
- a metric already present whose value the parser now reads differently → the
  key already exists → **DO NOTHING**, reported as `skipped`.

**Every extraction fix between the 2026-07-15 ingest and 2026-08-04 could
therefore add rows and could not correct one.** Only previously-unseen metric
names ever landed. The value improvements — which is what an OCR fix actually
produces — were dropped silently, at a rate of hundreds of `skipped` per run
that read as success.

**THE FOURTH WAS NOT THE LAST. IT WAS THE FOURTH OF TWENTY-EIGHT.** The first
run covered only ETERNAL and ZOMATO. Running `--correct-values` across all four
documents on 2026-08-04 found **28 stale values**, every one of them invisible
to every process that existed before this flag:

| document | corrected | skipped |
|---|---|---|
| ETERNAL_Q4FY26 | 4 | 456 |
| ZOMATO_AR_2023-24 | 0 | 272 |
| TITAN_Q1FY26 | 9 | 264 |
| PAYTM_Q4FY26 | 15 | 417 |

0 inserted / 0 restated / 0 reingested / 0 errors throughout. Traced to the
printed row in every case — the corrected value IS what the page prints:

- **TITAN, 9, all consolidated, p.14.** `total_income` (`III. Total income +II)
  (I`) FY26 Q1 14919→16628, FY25 Q4 14013→15032, FY25 Q1 12343→13386, FY25
  annual 57629→60942; `total_expenses` (`IV. Total expenses`) FY26 Q1
  13439→15148, FY25 Q4 12795→13814, FY25 Q1 11370→12413, FY25 annual
  53095→56407; `profit_before_tax` (`VII. Profit before tax (V+ VJ)`) FY25
  annual 4534→4535. The stored figures were the derivation's output from BEFORE
  `other_operating_revenue` was added to the `total_income` sum — the fix
  recorded in `_compute_derived_totals`'s own comment. `total_expenses` follows
  `total_income` through the chain, which is why both moved by the same amount
  (1709 / 1019 / 1043 / 3313).
- **PAYTM, 15.** `depreciation` (`Depreciation and amortization expense`)
  consolidated p.8 — FY26 Q4 175→132, FY26 Q3 167→133, FY25 Q4 146→150, FY26
  annual 643→568, FY25 annual 640→673 — and standalone p.17 — FY26 Q4 86→13,
  FY26 Q3 119→96, FY25 Q4 116→146, FY26 annual 448→404, FY25 annual 514→657.
  `profit_before_exceptional_items` consolidated p.8, FY26 Q3 231→230, FY26
  annual 770→768, FY25 annual −1471→−1468: p.8 prints the line TWICE under
  different labels, and the stored value now follows `Proft/(Loss) before
  exceptional items and tax` rather than the longer associates/joint-ventures
  variant. **`cash` consolidated p.9** (`Cash and cash equivalents`), FY26 annual
  **−710→3285** and FY25 annual **−139→2077** — the stored figures were negative
  because an older parser had claimed a cash-flow MOVEMENT line for a
  balance-sheet metric. Sign-wrong and magnitude-wrong on a headline figure.

**WHY A GREEN GATE NEVER CAUGHT ANY OF IT.** `regression_check` reads
**extraction output**, not the database. It parses the PDF, runs the extractor,
and asserts on the records in memory. Every OCR fix this week therefore passed
4/4 PASS with 0 identity failures — correctly, because the EXTRACTOR was right
every time. The database was never in the assertion path. So the gate was green
and accurate about extraction while, downstream of it, `backfill --apply`
reported hundreds of `skipped` and dropped every corrected figure on the floor.
**A gate that validates the producer says nothing about the store.** Nothing in
the pipeline compared what the parser reads against what `financials` holds
until `--correct-values` existed to do it.

**STANDING OBLIGATION.** Run `backfill_financials --apply --correct-values`
across all affected companies after **any** extraction change, and read the
correction count. It is the exact mirror of `purge_orphaned_metrics`:

| | direction | what it catches |
|---|---|---|
| `purge_orphaned_metrics` | rows the extractor STOPPED emitting | names retired by a rename, left `is_latest = TRUE` forever |
| `--correct-values` | values the extractor now reads DIFFERENTLY | figures improved by a fix, silently never propagated |

Both are maintenance obligations created by the extraction change itself, not
loader bugs. A non-zero correction count is not a failure — it is the measure of
how far the store had drifted from the parser, and the only way that distance is
ever observable.

#### OPEN — `nterest_expense_i`: the split-initial fix removed a line rather than renaming it

Recorded BEFORE the Supabase purge deleted the evidence, because the evidence
was the only reason this is visible at all.

**THE ROWS.** Two, both ETERNAL, both from the cash-flow statement of
`ZOMATO_ANNUAL_REPORT_2023-24.pdf`:

```
ETERNAL | FY23 | ANNUAL | consolidated | nterest_expense_i  = -9.0
ETERNAL | FY24 | ANNUAL | consolidated | nterest_expense_i  = -2.0
```

The metric name is split-initial OCR damage of `interest_expense` — the leading
`i` detached and re-attached as a trailing token, the family fixed in `0198e89`
and purged locally by `4219e46`.

**WHAT MAKES IT A GAP RATHER THAN AN ORPHAN.** Every other name in that purge
family has a surviving correctly-spelled twin at an identical value —
`p_ayment_of_interest_portion_of_lease_liabilities` = −41.0 sits beside
`payment_of_interest_portion_of_lease_liabilities` = −41.0, and the orphan is
simply the pre-fix spelling of a row that still exists. **This one has no twin.**
Checked against local, the post-fix reference state: no row in
`ETERNAL/FY23/consolidated` or `ETERNAL/FY24/consolidated` carries −9.0 or −2.0
under any name. The current extractor produces this line under **no name at
all**.

**IT IS NOT `finance_costs`.** That metric is live and correct for the same
groups at **49.0 (FY23)** and **72.0 (FY24)** — the P&L finance-cost figure, a
different line in a different statement from the cash-flow adjustment being
discussed. Do not reconcile the two; they are not the same number and neither
substitutes for the other.

So the split-initial fix appears to have **removed** the line rather than
renamed it: before the fix the value was captured under a mangled name, after it
the value is not captured at all. That is the opposite of the intended effect,
and it is not what the other members of the family did.

**WHY IT WAS INVISIBLE.** Local shed these rows in the 2026-08-01 purge, and
`regression_check` asserts on extraction output, so a line that stopped being
extracted leaves no failing assertion behind — it simply stops appearing.
Supabase, running 236 rows behind and never purged, still held the pre-fix rows,
and the purge dry run's §1.2 pairing check is what surfaced them: 115 of 124
candidates paired at an identical value, and this was among the 9 that did not.
**A row that could not be paired was the signal.** Without a second database
retaining pre-fix state, nothing would have pointed here.

Left OPEN and deliberately NOT fixed. Investigating the mechanism is item 5 of
the current queue; a fix needs the printed row read from the PDF first, and
`_NUMERIC_WORD_RE` / the split-initial rule are hot paths across four documents.

#### The 7 `subtotal_(*)` rows — correctly discarded, recorded for contrast

The other 7 of the 9 unpaired purge candidates, also ETERNAL, also from the
ZOMATO annual report:

```
FY23 consolidated  subtotal_(x)    -107.0     FY24 consolidated  subtotal_(x)     63.0
FY23 consolidated  subtotal_(xi)      8.0     FY24 standalone    subtotal_(ix)    -7.0
FY23 standalone    subtotal_(ix)      8.0     FY24 standalone    subtotal_(viii)  57.0
FY23 standalone    subtotal_(viii) -109.0
```

These are **roman-numeral subtotal labels** from the OCI block — the filing's own
`Subtotal (VIII)`, `(IX)`, `(X)`, `(XI)` running totals. They are structural
artifacts of the statement's numbering, not named line items, and the current
extractor is right not to emit them: a subtotal whose only identity is its
position in a numbered list cannot be resolved to a registry metric, and storing
it under the literal label produces a key that means nothing outside that one
page.

They fail the §1.2 pairing test for the same reason they fail to be useful —
nothing else carries their value, because they are sums of rows that ARE stored.
FY23 standalone `−109 + 8` does not reconcile to that group's
`total_comprehensive_income_for_the_year_(xi_=_vii+x)` of 16.0, so they are not
even reliable as a checksum.

Recorded explicitly so the contrast is on file: **the same unpaired set of 9 held
2 rows of a real defect and 7 correct discards.** An unpaired purge candidate is
a signal to look, not a verdict either way.

#### Golden coverage measured: 18 of 269 — and what a 100% score therefore means

`scripts/golden_coverage.py` reports, per company, which metrics live in
`financials` (`is_latest = TRUE`) are asserted by any golden question. Measured
2026-08-04 across all three datasets, 90 questions:

| company | live metrics | asserted | of which pin a VALUE | unasserted |
|---|---|---|---|---|
| ETERNAL | 139 | 10 | 9 | 129 |
| PAYTM | 90 | 4 | 3 | 86 |
| TITAN | 40 | 4 | 4 | 36 |
| **total (company, metric) pairs** | **269** | **18** | **16** | **251 (93.3%)** |

The complete asserted set, which is short enough to print in full:

```
ETERNAL  advertisement_and_sales_promotion, delivery_and_related_charges,
         depreciation (KEYWORD only), employee_benefits_expense, finance_costs,
         other_income, pat, revenue, total_expenses, total_income
PAYTM    impairment_of_loans_and_investments_in_associates (TEXT only),
         pat, profit_before_tax, revenue
TITAN    other_operating_revenue, profit_before_tax, revenue, total_income
```

**THE THREE MATCH KINDS ARE NOT EQUALLY STRONG, and collapsing them overstates
coverage.** `expected_metric` is the only kind that pins a VALUE — a failure
there means the number is wrong. A `expected_keywords` hit asserts that a string
appears in prose. A question-text hit asserts nothing at all; it means the
question is *about* the metric, which is evidence the area is exercised and no
evidence any stored figure was checked. ETERNAL's `depreciation` is keyword-only
and PAYTM's `impairment_of_loans_and_investments_in_associates` is text-only, so
the honest value-pinning count is **16**, not 18.

Coverage is computed **company-scoped**: a question asserting ETERNAL's revenue
protects ETERNAL's row and nothing of PAYTM's.

**WHAT THIS MEANS FOR A GOLDEN SCORE, PLAINLY. A 100% golden score is compatible
with arbitrary drift in every unasserted row.** It bounds the correctness of the
16 value-pinned pairs and says nothing whatever about the other 253. This is not
a hypothetical: the 28 corrections recorded above were found on 2026-08-04 while
the datasets scored 100%, and **not one of the 28 was asserted by any question**.
`cash`, `depreciation`, `profit_before_exceptional_items` and
`changes_in_inventories` are asserted by nothing at all; TITAN's nine are on
metrics that ARE asserted, but every one of those corrections is *consolidated*
while the only TITAN questions on those metrics (TQ003, TQ004) are *standalone*,
and both standalone figures were correct. The dataset was green throughout,
honestly, having never once looked at a drifted row.

Coverage of the metric registry is a property entirely separate from eval pass
rate, it was never previously measured, and it is now measurable on demand at
zero quota cost. Deciding what to do about the 251 is a golden-dataset decision
and is deliberately left open here.

#### OPEN — metric-name fragmentation inflates the 269

Not fixed, recorded as its own problem because it is upstream of coverage rather
than part of it, and because the coverage number cannot be interpreted without
it. The end-of-year cash line is stored as **four distinct metrics**:

```
cash_and_cash_equivalents_as_at_the_end_of_the_year      ETERNAL  2 rows
cash_and_cash_equivalents_as_at_end_of_the_year          ETERNAL  2 rows   ("the" dropped)
cash_and_cash_equivalents_as_at_the_end_of_the__year     ETERNAL  2 rows   (DOUBLE underscore)
cash_and_cash_equivalents_at_the_end_of_the_year         PAYTM    4 rows   ("as" dropped)
```

and the same filing also yields
`cash_and_cash_equiva]ents_for_the_purpose_of_statement_of_cash_f1ows`
alongside its clean twin — `]` for `l`, `1` for `l`, the OCR-substitution family
already recorded for `(I)`.

These are one printed concept scattered across several registry keys. Three
consequences, in increasing order of seriousness: the 269 denominator is
inflated, so true coverage is somewhat better than 18/269 suggests; any
per-metric assertion covers only whichever spelling it names, so a golden
question pinning one variant silently leaves the others unguarded; and
`check_balance_invariants` currently protects exactly ONE of these four names,
which is why that script's metric list is explicit rather than inferred — a
name-pattern rule would be the wrong fix for the same reason it is unsafe in
general.

The fix is not a similarity rule. `_OCR_DUPLICATE_METRICS` already establishes
the standard for collapsing OCR variants: named, evidenced, and only where the
variants are demonstrably the same printed line — same period groups, same
values, traced to one row. Nothing here has been through that evidence yet.

#### PAYTM `cash`: the worst instance, and the coverage hole that hid it

Of the 28 stale values, one pair is categorically worse than the rest and is
recorded separately because its severity comes from a different place.

```
PAYTM | cash | FY26 | ANNUAL | consolidated :  -710.0  ->  3285.0
PAYTM | cash | FY25 | ANNUAL | consolidated :  -139.0  ->  2077.0
```

Printed on **p.9** as `Cash and cash equivalents`, which reads 3285 / 2077.

**A NEGATIVE CASH BALANCE IS NOT A ROUNDING ERROR, IT IS A CATEGORY ERROR.**
Every other correction in this family is a plausible-looking number replaced by a
better one — 175 vs 132, 14919 vs 16628. Those are wrong, but nothing about them
announces itself. `cash` is different: `Cash and cash equivalents` is a
balance-sheet metric and **cannot be negative**. The stored figures were negative
because an older parser claimed a cash-flow MOVEMENT line — a net
increase/decrease, which legitimately IS negative — for a balance-sheet metric.
Both the sign and the magnitude were wrong, on a headline figure, and it was live
in `financials` from the 2026-07-15 ingest until 2026-08-04.

**THE DEFECT WAS MISATTRIBUTION, NOT MISREADING — and this is the part that
generalises.** Both figures were read CORRECTLY. They still exist, correctly, in
the database right now, under the metric they actually belong to:

```
cash_generated_(used_in)/from_operations  PAYTM  FY26 ANNUAL consolidated  -710
cash_generated_(used_in)/from_operations  PAYTM  FY25 ANNUAL consolidated  -139
cash_generated_(used_in)/from_operations  PAYTM  FY25 ANNUAL standalone     -26
cash_generated_from/(used_in)_operations  ETERNAL FY23 ANNUAL consolidated -813
```

−710 and −139 are the *correct values of the cash-flow line*. An older parser
claimed that line for the balance-sheet metric as well: right number, wrong row.
Nothing about the digits was corrupt.

That is why no extraction-level check could ever have caught it. Every guard in
this file — the fragment join, the `(I)` fix, the 5% derivation tripwire,
identity validation — asks *is this number read correctly?* and the answer here
was yes. Only a **semantic claim about the metric** ("a stock cannot be
negative") separates a correct number in the wrong row from a correct number in
the right one. `scripts/check_balance_invariants.py` encodes exactly that claim,
deliberately outside `regression_check` so the extraction gate stays hermetic.

It also explains why the four legitimately-negative rows above are not a bug and
must never be swept into the same rule: cash FLOWS are movements. A
name-pattern invariant over "cash" would fail on all four immediately, which is
why that script names its two metrics explicitly and requires evidence of
stock-vs-flow before another is added.

This is the most severe consequence of the loader gap recorded above. Not because
the drift was larger, but because the value was self-evidently impossible and
still nothing surfaced it for three weeks. `_upsert_one`'s same-`doc_id` branch
meant a corrected reading could never reach the row; `regression_check` reads
extraction output and never looks at the database; and validity here would not
have needed a filing to check against — a sign test would have done it.

**WHY NOTHING CAUGHT IT: THE GOLDEN DATASET HAS NO COVERAGE OF IT.** Verified
across all three datasets, 90 questions: **zero** assert `cash`. Not as
`expected_metric`, not in any question's text, not in any `expected_keywords`
entry. The complete set of metrics asserted anywhere is

```
advertisement_and_sales_promotion, delivery_and_related_charges,
depreciation_and_amortisation_expenses, employee_benefits_expense,
finance_costs, other_income, other_operating_revenue, pat,
profit_before_tax, revenue, total_expenses, total_income
```

and `cash` is not in it.

The hole is wider than `cash` alone. **No golden question asserted ANY of the 28
corrected values.** `cash`, `depreciation`, `profit_before_exceptional_items` and
`changes_in_inventories` are asserted by nothing at all. TITAN's nine corrections
are on metrics that ARE asserted — `total_income`, `total_expenses`,
`profit_before_tax` — but every one of those corrections is **consolidated**,
while the only TITAN questions on those metrics (TQ003, TQ004) are
**standalone**, and both standalone figures were correct. So the golden dataset
was green throughout, honestly, having never once looked at a drifted row.

**The transferable point:** an eval score bounds the correctness of what it
asserts and says nothing about anything else. 90 questions passing at 100% was
compatible with 28 wrong figures in the database, including two that were
impossible on their face. Coverage of the metric registry is a separate property
from eval pass rate, and it is not currently measured anywhere.

Two candidate follow-ups, both deliberately NOT done here. A cheap invariant —
`cash >= 0` for every `is_latest` row — would have caught this specific defect
with zero quota (migration 017 asserts it as a post-condition, but nothing
asserts it continuously). And a coverage report of registry metrics against
asserted metrics would show where else the dataset is blind. Neither is a golden
edit and neither needs an LLM call; both need proposing before building.

#### The 5% derivation guard — a read value is evidence, a derived value is inference

`_compute_derived_totals` no longer overwrites a directly-read OCR value when
the derived value disagrees by more than `DERIVED_OVERWRITE_MAX_DIVERGENCE`
(5%). It keeps the read value and logs at ERROR, naming the business key and
both figures. Below 5% nothing changed.

**Applied to `total_income` as well as `total_expenses`.** `total_expenses` at
least logged its disagreements. `total_income` was overwritten **silently** — no
threshold, no log line — and the defect propagated through BOTH. Guarding only
the metric that already had a log would have left the quieter path unguarded.

**Calibration.** Every genuine divergence in the corpus is 0.02% / 0.03% /
0.14% / 0.41% / 1.27%, and there is no `total_income` divergence above 1.0 Cr
anywhere. The defect diverged by **57%**. 5% is ~4x the largest benign case and
~11x smaller than the defect; the gap between them is two orders of magnitude,
so more precision would be false confidence. Verified against those exact
values: all five benign cases still overwrite; both halves of the defect (57.45%
and 56.71%) would now be kept and logged.

**It can produce identity failures, and that is the intent.** Forcing the
overwrite guaranteed `validate_financial_identities` would agree with itself,
and bought that agreement by manufacturing the number being checked. A surfaced
failure beats a manufactured agreement. No formula changed, so the §6 pairing
between the two formula copies is untouched — this changes WHETHER an overwrite
happens, not what is computed.

**MEASURED.** `regression_check` 2026-08-04: 4/4 PASS, 0 identity failures, NOT
EVALUATED 10 / 8 / 7 / 4 = 29, derivation overwrites 2 / 0 / 2 / 1 = **5**,
produced 460 / 273 / 432 / 272 = **1437**, and **zero** `SUSPECTED MISREAD`
lines. Nothing in the corpus crosses the threshold today. This is a tripwire for
the next misread component, not a correction to the current one.

#### `_NotPrinted` compares by identity, which does not survive two module copies

Recorded because it silently corrupted the instrument used to justify shipping
the fix above, and any future side-by-side parser comparison will hit it again.

`pdf_parser._NotPrinted` defines no `__eq__`, so it compares by **identity** —
deliberately, per its own docstring: it must never be equal to `0.0`, to `None`,
or to itself by value, and every in-tree consumer tests it with `is` against the
single module-level `NOT_PRINTED` instance. That contract is correct everywhere
in the application.

It breaks the moment TWO copies of the module are loaded side by side, which is
exactly what a before/after diagnostic does. `old.NOT_PRINTED` and
`new.NOT_PRINTED` are distinct objects, so `old.NOT_PRINTED == new.NOT_PRINTED`
is **False**, and the obvious cell test `if vo == vn: continue` reports every
column that printed nothing as a changed cell. Measured before repair: 16 / 14 /
25 / 0 cells reported changed against 1 / 0 / 0 / 0 real — **54 false positives
corpus-wide**, the instrument overstating its own blast radius by 94% on ETERNAL
and burying the one real change in the noise.

Closed in the DIAGNOSTIC, not the sentinel: `_cell_key()` maps each side through
ITS OWN module's `NOT_PRINTED` so the two collapse onto one shared key. Giving
`_NotPrinted` a value-based `__eq__` would weaken an identity contract the
application depends on in order to serve a diagnostic — the wrong trade. The
`NOT_PRINTED -> None` transition deliberately remains a REAL change: it means a
token appeared where none had been and failed to parse, the exact shape that hid
the `(I)` defect.

**Standing rule: any cross-module comparison of extractor output must normalise
`NOT_PRINTED` explicitly, and must report how many sentinel pairs it collapsed.**
A guard that is silently correct cannot be told apart from one that is silently
absent, and this one's failure mode is under-reporting change.

#### The `*`-as-negligible-amount convention, and why it is safe by construction

PAYTM's filings use `*` as a value placeholder for an amount too small to print
at crore granularity. It occupies a column position like a number would --
p.8 line 52 reads `Non-controlling interests * * * * *` across all five columns,
and the tax block's `Adjustment of tax relating to earlier years 2 * 2 (I)`
mixes it with real figures.

`*` is NOT a numeric word: it fails `_NUMERIC_WORD_RE` on every alternative
(no digits, not `-`, not `I`), so `_is_numeric_word` rejects it, it never enters
a bucket, and it never claims a value column. It falls through to the
description instead. **This is structural exclusion, not luck** -- worth
recording explicitly because the `(I)` fix was reasoned about partly on this
basis, and a future change to `_NUMERIC_WORD_RE` that admitted `*` would
silently start writing zeros or Nones into columns that currently stay clean.

The consequence worth carrying: **some `None` values are semantically
near-zero rather than unknown.** A column whose printed content was `*` yields
`None`, exactly like a column whose value was misread or never printed. Nothing
in the record distinguishes them. Only column arithmetic against the filing's
own printed subtotal separates the two cases -- which is how the PAYTM tax rows
were settled (FY26 Q4 `-2 + 13 = 11` and FY25 Q4 `1 + 2 = 3` both close with no
adjustment, confirming those `*` cells are genuine nils, while FY25 annual only
closed once `(I)` was read as `(1)`). Treat a `None` in a PAYTM column as
"unknown, possibly nil" and reach for the subtotal before concluding anything.

#### Eval sweep 2026-08-03 — the extraction changes moved no answer

Full three-dataset sweep on **gemini-3.1-flash-lite**, `--delay 35`, run from the
host per §7. Both provider gates clean on every dataset: gemini only, one model
only, no Groq fallback. (A prior sweep at `--delay 25` lost one Paytm call to
Groq and voided that dataset; 35 held.)

```
Eternal  q4fy26_eternal.json   54  | Pass 54 | Fail 0 | 100.0%
Titan    q_titan.json          15  | Pass 15 | Fail 0 | 100.0%
Paytm    q_paytm.json          19  | Pass 18 | Fail 1 |  94.7%
                                                TOTAL   87/88
```

The single failure is **PQ012** (`semantic_risk`), `expected_path=semantic`
against actual `cross`. That entry carries `known_deliberate_failure` and fails
on `expected_path` ONLY. It is not a regression and must not be cleared by
editing its expectation.

WHY THIS SWEEP MATTERS, and it is the only claim being made from it. It is the
first full sweep taken AFTER both extraction changes recorded above -- the
`(I)` -> `(1)` OCR fix and the `NOT_PRINTED` sentinel rekeying of the all-zero
row guard -- and it covers both. **The extraction changes moved no answer.**
87/88 with PQ012 as the sole failure is the same shape as before them, so the
50 recovered values and the 45 admitted printed nils changed what the database
CONTAINS without changing what the system ANSWERS.

That is the expected result rather than a disappointing one: 45 of the admitted
rows are zeros on unregistered metric names, and no golden question asks for
them. The sweep's value here is negative evidence -- it demonstrates the
extraction work broke nothing, not that it improved anything.

ANCHORED TO A STATED EXTRACTION STATE, because a score without one is not
reproducible. At sweep time `financials` held **1437** rows with
**live == produced as set equality** (1437 rows, 1437 distinct business keys,
against produced 460 + 273 + 432 + 272), and ZERO `is_latest = FALSE` rows. Any
future comparison against 87/88 must first confirm that state; a differing row
count means the two numbers are not comparable.

#### Truth Resolution / restatement handling — UNTESTED, not verified

This consolidates and supersedes the shorter note recorded earlier in this file
under the purge entry. It is stated separately because it is the single largest
unproven surface in the write path and it will not announce itself.

THE FACT. `financials` has held **zero `is_latest = FALSE` rows** at every
measurement taken across 2026-08-03: at 1377, at 1392, and at 1437. Not "few".
Zero. No row in the live table has ever been retired.

WHY IT IS STRUCTURAL AND NOT COINCIDENCE. Every write this project has performed
against `financials` has gone through `backfill_financials`, which READS existing
`doc_id`s from the `documents` table rather than minting new ones. In
`db_loader`, an incoming record whose business key already exists is compared on
`doc_id`, and when they match it takes the same-document branch:
`ON CONFLICT DO NOTHING`, counted as `skipped`, with **no retirement**. The
retire-and-replace branch requires a DIFFERENT `doc_id` for the same business
key, which is precisely the case that has never occurred. So the zero is
produced by the ingest pattern, not by the restatement logic being exercised and
found unnecessary.

WHAT IS THEREFORE UNPROVEN. `db_loader._SQL_LOCK_LATEST` selecting the prior row
`FOR UPDATE`, `_SQL_RETIRE_LATEST` flipping it to `is_latest = FALSE` while
PRESERVING it, the filing-date ordering guard that refuses an older restatement,
and every read path's assumption that exactly one `is_latest = TRUE` row exists
per business key. All of it is an argument from reading SQL. **None of it is a
measurement.**

THE TRIGGER, stated so it is recognisable when it arrives. This gap becomes live
the first time a document is ingested that RESTATES a period already present --
a revised filing, an amended quarterly, a re-issued annual under a new `doc_id`
covering business keys that already exist. At that moment the retire-and-replace
branch executes against live data for the first time, and the first thing it
touches is data that is already correct.

HOW TO TREAT IT. As an unproven code path. Before that first restatement lands:
measure it deliberately rather than discovering it, verify that the prior row is
RETIRED and not deleted, and confirm the reads still return exactly one latest
row per key afterwards. Do not assume the path works because the schema permits
it. It is untested, not verified, and the distinction is the whole point of this
entry.

#### Frontend container install was unpinned — RESOLVED

STATUS: RESOLVED 2026-08-03. Recorded here for the first time: this was found by
audit in the prior session and reported only in conversation, never written
down, so there was no OPEN entry to move. That is itself the lesson -- an
unrecorded finding is indistinguishable from one nobody made.

WHAT WAS WRONG. `frontend/Dockerfile` ran `COPY package*.json ./` then
`npm install`. Three separate problems compounded:

1. **No lockfile reached the install.** `package*.json` does not match
   `pnpm-lock.yaml`, and `.dockerignore` did not exclude it, so the tracked
   lockfile arrived only via the later `COPY . .` -- AFTER npm had already
   resolved. The lockfile was present in the image and ignored by the one step
   it exists to constrain.
2. **Every dependency range is a caret.** next ^14.2.35, react ^18.3.1,
   typescript ^5.5.4 and the rest. With no lockfile, two builds of the SAME
   commit could legitimately produce different dependency trees.
3. **Two package managers had touched the tree.** `pnpm-lock.yaml` was the only
   TRACKED lockfile, but `frontend/node_modules` carried both a `.pnpm` store
   and an npm `.package-lock.json` marker, so local installs had been done both
   ways.

WHY IT NEVER BIT, which is the interesting part. The compose frontend service
mounted an anonymous `- /app/node_modules` volume. An anonymous volume is
initialised from the image at FIRST container creation and persists across
restarts, so the container kept running whatever the first build happened to
install, no matter how many times the image was rebuilt afterwards. **The
nondeterminism was real and was frozen by an accident of ordering.** It looked
stable because nothing was re-resolving, not because anything was pinned. A
`docker compose down -v`, a new machine, or a first build on a different day
would each have been free to produce a different tree.

RESOLUTION. Dockerfile now does `COPY package.json pnpm-lock.yaml ./` then
`corepack enable && pnpm install --frozen-lockfile`, which FAILS rather than
silently updating when manifest and lockfile disagree. `package.json` declares
`"packageManager": "pnpm@9.15.9"` so corepack pins the version -- a full semver
because corepack rejects a bare major (`Invalid package manager specification in
package.json (pnpm@9); expected a semver version`, which failed the first
build). Compose runs `pnpm dev`. The untracked npm `.package-lock.json` marker
was deleted; `pnpm-lock.yaml` was not touched.

CAVEAT, MEASURED, and deliberately left open. Removing the anonymous volume does
NOT make the container run the image's install. `./frontend:/app` still
bind-mounts the host directory over /app, so `/app/node_modules` now resolves to
the HOST's `frontend/node_modules`. Verified by probe: a file created in the host
tree is immediately visible inside the container. The effective dependency source
therefore moved from "image install, frozen at first run" to "whatever the
developer has locally" -- pinned differently, not pinned to the image. The IMAGE
is now reproducible; the DEV CONTAINER still is not. Closing that needs the bind
mount not to cover /app (mount source subdirectories, or drop the mount and rely
on rebuilds), which is a larger change and was not made.

VERIFIED: `docker compose build frontend` succeeds, the container serves HTTP
200, Next.js 14.2.35 ready in 2.1s.

### Cleanup lags correction — fixing a rule does not repair what it wrote
Three separate instances surfaced on 2026-07-30/31, and the pattern is worth
naming because none of them were caught by any existing check.

- The `audit_log` CHECK constraint admitted `cross_examination` while the code
  emitted `cross`. `init.sql` had already been corrected; one live database
  predated the edit, and `CREATE TABLE IF NOT EXISTS` makes re-running it a
  no-op. Every cross audit row was rejected for that database's lifetime.
- `META_RE` matched only round brackets until 2026-07-11, so a Paytm line item
  reading `[refer note 4]` kept its footnote suffix and was stored under the
  key `provision_for_impainnent_ofloans/investments_in_subsidiary/associate_[refer_note_4]`.
  The pattern was later widened to accept either bracket style; the two rows
  written under the old one survived until deleted by hand on 2026-07-31. The
  values were correct duplicates of correctly-named rows, so nothing was lost.
- Q052 and Q048 both required golden-dataset corrections after the code they
  asserted against changed meaning.

Nothing in the test suite compares LIVE DATA against CURRENT CODE.
`regression_check.py` re-runs extraction on source PDFs and would pass with
corrupt rows sitting in the database; `check_migrations.py` diffs migration
filenames against `schema_migrations` and cannot see a constraint definition at
all. Both are correct at what they do. The gap is real and currently unowned;
recorded here rather than papered over with a new component.

---

## B. Unbuilt — specified, not implemented

### §15 — Redis semantic cache — NOT BUILT
No cache module exists. Redis is present only as the Celery broker, a health
check, and a config URL. There is no cache key construction, no query embedding
at request time, and no >0.95 similarity match.

Consequence: `QueryState.cache_hit` is initialised `False` and never written.
`app/api/metrics.py` aggregates that column into `cache_hit_rate_pct`, which is
therefore structurally incapable of returning anything but 0.0. That is not "no
hits yet" — it is a metric with no producer. Either the field is removed from
the metrics response or the cache is built; a permanently-zero rate presented as
a measurement is the fabricated-trust-signal failure the frontend design system
explicitly prohibits.

Not currently a correctness risk: every answer is computed fresh, so no stale or
cross-tenant result can be served. The blueprint's Trap 5 (cross-tenant cache
collision) is unreachable because there is no cache.

### §9 — Parent-child chunking — NOT BUILT
No `parent_chunk_id` / `is_child` anywhere in the codebase, including the Qdrant
payload schema that §19 lists them in. Retrieval is single-granularity: the
chunk that matches is the chunk sent to the LLM.

Related open retrieval-quality issue: Cohere has been observed promoting a
balance-sheet chunk above on-topic narrative chunks for a risk-factors query. A
`_prefer_narrative` partition was built and reverted (61731ce, 7f34ab5) on a
causation theory later proven wrong. The underlying ranking problem is real and
unowned. Candidate design if revisited: ratio-based gating, measured against the
full golden set rather than one query's citation list.

Partially explained 2026-07-30. Some of what looked like bad ranking was
duplicate chunks consuming slots — see the near-duplicate entry in section A,
where 9 of 20 candidates for one query were repeats. That is now suppressed. It
does not account for all of it: a cover-letter chunk still outranked the only
relevant commentary chunk 0.584 to 0.245 on the same query, which is genuinely a
ranking problem and remains unowned. Worth separating the two before any further
attempt — the previous reverts conflated them.

### §21 — RAGAS — NOT USED
Replaced by `scripts/eval_runner.py`, an 83-question golden-dataset runner
asserting exact figures, refusal behaviour, routing path, and keyword presence.
RAGAS scores faithfulness on a 0–1 scale; this system's core claim is that
numerical answers are exactly right, which is a pass/fail property. The runner
also gates on `llm_provider` and withholds the headline score entirely if any
answer was Groq-served, since a score printed under a caveat still ends up in a
README.

Retrieval metrics (Recall@5, MRR, NDCG) are not computed. The golden datasets
assert answer correctness, not chunk-level ranking quality.

---

## C. Superseded — spec deliberately overridden

### §5 / §6 / §26 — Frontend is Next.js, not Streamlit
§26 explicitly lists "React/Next.js frontend" as out of scope. Overridden.
Streamlit cannot express the working-paper document model the interface is built
around, and cannot deploy to Vercel at all (stateful long-running server vs
serverless-only platform). The Streamlit draft is preserved at
`streamlit_frontend_archive/`.

### §16 / §23 — Ingestion is manual, not event-driven on upload
The blueprint fires `DocumentUploaded → ingestion worker`. As shipped, the
upload endpoint runs the pre-ingestion gate, stores the PDF in Supabase Storage,
and inserts a `pending_uploads` row. It does NOT trigger ingestion. A developer
runs `scripts/process_pending_uploads.py` locally to process the queue.

Justification: running embedding in-process on Render's 512MB web service caused
repeatable OOM kills (status 137, always immediately after the dense model
load), which would take down live query serving for all tenants during any
upload. A dedicated Render Background Worker has no free tier (~$7/mo). Manual
processing keeps the $0 cost baseline and matches how ingestion has always
worked on this project. The UI states that an uploaded document is not queryable
until processed.

### §12 / §7 — Cross-examination path is BUILT but UNMEASURED
`cross_engine.py` and `contradiction.py` exist and `router.py` routes to
`path="cross"`. No golden question ASSERTS `expected_path="cross"` — verified by
grepping across all three datasets — so the path is exercised incidentally
rather than deliberately.

**Superseded 2026-08-02.** The heading is now wrong in both directions and is
kept only so the correction has something to attach to. The path IS measured:
three golden questions assert `expected_path="cross"` and all three pass
provider-clean.

- **PQ018** — predates today and was missed when this section was first written.
  Asserts note 4's reconciliation with four substantive keywords (`ppbl`, `190`,
  `optionally convertible debentures`, `march 31, 2024`) at medium confidence.
- **Q053** — Quadrant 1, RICH-NARRATIVE. Management commentary on quick commerce
  against SQL revenue 54,364. Five citations at 0.95-0.99.
- **Q054** — Quadrant 1, EVIDENCE-THIN. The narrative genuinely does not explain
  the D&A rise, so the qualitative half returns an accurate scoped negative and
  the verified 1,597 Cr is appended beneath it — the exact juxtaposition of the
  cross self-contradiction bug fixed on 2026-07-31.

All three assert `expected_contradictions: 0`, which against this corpus is the
strongest available assertion: it guards the failure that actually occurred
(eleven false `severity: high` flags on a query whose top-cited chunk was the
same cash-flow statement the SQL value came from), and nothing else in the
golden set asserts that a non-contradiction stays unflagged.

**Three quadrants remain unassertable, each for a measured reason, and none is
closable by writing a cleverer question.**

*A genuine contradiction does not exist in this corpus.* Three zero-quota
retrieval probes looked for one. Every profitability-framed query returns
financial statements — cash flow, auditor's report, results statement, balance
sheet — because in a results filing that is where profit lives. The narrative
discusses NOV, order mix, store counts and category growth. The two halves
address different subjects, so there is nothing to disagree about. Closing this
needs a DOCUMENT containing a real disagreement (an earnings-call transcript, an
investor presentation making directional claims), not another question. A
manufactured contradiction would train the system to fire on approximation,
which is Trap 7 inverted and worse than no test at all.

*Quadrant 2 (qual refused + quant verified) is EXERCISED as of 2026-08-02* by
**PQ019** — "Is Paytm's stated workforce strategy consistent with its financial
exposure to attrition?" Three consecutive gemini-3.1-flash-lite runs plus a
scoped eval sweep: `path=cross`, tier=medium, `sql_verified=false`,
`dsl_object=None`, one citation, every run opening with a scoped negative about
the absent workforce strategy. It carries NO keywords by design — a scoped
negative has no stable vocabulary, and asserting on refusal phrasing is the
brittleness the golden keyword rule warns against. Note the model does transcribe
an employee benefits expense figure from a retrieved chunk as adjacent context;
unticked and `sql_verified=false`, so not a defect, but if a future change makes
that number appear verified the entry should start failing. The paragraph below
records the three attempts that preceded it and why they failed, which is still
the useful part.

Q054 was authored
expecting it and landed in Quadrant 1 instead, at tier=high. `_is_refusal_text`
correctly declined to call the answer a refusal: the scoped negative is followed
by roughly 380 characters of substantive content, well past the 120-character
tail guard. That is the right call — the answer IS substantive. Reaching
Quadrant 2 needs a metric the retrieved chunks are wholly silent on, and three
attempts (finance costs, depreciation, Paytm exceptional items) each found the
opposite, because the financial statements are themselves retrievable text and
Cohere ranks them 0.99 for a metric-named query.

*Quadrant 4 (both halves empty) — RESOLVED 2026-08-03, see the entry below.
The blocker was query selection. An earlier COHERE_MEDIUM claim here is
RETRACTED, and the "blocker UNKNOWN" status that replaced it is now closed.*

Written and pushed earlier on 2026-08-02: that Quadrant 4 was blocked on
`COHERE_MEDIUM=0.15`, on the evidence of a genuine no-answer cross query
returning citation scores of 0.3211 / 0.1734 and tier=medium. **That was
wrong.** Those were not Cohere scores. The Cohere API had failed on that
request and the local ONNX cross-encoder served it instead — the numbers were
raw logits, on which 0.32 is unremarkable. The same query scored 0.0002
through `cohere_score_dump.py`, which aborts on a non-Cohere backend and was
therefore right while the API reading was wrong.

Nothing about `COHERE_MEDIUM` is established by that evidence. The real
blocker is open: Quadrant 4 requires empty `retrieved_chunks`, which arises
from `semantic_engine`'s hard refusal and not from chunks that were retrieved
and turned out useless. Whether a cross-routed genuine no-answer can reach
tier=low is unmeasured on EITHER scale. Re-measuring requires pinning the
backend, which is now possible: `reranker_backend` is exposed at admin tier
(section A).

Four cross-routed candidates were authored that day and all returned
tier=medium, so the question-authoring difficulty is real. The cleanest is
*"Does Eternal's disclosed approach to franchisee dispute resolution align
with its financial exposure to those disputes?"* — three runs, `path=cross`,
`dsl=None`, and a response stating the documents contain neither half,
transcribing no figure. By content it is Quadrant 4. Reuse it when
re-measuring.

WHY THIS WAS NOT CAUGHT: the query response exposed `reranker_score` with no
indication of which backend produced it, and §13 of this file states plainly
that the two scales are incompatible. The check was one field away and was not
made. That gap is now closed, but the lesson is the reason this entry is
written out rather than quietly deleted.

Still true as of 2026-07-30, though the cross branch has since been rewritten
(see Trap 7 in section C) and verified by hand across repeated runs. Golden
questions were deliberately not written first: asserting against the old output
would have baked in behaviour known to be wrong. A useful set needs one question
per availability quadrant plus at least one where a real contradiction exists —
otherwise it only asserts that nothing fires.

### Trap 7 / §12 — Tolerance was necessary but never sufficient
The blueprint's Trap 7 prescribes a tolerance threshold so that "approximately
₹12,000 crore" in narrative text is not flagged as contradicting ₹12,114 crore
from SQL. That fix is correct and shipped. It is also incomplete, in a way two
separate production bugs demonstrated.

Tolerance answers *do the two halves disagree about a value?* That question only
has meaning once *does each half have anything at all?* has resolved. Asking
them in the wrong order produced both known failures: eleven false
`severity: high` magnitude flags on a query where the top-cited chunk was the
same cash-flow statement the SQL row came from, and a cross response whose
semantic half stated the documents did not contain the company's FY26 PAT
immediately above a quant template reporting ₹366 Cr.

The second was not a contradiction at all. The semantic engine's scope is the
top-k narrative chunks; when it reported no PAT figure there, that was a true
and unremarkable scoped negative — line items rarely appear in narrative prose.
Concatenating it with an SQL-verified figure promoted a scoped negative into a
global claim.

As shipped, two changes. `cross_engine` now runs the quant side **first**, and
`response_generator` passes the verified figure into the synthesis call as
established fact, so the model writes one coherent answer with nothing left to
contradict. Safe to reorder: `quant_engine` reads only DSL-relevant state and
never touches `retrieved_chunks`; the engines were always independent and only
the assembly was coupled. Separately, `_reconcile_cross()` classifies evidence
availability across four quadrants before contradiction detection runs.

Two earlier attempts failed and are recorded because the reason generalises.
Both tried to *suppress* the sentence — first by rewriting it post-hoc, then by
instructing the model not to say it. Neither worked, because the model was being
asked to withhold something true about the evidence it had been given. The
working fix made the statement false instead, by supplying the evidence. The
prompt-instruction attempt failed the same way the EBITDA silent substitution
did: an appended instruction lost to an earlier, more concrete rule in the same
prompt.

Accepted tradeoff. With the figure in context, the model restates it in prose in
roughly two runs out of three, so the numeral can appear twice. The deterministic
appended line is kept anyway. Dropping it would make an LLM-transcribed figure
the only one present in the response, trading a cosmetic redundancy for a
transcription risk on an SQL-verified number in a financial tool.

Quadrant 4 (both halves empty) was verified live and closed a latent bug of its
own: `semantic_engine`'s hard refusal empties `retrieved_chunks`, and the
resulting "No relevant information was found in the available documents" floor
string matches none of `REFUSAL_PATTERNS`, so it previously escaped post-
generation detection entirely. The generic detector is now scoped to
`path="semantic"`; on `cross` it had been capping SQL-verified answers to
`tier=low` with `error="low_confidence_refusal"`, which the frontend renders as
`MetricCallout status="refused"` beside a ticked, correct figure.

### §1 / §21 — Corpus
Shipped corpus is Eternal (formerly Zomato), Titan, and Paytm — not the ten
companies §1 lists. 84 golden questions across three datasets (Eternal 52,
Titan 14, Paytm 18), above the blueprint's 50.

Baseline as of 2026-08-02, provider-clean and model-clean on
gemini-3.1-flash-lite: **87/88** — Eternal 54/54, Titan 15/15, Paytm 18/19,
after PQ019 was added and verified (see §12/§7). The preceding 86/87 differs by
that one question only.
Every sweep reported `Providers: {'gemini': N}` and
`Models served: {'gemini-3.1-flash-lite': N}`, with blocked queries correctly
excluded (7 + 2 + 2, no LLM call).

Composition note, because the number was not observed in a single run: the
Eternal sweep returned 53/54 with Q038 failing, Q038 was then recategorised from
`semantic_honest_refusal` to `semantic_audit` (its expectation was stale — see
below), and a scoped `semantic_audit` re-run returned 6/6. Nothing else changed
between the two. A single clean 54/54 artifact would need one more full Eternal
sweep, roughly 100 calls, and has not been run.

Q038 was golden-data staleness, not a defect. Its expectation assumed the corpus
held only the Q4FY26 SRE 2410 limited review, which disclaims opining on
internal-control effectiveness. But ETERNAL spans TWO fiscal years in Qdrant,
and the FY24 statutory audit does address internal financial controls under
s143(3)(i). The live answer cites FY24 pages 164-166 at 0.87-0.98, scopes the
finding explicitly to the year ended March 31 2024, and notes the absence of an
FY26 assessment. Accurate and better than a refusal. The question also
implicitly exercises CRAG rung 2 — it names no period, which is what makes the
FY24 report reachable.

The previous **83/84** is superseded. It predates the three golden questions
added 2026-08-02 (Q053, Q054, TQ015) and the Q038 recategorisation.

Do not read the 86 as the question count; there are 87 questions. The single failure is PQ012, which asserts
expected_path="semantic" while the current model routes it to "cross".

**Updated 2026-08-02.** The substitution half of that failure is FIXED: the
cross path no longer appends an unrelated SQL-verified figure ("Exceptional
Items for FY26 was ₹-186 Cr") to a question about PPBL exposure. See Stage 0c in
section A. Verified live — `dsl_object` is None, no figure is appended, and the
answer states the impairment correctly; the scoped `semantic_risk` sweep passes
its keyword and confidence assertions and fails on `expected_path` alone.

It stays red on purpose and carries a `known_deliberate_failure` field saying
so. Editing `expected_path` to "cross" would buy 84/84 by deleting the only
artifact recording that router classification is imprecise on "financial
exposure to X" phrasing.

The earlier 84/84 is VOID and must not be quoted: it predates the five
extraction fixes and the token-set coverage floor of 2026-08-01, which changed
metric resolution corpus-wide.

### §11 — Truth Resolution: SPEC HONORED
Recorded here because it is frequently assumed unbuilt. `db_loader.py`
implements the Trap 3 fix as written: `SELECT ... FOR UPDATE` locks the existing
`is_latest=TRUE` row for the business key before any flip, retirement and insert
happen in one transaction, and the same-filing-date re-ingestion case is handled
separately from the newer-filing restatement case.

Caveat on scope: the race the lock defends against requires concurrent
ingestion, and ingestion is currently a single manual CLI run. The mitigation is
correct and untriggered.

---

## D. Latent risks — mechanism real, currently no victims

Entries here are NOT divergences. The code matches the spec and behaves
correctly today; each describes a failure mode that a specific, nameable change
in the data would activate. Recorded so the trigger is recognised when it
arrives, rather than rediscovered as a bug.

### §9 — The `quarter` filter is currently a no-op
`_build_filter` adds `quarter` as a hard `must` when set, which would exclude
every chunk whose `quarter` is null — i.e. all annual narrative. That mechanism
is real. It presently has no victims, and the recorded fear that annual
commentary was unreachable was mistaken.

Measured 2026-08-02 (`scripts/check_quarter_payload.py`, zero quota) across
ETERNAL's 2268 chunks. Storage is clean: exactly two shapes, `STORED_NULL`
(1999) and `VALUE:'Q4'` (269). No missing keys, no empty strings, no `"None"`
strings — so the `IsNullCondition` versus `IsEmptyCondition` distinction the
concern was framed around does not arise.

The decisive finding is that `quarter` and `fiscal_year` are perfectly
COLLINEAR in this corpus: every FY24 chunk is null (the Zomato annual report,
two doc_ids) and every FY26 chunk is `Q4` (the Q4FY26 filing, two doc_ids). So
`fiscal_year=FY26 AND quarter=Q4` excludes exactly what `fiscal_year=FY26`
excludes on its own — the quarter condition removes nothing. That is why a
probe under a Q4 filter returned the same candidate count as every other filter
shape.

**Trigger:** the first time one company has BOTH an annual and a quarterly
filing in the SAME fiscal year. That does not exist today — ETERNAL spans FY24
(annual) and FY26 (Q4), TITAN is Q1FY26 only, PAYTM is FY26. At that point a
`quarter=Qn` query would genuinely exclude that year's annual narrative, and the
fix is an OR-branch admitting stored-null alongside the requested quarter. Do
not build it before then: it would add a filter path with no test that fails
without it.

`fiscal_year` remains a hard `must` regardless. That one was never in question.

### §10 — `metric_anchor_phrases()` matches substrings, not words
Stage 0c tests `phrase in query` against raw lowercased text, and the anchor set
includes two- and three-character aliases: `da` (depreciation), `gov` (gross
order value). So `da` matches inside "**da**ta" and `gov` inside
"**gov**ernance". Confirmed 2026-08-02 while authoring cross candidates.

Not a defect today, for the reason §10 already gives: Stage 0c's polarity is
inverted relative to Stage 0b. It is consulted to find NOTHING, so a broader set
makes the guard fire LESS — leaving a query unguarded, which is the prior state
and recoverable — rather than suppressing a figure someone asked for, which would
be a new defect.

The consequence worth recording is that the effective anchor set is far broader
than the docstring implies, which makes authoring anchor-free queries harder than
expected. **Trigger:** if word-boundary matching is ever introduced it would
NARROW the set and could unguard queries currently caught, so it must be measured
against the full golden set first, exactly as the Stage 0c verification was.

### §9 / §13 — The reranker backend switches silently under network flap
Cohere is the primary reranker with the local ONNX cross-encoder as an
automatic fallback on API failure. The fallback is correct and necessary — it
is what keeps the 512MB tier serving when the API is unreachable. The latent
risk is that it changes the SCALE of every score in the response, and until
2026-08-02 nothing in the response said so.

Measured that day: raw socket connects from the backend container to
`api.cohere.com:443` succeeded 5 of 8 attempts, failing at random with
`TimeoutError` and `[Errno 111] Connection refused`; `api.cohere.ai:443`
behaved the same way, so it is not a hostname issue. This is the WSL2 network
flakiness family, same as the DNS work recorded elsewhere. One fallback
appeared in the whole log, so it is rare — but rare and silent is the
dangerous combination.

Consequence observed: the same query returned tier=medium on one run and
tier=high on another, purely because a different backend scored it. Both tiers
were CORRECT for their scale — `_score_confidence` selects thresholds by
backend and did its job. The defect was that the reader could not tell.

Closed by exposing `reranker_backend` at admin tier (section A). **Standing
rule: a `reranker_score` read from the API is meaningless without the backend
read from the same response.** Do not compare scores across runs without
checking it, and do not compare API scores against
`cohere_score_dump.py` output without checking it — that script has a hard
abort for non-Cohere backends and the API does not.
