import pytest
from starlette.testclient import TestClient

from central_salas.app import criar_app
from central_salas.config import carregar_config

SECRET = "a" * 64
CAPS_ELIC = {"elicitation": {"form": {}}}
PROTO = "2026-07-28"


@pytest.fixture
def cfg():
    return carregar_config({"REQUEST_STATE_SECRET": SECRET})


@pytest.fixture
def client(cfg):
    with TestClient(criar_app(cfg), base_url="http://127.0.0.1:7301") as c:
        yield c


def rpc(client, metodo, params, nome=None, caps=CAPS_ELIC, omitir=None, id_="1"):
    meta = {"io.modelcontextprotocol/protocolVersion": PROTO, "io.modelcontextprotocol/clientCapabilities": caps}
    if omitir:
        meta.pop(f"io.modelcontextprotocol/{omitir}")
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTO, "Mcp-Method": metodo}
    if nome:
        headers["Mcp-Name"] = nome
    corpo = {"jsonrpc": "2.0", "id": id_, "method": metodo, "params": {**params, "_meta": meta}}
    r = client.post("/mcp", json=corpo, headers=headers)
    return r.status_code, r.json()


def call(client, nome, args, extra=None, **kw):
    return rpc(client, "tools/call", {"name": nome, "arguments": args, **(extra or {})}, nome, **kw)


def texto_de(res):
    return " ".join(p["text"] for p in res["content"])
