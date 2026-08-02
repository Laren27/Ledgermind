"""
Regression Check — verifies section_classifier.py and financial_extractor.py
produce sane output across all reference documents in the corpus.

Run this after ANY change to classification keywords, anchor phrases, or
column-map detection logic — before touching chunker/embedder/qdrant_writer/
pipeline, and before any Qdrant purge + re-ingestion.

Checks two independent layers:
  1. Block-type distribution — did a classifier change help one document
     while silently breaking another?
  2. Extracted financial records — does the column-map/extraction chain
     produce plausible numbers, not just "some numbers"?

Read-only. No DB writes, no Qdrant calls.
"""

import logging
import os
import sys
from collections import Counter
from pathlib import Path

from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.financial_extractor import extract_all_financial_records
from app.ingestion.models import BlockType

def _resolve_raw_dir() -> Path:
    """
    Source-PDF directory, resolved for whichever environment this runs in.

    Path.home() is /root inside the container but /home/<user> on the host,
    so a home-relative constant silently resolved to a nonexistent path
    under `docker compose exec` — every document SKIPPED, which the summary
    then reported as a failure (correctly, but for the wrong reason).
    """
    env = os.getenv("LEDGERMIND_RAW_DIR")
    if env:
        return Path(env)
    container = Path("/app/docs/raw")
    if container.is_dir():
        return container
    return Path.home() / "ledgermind/docs/raw"


RAW_DIR = _resolve_raw_dir()

# One entry per reference document. min_fs / max_fs bound the expected
# FINANCIAL_STATEMENT page count — catches both under- and over-classification
# regressions in a single assertion, rather than eyeballing Counters by hand.
DOCUMENTS = [
    {
        "filename": "ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf",
        "company": "ETERNAL", "ticker": "ETERNAL", "fiscal_year": "FY26",
        "quarter": "Q4", "doc_type": "quarterly_result",
        "filing_date": "2026-04-28",
        "min_fs_pages": 5, "max_fs_pages": 30,
        "expect_revenue_min": 10000, "expect_revenue_max": 70000,  # covers standalone (10899) + consolidated (54364)
    },
    {
        "filename": "TITAN_Q1FY26_PRESS_RELEASE_AND_FINANCIAL_RESULTS.pdf",
        "company": "TITAN", "ticker": "TITAN", "fiscal_year": "FY26",
        "quarter": "Q1", "doc_type": "quarterly_result",
        # 2025-08-07, not 2025-07-31. Confirmed against the PDF cover page.
        # The wrong value originated from an argparse DEFAULT silently applying
        # when --filing-date was omitted on Titan's first ingest, which also
        # mislabelled the quarter as Q4 in Postgres until it was re-ingested.
        # This script is read-only so the constant broke nothing, but a
        # reference file is exactly where a wrong constant gets trusted later.
        "filing_date": "2025-08-07",
        "min_fs_pages": 5, "max_fs_pages": 25,
        "expect_revenue_min": 1000, "expect_revenue_max": 20000,  # already covers both (13040, 14814)
    },
    {
        # PAYTM added 2026-08-02. Until then this gate covered 3 of the 4
        # corpus PDFs, and purge_orphaned_metrics.py correctly refused to
        # evaluate PAYTM's 395 is_latest rows because no document in this list
        # produced their business keys — they were reported under NOT
        # EVALUATED, never deleted. Adding it here extends BOTH.
        #
        # UNLIKE the other three, this ONE pdf yields five period/type
        # combinations (FY26 annual + Q4 + Q3, FY25 annual + Q4, each in both
        # financial types). quarter=None and doc_type=annual_report reflect the
        # document's primary character: 128 annual rows against 49 for Q4.
        # Labelling it Q4 would repeat the mistake Titan's comment below
        # records.
        #
        # The revenue band is deliberately wide because the five combinations
        # span 1005 (FY26 Q4 standalone) to 8437 (FY26 annual consolidated).
        # This catches a magnitude error or a total extraction failure and
        # little else — weaker coverage than the other three entries, recorded
        # as such rather than dressed up as equivalent.
        "filename": "FS-Results_Q4-&-Financial-Year-ended-March-31,-2026.pdf",
        "company": "PAYTM", "ticker": "PAYTM", "fiscal_year": "FY26",
        "quarter": None, "doc_type": "annual_report",
        "filing_date": "2026-05-06",
        # Measured 2026-08-02: 6 FS pages, [7,8,9,16,17,18] — two symmetric
        # three-page blocks (consolidated, then standalone), the same shape as
        # ETERNAL's Q4FY26 filing. Bounds are tighter than the other entries
        # because a real change here (a missing statement, a classifier
        # regression) moves the count by a whole block of 3, not by 1.
        "min_fs_pages": 4, "max_fs_pages": 10,
        "expect_revenue_min": 1000, "expect_revenue_max": 10000,
    },
    {
        "filename": "ZOMATO_ANNUAL_REPORT_2023-24.pdf",
        "company": "ETERNAL", "ticker": "ETERNAL", "fiscal_year": "FY24",
        "quarter": None, "doc_type": "annual_report",
        "filing_date": "2024-08-31",
        "min_fs_pages": 10, "max_fs_pages": 20,
        "expect_revenue_min": 6000, "expect_revenue_max": 16000,  # covers standalone (6622) + consolidated (12114)
    },
]

ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"


