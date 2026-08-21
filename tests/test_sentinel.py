"""
Unit Tests for Sentinel-Q System
Tests core functionality: target creation, health scoring, metric calculation
"""

import pytest
from datetime import datetime, timedelta
from pydantic import ValidationError
from sentinel.domain.models import ServiceTarget
from sentinel.infrastructure.orm_models import ServiceTargetTable, ServiceMetricsTable
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sentinel.infrastructure.database import Base
from sentinel.infrastructure.repository import TargetRepository
from sentinel.services.analytics_service import AnalyticsService


# --- FIXTURES ---

@pytest.fixture(scope="function")
def test_db():
    """Create an in-memory SQLite database for testing"""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    yield session
    
    session.close()
    engine.dispose()


# --- TEST: Pydantic Validation ---

def test_service_target_valid_creation():
    """Test successful creation of valid ServiceTarget"""
    target = ServiceTarget(
        name="Google API",
        url="https://www.google.com",
        check_interval=60,
        is_active=True
    )
    assert target.name == "Google API"
    assert str(target.url) == "https://www.google.com/"
    assert target.check_interval == 60
    assert target.id is None  # Not yet in DB


def test_service_target_name_validation():
    """Test that invalid names are rejected"""
    with pytest.raises(ValidationError) as exc_info:
        ServiceTarget(
            name="@#$%^&*()",  # Invalid characters
            url="https://example.com",
            check_interval=60
        )
    assert "Name must contain only alphanumeric" in str(exc_info.value)


def test_service_target_name_too_short():
    """Test that name must be at least 3 characters"""
    with pytest.raises(ValidationError) as exc_info:
        ServiceTarget(
            name="Go",  # Too short
            url="https://example.com",
            check_interval=60
        )
    assert "at least 3 characters" in str(exc_info.value).lower()


def test_service_target_name_too_long():
    """Test that name cannot exceed 100 characters"""
    long_name = "A" * 101
    with pytest.raises(ValidationError) as exc_info:
        ServiceTarget(
            name=long_name,
            url="https://example.com",
            check_interval=60
        )
    assert "at most 100 characters" in str(exc_info.value).lower()


def test_service_target_url_validation():
    """Test that invalid URLs are rejected"""
    with pytest.raises(ValidationError):
        ServiceTarget(
            name="Invalid Service",
            url="not-a-url",  # Missing protocol
            check_interval=60
        )


def test_service_target_check_interval_min():
    """Test that check_interval must be at least 5 seconds"""
    with pytest.raises(ValidationError) as exc_info:
        ServiceTarget(
            name="Test Service",
            url="https://example.com",
            check_interval=3  # Too low
        )
    assert "greater than or equal to 5" in str(exc_info.value)


def test_service_target_check_interval_max():
    """Test that check_interval cannot exceed 86400 seconds (1 day)"""
    with pytest.raises(ValidationError) as exc_info:
        ServiceTarget(
            name="Test Service",
            url="https://example.com",
            check_interval=86401  # Too high
        )
    assert "less than or equal to 86400" in str(exc_info.value)


def test_service_target_field_defaults():
    """Test that defaults are applied correctly"""
    target = ServiceTarget(
        name="Minimal Target",
        url="https://example.com"
    )
    assert target.check_interval == 60
    assert target.is_active is True
    assert target.last_check is None
    assert target.status_code is None


# --- TEST: Repository Operations ---

def test_save_new_target(test_db):
    """Test creating a new target (no ID)"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="GitHub API",
        url="https://api.github.com",
        check_interval=30,
        is_active=True
    )
    
    saved = repo.save_target(target)
    
    assert saved.id is not None
    assert saved.id == 1  # First target
    assert saved.name == "GitHub API"
    assert saved.check_interval == 30


def test_update_existing_target(test_db):
    """Test updating an existing target"""
    repo = TargetRepository(test_db)
    
    # Create initial target
    target1 = ServiceTarget(
        name="Original Name",
        url="https://example.com",
        check_interval=60
    )
    saved1 = repo.save_target(target1)
    
    # Update target
    target2 = ServiceTarget(
        id=saved1.id,
        name="Updated Name",
        url="https://example.com",
        check_interval=120
    )
    saved2 = repo.save_target(target2)
    
    assert saved2.id == saved1.id
    assert saved2.name == "Updated Name"
    assert saved2.check_interval == 120


def test_delete_target(test_db):
    """Test deleting a target"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="To Delete",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    target_id = saved.id
    
    # Verify it exists
    all_targets = repo.get_all()
    assert len(all_targets) == 1
    
    # Delete it
    deleted = repo.delete_target(target_id)
    assert deleted is True
    
    # Verify it's gone
    all_targets = repo.get_all()
    assert len(all_targets) == 0


