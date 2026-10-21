"""Health checks sin base de datos: liveness siempre responde; readiness avisa si la BD no está."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api.main import crear_app
from tests.factories import crear_settings

# Puerto 1: nadie escucha, la conexión se rechaza al instante.
MOTOR_SIN_BASE_DE_DATOS = create_engine(
    "postgresql+psycopg://usuario:clave@127.0.0.1:1/nada",
    connect_args={"connect_timeout": 1},
)


def test_liveness_responde_aunque_la_base_de_datos_no_este():
    cliente = TestClient(crear_app(crear_settings(), MOTOR_SIN_BASE_DE_DATOS))

    respuesta = cliente.get("/health/live")

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_readiness_devuelve_503_si_la_base_de_datos_no_responde():
    cliente = TestClient(crear_app(crear_settings(), MOTOR_SIN_BASE_DE_DATOS))

    respuesta = cliente.get("/health/ready")

    assert respuesta.status_code == 503
    assert respuesta.json() == {"status": "unavailable", "checks": {"database": "error"}}
