"""
JWT token creation and verification for Sentinel-Q.

Handles HS256 signed tokens with configurable expiry.
Called by auth.py (dependency) and main.py (token endpoint).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from loguru import logger

from sentinel.config import settings


@dataclass
class UserTokenData:
    """Decoded JWT payload for authenticated API users."""
    sub: str
    tenant_id: int
    role: str


def create_access_token(
    subject: str,
    tenant_id: int,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token with tenant and role claims.

    Args:
        subject: Identity claim (username).
        tenant_id: Tenant the user belongs to.
        role: User role (e.g. 'admin', 'viewer').
        expires_delta: Override default expiry. Defaults to JWT_EXPIRE_MINUTES.

    Returns:
        Encoded JWT string.
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    payload = {"sub": subject, "tenant_id": tenant_id, "role": role, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> Optional[UserTokenData]:
    """
    Decode and verify a JWT token.

    Args:
        token: Raw JWT string from Authorization header.

    Returns:
        UserTokenData if valid, None if invalid or expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        sub: Optional[str] = payload.get("sub")
        tenant_id: Optional[int] = payload.get("tenant_id")
        role: Optional[str] = payload.get("role")
        if sub is None or tenant_id is None or role is None:
            return None
        return UserTokenData(sub=sub, tenant_id=int(tenant_id), role=role)
    except JWTError as exc:
        logger.debug(f"JWT verification failed: {exc}")
        return None
