"""Contratos exatos (textos, chaves, codigos) que o validador e o enunciado cobram. Complementa os demais testes."""

import json
import logging
import re
import sys

import httpx
import pytest
from agente_salas import main as main_mod
from agente_salas import tasks
from agente_salas.a2a import criar_app
from agente_salas.card import build_card
from agente_salas.config import Config, carregar_config
from agente_salas.mcp_client import McpClient, McpError, McpResult, novo_traceparent
from agente_salas.pedido import PedidoInvalido, parse_pedido
from agente_salas.ponte import BridgeService, _versao, trace_id_de
from agente_salas.tasks import Task, TaskNaoEncontrada, TaskState, TaskStore, TaskTerminal, novo_id
from starlette.testclient import TestClient
from test_ponte import PEDIDO, PEDIDO_LIVRE, TP, TRACE, FakeMcp

# ---------- card ----------


def test_card_exato():
    # Arrange / Act
    card = build_card("http://h:1")

    # Assert
    assert card == {
        "name": "Central de Salas",
        "description": "Reserva salas de reuniao da Hill Valley Tech.",
        "provider": {"organization": "Hill Valley Tech", "url": "https://hillvalley.example"},
        "version": "1.0.0",
        "supportedInterfaces": [{"url": "http://h:1/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "reservar-sala",
                "name": "Reservar sala",
                "description": "Reserva uma sala em um intervalo. Se houver conflito, pergunta qual alternativa usar.",
                "tags": ["salas", "agenda"],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
                "examples": [
                    "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
                ],
            }
        ],
    }


# ---------- config e main ----------


def test_config_padroes_do_enunciado():
    # Arrange / Act
    cfg = carregar_config({})

    # Assert
    assert cfg == Config(7300, "http://127.0.0.1:7301/mcp", "http://127.0.0.1:7300")


def test_config_le_o_ambiente_e_tira_a_barra_final():
    # Arrange
    env = {"A2A_PORT": "8000", "MCP_URL": "http://m:9/mcp", "PUBLIC_URL": "http://pub:8000///"}

    # Act
    cfg = carregar_config(env)

    # Assert
    assert cfg == Config(8000, "http://m:9/mcp", "http://pub:8000")


def test_config_sem_argumento_usa_os_environ(monkeypatch):
    # Arrange
    monkeypatch.setenv("A2A_PORT", "8123")

    # Act
    cfg = carregar_config()

    # Assert
    assert cfg.port == 8123 and cfg.public_url == "http://127.0.0.1:8123"


def test_montar_liga_card_a_public_url_da_config():
    # Arrange
    cfg = Config(7300, "http://m/mcp", "http://pub:7300")

    # Act
    r = TestClient(main_mod.montar(cfg)).get("/.well-known/agent-card.json")

    # Assert
    assert r.json()["supportedInterfaces"][0]["url"] == "http://pub:7300/a2a"


def test_main_loga_no_stderr_e_sobe_uvicorn_em_loopback(monkeypatch):
    # Arrange
    monkeypatch.setenv("A2A_PORT", "7777")
    log_calls, run_args, run_kw = [], [], {}
    monkeypatch.setattr(main_mod.logging, "basicConfig", lambda **kw: log_calls.append(kw))
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda *a, **kw: (run_args.extend(a), run_kw.update(kw)))

    # Act
    main_mod.main()

    # Assert
    assert {"stream": sys.stderr, "level": logging.INFO} in log_calls
    assert run_kw == {"host": "127.0.0.1", "port": 7777, "log_level": "warning"}
    assert len(run_args) == 1 and run_args[0].routes


# ---------- borda A2A: erros exatos ----------


@pytest.fixture
def mcp():
    return FakeMcp()


@pytest.fixture
def client(mcp):
    store = TaskStore()
    return TestClient(criar_app(BridgeService(mcp, store), store, "http://127.0.0.1:7300"))


