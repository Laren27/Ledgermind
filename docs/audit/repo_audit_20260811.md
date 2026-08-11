# LedgerMind — Repository State Audit

**Date:** 2026-08-11
**Scope:** read-only. No file in the tree was modified; no write command, migration, ingest, purge, LLM call, Cohere call, or eval run was executed.
**Repo state at audit:** `main` @ `8df18ba`, working tree clean, unchanged at exit.

---

## 0. Preliminaries — what I actually measured against

Every claim below is anchored to one of two databases. They are **not** the same database, and the distinction changes how you read several findings.

| | LOCAL (docker `postgres:15-alpine`) | SUPABASE (`.env` `DATABASE_URL`) |
|---|---|---|
| Reached by | `settings.database_url` inside the container | direct connect, explicitly, for comparison only |
| Server | `172.18.0.3:5432`, PG 15.18 musl | `aws-0-ap-northeast-1.pooler.supabase.com` |
| db / user | `ledgermind` / `ledgermind_app` | `postgres` / `ledgermind_app` |
| `schema_migrations` | 12 rows | 16 rows |
| `documents` (tenant Alpha) | **11** (9 `indexed` + 2 `uploaded` ZOMATO seed) | **9** |
| `financials` (tenant Alpha) | 1437 | 1437 |

**The running stack reads LOCAL, not Supabase.** `docker-compose.yml:51-52` sets an `environment:` block that overrides `.env`:

```
$ docker compose exec -T backend printenv DATABASE_URL
postgresql://ledgermind_app:app_dev_pass@postgres:5432/ledgermind
# .env says: postgresql://ledgermind_app.sypstvwfzklerccwgaoh:***@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres
```

Unless stated otherwise, **all queries below are LOCAL**, and every one printed its scoping. RLS was confirmed non-bypassing:

```
SCOPING: user/db = ('ledgermind_app', 'ledgermind')
documents (NO tenant set): 0
financials (NO tenant set): 0
```

Corpus figures in the brief reconcile against **Supabase** (9 documents); LOCAL carries two extra ZOMATO seed rows in state `uploaded` with placeholder checksums (`abc123_z`). Qdrant (`2531` points) and `financials` (`1437`) match on both. 5 PDFs → 9 documents because consolidated and standalone share a `sha256_checksum` (e.g. ETERNAL Q4FY26 `8c53a092` appears twice).

Pre-flight per CLAUDE.md §4 was clean: code loads from `/app/app/`, `GEMINI_MODEL=gemini-3.1-flash-lite`, `QDRANT_URL` is the Cloud `https://` URL, no insecure-connection warning.

### One contradiction, reported rather than reasoned past

I predicted the LOCAL database would hold known-wrong values, because it is missing migrations `015`/`016`/`017` (`correct_eternal_fy26q4_misread_revenue`, `..._changes_in_inventories`, `correct_titan_paytm_stale_values`). **That prediction was wrong.** Direct comparison:

```
LOCAL (stack reads this)      SUPABASE (.env, overridden)
  revenue         17292.0       revenue         17292
  total_expenses  17406.0       total_expenses  17406
  total_income    17634.0       total_income    17634
```

LOCAL was re-ingested after the parser fix and never had the misread; `015`'s own header states this ("This database was ingested separately from the local docker one"). The migration-count divergence is **by design** (`018_..._local` vs `019_..._supabase`), not corruption. The residual finding is narrower — see F11.

---

## 1. Summary table

Ranked by blast radius, not category.

| # | Finding | Blast radius at company N+1 | Confidence |
|---|---|---|---|
| **F1** | Company onboarding needs a code edit; near-miss names silently misfile into an existing company | Ingest refuses outright, **or** corrupts an incumbent's data | Verified |
| **F2** | Router failure yields an unfiltered whole-tenant search that still answers confidently | Cross-company wrong answers, no refusal | Verified |
| **F3** | `unit="crore_inr"` hardcoded; no scale detection exists anywhere | 10×/100× wrong values, silently comparable | Verified |
| **F4** | Document metadata is caller-asserted, never validated against content | Misfiled doc is invisible or contaminating; already happened twice | Verified |
| **F5** | Restatement confidence penalty is entirely unimplemented | First real restatement answers at full confidence | Verified |
| **F6** | 174 stored metric names have no registry anchor — 686/1437 rows (48%) | Unqueryable data; grows linearly per new format | Verified |
| **F7** | `financial_type` retrieval filter is functionally inert (excludes 17 of 2531) | consolidated/standalone bleed; already measured at 23/85 | Verified |
| **F8** | Ingest completion Gate 4 cannot observe its own run | A zero-chunk ingest passes the gate | Verified |
| **F9** | Corpus-fitted constants: `min_chunks`, `SCAN_CHAR_LIMIT`, continuation windows, top-k | Rejected or truncated new formats | Verified |
| **F10** | No test suite — no pytest, 2 ad-hoc scripts | Every N+1 regression is found in production | Verified |
| **F11** | Two divergent databases; `check_migrations` reports permanent drift + wrong advice | Following its instruction damages Supabase | Verified |
| **F12** | Documented-vs-actual drift in CLAUDE.md and docstrings | Misleads the next change | Verified |
| **F13** | Loose ends: `Ellipsis` collection, dead payload fields, stale `__main__` defaults | Cosmetic to low | Verified |

---

## F1 — Company onboarding requires a code edit, and near-miss names silently misfile

**What.** The set of ingestible companies is a hardcoded Python list; an unknown company is hard-refused, while an unknown company whose name *contains* an existing alias is silently absorbed into that existing company.

**Evidence.** `app/ingestion/entity_resolver.py:18-26` — `COMPANY_REGISTRY` is a literal list of 7 `CompanyProfile` entries. `app/ingestion/pipeline.py:96-101` refuses anything else:

```python
profile = resolve_company(company)
if profile is None:
    raise ValueError(
        f"Cannot ingest — unresolvable company: '{company}'. "
        f"Add an alias to COMPANY_REGISTRY in entity_resolver.py before retrying."
    )
```

The matcher falls back to unanchored substring containment, returning the first dict-order hit — `app/ingestion/entity_resolver.py:312-318`:

```python
def resolve_company(raw_name: str) -> Optional[CompanyProfile]:
    key = raw_name.lower().strip()
    profile = _ALIAS_INDEX.get(key)
    if profile: return profile
    for alias, prof in _ALIAS_INDEX.items():
        if alias in key: return prof
    return None
```

Executed live:

