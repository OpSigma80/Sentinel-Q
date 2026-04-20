import httpx
import asyncio
import time
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sentinel.application.notifier import AlertNotifier
from sentinel.domain.models import ServiceTarget
from sentinel.infrastructure.database import SessionLocal 
from sentinel.infrastructure.repository import TargetRepository

class SentinelScheduler:
    def __init__(self):
        self._scheduler = AsyncIOScheduler()
        self._notifier = AlertNotifier()
        self._state_tracker = {}

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
        """Suscripción de un objetivo al ciclo de monitoreo."""
        target_id_str = str(target.id) # Normalización a String
        self.remove_target_watch(target_id_str)
        
        if target_id_str not in self._state_tracker:
            self._state_tracker[target_id_str] = "up"

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
        """Remoción segura de un objetivo del planificador."""
        try:
            target_id_str = str(target_id)
            job = self._scheduler.get_job(target_id_str)
            if job:
                self._scheduler.remove_job(target_id_str)
                logger.warning(f"🛑 Target {target_id_str} removido del scheduler")
                # Limpiar estado asociado
                if target_id_str in self._state_tracker:
                    del self._state_tracker[target_id_str]
                return True
            else:
                logger.debug(f"ℹ️  Target {target_id_str} no encontrado en scheduler")
        except Exception as e:
            logger.error(f"Error al remover job {target_id}: {e}")
        return False

    async def _check_target_status(self, target: ServiceTarget):
        """Lógica nuclear: Medición de latencia y persistencia de estado."""
        status_code = 0
        response_time = 0.0
        is_up = False
        
        start_time = time.perf_counter()
        
        # Uso de AsyncClient para máxima eficiencia en E/S
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

        # 💾 REGISTRO DE TELEMETRÍA (Heartbeat)
        with SessionLocal() as db:
            repo = TargetRepository(db)
            try:
                repo.add_metric(
                    target_id=str(target.id), # Aseguramos persistencia coherente
                    status_code=status_code,
                    response_time_ms=response_time
                )
            except Exception as db_err:
                logger.error(f"Error en persistencia para {target.name}: {db_err}")

        # 🚦 MÁQUINA DE ESTADOS Y NOTIFICACIONES
        target_id_str = str(target.id)
        last_known_state = self._state_tracker.get(target_id_str, "up")

        if not is_up and last_known_state == "up":
            logger.warning(f"❌ ALERTA: {target.name} CAÍDO | {status_desc} | {response_time:.2f}ms")
            self._state_tracker[target_id_str] = "down"
            await self._notifier.notify_failure(target.name, str(target.url), status_desc)
            with SessionLocal() as db:
                TargetRepository(db).register_incident(target_id_str, target.name, status_code)

        elif is_up and last_known_state == "down":
            logger.success(f"✅ RECUPERADO: {target.name} en {response_time:.2f}ms")
            self._state_tracker[target_id_str] = "up"
            await self._notifier.notify_success(target.name, str(target.url))
            
        else:
            log_level = "DEBUG" if is_up else "WARNING"
            logger.log(log_level, f"LATIDO: {target.name} | {status_desc} | {response_time:.2f}ms")