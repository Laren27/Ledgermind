"""
Thin wrapper around LedgerMind FastAPI calls.
All HTTP logic lives here so app.py stays readable.

API_BASE_URL defaults to http://backend:8000 (Docker internal network).
Override via environment variable for local dev outside Docker:
  export API_BASE_URL=http://localhost:8000
"""
import os
import requests

API_BASE = os.getenv("API_BASE_URL", "http://backend:8000")
TIMEOUT  = 60  # seconds — quant queries can take 10-30s on free-tier Gemini


class AuthError(Exception):
    """Raised on 401/403 from the API."""
    pass


class APIError(Exception):
    """Raised on unexpected API errors."""
    pass


def login(email: str, password: str) -> dict:
    """
    POST /auth/login
    Returns: {"access_token": ..., "role": ..., "tenant_id": ..., "expires_in_hours": 2}
    Raises: AuthError on bad credentials, APIError on server error.
    """
    try:
        resp = requests.post(
            f"{API_BASE}/auth/login",
            json={"email": email, "password": password},
            timeout=TIMEOUT,
        )
    except requests.exceptions.ConnectionError:
        raise APIError("Cannot reach the LedgerMind backend. Is it running?")

    if resp.status_code == 401:
        raise AuthError("Invalid email or password.")
    if resp.status_code != 200:
        raise APIError(f"Login failed (HTTP {resp.status_code}): {resp.text}")

    return resp.json()


def query(token: str, question: str) -> dict:
    """
    POST /api/query
    Returns the role-filtered QueryResponse dict.
    Raises: AuthError on 401/403, APIError on server error.
    """
    try:
        resp = requests.post(
            f"{API_BASE}/api/query",
            json={"query": question},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
    except requests.exceptions.ConnectionError:
        raise APIError("Cannot reach the LedgerMind backend. Is it running?")

    if resp.status_code in (401, 403):
        raise AuthError("Session expired. Please log in again.")
    if resp.status_code == 500:
        raise APIError("Backend error — check container logs.")
    if resp.status_code != 200:
        raise APIError(f"Query failed (HTTP {resp.status_code}): {resp.text}")

    return resp.json()

    
def get_metrics(token: str) -> dict:
    """
    GET /api/metrics — admin only.
    Returns pre-aggregated observability data for the Streamlit dashboard.
    """
    try:
        resp = requests.get(
            f"{API_BASE}/api/metrics",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
    except requests.exceptions.ConnectionError:
        raise APIError("Cannot reach the LedgerMind backend. Is it running?")

    if resp.status_code in (401, 403):
        raise AuthError("Session expired or insufficient role (admin required).")
    if resp.status_code != 200:
        raise APIError(f"Metrics fetch failed (HTTP {resp.status_code}): {resp.text}")

    return resp.json()