"""
F2 step 1 measurement -- does company_mentioned fire on non-issuers?

Calls _classify_query directly: no retrieval, no rerank, no synthesis.
One LLM call per golden question (91), not the ~110+ of a full sweep.

The decision this produces: WOULD_REFUSE must be 0 before step 2 wires
company_mentioned into company_unresolved. A false refusal on a question
that passes today is worse than the F2 bug it closes.
"""
import argparse, collections, json, sys, time
from pathlib import Path

sys.path.insert(0, "/app")
from app.engines.router import _classify_query, _KNOWN_TICKERS, RouterResponse

# CONTROL-CONDITION TOLERANCE. This probe is run at revisions where
# `company_mentioned` does not exist on RouterResponse (b8048dd and earlier) to
# establish a before-state. Absent is an EXPECTED condition, not a crash -- and
# it is NOT the same observation as present-and-null. `_classify_query` returns
# a plain dict, so .get() yields None for both, which is the same conflation F2
# was about one level up. The schema is the only thing that separates them.
_MENTIONED_IN_SCHEMA = "company_mentioned" in RouterResponse.model_fields

# The step-2 predicate is likewise absent before 04643cb. IMPORTED, never
# reimplemented: the stale `mentioned and got is None` copy this probe used to
# carry is exactly what drifted from the shipped condition and reported
# WOULD_REFUSE=1 on Q051 while the shipped code let Q051 through.
try:
    from app.engines.router import _resolve_mentioned_issuers
    _HAVE_RESOLVER = True
except ImportError:
    _resolve_mentioned_issuers = None
    _HAVE_RESOLVER = False

FIELD_ABSENT = "FIELD_ABSENT"
MENTIONED_NULL = "NULL"
MENTIONED_VALUE = "VALUE"

# route_reason is the field that explains WHY a route was chosen. It was on
# _classify_query's return dict and dropped at row-build time, so the two-arm
# probe of 2026-08-21 recorded WHICH routes moved (TQ008, ETQ001) and could not
# say why -- 182 calls that answered half the question.
#
# Three states, same shape as mentioned_state and for the same reason. An
# ABSENT key and an EMPTY string are different facts: router.py builds the
# success path with `result.get("route_reason", "")`, so a model that omits
# the field yields "" at a revision where the key exists. Collapsing that into
# "missing" would repeat the null-overloading defect F2 was about.
ROUTE_REASON_ABSENT = "FIELD_ABSENT"
ROUTE_REASON_EMPTY = "EMPTY"
ROUTE_REASON_VALUE = "VALUE"

_MISSING = object()


def _route_reason(result):
    """
    Return (state, value) for route_reason on a _classify_query result dict.

    Sentinel, not `.get(key, "")`: a default of "" is exactly the conflation
    this function exists to prevent.
    """
    raw = result.get("route_reason", _MISSING)
    if raw is _MISSING:
        return ROUTE_REASON_ABSENT, None
    if raw is None or raw == "":
        return ROUTE_REASON_EMPTY, raw
    return ROUTE_REASON_VALUE, raw


def _mentioned_state(raw):
    """Three states, because two lose the control condition."""
    if not _MENTIONED_IN_SCHEMA:
        return FIELD_ABSENT
    return MENTIONED_VALUE if raw else MENTIONED_NULL


def _would_refuse(mentioned):
    """
    Ask the SHIPPED predicate. router.py:174 refuses on
    `company_mentioned and not _res` -- nothing RESOLVES -- not on
    `company is None`. Q051 ("Eternal or Paytm") nulls `company` and resolves
    BOTH issuers, so it must not be flagged.

    None (not False) where no step-2 predicate exists: "not computable here"
    and "computed, came out false" are different facts about a run.
    """
    if not _HAVE_RESOLVER:
        return None
    resolved, _unresolved = _resolve_mentioned_issuers(mentioned)
    return bool(mentioned) and not resolved


def _gate_counters(rows):
    """
    PROVIDER GATE, same rule as eval_runner. None keys are excluded rather than
    counted: the FALLBACK_ERROR path legitimately has no provider, exactly as
    reranker_backend is legitimately absent on refusal paths.
    """
    provs = collections.Counter(r["llm_provider"] for r in rows)
    models = collections.Counter(r["llm_model"] for r in rows)
    clean = len([k for k in provs if k]) <= 1 and len([k for k in models if k]) <= 1
    return provs, models, clean


