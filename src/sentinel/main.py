from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from loguru import logger
import asyncio
from pathlib import Path
import os
import signal
import sys

from sentinel.config import settings
from sentinel.application.scheduler import SentinelScheduler
from sentinel.application.telegram_poller import TelegramBotPoller
from sentinel.domain.models import ServiceTarget as DomainServiceTarget
from sentinel.domain.schemas import ServiceTarget as ServiceTargetSchema, ServiceTargetCreate, HealthCheckResponse, ServiceStatusSnapshot, MetricsSnapshot, AlertHistorySnapshot
from sentinel.infrastructure.database import SessionLocal, engine, get_db, Base
from sentinel.infrastructure.repository import TargetRepository
from sentinel.infrastructure.auth import verify_jwt_token
from sentinel.services import TargetService, TelegramQueryService, MonitoringQueryService, SystemHealthService
from datetime import datetime

# --- CONFIGURAR LOGGING PERSISTENTE ---
from loguru import logger as loguru_logger

log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

loguru_logger.remove()  # Remover handler por defecto
loguru_logger.add(
    sink=str(log_dir / "sentinel.log"),
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    rotation="00:00",  # Rotar diariamente
    retention="10 days",  # Mantener últimos 10 días
    level="INFO"
)
loguru_logger.add(
    sink=sys.stderr,
    format="<level>{time:HH:mm:ss}</level> | <level>{level: <8}</level> | <level>{message}</level>",
    level="DEBUG"
)

# NO ejecutar create_all aquí - esperar al startup event
# Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manejador de ciclo de vida del sistema (Sustituye a startup/shutdown events)"""
    logger.info(f"--- INICIANDO {settings.APP_NAME} ---")
    
    # 1. Validación de base de datos
    max_retries = 5
    retry_delay = 2
    for attempt in range(1, max_retries + 1):
        try:
            Base.metadata.create_all(bind=engine)
            logger.success("✅ Esquemas de base de datos validados.")
            break
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"⚠️ Intento {attempt}/{max_retries} fallido. Reintentando en {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.error("❌ Fallo crítico al conectar con la base de datos.")

    # 2. Iniciar procesos en segundo plano
    scheduler.start()
    sync_task = asyncio.create_task(sync_database_targets())
    bot_task = asyncio.create_task(bot_poller.run())
    
    logger.success("🚀 SISTEMA OPERACIONAL: Motor, sincronizador y bot Telegram en línea.")
    
    yield
    
    # --- SHUTDOWN LOGIC ---
    logger.warning("Stopping Sentinel-Q services...")
    sync_task.cancel()
    bot_task.cancel()
    bot_poller.stop()
    scheduler.shutdown()
    engine.dispose()
    logger.success("✅ Shutdown completado con éxito.")

app = FastAPI(
    title=settings.APP_NAME, 
    version=settings.APP_VERSION,
    lifespan=lifespan,
    description="Professional-grade service monitoring engine with intelligent health scoring.",
    contact={
        "name": "Sentinel-Q Support",
        "email": "support@realsystembuilders.com",
    }
)

# --- SECURITY HEADERS MIDDLEWARE ---
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' fonts.googleapis.com"
    return response

# --- CONFIGURACIÓN DE CORS RESTRINGIDA ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8000,http://localhost:8501").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

scheduler = SentinelScheduler()
bot_poller = TelegramBotPoller()
_graceful_shutdown = False