```
  TITANIUM INDUSTRIES LIMITED      -> TITAN
  Titan Biotech Limited            -> TITAN
  ETERNAL MATERIALS PVT LTD        -> ETERNAL
  ONE97 REALTY                     -> PAYTM
  Bundl Foods                      -> SWIGGY
  HDFC BANK                        -> None
  Reliance Industries              -> None
```

**Titan Biotech Limited is a real, separately listed Indian company** (BSE 524717), distinct from Titan Company Limited.

**Blast radius.** Two failure modes, and the quiet one is worse. A genuinely new company (HDFC, Reliance) is refused at ingest — SaaS onboarding is gated on a code change and redeploy, so self-serve is impossible. But a new company whose legal name contains an incumbent alias is resolved to the incumbent, `company` is overwritten with `profile.primary`, and its financials land in the same tenant under the incumbent's key. Every subsequent query for the incumbent then silently mixes two companies' numbers, and nothing in the audit trail records that a substitution occurred. The substring branch is also dict-insertion-ordered, so which incumbent wins depends on registry ordering.

**Proposed change (not applied).** Move the company set out of code into a tenant-scoped table, and make resolution exact-match-plus-explicit-alias with no substring fallback; an unresolved name should return a distinct "needs onboarding" state rather than either a refusal or a guess. Re-measure afterwards: `resolve_ticker` is also on the query path (`app/engines/router.py:106`), so router entity extraction and the full golden sweep both need re-running — the substring branch may currently be *helping* some golden questions resolve.

**Confidence.** Verified.

---

## F2 — A router failure produces an unfiltered whole-tenant search that still answers

**What.** When both LLM providers fail or return unparseable JSON, the router returns all-null metadata; the retrieval filter treats null as "no filter", so the query searches every company and every year in the tenant and still produces a confident answer.

**Evidence.** Router fallback, `app/engines/router.py:151-160`:

```python
return {
    "company": None,
    "ticker": None,
    "fiscal_year": None,
    "quarter": None,
    "financial_type": "consolidated",
    "path": "semantic",
    "route_reason": "FALLBACK_ERROR: classification failed on all providers",
```

Filter construction, `app/engines/retriever.py:169-197` — only `tenant_id` and `is_latest` are unconditional:

```python
must_conditions = [
    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
    FieldCondition(key="is_latest", match=MatchValue(value=is_latest)),
]
if company:
    must_conditions.append(...)
if fiscal_year:
    must_conditions.append(...)
```

Note the inconsistency: `quarter` is guarded with `is not None` (line 184) while `company`, `fiscal_year` and `financial_type` use bare truthiness. There is no path that refuses on missing entity.

**Blast radius.** This works today because of a property of the corpus, not of the code: there are three companies, in three different sectors (quick-commerce, fintech, consumer goods), with distinct vocabulary — so an unfiltered dense+sparse search usually still surfaces the right company's chunks. At company N+1, and emphatically at N+20 with several issuers in one sector, "revenue from operations grew 23% year on year" is near-identical text across filings. An unfiltered search then retrieves a competitor's chunk, the reranker scores it highly because it *is* topically relevant, and the answer cites a real page from the wrong company. `route_reason` does record `FALLBACK_ERROR`, so it is auditable after the fact — but nothing refuses at request time.

The same path is reachable without an outage: any query where the LLM returns a company name that fails `resolve_ticker` (line 106-108) sets `company = None` while the rest of the routing succeeds.

**Proposed change (not applied).** On `FALLBACK_ERROR`, refuse rather than answer. Separately, distinguish "no company mentioned" (legitimately unfiltered — a corpus-wide question) from "company extraction failed" (must refuse); today both are `None`. Re-measure: the golden datasets contain corpus-wide questions that legitimately carry no company, so the refusal must key on the error flag, not on nullness, or those questions will start failing.

**Confidence.** Verified by code reading; I did not exercise it live, as that requires an LLM call.

---

## F3 — Every value is labelled `crore_inr` by assertion; no scale detection exists

**What.** The unit is a hardcoded string literal at the point of record construction. Nothing in the parser or extractor reads the scale annotation that Indian filings print above every statement.

**Evidence.** `app/ingestion/financial_extractor.py:457`:

```python
records.append(FinancialRecord(
    tenant_id=tenant_id, doc_id=doc_id, company=company, ticker=ticker,
    fiscal_year=fiscal_year, quarter=quarter, financial_type=financial_type,
    metric=normalized_metric, value=float(value), unit="crore_inr", filing_date=filing_date,
))
```

Same literal at lines 560, 592, 607, 632 for the derived rows. A search for any scale handling across the parser and extractor returns nothing:

```
$ grep -rniE "in (crore|lakh|million)|₹ in|rs\. in|scale|multiplier|magnitude" \
    app/ingestion/pdf_parser.py app/ingestion/financial_extractor.py
financial_extractor.py:471:# documents 2026-08-04: every genuine rounding-scale divergence is
financial_extractor.py:480:# sessions because that list was read as a category rather than by magnitude.
financial_extractor.py:483:# magnitude and precision here would be false confidence.
```

All three hits are prose in comments. And the stored data is uniform, which is exactly why this has never surfaced:

```
units: [('crore_inr',)]
```

**Blast radius.** All three current issuers report in ₹ crore, so the assertion is true for the whole corpus and invisible. It is not true of Indian filings generally — "₹ in lakhs" and "₹ in millions" are both common, and many issuers switch scale between the summary tables and the notes within one document. At company N+1 reporting in millions, every extracted value is stored 10× off with a `crore_inr` label that asserts otherwise. Nothing downstream can detect it: `dsl_compiler` selects on `metric` and returns `value` and `unit` without comparing units across rows, so a cross-company comparison would put a ₹-million figure and a ₹-crore figure side by side as if commensurable. The contradiction engine's `MAGNITUDE_TOLERANCE_PCT = 5.0` (`app/engines/contradiction.py:69`) would flag a 10× gap as a contradiction only if both figures reached it through the same question — otherwise the wrong number is simply served.

This is the highest-severity *silent* failure in the audit: F1 and F2 produce a wrong answer that is at least traceable to a named company; F3 produces a wrong *number* attached to the right company and the right page.

**Proposed change (not applied).** Detect the scale annotation per table during parsing, carry it through `PageBlock` to `FinancialRecord`, and normalise to a single internal unit at load time while preserving the source unit for display. Re-measure afterwards: `regression_check`'s golden value assertions (`pipeline.py:617-620`, e.g. `("consolidated","revenue",None): 54364.0`) are expressed in crore and would need re-stating if the internal unit changes; `_compute_derived_totals` and `validate_financial_identities` both do arithmetic across records and must be checked to confirm they never mix units.

