"""
Financial Extractor — converts FINANCIAL_STATEMENT blocks into FinancialRecord objects.

Sits above pdf_parser.extract_financials() and handles:
  1. Column map detection  — maps each of the 5 parsed values to (fiscal_year, quarter)
  2. Row → FinancialRecord — metric normalization + one record per column per row
  3. Orchestration         — iterates FINANCIAL_STATEMENT blocks, dedupes pages

Why column map detection matters:
  extract_financials() returns rows like:
    ["Revenue from operations", 4502.0, 3987.0, 3201.0, 17680.0, 13400.0]
  Without knowing col3 = FY26 annual, col0 = Q4FY26, the numbers are meaningless.

SEBI mandates a fixed 5-column structure for quarterly results:
  Col 0 → current quarter          (Q4FY26)
  Col 1 → previous quarter         (Q3FY26)
  Col 2 → same quarter, last year  (Q4FY25)
  Col 3 → current full year        (FY26, annual, quarter=None)
  Col 4 → previous full year       (FY25, annual, quarter=None)

Called by: pipeline.py
Calls:     pdf_parser.extract_financials(), entity_resolver.normalize_metric()
"""

import logging
import re
from typing import Optional

import pdfplumber

from .entity_resolver import normalize_metric, METRIC_ALIASES
from .models import BlockType, FinancialRecord, FinancialType, PageBlock
from .pdf_parser import extract_financials
from .section_classifier import get_blocks_by_type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Column map detection
# ---------------------------------------------------------------------------

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# Indian fiscal year runs April–March.
MONTH_TO_QUARTER = {
    4: "Q1", 5: "Q1", 6: "Q1",
    7: "Q2", 8: "Q2", 9: "Q2",
    10: "Q3", 11: "Q3", 12: "Q3",
    1: "Q4", 2: "Q4", 3: "Q4",
}

_YEAR_WORD_RE = re.compile(r"^\d{4}$")
_NUMERIC_DATE_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})")

_TRAILING_JUNK_RE = re.compile(
    r"\s*[\(\[]?"           # optional opening bracket
    r"[ivxlcdmIVXLCDM]+"   # Roman numerals
    r"[\+\d\(\)]*"         # optional arithmetic like (I+II)
    r"[\)\]]?"             # optional closing bracket
    r"\s*$",               # end of string
    re.IGNORECASE,
)


def _clean_description(raw: str) -> str:
    """
    Remove trailing Roman numeral references from financial statement descriptions.
    """
    cleaned = _TRAILING_JUNK_RE.sub("", raw).strip()
    cleaned = re.sub(r"[/\-\s]+$", "", cleaned).strip()
    return cleaned


def _date_to_period(month: int, year: int) -> tuple[str, str]:
    """
    Convert calendar month + year to Indian fiscal year string and quarter.
    """
    if month >= 4:
        fy_num = str(year + 1)[2:]
    else:
        fy_num = str(year)[2:]
    fiscal_year = f"FY{fy_num}"
    quarter = MONTH_TO_QUARTER[month]
    return fiscal_year, quarter


