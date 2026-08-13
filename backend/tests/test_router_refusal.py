"""
F2 step 0 — router refusals must terminate, not dispatch into an engine.

route_after_router originally read only `path`. A refusal written by
router_node therefore still routed into semantic/quant/cross and ran the
unfiltered search the refusal exists to prevent. Measured on prod
2026-08-12: a query naming a company not in the corpus returned 5 citations,
all from other issuers, at confidence_tier=high.

Neither refusal branch is reachable in production yet (routing_unavailable
needs a total provider outage; company_not_in_corpus needs a router schema
field that does not exist). These tests are what keeps the branch exercised
until it is.
"""

from pathlib import Path

from app.engines.router import route_after_router
from app.engines.state import make_initial_state


def _state(**overrides):
    s = make_initial_state(
        query="q", tenant_id="t", user_id="u", request_id="r",
    )
    s.update(overrides)
    return s


def test_router_error_routes_to_refused():
    s = _state(error_node="router", error="company_not_in_corpus", path="semantic")
    assert route_after_router(s) == "refused"


def test_downstream_error_does_not_route_to_refused():
    # Keyed on error_node, not bare `error`. quant_engine writes `error` on
    # eight of its own failure paths and those must not acquire terminal
    # routing from the router's edge.
    s = _state(error_node="quant_engine", error="no_data_found", path="quantitative")
    assert route_after_router(s) == "quant_engine"


def test_error_node_router_without_error_dispatches_normally():
    s = _state(error_node="router", error=None, path="semantic")
    assert route_after_router(s) == "semantic_engine"


def test_clean_states_dispatch_by_path():
    assert route_after_router(_state(path="semantic")) == "semantic_engine"
    assert route_after_router(_state(path="quantitative")) == "quant_engine"
    assert route_after_router(_state(path="cross")) == "cross_engine"
    assert route_after_router(_state(path=None)) == "semantic_engine"


def test_graph_maps_refused_target():
    # Two-file contract: LangGraph raises if a routing function returns a
    # value absent from the conditional-edge mapping. This asserts graph.py
    # still carries the key route_after_router can return.
    src = Path(__file__).resolve().parents[1] / "app" / "engines" / "graph.py"
    assert '"refused": "audit_writer"' in src.read_text()


# ── F2 step 2: multi-entity queries must not refuse ──────────────────────

from app.engines.router import _resolve_mentioned_issuers as _rmi


def test_single_known_issuer_resolves():
    assert _rmi("Eternal") == (["ETERNAL"], [])


def test_multi_entity_all_known_resolves_all():
    # Golden Q051. company is None here because RouterResponse holds one
    # company -- refusing on that null would break a passing question.
    res, unres = _rmi("Eternal, Paytm")
    assert set(res) == {"ETERNAL", "PAYTM"} and unres == []


def test_multi_entity_separators():
    for sep in ("Eternal and Paytm", "Eternal or Paytm", "Eternal vs Paytm"):
        res, _ = _rmi(sep)
        assert set(res) == {"ETERNAL", "PAYTM"}, sep


def test_unknown_issuer_does_not_resolve():
    res, unres = _rmi("Reliance Industries")
    assert res == [] and unres == ["Reliance Industries"]


def test_mixed_known_and_unknown_resolves_known():
    res, unres = _rmi("Eternal, Reliance Industries")
    assert res == ["ETERNAL"] and unres == ["Reliance Industries"]


def test_empty_mentions_resolve_to_nothing():
    assert _rmi(None) == ([], [])
    assert _rmi("") == ([], [])
