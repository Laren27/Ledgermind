"""
Read-only: does pdfplumber emit mangled label text, or does our
normalisation mangle clean text?

Reads the source PDF directly. Zero project code in the path except
pdfplumber itself, so the answer is about the parser, not the extractor.
"""
import re
import sys

import pdfplumber

PDF = "/app/docs/raw/ZOMATO_ANNUAL_REPORT_2023-24.pdf"

# Fragments from the corrupted labels seen in the financials dump.
NEEDLES = [
    "nterest expense",
    "nvestment in debentures",
    "oan given",
    "wners of the parent",
    "ayment of interest portion",
    "orrowing repaid",
    "emeasurements of the defined",
    "ale of non",
    "ransaction cost paid",
]


def main():
    hits = 0
    with pdfplumber.open(PDF) as pdf:
        print(f"pages: {len(pdf.pages)}\n")
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            low = text.lower()
            for needle in NEEDLES:
                if needle in low:
                    idx = low.index(needle)
                    lo = max(0, idx - 120)
                    hi = min(len(text), idx + 160)
                    print(f"--- p{i} | needle={needle!r}")
                    print(f"    RAW: {text[lo:hi]!r}")
                    print()
                    hits += 1
                    if hits > 40:
                        print("(truncated)")
                        return
    if hits == 0:
        print("No needles found. The mangling may originate in table "
              "extraction rather than extract_text — rerun against "
              "page.extract_tables().")


if __name__ == "__main__":
    main()
