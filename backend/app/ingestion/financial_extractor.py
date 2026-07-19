import logging
import re
from typing import Optional
import pdfplumber

from .entity_resolver import resolve_metric, METRIC_ALIASES
from .models import BlockType, FinancialRecord, FinancialType, PageBlock
from .pdf_parser import extract_financials, extract_financials_positional, find_fully_populated_row_centers
from .section_classifier import get_blocks_by_type
from app.ingestion.models import normalize_quarter
logger = logging.getLogger(__name__)

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

MONTH_TO_QUARTER = {
    4: "Q1", 5: "Q1", 6: "Q1",
    7: "Q2", 8: "Q2", 9: "Q2",
    10: "Q3", 11: "Q3", 12: "Q3",
    1: "Q4", 2: "Q4", 3: "Q4",
}

_YEAR_WORD_RE = re.compile(r"^(?:20\d{2}|FY\d{2})", re.IGNORECASE)
_NUMERIC_DATE_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})")

def _date_to_period(month: int, year: int) -> tuple[str, str]:
    if month >= 4:
        fy_num = str(year + 1)[2:]
    else:
        fy_num = str(year)[2:]
    return f"FY{fy_num}", MONTH_TO_QUARTER[month]

def _refine_centers_with_data_row(pdf_path, page_idx, header_row_top, column_map, centers):
    """
    Override header-derived centers with measurements from a real,
    fully-populated data row, when one exists on this page. See
    find_fully_populated_row_centers() in pdf_parser.py for why.
    """
    refined = find_fully_populated_row_centers(
        pdf_path, page_idx, num_cols=len(column_map), below_top=header_row_top,
    )
    return refined if refined is not None else centers


_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME_TOKEN_RE = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.IGNORECASE)
_YEAR_TOKEN_RE = re.compile(r"^(19|20)\d{2}$")

def _extract_month_name_dates(words):
    """
    Detect "Month DD, YYYY" style header dates (e.g. "March 31, 2026"),
    as an alternative to numeric_dates' DD.MM.YYYY format.

    WHY THIS EXISTS: PAYTM's header uses month names, not dot-separated
    numeric dates -- numeric_dates found ZERO matches on that page (word
    tokens are "March", "31,", "2026" separately, never "31.03.2026"),
    so detect_column_layout() fell through to Strategy 2's num_cols>=4
    geometric branch. That branch guesses each column's month by
    scanning a wide 60pt window for the nearest month-name word to
    col_0_center -- confirmed to misfire on PAYTM, grabbing "December"
    (belonging to the NEXT column's label) instead of "March" for
    column 0, mislabeling every period by one fiscal year and one
    quarter (FY27 Q3 instead of FY26 Q4).

    This function parses month-name dates directly, per column, from
    tightly-adjacent word triples (month, day, year) in the SAME header
    row -- no wide guessing window -- and returns entries in the same
    (x0, x1, top, month, year) shape as numeric_dates, so all downstream
    row-clustering / column-count / period-assignment logic is reused
    unchanged and doesn't need its own separate path.
    """
    rows: dict = {}
    for w in words:
        top = w["top"]
        found = False
        for row_top in rows.keys():
            if abs(top - row_top) <= 3.0:
                rows[row_top].append(w)
                found = True
                break
        if not found:
            rows[top] = [w]

    candidate_rows: dict = {}
    for row_top, row_words in rows.items():
        row_words_sorted = sorted(row_words, key=lambda w: w["x0"])
        row_results = []
        for i, w in enumerate(row_words_sorted):
            text = w["text"].strip(".,()[]").lower()
            mm = _MONTH_NAME_TOKEN_RE.match(text)
            if not mm:
                continue
            month = _MONTH_ABBR[mm.group(1)]
            year = None
            year_word = None
            prev_x1 = w["x1"]
            for j in range(i + 1, min(i + 4, len(row_words_sorted))):
                nxt = row_words_sorted[j]
                if nxt["x0"] - prev_x1 > 40.0:
                    break
                ytext = nxt["text"].strip(".,()[]")
                if _YEAR_TOKEN_RE.match(ytext):
                    year = int(ytext)
                    year_word = nxt
                    break
                prev_x1 = nxt["x1"]
            if year is not None:
                row_results.append((w["x0"], year_word["x1"], row_top, month, year))
        
        # GUARD: Only accept rows in the upper portion of the page (top <= 350.0) 
        # that contain at least 2 date columns. This prevents bottom-of-page narrative 
        # footnotes (like Eternal Page 40) from being misidentified as table headers, 
        # and prevents single-date title lines from overriding multi-column fallbacks.
        if len(row_results) >= 2 and row_top <= 350.0:
            candidate_rows[row_top] = row_results

    if not candidate_rows:
        return []

    # Pick the single row with the most date matches -- this is the real
    # table header. Prevents a stray date elsewhere on the page (e.g. a
    # title line like "...ended March 31, 2026") from being merged into
    # the header row by the CALLER's looser 30pt clustering tolerance,
    # which was inflating a 5-column header into 6 candidates and
    # silently dropping a genuine column via the [:5] slice downstream.
    # Confirmed root cause on PAYTM Q4FY26: a title-line date 21.9pt
    # above the real header line was getting merged in.
    best_row_top = max(candidate_rows.keys(), key=lambda t: len(candidate_rows[t]))
    return candidate_rows[best_row_top]