class _ExtractorCapture(logging.Handler):
    """
    Collects three kinds of extractor WARNING for one document:
    _compute_derived_totals' overwrite messages (printed, NOT asserted),
    validate_financial_identities' [IDENTITY FAIL] lines (ASSERTED), and its
    [IDENTITY NOT EVALUATED] lines (printed, NOT asserted).

    These are NOT asserted on. A count-pinned gate would fail on
    improvements as readily as regressions — this session shrank one
    divergence from 2212 Cr to 11 Cr, which a pinned count would have
    reported as a failure. A gate that goes red on good news gets
    switched off.

    The real failure mode was that these warnings scrolled past in a
    400-line log. Surfacing them as a labelled block after the PASS/FAIL
    lines makes a changed derivation chain visible without inventing a
    new way for the build to break.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []
        self.identity_failures: list[str] = []
        # Third bucket, added 2026-08-03 alongside the tax composition check.
        # An identity whose components are not all present is neither a pass
        # nor a failure, and collapsing it into either one loses the only
        # information it carries: WHICH component the extractor did not
        # produce for WHICH period. Reported, never asserted -- see the print
        # block in run_one().
        self.not_evaluated: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        if "disagrees with computed" in msg:
            self.messages.append(msg)
        elif "[IDENTITY FAIL]" in msg:
            self.identity_failures.append(msg.strip())
        elif "[IDENTITY NOT EVALUATED]" in msg:
            self.not_evaluated.append(msg.strip())


def run_one(doc: dict) -> bool:
    pdf_path = RAW_DIR / doc["filename"]
    if not pdf_path.exists():
        print(f"  [SKIP] File not found: {pdf_path}")
        return False

    print(f"\n{'='*70}")
    print(f"{doc['filename']}  ({doc['company']}/{doc['fiscal_year']}/{doc['quarter']})")
    print(f"{'='*70}")

    blocks = parse_pdf(str(pdf_path))
    sections = detect_sections(blocks)
    blocks = classify_blocks(blocks, sections)

    # --- Layer 1: block-type distribution ---
    counts = Counter(b.block_type for b in blocks)
    print(f"Block counts: {dict(counts)}")

    fs_pages = [b.page_number for b in get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)]
    fs_count = len(fs_pages)
    fs_ok = doc["min_fs_pages"] <= fs_count <= doc["max_fs_pages"]
    print(f"  FINANCIAL_STATEMENT pages ({fs_count}): {fs_pages[:10]}"
          f"{' ...' if fs_count > 10 else ''}")
    print(f"  [{'PASS' if fs_ok else 'FAIL'}] expected {doc['min_fs_pages']}-{doc['max_fs_pages']} pages")

    md_count = counts.get(BlockType.MANAGEMENT_DISCUSSION, 0)
    risk_count = counts.get(BlockType.RISK_DISCLOSURE, 0)
    print(f"  MANAGEMENT_DISCUSSION={md_count} | RISK_DISCLOSURE={risk_count}")

    # --- Layer 2: extracted record sanity ---
    # --- Layer 2: extracted record sanity ---
    doc_id_map = {s.financial_type: f"diagnostic-{s.financial_type}" for s in sections}
    _cap = _ExtractorCapture()
    _ext_log = logging.getLogger("app.ingestion.financial_extractor")
    _ext_log.addHandler(_cap)
    try:
        records = extract_all_financial_records(
            blocks=blocks, pdf_path=str(pdf_path), tenant_id=ALPHA_TENANT,
            company=doc["company"], ticker=doc["ticker"],
            filing_date=doc["filing_date"], doc_id_map=doc_id_map,
        )
    finally:
        _ext_log.removeHandler(_cap)
    print(f"\n  Records extracted: {len(records)}")

    # Golden comparison always targets the ANNUAL figure (quarter=None),
    # since that's the only value verified against known ground truth for
    # every document in this corpus — quarterly filings additionally report
    # a cumulative annual column (SEBI col3/col4), and annual reports are
    # annual-only by definition. Checking the raw quarter-scoped figure
    # here previously produced false failures on correctly extracted data.
    # Golden comparison targets Annual (quarter=None) for Q4/Annual reports, 
    # but looks at the specific quarter for Q1-Q3 filings.
    if doc["doc_type"] == "annual_report" or doc["quarter"] == "Q4":
        target_quarter = None
        label = "annual"
    else:
        target_quarter = doc["quarter"]
        label = f"quarterly ({doc['quarter']})"

    revenue_records = [
        r for r in records
        if r.metric == "revenue" and r.fiscal_year == doc["fiscal_year"]
        and r.quarter == target_quarter
    ]
    revenue_ok = False
    if revenue_records:
        for r in revenue_records:
            in_range = doc["expect_revenue_min"] <= r.value <= doc["expect_revenue_max"]
            revenue_ok = revenue_ok or in_range
            print(f"    revenue ({label}) | {r.financial_type:13s} | {r.value:>10.1f} cr "
                  f"{'✓' if in_range else '✗ OUT OF RANGE'}")
    print(f"  [{'PASS' if revenue_ok else 'FAIL'}] annual revenue in expected range "
          f"({doc['expect_revenue_min']}-{doc['expect_revenue_max']} cr)")

    print(f"\n  Derivation overwrites: {len(_cap.messages)}")
    for m in _cap.messages:
        print(f"    {m}")

    # ASSERTED, unlike derivation overwrites. A failing identity means the
    # extracted numbers contradict each other (e.g. pat != pbt - tax), which
    # is unambiguously wrong -- there is no "improvement" that raises this
    # count, so pinning it cannot go red on good news.
    #
    # These were INVISIBLE before 2026-08-02. This handler is attached around
    # the extraction call, which satisfies Python's handler search, so
    # logging.lastResort never fired and every WARNING the extractor emitted
    # during extraction -- including [IDENTITY FAIL] -- was discarded. Three
    # real PAYTM PAT failures sat behind a green 4/4 gate as a result.
    identity_ok = not _cap.identity_failures
    print(f"\n  Identity failures: {len(_cap.identity_failures)}")
    for m in _cap.identity_failures:
        print(f"    {m}")
    print(f"  [{'PASS' if identity_ok else 'FAIL'}] all financial identities hold")

    # NOT ASSERTED, and deliberately absent from `overall` below. These are
    # identities whose components the extractor did not all produce for a
    # given period. That is a coverage gap, not a contradiction: the numbers
    # present do not disagree with each other, there are simply not enough of
    # them to check. Failing the gate on it would make an extraction gap
    # indistinguishable from a wrong number, and pinning the count would go
    # red the day extraction IMPROVES and a component starts being produced.
    #
    # Printed after the PASS/FAIL lines for the same reason the derivation
    # overwrites are: the failure mode being addressed is a real signal
    # scrolling past unread in a 400-line log.
    print(f"\n  Identities NOT EVALUATED (missing components): {len(_cap.not_evaluated)}")
    for m in _cap.not_evaluated:
        print(f"    {m}")

    records_ok = len(records) > 0
    overall = fs_ok and records_ok and revenue_ok and identity_ok
    print(f"\n  OVERALL: {'✅ PASS' if overall else '❌ FAIL'}")
    return overall


def main():
    print("LedgerMind — Classifier/Extractor Regression Check")
    results = {}
    for doc in DOCUMENTS:
        results[doc["filename"]] = run_one(doc)

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    all_pass = True
    for name, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {name}")
        all_pass = all_pass and ok

    if not all_pass:
        print("\n⚠️  Do not proceed to Qdrant purge / re-ingestion until all documents pass.")
        sys.exit(1)
    print("\nAll documents pass. Safe to proceed to next step.")


if __name__ == "__main__":
    main()