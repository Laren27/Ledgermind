from pathlib import Path
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks, get_blocks_by_type
from app.ingestion.models import BlockType

pdf_path = str(Path.home() / "ledgermind/docs/raw/ZOMATO_ANNUAL_REPORT_2023-24.pdf")
blocks = parse_pdf(pdf_path)
sections = detect_sections(blocks)
blocks = classify_blocks(blocks, sections)

fs_blocks = get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)
print(f"Total FINANCIAL_STATEMENT pages: {len(fs_blocks)}")
for b in fs_blocks:
    preview = b.content[:100].replace("\n", " ")
    print(f"  page {b.page_number:4d} | {preview}")