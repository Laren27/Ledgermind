import os
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks

def run_diagnostic():
    # Using the absolute path so we don't have to guess relative folders
    pdf_path = os.path.expanduser("~/ledgermind/docs/raw/FS-Results_Q4-&-Financial-Year-ended-March-31,-2026.pdf")
    
    print(f"Scanning missing pages in: {pdf_path}\n")
    
    # 1. Extract raw blocks from PDF
    blocks = parse_pdf(pdf_path)
    
    # 2. Detect consolidated vs standalone sections
    sections = detect_sections(blocks)
    
    # 3. Classify the blocks (Risk, Financial Statement, Table, etc.)
    blocks = classify_blocks(blocks, sections)
    
    # 4. Print only our missing balance sheet pages
    for b in blocks:
        if b.page_number in (5, 6, 7, 16):
            # Safely grab text and type regardless of attribute naming
            content = getattr(b, 'text', getattr(b, 'content', 'No text found'))
            b_type = getattr(b, 'block_type', getattr(b, 'chunk_type', 'UNKNOWN'))
            
            print(f"page={b.page_number} type={b_type}")
            print(f"preview={content[:150]!r}\n")

if __name__ == "__main__":
    run_diagnostic()