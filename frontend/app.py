import streamlit as st
import httpx
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="LedgerMind", page_icon="📊", layout="wide")
st.title("📊 LedgerMind")
st.caption("Financial Intelligence Platform — Indian Capital Markets")

st.subheader("System Health")

try:
    resp = httpx.get(f"{BACKEND_URL}/health", timeout=5.0)
    data = resp.json()

    overall = data.get("status")
    if overall == "healthy":
        st.success("All systems operational")
    else:
        st.warning("One or more services degraded")

    services = data.get("services", {})
    cols = st.columns(len(services))
    for col, (name, status) in zip(cols, services.items()):
        with col:
            icon = "✅" if status == "ok" else "❌"
            st.metric(label=name.upper(), value=f"{icon} {status}")

except Exception as e:
    st.error(f"Backend unreachable: {e}")

st.divider()
st.info("Phase 1 — Infrastructure only. No application logic yet.")