"""Pruebas de la API HTTP contra PostgreSQL: contrato, 202/409/404/422 y listado."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.api.main import crear_app
from tests.factories import crear_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def cliente(motor: Engine, db: Session) -> Iterator[TestClient]:
    """Cliente HTTP de la API sobre una tabla vacía (el fixture db la vacía)."""
    with TestClient(crear_app(crear_settings(), motor)) as cliente:
        yield cliente


def test_post_registra_la_ejecucion_y_responde_202_sin_esperar(cliente: TestClient):
    respuesta = cliente.post("/executions")

    assert respuesta.status_code == 202
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "pending"
    assert cuerpo["trigger"] == "manual"
    assert respuesta.headers["Location"] == f"/executions/{cuerpo['id']}"
    assert cliente.get(respuesta.headers["Location"]).json()["id"] == cuerpo["id"]


def test_post_con_una_ejecucion_activa_devuelve_409_con_su_id(cliente: TestClient):
    primera = cliente.post("/executions").json()

    respuesta = cliente.post("/executions", json={"trigger": "scheduled"})

    assert respuesta.status_code == 409
    assert respuesta.json()["execution_id"] == primera["id"]


def test_post_con_escenario_y_reproceso_desde_una_fecha(cliente: TestClient):
    peticion = {"trigger": "scheduled", "scenario": "slow", "updated_since": "2026-10-16T02:00:00+02:00"}

    cuerpo = cliente.post("/executions", json=peticion).json()

    assert (cuerpo["trigger"], cuerpo["scenario"]) == ("scheduled", "slow")
    assert cuerpo["updated_since"] == "2026-10-16T00:00:00Z"


@pytest.mark.parametrize(
    "peticion",
    [
        pytest.param({"updated_since": "2026-10-16T00:00:00"}, id="fecha sin zona horaria"),
        pytest.param({"scenario": "explode"}, id="escenario desconocido"),
        pytest.param({"trigger": "manual", "force": True}, id="campo no previsto"),
    ],
)
def test_post_rechaza_peticiones_que_no_cumplen_el_contrato(cliente: TestClient, peticion: dict[str, object]):
    assert cliente.post("/executions", json=peticion).status_code == 422


@pytest.mark.parametrize(
    ("ruta", "codigo"),
    [
        pytest.param("/executions/8e7d2bb2-1dd2-4f4c-9bb7-6b8a2fd73c19", 404, id="no existe"),
        pytest.param("/executions/no-es-un-uuid", 422, id="id no valido"),
    ],
)
def test_get_de_una_ejecucion_que_no_existe(cliente: TestClient, ruta: str, codigo: int):
    assert cliente.get(ruta).status_code == codigo


def test_listado_de_la_mas_reciente_a_la_mas_antigua(cliente: TestClient, db: Session):
    antigua = cliente.post("/executions").json()
    db.execute(text("UPDATE ejecuciones SET estado = 'completed'"))
    db.commit()
    reciente = cliente.post("/executions").json()

    listado = cliente.get("/executions", params={"limit": 1}).json()

    assert [item["id"] for item in listado["items"]] == [reciente["id"]]
    assert cliente.get("/executions", params={"offset": 1}).json()["items"][0]["id"] == antigua["id"]


def test_readiness_con_la_base_de_datos_disponible(cliente: TestClient):
    respuesta = cliente.get("/health/ready")

    assert respuesta.status_code == 200
    assert respuesta.json()["checks"] == {"database": "ok"}
