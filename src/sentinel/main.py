from fastapi import FastAPI, Depends, Header, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger
import asyncio
from pathlib import Path
import os
import signal
import sys

from sentinel.config import settings
from sentinel.application.scheduler import SentinelScheduler
from sentinel.domain.models import ServiceTarget as DomainServiceTarget
from sentinel.domain.schemas import ServiceTarget as ServiceTargetSchema, ServiceTargetCreate, HealthCheckResponse
from sentinel.infrastructure.database import SessionLocal, engine, get_db, Base
from sentinel.infrastructure.repository import TargetRepository
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

app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

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
_graceful_shutdown = False

async def sync_database_targets():
    logger.info("📡 Iniciando ciclo de sincronización de objetivos...")
    while True:
        db = SessionLocal()
        try:
            # CORRECCIÓN: Usamos la tabla 'services' para ser consistentes con el ORM
            query = text("SELECT id, name, url, check_interval FROM services WHERE is_active = True")
            result = db.execute(query).fetchall()
            currently_watching = scheduler.get_running_target_ids()
            
            # Lista de IDs activos en la BD
            active_ids = {str(int(row[0])) for row in result}
            
            # Remover targets que ya no están en la BD
            for watching_id in currently_watching:
                if watching_id not in active_ids:
                    scheduler.remove_target_watch(watching_id)
                    logger.warning(f"⏹️  VIGILANCIA DETENIDA: ID {watching_id} (no encontrado en BD)")
            
            # Agregar targets nuevos que no están siendo vigilados
            for row in result:
                target_id = int(row[0])  # Convertir a int directamente
                target_id_str = str(target_id)  # Para comparación en scheduler
                if target_id_str not in currently_watching:
                    target_obj = DomainServiceTarget(
                        id=target_id,  # Pasar int, no string
                        name=row[1],
                        url=row[2],
                        check_interval=row[3] if row[3] else 60,
                        is_active=True
                    )
                    scheduler.add_target_watch(target_obj)
                    logger.success(f"✨ VIGILANCIA ACTIVADA: {row[1]} (@{row[2]})")
            
            if len(result) > 0:
                logger.debug(f"📊 Estado: {len(result)} objetivos en base de datos.")
        except Exception as e:
            logger.error(f"❌ Error crítico en sincronización: {e}")
        finally:
            db.close()
        await asyncio.sleep(10)  # Reducido a 10 segundos para sincronización más rápida

@app.on_event("startup")
async def startup_event():
    logger.info(f"--- INICIANDO {settings.APP_NAME} ---")
    
    # 1. Crear todas las tablas con reintentos (PostgreSQL puede necesitar tiempo)
    max_retries = 5
    retry_delay = 2
    
    for attempt in range(1, max_retries + 1):
        try:
            Base.metadata.create_all(bind=engine)
            logger.success("✅ Esquemas de base de datos validados/creados.")
            break
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"⚠️ Intento {attempt}/{max_retries} fallido. Reintenando en {retry_delay}s...")
                logger.debug(f"Error: {type(e).__name__}: {str(e)[:150]}")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Backoff exponencial
            else:
                logger.error(f"❌ No se pudo conectar a BD después de {max_retries} intentos. Continuando sin esquemas.")
                logger.info("ℹ️  Las tablas se crearán automáticamente en el primer acceso.")
    
    # 2. Montar directorio estático
    static_dir = Path(__file__).parent / "static"
    if not static_dir.exists():
        logger.error(f"❌ CRÍTICO: Directorio static no encontrado en {static_dir}")
        raise RuntimeError(f"Static files directory not found: {static_dir}")
    
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    # 3. Iniciar scheduler
    scheduler.start()
    asyncio.create_task(sync_database_targets())
    
    logger.success("🚀 SISTEMA OPERACIONAL: Motor y sincronizador en línea.")

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
        repo = TargetRepository(db)
        targets = repo.get_all()
        
        # Enriquecer cada target con health score
        enriched_targets = []
        for target in targets:
            target_dict = {
                'id': target.id,
                'name': target.name,
                'url': target.url,
                'check_interval': target.check_interval,
                'is_active': target.is_active,
                'last_check': target.last_check,
                'status_code': target.status_code,
                'health_score': repo.calculate_health_score(target.id)
            }
            enriched_targets.append(target_dict)
        
        return enriched_targets
    except Exception as e:
        logger.error(f"❌ Error en GET /targets: {e}")
        raise HTTPException(status_code=500, detail="Error al listar targets")

