"""
LedgerMind — Phase 6: Streamlit UI
====================================
Three screens on a single page:
  1. Login sidebar (role-switcher for RBAC demo)
  2. Query Interface
  3. Answer + Citation Panel (fields shown depend on role)

Role visibility (mirrors response_shaping.py on the backend):
  viewer  → answer, confidence, citations (doc/page only)
  analyst → + DSL object, SQL query, SQL result, full citation scores
  admin   → + latency, tokens used, cache hit
"""

import json
import streamlit as st
from utils.api_client import login, query, AuthError, APIError

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="LedgerMind",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
def _init_state():
    defaults = {
        "token":      None,
        "role":       None,
        "tenant_id":  None,
        "email":      None,
        "result":     None,
        "query_text": "",
        "error":      None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ---------------------------------------------------------------------------
# Role badge styling
# ---------------------------------------------------------------------------
_ROLE_COLOUR = {"admin": "#e74c3c", "analyst": "#2980b9", "viewer": "#27ae60"}
_ROLE_LABEL  = {"admin": "🔴 Admin", "analyst": "🔵 Analyst", "viewer": "🟢 Viewer"}

def _role_badge(role: str) -> str:
    colour = _ROLE_COLOUR.get(role, "#888")
    label  = _ROLE_LABEL.get(role, role)
    return (
        f'<span style="background:{colour};color:white;padding:3px 10px;'
        f'border-radius:12px;font-size:0.8rem;font-weight:600;">{label}</span>'
    )

# ---------------------------------------------------------------------------
# Sidebar — login / user info
# ---------------------------------------------------------------------------
def render_sidebar():
    with st.sidebar:
        st.markdown("## 📊 LedgerMind")
        st.markdown("*Financial Intelligence Platform*")
        st.divider()

        if st.session_state.token is None:
            # ── Login form ─────────────────────────────────────────────────
            st.markdown("### Sign In")
            st.caption("Use seeded demo credentials")

            with st.form("login_form", clear_on_submit=False):
                email    = st.text_input("Email", placeholder="admin@alpha.ledgermind.test")
                password = st.text_input("Password", type="password", placeholder="demo1234")
                submitted = st.form_submit_button("Login", use_container_width=True)

            if submitted:
                if not email or not password:
                    st.error("Enter both email and password.")
                else:
                    with st.spinner("Authenticating…"):
                        try:
                            data = login(email, password)
                            st.session_state.token     = data["access_token"]
                            st.session_state.role      = data["role"]
                            st.session_state.tenant_id = data["tenant_id"]
                            st.session_state.email     = email
                            st.session_state.result    = None
                            st.rerun()
                        except AuthError as e:
                            st.error(str(e))
                        except APIError as e:
                            st.error(str(e))

            st.divider()
            st.markdown("**Demo accounts**")
            st.caption(
                "admin@alpha — full internals\n\n"
                "analyst@alpha — DSL + SQL visible\n\n"
                "viewer@alpha — answer only\n\n"
                "admin@beta — cross-tenant isolation demo\n\n"
                "*Password: `demo1234` for all*"
            )

        else:
            # ── Logged-in user info ────────────────────────────────────────
            st.markdown(f"**{st.session_state.email}**")
            st.markdown(_role_badge(st.session_state.role), unsafe_allow_html=True)
            st.caption(f"Tenant: `{st.session_state.tenant_id[:8]}…`")
            st.divider()

            # Role switcher hint
            st.markdown("**Switch role**")
            st.caption("Log out and log back in with a different seeded account to demo RBAC.")

            if st.button("Logout", use_container_width=True):
                for k in ["token", "role", "tenant_id", "email", "result", "error"]:
                    st.session_state[k] = None
                st.session_state.query_text = ""
                st.rerun()

            st.divider()
            st.markdown("**What this role sees**")
            role = st.session_state.role
            if role == "viewer":
                st.markdown("✅ Answer\n\n✅ Confidence\n\n✅ Citations (doc/page)\n\n❌ DSL object\n\n❌ SQL query\n\n❌ Scores")
            elif role == "analyst":
                st.markdown("✅ Answer\n\n✅ Confidence\n\n✅ Full citations + scores\n\n✅ DSL object\n\n✅ SQL query + result\n\n❌ Latency / token usage")
            else:  # admin
                st.markdown("✅ Everything")

# ---------------------------------------------------------------------------
# Main area helpers
# ---------------------------------------------------------------------------

def _confidence_badge(tier: str) -> str:
    colours = {"high": "#27ae60", "medium": "#f39c12", "low": "#e74c3c"}
    colour  = colours.get(tier, "#888")
    return (
        f'<span style="background:{colour};color:white;padding:3px 10px;'
        f'border-radius:12px;font-size:0.85rem;font-weight:600;">'
        f'{tier.upper()}</span>'
    )


def render_answer_panel(result: dict):
    st.markdown("### 💬 Answer")

    if result.get("is_blocked"):
        st.warning(
            f"**Query blocked — SEBI Compliance**\n\n"
            f"{result.get('block_reason', 'This query was blocked by the Prompt Shield.')}"
        )
        return

    response_text = result.get("response_text")
    if not response_text:
        error = result.get("error", "unknown")
        st.error(f"No answer returned. Internal error: `{error}`")
        return

    # Answer text
    st.markdown(response_text)

    # Confidence badge
    tier = result.get("confidence_tier", "low")
    col1, col2 = st.columns([1, 4])
    with col1:
        st.markdown("**Confidence**")
    with col2:
        st.markdown(_confidence_badge(tier), unsafe_allow_html=True)

    # Path + CRAG info (shown if present)
    path = result.get("path")
    if path:
        meta_parts = [f"Path: `{path}`"]
        if result.get("crag_triggered"):
            meta_parts.append(f"CRAG triggered ({result.get('crag_count', 0)} retry)")
        st.caption(" · ".join(meta_parts))

    # Admin extras
    if st.session_state.role == "admin":
        cols = st.columns(3)
        with cols[0]:
            st.metric("Latency", f"{result.get('latency_ms', 0)} ms")
        with cols[1]:
            st.metric("Tokens", result.get("tokens_used", 0))
        with cols[2]:
            st.metric("Cache hit", "Yes" if result.get("cache_hit") else "No")


def render_citation_panel(result: dict):
    citations = result.get("citations", [])
    role      = st.session_state.role

    st.markdown("### 📎 Citations")

    if not citations:
        st.caption("No document citations for this query (quantitative path uses SQL, not retrieval).")
        return

    for i, c in enumerate(citations, 1):
        with st.expander(f"Source {i} — {c.get('company')} · Page {c.get('page_number')} · {c.get('fiscal_year')}"):
            cols = st.columns(2)
            with cols[0]:
                st.markdown(f"**Document ID**\n\n`{c.get('doc_id', '—')}`")
                st.markdown(f"**Financial type**\n\n`{c.get('financial_type', '—')}`")
            with cols[1]:
                st.markdown(f"**Filing date**\n\n`{c.get('filing_date', '—')}`")
                st.markdown(f"**Page**\n\n`{c.get('page_number', '—')}`")

            # Analyst / admin also see retrieval scores
            if role in ("analyst", "admin"):
                st.markdown(f"**Reranker score:** `{c.get('reranker_score', '—')}`")
                preview = c.get("text_preview", "")
                if preview:
                    st.markdown("**Text preview**")
                    st.markdown(f"> {preview[:300]}…" if len(preview) > 300 else f"> {preview}")


def render_quant_panel(result: dict):
    """DSL + SQL panel — analyst and admin only."""
    role = st.session_state.role
    if role not in ("analyst", "admin"):
        return

    dsl = result.get("dsl_object")
    sql = result.get("sql_query")
    sql_result = result.get("sql_result")

    if not dsl and not sql:
        return  # semantic path — nothing to show here

    st.markdown("### 🔬 Query Internals")

    if dsl:
        with st.expander("DSL Object (LLM → structured intent)", expanded=True):
            st.json(dsl)

    if sql:
        with st.expander("SQL Compiled (deterministic, LLM never touches this)", expanded=True):
            st.code(sql, language="sql")
            verified = result.get("sql_verified", False)
            st.caption(f"Verified: {'✅ Yes' if verified else '❌ No'}")

    if sql_result:
        with st.expander("SQL Result (raw rows from PostgreSQL)", expanded=False):
            st.json(sql_result)


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

def render_main():
    st.markdown("# LedgerMind")
    st.markdown("*Deterministic financial intelligence for Indian capital markets*")
    st.divider()

    if st.session_state.token is None:
        st.info("👈 Sign in from the sidebar to start querying.")
        return

    # ── Query input ─────────────────────────────────────────────────────────
    st.markdown("### Ask a question")

    example_queries = [
        "What was ETERNAL consolidated revenue for FY26?",
        "Who grew revenue faster in FY26, Eternal or Paytm?",
        "What is ETERNAL's standalone total expenses for FY25?",
        "What are the key risk factors disclosed by ETERNAL regarding food delivery?",
    ]
    selected = st.selectbox(
        "Try an example query or type your own below",
        ["(type your own)"] + example_queries,
        index=0,
    )

    if selected != "(type your own)":
        st.session_state.query_text = selected

    question = st.text_area(
        "Your question",
        value=st.session_state.query_text,
        height=80,
        placeholder="What was ETERNAL's consolidated revenue for FY26?",
        label_visibility="collapsed",
    )

    col1, col2 = st.columns([1, 6])
    with col1:
        submit = st.button("Ask", type="primary", use_container_width=True)
    with col2:
        if st.session_state.result:
            if st.button("Clear", use_container_width=False):
                st.session_state.result = None
                st.session_state.query_text = ""
                st.rerun()

    # ── Query execution ──────────────────────────────────────────────────────
    if submit:
        if not question.strip():
            st.warning("Enter a question first.")
        else:
            st.session_state.query_text = question
            st.session_state.error = None
            with st.spinner("Thinking…"):
                try:
                    result = query(st.session_state.token, question)
                    st.session_state.result = result
                except AuthError as e:
                    st.session_state.token  = None
                    st.session_state.error  = str(e)
                    st.session_state.result = None
                    st.rerun()
                except APIError as e:
                    st.session_state.error  = str(e)
                    st.session_state.result = None

    # ── Error display ────────────────────────────────────────────────────────
    if st.session_state.error:
        st.error(st.session_state.error)

    # ── Results ──────────────────────────────────────────────────────────────
    if st.session_state.result:
        result = st.session_state.result
        st.divider()

        render_answer_panel(result)
        st.divider()

        # Quant internals — analyst/admin only
        render_quant_panel(result)
        if st.session_state.role in ("analyst", "admin") and result.get("dsl_object"):
            st.divider()

        render_citation_panel(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
render_sidebar()
render_main()