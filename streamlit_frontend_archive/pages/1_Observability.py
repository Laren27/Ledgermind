"""
LedgerMind — Observability Dashboard
======================================
Admin-only page. Reads from GET /api/metrics (backend aggregates audit_log).
Frontend does zero computation — renders pre-aggregated data only.

Streamlit multipage convention: files in frontend/pages/ auto-appear in sidebar.
Named 1_Observability.py so it appears as the first item after the main page.
"""

import streamlit as st
import pandas as pd
from utils.api_client import get_metrics, AuthError, APIError

st.set_page_config(
    page_title="LedgerMind — Observability",
    page_icon="📈",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Auth gate — admin only
# ---------------------------------------------------------------------------
if not st.session_state.get("token"):
    st.warning("Please log in from the main page first.")
    st.stop()

if st.session_state.get("role") != "admin":
    st.error("This dashboard is restricted to admin users.")
    st.caption(f"You are logged in as: **{st.session_state.get('role', 'unknown')}**")
    st.stop()

# ---------------------------------------------------------------------------
# Fetch metrics
# ---------------------------------------------------------------------------
st.markdown("# 📈 Observability Dashboard")
st.markdown(
    f"*Tenant: `{st.session_state.get('tenant_id', '')[:8]}…` "
    f"· User: `{st.session_state.get('email', '')}`*"
)
st.divider()

with st.spinner("Loading metrics…"):
    try:
        data = get_metrics(st.session_state["token"])
    except AuthError as e:
        st.error(str(e))
        st.stop()
    except APIError as e:
        st.error(str(e))
        st.stop()

summary             = data["summary"]
path_distribution   = data["path_distribution"]
volume_by_day       = data["volume_by_day"]
confidence_dist     = data["confidence_distribution"]
latency_by_path     = data["avg_latency_by_path"]

# ---------------------------------------------------------------------------
# Panel 1 — Summary KPIs
# ---------------------------------------------------------------------------
st.markdown("### System Summary")

cols = st.columns(5)
with cols[0]:
    st.metric("Total Queries", summary["total_queries"])
with cols[1]:
    st.metric("Avg Latency", f"{summary['avg_latency_ms']:.0f} ms")
with cols[2]:
    st.metric("P95 Latency", f"{summary['p95_latency_ms']:.0f} ms")
with cols[3]:
    st.metric("Cache Hit Rate", f"{summary['cache_hit_rate_pct']:.1f}%")
with cols[4]:
    st.metric("Refusal Rate", f"{summary['refusal_rate_pct']:.1f}%",
              help="% of queries returned with confidence_tier=low (system refused to answer)")

st.divider()

# ---------------------------------------------------------------------------
# Panel 2 — Query Volume Over Time + Path Distribution (side by side)
# ---------------------------------------------------------------------------
col_left, col_right = st.columns([3, 2])

with col_left:
    st.markdown("### Query Volume Over Time")
    if volume_by_day:
        df_vol = pd.DataFrame(volume_by_day)
        df_vol["date"] = pd.to_datetime(df_vol["date"])
        df_vol = df_vol.set_index("date")
        st.line_chart(df_vol["count"], use_container_width=True)
    else:
        st.caption("No query volume data yet.")

with col_right:
    st.markdown("### Path Distribution")
    if path_distribution:
        df_path = pd.DataFrame(path_distribution)
        # Streamlit native bar chart — clean, no extra dependencies
        df_path = df_path.set_index("path")
        st.bar_chart(df_path["count"], use_container_width=True)
        # Also show as table for exact counts
        st.dataframe(
            df_path.rename(columns={"count": "Queries"}),
            use_container_width=True,
        )
    else:
        st.caption("No path distribution data yet.")

st.divider()

# ---------------------------------------------------------------------------
# Panel 3 — Confidence Distribution + Latency by Path (side by side)
# ---------------------------------------------------------------------------
col_left2, col_right2 = st.columns(2)

with col_left2:
    st.markdown("### Retrieval Confidence Distribution")
    if confidence_dist:
        df_conf = pd.DataFrame(confidence_dist).set_index("tier")
        st.bar_chart(df_conf["count"], use_container_width=True)

        # Colour-coded summary table
        for row in confidence_dist:
            tier  = row["tier"]
            count = row["count"]
            total = summary["total_queries"]
            pct   = (count / total * 100) if total > 0 else 0
            colour = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(tier, "⚪")
            st.caption(f"{colour} **{tier.upper()}**: {count} queries ({pct:.1f}%)")
    else:
        st.caption("No confidence data yet.")

with col_right2:
    st.markdown("### Avg Latency by Path")
    if latency_by_path:
        df_lat = pd.DataFrame(latency_by_path).set_index("path")
        st.bar_chart(df_lat["avg_ms"], use_container_width=True)

        for row in latency_by_path:
            st.caption(
                f"**{row['path']}**: {row['avg_ms']:.0f} ms avg"
            )
    else:
        st.caption("No latency data yet.")

st.divider()

# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
if st.button("🔄 Refresh", use_container_width=False):
    st.rerun()

st.caption(
    "Data sourced from audit_log · RLS-scoped to your tenant · "
    "Admin role required · Refresh to update"
)