def rpc(client, metodo, params, id_=1, **extra):
    return client.post("/a2a", json={"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params}, **extra).json()


def msg(texto="x", **kw):
    return {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": texto}], **kw}


@pytest.mark.parametrize(
    "metodo,params,code,mensagem",
    [
        ("SendMessage", {}, -32602, "params.message ausente"),
        ("SendMessage", {"message": "x"}, -32602, "params.message ausente"),
        ("SendMessage", {"message": {}}, -32602, "params.message.parts ausente"),
        ("SendMessage", {"message": {"parts": [{"url": "u"}]}}, -32602, "a mensagem precisa de uma parte de texto"),
        ("SendMessage", {"message": msg(taskId=5)}, -32602, "taskId deve ser string"),
        ("GetTask", {}, -32602, "params.id ausente"),
        ("GetTask", {"id": 7}, -32602, "params.id ausente"),
        ("GetTask", {"id": "task-nada"}, -32001, "Task nao encontrada: task-nada"),
        ("Nada", {}, -32601, "Metodo nao suportado: Nada"),
    ],
)
def test_erros_a2a_tem_codigo_e_mensagem_exatos(client, metodo, params, code, mensagem):
    # Arrange / Act
    r = rpc(client, metodo, params)

    # Assert
    assert r["error"]["code"] == code
    assert r["error"]["message"] == mensagem


def test_pedido_de_formato_invalido_tem_mensagem_exata(client):
    # Arrange / Act
    r = rpc(client, "SendMessage", {"message": msg("bom dia")})

    # Assert
    assert r["error"]["message"] == (
        "Formato invalido. Use: reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome> "
        "ou, para responder a uma pausa: escolha=<id da sala> | escolha=recusar"
    )


def test_sem_params_o_metodo_roda_com_params_vazio(client):
    # Arrange / Act
    r = client.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "GetTask"}).json()

    # Assert
    assert r["error"] == {"code": -32602, "message": "params.id ausente"}


def test_params_que_nao_e_objeto(client):
    # Arrange / Act
    r = client.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": [1]}).json()

    # Assert
    assert r["error"] == {"code": -32602, "message": "params deve ser um objeto"}


@pytest.mark.parametrize(
    "corpo,id_",
    [
        ({"jsonrpc": "1.0", "id": 3, "method": "GetTask"}, 3),
        ({"jsonrpc": "2.0", "id": 3, "method": 9}, 3),
        ({"jsonrpc": "2.0", "id": 3}, 3),
        ([1], None),
    ],
)
def test_requisicao_jsonrpc_invalida(client, corpo, id_):
    # Arrange / Act
    r = client.post("/a2a", json=corpo).json()

    # Assert
    assert r == {"jsonrpc": "2.0", "id": id_, "error": {"code": -32600, "message": "Requisicao JSON-RPC invalida"}}


def test_json_invalido(client):
    # Arrange / Act
    r = client.post("/a2a", content=b"{x").json()

    # Assert
    assert r == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalido"}}


def test_task_terminal_devolve_32004_com_o_id_do_pedido_e_mensagem(client):
    # Arrange
    t = rpc(client, "SendMessage", {"message": msg(PEDIDO_LIVRE)})["result"]["task"]

    # Act
    r = rpc(client, "SendMessage", {"message": msg("escolha=x", taskId=t["id"])}, id_="abc")

    # Assert
    assert r["id"] == "abc" and r["error"]["code"] == -32004
    assert r["error"]["message"] == f"Task {t['id']} ja esta em estado terminal (TASK_STATE_COMPLETED)"


def test_partes_de_texto_sao_unidas_por_espaco(client, mcp):
    # Arrange
    partes = [{"text": "reservar sala=sala-porao"}, {"url": "ignorada"}, {"text": PEDIDO_LIVRE.split(" ", 2)[2]}]

    # Act
    r = rpc(client, "SendMessage", {"message": {"parts": partes}})

    # Assert
    assert r["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_message_id_que_nao_e_string_e_ignorado(client):
    # Arrange / Act
    t = rpc(client, "SendMessage", {"message": msg(PEDIDO_LIVRE, messageId=5)})["result"]["task"]

    # Assert
    assert isinstance(t["history"][0]["messageId"], str) and t["history"][0]["messageId"].startswith("msg-")


def test_continuacao_leva_traceparent_e_message_id_do_cliente(client, mcp):
    # Arrange
    t = rpc(client, "SendMessage", {"message": msg(PEDIDO)})["result"]["task"]
    outro = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    # Act
    r = rpc(
        client,
        "SendMessage",
        {"message": msg("escolha=sala-mirante", taskId=t["id"], messageId="m-continua")},
        headers={"traceparent": f"00-{outro}-00f067aa0ba902b7-01"},
    )

    # Assert
    task = r["result"]["task"]
    assert any(m["messageId"] == "m-continua" for m in task["history"])
    assert mcp.calls[-1][1] == outro


def test_metodos_http_errados_dao_405(client):
    # Arrange / Act / Assert
    assert client.post("/.well-known/agent-card.json").status_code == 405
    assert client.get("/a2a").status_code == 405


# ---------- cliente MCP ----------


def cliente_http(resposta, vistos):
    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return (
            resposta(req)
            if callable(resposta)
            else httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "result": resposta})
        )

    return McpClient("http://mcp/mcp", transport=httpx.MockTransport(handler))


