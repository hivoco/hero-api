import hmac
import bcrypt
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Two roles: a regular "admin" can manage jobs (the home page), while a
# "superadmin" can additionally edit the pipeline config / vision / backend
# settings pages. The role is stamped into the JWT at login.
ROLE_ADMIN = "admin"
ROLE_SUPERADMIN = "superadmin"

security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def create_access_token(username: str, role: str = ROLE_ADMIN) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    token = credentials.credentials

    # Server-to-server internal key (the pipeline worker uses this)
    if hmac.compare_digest(token, settings.INTERNAL_API_KEY):
        return "internal_service"

    # Otherwise treat it as a JWT issued by the admin login
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return username
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


def require_superadmin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Dependency for edit endpoints that only a superadmin may call. The
    trusted internal service key is also allowed (the worker runs as full
    access). A regular admin's token is rejected with 403."""
    token = credentials.credentials

    if hmac.compare_digest(token, settings.INTERNAL_API_KEY):
        return "internal_service"

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if payload.get("role") != ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super-admin access required to edit this.",
        )
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return username
