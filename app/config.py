"""Configuración de la aplicación.

Los valores se leen de las variables de entorno (y del fichero .env en local) y
se validan al arrancar: si falta una variable obligatoria o tiene un valor
incorrecto, el proceso falla de inmediato con un mensaje claro, en lugar de
romperse a mitad de una ejecución.
"""

from __future__ import annotations

import re
from datetime import UTC
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import AwareDatetime, Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL

from app.constants import MAX_REGISTROS_POR_PAGINA

# Comprobación sencilla de formato; la entrega real la decide el servidor SMTP.
_PATRON_EMAIL: Final[re.Pattern[str]] = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

NivelLog = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Entorno(StrEnum):
    """Entornos de ejecución."""

    DESARROLLO = "development"
    PRODUCCION = "production"


class Settings(BaseSettings):
    """Configuración completa del servicio, validada al arrancar."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # --- General
    entorno: Entorno = Field(default=Entorno.DESARROLLO, alias="ENVIRONMENT")
    nivel_log: NivelLog = Field(default="INFO", alias="LOG_LEVEL")

    # --- API de origen
    api_origen_url: HttpUrl = Field(alias="SOURCE_API_URL")
    api_origen_timeout_segundos: float = Field(default=10, gt=0, alias="SOURCE_API_TIMEOUT_SECONDS")
    # El contrato de la API admite como máximo 500 registros por página.
    api_origen_registros_por_pagina: int = Field(
        default=100, ge=1, le=MAX_REGISTROS_POR_PAGINA, alias="SOURCE_API_PAGE_SIZE"
    )
    api_origen_max_paginas: int = Field(default=1000, ge=1, alias="SOURCE_API_MAX_PAGES")
    api_origen_max_reintentos: int = Field(default=3, ge=0, alias="SOURCE_API_MAX_RETRIES")
    api_origen_backoff_base_segundos: float = Field(default=1, gt=0, alias="SOURCE_API_BACKOFF_BASE_SECONDS")
    api_origen_backoff_max_segundos: float = Field(default=30, gt=0, alias="SOURCE_API_BACKOFF_MAX_SECONDS")

    # --- Exportación
    # AwareDatetime rechaza fechas sin zona horaria (el validador).
    exportacion_desde_inicial: AwareDatetime = Field(alias="EXPORT_INITIAL_UPDATED_SINCE")
    exportacion_directorio: Path = Field(default=Path("/app/exports"), alias="EXPORT_DIR")
    # NoDecode: el valor llega como texto "a@x.com,b@x.com" y lo separa el validador.
    exportacion_destinatarios: Annotated[tuple[str, ...], NoDecode] = Field(alias="EXPORT_RECIPIENT")

    # --- SMTP
    smtp_host: str = Field(alias="SMTP_HOST")
    smtp_puerto: int = Field(default=587, ge=1, le=65535, alias="SMTP_PORT")
    smtp_remitente: str = Field(alias="SMTP_FROM")
    smtp_usuario: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: SecretStr = Field(default=SecretStr(""), alias="SMTP_PASSWORD")
    smtp_usar_tls: bool = Field(default=True, alias="SMTP_USE_TLS")
    smtp_timeout_segundos: float = Field(default=10, gt=0, alias="SMTP_TIMEOUT_SECONDS")
    smtp_max_reintentos: int = Field(default=3, ge=0, alias="SMTP_MAX_RETRIES")

    # --- Worker
    worker_intervalo_sondeo_segundos: float = Field(default=5, gt=0, alias="WORKER_POLL_INTERVAL_SECONDS")
    worker_lease_segundos: int = Field(default=300, gt=0, alias="WORKER_LEASE_SECONDS")

    # --- Base de datos
    postgres_host: str = Field(alias="POSTGRES_HOST")
    postgres_puerto: int = Field(default=5432, ge=1, le=65535, alias="POSTGRES_PORT")
    postgres_db: str = Field(alias="POSTGRES_DB")
    postgres_usuario: str = Field(alias="POSTGRES_USER")
    postgres_password: SecretStr = Field(alias="POSTGRES_PASSWORD")

    @field_validator("nivel_log", mode="before")
    @classmethod
    def _normalizar_nivel_log(cls, valor: object) -> object:
        """Acepta 'info' o 'INFO' indistintamente."""
        return valor.upper() if isinstance(valor, str) else valor

    @field_validator("exportacion_desde_inicial")
    @classmethod
    def _convertir_a_utc(cls, valor: AwareDatetime) -> AwareDatetime:
        """Dentro del sistema todas las fechas van en UTC."""
        return valor.astimezone(UTC)

    @field_validator("exportacion_destinatarios", mode="before")
    @classmethod
    def _separar_destinatarios(cls, valor: object) -> object:
        """Convierte 'a@x.com, b@x.com' en ('a@x.com', 'b@x.com')."""
        if isinstance(valor, str):
            return tuple(destinatario.strip() for destinatario in valor.split(",") if destinatario.strip())
        return valor

    @field_validator("exportacion_destinatarios")
    @classmethod
    def _validar_destinatarios(cls, valor: tuple[str, ...]) -> tuple[str, ...]:
        """Exige al menos un destinatario y que todos tengan formato de email."""
        if not valor:
            raise ValueError("debe haber al menos un destinatario")
        invalidos = [destinatario for destinatario in valor if not _PATRON_EMAIL.match(destinatario)]
        if invalidos:
            raise ValueError(f"destinatarios con formato no válido: {', '.join(invalidos)}")
        return valor

    @model_validator(mode="after")
    def _validar_coherencia(self) -> Self:
        """Reglas que dependen de varios campos a la vez."""
        if self.api_origen_backoff_max_segundos < self.api_origen_backoff_base_segundos:
            raise ValueError("SOURCE_API_BACKOFF_MAX_SECONDS no puede ser menor que SOURCE_API_BACKOFF_BASE_SECONDS")
        if self.worker_lease_segundos <= self.worker_intervalo_sondeo_segundos:
            raise ValueError("WORKER_LEASE_SECONDS debe ser mayor que WORKER_POLL_INTERVAL_SECONDS")
        return self

    @property
    def url_base_datos(self) -> URL:
        """URL de conexión a PostgreSQL.

        Se construye con URL.create y no concatenando texto: así una contraseña
        con caracteres especiales (@, :, /) no rompe la URL.
        """
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_usuario,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_puerto,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración, leída y validada una sola vez por proceso."""
    return Settings()
