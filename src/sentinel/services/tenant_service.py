"""
TenantService — business logic for tenant management.
Admin-only operations: create tenant, list tenants.
"""

from dataclasses import dataclass
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from sentinel.infrastructure.repository import TargetRepository


@dataclass
class TenantDTO:
    id: int
    name: str


class TenantService:
    def __init__(self, db: Session) -> None:
        self._repo = TargetRepository(db)

    def create_tenant(self, name: str) -> TenantDTO:
        """Create a new tenant. Raises ValueError if name already exists."""
        existing = self._repo.get_tenant_by_name(name)
        if existing is not None:
            raise ValueError(f"Tenant '{name}' already exists.")
        tenant = self._repo.create_tenant(name)
        logger.info(f"✅ Tenant created: id={tenant.id} name='{tenant.name}'")
        return TenantDTO(id=tenant.id, name=tenant.name)

    def list_tenants(self) -> list[TenantDTO]:
        rows = self._repo.get_all_tenants()
        return [TenantDTO(id=r.id, name=r.name) for r in rows]

    def get_tenant(self, tenant_id: int) -> Optional[TenantDTO]:
        row = self._repo.get_tenant_by_id(tenant_id)
        if row is None:
            return None
        return TenantDTO(id=row.id, name=row.name)
