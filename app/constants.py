"""Constantes del servicio."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Servicio(StrEnum):
    """Proceso que emite cada log (campo "service")."""

    API = "api"
    WORKER = "worker"


class Evento(StrEnum):
    """Nombre estable de cada evento de log (campo "event"), apto para filtrar y alertar."""

    PAGINA_ORIGEN_OBTENIDA = "source_page_fetched"
    REINTENTO_API_ORIGEN = "source_api_retry"
    REINTENTO_SMTP = "smtp_retry"
    FICHERO_GUARDADO = "file_saved"


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


# Contrato de la API de origen (contracts/source-order*.json).
RUTA_PEDIDOS: Final[str] = "/v1/orders"
CABECERA_RETRY_AFTER: Final[str] = "Retry-After"
MAX_REGISTROS_POR_PAGINA: Final[int] = 500
PATRON_MONEDA: Final[str] = r"^[A-Z]{3}$"

# Fallos HTTP de la API de origen que se consideran transitorios y se reintentan.
# El 500 se excluye a propósito: suele indicar un error del servidor que no se
# arregla repitiendo la misma petición (por ejemplo, la fecha sin zona horaria).
CODIGOS_HTTP_TRANSITORIOS: Final[frozenset[int]] = frozenset({429, 502, 503, 504})

# Fichero de exportación: export-20261020T080000Z-8e7d2bb2.csv
PREFIJO_FICHERO: Final[str] = "export"
EXTENSION_FICHERO: Final[str] = ".csv"
FORMATO_FECHA_FICHERO: Final[str] = "%Y%m%dT%H%M%SZ"
CODIFICACION_FICHERO: Final[str] = "utf-8"

# Email de la exportación. El Message-ID es determinista: <execution_id@export-service>.
DOMINIO_MESSAGE_ID: Final[str] = "export-service"
ASUNTO_EMAIL: Final[str] = "Exportación de pedidos: {nombre}"
CUERPO_EMAIL: Final[str] = (
    "Se adjunta el fichero {nombre} con {filas} pedidos.\n\nEjecución: {execution_id}\nSHA-256: {sha256}\n"
)
# Backoff de los reintentos SMTP (el número de reintentos sí es configurable: SMTP_MAX_RETRIES).
BACKOFF_SMTP_BASE_SEGUNDOS: Final[float] = 1.0
BACKOFF_SMTP_MAX_SEGUNDOS: Final[float] = 10.0

# Columnas del fichero de exportación, en el orden de contracts/export-record.example.json.
COLUMNAS_EXPORTACION: Final[tuple[str, ...]] = (
    "order_id",
    "customer_id",
    "amount",
    "currency",
    "updated_at",
)
