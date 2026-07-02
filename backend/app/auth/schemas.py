from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: str
    expires_in_hours: int = 2


class TokenPayload(BaseModel):
    sub: str          # user_id
    tenant_id: str
    role: str