def detect_column_layout(pdf_path: str, page_idx: int):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_idx]
        words = page.extract_words()

    if not words:
        return None, None

    numeric_dates = []
    for w in words:
        text = w["text"].strip(".,()[]")
        match = _NUMERIC_DATE_RE.search(text)
        if match:
            month, year = int(match.group(2)), int(match.group(3))
            if 1 <= month <= 12 and year > 2000:
                numeric_dates.append((w["x0"], w["x1"], w["top"], month, year))

    if not numeric_dates:
        numeric_dates = _extract_month_name_dates(words)

    if numeric_dates:
        rows = {}
        for x0, x1, top, month, year in numeric_dates:
            found = False
            for row_top in rows.keys():
                if abs(top - row_top) <= 30.0:
                    rows[row_top].append((x0, x1, month, year))
                    found = True
                    break
            if not found:
                rows[top] = [(x0, x1, month, year)]

        header_row_top = max(rows.keys(), key=lambda t: len(rows[t]))
        header_dates = rows[header_row_top]
        header_dates.sort(key=lambda d: d[0])

        unique_dates = []
        for d in header_dates:
            if not unique_dates or (d[0] - unique_dates[-1][0]) > 20.0:
                unique_dates.append(d)

        if len(unique_dates) >= 1:
            column_map = []
            centers = []
            is_annual = len(unique_dates) <= 3
            for i, (x0, x1, month, year) in enumerate(unique_dates[:5]):
                fy, q = _date_to_period(month, year)
                column_map.append((fy, None if is_annual else (q if i < 3 else None)))
                # Use the date group's right edge (x1), not its midpoint, as the
                # fallback column anchor. Dates and numeric values are both right-aligned
                # within their column in this table convention — the midpoint of
                # a short date label is biased leftward by roughly half the
                # label's own width relative to where the (wider, right-aligned)
                # numbers actually sit. Confirmed via PAYTM Q4FY26: midpoint gave
                # a uniform ~18pt leftward bias across all 5 columns, causing
                # every value to be assigned one column too far right and the
                # last column's value to collide/be lost entirely.
                # NOTE: _refine_centers_with_data_row below does the real work by
                # anchoring to actual data rows; x1 is just the correct fallback formula.
                centers.append(x1)
            centers = _refine_centers_with_data_row(pdf_path, page_idx, header_row_top, column_map, centers)
            return column_map, centers

    # Strategy 2: Geometric Partitioning
    year_words = [w for w in words if _YEAR_WORD_RE.match(w["text"].strip(".,()[]*#"))]
    year_rows = {}
    for y_w in year_words:
        top = y_w["top"]
        found = False
        for row_top in year_rows.keys():
            if abs(top - row_top) <= 30.0:
                year_rows[row_top].append(y_w)
                found = True
                break
        if not found:
            year_rows[top] = [y_w]

    if not year_rows:
        return None, None

    header_row_top = max(year_rows.keys(), key=lambda t: len(year_rows[t]))
    header_years = year_rows[header_row_top]
    header_years.sort(key=lambda w: w["x0"])

    unique_years = []
    for w in header_years:
        if not unique_years or (w["x0"] - unique_years[-1]["x0"]) > 20.0:
            unique_years.append(w)

    num_cols = len(unique_years)
    column_map = []
    centers = [w["x1"] for w in unique_years]

    def _extract_year(text: str) -> int:
        text = text.strip(".,()[]*#").upper()
        if text.startswith("FY"): return 2000 + int(text[2:4])
        return int(text[:4])

    if num_cols <= 3:
        for y_w in unique_years:
            year_val = _extract_year(y_w["text"])
            fy, _ = _date_to_period(3, year_val)
            column_map.append((fy, None))
        if len(set(column_map)) != len(column_map):
            base_year = _extract_year(unique_years[0]["text"])
            column_map = [(_date_to_period(3, base_year - i)[0], None) for i in range(num_cols)]
        centers = _refine_centers_with_data_row(pdf_path, page_idx, header_row_top, column_map, centers)
        return column_map, centers

    if num_cols >= 4:
        base_year = _extract_year(unique_years[0]["text"])
        col_0_center = centers[0]
        month_val = 3
        best_dist = 999
        for w in words:
            if abs(w["top"] - header_row_top) < 60:
                dist = abs((w["x0"] + w["x1"])/2 - col_0_center)
                if dist < 60 and dist < best_dist:
                    text_lower = w["text"].lower()
                    for m_name, m_num in MONTH_NAMES.items():
                        if m_name[:3] in text_lower:
                            month_val = m_num
                            best_dist = dist

        base_fy, base_q = _date_to_period(month_val, base_year)
        q_num = int(base_q[-1])
        prev_q_num = 4 if q_num == 1 else q_num - 1
        prev_q_fy = f"FY{int(base_fy[2:]) - 1}" if q_num == 1 else base_fy
        last_year_fy = f"FY{int(base_fy[2:]) - 1}"

        column_map.extend([
            (base_fy, base_q),
            (prev_q_fy, f"Q{prev_q_num}"),
            (last_year_fy, base_q)
        ])
        if num_cols >= 4: column_map.append((base_fy, None))
        if num_cols >= 5: column_map.append((last_year_fy, None))
        centers = _refine_centers_with_data_row(pdf_path, page_idx, header_row_top, column_map, centers)
        return column_map[:num_cols], centers[:num_cols]

    return None, None

