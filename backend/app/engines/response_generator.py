"""
LedgerMind — Phase 4: Response Generator
============================================
Final node before audit_writer. Assembles the user-facing response_text
from whatever the active path produced.

Three response strategies by path:

  quantitative — TEMPLATED, not generative. The SQL value is already
    verified; wrapping it in an LLM-generated sentence adds hallucination
    risk for zero benefit. A deterministic template is safer and faster.

  semantic — GENERATIVE. Gemini synthesises an answer from the top
    retrieved chunks, with citations appended. This is the one place an
    LLM "explains" something, because there's no ground truth number to
    protect — only retrieved text to summarise faithfully.

  cross — GENERATIVE + contradiction disclosure. Combines the quant
    template with a Gemini-synthesised qualitative summary, then appends
    a contradiction disclosure block if any flags were raised.

Restatement disclosure: if any chunk or SQL row's filing_date differs from
the most recent filing_date in the corpus for that company/metric/period,
flag restatement_disclosed=True so confidence.py can apply its penalty.
(Defensive — normal retrieval already filters is_latest=True, so this is
a safety net for unexpected upstream gaps, not the primary mechanism.)
"""

import logging
import os
from typing import List, Optional

from google import genai
from google.genai import types

from app.engines.router import GEMINI_MODEL
from app.engines.state import ChunkResult, Citation, ContradictionFlag, QueryState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gemini client singleton (shared pattern across router/quant_engine)
# ---------------------------------------------------------------------------

_gemini_client: Optional[genai.Client] = None


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable not set")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


SYNTHESIS_SYSTEM_PROMPT = """You are a financial research assistant for LedgerMind.

Given retrieved document excerpts, answer the user's question using ONLY
information present in the excerpts. Do not add outside knowledge. Do not
speculate. If the excerpts don't fully answer the question, say what is
and isn't covered.

Write in plain, professional prose. No markdown headers. 2-4 sentences
for simple questions, up to 2 short paragraphs for complex ones.
Do not repeat citation details (page numbers, chunk IDs) in your prose —
citations are appended separately by the system.
"""


# ---------------------------------------------------------------------------
# Quantitative response — templated, deterministic
# ---------------------------------------------------------------------------

def _format_quant_response(state: QueryState) -> str:
    """
    Build a templated response from verified SQL results.
    No LLM call — the number is already verified, template wraps it safely.
    """
    sql_result = state.get("sql_result")
    dsl = state.get("dsl_object")

    if not sql_result or not dsl:
        return "No verified data available for this query."

    row = sql_result[0]
    entity = dsl["entity"]
    financial_type = dsl["financial_type"]

    # point_in_time
    if "value" in row:
        value = row["value"]
        fiscal_year = row["fiscal_year"]
        quarter = row.get("quarter")
        unit = row.get("unit", "crore_inr")
        unit_label = "Cr" if unit == "crore_inr" else unit
        period_label = f"{quarter} {fiscal_year}" if quarter else fiscal_year
        metric_label = row.get("metric", dsl["metric"]).replace("_", " ").title()

        return (
            f"{entity}'s {financial_type} {metric_label} for {period_label} "
            f"was ₹{float(value):,.1f} {unit_label}."
        )

    # yoy_growth
    if "yoy_pct" in row:
        if row.get("error"):
            return f"Could not compute year-over-year growth: {row['error']}"
        metric = row["metric"]
        current_fy = row["current_fy"]
        prior_fy = row["prior_fy"]
        current_val = row["current_value"]
        prior_val = row["prior_value"]
        yoy_pct = row["yoy_pct"]
        unit_label = "Cr" if row.get("unit") == "crore_inr" else row.get("unit", "")
        direction = "grew" if yoy_pct and yoy_pct > 0 else "declined"
        yoy_text = f"{abs(yoy_pct):.2f}%" if yoy_pct is not None else "an undetermined amount"

        return (
            f"{entity}'s {financial_type} {metric} {direction} {yoy_text} year-over-year, "
            f"from ₹{prior_val:,.1f} {unit_label} in {prior_fy} to "
            f"₹{current_val:,.1f} {unit_label} in {current_fy}."
        )

    # comparison
    if "entity1" in row:
        if row.get("error"):
            return f"Could not complete comparison: {row['error']}"
        metric = row["metric"]
        e1, v1 = row["entity1"], row["value1"]
        e2, v2 = row["entity2"], row["value2"]
        diff_pct = row.get("difference_pct")
        unit_label = "Cr" if row.get("unit") == "crore_inr" else row.get("unit", "")
        higher = e1 if v1 > v2 else e2
        lower_val, higher_val = min(v1, v2), max(v1, v2)
        if higher_val != 0:
            pct_magnitude = abs((higher_val - lower_val) / lower_val * 100) if lower_val != 0 else None
        else:
            pct_magnitude = None

        comparison_text = f"{pct_magnitude:.1f}% higher" if pct_magnitude is not None else "different"

        return (
            f"{e1}'s {metric} was ₹{v1:,.1f} {unit_label}, compared to "
            f"{e2}'s ₹{v2:,.1f} {unit_label} — {higher} reported {comparison_text} {metric.lower()}."
        )

    # cagr
    if "cagr_pct" in row:
        if row.get("error"):
            return f"Could not compute CAGR: {row['error']}"
        metric = row["metric"]
        start_fy, end_fy = row["start_fy"], row["end_fy"]
        cagr_pct = row["cagr_pct"]
        unit_label = "Cr" if row.get("unit") == "crore_inr" else row.get("unit", "")

        return (
            f"{entity}'s {metric} grew at a CAGR of {cagr_pct:.2f}% from "
            f"{start_fy} to {end_fy} (₹{row['start_value']:,.1f} {unit_label} → "
            f"₹{row['end_value']:,.1f} {unit_label})."
        )

    return "Verified data was retrieved but could not be formatted into a response."


