"""
READ-ONLY diagnostic: dump what each reference statement PRINTS for its tax
breakdown, alongside what the extractor SEES for the same page.

Answers one question and no other: for the period groups where
`adjustment_of_tax_relating_to_earlier_years` is absent, is the line genuinely
not printed, or is it printed and missed?

Two independent views per FINANCIAL_STATEMENT page:
  RAW   -- pdfplumber page text lines containing 'tax'. What is on the paper.
  ROWS  -- extract_financials_positional() output, the exact rows the
           extractor's _rows_to_records() is handed. What the code sees.

A line in RAW but not in ROWS is a positional-extraction miss. A line in ROWS
that produces no record is an alias/resolution miss. A line in neither is not
printed.

ONE document per invocation, by index, so each PDF is parsed exactly once per
process and peak RSS stays bounded (see CLAUDE.md §7: parsing twice restarts
the distro).

Usage: python -m scripts._tax_line_dump <doc_index>
"""

import logging
import sys

import pdfplumber

from app.ingestion.pdf_parser import parse_pdf, extract_financials_positional, extract_financials
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.financial_extractor import detect_column_layout
from app.ingestion.models import BlockType
from app.ingestion.entity_resolver import normalize_metric_label

from scripts.regression_check import DOCUMENTS, RAW_DIR


def main():
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    idx = int(sys.argv[1])
    doc = DOCUMENTS[idx]
    pdf_path = str(RAW_DIR / doc["filename"])

    print("=" * 78)
    print(f"DOC[{idx}] {doc['filename']}")
    print(f"  company={doc['company']}  fy={doc['fiscal_year']}  quarter={doc['quarter']}")
    print("=" * 78, flush=True)

    blocks = parse_pdf(pdf_path)
    sections = detect_sections(blocks)
    blocks = classify_blocks(blocks, sections)

    for s in sections:
        print(f"  section: {s.financial_type}  pages {s.page_start}-{s.page_end}")

    fs_blocks = get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)
    pages = []
    seen = set()
    for b in fs_blocks:
        if b.page_number in seen:
            continue
        seen.add(b.page_number)
        pages.append((b.page_number, getattr(b, "financial_type", "UNKNOWN")))
    print(f"  FS pages: {[p for p, _ in pages]}\n", flush=True)

    # Single open for the raw-text view of every FS page.
    raw_text = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, _ft in pages:
            try:
                raw_text[page_number] = pdf.pages[page_number - 1].extract_text() or ""
            except Exception as e:
                raw_text[page_number] = f"<<extract_text failed: {e}>>"

    for page_number, ftype in pages:
        page_idx = page_number - 1
        hits_raw = [ln for ln in raw_text[page_number].splitlines()
                    if "tax" in ln.lower()]
        try:
            column_map, column_centers = detect_column_layout(pdf_path, page_idx)
        except Exception as e:
            column_map, column_centers = None, None
            print(f"  page {page_number}: detect_column_layout raised {e}")

        rows = []
        if column_map is not None:
            if column_centers is not None:
                rows = extract_financials_positional(pdf_path, page_idx, column_centers)
            else:
                rows = extract_financials(pdf_path, page_idx)
        hits_rows = [r for r in rows if r and "tax" in str(r[0]).lower()]

        if not hits_raw and not hits_rows:
            continue

        print("-" * 78)
        print(f"PAGE {page_number}  ({ftype})  column_map={column_map}")
        print("-" * 78)

        print("  RAW printed lines containing 'tax':")
        if not hits_raw:
            print("    <none>")
        for ln in hits_raw:
            print(f"    | {ln}")

        print("  ROWS the extractor sees (label -> values), containing 'tax':")
        if not hits_rows:
            print("    <none>")
        for r in hits_rows:
            label = str(r[0])
            print(f"    | {label!r}")
            print(f"        normalised: {normalize_metric_label(label)!r}")
            print(f"        values: {r[1:]}")
        print(flush=True)


if __name__ == "__main__":
    main()