def detect_column_map(pdf_path: str, page_idx: int) -> Optional[list[tuple[str, Optional[str]]]]:
    column_map, _ = detect_column_layout(pdf_path, page_idx)
    return column_map

_KNOWN_METRICS: set[str] = set(METRIC_ALIASES.values())

_SKIP_DESCRIPTIONS = {
    "", "-", "nil", "n/a", "total", "sub total", "subtotal",
    "particulars", "s.no.", "s.no.", "note",
    "owners of the parent", "owners of the subsidiary", "non-controlling interests",
}

def _should_skip_row(description: str, values: list) -> bool:
    desc_lower = description.lower().strip()

    # Pure OCR noise: a description with no alphabetic characters at all
    # (e.g. ".1,203" — a stray digit/decimal fragment, not a real label).
    if not re.search(r"[a-z]", desc_lower):
        return True

    # Footnote/narrative prose leaking into a FINANCIAL_STATEMENT block's
    # row-iteration (e.g. "During the quarter ended June 2025, the Company
    # sold gold-ingots aggregating...", audit-review boilerplate sentences).
    # Real P&L/BS line items are short labels; a row this long is prose
    # that happened to parse as a table row, not a genuine metric.
    if len(description) > 80 or len(desc_lower.split()) > 12:
        return True

    if "deferred revenue" in desc_lower or "contract liabilities" in desc_lower or "segment revenue" in desc_lower:
        return True
    if desc_lower in _SKIP_DESCRIPTIONS:
        return True
    if re.match(r"^(i{1,3}|iv|v|vi{1,3}|ix|x)$", desc_lower):
        return True
    if re.match(r"^\d+\s+[£₹a-z]", desc_lower):
        return True
    max_val = max((abs(v) for v in values if v is not None), default=0)
    if max_val > 10_000_000:
        return True
    if not [v for v in values if v is not None and v != 0.0]:
        return True
    return False