**Confidence.** Verified.

---

## F4 — Document metadata is caller-asserted and never checked against the document

**What.** Company, fiscal year, quarter, doc type and filing date all arrive as form fields the uploader types. The ingestion gate checks only that the file *is* a financial filing — never that it is *this* filing.

**Evidence.** `app/api/documents.py:59-68`:

```python
async def upload_document(
    file: UploadFile,
    company: str = Form(...),
    ticker: str = Form(...),
    fiscal_year: str = Form(...),
    doc_type: str = Form(...),
    filing_date: str = Form(...),
    quarter: Optional[str] = Form(None),
```

The only content check is `check_is_financial_filing` (line 94), which scores generic SEBI-filing signals — `Financial Results`, `Ind AS`, `Auditor's Report`, `CIN:` — and never compares the claimed company or period to the text. There is no cross-check anywhere between the form values and the parsed document.

This has already caused two production incidents, both recorded in-tree:

- `app/ingestion/document_classifier.py:235-238` — "a test of the upload endpoint re-registered the real Paytm [document] … meant the `fiscal_year` retrieval filter could never match and every Paytm [query failed]".
- `app/ingestion/pipeline.py:478-479` — "omitting a flag silently ingested a document under Eternal's metadata. That already happened: TITAN was mislabelled Q4".

**Blast radius.** Today ingestion is operator-driven by one person against five known PDFs, so a typo is caught by the operator who made it. At N+1 with self-serve upload this is the primary data-integrity surface: a wrong `fiscal_year` makes the document permanently unretrievable (the filter can never match), and a wrong `company` files it under another issuer. Both are silent — the ingest reports success, chunks are written, and the failure only appears as "the system doesn't know about this filing" weeks later.

Two smaller defects sit in the same handler. `ticker` is a required form field but is overwritten from `COMPANY_REGISTRY` downstream (`pipeline.py:492` comment), so the caller's value is a no-op. And the field comment at `documents.py:64` documents `doc_type` as `transcript` while the chunker matches `earnings_transcript` (`app/ingestion/chunker.py:186`) — the DB `CHECK` constraint catches the mismatch, but at ingest time, after the upload has been accepted and stored:

```
documents_doc_type_check -> CHECK ((doc_type = ANY (ARRAY['annual_report','quarterly_result','drhp','earnings_transcript'])))
```

**Proposed change (not applied).** Extract company/period/type from the document during the gate pass and require agreement with the asserted values, rejecting on mismatch rather than warning. Re-measure: the gate currently reads only the first 2 pages (F9), which is not enough to identify period reliably for an annual report, so the scan window and the validation need designing together.

**Confidence.** Verified.

---

## F5 — The restatement confidence penalty is entirely unimplemented

**What.** `confidence_node` caps confidence when `restatement_disclosed` is set. Nothing in the codebase ever sets it, and the node that is documented to set it runs *after* the node that reads it.

**Evidence.** The read, `app/engines/confidence.py:86`:

```python
if state.get("restatement_disclosed"):
    capped_tier = _cap_tier(current_tier, "medium")
```

Every occurrence of the symbol in the entire backend:

```
app/engines/confidence.py:82:    # restatement_disclosed is set by response_generator in a later pass for
app/engines/confidence.py:86:    if state.get("restatement_disclosed"):
app/engines/state.py:123:    restatement_disclosed: bool
app/engines/state.py:197:        restatement_disclosed=False,
app/engines/response_generator.py:24:flag restatement_disclosed=True so confidence.py can apply its penalty.
```

`response_generator.py:24` is a **docstring line**, not code — a targeted search for an assignment in that file returns nothing:

```
$ grep -n 'restatement_disclosed\s*=\|\["restatement_disclosed"\]' app/engines/response_generator.py
NO ASSIGNMENT IN response_generator.py
```

So the flag is initialised `False` at `state.py:197` and never written. It is dead in two independent ways, because the graph also orders the reader before the claimed writer — `app/engines/graph.py:104`:

```python
graph.add_edge("confidence", "response_generator")
```

**Blast radius.** Zero today, and that is precisely the problem: `is_latest` is `TRUE` for all 1437 rows, so no restatement exists in the corpus and the dead path is unobservable. The moment a company files a revised figure — the ordinary case that motivated the feature, and a certainty as the corpus grows — the answer is served at whatever confidence the retrieval scored, with no cap and no disclosure. The user is told a restated figure with HIGH confidence and no indication that a prior version exists.

Note the design distinction the codebase draws correctly elsewhere: migration `015`'s header argues at length that a *parser* correction is not a restatement and must not touch `is_latest`. That reasoning is sound and preserved. It just means the restatement machinery has never been exercised by real data.

**Proposed change (not applied).** Either implement the flag in a node that runs before `confidence`, or move the restatement check into `confidence_node` itself where it can query directly. Re-measure: this will start capping tiers on any query touching a restated period, so the golden expectations for confidence tier need re-checking once real restated data exists — there is none to test against today, which is itself worth noting before shipping.

**Confidence.** Verified.

---

## F6 — 174 stored metric names have no registry anchor (686 of 1437 rows)

**What.** Just under half of all stored `financials` rows carry a metric name that is a slugified raw label rather than a canonical registry name. Among them are 13 OCR-mangled names and 33 clusters of distinct spellings denoting the same concept.

**Evidence.** The registry itself is in good shape — 70 metrics, 175 aliases, and **no alias claimed by more than one metric**:

```
=== REGISTRY SHAPE ===
total metrics: 70
dsl_enabled  : 26
raw/derived  : 62 / 8
total aliases: 175

=== ALIASES CLAIMED BY >1 METRIC ===
   none
```

Exactly one registry anchor phrase is an OCR artifact rather than a real alias — `app/metrics/registry.py:111`, on `total_income`:

```python
aliases=("total income", "total income i+ii", "ill total incomc 1+11"),
```

(`"ill total incomc 1+11"` is a mangling of "III. Total Income (I+II)".)

The stored data is where the drift lives. Scoped query against LOCAL:

```
SCOPING: SET LOCAL app.tenant_id = a0000000-0000-0000-0000-000000000001 (Tenant Alpha)
distinct stored metrics: 223
  -> 174 distinct names, 686 rows of 1437 total   [no registry anchor]

=== REGISTRY METRICS NEVER STORED ===
  21 of 70:  active_users, adjusted_ebitda, ebit, ebitda, ebitda_margin, equity,
    fcf, gmv, gov, gross_margin, gross_profit, mau, mtu, operating_cash_flow,
    operating_expenses, operating_profit, orders, pat_margin,
    ppe_disposal_gain_loss, total_assets, total_liabilities
```

13 of the unanchored names are OCR-mangled (45 rows):

```
rows=  2 'cash_and_cash_equiva]ents_for_the_purpose_of_statement_of_cash_f1ows'
rows=  4 'equity_instmments_1hrough_other_comprehensive_income_{515)'
rows=  5 'total_other_comprchcnsiye_income/(loss)_for_the_period/ye.ar'
rows=  5 'total_other_comprehensive_income/(loss)_for_the_11eriod/year'
rows=  3 'total_comprehensive_income/(loss)_for_lhe_period/year_(ix+x)_ll30>_fl66)'
rows=  4 'xjj._paid_up_equity_share_capital_(face_value_~_per_share):_1'
rows=  5 'face_value_of_the_share_(jnr)'
   ... 13 distinct names, 45 rows
```

And 33 near-duplicate clusters (similarity ≥ 0.85) cover 301 rows. Representative:

```
  cluster 3 (8 rows):
     [  2] (_iii)_income_tax_relating_to_above
     [  2] (_iii)_ncome_tax_relating_to_above
     [  2] income_tax_relating_to_above
     [  2] ncome_tax_relating_to_above
  cluster 9 (12 rows):
     [  4] cash_generated_(used_in)/from_operations
     [  4] cash_generated_from/(used_in)_operations
     [  2] cash_generated_from_(used_in)_operations
     [  2] cash_generated_from_operations
  cluster 15 (4 rows):
     [  2] increase/(_decrease)_in_other_financial_liabilities
     [  2] lncrease/(decrease)_in_other_financial_liabilities
```

Two independent artifacts are visible here: dropped leading capitals (`ncome`, and `lncrease` where `I` was read as `l`), and inconsistent bracket placement in the source typography.

*Caveat on my own method:* the 0.85 clustering over-merges in at least one case — cluster 8 groups `cash_and_cash_equivalents_at_the_beginning_of_the_year` with `..._at_the_end_of_the_year`, which are genuinely different metrics. Treat "33 clusters / 301 rows" as an upper bound on true duplication; the individually-quoted clusters above I checked by eye.

Related and worth separating: the extraction path is honest about this. `financial_extractor.py:447-448` logs every fall-through, and `entity_resolver.py:309` logs `"Unknown metric: '%s' … storing as-is"`. Nothing is hidden — but nothing is retired, either, which is exactly the maintenance obligation CLAUDE.md §9 describes.

**Blast radius.** The unanchored 48% is not *wrong* data — the values are real and correctly extracted — but it is **unreachable data**: `dsl_compiler` validates the requested metric against `METRIC_REGISTRY` (`dsl_compiler.py:79-84`) and rejects anything not in it, so no DSL query can ever return these rows. They are storage cost and audit surface with no query path. At company N+1 the ratio gets worse, not better: each new filing format contributes its own spellings of the same concepts, and because the loader retires rows by a business key that includes `metric`, a name that extraction stops emitting is never retired and stays `is_latest = TRUE` permanently.

The inverse gap matters too: 21 of 70 registry metrics have never been stored, including every derived metric (`ebitda`, `gross_profit`, `operating_expenses`) — consistent with the registry's own documented "no compiler support yet" — but also `total_assets`, `total_liabilities` and `equity`, which are ordinary balance-sheet lines a user would reasonably ask for.

**Proposed change (not applied).** Per the brief I propose nothing that alters the registry's contents. The tractable change is operational, not semantic: schedule the existing `purge_orphaned_metrics` dry run as a standing report so the unanchored count is tracked over time rather than discovered. Any actual alias addition is a measured-constant change and belongs in its own reviewed commit with a `regression_check` before and after.

**Note:** I attempted the `purge_orphaned_metrics` dry run and **stopped it deliberately**. It re-parses every reference PDF (`scripts/purge_orphaned_metrics.py:96`, inside the `for doc in DOCUMENTS` loop at line 88), and CLAUDE.md §7 warns that parsing the corpus twice exhausts WSL RAM and restarts the distro. The direct DB evidence above is both cheaper and stronger.

**Confidence.** Verified.

---

## F7 — The `financial_type` retrieval filter is functionally inert

**What.** 98.6% of chunks are tagged `financial_type='unknown'`, and the filter admits `unknown` as a match — so it excludes 17 chunks out of 2531.

**Evidence.** Payload distribution across all 2531 points:

```
financial_type:
   'unknown'        2496
   'consolidated'     18
   'standalone'       17
```

The filter, `app/engines/retriever.py:189-197`:

```python
if financial_type:
    must_conditions.append(
        Filter(should=[
            FieldCondition(key="financial_type", match=MatchValue(value=financial_type)),
            FieldCondition(key="financial_type", match=MatchValue(value="unknown")),
        ])
    )
```

Measured selectivity:

```
chunks EXCLUDED by a financial_type=consolidated filter: 17
chunks EXCLUDED by a financial_type=standalone   filter: 18
total: 2531
```

This is a *consequence* of a deliberate and well-argued decision, not an accident. `app/ingestion/section_classifier.py:469-473` assigns a real `financial_type` only to `FINANCIAL_STATEMENT` blocks and `UNKNOWN` to everything else, because narrative content that merely sits within a section's page range is not actually scoped to that statement set — the comment documents TITAN's entire press release having been made permanently unretrievable by the previous behaviour. 35 chunks are `FINANCIAL_STATEMENT`, matching 18 + 17 exactly.

The cost of that fix is measured in-tree: git log records `financial_type leak probe — 23/85 citations from opposite statement set` (`9088ebf`) and `5/17 questions change under filter value` (`dbc5cdc`).

**Blast radius.** The consolidated/standalone distinction is the single most consequential axis in Indian financial reporting — the two statement sets carry genuinely different numbers for the same metric name. In this corpus, 553 metric keys exist under *both* types:

```
metric keys present in BOTH types: 553
financials by type: [('consolidated', 761), ('standalone', 676)]
```

For the **quant** path this is enforced correctly — `dsl_compiler` puts `financial_type = %s` in the SQL WHERE clause. For the **semantic** path it is not enforced at all, so a question about standalone results retrieves consolidated narrative and vice versa, at a rate already measured at 23/85 citations. At N+1 this scales with the number of documents that have both statement sets — which is all of them.

