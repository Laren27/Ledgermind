"""
Unit tests — the quantitative path's DSL entry point actually binds.

WHY THIS FILE EXISTS. F14 renamed `company` -> `companies` across the query
path. `_generate_dsl`'s CALL SITE and BODY were renamed; its SIGNATURE was not.
The 222-test suite passed, the change was pushed, and every quantitative query
then returned:

    HTTP 500: Pipeline execution failed:
    _generate_dsl() got an unexpected keyword argument 'companies'

The suite could not catch it because every test in this directory exercises a
PURE function, and nothing imported the quantitative node or its DSL helper at
all. A signature/call-site mismatch is invisible to a test that never performs
the call.

So these tests perform the call. The provider boundary is monkeypatched --
`generate_structured` is replaced with a callable returning a canned response --
so this stays a pure unit test: no network, no LLM, no DB, and conftest's guard
is active throughout.
"""
import inspect
import json
from types import SimpleNamespace

import pytest

from app.engines import quant_engine
from app.engines.quant_engine import _build_dsl_user_message, _generate_dsl

# The kwargs quant_engine_node actually passes (quant_engine.py:718-724). If the
# signature and this set ever diverge again, the binding test below fails.
CALL_SITE_KWARGS = {
    "query": "What was Titan's consolidated revenue for FY26?",
    "companies": ["TITAN"],
    "fiscal_year": "FY26",
    "quarter": None,
    "financial_type": "consolidated",
}

_VALID_DSL = {
    "metric": "revenue",
    "entity": "TITAN",
    "fiscal_year": "FY26",
    "quarter": None,
    "financial_type": "consolidated",
    "operation": "point_in_time",
}


@pytest.fixture
def stub_provider(monkeypatch):
    """
    Replace the LLM boundary with a canned structured response.

    Monkeypatching the boundary rather than reaching through it is the pattern
    scripts/test_synthesis_floor.py already uses; this is the same manoeuvre,
    not a second approach to it.
    """
    calls = {"n": 0}

    def _fake(*_args, **_kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            text=json.dumps(_VALID_DSL),
            provider="stub",
            model="stub-model",
        )

    monkeypatch.setattr(quant_engine, "generate_structured", _fake)
    return calls


# ---------------------------------------------------------------------------
# The signature and the call site must agree
# ---------------------------------------------------------------------------

class TestSignatureBindsTheCallSite:
    def test_call_site_kwargs_bind_to_the_signature(self):
        """
        The direct guard. inspect.Signature.bind raises TypeError on exactly the
        mismatch that shipped -- an unexpected keyword argument -- without
        invoking anything.
        """
        sig = inspect.signature(_generate_dsl)
        bound = sig.bind(**CALL_SITE_KWARGS)
        assert bound.arguments["companies"] == ["TITAN"]
        with pytest.raises(TypeError):          # NEGATIVE CONTROL
            sig.bind(**{**CALL_SITE_KWARGS, "company": "TITAN"})

    def test_the_parameter_is_named_companies_not_company(self):
        names = list(inspect.signature(_generate_dsl).parameters)
        assert "companies" in names
        assert "company" not in names
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert "company" in names


# ---------------------------------------------------------------------------
# The call itself, with the provider stubbed
# ---------------------------------------------------------------------------

class TestGenerateDslExecutes:
    def test_it_returns_a_dsl_without_raising(self, stub_provider):
        dsl, attempts, error, llm = _generate_dsl(**CALL_SITE_KWARGS)
        assert error is None
        assert dsl is not None
        assert attempts >= 1
        assert stub_provider["n"] >= 1
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert dsl is None

    def test_single_issuer_is_applied_as_the_entity(self, stub_provider):
        dsl, _, _, _ = _generate_dsl(**CALL_SITE_KWARGS)
        assert dsl["entity"] == "TITAN"
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert dsl["entity"] == "PAYTM"

    def test_two_issuers_do_not_raise_either(self, stub_provider):
        """
        The Q051 shape. It must reach the model rather than failing to bind.
        """
        dsl, _, error, _ = _generate_dsl(
            **{**CALL_SITE_KWARGS, "companies": ["ETERNAL", "PAYTM"]}
        )
        assert error is None
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert error is not None

    def test_no_issuer_does_not_raise_either(self, stub_provider):
        dsl, _, error, _ = _generate_dsl(**{**CALL_SITE_KWARGS, "companies": []})
        assert error is None
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert error is not None


# ---------------------------------------------------------------------------
# The 'unknown' equivalence Q051's measured pass depends on
# ---------------------------------------------------------------------------

class TestDslPromptUnknownEquivalence:
    """
    NON-NEGOTIABLE, and the reason Q051 survived F14 by construction.

    Pre-F14 the prompt rendered `company or 'unknown'`. A two-issuer query
    NULLED `company`, so it rendered 'unknown' -- exactly as a no-issuer query
    did. Q051 was measured passing 2026-08-22 with that prompt. Both cases must
    still render 'unknown', byte-identical, or the DSL model stops producing
    entity/comparison_entity itself.
    """

    def _entity_line(self, companies):
        msg = _build_dsl_user_message(
            query="q", companies=companies, fiscal_year="FY26",
            quarter=None, financial_type="consolidated",
        )
        return [l for l in msg.splitlines() if l.strip().startswith("entity:")][0]

    def test_zero_issuers_render_unknown(self):
        assert self._entity_line([]) == "  entity: unknown"
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert self._entity_line([]) == "  entity: "

    def test_several_issuers_render_unknown(self):
        assert self._entity_line(["ETERNAL", "PAYTM"]) == "  entity: unknown"
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert self._entity_line(["ETERNAL", "PAYTM"]) == "  entity: ETERNAL"

    def test_zero_and_several_are_byte_identical(self):
        """The equivalence itself, asserted directly rather than inferred."""
        assert self._entity_line([]) == self._entity_line(["ETERNAL", "PAYTM"])
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert self._entity_line([]) == self._entity_line(["TITAN"])

    def test_one_issuer_names_it(self):
        assert self._entity_line(["TITAN"]) == "  entity: TITAN"
        with pytest.raises(AssertionError):     # NEGATIVE CONTROL
            assert self._entity_line(["TITAN"]) == "  entity: unknown"
