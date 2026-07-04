import sys
from pathlib import Path
from app.ingestion.pdf_parser import parse_pdf, extract_financials

def run_debug():
    print("=== ETERNAL PAGE 31 ROWS ===")
    eternal_path = str(Path.home() / "ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf")
    # Page 31 is index 30 in the zero-indexed list
    rows = extract_financials(eternal_path, 30)
    for r in rows[:15]:
        print(r)

    print("\n=== ZOMATO PAGE 164 TEXT ===")
    zomato_path = str(Path.home() / "ledgermind/docs/raw/ZOMATO_ANNUAL_REPORT_2023-24.pdf")
    blocks = parse_pdf(zomato_path)
    # Print the first 800 characters to see exactly where the title is
    p164_text = "\n".join(b.content for b in blocks if b.page_number == 164)
    print(p164_text[:800].replace('\n', ' | '))

if __name__ == "__main__":
    run_debug()