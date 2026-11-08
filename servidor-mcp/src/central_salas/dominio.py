from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from central_salas.erros import ErroDeNegocio

SP = timezone(timedelta(hours=-3))
ABERTURA, FECHAMENTO = 8, 20
DURACAO_MAXIMA = timedelta(hours=2)
MAX_ALTERNATIVAS = 3

ERRO_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
ERRO_DURACAO = "Duracao acima do limite: a politica permite no maximo 2 horas"
ERRO_INTERVALO = "Intervalo invalido: fim deve ser posterior a inicio"
ERRO_FORMATO = "Intervalo invalido: informe inicio e fim em ISO 8601 com fuso"
ERRO_SEM_ALTERNATIVA = "Sem alternativas disponiveis no intervalo"


@dataclass(frozen=True)
class Sala:
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


@dataclass(frozen=True)
class Reserva:
    id: str
    sala: str
    inicio: datetime
    fim: datetime
    responsavel: str


def _dt(texto: str) -> datetime:
    try:
        valor = datetime.fromisoformat(texto)
    except (ValueError, TypeError) as err:
        raise ErroDeNegocio(ERRO_FORMATO) from err
    if valor.tzinfo is None:
        raise ErroDeNegocio(ERRO_FORMATO)
    return valor


def carregar_salas(path: Path) -> list[Sala]:
    return [Sala(**s) for s in json.loads(path.read_text(encoding="utf-8"))]


def carregar_reservas(path: Path) -> list[Reserva]:
    return [
        Reserva(r["id"], r["sala"], _dt(r["inicio"]), _dt(r["fim"]), r["responsavel"])
        for r in json.loads(path.read_text(encoding="utf-8"))
    ]


def versao_politica(path: Path) -> str:
    primeira = path.read_text(encoding="utf-8").splitlines()[0]
    return primeira.split(":", 1)[1].strip()


def validar(sala_id: str, inicio: str, fim: str, salas: list[Sala]) -> tuple[Sala, datetime, datetime]:
    sala = next((s for s in salas if s.id == sala_id), None)
    if sala is None:
        raise ErroDeNegocio(f"Sala inexistente: {sala_id}")
    i, f = _dt(inicio), _dt(fim)
    if f <= i:
        raise ErroDeNegocio(ERRO_INTERVALO)
    for t in (i, f):
        local = t.astimezone(SP)
        minutos = local.hour * 60 + local.minute
        if not ABERTURA * 60 <= minutos <= FECHAMENTO * 60 or local.date() != i.astimezone(SP).date():
            raise ErroDeNegocio(ERRO_JANELA)
    if f - i > DURACAO_MAXIMA:
        raise ErroDeNegocio(ERRO_DURACAO)
    return sala, i, f


class ReservaRepo:
    """Reservas em memoria; nao sobrevive a restart (por enunciado)."""

    def __init__(self, iniciais: list[Reserva]) -> None:
        self._reservas = list(iniciais)
        self._lock = threading.Lock()
        self._seq = max((int(r.id.split("-")[1]) for r in iniciais), default=0)

    def add(self, sala: str, inicio: datetime, fim: datetime, responsavel: str) -> Reserva:
        with self._lock:
            self._seq += 1
            r = Reserva(f"res-{self._seq:04d}", sala, inicio, fim, responsavel)
            self._reservas.append(r)
            return r

    def todas(self) -> list[Reserva]:
        return list(self._reservas)


def conflitos(repo: ReservaRepo, sala: str, inicio: datetime, fim: datetime) -> list[Reserva]:
    return [r for r in repo.todas() if r.sala == sala and r.inicio < fim and inicio < r.fim]


def alternativas(repo: ReservaRepo, salas: list[Sala], sala_id: str, inicio: datetime, fim: datetime) -> list[Sala]:
    pedida = next(s for s in salas if s.id == sala_id)
    livres = [
        s for s in salas
        if s.id != sala_id and s.capacidade >= pedida.capacidade and not conflitos(repo, s.id, inicio, fim)
    ]
    return sorted(livres, key=lambda s: (s.capacidade, s.id))[:MAX_ALTERNATIVAS]
