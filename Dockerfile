# 1. Imagen base ligera y estable
FROM python:3.11-slim

# 2. Metadatos del sistema
LABEL maintainer="Sentinel-Q Team"
LABEL version="1.0.0"

# 3. Variables de entorno críticas
# Evita que Python genere archivos .pyc y asegura logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
# Define la ruta de búsqueda de módulos para el 'src layout'
ENV PYTHONPATH=/app/src

WORKDIR /app

# 4. Instalación de dependencias del Sistema Operativo
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    build-essential \
    python3-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 5. Crear usuario no-root dedicado con UID 1000 para coincidir con el host
# Esto permite escribir en volúmenes montados sin conflictos de permisos
RUN groupadd --system --gid 1000 appgroup && \
    useradd --system --uid 1000 --gid appgroup --no-create-home appuser

# 6. Gestión de dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 7. Copia del Código Fuente y Assets
COPY src/ /app/src/

# 8. Crear directorio de logs con ownership correcto ANTES de cambiar de usuario
# Esto garantiza que appuser pueda escribir logs sin escalar privilegios
RUN mkdir -p /app/logs && chown -R appuser:appgroup /app/logs

# 9. Cambiar a usuario no-root para runtime
USER appuser

# 10. Exposición de puertos
EXPOSE 8000

# 11. Comando de ejecución
CMD ["uvicorn", "sentinel.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]