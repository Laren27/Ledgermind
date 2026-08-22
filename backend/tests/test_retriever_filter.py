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
import logging

import pytest

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from app.engines.retriever import _build_filter


def _conditions_by_key(built: Filter) -> dict:
    """key -> matched value, for the flat FieldConditions in `must`."""
    return {
        c.key: getattr(c.match, "value", None) if hasattr(c.match, "value")
        else getattr(c.match, "any", None)
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
            companies=None, fiscal_year=None, quarter=None, financial_type=None,
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
            tenant_id="t", companies=["ETERNAL"], fiscal_year="FY26",
            quarter="Q4", financial_type="consolidated",
        )
        assert len(built.must) == 6
        assert _conditions_by_key(built) == {
            "tenant_id": "t",
            "is_latest": True,
            "company": ["ETERNAL"],
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
    documents F2 — fiscal_year and financial_type are guarded by bare
    truthiness, so None AND empty string both mean "do not filter on this at
    all" rather than "match nothing".

    COMPANY IS NO LONGER BARE TRUTHINESS as of 2026-08-22: it is an explicit
    `is None or len(...) == 0` that also emits a WARNING. The BEHAVIOUR below
    is unchanged and these assertions still hold — see
    TestCompanyOmissionIsExplicitAndLogged for what changed.

    Combined with the router's all-null fallback (router.py:151-160, returned
    when both LLM providers fail), a routing failure produces an unfiltered
    search across every company and year in the tenant that still returns an
    answer. It works on the current corpus because three companies in three
    sectors have distinguishable vocabulary; that is a property of the corpus,
    not of the code.
    """

    @pytest.mark.parametrize("falsy", [None, []])
    def test_falsy_company_is_dropped_entirely(self, falsy):
        built = _build_filter(tenant_id="t", companies=falsy)
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
            companies=None, fiscal_year=None, quarter=None,
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


# ---------------------------------------------------------------------------
# The company omission is now explicit and recorded
# ---------------------------------------------------------------------------

class TestCompanyOmissionIsExplicitAndLogged:
    """
    F2's mechanism, made legible 2026-08-22.

    `_build_filter` used to gate the company condition on bare truthiness, so
    an unfiltered whole-tenant search was a falsy branch nobody could see. The
    test above still asserts the BEHAVIOUR is unchanged — None and "" drop the
    condition, exactly as before. What is new is that the branch is named
    (`companies is None or len(companies) == 0`) and emits a WARNING.

    Landed ahead of F14 deliberately. That change makes the field
    `companies: list[str]`, and `[]` is falsy too, so it would have taken the
    same silent branch — and because the Gemini schema node loses `nullable`
    under a list type, `[]` becomes the model's only way to express "no
    issuer", making the branch more reachable than the null it replaces.

    It does NOT refuse, and must not start to: Q051 passes today precisely
    because the search runs unfiltered here while the DSL carries both issuers.
    """

    def test_present_company_still_produces_the_condition(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.engines.retriever"):
            built = _build_filter(tenant_id="t", companies=["ETERNAL"])
        assert _conditions_by_key(built)["company"] == ["ETERNAL"]
        assert "UNFILTERED WHOLE-TENANT SEARCH" not in caplog.text
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert "company" not in _conditions_by_key(built)

    @pytest.mark.parametrize("empty", [None, []])
    def test_absent_company_drops_the_condition_and_warns(self, caplog, empty):
        with caplog.at_level(logging.WARNING, logger="app.engines.retriever"):
            built = _build_filter(tenant_id="tenant-42", companies=empty)
        assert "company" not in _conditions_by_key(built)
        assert "UNFILTERED WHOLE-TENANT SEARCH" in caplog.text
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert "UNFILTERED WHOLE-TENANT SEARCH" not in caplog.text

    def test_the_warning_names_the_tenant(self, caplog):
        """
        tenant_id is the only identifier in scope at this layer — _build_filter
        receives no request_id or query — so it is what makes a logged
        unfiltered search attributable at all.
        """
        with caplog.at_level(logging.WARNING, logger="app.engines.retriever"):
            _build_filter(tenant_id="tenant-42", companies=None)
        assert "tenant_id=tenant-42" in caplog.text
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert "tenant_id=some-other-tenant" in caplog.text

    def test_the_warning_is_a_single_line(self):
        """Render truncates multi-line output, so a wrapped record loses its tail."""
        record = logging.LogRecord(
            "app.engines.retriever", logging.WARNING, __file__, 0,
            "UNFILTERED WHOLE-TENANT SEARCH | no company condition applied | "
            "tenant_id=%s companies=%r fiscal_year=%r quarter=%r financial_type=%r",
            ("t", None, None, None, None), None,
        )
        assert "\n" not in record.getMessage()
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert "\n" in record.getMessage()

    def test_empty_list_takes_the_same_branch_as_none(self, caplog):
        """
        FORWARD COMPAT, asserted now so F14 cannot land it silently. `[]` is the
        shape `companies: list[str]` will produce for "no issuer"; it must drop
        the condition and warn, exactly as None does — not append a condition
        matching an empty list.
        """
        with caplog.at_level(logging.WARNING, logger="app.engines.retriever"):
            built = _build_filter(tenant_id="t", companies=[])
        assert "company" not in _conditions_by_key(built)
        assert "UNFILTERED WHOLE-TENANT SEARCH" in caplog.text
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert "company" in _conditions_by_key(built)

    def test_no_refusal_is_raised_on_the_empty_path(self):
        """
        Detect and report only. Q051 passes BECAUSE this path runs unfiltered;
        a refusal here would refuse a passing golden question.
        """
        assert isinstance(_build_filter(tenant_id="t", companies=None), Filter)
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _build_filter(tenant_id="t", companies=None) is None


# ---------------------------------------------------------------------------
# F14 — the company condition is an any-of over every named issuer
# ---------------------------------------------------------------------------

class TestAnyOfOverNamedIssuers:
    """
    F14. `MatchValue` held one issuer, so a two-issuer query had nowhere to put
    the second and the field nulled — which `_build_filter` then read as "no
    filter at all". `MatchAny` carries every named issuer.

    The `company` payload key is already indexed KEYWORD (qdrant_writer.py:81),
    which is the type MatchAny operates on, so this required no re-index.
    """

    def _company_condition(self, built):
        return [c for c in built.must
                if isinstance(c, FieldCondition) and c.key == "company"]

    def test_single_issuer_produces_an_any_of_with_one_value(self):
        """
        A one-element any-of matches exactly what the pre-F14 MatchValue
        matched, so single-issuer result sets are unchanged.
        """
        cond = self._company_condition(_build_filter(tenant_id="t", companies=["ETERNAL"]))
        assert len(cond) == 1
        assert cond[0].match.any == ["ETERNAL"]
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert cond[0].match.any == ["PAYTM"]

    def test_two_issuers_produce_an_any_of_over_both(self):
        cond = self._company_condition(
            _build_filter(tenant_id="t", companies=["ETERNAL", "PAYTM"])
        )
        assert len(cond) == 1
        assert cond[0].match.any == ["ETERNAL", "PAYTM"]
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert cond[0].match.any == ["ETERNAL"]

    def test_the_second_issuer_is_not_silently_dropped(self):
        """
        F14's defect in one line: the old shape kept one issuer and the answer
        then denied the other existed. ETERNAL is 732 rows.
        """
        cond = self._company_condition(
            _build_filter(tenant_id="t", companies=["ETERNAL", "PAYTM"])
        )
        assert "PAYTM" in cond[0].match.any
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert "PAYTM" not in cond[0].match.any

    def test_it_is_a_match_any_not_a_match_value(self):
        cond = self._company_condition(_build_filter(tenant_id="t", companies=["ETERNAL"]))
        assert isinstance(cond[0].match, MatchAny)
        assert not hasattr(cond[0].match, "value")
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert isinstance(cond[0].match, MatchValue)

    def test_tenant_isolation_survives_a_multi_issuer_filter(self):
        """tenant_id has no guard around it; whatever else changes, that holds."""
        built = _build_filter(tenant_id="tenant-1", companies=["ETERNAL", "PAYTM"])
        assert _conditions_by_key(built)["tenant_id"] == "tenant-1"
        with pytest.raises(AssertionError):          # NEGATIVE CONTROL
            assert _conditions_by_key(built)["tenant_id"] == "tenant-2"
