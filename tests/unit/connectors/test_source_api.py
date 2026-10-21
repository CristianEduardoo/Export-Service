"""Pruebas del cliente de la API de origen: paginación, timeout y reintentos.

Se usa un transport falso de httpx: sin red, sin Docker y sin esperas reales.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.connectors.source_api import ApiOrigenNoDisponibleError, ClienteApiOrigen
from app.constants import EscenarioOrigen
from app.domain.pedidos import ContratoOrigenInvalidoError, RespuestaPedidos
from tests.factories import crear_settings

DESDE = datetime(2026, 10, 1, tzinfo=UTC)
PEDIDO = {
    "id": "ord-1001",
    "customer_id": "customer-42",
    "amount": 129.95,
    "currency": "EUR",
    "updated_at": "2026-10-16T07:45:00Z",
}

Manejador = Callable[[httpx.Request], httpx.Response]


def _pagina(page: int, has_next: bool, items: list[dict[str, Any]] | None = None) -> httpx.Response:
    """Respuesta 200 con el sobre del contrato."""
    items = [PEDIDO] if items is None else items
    cuerpo = {"items": items, "page": page, "page_size": 2, "total": 3, "has_next": has_next}
    return httpx.Response(200, json=cuerpo)


def _secuencia(*respuestas: httpx.Response | Exception) -> tuple[Manejador, list[httpx.Request]]:
    """Manejador que devuelve (o lanza) cada respuesta en orden y guarda las peticiones."""
    pendientes: Iterator[httpx.Response | Exception] = iter(respuestas)
    peticiones: list[httpx.Request] = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        siguiente = next(pendientes)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    return manejador, peticiones


def _cliente(manejador: Manejador, esperas: list[float], **config: str) -> ClienteApiOrigen:
    settings = crear_settings(SOURCE_API_MAX_RETRIES="3", SOURCE_API_PAGE_SIZE="2", **config)
    http = httpx.Client(base_url="http://source-api:8090", transport=httpx.MockTransport(manejador))
    return ClienteApiOrigen(settings, http=http, esperar=esperas.append)


def _todas(cliente: ClienteApiOrigen, escenario: EscenarioOrigen | None = None) -> list[RespuestaPedidos]:
    return list(cliente.iterar_paginas(DESDE, escenario))


def test_recorre_las_paginas_hasta_que_has_next_es_false():
    manejador, peticiones = _secuencia(_pagina(1, has_next=True), _pagina(2, has_next=False))

    paginas = _todas(_cliente(manejador, []), EscenarioOrigen.LENTO)

    assert [pagina.page for pagina in paginas] == [1, 2]
    parametros = peticiones[0].url.params
    assert parametros["updated_since"] == "2026-10-01T00:00:00Z"
    assert parametros["page_size"] == "2"
    assert parametros["scenario"] == "slow"
    assert peticiones[1].url.params["page"] == "2"


def test_los_importes_llegan_como_decimal_sin_pasar_por_float():
    manejador, _ = _secuencia(_pagina(1, has_next=False))

    [pagina] = _todas(_cliente(manejador, []))

    assert pagina.items[0] == {**PEDIDO, "amount": Decimal("129.95")}


@pytest.mark.parametrize(
    "fallo",
    [
        pytest.param(httpx.Response(503), id="503"),
        pytest.param(httpx.Response(429), id="429"),
        pytest.param(httpx.ReadTimeout("lento"), id="timeout"),
        pytest.param(httpx.ConnectError("caida"), id="conexion"),
    ],
)
def test_fallo_transitorio_se_reintenta_y_se_recupera(fallo: httpx.Response | Exception):
    esperas: list[float] = []
    manejador, peticiones = _secuencia(fallo, _pagina(1, has_next=False))

    paginas = _todas(_cliente(manejador, esperas))

    assert len(paginas) == 1
    assert len(peticiones) == 2
    assert len(esperas) == 1


def test_fallo_transitorio_persistente_agota_los_reintentos():
    esperas: list[float] = []
    manejador, peticiones = _secuencia(*[httpx.Response(503) for _ in range(4)])

    with pytest.raises(ApiOrigenNoDisponibleError, match="tras 4 intentos"):
        _todas(_cliente(manejador, esperas))

    assert len(peticiones) == 4
    assert len(esperas) == 3


@pytest.mark.parametrize("codigo", [500, 400, 404])
def test_error_no_transitorio_no_se_reintenta(codigo: int):
    esperas: list[float] = []
    manejador, peticiones = _secuencia(httpx.Response(codigo))

    with pytest.raises(ApiOrigenNoDisponibleError, match=f"HTTP {codigo}") as error:
        _todas(_cliente(manejador, esperas))

    assert error.value.codigo_http == codigo
    assert len(peticiones) == 1
    assert esperas == []


def test_backoff_crece_y_no_supera_el_maximo():
    esperas: list[float] = []
    manejador, _ = _secuencia(*[httpx.Response(503) for _ in range(4)])
    cliente = _cliente(
        manejador,
        esperas,
        SOURCE_API_BACKOFF_BASE_SECONDS="1",
        SOURCE_API_BACKOFF_MAX_SECONDS="3",
    )

    with pytest.raises(ApiOrigenNoDisponibleError):
        _todas(cliente)

    # Techos 1, 2 y 4 (acotado a 3); con jitter cada espera está entre la mitad del techo y el techo.
    assert 0.5 <= esperas[0] <= 1
    assert 1 <= esperas[1] <= 2
    assert 1.5 <= esperas[2] <= 3


def test_respeta_retry_after_sin_superar_el_maximo():
    esperas: list[float] = []
    manejador, _ = _secuencia(httpx.Response(429, headers={"Retry-After": "7"}), _pagina(1, has_next=False))

    _todas(_cliente(manejador, esperas, SOURCE_API_BACKOFF_MAX_SECONDS="30"))

    assert esperas == [7]


def test_sobre_invalido_no_se_reintenta():
    manejador, peticiones = _secuencia(httpx.Response(200, json={"items": [], "page": 1}))

    with pytest.raises(ContratoOrigenInvalidoError, match="has_next"):
        _todas(_cliente(manejador, []))

    assert len(peticiones) == 1


def test_respuesta_que_no_es_json():
    manejador, _ = _secuencia(httpx.Response(200, text="<html>error</html>"))

    with pytest.raises(ContratoOrigenInvalidoError, match="JSON"):
        _todas(_cliente(manejador, []))


def test_tope_de_paginas_evita_un_bucle_infinito():
    manejador, peticiones = _secuencia(_pagina(1, has_next=True), _pagina(2, has_next=True))

    with pytest.raises(ApiOrigenNoDisponibleError, match="tope de 2 páginas"):
        _todas(_cliente(manejador, [], SOURCE_API_MAX_PAGES="2"))

    assert len(peticiones) == 2
