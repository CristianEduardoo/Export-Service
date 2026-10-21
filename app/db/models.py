"""Modelo de la tabla de ejecuciones.

Las restricciones CHECK de los enums y el índice único parcial también están en
la migración (alembic/versions): la base de datos protege la integridad aunque
alguien escriba en ella sin pasar por este código.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, MetaData, String, Text, column, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, registry

from app.constants import (
    ESTADOS_ACTIVOS,
    CodigoError,
    EscenarioOrigen,
    EstadoEjecucion,
    OrigenEjecucion,
    PasoEjecucion,
)

# Nombres de restricciones predecibles: la migración usa exactamente los mismos.
_CONVENCION_NOMBRES = {
    "pk": "pk_%(table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
}


def _ahora_utc() -> datetime:
    return datetime.now(UTC)


def _enum(tipo: type[StrEnum], nombre: str) -> SAEnum:
    """Enum guardado como texto (su valor) con una restricción CHECK en la base de datos."""
    return SAEnum(
        tipo,
        name=nombre,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda enum: [miembro.value for miembro in enum],
    )


class Base(DeclarativeBase):
    """Base de los modelos: nombres de restricciones predecibles y fechas siempre con zona horaria."""

    registry = registry(
        metadata=MetaData(naming_convention=_CONVENCION_NOMBRES),
        type_annotation_map={datetime: DateTime(timezone=True)},
    )


class Ejecucion(Base):
    """Una ejecución de la exportación: estado, progreso, resultado y reserva del worker."""

    __tablename__ = "ejecuciones"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    estado: Mapped[EstadoEjecucion] = mapped_column(_enum(EstadoEjecucion, "estado"))
    paso: Mapped[PasoEjecucion | None] = mapped_column(_enum(PasoEjecucion, "paso"))
    origen: Mapped[OrigenEjecucion] = mapped_column(_enum(OrigenEjecucion, "origen"))
    escenario: Mapped[EscenarioOrigen | None] = mapped_column(_enum(EscenarioOrigen, "escenario"))

    creada_en: Mapped[datetime] = mapped_column(default=_ahora_utc)
    iniciada_en: Mapped[datetime | None]
    finalizada_en: Mapped[datetime | None]

    # updated_since usado al consultar la API y updated_at más reciente exportado.
    desde: Mapped[datetime | None]
    marca_agua: Mapped[datetime | None]

    registros_leidos: Mapped[int] = mapped_column(default=0)
    registros_exportados: Mapped[int] = mapped_column(default=0)
    registros_invalidos: Mapped[int] = mapped_column(default=0)
    registros_duplicados: Mapped[int] = mapped_column(default=0)

    nombre_fichero: Mapped[str | None] = mapped_column(String(255))
    sha256_fichero: Mapped[str | None] = mapped_column(String(64))
    email_enviado: Mapped[bool] = mapped_column(default=False)
    message_id: Mapped[str | None] = mapped_column(String(255))

    codigo_error: Mapped[CodigoError | None] = mapped_column(_enum(CodigoError, "codigo_error"))
    error: Mapped[str | None] = mapped_column(Text)

    # Reserva (lease): qué worker la procesa y hasta cuándo se le considera vivo.
    worker_id: Mapped[str | None] = mapped_column(String(255))
    lease_hasta: Mapped[datetime | None]

    __table_args__ = (
        Index("ix_ejecuciones_creada_en", "creada_en"),
        # Como mucho UNA ejecución activa: un índice único sobre una constante,
        # limitado a las filas pending/running. Lo garantiza PostgreSQL, no el código.
        Index(
            "ux_ejecuciones_una_activa",
            text("true"),
            unique=True,
            postgresql_where=column("estado").in_(sorted(estado.value for estado in ESTADOS_ACTIVOS)),
        ),
    )
