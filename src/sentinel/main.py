from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from slowapi.errors import RateLimitExceeded
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
from sentinel.domain.schemas import (
    ServiceTarget as ServiceTargetSchema,
    ServiceTargetCreate,
    HealthCheckResponse,
    ServiceStatusSnapshot,
    MetricsSnapshot,
    AlertHistorySnapshot,
    Token,
)
from sentinel.infrastructure.database import SessionLocal, engine, get_db, Base
from sentinel.infrastructure.repository import TargetRepository
from sentinel.infrastructure.auth import verify_jwt_token
from sentinel.infrastructure.jwt_service import create_access_token
from sentinel.infrastructure.rate_limiter import limiter
from sentinel.services import (
    TargetService,
    TelegramQueryService,
    MonitoringQueryService,
    SystemHealthService,
)

APP_DESCRIPTION = (
    "Professional-grade service monitoring engine "
    "with intelligent health scoring."
)
DEFAULT_CORS_ORIGINS = "http://localhost:8000,http://localhost:8501"

# --- CONFIGURAR LOGGING PERSISTENTE ---
log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

logger.remove()
logger.add(
    sink=str(log_dir / "sentinel.log"),
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    rotation="00:00",
    retention="10 days",
    level="INFO",
)
logger.add(
    sink=sys.stderr,
    format=(
        "<level>{time:HH:mm:ss}</level> | "
        "<level>{level: <8}</level> | "
        "<level>{message}</level>"
    ),
    level="DEBUG",
)


def is_testing() -> bool:
    """True under pytest (TESTING=1 from conftest.py) or during a test."""
    flag = os.environ.get("TESTING", "").strip().lower() in {"1", "true", "yes"}
    return flag or "PYTEST_CURRENT_TEST" in os.environ


# Singletons used by the lifespan and the endpoints (created at import time).
scheduler = SentinelScheduler()
bot_poller = TelegramBotPoller()
_graceful_shutdown = False


async def _init_database_schema(max_retries: int = 5, retry_delay: float = 2) -> None:
    """Create/validate the schema, retrying with exponential backoff."""
    for attempt in range(1, max_retries + 1):
        try:
            Base.metadata.create_all(bind=engine)
            logger.success("✅ Esquemas de base de datos validados.")
            return
        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    f"⚠️ BD no disponible (intento {attempt}/{max_retries}): {e}"
                )
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.error(
                    f"❌ Fallo crítico al conectar con la base de datos: {e}"
                )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"--- INICIANDO {settings.APP_NAME} ---")

    # Testing: sin BD real, scheduler, sync loop ni poller de Telegram.
    if is_testing():
        logger.info(
            "🧪 Modo de pruebas detectado: "
            "omitiendo BD real y tareas en segundo plano."
        )
        yield
        return

    # --- STARTUP (producción) ---
    await _init_database_schema()

    scheduler.start()
    sync_task = asyncio.create_task(sync_database_targets(), name="sync_targets")
    bot_task = asyncio.create_task(bot_poller.run(), name="telegram_poller")
    logger.success(
        "🚀 SISTEMA OPERACIONAL: "
        "Motor, sincronizador y bot Telegram en línea."
    )

    try:
        yield
    finally:
        # --- SHUTDOWN: cada paso aislado para que un fallo no bloquee el resto ---
        logger.warning("Stopping Sentinel-Q services...")
        for task in (sync_task, bot_task):
            task.cancel()
        await asyncio.gather(sync_task, bot_task, return_exceptions=True)

        for step_name, step in (
            ("bot_poller.stop", bot_poller.stop),
            ("scheduler.shutdown", scheduler.shutdown),
            ("engine.dispose", engine.dispose),
        ):
            try:
                step()
            except Exception as e:
                logger.error(f"❌ Error en {step_name} durante el shutdown: {e}")
        logger.success("✅ Shutdown completado con éxito.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    description=APP_DESCRIPTION,
)


# --- RATE LIMITING (SlowAPI) ---
async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """429 con cuerpo {"detail": ...}, igual que el resto de errores de la API."""
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
    )


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


# --- SECURITY HEADERS MIDDLEWARE ---
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    return response


# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


async def sync_database_targets():
    logger.info("📡 Iniciando ciclo de sincronización de objetivos...")
    while True:
        db = SessionLocal()
        try:
            repo = TargetRepository(db)
            result = repo.get_all_active()
            currently_watching = scheduler.get_running_target_ids()
            active_ids = {str(row.id) for row in result}

            for watching_id in currently_watching:
                if watching_id not in active_ids:
                    scheduler.remove_target_watch(watching_id)

            for row in result:
                target_id_str = str(row.id)
                if target_id_str not in currently_watching:
                    interval = row.check_interval or 60
                    target_obj = DomainServiceTarget(
                        id=row.id,
                        name=row.name,
                        url=row.url,
                        check_interval=interval,
                        is_active=True,
                    )
                    scheduler.add_target_watch(target_obj)
        except Exception as e:
            logger.error(f"❌ Error crítico en sincronización: {e}")
        finally:
            db.close()
        await asyncio.sleep(10)


