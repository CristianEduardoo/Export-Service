"""Base de datos para los tests de integración.

Usan un PostgreSQL real y separado del de la aplicación (los tests vacían la
tabla). Arranque en local:

    docker run --rm -d --name pg-tests -p 5433:5432 -e POSTGRES_PASSWORD=tests \
        -e POSTGRES_DB=export_service_test postgres:17

Si no está disponible: en local los tests se saltan con un aviso; en el CI
(variable CI definida) fallan, para que la pipeline nunca salga en verde sin
haberlos ejecutado.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

URL_POR_DEFECTO = "postgresql+psycopg://postgres:tests@localhost:5433/export_service_test"
RAIZ_PROYECTO = Path(__file__).parents[2]


@pytest.fixture(scope="session")
def motor() -> Iterator[Engine]:
    """Conecta, aplica las migraciones desde cero y devuelve el motor."""
    url = os.environ.get("TEST_DATABASE_URL", URL_POR_DEFECTO)
    motor = create_engine(url, pool_pre_ping=True)
    try:
        with motor.connect():
            pass
    except OperationalError as error:
        motor.dispose()
        if os.environ.get("CI"):
            pytest.fail(f"PostgreSQL de tests no disponible en el CI: {error}")
        pytest.skip("PostgreSQL de tests no disponible: arráncalo con el comando de tests/integration/conftest.py")

    configuracion = Config(str(RAIZ_PROYECTO / "alembic.ini"))
    configuracion.attributes["url"] = url
    # Bajar y subir todas las migraciones prueba también el downgrade.
    command.downgrade(configuracion, "base")
    command.upgrade(configuracion, "head")
    yield motor
    motor.dispose()


@pytest.fixture
def db(motor: Engine) -> Iterator[Session]:
    """Sesión sobre una tabla vacía en cada test."""
    with motor.begin() as conexion:
        conexion.execute(text("TRUNCATE ejecuciones"))
    sesion = Session(motor, expire_on_commit=False)
    yield sesion
    sesion.rollback()
    sesion.close()
