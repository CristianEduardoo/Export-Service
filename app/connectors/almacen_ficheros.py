"""Almacenamiento de los ficheros exportados en disco local.

En local y en Docker es un volumen; en producción se sustituiría por un
almacenamiento gestionado (Azure Blob Storage o Azure Files) con la misma
interfaz: guardar(fichero) -> ubicación.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from app.constants import Evento
from app.domain.exportacion import FicheroExportacion

logger = logging.getLogger(__name__)


class AlmacenFicherosLocal:
    """Guarda los ficheros en un directorio con escritura atómica."""

    def __init__(self, directorio: Path) -> None:
        self._directorio = directorio

    def guardar(self, fichero: FicheroExportacion) -> Path:
        """Guarda el fichero y devuelve su ruta.

        Escritura atómica: se escribe en un temporal del mismo directorio y se
        renombra al final. Si el proceso muere a mitad, nunca queda un CSV
        incompleto con el nombre definitivo.
        """
        if Path(fichero.nombre).name != fichero.nombre:
            raise ValueError(f"nombre de fichero no válido: {fichero.nombre!r}")

        self._directorio.mkdir(parents=True, exist_ok=True)
        destino = self._directorio / fichero.nombre
        descriptor, ruta_temporal = tempfile.mkstemp(dir=self._directorio, prefix=".", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as temporal:
                temporal.write(fichero.contenido)
                temporal.flush()
                os.fsync(temporal.fileno())
            os.replace(ruta_temporal, destino)
        except BaseException:
            # Si algo falla (disco lleno, interrupción...), no se deja el temporal abandonado.
            Path(ruta_temporal).unlink(missing_ok=True)
            raise

        logger.info(
            "Fichero de exportación guardado",
            extra={
                "event": Evento.FICHERO_GUARDADO,
                "file_name": fichero.nombre,
                "bytes": len(fichero.contenido),
                "file_sha256": fichero.sha256,
            },
        )
        return destino
