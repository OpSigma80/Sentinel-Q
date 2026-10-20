import os
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from dotenv import load_dotenv
from sentinel.infrastructure.base import Base
from sentinel.infrastructure.orm_models import (
    ServiceTargetTable,
    ServiceMetricsTable,
    IncidentHistoryTable,
)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@db:5432/sentinel_db")


def _build_engine(url: str):
    """
    Crea el engine según el backend.

    - SQLite (tests): sin pool_size/max_overflow ni connect_args de PostgreSQL,
      que SQLite rechaza con TypeError. Si es en memoria, StaticPool para que
      todas las conexiones vean la misma BD.
    - PostgreSQL (producción): configuración original, sin cambios.
    """
    parsed = make_url(url)

    if parsed.get_backend_name() == "sqlite":
        kwargs = {"echo": False, "connect_args": {"check_same_thread": False}}
        if parsed.database in (None, "", ":memory:"):
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)

    # El pool_pre_ping es vital para evitar el "Connection Reset"
    return create_engine(
        url,
        pool_size=20,
        max_overflow=40,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=False,
        connect_args={
            "connect_timeout": 10,
            "options": "-c statement_timeout=30000",  # 30 segundos timeout
        },
    )


engine = _build_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency para inyectar sesiones de BD en los endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Re-exportar Base para que sea accesible desde este módulo
__all__ = ["SessionLocal", "engine", "get_db", "Base"]
