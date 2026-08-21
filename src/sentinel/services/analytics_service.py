from datetime import UTC, datetime, timedelta
import statistics

from sqlalchemy.orm import Session

from sentinel.domain.schemas import (
    AlertHistoryEntry,
    AlertHistorySnapshot,
    AlertTrendData,
    MetricsSnapshot,
    ServiceStatusSnapshot,
)
from sentinel.infrastructure.repository import TargetRepository


class AnalyticsService:
    """Business rules for health scoring, trends and operational snapshots."""

    def __init__(self, db: Session) -> None:
        self._repo = TargetRepository(db)

    def calculate_health_score(self, target_id: int) -> float:
        metrics = self._repo.get_recent_metrics_for_target(target_id=target_id, limit=100)
        if not metrics:
            return 50.0

        successful = sum(1 for m in metrics if 200 <= m.status_code < 400)
        uptime_score = (successful / len(metrics)) * 100

        response_times = [m.response_time_ms for m in metrics if m.response_time_ms]
        if len(response_times) < 2:
            stability_score = 50.0
        else:
            mean_latency = statistics.mean(response_times)
            std_dev = statistics.stdev(response_times)
            cv = (std_dev / mean_latency) if mean_latency > 0 else 1
            stability_score = max(0, 100 - (cv * 50))

        health_score = (uptime_score * 0.6) + (stability_score * 0.4)
        return round(health_score, 2)

    def get_target_statistics(self, target_id: int) -> dict:
        metrics = self._repo.get_metrics_for_target(target_id)
        if not metrics:
            return {
                "total_checks": 0,
                "uptime_percent": 0,
                "avg_latency_ms": 0,
                "min_latency_ms": 0,
                "max_latency_ms": 0,
            }

        status_codes = [m.status_code for m in metrics]
        response_times = [m.response_time_ms for m in metrics if m.response_time_ms]
        successful = sum(1 for s in status_codes if 200 <= s < 400)
        uptime = (successful / len(status_codes)) * 100 if status_codes else 0

        return {
            "total_checks": len(status_codes),
            "uptime_percent": round(uptime, 2),
            "avg_latency_ms": round(statistics.mean(response_times), 2) if response_times else 0,
            "min_latency_ms": round(min(response_times), 2) if response_times else 0,
            "max_latency_ms": round(max(response_times), 2) if response_times else 0,
        }

    def get_service_trend(self, target_id: int) -> AlertTrendData:
        target = self._repo.get_target_by_id(target_id)
        if not target:
            raise ValueError(f"Target {target_id} not found")

        cutoff_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
        metrics = self._repo.get_recent_metrics_for_target(target_id, limit=288, since=cutoff_time)

        if not metrics:
            return AlertTrendData(
                service_name=target.name,
                current_status="UNKNOWN",
                uptime_percentage=0,
                avg_response_time_ms=0,
                last_check_at=datetime.now(UTC).replace(tzinfo=None),
            )

        successful = sum(1 for m in metrics if 200 <= m.status_code < 400)
        uptime = (successful / len(metrics)) * 100
        response_times = [m.response_time_ms for m in metrics if m.response_time_ms]
        avg_latency = statistics.mean(response_times) if response_times else 0

        consecutive_failures = 0
        for m in metrics:
            if not (200 <= m.status_code < 400):
                consecutive_failures += 1
            else:
                break

        if consecutive_failures > 0:
            current_status = "CRITICAL" if consecutive_failures > 5 else "WARNING"
        else:
            current_status = "OK"

        return AlertTrendData(
            service_name=target.name,
            current_status=current_status,
            uptime_percentage=round(uptime, 1),
            avg_response_time_ms=round(avg_latency, 2),
            last_check_at=metrics[0].timestamp if metrics else datetime.now(UTC).replace(tzinfo=None),
            failure_count_today=len(metrics) - successful,
            consecutive_failures=consecutive_failures,
        )

    def get_status_snapshot(self) -> ServiceStatusSnapshot:
        targets = self._repo.get_active_services()
        cutoff_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)

        healthy = 0
        degraded = 0
        critical = 0
        services = []

        for target in targets:
            recent_metrics = self._repo.get_recent_metrics_for_target(target.id, limit=100, since=cutoff_time)
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
                    status = "CRITICAL"
                    critical += 1

            services.append(
                {
                    "id": target.id,
                    "name": target.name,
                    "status": status,
                    "uptime": round(uptime, 1),
                }
            )

        return ServiceStatusSnapshot(
            total_services=len(targets),
            healthy=healthy,
            degraded=degraded,
            critical=critical,
            services=services,
        )

    def get_metrics_snapshot(self) -> MetricsSnapshot:
        cutoff_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
        metrics = self._repo.get_recent_metrics_global(cutoff_time)

        total_checks = len(metrics)
        successful = sum(1 for m in metrics if 200 <= m.status_code < 400)
        response_times = [m.response_time_ms for m in metrics if m.response_time_ms is not None]

        success_rate = (successful / total_checks * 100) if total_checks > 0 else 0
        avg_latency = statistics.mean(response_times) if response_times else 0

        if response_times:
            sorted_desc = sorted(response_times, reverse=True)
            p95_idx = max(0, int(len(sorted_desc) * 0.05))
            p95 = sorted_desc[p95_idx] if p95_idx < len(sorted_desc) else max(sorted_desc)
            max_lat = sorted_desc[0]
        else:
            p95 = 0
            max_lat = 0

        return MetricsSnapshot(
            total_checks=total_checks,
            success_rate=round(success_rate, 2),
            avg_response_time_ms=round(avg_latency, 2),
            p95_response_time_ms=round(p95, 2),
            max_response_time_ms=round(max_lat, 2),
        )

    def get_alerts_history(self, hours: int = 24) -> AlertHistorySnapshot:
        cutoff_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)
        incidents = self._repo.get_recent_incidents(cutoff_time, limit=50)

        critical_count = sum(1 for i in incidents if i.status_code >= 500)
        warning_count = sum(1 for i in incidents if 400 <= i.status_code < 500)

        entries = []
        for incident in incidents[:10]:
            event_type = "FAILURE" if incident.status_code >= 400 else "UNKNOWN"
            severity = "CRITICAL" if incident.status_code >= 500 else "WARNING"
            entries.append(
                AlertHistoryEntry(
                    service_name=incident.service_name,
                    event_type=event_type,
                    severity=severity,
                    message=f"HTTP {incident.status_code}",
                    timestamp=incident.timestamp,
                )
            )

        return AlertHistorySnapshot(
            total_alerts_today=len(incidents),
            critical_count=critical_count,
            warning_count=warning_count,
            last_10_alerts=entries,
        )
