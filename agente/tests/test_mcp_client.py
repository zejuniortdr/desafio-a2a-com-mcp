import json

import httpx
import pytest
from agente_salas.mcp_client import McpClient, McpError

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
PAUSA = {
    "resultType": "input_required",
    "requestState": "ST",
    "inputRequests": {
        "k": {
            "method": "elicitation/create",
            "params": {"mode": "form", "requestedSchema": {"properties": {"sala": {"enum": ["a", "b"]}}}},
        }
    },
}


def cliente(respostas, vistos):
    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append((dict(req.headers), json.loads(req.content)))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "result": respostas.pop(0)})

    return McpClient("http://mcp/mcp", transport=httpx.MockTransport(handler))


def cliente_cru(resposta: httpx.Response) -> McpClient:
    return McpClient("http://mcp/mcp", transport=httpx.MockTransport(lambda req: resposta))


async def test_tools_list_manda_headers_e_meta():
    vistos = []
    c = cliente([{"tools": []}], vistos)

    await c.list_tools(TRACE)

    ((h, b),) = vistos
    assert h["mcp-method"] == "tools/list" and "mcp-name" not in h and h["mcp-protocol-version"] == "2026-07-28"
    meta = b["params"]["_meta"]
    assert meta["io.modelcontextprotocol/clientCapabilities"] == {"elicitation": {"form": {}}}
    assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert meta["traceparent"].split("-")[1] == TRACE


async def test_read_resource_manda_mcp_name():
    vistos = []
    c = cliente([{"contents": [{"text": "versao: 1"}]}], vistos)

    texto = await c.read_resource("politica://uso", TRACE)

    ((h, _),) = vistos
    assert texto == "versao: 1"
    assert h["mcp-method"] == "resources/read" and h["mcp-name"] == "politica://uso"


async def test_cada_chamada_tem_id_e_traceparent_proprios():
    vistos = []
    c = cliente([{"tools": []}, {"tools": []}], vistos)
    await c.list_tools(TRACE)

    await c.list_tools(TRACE)

    (_, b1), (_, b2) = vistos
    assert b1["id"] != b2["id"]
    assert b1["params"]["_meta"]["traceparent"] != b2["params"]["_meta"]["traceparent"]


async def test_input_required_cru():
    vistos = []
    c = cliente([PAUSA], vistos)

    r = await c.call_tool("reservar_sala", {"sala": "x"}, TRACE)

    assert (r.kind, r.input_key, r.enum, r.request_state) == ("input_required", "k", ["a", "b"], "ST")
    assert "requestState" not in vistos[0][1]["params"]


async def test_retry_ecoa_state_e_respostas():
    vistos = []
    c = cliente(
        [{"resultType": "complete", "isError": False, "content": [{"text": "ok"}], "structuredContent": {"sala": "a"}}],
        vistos,
    )
    respostas = {"k": {"action": "accept", "content": {"sala": "a"}}}

    r = await c.call_tool("reservar_sala", {"sala": "x"}, TRACE, respostas, "ST")

    assert r.kind == "complete" and not r.is_error and r.structured == {"sala": "a"}
    params = vistos[0][1]["params"]
    assert params["requestState"] == "ST" and params["inputResponses"] == respostas


async def test_enum_const():
    c = cliente(
        [
            {
                "resultType": "input_required",
                "requestState": "S",
                "inputRequests": {"k": {"params": {"requestedSchema": {"properties": {"sala": {"const": "z"}}}}}},
            }
        ],
        [],
    )

    r = await c.call_tool("t", {}, TRACE)

    assert r.enum == ["z"]


async def test_erro_jsonrpc():
    c = cliente_cru(httpx.Response(400, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "ruim"}}))

    with pytest.raises(McpError) as e:
        await c.call_tool("t", {}, TRACE)

    assert (e.value.code, e.value.message) == (-32602, "ruim")


@pytest.mark.parametrize("corpo", [{"detail": "Bad Gateway"}, ["lixo"], {"result": "nao e objeto"}])
async def test_corpo_json_que_nao_e_jsonrpc_vira_mcp_error(corpo):
    # Regressao: um 502 com corpo JSON sem result/error levantava KeyError e prendia a Task em WORKING.
    c = cliente_cru(httpx.Response(502, json=corpo))

    with pytest.raises(McpError) as e:
        await c.list_tools(TRACE)

    assert e.value.code == -32603 and "502" in e.value.message


async def test_resposta_que_nao_e_json_vira_mcp_error():
    c = cliente_cru(httpx.Response(502, text="<html>"))

    with pytest.raises(McpError) as e:
        await c.list_tools(TRACE)

    assert e.value.code == -32700


async def test_envelope_do_tools_call_e_is_error():
    vistos = []
    c = cliente([{"resultType": "complete", "isError": True, "content": [{"text": "a"}, {"text": "b"}]}], vistos)

    r = await c.call_tool("reservar_sala", {"sala": "x"}, TRACE)

    assert (r.kind, r.is_error, r.text, r.structured) == ("complete", True, "a b", None)
    h, b = vistos[0]
    assert (b["jsonrpc"], b["method"]) == ("2.0", "tools/call")
    assert (b["params"]["name"], b["params"]["arguments"]) == ("reservar_sala", {"sala": "x"})
    assert "inputResponses" not in b["params"]
    assert h["mcp-name"] == "reservar_sala" and h["content-type"] == "application/json"
    assert b["params"]["_meta"]["io.modelcontextprotocol/clientInfo"]["name"] == "agente-central-de-salas"
