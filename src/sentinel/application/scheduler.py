import httpx
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sentinel.application.notifier import AlertNotifier
from sentinel.domain.models import ServiceTarget
from sentinel.infrastructure.database import SessionLocal 
from sentinel.infrastructure.repository import TargetRepository
from sentinel.config import settings


@dataclass
class TargetAlertState:
    current_state: str = "up"
    failure_streak: int = 0
    recovery_streak: int = 0
    down_since: Optional[datetime] = None
    pending_failure_since: Optional[datetime] = None
    last_alert_at: Optional[datetime] = None
    last_failure_alert_at: Optional[datetime] = None
    incident_alert_sent: bool = False

class SentinelScheduler:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._notifier = AlertNotifier()
        self._state_tracker: dict[str, TargetAlertState] = {}

    def start(self):
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("🚀 Motor de Vigilancia activo con Telemetría de Alta Precisión.")

    def get_running_target_ids(self):
        """
        REPORTE DE ESTADO: Devuelve una lista de los IDs que están 
        siendo vigilados actualmente. Vital para la sincronización dinámica.
        """
        return [str(job.id) for job in self._scheduler.get_jobs()]

    def add_target_watch(self, target: ServiceTarget):
        """Subscribe a target to the monitoring loop."""
        target_id_str = str(target.id)
        self.remove_target_watch(target_id_str)
        
        if target_id_str not in self._state_tracker:
            self._state_tracker[target_id_str] = TargetAlertState()

        self._scheduler.add_job(
            self._check_target_status,
            'interval',
            seconds=target.check_interval,
            id=target_id_str,
            args=[target],
            replace_existing=True
        )
        logger.info(f"📍 Vigilancia programada: {target.name} (frecuencia: {target.check_interval}s)")

    def remove_target_watch(self, target_id: str):
        """Safely remove a target from the scheduler."""
        try:
            target_id_str = str(target_id)
            job = self._scheduler.get_job(target_id_str)
            if job:
                self._scheduler.remove_job(target_id_str)
                logger.warning(f"🛑 Target {target_id_str} removido del scheduler")
                if target_id_str in self._state_tracker:
                    del self._state_tracker[target_id_str]
                return True
            else:
                logger.debug(f"ℹ️  Target {target_id_str} no encontrado en scheduler")
        except Exception as e:
            logger.error(f"Error al remover job {target_id}: {e}")
        return False

    def _get_target_state(self, target_id: str) -> TargetAlertState:
        """Return alert state for a target, initializing it when needed."""
        if target_id not in self._state_tracker:
            self._state_tracker[target_id] = TargetAlertState()
        return self._state_tracker[target_id]

    def _should_send_alert(self, state: TargetAlertState, checked_at: datetime) -> bool:
        """Apply per-target cooldown between incident notifications."""
        if state.last_alert_at is None:
            return True

        elapsed_seconds = (checked_at - state.last_alert_at).total_seconds()
        return elapsed_seconds >= settings.ALERT_COOLDOWN_SECONDS

    def _classify_severity(self, status_code: int, status_desc: str, failure_streak: int) -> str:
        """Classify an incident severity for Telegram context."""
        if status_code == 0 or "error" in status_desc.lower() or failure_streak >= 5:
            return "CRITICAL"
        if status_code >= 500:
            return "HIGH"
        return "MEDIUM"

    def _get_severity_throttle_seconds(self, severity: str) -> int:
        """Return per-severity throttle to reduce alert fatigue on noisy services."""
        if severity == "CRITICAL":
            return settings.ALERT_CRITICAL_THROTTLE_SECONDS
        if severity == "HIGH":
            return settings.ALERT_WARNING_THROTTLE_SECONDS
        return settings.ALERT_MEDIUM_THROTTLE_SECONDS

    def _should_send_failure_alert(self, state: TargetAlertState, severity: str, checked_at: datetime) -> bool:
        """Apply severity-aware throttle to failure alerts."""
        if state.last_failure_alert_at is None:
            return True

        throttle_seconds = self._get_severity_throttle_seconds(severity)
        elapsed_seconds = (checked_at - state.last_failure_alert_at).total_seconds()
        return elapsed_seconds >= throttle_seconds

    async def _handle_target_result(
        self,
        target: ServiceTarget,
        *,
        status_code: int,
        response_time: float,
        is_up: bool,
        status_desc: str,
        checked_at: Optional[datetime] = None,
    ) -> None:
        """Persist telemetry and evaluate alert transitions for a target."""
        checked_at = checked_at or datetime.now()

        with SessionLocal() as db:
            repo = TargetRepository(db)
            try:
                repo.add_metric(
                    target_id=str(target.id),
                    status_code=status_code,
                    response_time_ms=response_time
                )
            except Exception as db_err:
                logger.error(f"Error en persistencia para {target.name}: {db_err}")

        target_id_str = str(target.id)
        state = self._get_target_state(target_id_str)

        if not is_up:
            state.failure_streak += 1
            state.recovery_streak = 0
            if state.pending_failure_since is None:
                state.pending_failure_since = checked_at

            stability_seconds = (checked_at - state.pending_failure_since).total_seconds()

            if state.current_state == "up" and state.failure_streak >= settings.ALERT_FAILURE_THRESHOLD:
                if stability_seconds < settings.ALERT_STABILITY_WINDOW_SECONDS:
                    logger.warning(
                        f"FLAP GUARD: {target.name} failure threshold met but waiting stability window "
                        f"{stability_seconds:.0f}/{settings.ALERT_STABILITY_WINDOW_SECONDS}s"
                    )
                    return

                if state.down_since is None:
                    state.down_since = state.pending_failure_since

                severity = self._classify_severity(status_code, status_desc, state.failure_streak)
                state.current_state = "down"
                state.incident_alert_sent = (
                    self._should_send_alert(state, checked_at)
                    and self._should_send_failure_alert(state, severity, checked_at)
                )

                logger.warning(
                    f"❌ ALERT: {target.name} DOWN | {status_desc} | {response_time:.2f}ms | "
                    f"streak={state.failure_streak} | severity={severity}"
                )

                if state.incident_alert_sent:
                    await self._notifier.notify_failure(
                        target.name,
                        str(target.url),
                        status_desc,
                        severity=severity,
                        response_time_ms=response_time,
                        failure_streak=state.failure_streak,
                        incident_started_at=state.down_since,
                    )
                    state.last_alert_at = checked_at
                    state.last_failure_alert_at = checked_at
                else:
                    logger.warning(
                        f"Telegram failure alert suppressed by cooldown for {target.name}"
                    )

                with SessionLocal() as db:
                    TargetRepository(db).register_incident(target_id_str, target.name, status_code)
            else:
                logger.warning(
                    f"HEARTBEAT DEGRADED: {target.name} | {status_desc} | {response_time:.2f}ms | "
                    f"failure_streak={state.failure_streak}"
                )
            return

        state.failure_streak = 0
        state.pending_failure_since = None

        if state.current_state == "down":
            state.recovery_streak += 1
            if state.recovery_streak < settings.ALERT_RECOVERY_THRESHOLD:
                logger.info(
                    f"RECOVERY PENDING: {target.name} | success_streak={state.recovery_streak}/"
                    f"{settings.ALERT_RECOVERY_THRESHOLD}"
                )
                return

            downtime_seconds = None
            if state.down_since is not None:
                downtime_seconds = (checked_at - state.down_since).total_seconds()

            logger.success(
                f"✅ RECOVERED: {target.name} | {response_time:.2f}ms | downtime={downtime_seconds or 0:.0f}s"
            )

            if state.incident_alert_sent:
                await self._notifier.notify_success(
                    target.name,
                    str(target.url),
                    response_time_ms=response_time,
                    recovery_streak=state.recovery_streak,
                    downtime_seconds=downtime_seconds,
                )
                state.last_alert_at = checked_at
            else:
                logger.info(f"Recovery alert skipped for {target.name} because incident alert was suppressed")

            state.current_state = "up"
            state.recovery_streak = 0
            state.down_since = None
            state.incident_alert_sent = False
            return

        state.recovery_streak = 0
        state.down_since = None
        logger.debug(f"HEARTBEAT OK: {target.name} | {status_desc} | {response_time:.2f}ms")

    async def _check_target_status(self, target: ServiceTarget):
        """Perform an HTTP health check and feed the alert state machine."""
        status_code = 0
        response_time = 0.0
        is_up = False
        
        start_time = time.perf_counter()
        
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            try:
                response = await client.get(str(target.url))
                response_time = (time.perf_counter() - start_time) * 1000 
                status_code = response.status_code
                is_up = 200 <= status_code < 400
                status_desc = f"HTTP {status_code}"
                
            except Exception as e:
                response_time = (time.perf_counter() - start_time) * 1000
                is_up = False
                status_code = 0 
                status_desc = f"Error: {type(e).__name__}"

        await self._handle_target_result(
            target,
            status_code=status_code,
            response_time=response_time,
            is_up=is_up,
            status_desc=status_desc,
        )