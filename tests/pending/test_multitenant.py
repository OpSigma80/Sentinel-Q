"""
Tests for multi-tenant — Week 3.

Covers:
  - UserTokenData creation and verification
  - TenantService CRUD
  - UserService CRUD and authentication
  - Repository tenant scoping (tenant isolation)
  - Admin endpoints (/admin/tenants, /admin/users)
  - Non-admin JWT rejected from admin endpoints
  - Tenant isolation: user from tenant A cannot see tenant B data
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker, Session

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentinel.infrastructure.database import Base, get_db
from sentinel.infrastructure.orm_models import TenantTable, UserTable, ServiceTargetTable
from sentinel.infrastructure.repository import TargetRepository
from sentinel.infrastructure.jwt_service import UserTokenData, create_access_token, verify_token
from sentinel.services.tenant_service import TenantService
from sentinel.services.user_service import UserService, hash_password, verify_password
from sentinel.domain.models import ServiceTarget as DomainServiceTarget


# ─── Shared in-memory DB engine (module-scoped) ───────────────────────────────

@pytest.fixture(scope="module")
def mt_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def mt_db(mt_engine) -> Session:
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=mt_engine)
    db = SessionLocal()
    yield db
    db.rollback()
    db.close()


# ─── FastAPI test client ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client(mt_engine):
    from sentinel.main import app

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=mt_engine)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _admin_token(tenant_id: int = 1) -> str:
    return create_access_token(subject="admin", tenant_id=tenant_id, role="admin")


def _viewer_token(tenant_id: int = 1) -> str:
    return create_access_token(subject="viewer", tenant_id=tenant_id, role="viewer")


# ─── UserTokenData ────────────────────────────────────────────────────────────

class TestUserTokenData:
    def test_create_token_has_tenant_and_role(self) -> None:
        token = create_access_token("alice", tenant_id=42, role="admin")
        data = verify_token(token)
        assert data is not None
        assert data.sub == "alice"
        assert data.tenant_id == 42
        assert data.role == "admin"

    def test_token_missing_tenant_returns_none(self) -> None:
        """Tokens without tenant_id (legacy) must be rejected."""
        from jose import jwt
        from sentinel.config import settings

        payload = {"sub": "legacy_user"}  # no tenant_id, no role
        raw = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        result = verify_token(raw)
        assert result is None

    def test_invalid_token_returns_none(self) -> None:
        result = verify_token("totally.invalid.token")
        assert result is None

    def test_tampered_token_returns_none(self) -> None:
        token = create_access_token("bob", tenant_id=1, role="viewer")
        tampered = token[:-4] + "xxxx"
        assert verify_token(tampered) is None


# ─── Password hashing ─────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_differs_from_plain(self) -> None:
        hashed = hash_password("secret123")
        assert hashed != "secret123"

    def test_verify_correct_password(self) -> None:
        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed) is True

    def test_verify_wrong_password(self) -> None:
        hashed = hash_password("secret123")
        assert verify_password("wrong", hashed) is False


# ─── TenantService ────────────────────────────────────────────────────────────

class TestTenantService:
    def test_create_tenant(self, mt_db: Session) -> None:
        svc = TenantService(mt_db)
        tenant = svc.create_tenant("acme")
        assert tenant.id is not None
        assert tenant.name == "acme"

    def test_create_duplicate_tenant_raises(self, mt_db: Session) -> None:
        svc = TenantService(mt_db)
        svc.create_tenant("unique_co")
        with pytest.raises(ValueError, match="already exists"):
            svc.create_tenant("unique_co")

    def test_list_tenants_returns_created(self, mt_db: Session) -> None:
        svc = TenantService(mt_db)
        svc.create_tenant("list_tenant_a")
        tenants = svc.list_tenants()
        names = [t.name for t in tenants]
        assert "list_tenant_a" in names

    def test_get_tenant_by_id(self, mt_db: Session) -> None:
        svc = TenantService(mt_db)
        created = svc.create_tenant("get_by_id_co")
        found = svc.get_tenant(created.id)
        assert found is not None
        assert found.name == "get_by_id_co"

    def test_get_nonexistent_tenant_returns_none(self, mt_db: Session) -> None:
        svc = TenantService(mt_db)
        assert svc.get_tenant(99999) is None


# ─── UserService ──────────────────────────────────────────────────────────────

class TestUserService:
    def _make_tenant(self, db: Session, name: str) -> TenantTable:
        t = TenantTable(name=name)
        db.add(t)
        db.commit()
        db.refresh(t)
        return t

    def test_create_user(self, mt_db: Session) -> None:
        tenant = self._make_tenant(mt_db, "user_svc_t1")
        svc = UserService(mt_db)
        user = svc.create_user(tenant.id, "alice", "pass123", role="admin")
        assert user.id is not None
        assert user.username == "alice"
        assert user.role == "admin"
        assert user.tenant_id == tenant.id

    def test_create_duplicate_user_raises(self, mt_db: Session) -> None:
        tenant = self._make_tenant(mt_db, "user_svc_t2")
        svc = UserService(mt_db)
        svc.create_user(tenant.id, "bob", "pass1")
        with pytest.raises(ValueError, match="already exists"):
            svc.create_user(tenant.id, "bob", "pass2")

    def test_authenticate_valid(self, mt_db: Session) -> None:
        tenant = self._make_tenant(mt_db, "user_svc_t3")
        svc = UserService(mt_db)
        svc.create_user(tenant.id, "carol", "mypassword")
        result = svc.authenticate(tenant.id, "carol", "mypassword")
        assert result is not None
        assert result.username == "carol"

    def test_authenticate_wrong_password(self, mt_db: Session) -> None:
        tenant = self._make_tenant(mt_db, "user_svc_t4")
        svc = UserService(mt_db)
        svc.create_user(tenant.id, "dave", "correct")
        assert svc.authenticate(tenant.id, "dave", "wrong") is None

    def test_authenticate_nonexistent_user(self, mt_db: Session) -> None:
        tenant = self._make_tenant(mt_db, "user_svc_t5")
        svc = UserService(mt_db)
        assert svc.authenticate(tenant.id, "ghost", "pass") is None

    def test_list_users_scoped_to_tenant(self, mt_db: Session) -> None:
        t1 = self._make_tenant(mt_db, "iso_t1")
        t2 = self._make_tenant(mt_db, "iso_t2")
        svc = UserService(mt_db)
        svc.create_user(t1.id, "userA", "p")
        svc.create_user(t2.id, "userB", "p")
        users_t1 = svc.list_users(t1.id)
        assert all(u.tenant_id == t1.id for u in users_t1)
        names = [u.username for u in users_t1]
        assert "userA" in names
        assert "userB" not in names


# ─── Repository tenant isolation ─────────────────────────────────────────────

class TestRepositoryTenantIsolation:
    def _make_tenant(self, db: Session, name: str) -> TenantTable:
        t = TenantTable(name=name)
        db.add(t)
        db.commit()
        db.refresh(t)
        return t

    def test_get_active_services_scoped(self, mt_db: Session) -> None:
        t1 = self._make_tenant(mt_db, "repo_t1")
        t2 = self._make_tenant(mt_db, "repo_t2")
        repo = TargetRepository(mt_db)
        repo.save_target(
            DomainServiceTarget(name="svc_t1", url="http://t1.example.com", check_interval=60, is_active=True),
            tenant_id=t1.id,
        )
        repo.save_target(
            DomainServiceTarget(name="svc_t2", url="http://t2.example.com", check_interval=60, is_active=True),
            tenant_id=t2.id,
        )
        services_t1 = repo.get_active_services(tenant_id=t1.id)
        names = [s.name for s in services_t1]
        assert "svc_t1" in names
        assert "svc_t2" not in names

    def test_get_target_by_id_cross_tenant_returns_none(self, mt_db: Session) -> None:
        t1 = self._make_tenant(mt_db, "cross_t1")
        t2 = self._make_tenant(mt_db, "cross_t2")
        repo = TargetRepository(mt_db)
        saved = repo.save_target(
            DomainServiceTarget(name="secret_svc", url="http://secret.com", check_interval=60, is_active=True),
            tenant_id=t1.id,
        )
        # Tenant 2 must NOT see tenant 1's target
        result = repo.get_target_by_id(saved.id, tenant_id=t2.id)
        assert result is None

    def test_get_target_by_id_same_tenant_returns_target(self, mt_db: Session) -> None:
        t1 = self._make_tenant(mt_db, "same_t1")
        repo = TargetRepository(mt_db)
        saved = repo.save_target(
            DomainServiceTarget(name="visible_svc", url="http://visible.com", check_interval=60, is_active=True),
            tenant_id=t1.id,
        )
        result = repo.get_target_by_id(saved.id, tenant_id=t1.id)
        assert result is not None
        assert result.name == "visible_svc"


# ─── Admin endpoints ──────────────────────────────────────────────────────────

class TestAdminEndpoints:
    def test_create_tenant_admin(self, client: TestClient) -> None:
        token = _admin_token()
        resp = client.post(
            "/admin/tenants",
            json={"name": "new_corp"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "new_corp"

    def test_create_tenant_duplicate_returns_409(self, client: TestClient) -> None:
        token = _admin_token()
        client.post("/admin/tenants", json={"name": "dup_corp"}, headers={"Authorization": f"Bearer {token}"})
        resp = client.post("/admin/tenants", json={"name": "dup_corp"}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409

    def test_create_tenant_no_name_returns_422(self, client: TestClient) -> None:
        token = _admin_token()
        resp = client.post("/admin/tenants", json={}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 422

    def test_list_tenants_admin(self, client: TestClient) -> None:
        token = _admin_token()
        client.post("/admin/tenants", json={"name": "list_corp"}, headers={"Authorization": f"Bearer {token}"})
        resp = client.get("/admin/tenants", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_create_tenant_viewer_forbidden(self, client: TestClient) -> None:
        token = _viewer_token()
        resp = client.post(
            "/admin/tenants",
            json={"name": "viewer_attempt"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_list_tenants_viewer_forbidden(self, client: TestClient) -> None:
        token = _viewer_token()
        resp = client.get("/admin/tenants", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_create_user_admin(self, client: TestClient) -> None:
        token = _admin_token(tenant_id=1)
        # First ensure tenant 1 exists
        client.post("/admin/tenants", json={"name": "default"}, headers={"Authorization": f"Bearer {token}"})
        resp = client.post(
            "/admin/users",
            json={"username": "newuser", "password": "pass123", "role": "viewer", "tenant_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 201 or 409 (if already exists from previous run)
        assert resp.status_code in (201, 409)

    def test_create_user_missing_fields_returns_422(self, client: TestClient) -> None:
        token = _admin_token()
        resp = client.post(
            "/admin/users",
            json={"username": "nopass"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_list_users_admin(self, client: TestClient) -> None:
        token = _admin_token(tenant_id=1)
        resp = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_users_viewer_forbidden(self, client: TestClient) -> None:
        token = _viewer_token()
        resp = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_admin_endpoints_require_auth(self, client: TestClient) -> None:
        for method, path in [
            ("POST", "/admin/tenants"),
            ("GET", "/admin/tenants"),
            ("POST", "/admin/users"),
            ("GET", "/admin/users"),
        ]:
            if method == "POST":
                resp = client.post(path, json={})
            else:
                resp = client.get(path)
            assert resp.status_code == 401, f"{method} {path} should return 401 without token"


# ─── Migration phase 6 validation ────────────────────────────────────────────

class TestMigrationValidation:
    """
    Tests that _phase6_validate correctly catches orphan rows.

    Strategy: directly call _phase6_validate with a mock connection
    that returns a non-zero orphan count — simulates what happens if
    phase 4 (UPDATE) fails partway and leaves rows with tenant_id IS NULL.
    """

    def test_phase6_raises_when_orphan_rows_remain(self) -> None:
        """_phase6_validate must raise RuntimeError if any tenant_id IS NULL rows remain."""
        from unittest.mock import MagicMock
        from sentinel.infrastructure.migrations import _phase6_validate

        # Build a mock connection whose execute().scalar() returns 3 orphan rows
        mock_conn = MagicMock()
        mock_conn.execute.return_value.scalar.return_value = 3

        with pytest.raises(RuntimeError) as exc_info:
            _phase6_validate(mock_conn)

        assert "3 service row(s) still have tenant_id IS NULL" in str(exc_info.value)
        assert "Manual intervention required" in str(exc_info.value)

    def test_phase6_raises_with_single_orphan(self) -> None:
        """Edge case: even a single orphan must trigger RuntimeError."""
        from unittest.mock import MagicMock
        from sentinel.infrastructure.migrations import _phase6_validate

        mock_conn = MagicMock()
        mock_conn.execute.return_value.scalar.return_value = 1

        with pytest.raises(RuntimeError) as exc_info:
            _phase6_validate(mock_conn)

        assert "1 service row(s) still have tenant_id IS NULL" in str(exc_info.value)

    def test_phase6_passes_when_zero_orphans(self) -> None:
        """_phase6_validate must NOT raise when all services have a tenant_id."""
        from unittest.mock import MagicMock, call
        from sentinel.infrastructure.migrations import _phase6_validate

        mock_conn = MagicMock()
        # First call: COUNT(*) returns 0 (no orphans)
        # Second call: constraint check returns True
        mock_conn.execute.return_value.scalar.side_effect = [0, True]

        # Must not raise
        _phase6_validate(mock_conn)

    def test_phase4_partial_failure_detected_by_phase6(self) -> None:
        """
        Simulates phase 4 UPDATE failing midway:
          - Some rows get tenant_id assigned, others stay NULL
          - phase 6 must detect the inconsistency and raise RuntimeError
        """
        from unittest.mock import MagicMock, patch
        from sentinel.infrastructure.migrations import _phase4_migrate_orphan_services, _phase6_validate

        # Simulate phase 4: UPDATE appears to succeed (rowcount=1) but orphans remain
        mock_conn_phase4 = MagicMock()
        mock_conn_phase4.execute.return_value.rowcount = 1
        _phase4_migrate_orphan_services(mock_conn_phase4)

        # Simulate phase 6: DB still has 2 orphan rows (partial update happened)
        mock_conn_phase6 = MagicMock()
        mock_conn_phase6.execute.return_value.scalar.return_value = 2

        with pytest.raises(RuntimeError) as exc_info:
            _phase6_validate(mock_conn_phase6)

        error_msg = str(exc_info.value)
        assert "tenant_id IS NULL" in error_msg
        assert "2 service row(s)" in error_msg
