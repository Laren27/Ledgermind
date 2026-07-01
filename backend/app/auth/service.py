from fastapi import HTTPException, status
from app.db.session import db_transaction
from app.core.security import verify_password, create_access_token


def authenticate_user(email: str, password: str) -> dict:
    """
    Looks up a seeded user by email and verifies password.

    Uses db_transaction(tenant_id=None) -- this is the ONE place in the app
    that queries with no RLS tenant context set. It relies on the
    auth_bootstrap_lookup policy (migration 006) which only permits SELECT
    when app.tenant_id is unset. Do not reuse this pattern elsewhere.
    """
    with db_transaction(tenant_id=None) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, tenant_id, role, password_hash FROM users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()

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