from sqlalchemy.orm import Session

from sentinel.application.scheduler import SentinelScheduler
from sentinel.domain.models import ServiceTarget as DomainServiceTarget
from sentinel.domain.schemas import ServiceTargetCreate
from sentinel.infrastructure.repository import TargetRepository
from sentinel.services.analytics_service import AnalyticsService


class TargetService:
    """Application service for target lifecycle operations."""

    def __init__(self, db: Session, scheduler: SentinelScheduler) -> None:
        self._db = db
        self._scheduler = scheduler
        self._repo = TargetRepository(db)
        self._analytics = AnalyticsService(db)

    def list_targets_with_health(self, tenant_id: int | None = None) -> list[dict]:
        targets = self._repo.get_all()
        enriched_targets: list[dict] = []
        for target in targets:
            enriched_targets.append(
                {
                    "id": target.id,
                    "name": target.name,
                    "url": target.url,
                    "check_interval": target.check_interval,
                    "is_active": target.is_active,
                    "last_check": target.last_check,
                    "status_code": target.status_code,
                    "health_score": self._analytics.calculate_health_score(target.id),
                }
            )
        return enriched_targets

    def create_target(self, target_in: ServiceTargetCreate, tenant_id: int | None = None):
        """Persist a new target and attach it to scheduler monitoring."""
        saved = self._repo.save_target(
            DomainServiceTarget(
                name=target_in.name,
                url=str(target_in.url),
                check_interval=target_in.check_interval,
                is_active=target_in.is_active,
            )
        )

        self._db.refresh(saved)

        self._scheduler.add_target_watch(
            DomainServiceTarget(
                id=saved.id,
                name=saved.name,
                url=saved.url,
                check_interval=saved.check_interval,
                is_active=saved.is_active,
            )
        )

        return saved

    def delete_target(self, target_id: int) -> bool:
        """Remove a target from scheduler and persistence."""
        self._scheduler.remove_target_watch(str(target_id))
        return self._repo.delete_target(target_id)