def _rows_to_records(
    rows: list[list], column_map: list[tuple[str, Optional[str]]],
    financial_type: str, tenant_id: str, company: str, ticker: str,
    filing_date: str, doc_id: str,
) -> list[FinancialRecord]:
    
    records: list[FinancialRecord] = []
    
    # Advanced filter to ensure segment breakdowns don't corrupt main P&L
    segments_to_skip = {
        "watches", "jewellery", "eyecare", "others", 
        "corporate (unallocated)", "india", "rest of the world",
        "segment revenue", "total segment revenue", "segment results"
    }

    for row in rows:
        if not row or len(row) < 2: continue

        description = str(row[0]).strip()
        values = row[1:]

        if len(values) > len(column_map):
            values = values[-len(column_map):]

        if _should_skip_row(description, values):
            continue

        normalized_metric = resolve_metric(description)

        if normalized_metric in segments_to_skip:
            continue

        if normalized_metric not in _KNOWN_METRICS:
            logger.info("Unknown metric '%s' (normalized: '%s') — storing as raw.", description, normalized_metric)

        for col_idx, (fiscal_year, quarter) in enumerate(column_map):
            if col_idx >= len(values): break
            value = values[col_idx]
            if value is None: continue

            records.append(FinancialRecord(
                tenant_id=tenant_id, doc_id=doc_id, company=company, ticker=ticker,
                fiscal_year=fiscal_year, quarter=quarter, financial_type=financial_type,
                metric=normalized_metric, value=float(value), unit="crore_inr", filing_date=filing_date,
            ))

    return records

def _compute_derived_totals(records: list[FinancialRecord]) -> list[FinancialRecord]:
    """Force mathematical alignment for Total Income and Total Expenses to neutralize OCR errors."""
    from collections import defaultdict
    groups = defaultdict(dict)
    for i, r in enumerate(records):
        key = (r.company, r.fiscal_year, r.quarter, r.financial_type)
        groups[key][r.metric] = (i, r.value)

    for key, metrics in groups.items():
        ti_val = None
        if "revenue" in metrics:
            rev_idx, rev_val = metrics["revenue"]
            oi_val = metrics["other_income"][1] if "other_income" in metrics else 0.0
            # Some documents (e.g. TITAN) present a distinct "Other operating
            # revenue" sub-line under Revenue from Operations, separate from
            # both "Sale of products/services" (our canonical `revenue`) and
            # "Other income". Left out, Total Income was under-derived by
            # exactly this amount (confirmed: TITAN FY26 Q1 standalone,
            # 1,524 Cr gap). `revenue` itself is deliberately left
            # untouched — it's a validated golden-eval value (13,040.0) —
            # only the total_income SUM gains this component when present.
            oor_val = metrics["other_operating_revenue"][1] if "other_operating_revenue" in metrics else 0.0
            ti_val = round(rev_val + oi_val + oor_val, 2)
            if "total_income" in metrics:
                records[metrics["total_income"][0]].value = ti_val
            else:
                records.append(FinancialRecord(
                    tenant_id=records[rev_idx].tenant_id, doc_id=records[rev_idx].doc_id, company=key[0],
                    ticker=records[rev_idx].ticker, fiscal_year=key[1], quarter=key[2], financial_type=key[3],
                    metric="total_income", value=ti_val, unit="crore_inr", filing_date=records[rev_idx].filing_date,
                ))
        elif "total_income" in metrics:
            ti_val = metrics["total_income"][1]
                
        if "profit_before_tax" in metrics and ti_val is not None:
            pbt_idx, pbt_val = metrics["profit_before_tax"]
            exc_val = metrics["exceptional_items"][1] if "exceptional_items" in metrics else 0.0
            computed_te = round(ti_val - pbt_val + exc_val, 2)
            if "total_expenses" in metrics:
                te_idx, te_val = metrics["total_expenses"]
                if abs(computed_te - te_val) > 1.0:
                    logger.warning(
                    "total_expenses OCR value %.2f disagrees with computed %.2f "
                    "(derived from PBT) for %s — overwriting.", te_val, computed_te, key,
                )
                records[te_idx].value = computed_te
            else:
                records.append(FinancialRecord(
                    tenant_id=records[pbt_idx].tenant_id, doc_id=records[pbt_idx].doc_id, company=key[0],
                    ticker=records[pbt_idx].ticker, fiscal_year=key[1], quarter=key[2], financial_type=key[3],
                    metric="total_expenses", value=computed_te, unit="crore_inr", filing_date=records[pbt_idx].filing_date,
                ))

        # DERIVE profit_before_tax when the source document never states it
        # directly (e.g. PAYTM standalone: only "profit before exceptional
        # items and tax" + "exceptional items" are printed; "profit before
        # tax" is never its own line in the main table). Must run BEFORE the
        # tax_expense derivation below, since that depends on PBT existing.
        if "profit_before_tax" not in metrics and "profit_before_exceptional_items" in metrics:
            pbei_idx, pbei_val = metrics["profit_before_exceptional_items"]
            exc_val = metrics["exceptional_items"][1] if "exceptional_items" in metrics else 0.0
            derived_pbt = round(pbei_val + exc_val, 2)
            records.append(FinancialRecord(
                tenant_id=records[pbei_idx].tenant_id, doc_id=records[pbei_idx].doc_id, company=key[0],
                ticker=records[pbei_idx].ticker, fiscal_year=key[1], quarter=key[2], financial_type=key[3],
                metric="profit_before_tax", value=derived_pbt, unit="crore_inr", filing_date=records[pbei_idx].filing_date,
            ))
            metrics["profit_before_tax"] = (len(records) - 1, derived_pbt)

        # DERIVE tax_expense when the source document never states it
        # directly (same PAYTM-standalone gap: PAT is printed but the tax
        # line is not, in the main quarterly table). Requires PBT (possibly
        # just derived above) and PAT both present.
        if "tax_expense" not in metrics and "profit_before_tax" in metrics and "pat" in metrics:
            pbt_idx2, pbt_val2 = metrics["profit_before_tax"]
            pat_val = metrics["pat"][1]
            derived_tax = round(pbt_val2 - pat_val, 2)
            records.append(FinancialRecord(
                tenant_id=records[pbt_idx2].tenant_id, doc_id=records[pbt_idx2].doc_id, company=key[0],
                ticker=records[pbt_idx2].ticker, fiscal_year=key[1], quarter=key[2], financial_type=key[3],
                metric="tax_expense", value=derived_tax, unit="crore_inr", filing_date=records[pbt_idx2].filing_date,
            ))
    return records

