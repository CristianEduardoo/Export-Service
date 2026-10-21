"""Entorno de Alembic: de dónde sale la URL y qué metadatos se migran."""

from __future__ import annotations

import logging

from alembic import context
from sqlalchemy import create_engine, pool

from app.constants import Servicio
from app.db.models import Base
from app.logging_config import configurar_logging

configuracion = context.config

# Sin esto Alembic no muestra nada. Si ya hay handlers (pytest), se respetan.
if not logging.getLogger().handlers:
    configurar_logging(Servicio.MIGRACIONES, "INFO")
metadatos = Base.metadata


def _url() -> str:
    """URL de la base de datos.

    Los tests la pasan en configuracion.attributes["url"]; en el resto de casos
    se usa la configuración validada de la aplicación (variables POSTGRES_*).
    """
    url = configuracion.attributes.get("url")
    if url:
        return str(url)
    # Import aquí y no arriba: los tests pasan la URL y no necesitan el .env.
    from app.config import get_settings

    return get_settings().url_base_datos.render_as_string(hide_password=False)


def migrar_sin_conexion() -> None:
    """Genera el SQL sin conectarse (alembic upgrade head --sql)."""
    context.configure(url=_url(), target_metadata=metadatos, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def migrar_con_conexion() -> None:
    """Aplica las migraciones contra la base de datos."""
    motor = create_engine(_url(), poolclass=pool.NullPool)
    with motor.connect() as conexion:
        context.configure(connection=conexion, target_metadata=metadatos)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    migrar_sin_conexion()
else:
    migrar_con_conexion()
