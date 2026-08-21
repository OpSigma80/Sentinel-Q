from sqlalchemy import text
from sqlalchemy.orm import Session

from sentinel.domain.schemas import HealthCheckResponse


class SystemHealthService:
    """Service layer for system health evaluation."""

    def __init__(self, db: Session, scheduler) -> None:
        self._db = db
        self._scheduler = scheduler

    def get_health(self) -> HealthCheckResponse:
        """Evaluate database and scheduler readiness."""
        db_alive = False
        try:
            result = self._db.execute(text("SELECT 1")).first()
            db_alive = result is not None
        except Exception:
            db_alive = False

        scheduler_obj = getattr(self._scheduler, "_scheduler", None)
        scheduler_alive = scheduler_obj is not None and scheduler_obj.running
        active_count = len(self._scheduler.get_running_target_ids())

        if db_alive and scheduler_alive:
            status = "ok"
        elif db_alive or scheduler_alive:
            status = "degraded"
        else:
            status = "critical"

        return HealthCheckResponse(
            status=status,
            database=db_alive,
            scheduler=scheduler_alive,
            active_targets=active_count,
        )
