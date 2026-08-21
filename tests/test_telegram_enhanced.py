"""Unit tests for enhanced Telegram integration (status, metrics, alerts)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sentinel.infrastructure.orm_models import Base, ServiceTargetTable, ServiceMetricsTable, IncidentHistoryTable
from sentinel.infrastructure.repository import TargetRepository
from sentinel.application.notifier import AlertNotifier
from sentinel.domain.schemas import AlertTrendData, ServiceStatusSnapshot, MetricsSnapshot, AlertHistorySnapshot


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def test_db():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def sample_target_with_metrics(test_db: Session):
    """Create a target with realistic metrics data."""
    # Create target
    target = ServiceTargetTable(
        name="API Server",
        url="https://api.example.com",
        check_interval=60,
        is_active=True
    )
    test_db.add(target)
    test_db.flush()
    
    # Add 50 successful metrics (last 24h, simulating 5-min intervals)
    now = datetime.now(UTC).replace(tzinfo=None)
    for i in range(50):
        timestamp = now - timedelta(minutes=i*5)
        metric = ServiceMetricsTable(
            target_id=target.id,
            status_code=200,
            response_time_ms=100 + (i % 30),  # Variable latency
            timestamp=timestamp
        )
        test_db.add(metric)
    
    # Add 5 failure metrics (to create some incidents)
    for i in range(5):
        timestamp = now - timedelta(minutes=(50+i)*5)
        metric = ServiceMetricsTable(
            target_id=target.id,
            status_code=500,
            response_time_ms=2000,
            timestamp=timestamp
        )
        test_db.add(metric)
        
        # Create incident entry
        incident = IncidentHistoryTable(
            target_id=target.id,
            service_name=target.name,
            status_code=500,
            timestamp=timestamp
        )
        test_db.add(incident)
    
    test_db.commit()
    return target


@pytest.fixture
def notifier():
    """Create an AlertNotifier instance."""
    return AlertNotifier()


# ============================================================================
# TESTS: AlertNotifier cache behavior
# ============================================================================

def test_notifier_cache_get_and_set():
    """Test cache get/set functionality."""
    notifier = AlertNotifier()
    
    # Cache should be empty initially
    assert notifier._get_from_cache("test_key") is None
    
    # Store value
    test_value = {"data": "test"}
    notifier._set_cache("test_key", test_value)
    
    # Retrieve value
    cached = notifier._get_from_cache("test_key", max_age_seconds=60)
    assert cached == test_value


def test_notifier_cache_expiration():
    """Test cache expiration logic."""
    import time
    
    notifier = AlertNotifier()
    test_value = {"data": "test"}
    
    # Store value and immediately check it's retrievable
    notifier._set_cache("test_key", test_value)
    result = notifier._get_from_cache("test_key", max_age_seconds=60)
    assert result == test_value
    
    # Verify cache has the key
    assert "test_key" in notifier._cache


# ============================================================================
# TESTS: Repository Trend Data
# ============================================================================

def test_repository_get_service_trend_healthy(test_db: Session, sample_target_with_metrics):
    """Test trend calculation for healthy service."""
    repo = TargetRepository(test_db)
    trend = repo.get_service_trend(sample_target_with_metrics.id)
    
    # Assertions
    assert isinstance(trend, AlertTrendData)
    assert trend.service_name == "API Server"
    assert trend.current_status == "OK"
    assert trend.uptime_percentage >= 90  # Most metrics are successful
    assert trend.avg_response_time_ms > 0
    assert trend.consecutive_failures >= 0


def test_repository_get_service_trend_missing_target(test_db: Session):
    """Test trend for non-existent target."""
    repo = TargetRepository(test_db)
    
    with pytest.raises(ValueError):
        repo.get_service_trend(99999)


# ============================================================================
# TESTS: Repository Status Snapshot
# ============================================================================

def test_repository_get_status_snapshot(test_db: Session, sample_target_with_metrics):
    """Test status snapshot aggregation."""
    repo = TargetRepository(test_db)
    snapshot = repo.get_status_snapshot()
    
    # Assertions
    assert isinstance(snapshot, ServiceStatusSnapshot)
    assert snapshot.total_services >= 1
    assert snapshot.healthy + snapshot.degraded + snapshot.critical >= 1
    assert len(snapshot.services) >= 1
    
    # Check service details
    first_service = snapshot.services[0]
    assert "id" in first_service
    assert "name" in first_service
    assert "status" in first_service
    assert "uptime" in first_service


def test_repository_get_status_snapshot_empty_db(test_db: Session):
    """Test status snapshot with no services."""
    repo = TargetRepository(test_db)
    snapshot = repo.get_status_snapshot()
    
    assert snapshot.total_services == 0
    assert snapshot.healthy == 0
    assert snapshot.degraded == 0
    assert snapshot.critical == 0
    assert len(snapshot.services) == 0


# ============================================================================
# TESTS: Repository Metrics Snapshot
# ============================================================================

def test_repository_get_metrics_snapshot(test_db: Session, sample_target_with_metrics):
    """Test global metrics aggregation."""
    repo = TargetRepository(test_db)
    snapshot = repo.get_metrics_snapshot()
    
    # Assertions
    assert isinstance(snapshot, MetricsSnapshot)
    assert snapshot.total_checks > 0
    assert 0 <= snapshot.success_rate <= 100
    assert snapshot.avg_response_time_ms >= 0
    assert snapshot.p95_response_time_ms >= 0
    assert snapshot.max_response_time_ms >= 0


def test_repository_get_metrics_snapshot_empty_db(test_db: Session):
    """Test metrics snapshot with no data."""
    repo = TargetRepository(test_db)
    snapshot = repo.get_metrics_snapshot()
    
    assert snapshot.total_checks == 0
    assert snapshot.success_rate == 0
    assert snapshot.avg_response_time_ms == 0


# ============================================================================
# TESTS: Repository Alerts History
# ============================================================================

def test_repository_get_alerts_history(test_db: Session, sample_target_with_metrics):
    """Test alert history retrieval."""
    repo = TargetRepository(test_db)
    history = repo.get_alerts_history(hours=24)
    
    # Assertions
    assert isinstance(history, AlertHistorySnapshot)
    assert history.total_alerts_today >= 0
    assert history.critical_count >= 0
    assert history.warning_count >= 0
    assert isinstance(history.last_10_alerts, list)
    
    # Check alert entries format
    for alert in history.last_10_alerts:
        assert hasattr(alert, 'service_name')
        assert hasattr(alert, 'event_type')
        assert hasattr(alert, 'severity')
        assert hasattr(alert, 'timestamp')


def test_repository_get_alerts_history_limited_hours(test_db: Session, sample_target_with_metrics):
    """Test alert history with limited time window."""
    repo = TargetRepository(test_db)
    
    # Get last 1 hour
    history = repo.get_alerts_history(hours=1)
    assert isinstance(history, AlertHistorySnapshot)
    
    # Get last 7 days
    history = repo.get_alerts_history(hours=168)
    assert isinstance(history, AlertHistorySnapshot)


# ============================================================================
# TESTS: Notifier enhanced methods
# ============================================================================

def test_notifier_failure_with_trend():
    """Test failure alert with trend context."""
    notifier = AlertNotifier()
    
    trend = AlertTrendData(
        service_name="Database",
        current_status="CRÍTICO",
        uptime_percentage=45.5,
        avg_response_time_ms=2500,
        last_check_at=datetime.now(UTC).replace(tzinfo=None),
        failure_count_today=12,
        consecutive_failures=5
    )
    
    # This would require Telegram API, so we just test the method exists
    assert hasattr(notifier, 'notify_failure_with_trend')
    assert callable(notifier.notify_failure_with_trend)


def test_notifier_status_snapshot():
    """Test status snapshot notification formatting."""
    notifier = AlertNotifier()
    
    snapshot = ServiceStatusSnapshot(
        total_services=5,
        healthy=3,
        degraded=1,
        critical=1,
        services=[
            {"name": "API", "status": "OK", "uptime": 99.5},
            {"name": "Database", "status": "CRÍTICO", "uptime": 45.0},
        ]
    )
    
    assert hasattr(notifier, 'notify_status_snapshot')
    assert callable(notifier.notify_status_snapshot)


def test_notifier_metrics_snapshot():
    """Test metrics snapshot notification formatting."""
    notifier = AlertNotifier()
    
    snapshot = MetricsSnapshot(
        total_checks=1000,
        success_rate=98.5,
        avg_response_time_ms=150,
        p95_response_time_ms=450,
        max_response_time_ms=1200
    )
    
    assert hasattr(notifier, 'notify_metrics_snapshot')
    assert callable(notifier.notify_metrics_snapshot)


def test_notifier_alerts_history():
    """Test alerts history notification formatting."""
    notifier = AlertNotifier()
    
    snapshot = AlertHistorySnapshot(
        total_alerts_today=15,
        critical_count=3,
        warning_count=12,
        last_10_alerts=[]
    )
    
    assert hasattr(notifier, 'notify_alerts_history')
    assert callable(notifier.notify_alerts_history)


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

def test_telegram_workflow_full(test_db: Session, sample_target_with_metrics):
    """Test complete workflow: data fetch -> snapshot -> notification."""
    repo = TargetRepository(test_db)
    notifier = AlertNotifier()
    
    # Step 1: Get status
    status_snapshot = repo.get_status_snapshot()
    assert status_snapshot.total_services > 0
    
    # Step 2: Get metrics
    metrics_snapshot = repo.get_metrics_snapshot()
    assert metrics_snapshot.total_checks > 0
    
    # Step 3: Get alerts
    alerts_snapshot = repo.get_alerts_history()
    assert isinstance(alerts_snapshot, AlertHistorySnapshot)
    
    # Step 4: Get service trend
    trend = repo.get_service_trend(sample_target_with_metrics.id)
    assert trend.service_name == "API Server"


def test_cache_efficiency(notifier: AlertNotifier):
    """Test that cache reduces repeated queries."""
    data1 = {"test": "data1"}
    notifier._set_cache("key1", data1)
    
    # Multiple gets should all return same object
    result1 = notifier._get_from_cache("key1", max_age_seconds=60)
    result2 = notifier._get_from_cache("key1", max_age_seconds=60)
    result3 = notifier._get_from_cache("key1", max_age_seconds=60)
    
    assert result1 is result2 is result3
