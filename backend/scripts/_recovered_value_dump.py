"""
READ-ONLY diagnostic: enumerate every record the (I) -> (1) fix recovered, with
its source page and the printed line it came from.

Adapted from _tax_line_dump.py (same parse-once-per-process discipline, same
two-view RAW/ROWS idea), pointed at a different question: not "is the line
printed" but "which values did the fix turn from None into a number, and what
was printed where each one came from".

METHOD. ONE parse_pdf per process. The old and new conversions are then
compared against that SAME parse by monkeypatching pdf_parser._ocr_one_to_digit
back to its pre-fix behaviour ("1" if t == "I" else t, which never saw through
a wrapper) and running the extractor twice. Nothing is re-parsed between the
two runs.

TWO LAYERS, because they answer different halves of the question:

  LAYER A -- record diff, via extract_all_financial_records(). Authoritative
    for "which records are new", because it is the only view that accounts for
    _should_skip_row (a row whose values were ALL None/zero is dropped
    entirely, so a single recovered token can resurrect a whole row and add
    several records at once), for seen_keys first-wins dedup, and for
    _compute_derived_totals (a recovered component can change a DERIVED value,
    which is not a printed figure and must be labelled as such).

  LAYER B -- token diff, via extract_financials_positional(). Supplies
    provenance: page, financial_type, row description, column (fiscal_year,
    quarter), old value, new value, plus the verbatim printed line the row was
    read from. This is the evidence that a recovery is a real printed figure
    rather than a token that was previously and correctly discarded.

Usage: python -m scripts._recovered_value_dump <doc_index>
"""

import logging
import re
import sys

import pdfplumber

from app.ingestion import pdf_parser
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.financial_extractor import (
    detect_column_layout, extract_all_financial_records,
)
from app.ingestion.entity_resolver import resolve_metric
from app.ingestion.models import BlockType

from scripts.regression_check import DOCUMENTS, RAW_DIR, ALPHA_TENANT

_NEW_FN = pdf_parser._ocr_one_to_digit


def _old_ocr_one_to_digit(token: str) -> str:
    """The pre-fix call-site expression, verbatim: wrapper-blind."""
    return "1" if token == "I" else token


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _find_printed_line(page_lines: list[str], description: str) -> str:
    """Best-effort match of a parsed row description back to its printed line."""
    key = _norm(description)[:24]
    if not key:
        return "<no description>"
    for ln in page_lines:
        if key and key in _norm(ln):
            return ln
    # Fall back to a looser prefix so a partially-OCR-damaged label still cites.
    key = key[:12]
    for ln in page_lines:
        if key and key in _norm(ln):
            return ln
    return "<no matching printed line found>"


def _record_keys(records) -> dict:
    out = {}
    for r in records:
        out[(r.company, r.fiscal_year, r.quarter, r.financial_type, r.metric)] = r.value
    return out