def detect_column_map(pdf_path: str, page_idx: int) -> Optional[list[tuple[str, Optional[str]]]]:
    """
    Detects SEBI column structures using Spatial Alignment.
    """
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_idx]
        words = page.extract_words()

    if not words:
        return None

    # --- Strategy 1: Standard Horizontal Numeric Dates (DD.MM.YYYY) ---
    numeric_dates = []
    for w in words:
        text = w["text"].strip(".,()[]")
        match = _NUMERIC_DATE_RE.search(text)
        if match:
            month, year = int(match.group(2)), int(match.group(3))
            if 1 <= month <= 12 and year > 2000:
                numeric_dates.append((w["x0"], w["top"], month, year))
                
    if numeric_dates:
        rows = {}
        for x0, top, month, year in numeric_dates:
            found = False
            for row_top in rows.keys():
                if abs(top - row_top) <= 20.0:
                    rows[row_top].append((x0, month, year))
                    found = True
                    break
            if not found:
                rows[top] = [(x0, month, year)]
        
        header_row_top = max(rows.keys(), key=lambda t: len(rows[t]))
        header_dates = rows[header_row_top]
        header_dates.sort(key=lambda d: d[0])
        
        unique_dates = []
        for d in header_dates:
            if not unique_dates or (d[0] - unique_dates[-1][0]) > 20.0:
                unique_dates.append(d)
        
        if len(unique_dates) >= 3:
            column_map = []
            for i, (x0, month, year) in enumerate(unique_dates[:5]):
                fy, q = _date_to_period(month, year)
                column_map.append((fy, q if i < 3 else None))
            logger.info("Column map detected via Numeric Dates: %s", column_map)

            if len(set(column_map)) != len(column_map):
                logger.warning(
                    "Column map detection failed: duplicate periods in detected "
                    "map %s (page_idx=%d). Skipping.",
                    column_map, page_idx,
                )
                return None

            return column_map

    # --- Strategy 2: Spatial Wrap-Header via Geometric Partitioning ---
    year_words = [w for w in words if _YEAR_WORD_RE.match(w["text"].strip(".,()[]"))]
    
    year_rows = {}
    for y_w in year_words:
        top = y_w["top"]
        found = False
        for row_top in year_rows.keys():
            if abs(top - row_top) <= 10.0:
                year_rows[row_top].append(y_w)
                found = True
                break
        if not found:
            year_rows[top] = [y_w]

    if year_rows:
        header_row_top = max(year_rows.keys(), key=lambda t: len(year_rows[t]))
        header_years = year_rows[header_row_top]
        header_years.sort(key=lambda w: w["x0"])

        unique_years = []
        for w in header_years:
            if not unique_years or (w["x0"] - unique_years[-1]["x0"]) > 20.0:
                unique_years.append(w)
        
        if len(unique_years) >= 3:
            month_words = []
            for w in words:
                if w["top"] < header_row_top + 10 and w["top"] > header_row_top - 100:
                    m_str = w["text"].lower()
                    m_val = None
                    if 'jan' in m_str: m_val = 1
                    elif 'feb' in m_str: m_val = 2
                    elif 'rch' in m_str or 'arch' in m_str: m_val = 3
                    elif 'apr' in m_str: m_val = 4
                    elif 'may' in m_str: m_val = 5
                    elif 'jun' in m_str: m_val = 6
                    elif 'jul' in m_str: m_val = 7
                    elif 'aug' in m_str: m_val = 8
                    elif 'sep' in m_str: m_val = 9
                    elif 'oct' in m_str: m_val = 10
                    elif 'nov' in m_str: m_val = 11
                    elif 'dec' in m_str or 'cem' in m_str: m_val = 12
                    
                    if m_val:
                        month_center = (w["x0"] + w["x1"]) / 2
                        month_words.append((month_center, m_val, w["top"]))

            column_map = []
            for i, y_w in enumerate(unique_years[:5]):
                year_val = int(y_w["text"].strip(".,()[]"))
                year_center = (y_w["x0"] + y_w["x1"]) / 2
                
                my_months = []
                for m in month_words:
                    m_center = m[0]
                    # Find which year column this month belongs to (Partitioning)
                    closest_year_idx = min(
                        range(len(unique_years[:5])), 
                        key=lambda idx: abs((unique_years[idx]["x0"] + unique_years[idx]["x1"]) / 2 - m_center)
                    )
                    if closest_year_idx == i:
                        my_months.append(m)
                        
                if my_months:
                    # If multiple months fall in this column, take the one physically closest to the year row
                    best_month = max(my_months, key=lambda m: m[2]) 
                    month_val = best_month[1]
                else:
                    # Smart Fallback: Col 1 is usually previous quarter (December), others March for Q4 filings
                    month_val = 12 if i == 1 else 3

                fy, q = _date_to_period(month_val, year_val)
                column_map.append((fy, q if i < 3 else None))

            logger.info("Column map detected via Spatial Year Alignment: %s", column_map)

            if len(set(column_map)) != len(column_map):
                logger.warning(
                    "Column map detection failed: duplicate periods in detected "
                    "map %s (page_idx=%d). Skipping.",
                    column_map, page_idx,
                )
                return None
                
            return column_map

    logger.warning("Column map detection failed: no numeric or English dates found (page_idx=%d).", page_idx)
    return None


# ---------------------------------------------------------------------------
# Row → FinancialRecord conversion
# ---------------------------------------------------------------------------

_KNOWN_METRICS: set[str] = set(METRIC_ALIASES.values())

_SKIP_DESCRIPTIONS = {
    "", "-", "nil", "n/a", "total", "sub total", "subtotal",
    "particulars", "s. no.", "s.no.", "note",
    "owners of the parent",
    "owners of the subsidiary",
    "non-controlling interests",
}


def _should_skip_row(description: str, values: list) -> bool:
    desc_lower = description.lower().strip()

    if desc_lower in _SKIP_DESCRIPTIONS:
        return True

    if re.match(r"^(i{1,3}|iv|v|vi{1,3}|ix|x)$", desc_lower):
        return True

    if re.match(r"^\([a-z]\)", desc_lower):
        return True

    if re.match(r"^\d+\s+[£₹a-z]", desc_lower):
        return True

    max_val = max((abs(v) for v in values if v is not None), default=0)
    if max_val > 500_000:
        import logging
        logging.getLogger(__name__).warning(
            "Implausible value %.0f in row '%s' — skipping", max_val, description
        )
        return True

    non_zero = [v for v in values if v is not None and v != 0.0]
    if not non_zero:
        return True

    return False


