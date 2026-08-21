"""
Rate limiter configuration for Sentinel-Q.

Uses SlowAPI (in-memory backend) — no Redis required.

Key functions:
  - Unauthenticated endpoints (/auth/*): keyed by client IP via get_remote_address (default)
  - Authenticated endpoints (/targets, /admin/*): keyed by "tenant_id:sub"

Execution order guarantee (FastAPI + SlowAPI decorator pattern):
  FastAPI resolves ALL Depends() BEFORE calling the decorated handler.
  This means verify_jwt_token runs and stores token_data in request.state
  before _get_authenticated_key is ever invoked.

  Consequence: _get_authenticated_key ALWAYS reads verified identity.
  A request with an invalid or forged JWT is rejected by verify_jwt_token
  (raises 401/403) and the key_func is never called — no bucket is created.

  The IP fallback in _get_authenticated_key is defensive dead code for
  protected endpoints, kept to guard against unexpected call paths.
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _get_authenticated_key(request: Request) -> str:
    """
    Rate limit key for authenticated endpoints.

    Reads the verified UserTokenData already stored in request.state by
    verify_jwt_token (auth.py). Returns "tenant_id:sub" to isolate quotas
    per user + tenant combination.

    Falls back to client IP only if request.state.token_data is not set,
    which cannot happen on correctly protected endpoints (the dependency
    would have raised 401/403 before reaching this function).
    """
    token_data = getattr(request.state, "token_data", None)
    if token_data is not None:
        return f"{token_data.tenant_id}:{token_data.sub}"
    # Defensive fallback — unreachable on authenticated endpoints.
    return get_remote_address(request)


# Global limiter instance.
# Default key function: IP-based (overridden per-endpoint via key_func arg in @limiter.limit).
limiter = Limiter(key_func=get_remote_address, default_limits=[])
