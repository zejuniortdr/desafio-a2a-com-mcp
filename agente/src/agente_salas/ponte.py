"""A ponte: traduz o MRTR do MCP (input_required + requestState) na Task A2A (INPUT_REQUIRED) e de volta.

E o unico modulo que conhece os dois protocolos. Nao ha regra de sala aqui: conflito, politica e
alternativas sao decisao do servidor MCP; o agente so traduz protocolo.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict

import httpx

from agente_salas.mcp_client import McpError, McpPort, McpResult
from agente_salas.pedido import Escolha, PedidoInvalido, PedidoReserva, parse_pedido
from agente_salas.tasks import TERMINAIS, Task, TaskState, TaskStore, TaskTerminal, novo_id

TOOL_RESERVA = "reservar_sala"
URI_POLITICA = "politica://uso"
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def trace_id_de(traceparent: str | None) -> str | None:
    m = _TRACEPARENT.match((traceparent or "").strip().lower())
    return m[1] if m and m[1] != "0" * 32 else None


def _versao(politica_md: str) -> str:
    return politica_md.splitlines()[0].split(":", 1)[1].strip()


def _alternativas(enum: list[str]) -> str:
    return "alternativas: " + ", ".join(enum)


def _descrever(err: Exception) -> str:
    if isinstance(err, McpError):
        return f"Erro do servidor MCP ({err.code}): {err.message}"
    return f"Servidor MCP indisponivel: {err}"


class BridgeService:
    def __init__(self, mcp: McpPort, store: TaskStore) -> None:
        self._mcp = mcp
        self._store = store

    async def iniciar(self, texto: str, traceparent: str | None, message_id: str | None = None) -> Task:
        pedido = parse_pedido(texto)
        if not isinstance(pedido, PedidoReserva):
            raise PedidoInvalido("Sem taskId nao ha pausa para responder: envie 'reservar sala=... inicio=... fim=... responsavel=...'")
        task = self._store.create(trace_id_de(traceparent) or secrets.token_hex(16))
        task.mensagem("ROLE_USER", texto, com_ids=False, message_id=message_id)
        task.transicionar(TaskState.WORKING)
        task.pedido = pedido
        try:
            tools = await self._mcp.list_tools(task.trace_id)  # descoberta em runtime, nunca lista fixa
            if TOOL_RESERVA not in {t["name"] for t in tools}:
                return self._falhar(task, f"Ferramenta {TOOL_RESERVA} nao encontrada no servidor MCP")
            politica = _versao(await self._mcp.read_resource(URI_POLITICA, task.trace_id))
            resultado = await self._mcp.call_tool(TOOL_RESERVA, asdict(pedido), task.trace_id)
        except (McpError, httpx.HTTPError) as err:
            return self._falhar(task, _descrever(err))
        return self._aplicar(task, resultado, politica)

    async def continuar(self, task_id: str, texto: str, traceparent: str | None, message_id: str | None = None) -> Task:
        task = self._store.get(task_id)
        if task.state in TERMINAIS:
            raise TaskTerminal(f"Task {task.id} ja esta em estado terminal ({task.state.value})")
        if task.state != TaskState.INPUT_REQUIRED:
            raise PedidoInvalido(f"Task {task.id} esta em {task.state.value} e nao aceita novas mensagens agora")
        escolha = parse_pedido(texto)
        if not isinstance(escolha, Escolha):
            raise PedidoInvalido(f"Task {task.id} aguarda a resposta da pausa: escolha=<id da sala> ou escolha=recusar")
        task.mensagem("ROLE_USER", texto, message_id=message_id)
        if escolha.valor != "recusar" and escolha.valor not in task.enum:
            task.transicionar(TaskState.INPUT_REQUIRED, _alternativas(task.enum))
            return task
        trace_id = trace_id_de(traceparent) or task.trace_id
        resposta = {"action": "decline"} if escolha.valor == "recusar" else {"action": "accept", "content": {"sala": escolha.valor}}
        chave, estado, pedido = task.input_key, task.request_state, task.pedido
        assert chave is not None and estado is not None and pedido is not None
        task.transicionar(TaskState.WORKING)  # sem await entre a checagem de estado e aqui: sem corrida
        try:
            politica = _versao(await self._mcp.read_resource(URI_POLITICA, trace_id))
            # A PONTE (volta): o requestState guardado nesta Task e ecoado sem abrir, com id JSON-RPC novo
            # (o McpClient gera um id por request) e inputResponses sob a mesma chave que o servidor atribuiu.
            resultado = await self._mcp.call_tool(TOOL_RESERVA, asdict(pedido), trace_id, {chave: resposta}, estado)
        except (McpError, httpx.HTTPError) as err:
            return self._falhar(task, _descrever(err))
        task.limpar_pausa()
        return self._aplicar(task, resultado, politica)

    def _aplicar(self, task: Task, r: McpResult, politica: str) -> Task:
        if r.kind == "input_required":
            # A PONTE (ida): input_required do MCP vira Task INPUT_REQUIRED; o requestState fica preso a esta
            # Task (nunca vai ao cliente A2A) e a mensagem lista as alternativas na ordem do enum.
            task.input_key, task.request_state, task.enum = r.input_key, r.request_state, r.enum
            task.transicionar(TaskState.INPUT_REQUIRED, _alternativas(r.enum))
            return task
        if r.is_error:
            return self._falhar(task, r.text)
        s = r.structured or {}
        if not s.get("reservado"):
            task.transicionar(TaskState.CANCELED, s.get("motivo") or "Reserva recusada")
            return task
        corpo = {k: s[k] for k in ("reserva", "sala", "inicio", "fim", "responsavel")} | {"politica": politica}
        task.artifacts.append({"artifactId": novo_id("art"), "name": "reserva", "parts": [{"text": json.dumps(corpo)}]})
        task.transicionar(TaskState.COMPLETED, f"Reserva {s['reserva']} confirmada na {s['sala']}.")
        return task

    def _falhar(self, task: Task, texto: str) -> Task:
        task.limpar_pausa()
        task.transicionar(TaskState.FAILED, texto)
        return task