def test_novo_traceparent_tem_formato_w3c():
    # Arrange / Act
    tp = novo_traceparent(TRACE)

    # Assert
    assert re.fullmatch(rf"00-{TRACE}-[0-9a-f]{{16}}-01", tp)


def test_cliente_tem_timeout_de_30s():
    # Arrange / Act
    c = McpClient("http://mcp/mcp")

    # Assert
    assert c._http.timeout == httpx.Timeout(30)


async def test_cabecalhos_e_corpo_exatos():
    # Arrange
    vistos = []
    c = cliente_http({"contents": [{"text": "versao: 1"}]}, vistos)

    # Act
    await c.read_resource("politica://uso", TRACE)

    # Assert
    req = vistos[0]
    nomes = {k.decode() for k, _ in req.headers.raw}
    assert {"Content-Type", "Accept", "MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"} <= nomes
    assert {n.lower() for n in nomes} <= {
        "content-type",
        "accept",
        "mcp-protocol-version",
        "mcp-method",
        "mcp-name",
        "host",
        "content-length",
        "accept-encoding",
        "connection",
        "user-agent",
    }
    assert req.headers["accept"] == "application/json, text/event-stream"
    corpo = json.loads(req.content)
    assert re.fullmatch(r"[0-9a-f]{12}", corpo["id"])
    assert corpo["params"]["uri"] == "politica://uso"
    assert corpo["params"]["_meta"]["traceparent"].startswith(f"00-{TRACE}-")
    assert req.headers["mcp-name"] == "politica://uso"


async def test_tools_list_nao_manda_mcp_name():
    # Arrange
    vistos = []
    c = cliente_http({"tools": []}, vistos)

    # Act
    await c.list_tools(TRACE)

    # Assert
    assert "mcp-name" not in vistos[0].headers and vistos[0].headers["mcp-method"] == "tools/list"


async def test_call_tool_propaga_trace():
    # Arrange
    vistos = []
    c = cliente_http({"content": []}, vistos)

    # Act
    await c.call_tool("reservar_sala", {}, TRACE)

    # Assert
    assert json.loads(vistos[0].content)["params"]["_meta"]["traceparent"].startswith(f"00-{TRACE}-")


async def test_resposta_nao_json_tem_mensagem_com_status_http():
    # Arrange
    c = cliente_http(lambda r: httpx.Response(502, content=b"<html>"), [])

    # Act
    with pytest.raises(McpError) as e:
        await c.list_tools(TRACE)

    # Assert
    assert (e.value.code, e.value.message) == (-32700, "resposta MCP nao e JSON (HTTP 502)")
    assert str(e.value) == "resposta MCP nao e JSON (HTTP 502)"


