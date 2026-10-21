"""Aplicación FastAPI.

Se crea con una fábrica (crear_app) en lugar de una variable global: los tests
pueden pasarle su propia configuración y su base de datos. Arranque:

    uvicorn --factory app.api.main:crear_app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from sqlalchemy import Engine

from app.api.routes import ejecuciones, health
from app.config import Settings, get_settings
from app.constants import Evento, Servicio
from app.db.session import crear_fabrica_sesiones, crear_motor
from app.logging_config import configurar_logging

logger = logging.getLogger(__name__)


def crear_app(settings: Settings | None = None, motor: Engine | None = None) -> FastAPI:
    """Construye la aplicación con su configuración y su conexión a la base de datos."""
    settings = settings or get_settings()
    # Después de que uvicorn configure sus logs: así también salen en nuestro JSON.
    configurar_logging(Servicio.API, settings.nivel_log)
    motor = motor or crear_motor(settings.url_base_datos)

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        logger.info("API arrancada", extra={"event": Evento.API_ARRANCADA, "environment": settings.entorno})
        yield
        motor.dispose()

    app = FastAPI(
        title="Export Service",
        description="Lanza y consulta ejecuciones de la exportación de pedidos.",
        version=version("export-service"),
        lifespan=ciclo_de_vida,
    )
    app.state.motor = motor
    app.state.fabrica_sesiones = crear_fabrica_sesiones(motor)
    app.include_router(ejecuciones.router)
    app.include_router(health.router)
    return app
