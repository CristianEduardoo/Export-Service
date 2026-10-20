"""Preparación de la exportación: deduplicación, transformación y orden.

Recibe los pedidos ya validados de todas las páginas y produce las filas del
fichero de exportación (contracts/export-record.example.json):

- Deduplicación por id: se conserva la versión con el updated_at más reciente.
  Si dos versiones tienen el mismo updated_at, gana la última recibida.
- Transformación: id pasa a order_id y description no se exporta.
- Orden estable por (updated_at, order_id): los mismos datos producen siempre
  el mismo fichero y, por tanto, la misma huella sha256.

Después genera el fichero CSV en memoria (bytes), con su sha256 y su nombre.
Escribirlo en disco o enviarlo no es asunto del dominio.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.constants import (
    CODIFICACION_FICHERO,
    COLUMNAS_EXPORTACION,
    EXTENSION_FICHERO,
    FORMATO_FECHA_FICHERO,
    PREFIJO_FICHERO,
)
from app.domain.pedidos import PedidoOrigen


@dataclass(frozen=True, slots=True)
class FilaExportacion:
    """Una fila del fichero de exportación."""

    order_id: str
    customer_id: str
    amount: Decimal
    currency: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Exportacion:
    """Filas listas para escribir y contadores para el registro de la ejecución."""

    filas: tuple[FilaExportacion, ...]
    duplicados: int

    @property
    def ultima_actualizacion(self) -> datetime | None:
        """updated_at más reciente exportado: la marca de agua de la siguiente ejecución."""
        return max((fila.updated_at for fila in self.filas), default=None)


@dataclass(frozen=True, slots=True)
class FicheroExportacion:
    """Fichero generado en memoria, listo para guardar y enviar."""

    nombre: str
    contenido: bytes
    sha256: str
    filas: int


def preparar_exportacion(pedidos: Iterable[PedidoOrigen]) -> Exportacion:
    """Deduplica, transforma y ordena los pedidos válidos."""
    unicos, duplicados = _deduplicar(pedidos)
    filas = sorted((_a_fila(pedido) for pedido in unicos), key=lambda fila: (fila.updated_at, fila.order_id))
    return Exportacion(filas=tuple(filas), duplicados=duplicados)


def _deduplicar(pedidos: Iterable[PedidoOrigen]) -> tuple[list[PedidoOrigen], int]:
    """Deja un pedido por id y cuenta cuántas copias se han descartado."""
    por_id: dict[str, PedidoOrigen] = {}
    duplicados = 0
    for pedido in pedidos:
        actual = por_id.get(pedido.id)
        if actual is None:
            por_id[pedido.id] = pedido
            continue
        duplicados += 1
        # >= y no >: con el mismo updated_at gana la última versión recibida.
        if pedido.updated_at >= actual.updated_at:
            por_id[pedido.id] = pedido
    return list(por_id.values()), duplicados


def _a_fila(pedido: PedidoOrigen) -> FilaExportacion:
    """Transforma un pedido de origen en una fila de exportación."""
    return FilaExportacion(
        order_id=pedido.id,
        customer_id=pedido.customer_id,
        amount=pedido.amount,
        currency=pedido.currency,
        updated_at=pedido.updated_at,
    )


def generar_fichero(exportacion: Exportacion, execution_id: UUID, momento: datetime) -> FicheroExportacion:
    """Genera el CSV en memoria con su nombre y su huella sha256.

    El nombre lleva fecha, hora y parte del execution_id: con ejecuciones cada
    5 minutos, un nombre solo con la fecha (export-2026-10-16.csv) se repetiría.
    """
    if momento.tzinfo is None:
        raise ValueError("el momento de la exportación debe llevar zona horaria")

    contenido = _escribir_csv(exportacion.filas)
    nombre = (
        f"{PREFIJO_FICHERO}-{momento.astimezone(UTC).strftime(FORMATO_FECHA_FICHERO)}"
        f"-{str(execution_id)[:8]}{EXTENSION_FICHERO}"
    )
    return FicheroExportacion(
        nombre=nombre,
        contenido=contenido,
        sha256=hashlib.sha256(contenido).hexdigest(),
        filas=len(exportacion.filas),
    )


def _escribir_csv(filas: Iterable[FilaExportacion]) -> bytes:
    """Escribe las filas en CSV (RFC 4180: separador coma y fin de línea CRLF)."""
    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(COLUMNAS_EXPORTACION)
    for fila in filas:
        escritor.writerow(
            [
                fila.order_id,
                fila.customer_id,
                # Formato "f": nunca notación científica (1E+2 se escribe 100).
                format(fila.amount, "f"),
                fila.currency,
                _fecha_iso_utc(fila.updated_at),
            ],
        )
    return buffer.getvalue().encode(CODIFICACION_FICHERO)


def _fecha_iso_utc(fecha: datetime) -> str:
    """2026-10-16T07:45:00Z: el mismo formato que usa el contrato."""
    return fecha.astimezone(UTC).isoformat().replace("+00:00", "Z")
