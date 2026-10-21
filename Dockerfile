# Imagen única para todos los procesos del servicio: API, worker y migraciones.
# Cada servicio del compose la arranca con un comando distinto.

# Misma versión que el venv local (.python-version) y que el CI.
FROM python:3.12.15-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1


# --- Etapa de construcción: instala las dependencias en un entorno virtual aislado.
FROM base AS builder
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build

# Primero solo las dependencias, con un paquete vacío: esta capa queda en caché y
# no se reinstala nada cuando lo único que cambia es el código.
COPY pyproject.toml README.md ./
RUN mkdir app && touch app/__init__.py && pip install .

# Después el código, sin volver a resolver dependencias.
COPY app ./app
RUN pip install --no-deps --force-reinstall .


# --- Etapa final: solo el entorno virtual y lo necesario para ejecutar (sin herramientas de build).
FROM base AS runtime

ARG VERSION=dev
LABEL org.opencontainers.image.title="export-service" \
      org.opencontainers.image.description="Exporta pedidos a CSV y los envía por email" \
      org.opencontainers.image.version="${VERSION}"

# Usuario sin privilegios: si alguien comprometiera el proceso, no sería root en el contenedor.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
RUN mkdir -p /app/exports && chown app:app /app/exports

USER app
EXPOSE 8000

# Health check de la API (el worker lo sustituye en el compose por el suyo).
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live', timeout=2)"]

CMD ["uvicorn", "--factory", "app.api.main:crear_app", "--host", "0.0.0.0", "--port", "8000"]
