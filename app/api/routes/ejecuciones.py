"""POST/GET /executions.

Las rutas solo traducen HTTP: leen la petición, llaman al servicio y construyen
la respuesta. La lógica vive en app/services.

Decisión: la ejecución es ASÍNCRONA. POST registra la ejecución como pending y
responde 202 al momento; el worker la procesa. Así la API nunca queda bloqueada
por una API de origen lenta, y API y worker escalan por separado.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.dependencias import SesionDb
from app.api.schemas import ConflictoRespuesta, CrearEjecucionPeticion, EjecucionRespuesta, ListaEjecuciones
from app.constants import LIMITE_LISTADO_MAXIMO, LIMITE_LISTADO_POR_DEFECTO, Evento
from app.db.repositories.ejecucion_repository import EjecucionActivaExistenteError
from app.services.ejecuciones_service import EjecucionesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/executions", tags=["executions"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EjecucionRespuesta,
    responses={status.HTTP_409_CONFLICT: {"model": ConflictoRespuesta}},
    summary="Lanza una ejecución (asíncrona)",
)
def crear_ejecucion(
    db: SesionDb,
    response: Response,
    peticion: Annotated[CrearEjecucionPeticion | None, Body()] = None,
) -> EjecucionRespuesta | JSONResponse:
    datos = peticion or CrearEjecucionPeticion()
    try:
        ejecucion = EjecucionesService(db).crear(datos.trigger, datos.scenario, datos.updated_since)
    except EjecucionActivaExistenteError as error:
        logger.info(
            "Ejecución rechazada: ya hay una activa",
            extra={"event": Evento.EJECUCION_RECHAZADA, "active_execution_id": error.ejecucion_activa_id},
        )
        conflicto = ConflictoRespuesta(detail=str(error), execution_id=error.ejecucion_activa_id)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflicto.model_dump(mode="json"))

    response.headers["Location"] = f"/executions/{ejecucion.id}"
    return EjecucionRespuesta.model_validate(ejecucion)


@router.get("", response_model=ListaEjecuciones, summary="Lista las ejecuciones, de la más reciente a la más antigua")
def listar_ejecuciones(
    db: SesionDb,
    limit: Annotated[int, Query(ge=1, le=LIMITE_LISTADO_MAXIMO)] = LIMITE_LISTADO_POR_DEFECTO,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListaEjecuciones:
    ejecuciones = EjecucionesService(db).listar(limit, offset)
    return ListaEjecuciones(
        items=[EjecucionRespuesta.model_validate(ejecucion) for ejecucion in ejecuciones],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{execution_id}",
    response_model=EjecucionRespuesta,
    responses={status.HTTP_404_NOT_FOUND: {"description": "La ejecución no existe"}},
    summary="Detalle de una ejecución",
)
def obtener_ejecucion(db: SesionDb, execution_id: UUID) -> EjecucionRespuesta:
    ejecucion = EjecucionesService(db).obtener(execution_id)
    if ejecucion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ejecución no encontrada")
    return EjecucionRespuesta.model_validate(ejecucion)
