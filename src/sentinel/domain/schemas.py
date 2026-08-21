from pydantic import BaseModel, HttpUrl, ConfigDict, Field, field_validator
from datetime import datetime
from typing import Optional

class ServiceTargetBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=256, description="Nombre único identificable del servicio", examples=["API-Produccion-Central"])
    url: HttpUrl = Field(..., description="URL absoluta del servicio a monitorear", examples=["https://api.example.com/health"])
    check_interval: int = Field(60, ge=5, le=86400, description="Frecuencia de monitoreo en segundos (Rango: 5s a 24h)", examples=[60])
    is_active: bool = Field(True, description="Define si el servicio está bajo vigilancia activa")
    
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


# ============================================================================
# TELEGRAM MEJORADO - Schemas para mensajes ricos y comandos interactivos
# ============================================================================

class AlertTrendData(BaseModel):
    """Trend e histórico de una alerta para contexto en Telegram"""
    service_name: str = Field(..., description="Nombre del servicio")
    current_status: str = Field(..., description="Estado actual: CRÍTICO, ADVERTENCIA, OK")
    uptime_percentage: float = Field(..., ge=0, le=100, description="Uptime % en últimas 24h")
    avg_response_time_ms: float = Field(..., ge=0, description="Latencia promedio")
    last_check_at: datetime = Field(..., description="Última verificación")
    failure_count_today: int = Field(default=0, ge=0, description="Fallos en las últimas 24h")
    consecutive_failures: int = Field(default=0, ge=0, description="Fallos consecutivos actuales")
    
    model_config = ConfigDict(from_attributes=True)


class ServiceStatusSnapshot(BaseModel):
    """Snapshot del estado de todos los servicios para /status"""
    total_services: int = Field(..., ge=0, description="Total de servicios monitoreados")
    healthy: int = Field(default=0, ge=0, description="Servicios OK")
    degraded: int = Field(default=0, ge=0, description="Servicios con problemas")
    critical: int = Field(default=0, ge=0, description="Servicios críticos")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    services: list[dict] = Field(default_factory=list, description="Detalles de cada servicio")
    
    model_config = ConfigDict(from_attributes=True)


class MetricsSnapshot(BaseModel):
    """Snapshot de métricas globales para /metrics"""
    total_checks: int = Field(default=0, ge=0, description="Total de checks en 24h")
    success_rate: float = Field(default=100.0, ge=0, le=100, description="% de checks exitosos")
    avg_response_time_ms: float = Field(default=0, ge=0, description="Latencia promedio")
    p95_response_time_ms: float = Field(default=0, ge=0, description="P95 latencia")
    max_response_time_ms: float = Field(default=0, ge=0, description="Latencia máxima")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    model_config = ConfigDict(from_attributes=True)


class AlertHistoryEntry(BaseModel):
    """Un evento en el historial de alertas"""
    service_name: str = Field(..., description="Nombre del servicio")
    event_type: str = Field(..., description="FAILURE, RECOVERY, DEGRADED")
    severity: str = Field(..., description="CRÍTICO, ADVERTENCIA, INFO")
    message: str = Field(..., description="Descripción del evento")
    timestamp: datetime = Field(..., description="Cuándo ocurrió")
    
    model_config = ConfigDict(from_attributes=True)


class AlertHistorySnapshot(BaseModel):
    """Histórico de alertas para /alerts"""
    total_alerts_today: int = Field(default=0, ge=0, description="Total de alertas en 24h")
    critical_count: int = Field(default=0, ge=0, description="Alertas críticas")
    warning_count: int = Field(default=0, ge=0, description="Alertas de advertencia")
    last_10_alerts: list[AlertHistoryEntry] = Field(default_factory=list, description="Últimas 10 alertas")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    model_config = ConfigDict(from_attributes=True)