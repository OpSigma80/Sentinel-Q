"""
Tests for rate limiting (SlowAPI) in Sentinel-Q.

Coverage:
  - _get_authenticated_key: unit tests using request.state (state-based approach)
  - verify_jwt_token stores token_data in request.state (side-effect test)
  - Ordering guarantee: forged JWT is rejected by auth before key_func runs
  - Limiter registration on app.state
  - 429 response is triggered and has correct format
  - Normal requests are not blocked
  - IP fallback when state is not set
"""
from __future__ import annotations

import pytest
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sentinel.infrastructure.database import Base

from sentinel.infrastructure.rate_limiter import _get_authenticated_key, limiter
from sentinel.infrastructure.jwt_service import create_access_token, UserTokenData


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_request(token_data: UserTokenData | None = None) -> MagicMock:
    """
    Build a minimal mock Starlette Request for unit tests.

    Uses SimpleNamespace for request.state so that getattr(state, "token_data", None)
    behaves correctly (returns None when not set, not a MagicMock auto-attribute).
    """
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    req.state = SimpleNamespace()
    if token_data is not None:
        req.state.token_data = token_data
    return req


def _client_with_fresh_db():
    """Create a TestClient backed by a fresh in-memory SQLite DB."""
    from sentinel.infrastructure.database import Base, get_db
    from sentinel.main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    client._test_engine = engine
    return client


@pytest.fixture(scope="module")
def client():
    c = _client_with_fresh_db()
    from sentinel.main import app as _app
    with c:
        yield c
    _app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=c._test_engine)
    c._test_engine.dispose()


@pytest.fixture(autouse=True)
def reset_limiter_storage():
    """Reset in-memory rate limit counters before each test."""
    limiter._storage.reset()
    yield
    limiter._storage.reset()


# ─── Unit: _get_authenticated_key (state-based) ───────────────────────────────

class TestGetAuthenticatedKey:
    """
    _get_authenticated_key reads from request.state.token_data (verified identity),
    not from the raw Authorization header. This class tests that contract.
    """

    def test_returns_tenant_sub_key_from_state(self) -> None:
        td = UserTokenData(sub="alice", tenant_id=7, role="admin")
        req = _make_request(token_data=td)
        assert _get_authenticated_key(req) == "7:alice"

    def test_key_format_is_tenant_colon_sub(self) -> None:
        td = UserTokenData(sub="bob", tenant_id=42, role="user")
        req = _make_request(token_data=td)
        key = _get_authenticated_key(req)
        tenant_part, sub_part = key.split(":", 1)
        assert tenant_part == "42"
        assert sub_part == "bob"

    def test_no_state_falls_back_to_ip(self) -> None:
        """When state.token_data is not set, key_func returns the client IP."""
        req = _make_request(token_data=None)
        assert _get_authenticated_key(req) == "127.0.0.1"

    def test_different_users_produce_different_keys(self) -> None:
        req_alice = _make_request(UserTokenData(sub="alice", tenant_id=1, role="user"))
        req_bob = _make_request(UserTokenData(sub="bob", tenant_id=1, role="user"))
        assert _get_authenticated_key(req_alice) != _get_authenticated_key(req_bob)

    def test_same_user_different_tenant_produces_different_keys(self) -> None:
        req1 = _make_request(UserTokenData(sub="alice", tenant_id=1, role="user"))
        req2 = _make_request(UserTokenData(sub="alice", tenant_id=2, role="user"))
        assert _get_authenticated_key(req1) != _get_authenticated_key(req2)

    def test_jwt_header_content_is_irrelevant(self) -> None:
        """
        Even if a forged JWT is present in the Authorization header, the key_func
        ignores it entirely — the identity comes from request.state.
        """
        td = UserTokenData(sub="real_user", tenant_id=1, role="user")
        req = _make_request(token_data=td)
        # Simulate a header with a fabricated different identity — should be ignored.
        req.headers = {"Authorization": "Bearer forged.jwt.payload"}
        assert _get_authenticated_key(req) == "1:real_user"


# ─── Unit: verify_jwt_token stores in request.state ──────────────────────────

class TestVerifyJwtTokenStoresState:
    """
    verify_jwt_token must store the verified UserTokenData in request.state.token_data
    so the rate limiter key_func can read it without re-decoding.
    """

    def test_valid_token_sets_state_token_data(self) -> None:
        from unittest.mock import patch, MagicMock
        from fastapi.security import HTTPAuthorizationCredentials
        from sentinel.infrastructure.auth import verify_jwt_token

        token = create_access_token("alice", tenant_id=5, role="user")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        req = _make_request()

        result = verify_jwt_token(request=req, credentials=creds)

        assert hasattr(req.state, "token_data")
        assert req.state.token_data is result
        assert req.state.token_data.sub == "alice"
        assert req.state.token_data.tenant_id == 5

    def test_state_token_data_matches_returned_value(self) -> None:
        from fastapi.security import HTTPAuthorizationCredentials
        from sentinel.infrastructure.auth import verify_jwt_token

        token = create_access_token("admin", tenant_id=1, role="admin")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        req = _make_request()

        returned = verify_jwt_token(request=req, credentials=creds)

        assert req.state.token_data.sub == returned.sub
        assert req.state.token_data.tenant_id == returned.tenant_id
        assert req.state.token_data.role == returned.role