# ---------------------------------------------------------------------------
# Semantic response — generative, citation-grounded
# ---------------------------------------------------------------------------

def _format_chunks_for_prompt(chunks: List[ChunkResult]) -> str:
    """Format retrieved chunks into a numbered context block for Gemini."""
    blocks = []
    for i, chunk in enumerate(chunks, 1):
        blocks.append(
            f"[Excerpt {i} — page {chunk['page_number']}, "
            f"{chunk['financial_type']} {chunk['fiscal_year']}]\n{chunk['text']}"
        )
    return "\n\n".join(blocks)


def _generate_semantic_response(query: str, chunks: List[ChunkResult]) -> str:
    """
    Call Gemini to synthesise an answer from retrieved chunks.
    Falls back to a raw excerpt dump if Gemini fails — never returns empty.
    """
    if not chunks:
        return "No relevant information was found in the available documents."

    client = _get_gemini_client()
    context = _format_chunks_for_prompt(chunks)
    user_message = f"Question: {query}\n\nRetrieved excerpts:\n\n{context}"

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=SYNTHESIS_SYSTEM_PROMPT,
                temperature=0.2,
                max_output_tokens=400,
            ),
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Response synthesis Gemini call failed: %s", e)
        # Fallback: return the top chunk's text directly rather than failing silently
        return (
            f"Unable to synthesise a summary due to a temporary error. "
            f"Top matching excerpt (page {chunks[0]['page_number']}): "
            f"{chunks[0]['text'][:300].strip()}"
        )


# ---------------------------------------------------------------------------
# Citation formatting
# ---------------------------------------------------------------------------

def _format_citations_block(citations: List[Citation]) -> str:
    """Format citations as a readable appendix to the response."""
    if not citations:
        return ""
    lines = ["\n\nSources:"]
    for i, c in enumerate(citations, 1):
        lines.append(
            f"  [{i}] {c['company']} {c['fiscal_year']} ({c['financial_type']}) — "
            f"page {c['page_number']}, filed {c['filing_date']} "
            f"(relevance score: {c['reranker_score']:.2f})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Contradiction disclosure formatting
# ---------------------------------------------------------------------------

def _format_contradiction_block(contradictions: List[ContradictionFlag]) -> str:
    """Format detected contradictions as a disclosure block per blueprint §12."""
    if not contradictions:
        return ""

    lines = ["\n\n⚠ Sources disagree on this topic:"]
    for c in contradictions:
        if c["type"] == "magnitude":
            lines.append(
                f"  • Qualitative source claims a figure differing by {c['delta_pct']:+.1f}% "
                f"from the verified {c['quantitative_metric']} value of "
                f"₹{c['quantitative_value']:,.1f} Cr. (severity: {c['severity']})"
            )
        elif c["type"] == "direction":
            direction_word = "positive" if c["quantitative_value"] > 0 else "negative"
            lines.append(
                f"  • Qualitative text uses language inconsistent with the "
                f"{direction_word} {c['quantitative_metric']} trend "
                f"({c['quantitative_value']:+.2f}%). (severity: {c['severity']})"
            )
    lines.append("  Review the cited sources directly before drawing conclusions.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def response_generator_node(state: QueryState) -> QueryState:
    """
    Final response assembly node.

    Skips entirely if:
      - Query was blocked (response_text already set by prompt_shield)
      - An unrecoverable error already set response_text (low confidence
        refusal, DSL failure, SQL failure, etc.)

    Otherwise builds response_text based on state["path"].
    """
    # Already has a response (blocked, or error path already set a message)
    if state.get("response_text"):
        return state

    path = state.get("path")
    citations_block = _format_citations_block(state.get("citations", []))
    contradiction_block = _format_contradiction_block(state.get("contradictions", []))

    if path == "quantitative":
        body = _format_quant_response(state)
        state["response_text"] = body  # no citations block — SQL is the source of truth

    elif path == "semantic":
        body = _generate_semantic_response(
            query=state["query"],
            chunks=state.get("retrieved_chunks", []),
        )
        state["response_text"] = body + citations_block

    elif path == "cross":
        qual_body = _generate_semantic_response(
            query=state["query"],
            chunks=state.get("retrieved_chunks", []),
        )
        quant_body = ""
        if state.get("sql_verified") and state.get("sql_result"):
            quant_body = "\n\n" + _format_quant_response(state)

        state["response_text"] = qual_body + quant_body + citations_block + contradiction_block

    else:
        state["response_text"] = (
            "Unable to determine how to process this query. Please rephrase."
        )

    logger.info(
        "Response generated | path=%s length=%d contradictions=%d",
        path, len(state["response_text"]), len(state.get("contradictions", [])),
    )

    return state