# ====================================================================
# ENDPOINT PÚBLICO DE AUTENTICACIÓN JWT
# ====================================================================
@app.post("/auth/token", response_model=Token)
@limiter.limit("5/minute")
async def login_for_access_token(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    # `request` es obligatorio para SlowAPI; `response` le permite
    # inyectar cabeceras.
    admin_user = getattr(
        settings, "ADMIN_USERNAME", getattr(settings, "ADMIN_USER", "admin")
    )
    admin_pass = getattr(settings, "ADMIN_PASSWORD", "admin")

    if form_data.username != admin_user or form_data.password != admin_pass:
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        subject=form_data.username, tenant_id=1, role="admin"
    )
    return {"access_token": access_token, "token_type": "bearer"}


# ====================================================================
# ENDPOINTS PROTEGIDOS CON JWT (cubre todas las rutas de la suite)
# ====================================================================


@app.get(
    "/targets",
    response_model=list[ServiceTargetSchema],
    dependencies=[Depends(verify_jwt_token)],
)
async def list_targets(db: Session = Depends(get_db)):
    service = TargetService(db, scheduler)
    return service.list_targets_with_health()


@app.post(
    "/targets",
    response_model=ServiceTargetSchema,
    status_code=201,
    dependencies=[Depends(verify_jwt_token)],
)
async def create_target(target_in: ServiceTargetCreate, db: Session = Depends(get_db)):
    service = TargetService(db, scheduler)
    return service.create_target(target_in)


@app.delete(
    "/targets/{target_id}",
    status_code=204,
    dependencies=[Depends(verify_jwt_token)],
)
async def delete_target(target_id: int, db: Session = Depends(get_db)):
    service = TargetService(db, scheduler)
    if not service.delete_target(target_id):
        raise HTTPException(status_code=404, detail="Target no encontrado")
    return Response(status_code=204)


@app.delete(
    "/stop/{target_id}",
    status_code=204,
    dependencies=[Depends(verify_jwt_token)],
)
async def stop_target(target_id: int, db: Session = Depends(get_db)):
    service = TargetService(db, scheduler)
    if not service.delete_target(target_id):
        raise HTTPException(status_code=404, detail="Target no encontrado")
    return Response(status_code=204)


@app.get("/status", dependencies=[Depends(verify_jwt_token)])
async def get_status(db: Session = Depends(get_db)):
    service = MonitoringQueryService(db)
    return service.get_active_status_with_health()


@app.get("/metrics/{target_id}", dependencies=[Depends(verify_jwt_token)])
async def get_metrics(target_id: int, db: Session = Depends(get_db)):
    service = MonitoringQueryService(db)
    return service.get_target_metrics(target_id=target_id)


@app.get(
    "/health",
    response_model=HealthCheckResponse,
    dependencies=[Depends(verify_jwt_token)],
)
async def health_check(db: Session = Depends(get_db)):
    service = SystemHealthService(db, scheduler)
    return service.get_health()


@app.get("/stats/{target_id}", dependencies=[Depends(verify_jwt_token)])
async def get_target_stats(target_id: int, db: Session = Depends(get_db)):
    service = MonitoringQueryService(db)
    return service.get_target_stats_with_health(target_id)


@app.get(
    "/telegram/status",
    response_model=ServiceStatusSnapshot,
    dependencies=[Depends(verify_jwt_token)],
)
async def telegram_status(db: Session = Depends(get_db)):
    service = TelegramQueryService(db)
    return service.get_status_snapshot()


@app.get(
    "/telegram/metrics",
    response_model=MetricsSnapshot,
    dependencies=[Depends(verify_jwt_token)],
)
async def telegram_metrics(db: Session = Depends(get_db)):
    service = TelegramQueryService(db)
    return service.get_metrics_snapshot()


@app.get(
    "/telegram/alerts",
    response_model=AlertHistorySnapshot,
    dependencies=[Depends(verify_jwt_token)],
)
async def telegram_alerts(hours: int = 24, db: Session = Depends(get_db)):
    service = TelegramQueryService(db)
    return service.get_alerts_history(hours=hours)


# ====================================================================
# RUTAS PÚBLICAS (Dashboard HTML y Estáticos)
# ====================================================================
@app.get("/")
async def dashboard():
    static_dir = Path(__file__).parent / "static" / "index.html"
    if not static_dir.exists():
        raise HTTPException(status_code=404, detail="Dashboard file not found")
    return FileResponse(str(static_dir), media_type="text/html")


static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# --- GRACEFUL SHUTDOWN ---
def handle_signal(signum, frame):
    logger.warning(f"⚠️ Señal {signum} recibida. Apagando...")
    try:
        bot_poller.stop()
        scheduler.shutdown()
        engine.dispose()
    except Exception:
        pass
    sys.exit(0)


# Bajo pytest estos handlers secuestrarían Ctrl+C (sys.exit(0) = "éxito"
# silencioso), y signal.signal() falla fuera del hilo principal.
if not is_testing():
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)