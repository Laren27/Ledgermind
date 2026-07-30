import logging

import psycopg2
from fastapi import HTTPException, status

from app.core.security import create_access_token, verify_password
from app.db.session import db_transaction

logger = logging.getLogger(__name__)


def authenticate_user(email: str, password: str) -> dict:
    """
    Looks up a seeded user by email and verifies password.

    Uses db_transaction(tenant_id=None) -- this is the ONE place in the app
    that queries with no RLS tenant context set. It relies on the
    auth_bootstrap_lookup policy (migration 006) which only permits SELECT
    when app.tenant_id is unset. Do not reuse this pattern elsewhere.
    """
    try:
        with db_transaction(tenant_id=None) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, tenant_id, role, password_hash FROM users WHERE email = %s",
                    (email,),
                )
                row = cur.fetchone()
    except psycopg2.Error as e:
        # Single-line by design. Render's log stream truncates multi-line
        # tracebacks, which cost several rounds of debugging on 2026-07-30
        # while chasing intermittent login 500s under concurrent load.
        # pgcode names the Postgres error class exactly, with no traceback.
        logger.error(
            "LOGIN DB FAILURE pgcode=%s pgerror=%s exc=%s msg=%s",
            getattr(e, "pgcode", None),
            (getattr(e, "pgerror", "") or "").replace("\n", " ")[:300],
            type(e).__name__,
            str(e).replace("\n", " ")[:300],
        )
        # 503, not 500: a transient database failure is retryable and is not
        # a defect in the request. The eval runner and the frontend can both
        # act on that distinction; a 500 tells them nothing.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication temporarily unavailable. Please retry.",
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    user_id, tenant_id, role, password_hash = row

    if not verify_password(password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(user_id=str(user_id), tenant_id=str(tenant_id), role=role)

    return {
        "access_token": token,
        "role": role,
        "tenant_id": str(tenant_id),
    }
