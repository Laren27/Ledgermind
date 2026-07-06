from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections, classify_and_register
from app.ingestion.section_classifier import classify_blocks, STATEMENT_TITLE_ANCHORS
from app.ingestion.db_loader import get_connection

pdf_path = "../docs/raw/ZOMATO_ANNUAL_REPORT_2023-24.pdf"
blocks = parse_pdf(pdf_path)
sections = detect_sections(blocks)
conn = get_connection()
try:
    sections = classify_and_register(
        blocks=blocks, pdf_path=pdf_path, tenant_id="a0000000-0000-0000-0000-000000000001",
        company="ETERNAL", ticker="ETERNAL", fiscal_year="FY24", quarter=None,
        doc_type="annual_report", filing_date="2024-08-31", conn=conn,
    )
finally:
    conn.close()
blocks = classify_blocks(blocks, sections)

for p in [57, 167, 168, 169, 170, 175, 176, 177, 283, 284]:
    b = next(b for b in blocks if b.page_number == p)
    content_lower = b.content.lower()
    matched = [a for a in STATEMENT_TITLE_ANCHORS if a in content_lower]
    print(f"page={p} type={b.block_type} matched_anchors={matched}")
    print(f"  preview: {b.content[:120]!r}\n")