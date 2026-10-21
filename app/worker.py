"""Worker: procesa las ejecuciones pendientes, separado de la API.

Uso:
    python -m app.worker            Proceso continuo (servicio "worker" del compose).
    python -m app.worker --enqueue  Solo registra una ejecución programada y termina: el worker
                                    en bucle la procesa. Es lo que ejecuta Ofelia (job-exec), así
                                    el scheduler no contiene lógica de negocio.
    python -m app.worker --health   Health check del contenedor: ¿sigue latiendo el bucle?

En cada vuelta del bucle: recupera ejecuciones abandonadas, procesa la siguiente
pendiente (si la hay) y actualiza el latido. Ante SIGTERM (docker stop) termina
la ejecución en curso y sale: nunca corta un envío a medias.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.connectors.almacen_ficheros import AlmacenFicherosLocal
from app.connectors.smtp import ClienteSmtp
from app.connectors.source_api import ClienteApiOrigen
from app.constants import NOMBRE_FICHERO_LATIDO, Evento, OrigenEjecucion, Servicio
from app.db.repositories.ejecucion_repository import EjecucionActivaExistenteError
from app.db.session import crear_fabrica_sesiones, crear_motor
from app.logging_config import configurar_logging
from app.services.ejecuciones_service import EjecucionesService
from app.services.exportacion_service import AlmacenFicheros, ClienteOrigen, EnviadorEmail, ExportacionService

logger = logging.getLogger(__name__)

FICHERO_LATIDO = Path(tempfile.gettempdir()) / NOMBRE_FICHERO_LATIDO


class Worker:
    """Bucle del worker. Las dependencias se inyectan para poder probarlo sin red."""

    def __init__(
        self,
        settings: Settings,
        fabrica_sesiones: sessionmaker[Session],
        origen: ClienteOrigen,
        email: EnviadorEmail,
        almacen: AlmacenFicheros,
    ) -> None:
        self.id = f"{socket.gethostname()}-{os.getpid()}"
        self._settings = settings
        self._fabrica = fabrica_sesiones
        self._origen = origen
        self._email = email
        self._almacen = almacen
        self._parada = threading.Event()

    def detener(self, *_: object) -> None:
        """Pide al bucle que termine al acabar la vuelta actual (manejador de SIGTERM/SIGINT)."""
        self._parada.set()

    def iteracion(self) -> bool:
        """Una vuelta: recupera abandonadas y procesa la siguiente pendiente. True si hubo trabajo."""
        with self._fabrica() as db:
            servicio = ExportacionService(db, self._settings, self._origen, self._email, self._almacen)
            servicio.recuperar_abandonadas()
            return servicio.procesar_siguiente(self.id)

    def bucle(self) -> None:
        """Procesa ejecuciones hasta recibir la señal de parada."""
        logger.info(
            "Worker arrancado",
            extra={
                "event": Evento.WORKER_ARRANCADO,
                "worker_id": self.id,
                "poll_interval_seconds": self._settings.worker_intervalo_sondeo_segundos,
            },
        )
        while not self._parada.is_set():
            hubo_trabajo = False
            try:
                hubo_trabajo = self.iteracion()
            except Exception:
                # Por ejemplo, PostgreSQL caído: se registra y se reintenta en la siguiente vuelta.
                logger.exception("Error en el bucle del worker: se reintenta", extra={"event": Evento.ERROR_WORKER})
            registrar_latido()
            # Si hubo trabajo se busca más sin esperar; si no, se espera (o se sale antes si llega la parada).
            if not hubo_trabajo:
                self._parada.wait(self._settings.worker_intervalo_sondeo_segundos)
        logger.info("Worker detenido", extra={"event": Evento.WORKER_DETENIDO, "worker_id": self.id})

    def programar(self) -> None:
        """Registra una ejecución programada. Si ya hay una activa no hace nada (no es un error)."""
        with self._fabrica() as db:
            try:
                EjecucionesService(db).crear(OrigenEjecucion.PROGRAMADA)
            except EjecucionActivaExistenteError as error:
                logger.warning(
                    "Ya hay una ejecución activa: no se programa otra",
                    extra={"event": Evento.EJECUCION_RECHAZADA, "active_execution_id": error.ejecucion_activa_id},
                )


def registrar_latido() -> None:
    """Actualiza la fecha del fichero de latido."""
    FICHERO_LATIDO.touch()


def latido_reciente(maximo_segundos: float) -> bool:
    """True si el bucle ha latido hace menos de maximo_segundos."""
    try:
        return time.time() - FICHERO_LATIDO.stat().st_mtime < maximo_segundos
    except FileNotFoundError:
        return False


def main(argumentos: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description="Worker de la exportación de pedidos")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--enqueue", action="store_true", help="registra una ejecución programada y termina")
    modo.add_argument("--health", action="store_true", help="comprueba que el bucle sigue latiendo")
    opciones = parser.parse_args(argumentos)

    settings = get_settings()
    if opciones.health:
        # Una ejecución larga renueva su lease sin pasar por el bucle: el latido se mide con el mismo margen.
        return 0 if latido_reciente(settings.worker_lease_segundos) else 1

    configurar_logging(Servicio.WORKER, settings.nivel_log)
    motor = crear_motor(settings.url_base_datos)
    try:
        with ClienteApiOrigen(settings) as origen:
            worker = Worker(
                settings,
                crear_fabrica_sesiones(motor),
                origen,
                ClienteSmtp(settings),
                AlmacenFicherosLocal(settings.exportacion_directorio),
            )
            if opciones.enqueue:
                worker.programar()
            else:
                signal.signal(signal.SIGTERM, worker.detener)
                signal.signal(signal.SIGINT, worker.detener)
                worker.bucle()
    finally:
        motor.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
