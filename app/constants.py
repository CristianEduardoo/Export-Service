"""Constantes del servicio."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Servicio(StrEnum):
    """Proceso que emite cada log (campo "service")."""

    API = "api"
    WORKER = "worker"


class EstadoEjecucion(StrEnum):
    """Estado público de una ejecución (contrato de la API)."""

    PENDIENTE = "pending"
    EN_CURSO = "running"
    COMPLETADA = "completed"
    FALLIDA = "failed"


# Solo puede haber una ejecución activa a la vez (protección frente a concurrencia).
ESTADOS_ACTIVOS: Final[frozenset[EstadoEjecucion]] = frozenset(
    {EstadoEjecucion.PENDIENTE, EstadoEjecucion.EN_CURSO},
)
ESTADOS_FINALES: Final[frozenset[EstadoEjecucion]] = frozenset(
    {EstadoEjecucion.COMPLETADA, EstadoEjecucion.FALLIDA},
)


class PasoEjecucion(StrEnum):
    """Punto exacto en el que está una ejecución en curso.

    Permite decidir qué hacer si el worker muere a mitad: el paso ENVIANDO_EMAIL
    se guarda ANTES de enviar, de modo que una caída en ese punto se detecta como
    envío de resultado incierto.
    """

    CONSULTANDO_ORIGEN = "fetching_source"
    GENERANDO_FICHERO = "writing_file"
    ENVIANDO_EMAIL = "sending_email"
    EMAIL_ENVIADO = "email_sent"


class OrigenEjecucion(StrEnum):
    """Quién lanzó la ejecución (útil para entender el historial y los logs)."""

    MANUAL = "manual"
    PROGRAMADA = "scheduled"


class CodigoError(StrEnum):
    """Motivo de una ejecución fallida, estable y apto para filtrar o alertar."""

    ORIGEN_NO_DISPONIBLE = "source_unavailable"
    CONTRATO_ORIGEN_INVALIDO = "source_contract_violation"
    ENVIO_EMAIL_FALLIDO = "email_send_failed"
    ENVIO_EMAIL_INCIERTO = "email_delivery_unknown"
    WORKER_PERDIDO = "worker_lost"
    ERROR_INESPERADO = "unexpected_error"


class EscenarioOrigen(StrEnum):
    """Escenarios de prueba que admite la API mock (parámetro ?scenario=)."""

    ERROR_TEMPORAL = "temporary_error"
    LENTO = "slow"


# Fallos HTTP de la API de origen que se consideran transitorios y se reintentan.
# El 500 se excluye a propósito: suele indicar un error del servidor que no se
# arregla repitiendo la misma petición (por ejemplo, la fecha sin zona horaria).
CODIGOS_HTTP_TRANSITORIOS: Final[frozenset[int]] = frozenset({429, 502, 503, 504})

# Columnas del fichero de exportación, en el orden de contracts/export-record.example.json.
COLUMNAS_EXPORTACION: Final[tuple[str, ...]] = (
    "order_id",
    "customer_id",
    "amount",
    "currency",
    "updated_at",
)
