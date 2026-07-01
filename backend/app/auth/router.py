from fastapi import APIRouter
from app.auth.schemas import LoginRequest, TokenResponse
from app.auth.service import authenticate_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    result = authenticate_user(body.email, body.password)
    return TokenResponse(**result)