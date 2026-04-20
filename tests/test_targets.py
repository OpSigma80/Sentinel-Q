"""
Unit tests for Target CRUD operations and validation.
Tests the repository layer and target lifecycle management.
"""

import pytest
from sentinel.infrastructure.repository import TargetRepository
from sentinel.infrastructure.orm_models import ServiceTargetTable
from sentinel.domain.models import ServiceTarget as DomainServiceTarget
from pydantic import ValidationError


class TestTargetCRUDOperations:
    """Test suite for Create, Read, Update, Delete operations"""
    
    def test_create_target_with_valid_data(self, db_session):
        """Test successful target creation with valid input"""
        repo = TargetRepository(db_session)
        
        domain_target = DomainServiceTarget(
            name="Google Search",
            url="https://www.google.com",
            check_interval=60,
            is_active=True
        )
        
        saved = repo.save_target(domain_target)
        
        assert saved.id is not None, "Saved target should have auto-generated ID"
        assert saved.name == "Google Search"
        assert saved.url == "https://www.google.com/"
        assert saved.check_interval == 60
        assert saved.is_active is True
    
    def test_read_target_by_id(self, db_session):
        """Test retrieving a target by ID"""
        repo = TargetRepository(db_session)
        
        # Create target
        domain_target = DomainServiceTarget(
            name="Test Service",
            url="http://localhost:8000",
            check_interval=30,
            is_active=True
        )
        saved = repo.save_target(domain_target)
        target_id = saved.id
        
        # Retrieve
        retrieved = db_session.query(ServiceTargetTable).filter(
            ServiceTargetTable.id == target_id
        ).first()
        
        assert retrieved is not None
        assert retrieved.name == "Test Service"
        assert retrieved.check_interval == 30
    
    def test_update_target(self, db_session):
        """Test updating an existing target"""
        repo = TargetRepository(db_session)
        
        # Create
        domain_target = DomainServiceTarget(
            name="Original Name",
            url="http://localhost:8000",
            check_interval=60,
            is_active=True
        )
        saved = repo.save_target(domain_target)
        
        # Update
        updated_domain = DomainServiceTarget(
            id=saved.id,
            name="Updated Name",
            url="http://localhost:9000",
            check_interval=120,
            is_active=True
        )
        updated = repo.save_target(updated_domain)
        
        assert updated.name == "Updated Name"
        assert updated.check_interval == 120
    
    def test_delete_target_success(self, db_session):
        """Test successful target deletion"""
        repo = TargetRepository(db_session)
        
        # Create
        domain_target = DomainServiceTarget(
            name="To Delete",
            url="http://localhost:8000",
            check_interval=60,
            is_active=True
        )
        saved = repo.save_target(domain_target)
        target_id = saved.id
        
        # Delete
        result = repo.delete_target(target_id)
        
        assert result is True, "Delete should return True on success"
        
        # Verify deletion
        retrieved = db_session.query(ServiceTargetTable).filter(
            ServiceTargetTable.id == target_id
        ).first()
        assert retrieved is None, "Target should be removed from DB"
    
    def test_delete_nonexistent_target(self, db_session):
        """Test deleting a target that doesn't exist"""
        repo = TargetRepository(db_session)
        
        result = repo.delete_target(9999)
        
        assert result is False, "Delete should return False for nonexistent target"
    
    def test_get_all_targets(self, db_session):
        """Test retrieving all targets"""
        repo = TargetRepository(db_session)
        
        # Create multiple targets
        for i in range(3):
            domain_target = DomainServiceTarget(
                name=f"Service {i}",
                url=f"http://localhost:{8000+i}",
                check_interval=60,
                is_active=True
            )
            repo.save_target(domain_target)
        
        all_targets = repo.get_all()
        
        assert len(all_targets) == 3
        assert all(t.is_active for t in all_targets)
    
    def test_get_all_active_targets(self, db_session):
        """Test retrieving only active targets"""
        repo = TargetRepository(db_session)
        
        # Create active and inactive targets
        active = DomainServiceTarget(
            name="Active Service",
            url="http://localhost:8000",
            check_interval=60,
            is_active=True
        )
        inactive = DomainServiceTarget(
            name="Inactive Service",
            url="http://localhost:8001",
            check_interval=60,
            is_active=False
        )
        
        repo.save_target(active)
        repo.save_target(inactive)
        
        active_only = repo.get_all_active()
        
        assert len(active_only) == 1
        assert active_only[0].name == "Active Service"


class TestTargetValidation:
    """Test suite for input validation using Pydantic"""
    
    def test_url_validation_http(self):
        """Test that HTTP URLs are accepted"""
        target = DomainServiceTarget(
            name="Valid HTTP",
            url="http://example.com",
            check_interval=60,
            is_active=True
        )
        assert str(target.url) == "http://example.com/"
    
    def test_url_validation_https(self):
        """Test that HTTPS URLs are accepted"""
        target = DomainServiceTarget(
            name="Valid HTTPS",
            url="https://secure.example.com",
            check_interval=60,
            is_active=True
        )
        assert "secure.example.com" in str(target.url)
    
    def test_url_validation_invalid(self):
        """Test that invalid URLs are rejected"""
        with pytest.raises(ValidationError) as exc_info:
            DomainServiceTarget(
                name="Invalid URL",
                url="not a url",
                check_interval=60,
                is_active=True
            )
        assert "url" in str(exc_info.value).lower()
    
    def test_check_interval_minimum(self):
        """Test that check_interval below 5s is rejected"""
        with pytest.raises(ValidationError):
            DomainServiceTarget(
                name="Too Fast",
                url="http://localhost:8000",
                check_interval=4,  # Below minimum
                is_active=True
            )
    
    def test_check_interval_maximum(self):
        """Test that check_interval above 86400s is rejected"""
        with pytest.raises(ValidationError):
            DomainServiceTarget(
                name="Too Slow",
                url="http://localhost:8000",
                check_interval=86401,  # Above maximum
                is_active=True
            )
    
    def test_check_interval_valid_range(self):
        """Test valid check_interval values"""
        # Minimum valid
        target1 = DomainServiceTarget(
            name="Min Interval",
            url="http://localhost:8000",
            check_interval=5,
            is_active=True
        )
        assert target1.check_interval == 5
        
        # Maximum valid
        target2 = DomainServiceTarget(
            name="Max Interval",
            url="http://localhost:8000",
            check_interval=86400,
            is_active=True
        )
        assert target2.check_interval == 86400
    
    def test_name_validation_special_chars(self):
        """Test that names with invalid characters are rejected"""
        with pytest.raises(ValidationError):
            DomainServiceTarget(
                name="Invalid@Name#",
                url="http://localhost:8000",
                check_interval=60,
                is_active=True
            )
    
    def test_name_validation_valid_chars(self):
        """Test that names with valid characters are accepted"""
        valid_names = [
            "Service-Name",
            "Service_Name",
            "Service Name",
            "ServiceName123"
        ]
        
        for name in valid_names:
            target = DomainServiceTarget(
                name=name,
                url="http://localhost:8000",
                check_interval=60,
                is_active=True
            )
            assert target.name == name.strip()


@pytest.fixture
def db_session():
    """Fixture providing a test database session"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sentinel.infrastructure.orm_models import Base
    
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    yield session
    
    session.close()
    engine.dispose()
