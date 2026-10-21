"""Acceso a datos de las ejecuciones.

Las garantías de concurrencia se apoyan en PostgreSQL, no en el código:

- Una sola ejecución activa: índice único parcial (ver models.py). Si dos
  peticiones llegan a la vez, una inserta y la otra recibe el error.
- Un worker por ejecución: SELECT ... FOR UPDATE SKIP LOCKED. Si dos workers
  buscan trabajo a la vez, el segundo se salta la fila que el primero ya ha
  bloqueado en lugar de esperar o cogerla también.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import ESTADOS_ACTIVOS, EscenarioOrigen, EstadoEjecucion, OrigenEjecucion
from app.db.models import Ejecucion


class EjecucionActivaExistenteError(Exception):
    """Ya hay una ejecución pendiente o en curso."""

    def __init__(self, ejecucion_activa_id: UUID) -> None:
        super().__init__(f"ya hay una ejecución activa: {ejecucion_activa_id}")
        self.ejecucion_activa_id = ejecucion_activa_id


class EjecucionRepository:
    """Operaciones de base de datos sobre la tabla de ejecuciones."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def crear(
        self,
        origen: OrigenEjecucion,
        ahora: datetime,
        escenario: EscenarioOrigen | None = None,
    ) -> Ejecucion:
        """Crea una ejecución pendiente.

        Lanza EjecucionActivaExistenteError si ya hay una activa. Se usa un
        savepoint para que el error no invalide el resto de la transacción.
        """
        ejecucion = Ejecucion(
            estado=EstadoEjecucion.PENDIENTE,
            origen=origen,
            escenario=escenario,
            creada_en=ahora,
        )
        try:
            with self._db.begin_nested():
                self._db.add(ejecucion)
        except IntegrityError as error:
            activa = self.obtener_activa()
            if activa is None:
                raise
            raise EjecucionActivaExistenteError(activa.id) from error
        return ejecucion

    def obtener(self, ejecucion_id: UUID) -> Ejecucion | None:
        return self._db.get(Ejecucion, ejecucion_id)

    def obtener_activa(self) -> Ejecucion | None:
        sentencia = select(Ejecucion).where(Ejecucion.estado.in_(sorted(ESTADOS_ACTIVOS))).limit(1)
        return self._db.scalars(sentencia).first()

    def listar(self, limite: int, desplazamiento: int = 0) -> Sequence[Ejecucion]:
        """Ejecuciones de la más reciente a la más antigua."""
        sentencia = (
            select(Ejecucion).order_by(Ejecucion.creada_en.desc(), Ejecucion.id).limit(limite).offset(desplazamiento)
        )
        return self._db.scalars(sentencia).all()

    def reclamar_pendiente(self, worker_id: str, ahora: datetime, lease_segundos: int) -> Ejecucion | None:
        """Reserva la ejecución pendiente más antigua para este worker.

        FOR UPDATE SKIP LOCKED: si otro worker ya la tiene bloqueada, se salta en
        lugar de esperar. La reserva caduca en lease_hasta si no se renueva.
        """
        sentencia = (
            select(Ejecucion)
            .where(Ejecucion.estado == EstadoEjecucion.PENDIENTE)
            .order_by(Ejecucion.creada_en)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        ejecucion = self._db.scalars(sentencia).first()
        if ejecucion is None:
            return None

        ejecucion.estado = EstadoEjecucion.EN_CURSO
        ejecucion.iniciada_en = ahora
        ejecucion.worker_id = worker_id
        ejecucion.lease_hasta = ahora + timedelta(seconds=lease_segundos)
        self._db.flush()
        return ejecucion

    def renovar_lease(self, ejecucion_id: UUID, worker_id: str, ahora: datetime, lease_segundos: int) -> bool:
        """Amplía la reserva. Devuelve False si este worker ya no es su dueño."""
        sentencia = (
            update(Ejecucion)
            .where(
                Ejecucion.id == ejecucion_id,
                Ejecucion.worker_id == worker_id,
                Ejecucion.estado == EstadoEjecucion.EN_CURSO,
            )
            .values(lease_hasta=ahora + timedelta(seconds=lease_segundos))
            .returning(Ejecucion.id)
        )
        return self._db.execute(sentencia).scalar_one_or_none() is not None

    def buscar_abandonadas(self, ahora: datetime) -> Sequence[Ejecucion]:
        """Ejecuciones en curso cuyo worker dejó de renovar la reserva (se cayó)."""
        sentencia = (
            select(Ejecucion)
            .where(Ejecucion.estado == EstadoEjecucion.EN_CURSO, Ejecucion.lease_hasta < ahora)
            .with_for_update(skip_locked=True)
        )
        return self._db.scalars(sentencia).all()

    def obtener_marca_de_agua(self) -> datetime | None:
        """updated_at más reciente exportado por una ejecución completada."""
        sentencia = select(func.max(Ejecucion.marca_agua)).where(Ejecucion.estado == EstadoEjecucion.COMPLETADA)
        return self._db.scalar(sentencia)
