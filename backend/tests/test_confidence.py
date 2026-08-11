"""
Unit tests — app.engines.confidence._cap_tier

_cap_tier maps two tier strings to the lower of the two through _TIER_RANK.
Pure dict lookup and a min(); no state, no I/O.

confidence_node itself is NOT covered here. It takes a QueryState TypedDict and
is pure in principle, but its two branches are driven by `contradictions` and
`restatement_disclosed`, and asserting on it would mean constructing a full
QueryState -- testing the shape of a state object rather than a behaviour. The
capping logic it delegates to is the part with the decisions in it.
"""
import pytest

from app.engines.confidence import _RANK_TIER, _TIER_RANK, _cap_tier


class TestCapTierOrdering:
    def test_returns_the_lower_of_two_known_tiers(self):
        assert _cap_tier("high", "medium") == "medium"
        assert _cap_tier("medium", "high") == "medium"
        assert _cap_tier("low", "high") == "low"
        assert _cap_tier("high", "low") == "low"

    def test_equal_tiers_are_unchanged(self):
        for tier in ("high", "medium", "low"):
            assert _cap_tier(tier, tier) == tier

    def test_never_raises_a_tier(self):
        """
        The module contract is 'never raises confidence, only lowers it'. Every
        ordered pair must return something no higher than the current tier.
        """
        for current in ("high", "medium", "low"):
            for cap in ("high", "medium", "low"):
                result = _cap_tier(current, cap)
                assert _TIER_RANK[result] <= _TIER_RANK[current]

    def test_result_is_always_a_known_tier_name(self):
        for current in ("high", "medium", "low"):
            for cap in ("high", "medium", "low"):
                assert _cap_tier(current, cap) in _TIER_RANK


class TestCapTierUnknownInput:
    """
    _TIER_RANK.get(tier, 0) defaults an unrecognised tier to rank 0, which
    _RANK_TIER maps back to 'low'. An unknown tier is therefore treated as the
    WORST tier, not rejected.

    This is the safe direction for a confidence cap -- an unrecognised value
    can only lower an answer's stated confidence, never raise it -- but it is
    silent, and 'low' is indistinguishable from a genuinely low-confidence
    result downstream.
    """

    def test_unknown_current_tier_collapses_to_low(self):
        assert _cap_tier("bogus", "high") == "low"

    def test_unknown_cap_tier_collapses_to_low(self):
        assert _cap_tier("high", "bogus") == "low"

    def test_empty_strings_collapse_to_low(self):
        assert _cap_tier("", "") == "low"

    def test_tier_names_are_case_sensitive(self):
        """'HIGH' is not a known tier and is treated as the worst one."""
        assert _cap_tier("HIGH", "medium") == "low"
        assert _cap_tier("high", "MEDIUM") == "low"


class TestTierTables:
    def test_rank_tables_are_inverses(self):
        assert {v: k for k, v in _TIER_RANK.items()} == _RANK_TIER

    def test_three_tiers_ordered_low_to_high(self):
        assert _TIER_RANK == {"high": 2, "medium": 1, "low": 0}