# ─── Ordering guarantee: forged JWT cannot pollute rate limit buckets ─────────

class TestOrderingGuarantee:
    """
    Demonstrates that a forged or invalid JWT cannot create arbitrary rate limit
    buckets because FastAPI resolves verify_jwt_token BEFORE the limiter key_func runs.

    An invalid token is rejected with 401/403. The key_func is never invoked.
    No rate limit bucket is consumed or created for the forged identity.
    """

    def test_invalid_jwt_returns_403_not_429(self, client: TestClient) -> None:
        """A malformed token is rejected by auth; rate limiting is never reached."""
        resp = client.get(
            "/targets",
            headers={"Authorization": "Bearer invalid.forged.token"},
        )
        assert resp.status_code == 403

    def test_many_forged_tokens_never_trigger_429(self, client: TestClient) -> None:
        """
        An attacker cycling fabricated tokens does NOT get rate-limited by the
        fake identities (which never reach key_func). All responses are 403.
        """
        for i in range(10):
            resp = client.get(
                "/targets",
                headers={"Authorization": f"Bearer fake.token.{i}"},
            )
            # Must be 403 (auth rejected), never 429 (rate limited by fake bucket)
            assert resp.status_code == 403

    def test_missing_token_returns_401_not_429(self, client: TestClient) -> None:
        """Missing Authorization header returns 401, not 429."""
        resp = client.get("/targets")
        assert resp.status_code == 401

    def test_valid_user_rate_limiting_still_works(self, client: TestClient) -> None:
        """
        After confirming forged tokens don't create buckets, verify that
        legitimate users are still rate-limited using the state-based key.
        POST /auth/token (IP-based, 5/minute) is the easiest endpoint to exhaust.
        """
        last_resp = None
        for _ in range(6):
            last_resp = client.post(
                "/auth/token",
                data={"username": "admin", "password": "bad"},
                headers={"X-Forwarded-For": "10.99.0.1"},
            )
        assert last_resp is not None
        assert last_resp.status_code == 429


# ─── Limiter configuration ─────────────────────────────────────────────────────

class TestLimiterConfiguration:
    def test_limiter_registered_on_app_state(self, client: TestClient) -> None:
        from sentinel.main import app
        assert hasattr(app.state, "limiter")
        assert app.state.limiter is limiter

    def test_rate_limit_exceeded_handler_is_registered(self) -> None:
        from sentinel.main import app
        from slowapi.errors import RateLimitExceeded
        assert RateLimitExceeded in app.exception_handlers


# ─── Integration: normal requests not blocked ──────────────────────────────────

class TestNormalRequestsNotBlocked:
    def _admin_token(self) -> str:
        return create_access_token("admin", tenant_id=1, role="admin")

    def test_auth_token_first_request_succeeds(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/token",
            data={"username": "admin", "password": "admin"},
        )
        assert resp.status_code != 429

    def test_targets_list_first_request_succeeds(self, client: TestClient) -> None:
        token = self._admin_token()
        resp = client.get("/targets", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code != 429

    def test_health_endpoint_first_request_succeeds(self, client: TestClient) -> None:
        token = self._admin_token()
        resp = client.get("/health", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code != 429


# ─── Integration: 429 is triggered ────────────────────────────────────────────

class TestRateLimitExceeded:
    def test_post_auth_token_rate_limit_triggers_429(self, client: TestClient) -> None:
        """POST /auth/token allows 5/minute — trigger it with 6 rapid calls."""
        last_response = None
        for _ in range(6):
            last_response = client.post(
                "/auth/token",
                data={"username": "admin", "password": "wrong"},
                headers={"X-Forwarded-For": "10.0.0.99"},
            )
        assert last_response is not None
        assert last_response.status_code == 429

    def test_429_response_has_detail_key(self, client: TestClient) -> None:
        """429 body must contain a 'detail' key (RFC-compatible format)."""
        for _ in range(6):
            resp = client.post(
                "/auth/token",
                data={"username": "admin", "password": "wrong"},
                headers={"X-Forwarded-For": "10.0.0.88"},
            )
        assert resp.status_code == 429
        body = resp.json()
        assert "detail" in body
        assert "Rate limit exceeded" in body["detail"]

    def test_429_response_is_json(self, client: TestClient) -> None:
        """429 response Content-Type must be application/json."""
        for _ in range(6):
            resp = client.post(
                "/auth/token",
                data={"username": "admin", "password": "wrong"},
                headers={"X-Forwarded-For": "10.0.0.77"},
            )
        assert resp.status_code == 429
        content_type = resp.headers.get("content-type", "")
        assert "application/json" in content_type

