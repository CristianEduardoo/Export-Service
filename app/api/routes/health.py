"""Health checks.

- live: el proceso responde. Si falla, el orquestador reinicia el contenedor.
- ready: puede atender peticiones (la base de datos responde). Si falla, se deja
  de enviarle tráfico, pero no se reinicia.

Readiness NO comprueba la API de origen ni el SMTP: si caen, nuestra API sigue
pudiendo registrar y consultar ejecuciones; lo que falla son las ejecuciones, y
eso queda registrado en cada una.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import SaludRespuesta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=SaludRespuesta, summary="Liveness: el proceso responde")
def vivo() -> SaludRespuesta:
    return SaludRespuesta(status="ok")


@router.get(
    "/ready",
    response_model=SaludRespuesta,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SaludRespuesta}},
    summary="Readiness: la base de datos responde",
)
def listo(request: Request, response: Response) -> SaludRespuesta:
    motor: Engine = request.app.state.motor
    try:
        with motor.connect() as conexion:
            conexion.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("Readiness: la base de datos no responde", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return SaludRespuesta(status="unavailable", checks={"database": "error"})
    return SaludRespuesta(status="ok", checks={"database": "ok"})
