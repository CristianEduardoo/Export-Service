"""Prueba de punta a punta del dominio con el fichero de casuísticas.

Es el mismo fichero que se monta sobre la API mock en Docker: lo que se prueba
aquí es lo que se ve en la demo. Recorre todo el dominio como lo hará el
servicio: validar cada página, juntar los válidos, preparar y generar el CSV.

Nota: el fichero no contiene fechas inválidas a propósito. La API mock filtra
por updated_at y una fecha sin zona o ausente la haría responder con un 500;
esos casos están cubiertos en test_pedidos.py.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from app.domain.exportacion import generar_fichero, preparar_exportacion
from app.domain.pedidos import PedidoInvalido, PedidoOrigen, validar_pedidos, validar_respuesta

FICHERO_CASUISTICAS = Path(__file__).parents[2] / "fixtures" / "source_orders_casuisticas.json"


def _paginas(items: list[Any], tamano: int) -> Iterator[dict[str, Any]]:
    """Trocea los pedidos en páginas con el mismo sobre que devuelve la API mock."""
    for inicio in range(0, len(items), tamano):
        yield {
            "items": items[inicio : inicio + tamano],
            "page": inicio // tamano + 1,
            "page_size": tamano,
            "total": len(items),
            "has_next": inicio + tamano < len(items),
        }


def test_flujo_completo_del_dominio_con_las_casuisticas():
    # parse_float=Decimal: los importes no pasan nunca por float.
    items = json.loads(FICHERO_CASUISTICAS.read_text(), parse_float=Decimal)
    validos: list[PedidoOrigen] = []
    invalidos: list[PedidoInvalido] = []

    for pagina in _paginas(items, tamano=5):
        resultado = validar_pedidos(validar_respuesta(pagina).items)
        validos.extend(resultado.validos)
        invalidos.extend(resultado.invalidos)
    exportacion = preparar_exportacion(validos)
    fichero = generar_fichero(exportacion, UUID("8e7d2bb2-1dd2-4f4c-9bb7-6b8a2fd73c19"), datetime.now(UTC))

    # 13 leídos = 7 inválidos + 2 duplicados + 4 exportados
    assert len(items) == 13
    assert sorted(invalido.id or "" for invalido in invalidos) == [
        "",
        "ord-2001",
        "ord-2002",
        "ord-2003",
        "ord-2004",
        "ord-2005",
        "ord-2006",
    ]
    assert exportacion.duplicados == 2
    assert fichero.filas == 4
    assert exportacion.ultima_actualizacion == datetime(2026, 10, 16, 8, 30, tzinfo=UTC)
    assert fichero.contenido.decode().splitlines() == [
        "order_id,customer_id,amount,currency,updated_at",
        "ord-1001,customer-42,129.95,EUR,2026-10-16T07:45:00Z",
        "ord-1002,customer-43,49.5,EUR,2026-10-16T07:50:00Z",
        "ord-1003,customer-42,19.99,EUR,2026-10-16T07:55:00Z",
        "ord-1004,customer-44,12.0,EUR,2026-10-16T08:30:00Z",
    ]
