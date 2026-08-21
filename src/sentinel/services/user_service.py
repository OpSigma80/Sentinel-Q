"""
UserService — business logic for user management within a tenant.
Password hashing via passlib[bcrypt].
"""

from dataclasses import dataclass
from typing import Optional

from loguru import logger
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from sentinel.infrastructure.repository import TargetRepository

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class UserDTO:
    id: int
    tenant_id: int
    username: str
    role: str
    is_active: bool


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


class UserService:
    def __init__(self, db: Session) -> None:
        self._repo = TargetRepository(db)

    def create_user(
        self,
        tenant_id: int,
        username: str,
        password: str,
        role: str = "viewer",
    ) -> UserDTO:
        """Create a user within a tenant. Raises ValueError if username taken."""
        existing = self._repo.get_user_by_username(username, tenant_id)
        if existing is not None:
            raise ValueError(f"Username '{username}' already exists in tenant {tenant_id}.")
        hashed = hash_password(password)
        user = self._repo.create_user(tenant_id, username, hashed, role)
        logger.info(f"✅ User created: id={user.id} username='{user.username}' tenant={tenant_id} role={role}")
        return UserDTO(
            id=user.id,
            tenant_id=user.tenant_id,
            username=user.username,
            role=user.role,
            is_active=user.is_active,
        )

    def list_users(self, tenant_id: int) -> list[UserDTO]:
        rows = self._repo.get_all_users(tenant_id)
        return [
            UserDTO(id=r.id, tenant_id=r.tenant_id, username=r.username, role=r.role, is_active=r.is_active)
            for r in rows
        ]

    def authenticate(self, tenant_id: int, username: str, password: str) -> Optional[UserDTO]:
        """
        Verify credentials for a tenant user.
        Returns UserDTO if valid, None if invalid.
        """
        user = self._repo.get_user_by_username(username, tenant_id)
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return UserDTO(
            id=user.id,
            tenant_id=user.tenant_id,
            username=user.username,
            role=user.role,
            is_active=user.is_active,
        )
