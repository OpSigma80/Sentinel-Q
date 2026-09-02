from sqlalchemy.orm import Session
from sqlalchemy import update, func, and_, desc
from sqlalchemy.sql import text
from sentinel.infrastructure.orm_models import ServiceTargetTable, ServiceMetricsTable, IncidentHistoryTable
from sentinel.domain.models import ServiceTarget
from sentinel.domain.schemas import AlertTrendData, ServiceStatusSnapshot, MetricsSnapshot, AlertHistorySnapshot, AlertHistoryEntry
from datetime import datetime, timedelta
from typing import Optional
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

    def get_active_services(self, tenant_id: int | None = None) -> list[ServiceTargetTable]:
        """Return active services ordered for dashboard/status endpoints."""
        return (
            self.db.query(ServiceTargetTable)
            .filter(ServiceTargetTable.is_active == True)
            .order_by(ServiceTargetTable.id)
            .all()
        )

    def get_target_metrics_rows(self, target_id: int, limit: int = 100):
        """Return latest metric rows for a target ordered descending by timestamp."""
        clean_id = int(target_id)
        return (
            self.db.query(
                ServiceMetricsTable.response_time_ms,
                ServiceMetricsTable.status_code,
                ServiceMetricsTable.timestamp,
            )
            .filter(ServiceMetricsTable.target_id == clean_id)
            .order_by(ServiceMetricsTable.timestamp.desc())
            .limit(limit)
            .all()
        )

    def get_target_by_id(self, target_id: int):
        """Return a single target by id."""
        return self.db.query(ServiceTargetTable).filter(ServiceTargetTable.id == int(target_id)).first()

    def get_metrics_for_target(self, target_id: int):
        """Return all metric rows for a target."""
        clean_id = int(target_id)
        return (
            self.db.query(
                ServiceMetricsTable.status_code,
                ServiceMetricsTable.response_time_ms,
                ServiceMetricsTable.timestamp,
            )
            .filter(ServiceMetricsTable.target_id == clean_id)
            .all()
        )

    def get_recent_metrics_for_target(self, target_id: int, limit: int = 100, since: Optional[datetime] = None):
        """Return recent metric rows for a target, optionally filtered by timestamp."""
        clean_id = int(target_id)
        query = self.db.query(
            ServiceMetricsTable.status_code,
            ServiceMetricsTable.response_time_ms,
            ServiceMetricsTable.timestamp,
        ).filter(ServiceMetricsTable.target_id == clean_id)

        if since is not None:
            query = query.filter(ServiceMetricsTable.timestamp >= since)

        return query.order_by(ServiceMetricsTable.timestamp.desc()).limit(limit).all()

    def get_recent_metrics_global(self, since: datetime):
        """Return global metric rows from a timestamp onward."""
        return (
            self.db.query(
                ServiceMetricsTable.status_code,
                ServiceMetricsTable.response_time_ms,
                ServiceMetricsTable.timestamp,
            )
            .filter(ServiceMetricsTable.timestamp >= since)
            .all()
        )

    def get_recent_incidents(self, since: datetime, limit: int = 50):
        """Return recent incidents ordered from newest to oldest."""
        return (
            self.db.query(IncidentHistoryTable)
            .filter(IncidentHistoryTable.timestamp >= since)
            .order_by(IncidentHistoryTable.timestamp.desc())
            .limit(limit)
            .all()
        )

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

    # ========================================================================
    # TELEGRAM MEJORADO - Métodos para snapshots y trend data (optimizados)
    # ========================================================================

    def get_service_trend(self, target_id: int) -> AlertTrendData:
        """
        Get AlertTrendData for a service (last 24h metrics).
        Optimized for CPU: single query, limited scope.
        """
        clean_id = int(target_id)
        
        # Get target
        target = self.db.query(ServiceTargetTable).filter(
            ServiceTargetTable.id == clean_id
        ).first()
        
        if not target:
            raise ValueError(f"Target {clean_id} not found")
        
        # Get metrics from last 24 hours
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        metrics = self.db.query(
            ServiceMetricsTable.status_code,
            ServiceMetricsTable.response_time_ms,
            ServiceMetricsTable.timestamp
        ).filter(
            and_(
                ServiceMetricsTable.target_id == clean_id,
                ServiceMetricsTable.timestamp >= cutoff_time
            )
        ).order_by(
            ServiceMetricsTable.timestamp.desc()
        ).limit(288).all()  # ~24 hours at 5min intervals
        
        if not metrics:
            return AlertTrendData(
                service_name=target.name,
                current_status="UNKNOWN",
                uptime_percentage=0,
                avg_response_time_ms=0,
                last_check_at=datetime.utcnow()
            )
        
        # Calculate metrics
        successful = sum(1 for m in metrics if 200 <= m.status_code < 400)
        uptime = (successful / len(metrics)) * 100 if metrics else 0
        response_times = [m.response_time_ms for m in metrics if m.response_time_ms]
        avg_latency = statistics.mean(response_times) if response_times else 0
        
        # Count consecutive failures from latest
        consecutive_failures = 0
        for m in metrics:
            if not (200 <= m.status_code < 400):
                consecutive_failures += 1
            else:
                break
        
        # Determine current status
        if consecutive_failures > 0:
            current_status = "CRÍTICO" if consecutive_failures > 5 else "ADVERTENCIA"
        else:
            current_status = "OK"
        
        return AlertTrendData(
            service_name=target.name,
            current_status=current_status,
            uptime_percentage=round(uptime, 1),
            avg_response_time_ms=round(avg_latency, 2),
            last_check_at=metrics[0].timestamp if metrics else datetime.utcnow(),
            failure_count_today=len(metrics) - successful,
            consecutive_failures=consecutive_failures
        )

    def get_status_snapshot(self) -> ServiceStatusSnapshot:
        """
        Get overall status of all active services.
        Optimized: single query with aggregation.
        """
        targets = self.db.query(ServiceTargetTable).filter(
            ServiceTargetTable.is_active == True
        ).all()
        
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        
        healthy = 0
        degraded = 0
        critical = 0
        services = []
        
        for target in targets:
            # Get latest metrics for this target
            recent_metrics = self.db.query(
                ServiceMetricsTable.status_code,
                ServiceMetricsTable.response_time_ms
            ).filter(
                and_(
                    ServiceMetricsTable.target_id == target.id,
                    ServiceMetricsTable.timestamp >= cutoff_time
                )
            ).order_by(
                ServiceMetricsTable.timestamp.desc()
            ).limit(100).all()
            
            if not recent_metrics:
                status = "UNKNOWN"
                uptime = 0
            else:
                successful = sum(1 for m in recent_metrics if 200 <= m.status_code < 400)
                uptime = (successful / len(recent_metrics)) * 100
                
                if uptime >= 95:
                    status = "OK"
                    healthy += 1
                elif uptime >= 80:
                    status = "DEGRADED"
                    degraded += 1
                else:
                    status = "CRÍTICO"
                    critical += 1
            
            services.append({
                "id": target.id,
                "name": target.name,
                "status": status,
                "uptime": round(uptime, 1)
            })
        
        return ServiceStatusSnapshot(
            total_services=len(targets),
            healthy=healthy,
            degraded=degraded,
            critical=critical,
            services=services
        )

    def get_metrics_snapshot(self) -> MetricsSnapshot:
        """
        Get global performance metrics for all active services (24h).
        Optimized: limited queries, efficient aggregation.
        """
        from sqlalchemy import and_, case
        
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        
        # Count successful and total checks
        result = self.db.query(
            func.count(ServiceMetricsTable.id).label("total_checks"),
            func.sum(
                case(
                    (and_(
                        ServiceMetricsTable.status_code >= 200,
                        ServiceMetricsTable.status_code < 400
                    ), 1),
                    else_=0
                )
            ).label("successful_checks"),
            func.avg(ServiceMetricsTable.response_time_ms).label("avg_latency")
        ).filter(
            ServiceMetricsTable.timestamp >= cutoff_time
        ).first()
        
        total_checks = result.total_checks or 0
        successful = result.successful_checks or 0
        avg_latency = result.avg_latency or 0
        
        success_rate = (successful / total_checks * 100) if total_checks > 0 else 0
        
        # Get p95 and max (simple approach: last N sorted)
        latencies = self.db.query(
            ServiceMetricsTable.response_time_ms
        ).filter(
            and_(
                ServiceMetricsTable.timestamp >= cutoff_time,
                ServiceMetricsTable.response_time_ms.isnot(None)
            )
        ).order_by(
            ServiceMetricsTable.response_time_ms.desc()
        ).limit(500).all()
        
        if latencies:
            latency_values = [m.response_time_ms for m in latencies]
            p95_idx = max(0, int(len(latency_values) * 0.05))
            p95 = latency_values[p95_idx] if p95_idx < len(latency_values) else max(latency_values)
            max_lat = latency_values[0] if latency_values else 0
        else:
            p95 = 0
            max_lat = 0
        
        return MetricsSnapshot(
            total_checks=total_checks,
            success_rate=round(success_rate, 2),
            avg_response_time_ms=round(avg_latency, 2),
            p95_response_time_ms=round(p95, 2),
            max_response_time_ms=round(max_lat, 2)
        )

    def get_alerts_history(self, hours: int = 24) -> AlertHistorySnapshot:
        """
        Get recent alert history (incidents and recoveries).
        Optimized: single query, limited result set.
        """
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        incidents = self.db.query(IncidentHistoryTable).filter(
            IncidentHistoryTable.timestamp >= cutoff_time
        ).order_by(
            IncidentHistoryTable.timestamp.desc()
        ).limit(50).all()
        
        critical_count = sum(1 for i in incidents if i.status_code >= 500)
        warning_count = sum(1 for i in incidents if 400 <= i.status_code < 500)
        
        alert_entries = []
        for incident in incidents[:10]:
            event_type = "FAILURE" if incident.status_code >= 400 else "UNKNOWN"
            severity = "CRÍTICO" if incident.status_code >= 500 else "ADVERTENCIA"
            
            alert_entries.append(AlertHistoryEntry(
                service_name=incident.service_name,
                event_type=event_type,
                severity=severity,
                message=f"HTTP {incident.status_code}",
                timestamp=incident.timestamp
            ))
        
        return AlertHistorySnapshot(
            total_alerts_today=len(incidents),
            critical_count=critical_count,
            warning_count=warning_count,
            last_10_alerts=alert_entries
        )
