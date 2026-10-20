"""Contrato de la API de origen y validación de los pedidos recibidos.

La validación tiene dos niveles:

- El sobre de la respuesta (items, page, page_size, total, has_next) se valida
  entero: si no cumple el contrato, la respuesta no es fiable y la ejecución
  debe fallar.
- Cada pedido se valida por separado: uno inválido se descarta con su motivo y
  no afecta al resto de la página.

El contrato se aplica a rajatabla (contracts/source-order.json):

- additionalProperties: false, así que un campo desconocido invalida el pedido.
- Los tipos JSON se respetan: un importe en texto ("129.95") o una fecha como
  número no se convierten, se rechazan.

Este módulo no escribe logs: devuelve los motivos y es el servicio quien decide
cómo registrarlos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from app.constants import MAX_REGISTROS_POR_PAGINA, PATRON_MONEDA


class ContratoOrigenInvalidoError(Exception):
    """El sobre de la respuesta de la API de origen no cumple el contrato."""


def _exigir_numero(valor: object) -> object:
    """Un importe debe llegar como número JSON: ni texto ni booleano."""
    # bool se comprueba aparte porque en Python True es un int.
    if isinstance(valor, bool) or not isinstance(valor, int | float | Decimal):
        raise ValueError("debe ser un número JSON")
    return valor


def _exigir_texto(valor: object) -> object:
    """Una fecha debe llegar como texto ISO 8601, no como número."""
    if not isinstance(valor, str):
        raise ValueError("debe ser un texto con fecha ISO 8601")
    return valor


def _convertir_a_utc(valor: datetime) -> datetime:
    """Dentro del sistema todas las fechas van en UTC."""
    return valor.astimezone(UTC)


# Tipos reutilizables que respetan el tipo JSON del contrato antes de convertir.
ImporteJson = Annotated[Decimal, BeforeValidator(_exigir_numero)]
FechaJson = Annotated[AwareDatetime, BeforeValidator(_exigir_texto), AfterValidator(_convertir_a_utc)]


class PedidoOrigen(BaseModel):
    """Un pedido tal como lo define contracts/source-order.json."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    amount: ImporteJson = Field(gt=0)
    currency: str = Field(pattern=PATRON_MONEDA)
    updated_at: FechaJson
    description: str | None = None


class RespuestaPedidos(BaseModel):
    """El sobre de la respuesta paginada (contracts/source-orders-response.json).

    Los items no se validan aquí: se validan uno a uno con validar_pedidos().
    strict=True: en el sobre no se admite ninguna conversión ("1" no es un 1).
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    items: list[object]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_REGISTROS_POR_PAGINA)
    total: int = Field(ge=0)
    has_next: bool


@dataclass(frozen=True, slots=True)
class PedidoInvalido:
    """Un pedido descartado y por qué."""

    posicion: int
    id: str | None
    motivos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResultadoValidacion:
    """Pedidos de una página separados en válidos e inválidos."""

    validos: tuple[PedidoOrigen, ...]
    invalidos: tuple[PedidoInvalido, ...]


def validar_respuesta(datos: object) -> RespuestaPedidos:
    """Valida el sobre de la respuesta.

    Lanza ContratoOrigenInvalidoError si no cumple el contrato: no es un fallo
    transitorio, así que no tiene sentido reintentar.
    """
    try:
        return RespuestaPedidos.model_validate(datos)
    except ValidationError as error:
        raise ContratoOrigenInvalidoError("; ".join(_motivos(error, por_defecto="respuesta"))) from error


def validar_pedidos(items: Sequence[object]) -> ResultadoValidacion:
    """Valida cada pedido por separado y devuelve válidos e inválidos."""
    validos: list[PedidoOrigen] = []
    invalidos: list[PedidoInvalido] = []
    for posicion, item in enumerate(items):
        try:
            validos.append(PedidoOrigen.model_validate(item))
        except ValidationError as error:
            invalido = PedidoInvalido(
                posicion=posicion,
                id=_id_si_existe(item),
                motivos=_motivos(error, por_defecto="pedido"),
            )
            invalidos.append(invalido)
    return ResultadoValidacion(validos=tuple(validos), invalidos=tuple(invalidos))


def _motivos(error: ValidationError, por_defecto: str) -> tuple[str, ...]:
    """Convierte los errores de pydantic en textos 'campo: motivo'.

    include_input=False: el valor recibido no se copia al motivo, que acaba en
    los logs; así no se filtran datos de clientes.
    """
    return tuple(
        f"{'.'.join(str(parte) for parte in detalle['loc']) or por_defecto}: {detalle['msg']}"
        for detalle in error.errors(include_url=False, include_input=False)
    )


def _id_si_existe(item: object) -> str | None:
    """Devuelve el id del pedido si se puede leer, para poder identificarlo en los logs."""
    if isinstance(item, dict):
        valor = item.get("id")
        if isinstance(valor, str) and valor:
            return valor
    return None
