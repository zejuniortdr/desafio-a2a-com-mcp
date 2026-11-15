from __future__ import annotations

import re
from dataclasses import dataclass


class PedidoInvalido(Exception):
    pass


@dataclass(frozen=True)
class PedidoReserva:
    sala: str
    inicio: str
    fim: str
    responsavel: str


@dataclass(frozen=True)
class Escolha:
    valor: str  # id de sala ou "recusar"


_RESERVA = re.compile(r"^reservar\s+sala=(\S+)\s+inicio=(\S+)\s+fim=(\S+)\s+responsavel=(.+)$")
_ESCOLHA = re.compile(r"^escolha=(\S+)$")


def parse_pedido(texto: str) -> PedidoReserva | Escolha:
    t = (texto or "").strip()
    if m := _RESERVA.match(t):
        return PedidoReserva(m[1], m[2], m[3], m[4].strip())
    if m := _ESCOLHA.match(t):
        return Escolha(m[1])
    raise PedidoInvalido(
        "Formato invalido. Use: reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome> "
        "ou, para responder a uma pausa: escolha=<id da sala> | escolha=recusar"
    )