def test_get_all_targets(test_db):
    """Test retrieving all targets"""
    repo = TargetRepository(test_db)
    
    # Create multiple targets
    for i in range(3):
        target = ServiceTarget(
            name=f"Service {i}",
            url=f"https://service{i}.com",
            check_interval=60 + (i * 10)
        )
        repo.save_target(target)
    
    all_targets = repo.get_all()
    assert len(all_targets) == 3


def test_add_metric(test_db):
    """Test adding a metric for a target"""
    repo = TargetRepository(test_db)
    
    # Create target first
    target = ServiceTarget(
        name="Monitor Me",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    
    # Add metric
    repo.add_metric(saved.id, status_code=200, response_time_ms=45.2)
    
    # Verify metric was added
    metrics = test_db.query(ServiceMetricsTable).filter(
        ServiceMetricsTable.target_id == saved.id
    ).all()
    
    assert len(metrics) == 1
    assert metrics[0].status_code == 200
    assert metrics[0].response_time_ms == 45.2


# --- TEST: Health Score Calculation ---

def test_health_score_no_metrics(test_db):
    """Test health score returns 50.0 when no metrics exist"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="No Metrics",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    
    score = AnalyticsService(test_db).calculate_health_score(saved.id)
    assert score == 50.0  # Default neutral score


def test_health_score_all_successful(test_db):
    """Test health score when all responses are successful"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="Healthy Service",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    
    # Add 10 successful metrics
    for _ in range(10):
        repo.add_metric(saved.id, status_code=200, response_time_ms=50.0)
    
    score = AnalyticsService(test_db).calculate_health_score(saved.id)
    assert score == 100.0  # Perfect score: 100% uptime, stable latency


def test_health_score_partial_failures(test_db):
    """Test health score with some failed responses"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="Flaky Service",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    
    # Add 9 successful + 1 failed metrics
    for i in range(10):
        status = 200 if i < 9 else 500
        repo.add_metric(saved.id, status_code=status, response_time_ms=50.0)
    
    score = AnalyticsService(test_db).calculate_health_score(saved.id)
    assert 80.0 < score < 100.0  # Between 80-100, not perfect
    assert score == 94.0  # 90% uptime plus stable latency weighting


def test_health_score_latency_stability(test_db):
    """Test health score considers latency stability"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="Variable Service",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    
    # Add metrics with varying latencies
    latencies = [50, 45, 60, 55, 48, 200, 52, 49, 51, 50]  # One spike to 200ms
    for latency in latencies:
        repo.add_metric(saved.id, status_code=200, response_time_ms=latency)
    
    score = AnalyticsService(test_db).calculate_health_score(saved.id)
    # Score should be lower due to latency instability
    assert score < 100.0
    assert score > 50.0


# --- TEST: Metric Statistics ---

def test_get_target_statistics(test_db):
    """Test retrieving statistics for a target"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="Stats Target",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    
    # Add diverse metrics
    for i in range(5):
        repo.add_metric(saved.id, status_code=200, response_time_ms=50 + (i * 10))
    
    stats = AnalyticsService(test_db).get_target_statistics(saved.id)
    
    assert stats['uptime_percent'] == 100.0
    assert stats['avg_latency_ms'] > 0
    assert stats['total_checks'] == 5


# --- Test Edge Cases ---

def test_metric_with_zero_response_time(test_db):
    """Test adding a metric with 0ms response time (shouldn't crash)"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="Fast Service",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    
    repo.add_metric(saved.id, status_code=200, response_time_ms=0.0)
    metrics = test_db.query(ServiceMetricsTable).all()
    assert len(metrics) == 1


def test_metric_with_http_4xx_error(test_db):
    """Test adding metric with 4xx error status"""
    repo = TargetRepository(test_db)
    
    target = ServiceTarget(
        name="Error Service",
        url="https://example.com"
    )
    saved = repo.save_target(target)
    
    repo.add_metric(saved.id, status_code=404, response_time_ms=30.0)
    metrics = test_db.query(ServiceMetricsTable).all()
    
    assert len(metrics) == 1
    assert metrics[0].status_code == 404


# --- Run Tests ---
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