Compounding: `app/engines/router.py:128-130` silently defaults `financial_type` to `consolidated` on any unrecognised value, so a standalone question that the router mis-parses becomes a consolidated query with no signal.

**Proposed change (not applied).** Separate "this chunk is narrative, not scoped to a statement set" from "this chunk's statement set could not be determined" — currently both are `unknown`, and only the second should be admitted by the filter. Re-measure: the leak probe (`scripts/financial_type_leak_probe.py`) and counterpart probe already exist and quantify exactly this; re-run both before and after, and expect the 5/17 questions that change under filter value to move.

**Confidence.** Verified.

---

## F8 — Ingest completion Gate 4 cannot observe its own run

**What.** The final ingestion gate asserts that *some* point exists for the tenant, not that this run indexed anything — the exact defect class already identified and fixed in Gates 1 and 2, left in place in Gate 4.

**Evidence.** `app/ingestion/pipeline.py:645-660`:

```python
# Gate 4: Semantic search works
results = client.query_points(
    collection_name=COLLECTION_NAME,
    query=[0.0] * 384,    # zero vector — returns any valid points
    using=DENSE_VECTOR_NAME,
    query_filter=Filter(must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=ALPHA_TENANT)),
        FieldCondition(key="is_latest", match=MatchValue(value=True)),
    ]),
    limit=1,
    with_payload=False,
).points
search_ok = len(results) > 0
```

No `doc_id` filter, no company filter, `limit=1`. Contrast Gate 2 at lines 581-591, where this was explicitly fixed:

```python
# This previously read verify_collection(ALPHA_TENANT)["total_points"],
# a TENANT-WIDE count. ETERNAL alone holds 2268 chunks, so a threshold of
# 100 was already satisfied before the run started -- the gate passed
# unconditionally on any ingest into a non-empty tenant, including one
# that indexed zero chunks. It could not fail.
this_run_chunks = result["chunks_indexed"]
```

Gate 4 still has the property Gate 2 was fixed to remove. With 2531 points already in tenant Alpha, `len(results) > 0` is true regardless of what the current ingest did.

**Blast radius.** Gates 1, 2 and 3 are now run-scoped and would catch a failed ingest, so Gate 4 is not the last line of defence — the practical severity is lower than the other findings here. But it reports "Semantic search: ✅" as evidence about a document it never queried, and at N+1 that reassurance is worth exactly nothing on a tenant that already has data. The one case it would catch — a completely empty tenant — is the case that no longer occurs.

**Proposed change (not applied).** Filter on `result["doc_ids"]` as Gates 1 and 2 now do. Re-measure: nothing else depends on it; this is a self-contained assertion change.

**Confidence.** Verified.

---

## F9 — Constants fitted to the current corpus

**What.** A set of absolute thresholds chosen against five known PDFs, several of which are size- or layout-dependent rather than semantic.

**Evidence and per-constant assessment.**

| Constant | Location | Basis | Behaviour at N+1 |
|---|---|---|---|
| `--min-chunks` default `100` | `pipeline.py:505`, `chunker.py:630` | Chosen, not measured | The brief's own calibration case: TITAN legitimately produces 24 and exits 1 on a fully successful ingest. It is an **absolute** floor on a quantity proportional to document length. A 4-page result and a 300-page AR are judged by the same number. |
| `SCAN_CHAR_LIMIT = 6000` | `gate.py:68` | "roughly first 2 pages" | The gate reads only the first ~2 pages. An annual report opening with a glossy cover, contents and chairman's photo may not reach `MIN_SCORE = 6` before the window closes, and is **rejected at upload**. Fails closed, which is the safe direction, but rejects legitimate filings. |
| `MIN_SCORE = 6`, `MIN_CATEGORIES = 2` | `gate.py:64-65` | Chosen | Regex signals are English-only and SEBI-format-specific (`CIN\s*:`, `Limited Review Report`). |
| `CONTINUATION_MAX_PAGES = 4`, `AUDITOR_CONTINUATION_MAX_PAGES = 6` | `section_classifier.py:243,245` | Comment says "auditor reports commonly run 3-8 pages" | A statement or auditor report running longer than the window loses its section label partway through. Fitted to observed page-run lengths in this corpus. |
| `ANCHOR_HEADING_CHARS = 400` | `section_classifier.py:242` | Chosen | Assumes the identifying heading falls in the first 400 chars of a block. |
| `TOP_K_RETRIEVAL = 20`, `TOP_K_RERANK = 5` | `retriever.py:58-59` | Chosen | **Absolute, not relative to corpus size.** Top-20 from 2531 chunks is a generous net; top-20 from 50k chunks across 50 companies is a much weaker one, particularly when F2 has removed the company filter. |
| `MIN_VALUE_COLUMNS = 2` | `pdf_parser.py:44` | "a real financial data row always has at least 2 periods" | A single-period statement (common in a first-year filing or an interim standalone) has one. |
| `CHARS_PER_TOKEN = 4` | `chunker.py:47` | English-prose heuristic | Degrades on dense numeric tables. |
| `LOCAL_HIGH/MEDIUM = -4.5 / -7.5` | `semantic_engine.py:37-38` | **"Calibrated from live test: strong match scored -3.83"** | A single observation — see below. |
| `COHERE_HIGH/MEDIUM = 0.5 / 0.15` | `semantic_engine.py:48-49` | 83 golden questions | High boundary well-validated; **medium/low boundary explicitly unvalidated** — see below. |

Two of these deserve separate comment because the code is candid about them.

The Cohere thresholds carry their own honest caveat at `semantic_engine.py:41-47`:

> "No query in this run fell between 0.15-0.5 or below 0.15, so the MEDIUM/LOW boundary itself remains unstressed by real data — revisit if a future query's tier looks wrong given its logged score."

So `COHERE_HIGH` (0.5) is measured against 83 questions, but `COHERE_MEDIUM` (0.15) — the constant that decides **refuse vs. answer** — has never been exercised by a real query. At N+1, with documents the retriever handles less well, queries will land in that band for the first time, and the refusal boundary is untested at exactly the moment it starts mattering.

The local thresholds are weaker still. The header comment reads "Calibrated from live test: strong match scored -3.83 on financial text" — one observation, which CLAUDE.md §8 explicitly warns against ("Do not trust a single observation. Verify across runs *and* across models"). This matters more than it first appears because the local ONNX reranker is the **fallback** path (`retriever.py:423`), which fires on Cohere API failure — including the WSL2 network flap CLAUDE.md §4 documents. The less-validated thresholds serve precisely when conditions are worst.

