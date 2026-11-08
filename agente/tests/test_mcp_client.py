import json

import httpx
import pytest

from agente_salas.mcp_client import McpClient, McpError

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def cliente(respostas, vistos):
    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append((dict(req.headers), json.loads(req.content)))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "result": respostas.pop(0)})

    return McpClient("http://mcp/mcp", transport=httpx.MockTransport(handler))


async def test_meta_headers_e_trace():
    vistos = []
    c = cliente([{"tools": []}, {"contents": [{"text": "versao: 1"}]}], vistos)
    await c.list_tools(TRACE)
    await c.read_resource("politica://uso", TRACE)
    (h1, b1), (h2, b2) = vistos
    assert h1["mcp-method"] == "tools/list" and "mcp-name" not in h1 and h1["mcp-protocol-version"] == "2026-07-28"
    assert h2["mcp-method"] == "resources/read" and h2["mcp-name"] == "politica://uso"
    meta = b1["params"]["_meta"]
    assert meta["io.modelcontextprotocol/clientCapabilities"] == {"elicitation": {"form": {}}}
    assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert meta["traceparent"].split("-")[1] == TRACE
    assert b1["id"] != b2["id"] and b1["params"]["_meta"]["traceparent"] != b2["params"]["_meta"]["traceparent"]


async def test_input_required_cru_e_retry_com_id_novo():
    vistos = []
    c = cliente(
        [
            {"resultType": "input_required", "requestState": "ST", "inputRequests": {"k": {"method": "elicitation/create", "params": {"mode": "form", "requestedSchema": {"properties": {"sala": {"enum": ["a", "b"]}}}}}}},
            {"resultType": "complete", "isError": False, "content": [{"text": "ok"}], "structuredContent": {"sala": "a"}},
        ],
        vistos,
    )
    r1 = await c.call_tool("reservar_sala", {"sala": "x"}, TRACE)
    assert (r1.kind, r1.input_key, r1.enum, r1.request_state) == ("input_required", "k", ["a", "b"], "ST")
    r2 = await c.call_tool("reservar_sala", {"sala": "x"}, TRACE, {r1.input_key: {"action": "accept", "content": {"sala": "a"}}}, r1.request_state)
    assert r2.kind == "complete" and not r2.is_error and r2.structured == {"sala": "a"}
    (_, b1), (_, b2) = vistos
    assert b1["id"] != b2["id"]
    assert b2["params"]["requestState"] == "ST" and "k" in b2["params"]["inputResponses"]
    assert "requestState" not in b1["params"]


async def test_enum_const():
    c = cliente([{"resultType": "input_required", "requestState": "S", "inputRequests": {"k": {"params": {"requestedSchema": {"properties": {"sala": {"const": "z"}}}}}}}], [])
    assert (await c.call_tool("t", {}, TRACE)).enum == ["z"]


async def test_erro_jsonrpc():
    def handler(req):
        return httpx.Response(400, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "ruim"}})

    c = McpClient("http://mcp/mcp", transport=httpx.MockTransport(handler))
    with pytest.raises(McpError) as e:
        await c.call_tool("t", {}, TRACE)
    assert e.value.code == -32602
