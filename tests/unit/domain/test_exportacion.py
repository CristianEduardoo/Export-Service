"""Pruebas de la preparación de la exportación: duplicados, transformación y orden."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.domain.exportacion import FilaExportacion, generar_fichero, preparar_exportacion
from app.domain.pedidos import PedidoOrigen


def _pedido(id_pedido: str, hora: int, amount: str = "10.00", description: str | None = None) -> PedidoOrigen:
    """Pedido válido del 16/10/2026 a la hora indicada, construido como llega de la API (JSON)."""
    return PedidoOrigen.model_validate(
        {
            "id": id_pedido,
            "customer_id": "customer-42",
            "amount": Decimal(amount),
            "currency": "EUR",
            "updated_at": f"2026-10-16T{hora:02d}:00:00Z",
            "description": description,
        },
    )


def test_transforma_id_en_order_id_y_descarta_description():
    exportacion = preparar_exportacion([_pedido("ord-1", 8, amount="129.95", description="Monthly")])

    assert exportacion.filas == (
        FilaExportacion(
            order_id="ord-1",
            customer_id="customer-42",
            amount=Decimal("129.95"),
            currency="EUR",
            updated_at=datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
        ),
    )
    assert exportacion.duplicados == 0


def test_duplicado_conserva_la_version_mas_reciente_aunque_llegue_antes():
    reciente = _pedido("ord-1", 9, amount="20.00")
    antigua = _pedido("ord-1", 8, amount="10.00")

    exportacion = preparar_exportacion([reciente, antigua])

    [fila] = exportacion.filas
    assert fila.amount == Decimal("20.00")
    assert exportacion.duplicados == 1


def test_duplicado_con_la_misma_fecha_gana_el_ultimo_recibido():
    exportacion = preparar_exportacion([_pedido("ord-1", 8, amount="10.00"), _pedido("ord-1", 8, amount="11.00")])

    [fila] = exportacion.filas
    assert fila.amount == Decimal("11.00")


def test_mismo_contenido_en_distinto_orden_produce_la_misma_exportacion():
    pedidos = [_pedido("ord-1", 8), _pedido("ord-2", 9), _pedido("ord-3", 10)]

    assert preparar_exportacion(pedidos) == preparar_exportacion(reversed(pedidos))


def test_sin_pedidos_no_hay_filas_ni_marca_de_agua():
    exportacion = preparar_exportacion([])

    assert exportacion.filas == ()
    assert exportacion.duplicados == 0
    assert exportacion.ultima_actualizacion is None


# --------------------------------------------------------------- fichero CSV

EXECUTION_ID = UUID("8e7d2bb2-1dd2-4f4c-9bb7-6b8a2fd73c19")
MOMENTO = datetime(2026, 10, 20, 8, 0, 5, tzinfo=UTC)


def test_csv_con_cabecera_formato_del_contrato_y_crlf():
    exportacion = preparar_exportacion([_pedido("ord-1", 8, amount="129.95"), _pedido("ord-2", 9, amount="1E+2")])

    fichero = generar_fichero(exportacion, EXECUTION_ID, MOMENTO)

    assert fichero.contenido == (
        b"order_id,customer_id,amount,currency,updated_at\r\n"
        b"ord-1,customer-42,129.95,EUR,2026-10-16T08:00:00Z\r\n"
        b"ord-2,customer-42,100,EUR,2026-10-16T09:00:00Z\r\n"
    )
    assert fichero.filas == 2


def test_nombre_unico_por_ejecucion_y_sha256_del_contenido():
    fichero = generar_fichero(preparar_exportacion([_pedido("ord-1", 8)]), EXECUTION_ID, MOMENTO)

    assert fichero.nombre == "export-20261020T080005Z-8e7d2bb2.csv"
    assert fichero.sha256 == hashlib.sha256(fichero.contenido).hexdigest()
