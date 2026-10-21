"""Pruebas de la máquina de estados de una ejecución.

PostgreSQL real (las transiciones se confirman de verdad) y dobles solo para lo
externo: la API de origen, el email y el reloj.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.connectors.almacen_ficheros import AlmacenFicherosLocal
from app.connectors.smtp import EnvioEmailError, EnvioEmailInciertoError, message_id
from app.connectors.source_api import ApiOrigenNoDisponibleError
from app.constants import CodigoError, EscenarioOrigen, EstadoEjecucion, OrigenEjecucion, PasoEjecucion
from app.db.models import Ejecucion
from app.db.repositories.ejecucion_repository import EjecucionRepository
from app.domain.exportacion import FicheroExportacion
from app.domain.pedidos import ContratoOrigenInvalidoError, RespuestaPedidos, validar_respuesta
from app.services.ejecuciones_service import EjecucionesService
from app.services.exportacion_service import ExportacionService
from tests.factories import crear_settings, sobres

pytestmark = pytest.mark.integration

AHORA = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
WORKER = "worker-test"
CASUISTICAS = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "source_orders_casuisticas.json").read_text(),
    parse_float=Decimal,
)


class OrigenFalso:
    """API de origen que filtra por updated_since (>=) como la real, o falla."""

    def __init__(self, items: list[dict[str, Any]], error: Exception | None = None) -> None:
        self._items = items
        self._error = error
        self.desde_recibidos: list[datetime] = []

    def iterar_paginas(
        self,
        desde: datetime,
        escenario: EscenarioOrigen | None = None,
    ) -> Iterator[RespuestaPedidos]:
        self.desde_recibidos.append(desde)
        if self._error is not None:
            raise self._error
        filtrados = [item for item in self._items if datetime.fromisoformat(item["updated_at"]) >= desde]
        for sobre in sobres(filtrados, tamano=5):
            yield validar_respuesta(sobre)


class EmailFalso:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.enviados: list[FicheroExportacion] = []

    def enviar_exportacion(self, execution_id: UUID, fichero: FicheroExportacion) -> str:
        if self._error is not None:
            raise self._error
        self.enviados.append(fichero)
        return message_id(execution_id)


def _servicio(
    db: Session,
    directorio: Path,
    origen: OrigenFalso,
    email: EmailFalso,
    ahora: datetime = AHORA,
) -> ExportacionService:
    almacen = AlmacenFicherosLocal(directorio)
    return ExportacionService(db, crear_settings(), origen, email, almacen, reloj=lambda: ahora)


def _lanzar(db: Session, servicio: ExportacionService, desde: datetime | None = None) -> Ejecucion:
    """Crea una ejecución (como la API) y la procesa (como el worker)."""
    ejecucion = EjecucionesService(db, reloj=lambda: AHORA).crear(OrigenEjecucion.MANUAL, desde=desde)
    assert servicio.procesar_siguiente(WORKER) is True
    db.refresh(ejecucion)
    return ejecucion


def test_ejecucion_completa_con_las_casuisticas(db: Session, tmp_path: Path):
    email = EmailFalso()

    ejecucion = _lanzar(db, _servicio(db, tmp_path, OrigenFalso(CASUISTICAS), email))

    assert (ejecucion.estado, ejecucion.paso) == (EstadoEjecucion.COMPLETADA, PasoEjecucion.EMAIL_ENVIADO)
    assert ejecucion.desde == datetime(2026, 10, 1, tzinfo=UTC)
    contadores = (
        ejecucion.registros_leidos,
        ejecucion.registros_exportados,
        ejecucion.registros_invalidos,
        ejecucion.registros_duplicados,
    )
    assert contadores == (13, 4, 7, 2)
    assert ejecucion.email_enviado is True
    assert ejecucion.message_id == message_id(ejecucion.id)
    assert ejecucion.marca_agua == datetime(2026, 10, 16, 8, 30, tzinfo=UTC)
    assert ejecucion.nombre_fichero is not None
    assert (tmp_path / ejecucion.nombre_fichero).exists()
    assert len(email.enviados) == 1


def test_siguiente_ejecucion_es_incremental_y_sin_novedades_no_envia_email(db: Session, tmp_path: Path):
    email = EmailFalso()
    origen = OrigenFalso(CASUISTICAS)
    servicio = _servicio(db, tmp_path, origen, email)
    _lanzar(db, servicio)

    segunda = _lanzar(db, servicio)

    assert origen.desde_recibidos[-1] == datetime(2026, 10, 16, 8, 30, tzinfo=UTC)
    assert segunda.estado is EstadoEjecucion.COMPLETADA
    # ord-1004 llega otra vez (updated_at == marca de agua) pero ya se exportó.
    assert (segunda.registros_exportados, segunda.registros_duplicados) == (0, 1)
    assert segunda.email_enviado is False
    assert len(email.enviados) == 1


def test_reproceso_explicito_desde_una_fecha(db: Session, tmp_path: Path):
    email = EmailFalso()
    origen = OrigenFalso(CASUISTICAS)
    servicio = _servicio(db, tmp_path, origen, email)
    _lanzar(db, servicio)

    reproceso = _lanzar(db, servicio, desde=datetime(2026, 10, 16, tzinfo=UTC))

    assert origen.desde_recibidos[-1] == datetime(2026, 10, 16, tzinfo=UTC)
    assert reproceso.registros_exportados == 4
    assert len(email.enviados) == 2


@pytest.mark.parametrize(
    ("origen", "email", "codigo"),
    [
        pytest.param(
            OrigenFalso([], ApiOrigenNoDisponibleError("HTTP 503 tras 4 intentos")),
            EmailFalso(),
            CodigoError.ORIGEN_NO_DISPONIBLE,
            id="api no disponible",
        ),
        pytest.param(
            OrigenFalso([], ContratoOrigenInvalidoError("has_next: Field required")),
            EmailFalso(),
            CodigoError.CONTRATO_ORIGEN_INVALIDO,
            id="sobre invalido",
        ),
        pytest.param(
            OrigenFalso(CASUISTICAS),
            EmailFalso(EnvioEmailError("rechazo definitivo SMTP 554")),
            CodigoError.ENVIO_EMAIL_FALLIDO,
            id="email rechazado",
        ),
    ],
)
def test_fallos_sin_envio_no_avanzan_la_marca_de_agua(
    db: Session,
    tmp_path: Path,
    origen: OrigenFalso,
    email: EmailFalso,
    codigo: CodigoError,
):
    ejecucion = _lanzar(db, _servicio(db, tmp_path, origen, email))

    assert ejecucion.estado is EstadoEjecucion.FALLIDA
    assert ejecucion.codigo_error is codigo
    assert ejecucion.error
    assert EjecucionRepository(db).obtener_marca_de_agua() is None


def test_envio_incierto_queda_para_revision_y_avanza_la_marca_de_agua(db: Session, tmp_path: Path):
    """Como mucho una vez: la siguiente ejecución no reenvía esos pedidos."""
    email = EmailFalso(EnvioEmailInciertoError("la conexión SMTP se cortó durante el envío"))
    servicio = _servicio(db, tmp_path, OrigenFalso(CASUISTICAS), email)

    ejecucion = _lanzar(db, servicio)

    assert ejecucion.estado is EstadoEjecucion.FALLIDA
    assert ejecucion.codigo_error is CodigoError.ENVIO_EMAIL_INCIERTO
    assert ejecucion.paso is PasoEjecucion.ENVIANDO_EMAIL
    assert EjecucionRepository(db).obtener_marca_de_agua() == datetime(2026, 10, 16, 8, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("paso", "estado", "codigo"),
    [
        pytest.param(PasoEjecucion.CONSULTANDO_ORIGEN, EstadoEjecucion.FALLIDA, CodigoError.WORKER_PERDIDO, id="antes"),
        pytest.param(
            PasoEjecucion.ENVIANDO_EMAIL,
            EstadoEjecucion.FALLIDA,
            CodigoError.ENVIO_EMAIL_INCIERTO,
            id="durante el envio",
        ),
        pytest.param(PasoEjecucion.EMAIL_ENVIADO, EstadoEjecucion.COMPLETADA, None, id="despues del envio"),
    ],
)
def test_recupera_ejecuciones_abandonadas_segun_el_paso(
    db: Session,
    tmp_path: Path,
    paso: PasoEjecucion,
    estado: EstadoEjecucion,
    codigo: CodigoError | None,
):
    """El worker murió en ese paso: su reserva caduca y otro proceso la cierra."""
    repositorio = EjecucionRepository(db)
    repositorio.crear(OrigenEjecucion.PROGRAMADA, AHORA)
    abandonada = repositorio.reclamar_pendiente("worker-caido", AHORA, crear_settings().worker_lease_segundos)
    assert abandonada is not None
    abandonada.paso = paso
    db.commit()
    despues_del_lease = AHORA + timedelta(seconds=crear_settings().worker_lease_segundos + 1)
    servicio = _servicio(db, tmp_path, OrigenFalso([]), EmailFalso(), ahora=despues_del_lease)

    assert servicio.recuperar_abandonadas() == 1

    db.refresh(abandonada)
    assert (abandonada.estado, abandonada.codigo_error) == (estado, codigo)
    assert repositorio.obtener_activa() is None