async def test_erro_jsonrpc_propaga_code_message_e_data():
    # Arrange
    c = cliente_http(
        lambda r: httpx.Response(400, json={"error": {"code": -32021, "message": "faltou", "data": {"x": 1}}}), []
    )

    # Act
    with pytest.raises(McpError) as e:
        await c.list_tools(TRACE)

    # Assert
    assert (e.value.code, e.value.message, e.value.data) == (-32021, "faltou", {"x": 1})


async def test_erro_jsonrpc_sem_campos_usa_defaults():
    # Arrange
    c = cliente_http(lambda r: httpx.Response(500, json={"error": {}}), [])

    # Act
    with pytest.raises(McpError) as e:
        await c.list_tools(TRACE)

    # Assert
    assert (e.value.code, e.value.message, e.value.data) == (-32603, "", None)


async def test_call_tool_conteudo_sem_texto_ou_ausente():
    # Arrange
    c1 = cliente_http({"content": [{"type": "image"}, {"text": "a"}]}, [])
    c2 = cliente_http({}, [])

    # Act
    r1 = await c1.call_tool("t", {}, TRACE)
    r2 = await c2.call_tool("t", {}, TRACE)

    # Assert
    assert r1.text == " a" and r2.text == ""


async def test_pausa_sem_enum_nem_const_tem_lista_vazia():
    # Arrange
    pausa = {
        "resultType": "input_required",
        "requestState": "S",
        "inputRequests": {"k": {"params": {"requestedSchema": {"properties": {"sala": {"type": "string"}}}}}},
    }
    c = cliente_http(pausa, [])

    # Act
    r = await c.call_tool("t", {}, TRACE)

    # Assert
    assert r.enum == []


# ---------- pedido ----------


def test_pedido_invalido_tem_mensagem_exata():
    # Arrange / Act
    with pytest.raises(PedidoInvalido) as e:
        parse_pedido("oi")

    # Assert
    assert str(e.value) == (
        "Formato invalido. Use: reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome> "
        "ou, para responder a uma pausa: escolha=<id da sala> | escolha=recusar"
    )


# ---------- ponte ----------


class McpRegistraUri(FakeMcp):
    def __init__(self):
        super().__init__()
        self.uris = []

    async def read_resource(self, uri, trace_id):
        self.uris.append(uri)
        return await super().read_resource(uri, trace_id)


@pytest.fixture
def ponte(mcp):
    return BridgeService(mcp, TaskStore())


@pytest.mark.parametrize(
    "md,esperado",
    [("versao:2026-11-01", "2026-11-01"), ("versao: 12:30:45", "12:30:45"), ("versao:   x  \n\n- r: y", "x")],
)
def test_versao_corta_so_no_primeiro_dois_pontos(md, esperado):
    # Arrange / Act / Assert
    assert _versao(md) == esperado


async def test_a_politica_e_lida_pela_uri_certa_na_ida_e_na_volta():
    # Arrange
    mcp = McpRegistraUri()
    ponte = BridgeService(mcp, TaskStore())

    # Act
    t = await ponte.iniciar(PEDIDO, TP)
    await ponte.continuar(t.id, "escolha=sala-mirante", None)

    # Assert
    assert mcp.uris == ["politica://uso", "politica://uso"]


async def test_pedido_inicial_vira_mensagem_de_usuario_sem_ids(ponte):
    # Arrange / Act
    t = await ponte.iniciar(PEDIDO_LIVRE, TP, "m-ini")

    # Assert
    assert t.history[0] == {"messageId": "m-ini", "role": "ROLE_USER", "parts": [{"text": PEDIDO_LIVRE}]}


async def test_resposta_do_usuario_entra_no_historico_com_ids(ponte):
    # Arrange
    t = await ponte.iniciar(PEDIDO, TP)

    # Act
    await ponte.continuar(t.id, "escolha=sala-mirante", None, "m-esc")

    # Assert
    user = [m for m in t.history if m["role"] == "ROLE_USER"][1]
    assert user == {
        "messageId": "m-esc",
        "role": "ROLE_USER",
        "parts": [{"text": "escolha=sala-mirante"}],
        "taskId": t.id,
        "contextId": t.context_id,
    }