def main():
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    idx = int(sys.argv[1])
    doc = DOCUMENTS[idx]
    pdf_path = str(RAW_DIR / doc["filename"])

    print("=" * 78)
    print(f"DOC[{idx}] {doc['filename']}   company={doc['company']}")
    print("=" * 78, flush=True)

    blocks = parse_pdf(pdf_path)                      # THE one parse
    sections = detect_sections(blocks)
    blocks = classify_blocks(blocks, sections)
    doc_id_map = {s.financial_type: f"diagnostic-{s.financial_type}" for s in sections}

    kwargs = dict(
        blocks=blocks, pdf_path=pdf_path, tenant_id=ALPHA_TENANT,
        company=doc["company"], ticker=doc["ticker"],
        filing_date=doc["filing_date"], doc_id_map=doc_id_map,
    )

    # ---- LAYER A: record diff -------------------------------------------
    pdf_parser._ocr_one_to_digit = _old_ocr_one_to_digit
    try:
        old_recs = _record_keys(extract_all_financial_records(**kwargs))
    finally:
        pdf_parser._ocr_one_to_digit = _NEW_FN
    new_recs = _record_keys(extract_all_financial_records(**kwargs))

    gained = {k: v for k, v in new_recs.items() if k not in old_recs}
    lost = {k: v for k, v in old_recs.items() if k not in new_recs}
    changed = {k: (old_recs[k], new_recs[k]) for k in old_recs
               if k in new_recs and old_recs[k] != new_recs[k]}

    print(f"\nrecords old={len(old_recs)}  new={len(new_recs)}  "
          f"gained={len(gained)}  lost={len(lost)}  value-changed={len(changed)}\n")

    print("LAYER A — RECORDS GAINED")
    print(f"{'company':<8} {'fy':<6} {'q':<5} {'type':<13} {'value':>11}  metric")
    print("-" * 78)
    for k in sorted(gained, key=lambda x: (x[0], str(x[1]), str(x[2]), x[3], x[4])):
        co, fy, q, ft, metric = k
        print(f"{co:<8} {str(fy):<6} {str(q):<5} {ft:<13} {gained[k]:>11.2f}  {metric}")

    if lost:
        print("\nLAYER A — RECORDS LOST (must be empty; the fix only adds)")
        for k in sorted(lost, key=str):
            print(f"  {k} = {lost[k]}")

    if changed:
        print("\nLAYER A — VALUES CHANGED IN PLACE (derived totals downstream)")
        for k in sorted(changed, key=str):
            o, n = changed[k]
            print(f"  {k}: {o} -> {n}")

    # ---- LAYER B: token provenance --------------------------------------
    fs_blocks = get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)
    pages, seen = [], set()
    for b in fs_blocks:
        if b.page_number not in seen:
            seen.add(b.page_number)
            pages.append((b.page_number, getattr(b, "financial_type", "UNKNOWN")))

    page_text = {}
    with pdfplumber.open(pdf_path) as pdf:
        for pn, _ft in pages:
            page_text[pn] = (pdf.pages[pn - 1].extract_text() or "").splitlines()

    print("\n\nLAYER B — TOKEN-LEVEL RECOVERIES, WITH THE PRINTED LINE")
    print("=" * 78, flush=True)

    for page_number, ftype in pages:
        page_idx = page_number - 1
        try:
            column_map, centers = detect_column_layout(pdf_path, page_idx)
        except Exception as e:
            print(f"  page {page_number}: detect_column_layout raised {e}")
            continue
        if column_map is None or centers is None:
            continue

        pdf_parser._ocr_one_to_digit = _old_ocr_one_to_digit
        try:
            rows_old = pdf_parser.extract_financials_positional(pdf_path, page_idx, centers)
        finally:
            pdf_parser._ocr_one_to_digit = _NEW_FN
        rows_new = pdf_parser.extract_financials_positional(pdf_path, page_idx, centers)

        by_desc_old = {}
        for r in rows_old:
            by_desc_old.setdefault(str(r[0]), []).append(r)

        for rn in rows_new:
            desc = str(rn[0])
            candidates = by_desc_old.get(desc)
            ro = candidates[0] if candidates else None
            vals_new, vals_old = rn[1:], (ro[1:] if ro else [None] * len(rn[1:]))

            diffs = []
            for ci in range(len(vals_new)):
                o = vals_old[ci] if ci < len(vals_old) else None
                n = vals_new[ci]
                if o is None and n is not None:
                    diffs.append((ci, o, n))
            if not diffs:
                continue

            printed = _find_printed_line(page_text.get(page_number, []), desc)
            print(f"\n  page {page_number} ({ftype})  row: {desc!r}")
            print(f"    resolve_metric -> {resolve_metric(desc)!r}")
            print(f"    PRINTED: | {printed}")
            if ro is None:
                print("    NOTE: row absent from the OLD output entirely "
                      "(all-None/zero rows are dropped by _should_skip_row)")
            for ci, o, n in diffs:
                fy, q = column_map[ci] if ci < len(column_map) else ("?", "?")
                print(f"    col{ci} {str(fy):<6} {str(q):<5}  {o} -> {n}")

    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()
