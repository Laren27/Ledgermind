"""
READ-ONLY diagnostic: find every row dropped because ALL of its values are zero,
and measure how many records that costs across the corpus.

WHY. ZOMATO AR 2023-24 p.285 standalone prints "Deferred tax - -". The positional
row view reads that as [0.0, 0.0] -- the dashes are nil markers and
clean_financial_number maps "-" to 0.0 -- yet `deferred_tax` is absent from
`financials` for ETERNAL FY24 and FY23 standalone. This locates where it stops
existing.

METHOD. One parse_pdf per process. For every FINANCIAL_STATEMENT page the
extractor would process, this replays exactly what _rows_to_records() does to
each row (same description strip, same values truncation against column_map)
and then calls the REAL _should_skip_row(). No reimplementation of the predicate.

Deciding WHICH clause fired, without duplicating the clause list: a row is
attributed to the all-zero clause when _should_skip_row(desc, values) is True
but _should_skip_row(desc, [1.0]*len(values)) is False. Only two clauses in that
predicate read `values` at all -- the >10M magnitude guard and the all-zero
guard -- and a row of zeros cannot trip the magnitude guard, so a verdict that
flips when the values are replaced by ones is attributable to the all-zero
clause alone. Every label-driven clause is held constant by construction.

DELIBERATELY NOT description-matched. _recovered_value_dump.py's LAYER B pairs
old and new rows by description string and takes the first match, which produces
spurious diffs on pages carrying several identically-labelled rows (TITAN p14
has three "-Non-controlling interesi-"). Nothing here pairs rows at all: each
row is judged on its own, in the same pass that produced it.

NET vs GROSS. A dropped row is only a real loss if its business key is not
produced elsewhere -- the same metric often appears on another page of the same
filing. Both numbers are reported: candidate records the dropped rows would have
emitted (gross), and those whose (company, fy, quarter, financial_type, metric)
is absent from the actual extractor output (net).

Usage: python -m scripts._zero_row_loss_scan <doc_index>
"""

import logging
import sys
from collections import defaultdict

from app.ingestion.pdf_parser import parse_pdf, extract_financials_positional, extract_financials
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.financial_extractor import (
    detect_column_layout, extract_all_financial_records, _should_skip_row,
)
from app.ingestion.entity_resolver import resolve_metric
from app.ingestion.models import BlockType

from scripts.regression_check import DOCUMENTS, RAW_DIR, ALPHA_TENANT


def main():
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    idx = int(sys.argv[1])
    doc = DOCUMENTS[idx]
    pdf_path = str(RAW_DIR / doc["filename"])
    company = doc["company"]

    print("=" * 78)
    print(f"DOC[{idx}] {doc['filename']}   company={company}")
    print("=" * 78, flush=True)

    blocks = parse_pdf(pdf_path)                     # THE one parse
    sections = detect_sections(blocks)
    blocks = classify_blocks(blocks, sections)
    doc_id_map = {s.financial_type: f"diagnostic-{s.financial_type}" for s in sections}

    produced = extract_all_financial_records(
        blocks=blocks, pdf_path=pdf_path, tenant_id=ALPHA_TENANT,
        company=company, ticker=doc["ticker"],
        filing_date=doc["filing_date"], doc_id_map=doc_id_map,
    )
    produced_keys = {
        (r.company, r.fiscal_year, r.quarter, r.financial_type, r.metric) for r in produced
    }
    print(f"records the extractor actually produces: {len(produced)}\n", flush=True)

    fs_blocks = get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)

    gross = 0            # candidate records the dropped rows would have emitted
    net_missing = []     # those whose business key is produced nowhere else
    dropped_rows = 0
    by_metric = defaultdict(int)
    processed_pages = set()

    for block in fs_blocks:
        page_number = block.page_number
        page_idx = page_number - 1
        if page_idx in processed_pages:
            continue
        processed_pages.add(page_idx)
        financial_type = getattr(block, "financial_type", "UNKNOWN")

        try:
            column_map, centers = detect_column_layout(pdf_path, page_idx)
        except Exception:
            continue
        if column_map is None:
            continue
        rows = (extract_financials_positional(pdf_path, page_idx, centers)
                if centers is not None else extract_financials(pdf_path, page_idx))
        if not rows:
            continue

        for row in rows:
            if not row or len(row) < 2:
                continue
            description = str(row[0]).strip()
            values = row[1:]
            if len(values) > len(column_map):        # same truncation as _rows_to_records
                values = values[-len(column_map):]

            if not _should_skip_row(description, values):
                continue
            # Would the SAME label survive with non-zero values? If yes, the
            # all-zero clause is what dropped this row.
            if _should_skip_row(description, [1.0] * len(values)):
                continue

            dropped_rows += 1
            metric = resolve_metric(description)
            by_metric[metric] += 1

            cand = []
            for col_idx, (fy, q) in enumerate(column_map):
                if col_idx >= len(values):
                    break
                if values[col_idx] is None:
                    continue
                cand.append((col_idx, fy, q, values[col_idx]))
            gross += len(cand)

            missing = [c for c in cand
                       if (company, c[1], c[2], financial_type, metric) not in produced_keys]

            print(f"  page {page_number} ({financial_type})  {description!r}")
            print(f"    resolve_metric -> {metric!r}   values={values}")
            for col_idx, fy, q, v in cand:
                key = (company, fy, q, financial_type, metric)
                mark = "LOST" if key not in produced_keys else "present from another row/page"
                print(f"      col{col_idx} {str(fy):<6} {str(q):<5} = {v:<8} -> {mark}")
            net_missing.extend(
                (company, c[1], c[2], financial_type, metric, c[3]) for c in missing
            )
            print(flush=True)

    print("-" * 78)
    print(f"rows dropped by the all-zero clause : {dropped_rows}")
    print(f"candidate records (GROSS)           : {gross}")
    print(f"records lost outright (NET)         : {len(net_missing)}")
    print("-" * 78)
    if by_metric:
        print("by resolved metric (rows):")
        for m, n in sorted(by_metric.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {n:>3}  {m}")
    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()