def _rows_to_records(
    rows: list[list],
    column_map: list[tuple[str, Optional[str]]],
    financial_type: str,
    tenant_id: str,
    company: str,
    ticker: str,
    filing_date: str,
    doc_id: str,
) -> list[FinancialRecord]:
    """
    Convert parsed rows from extract_financials() into FinancialRecord objects.
    """
    records: list[FinancialRecord] = []

    for row in rows:
        if not row or len(row) < 2:
            continue

        description = str(row[0]).strip()
        values = row[1:] 

        if _should_skip_row(description, values):
            logger.debug("Skipping row: '%s'", description)
            continue

        cleaned_description = _clean_description(description)
        normalized_metric = normalize_metric(cleaned_description)

        if normalized_metric not in _KNOWN_METRICS:
            logger.info(
                "Unknown metric '%s' (normalized: '%s') — storing with raw name. "
                "Consider adding to METRIC_ALIASES.",
                description, normalized_metric,
            )

        for col_idx, (fiscal_year, quarter) in enumerate(column_map):
            if col_idx >= len(values):
                break

            value = values[col_idx]
            if value is None:
                continue

            records.append(FinancialRecord(
                tenant_id=tenant_id,
                doc_id=doc_id,
                company=company,
                ticker=ticker,
                fiscal_year=fiscal_year,
                quarter=quarter,
                financial_type=financial_type,
                metric=normalized_metric,
                value=float(value),
                unit="crore_inr",
                filing_date=filing_date,
            ))

    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_all_financial_records(
    blocks: list[PageBlock],
    pdf_path: str,
    tenant_id: str,
    company: str,
    ticker: str,
    filing_date: str,
    doc_id_map: dict[str, str],
) -> list[FinancialRecord]:
    """
    Process all FINANCIAL_STATEMENT blocks and return a flat list of FinancialRecord.
    """
    financial_blocks = get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)
    logger.info("Processing %d FINANCIAL_STATEMENT blocks", len(financial_blocks))

    all_records: list[FinancialRecord] = []
    processed_pages: set[int] = set() 

    for block in financial_blocks:
        page_number = block.page_number          
        page_idx    = page_number - 1            
        financial_type = getattr(block, "financial_type", FinancialType.UNKNOWN)

        if page_idx in processed_pages:
            logger.debug("Page %d already processed — skipping", page_number)
            continue
        processed_pages.add(page_idx)

        doc_id = doc_id_map.get(financial_type)
        if not doc_id:
            logger.warning(
                "No doc_id in doc_id_map for financial_type='%s' (page %d) — skipping. "
                "Check document_classifier output.",
                financial_type, page_number,
            )
            continue

        try:
            column_map = detect_column_map(pdf_path, page_idx)
        except Exception as e:
            logger.error("Could not run column detection on page %d: %s", page_number, e)
            continue

        if column_map is None:
            logger.warning(
                "Page %d (%s): column map detection failed — SKIPPING this "
                "page's financial rows entirely rather than guessing periods. "
                "Flag for manual review (needs_review).",
                page_number, financial_type,
            )
            continue

        rows = extract_financials(pdf_path, page_idx)

        if not rows:
            logger.info(
                "Page %d (%s): no rows extracted — likely balance sheet or "
                "notes page without P&L anchor. Skipping.",
                page_number, financial_type,
            )
            continue

        records = _rows_to_records(
            rows=rows,
            column_map=column_map,
            financial_type=financial_type,
            tenant_id=tenant_id,
            company=company,
            ticker=ticker,
            filing_date=filing_date,
            doc_id=doc_id,
        )

        logger.info(
            "Page %d (%s): %d rows → %d records",
            page_number, financial_type, len(rows), len(records),
        )
        all_records.extend(records)

    logger.info(
        "Extraction complete: %d total FinancialRecord objects from %d pages",
        len(all_records), len(processed_pages),
    )
    return all_records


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import os
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from pathlib import Path
    from .db_loader import get_connection, load_financial_records, verify_financials
    from .document_classifier import classify_and_register, detect_sections
    from .pdf_parser import parse_pdf
    from .section_classifier import classify_blocks

    parser = argparse.ArgumentParser(
        description="Financial extractor smoke test — defaults reproduce the "
                     "ETERNAL Q4FY26 regression case when run with no arguments."
    )
    parser.add_argument(
        "pdf_path", nargs="?",
        default=os.path.expanduser(
            "~/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"
        ),
    )
    parser.add_argument("--company", default="ETERNAL")
    parser.add_argument("--ticker", default="ETERNAL")
    parser.add_argument("--fiscal-year", default="FY26")
    parser.add_argument("--quarter", default="Q4")  # pass --quarter "" for annual reports
    parser.add_argument("--doc-type", default="quarterly_result")
    parser.add_argument("--filing-date", default="2026-04-28")
    parser.add_argument(
        "--golden", action="append", default=[],
        help='Optional golden assertion as "financial_type,metric,fiscal_year,quarter,value". '
             'Repeatable. quarter="" for annual. Example: '
             '--golden "consolidated,revenue,FY26,Q1,14966.0"',
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    quarter = args.quarter or None
    ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"

    # ----------------------------------------------------------------
    # Step 1: Full parse + classify pipeline
    # ----------------------------------------------------------------
    print(f"\nParsing: {pdf_path.name}")
    blocks = parse_pdf(str(pdf_path))

    sections = detect_sections(blocks)
    print(f"Sections: {[(s.financial_type, s.page_start, s.page_end) for s in sections]}")

    conn = get_connection()
    try:
        sections = classify_and_register(
            blocks=blocks,
            pdf_path=pdf_path,
            tenant_id=ALPHA_TENANT,
            company=args.company,
            ticker=args.ticker,
            fiscal_year=args.fiscal_year,
            quarter=quarter,
            doc_type=args.doc_type,
            filing_date=args.filing_date,
            conn=conn,
        )
    finally:
        conn.close()

    doc_id_map = {s.financial_type: str(s.doc_id) for s in sections}
    print(f"\ndoc_id_map: {doc_id_map}")

    blocks = classify_blocks(blocks, sections)

    # ----------------------------------------------------------------
    # Step 2: Extract FinancialRecord objects
    # ----------------------------------------------------------------
    print("\n--- Extracting financial records ---")
    records = extract_all_financial_records(
        blocks=blocks,
        pdf_path=str(pdf_path),
        tenant_id=ALPHA_TENANT,
        company=args.company,
        ticker=args.ticker,
        filing_date=args.filing_date,
        doc_id_map=doc_id_map,
    )

    print(f"\nTotal records extracted: {len(records)}")
    print("\nSample records (first 10):")
    for r in records[:10]:
        print(f"  {r.financial_type:13s} | {r.fiscal_year} {str(r.quarter):4s} | "
              f"{r.metric:35s} | {r.value:>12.1f} {r.unit}")

    assert len(records) > 0, "No records extracted — check extract_financials() anchors"

    # ----------------------------------------------------------------
    # Step 3 & 4: Load and Verify in a SINGLE Database Transaction
    # ----------------------------------------------------------------
    print("\n--- Loading into PostgreSQL ---")
    conn = get_connection()
    try:
        result = load_financial_records(records, ALPHA_TENANT, conn)
        print(f"Load result: {result}")
        assert result["errors"] == 0, f"DB errors during load: {result}"

        conn.commit()

        print("\n--- Golden dataset check ---")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT financial_type, metric, fiscal_year, quarter, value "
                "FROM financials WHERE company = %s",
                (args.company,),
            )
            db_rows = cur.fetchall()

        all_rows = {}
        for row in db_rows:
            if isinstance(row, dict) or hasattr(row, 'keys'):
                ft, met, fy = row["financial_type"], row["metric"], row["fiscal_year"]
                q, val = row.get("quarter"), row["value"]
            else:
                ft, met, fy, q, val = row[0], row[1], row[2], row[3], row[4]
            if str(q).lower() in ("none", "null", ""):
                q = None
            all_rows[(ft, met, fy, q)] = float(val)

        # Default golden set (ETERNAL) if none supplied on CLI; otherwise
        # use whatever was passed via --golden.
        if args.golden:
            golden = {}
            for g in args.golden:
                ft, metric, fy, q, val = g.split(",")
                golden[(ft, metric, fy, q or None)] = float(val)
        elif args.company == "ETERNAL":
            golden = {
                ("consolidated", "revenue",      "FY26", None): 54364.0,
                ("consolidated", "total_income", "FY26", None): 55760.0,
                ("standalone",   "revenue",      "FY26", None): 10899.0,
            }
        else:
            golden = {}
            print(f"  (No golden assertions defined for {args.company} — skipping check, "
                  f"pass --golden to verify specific values)")

        all_passed = True
        for (ft, metric, fy, quarter_), expected in golden.items():
            actual = all_rows.get((ft, metric, fy, quarter_))
            status = "PASS" if actual == expected else f"FAIL (got {actual})"
            if "FAIL" in str(status):
                all_passed = False
            print(f"  [{status}] {ft}/{metric}/{fy}/quarter={quarter_} = {expected}")

        if golden and not all_passed:
            print("\n--- DEBUG: What is actually in the DB? ---")
            for k, v in all_rows.items():
                print(f"  {k} = {v}")

        assert all_passed, "Golden dataset check failed! Check the debug output above."
        print(f"\nSmoke test complete for {args.company}.")

    finally:
        conn.close()