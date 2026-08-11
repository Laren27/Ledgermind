"""
Unit tests — app.engines.contradiction

Covers extract_numeric_claims, extract_direction, _classify_severity and
has_approximation_language. All four take a string (plus an optional compiled
regex) and return plain values.

The detect_* entry points are NOT covered: they take List[ChunkResult] with
reranker scores and speaker roles attached, and asserting on them means
building retrieval output by hand -- a fixture describing the shape of a chunk
rather than a behaviour of the detector. The extraction primitives they compose
are where the parsing decisions live.

This module is the one whose failure mode inverts the system's stated value: a
FALSE contradiction is worse than a missed one. Several tests below pin the
places where the code deliberately declines to decide.
"""
import pytest

from app.engines.contradiction import (
    PROXIMITY_WINDOW,
    SEVERITY_HIGH_PCT,
    SEVERITY_MEDIUM_PCT,
    _classify_severity,
    _metric_alias_pattern,
    extract_direction,
    extract_numeric_claims,
    has_approximation_language,
)


@pytest.fixture(scope="module")
def revenue_anchor():
    """Compiled alias pattern for `revenue`, sourced from the shared registry."""
    pattern = _metric_alias_pattern("revenue")
    assert pattern is not None, "registry no longer knows 'revenue'"
    return pattern


# ---------------------------------------------------------------------------
# extract_numeric_claims
# ---------------------------------------------------------------------------

class TestExtractNumericClaims:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Revenue was Rs 12,114 crore", [12114.0]),
            ("₹12114.5 Cr and 3,000 crore", [12114.5, 3000.0]),
            ("INR 500 Cr.", [500.0]),
            ("12,114 crore", [12114.0]),
            ("revenue of 1.5 cr", [1.5]),
            ("100 CRORE", [100.0]),
        ],
    )
    def test_indian_currency_formats(self, text, expected):
        assert extract_numeric_claims(text) == expected

    def test_lakh_style_comma_grouping_is_parsed(self):
        """Commas are stripped wholesale, so 1,00,000 reads as 100000."""
        assert extract_numeric_claims("Rs 1,00,000 crore") == [100000.0]

    @pytest.mark.parametrize(
        "text",
        [
            "no numbers here",
            "5 million rupees",   # crore/cr is the only recognised unit
            "Rs crore",           # unit with no figure
            "",
        ],
    )
    def test_non_crore_text_yields_no_claims(self, text):
        assert extract_numeric_claims(text) == []

    def test_unanchored_returns_every_figure(self):
        text = "Revenue 100 crore and headcount costs 20 crore"
        assert extract_numeric_claims(text) == [100.0, 20.0]


class TestNumericClaimAnchoring:
    def test_figure_near_a_metric_alias_is_kept(self, revenue_anchor):
        assert extract_numeric_claims("Revenue was 100 crore", revenue_anchor) == [100.0]

    def test_figure_beyond_the_proximity_window_is_dropped(self, revenue_anchor):
        far = "Revenue " + ("x" * 300) + " 100 crore"
        assert extract_numeric_claims(far, revenue_anchor) == []

    def test_anchoring_is_what_prevents_cross_metric_contradictions(self, revenue_anchor):
        """
        Without an anchor a PAT figure in the same chunk would be compared
        against a revenue claim. The module docstring records this as the
        defect the anchor exists to prevent.
        """
        text = "Revenue was 100 crore. " + ("padding " * 40) + "PAT was 7 crore."
        assert extract_numeric_claims(text) == [100.0, 7.0]
        assert extract_numeric_claims(text, revenue_anchor) == [100.0]

    def test_unknown_metric_has_no_anchor_pattern(self):
        """
        _metric_alias_pattern returns None for a metric the registry does not
        know, so the caller can tell "no aliases" apart from "no match".
        """
        assert _metric_alias_pattern("not_a_registered_metric") is None

    def test_proximity_window_constant(self):
        assert PROXIMITY_WINDOW == 120


# ---------------------------------------------------------------------------
# extract_direction
# ---------------------------------------------------------------------------

class TestExtractDirection:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Revenue grew strongly", "positive"),
            ("growth accelerated and improved", "positive"),
            ("GREW", "positive"),
            ("Revenue declined sharply", "negative"),
            ("fell and dropped and lost", "negative"),
        ],
    )
    def test_dominant_polarity(self, text, expected):
        assert extract_direction(text) == expected

    def test_no_directional_language_returns_none(self):
        assert extract_direction("nothing directional here") is None
        assert extract_direction("") is None

    def test_exact_tie_is_ambiguous_not_guessed(self):
        """
        contradiction.py:271 returns None on a tie rather than picking. A
        manufactured direction is a manufactured contradiction.
        """
        assert extract_direction("Revenue grew and then declined") is None

    def test_majority_wins_when_counts_differ(self):
        assert extract_direction(
            "revenue grew but margins fell and profits dropped"
        ) == "negative"

    def test_anchored_polarity_near_the_metric_is_counted(self, revenue_anchor):
        assert extract_direction("Revenue grew strongly", revenue_anchor) == "positive"

    def test_anchored_polarity_beyond_the_window_is_ignored(self, revenue_anchor):
        far = "Revenue " + ("x" * 300) + " grew strongly"
        assert extract_direction(far, revenue_anchor) is None


# ---------------------------------------------------------------------------
# _classify_severity
# ---------------------------------------------------------------------------

class TestClassifySeverity:
    @pytest.mark.parametrize(
        "delta,expected",
        [
            (0, "low"), (5, "low"), (9.99, "low"),
            (10, "medium"), (15, "medium"), (19.99, "medium"),
            (20, "high"), (25, "high"), (1e9, "high"),
        ],
    )
    def test_bands(self, delta, expected):
        assert _classify_severity(delta) == expected

    def test_boundaries_are_inclusive_at_the_lower_edge(self):
        assert _classify_severity(SEVERITY_MEDIUM_PCT) == "medium"
        assert _classify_severity(SEVERITY_HIGH_PCT) == "high"

    @pytest.mark.parametrize("delta,expected", [(-25, "high"), (-10, "medium"), (-9.9, "low")])
    def test_sign_is_ignored(self, delta, expected):
        """abs() first -- an under-statement is as severe as an over-statement."""
        assert _classify_severity(delta) == expected

    def test_documented_thresholds(self):
        assert SEVERITY_HIGH_PCT == 20.0
        assert SEVERITY_MEDIUM_PCT == 10.0


# ---------------------------------------------------------------------------
# has_approximation_language
# ---------------------------------------------------------------------------

class TestApproximationLanguage:
    @pytest.mark.parametrize(
        "text", ["approximately 100", "around 5", "ROUGHLY", "nearly there", "about 10"]
    )
    def test_hedging_words_are_detected(self, text):
        assert has_approximation_language(text) is True

    def test_precise_language_is_not_flagged(self):
        assert has_approximation_language("exactly 100 crore") is False
        assert has_approximation_language("") is False

    def test_tilde_alternative_can_never_match(self):
        """
        The tilde is listed in _APPROXIMATION_SIGNAL (contradiction.py:98-101)
        but the alternation is wrapped in \\b...\\b. '~' is not a word
        character, so a word boundary can never sit on both sides of it and
        that alternative is unreachable.

        Consequence: "~10 crore" is treated as a precise claim. Approximation
        language relaxes contradiction detection, so the failure direction is
        toward flagging a hedged figure as a contradiction -- the false-positive
        direction this module is most concerned with.
        """
        assert has_approximation_language("~10 crore") is False
        assert has_approximation_language("revenue of ~ 10 crore") is False
