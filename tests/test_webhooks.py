"""
Tests for outbound webhooks in Sentinel-Q.

Coverage:
  1.  Webhook creation (valid)
  2.  Webhook creation rejects invalid events
  3.  Webhook list scoped to tenant (isolation)
  4.  Delete rejects webhook from another tenant
  5.  Dispatcher sends correct payload
  6.  Dispatcher adds X-Sentinel-Event header
  7.  Dispatcher adds HMAC signature when secret is set
  8.  Dispatcher does NOT sign when secret is None/empty
  9.  HTTP failure does not raise (fire-and-forget)
  10. Timeout does not raise (fire-and-forget)
  11. Scheduler 'up -> down' transition dispatches webhook
  12. Scheduler 'down -> up' (recovery) transition dispatches webhook
  13. No transition → no dispatch
  14. target with tenant_id=None → no dispatch
  15. Full suite still passes (implicit — run via pytest tests/)
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from sentinel.infrastructure.database import Base
from sentinel.infrastructure.repository import TargetRepository
from sentinel.infrastructure.orm_models import TenantTable, WebhookSubscriptionTable
from sentinel.infrastructure.jwt_service import create_access_token
from sentinel.application.webhook_dispatcher import (
    _build_payload,
    _sign_payload,
    dispatch_webhook,
    dispatch_webhooks_for_event,
)
from sentinel.domain.models import ServiceTarget


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    # Create two tenants for isolation tests
    t1 = TenantTable(name="tenant-one")
    t2 = TenantTable(name="tenant-two")
    db.add_all([t1, t2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    db._tenant1_id = t1.id
    db._tenant2_id = t2.id
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="module")
def api_client():
    from sentinel.infrastructure.database import get_db
    from sentinel.main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestSession()
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


def _admin_token(tenant_id: int = 1, sub: str = "admin") -> str:
    return create_access_token(sub, tenant_id=tenant_id, role="admin")


# ─── 1. Repository: create webhook (valid) ────────────────────────────────────

class TestWebhookRepository:
    def test_create_webhook_valid(self, db_session) -> None:
        repo = TargetRepository(db_session)
        w = repo.create_webhook(
            tenant_id=db_session._tenant1_id,
            url="https://example.com/hook",
            events="down,up",
        )
        assert w.id is not None
        assert w.url == "https://example.com/hook"
        assert w.events == "down,up"
        assert w.is_active is True

    def test_create_webhook_rejects_invalid_events(self, db_session) -> None:
        repo = TargetRepository(db_session)
        with pytest.raises(ValueError, match="Invalid events"):
            repo.create_webhook(
                tenant_id=db_session._tenant1_id,
                url="https://example.com/hook",
                events="all",  # invalid
            )

    def test_list_webhooks_scoped_to_tenant(self, db_session) -> None:
        repo = TargetRepository(db_session)
        repo.create_webhook(db_session._tenant1_id, "https://t1.com/hook")
        repo.create_webhook(db_session._tenant1_id, "https://t1.com/hook2")
        repo.create_webhook(db_session._tenant2_id, "https://t2.com/hook")

        t1_hooks = repo.list_webhooks(db_session._tenant1_id)
        t2_hooks = repo.list_webhooks(db_session._tenant2_id)

        assert len(t1_hooks) == 2
        assert len(t2_hooks) == 1
        assert all(w.tenant_id == db_session._tenant1_id for w in t1_hooks)

    def test_delete_rejects_webhook_from_other_tenant(self, db_session) -> None:
        repo = TargetRepository(db_session)
        w = repo.create_webhook(db_session._tenant1_id, "https://t1.com/hook")
        # Attempt to delete from tenant2 context
        deleted = repo.delete_webhook(w.id, db_session._tenant2_id)
        assert deleted is False
        # Still exists for tenant1
        assert len(repo.list_webhooks(db_session._tenant1_id)) == 1

    def test_delete_own_webhook_succeeds(self, db_session) -> None:
        repo = TargetRepository(db_session)
        w = repo.create_webhook(db_session._tenant1_id, "https://t1.com/del")
        deleted = repo.delete_webhook(w.id, db_session._tenant1_id)
        assert deleted is True
        assert len(repo.list_webhooks(db_session._tenant1_id)) == 0

    def test_get_active_webhooks_for_event_filters_correctly(self, db_session) -> None:
        repo = TargetRepository(db_session)
        repo.create_webhook(db_session._tenant1_id, "https://down-only.com", events="down")
        repo.create_webhook(db_session._tenant1_id, "https://both.com", events="down,up")
        repo.create_webhook(db_session._tenant1_id, "https://up-only.com", events="up")

        down_hooks = repo.get_active_webhooks_for_event(db_session._tenant1_id, "down")
        up_hooks = repo.get_active_webhooks_for_event(db_session._tenant1_id, "up")

        assert len(down_hooks) == 2  # "down" and "down,up"
        assert len(up_hooks) == 2    # "up" and "down,up"


# ─── Admin endpoints (integration) ────────────────────────────────────────────

class TestAdminWebhookEndpoints:
    def test_create_webhook_returns_201(self, api_client: TestClient) -> None:
        token = _admin_token(tenant_id=1)
        resp = api_client.post(
            "/admin/webhooks",
            json={"url": "https://example.com/hook", "events": "down,up"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["events"] == "down,up"

    def test_create_webhook_invalid_events_returns_422(self, api_client: TestClient) -> None:
        token = _admin_token(tenant_id=1)
        resp = api_client.post(
            "/admin/webhooks",
            json={"url": "https://example.com/hook", "events": "everything"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_create_webhook_missing_url_returns_422(self, api_client: TestClient) -> None:
        token = _admin_token(tenant_id=1)
        resp = api_client.post(
            "/admin/webhooks",
            json={"events": "down"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_list_webhooks_returns_own_tenant_only(self, api_client: TestClient) -> None:
        token_t1 = _admin_token(tenant_id=1)
        token_t2 = _admin_token(tenant_id=2, sub="admin2")

        api_client.post(
            "/admin/webhooks",
            json={"url": "https://t1-unique.com/hook"},
            headers={"Authorization": f"Bearer {token_t1}"},
        )

        resp_t1 = api_client.get(
            "/admin/webhooks",
            headers={"Authorization": f"Bearer {token_t1}"},
        )
        resp_t2 = api_client.get(
            "/admin/webhooks",
            headers={"Authorization": f"Bearer {token_t2}"},
        )
        assert resp_t1.status_code == 200
        assert resp_t2.status_code == 200
        t1_urls = [w["url"] for w in resp_t1.json()]
        t2_urls = [w["url"] for w in resp_t2.json()]
        assert "https://t1-unique.com/hook" in t1_urls
        assert "https://t1-unique.com/hook" not in t2_urls

    def test_delete_webhook_returns_204(self, api_client: TestClient) -> None:
        token = _admin_token(tenant_id=1)
        create_resp = api_client.post(
            "/admin/webhooks",
            json={"url": "https://todelete.com/hook"},
            headers={"Authorization": f"Bearer {token}"},
        )
        webhook_id = create_resp.json()["id"]
        del_resp = api_client.delete(
            f"/admin/webhooks/{webhook_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert del_resp.status_code == 204

    def test_delete_nonexistent_returns_404(self, api_client: TestClient) -> None:
        token = _admin_token(tenant_id=1)
        resp = api_client.delete(
            "/admin/webhooks/999999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


# ─── Dispatcher unit tests ────────────────────────────────────────────────────

class TestWebhookDispatcher:
    def test_payload_structure(self) -> None:
        p = _build_payload("down", 42, "My API", "https://api.example.com", 1)
        assert p["event"] == "down"
        assert p["target"]["id"] == 42
        assert p["target"]["name"] == "My API"
        assert p["tenant_id"] == 1
        assert "timestamp" in p

    def test_dispatcher_sends_correct_event_header(self) -> None:
        captured: dict = {}

        async def _run():
            with patch("httpx.AsyncClient") as mock_cls:
                mock_instance = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_instance.post = AsyncMock(return_value=mock_response)

                await dispatch_webhook(
                    webhook_id=1,
                    url="https://example.com/hook",
                    secret=None,
                    event="down",
                    target_id=5,
                    target_name="Test",
                    target_url="https://svc.example.com",
                    tenant_id=1,
                )
                call_kwargs = mock_instance.post.call_args
                captured["headers"] = call_kwargs.kwargs["headers"]

        import asyncio
        asyncio.run(_run())
        assert captured["headers"]["X-Sentinel-Event"] == "down"
        assert captured["headers"]["Content-Type"] == "application/json"

    def test_dispatcher_signs_when_secret_set(self) -> None:
        secret = "mysecret"
        captured: dict = {}

        async def _run():
            with patch("httpx.AsyncClient") as mock_cls:
                mock_instance = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_instance.post = AsyncMock(return_value=mock_response)

                await dispatch_webhook(
                    webhook_id=1,
                    url="https://example.com/hook",
                    secret=secret,
                    event="down",
                    target_id=5,
                    target_name="Test",
                    target_url="https://svc.example.com",
                    tenant_id=1,
                )
                call_kwargs = mock_instance.post.call_args
                captured["headers"] = call_kwargs.kwargs["headers"]
                captured["body"] = call_kwargs.kwargs["content"]

        import asyncio
        asyncio.run(_run())
        sig = captured["headers"]["X-Sentinel-Signature"]
        expected = _sign_payload(captured["body"], secret)
        assert sig == expected
        assert sig.startswith("sha256=")

    def test_dispatcher_no_signature_without_secret(self) -> None:
        captured: dict = {}

        async def _run():
            with patch("httpx.AsyncClient") as mock_cls:
                mock_instance = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_instance.post = AsyncMock(return_value=mock_response)

                await dispatch_webhook(
                    webhook_id=1,
                    url="https://example.com/hook",
                    secret=None,
                    event="down",
                    target_id=5,
                    target_name="Test",
                    target_url="https://svc.example.com",
                    tenant_id=1,
                )
                captured["headers"] = mock_instance.post.call_args.kwargs["headers"]

        import asyncio
        asyncio.run(_run())
        assert "X-Sentinel-Signature" not in captured["headers"]

    def test_http_failure_does_not_raise(self) -> None:
        import asyncio

        async def _run():
            with patch("httpx.AsyncClient") as mock_cls:
                mock_instance = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_instance.post = AsyncMock(side_effect=Exception("connection refused"))
                # Must NOT raise
                await dispatch_webhook(
                    webhook_id=1,
                    url="https://dead.example.com/hook",
                    secret=None,
                    event="down",
                    target_id=5,
                    target_name="Test",
                    target_url="https://svc.example.com",
                    tenant_id=1,
                )

        asyncio.run(_run())  # no exception

    def test_timeout_does_not_raise(self) -> None:
        import asyncio
        import httpx

        async def _run():
            with patch("httpx.AsyncClient") as mock_cls:
                mock_instance = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_instance.post = AsyncMock(
                    side_effect=httpx.TimeoutException("timeout")
                )
                await dispatch_webhook(
                    webhook_id=1,
                    url="https://slow.example.com/hook",
                    secret=None,
                    event="up",
                    target_id=5,
                    target_name="Test",
                    target_url="https://svc.example.com",
                    tenant_id=1,
                )

        asyncio.run(_run())  # no exception


# ─── Scheduler dispatch integration ──────────────────────────────────────────

class TestSchedulerDispatch:
    """
    Tests verify that the scheduler calls dispatch_webhooks_for_event on
    real state transitions and NOT on stable states or when tenant_id is None.
    Uses asyncio.run() — no pytest-asyncio dependency required.
    """

    def _make_target(self, tenant_id: Optional[int] = 1) -> ServiceTarget:
        return ServiceTarget(
            id=99,
            name="Test Service",
            url="https://svc.example.com",
            check_interval=60,
            is_active=True,
            tenant_id=tenant_id,
        )

    def test_up_to_down_dispatches_down_event(self) -> None:
        import asyncio
        from datetime import timedelta
        from sentinel.application.scheduler import SentinelScheduler
        from sentinel.config import settings

        scheduler = SentinelScheduler()
        target = self._make_target(tenant_id=3)

        async def _run():
            with patch(
                "sentinel.application.scheduler.dispatch_webhooks_for_event",
                new_callable=AsyncMock,
            ) as mock_dispatch, patch.object(
                scheduler._notifier, "notify_failure", new_callable=AsyncMock
            ), patch(
                "sentinel.application.scheduler.SessionLocal"
            ) as mock_session:
                # Mock SessionLocal context manager so no real DB connection is made
                mock_db = MagicMock()
                mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
                mock_session.return_value.__exit__ = MagicMock(return_value=False)
                mock_db.query.return_value.filter.return_value.first.return_value = None

                state = scheduler._get_target_state("99")
                state.failure_streak = settings.ALERT_FAILURE_THRESHOLD - 1
                # Place pending_failure_since in the past to bypass the flap guard
                state.pending_failure_since = datetime.now() - timedelta(
                    seconds=settings.ALERT_STABILITY_WINDOW_SECONDS + 10
                )

                await scheduler._handle_target_result(
                    target,
                    status_code=500,
                    response_time=100.0,
                    is_up=False,
                    status_desc="HTTP 500",
                )
                mock_dispatch.assert_called_once()
                call_kwargs = mock_dispatch.call_args.kwargs
                assert call_kwargs["event"] == "down"
                assert call_kwargs["tenant_id"] == 3

        asyncio.run(_run())

    def test_down_to_up_dispatches_up_event(self) -> None:
        import asyncio
        from sentinel.application.scheduler import SentinelScheduler
        from sentinel.config import settings

        scheduler = SentinelScheduler()
        target = self._make_target(tenant_id=3)

        async def _run():
            with patch(
                "sentinel.application.scheduler.dispatch_webhooks_for_event",
                new_callable=AsyncMock,
            ) as mock_dispatch, patch.object(
                scheduler._notifier, "notify_success", new_callable=AsyncMock
            ):
                state = scheduler._get_target_state("99")
                state.current_state = "down"
                state.incident_alert_sent = True
                state.recovery_streak = settings.ALERT_RECOVERY_THRESHOLD - 1

                await scheduler._handle_target_result(
                    target,
                    status_code=200,
                    response_time=50.0,
                    is_up=True,
                    status_desc="HTTP 200",
                )
                mock_dispatch.assert_called_once()
                call_kwargs = mock_dispatch.call_args.kwargs
                assert call_kwargs["event"] == "up"
                assert call_kwargs["tenant_id"] == 3

        asyncio.run(_run())

    def test_no_state_change_no_dispatch(self) -> None:
        import asyncio
        from sentinel.application.scheduler import SentinelScheduler

        scheduler = SentinelScheduler()
        target = self._make_target(tenant_id=3)

        async def _run():
            with patch(
                "sentinel.application.scheduler.dispatch_webhooks_for_event",
                new_callable=AsyncMock,
            ) as mock_dispatch:
                await scheduler._handle_target_result(
                    target,
                    status_code=200,
                    response_time=50.0,
                    is_up=True,
                    status_desc="HTTP 200",
                )
                mock_dispatch.assert_not_called()

        asyncio.run(_run())

    def test_no_tenant_id_no_dispatch(self) -> None:
        import asyncio
        from datetime import timedelta
        from sentinel.application.scheduler import SentinelScheduler
        from sentinel.config import settings

        scheduler = SentinelScheduler()
        target = self._make_target(tenant_id=None)

        async def _run():
            with patch(
                "sentinel.application.scheduler.dispatch_webhooks_for_event",
                new_callable=AsyncMock,
            ) as mock_dispatch, patch.object(
                scheduler._notifier, "notify_failure", new_callable=AsyncMock
            ), patch(
                "sentinel.application.scheduler.SessionLocal"
            ) as mock_session:
                mock_db = MagicMock()
                mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
                mock_session.return_value.__exit__ = MagicMock(return_value=False)
                mock_db.query.return_value.filter.return_value.first.return_value = None

                state = scheduler._get_target_state("99")
                state.failure_streak = settings.ALERT_FAILURE_THRESHOLD - 1
                state.pending_failure_since = datetime.now() - timedelta(
                    seconds=settings.ALERT_STABILITY_WINDOW_SECONDS + 10
                )

                await scheduler._handle_target_result(
                    target,
                    status_code=500,
                    response_time=100.0,
                    is_up=False,
                    status_desc="HTTP 500",
                )
                mock_dispatch.assert_not_called()

        asyncio.run(_run())
