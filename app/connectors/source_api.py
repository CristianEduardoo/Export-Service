"""Cliente de la API de origen: GET /v1/orders con paginación y reintentos.

Responsabilidades:

- Paginación: pide páginas mientras has_next sea true, con un tope de páginas
  para no entrar en un bucle infinito si la API se comporta mal.
- Timeout en cada petición.
- Reintentos solo ante fallos transitorios (timeout, conexión, 429, 502, 503,
  504), con backoff exponencial con jitter y respetando Retry-After.
- Contrato del sobre: cada página se valida con el dominio. Un sobre inválido
  no se reintenta, porque repetir la petición no lo arreglaría.

Los pedidos de cada página NO se validan aquí: eso es lógica de negocio y la
hace el servicio con el dominio. Este cliente solo habla HTTP.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Self

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt

from app.config import Settings
from app.connectors.reintentos import espera_exponencial_con_jitter
from app.constants import CABECERA_RETRY_AFTER, CODIGOS_HTTP_TRANSITORIOS, RUTA_PEDIDOS, EscenarioOrigen, Evento
from app.domain.fechas import fecha_iso_utc
from app.domain.pedidos import ContratoOrigenInvalidoError, RespuestaPedidos, validar_respuesta

logger = logging.getLogger(__name__)


class ApiOrigenNoDisponibleError(Exception):
    """La API de origen no ha devuelto una respuesta utilizable.

    Se lanza tras agotar los reintentos de un fallo transitorio, ante un error
    HTTP no transitorio (por ejemplo un 500 o un 404) o al superar el tope de
    páginas.
    """

    def __init__(self, mensaje: str, codigo_http: int | None = None) -> None:
        super().__init__(mensaje)
        self.codigo_http = codigo_http


class _FalloTransitorioError(Exception):
    """Fallo que puede arreglarse solo repitiendo la petición. Uso interno."""

    def __init__(self, mensaje: str, retry_after: float | None = None) -> None:
        super().__init__(mensaje)
        self.retry_after = retry_after


class ClienteApiOrigen:
    """Cliente HTTP de la API de origen.

    El cliente httpx y la función de espera se pueden inyectar: en los tests se
    sustituyen por un transport falso y una espera que no duerme.
    """

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        esperar: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http or httpx.Client(
            base_url=str(settings.api_origen_url),
            timeout=settings.api_origen_timeout_segundos,
        )
        self._esperar = esperar
        self._registros_por_pagina = settings.api_origen_registros_por_pagina
        self._max_paginas = settings.api_origen_max_paginas
        self._max_intentos = settings.api_origen_max_reintentos + 1
        self._backoff_base = settings.api_origen_backoff_base_segundos
        self._backoff_max = settings.api_origen_backoff_max_segundos

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        error: BaseException | None,
        traza: TracebackType | None,
    ) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        """Cierra las conexiones abiertas con la API."""
        self._http.close()

    def iterar_paginas(
        self,
        desde: datetime,
        escenario: EscenarioOrigen | None = None,
    ) -> Iterator[RespuestaPedidos]:
        """Devuelve las páginas una a una, ya validadas contra el contrato del sobre.

        Es un generador: el servicio procesa cada página antes de pedir la
        siguiente, así la memoria no crece con el volumen de datos.

        Lanza ApiOrigenNoDisponibleError o ContratoOrigenInvalidoError.
        """
        if desde.tzinfo is None:
            raise ValueError("updated_since debe llevar zona horaria")

        for pagina in range(1, self._max_paginas + 1):
            respuesta = self._obtener_pagina_con_reintentos(desde, pagina, escenario)
            yield respuesta
            if not respuesta.has_next:
                return
        raise ApiOrigenNoDisponibleError(f"se alcanzó el tope de {self._max_paginas} páginas y has_next sigue en true")

    def _obtener_pagina_con_reintentos(
        self,
        desde: datetime,
        pagina: int,
        escenario: EscenarioOrigen | None,
    ) -> RespuestaPedidos:
        """Pide una página reintentando solo los fallos transitorios."""
        reintentador = Retrying(
            stop=stop_after_attempt(self._max_intentos),
            wait=self._calcular_espera,
            retry=retry_if_exception_type(_FalloTransitorioError),
            sleep=self._esperar,
            before_sleep=self._registrar_reintento,
            reraise=True,
        )
        try:
            return reintentador(self._obtener_pagina, desde, pagina, escenario)
        except _FalloTransitorioError as error:
            raise ApiOrigenNoDisponibleError(
                f"la API de origen sigue fallando tras {self._max_intentos} intentos: {error}",
            ) from error

    def _obtener_pagina(self, desde: datetime, pagina: int, escenario: EscenarioOrigen | None) -> RespuestaPedidos:
        """Una sola petición HTTP, sin reintentos. Clasifica el fallo si lo hay."""
        parametros: dict[str, str | int] = {
            "updated_since": fecha_iso_utc(desde),
            "page": pagina,
            "page_size": self._registros_por_pagina,
        }
        if escenario is not None:
            parametros["scenario"] = escenario.value

        inicio = time.perf_counter()
        try:
            respuesta = self._http.get(RUTA_PEDIDOS, params=parametros)
        # TimeoutException hereda de TransportError: el orden de los except importa.
        except httpx.TimeoutException as error:
            raise _FalloTransitorioError("timeout de la API de origen") from error
        except httpx.TransportError as error:
            raise _FalloTransitorioError(f"error de conexión con la API de origen: {error}") from error

        if respuesta.status_code in CODIGOS_HTTP_TRANSITORIOS:
            raise _FalloTransitorioError(
                f"HTTP {respuesta.status_code} transitorio",
                retry_after=_leer_retry_after(respuesta),
            )
        if respuesta.is_error:
            raise ApiOrigenNoDisponibleError(
                f"HTTP {respuesta.status_code} no transitorio: no se reintenta",
                codigo_http=respuesta.status_code,
            )

        try:
            # parse_float=Decimal: los importes nunca pasan por float.
            datos = json.loads(respuesta.content, parse_float=Decimal)
        except ValueError as error:
            raise ContratoOrigenInvalidoError("la respuesta de la API de origen no es JSON válido") from error

        sobre = validar_respuesta(datos)
        if sobre.page != pagina:
            raise ContratoOrigenInvalidoError(f"se pidió la página {pagina} y la API devolvió la {sobre.page}")

        logger.info(
            "Página obtenida de la API de origen",
            extra={
                "event": Evento.PAGINA_ORIGEN_OBTENIDA,
                "page": pagina,
                "items": len(sobre.items),
                "has_next": sobre.has_next,
                "duration_ms": round((time.perf_counter() - inicio) * 1000),
            },
        )
        return sobre

    def _calcular_espera(self, estado: RetryCallState) -> float:
        """Backoff exponencial con jitter, acotado y respetando Retry-After."""
        espera = espera_exponencial_con_jitter(estado.attempt_number, self._backoff_base, self._backoff_max)
        error = estado.outcome.exception() if estado.outcome else None
        if isinstance(error, _FalloTransitorioError) and error.retry_after is not None:
            espera = max(espera, error.retry_after)
        return min(espera, self._backoff_max)

    def _registrar_reintento(self, estado: RetryCallState) -> None:
        """Deja constancia en el log de cada reintento: cuál, cuánto espera y por qué."""
        error = estado.outcome.exception() if estado.outcome else None
        espera = estado.next_action.sleep if estado.next_action else 0.0
        logger.warning(
            "Fallo transitorio en la API de origen: se reintenta",
            extra={
                "event": Evento.REINTENTO_API_ORIGEN,
                "attempt": estado.attempt_number,
                "max_attempts": self._max_intentos,
                "wait_seconds": round(espera, 2),
                "reason": str(error),
            },
        )


def _leer_retry_after(respuesta: httpx.Response) -> float | None:
    """Segundos que pide esperar el servidor (solo el formato en segundos)."""
    valor = respuesta.headers.get(CABECERA_RETRY_AFTER)
    if valor is None:
        return None
    try:
        return max(float(valor), 0.0)
    except ValueError:
        return None
