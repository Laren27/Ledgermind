"""
Unit tests — app.metrics.registry

Covers get_metric, metric_anchor_phrases and dsl_registry. The registry is a
frozen dataclass table plus pure derivations over it, so every function here is
a plain in-memory transform.

CLAUDE.md §1.3 treats registry CONTENTS as measured constants. Nothing in this
file proposes or asserts a change to an alias. The structural invariants below
(no alias claimed twice, derived metrics unavailable) are properties the
registry's own module docstring claims, checked rather than assumed.
"""
import pytest

from app.metrics.registry import (
    ALL_METRICS,
    MetricDefinition,
    dsl_registry,
    get_metric,
    metric_anchor_phrases,
)


# ---------------------------------------------------------------------------
# get_metric
# ---------------------------------------------------------------------------

class TestGetMetric:
    def test_known_canonical_name_returns_definition(self):
        metric = get_metric("revenue")
        assert isinstance(metric, MetricDefinition)
        assert metric.canonical_name == "revenue"
        assert metric.label == "Revenue"
        assert metric.metric_type == "raw"
        assert metric.dsl_enabled is True

    def test_unknown_name_returns_none(self):
        assert get_metric("nope_not_a_metric") is None

    def test_lookup_is_case_sensitive(self):
        """
        _BY_CANONICAL is keyed on the exact canonical_name. 'Revenue' misses.
        Callers pass canonical names produced by the registry itself, so this
        has no live victim -- recorded because it is a silent None rather than
        an error, and None is what an unknown metric also returns.
        """
        assert get_metric("Revenue") is None
        assert get_metric("REVENUE") is None

    def test_returned_definition_is_immutable(self):
        """MetricDefinition is frozen -- a caller cannot mutate shared state."""
        metric = get_metric("revenue")
        with pytest.raises(Exception):
            metric.canonical_name = "something_else"


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------

class TestRegistryStructure:
    def test_no_alias_is_claimed_by_two_metrics(self):
        """
        The whole point of the single-registry consolidation. An alias mapping
        to two canonicals is the exceptional_items collision that silently
        backfilled a blank cell from an unrelated row.
        """
        seen: dict[str, str] = {}
        collisions = []
        for metric in ALL_METRICS:
            for alias in metric.aliases:
                key = alias.lower().strip()
                if key in seen and seen[key] != metric.canonical_name:
                    collisions.append((key, seen[key], metric.canonical_name))
                seen[key] = metric.canonical_name
        assert collisions == []

    def test_canonical_names_are_unique(self):
        names = [m.canonical_name for m in ALL_METRICS]
        assert len(names) == len(set(names))

    def test_every_metric_has_at_least_one_alias(self):
        """An aliasless metric is unreachable from any document label."""
        assert [m.canonical_name for m in ALL_METRICS if not m.aliases] == []

    def test_metric_type_is_one_of_two_values(self):
        assert {m.metric_type for m in ALL_METRICS} <= {"raw", "derived"}


# ---------------------------------------------------------------------------
# dsl_registry
# ---------------------------------------------------------------------------

class TestDslRegistry:
    def test_shape_of_an_entry(self):
        entry = dsl_registry()["revenue"]
        assert entry == {"available": True, "column": "value", "label": "Revenue"}

    def test_contains_only_dsl_enabled_metrics(self):
        registry = dsl_registry()
        expected = {m.canonical_name for m in ALL_METRICS if m.dsl_enabled}
        assert set(registry) == expected

    def test_available_is_derived_from_metric_type_not_hardcoded(self):
        """
        registry.py:613 computes available as (metric_type == 'raw'). The
        module docstring claims the registry tracks semantics, never corpus
        state; this is the line that has to hold for that claim to be true.
        """
        registry = dsl_registry()
        for metric in ALL_METRICS:
            if metric.dsl_enabled:
                assert registry[metric.canonical_name]["available"] == (
                    metric.metric_type == "raw"
                ), metric.canonical_name

    def test_derived_metrics_are_registered_but_unavailable(self):
        """
        No SQL formula compiler exists for derived metrics, so they must
        surface as a clean 'not yet available' rather than being absent (which
        would let the LLM substitute a neighbouring metric instead).
        """
        entry = dsl_registry()["ebitda"]
        assert entry["available"] is False
        assert entry["label"] == "EBITDA"

    def test_every_column_is_the_value_column(self):
        """LLMs never name a column -- the shape exists so the compiler can."""
        assert {e["column"] for e in dsl_registry().values()} == {"value"}


# ---------------------------------------------------------------------------
# metric_anchor_phrases
# ---------------------------------------------------------------------------

class TestMetricAnchorPhrases:
    def test_returns_a_set_of_lowercase_phrases(self):
        phrases = metric_anchor_phrases()
        assert isinstance(phrases, set)
        assert phrases
        assert all(p == p.lower() for p in phrases)
        assert all(p == p.strip() for p in phrases)

    def test_includes_every_registry_alias(self):
        for metric in ALL_METRICS:
            for alias in metric.aliases:
                assert alias.lower().strip() in metric_anchor_phrases(), alias

    def test_includes_canonical_names_with_underscores_expanded(self):
        phrases = metric_anchor_phrases()
        assert "profit before tax" in phrases
        assert "total income" in phrases

    def test_includes_prompt_only_aliases(self):
        """
        'delivery charges' and 'employee benefits' exist ONLY in
        prompt_aliases, in no aliases tuple. registry.py:756 documents that a
        first pass reading aliases alone left four golden questions unanchored.
        """
        phrases = metric_anchor_phrases()
        assert "delivery charges" in phrases
        assert "employee benefits" in phrases

    def test_includes_short_aliases_deliberately(self):
        """
        Polarity is the opposite of unqueryable_metric_aliases: this set is
        consulted to find NOTHING, so breadth makes the guard fire LESS.
        registry.py:744-750 states short aliases are free safety here.
        """
        phrases = metric_anchor_phrases()
        assert "cash" in phrases
        assert "equity" in phrases

    def test_prompt_alias_fragments_with_parentheses_are_dropped(self):
        """
        registry.py:766 skips prompt_aliases entries containing '(' -- those
        are prose for Gemini, not matchable phrases. Aliases proper may still
        contain parentheses, so this is asserted against the prompt_aliases
        derivation specifically, not against the whole set.
        """
        phrases = metric_anchor_phrases()
        for metric in ALL_METRICS:
            for fragment in (metric.prompt_aliases or "").split(","):
                cleaned = fragment.lower().strip()
                if cleaned and "(" in cleaned:
                    assert cleaned not in phrases, cleaned
