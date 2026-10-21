"""Pruebas del envío SMTP: el email y, sobre todo, cuándo se reintenta y cuándo no."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from uuid import UUID

import pytest

from app.connectors.smtp import ClienteSmtp, EnvioEmailError, EnvioEmailInciertoError
from app.domain.exportacion import FicheroExportacion
from tests.factories import crear_settings

EXECUTION_ID = UUID("8e7d2bb2-1dd2-4f4c-9bb7-6b8a2fd73c19")
FICHERO = FicheroExportacion(
    nombre="export-20261020T080005Z-8e7d2bb2.csv",
    contenido=b"order_id,customer_id,amount,currency,updated_at\r\n",
    sha256="abc123",
    filas=0,
)


class ServidorSmtpFalso:
    """Sustituye a smtplib.SMTP. Cada conexión sigue el siguiente comportamiento del guion.

    - None: el envío funciona.
    - ConnectionRefusedError: falla al conectar.
    - Cualquier otra excepción: se lanza al enviar.
    """

    def __init__(self, *guion: Exception | None) -> None:
        self._guion = list(guion)
        self.conexiones = 0
        self.enviados: list[EmailMessage] = []
        self.llamadas: list[str] = []
        self._actual: Exception | None = None

    def __call__(self, host: str, puerto: int, timeout: float) -> ServidorSmtpFalso:
        self.conexiones += 1
        self._actual = self._guion.pop(0)
        if isinstance(self._actual, ConnectionRefusedError):
            raise self._actual
        return self

    def starttls(self, **_: object) -> None:
        self.llamadas.append("starttls")

    def login(self, usuario: str, password: str) -> None:
        self.llamadas.append(f"login:{usuario}")

    def send_message(self, mensaje: EmailMessage) -> None:
        if self._actual is not None:
            raise self._actual
        self.enviados.append(mensaje)

    def quit(self) -> None:
        self.llamadas.append("quit")

    def close(self) -> None:
        self.llamadas.append("close")


def _cliente(servidor: ServidorSmtpFalso, esperas: list[float], **config: str) -> ClienteSmtp:
    base = {"EXPORT_RECIPIENT": "a@example.test, b@example.test", "SMTP_USE_TLS": "false", "SMTP_MAX_RETRIES": "2"}
    settings = crear_settings(**{**base, **config})
    return ClienteSmtp(settings, fabrica=servidor, esperar=esperas.append)


def test_email_con_adjunto_destinatarios_y_message_id_determinista():
    servidor = ServidorSmtpFalso(None)

    message_id = _cliente(servidor, []).enviar_exportacion(EXECUTION_ID, FICHERO)

    [mensaje] = servidor.enviados
    assert message_id == mensaje["Message-ID"] == "<8e7d2bb2-1dd2-4f4c-9bb7-6b8a2fd73c19@export-service>"
    assert mensaje["To"] == "a@example.test, b@example.test"
    assert FICHERO.nombre in mensaje["Subject"]
    [adjunto] = list(mensaje.iter_attachments())
    assert adjunto.get_filename() == FICHERO.nombre
    assert adjunto.get_content_type() == "text/csv"
    assert adjunto.get_payload(decode=True) == FICHERO.contenido


def test_usa_tls_y_credenciales_si_estan_configurados():
    servidor = ServidorSmtpFalso(None)

    _cliente(servidor, [], SMTP_USE_TLS="true", SMTP_USERNAME="usuario").enviar_exportacion(EXECUTION_ID, FICHERO)

    assert servidor.llamadas[:2] == ["starttls", "login:usuario"]


@pytest.mark.parametrize(
    "fallo",
    [
        pytest.param(ConnectionRefusedError("rechazada"), id="no conecta"),
        pytest.param(smtplib.SMTPDataError(451, b"intentalo luego"), id="rechazo temporal 4xx"),
    ],
)
def test_fallo_sin_envio_se_reintenta(fallo: Exception):
    esperas: list[float] = []
    servidor = ServidorSmtpFalso(fallo, None)

    _cliente(servidor, esperas).enviar_exportacion(EXECUTION_ID, FICHERO)

    assert servidor.conexiones == 2
    assert len(servidor.enviados) == 1
    assert len(esperas) == 1


def test_reintentos_agotados():
    servidor = ServidorSmtpFalso(*[ConnectionRefusedError("rechazada") for _ in range(3)])

    with pytest.raises(EnvioEmailError, match="tras 3 intentos"):
        _cliente(servidor, []).enviar_exportacion(EXECUTION_ID, FICHERO)

    assert servidor.conexiones == 3


def test_rechazo_definitivo_5xx_no_se_reintenta():
    servidor = ServidorSmtpFalso(smtplib.SMTPDataError(554, b"rechazado"))

    with pytest.raises(EnvioEmailError, match="554"):
        _cliente(servidor, []).enviar_exportacion(EXECUTION_ID, FICHERO)

    assert servidor.conexiones == 1


def test_corte_durante_el_envio_es_incierto_y_no_se_reintenta():
    """Como mucho una vez: si no sabemos si salió, no se arriesga un duplicado."""
    servidor = ServidorSmtpFalso(smtplib.SMTPServerDisconnected("conexión perdida"))

    with pytest.raises(EnvioEmailInciertoError):
        _cliente(servidor, []).enviar_exportacion(EXECUTION_ID, FICHERO)

    assert servidor.conexiones == 1
