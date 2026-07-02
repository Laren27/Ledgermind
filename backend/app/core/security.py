"""
Password hashing and JWT helpers.

NOTE: We use the `bcrypt` library directly, NOT passlib. passlib's CryptContext
reads bcrypt.__about__.__version__ to detect the backend version, which was
removed in bcrypt>=4.1 -- this breaks passlib's bcrypt backend on any current
pip install. Calling bcrypt.hashpw/checkpw directly avoids the dependency
entirely. Do not re-add passlib for this.
"""
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from app.core.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 2


def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(user_id: str, tenant_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    # Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError on failure --
    # caller (dependencies.py) is responsible for turning these into HTTP 401.
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])