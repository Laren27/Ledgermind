"""
Diagnostic: dump raw pdfplumber word positions for the standalone quarterly
table on page 40 (0-indexed page 39), specifically around the
finance_costs / depreciation rows that are failing to bucket correctly.

Run from ~/ledgermind/backend:
  set -a; source ../.env; set +a
  python3 diagnose_page40.py
"""
import pdfplumber
import os

pdf_path = os.path.expanduser(
    "~/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"
)
PAGE_IDX = 39  # page 40, 0-indexed

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[PAGE_IDX]
    words = page.extract_words()

# Group words into rows the same way extract_financials_positional does
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

print(f"Total rows detected on page 40: {len(rows)}\n")

for row_top in sorted(rows.keys()):
    row_words = sorted(rows[row_top], key=lambda w: w["x0"])
    row_text = " ".join(w["text"] for w in row_words)
    if "finance" in row_text.lower() or "depreciation" in row_text.lower() or "amortisation" in row_text.lower():
        print(f"--- row at top={row_top:.1f} ---")
        print(f"  full text: {row_text}")
        for w in row_words:
            print(f"    x0={w['x0']:>7.2f}  x1={w['x1']:>7.2f}  text={w['text']!r}")
        print()

# Also dump the header row detection for this page so we can compare
# column centers against the word positions above.
print("=" * 60)
print("Header/column detection for this page:")
import sys
sys.path.insert(0, os.path.expanduser("~/ledgermind/backend"))
from app.ingestion.financial_extractor import detect_column_layout

column_map, column_centers = detect_column_layout(pdf_path, PAGE_IDX)
print(f"  column_map: {column_map}")
print(f"  column_centers: {column_centers}")