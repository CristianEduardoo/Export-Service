"""Conexión a la base de datos: un motor por proceso y sesiones de vida corta."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.constants import TIMEOUT_CONEXION_BD_SEGUNDOS


def crear_motor(url: URL | str) -> Engine:
    """Motor con pool de conexiones.

    pool_pre_ping descarta conexiones caídas antes de usarlas y connect_timeout
    evita quedarse esperando indefinidamente si PostgreSQL no responde.
    """
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": TIMEOUT_CONEXION_BD_SEGUNDOS})


def crear_fabrica_sesiones(motor: Engine) -> sessionmaker[Session]:
    """expire_on_commit=False: los objetos siguen legibles después del commit."""
    return sessionmaker(bind=motor, expire_on_commit=False)


@contextmanager
def sesion(fabrica: sessionmaker[Session]) -> Iterator[Session]:
    """Abre una sesión, confirma al salir y deshace los cambios si hay un error."""
    db = fabrica()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
