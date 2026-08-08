"""
READ-ONLY diagnostic: find every page in the corpus where one data-row label
repeats, i.e. every page that can lose rows to first-wins in seen_keys.

WHY. TITAN's four segment sub-tables share one set of segment names, and the
losing three are discarded by page order. The question this answers is whether
that shape is TITAN-specific -- which would justify a TITAN-shaped fix -- or
corpus-wide, which means the mechanism carrying sub-table identity has to be
general. Answer, measured 2026-08-08: corpus-wide. See
docs/IMPLEMENTATION_DELTAS.md, "TITAN segment sub-tables".

METHOD. One pdfplumber.open per document, page caches flushed as we go (an
annual report at 371 pages will otherwise sit in RAM). For each page: take every
line carrying >= 2 numeric tokens, treat its leading run of non-numeric tokens as
the label, and report labels occurring more than once on that page. That is
exactly the condition under which two DISTINCT source rows collide on
(financial_type, fiscal_year, quarter, metric) and one is silently dropped.
Each repeated label is passed through the REAL resolve_metric so the canonical
they collide on is visible, and so resolver defects surface in the same pass --
that is how `Total` -> `revenue` on a declared tie was found.

DELIBERATELY TEXT-BASED, NOT the positional path. Running detect_column_layout +
extract_financials_positional across 371 ZOMATO pages is minutes of work to
answer a yes/no question. This is a cheap detector, so it is VALIDATED against a
known-true case rather than trusted: TITAN p8 and p15, whose true repeated-label
sets are established by _titan_segment_probe.py, are flagged correctly with
exactly the segment names.

KNOWN OVER-REPORTING, and why the ZOMATO number is not 101 defects. This reads
raw page text, so it counts pages the extractor never processes: only blocks
classified into a detected consolidated/standalone section reach
_rows_to_records. ZOMATO's count is dominated by ESOP and note tables
('Outstanding at April', 'Exercised during the year') that are not financial
statement pages at all. How many survive section classification is NOT measured
here and would need a pipeline run. Read the per-page detail, not the tally.

Run: docker compose exec -T backend python -m scripts._repeated_label_scan
Runtime: ~4 min, dominated by ZOMATO.
"""
import re
from collections import Counter

import pdfplumber

from app.ingestion.entity_resolver import resolve_metric

DOCS = [
    ("TITAN  ", "/app/docs/raw/TITAN_Q1FY26_PRESS_RELEASE_AND_FINANCIAL_RESULTS.pdf"),
    ("ETERNAL", "/app/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"),
    ("PAYTM  ", "/app/docs/raw/FS-Results_Q4-&-Financial-Year-ended-March-31,-2026.pdf"),
    ("ZOMATO ", "/app/docs/raw/ZOMATO_ANNUAL_REPORT_2023-24.pdf"),
]

NUM = re.compile(r"^\(?-?[\d,]+\.?\d*\)?$|^-$")


def label_of(line):
    toks = line.split()
    if len([t for t in toks if NUM.match(t)]) < 2:
        return None
    lab = []
    for t in toks:
        if NUM.match(t):
            break
        lab.append(t)
    return " ".join(lab).strip() or None


def main():
    for name, path in DOCS:
        print("=" * 78)
        print(f"{name}  {path.split('/')[-1]}")
        print("=" * 78)
        try:
            with pdfplumber.open(path) as pdf:
                npages = len(pdf.pages)
                print(f"pages: {npages}")
                hits = 0
                for i in range(npages):
                    page = pdf.pages[i]
                    text = page.extract_text() or ""
                    page.flush_cache()
                    labels = [l for l in (label_of(ln) for ln in text.split("\n")) if l]
                    if not labels:
                        continue
                    dupes = {k: v for k, v in Counter(labels).items() if v > 1}
                    if not dupes:
                        continue
                    hits += 1
                    print(f"\n  --- page {i+1}: {len(dupes)} repeated data-row label(s) ---")
                    head = text.split("\n")[:3]
                    print(f"      head: {' | '.join(h[:60] for h in head)}")
                    for lab, c in sorted(dupes.items(), key=lambda x: -x[1]):
                        print(f"      x{c}  {lab!r:<44} -> {resolve_metric(lab)}")
                print(f"\n  pages with repeated data-row labels: {hits}/{npages}")
        except Exception as e:
            print(f"  RAISED {type(e).__name__}: {e}")
        print()


if __name__ == "__main__":
    main()
