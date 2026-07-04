from pathlib import Path
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.models import BlockType

pdf_path = str(Path.home() / "ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf")
blocks = parse_pdf(pdf_path)
sections = detect_sections(blocks)
blocks = classify_blocks(blocks, sections)

for b in get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT):
    print(f"--- page {b.page_number} ---")
    print(b.content[:200].replace("\n", " "))
    print()