from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.core.config import settings
from app.core.admin_auth import (
    verify_password,
    create_access_token,
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str = ROLE_ADMIN


@router.post("/login", response_model=LoginResponse)
def admin_login(body: LoginRequest):
    # Superadmin credential first, then the regular admin.
    if body.username == settings.SUPERADMIN_USERNAME and verify_password(
        body.password, settings.SUPERADMIN_PASSWORD_HASH
    ):
        role = ROLE_SUPERADMIN
    elif body.username == settings.ADMIN_USERNAME and verify_password(
        body.password, settings.ADMIN_PASSWORD_HASH
    ):
        role = ROLE_ADMIN
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(body.username, role)
    return LoginResponse(access_token=token, role=role)
