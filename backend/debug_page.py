"""
Diagnostic: dump raw pdfplumber word positions for the Total Income row on
page 31 (consolidated), to determine whether "17.634" (should be "7.634"
per the identity check: revenue 7292 + other_income 342 = 7634) arrives as
ONE single pdfplumber word (meaning the error is baked into the PDF's own
OCR text layer, outside our control) or as multiple fragments that our
bucketing logic incorrectly concatenated (meaning it's a code bug we can fix).

Run from ~/ledgermind/backend:
  set -a; source ../.env; set +a
  python3 diagnose_page31_totalincome.py
"""
import pdfplumber
import os

pdf_path = os.path.expanduser(
    "~/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"
)
PAGE_IDX = 30  # page 31, 0-indexed

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[PAGE_IDX]
    words = page.extract_words()

rows = {}
for w in words:
    top = w["top"]
    found = False
    for row_top in rows.keys():
        if abs(top - row_top) <= 3.0:
            rows[row_top].append(w)
            found = True
            break
    if not found:
        rows[top] = [w]

print(f"Total rows detected on page 31: {len(rows)}\n")

for row_top in sorted(rows.keys()):
    row_words = sorted(rows[row_top], key=lambda w: w["x0"])
    row_text = " ".join(w["text"] for w in row_words)
    if "total" in row_text.lower() and ("income" in row_text.lower() or "incomc" in row_text.lower()):
        print(f"--- row at top={row_top:.1f} ---")
        print(f"  full text: {row_text}")
        for w in row_words:
            print(f"    x0={w['x0']:>7.2f}  x1={w['x1']:>7.2f}  text={w['text']!r}")
        print()