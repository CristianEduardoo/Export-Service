"""Pruebas del almacén de ficheros: escritura completa y sin restos."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.connectors.almacen_ficheros import AlmacenFicherosLocal
from app.domain.exportacion import FicheroExportacion


def _fichero(nombre: str = "export-20261020T080005Z-8e7d2bb2.csv") -> FicheroExportacion:
    return FicheroExportacion(nombre=nombre, contenido=b"order_id\r\nord-1\r\n", sha256="abc", filas=1)


def test_guarda_el_contenido_completo_sin_dejar_temporales(tmp_path: Path):
    destino = AlmacenFicherosLocal(tmp_path / "exports").guardar(_fichero())

    assert destino.read_bytes() == b"order_id\r\nord-1\r\n"
    assert [ruta.name for ruta in destino.parent.iterdir()] == [destino.name]
    # Sin chmod explícito heredaría el 600 del temporal y nadie más podría recogerlo.
    assert stat.S_IMODE(destino.stat().st_mode) == 0o640


@pytest.mark.parametrize("nombre", ["../fuera.csv", "subcarpeta/export.csv"])
def test_rechaza_nombres_que_salen_del_directorio(tmp_path: Path, nombre: str):
    with pytest.raises(ValueError, match="no válido"):
        AlmacenFicherosLocal(tmp_path).guardar(_fichero(nombre))
