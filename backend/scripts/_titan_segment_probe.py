"""
READ-ONLY diagnostic: show everything the parser sees on TITAN's two segment
pages, and name the exact guard that consumes each sub-table's section header.

WHY. TITAN p8 (standalone) and p15 (consolidated) each print FOUR sub-tables --
revenue, result, assets, liabilities -- that share one set of segment names. All
four resolve to the segment_revenue_* family, so three of four lose their slot in
_rows_to_records' seen_keys dict by page order. Splitting them needs the
sub-table's identity to reach resolve_metric, and resolve_metric(raw: str) takes
only the row label. This locates where that identity stops existing.

METHOD. ONE parse_pdf and ONE pdfplumber.open for the whole run, reused across
both pages (parsing a corpus PDF twice exhausts WSL RAM). For each page it prints:

  [A] raw page.extract_text() lines in document order -- EVERYTHING, not just
      rows that became records, so the section headers are visible in place
  [B] pdfplumber find_tables() -- how many table objects the page really has
  [C] detect_column_layout() output
  [D] what extract_financials_positional() actually returns
  [E] a SHADOW pass: every physical row, tagged with the guard that consumed it
  [F] validation of [E] against [D]

The shadow pass in [E] re-walks extract_financials_positional's own decision
points using the REAL helpers it imports (_is_numeric_word, _apply_typo_map,
MIN_VALUE_COLUMNS) and the same adaptive tolerance. It exists because the guards
`continue` silently and there is otherwise no way to ask a dropped row why it
died. It is NOT trusted on its own -- [F] asserts its emitted list equals the
real function's return value, so a divergence between shadow and reality is
reported rather than believed. Measured 2026-08-08: 25/25 on p8, 26/26 on p15.

WHAT IT ESTABLISHED (2026-08-08). Section headers are ordinary physical rows
carrying zero numeric words, dropped at pdf_parser.py:503 by
`len(non_empty) < MIN_VALUE_COLUMNS`. They never become rows and never reach
_rows_to_records, so nothing downstream skips them -- they are gone before the
row list is built. See docs/IMPLEMENTATION_DELTAS.md, "TITAN segment sub-tables".

Run: docker compose exec -T backend python -m scripts._titan_segment_probe
"""
import pdfplumber

from app.ingestion.pdf_parser import (
    parse_pdf, extract_financials_positional, _is_numeric_word,
    _apply_typo_map, MIN_VALUE_COLUMNS, NOT_PRINTED,
)
from app.ingestion.financial_extractor import detect_column_layout

PDF = "/app/docs/raw/TITAN_Q1FY26_PRESS_RELEASE_AND_FINANCIAL_RESULTS.pdf"
PAGES = [8, 15]  # 1-indexed

# Mirrors pdf_parser.extract_financials_positional's local constant. Kept as a
# literal rather than imported because it is function-local there; if it ever
# moves to module scope, import it instead of redeclaring.
FRAGMENT_ADJACENCY_GAP = 8.0


def group_rows(words):
    """Exactly extract_financials_positional's grouping: 3.0pt top tolerance."""
    rows = {}
    for w in words:
        top = w["top"]
        for row_top in rows:
            if abs(top - row_top) <= 3.0:
                rows[row_top].append(w)
                break
        else:
            rows[top] = [w]
    return rows


