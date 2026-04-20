"""
Tests unitarios para TargetRepository
"""
import pytest
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentinel.domain.models import ServiceTarget
from sentinel.infrastructure.orm_models import ServiceMetricsTable


class TestTargetRepositoryCreate:
    """Tests para creación de targets"""
    
    def test_save_new_target(self, repository, sample_target_data):
        """Prueba que se puede guardar un nuevo target"""
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        assert saved.id is not None
        assert saved.name == "Test Service"
        assert saved.url == "https://example.com/"
        assert saved.check_interval == 60
    
    def test_save_target_generates_unique_id(self, repository, sample_target_data):
        """Prueba que cada target nuevo obtiene un ID único"""
        target1 = ServiceTarget(**sample_target_data)
        saved1 = repository.save_target(target1)
        
        data2 = sample_target_data.copy()
        data2["name"] = "Another Service"
        target2 = ServiceTarget(**data2)
        saved2 = repository.save_target(target2)
        
        assert saved1.id != saved2.id
    
    def test_save_target_with_custom_interval(self, repository, sample_target_data):
        """Prueba que se guarda el intervalo personalizado"""
        sample_target_data["check_interval"] = 120
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        assert saved.check_interval == 120


class TestTargetRepositoryDelete:
    """Tests para eliminación de targets"""
    
    def test_delete_existing_target(self, repository, sample_target_data):
        """Prueba que se elimina correctamente un target existente"""
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        result = repository.delete_target(saved.id)
        assert result is True
        
        # Verificar que fue eliminado
        all_targets = repository.get_all()
        assert len(all_targets) == 0
    
    def test_delete_nonexistent_target(self, repository):
        """Prueba que devuelve False al intentar eliminar un target que no existe"""
        result = repository.delete_target(9999)
        assert result is False


class TestTargetRepositoryRetrieval:
    """Tests para recuperación de targets"""
    
    def test_get_all_targets(self, repository, sample_target_data):
        """Prueba que se recuperan todos los targets"""
        # Crear 3 targets
        for i in range(3):
            data = sample_target_data.copy()
            data["name"] = f"Service {i}"
            target = ServiceTarget(**data)
            repository.save_target(target)
        
        all_targets = repository.get_all()
        assert len(all_targets) == 3
    
    def test_get_all_active_targets(self, repository, sample_target_data):
        """Prueba que solo se recuperan targets activos"""
        # Crear target activo
        target1 = ServiceTarget(**sample_target_data)
        saved1 = repository.save_target(target1)
        
        # Crear target inactivo
        data2 = sample_target_data.copy()
        data2["name"] = "Inactive Service"
        data2["is_active"] = False
        target2 = ServiceTarget(**data2)
        saved2 = repository.save_target(target2)
        
        active = repository.get_all_active()
        assert len(active) == 1
        assert active[0].id == saved1.id


class TestHealthScore:
    """Tests para cálculo de health score"""
    
    def test_health_score_perfect_uptime(self, test_db, repository, sample_target_data):
        """Prueba health score con 100% uptime"""
        # Crear target
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        # Agregar 10 métricas exitosas
        for _ in range(10):
            repository.add_metric(saved.id, status_code=200, response_time_ms=50)
        
        score = repository.calculate_health_score(saved.id)
        assert score > 80  # Score alto con uptime perfecto
    
    def test_health_score_with_failures(self, test_db, repository, sample_target_data):
        """Prueba health score con algunos errores"""
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        # 7 exitosas, 3 fallidas
        for _ in range(7):
            repository.add_metric(saved.id, status_code=200, response_time_ms=50)
        
        for _ in range(3):
            repository.add_metric(saved.id, status_code=500, response_time_ms=5000)
        
        score = repository.calculate_health_score(saved.id)
        assert 0 < score < 100  # Score intermedio
    
    def test_health_score_no_metrics(self, repository, sample_target_data):
        """Prueba que health score es neutral sin métricas"""
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        score = repository.calculate_health_score(saved.id)
        assert score == 50.0  # Score neutro


class TestMetrics:
    """Tests para agregar métricas"""
    
    def test_add_metric(self, repository, sample_target_data):
        """Prueba que se agrega correctamente una métrica"""
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        repository.add_metric(saved.id, status_code=200, response_time_ms=100)
        
        # Verificar que el target tiene status_code actualizado
        all_targets = repository.get_all()
        assert all_targets[0].status_code == 200
    
    def test_add_multiple_metrics(self, repository, sample_target_data):
        """Prueba que se agregan múltiples métricas"""
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        for i in range(5):
            repository.add_metric(saved.id, status_code=200, response_time_ms=100 + i)
        
        # Verificar que el status code es el del último
        all_targets = repository.get_all()
        assert all_targets[0].status_code == 200


class TestTargetStatistics:
    """Tests para estadísticas de targets"""
    
    def test_get_target_statistics_with_data(self, repository, sample_target_data):
        """Prueba que se calculan correctamente las estadísticas"""
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        # Agregar métricas variadas
        response_times = [100, 150, 120, 110, 200]
        for rt in response_times:
            repository.add_metric(saved.id, status_code=200, response_time_ms=rt)
        
        stats = repository.get_target_statistics(saved.id)
        
        assert stats["total_checks"] == 5
        assert stats["uptime_percent"] == 100.0
        assert 100 <= stats["avg_latency_ms"] <= 200
        assert stats["min_latency_ms"] == 100
        assert stats["max_latency_ms"] == 200
    
    def test_get_target_statistics_no_data(self, repository, sample_target_data):
        """Prueba estadísticas sin datos retorna valores neutros"""
        target = ServiceTarget(**sample_target_data)
        saved = repository.save_target(target)
        
        stats = repository.get_target_statistics(saved.id)
        
        assert stats["total_checks"] == 0
        assert stats["uptime_percent"] == 0
        assert stats["avg_latency_ms"] == 0