To the system's credit, the scale-confusion bug this created was found and fixed, and the fix is documented at `semantic_engine.py:52-59`; `reranker_backend` is now recorded on every chunk (`retriever.py:402`, `434`), which is what makes the two scales distinguishable at all.

**Blast radius.** Mixed, and mostly *loud* rather than silent: `min_chunks` and the gate thresholds produce visible failures (non-zero exit, HTTP 400) that an operator investigates. The section-continuation windows and `TOP_K` are the quiet ones — they degrade retrieval quality without any error.

**Proposed change (not applied).** Make the size-dependent floors relative rather than absolute (`min_chunks` as a function of page count; `TOP_K_RETRIEVAL` scaled to filtered corpus size). Widen `SCAN_CHAR_LIMIT` or make the gate scan until it reaches a decision rather than a fixed budget. Re-measure after any of these: `min_chunks` changes affect the ingest gate for every document; `TOP_K` changes affect every semantic and cross answer and require a full golden sweep. The Cohere and local thresholds are measured constants under CLAUDE.md §1 §3 — I propose nothing for them beyond noting that the medium/low boundary is unvalidated by the code's own admission.

**Confidence.** Verified (values and locations); the N+1 behaviours are reasoned from the code, not exercised.

---

## F10 — No test suite

**What.** There is no test framework and no unit coverage of any ingestion, retrieval, or extraction module. Two hand-written check scripts exist and both pass.

**Evidence.**

```
$ docker compose exec -T backend python -c "import pytest"
ModuleNotFoundError: No module named 'pytest'

$ find . -name "test_*.py" -o -name conftest.py -o -name pytest.ini   # excl. venv/node_modules
./backend/scripts/test_synthesis_floor.py
./backend/scripts/test_eval_matcher.py
```

Both run clean, and both are genuinely good — deterministic, zero-quota, and each documents why it exists:

```
$ python3 backend/scripts/test_eval_matcher.py
  PASS  golden_dataset holds the four documented q*.json inputs
  PASS  live dataset validates: q4fy26_eternal.json
  ... FAILURES: none

$ docker compose exec -T backend python -m scripts.test_synthesis_floor
  PASS  error is synthesis_unavailable
  PASS  tier capped to low
  PASS  floor apology not concatenated as analysis
  PASS  gemini then groq -> groq (order-independent)
  ALL CHECKS PASSED
```

Their combined scope is the eval keyword matcher and the LLM-outage floor. Nothing covers `pdf_parser`, `financial_extractor`, `section_classifier`, `document_classifier`, `chunker`, `entity_resolver`, `retriever`, `dsl_compiler`, or `confidence`.

**Blast radius.** The de facto test suite is `regression_check.py` — a 4-to-5-document integration gate that requires the corpus PDFs, parses them (RAM-heavy, per CLAUDE.md §7), and asserts against golden values for one dataset only. It is a good regression detector for known documents and provides no coverage of behaviour on unknown ones. Every finding in this report that is phrased as "at company N+1" is untested by construction: there is no fixture representing a document format the corpus has not seen, so the parser's assumptions cannot be exercised against anything that would violate them.

The paths a new format would exercise **first**, in order, none of which have unit coverage:

1. `gate.check_is_financial_filing` — accept/reject on an unfamiliar cover page (F9)
2. `entity_resolver.resolve_company` — the substring fallback (F1)
3. `pdf_parser` column bucketing — `MIN_VALUE_COLUMNS`, comma handling, the ~18pt centring assumption (`pdf_parser.py:593`)
4. `document_classifier.detect_sections` — the no-marker default (F12) and the at-most-two-sections model
5. `section_classifier` continuation windows (F9)
6. `financial_extractor` unit assignment (F3) and `resolve_metric` fall-through (F6)

**Proposed change (not applied).** Add `pytest` and unit-test the pure functions first — `resolve_company`, `resolve_metric`, `detect_sections`, `check_is_financial_filing`, `_build_filter`, `_cap_tier` — all of which take plain inputs and need no corpus, no DB, and no network. Fixtures for synthetic table shapes (single-period, ₹-millions, interleaved statement sets) would cover most of F3, F5 and F9 without adding a PDF to the repo. Re-measure: nothing; adding tests changes no production behaviour, which is why this is the cheapest item in the report.

**Confidence.** Verified.

---

## F11 — Two divergent databases, and a maintenance tool that gives wrong advice about them

**What.** The project runs two databases with deliberately divergent migration sets. `check_migrations` assumes one, and therefore reports permanent drift and recommends an action that would damage Supabase.

**Evidence.** The split is by design, documented in `015`'s header ("This database was ingested separately from the local docker one and its doc_ids are not assumed to match") and visible in the file names — `018_deterministic_doc_ids_local.sql` vs `019_deterministic_doc_ids_supabase.sql`. Both exist on disk; each belongs to exactly one database.

`check_migrations` compares one directory against one connection. Run against Supabase (via the repo venv from the host):

```
  [ok]      019_deterministic_doc_ids_supabase.sql  -- doc_id -> uuid5(...); 8 documents, 1437 financials remapped
  [ok]      020_supabase_transcript_row.sql  -- registers ETERNAL Q4FY26 earnings transcript ...

PENDING (1) -- on disk, NOT applied:
  [pending] 018_deterministic_doc_ids_local.sql
  Apply these in the Supabase SQL editor, then add a row to
  schema_migrations recording each one.

RESULT: drift found.
```

The instruction is wrong. `018` is the local-only counterpart of `019`, which Supabase already has; applying it there would re-run a doc_id remap against a database that has already been remapped. The tool cannot be clean by construction, so `RESULT: drift found` is its permanent steady state — which trains the reader to ignore it, and that is the actual hazard.

It is also environment-fragile. In the container it cannot find the migrations directory, because `parents[2]` from `/app/scripts/` resolves to `/` while `sql/` lives at the repo root, outside the `./backend:/app` bind mount:

```
$ docker compose exec -T backend python -m scripts.check_migrations
ERROR: migrations dir not found: /sql/migrations
```

From the host with system `python3` it fails on `ModuleNotFoundError: No module named 'psycopg2'`. It works from the host via `../venv/bin/python` only. CLAUDE.md §7's "scripts run as `python -m scripts.X`" does not hold for this one, and CLAUDE.md §1 lists it as the post-migration verification step.

