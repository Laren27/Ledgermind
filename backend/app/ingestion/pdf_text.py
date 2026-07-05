"""
app/ingestion/pdf_text.py

Cheap text extraction for the pre-ingestion gate ONLY. Deliberately does
NOT use the full pdfplumber layout-mode extraction from the main parsing
pipeline (Stage 2 of the blueprint's ingestion pipeline) — the gate just
needs to score keywords, not reconstruct tables. Keeping this separate
avoids coupling the gate to parser changes and keeps gate-check latency
near-zero (no layout analysis, no table detection).
"""

import pdfplumber


def extract_first_n_pages_text(pdf_path: str, n: int = 2) -> str:
    """
    Extract plain text from the first n pages of a PDF for gate scoring.

    Uses plain extract_text() (fast) rather than extract_text(layout=True)
    (used later by the real parser) since column/row structure doesn't
    matter for keyword scoring.
    """
    text_chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:n]:
            page_text = page.extract_text() or ""
            text_chunks.append(page_text)
    return "\n".join(text_chunks)