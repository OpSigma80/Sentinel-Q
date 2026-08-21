from sqlalchemy.orm import Session

from sentinel.domain.schemas import AlertHistorySnapshot, MetricsSnapshot, ServiceStatusSnapshot
from sentinel.services.analytics_service import AnalyticsService


class TelegramQueryService:
    """Read-only service for Telegram command snapshots."""

    def __init__(self, db: Session) -> None:
        self._analytics = AnalyticsService(db)

    def get_status_snapshot(self) -> ServiceStatusSnapshot:
        return self._analytics.get_status_snapshot()

    def get_metrics_snapshot(self) -> MetricsSnapshot:
        return self._analytics.get_metrics_snapshot()

    def get_alerts_history(self, hours: int = 24) -> AlertHistorySnapshot:
        safe_hours = 24 if hours < 1 or hours > 168 else hours
        return self._analytics.get_alerts_history(hours=safe_hours)
