import json

import pytest

from agente_salas.mcp_client import McpError, McpResult
from agente_salas.pedido import PedidoInvalido
from agente_salas.ponte import BridgeService
from agente_salas.tasks import TaskNaoEncontrada, TaskState, TaskStore, TaskTerminal

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TP = f"00-{TRACE}-00f067aa0ba902b7-01"
PEDIDO = "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"


class FakeMcp:
    """Servidor MCP falso: conflito em sala-garagem; carimba cada pausa com um state proprio."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.n = 0

    async def list_tools(self, trace_id):
        self.calls.append(("tools/list", trace_id))
        return [{"name": "reservar_sala"}]

    async def read_resource(self, uri, trace_id):
        self.calls.append(("resources/read", trace_id))
        return "versao: 2026-11-01\n\n- regras"

    async def call_tool(self, name, arguments, trace_id, input_responses=None, request_state=None):
        self.calls.append(("tools/call", trace_id, arguments, input_responses, request_state))
        if arguments["sala"] == "sala-x":
            return McpResult("complete", is_error=True, text="Error executing tool reservar_sala: Sala inexistente: sala-x")
        if request_state is None and arguments["sala"] == "sala-garagem":
            self.n += 1
            return McpResult("input_required", input_key="k", enum=["sala-fusca", "sala-mirante"], request_state=f"STATE-SEGREDO-{arguments['responsavel']}-{self.n}")
        resp = (input_responses or {}).get("k", {})
        if resp.get("action") == "decline":
            return McpResult("complete", structured={"reservado": False, "motivo": "recusou"})
        sala = resp.get("content", {}).get("sala", arguments["sala"])
        return McpResult("complete", structured={"reserva": "res-0003", "reservado": True, "sala": sala, "inicio": arguments["inicio"], "fim": arguments["fim"], "responsavel": arguments["responsavel"], "politica": "2026-11-01"})


@pytest.fixture
def mcp():
    return FakeMcp()


@pytest.fixture
def ponte(mcp):
    return BridgeService(mcp, TaskStore())


def texto_status(t):
    return t.status_message["parts"][0]["text"]


async def test_sala_livre_conclui_com_artifact(ponte):
    t = await ponte.iniciar(PEDIDO.replace("sala-garagem", "sala-porao"), TP)
    assert t.state == TaskState.COMPLETED
    art = t.artifacts[0]
    assert art["name"] == "reserva"
    assert json.loads(art["parts"][0]["text"]) == {"reserva": "res-0003", "sala": "sala-porao", "inicio": "2026-11-03T14:00:00-03:00", "fim": "2026-11-03T15:00:00-03:00", "responsavel": "Marty", "politica": "2026-11-01"}


async def test_descoberta_antes_da_primeira_chamada_e_trace(ponte, mcp):
    await ponte.iniciar(PEDIDO, TP)
    metodos = [c[0] for c in mcp.calls]
    assert metodos.index("tools/list") < metodos.index("tools/call")
    assert {c[1] for c in mcp.calls} == {TRACE}


async def test_erro_da_tool_vira_failed_com_mensagem_exata(ponte):
    t = await ponte.iniciar(PEDIDO.replace("sala-garagem", "sala-x"), None)
    assert t.state == TaskState.FAILED and "Sala inexistente: sala-x" in texto_status(t)


async def test_pausa_mensagem_exata_e_state_guardado(ponte):
    t = await ponte.iniciar(PEDIDO, TP)
    assert t.state == TaskState.INPUT_REQUIRED
    assert texto_status(t) == "alternativas: sala-fusca, sala-mirante"
    assert t.request_state.startswith("STATE-SEGREDO") and t.input_key == "k"


async def test_continuacao_aceite_usa_id_novo_state_ecoado(ponte, mcp):
    t = await ponte.iniciar(PEDIDO, TP)
    estado = t.request_state
    t = await ponte.continuar(t.id, "escolha=sala-mirante", TP)
    assert t.state == TaskState.COMPLETED
    assert json.loads(t.artifacts[0]["parts"][0]["text"])["sala"] == "sala-mirante"
    retry = [c for c in mcp.calls if c[0] == "tools/call"][-1]
    assert retry[3] == {"k": {"action": "accept", "content": {"sala": "sala-mirante"}}} and retry[4] == estado
    assert t.request_state is None


async def test_recusa_cancela(ponte, mcp):
    t = await ponte.iniciar(PEDIDO, TP)
    t = await ponte.continuar(t.id, "escolha=recusar", None)
    assert t.state == TaskState.CANCELED
    retry = [c for c in mcp.calls if c[0] == "tools/call"][-1]
    assert retry[3] == {"k": {"action": "decline"}}
    assert retry[1] == TRACE  # sem header na continuacao: herda o trace da Task


async def test_escolha_fora_do_enum_mantem_pausa_sem_chamar_mcp(ponte, mcp):
    t = await ponte.iniciar(PEDIDO, TP)
    antes = len(mcp.calls)
    t = await ponte.continuar(t.id, "escolha=sala-aquario", TP)
    assert t.state == TaskState.INPUT_REQUIRED and texto_status(t) == "alternativas: sala-fusca, sala-mirante"
    assert len(mcp.calls) == antes
    t = await ponte.continuar(t.id, "escolha=sala-fusca", TP)  # ainda da para concluir
    assert t.state == TaskState.COMPLETED


async def test_duas_tasks_pausadas_nao_trocam_state(ponte, mcp):
    a = await ponte.iniciar(PEDIDO.replace("Marty", "Lorraine"), None)
    b = await ponte.iniciar(PEDIDO.replace("Marty", "George"), None)
    assert a.request_state != b.request_state
    await ponte.continuar(a.id, "escolha=sala-fusca", None)
    await ponte.continuar(b.id, "escolha=sala-mirante", None)
    retry_a, retry_b = [c for c in mcp.calls if c[0] == "tools/call"][-2:]
    assert "Lorraine" in retry_a[4] and "George" in retry_b[4]
    assert retry_a[2]["responsavel"] == "Lorraine" and retry_b[2]["responsavel"] == "George"


async def test_terminal_inexistente_e_entradas_invalidas(ponte):
    t = await ponte.iniciar(PEDIDO.replace("sala-garagem", "sala-porao"), None)
    with pytest.raises(TaskTerminal):
        await ponte.continuar(t.id, "escolha=sala-fusca", None)
    with pytest.raises(TaskNaoEncontrada):
        await ponte.continuar("task-nada", "escolha=sala-fusca", None)
    with pytest.raises(PedidoInvalido):
        await ponte.iniciar("escolha=sala-fusca", None)
    with pytest.raises(PedidoInvalido):
        await ponte.iniciar("bom dia", None)
    p = await ponte.iniciar(PEDIDO, None)
    with pytest.raises(PedidoInvalido):
        await ponte.continuar(p.id, "bom dia", None)
    with pytest.raises(PedidoInvalido):
        await ponte.continuar(p.id, PEDIDO, None)


@pytest.mark.parametrize("tp", [None, "lixo", "00-" + "0" * 32 + "-00f067aa0ba902b7-01"])
async def test_traceparent_ausente_ou_invalido_gera_trace_novo(ponte, mcp, tp):
    t = await ponte.iniciar(PEDIDO.replace("sala-garagem", "sala-porao"), tp)
    assert t.state == TaskState.COMPLETED
    assert len(t.trace_id) == 32 and t.trace_id != "0" * 32


async def test_erro_do_mcp_vira_failed(ponte, mcp):
    async def boom(*a, **k):
        raise McpError(-32602, "requestState expirado")

    t = await ponte.iniciar(PEDIDO, None)
    mcp.call_tool = boom
    t = await ponte.continuar(t.id, "escolha=sala-fusca", None)
    assert t.state == TaskState.FAILED and "requestState expirado" in texto_status(t)