async def sync_database_targets():
    logger.info("📡 Iniciando ciclo de sincronización de objetivos...")
    while True:
        db = SessionLocal()
        try:
            repo = TargetRepository(db)
            result = repo.get_all_active()
            currently_watching = scheduler.get_running_target_ids()
            
            # Lista de IDs activos en la BD
            active_ids = {str(row.id) for row in result}
            
            # Remover targets que ya no están en la BD
            for watching_id in currently_watching:
                if watching_id not in active_ids:
                    scheduler.remove_target_watch(watching_id)
                    logger.warning(f"⏹️  VIGILANCIA DETENIDA: ID {watching_id} (no encontrado en BD)")
            
            # Agregar targets nuevos que no están siendo vigilados
            for row in result:
                target_id_str = str(row.id)
                if target_id_str not in currently_watching:
                    target_obj = DomainServiceTarget(
                        id=row.id,
                        name=row.name,
                        url=row.url,
                        check_interval=row.check_interval if row.check_interval else 60,
                        is_active=True
                    )
                    scheduler.add_target_watch(target_obj)
                    logger.success(f"✨ VIGILANCIA ACTIVADA: {row.name} (@{row.url})")
            
            if len(result) > 0:
                logger.debug(f"📊 Estado: {len(result)} objetivos en base de datos.")
        except Exception as e:
            logger.error(f"❌ Error crítico en sincronización: {e}")
        finally:
            db.close()
        await asyncio.sleep(10)  # Reducido a 10 segundos para sincronización más rápida

# Montar estáticos (fuera del lifespan para que FastAPI los reconozca al iniciar)
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

@app.get("/")
async def dashboard():
    """Sirve el dashboard HTML principal"""
    static_dir = Path(__file__).parent / "static" / "index.html"
    
    if not static_dir.exists():
        logger.error(f"❌ index.html no encontrado en {static_dir}")
        raise HTTPException(status_code=404, detail="Dashboard file not found")
    
    return FileResponse(str(static_dir), media_type="text/html")

@app.get("/targets", response_model=list[ServiceTargetSchema])
async def list_targets(db: Session = Depends(get_db)):
    """Retorna todos los objetivos registrados con su health score."""
    try:
        service = TargetService(db, scheduler)
        return service.list_targets_with_health()
    except Exception as e:
        logger.error(f"❌ Error en GET /targets: {e}")
        raise HTTPException(status_code=500, detail="Error al listar targets")

@app.post("/targets", response_model=ServiceTargetSchema, status_code=201)
async def create_target(target_in: ServiceTargetCreate, db: Session = Depends(get_db)):
    """Crea un nuevo target y lo arma en el scheduler."""
    try:
        logger.info(f"📝 POST /targets: Creando target '{target_in.name}' ({target_in.url})")
        service = TargetService(db, scheduler)
        saved = service.create_target(target_in)

        logger.success(f"✨ Target creado: ID={saved.id}, Nombre={saved.name}")
        logger.success(f"✅ Target {saved.id} agregado al scheduler")

        return saved
    except Exception as e:
        logger.error(f"❌ Error en POST /targets: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Error al crear target")

