"""Pruebas del contrato de la API de origen: qué pedidos se aceptan y cuáles no."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.domain.pedidos import (
    ContratoOrigenInvalidoError,
    PedidoOrigen,
    validar_pedidos,
    validar_respuesta,
)


def _pedido(**cambios: object) -> dict[str, Any]:
    """Pedido válido (el primero de examples/source-api/data.json) con los cambios indicados."""
    pedido: dict[str, Any] = {
        "id": "ord-1001",
        "customer_id": "customer-42",
        "amount": 129.95,
        "currency": "EUR",
        "updated_at": "2026-10-16T07:45:00Z",
        "description": "Monthly subscription",
    }
    pedido.update(cambios)
    return pedido


def _respuesta(**cambios: object) -> dict[str, Any]:
    """Sobre válido de una respuesta paginada con los cambios indicados."""
    respuesta: dict[str, Any] = {"items": [_pedido()], "page": 1, "page_size": 50, "total": 1, "has_next": False}
    respuesta.update(cambios)
    return respuesta


# --------------------------------------------------------------- pedidos válidos


def test_pedido_valido():
    pedido = PedidoOrigen.model_validate(_pedido())

    assert pedido.id == "ord-1001"
    assert pedido.updated_at == datetime(2026, 10, 16, 7, 45, tzinfo=UTC)


def test_importe_se_convierte_a_decimal_exacto():
    """Con float, 129.95 no es exactamente 129.95; con Decimal sí."""
    assert PedidoOrigen.model_validate(_pedido(amount=129.95)).amount == Decimal("129.95")


def test_descripcion_puede_no_venir():
    pedido = _pedido()
    del pedido["description"]

    assert PedidoOrigen.model_validate(pedido).description is None


def test_fecha_con_otra_zona_se_guarda_en_utc():
    pedido = PedidoOrigen.model_validate(_pedido(updated_at="2026-10-16T09:45:00+02:00"))

    assert pedido.updated_at == datetime(2026, 10, 16, 7, 45, tzinfo=UTC)
    assert pedido.updated_at.tzinfo == UTC


# --------------------------------------------------------------- pedidos inválidos

CASOS_INVALIDOS = [
    pytest.param({"customer_id": None}, "customer_id", id="customer_id nulo"),
    pytest.param({"id": ""}, "id", id="id vacio"),
    pytest.param({"amount": 0}, "amount", id="importe cero"),
    pytest.param({"amount": -5}, "amount", id="importe negativo"),
    pytest.param({"amount": "129.95"}, "amount", id="importe en texto"),
    pytest.param({"amount": True}, "amount", id="importe booleano"),
    pytest.param({"currency": "eur"}, "currency", id="moneda en minusculas"),
    pytest.param({"currency": "EURO"}, "currency", id="moneda de 4 letras"),
    pytest.param({"updated_at": "2026-10-16T07:45:00"}, "updated_at", id="fecha sin zona"),
    pytest.param({"updated_at": "2026-10-16"}, "updated_at", id="solo fecha"),
    pytest.param({"updated_at": 1792000000}, "updated_at", id="fecha como numero"),
    pytest.param({"description": 42}, "description", id="descripcion numerica"),
    pytest.param({"discount": 5}, "discount", id="campo no previsto en el contrato"),
]


@pytest.mark.parametrize(("cambios", "campo"), CASOS_INVALIDOS)
def test_pedido_invalido_se_descarta_con_motivo(cambios: dict[str, object], campo: str):
    resultado = validar_pedidos([_pedido(**cambios)])

    assert resultado.validos == ()
    [invalido] = resultado.invalidos
    assert invalido.motivos[0].startswith(f"{campo}:")


@pytest.mark.parametrize("campo", ["id", "customer_id", "amount", "currency", "updated_at"])
def test_falta_campo_obligatorio(campo: str):
    pedido = _pedido()
    del pedido[campo]

    [invalido] = validar_pedidos([pedido]).invalidos

    assert invalido.motivos == (f"{campo}: Field required",)


def test_motivo_no_incluye_el_valor_recibido():
    """Los motivos acaban en los logs: no deben copiar datos del pedido."""
    [invalido] = validar_pedidos([_pedido(customer_id="", amount="dato-sensible")]).invalidos

    assert "dato-sensible" not in " ".join(invalido.motivos)
    assert len(invalido.motivos) == 2


def test_un_pedido_invalido_no_afecta_al_resto():
    resultado = validar_pedidos([_pedido(id="ord-1"), _pedido(id="ord-2", amount=0), _pedido(id="ord-3")])

    assert [pedido.id for pedido in resultado.validos] == ["ord-1", "ord-3"]
    [invalido] = resultado.invalidos
    assert (invalido.posicion, invalido.id) == (1, "ord-2")


# --------------------------------------------------------------- sobre de la respuesta


def test_respuesta_con_pedidos_invalidos_sigue_siendo_valida():
    """El sobre no valida los pedidos: un pedido malo no invalida la página."""
    respuesta = validar_respuesta(_respuesta(items=[_pedido(amount=-1)]))

    assert len(respuesta.items) == 1


@pytest.mark.parametrize(
    ("datos", "motivo"),
    [
        pytest.param(_respuesta(has_next=None), "has_next", id="has_next nulo"),
        pytest.param(_respuesta(page="1"), "page", id="pagina en texto"),
        pytest.param(_respuesta(page_size=501), "page_size", id="pagina mayor que el contrato"),
        pytest.param(_respuesta(total=-1), "total", id="total negativo"),
        pytest.param(_respuesta(items={}), "items", id="items no es una lista"),
        pytest.param(_respuesta(cursor="abc"), "cursor", id="campo no previsto"),
        pytest.param([], "respuesta", id="no es un objeto"),
    ],
)
def test_respuesta_que_incumple_el_contrato(datos: object, motivo: str):
    with pytest.raises(ContratoOrigenInvalidoError, match=motivo):
        validar_respuesta(datos)
