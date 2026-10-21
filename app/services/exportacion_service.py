"""Orquestación de una ejecución: el "cerebro" del worker.

Pasos de una ejecución (cada uno se guarda en la base de datos antes de seguir):

    fetching_source → writing_file → sending_email → email_sent → completed

El paso sending_email se guarda ANTES de enviar. Si el worker muere después de
guardarlo, al recuperar la ejecución se sabe que el envío quedó en duda.

Política del email: "como mucho una vez". Un email no es transaccional, así que
no existe "exactamente una vez": ante la duda no se reenvía, se marca la
ejecución para revisión (email_delivery_unknown) y se resuelve con un reproceso
explícito (POST /executions con updated_since).

Recuperación de ejecuciones abandonadas (lease caducado), según el último paso:

- sin paso, fetching_source o writing_file: no salió nada → failed, worker_lost.
  La marca de agua no avanzó: la siguiente ejecución recoge esos pedidos.
- sending_email: envío incierto → failed, email_delivery_unknown.
- email_sent: el email salió, solo faltaba cerrarla → completed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.smtp import EnvioEmailError, EnvioEmailInciertoError, message_id
from app.connectors.source_api import ApiOrigenNoDisponibleError
from app.constants import CodigoError, EscenarioOrigen, EstadoEjecucion, Evento, PasoEjecucion
from app.db.models import Ejecucion
from app.db.repositories.ejecucion_repository import EjecucionRepository
from app.domain.exportacion import FicheroExportacion, generar_fichero, preparar_exportacion
from app.domain.pedidos import ContratoOrigenInvalidoError, PedidoOrigen, RespuestaPedidos, validar_pedidos
from app.logging_config import contexto_ejecucion

logger = logging.getLogger(__name__)


class ClienteOrigen(Protocol):
    """Lo que el servicio necesita de la API de origen."""

    def iterar_paginas(
        self,
        desde: datetime,
        escenario: EscenarioOrigen | None = None,
    ) -> Iterator[RespuestaPedidos]: ...


class EnviadorEmail(Protocol):
    """Lo que el servicio necesita para enviar el fichero."""

    def enviar_exportacion(self, execution_id: UUID, fichero: FicheroExportacion) -> str: ...


class AlmacenFicheros(Protocol):
    """Lo que el servicio necesita para guardar el fichero."""

    def guardar(self, fichero: FicheroExportacion) -> Path: ...


class _ReservaPerdidaError(Exception):
    """Otro proceso dio la ejecución por abandonada: este worker debe dejarla."""


def _ahora_utc() -> datetime:
    return datetime.now(UTC)


class ExportacionService:
    """Procesa ejecuciones pendientes y recupera las abandonadas."""

    def __init__(
        self,
        db: Session,
        settings: Settings,
        origen: ClienteOrigen,
        email: EnviadorEmail,
        almacen: AlmacenFicheros,
        reloj: Callable[[], datetime] = _ahora_utc,
    ) -> None:
        self._db = db
        self._repositorio = EjecucionRepository(db)
        self._origen = origen
        self._email = email
        self._almacen = almacen
        self._reloj = reloj
        self._lease_segundos = settings.worker_lease_segundos
        self._desde_inicial = settings.exportacion_desde_inicial

    # ------------------------------------------------------------- recuperación

    def recuperar_abandonadas(self) -> int:
        """Cierra las ejecuciones cuyo worker dejó de renovar la reserva. Devuelve cuántas."""
        ahora = self._reloj()
        abandonadas = self._repositorio.buscar_abandonadas(ahora)
        for ejecucion in abandonadas:
            with contexto_ejecucion(str(ejecucion.id)):
                self._resolver_abandonada(ejecucion, ahora)
        self._db.commit()
        return len(abandonadas)

    def _resolver_abandonada(self, ejecucion: Ejecucion, ahora: datetime) -> None:
        paso = ejecucion.paso
        if paso is PasoEjecucion.EMAIL_ENVIADO:
            ejecucion.estado = EstadoEjecucion.COMPLETADA
            ejecucion.finalizada_en = ahora
            logger.warning(
                "Ejecución abandonada con el email ya enviado: se da por completada",
                extra={"event": Evento.EJECUCION_RECUPERADA, "step": paso, "status": ejecucion.estado},
            )
        elif paso is PasoEjecucion.ENVIANDO_EMAIL:
            self._marcar_fallida(
                ejecucion,
                CodigoError.ENVIO_EMAIL_INCIERTO,
                "el worker se detuvo durante el envío: verificar si el email llegó buscando su Message-ID",
                ahora,
            )
            logger.error(
                "Envío del email incierto: requiere revisión",
                extra={"event": Evento.EMAIL_INCIERTO, "step": paso, "message_id": message_id(ejecucion.id)},
            )
        else:
            self._marcar_fallida(
                ejecucion,
                CodigoError.WORKER_PERDIDO,
                "el worker se detuvo antes de enviar nada: la siguiente ejecución recogerá los pedidos",
                ahora,
            )
            logger.error(
                "Ejecución abandonada por el worker",
                extra={"event": Evento.EJECUCION_RECUPERADA, "step": paso, "status": ejecucion.estado},
            )

    # ------------------------------------------------------------- procesamiento

    def procesar_siguiente(self, worker_id: str) -> bool:
        """Reserva la siguiente ejecución pendiente y la procesa. Devuelve False si no había."""
        ejecucion = self._repositorio.reclamar_pendiente(worker_id, self._reloj(), self._lease_segundos)
        self._db.commit()
        if ejecucion is None:
            return False
        self.ejecutar(ejecucion, worker_id)
        return True

    def ejecutar(self, ejecucion: Ejecucion, worker_id: str) -> None:
        """Ejecuta todos los pasos y deja la ejecución completed o failed. No lanza excepciones."""
        with contexto_ejecucion(str(ejecucion.id)):
            logger.info(
                "Ejecución iniciada",
                extra={
                    "event": Evento.EJECUCION_INICIADA,
                    "trigger": ejecucion.origen,
                    "scenario": ejecucion.escenario,
                    "worker_id": worker_id,
                },
            )
            try:
                self._ejecutar_pasos(ejecucion, worker_id)
            except _ReservaPerdidaError:
                self._db.rollback()
                logger.warning(
                    "La reserva de la ejecución ha caducado y otro proceso la ha cerrado: se abandona",
                    extra={"event": Evento.RESERVA_PERDIDA},
                )
            except ApiOrigenNoDisponibleError as error:
                self._fallar(ejecucion, CodigoError.ORIGEN_NO_DISPONIBLE, error)
            except ContratoOrigenInvalidoError as error:
                self._fallar(ejecucion, CodigoError.CONTRATO_ORIGEN_INVALIDO, error)
            except EnvioEmailInciertoError as error:
                self._fallar(ejecucion, CodigoError.ENVIO_EMAIL_INCIERTO, error)
            except EnvioEmailError as error:
                self._fallar(ejecucion, CodigoError.ENVIO_EMAIL_FALLIDO, error)
            except Exception as error:
                self._fallar(ejecucion, CodigoError.ERROR_INESPERADO, error)

    def _ejecutar_pasos(self, ejecucion: Ejecucion, worker_id: str) -> None:
        marca_agua = self._repositorio.obtener_marca_de_agua()
        # Incremental salvo que se haya pedido un reproceso desde una fecha concreta.
        incremental = ejecucion.desde is None and marca_agua is not None
        ejecucion.desde = ejecucion.desde or marca_agua or self._desde_inicial

        # 1. Consultar la API de origen, validando cada página según llega.
        self._avanzar(ejecucion, worker_id, PasoEjecucion.CONSULTANDO_ORIGEN)
        validos: list[PedidoOrigen] = []
        for pagina in self._origen.iterar_paginas(ejecucion.desde, ejecucion.escenario):
            resultado = validar_pedidos(pagina.items)
            ejecucion.registros_leidos += len(pagina.items)
            ejecucion.registros_invalidos += len(resultado.invalidos)
            validos.extend(resultado.validos)
            for invalido in resultado.invalidos:
                logger.warning(
                    "Pedido descartado: no cumple el contrato",
                    extra={
                        "event": Evento.PEDIDO_INVALIDO,
                        "page": pagina.page,
                        "position": invalido.posicion,
                        "order_id": invalido.id,
                        "reasons": list(invalido.motivos),
                    },
                )
            self._renovar_reserva(ejecucion, worker_id)

        # La API filtra con updated_at >= updated_since: lo que coincide con la marca
        # de agua ya se exportó en la ejecución anterior y no debe repetirse.
        ya_exportados = 0
        if incremental and marca_agua is not None:
            nuevos = [pedido for pedido in validos if pedido.updated_at > marca_agua]
            ya_exportados = len(validos) - len(nuevos)
            validos = nuevos

        exportacion = preparar_exportacion(validos)
        ejecucion.registros_duplicados = exportacion.duplicados + ya_exportados
        ejecucion.registros_exportados = len(exportacion.filas)
        if not exportacion.filas:
            self._completar(ejecucion)
            return

        # 2. Generar y guardar el fichero.
        self._avanzar(ejecucion, worker_id, PasoEjecucion.GENERANDO_FICHERO)
        fichero = generar_fichero(exportacion, ejecucion.id, self._reloj())
        self._almacen.guardar(fichero)
        ejecucion.nombre_fichero = fichero.nombre
        ejecucion.sha256_fichero = fichero.sha256
        ejecucion.marca_agua = exportacion.ultima_actualizacion

        # 3. Enviar el email. El paso se confirma ANTES de enviar (ver docstring del módulo).
        self._avanzar(ejecucion, worker_id, PasoEjecucion.ENVIANDO_EMAIL)
        ejecucion.message_id = self._email.enviar_exportacion(ejecucion.id, fichero)
        ejecucion.email_enviado = True
        self._avanzar(ejecucion, worker_id, PasoEjecucion.EMAIL_ENVIADO)
        logger.info(
            "Email enviado",
            extra={"event": Evento.EMAIL_ENVIADO, "message_id": ejecucion.message_id, "file_name": fichero.nombre},
        )

        self._completar(ejecucion)

    # ------------------------------------------------------------- transiciones

    def _avanzar(self, ejecucion: Ejecucion, worker_id: str, paso: PasoEjecucion) -> None:
        """Pasa al siguiente paso y lo confirma en la base de datos."""
        ejecucion.paso = paso
        self._renovar_reserva(ejecucion, worker_id)

    def _renovar_reserva(self, ejecucion: Ejecucion, worker_id: str) -> None:
        """Amplía el lease y confirma el progreso. Si se perdió la reserva, se abandona."""
        if not self._repositorio.renovar_lease(ejecucion.id, worker_id, self._reloj(), self._lease_segundos):
            raise _ReservaPerdidaError
        self._db.commit()

    def _completar(self, ejecucion: Ejecucion) -> None:
        ahora = self._reloj()
        ejecucion.estado = EstadoEjecucion.COMPLETADA
        ejecucion.finalizada_en = ahora
        self._db.commit()
        logger.info(
            "Ejecución completada",
            extra={
                "event": Evento.EJECUCION_COMPLETADA,
                "records_read": ejecucion.registros_leidos,
                "records_exported": ejecucion.registros_exportados,
                "records_invalid": ejecucion.registros_invalidos,
                "records_duplicated": ejecucion.registros_duplicados,
                "file_name": ejecucion.nombre_fichero,
                "email_sent": ejecucion.email_enviado,
                "duration_ms": _duracion_ms(ejecucion.iniciada_en, ahora),
            },
        )

    def _fallar(self, ejecucion: Ejecucion, codigo: CodigoError, error: Exception) -> None:
        """Deshace lo pendiente y deja la ejecución en failed con su motivo."""
        # Lo ya confirmado (paso, contadores, marca de agua) se conserva; lo pendiente se descarta.
        self._db.rollback()
        ahora = self._reloj()
        self._marcar_fallida(ejecucion, codigo, str(error), ahora)
        self._db.commit()
        logger.error(
            "Ejecución fallida",
            exc_info=error,
            extra={
                "event": Evento.EJECUCION_FALLIDA,
                "error_code": codigo,
                "step": ejecucion.paso,
                "duration_ms": _duracion_ms(ejecucion.iniciada_en, ahora),
            },
        )

    @staticmethod
    def _marcar_fallida(ejecucion: Ejecucion, codigo: CodigoError, mensaje: str, ahora: datetime) -> None:
        ejecucion.estado = EstadoEjecucion.FALLIDA
        ejecucion.codigo_error = codigo
        ejecucion.error = mensaje
        ejecucion.finalizada_en = ahora


def _duracion_ms(inicio: datetime | None, fin: datetime) -> int | None:
    return None if inicio is None else round((fin - inicio).total_seconds() * 1000)
