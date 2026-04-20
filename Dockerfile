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
# Necesitamos libpq para Postgres y build-essential para compilar librerías de alto rendimiento
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    build-essential \
    python3-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 5. Gestión de dependencias de Python
# Copiamos solo los requerimientos para aprovechar la caché de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 6. Copia del Código Fuente y Assets
# Aseguramos que la carpeta static (UI) se incluya en la imagen
COPY src/ /app/src/

# 7. Exposición de puertos
EXPOSE 8000

# 8. Comando de ejecución
# Usamos el modo producción de Uvicorn
CMD ["uvicorn", "sentinel.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]