async def test_mensagens_de_recusa_de_pedido(ponte):
    # Arrange
    pausada = await ponte.iniciar(PEDIDO, TP)

    # Act
    with pytest.raises(PedidoInvalido) as sem_task:
        await ponte.iniciar("escolha=x", None)
    with pytest.raises(PedidoInvalido) as sem_escolha:
        await ponte.continuar(pausada.id, PEDIDO, None)

    # Assert
    assert (
        str(sem_task.value)
        == "Sem taskId nao ha pausa para responder: envie 'reservar sala=... inicio=... fim=... responsavel=...'"
    )
    assert (
        str(sem_escolha.value)
        == f"Task {pausada.id} aguarda a resposta da pausa: escolha=<id da sala> ou escolha=recusar"
    )


async def test_mensagens_de_estado_invalido_na_continuacao(ponte):
    # Arrange
    concluida = await ponte.iniciar(PEDIDO_LIVRE, TP)
    trabalhando = Task("t-w", "c-w", TaskState.WORKING)
    ponte._store._tasks["t-w"] = trabalhando

    # Act
    with pytest.raises(TaskTerminal) as terminal:
        await ponte.continuar(concluida.id, "escolha=a", None)
    with pytest.raises(PedidoInvalido) as ocupada:
        await ponte.continuar("t-w", "escolha=a", None)

    # Assert
    assert str(terminal.value) == f"Task {concluida.id} ja esta em estado terminal (TASK_STATE_COMPLETED)"
    assert str(ocupada.value) == "Task t-w esta em TASK_STATE_WORKING e nao aceita novas mensagens agora"


async def test_pausa_inconsistente_e_recusada_por_invariante(ponte):
    # Arrange: input_key ausente, mas requestState e pedido presentes
    pedido = parse_pedido(PEDIDO)
    t = Task("t-i", "c-i", TaskState.INPUT_REQUIRED, request_state="S", pedido=pedido, enum=["sala-fusca"])
    ponte._store._tasks["t-i"] = t

    # Act / Assert
    with pytest.raises(AssertionError):
        await ponte.continuar("t-i", "escolha=sala-fusca", None)


async def test_recusa_sem_motivo_usa_texto_padrao():
    # Arrange
    class SemMotivo(FakeMcp):
        async def call_tool(self, *a, **kw):
            return McpResult("complete", structured={"reservado": False})

    ponte = BridgeService(SemMotivo(), TaskStore())

    # Act
    t = await ponte.iniciar(PEDIDO_LIVRE, TP)

    # Assert
    assert t.state == TaskState.CANCELED and t.status_message["parts"][0]["text"] == "Reserva recusada"


async def test_falha_mcp_vira_texto_exato_no_status():
    # Arrange
    class Quebra(FakeMcp):
        async def call_tool(self, *a, **kw):
            raise McpError(-32602, "xyz")

    ponte = BridgeService(Quebra(), TaskStore())

    # Act
    t = await ponte.iniciar(PEDIDO_LIVRE, TP)

    # Assert
    assert t.state == TaskState.FAILED
    assert t.status_message["parts"][0]["text"] == "Erro do servidor MCP (-32602): xyz"


def test_trace_id_de():
    # Arrange / Act / Assert
    assert trace_id_de(None) is None and trace_id_de(TP) == TRACE and trace_id_de(TP.upper()) == TRACE


# ---------- tasks ----------


def test_ids_tem_prefixo_e_12_hex():
    # Arrange / Act
    t = TaskStore().create("tr")
    m = t.mensagem("ROLE_USER", "x")

    # Assert
    assert re.fullmatch(r"task-[0-9a-f]{12}", t.id) and re.fullmatch(r"ctx-[0-9a-f]{12}", t.context_id)
    assert re.fullmatch(r"msg-[0-9a-f]{12}", m["messageId"]) and re.fullmatch(r"x-[0-9a-f]{12}", novo_id("x"))


