"""
Authentication dependency for Sentinel-Q API endpoints.

Week 2: Bearer JWT validation.
Week 3: multi-tenant - verify_jwt_token returns UserTokenData with tenant_id and role.
Week 3.5: the verified identity is stored in request.state.token_data so the
          rate limiter key_func can reuse it without re-decoding the JWT.
"""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from sentinel.infrastructure.jwt_service import UserTokenData, verify_token

_bearer_scheme = HTTPBearer(auto_error=False)


def verify_jwt_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> UserTokenData:
    """
    FastAPI dependency that validates a Bearer JWT.

    Returns UserTokenData (sub, tenant_id, role) when valid.
    Raises 401 if missing or 403 if invalid or expired.

    Side effect: stores the verified UserTokenData in request.state.token_data
    so the rate limiter key_func can use the already-validated identity without
    decoding the JWT again.
    """
    if credentials is None:
        logger.warning("\U0001f510 Unauthorized request - missing Bearer token.")
        raise HTTPException(status_code=401, detail="Missing authentication token")

    token_data = verify_token(credentials.credentials)
    if token_data is None:
        logger.warning("\U0001f510 Unauthorized request - invalid or expired JWT.")
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    # Store for the rate limiter key_func to avoid reprocessing the JWT.
    request.state.token_data = token_data
    return token_data