def main():
    print("=" * 78)
    print("parse_pdf() -- ONE full parse, reused for both pages")
    print("=" * 78)
    blocks = parse_pdf(PDF)
    print(f"total PageBlocks: {len(blocks)}  (one per page)")

    with pdfplumber.open(PDF) as pdf:
        for pn in PAGES:
            page = pdf.pages[pn - 1]
            blk = blocks[pn - 1]

            print()
            print("#" * 78)
            print(f"# PAGE {pn}   (page_index {pn - 1})")
            print("#" * 78)

            print("\n--- PageBlock ---")
            print(f"page_number    : {blk.page_number}")
            print(f"block_type     : {blk.block_type}")
            print(f"is_continuation: {blk.is_continuation}")
            print(f"table attr     : {blk.table!r}")
            print(f"content chars  : {len(blk.content)}")

            print("\n--- [A] RAW TEXT, document order (page.extract_text lines) ---")
            for i, line in enumerate((page.extract_text() or "").split("\n")):
                print(f"  L{i:03d} | {line}")

            print("\n--- [B] pdfplumber find_tables() ---")
            tables = page.find_tables()
            print(f"  table objects found: {len(tables)}")
            for ti, t in enumerate(tables):
                print(f"   t{ti}: bbox={tuple(round(v, 1) for v in t.bbox)} rows={len(t.rows)}")

            print("\n--- [C] detect_column_layout() ---")
            try:
                column_map, centers = detect_column_layout(PDF, pn - 1)
            except Exception as e:
                column_map, centers = None, None
                print(f"  RAISED {type(e).__name__}: {e}")
            print(f"  column_map: {column_map}")
            print(f"  centers   : {centers}")

            if column_map is None or centers is None:
                print("  -> no layout; positional path not taken for this page")
                continue

            print("\n--- [D] extract_financials_positional() RETURNED rows ---")
            real_rows = extract_financials_positional(PDF, pn - 1, centers)
            print(f"  rows returned: {len(real_rows)}")
            for r in real_rows:
                vals = ["NOT_PRINTED" if v is NOT_PRINTED else v for v in r[1:]]
                print(f"    {r[0]!r:<62} {vals}")

            if len(centers) > 1:
                gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
                tolerance = 0.95 * (min(gaps) / 2)
            else:
                tolerance = 25.0

            print("\n--- [E] SHADOW: every physical row + the guard that consumed it ---")
            print(f"  (tolerance={tolerance:.2f})")
            words = page.extract_words()
            rows = group_rows(words)
            parsing_started = False
            emitted = []
            for row_top in sorted(rows):
                row_words = sorted(rows[row_top], key=lambda w: w["x0"])
                raw_text = " ".join(w["text"] for w in row_words)
                low = raw_text.lower()

                if not parsing_started:
                    if "revenue" in low or "income" in low or "sale" in low:
                        parsing_started = True
                        note = "STARTS parsing (pdf_parser.py:434)"
                    else:
                        print(f"  top={row_top:7.2f} DROP@436 pre-start        | {raw_text}")
                        continue
                else:
                    note = ""

                desc_words, numeric_words = [], []
                for w in row_words:
                    cleaned = w["text"].strip().strip("()")
                    if _is_numeric_word(cleaned) or cleaned == "-":
                        numeric_words.append(w)
                    else:
                        desc_words.append(w["text"])

                clusters, cur = [], []
                for w in numeric_words:
                    if not cur:
                        cur = [w]
                        continue
                    if w["x0"] - cur[-1]["x1"] <= FRAGMENT_ADJACENCY_GAP:
                        cur.append(w)
                    else:
                        clusters.append(cur)
                        cur = [w]
                if cur:
                    clusters.append(cur)

                cands = []
                for cl in clusters:
                    cx1 = max(w["x1"] for w in cl)
                    for ci, c in enumerate(centers):
                        if abs(cx1 - c) <= tolerance:
                            cands.append((abs(cx1 - c), cl, ci))
                cands.sort(key=lambda x: x[0])
                buckets = [[] for _ in centers]
                claimed, assigned = set(), set()
                for dist, cl, ci in cands:
                    if ci in claimed or id(cl) in assigned:
                        continue
                    buckets[ci].extend(cl)
                    claimed.add(ci)
                    assigned.add(id(cl))
                for cl in clusters:
                    if id(cl) not in assigned:
                        desc_words.extend(w["text"] for w in cl)

                non_empty = [b for b in buckets if b]
                if len(non_empty) < MIN_VALUE_COLUMNS:
                    print(f"  top={row_top:7.2f} DROP@503 buckets={len(non_empty)}<2  | {raw_text}   {note}")
                    continue
                description = _apply_typo_map(" ".join(desc_words).strip())
                if not description:
                    print(f"  top={row_top:7.2f} DROP@507 empty desc      | {raw_text}   {note}")
                    continue
                print(f"  top={row_top:7.2f} EMIT@575 buckets={len(non_empty)}      | {description!r}   {note}")
                emitted.append(description)

            print("\n--- [F] SHADOW VALIDATION ---")
            real_desc = [r[0] for r in real_rows]
            print(f"  real emitted   : {len(real_desc)}")
            print(f"  shadow emitted : {len(emitted)}")
            print(f"  MATCH: {real_desc == emitted}")
            if real_desc != emitted:
                print(f"  real  : {real_desc}")
                print(f"  shadow: {emitted}")


if __name__ == "__main__":
    main()
