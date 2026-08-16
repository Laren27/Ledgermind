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
from app.engines.router import _classify_query, _KNOWN_TICKERS

DATASETS = ["q4fy26_eternal.json", "q_titan.json", "q_paytm.json",
            "q_eternal_transcript.json"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden-dir", default="/app/golden_dataset")
    ap.add_argument("--delay", type=float, default=20.0)
    ap.add_argument("--out", default="/app/eval_results/router_probe.json")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    args = ap.parse_args()

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
            # what step 2 would do if it were wired
            "would_refuse": bool(mentioned and got is None),
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

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(args.out, "w"), indent=2)

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
    provs = collections.Counter(r["llm_provider"] for r in rows)
    models = collections.Counter(r["llm_model"] for r in rows)
    print(f"Providers         {dict(provs)}")
    print(f"Models served     {dict(models)}")
    pmis = [r for r in rows if r["path_mismatch"]]
    clean = len([k for k in provs if k]) <= 1 and len([k for k in models if k]) <= 1
    if not clean:
        print("path mismatch     WITHHELD -- mixed providers/models above")
    else:
        print(f"path mismatch     {len(pmis):3}   (of {sum(1 for r in rows if r['expected_path'])} with expected_path)")
    for r in pmis:
        print(f"  {r['id']:10} expected={r['expected_path']:14} got={r['path']:14} "
              f"[{r['llm_provider']}] {r['question'][:44]}")
    print(f"mentioned set     {sum(1 for r in rows if r['company_mentioned']):3} / {len(rows)}")
    for r in would + mism:
        print(f"  {r['id']:10} {r['question'][:70]}")
        print(f"             got={r['company']!r} mentioned={r['company_mentioned']!r}")
    print("=" * 60)
    print(f"written: {args.out}")


if __name__ == "__main__":
    main()
