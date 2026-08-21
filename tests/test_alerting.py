"""Unit tests for intelligent Telegram alerting behavior."""

import asyncio
from datetime import datetime, timedelta

import pytest
import sentinel.application.scheduler as scheduler_module
from sentinel.application.notifier import AlertNotifier
from sentinel.application.scheduler import SentinelScheduler
from sentinel.config import Settings
from sentinel.domain.models import ServiceTarget
from pydantic import ValidationError


class DummySessionManager:
    """Minimal context manager used to isolate scheduler tests from the database."""

    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


class StubNotifier:
    """Capture alert payloads emitted by the scheduler."""

    def __init__(self) -> None:
        self.failures: list[dict] = []
        self.successes: list[dict] = []

    async def notify_failure(self, service_name: str, url: str, status: str, **context) -> bool:
        self.failures.append(
            {
                "service_name": service_name,
                "url": url,
                "status": status,
                **context,
            }
        )
        return True

    async def notify_success(self, service_name: str, url: str, **context) -> bool:
        self.successes.append(
            {
                "service_name": service_name,
                "url": url,
                **context,
            }
        )
        return True


def build_scheduler(
    monkeypatch,
    *,
    failure_threshold: int = 2,
    recovery_threshold: int = 2,
    cooldown_seconds: int = 0,
    stability_window_seconds: int = 0,
    critical_throttle_seconds: int = 0,
    warning_throttle_seconds: int = 0,
    medium_throttle_seconds: int = 0,
):
    """Create a scheduler with in-memory collaborators for deterministic tests."""
    repository_calls = {
        "metrics": [],
        "incidents": [],
    }

    class FakeRepository:
        def __init__(self, db) -> None:
            self.db = db

        def add_metric(self, target_id, status_code: int, response_time_ms: float) -> None:
            repository_calls["metrics"].append(
                {
                    "target_id": target_id,
                    "status_code": status_code,
                    "response_time_ms": response_time_ms,
                }
            )

        def register_incident(self, target_id, service_name: str, status_code: int) -> None:
            repository_calls["incidents"].append(
                {
                    "target_id": target_id,
                    "service_name": service_name,
                    "status_code": status_code,
                }
            )

    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: DummySessionManager())
    monkeypatch.setattr(scheduler_module, "TargetRepository", FakeRepository)
    monkeypatch.setattr(scheduler_module.settings, "ALERT_FAILURE_THRESHOLD", failure_threshold, raising=False)
    monkeypatch.setattr(scheduler_module.settings, "ALERT_RECOVERY_THRESHOLD", recovery_threshold, raising=False)
    monkeypatch.setattr(scheduler_module.settings, "ALERT_COOLDOWN_SECONDS", cooldown_seconds, raising=False)
    monkeypatch.setattr(
        scheduler_module.settings,
        "ALERT_STABILITY_WINDOW_SECONDS",
        stability_window_seconds,
        raising=False,
    )
    monkeypatch.setattr(
        scheduler_module.settings,
        "ALERT_CRITICAL_THROTTLE_SECONDS",
        critical_throttle_seconds,
        raising=False,
    )
    monkeypatch.setattr(
        scheduler_module.settings,
        "ALERT_WARNING_THROTTLE_SECONDS",
        warning_throttle_seconds,
        raising=False,
    )
    monkeypatch.setattr(
        scheduler_module.settings,
        "ALERT_MEDIUM_THROTTLE_SECONDS",
        medium_throttle_seconds,
        raising=False,
    )

    scheduler = SentinelScheduler()
    notifier = StubNotifier()
    scheduler._notifier = notifier

    return scheduler, notifier, repository_calls


def build_target() -> ServiceTarget:
    """Create a deterministic service target for alerting tests."""
    return ServiceTarget(
        id=1,
        name="Primary API",
        url="https://example.com",
        check_interval=60,
        is_active=True,
    )


def test_scheduler_requires_consecutive_failures_before_alert(monkeypatch):
    """A target should alert only after the configured failure threshold is met."""
    scheduler, notifier, repository_calls = build_scheduler(monkeypatch)
    target = build_target()
    started_at = datetime(2026, 4, 20, 10, 0, 0)

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=125.5,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at,
        )
    )

    state = scheduler._state_tracker[str(target.id)]
    assert state.current_state == "up"
    assert state.failure_streak == 1
    assert len(notifier.failures) == 0
    assert len(repository_calls["incidents"]) == 0

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=130.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=60),
        )
    )

    state = scheduler._state_tracker[str(target.id)]
    assert state.current_state == "down"
    assert state.failure_streak == 2
    assert len(notifier.failures) == 1
    assert notifier.failures[0]["severity"] == "HIGH"
    assert len(repository_calls["incidents"]) == 1


def test_scheduler_requires_consecutive_recoveries_and_reports_downtime(monkeypatch):
    """A recovery alert should wait for stable success and include downtime context."""
    scheduler, notifier, _ = build_scheduler(monkeypatch)
    target = build_target()
    started_at = datetime(2026, 4, 20, 11, 0, 0)

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=0,
            response_time=410.0,
            is_up=False,
            status_desc="Error: ConnectError",
            checked_at=started_at,
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=0,
            response_time=430.0,
            is_up=False,
            status_desc="Error: ConnectError",
            checked_at=started_at + timedelta(seconds=60),
        )
    )

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=200,
            response_time=95.0,
            is_up=True,
            status_desc="HTTP 200",
            checked_at=started_at + timedelta(seconds=120),
        )
    )

    assert len(notifier.successes) == 0

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=200,
            response_time=90.0,
            is_up=True,
            status_desc="HTTP 200",
            checked_at=started_at + timedelta(seconds=180),
        )
    )

    state = scheduler._state_tracker[str(target.id)]
    assert state.current_state == "up"
    assert state.down_since is None
    assert len(notifier.failures) == 1
    assert len(notifier.successes) == 1
    assert notifier.successes[0]["downtime_seconds"] == 180.0
    assert notifier.successes[0]["recovery_streak"] == 2


