"""
PDF Parser — pdfplumber wrapper.

Responsibility: extract raw content from a PDF and return List[PageBlock].
Nothing else. No chunking, no classification, no metadata injection.

Design decisions:
  - Tables extracted via pdfplumber's extract_tables() (returns list of row lists)
  - Text extracted via extract_text() with layout preservation
  - Each page produces N PageBlocks: one per table + one text block for remaining text
  - Tables are extracted first; text blocks have table regions masked out
    (pdfplumber handles this automatically when you call extract_text after
     extract_tables on the same page object)
  - Returns empty list on encrypted or corrupted PDF with clear error logged

Downstream consumers:
  - document_classifier.py reads PageBlock.content to find section boundaries
  - section_classifier.py reads block_type
  - table_extractor.py reads PageBlock.table for header stitching
"""
from .models import BlockType, PageBlock
import pdfplumber
import re

# 1. TYPO MAPPING: Fix consistent OCR artifacts
# 1. TYPO MAPPING: Fix consistent OCR artifacts
TYPO_MAP = {
    "Ill": "III",
    "ll": "II",
    "l": "I",
    "COSIS": "costs",
    "ofs tock": "of stock",
    "amonisation": "amortisation",
    "benefi1s": "benefits",
    "incomc": "income",
    "TotaI": "Total",
    "EmpIoyee": "Employee",
    "DeIivery": "Delivery",
    "reIated": "related",
    "saIes": "sales",
    "Advcniscmcnt": "Advertisement"
}


def parse_pdf(pdf_path: str) -> list:
    import pdfplumber
    # Adjust your import path for PageBlock/BlockType if needed
    from .models import PageBlock, BlockType 

    blocks = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            
            # THE FIX: Check if pdfplumber sees a table on this page
            if page.find_tables():
                b_type = BlockType.TABLE
            else:
                b_type = BlockType.TEXT
                
            blocks.append(PageBlock(
                page_number=i + 1,
                content=text,
                block_type=b_type
            ))
    return blocks


def get_page_count(pdf_path: str) -> int:
    """Return total page count without full parse."""
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def clean_financial_number(val):
    """
    2. NUMBER NORMALIZATION: Converts OCR strings to standard floats.
    Handles negatives in parentheses, nil dashes, and OCR comma/period confusion.
    """
    if not val or val == '-':
        return 0.0
        
    # Check for negatives before stripping formatting
    is_negative = '(' in val and ')' in val
    val = val.replace('(', '').replace(')', '')
    
    # Fix OCR comma/period confusion (e.g., '17.634' -> '17634')
    # If a period is followed by exactly 3 digits at the end, it is a misread comma
    val = re.sub(r'\.(?=\d{3}$)', '', val)
    val = val.replace(',', '')
    
    try:
        num = float(val)
        return -num if is_negative else num
    except ValueError:
        return None

def parse_financial_line(line):
    """Applies right-to-left regex extraction to pull the description and 5 values."""
    line = line.strip()
    if not line:
        return None
        
    # Apply Typo Mapping
    for wrong, right in TYPO_MAP.items():
        line = line.replace(wrong, right)
        
    # Clean Roman numerals at the very start of the line
    line = re.sub(r'^(I{1,3}|IV|V|VI{1,3}|IX|X)\s+', '', line)
    
    # Fix internal spaces inside numbers ('16 663' -> '16663')
    # AFTER — only merges Indian-format grouped numbers (e.g. "16 663" → "16663")
    # Requires the second group to be exactly 3 digits followed by non-digit or end
    line = re.sub(r'(?<=\d) (?=\d{3}(?:[^\d]|$))', '', line)
    
    # Match right-to-left financial values (numbers, parens, dashes)
    val_pattern = r'(?:\([\d.,]+\)|[\d.,]+|-)'
    matches = list(re.finditer(val_pattern, line))
    
    # If we find at least 5 values, safely split the row
    if len(matches) >= 5:
        first_val_idx = matches[-5].start()
        description = line[:first_val_idx].strip()
        
        # Extract the raw strings and pass them through the cleaner
        raw_values = [m.group() for m in matches[-5:]]
        clean_values = [clean_financial_number(v) for v in raw_values]
        
        return [description] + clean_values
        
    return None

def extract_financials(pdf_path, page_index):
    """Main extraction function with Header Skipping."""
    financial_data = []
    parsing_started = False
    
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        text = page.extract_text(layout=True)
        
        for line in text.split('\n'):
            # 3. HEADER SKIPPING: Ignore dates/headers until we hit the first real row
            if not parsing_started:
                if "Revenue from operations" in line or "Other income" in line:
                    parsing_started = True
                else:
                    continue
                    
            parsed_row = parse_financial_line(line)
            if parsed_row:
                financial_data.append(parsed_row)
                
    return financial_data

# --- Execution ---
if __name__ == "__main__":
    pdf_path = "/home/laren/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"
    
    # Extract Page 31 (Index 30) - Consolidated Results
    print("Extracting Consolidated Results...")
    extracted_data = extract_financials(pdf_path, 30)
    
    for row in extracted_data[:10]:
        print(row)