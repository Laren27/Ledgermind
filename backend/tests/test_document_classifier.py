"""
Unit tests — app.ingestion.document_classifier.detect_sections

detect_sections takes a list[PageBlock] and returns a list[DocSection]. It is
explicitly "pure section detection -- no DB" (the module's own section header),
and PageBlock is a plain dataclass, so a document layout can be written as a
literal. The `make_block` fixture in conftest.py builds them.

The register_sections/persist half of this module IS out of scope: it opens a
connection and writes rows.

The no-marker default is audit finding F12(b) and is asserted as observed.
"""
import pytest

from app.ingestion.document_classifier import detect_sections
from app.ingestion.models import BlockType, FinancialType

CONSOL = "Statement of Consolidated Financial Results for the quarter"
STAND = "Statement of Standalone Financial Results for the quarter"


def _tuples(sections):
    return [(s.financial_type, s.page_start, s.page_end) for s in sections]


class TestSingleMarker:
    def test_consolidated_marker_types_the_whole_document(self, make_block):
        sections = detect_sections([make_block(1, CONSOL), make_block(2, "figures")])
        assert _tuples(sections) == [(FinancialType.CONSOLIDATED, 1, 2)]

    def test_standalone_marker_types_the_whole_document(self, make_block):
        sections = detect_sections([make_block(1, STAND), make_block(2, "figures")])
        assert _tuples(sections) == [(FinancialType.STANDALONE, 1, 2)]

    def test_marker_matching_is_case_insensitive(self, make_block):
        """detect_sections lowercases block content before matching."""
        sections = detect_sections([make_block(1, CONSOL.upper()), make_block(2, "x")])
        assert _tuples(sections) == [(FinancialType.CONSOLIDATED, 1, 2)]


class TestTwoSections:
    def test_standalone_then_consolidated_splits_at_the_second_marker(self, make_block):
        sections = detect_sections([
            make_block(1, STAND),
            make_block(2, "standalone figures"),
            make_block(3, CONSOL),
            make_block(4, "consolidated figures"),
        ])
        assert _tuples(sections) == [
            (FinancialType.STANDALONE, 1, 2),
            (FinancialType.CONSOLIDATED, 3, 4),
        ]

    def test_consolidated_then_standalone_splits_at_the_second_marker(self, make_block):
        sections = detect_sections([
            make_block(1, CONSOL),
            make_block(2, "consolidated figures"),
            make_block(3, STAND),
            make_block(4, "standalone figures"),
        ])
        assert _tuples(sections) == [
            (FinancialType.CONSOLIDATED, 1, 2),
            (FinancialType.STANDALONE, 3, 4),
        ]

    def test_only_the_first_occurrence_of_each_marker_counts(self, make_block):
        """
        first_standalone_page / first_consolidated_page latch on first sight
        (document_classifier.py:137-144), so a later repeat of either marker
        does not move the boundary or add a third section.
        """
        sections = detect_sections([
            make_block(1, STAND),
            make_block(2, CONSOL),
            make_block(3, STAND),
            make_block(4, CONSOL),
        ])
        assert _tuples(sections) == [
            (FinancialType.STANDALONE, 1, 1),
            (FinancialType.CONSOLIDATED, 2, 4),
        ]

    def test_at_most_two_sections_are_ever_returned(self, make_block):
        """
        The model is one boundary, two sections. A filing that interleaves
        (consolidated statements, standalone statements, then notes to the
        consolidated accounts) cannot be represented.
        """
        sections = detect_sections([
            make_block(1, CONSOL), make_block(2, STAND),
            make_block(3, CONSOL), make_block(4, STAND),
        ])
        assert len(sections) <= 2


class TestAmbiguousAndAbsentMarkers:
    def test_both_markers_in_one_block_are_ignored(self, make_block):
        """
        A block carrying BOTH markers sets neither: each branch requires its
        own marker AND the absence of the other (document_classifier.py:137,
        142). With no marker latched, the no-marker default applies.
        """
        sections = detect_sections([
            make_block(1, f"{CONSOL} and also {STAND}"),
            make_block(2, "figures"),
        ])
        assert _tuples(sections) == [(FinancialType.CONSOLIDATED, 1, 2)]

    def test_no_marker_silently_defaults_to_consolidated(self, make_block):
        """
        documents F12(b) — the module docstring (line 16) claims this path
        "sets needs_review=True -- never silently defaults to wrong
        financial_type". It does neither: it types the whole document
        CONSOLIDATED and logs a warning. The in-code comment at lines 149-153
        records the same correction.

        Correct for a transcript or press release, which have no statutory
        financial_type. Indistinguishable, from here, from a filing whose
        markers failed to parse.
        """
        sections = detect_sections([
            make_block(1, "just some prose"),
            make_block(2, "more prose"),
        ])
        assert _tuples(sections) == [(FinancialType.CONSOLIDATED, 1, 2)]

    def test_markers_in_text_blocks_are_not_scanned(self, make_block):
        """
        Only TABLE blocks are scanned (document_classifier.py:128), so a
        press release mentioning "consolidated results" in a paragraph cannot
        set the section type. Here the marker is present but in a TEXT block,
        so the no-marker default applies instead.
        """
        sections = detect_sections([
            make_block(1, CONSOL, BlockType.TEXT),
            make_block(2, "prose", BlockType.TEXT),
        ])
        assert _tuples(sections) == [(FinancialType.CONSOLIDATED, 1, 2)]

    def test_empty_block_list_yields_an_inverted_page_range(self, make_block):
        """
        total_pages is 0 for an empty input (document_classifier.py:123), and
        the no-marker branch returns page_start=1, page_end=total_pages -- so
        an empty document produces one CONSOLIDATED section spanning pages
        1..0, a range containing no pages.

        No live victim: the ingestion pipeline never calls detect_sections on
        a parse that produced nothing. Recorded because the function returns a
        structurally invalid section rather than an empty list or an error.
        """
        sections = detect_sections([])
        assert _tuples(sections) == [(FinancialType.CONSOLIDATED, 1, 0)]


class TestPageNumbering:
    def test_page_end_tracks_the_highest_page_number_seen(self, make_block):
        sections = detect_sections([make_block(1, CONSOL), make_block(9, "later")])
        assert _tuples(sections) == [(FinancialType.CONSOLIDATED, 1, 9)]

    def test_blocks_are_ordered_by_page_not_by_list_position(self, make_block):
        """
        detect_sections sorts table blocks by page_number
        (document_classifier.py:130), so parser output arriving out of order
        still yields the correct boundary.
        """
        sections = detect_sections([
            make_block(3, CONSOL),
            make_block(1, STAND),
            make_block(4, "figures"),
        ])
        assert _tuples(sections) == [
            (FinancialType.STANDALONE, 1, 2),
            (FinancialType.CONSOLIDATED, 3, 4),
        ]
