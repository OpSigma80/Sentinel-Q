from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from sentinel.infrastructure.base import Base

class ServiceTargetTable(Base):
    """
    Entidad Principal: Definición de Objetos de Vigilancia (Targets).
    Implementa el estándar de integridad referencial para el motor Sentinel-Q.
    """
    __tablename__ = "services"

    # ID como Integer para coincidir con el motor de base de datos y logs
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    url = Column(String, nullable=False)
    check_interval = Column(Integer, default=60)
    is_active = Column(Boolean, default=True, index=True)
    last_check = Column(DateTime(timezone=True), onupdate=func.now())
    status_code = Column(Integer, nullable=True)
    
    # Relaciones con cascada física (PostgreSQL) y lógica (SQLAlchemy)
    metrics = relationship(
        "ServiceMetricsTable", 
        back_populates="target", 
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    incidents = relationship(
        "IncidentHistoryTable", 
        back_populates="target", 
        cascade="all, delete-orphan",
        passive_deletes=True
    )

class ServiceMetricsTable(Base):
    """
    Motor de Observabilidad: Registro de latencia y disponibilidad.
    """
    __tablename__ = "service_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Llave foránea vinculada estrictamente al ID de services
    target_id = Column(
        Integer, 
        ForeignKey("services.id", ondelete="CASCADE"), 
        index=True, 
        nullable=False
    )
    
    status_code = Column(Integer, nullable=False)
    response_time_ms = Column(Float, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    target = relationship("ServiceTargetTable", back_populates="metrics")

class IncidentHistoryTable(Base):
    """
    Persistencia de Eventos Críticos y Alertas.
    """
    __tablename__ = "incident_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Llave foránea vinculada estrictamente al ID de services
    target_id = Column(
        Integer, 
        ForeignKey("services.id", ondelete="CASCADE"), 
        index=True,
        nullable=False
    )
    service_name = Column(String, nullable=False)           
    status_code = Column(Integer, nullable=False)           
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    target = relationship("ServiceTargetTable", back_populates="incidents")