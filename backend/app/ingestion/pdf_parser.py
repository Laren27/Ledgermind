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

            has_balance_sheet_anchors = bool(re.search(
                r"(statement of assets and liabilities|total assets|"
                r"total equity and liabilities)",
                text_lower
            ))

            if (has_table_borders or has_financial_header_markers or has_numeric_dates
              or has_pnl_anchors or has_balance_sheet_anchors):
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


def _apply_typo_map(text: str) -> str:
    """
    Apply TYPO_MAP corrections (word-boundary matches only). Shared by both
    parse_financial_line() (legacy whitespace path) and
    extract_financials_positional() — previously this was only wired into
    the legacy path, so descriptions built positionally (e.g. "Total incomc
    (1+11)") never got OCR-typo-corrected, causing avoidable metric-name
    misses downstream (normalize_metric() sees "incomc" instead of "income").
    """
    for wrong, right in sorted(TYPO_MAP.items(), key=lambda kv: -len(kv[0])):
        pattern = r'\b' + re.escape(wrong) + r'\b'
        text = re.sub(pattern, right, text)
    return text


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
    line = _apply_typo_map(line)

    # Strip leading Roman numeral / letter prefixes: "I.", "V.", "(a)" etc.
    line = re.sub(r'^(I{1,3}|IV|V|VI{1,3}|IX|X)\.?\s+', '', line)

    # OCR sometimes misreads the leading "1" of a thousand-grouped number as
    # the capital letter "I" (e.g. "1,960" -> "I 960" after the comma is also
    # lost as a space). Left uncorrected, "I" fails _VALUE_TOKEN_RE and BREAKS
    # the right-to-left value-token scan below — every value token to the left
    # of it silently gets misread as part of the description, corrupting the
    # entire row's column alignment (root cause of the ETERNAL FY25/FY26 PAT
    # swap — see regression_check.py golden assertions). Scoped narrowly to
    # "I" flanked by a space and exactly 3 digits, so it cannot match genuine
    # Roman-numeral row labels (those never sit directly next to a bare
    # 3-digit group in this exact shape).
    line = re.sub(r'\bI(?=\s\d{3}(?:\s|$))', '1', line)

    # OCR sometimes misreads the leading "1" of a thousand-grouped number as
    # the capital letter "I" (e.g. "1,960" -> "I 960" after the comma is also
    # lost as a space). Left uncorrected, "I" fails _VALUE_TOKEN_RE and BREAKS
    # the right-to-left value-token scan below — every value token to the left
    # of it silently gets misread as part of the description, corrupting the
    # entire row's column alignment (root cause of the ETERNAL FY25/FY26 PAT
    # swap — see regression_check.py golden assertions). Scoped narrowly to
    # "I" flanked by a space and exactly 3 digits, so it cannot match genuine
    # Roman-numeral row labels (those never sit directly next to a bare
    # 3-digit group in this exact shape).
    line = re.sub(r'\bI(?=\s\d{3}(?:\s|$))', '1', line)

    # RESTORED: merge OCR-artifact spaces inside Indian comma-grouped numbers
    # ("16 663" -> "16663"). Must run BEFORE line.split() below — without
    # this, a single mis-extracted comma-as-space corrupts the entire
    # column count for the rest of the row. This was present in the
    # original ETERNAL-verified parser and was accidentally dropped when
    # switching to bare line.split() for Titan's single-space columns.
    #
    # NOTE: this text-only merge is fundamentally ambiguous — it cannot
    # distinguish a genuinely broken thousands number ("2 655" -> "2,655")
    # from two independent short column values that happen to sit next to
    # each other ("705 657" -- two separate values). A tightened version of
    # this regex was tried and caused a real regression (fixed one row,
    # corrupted another). The actual fix for that ambiguity is
    # extract_financials_positional() below, which resolves it correctly
    # using column x-position instead of text. This function
    # (parse_financial_line/extract_financials) remains ONLY as a fallback
    # for pages where column-layout detection fails entirely and no
    # column_centers are available — do not re-attempt to fix the ambiguity
    # here with more regex; use the positional path instead.
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


# ---------------------------------------------------------------------------
# Positional extraction — replaces whitespace tokenization for pages where a
# column layout (column_centers) is available.
#
# WHY THIS EXISTS: parse_financial_line() (above) tokenizes on whitespace and
# cannot distinguish "one OCR-broken number with a lost comma" (e.g. "2 655"
# meaning "2,655") from "two independent adjacent column values that happen
# to be short" (e.g. "705 657" -- two separate values, not one). Both produce
# identical token sequences under pure text splitting; no regex on the
# flattened text can resolve this correctly in all cases (confirmed via a
# real regression: a tightened digit-merge regex fixed one row and corrupted
# another in the same document — see financial_extractor.py's column-layout
# detection notes).
#
# Bucketing each numeric word by its x-position against the KNOWN column
# centers (already computed by column-layout detection for the header row)
# resolves this correctly: two independent values fall into two different
# column x-ranges, while fragments of one OCR-broken number fall into the
# SAME column x-range and get concatenated in x0 order.
# ---------------------------------------------------------------------------

