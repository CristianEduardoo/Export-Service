"""Contrato de la API: lo que se recibe y lo que se devuelve."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

from app.constants import CodigoError, EscenarioOrigen, EstadoEjecucion, OrigenEjecucion, PasoEjecucion
from app.domain.fechas import a_utc


class CrearEjecucionPeticion(BaseModel):
    """Cuerpo opcional de POST /executions."""

    model_config = ConfigDict(extra="forbid")

    trigger: OrigenEjecucion = Field(
        default=OrigenEjecucion.MANUAL,
        description="Quién lanza la ejecución: manual o scheduled (el scheduler).",
    )
    scenario: EscenarioOrigen | None = Field(
        default=None,
        description="Solo para pruebas: escenario de la API mock (temporary_error, slow).",
    )
    updated_since: Annotated[AwareDatetime, AfterValidator(a_utc)] | None = Field(
        default=None,
        description="Reproceso: exportar desde esta fecha en lugar de desde la marca de agua.",
    )


class EjecucionRespuesta(BaseModel):
    """Detalle de una ejecución. Los primeros campos siguen el ejemplo de docs/CHALLENGE.md."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: EstadoEjecucion = Field(validation_alias="estado")
    started_at: datetime | None = Field(validation_alias="iniciada_en")
    finished_at: datetime | None = Field(validation_alias="finalizada_en")
    records_read: int = Field(validation_alias="registros_leidos")
    records_exported: int = Field(validation_alias="registros_exportados")
    file_name: str | None = Field(validation_alias="nombre_fichero")
    file_sha256: str | None = Field(validation_alias="sha256_fichero")
    email_sent: bool = Field(validation_alias="email_enviado")
    error: str | None
    # Campos adicionales para entender qué pasó.
    error_code: CodigoError | None = Field(validation_alias="codigo_error")
    step: PasoEjecucion | None = Field(validation_alias="paso")
    trigger: OrigenEjecucion = Field(validation_alias="origen")
    scenario: EscenarioOrigen | None = Field(validation_alias="escenario")
    created_at: datetime = Field(validation_alias="creada_en")
    updated_since: datetime | None = Field(validation_alias="desde")
    watermark: datetime | None = Field(validation_alias="marca_agua")
    records_invalid: int = Field(validation_alias="registros_invalidos")
    records_duplicated: int = Field(validation_alias="registros_duplicados")
    message_id: str | None


class ListaEjecuciones(BaseModel):
    """Página del listado de ejecuciones, de la más reciente a la más antigua."""

    items: list[EjecucionRespuesta]
    limit: int
    offset: int


class ConflictoRespuesta(BaseModel):
    """409: ya hay una ejecución activa. Se devuelve su id para poder consultarla."""

    detail: str
    execution_id: UUID


class SaludRespuesta(BaseModel):
    status: str
    checks: dict[str, str] = Field(default_factory=dict)
