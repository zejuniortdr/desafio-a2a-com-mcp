"""Borda A2A v1.0 (binding JSON-RPC): card, SendMessage e GetTask. Traduz HTTP/JSON-RPC <-> BridgeService."""
from __future__ import annotations

from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agente_salas.card import build_card
from agente_salas.pedido import PedidoInvalido
from agente_salas.ponte import BridgeService
from agente_salas.tasks import Task, TaskNaoEncontrada, TaskStore, TaskTerminal

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
TASK_NOT_FOUND, UNSUPPORTED_OPERATION = -32001, -32004


def task_dto(t: Task) -> dict:
    """DTO explicito: so o que o cliente A2A pode ver. requestState/input_key/enum nunca saem daqui."""
    status: dict[str, Any] = {"state": t.state.value}
    if t.status_message:
        status["message"] = t.status_message
    return {"id": t.id, "contextId": t.context_id, "status": status, "history": t.history, "artifacts": t.artifacts}


def _erro(id_: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _texto_da_mensagem(msg: Any) -> str:
    if not isinstance(msg, dict) or not isinstance(msg.get("parts"), list):
        raise PedidoInvalido("params.message.parts ausente")
    partes = [p["text"] for p in msg["parts"] if isinstance(p, dict) and isinstance(p.get("text"), str)]
    if not partes:
        raise PedidoInvalido("a mensagem precisa de uma parte de texto")
    return " ".join(partes)


def criar_app(bridge: BridgeService, store: TaskStore, public_url: str) -> Starlette:
    async def card(_: Request) -> JSONResponse:
        return JSONResponse(build_card(public_url))

    async def send_message(params: dict, traceparent: str | None) -> dict:
        msg = params.get("message")
        texto = _texto_da_mensagem(msg)
        message_id = msg.get("messageId") if isinstance(msg.get("messageId"), str) else None
        task_id = msg.get("taskId")
        if task_id is None:
            task = await bridge.iniciar(texto, traceparent, message_id)
        elif isinstance(task_id, str):
            task = await bridge.continuar(task_id, texto, traceparent, message_id)
        else:
            raise PedidoInvalido("taskId deve ser string")
        return {"task": task_dto(task)}

    async def get_task(params: dict) -> dict:
        if not isinstance(params.get("id"), str):
            raise PedidoInvalido("params.id ausente")
        return {"task": task_dto(store.get(params["id"]))}

    async def rpc(request: Request) -> JSONResponse:
        try:
            corpo = await request.json()
        except ValueError:
            return _erro(None, PARSE_ERROR, "JSON invalido")
        if not isinstance(corpo, dict) or corpo.get("jsonrpc") != "2.0" or not isinstance(corpo.get("method"), str):
            return _erro(None, INVALID_REQUEST, "Requisicao JSON-RPC invalida")
        id_, metodo = corpo.get("id"), corpo["method"]
        params = corpo.get("params") if isinstance(corpo.get("params"), dict) else {}
        try:
            if metodo == "SendMessage":
                resultado = await send_message(params, request.headers.get("traceparent"))
            elif metodo == "GetTask":
                resultado = await get_task(params)
            else:
                return _erro(id_, METHOD_NOT_FOUND, f"Metodo nao suportado: {metodo}")
        except PedidoInvalido as e:
            return _erro(id_, INVALID_PARAMS, str(e))
        except TaskNaoEncontrada as e:
            return _erro(id_, TASK_NOT_FOUND, f"Task nao encontrada: {e}")
        except TaskTerminal as e:
            return _erro(id_, UNSUPPORTED_OPERATION, str(e))
        return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": resultado})

    return Starlette(routes=[
        Route("/.well-known/agent-card.json", card, methods=["GET"]),
        Route("/a2a", rpc, methods=["POST"]),
    ])
