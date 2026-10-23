"""
Pytest fixtures para Sentinel-Q
"""

import os

# Deben fijarse ANTES de importar cualquier módulo de sentinel.
# TESTING: main.py no arranca BD real, scheduler, sync loop ni bot de Telegram.
# DATABASE_URL: los tests nunca usan la BD del .env ni la del shell, y la suite
# funciona en un clon limpio sin .env. Se asigna (no setdefault) a propósito,
# para que una URL de producción exportada en el shell no llegue a los tests.
os.environ["TESTING"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from pathlib import Path
import sys

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentinel.infrastructure.database import Base
from sentinel.infrastructure.orm_models import ServiceTargetTable, ServiceMetricsTable
from sentinel.infrastructure.repository import TargetRepository

# Tests escritos para funcionalidad aún no implementada (multitenencia, webhooks).
# Se conservan como especificación, pero no se recolectan hasta que exista el código.
collect_ignore_glob = ["pending/*"]

# Base de datos en memoria para tests
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reinicia los contadores de SlowAPI antes de cada test (evita contaminación entre módulos)."""
    from sentinel.infrastructure.rate_limiter import limiter

    limiter._storage.reset()
    yield


@pytest.fixture(scope="function")
def test_db():
    """Crea una BD SQLite en memoria para tests"""
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = TestingSessionLocal()
    yield db

    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def repository(test_db):
    """Proporciona un TargetRepository con DB de test"""
    return TargetRepository(test_db)


@pytest.fixture
def sample_target_data():
    """Datos de ejemplo para tests"""
    return {
        "name": "Test Service",
        "url": "https://example.com",
        "check_interval": 60,
        "is_active": True,
    }
