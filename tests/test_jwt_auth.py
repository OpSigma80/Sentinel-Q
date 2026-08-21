"""
Tests for JWT authentication — Week 2.

Covers:
  - Token creation and expiry
  - verify_token valid / invalid / expired
  - verify_jwt_token FastAPI dependency
  - POST /auth/token endpoint (happy path + bad credentials)
  - Protected endpoint rejects missing / invalid token
"""

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentinel.infrastructure.jwt_service import create_access_token, verify_token


# ─── jwt_service unit tests ───────────────────────────────────────────────────

class TestCreateAccessToken:
    def test_returns_string(self) -> None:
        token = create_access_token("admin", tenant_id=1, role="admin")
        assert isinstance(token, str)
        assert len(token) > 20

    def test_subject_is_recovered(self) -> None:
        token = create_access_token("admin", tenant_id=1, role="admin")
        data = verify_token(token)
        assert data is not None
        assert data.sub == "admin"

    def test_custom_subject(self) -> None:
        token = create_access_token("tenant_42", tenant_id=42, role="viewer")
        data = verify_token(token)
        assert data is not None
        assert data.sub == "tenant_42"
        assert data.tenant_id == 42

    def test_custom_expiry_accepted(self) -> None:
        token = create_access_token("admin", tenant_id=1, role="admin", expires_delta=timedelta(minutes=5))
        assert verify_token(token) is not None


class TestVerifyToken:
    def test_valid_token(self) -> None:
        token = create_access_token("admin", tenant_id=1, role="admin")
        assert verify_token(token) is not None

    def test_invalid_token_returns_none(self) -> None:
        assert verify_token("not.a.valid.token") is None

    def test_empty_string_returns_none(self) -> None:
        assert verify_token("") is None

    def test_tampered_token_returns_none(self) -> None:
        token = create_access_token("admin", tenant_id=1, role="admin")
        # Flip last character to tamper signature
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        assert verify_token(tampered) is None

    def test_expired_token_returns_none(self) -> None:
        token = create_access_token("admin", tenant_id=1, role="admin", expires_delta=timedelta(seconds=-1))
        assert verify_token(token) is None


# ─── FastAPI integration tests ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """TestClient with in-memory SQLite override."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

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
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


class TestAuthTokenEndpoint:
    def test_valid_credentials_returns_token(self, client: TestClient) -> None:
        from sentinel.config import settings

        resp = client.post(
            "/auth/token",
            data={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_wrong_password_returns_401(self, client: TestClient) -> None:
        from sentinel.config import settings

        resp = client.post(
            "/auth/token",
            data={"username": settings.ADMIN_USERNAME, "password": "wrong_password"},
        )
        assert resp.status_code == 401

    def test_wrong_username_returns_401(self, client: TestClient) -> None:
        from sentinel.config import settings

        resp = client.post(
            "/auth/token",
            data={"username": "hacker", "password": settings.ADMIN_PASSWORD},
        )
        assert resp.status_code == 401

    def test_missing_fields_returns_422(self, client: TestClient) -> None:
        resp = client.post("/auth/token", data={})
        assert resp.status_code == 422


class TestProtectedEndpoint:
    """DELETE /stop/{id} requires valid Bearer token."""

    def _get_token(self, client: TestClient) -> str:
        from sentinel.config import settings

        resp = client.post(
            "/auth/token",
            data={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
        )
        return resp.json()["access_token"]

    def test_missing_token_returns_401(self, client: TestClient) -> None:
        resp = client.delete("/stop/999")
        assert resp.status_code == 401

    def test_invalid_token_returns_403(self, client: TestClient) -> None:
        resp = client.delete(
            "/stop/999", headers={"Authorization": "Bearer invalid.token.here"}
        )
        assert resp.status_code == 403

    def test_valid_token_nonexistent_target_returns_404(self, client: TestClient) -> None:
        token = self._get_token(client)
        resp = client.delete(
            "/stop/999999", headers={"Authorization": f"Bearer {token}"}
        )
        # Auth passes, target not found → 404
        assert resp.status_code == 404


# ─── Newly protected endpoints — 401 / 403 coverage ─────────────────────────

import pytest

# (method, path) pairs for all newly protected endpoints
_PROTECTED_ENDPOINTS = [
    ("GET", "/targets"),
    ("GET", "/status"),
    ("GET", "/metrics/1"),
    ("GET", "/health"),
    ("GET", "/stats/1"),
    ("GET", "/telegram/status"),
    ("GET", "/telegram/metrics"),
    ("GET", "/telegram/alerts"),
    ("POST", "/targets"),
    ("DELETE", "/targets/1"),
    ("DELETE", "/stop/1"),
]


class TestAllEndpointsRequireAuth:
    """Every protected endpoint must reject missing/invalid tokens."""

    @pytest.mark.parametrize("method,path", _PROTECTED_ENDPOINTS)
    def test_missing_token_returns_401(
        self, client: TestClient, method: str, path: str
    ) -> None:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} should return 401 without token"

    @pytest.mark.parametrize("method,path", _PROTECTED_ENDPOINTS)
    def test_invalid_token_returns_403(
        self, client: TestClient, method: str, path: str
    ) -> None:
        resp = client.request(
            method, path, headers={"Authorization": "Bearer bad.token.here"}
        )
        assert resp.status_code == 403, f"{method} {path} should return 403 with invalid token"


class TestPublicEndpoints:
    """These endpoints must NOT require authentication."""

    def test_dashboard_root_is_public(self, client: TestClient) -> None:
        resp = client.get("/")
        # Returns HTML (200) or 404 if static file missing in test env — never 401/403
        assert resp.status_code not in (401, 403)

    def test_auth_token_endpoint_is_public(self, client: TestClient) -> None:
        # Missing credentials → 401 from business logic, not from JWT guard
        resp = client.post("/auth/token", data={"username": "x", "password": "y"})
        assert resp.status_code == 401  # credential error, not auth guard