@app.post("/targets", response_model=ServiceTargetSchema, status_code=201)
async def create_target(target_in: ServiceTargetCreate, db: Session = Depends(get_db)):
    """Crea un nuevo target y lo arma en el scheduler."""
    try:
        logger.info(f"📝 POST /targets: Creando target '{target_in.name}' ({target_in.url})")
        repo = TargetRepository(db)
        saved = repo.save_target(DomainServiceTarget(
            name=target_in.name,
            url=target_in.url,
            check_interval=target_in.check_interval,
            is_active=target_in.is_active
        ))

        logger.success(f"✨ Target creado: ID={saved.id}, Nombre={saved.name}")
        scheduler.add_target_watch(DomainServiceTarget(
            id=saved.id,
            name=saved.name,
            url=saved.url,
            check_interval=saved.check_interval,
            is_active=saved.is_active
        ))
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
        target_id_str = str(target_id)
        
        # Paso 1: Remover del scheduler PRIMERO
        removed_from_scheduler = scheduler.remove_target_watch(target_id_str)
        if removed_from_scheduler:
            logger.success(f"⏹️  Target {target_id} removido del scheduler")
        else:
            logger.warning(f"⚠️  Target {target_id} no estaba en el scheduler")
        
        # Paso 2: Eliminar de la BD
        repo = TargetRepository(db)
        if not repo.delete_target(target_id):
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
async def stop_target(target_id: int, db: Session = Depends(get_db), sentinel_key: str = Header(None, alias="X-Sentinel-Key")):
    """Detiene y elimina un target usando el endpoint de la UI."""
    if sentinel_key != settings.API_KEY:
        logger.warning(f"🔐 Intento de DELETE /stop/{target_id} sin autorización")
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        logger.info(f"🗑️  DELETE /stop/{target_id}: Eliminando target...")
        target_id_str = str(target_id)
        
        # Paso 1: Remover del scheduler PRIMERO
        removed_from_scheduler = scheduler.remove_target_watch(target_id_str)
        if removed_from_scheduler:
            logger.success(f"⏹️  Target {target_id} removido del scheduler")
        else:
            logger.warning(f"⚠️  Target {target_id} no estaba en el scheduler")
        
        # Paso 2: Eliminar de la BD
        repo = TargetRepository(db)
        if not repo.delete_target(target_id):
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
        query = text("""
            SELECT s.id, s.name, s.url, s.is_active, s.status_code, s.last_check
            FROM services s
            WHERE s.is_active = True
            ORDER BY s.id
        """)
        result = db.execute(query).fetchall()
        repo = TargetRepository(db)
        
        return [
            {
                "id": row[0],
                "name": row[1],
                "url": row[2],
                "is_active": row[3],
                "status_code": row[4],
                "last_check": str(row[5]) if row[5] else None,
                "health_score": repo.calculate_health_score(row[0])
            }
            for row in result
        ]
    except Exception as e:
        logger.error(f"❌ Error in GET /status: {e}")
        raise HTTPException(status_code=500, detail="Error querying service status")

@app.get("/metrics/{target_id}")
async def get_metrics(target_id: int, db: Session = Depends(get_db)):
    """Retorna las últimas 100 métricas para el gráfico de Chart.js"""
    try:
        query = text("""
            SELECT response_time_ms, status_code, timestamp
            FROM service_metrics
            WHERE target_id = :tid
            ORDER BY timestamp DESC
            LIMIT 100
        """)
        result = db.execute(query, {"tid": target_id}).fetchall()
        rows = list(reversed(result))
        return {
            "target_id": target_id,
            "count": len(rows),
            "metrics": [
                {
                    "response_time_ms": row[0],
                    "status_code": row[1],
                    "timestamp": row[2].isoformat() if row[2] else None
                }
                for row in rows
            ]
        }
    except Exception as e:
        logger.error(f"Error en /metrics/{target_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener métricas")

@app.get("/health", response_model=HealthCheckResponse)
async def health_check(db: Session = Depends(get_db)):
    """Endpoint de health check: verifica BD y scheduler"""
    try:
        # Verificar conexión a DB
        db_alive = False
        try:
            result = db.execute(text("SELECT 1")).first()
            db_alive = result is not None
        except:
            db_alive = False
        
        # Verificar scheduler
        scheduler_alive = scheduler._scheduler is not None and scheduler._scheduler.running
        
        # Contar targets activos
        active_count = len(scheduler.get_running_target_ids())
        
        # Determinar estado general
        if db_alive and scheduler_alive:
            status = "ok"
        elif db_alive or scheduler_alive:
            status = "degraded"
        else:
            status = "critical"
        
        return HealthCheckResponse(
            status=status,
            database=db_alive,
            scheduler=scheduler_alive,
            active_targets=active_count
        )
    except Exception as e:
        logger.error(f"Error en /health: {e}")
        raise HTTPException(status_code=503, detail="Health check failed")

@app.get("/stats/{target_id}")
async def get_target_stats(target_id: int, db: Session = Depends(get_db)):
    """Retorna estadísticas detalladas y health score de un target"""
    try:
        repo = TargetRepository(db)
        health_score = repo.calculate_health_score(target_id)
        stats = repo.get_target_statistics(target_id)
        
        return {
            "target_id": target_id,
            "health_score": health_score,
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Error en /stats/{target_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener estadísticas")

# --- GRACEFUL SHUTDOWN HANDLERS ---
def handle_signal(signum, frame):
    """Manejador de señal para graceful shutdown"""
    global _graceful_shutdown
    _graceful_shutdown = True
    logger.warning(f"⚠️  Señal {signum} recibida. Iniciando shutdown gracioso...")
    
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