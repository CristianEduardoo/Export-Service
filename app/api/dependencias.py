"""Dependencias comunes de las rutas."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker


def obtener_sesion(request: Request) -> Iterator[Session]:
    """Una sesión de base de datos por petición, cerrada siempre al terminar."""
    fabrica: sessionmaker[Session] = request.app.state.fabrica_sesiones
    with fabrica() as db:
        yield db


SesionDb = Annotated[Session, Depends(obtener_sesion)]