_NUMERIC_WORD_RE = re.compile(r"^\(?-?[\d,]*\.?\d+\)?$|^-$|^I$")


def _is_numeric_word(text: str) -> bool:
    """
    Word-level numeric check (looser than _VALUE_TOKEN_RE since pdfplumber
    words never span spaces, so fragments like "2" or "655" or a lone "I"
    -- OCR misread of "1" -- must each independently qualify).
    """
    return bool(_NUMERIC_WORD_RE.match(text.strip()))


def extract_financials_positional(pdf_path, page_index, column_centers, tolerance=None):
    """
    Row-value extraction using physical x-position instead of whitespace
    tokenization. Use this instead of extract_financials() whenever
    column_centers is available (i.e. whenever column-layout detection
    succeeded for this page).

    column_centers: list[float], the x-center of each detected column header,
        in left-to-right order, aligned with the column_map returned
        alongside it by detect_column_layout() in financial_extractor.py.
    tolerance: max x-distance (in PDF points) a word's center may be from a
        column center to be claimed by that column. If None (default),
        computed ADAPTIVELY from the actual gaps between adjacent column
        centers: 0.95 * (half the smallest gap). This matters because
        header text is typically centered in its column while numeric
        values are often right-aligned within the same column width,
        producing a systematic rightward offset (~25pt observed on a real
        ETERNAL standalone table) that a fixed tolerance tuned for one
        document can silently miss on another — three of five values in
        one row missed a fixed tolerance=25.0 by margins as small as 0.03pt,
        each one falling through into the row's description text instead
        of being recognized as a value (root cause of e.g.
        "depreciation_and_amortisation_expenses_55_29_97" metric-name
        pollution). The 0.95 safety factor guarantees adjacent columns'
        tolerance zones never overlap (2*tolerance < gap), so widening
        tolerance can never cause cross-column bleed.
    """
    if tolerance is None:
        if len(column_centers) >= 2:
            gaps = [abs(column_centers[i + 1] - column_centers[i])
                    for i in range(len(column_centers) - 1)]
            tolerance = 0.95 * (min(gaps) / 2)
        else:
            tolerance = 25.0  # fallback for single-column layouts
    financial_data = []
    parsing_started = False

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        words = page.extract_words()

    if not words:
        return financial_data

    # Group words into rows by top-coordinate. Tight tolerance (single text
    # line) — NOT the 30px header-clustering tolerance used elsewhere, since
    # here we want each physical row of the table kept separate.
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

    for row_top in sorted(rows.keys()):
        row_words = sorted(rows[row_top], key=lambda w: w["x0"])
        row_text_lower = " ".join(w["text"] for w in row_words).lower()

        if not parsing_started:
            if "revenue" in row_text_lower or "income" in row_text_lower or "sale" in row_text_lower:
                parsing_started = True
            else:
                continue

        desc_words = []
        buckets = [[] for _ in column_centers]

        for w in row_words:
            text = w["text"].strip()
            cleaned = text.strip("()")
            if _is_numeric_word(cleaned) or cleaned == "-":
                center = (w["x0"] + w["x1"]) / 2
                distances = [abs(center - c) for c in column_centers]
                best_idx = distances.index(min(distances))
                if distances[best_idx] <= tolerance:
                    buckets[best_idx].append(w)
                    continue
            desc_words.append(w["text"])

        non_empty = [b for b in buckets if b]
        if len(non_empty) < MIN_VALUE_COLUMNS:
            continue

        description = " ".join(desc_words).strip()
        if not description:
            continue
        description = _apply_typo_map(description)

        values = []
        for bucket in buckets:
            if not bucket:
                values.append(None)
                continue
            bucket_sorted = sorted(bucket, key=lambda w: w["x0"])
            texts = [w["text"].strip() for w in bucket_sorted]

            # If any fragment in this bucket already contains a comma, it is
            # a COMPLETE, correctly-extracted number (pdfplumber did not lose
            # anything for it) — trust it alone and discard any other stray
            # token sharing the bucket (e.g. a nearby border/rule artifact
            # that happens to sit close enough to this column to be claimed,
            # such as a spurious standalone "I"). Concatenating a stray token
            # onto an already-complete number is what corrupted "7,292" into
            # "17292" — see regression note in financial_extractor.py.
            #
            # Only when NO fragment has a comma do we treat this as a
            # genuine OCR-broken number (comma lost, split across words) and
            # concatenate all fragments in x0 order.
            comma_fragments = [t for t in texts if ',' in t]
            if comma_fragments:
                fragment = max(comma_fragments, key=len)
            else:
                fragment = "".join("1" if t == "I" else t for t in texts)

            values.append(clean_financial_number(fragment))

        financial_data.append([description] + values)

    return financial_data