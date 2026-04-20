"""
Tests para validación de Pydantic schemas
"""
import pytest
from pydantic import ValidationError
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentinel.domain.schemas import ServiceTargetCreate


class TestValidationConstraints:
    """Tests para validaciones de seguridad en inputs"""
    
    def test_check_interval_minimum(self):
        """Prueba que check_interval no puede ser menor a 5 segundos"""
        with pytest.raises(ValidationError) as exc_info:
            ServiceTargetCreate(
                name="Test",
                url="https://example.com",
                check_interval=2  # Menor que el mínimo
            )
        assert "greater than or equal to 5" in str(exc_info.value)
    
    def test_check_interval_maximum(self):
        """Prueba que check_interval no puede exceder 86400 segundos (1 día)"""
        with pytest.raises(ValidationError) as exc_info:
            ServiceTargetCreate(
                name="Test",
                url="https://example.com",
                check_interval=100000  # Mayor que el máximo
            )
        assert "less than or equal to 86400" in str(exc_info.value)
    
    def test_check_interval_valid_range(self):
        """Prueba que valores válidos de check_interval se aceptan"""
        # Mínimo válido
        target = ServiceTargetCreate(
            name="Test",
            url="https://example.com",
            check_interval=5
        )
        assert target.check_interval == 5
        
        # Máximo válido
        target = ServiceTargetCreate(
            name="Test",
            url="https://example.com",
            check_interval=86400
        )
        assert target.check_interval == 86400
    
    def test_name_length_maximum(self):
        """Prueba que el nombre no puede exceder 256 caracteres"""
        long_name = "a" * 257
        with pytest.raises(ValidationError) as exc_info:
            ServiceTargetCreate(
                name=long_name,
                url="https://example.com"
            )
        assert "String should have at most 256 characters" in str(exc_info.value)
    
    def test_name_cannot_be_empty(self):
        """Prueba que el nombre no puede estar vacío"""
        with pytest.raises(ValidationError) as exc_info:
            ServiceTargetCreate(
                name="",
                url="https://example.com"
            )
        assert "String should have at least 1 character" in str(exc_info.value)
    
    def test_name_with_valid_characters(self):
        """Prueba que el nombre acepta letras, números, espacios, guiones y guiones bajos"""
        target = ServiceTargetCreate(
            name="My-Service_01 Test",
            url="https://example.com"
        )
        assert target.name == "My-Service_01 Test"
    
    def test_name_with_invalid_special_characters(self):
        """Prueba que el nombre rechaza caracteres especiales"""
        with pytest.raises(ValidationError) as exc_info:
            ServiceTargetCreate(
                name="Service@#$%",
                url="https://example.com"
            )
        assert "debe contener solo letras" in str(exc_info.value)
    
    def test_url_must_be_valid(self):
        """Prueba que la URL debe ser válida"""
        with pytest.raises(ValidationError):
            ServiceTargetCreate(
                name="Test",
                url="not-a-valid-url"
            )
    
    def test_url_https_accepted(self):
        """Prueba que URLs HTTPS se aceptan"""
        target = ServiceTargetCreate(
            name="Test",
            url="https://example.com/path"
        )
        assert str(target.url) == "https://example.com/path"
    
    def test_url_http_accepted(self):
        """Prueba que URLs HTTP se aceptan"""
        target = ServiceTargetCreate(
            name="Test",
            url="http://example.com"
        )
        assert "http://example.com" in str(target.url)
    
    def test_name_whitespace_trimming(self):
        """Prueba que espacios en blanco se recortan en nombres"""
        target = ServiceTargetCreate(
            name="  Test Service  ",
            url="https://example.com"
        )
        assert target.name == "Test Service"
