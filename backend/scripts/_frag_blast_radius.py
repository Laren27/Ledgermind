r"""
READ-ONLY diagnostic: full blast radius of a pdf_parser extraction change.

(Raw docstring: the vendoring command below contains a shell line-continuation
backslash, which a normal string would silently eat, changing what is
documented into something subtly different from what was verified.)

Compares, cell by cell, what extract_financials_positional() returned BEFORE a
change against what it returns AFTER, across every FINANCIAL_STATEMENT page of
a reference document. Written for the fragment-joining fix (dba2af8) and kept
because this is the THIRD OCR defect of that family; the next one will want it
too.

The two parsers are run against the SAME page in the same pass, so the two
views differ only by the change under test.

ONE parse_pdf per process, one document per invocation, per CLAUDE.md §7.


REQUIRED: THE PRE-EDIT PARSER, VENDORED FROM GIT
------------------------------------------------
This script imports `_pdf_parser_old`, which is NOT in the repository. It is a
generated artifact you must produce before each use, because "before" means a
different commit every time.

It cannot be produced from inside the container: docker-compose bind-mounts
only ./backend to /app, so .git is not visible there. Generate it on the HOST,
into backend/, where the bind mount then exposes it to the container as
/app/_pdf_parser_old.py — which is on sys.path for `python -m scripts.X`.

The one relative import must be rewritten, or the module raises
"ImportError: attempted relative import with no known parent package".

    # from the repo root, on the host. <REF> is the commit BEFORE the change --
    # for a change committed as C, that is C^; for an uncommitted working-tree
    # change, that is HEAD.
    git show <REF>:backend/app/ingestion/pdf_parser.py \
      | python3 -c "
    import sys
    src = sys.stdin.read()
    old = 'from .models import BlockType, PageBlock'
    new = 'from app.ingestion.models import BlockType, PageBlock'
    n = src.count(old)
    assert n == 1, f'ABORT: found {n}'
    sys.stdout.write(src.replace(old, new))
    " > backend/_pdf_parser_old.py

Then confirm you vendored what you meant to — a stale copy silently reports
"0 cells changed", which reads exactly like a clean result:

    grep -c "<a marker string from the change>" backend/_pdf_parser_old.py   # expect 0

DELETE backend/_pdf_parser_old.py when finished. It is a vendored copy of a
module that also exists at its real path; leaving it behind invites both an
accidental commit and a later run against the wrong "before".


WHY THE SENTINEL NEEDS SPECIAL HANDLING
----------------------------------------
pdf_parser._NotPrinted defines no __eq__, so it compares by IDENTITY. Its
docstring says so deliberately: it must never be equal to 0.0, to None, or to
itself by value, and every in-tree consumer tests it with `is` against the
single module-level NOT_PRINTED instance.

That is correct everywhere in the application, and it breaks the moment TWO
copies of the module are loaded side by side — which is this script's entire
premise. `old.NOT_PRINTED` and `new.NOT_PRINTED` are two distinct objects, so:

    old.NOT_PRINTED == new.NOT_PRINTED   ->   False

The obvious cell test, `if vo == vn: continue`, therefore reports EVERY column
that printed nothing as a changed cell. Measured on ETERNAL before this was
fixed: 16 cells reported changed, of which 15 were NOT_PRINTED -> NOT_PRINTED.
The instrument overstated its own blast radius by 94% and buried the one real
change among the noise. An instrument used to justify shipping a parser change
must not do that.

_cell_key() closes the gap for cross-module comparison WITHOUT touching
_NotPrinted itself: giving it a value-based __eq__ would weaken the identity
contract the application depends on, to serve a diagnostic. Each side is mapped
through its OWN module's NOT_PRINTED, so the two sentinels collapse onto one
shared key object while every other distinction is preserved. In particular
NOT_PRINTED -> None stays a REAL change: it means a token appeared where none
had been, and failed to parse — the exact shape that hid the (I) defect.

Usage: python -m scripts._frag_blast_radius <doc_index>
       doc_index indexes regression_check.DOCUMENTS (0=ETERNAL 1=TITAN
       2=PAYTM 3=ZOMATO at time of writing; the script prints what it picked).
"""