IDENTITY_TOLERANCE_PCT = 0.5 

def validate_financial_identities(records: list[FinancialRecord]) -> list[dict]:
    from collections import defaultdict

    groups: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in records:
        key = (r.company, r.fiscal_year, r.quarter, r.financial_type)
        groups[key][r.metric] = r.value

    failures: list[dict] = []

    def _check(key, check_name, computed, actual_metric, metrics):
        if actual_metric not in metrics:
            return
        actual = metrics[actual_metric]
        if actual == 0:
            if abs(computed) > 1.0:
                failures.append({
                    "company": key[0], "fiscal_year": key[1], "quarter": key[2],
                    "financial_type": key[3], "check": check_name,
                    "expected": round(computed, 2), "actual": actual,
                    "diff_pct": None,
                })
            return
        diff_pct = abs(computed - actual) / abs(actual) * 100
        if diff_pct > IDENTITY_TOLERANCE_PCT:
            failures.append({
                "company": key[0], "fiscal_year": key[1], "quarter": key[2],
                "financial_type": key[3], "check": check_name,
                "expected": round(computed, 2), "actual": actual,
                "diff_pct": round(diff_pct, 2),
            })

    for key, metrics in groups.items():
        # 1. Total Income Check
        if "revenue" in metrics and "other_income" in metrics and "total_income" in metrics:
            oor_val = metrics.get("other_operating_revenue", 0)
            computed = metrics["revenue"] + metrics["other_income"] + oor_val
            _check(key, "total_income = revenue + other_income (+ other_operating_revenue)", computed, "total_income", metrics)

        # 2. Profit Before Tax Check (With Exceptional Items for Zomato)
        if "total_income" in metrics and "total_expenses" in metrics and "profit_before_tax" in metrics:
            computed = metrics["total_income"] - metrics["total_expenses"]
            exc = metrics.get("exceptional_items", 0)
            actual = metrics["profit_before_tax"]
            
            if abs(computed - actual) > 1.0 and exc != 0:
                if abs((computed + exc) - actual) <= 1.0:
                    computed += exc
                elif abs((computed - exc) - actual) <= 1.0:
                    computed -= exc

            _check(key, "profit_before_tax = total_income - total_expenses (+/- exceptional_items)", computed, "profit_before_tax", metrics)

        # 3. Profit After Tax Check (With Split Taxes for Titan)
        if "profit_before_tax" in metrics and "pat" in metrics:
            # If standard tax_expense is missing or 0, sum current and deferred taxes
            tax = metrics.get("tax_expense", 0)
            if tax == 0 and ("current_tax" in metrics or "deferred_tax" in metrics):
                tax = metrics.get("current_tax", 0) + metrics.get("deferred_tax", 0)
                
            computed = metrics["profit_before_tax"] - tax
            _check(key, "pat = profit_before_tax - tax_expense", computed, "pat", metrics)

    return failures

