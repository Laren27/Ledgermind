"""
Unit tests — app.engines.retriever._build_filter

_build_filter takes strings and returns a qdrant_client Filter object. It makes
no call: constructing a Filter is local model construction, and the network
guard in conftest.py is active throughout.

Importing app.engines.retriever pulls fastembed and qdrant_client. Both are
import-clean (measured 2.77s, no connection); the reranker model and Qdrant
client in that module are lazily constructed behind module-level globals and
are never touched by these tests.

The filter's fail-open behaviour is audit finding F2. Those assertions record
what the code does today.
"""
import pytest

from qdrant_client.models import FieldCondition, Filter

from app.engines.retriever import _build_filter


def _conditions_by_key(built: Filter) -> dict:
    """key -> matched value, for the flat FieldConditions in `must`."""
    return {
        c.key: c.match.value
        for c in built.must
        if isinstance(c, FieldCondition)
    }


# ---------------------------------------------------------------------------
# Unconditional conditions
# ---------------------------------------------------------------------------

class TestAlwaysApplied:
    def test_tenant_and_is_latest_are_always_present(self):
        built = _build_filter(tenant_id="tenant-1")
        assert _conditions_by_key(built) == {
            "tenant_id": "tenant-1",
            "is_latest": True,
        }

    def test_tenant_isolation_survives_every_other_argument_being_absent(self):
        """
        tenant_id is the one condition with no guard around it
        (retriever.py:170-172). Whatever else fails to be extracted, a query
        cannot escape its tenant.
        """
        built = _build_filter(
            tenant_id="tenant-1",
            company=None, fiscal_year=None, quarter=None, financial_type=None,
        )
        assert _conditions_by_key(built)["tenant_id"] == "tenant-1"

    def test_is_latest_is_overridable(self):
        built = _build_filter(tenant_id="t", is_latest=False)
        assert _conditions_by_key(built)["is_latest"] is False


# ---------------------------------------------------------------------------
# Fully specified
# ---------------------------------------------------------------------------

class TestFullySpecified:
    def test_all_arguments_produce_six_conditions(self):
        built = _build_filter(
            tenant_id="t", company="ETERNAL", fiscal_year="FY26",
            quarter="Q4", financial_type="consolidated",
        )
        assert len(built.must) == 6
        assert _conditions_by_key(built) == {
            "tenant_id": "t",
            "is_latest": True,
            "company": "ETERNAL",
            "fiscal_year": "FY26",
            "quarter": "Q4",
        }

    def test_financial_type_is_a_nested_should_admitting_unknown(self):
        """
        documents F7 — the financial_type condition matches the requested type
        OR the literal 'unknown' (retriever.py:190-197). 2496 of 2531 indexed
        chunks carry 'unknown' by design (section_classifier assigns a real
        type only to FINANCIAL_STATEMENT blocks), so this condition excludes
        17 chunks corpus-wide and the consolidated/standalone distinction is
        not enforced on the semantic path.
        """
        built = _build_filter(tenant_id="t", financial_type="consolidated")
        nested = [c for c in built.must if isinstance(c, Filter)]
        assert len(nested) == 1
        assert [c.match.value for c in nested[0].should] == ["consolidated", "unknown"]


# ---------------------------------------------------------------------------
# Fail-open guards
# ---------------------------------------------------------------------------

class TestFalsyArgumentsSkipTheFilter:
    """
    documents F2 — company, fiscal_year and financial_type are guarded by bare
    truthiness (retriever.py:174, 179, 189), so None AND empty string both mean
    "do not filter on this at all" rather than "match nothing".

    Combined with the router's all-null fallback (router.py:151-160, returned
    when both LLM providers fail), a routing failure produces an unfiltered
    search across every company and year in the tenant that still returns an
    answer. It works on the current corpus because three companies in three
    sectors have distinguishable vocabulary; that is a property of the corpus,
    not of the code.
    """

    @pytest.mark.parametrize("falsy", [None, ""])
    def test_falsy_company_is_dropped_entirely(self, falsy):
        built = _build_filter(tenant_id="t", company=falsy)
        assert "company" not in _conditions_by_key(built)

    @pytest.mark.parametrize("falsy", [None, ""])
    def test_falsy_fiscal_year_is_dropped_entirely(self, falsy):
        built = _build_filter(tenant_id="t", fiscal_year=falsy)
        assert "fiscal_year" not in _conditions_by_key(built)

    @pytest.mark.parametrize("falsy", [None, ""])
    def test_falsy_financial_type_is_dropped_entirely(self, falsy):
        built = _build_filter(tenant_id="t", financial_type=falsy)
        assert [c for c in built.must if isinstance(c, Filter)] == []

    def test_router_fallback_shape_yields_an_unscoped_tenant_wide_filter(self):
        """The exact dict router._classify returns on total provider failure."""
        built = _build_filter(
            tenant_id="t",
            company=None, fiscal_year=None, quarter=None,
            financial_type="consolidated",
        )
        flat = _conditions_by_key(built)
        assert set(flat) == {"tenant_id", "is_latest"}


class TestQuarterUsesAnIsNoneGuard:
    """
    quarter is guarded with `is not None` (retriever.py:184), unlike its three
    neighbours. The asymmetry is load-bearing in one direction and surprising
    in the other, so both halves are pinned.
    """

    def test_none_quarter_is_dropped(self):
        built = _build_filter(tenant_id="t", quarter=None)
        assert "quarter" not in _conditions_by_key(built)

    def test_empty_string_quarter_is_kept_as_a_literal_match(self):
        """
        Unlike company/fiscal_year, quarter="" produces a real condition
        matching the empty string -- which no indexed chunk carries (annual
        documents store quarter as None). An empty-string quarter therefore
        matches nothing rather than being ignored.
        """
        built = _build_filter(tenant_id="t", quarter="")
        assert _conditions_by_key(built)["quarter"] == ""
