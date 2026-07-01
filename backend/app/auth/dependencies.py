import jwt
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import decode_access_token

bearer_scheme = HTTPBearer()

ROLE_RANK = {"viewer": 0, "analyst": 1, "admin": 2}


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Decodes and validates the JWT, attaches the payload to request.state.user
    so downstream dependencies (get_db_conn) can read tenant_id without
    re-decoding the token. This dependency must run before get_db_conn on
    every protected route.
    """
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired, please log in again",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user = {
        "user_id": payload["sub"],
        "tenant_id": payload["tenant_id"],
        "role": payload["role"],
    }
    request.state.user = user
    return user


def require_role(minimum_role: str):
    """
    Route-level RBAC. Usage: Depends(require_role("analyst"))
    Role hierarchy: viewer(0) < analyst(1) < admin(2) -- higher roles pass
    checks for lower minimums.
    """
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if ROLE_RANK[user["role"]] < ROLE_RANK[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role '{minimum_role}' or higher",
            )
        return user
    return checker