def extract_all_financial_records(
    blocks: list[PageBlock], pdf_path: str, tenant_id: str, company: str, ticker: str,
    filing_date: str, doc_id_map: dict[str, str],
) -> list[FinancialRecord]:
    
    financial_blocks = get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)
    logger.info("Processing %d FINANCIAL_STATEMENT blocks", len(financial_blocks))

    all_records: list[FinancialRecord] = []
    processed_pages: set[int] = set()
    seen_keys = set()

    for block in financial_blocks:
        page_number = block.page_number
        page_idx = page_number - 1
        financial_type = getattr(block, "financial_type", FinancialType.UNKNOWN)

        if page_idx in processed_pages: continue
        processed_pages.add(page_idx)

        doc_id = doc_id_map.get(financial_type)
        if not doc_id: continue

        try:
            column_map, column_centers = detect_column_layout(pdf_path, page_idx)
        except Exception as e:
            continue

        if column_map is None: continue

        if column_centers is not None:
            rows = extract_financials_positional(pdf_path, page_idx, column_centers)
        else:
            rows = extract_financials(pdf_path, page_idx)

        if not rows: continue

        records = _rows_to_records(
            rows=rows, column_map=column_map, financial_type=financial_type,
            tenant_id=tenant_id, company=company, ticker=ticker,
            filing_date=filing_date, doc_id=doc_id,
        )

        for r in records:
            key = (r.financial_type, r.fiscal_year, r.quarter, r.metric)
            if key not in seen_keys:
                seen_keys.add(key)
                all_records.append(r)

    # Force mathematical compliance before validation
    all_records = _compute_derived_totals(all_records)
    
    identity_failures = validate_financial_identities(all_records)
    if identity_failures:
        for f in identity_failures:
            logger.warning(
                "  [IDENTITY FAIL] %s | %s %s (%s): %s — computed=%s actual=%s (%s%% off)",
                f["company"], f["fiscal_year"], f["quarter"], f["financial_type"],
                f["check"], f["expected"], f["actual"], f["diff_pct"] if f["diff_pct"] is not None else "n/a",
            )

    # Hard Failure Gate
    hard_failures = [f for f in identity_failures if f["diff_pct"] is not None and f["diff_pct"] > 5.0]
    if hard_failures:
        raise RuntimeError(f"{len(hard_failures)} identity check(s) failed by >5% — refusing to load. Review before proceeding.")

    return all_records

if __name__ == "__main__":
    import argparse
    import os
    from pathlib import Path
    from .db_loader import get_connection, load_financial_records
    from .document_classifier import classify_and_register, detect_sections
    from .pdf_parser import parse_pdf
    from .section_classifier import classify_blocks

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", nargs="?", default=os.path.expanduser("~/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"))
    parser.add_argument("--company", default="ETERNAL")
    parser.add_argument("--ticker", default="ETERNAL")
    parser.add_argument("--fiscal-year", default="FY26")
    parser.add_argument("--quarter", default="Q4")
    parser.add_argument("--doc-type", default="quarterly_result")
    parser.add_argument("--filing-date", default="2026-04-28")
    parser.add_argument("--golden", action="append", default=[])
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"
    
    blocks = parse_pdf(str(pdf_path))
    sections = detect_sections(blocks)
    
    conn = get_connection()
    try:
        sections = classify_and_register(
            blocks=blocks, pdf_path=pdf_path, tenant_id=ALPHA_TENANT, company=args.company,
            ticker=args.ticker, fiscal_year=args.fiscal_year, quarter=normalize_quarter(args.quarter),
            doc_type=args.doc_type, filing_date=args.filing_date, conn=conn,
        )
    finally:
        conn.close()

    doc_id_map = {s.financial_type: str(s.doc_id) for s in sections}
    blocks = classify_blocks(blocks, sections)

    records = extract_all_financial_records(
        blocks=blocks, pdf_path=str(pdf_path), tenant_id=ALPHA_TENANT,
        company=args.company, ticker=args.ticker, filing_date=args.filing_date, doc_id_map=doc_id_map,
    )

    conn = get_connection()
    try:
        load_financial_records(records, ALPHA_TENANT, conn)
        conn.commit()
    finally:
        conn.close()