def test_limpar_pausa_zera_tudo():
    # Arrange
    t = Task("a", "b", request_state="S", input_key="k", enum=["x"], tentativas_invalidas=2)

    # Act
    t.limpar_pausa()

    # Assert
    assert (t.request_state, t.input_key, t.enum, t.tentativas_invalidas) == (None, None, [], 0)


def test_task_na_fronteira_exata_da_retencao_ainda_vive(monkeypatch):
    # Arrange
    monkeypatch.setattr(tasks, "monotonic", lambda: 5000.0)
    s = TaskStore()
    t = s.create("x")
    t.tocada_em = 5000.0 - tasks.RETENCAO_S

    # Act
    s.create("y")

    # Assert
    assert s.get(t.id) is t


def test_mensagens_de_erro_do_store_e_da_maquina_de_estados():
    # Arrange
    s = TaskStore()
    t = s.create("x")
    done = s.create("y")
    t.transicionar(TaskState.WORKING)
    done.transicionar(TaskState.WORKING)
    done.transicionar(TaskState.COMPLETED)

    # Act
    with pytest.raises(TaskNaoEncontrada) as nao:
        s.get("nada")
    with pytest.raises(TaskTerminal) as term:
        done.transicionar(TaskState.WORKING)
    with pytest.raises(ValueError) as inval:
        t.transicionar(TaskState.SUBMITTED)

    # Assert
    assert str(nao.value) == "nada"
    assert str(term.value) == f"Task {done.id} ja esta em estado terminal (TASK_STATE_COMPLETED)"
    assert str(inval.value) == "transicao invalida TASK_STATE_WORKING -> TASK_STATE_SUBMITTED"


def test_config_so_tira_barras_do_fim_da_public_url():
    # Arrange / Act
    cfg = carregar_config({"PUBLIC_URL": "http://exemploX/"})

    # Assert
    assert cfg.public_url == "http://exemploX"


def test_montar_liga_store_ponte_e_cliente_mcp(monkeypatch):
    # Arrange
    fake, urls = FakeMcp(), []
    monkeypatch.setattr(main_mod, "McpClient", lambda url: urls.append(url) or fake)
    c = TestClient(main_mod.montar(Config(7300, "http://m:9/mcp", "http://pub:7300")))

    # Act
    t = rpc(c, "SendMessage", {"message": msg(PEDIDO_LIVRE)})["result"]["task"]
    g = rpc(c, "GetTask", {"id": t["id"]})

    # Assert
    assert urls == ["http://m:9/mcp"]
    assert t["status"]["state"] == "TASK_STATE_COMPLETED" and g["result"]["task"]["id"] == t["id"]


# ---------- restricoes estruturais e determinismo ----------


def test_agente_nao_importa_o_servidor_nem_sdk_de_llm_e_nao_tem_regra_de_sala():
    from pathlib import Path

    # Arrange
    raiz = Path(__file__).resolve().parents[1]
    codigo = "\n".join(f.read_text(encoding="utf-8") for f in (raiz / "src").rglob("*.py"))
    deps = (raiz / "pyproject.toml").read_text(encoding="utf-8")

    # Act / Assert
    assert "central_salas" not in codigo
    assert not re.search(r"\b(openai|anthropic|langchain|google\.generativeai|ollama)\b", codigo + deps, re.IGNORECASE)
    assert not re.search(r"\b(08:00|20:00|capacidade|MAX_ALTERNATIVAS)\b", codigo)  # politica e dominio sao do servidor


async def test_mesmo_pedido_duas_vezes_produz_o_mesmo_resultado(mcp):
    # Arrange
    ponte = BridgeService(mcp, TaskStore())

    # Act
    a = await ponte.iniciar(PEDIDO, TP)
    b = await ponte.iniciar(PEDIDO, TP)
    livre_a = await ponte.iniciar(PEDIDO_LIVRE, TP)
    livre_b = await ponte.iniciar(PEDIDO_LIVRE, TP)

    # Assert
    assert a.state == b.state and a.status_message["parts"] == b.status_message["parts"]
    assert a.enum == b.enum
    assert livre_a.artifacts[0]["parts"] == livre_b.artifacts[0]["parts"]
