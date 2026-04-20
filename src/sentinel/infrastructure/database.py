import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from sentinel.infrastructure.base import Base
from sentinel.infrastructure.orm_models import ServiceTargetTable, ServiceMetricsTable, IncidentHistoryTable

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@db:5432/sentinel_db")

# El pool_pre_ping es vital para evitar el "Connection Reset"
engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True, 
    pool_recycle=1800,
    echo=False,
    connect_args={
        "connect_timeout": 10,
        "options": "-c statement_timeout=30000"  # 30 segundos timeout
    }
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """Dependency para inyectar sesiones de BD en los endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Re-exportar Base para que sea accesible desde este módulo
__all__ = ['SessionLocal', 'engine', 'get_db', 'Base']