def _write_rows_only(path, rows):
    """
    CRASH-SAFE PARTIAL WRITE -- deliberate, not a bug. The gate dicts are
    computed after this point, so a run that dies mid-summary still leaves its
    91 classifications on disk. Retained and asserted by _self_test.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(path, "w"), indent=2)


def _build_meta(rows, provs, models, clean):
    return {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "questions_run": len(rows),
        "datasets": DATASETS,
        "providers": dict(provs),
        "models_served": dict(models),
        "single_provider_and_model": clean,
        "path_mismatch_withheld": not clean,
    }


def _write_full(path, rows, provs, models, clean):
    """
    Second write, replacing the rows-only dump. A file carrying rows without
    the provider/model counts that decide whether its aggregates are readable
    is the same defect as an eval_results JSON read without its header check.
    """
    json.dump({"meta": _build_meta(rows, provs, models, clean), "rows": rows},
              open(path, "w"), indent=2)


def _synthetic_row(rid, provider, model, **kw):
    row = {
        "dataset": "synthetic.json", "id": rid, "question": "synthetic " + rid,
        "expected_company": None, "company": None,
        "company_mentioned": None, "mentioned_state": MENTIONED_NULL,
        "route_reason": "synthetic reason", "route_reason_state": ROUTE_REASON_VALUE,
        "company_unresolved": None, "path": "semantic",
        "expected_path": "semantic", "path_mismatch": False,
        "llm_provider": provider, "llm_model": model,
        "would_refuse": False, "company_mismatch": False, "company_null": True,
    }
    row.update(kw)
    return row


def _self_test():
    """
    Exercises the {meta,rows} writer end to end on synthetic rows: build, write,
    read back, assert. ZERO LLM calls, zero network, zero dataset reads.

    WHY THIS EXISTS. The {meta,rows} block compiled from the day it was written
    and no run had ever written through it -- the same shape as a guard that has
    never fired. Every assertion below is therefore paired with a NEGATIVE
    CONTROL that must fail: a check never observed failing is not evidence that
    it can fail. Returns 0 on success, 1 on failure.
    """
    import tempfile
    failures = []
    counts = {"assert": 0, "neg": 0}

    def expect(label, actual, want):
        if actual != want:
            raise AssertionError("%s: got %r, want %r" % (label, actual, want))

    def must_fail(label, fn):
        counts["neg"] += 1
        try:
            fn()
        except AssertionError:
            print("    neg-control OK    %s failed as required" % label)
            return
        failures.append("NEGATIVE CONTROL DID NOT FAIL: " + label)
        print("    neg-control FAIL  %s passed when it must not" % label)

    def check(label, fn):
        counts["assert"] += 1
        try:
            fn()
            print("    assert OK         %s" % label)
        except AssertionError as e:
            failures.append("%s -- %s" % (label, e))
            print("    assert FAIL       %s -- %s" % (label, e))

    tmpdir = tempfile.mkdtemp(prefix="router_probe_selftest_")
    out = str(Path(tmpdir) / "probe.json")

    # ── CASE 1: single provider + model, one FALLBACK_ERROR row (provider None)
    print("  CASE 1  single provider, one fallback row (None must be excluded)")
    rows = [
        _synthetic_row("S001", "gemini", "gemini-3.1-flash-lite"),
        _synthetic_row("S002", "gemini", "gemini-3.1-flash-lite"),
        _synthetic_row("S003", None, None),
    ]
    provs, models, clean = _gate_counters(rows)

    _write_rows_only(out, rows)
    partial = json.load(open(out))
    check("rows-only partial write is a LIST, no meta key",
          lambda: expect("partial type", isinstance(partial, list), True))
    check("rows-only partial write carries every row",
          lambda: expect("partial len", len(partial), 3))
    must_fail("rows-only partial is a dict",
              lambda: expect("partial type", isinstance(partial, dict), True))

    _write_full(out, rows, provs, models, clean)
    got = json.load(open(out))
    meta = got["meta"]
    check("full write replaced partial with {meta,rows}",
          lambda: expect("top-level keys", sorted(got.keys()), ["meta", "rows"]))
    check("meta.providers", lambda: expect("providers", meta["providers"],
                                           {"gemini": 2, "None": 1} if "None" in meta["providers"]
                                           else {"gemini": 2, "null": 1}))
    check("meta.models_served counts gemini twice",
          lambda: expect("models gemini", meta["models_served"].get("gemini-3.1-flash-lite"), 2))
    check("meta.single_provider_and_model is True (None excluded)",
          lambda: expect("clean", meta["single_provider_and_model"], True))
    check("meta.path_mismatch_withheld is False when clean",
          lambda: expect("withheld", meta["path_mismatch_withheld"], False))
    check("meta.questions_run", lambda: expect("questions_run", meta["questions_run"], 3))
    check("rows survived the round trip", lambda: expect("rows len", len(got["rows"]), 3))

    print("  CASE 1  negative controls")
    must_fail("single_provider_and_model == False",
              lambda: expect("clean", meta["single_provider_and_model"], False))
    must_fail("path_mismatch_withheld == True",
              lambda: expect("withheld", meta["path_mismatch_withheld"], True))
    must_fail("models_served gemini == 99",
              lambda: expect("models", meta["models_served"].get("gemini-3.1-flash-lite"), 99))
    must_fail("questions_run == 0",
              lambda: expect("questions_run", meta["questions_run"], 0))

    # ── CASE 2: MIXED providers -- the gate must withhold
    print("  CASE 2  mixed providers, gate must withhold")
    rows2 = [
        _synthetic_row("S101", "gemini", "gemini-3.1-flash-lite"),
        _synthetic_row("S102", "groq", "openai/gpt-oss-120b"),
    ]
    provs2, models2, clean2 = _gate_counters(rows2)
    _write_full(out, rows2, provs2, models2, clean2)
    meta2 = json.load(open(out))["meta"]
    check("mixed: single_provider_and_model is False",
          lambda: expect("clean", meta2["single_provider_and_model"], False))
    check("mixed: path_mismatch_withheld is True",
          lambda: expect("withheld", meta2["path_mismatch_withheld"], True))
    check("mixed: both providers recorded",
          lambda: expect("providers", sorted(meta2["providers"].keys()), ["gemini", "groq"]))

    print("  CASE 2  negative controls")
    must_fail("mixed single_provider_and_model == True",
              lambda: expect("clean", meta2["single_provider_and_model"], True))
    must_fail("mixed path_mismatch_withheld == False",
              lambda: expect("withheld", meta2["path_mismatch_withheld"], False))
    must_fail("mixed providers == gemini only",
              lambda: expect("providers", sorted(meta2["providers"].keys()), ["gemini"]))

    # ── CASE 3: the three mentioned states are distinct values
    print("  CASE 3  three-state mentioned_state")
    check("FIELD_ABSENT, NULL and VALUE are distinct",
          lambda: expect("distinct", len({FIELD_ABSENT, MENTIONED_NULL, MENTIONED_VALUE}), 3))
    must_fail("the three states collapse to one",
              lambda: expect("distinct", len({FIELD_ABSENT, MENTIONED_NULL, MENTIONED_VALUE}), 1))

    # ── CASE 4: route_reason -- three states, and the round trip
    print("  CASE 4  route_reason three-state + round trip")
    check("ABSENT, EMPTY and VALUE are distinct",
          lambda: expect("distinct",
                         len({ROUTE_REASON_ABSENT, ROUTE_REASON_EMPTY, ROUTE_REASON_VALUE}), 3))
    # The discriminator that matters: key-absent vs present-but-empty. A
    # `.get(key, "")` default would make these two identical.
    check("missing key -> ABSENT, value None",
          lambda: expect("absent", _route_reason({}), (ROUTE_REASON_ABSENT, None)))
    check("empty string -> EMPTY, not ABSENT",
          lambda: expect("empty", _route_reason({"route_reason": ""}), (ROUTE_REASON_EMPTY, "")))
    check("None -> EMPTY, not ABSENT",
          lambda: expect("none", _route_reason({"route_reason": None}), (ROUTE_REASON_EMPTY, None)))
    check("prose -> VALUE, verbatim",
          lambda: expect("value", _route_reason({"route_reason": "because X"}),
                         (ROUTE_REASON_VALUE, "because X")))
    check("FALLBACK_ERROR prose -> VALUE, verbatim",
          lambda: expect("fallback",
                         _route_reason({"route_reason": "FALLBACK_ERROR: classification failed on all providers"}),
                         (ROUTE_REASON_VALUE, "FALLBACK_ERROR: classification failed on all providers")))

    rows4 = [
        _synthetic_row("S201", "gemini", "gemini-3.1-flash-lite",
                       route_reason="quantitative: names a metric and a period",
                       route_reason_state=ROUTE_REASON_VALUE),
        _synthetic_row("S202", "gemini", "gemini-3.1-flash-lite",
                       route_reason="", route_reason_state=ROUTE_REASON_EMPTY),
        _synthetic_row("S203", "gemini", "gemini-3.1-flash-lite",
                       route_reason=None, route_reason_state=ROUTE_REASON_ABSENT),
    ]
    provs4, models4, clean4 = _gate_counters(rows4)
    _write_full(out, rows4, provs4, models4, clean4)
    back = json.load(open(out))["rows"]
    check("route_reason survives the JSON round trip verbatim",
          lambda: expect("rr", back[0]["route_reason"],
                         "quantitative: names a metric and a period"))
    check("EMPTY row round-trips as '' not null",
          lambda: expect("rr empty", back[1]["route_reason"], ""))
    check("ABSENT row round-trips with state preserved",
          lambda: expect("rr absent state", back[2]["route_reason_state"], ROUTE_REASON_ABSENT))
    check("all three states distinguishable after round trip",
          lambda: expect("states",
                         [r["route_reason_state"] for r in back],
                         [ROUTE_REASON_VALUE, ROUTE_REASON_EMPTY, ROUTE_REASON_ABSENT]))

    print("  CASE 4  negative controls")
    must_fail("missing key claimed to be EMPTY",
              lambda: expect("absent", _route_reason({}), (ROUTE_REASON_EMPTY, None)))
    must_fail("empty string claimed to be ABSENT",
              lambda: expect("empty", _route_reason({"route_reason": ""}), (ROUTE_REASON_ABSENT, "")))
    must_fail("prose claimed to be EMPTY",
              lambda: expect("value", _route_reason({"route_reason": "because X"}),
                             (ROUTE_REASON_EMPTY, "because X")))
    must_fail("round-tripped route_reason claimed to be something else",
              lambda: expect("rr", back[0]["route_reason"], "a different reason"))
    must_fail("EMPTY row claimed to round-trip as None",
              lambda: expect("rr empty", back[1]["route_reason"], None))
    must_fail("the three route_reason states collapse to one",
              lambda: expect("distinct",
                             len({ROUTE_REASON_ABSENT, ROUTE_REASON_EMPTY, ROUTE_REASON_VALUE}), 1))

    # The crash-safe partial write, re-asserted with the new keys present.
    _write_rows_only(out, rows4)
    partial4 = json.load(open(out))
    check("rows-only partial write still fires and is a bare LIST",
          lambda: expect("partial type", isinstance(partial4, list), True))
    check("rows-only partial carries route_reason",
          lambda: expect("partial rr", partial4[0]["route_reason"],
                         "quantitative: names a metric and a period"))
    must_fail("rows-only partial claimed to carry a meta key",
              lambda: expect("partial type", isinstance(partial4, dict), True))

    print("=" * 60)
    if failures:
        print("SELF-TEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  " + f)
        return 1
    print("SELF-TEST PASSED -- all assertions held, all negative controls failed")
    print("  assertions: %d   negative controls: %d"
          % (counts["assert"], counts["neg"]))
    print("scratch: " + tmpdir)
    return 0

DATASETS = ["q4fy26_eternal.json", "q_titan.json", "q_paytm.json",
            "q_eternal_transcript.json"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden-dir", default="/app/golden_dataset")
    ap.add_argument("--delay", type=float, default=20.0)
    ap.add_argument("--out", default="/app/eval_results/router_probe.json")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--self-test", action="store_true",
                    help="Exercise the {meta,rows} writer and its gate on synthetic "
                         "rows. ZERO LLM calls, zero network. Exits before any "
                         "dataset is read.")
    args = ap.parse_args()

    # Placed BEFORE the dataset load and the classify loop, deliberately:
    # a self-test that could spend quota is not a self-test.
    if args.self_test:
        sys.exit(_self_test())

    records = []
    for name in DATASETS:
        p = Path(args.golden_dir) / name
        for rec in json.load(open(p)):
            records.append((name, rec))
    if args.limit:
        records = records[: args.limit]
    print(f"{len(records)} questions across {len(DATASETS)} datasets\n", flush=True)

    rows = []
    for i, (ds, rec) in enumerate(records, 1):
        r = _classify_query(rec["question"])
        exp = rec.get("expected_company")
        exp_path = rec.get("expected_path")
        got = r["company"]
        mentioned = r.get("company_mentioned")
        # Attribution, sourced exactly as eval_runner sources it. _classify_query
        # carries the whole LLMResult out (router.py:130-132) precisely so the
        # caller can record provider AND model in one write; record_llm_call
        # (state.py:250) reads .provider/.model off it to populate the
        # llm_provider/llm_model the API returns and eval_runner reads back.
        # This probe bypasses the API, so it reads the same two attributes at
        # the source and keeps eval_runner's field names.
        #
        # None on both means the FALLBACK_ERROR path -- both providers failed
        # and the row's classification is the hardcoded fallback, not a model's
        # answer. Without this the two are indistinguishable in the output, and
        # a WOULD_REFUSE count computed over silently-unclassified rows is not
        # the measurement this probe exists to make.
        llm = r.get("llm_result")
        rr_state, rr_value = _route_reason(r)
        row = {
            "dataset": ds, "id": rec["id"], "question": rec["question"],
            "expected_company": exp, "company": got,
            "company_mentioned": mentioned,
            "company_unresolved": r.get("company_unresolved"),
            "path": r["path"],
            "expected_path": exp_path,
            # The classifier's own verdict, NOT the graph's. eval_runner sees
            # the path the graph TOOK; Stage 0c and the refusal routing both
            # sit downstream of classification, so these can legitimately
            # differ. A mismatch here is evidence about _classify_query, not
            # proof the graph disagrees with golden.
            "path_mismatch": bool(exp_path and r["path"] != exp_path),
            "llm_provider": getattr(llm, "provider", None),
            "llm_model": getattr(llm, "model", None),
            # What step 2 ACTUALLY does, asked of the shipped predicate
            # rather than restated here. None = no such predicate at this
            # revision. See _would_refuse.
            "would_refuse": _would_refuse(mentioned),
            "mentioned_state": _mentioned_state(mentioned),
            # WHY the route was chosen, not just which. See _route_reason.
            "route_reason": rr_value,
            "route_reason_state": rr_state,
            "company_mismatch": bool(exp and got != exp),
            "company_null": got is None,
        }
        rows.append(row)
        flag = "REFUSE" if row["would_refuse"] else ("MISMATCH" if row["company_mismatch"] else "")
        if row["path_mismatch"]:
            flag = (flag + " PATH").strip()
        print(f"[{i:3}/{len(records)}] {rec['id']:10} exp={str(exp):8} "
              f"got={str(got):8} mentioned={str(mentioned):22} {flag}", flush=True)
        if i < len(records):
            time.sleep(args.delay)

    _write_rows_only(args.out, rows)

    would = [r for r in rows if r["would_refuse"]]
    mism = [r for r in rows if r["company_mismatch"]]
    nulls = [r for r in rows if r["company_null"]]
    print("\n" + "=" * 60)
    print(f"WOULD_REFUSE      {len(would):3}   <- MUST BE 0 to proceed to step 2")
    print(f"company mismatch  {len(mism):3}   (of {sum(1 for r in rows if r['expected_company'])} with expected_company)")
    print(f"company null      {len(nulls):3}")
    # PROVIDER GATE, same rule as eval_runner. Groq classifies differently
    # from Gemini -- TQ006 routes quantitative on one and semantic on the
    # other, confirmed from audit_log -- so a path-mismatch count computed
    # across a mixed run says nothing about the classifier. The number is
    # WITHHELD rather than annotated: a figure printed under a caveat still
    # ends up quoted without it.
    provs, models, clean = _gate_counters(rows)
    print(f"Providers         {dict(provs)}")
    print(f"Models served     {dict(models)}")
    pmis = [r for r in rows if r["path_mismatch"]]
    if not clean:
        print("path mismatch     WITHHELD -- mixed providers/models above")
    else:
        print(f"path mismatch     {len(pmis):3}   (of {sum(1 for r in rows if r['expected_path'])} with expected_path)")
    for r in pmis:
        print(f"  {r['id']:10} expected={r['expected_path']:14} got={r['path']:14} "
              f"[{r['llm_provider']}] {r['question'][:44]}")
    print(f"mentioned set     {sum(1 for r in rows if r['company_mentioned']):3} / {len(rows)}")
    ms = collections.Counter(r["mentioned_state"] for r in rows)
    print(f"  mentioned_state  FIELD_ABSENT={ms[FIELD_ABSENT]:3} "
          f"NULL={ms[MENTIONED_NULL]:3} VALUE={ms[MENTIONED_VALUE]:3}")
    if not _HAVE_RESOLVER:
        print("  would_refuse     NOT COMPUTABLE -- no step-2 predicate at this revision")
    for r in would + mism:
        print(f"  {r['id']:10} {r['question'][:70]}")
        print(f"             got={r['company']!r} mentioned={r['company_mentioned']!r}")
    # Second write, replacing the crash-safe rows-only dump above. The gate
    # dicts are computed after that write, and a file carrying rows without
    # the provider/model counts that decide whether its aggregates are
    # readable is the same defect as an eval_results JSON read without its
    # header check. Same values as the printed gate -- read, not recomputed.
    _write_full(args.out, rows, provs, models, clean)
    print("=" * 60)
    print(f"written: {args.out}")


if __name__ == "__main__":
    main()
