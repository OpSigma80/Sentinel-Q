from sqlalchemy.orm import Session
from sqlalchemy import update, func, and_
from sqlalchemy.sql import text
from sentinel.infrastructure.orm_models import ServiceTargetTable, ServiceMetricsTable, IncidentHistoryTable
from sentinel.domain.models import ServiceTarget
import statistics

class TargetRepository:
    def __init__(self, db: Session):
        self.db = db

    def save_target(self, target: ServiceTarget) -> ServiceTargetTable:
        """Registra o actualiza un objetivo de monitoreo."""
        # Si existe ID, es una actualización; si no, es un INSERT nuevo
        if target.id is not None:
            # Actualización: usa merge para sincronizar registros existentes
            db_target = ServiceTargetTable(
                id=target.id,
                name=target.name,
                url=str(target.url),
                check_interval=target.check_interval,
                is_active=target.is_active
            )
            merged_target = self.db.merge(db_target)
        else:
            # Inserción: no setees ID, deja que autoincrement lo maneje
            db_target = ServiceTargetTable(
                name=target.name,
                url=str(target.url),
                check_interval=target.check_interval,
                is_active=target.is_active
            )
            self.db.add(db_target)
            merged_target = db_target
        
        self.db.commit()
        self.db.refresh(merged_target)  # Recarga para obtener el ID si es nuevo
        return merged_target

    def delete_target(self, target_id: int) -> bool:
        """Elimina un objetivo y su telemetría relacionada."""
        clean_id = int(target_id)
        target = self.db.query(ServiceTargetTable).filter(ServiceTargetTable.id == clean_id).first()
        if target is None:
            return False
        try:
            self.db.delete(target)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def get_all_active(self):
        """Retorna todos los servicios activos para el scheduler."""
        return self.db.query(ServiceTargetTable).filter(ServiceTargetTable.is_active == True).all()

    def get_all(self):
        """Retorna todos los objetivos, activos e inactivos."""
        return self.db.query(ServiceTargetTable).order_by(ServiceTargetTable.id).all()

    def add_metric(self, target_id, status_code: int, response_time_ms: float):
        """
        Registra un latido. Forzamos target_id a int para evitar errores de FK.
        """
        try:
            clean_id = int(target_id)
            
            new_metric = ServiceMetricsTable(
                target_id=clean_id,
                status_code=status_code,
                response_time_ms=response_time_ms
            )
            self.db.add(new_metric)
            
            # Sincronizamos el último status en la tabla principal de forma atómica
            self.db.execute(
                update(ServiceTargetTable)
                .where(ServiceTargetTable.id == clean_id)
                .values(status_code=status_code)
            )
            
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e

    def register_incident(self, target_id, service_name: str, status_code: int):
        """Registra una alerta crítica en el historial."""
        try:
            clean_id = int(target_id)
            new_incident = IncidentHistoryTable(
                target_id=clean_id,
                service_name=service_name,
                status_code=status_code
            )
            self.db.add(new_incident)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e

    def calculate_health_score(self, target_id: int) -> float:
        """
        Calcula salud del servicio (0-100) basado en:
        - Uptime (% de respuestas 2xx-3xx en últimas 100 métricas)
        - Estabilidad de latencia (inverso de coeficiente de variación)
        """
        clean_id = int(target_id)
        
        # Obtener últimas 100 métricas
        metrics = self.db.query(
            ServiceMetricsTable.status_code,
            ServiceMetricsTable.response_time_ms
        ).filter(
            ServiceMetricsTable.target_id == clean_id
        ).order_by(
            ServiceMetricsTable.timestamp.desc()
        ).limit(100).all()
        
        if not metrics:
            return 50.0  # Score neutro si no hay datos
        
        # Calcular uptime (% de respuestas 2xx-3xx)
        successful = sum(1 for m in metrics if 200 <= m.status_code < 400)
        uptime_score = (successful / len(metrics)) * 100
        
        # Calcular estabilidad de latencia
        response_times = [m.response_time_ms for m in metrics if m.response_time_ms]
        
        if len(response_times) < 2:
            stability_score = 50.0
        else:
            mean_latency = statistics.mean(response_times)
            std_dev = statistics.stdev(response_times)
            # Coeficiente de variación: si es bajo, es estable (score alto)
            cv = (std_dev / mean_latency) if mean_latency > 0 else 1
            stability_score = max(0, 100 - (cv * 50))  # Escala entre 0-100
        
        # Health score = promedio ponderado (60% uptime, 40% estabilidad)
        health_score = (uptime_score * 0.6) + (stability_score * 0.4)
        
        return round(health_score, 2)

    def get_target_statistics(self, target_id: int) -> dict:
        """Retorna estadísticas detalladas de un target"""
        clean_id = int(target_id)
        
        metrics = self.db.query(
            ServiceMetricsTable.status_code,
            ServiceMetricsTable.response_time_ms,
            func.count(ServiceMetricsTable.id).label('count')
        ).filter(
            ServiceMetricsTable.target_id == clean_id
        ).all()
        
        if not metrics:
            return {
                "total_checks": 0,
                "uptime_percent": 0,
                "avg_latency_ms": 0,
                "min_latency_ms": 0,
                "max_latency_ms": 0
            }
        
        response_times = []
        status_codes = []
        
        for metric in metrics:
            status_codes.append(metric.status_code)
            if metric.response_time_ms:
                response_times.append(metric.response_time_ms)
        
        successful = sum(1 for s in status_codes if 200 <= s < 400)
        uptime = (successful / len(status_codes)) * 100 if status_codes else 0
        
        return {
            "total_checks": len(status_codes),
            "uptime_percent": round(uptime, 2),
            "avg_latency_ms": round(statistics.mean(response_times), 2) if response_times else 0,
            "min_latency_ms": round(min(response_times), 2) if response_times else 0,
            "max_latency_ms": round(max(response_times), 2) if response_times else 0
        }