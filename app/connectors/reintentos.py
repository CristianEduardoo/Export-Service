"""Cálculo de esperas entre reintentos, común a todos los adaptadores."""

from __future__ import annotations

import random


def espera_exponencial_con_jitter(intento: int, base: float, maximo: float) -> float:
    """Segundos a esperar antes del siguiente intento.

    Backoff exponencial: el techo se duplica en cada intento (base, 2·base,
    4·base...) sin superar el máximo. Jitter "igual": la mitad del techo es fija
    y la otra mitad al azar, para que varios clientes que fallaron a la vez no
    reintenten todos en el mismo instante.
    """
    # 2.0 y no 2: int ** int puede ser int o float según el signo del exponente y mypy lo tipa como Any.
    techo: float = min(maximo, base * 2.0 ** (intento - 1))
    # Azar para repartir reintentos, no para criptografía.
    return techo / 2 + random.uniform(0, techo / 2)  # noqa: S311
