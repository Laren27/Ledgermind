"""
A Prompt Shield block must not report a confidence tier it never computed.

graph.py routes a blocked query prompt_shield -> audit_writer on the "blocked"
edge, so confidence_node never runs and nothing writes a tier. Before this
guard, what reached the client was make_initial_state's default "low" --
byte-identical on the wire to a tier that WAS computed and came out low.
Confirmed against the deployed Render backend 2026-08-22: a blocked query
returned confidence_tier="low", confidence_score=0.0.

Same null-overloading shape as company_unresolved: one value standing for two
different facts, with no way for a consumer to tell which one it has.

OMITTED, not nulled, and the choice was measured rather than argued.
scripts/eval_runner.py's out_of_corpus scorer reads the tier through
`.get("confidence_tier", "low")` inside a PASS condition. An absent key
therefore scores exactly as today; an explicit None flips that verdict from
pass to fail. Running score_result over all twelve golden categories with the
field set three ways showed absent and "low" agreeing everywhere and None
diverging on out_of_corpus -- which is why this asserts absence.

confidence_score is deliberately NOT part of this contract. It is a stored
audit_log column that metrics.py aggregates over, so changing it is a
stored-data decision, not a response one. The test pins that it stays put.
"""

import pytest

from app.api.response_shaping import role_filtered_response
from app.engines.state import make_initial_state

ROLES = ["viewer", "analyst", "admin"]


def _state(**overrides):
    s = make_initial_state(query="q", tenant_id="t", user_id="u", request_id="r")
    s.update(overrides)
    return s


def _blocked():
    """Exactly what prompt_shield_node leaves behind: is_blocked and a reason,
    and NOTHING touching confidence -- the node that would set it never ran."""
    return _state(
        is_blocked=True,
        block_reason="trading_advice: LedgerMind cannot provide trading recommendations.",
        response_text="LedgerMind is a financial research tool and cannot provide that.",
    )


def _measured_low():
    """A query that ran the full pipeline and was scored low."""
    return _state(path="semantic", confidence_tier="low", confidence_score=0.11)


@pytest.mark.parametrize("role", ROLES)
def test_blocked_omits_the_tier_it_never_computed(role):
    out = role_filtered_response(_blocked(), role)
    assert "confidence_tier" not in out


@pytest.mark.parametrize("role", ROLES)
def test_measured_low_still_reports_its_tier(role):
    out = role_filtered_response(_measured_low(), role)
    assert out["confidence_tier"] == "low"


@pytest.mark.parametrize("role", ROLES)
def test_blocked_and_measured_low_are_distinguishable(role):
    """The property the change exists to produce, stated directly.

    Asserted as a difference between the two payloads rather than as two
    separate value checks: a future default that made both absent, or both
    "low", would satisfy either check alone and still lose the distinction.
    """
    blocked = role_filtered_response(_blocked(), role)
    low = role_filtered_response(_measured_low(), role)

    assert ("confidence_tier" in blocked) != ("confidence_tier" in low)
    assert blocked.get("confidence_tier") != low.get("confidence_tier")


def test_router_refusal_keeps_its_measured_low():
    """The refusal path is NOT the blocked path and must not be swept up.

    router_node writes "low"/0.0 explicitly on company_not_in_corpus and
    routing_unavailable (the "refused" edge). That IS a decision the router
    made, and it stays reported.
    """
    refused = _state(
        path="semantic",
        error="company_not_in_corpus",
        error_node="router",
        confidence_tier="low",
        confidence_score=0.0,
    )
    out = role_filtered_response(refused, "admin")
    assert out["confidence_tier"] == "low"
    assert out["confidence_score"] == 0.0


def test_blocked_confidence_score_is_unchanged():
    """Pins the deliberate half-scope.

    audit_log.confidence_score is a stored column and metrics.py's
    refusal_rate_pct and confidence_distribution aggregate over it. Nulling it
    would retroactively change what those mean for every blocked row already
    written, so the score stays 0.0 until that is decided on its own terms.
    If this test starts failing, that decision was made somewhere else.
    """
    out = role_filtered_response(_blocked(), "admin")
    assert out["confidence_score"] == 0.0
