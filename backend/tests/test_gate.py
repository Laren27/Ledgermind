"""
Unit tests — app.ingestion.gate.check_is_financial_filing

The gate scores raw extracted text against weighted regex signal categories and
returns ACCEPT/REJECT. It takes a string, so it is testable without a PDF --
the PDF-reading step (extract_first_n_pages_text) lives in the caller,
api/documents.py:88, and is out of scope here.

Text fixtures below are synthetic, written to exercise the scoring rules. None
is copied from a corpus filing.
"""
import pytest

from app.ingestion.gate import (
    MIN_CATEGORIES,
    MIN_SCORE,
    SCAN_CHAR_LIMIT,
    GateDecision,
    check_is_financial_filing,
)

# Synthetic filing front matter: hits four categories well past MIN_SCORE.
FILING_TEXT = (
    "Statement of Consolidated Financial Results for the quarter ended March 31, 2026\n"
    "Prepared in accordance with Ind AS 115. Auditor's Report attached.\n"
    "CIN: L74899DL1995PLC000000\n"
    "For and on behalf of the Board. Chartered Accountants.\n"
    "Pursuant to Regulation 33 of the SEBI LODR Regulations.\n"
    "Total income 17,634 crore. Balance Sheet and Segment Information follow."
)


class TestAccept:
    def test_filing_front_matter_is_accepted(self):
        result = check_is_financial_filing(FILING_TEXT)
        assert result.decision is GateDecision.ACCEPT
        assert result.score >= MIN_SCORE
        assert len(result.matched_categories) >= MIN_CATEGORIES

    def test_accept_reports_which_signals_fired(self):
        """The gate result is an audit record, not just a boolean."""
        result = check_is_financial_filing(FILING_TEXT)
        assert result.matched_signals
        assert "statement_type" in result.matched_categories
        assert "audit_and_accounting" in result.matched_categories

    def test_matching_is_case_insensitive(self):
        """gate.py:73 passes re.IGNORECASE, so an all-caps cover page scores."""
        assert (
            check_is_financial_filing(FILING_TEXT.upper()).decision
            is GateDecision.ACCEPT
        )


class TestReject:
    def test_empty_text_is_rejected_with_zero_score(self):
        result = check_is_financial_filing("")
        assert result.decision is GateDecision.REJECT
        assert result.score == 0
        assert result.matched_categories == []

    def test_unrelated_prose_is_rejected(self):
        result = check_is_financial_filing(
            "Add two cups of flour and bake for thirty minutes until golden."
        )
        assert result.decision is GateDecision.REJECT
        assert result.score == 0

    def test_single_weak_signal_fails_both_thresholds(self):
        result = check_is_financial_filing("Balance Sheet")
        assert result.decision is GateDecision.REJECT
        assert result.score == 2
        assert result.matched_categories == ["statement_type"]

    def test_two_categories_still_reject_when_score_is_short(self):
        """
        Both gates must pass: MIN_CATEGORIES is satisfied here but MIN_SCORE
        is not, and the decision is REJECT.
        """
        result = check_is_financial_filing("Balance Sheet and CIN : X")
        assert len(result.matched_categories) >= MIN_CATEGORIES
        assert result.score < MIN_SCORE
        assert result.decision is GateDecision.REJECT

    def test_rejection_reason_names_the_failing_threshold(self):
        result = check_is_financial_filing("Balance Sheet")
        assert "score 2" in result.reason
        assert str(MIN_SCORE) in result.reason


class TestScanWindow:
    """
    documents F9 — the gate reads only the first SCAN_CHAR_LIMIT characters,
    described in-code as "roughly the first 2 pages".

    An annual report whose front matter is a glossy cover, contents page and
    chairman's photograph may not reach a scoring signal before the window
    closes. The gate fails CLOSED (rejects at upload), which is the safe
    direction, but it rejects a legitimate filing rather than mis-ingesting it.
    """

    def test_signals_beyond_the_window_are_not_scored(self):
        padded = ("Annual Report. " * 1000)[:SCAN_CHAR_LIMIT] + FILING_TEXT
        result = check_is_financial_filing(padded)
        assert result.decision is GateDecision.REJECT
        assert result.score == 0

    def test_the_same_text_inside_the_window_is_accepted(self):
        """Confirms the rejection above is the window, not the text."""
        assert (
            check_is_financial_filing(FILING_TEXT).decision is GateDecision.ACCEPT
        )

    def test_window_boundary_is_the_documented_constant(self):
        assert SCAN_CHAR_LIMIT == 6000


class TestThresholdConstants:
    def test_documented_thresholds(self):
        assert MIN_SCORE == 6
        assert MIN_CATEGORIES == 2

    def test_decision_values(self):
        assert GateDecision.ACCEPT.value == "accept"
        assert GateDecision.REJECT.value == "reject"
