import json

import pytest
from starlette.testclient import TestClient

from agente_salas.a2a import criar_app
from agente_salas.ponte import BridgeService
from agente_salas.tasks import TaskStore
from test_ponte import PEDIDO, PEDIDO_LIVRE, TP, TRACE, FakeMcp


@pytest.fixture
def mcp():
    return FakeMcp()


@pytest.fixture
def client(mcp):
    store = TaskStore()
    return TestClient(criar_app(BridgeService(mcp, store), store, "http://127.0.0.1:7300"))


def send(client, texto, task_id=None, headers=None, id_=1):
    msg = {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": texto}]}
    if task_id:
        msg["taskId"] = task_id
    return client.post("/a2a", json={"jsonrpc": "2.0", "id": id_, "method": "SendMessage", "params": {"message": msg}}, headers=headers or {}).json()


def get_task(client, task_id):
    return client.post("/a2a", json={"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task_id}}).json()


def test_card(client):
    r = client.get("/.well-known/agent-card.json")

    assert r.status_code == 200
    card = r.json()
    assert (card["name"], card["version"]) == ("Central de Salas", "1.0.0")
    assert card["supportedInterfaces"] == [
        {"url": "http://127.0.0.1:7300/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
    ]
    assert card["capabilities"] == {"streaming": False, "pushNotifications": False, "extendedAgentCard": False}
    assert card["defaultInputModes"] == card["defaultOutputModes"] == ["text/plain"]
    (skill,) = card["skills"]
    assert skill["id"] == "reservar-sala" and skill["examples"][0].startswith("reservar sala=")


def test_send_pausa_propaga_trace_e_nao_vaza_request_state(client, mcp):
    r = send(client, PEDIDO, headers={"traceparent": TP}, id_="req-7")

    assert (r["jsonrpc"], r["id"]) == ("2.0", "req-7")
    assert {c[1] for c in mcp.calls} == {TRACE}  # o header traceparent da borda A2A chega ao MCP
    t = r["result"]["task"]
    assert t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert t["status"]["message"]["parts"][0]["text"] == "alternativas: sala-fusca, sala-mirante"
    assert t["history"][0]["messageId"] == "m1"
    assert "STATE-SEGREDO" not in json.dumps(r)


def test_get_task_devolve_a_task_sem_request_state(client):
    t = send(client, PEDIDO)["result"]["task"]

    g = get_task(client, t["id"])

    assert g["result"]["task"]["id"] == t["id"] and g["result"]["task"]["contextId"] == t["contextId"]
    assert "STATE-SEGREDO" not in json.dumps(g)


def test_continuacao_conclui_sem_vazar_request_state(client):
    t = send(client, PEDIDO)["result"]["task"]

    c = send(client, "escolha=sala-mirante", t["id"])

    task = c["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED" and task["artifacts"][0]["name"] == "reserva"
    assert "STATE-SEGREDO" not in json.dumps(c)


@pytest.mark.parametrize(
    "corpo,code",
    [
        ({"jsonrpc": "2.0", "id": 1, "method": "Nada", "params": {}}, -32601),
        ({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}}, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": {"parts": [{"text": "bom dia"}]}}}, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": {"parts": [{"url": "x"}]}}}, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": {"taskId": 5, "parts": [{"text": "escolha=a"}]}}}, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "task-nada"}}, -32001),
        ({"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {}}, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": [1]}, -32602),
        ({"id": 1}, -32600),
    ],
)
def test_erros_jsonrpc_devolvem_o_id_do_pedido(client, corpo, code):
    r = client.post("/a2a", json=corpo).json()

    assert (r["jsonrpc"], r["id"], r["error"]["code"]) == ("2.0", 1, code)


@pytest.mark.parametrize("conteudo, code", [(b"[1, 2]", -32600), (b"{nao json", -32700)])
def test_sem_id_legivel_o_erro_vai_com_id_nulo(client, conteudo, code):
    r = client.post("/a2a", content=conteudo).json()

    assert (r["id"], r["error"]["code"]) == (None, code)


def test_send_em_task_terminal_e_erro(client):
    t = send(client, PEDIDO_LIVRE)["result"]["task"]

    r = send(client, "escolha=sala-fusca", t["id"])

    assert r["error"]["code"] == -32004
