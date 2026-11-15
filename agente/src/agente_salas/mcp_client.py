from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

PROTOCOLO = "2026-07-28"
CLIENT_INFO = {"name": "agente-central-de-salas", "version": "1.0.0"}
CAPABILITIES: dict[str, dict[str, dict[str, Any]]] = {"elicitation": {"form": {}}}


class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


@dataclass
class McpResult:
    kind: Literal["complete", "input_required"]
    is_error: bool = False
    text: str = ""
    structured: dict | None = None
    input_key: str | None = None
    enum: list[str] = field(default_factory=list)
    request_state: str | None = None


class McpPort(Protocol):
    async def list_tools(self, trace_id: str) -> list[dict]: ...
    async def read_resource(self, uri: str, trace_id: str) -> str: ...
    async def call_tool(
        self,
        name: str,
        arguments: dict,
        trace_id: str,
        input_responses: dict | None = None,
        request_state: str | None = None,
    ) -> McpResult: ...


def novo_traceparent(trace_id: str) -> str:
    return f"00-{trace_id}-{secrets.token_hex(8)}-01"


class McpClient:
    """Cliente MCP cru (JSON-RPC sobre HTTP): enxerga o input_required sem callback de elicitation."""

    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = url
        self._http = httpx.AsyncClient(transport=transport, timeout=30)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _rpc(self, metodo: str, params: dict, trace_id: str, nome: str | None = None) -> dict:
        meta = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOLO,
            "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
            "io.modelcontextprotocol/clientCapabilities": CAPABILITIES,
            "traceparent": novo_traceparent(trace_id),
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOLO,
            "Mcp-Method": metodo,
        }
        if nome:
            headers["Mcp-Name"] = nome
        corpo = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:12], "method": metodo, "params": {**params, "_meta": meta}}
        resp = await self._http.post(self._url, json=corpo, headers=headers)
        try:
            dados = resp.json()
        except ValueError as err:
            raise McpError(-32700, f"resposta MCP nao e JSON (HTTP {resp.status_code})") from err
        erro = dados.get("error") if isinstance(dados, dict) else None
        if isinstance(erro, dict):
            raise McpError(erro.get("code", -32603), erro.get("message", ""), erro.get("data"))
        resultado = dados.get("result") if isinstance(dados, dict) else None
        if not isinstance(resultado, dict):  # ex.: 502 de um proxy com corpo JSON que nao e JSON-RPC
            raise McpError(-32603, f"resposta MCP fora do formato JSON-RPC (HTTP {resp.status_code})")
        return resultado

    async def list_tools(self, trace_id: str) -> list[dict]:
        return (await self._rpc("tools/list", {}, trace_id))["tools"]

    async def read_resource(self, uri: str, trace_id: str) -> str:
        r = await self._rpc("resources/read", {"uri": uri}, trace_id, nome=uri)
        return r["contents"][0]["text"]

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        trace_id: str,
        input_responses: dict | None = None,
        request_state: str | None = None,
    ) -> McpResult:
        params: dict = {"name": name, "arguments": arguments}
        if input_responses is not None:
            params["inputResponses"] = input_responses
        if request_state is not None:
            params["requestState"] = request_state
        r = await self._rpc("tools/call", params, trace_id, nome=name)
        if r.get("resultType") == "input_required":
            chave, pedido = next(iter(r["inputRequests"].items()))
            campo = pedido["params"]["requestedSchema"]["properties"]["sala"]
            enum = campo.get("enum") or ([campo["const"]] if "const" in campo else [])
            return McpResult("input_required", input_key=chave, enum=list(enum), request_state=r["requestState"])
        return McpResult(
            "complete",
            is_error=bool(r.get("isError")),
            text=" ".join(p.get("text", "") for p in r.get("content", [])),
            structured=r.get("structuredContent"),
        )
