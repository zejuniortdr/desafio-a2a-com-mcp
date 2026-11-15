from __future__ import annotations

import json

from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    ClientCapabilities,
    ElicitationCapability,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    FormElicitationCapability,
    InputRequiredResult,
)
from pydantic import BaseModel

from central_salas.dominio import (
    ERRO_SEM_ALTERNATIVA,
    Reserva,
    ReservaRepo,
    Sala,
    alternativas,
    conflitos,
    validar,
)
from central_salas.erros import ErroDeNegocio

CHAVE_ELICITATION = "escolha_de_sala"
MOTIVO_RECUSA = "Usuario recusou as salas alternativas"


class SalaOut(BaseModel):
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


class ListaDeSalas(BaseModel):
    salas: list[SalaOut]


class ReservaExistente(BaseModel):
    id: str
    sala: str
    inicio: str
    fim: str
    responsavel: str


class Disponibilidade(BaseModel):
    sala: str
    inicio: str
    fim: str
    livre: bool
    conflitos: list[ReservaExistente]


class ReservaOut(BaseModel):
    reserva: str | None
    reservado: bool
    sala: str
    inicio: str
    fim: str
    responsavel: str
    politica: str
    motivo: str | None


def _existente(r: Reserva) -> ReservaExistente:
    return ReservaExistente(
        id=r.id, sala=r.sala, inicio=r.inicio.isoformat(), fim=r.fim.isoformat(), responsavel=r.responsavel
    )


def _exigir_elicitation_form(ctx: Context) -> None:
    caps = ctx.client_capabilities
    el = caps.elicitation if caps is not None else None
    if el is not None and (el.form is not None or el.url is None):
        return
    faltando = ClientCapabilities(elicitation=ElicitationCapability(form=FormElicitationCapability()))
    raise MCPError(
        code=MISSING_REQUIRED_CLIENT_CAPABILITY,
        message="Client did not declare the form elicitation capability required by reservar_sala",
        data={"requiredCapabilities": faltando.model_dump(by_alias=True, mode="json", exclude_none=True)},
    )


def registrar_tools(srv: MCPServer, salas: list[Sala], repo: ReservaRepo, politica: str) -> None:
    def confirmada(r: Reserva) -> ReservaOut:
        return ReservaOut(
            reserva=r.id,
            reservado=True,
            sala=r.sala,
            inicio=r.inicio.isoformat(),
            fim=r.fim.isoformat(),
            responsavel=r.responsavel,
            politica=politica,
            motivo=None,
        )

    @srv.tool()
    def listar_salas() -> ListaDeSalas:
        """Lista todas as salas com capacidade e recursos."""
        return ListaDeSalas(salas=[SalaOut(**vars(s)) for s in salas])

    @srv.tool()
    def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Disponibilidade:
        """Diz se uma sala esta livre no intervalo, e quais reservas conflitam."""
        s, i, f = validar(sala, inicio, fim, salas)
        achados = conflitos(repo, s.id, i, f)
        return Disponibilidade(
            sala=s.id, inicio=inicio, fim=fim, livre=not achados, conflitos=[_existente(r) for r in achados]
        )

    @srv.tool()
    def reservar_sala(
        sala: str, inicio: str, fim: str, responsavel: str, ctx: Context
    ) -> ReservaOut | InputRequiredResult:
        """Reserva uma sala. Em conflito, pede ao cliente a escolha de uma sala alternativa."""
        if ctx.request_state is not None:
            return _retomar(ctx)
        s, i, f = validar(sala, inicio, fim, salas)
        reserva = repo.reservar_se_livre(s.id, i, f, responsavel)  # checagem e escrita atomicas
        if reserva is not None:
            return confirmada(reserva)
        alts = alternativas(repo, salas, s.id, i, f)
        if not alts:
            raise ErroDeNegocio(ERRO_SEM_ALTERNATIVA)
        _exigir_elicitation_form(ctx)
        oferecidas = [a.id for a in alts]
        campo = {"type": "string", "enum": oferecidas, "title": "Sala", "description": "Sala alternativa escolhida"}
        pedido = ElicitRequest(
            params=ElicitRequestFormParams(
                message="A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.",
                requested_schema={"type": "object", "properties": {"sala": campo}, "required": ["sala"]},
            )
        )
        # O pedido original viaja selado (AES-GCM, pelo SDK): no retry os argumentos do cliente nao valem.
        # As alternativas oferecidas vao junto: o retry so aceita o que foi de fato oferecido.
        selado = json.dumps(
            {"sala": sala, "inicio": inicio, "fim": fim, "responsavel": responsavel, "alternativas": oferecidas}
        )
        return InputRequiredResult(input_requests={CHAVE_ELICITATION: pedido}, request_state=selado)

    def _retomar(ctx: Context) -> ReservaOut:
        try:
            orig = json.loads(ctx.request_state or "")
            s, i, f = validar(orig["sala"], orig["inicio"], orig["fim"], salas)
            responsavel = orig["responsavel"]
            oferecidas = list(orig["alternativas"])
        except (ValueError, KeyError, TypeError) as err:
            raise MCPError(code=INVALID_PARAMS, message="requestState invalido") from err
        resposta = (ctx.input_responses or {}).get(CHAVE_ELICITATION)
        if not isinstance(resposta, ElicitResult):
            raise MCPError(code=INVALID_PARAMS, message=f"inputResponses sem a chave {CHAVE_ELICITATION!r}")
        if resposta.action != "accept":
            return ReservaOut(
                reserva=None,
                reservado=False,
                sala=s.id,
                inicio=orig["inicio"],
                fim=orig["fim"],
                responsavel=responsavel,
                politica=politica,
                motivo=MOTIVO_RECUSA,
            )
        escolhida = (resposta.content or {}).get("sala")
        # So vale o que foi oferecido (selado no state) e continua livre; a reserva e atomica.
        if not isinstance(escolhida, str) or escolhida not in oferecidas:
            raise ErroDeNegocio(ERRO_SEM_ALTERNATIVA)
        reserva = repo.reservar_se_livre(escolhida, i, f, responsavel)
        if reserva is None:
            raise ErroDeNegocio(ERRO_SEM_ALTERNATIVA)
        return confirmada(reserva)
