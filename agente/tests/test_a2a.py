import json

import pytest
from starlette.testclient import TestClient

from agente_salas.a2a import criar_app
from agente_salas.card import build_card
from agente_salas.ponte import BridgeService
from agente_salas.tasks import TaskStore
from test_ponte import PEDIDO, TP, FakeMcp


@pytest.fixture
def client():
    store = TaskStore()
    return TestClient(criar_app(BridgeService(FakeMcp(), store), store, "http://127.0.0.1:7300"))


def send(client, texto, task_id=None, headers=None, id_=1):
    msg = {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": texto}]}
    if task_id:
        msg["taskId"] = task_id
    return client.post("/a2a", json={"jsonrpc": "2.0", "id": id_, "method": "SendMessage", "params": {"message": msg}}, headers=headers or {}).json()


def test_card(client):
    r = client.get("/.well-known/agent-card.json")
    assert r.status_code == 200 and r.json() == build_card("http://127.0.0.1:7300")
    i = r.json()["supportedInterfaces"][0]
    assert (i["url"], i["protocolBinding"], i["protocolVersion"]) == ("http://127.0.0.1:7300/a2a", "JSONRPC", "1.0")
    assert "reservar-sala" in [s["id"] for s in r.json()["skills"]]


def test_send_get_e_nao_vaza_request_state(client):
    r = send(client, PEDIDO, headers={"traceparent": TP})
    t = r["result"]["task"]
    assert t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED" and t["status"]["message"]["parts"][0]["text"] == "alternativas: sala-fusca, sala-mirante"
    assert t["history"][0]["messageId"] == "m1"
    g = client.post("/a2a", json={"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": t["id"]}}).json()
    assert g["result"]["task"]["id"] == t["id"] and g["result"]["task"]["contextId"] == t["contextId"]
    assert "STATE-SEGREDO" not in json.dumps(r) + json.dumps(g)
    c = send(client, "escolha=sala-mirante", t["id"])
    assert c["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED" and c["result"]["task"]["artifacts"][0]["name"] == "reserva"
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
        ({"id": 1}, -32600),
        ([1, 2], -32600),
    ],
)
def test_erros_jsonrpc(client, corpo, code):
    assert client.post("/a2a", json=corpo).json()["error"]["code"] == code


def test_json_invalido(client):
    assert client.post("/a2a", content=b"{nao json").json()["error"]["code"] == -32700


def test_send_em_task_terminal_e_erro(client):
    t = send(client, PEDIDO.replace("sala-garagem", "sala-porao"))["result"]["task"]
    assert t["status"]["state"] == "TASK_STATE_COMPLETED"
    assert send(client, "escolha=sala-fusca", t["id"])["error"]["code"] == -32004