Separately, the `environment:` override in `docker-compose.yml:51-52` is the same hazard CLAUDE.md §6 records for Qdrant — "`QDRANT_URL` and all cloud credentials flow purely through `env_file: .env`. Never override via an `environment:` block — that exact override invalidated every local measurement for a week." The rule was learned for `QDRANT_URL` and correctly applied there (it is not in any `environment:` block). `DATABASE_URL` has exactly that override.

**Blast radius.** Low today, and I want to be precise about that: this is a **working-practice** hazard, not a data-correctness one. The spot-check in §0 showed the two databases agree on the values that migrations 015–017 corrected. But the two databases hold different document counts (11 vs 9), and nothing automated checks that they agree on anything. At N+1, "which database did that measurement come from?" becomes a live question every time a result is reported, and the tool that exists to answer it is the one that cries wolf.

**Proposed change (not applied).** Make the environment explicit in the tool: have `check_migrations` take a target (`local` / `supabase`) and filter the on-disk set by the corresponding suffix, so a clean environment reports clean. Re-measure: nothing in the application depends on it; verify only that both targets report clean afterwards.

**Confidence.** Verified.

---

## F12 — Documented-vs-actual drift

**What.** Five places where `CLAUDE.md` or a module docstring describes behaviour the code no longer has.

**Evidence.**

**(a) Citation relevance floor — CLAUDE.md §3 lists `citation relevance floor (0.05)` among measured constants not to modify. It no longer exists.** `semantic_engine.py:63-64`: `# CITATION_RELEVANCE_FLOOR REMOVED 2026-08-08. Do not reintroduce without reading this.` The removal is well-argued (the floor made a real figure untraceable rather than preventing an unsupported claim) — CLAUDE.md simply was not updated to match, and it is the file that instructs the next session.

**(b) `document_classifier` docstring contradicts its own code, and says so.** Line 16 claims the module "sets `needs_review=True` — never silently defaults to wrong `financial_type`". Lines 149-153 record that it "does neither; it defaults to CONSOLIDATED over the whole document with no signal at all. Measured 2026-08-08." The correction is in a comment 130 lines below the claim; the docstring itself still asserts the false version.

**(c) `needs_review` has no writer anywhere in the codebase.** It is declared (`models.py:155`), read (`chunker.py:387`), and never assigned:

```
needs_review:  {'False': 2531}
```

A review-flagging mechanism exists as a field, a payload key, and a docstring promise, with no producer. This is the same shape as F5.

**(d) `regression_check` is a 5-document gate, documented as 4.** CLAUDE.md §9 says "4-doc gate: ETERNAL / TITAN / PAYTM / ZOMATO". Actual:

```
DOCUMENTS entries: 5
   ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf  ETERNAL FY26 Q4 quarterly_result
   TITAN_Q1FY26_PRESS_RELEASE_AND_FINANCIAL_RESULTS.pdf  TITAN FY26 Q1 quarterly_result
   FS-Results_Q4-&-Financial-Year-ended-March-31,-2026.pdf  PAYTM FY26 annual_report
   ZOMATO_ANNUAL_REPORT_2023-24.pdf  ETERNAL FY24 annual_report
   Q4FY26-earnings-call-transcript.pdf  ETERNAL FY26 Q4 earnings_transcript
```

The transcript joined in commit `a5b5467`; CLAUDE.md was not updated in the same commit, which §9 requires.

**(e) `doc_type` form comment is stale** — `documents.py:64` documents `transcript`; the accepted value is `earnings_transcript` (F4).

Also worth flagging, though not drift: the golden datasets now hold **91** questions (55 + 20 + 15 + 1), while the stated eval baseline is 88/90. The transcript question (`q_eternal_transcript.json`, 1 question) postdates that baseline, so the baseline and the current dataset are not directly comparable.

**Blast radius.** Documentation drift in a project whose central discipline is "read the docs instead of guessing" is more costly than usual — CLAUDE.md §7 exists precisely because guessing the environment has repeatedly cost time. (a) is the most consequential: it protects a constant that is gone, so a future session may look for it, not find it, and reintroduce it — which the code explicitly warns against.

**Proposed change (not applied).** Update CLAUDE.md §3 (remove the citation floor, noting it was deliberately removed) and §9 (5-doc gate); fix the `document_classifier` docstring to match lines 149-153; either implement `needs_review` or delete the field and its payload key. Re-measure: none of these change behaviour except deleting `needs_review`, which would alter the Qdrant payload schema and require a re-index — not worth it on its own.

**Confidence.** Verified.

---

## F13 — Loose ends

Ranked last, reported briefly.

**Stray Qdrant collection `Ellipsis`** — confirmed present, 0 points, empty vector config:

```
collections: ['Ellipsis', 'ledgermind_chunks']
  Ellipsis: points=0 vectors=vectors={} ...
```

The name does not appear anywhere in the repo. It is the residue of a call that passed `collection_name=...` — Python's literal `Ellipsis` — almost certainly from an interactive session. Harmless, but it is in Qdrant Cloud, and a collection nobody created deliberately is worth removing.

**Metric `.1_203` — already resolved, no longer present.** Checked explicitly:

```
'.1_203' -> []
'%1_203%' -> []
'%203%' -> []
```

`scripts/purge_mangled_metrics.py` is tracked in git. Nothing to do; noting it so the item is closed rather than left open.

**Three payload fields are constant literals at their only construction site** — `chunker.py:375,383,386`:

```python
reporting_standard="Ind AS",   # 2531/2531 points
subsection="",                 # 2531/2531 points
table_header=None,             # 2531/2531 points
```

`reporting_standard` is an assumption (Ind AS is near-universal for listed Indian issuers, so it is *usually* true — but it is asserted, not detected, the same shape as F3). `subsection` and `table_header` are dead weight in every vector payload; `table_header` is documented in `models.py:137` as supporting multi-page table headers, a feature that is handled instead by the section-classifier continuation windows.

**Stale `__main__` defaults across five ingestion modules** — `chunker.py:624-629`, `embedder.py:215-220`, `qdrant_writer.py:283-289`, `financial_extractor.py:867-873` all default to `--company ETERNAL --ticker ETERNAL --fiscal-year FY26 --quarter Q4 --filing-date 2026-04-28`, and two default `pdf_path` to a hardcoded `~/ledgermind/docs/raw/ETERNAL_Q4FY26_...pdf`. These are smoke-test entry points, not production paths, so this is the benign category the brief distinguishes — but the defaults are silent, so invoking any of them without flags produces ETERNAL-labelled output regardless of the PDF supplied. Given that mislabelled ingestion has already happened twice (F4), defaults that supply a plausible company name are worth making required.

