"""One-off: dump consecutive transcript chunks to inspect speaker-line survival.

Reads only. No DB writes, no Qdrant, no LLM. Sections are built by hand
rather than via classify_and_register so this cannot register a document.
"""
import sys, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.models import DocSection, FinancialType
from app.ingestion.section_classifier import classify_blocks
from app.ingestion.chunker import chunk_blocks

PDF = sys.argv[1] if len(sys.argv) > 1 else "docs/raw/Q4FY26-earnings-call-transcript.pdf"
START = int(sys.argv[2]) if len(sys.argv) > 2 else 90
COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 3

blocks = parse_pdf(PDF)
total = max(b.page_number for b in blocks)
sections = [DocSection(financial_type=FinancialType.CONSOLIDATED,
                       page_start=1, page_end=total)]
sections[0].doc_id = "00000000-0000-0000-0000-000000000000"
blocks = classify_blocks(blocks, sections)

chunks = chunk_blocks(
    blocks=blocks, sections=sections,
    tenant_id="a0000000-0000-0000-0000-000000000001",
    company="ETERNAL", ticker="ETERNAL", fiscal_year="FY26", quarter="Q4",
    document_type="earnings_transcript", filing_date="2026-04-28",
)

print(f"\nTOTAL CHUNKS: {len(chunks)}\n")
for i in range(START, min(START + COUNT, len(chunks))):
    c = chunks[i]
    print("=" * 70)
    print(f"CHUNK {i} | page {c.metadata.page_number} | "
          f"{len(c.text)} chars | type={c.metadata.chunk_type} | "
          f"ft={c.metadata.financial_type}")
    print("=" * 70)
    print(c.text)
    print()
