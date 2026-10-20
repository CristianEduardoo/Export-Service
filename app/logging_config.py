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

_execution_id_actual: ContextVar[str | None] = ContextVar("execution_id", default=None)

# Atributos que todo LogRecord trae de serie; el resto son campos extra del usuario.
_ATRIBUTOS_ESTANDAR: Final[frozenset[str]] = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", None, None).__dict__,
) | {"message", "asctime"}

# Librerías que configuran sus propios handlers; se redirigen a nuestro formato.
_LOGGERS_EXTERNOS: Final[tuple[str, ...]] = ("uvicorn", "uvicorn.error", "uvicorn.access")


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
        # default=str: fechas, Decimal o UUID se escriben como texto en lugar de romper el log.
        return json.dumps(datos, default=str, ensure_ascii=False)


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


def _formatear_timestamp(marca: float) -> str:
    """Fecha del log en ISO 8601, UTC y con milisegundos: 2026-10-20T08:00:00.123Z."""
    return datetime.fromtimestamp(marca, tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
