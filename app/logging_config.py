"""Logs estructurados en JSON por stdout.

Uso:
    logger.info("Ejecución completada", extra={"event": "execution_completed", "records_exported": 3})
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Final

from app.constants import Servicio
from app.domain.fechas import fecha_iso_utc

_execution_id_actual: ContextVar[str | None] = ContextVar("execution_id", default=None)

# Atributos que todo LogRecord trae de serie; el resto son campos extra del usuario.
# color_message lo añade uvicorn con códigos de color de terminal: en un log JSON es ruido.
_ATRIBUTOS_ESTANDAR: Final[frozenset[str]] = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", None, None).__dict__,
) | {"message", "asctime", "color_message"}

# Librerías que configuran sus propios handlers; se redirigen a nuestro formato.
_LOGGERS_EXTERNOS: Final[tuple[str, ...]] = ("uvicorn", "uvicorn.error", "uvicorn.access")

# Librerías que registran cada petición HTTP: duplican nuestro evento source_page_fetched
# (que ya incluye página, pedidos y duración), así que solo se dejan sus avisos y errores.
_LOGGERS_RUIDOSOS: Final[tuple[str, ...]] = ("httpx", "httpcore")


@contextmanager
def contexto_ejecucion(execution_id: str) -> Iterator[None]:
    """Asocia execution_id a todos los logs emitidos dentro del bloque."""
    token = _execution_id_actual.set(execution_id)
    try:
        yield
    finally:
        _execution_id_actual.reset(token)


class FormateadorJson(logging.Formatter):
    """Convierte cada LogRecord en una línea JSON."""

    def __init__(self, servicio: Servicio) -> None:
        super().__init__()
        self._servicio = servicio

    def format(self, record: logging.LogRecord) -> str:
        """Devuelve el log como una única línea JSON."""
        datos: dict[str, Any] = {
            "timestamp": _formatear_timestamp(record.created),
            "level": record.levelname,
            "service": self._servicio.value,
            "logger": record.name,
            "event": None,
            "message": record.getMessage(),
            "execution_id": _execution_id_actual.get(),
        }
        datos.update(
            {clave: valor for clave, valor in record.__dict__.items() if clave not in _ATRIBUTOS_ESTANDAR},
        )
        if record.exc_info and record.exc_info[0] is not None:
            tipo, error, _ = record.exc_info
            datos["error"] = {
                "type": tipo.__name__,
                "message": str(error),
                "stack_trace": self.formatException(record.exc_info),
            }
        return json.dumps(datos, default=_serializar, ensure_ascii=False)


def configurar_logging(servicio: Servicio, nivel: str) -> None:
    """Configura todos los logs del proceso para que salgan en JSON por stdout.

    Se llama una vez al arrancar cada proceso (API o worker).
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(FormateadorJson(servicio))

    raiz = logging.getLogger()
    raiz.handlers = [handler]
    raiz.setLevel(nivel)

    for nombre in _LOGGERS_EXTERNOS:
        externo = logging.getLogger(nombre)
        externo.handlers.clear()
        externo.propagate = True

    for nombre in _LOGGERS_RUIDOSOS:
        logging.getLogger(nombre).setLevel(logging.WARNING)


def _serializar(valor: object) -> str:
    """Valores que JSON no conoce: fechas en ISO con Z; el resto (Decimal, UUID...) como texto.

    Nunca lanza: un log que falla justo cuando hay un error es lo peor que puede pasar.
    """
    if isinstance(valor, datetime) and valor.tzinfo is not None:
        return fecha_iso_utc(valor)
    return str(valor)


def _formatear_timestamp(marca: float) -> str:
    """Fecha del log en ISO 8601, UTC y con milisegundos: 2026-10-20T08:00:00.123Z."""
    return datetime.fromtimestamp(marca, tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
