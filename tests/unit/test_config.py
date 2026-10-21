"""Pruebas de la configuración: lo que se acepta, lo que se rechaza y por qué."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.factories import ENTORNO_VALIDO


@pytest.fixture(autouse=True)
def entorno_valido(monkeypatch: pytest.MonkeyPatch) -> None:
    """Carga la configuración mínima en el entorno de cada prueba."""
    for clave, valor in ENTORNO_VALIDO.items():
        monkeypatch.setenv(clave, valor)


def _cargar() -> Settings:
    """Lee solo del entorno: el .env local no debe influir en las pruebas."""
    return Settings(_env_file=None)


@pytest.mark.parametrize("variable", ["SOURCE_API_URL", "EXPORT_RECIPIENT", "POSTGRES_PASSWORD"])
def test_falta_variable_obligatoria(monkeypatch: pytest.MonkeyPatch, variable: str):
    monkeypatch.delenv(variable)

    with pytest.raises(ValidationError, match=variable):
        _cargar()


def test_fecha_inicial_sin_zona_horaria(monkeypatch: pytest.MonkeyPatch):
    """La trampa del mock: una fecha sin zona provoca un 500 en la API de origen."""
    monkeypatch.setenv("EXPORT_INITIAL_UPDATED_SINCE", "2026-10-01T00:00:00")

    with pytest.raises(ValidationError, match="timezone"):
        _cargar()


def test_fecha_inicial_se_convierte_a_utc(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EXPORT_INITIAL_UPDATED_SINCE", "2026-10-01T02:00:00+02:00")

    assert _cargar().exportacion_desde_inicial == datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    assert _cargar().exportacion_desde_inicial.utcoffset() is not None


def test_varios_destinatarios_separados_por_comas(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EXPORT_RECIPIENT", " a@example.test , b@example.test,, ")

    assert _cargar().exportacion_destinatarios == ("a@example.test", "b@example.test")


@pytest.mark.parametrize(
    ("valor", "mensaje"),
    [
        ("a@example.test, esto-no-es-un-email", "formato no válido"),
        (" , ", "al menos un destinatario"),
    ],
)
def test_destinatarios_invalidos(monkeypatch: pytest.MonkeyPatch, valor: str, mensaje: str):
    monkeypatch.setenv("EXPORT_RECIPIENT", valor)

    with pytest.raises(ValidationError, match=mensaje):
        _cargar()


@pytest.mark.parametrize("tamano", ["0", "501"])
def test_registros_por_pagina_fuera_del_contrato(monkeypatch: pytest.MonkeyPatch, tamano: str):
    """El contrato de la API admite entre 1 y 500 registros por página."""
    monkeypatch.setenv("SOURCE_API_PAGE_SIZE", tamano)

    with pytest.raises(ValidationError, match="SOURCE_API_PAGE_SIZE"):
        _cargar()


def test_lease_no_mayor_que_intervalo_de_sondeo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "30")

    with pytest.raises(ValidationError, match="WORKER_LEASE_SECONDS"):
        _cargar()


def test_contrasenas_ocultas_al_mostrar_la_configuracion(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SMTP_PASSWORD", "secreto-smtp")

    texto = repr(_cargar())

    assert "solo_desarrollo_local" not in texto
    assert "secreto-smtp" not in texto


def test_url_base_datos_admite_caracteres_especiales_en_la_contrasena(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")

    url = _cargar().url_base_datos

    assert url.password == "p@ss:w/rd"
    assert url.host == "postgres"
    assert url.database == "export_service"
    assert "p@ss:w/rd" not in str(url)
