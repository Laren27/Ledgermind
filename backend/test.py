from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections, classify_and_register
from app.ingestion.section_classifier import classify_blocks
from app.ingestion.db_loader import get_connection

pdf_path = "../docs/raw/TITAN_Q1FY26_PRESS_RELEASE_AND_FINANCIAL_RESULTS.pdf"
ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"

blocks = parse_pdf(pdf_path)
sections = detect_sections(blocks)

conn = get_connection()
try:
    sections = classify_and_register(
        blocks=blocks, pdf_path=pdf_path, tenant_id=ALPHA_TENANT,
        company="TITAN", ticker="TITAN", fiscal_year="FY26", quarter="Q1",
        doc_type="quarterly_result", filing_date="2026-07-25", 
        conn=conn,
    )
finally:
    conn.close()

blocks = classify_blocks(blocks, sections)

for b in blocks:
    # Dropped the .name right here!
    if b.block_type == "FINANCIAL_STATEMENT" and "revenue" in b.content.lower():
        print(f"--- page {b.page_number} ---")
        print(b.content[:2000])
        print()