def test_scheduler_cooldown_suppresses_flapping_incident_pair(monkeypatch):
    """Cooldown should suppress repeated alert pairs when a target flaps quickly."""
    scheduler, notifier, repository_calls = build_scheduler(monkeypatch, cooldown_seconds=300)
    target = build_target()
    started_at = datetime(2026, 4, 20, 12, 0, 0)

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=200.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at,
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=210.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=60),
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=200,
            response_time=80.0,
            is_up=True,
            status_desc="HTTP 200",
            checked_at=started_at + timedelta(seconds=120),
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=200,
            response_time=78.0,
            is_up=True,
            status_desc="HTTP 200",
            checked_at=started_at + timedelta(seconds=180),
        )
    )

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=190.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=210),
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=195.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=240),
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=200,
            response_time=75.0,
            is_up=True,
            status_desc="HTTP 200",
            checked_at=started_at + timedelta(seconds=270),
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=200,
            response_time=74.0,
            is_up=True,
            status_desc="HTTP 200",
            checked_at=started_at + timedelta(seconds=300),
        )
    )

    state = scheduler._state_tracker[str(target.id)]
    assert state.current_state == "up"
    assert len(notifier.failures) == 1
    assert len(notifier.successes) == 1
    assert len(repository_calls["incidents"]) == 2


def test_notifier_failure_message_contains_operational_context(monkeypatch):
    """Telegram failure messages should include the new production context fields."""
    notifier = AlertNotifier()
    sent_messages: list[str] = []

    async def fake_send(message: str) -> bool:
        sent_messages.append(message)
        return True

    monkeypatch.setattr(notifier, "_send_telegram_msg", fake_send)
    incident_started_at = datetime(2026, 4, 20, 13, 0, 0)

    result = asyncio.run(
        notifier.notify_failure(
            "Primary API",
            "https://example.com",
            "HTTP 503",
            severity="CRITICAL",
            response_time_ms=321.5,
            failure_streak=4,
            incident_started_at=incident_started_at,
        )
    )

    assert result is True
    assert len(sent_messages) == 1
    assert "Severity" in sent_messages[0]
    assert "CRITICAL" in sent_messages[0]
    assert "321.50 ms" in sent_messages[0]
    assert "Failure Streak" in sent_messages[0]
    assert "2026-04-20 13:00:00" in sent_messages[0]


def test_settings_reject_invalid_alert_thresholds():
    """Alert thresholds should fail fast when configured with absurd values."""
    with pytest.raises(ValidationError):
        Settings(ALERT_FAILURE_THRESHOLD=0)

    with pytest.raises(ValidationError):
        Settings(ALERT_RECOVERY_THRESHOLD=99)


def test_settings_reject_invalid_alert_cooldown():
    """Cooldown should be bounded to an operator-safe range."""
    with pytest.raises(ValidationError):
        Settings(ALERT_COOLDOWN_SECONDS=-1)

    with pytest.raises(ValidationError):
        Settings(ALERT_COOLDOWN_SECONDS=86401)


def test_scheduler_stability_window_blocks_short_flap(monkeypatch):
    """Failures must remain stable long enough before opening a real incident."""
    scheduler, notifier, repository_calls = build_scheduler(
        monkeypatch,
        failure_threshold=2,
        stability_window_seconds=180,
    )
    target = build_target()
    started_at = datetime(2026, 4, 20, 14, 0, 0)

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=140.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at,
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=145.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=60),
        )
    )

    state = scheduler._state_tracker[str(target.id)]
    assert state.current_state == "up"
    assert state.pending_failure_since == started_at
    assert len(notifier.failures) == 0
    assert len(repository_calls["incidents"]) == 0

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=150.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=240),
        )
    )

    state = scheduler._state_tracker[str(target.id)]
    assert state.current_state == "down"
    assert len(notifier.failures) == 1
    assert len(repository_calls["incidents"]) == 1


def test_scheduler_severity_throttle_suppresses_back_to_back_failures(monkeypatch):
    """Failure notifications should be throttled by severity during rapid flapping."""
    scheduler, notifier, repository_calls = build_scheduler(
        monkeypatch,
        failure_threshold=2,
        recovery_threshold=2,
        cooldown_seconds=0,
        stability_window_seconds=0,
        warning_throttle_seconds=300,
    )
    target = build_target()
    started_at = datetime(2026, 4, 20, 15, 0, 0)

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=160.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at,
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=165.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=60),
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=200,
            response_time=85.0,
            is_up=True,
            status_desc="HTTP 200",
            checked_at=started_at + timedelta(seconds=120),
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=200,
            response_time=80.0,
            is_up=True,
            status_desc="HTTP 200",
            checked_at=started_at + timedelta(seconds=180),
        )
    )

    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=170.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=220),
        )
    )
    asyncio.run(
        scheduler._handle_target_result(
            target,
            status_code=503,
            response_time=175.0,
            is_up=False,
            status_desc="HTTP 503",
            checked_at=started_at + timedelta(seconds=240),
        )
    )

    state = scheduler._state_tracker[str(target.id)]
    assert state.current_state == "down"
    assert len(notifier.failures) == 1
    assert len(repository_calls["incidents"]) == 2