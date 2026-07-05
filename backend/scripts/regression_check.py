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

import os
import sys
from collections import Counter
from pathlib import Path

from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.financial_extractor import extract_all_financial_records
from app.ingestion.models import BlockType

RAW_DIR = Path.home() / "ledgermind/docs/raw"

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
        "filing_date": "2025-07-31",
        "min_fs_pages": 5, "max_fs_pages": 25,
        "expect_revenue_min": 1000, "expect_revenue_max": 20000,  # already covers both (13040, 14814)
    },
    {
        "filename": "ZOMATO_ANNUAL_REPORT_2023-24.pdf",
        "company": "ETERNAL", "ticker": "ETERNAL", "fiscal_year": "FY24",
        "quarter": None, "doc_type": "annual_report",
        "filing_date": "2024-08-31",
        "min_fs_pages": 2, "max_fs_pages": 12,
        "expect_revenue_min": 6000, "expect_revenue_max": 16000,  # covers standalone (6622) + consolidated (12114)
    },
]

ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"


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
    records = extract_all_financial_records(
        blocks=blocks, pdf_path=str(pdf_path), tenant_id=ALPHA_TENANT,
        company=doc["company"], ticker=doc["ticker"],
        filing_date=doc["filing_date"], doc_id_map=doc_id_map,
    )
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
            print(f"    revenue (annual) | {r.financial_type:13s} | {r.value:>10.1f} cr "
                  f"{'✓' if in_range else '✗ OUT OF RANGE'}")
    print(f"  [{'PASS' if revenue_ok else 'FAIL'}] annual revenue in expected range "
          f"({doc['expect_revenue_min']}-{doc['expect_revenue_max']} cr)")

    records_ok = len(records) > 0
    overall = fs_ok and records_ok and revenue_ok
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