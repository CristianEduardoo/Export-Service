"""Pruebas de los logs JSON: campos obligatorios, execution_id y errores."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.constants import Servicio
from app.logging_config import FormateadorJson, configurar_logging, contexto_ejecucion

CAMPOS_OBLIGATORIOS = {"timestamp", "level", "service", "logger", "event", "message", "execution_id"}


def _formatear(mensaje: str = "hola", **extra: object) -> dict[str, Any]:
    """Formatea un log como lo haría el handler real y devuelve el JSON ya leído."""
    registro = logging.LogRecord("prueba", logging.INFO, __file__, 1, mensaje, None, None)
    for clave, valor in extra.items():
        setattr(registro, clave, valor)
    resultado: dict[str, Any] = json.loads(FormateadorJson(Servicio.WORKER).format(registro))
    return resultado


@pytest.fixture
def logging_aislado() -> Iterator[None]:
    """configurar_logging cambia el logging global: se restaura al terminar."""
    raiz = logging.getLogger()
    handlers, nivel = raiz.handlers[:], raiz.level
    yield
    raiz.handlers, raiz.level = handlers, nivel


def test_log_contiene_los_campos_obligatorios():
    datos = _formatear("Worker arrancado", event="worker_started")

    assert datos.keys() >= CAMPOS_OBLIGATORIOS
    assert datos["level"] == "INFO"
    assert datos["service"] == "worker"
    assert datos["event"] == "worker_started"
    assert datos["message"] == "Worker arrancado"


def test_execution_id_solo_dentro_del_contexto():
    assert _formatear()["execution_id"] is None
    with contexto_ejecucion("ejecucion-1"):
        assert _formatear()["execution_id"] == "ejecucion-1"
    assert _formatear()["execution_id"] is None


def test_execution_id_se_limpia_aunque_haya_un_error():
    with pytest.raises(RuntimeError), contexto_ejecucion("ejecucion-1"):
        raise RuntimeError("fallo")

    assert _formatear()["execution_id"] is None


def test_campos_extra_incluidos_y_no_serializables_como_texto():
    datos = _formatear(
        records_read=5,
        amount=Decimal("129.95"),
        updated_since=datetime(2026, 10, 1, 2, 0, tzinfo=timezone(timedelta(hours=2))),
        color_message="\u001b[36mruido de uvicorn\u001b[0m",
    )

    assert datos["records_read"] == 5
    assert datos["amount"] == "129.95"
    assert datos["updated_since"] == "2026-10-01T00:00:00Z"
    assert "color_message" not in datos


def test_excepcion_incluye_tipo_mensaje_y_stack_trace():
    registro = logging.LogRecord("prueba", logging.ERROR, __file__, 1, "Fallo", None, None)
    try:
        raise ValueError("dato incorrecto")
    except ValueError:
        registro.exc_info = sys.exc_info()

    error = json.loads(FormateadorJson(Servicio.API).format(registro))["error"]

    assert error["type"] == "ValueError"
    assert error["message"] == "dato incorrecto"
    assert "Traceback" in error["stack_trace"]


@pytest.mark.usefixtures("logging_aislado")
def test_configurar_logging_emite_json_por_stdout(capsys: pytest.CaptureFixture[str]):
    configurar_logging(Servicio.API, "INFO")

    logging.getLogger("uvicorn.error").info("Servidor arrancado")

    linea = capsys.readouterr().out.strip()
    assert json.loads(linea)["service"] == "api"
