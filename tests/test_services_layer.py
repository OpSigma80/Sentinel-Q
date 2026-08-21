from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

from sentinel.services.target_service import TargetService
from sentinel.services.telegram_query_service import TelegramQueryService
from sentinel.services.monitoring_query_service import MonitoringQueryService
from sentinel.services.health_service import SystemHealthService
from sentinel.infrastructure.orm_models import ServiceMetricsTable


class FakeScheduler:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []

    def add_target_watch(self, target) -> None:
        self.added.append(str(target.id))

    def remove_target_watch(self, target_id: str) -> bool:
        self.removed.append(str(target_id))
        return True


class FakeHealthScheduler:
    def __init__(self, *, running: bool, active_targets: int) -> None:
        self._scheduler = SimpleNamespace(running=running)
        self._active_targets = active_targets

    def get_running_target_ids(self):
        return [str(i) for i in range(self._active_targets)]


def test_target_service_create_and_list(test_db):
    scheduler = FakeScheduler()
    service = TargetService(test_db, scheduler)

    payload = SimpleNamespace(
        name="Service Layer API",
        url="https://example.com",
        check_interval=60,
        is_active=True,
    )

    saved = service.create_target(payload)
    listed = service.list_targets_with_health()

    assert saved.id is not None
    assert scheduler.added == [str(saved.id)]
    assert len(listed) == 1
    assert listed[0]["name"] == "Service Layer API"
    assert "health_score" in listed[0]


def test_target_service_delete_invokes_scheduler_and_repository(test_db):
    scheduler = FakeScheduler()
    service = TargetService(test_db, scheduler)

    payload = SimpleNamespace(
        name="Delete Me",
        url="https://delete.example.com",
        check_interval=60,
        is_active=True,
    )
    saved = service.create_target(payload)

    deleted = service.delete_target(saved.id)

    assert deleted is True
    assert scheduler.removed == [str(saved.id)]


def test_telegram_query_service_clamps_invalid_hours(test_db):
    service = TelegramQueryService(test_db)

    snapshot = service.get_alerts_history(hours=999)

    assert snapshot.total_alerts_today == 0
    assert snapshot.critical_count == 0
    assert snapshot.warning_count == 0


def test_telegram_query_service_returns_status_and_metrics(test_db):
    service = TelegramQueryService(test_db)

    status = service.get_status_snapshot()
    metrics = service.get_metrics_snapshot()

    assert status.total_services == 0
    assert metrics.total_checks == 0


def test_monitoring_query_service_status_and_metrics_order(test_db):
    scheduler = FakeScheduler()
    target_service = TargetService(test_db, scheduler)

    payload = SimpleNamespace(
        name="Metrics API",
        url="https://metrics.example.com",
        check_interval=60,
        is_active=True,
    )
    saved = target_service.create_target(payload)

    now = datetime.now(UTC).replace(tzinfo=None)
    test_db.add(
        ServiceMetricsTable(
            target_id=saved.id,
            status_code=500,
            response_time_ms=250.0,
            timestamp=now - timedelta(minutes=2),
        )
    )
    test_db.add(
        ServiceMetricsTable(
            target_id=saved.id,
            status_code=200,
            response_time_ms=95.0,
            timestamp=now - timedelta(minutes=1),
        )
    )
    test_db.commit()

    monitoring_service = MonitoringQueryService(test_db)
    status_rows = monitoring_service.get_active_status_with_health()
    metrics_payload = monitoring_service.get_target_metrics(saved.id)

    assert len(status_rows) == 1
    assert status_rows[0]["name"] == "Metrics API"
    assert "health_score" in status_rows[0]

    assert metrics_payload["target_id"] == saved.id
    assert metrics_payload["count"] == 2
    assert metrics_payload["metrics"][0]["status_code"] == 500
    assert metrics_payload["metrics"][1]["status_code"] == 200


def test_monitoring_query_service_target_stats_payload(test_db):
    scheduler = FakeScheduler()
    target_service = TargetService(test_db, scheduler)

    payload = SimpleNamespace(
        name="Stats API",
        url="https://stats.example.com",
        check_interval=60,
        is_active=True,
    )
    saved = target_service.create_target(payload)

    now = datetime.now(UTC).replace(tzinfo=None)
    test_db.add(
        ServiceMetricsTable(
            target_id=saved.id,
            status_code=200,
            response_time_ms=100.0,
            timestamp=now - timedelta(minutes=1),
        )
    )
    test_db.add(
        ServiceMetricsTable(
            target_id=saved.id,
            status_code=503,
            response_time_ms=300.0,
            timestamp=now,
        )
    )
    test_db.commit()

    monitoring_service = MonitoringQueryService(test_db)
    stats_payload = monitoring_service.get_target_stats_with_health(saved.id)

    assert stats_payload["target_id"] == saved.id
    assert "health_score" in stats_payload
    assert "stats" in stats_payload
    assert stats_payload["stats"]["total_checks"] == 2


def test_system_health_service_returns_ok_when_all_green(test_db):
    scheduler = FakeHealthScheduler(running=True, active_targets=2)
    service = SystemHealthService(test_db, scheduler)

    payload = service.get_health()

    assert payload.status == "ok"
    assert payload.database is True
    assert payload.scheduler is True
    assert payload.active_targets == 2


def test_system_health_service_returns_degraded_when_scheduler_down(test_db):
    scheduler = FakeHealthScheduler(running=False, active_targets=0)
    service = SystemHealthService(test_db, scheduler)

    payload = service.get_health()

    assert payload.status == "degraded"
    assert payload.database is True
    assert payload.scheduler is False
