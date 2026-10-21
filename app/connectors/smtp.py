"""Envío del fichero de exportación por SMTP.

Política "como mucho una vez": solo se reintenta cuando hay certeza de que el
email NO ha salido:

- fallo al conectar, al iniciar TLS o al autenticarse (aún no se ha enviado nada);
- respuesta 4xx del servidor: rechazo temporal explícito, el email no se aceptó.

Si la conexión se corta mientras se envía, no se sabe si el servidor llegó a
aceptarlo: no se reintenta y se lanza EnvioEmailInciertoError, para que el
servicio marque la ejecución para revisión en lugar de arriesgarse a enviar un
duplicado. Un 5xx es un rechazo definitivo: EnvioEmailError, sin reintento.
"""

from __future__ import annotations

import contextlib
import logging
import smtplib
import ssl
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from uuid import UUID

from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt

from app.config import Settings
from app.connectors.reintentos import espera_exponencial_con_jitter
from app.constants import (
    ASUNTO_EMAIL,
    BACKOFF_SMTP_BASE_SEGUNDOS,
    BACKOFF_SMTP_MAX_SEGUNDOS,
    CUERPO_EMAIL,
    DOMINIO_MESSAGE_ID,
    Evento,
)
from app.domain.exportacion import FicheroExportacion

logger = logging.getLogger(__name__)

# smtplib.SMTP(host, port, timeout=...) o un sustituto en los tests.
FabricaSmtp = Callable[..., smtplib.SMTP]


class EnvioEmailError(Exception):
    """El email no se ha enviado: rechazo definitivo o reintentos agotados."""


class EnvioEmailInciertoError(Exception):
    """La conexión se cortó durante el envío: no se sabe si el email salió."""


class _FalloSmtpTransitorioError(Exception):
    """Fallo con la certeza de que el email no salió. Uso interno."""


def message_id(execution_id: UUID) -> str:
    """Message-ID determinista: la misma ejecución produce siempre el mismo identificador.

    Permite al destinatario (o a quien revise) detectar un posible duplicado.
    """
    return f"<{execution_id}@{DOMINIO_MESSAGE_ID}>"


def construir_email(
    execution_id: UUID,
    fichero: FicheroExportacion,
    remitente: str,
    destinatarios: Sequence[str],
    momento: datetime,
) -> EmailMessage:
    """Construye el email con el CSV adjunto."""
    mensaje = EmailMessage()
    mensaje["From"] = remitente
    mensaje["To"] = ", ".join(destinatarios)
    mensaje["Subject"] = ASUNTO_EMAIL.format(nombre=fichero.nombre)
    mensaje["Message-ID"] = message_id(execution_id)
    mensaje["Date"] = format_datetime(momento)
    mensaje.set_content(
        CUERPO_EMAIL.format(
            nombre=fichero.nombre,
            filas=fichero.filas,
            execution_id=execution_id,
            sha256=fichero.sha256,
        ),
    )
    mensaje.add_attachment(fichero.contenido, maintype="text", subtype="csv", filename=fichero.nombre)
    return mensaje


class ClienteSmtp:
    """Cliente SMTP con reintentos seguros (solo cuando el email no salió)."""

    def __init__(
        self,
        settings: Settings,
        fabrica: FabricaSmtp = smtplib.SMTP,
        esperar: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fabrica = fabrica
        self._esperar = esperar
        self._host = settings.smtp_host
        self._puerto = settings.smtp_puerto
        self._timeout = settings.smtp_timeout_segundos
        self._usar_tls = settings.smtp_usar_tls
        self._usuario = settings.smtp_usuario
        self._password = settings.smtp_password
        self._remitente = settings.smtp_remitente
        self._destinatarios = settings.exportacion_destinatarios
        self._max_intentos = settings.smtp_max_reintentos + 1

    def enviar_exportacion(self, execution_id: UUID, fichero: FicheroExportacion) -> str:
        """Envía el fichero y devuelve el Message-ID usado.

        Lanza EnvioEmailError o EnvioEmailInciertoError.
        """
        mensaje = construir_email(execution_id, fichero, self._remitente, self._destinatarios, datetime.now(UTC))
        reintentador = Retrying(
            stop=stop_after_attempt(self._max_intentos),
            wait=self._calcular_espera,
            retry=retry_if_exception_type(_FalloSmtpTransitorioError),
            sleep=self._esperar,
            before_sleep=self._registrar_reintento,
            reraise=True,
        )
        try:
            reintentador(self._enviar, mensaje)
        except _FalloSmtpTransitorioError as error:
            raise EnvioEmailError(f"el email no se pudo enviar tras {self._max_intentos} intentos: {error}") from error
        return message_id(execution_id)

    def _enviar(self, mensaje: EmailMessage) -> None:
        """Un intento de envío. Clasifica el fallo según si el email pudo salir o no."""
        try:
            conexion = self._conectar()
        # smtplib.SMTPException hereda de OSError: aquí entran también los errores SMTP.
        except OSError as error:
            raise _FalloSmtpTransitorioError(f"no se pudo conectar con el servidor SMTP: {error}") from error

        try:
            conexion.send_message(mensaje)
        # El orden importa: las respuestas del servidor antes que el corte genérico (OSError).
        except smtplib.SMTPResponseException as error:
            if 400 <= error.smtp_code < 500:
                raise _FalloSmtpTransitorioError(f"rechazo temporal SMTP {error.smtp_code}") from error
            raise EnvioEmailError(f"rechazo definitivo SMTP {error.smtp_code}") from error
        except smtplib.SMTPRecipientsRefused as error:
            raise EnvioEmailError("el servidor SMTP rechazó todos los destinatarios") from error
        except OSError as error:
            raise EnvioEmailInciertoError(f"la conexión SMTP se cortó durante el envío: {error}") from error
        finally:
            # El email ya está resuelto: un fallo al despedirse del servidor no cambia nada.
            with contextlib.suppress(OSError):
                conexion.quit()

    def _conectar(self) -> smtplib.SMTP:
        """Abre la conexión, activa TLS y se autentica si está configurado."""
        conexion = self._fabrica(self._host, self._puerto, timeout=self._timeout)
        try:
            if self._usar_tls:
                conexion.starttls(context=ssl.create_default_context())
            if self._usuario:
                conexion.login(self._usuario, self._password.get_secret_value())
        except OSError:
            conexion.close()
            raise
        return conexion

    def _calcular_espera(self, estado: RetryCallState) -> float:
        return espera_exponencial_con_jitter(
            estado.attempt_number,
            BACKOFF_SMTP_BASE_SEGUNDOS,
            BACKOFF_SMTP_MAX_SEGUNDOS,
        )

    def _registrar_reintento(self, estado: RetryCallState) -> None:
        error = estado.outcome.exception() if estado.outcome else None
        espera = estado.next_action.sleep if estado.next_action else 0.0
        logger.warning(
            "Fallo SMTP sin envío: se reintenta",
            extra={
                "event": Evento.REINTENTO_SMTP,
                "attempt": estado.attempt_number,
                "max_attempts": self._max_intentos,
                "wait_seconds": round(espera, 2),
                "reason": str(error),
            },
        )
