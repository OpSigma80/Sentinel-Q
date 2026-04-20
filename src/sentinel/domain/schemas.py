from pydantic import BaseModel, HttpUrl, ConfigDict, Field, field_validator
from datetime import datetime
from typing import Optional

class ServiceTargetBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=256, description="Nombre único del servicio")
    url: HttpUrl = Field(..., description="URL válida del servicio a monitorear")
    check_interval: int = Field(60, ge=5, le=86400, description="Intervalo en segundos (5-86400)")
    is_active: bool = Field(True, description="Estado de actividad del target")
    
    @field_validator('name')
    @classmethod
    def name_must_not_contain_special_chars(cls, v):
        if not v.replace('-', '').replace('_', '').replace(' ', '').isalnum():
            raise ValueError('nombre debe contener solo letras, números, espacios, guiones y guiones bajos')
        return v.strip()

class ServiceTargetCreate(ServiceTargetBase):
    """Validación estricta para creación de targets"""
    pass

class ServiceTarget(ServiceTargetBase):
    """Representación completa del objeto en el sistema"""
    id: int = Field(..., gt=0, description="ID único autogenerado")
    last_check: Optional[datetime] = Field(None, description="Última verificación realizada")
    status_code: Optional[int] = Field(None, ge=100, le=599, description="Último código HTTP")
    health_score: Optional[float] = Field(None, ge=0, le=100, description="Score de salud (0-100)")
    
    model_config = ConfigDict(from_attributes=True)

class HealthCheckResponse(BaseModel):
    """Respuesta del endpoint de health check"""
    status: str = Field(..., description="Estado general: ok, degraded, critical")
    database: bool = Field(..., description="Conexión a BD operacional")
    scheduler: bool = Field(..., description="Scheduler operacional")
    active_targets: int = Field(..., description="Cantidad de targets bajo vigilancia")
    timestamp: datetime = Field(default_factory=datetime.utcnow)