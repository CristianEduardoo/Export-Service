"""Casos de uso de la API: crear, consultar y listar ejecuciones.

La API no ejecuta la exportación: solo la registra como pendiente. El worker la
recoge y la procesa (ver exportacion_service.py).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.constants import EscenarioOrigen, Evento, OrigenEjecucion
from app.db.models import Ejecucion
from app.db.repositories.ejecucion_repository import EjecucionRepository
from app.logging_config import contexto_ejecucion

logger = logging.getLogger(__name__)


def _ahora_utc() -> datetime:
    return datetime.now(UTC)


class EjecucionesService:
    """Operaciones que expone la API sobre las ejecuciones."""

    def __init__(self, db: Session, reloj: Callable[[], datetime] = _ahora_utc) -> None:
        self._db = db
        self._repositorio = EjecucionRepository(db)
        self._reloj = reloj

    def crear(
        self,
        origen: OrigenEjecucion,
        escenario: EscenarioOrigen | None = None,
        desde: datetime | None = None,
    ) -> Ejecucion:
        """Registra una ejecución pendiente.

        Lanza EjecucionActivaExistenteError si ya hay una pendiente o en curso.
        """
        ejecucion = self._repositorio.crear(origen, self._reloj(), escenario=escenario, desde=desde)
        self._db.commit()
        with contexto_ejecucion(str(ejecucion.id)):
            logger.info(
                "Ejecución creada",
                extra={
                    "event": Evento.EJECUCION_CREADA,
                    "trigger": origen,
                    "scenario": escenario,
                    "updated_since": desde,
                },
            )
        return ejecucion

    def obtener(self, ejecucion_id: UUID) -> Ejecucion | None:
        return self._repositorio.obtener(ejecucion_id)

    def listar(self, limite: int, desplazamiento: int = 0) -> Sequence[Ejecucion]:
        return self._repositorio.listar(limite, desplazamiento)
