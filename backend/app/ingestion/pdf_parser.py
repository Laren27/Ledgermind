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
import re
import pdfplumber
from .models import BlockType, PageBlock

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

_VALUE_TOKEN_RE = re.compile(r"^\(?-?[\d,]*\.?\d+\)?$|^-$")
MIN_VALUE_COLUMNS = 2  # a real financial data row always has at least 2 periods


def parse_pdf(pdf_path: str) -> list:
    blocks = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            text_lower = text.lower()

            # Multiple independent signals for "this page contains a real table":
            has_table_borders = bool(page.find_tables())
            has_financial_header_markers = (
                "quarter ended" in text_lower or "year ended" in text_lower
            )
            # Catch borderless tables using numeric dates (e.g. 30-06-2025)
            has_numeric_dates = bool(re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", text_lower))
            # Catch borderless tables using core P&L structural words
            has_pnl_anchors = bool(re.search(r"(revenue from operations|sale of products|total income|profit before tax)", text_lower))

            if has_table_borders or has_financial_header_markers or has_numeric_dates or has_pnl_anchors:
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
    NUMBER NORMALIZATION: Converts OCR strings to standard floats.
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
    """
    Split a financial statement line into [description, value1, value2, ...]
    """
    line = line.rstrip("\n")
    if not line.strip():
        return None

    # Strip common footnote references that glue to descriptions and confuse the parser
    line = re.sub(r'\(refer note \d+\)', '', line, flags=re.IGNORECASE)

    # Apply Typo Mapping (word-boundary matches only)
    for wrong, right in sorted(TYPO_MAP.items(), key=lambda kv: -len(kv[0])):
        pattern = r'\b' + re.escape(wrong) + r'\b'
        line = re.sub(pattern, right, line)

    # Strip leading Roman numeral / letter prefixes: "I.", "V.", "(a)" etc.
    line = re.sub(r'^(I{1,3}|IV|V|VI{1,3}|IX|X)\.?\s+', '', line)

    # RESTORED: merge OCR-artifact spaces inside Indian comma-grouped numbers
    # ("16 663" -> "16663"). Must run BEFORE line.split() below — without
    # this, a single mis-extracted comma-as-space corrupts the entire
    # column count for the rest of the row. This was present in the
    # original ETERNAL-verified parser and was accidentally dropped when
    # switching to bare line.split() for Titan's single-space columns.
    # Safe for both filings: only merges a digit + space + exactly-3-digits
    # pattern; Titan's numbers extract with commas intact and never match.
    line = re.sub(r'(?<=\d) (?=\d{3}(?:[^\d]|$))', '', line)

    # Split on ANY whitespace (handles Titan's tightly-squeezed columns).
    parts = line.split()
    if len(parts) < 1:
        return None

    # Scan from the right: collect trailing tokens that look like a value
    value_tokens = []
    split_idx = len(parts)
    for i in range(len(parts) - 1, -1, -1):
        token = parts[i].strip()
        if _VALUE_TOKEN_RE.match(token):
            value_tokens.insert(0, token)
            split_idx = i
        else:
            break

    if len(value_tokens) < MIN_VALUE_COLUMNS:
        return None

    description = " ".join(parts[:split_idx]).strip()
    if not description:
        return None

    clean_values = [clean_financial_number(v) for v in value_tokens]
    return [description] + clean_values


def extract_financials(pdf_path, page_index):
    """Main extraction function with Header Skipping."""
    financial_data = []
    parsing_started = False
    
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        text = page.extract_text(layout=True)
        
        for line in text.split('\n'):
            # HEADER SKIPPING: Ignore dates/headers until we hit the first real row
            if not parsing_started:
                lower_line = line.lower()
                if "revenue" in lower_line or "income" in lower_line or "sale" in lower_line:
                    parsing_started = True
                else:
                    continue
                    
            parsed_row = parse_financial_line(line)
            if parsed_row:
                financial_data.append(parsed_row)
                
    return financial_data