"""
Unit tests — scripts/eval_runner.py sql_result extraction helpers

scripts/test_eval_matcher.py already covers the expected_keywords matcher
(_missing_keywords, validate_expected_keywords, and the alternatives form) and
is the authoritative test for that path -- it also re-scores archived eval runs,
which pytest cannot do without the archives. Nothing here duplicates it.

What is NOT covered there, and is covered here: the four _extract_* helpers that
normalise a sql_result payload before scoring. Each takes a list, a dict or
None and returns a plain value; they run before any comparison and a wrong
normalisation silently mis-scores a question.

_keyword_alternatives is included because test_eval_matcher exercises it only
indirectly, through _missing_keywords.

The module is imported through the `eval_runner` fixture (conftest.py), which
substitutes sys.argv for the duration -- eval_runner calls parse_args() at
module scope with --model required.
"""
import pytest


# ---------------------------------------------------------------------------
# _extract_point_value / _extract_yoy_pct
# ---------------------------------------------------------------------------

class TestExtractPointValue:
    def test_reads_value_from_a_single_row_list(self, eval_runner):
        assert eval_runner._extract_point_value([{"value": 17292.0}]) == 17292.0

    def test_reads_value_from_a_bare_dict(self, eval_runner):
        assert eval_runner._extract_point_value({"value": 42.0}) == 42.0

    def test_reads_only_the_first_row(self, eval_runner):
        assert eval_runner._extract_point_value(
            [{"value": 1.0}, {"value": 2.0}]
        ) == 1.0

    @pytest.mark.parametrize("empty", [None, [], {}])
    def test_falsy_payloads_return_none(self, eval_runner, empty):
        assert eval_runner._extract_point_value(empty) is None

    def test_missing_key_returns_none_not_an_error(self, eval_runner):
        """A row without `value` scores as absent rather than raising."""
        assert eval_runner._extract_point_value([{"yoy_pct": 12.0}]) is None

    def test_zero_is_preserved_and_not_confused_with_absent(self, eval_runner):
        """0.0 is a real figure; only a falsy PAYLOAD means no data."""
        assert eval_runner._extract_point_value([{"value": 0.0}]) == 0.0


class TestExtractYoyPct:
    def test_reads_yoy_pct_from_a_list(self, eval_runner):
        assert eval_runner._extract_yoy_pct([{"yoy_pct": 23.4}]) == 23.4

    def test_reads_yoy_pct_from_a_bare_dict(self, eval_runner):
        assert eval_runner._extract_yoy_pct({"yoy_pct": -8.1}) == -8.1

    @pytest.mark.parametrize("empty", [None, [], {}])
    def test_falsy_payloads_return_none(self, eval_runner, empty):
        assert eval_runner._extract_yoy_pct(empty) is None

    def test_missing_key_returns_none(self, eval_runner):
        assert eval_runner._extract_yoy_pct([{"value": 100.0}]) is None


# ---------------------------------------------------------------------------
# _extract_comparison_values / _extract_growth_comparison_values
# ---------------------------------------------------------------------------

class TestExtractComparisonValues:
    def test_flat_shape_is_returned_whole(self, eval_runner):
        row = {"entity1": "ETERNAL", "value1": 100.0, "entity2": "PAYTM", "value2": 50.0}
        assert eval_runner._extract_comparison_values([row]) == row

    def test_nested_shape_is_returned_whole(self, eval_runner):
        row = {"entity_a": {"name": "ETERNAL", "value": 100.0}}
        assert eval_runner._extract_comparison_values(row) == row

    @pytest.mark.parametrize("empty", [None, [], {}])
    def test_falsy_payloads_return_none(self, eval_runner, empty):
        assert eval_runner._extract_comparison_values(empty) is None

    def test_non_dict_row_returns_none(self, eval_runner):
        """The isinstance guard is what keeps a scalar row from being scored."""
        assert eval_runner._extract_comparison_values(["not-a-dict"]) is None
        assert eval_runner._extract_comparison_values([42]) is None

    def test_growth_comparison_helper_behaves_identically(self, eval_runner):
        row = {"entity1": "ETERNAL", "growth1": 12.0}
        assert eval_runner._extract_growth_comparison_values([row]) == row
        assert eval_runner._extract_growth_comparison_values(None) is None
        assert eval_runner._extract_growth_comparison_values([1]) is None


# ---------------------------------------------------------------------------
# _keyword_alternatives
# ---------------------------------------------------------------------------

class TestKeywordAlternatives:
    def test_bare_string_becomes_a_one_element_set(self, eval_runner):
        assert eval_runner._keyword_alternatives("SEBI") == ["sebi"]

    def test_list_is_lowercased_elementwise(self, eval_runner):
        assert eval_runner._keyword_alternatives(
            ["SEBI (Listing Obligations", "Securities and Exchange Board"]
        ) == ["sebi (listing obligations", "securities and exchange board"]

    def test_bare_string_and_single_alternative_normalise_identically(self, eval_runner):
        """
        The docstring's claim: "the bare-string code path and the list code
        path are the same path". Both forms must produce the same normalised
        output for the same content.
        """
        assert (
            eval_runner._keyword_alternatives("LODR")
            == eval_runner._keyword_alternatives(["LODR"])
        )

    def test_empty_list_normalises_to_empty(self, eval_runner):
        """
        Normalisation itself does not reject an unsatisfiable entry --
        validate_expected_keywords does, at dataset load time, and
        scripts/test_eval_matcher.py covers that rejection.
        """
        assert eval_runner._keyword_alternatives([]) == []
