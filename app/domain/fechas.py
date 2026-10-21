"""Formato de fechas común a todo el servicio."""

from __future__ import annotations

from datetime import UTC, datetime


def a_utc(fecha: datetime) -> datetime:
    """Misma fecha y hora expresada en UTC. Dentro del sistema todas las fechas van en UTC."""
    if fecha.tzinfo is None:
        raise ValueError("la fecha debe llevar zona horaria")
    return fecha.astimezone(UTC)


def fecha_iso_utc(fecha: datetime) -> str:
    """Fecha en ISO 8601 y UTC con Z final: 2026-10-16T07:45:00Z.

    Es el formato del contrato. Además, en una URL la Z evita el '+' de +00:00,
    que se interpretaría como un espacio.
    """
    return a_utc(fecha).isoformat().replace("+00:00", "Z")