**`_compile_cagr` has no fiscal-year bound** — `dsl_compiler.py:246-253` selects all annual rows for the company/metric with `ORDER BY fiscal_year ASC` and no year range. As more years are ingested the same question silently yields a different CAGR. Also `ORDER BY fiscal_year` is a string sort, correct for `FY23`…`FY26` and wrong at `FY9` vs `FY10`.

**Frontend** — the `composeDocumentBody()` invariant holds: it is defined once at `frontend/app/page.tsx:68` and called once at line 440. I did not audit the frontend further; it was outside the ingestion/retrieval focus of this brief.

---

## What is genuinely solid

This section is not a courtesy. Several of the things below are the reason the findings above are *findable* — a system that hid its own failures would not have produced this much evidence.

**The diagnostic record is exceptional, and it is load-bearing.** Nearly every non-obvious constant carries the measurement that produced it, the date, and the document it was measured on. `semantic_engine.py:52-59` explains a reranker scale-confusion bug in enough detail to reconstruct it; `pipeline.py:581-591` explains why a gate that could not fail was replaced; migration `015`'s header argues from first principles why a parser correction is not a restatement and must not touch `is_latest`. Three of my findings (F7, F9, F12b) are things the codebase had **already diagnosed and written down** — I confirmed them rather than discovering them. That is a system documenting its own limits honestly, which is rarer and more valuable than a system without limits.

**The metric registry is clean and the consolidation worked.** 70 metrics, 175 aliases, **zero aliases claimed by more than one metric**, and exactly one OCR artifact among the anchor phrases. The single-registry consolidation solved the class of bug it was built for: the three-way split that caused `profit_before_tax` to be silently substituted with `pat` is gone, and `app/metrics/registry.py` is genuinely the only definition site. F6 is about the *stored data*, not the registry — the registry itself is in better shape than most of the system.

**Tenant isolation is real and verified.** RLS is not bypassed by the application role — `ledgermind_app` returns 0 rows unscoped, confirmed live at the top of this audit. `db_transaction` uses `SET LOCAL`, not `SET`, with an explicit comment about connection-pool leakage between tenants. Tenant Beta returns 0 documents and 0 financials. The Qdrant payload carries `tenant_id` on all 2531 points and it is the one filter condition that is never conditional (`retriever.py:170`).

**The SQL path is correctly parameterised and whitelisted.** Every query in `dsl_compiler` uses `%s` placeholders with tuple params — no string interpolation of user or LLM content anywhere. The metric is resolved against the registry and rejected if absent (`dsl_compiler.py:79-84`), so the LLM cannot name a table, column, or metric that does not exist. `metric_def["available"]` is *derived* (`registry.py:613`: `m.metric_type == "raw"`) rather than a hardcoded corpus-availability flag — the registry docstring's claim about not tracking data state holds up.

**"LLMs never do math" holds.** Derived totals are Python arithmetic in `_compute_derived_totals`; the DSL produces SQL only. The `DERIVED_OVERWRITE_MAX_DIVERGENCE = 0.05` guard encodes a genuinely subtle and correct distinction — a directly-read value is evidence, a derived value is inference, and above the divergence threshold the disagreement is preserved rather than papered over, because overwriting would destroy the evidence that exposes a component misread.

**The programmatic operation override is the right shape.** `dsl_compiler.py:94-105` overrides the LLM's chosen operation with a deterministically preferred one and logs the override. Deterministic guardrails wrapping a non-deterministic component, with the override recorded — this is the pattern the rest of the system should be measured against.

**The two test scripts are well-built and both pass.** Zero API calls, deterministic, and each documents why it exists rather than what it does. `test_synthesis_floor` monkeypatches `generate_text` to simulate a dual-provider outage rather than tampering with credentials, and explicitly rejects the `LLM_FORCE_UNAVAILABLE` env-flag approach on the grounds that a test-only branch in a production path gets left on. That reasoning is correct. The problem is that there are two of them (F10), not their quality.

**Deterministic document identity.** `doc_id` is `uuid5(LEDGERMIND_DOC_NS, sha256_checksum)` (`document_classifier.py:102`), so re-ingesting the same PDF is idempotent and the consolidated/standalone pair derives from one checksum by design — visible in the data as two documents sharing `8c53a092`. This is the property that makes re-ingestion safe, and it is why the local database could be rebuilt post-parser-fix without the corrections Supabase needed.

**Failure attribution in the router is deliberate.** The `FALLBACK_ERROR` route reason (`router.py:159`) exists specifically so an error-masked-as-semantic route is distinguishable in the audit trail — the comment notes this defect class "cost two sessions of investigation". F2 criticises what the system *does* with that state, not its bookkeeping, which is correct.

---

## Method and limits

**What I ran:** read-only SQL against both databases (every container-side query printed its `SET LOCAL app.tenant_id` scoping); Qdrant `scroll` and `get_collection`; `grep`/`sed` over the tree; Python introspection of the registry and of `regression_check.DOCUMENTS` via `ast`; both test scripts; `check_migrations`.

**What I did not run, and why:** no eval sweep, no LLM or Cohere call, no ingest, no migration, no `--apply`/`--execute` of anything, no git write. I started the `purge_orphaned_metrics` dry run — permitted, and CLAUDE.md §9 recommends it — and **killed it deliberately** on realising it re-parses every reference PDF (`scripts/purge_orphaned_metrics.py:96`), which CLAUDE.md §7 warns can exhaust WSL RAM and restart the distro. The direct DB evidence in F6 is stronger than what that script would have printed.

**Confidence conventions:** "Verified" means I ran a command and pasted its real output, or read the exact lines cited. Where a finding's *severity* is reasoned forward to a hypothetical company N+1 — F1's data-corruption path, F2's cross-company retrieval, F3's unit mismatch — the **mechanism** is verified from code and the **consequence** is inference, and I have said so in each case rather than blurring the two.

**One self-correction, already noted in §0:** I predicted a data divergence between the two databases and the check disproved it. Reported rather than reasoned past, per the brief. The clustering heuristic in F6 also over-merges in at least one cluster, which I flagged inline rather than letting the headline count stand unqualified.

**Not covered:** the frontend beyond the one invariant check; `prompt_shield` internals; `audit_writer` and audit-trail completeness; `cross_engine` beyond its subsidiary map; auth and RBAC beyond confirming RLS is not bypassed; Celery worker and scheduler behaviour.
