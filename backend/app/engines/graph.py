"""
LedgerMind — Phase 4: Graph Assembly
========================================
Wires every engine node into a single LangGraph StateGraph.

Topology:

  START
    ↓
  prompt_shield ──(blocked)──→ audit_writer ──→ END
    │
   (clean)
    ↓
  router ──(path=quantitative)──→ quant_engine ─────┐
    │                                                 │
    ├──(path=semantic)──→ semantic_engine ───────────┤
    │                                                 │
    └──(path=cross)──→ cross_engine ─────────────────┤
                                                       ↓
                                                  confidence
                                                       ↓
                                              response_generator
                                                       ↓
                                                 audit_writer
                                                       ↓
                                                      END

Uses StateGraph + TypedDict pattern only (no MessagesState, no agent
abstractions) — this is the stable subset of the LangGraph API per the
risk noted at the start of Phase 4 planning.
"""

import logging

from langgraph.graph import END, StateGraph

from app.engines.audit_writer import audit_writer_node
from app.engines.confidence import confidence_node
from app.engines.cross_engine import cross_engine_node
from app.engines.prompt_shield import prompt_shield_node
from app.engines.quant_engine import quant_engine_node
from app.engines.response_generator import response_generator_node
from app.engines.router import (
    route_after_router,
    route_after_shield,
    router_node,
)
from app.engines.semantic_engine import semantic_engine_node
from app.engines.state import QueryState

logger = logging.getLogger(__name__)


def build_graph():
    """
    Construct and compile the LedgerMind query graph.

    Returns a compiled LangGraph application with an .invoke(state) method.
    Compiled once at FastAPI startup and reused across requests (graph
    compilation is not free — don't rebuild per-request).
    """
    graph = StateGraph(QueryState)

    # ── Register nodes ───────────────────────────────────────────────────
    graph.add_node("prompt_shield", prompt_shield_node)
    graph.add_node("router", router_node)
    graph.add_node("semantic_engine", semantic_engine_node)
    graph.add_node("quant_engine", quant_engine_node)
    graph.add_node("cross_engine", cross_engine_node)
    graph.add_node("confidence", confidence_node)
    graph.add_node("response_generator", response_generator_node)
    graph.add_node("audit_writer", audit_writer_node)

    # ── Entry point ──────────────────────────────────────────────────────
    graph.set_entry_point("prompt_shield")

    # ── Conditional edge: prompt_shield → router OR straight to audit ─────
    graph.add_conditional_edges(
        "prompt_shield",
        route_after_shield,
        {
            "router": "router",
            "blocked": "audit_writer",   # blocked queries skip everything else
        },
    )

    # ── Conditional edge: router → one of three engines ────────────────────
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {
            "semantic_engine": "semantic_engine",
            "quant_engine": "quant_engine",
            "cross_engine": "cross_engine",
        },
    )

    # ── All three engines converge on confidence ────────────────────────────
    graph.add_edge("semantic_engine", "confidence")
    graph.add_edge("quant_engine", "confidence")
    graph.add_edge("cross_engine", "confidence")

    # ── Linear tail: confidence → response → audit → END ───────────────────
    graph.add_edge("confidence", "response_generator")
    graph.add_edge("response_generator", "audit_writer")
    graph.add_edge("audit_writer", END)

    compiled = graph.compile()
    logger.info("LedgerMind query graph compiled successfully")

    return compiled


# ---------------------------------------------------------------------------
# Module-level singleton — compiled once, reused across requests
# ---------------------------------------------------------------------------

_compiled_graph = None


def get_graph():
    """Returns the compiled graph singleton, building it on first call."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph