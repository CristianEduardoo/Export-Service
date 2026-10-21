"""Pruebas del repositorio contra PostgreSQL: concurrencia, lease y marca de agua."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import CodigoError, EstadoEjecucion, OrigenEjecucion
from app.db.models import Base
from app.db.repositories.ejecucion_repository import EjecucionActivaExistenteError, EjecucionRepository

pytestmark = pytest.mark.integration

AHORA = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
LEASE = 300


def _finalizar(db: Session, repositorio: EjecucionRepository, marca_agua: datetime | None = None) -> None:
    """Crea una ejecución y la deja completada (deja de estar activa)."""
    ejecucion = repositorio.crear(OrigenEjecucion.MANUAL, AHORA)
    ejecucion.estado = EstadoEjecucion.COMPLETADA
    ejecucion.marca_agua = marca_agua
    db.commit()


def test_el_modelo_coincide_con_la_migracion(motor: Engine):
    """Si alguien cambia models.py y olvida la migración, este test lo detecta."""
    with motor.connect() as conexion:
        diferencias = compare_metadata(MigrationContext.configure(conexion), Base.metadata)

    # El índice parcial sobre una expresión se valida con su propio test de comportamiento.
    relevantes = [d for d in diferencias if "ux_ejecuciones_una_activa" not in str(d)]
    assert relevantes == []


def test_crear_ejecucion_pendiente(db: Session):
    ejecucion = EjecucionRepository(db).crear(OrigenEjecucion.PROGRAMADA, AHORA)
    db.commit()

    assert ejecucion.estado is EstadoEjecucion.PENDIENTE
    assert ejecucion.registros_leidos == 0
    assert ejecucion.email_enviado is False


def test_solo_puede_haber_una_ejecucion_activa(db: Session):
    repositorio = EjecucionRepository(db)
    primera = repositorio.crear(OrigenEjecucion.MANUAL, AHORA)
    db.commit()

    with pytest.raises(EjecucionActivaExistenteError) as error:
        repositorio.crear(OrigenEjecucion.PROGRAMADA, AHORA)

    assert error.value.ejecucion_activa_id == primera.id


def test_tras_terminar_se_puede_crear_otra(db: Session):
    repositorio = EjecucionRepository(db)
    _finalizar(db, repositorio)

    repositorio.crear(OrigenEjecucion.MANUAL, AHORA)
    db.commit()

    assert len(repositorio.listar(limite=10)) == 2


def test_dos_workers_nunca_reclaman_la_misma_ejecucion(motor: Engine, db: Session):
    EjecucionRepository(db).crear(OrigenEjecucion.MANUAL, AHORA)
    db.commit()

    with Session(motor, expire_on_commit=False) as worker_a, Session(motor, expire_on_commit=False) as worker_b:
        # worker_a reclama y mantiene la fila bloqueada (su transacción sigue abierta).
        reclamada_a = EjecucionRepository(worker_a).reclamar_pendiente("worker-a", AHORA, LEASE)
        reclamada_b = EjecucionRepository(worker_b).reclamar_pendiente("worker-b", AHORA, LEASE)
        worker_a.commit()

    assert reclamada_a is not None
    assert reclamada_a.estado is EstadoEjecucion.EN_CURSO
    assert reclamada_a.lease_hasta == AHORA + timedelta(seconds=LEASE)
    assert reclamada_b is None


def test_solo_el_worker_duenio_renueva_el_lease(db: Session):
    repositorio = EjecucionRepository(db)
    repositorio.crear(OrigenEjecucion.MANUAL, AHORA)
    ejecucion = repositorio.reclamar_pendiente("worker-a", AHORA, LEASE)
    assert ejecucion is not None
    despues = AHORA + timedelta(seconds=60)

    assert repositorio.renovar_lease(ejecucion.id, "worker-a", despues, LEASE) is True
    assert repositorio.renovar_lease(ejecucion.id, "worker-b", despues, LEASE) is False


def test_detecta_ejecuciones_abandonadas_por_lease_caducado(db: Session):
    repositorio = EjecucionRepository(db)
    repositorio.crear(OrigenEjecucion.MANUAL, AHORA)
    ejecucion = repositorio.reclamar_pendiente("worker-a", AHORA, LEASE)
    db.commit()

    assert repositorio.buscar_abandonadas(AHORA + timedelta(seconds=LEASE - 1)) == []
    assert repositorio.buscar_abandonadas(AHORA + timedelta(seconds=LEASE + 1)) == [ejecucion]


def test_marca_de_agua_solo_de_ejecuciones_completadas(db: Session):
    repositorio = EjecucionRepository(db)
    assert repositorio.obtener_marca_de_agua() is None

    _finalizar(db, repositorio, marca_agua=datetime(2026, 10, 16, 8, 30, tzinfo=UTC))
    _finalizar(db, repositorio, marca_agua=datetime(2026, 10, 16, 9, 0, tzinfo=UTC))
    fallida = repositorio.crear(OrigenEjecucion.MANUAL, AHORA)
    fallida.estado = EstadoEjecucion.FALLIDA
    fallida.codigo_error = CodigoError.ENVIO_EMAIL_FALLIDO
    fallida.marca_agua = datetime(2026, 10, 17, tzinfo=UTC)
    db.commit()

    # Un envío fallido no avanza la marca: esos pedidos se vuelven a exportar.
    assert repositorio.obtener_marca_de_agua() == datetime(2026, 10, 16, 9, 0, tzinfo=UTC)

    incierta = repositorio.crear(OrigenEjecucion.MANUAL, AHORA)
    incierta.estado = EstadoEjecucion.FALLIDA
    incierta.codigo_error = CodigoError.ENVIO_EMAIL_INCIERTO
    incierta.marca_agua = datetime(2026, 10, 18, tzinfo=UTC)
    db.commit()

    # Un envío incierto sí la avanza: como mucho una vez, sin duplicados.
    assert repositorio.obtener_marca_de_agua() == datetime(2026, 10, 18, tzinfo=UTC)


def test_la_base_de_datos_rechaza_un_estado_desconocido(db: Session):
    """Defensa en profundidad: la restricción CHECK protege aunque se salte el código."""
    with pytest.raises(IntegrityError, match='constraint "ck_ejecuciones_estado"'):
        db.execute(
            text(
                "INSERT INTO ejecuciones (id, estado, origen, creada_en, registros_leidos, registros_exportados, "
                "registros_invalidos, registros_duplicados, email_enviado) "
                "VALUES (gen_random_uuid(), 'paused', 'manual', now(), 0, 0, 0, 0, false)",
            ),
        )
