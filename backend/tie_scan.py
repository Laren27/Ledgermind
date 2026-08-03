"""
READ-ONLY diagnostic: find raw labels whose normalised form ties across TWO OR
MORE different canonical metrics under METRIC_ALIASES subset matching.

WHY THIS EXISTS. A tie is the shape that lets one source row silently steal
another's slot. PAYTM's 'Deferred tax expense/ (credit)' tokenises to a 4-word
label of which both "deferred tax" (-> deferred_tax) and tax_expense's "tax
expense" are 2-word subsets, scoring identically; tax_expense is declared first
in ALL_METRICS, so the deferred row won the tax_expense key and the genuine
'Total Tax expense' row was discarded by seen_keys first-wins. Consolidated
tax_expense then held the DEFERRED figure for weeks. This scan enumerates every
label in the corpus with that ambiguity, before it costs anything.

WHY THE __main__ GUARD, which is the reason this file changed. The scan body
used to sit at module level, so `import tie_scan` -- or any tool that imports
every module in the tree -- triggered a parse_pdf() over all four corpus PDFs as
an import side effect. CLAUDE.md §7 is explicit that parsing the corpus twice
exhausts WSL RAM and restarts the distro, so an accidental import was a way to
take the machine down. Importing this module is now free; only running it does
work.

Parses each document ONCE per run. Read-only: no DB writes, no Qdrant calls.

Usage:
  docker compose exec -T backend python tie_scan.py
"""

import re
from collections import defaultdict

from app.ingestion.entity_resolver import METRIC_ALIASES, normalize_metric_label
from scripts.regression_check import DOCUMENTS, RAW_DIR
from app.ingestion.pdf_parser import parse_pdf, extract_financials, extract_financials_positional
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.models import BlockType
from app.ingestion.financial_extractor import detect_column_layout

SPLIT = re.compile(r"[\s/]+")


def candidates(raw):
    n = normalize_metric_label(raw)
    if not n or n in METRIC_ALIASES:
        return n, []
    nw = set(SPLIT.split(n)) - {""}
    best, hits = 0, []
    for alias, canon in METRIC_ALIASES.items():
        aw = set(SPLIT.split(alias)) - {""}
        if not aw:
            continue
        if aw <= nw and len(aw) / len(nw) < 0.5:
            continue
        if aw <= nw or nw <= aw:
            if len(aw) > best:
                best, hits = len(aw), [(alias, canon)]
            elif len(aw) == best:
                hits.append((alias, canon))
    return n, hits


def scan():
    ties = defaultdict(set)
    for doc in DOCUMENTS:
        path = str(RAW_DIR / doc["filename"])
        _b = parse_pdf(path)
        blocks = classify_blocks(_b, detect_sections(_b))
        seen = set()
        for b in get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT):
            pi = b.page_number - 1
            if pi in seen:
                continue
            seen.add(pi)
            try:
                cmap, centers = detect_column_layout(path, pi)
            except Exception:
                continue
            if cmap is None:
                continue
            rows = (extract_financials_positional(path, pi, centers)
                    if centers is not None else extract_financials(path, pi))
            for r in rows or []:
                if not r or len(r) < 2:
                    continue
                n, hits = candidates(str(r[0]).strip())
                canons = {c for _, c in hits}
                if len(canons) > 1:
                    ties[(doc["company"], n)] |= {f"{a}->{c}" for a, c in hits}
    return ties


def main():
    ties = scan()
    print(f"\n=== {len(ties)} distinct tied labels ===")
    for (co, label), opts in sorted(ties.items()):
        print(f"  {co} | {label!r}\n      {sorted(opts)}")


if __name__ == "__main__":
    main()
