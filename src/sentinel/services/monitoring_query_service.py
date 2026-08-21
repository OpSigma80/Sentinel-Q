from sqlalchemy.orm import Session

from sentinel.services.analytics_service import AnalyticsService
from sentinel.infrastructure.repository import TargetRepository


class MonitoringQueryService:
    """Read-only service for dashboard status and chart metrics endpoints."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = TargetRepository(db)
        self._analytics = AnalyticsService(db)

    def get_active_status_with_health(self, tenant_id: int | None = None) -> list[dict]:
        """Return active services and their computed health score.
        
        Pass tenant_id to scope to a specific tenant (API endpoints).
        Pass None to return all (internal use).
        """
        services = self._repo.get_active_services(tenant_id=tenant_id)

        return [
            {
                "id": svc.id,
                "name": svc.name,
                "url": svc.url,
                "is_active": svc.is_active,
                "status_code": svc.status_code,
                "last_check": str(svc.last_check) if svc.last_check else None,
                "health_score": self._analytics.calculate_health_score(svc.id),
            }
            for svc in services
        ]

    def get_target_metrics(self, target_id: int, limit: int = 100) -> dict:
        """Return latest metrics ordered chronologically for chart rendering."""
        rows = self._repo.get_target_metrics_rows(target_id=target_id, limit=limit)

        ordered_rows = list(reversed(rows))
        return {
            "target_id": int(target_id),
            "count": len(ordered_rows),
            "metrics": [
                {
                    "response_time_ms": row[0],
                    "status_code": row[1],
                    "timestamp": row[2].isoformat() if row[2] else None,
                }
                for row in ordered_rows
            ],
        }

    def get_target_stats_with_health(self, target_id: int) -> dict:
        """Return detailed stats payload used by the stats endpoint."""
        clean_id = int(target_id)
        return {
            "target_id": clean_id,
            "health_score": self._analytics.calculate_health_score(clean_id),
            "stats": self._analytics.get_target_statistics(clean_id),
        }