import logging
import sys

try:
    import _pdf_parser_old as old
except ImportError as exc:
    sys.exit(
        f"ABORT: cannot import _pdf_parser_old ({exc}).\n"
        "This script needs the PRE-EDIT parser vendored from git into backend/.\n"
        "See the module docstring for the exact command — it must be run on the\n"
        "HOST (.git is not bind-mounted into the container) and it must rewrite\n"
        "the `from .models import` relative import."
    )

from app.ingestion import pdf_parser as new
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.financial_extractor import detect_column_layout
from app.ingestion.models import BlockType

from scripts.regression_check import DOCUMENTS, RAW_DIR


# One shared object, returned for the sentinel by BOTH sides, so the two
# distinct _NotPrinted instances compare equal to each other and to nothing
# else. Tagged tuples elsewhere so that no float or None can collide with it.
_NOT_PRINTED_KEY = ("<NOT_PRINTED>",)


def _cell_key(value, module):
    """Comparable form of one extracted cell, resolved against ITS OWN module.

    `module` must be the parser that produced `value` — passing the wrong one
    reintroduces exactly the identity mismatch this exists to close.
    """
    if value is module.NOT_PRINTED:
        return _NOT_PRINTED_KEY
    return ("value", value)


def main():
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    idx = int(sys.argv[1])
    doc = DOCUMENTS[idx]
    pdf_path = str(RAW_DIR / doc["filename"])

    print("=" * 96)
    print(f"DOC[{idx}] {doc['filename']}   company={doc['company']}")
    print(f"OLD parser: {old.__file__}")
    print(f"NEW parser: {new.__file__}")
    print("=" * 96, flush=True)

    blocks = parse_pdf(pdf_path)                      # THE one parse
    sections = detect_sections(blocks)
    blocks = classify_blocks(blocks, sections)

    seen, changed, rows_seen, both_not_printed = set(), 0, 0, 0
    for b in get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT):
        pi = b.page_number - 1
        if pi in seen:
            continue
        seen.add(pi)
        ftype = getattr(b, "financial_type", "UNKNOWN")

        try:
            cmap, centers = detect_column_layout(pdf_path, pi)
        except Exception:
            continue
        if cmap is None or centers is None:
            continue

        rows_old = old.extract_financials_positional(pdf_path, pi, centers)
        rows_new = new.extract_financials_positional(pdf_path, pi, centers)

        # Positional pairing: both runs walk the same rows in the same order,
        # and the change cannot add, drop or reorder a row -- it only alters a
        # value inside an already-formed bucket. Guarded rather than assumed.
        if len(rows_old) != len(rows_new):
            print(f"  !! page {b.page_number}: row count differs "
                  f"({len(rows_old)} -> {len(rows_new)}) -- pairing unsafe, skipped")
            continue

        for ro, rn in zip(rows_old, rows_new):
            rows_seen += 1
            if str(ro[0]) != str(rn[0]):
                print(f"  !! page {b.page_number}: label differs {ro[0]!r} -> {rn[0]!r}")
            for ci, (vo, vn) in enumerate(zip(ro[1:], rn[1:])):
                ko, kn = _cell_key(vo, old), _cell_key(vn, new)
                if ko == kn:
                    if ko is _NOT_PRINTED_KEY:
                        both_not_printed += 1
                    continue
                changed += 1
                fy, q = cmap[ci] if ci < len(cmap) else ("?", "?")
                print(f"\n  page {b.page_number} ({ftype})  col{ci} {fy} {q or 'ANNUAL'}")
                print(f"    label : {rn[0]!r}")
                print(f"    OLD   : {vo!r}")
                print(f"    NEW   : {vn!r}")

    print(f"\n{'-'*96}")
    print(f"pages {len(seen)} | rows compared {rows_seen} | CELLS CHANGED {changed}")
    # Reported so the sentinel guard is OBSERVABLE rather than silently
    # correct. If this is 0 on a document with any sparse column, _cell_key is
    # not doing what it claims and the CELLS CHANGED figure is not trustworthy.
    print(f"sentinel pairs collapsed (NOT_PRINTED both sides): {both_not_printed}")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
