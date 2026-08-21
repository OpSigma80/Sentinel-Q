"""
Unit tests for health scoring algorithm and metrics calculation.
Tests the core algorithms that determine service health (0-100 scale).
"""

import pytest
from datetime import datetime
from sentinel.infrastructure.repository import TargetRepository
from sentinel.infrastructure.orm_models import ServiceTargetTable, ServiceMetricsTable
from sentinel.services.analytics_service import AnalyticsService
from sqlalchemy.orm import Session


class TestHealthScoringAlgorithm:
    """Test suite for the health scoring engine"""
    
    def test_health_score_perfect_uptime(self, db_session: Session):
        """
        Health score should be ~100 when:
        - All responses are 2xx-3xx (uptime = 100%)
        - Latency is stable (low variance)
        """
        repo = TargetRepository(db_session)
        
        # Create test target
        target = ServiceTargetTable(
            name="Test Service Healthy",
            url="http://localhost:8000",
            check_interval=60,
            is_active=True
        )
        db_session.add(target)
        db_session.commit()
        
        # Add perfect metrics: all 200 OK, consistent latency
        for i in range(10):
            metric = ServiceMetricsTable(
                target_id=target.id,
                status_code=200,
                response_time_ms=100.0  # Consistent latency
            )
            db_session.add(metric)
        db_session.commit()
        
        health_score = AnalyticsService(db_session).calculate_health_score(target.id)
        
        assert health_score >= 90, f"Perfect uptime should score >= 90, got {health_score}"
        assert health_score <= 100, f"Health score should not exceed 100, got {health_score}"
    
    def test_health_score_degraded_service(self, db_session: Session):
        """
        Health score should be moderate when:
        - Some responses are errors (50% uptime)
        - Latency increases
        """
        repo = TargetRepository(db_session)
        
        target = ServiceTargetTable(
            name="Test Service Degraded",
            url="http://localhost:8001",
            check_interval=60,
            is_active=True
        )
        db_session.add(target)
        db_session.commit()
        
        # Add degraded metrics: 50% success, variable latency
        for i in range(10):
            status = 200 if i % 2 == 0 else 500
            latency = 100.0 if i % 2 == 0 else 500.0
            metric = ServiceMetricsTable(
                target_id=target.id,
                status_code=status,
                response_time_ms=latency
            )
            db_session.add(metric)
        db_session.commit()
        
        health_score = AnalyticsService(db_session).calculate_health_score(target.id)
        
        assert 30 <= health_score <= 70, f"Degraded service should score 30-70, got {health_score}"
    
    def test_health_score_down_service(self, db_session: Session):
        """
        Health score should be low when:
        - All responses are errors (0% uptime)
        - Timeouts occur
        """
        repo = TargetRepository(db_session)
        
        target = ServiceTargetTable(
            name="Test Service Down",
            url="http://localhost:8002",
            check_interval=60,
            is_active=True
        )
        db_session.add(target)
        db_session.commit()
        
        # Add all error metrics
        for i in range(10):
            metric = ServiceMetricsTable(
                target_id=target.id,
                status_code=503,  # Service Unavailable
                response_time_ms=10000.0  # Very high latency (timeout)
            )
            db_session.add(metric)
        db_session.commit()
        
        health_score = AnalyticsService(db_session).calculate_health_score(target.id)
        
        assert health_score <= 50, f"Down service should score <= 50, got {health_score}"
    
    def test_health_score_no_data(self, db_session: Session):
        """
        Health score should be neutral (50) when no metrics exist
        """
        repo = TargetRepository(db_session)
        
        target = ServiceTargetTable(
            name="Test Service No Data",
            url="http://localhost:8003",
            check_interval=60,
            is_active=True
        )
        db_session.add(target)
        db_session.commit()
        
        health_score = AnalyticsService(db_session).calculate_health_score(target.id)
        
        assert health_score == 50.0, f"No data should score 50.0, got {health_score}"


class TestMetricsCalculation:
    """Test suite for metrics calculation and statistics"""
    
    def test_uptime_calculation_all_success(self, db_session: Session):
        """Uptime should be 100% when all responses are 2xx-3xx"""
        repo = TargetRepository(db_session)
        
        target = ServiceTargetTable(
            name="Test Uptime",
            url="http://localhost:9000",
            check_interval=60,
            is_active=True
        )
        db_session.add(target)
        db_session.commit()
        
        # Add 100 success metrics
        for i in range(100):
            metric = ServiceMetricsTable(
                target_id=target.id,
                status_code=200 + (i % 3),  # 200, 201, 202
                response_time_ms=50.0
            )
            db_session.add(metric)
        db_session.commit()
        
        stats = AnalyticsService(db_session).get_target_statistics(target.id)
        
        assert stats["uptime_percent"] == 100.0, f"100% success should give 100% uptime"
        assert stats["total_checks"] == 100, f"Should have 100 checks"
    
    def test_latency_calculation(self, db_session: Session):
        """Test average, min, and max latency calculations"""
        repo = TargetRepository(db_session)
        
        target = ServiceTargetTable(
            name="Test Latency",
            url="http://localhost:9001",
            check_interval=60,
            is_active=True
        )
        db_session.add(target)
        db_session.commit()
        
        # Add metrics with known latencies: 50, 100, 150
        latencies = [50.0, 100.0, 150.0] * 3  # Repeat for sample size
        for latency in latencies:
            metric = ServiceMetricsTable(
                target_id=target.id,
                status_code=200,
                response_time_ms=latency
            )
            db_session.add(metric)
        db_session.commit()
        
        stats = AnalyticsService(db_session).get_target_statistics(target.id)
        
        assert stats["min_latency_ms"] == 50.0, f"Min latency should be 50.0"
        assert stats["max_latency_ms"] == 150.0, f"Max latency should be 150.0"
        assert stats["avg_latency_ms"] == 100.0, f"Avg latency should be 100.0"
    
    def test_mixed_status_codes(self, db_session: Session):
        """Test uptime calculation with mixed success/failure status codes"""
        repo = TargetRepository(db_session)
        
        target = ServiceTargetTable(
            name="Test Mixed Status",
            url="http://localhost:9002",
            check_interval=60,
            is_active=True
        )
        db_session.add(target)
        db_session.commit()
        
        # Add 50 success (2xx-3xx) and 50 errors (4xx-5xx)
        for i in range(50):
            # Success
            metric1 = ServiceMetricsTable(
                target_id=target.id,
                status_code=200,
                response_time_ms=100.0
            )
            # Error
            metric2 = ServiceMetricsTable(
                target_id=target.id,
                status_code=500,
                response_time_ms=5000.0
            )
            db_session.add(metric1)
            db_session.add(metric2)
        db_session.commit()
        
        stats = AnalyticsService(db_session).get_target_statistics(target.id)
        
        assert stats["uptime_percent"] == 50.0, f"50% success should give 50% uptime"
        assert stats["total_checks"] == 100, f"Should have 100 total checks"


@pytest.fixture
def db_session():
    """
    Fixture providing a test database session.
    Uses SQLite in-memory for fast, isolated tests.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sentinel.infrastructure.orm_models import Base
    
    # Create in-memory SQLite database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    yield session
    
    session.close()
    engine.dispose()
