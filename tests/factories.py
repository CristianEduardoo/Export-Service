"""Datos de prueba compartidos por varios ficheros de tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.config import Settings

# Configuración mínima válida (nombres de las variables de entorno).
ENTORNO_VALIDO: dict[str, str] = {
    "SOURCE_API_URL": "http://source-api:8090",
    "EXPORT_INITIAL_UPDATED_SINCE": "2026-10-01T00:00:00Z",
    "EXPORT_RECIPIENT": "integration@example.test",
    "SMTP_HOST": "mailpit",
    "SMTP_FROM": "export-service@example.test",
    "POSTGRES_HOST": "postgres",
    "POSTGRES_DB": "export_service",
    "POSTGRES_USER": "export_service",
    "POSTGRES_PASSWORD": "solo_desarrollo_local",
}


def crear_settings(**cambios: str) -> Settings:
    """Settings válidos con los cambios indicados, sin leer el .env local."""
    return Settings(_env_file=None, **{**ENTORNO_VALIDO, **cambios})


def sobres(items: list[Any], tamano: int) -> Iterator[dict[str, Any]]:
    """Trocea los pedidos en páginas con el mismo sobre que devuelve la API mock."""
    for inicio in range(0, len(items), tamano):
        yield {
            "items": items[inicio : inicio + tamano],
            "page": inicio // tamano + 1,
            "page_size": tamano,
            "total": len(items),
            "has_next": inicio + tamano < len(items),
        }