@app.delete("/targets/{target_id}", status_code=204)
async def delete_target(target_id: int, db: Session = Depends(get_db)):
    """Elimina un target definitivamente de la base de datos y del scheduler."""
    try:
        logger.info(f"🗑️  DELETE /targets/{target_id}: Eliminando target...")
        service = TargetService(db, scheduler)
        if not service.delete_target(target_id):
            logger.warning(f"⚠️  Target {target_id} no encontrado en BD")
            raise HTTPException(status_code=404, detail="Target no encontrado")
        
        logger.success(f"✅ Target {target_id} eliminado completamente (BD + Scheduler)")
        return Response(status_code=204)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error en DELETE /targets/{target_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Error al eliminar target")
@app.delete("/stop/{target_id}", status_code=204)
async def stop_target(target_id: int, db: Session = Depends(get_db), _: object = Depends(verify_jwt_token)):
    """Detiene y elimina un target usando el endpoint de la UI."""

    try:
        logger.info(f"🗑️  DELETE /stop/{target_id}: Eliminando target...")
        service = TargetService(db, scheduler)
        if not service.delete_target(target_id):
            logger.warning(f"⚠️  Target {target_id} no encontrado en BD")
            raise HTTPException(status_code=404, detail="Target no encontrado")
        
        logger.success(f"✅ Target {target_id} eliminado completamente (BD + Scheduler)")
        return Response(status_code=204)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error en DELETE /stop/{target_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Error al detener target")

@app.get("/status")
async def get_status(db: Session = Depends(get_db)):
    """Lists all active services with current status and health score"""
    try:
        service = MonitoringQueryService(db)
        return service.get_active_status_with_health()
    except Exception as e:
        logger.error(f"❌ Error in GET /status: {e}")
        raise HTTPException(status_code=500, detail="Error querying service status")

@app.get("/metrics/{target_id}")
async def get_metrics(target_id: int, db: Session = Depends(get_db)):
    """Retorna las últimas 100 métricas para el gráfico de Chart.js"""
    try:
        service = MonitoringQueryService(db)
        return service.get_target_metrics(target_id=target_id)
    except Exception as e:
        logger.error(f"Error en /metrics/{target_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener métricas")

@app.get("/health", response_model=HealthCheckResponse)
async def health_check(db: Session = Depends(get_db)):
    """Endpoint de health check: verifica BD y scheduler"""
    try:
        service = SystemHealthService(db, scheduler)
        return service.get_health()
    except Exception as e:
        logger.error(f"Error en /health: {e}")
        raise HTTPException(status_code=503, detail="Health check failed")

@app.get("/stats/{target_id}")
async def get_target_stats(target_id: int, db: Session = Depends(get_db)):
    """Retorna estadísticas detalladas y health score de un target"""
    try:
        service = MonitoringQueryService(db)
        return service.get_target_stats_with_health(target_id)
    except Exception as e:
        logger.error(f"Error en /stats/{target_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener estadísticas")

# ========================================================================
# TELEGRAM MEJORADO - Endpoints para comandos interactivos
# ========================================================================

@app.get("/telegram/status", response_model=ServiceStatusSnapshot)
async def telegram_status(db: Session = Depends(get_db)):
    """API endpoint for /status Telegram command - returns status snapshot"""
    try:
        service = TelegramQueryService(db)
        snapshot = service.get_status_snapshot()
        logger.info(f"📊 /telegram/status: {snapshot.healthy}✅ {snapshot.degraded}🟡 {snapshot.critical}🔴")
        return snapshot
    except Exception as e:
        logger.error(f"Error en /telegram/status: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener status snapshot")

@app.get("/telegram/metrics", response_model=MetricsSnapshot)
async def telegram_metrics(db: Session = Depends(get_db)):
    """API endpoint for /metrics Telegram command - returns global performance metrics"""
    try:
        service = TelegramQueryService(db)
        snapshot = service.get_metrics_snapshot()
        logger.info(f"📈 /telegram/metrics: {snapshot.success_rate:.1f}% success rate, {snapshot.avg_response_time_ms:.0f}ms avg")
        return snapshot
    except Exception as e:
        logger.error(f"Error en /telegram/metrics: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener metrics snapshot")

@app.get("/telegram/alerts", response_model=AlertHistorySnapshot)
async def telegram_alerts(hours: int = 24, db: Session = Depends(get_db)):
    """API endpoint for /alerts Telegram command - returns recent alert history"""
    try:
        service = TelegramQueryService(db)
        snapshot = service.get_alerts_history(hours=hours)
        logger.info(f"🚨 /telegram/alerts: {snapshot.total_alerts_today} alerts, {snapshot.critical_count} critical")
        return snapshot
    except Exception as e:
        logger.error(f"Error en /telegram/alerts: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener alerts history")

# --- GRACEFUL SHUTDOWN HANDLERS ---
def handle_signal(signum, frame):
    """Manejador de señal para graceful shutdown"""
    global _graceful_shutdown
    _graceful_shutdown = True
    logger.warning(f"⚠️  Señal {signum} recibida. Iniciando shutdown gracioso...")
    
    try:
        bot_poller.stop()
        logger.success("✅ Bot Telegram detenido")
    except Exception as e:
        logger.error(f"Error al detener bot: {e}")

    try:
        scheduler.shutdown()
        logger.success("✅ Scheduler cerrado correctamente")
    except Exception as e:
        logger.error(f"Error al cerrar scheduler: {e}")
    
    try:
        engine.dispose()
        logger.success("✅ Pool de conexiones liberado")
    except Exception as e:
        logger.error(f"Error al liberar conexiones: {e}")
    
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)