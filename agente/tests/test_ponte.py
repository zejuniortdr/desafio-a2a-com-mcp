import json

import httpx
import pytest

from agente_salas.mcp_client import McpError, McpResult
from agente_salas.pedido import PedidoInvalido
from agente_salas.ponte import BridgeService
from agente_salas.tasks import TaskNaoEncontrada, TaskState, TaskStore, TaskTerminal

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TP = f"00-{TRACE}-00f067aa0ba902b7-01"
PEDIDO = "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
PEDIDO_LIVRE = PEDIDO.replace("sala-garagem", "sala-porao")


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
        self.calls.append(("tools/call", trace_id, arguments, input_responses, request_state, name))
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


def chamadas_de_tool(mcp):
    return [c for c in mcp.calls if c[0] == "tools/call"]


async def test_sala_livre_conclui_com_artifact(ponte):
    t = await ponte.iniciar(PEDIDO_LIVRE, TP)

    assert t.state == TaskState.COMPLETED
    art = t.artifacts[0]
    assert art["name"] == "reserva"
    assert json.loads(art["parts"][0]["text"]) == {"reserva": "res-0003", "sala": "sala-porao", "inicio": "2026-11-03T14:00:00-03:00", "fim": "2026-11-03T15:00:00-03:00", "responsavel": "Marty", "politica": "2026-11-01"}


async def test_descoberta_antes_da_primeira_chamada_e_trace(ponte, mcp):
    await ponte.iniciar(PEDIDO, TP)

    metodos = [c[0] for c in mcp.calls]
    assert metodos.index("tools/list") < metodos.index("tools/call")
    assert {c[1] for c in mcp.calls} == {TRACE}
    assert mcp.calls[-1][5] == "reservar_sala"


async def test_erro_da_tool_vira_failed_com_mensagem_exata(ponte):
    t = await ponte.iniciar(PEDIDO.replace("sala-garagem", "sala-x"), None)

    assert t.state == TaskState.FAILED and "Sala inexistente: sala-x" in texto_status(t)


async def test_pausa_mensagem_exata_e_state_guardado(ponte):
    t = await ponte.iniciar(PEDIDO, TP)

    assert t.state == TaskState.INPUT_REQUIRED
    assert texto_status(t) == "alternativas: sala-fusca, sala-mirante"
    assert t.request_state.startswith("STATE-SEGREDO") and t.input_key == "k"


async def test_continuacao_aceite_usa_id_novo_state_ecoado(ponte, mcp):
    pausada = await ponte.iniciar(PEDIDO, TP)
    estado = pausada.request_state

    t = await ponte.continuar(pausada.id, "escolha=sala-mirante", TP)

    assert t.state == TaskState.COMPLETED
    corpo = json.loads(t.artifacts[0]["parts"][0]["text"])
    assert (corpo["sala"], corpo["politica"]) == ("sala-mirante", "2026-11-01")
    assert t.artifacts[0]["artifactId"].startswith("art-")
    assert texto_status(t) == "Reserva res-0003 confirmada na sala-mirante."
    retry = chamadas_de_tool(mcp)[-1]
    assert retry[3] == {"k": {"action": "accept", "content": {"sala": "sala-mirante"}}} and retry[4] == estado
    assert retry[5] == "reservar_sala"
    assert t.request_state is None


async def test_continuacao_com_traceparent_novo_usa_o_trace_novo(ponte, mcp):
    t = await ponte.iniciar(PEDIDO, TP)
    outro = "a" * 32

    await ponte.continuar(t.id, "escolha=sala-fusca", f"00-{outro}-00f067aa0ba902b7-01")

    assert {c[1] for c in mcp.calls[-2:]} == {outro}


async def test_historico_guarda_papeis_e_ids(ponte):
    t = await ponte.iniciar(PEDIDO, TP, "m1")

    usuario, agente = t.history
    assert (usuario["messageId"], usuario["role"]) == ("m1", "ROLE_USER") and "taskId" not in usuario
    assert (agente["role"], agente["taskId"], agente["contextId"]) == ("ROLE_AGENT", t.id, t.context_id)


async def test_recusa_cancela(ponte, mcp):
    pausada = await ponte.iniciar(PEDIDO, TP)

    t = await ponte.continuar(pausada.id, "escolha=recusar", None)

    assert t.state == TaskState.CANCELED and texto_status(t) == "recusou"
    retry = chamadas_de_tool(mcp)[-1]
    assert retry[3] == {"k": {"action": "decline"}}
    assert retry[1] == TRACE  # sem header na continuacao: herda o trace da Task


async def test_escolha_fora_do_enum_mantem_pausa_sem_chamar_mcp(ponte, mcp):
    pausada = await ponte.iniciar(PEDIDO, TP)
    antes = len(mcp.calls)

    t = await ponte.continuar(pausada.id, "escolha=sala-aquario", TP)

    assert t.state == TaskState.INPUT_REQUIRED and texto_status(t) == "alternativas: sala-fusca, sala-mirante"
    assert len(mcp.calls) == antes


async def test_escolha_valida_depois_de_uma_fora_do_enum_conclui(ponte):
    pausada = await ponte.iniciar(PEDIDO, TP)
    await ponte.continuar(pausada.id, "escolha=sala-aquario", TP)

    t = await ponte.continuar(pausada.id, "escolha=sala-fusca", TP)

    assert t.state == TaskState.COMPLETED


async def test_duas_pausas_recebem_states_distintos(ponte):
    a = await ponte.iniciar(PEDIDO.replace("Marty", "Lorraine"), None)

    b = await ponte.iniciar(PEDIDO.replace("Marty", "George"), None)

    assert a.request_state != b.request_state


async def test_duas_tasks_pausadas_nao_trocam_state(ponte, mcp):
    a = await ponte.iniciar(PEDIDO.replace("Marty", "Lorraine"), None)
    b = await ponte.iniciar(PEDIDO.replace("Marty", "George"), None)

    await ponte.continuar(a.id, "escolha=sala-fusca", None)
    await ponte.continuar(b.id, "escolha=sala-mirante", None)

    retry_a, retry_b = chamadas_de_tool(mcp)[-2:]
    assert "Lorraine" in retry_a[4] and "George" in retry_b[4]
    assert retry_a[2]["responsavel"] == "Lorraine" and retry_b[2]["responsavel"] == "George"


async def test_continuar_task_terminal_e_recusado(ponte):
    t = await ponte.iniciar(PEDIDO_LIVRE, None)

    with pytest.raises(TaskTerminal):
        await ponte.continuar(t.id, "escolha=sala-fusca", None)


async def test_continuar_task_inexistente(ponte):
    with pytest.raises(TaskNaoEncontrada):
        await ponte.continuar("task-nada", "escolha=sala-fusca", None)


@pytest.mark.parametrize("texto", ["escolha=sala-fusca", "bom dia"])
async def test_iniciar_sem_pedido_de_reserva_e_invalido(ponte, texto):
    with pytest.raises(PedidoInvalido):
        await ponte.iniciar(texto, None)


@pytest.mark.parametrize("texto", ["bom dia", PEDIDO])
async def test_continuar_sem_escolha_e_invalido_e_mantem_a_pausa(ponte, texto):
    p = await ponte.iniciar(PEDIDO, None)

    with pytest.raises(PedidoInvalido):
        await ponte.continuar(p.id, texto, None)

    assert p.state == TaskState.INPUT_REQUIRED


@pytest.mark.parametrize("tp", [None, "lixo", "00-" + "0" * 32 + "-00f067aa0ba902b7-01"])
async def test_traceparent_ausente_ou_invalido_gera_trace_novo(ponte, tp):
    t = await ponte.iniciar(PEDIDO_LIVRE, tp)

    assert t.state == TaskState.COMPLETED
    assert len(t.trace_id) == 32 and t.trace_id != "0" * 32


async def test_erro_do_mcp_vira_failed(ponte, mcp):
    async def boom(*a, **k):
        raise McpError(-32602, "requestState expirado")

    pausada = await ponte.iniciar(PEDIDO, None)
    mcp.call_tool = boom

    t = await ponte.continuar(pausada.id, "escolha=sala-fusca", None)

    assert t.state == TaskState.FAILED and "requestState expirado" in texto_status(t)


@pytest.mark.parametrize(
    "erro, trecho",
    [
        (httpx.ConnectError("conexao recusada"), "Servidor MCP indisponivel: conexao recusada"),
        (KeyError("result"), "fora do formato esperado"),
        (IndexError("contents"), "fora do formato esperado"),
        (TypeError("nao e dict"), "fora do formato esperado"),
        (AttributeError("'str' object has no attribute 'get'"), "fora do formato esperado"),
        (RuntimeError("coroutine raised StopIteration"), "fora do formato esperado"),
        (ValueError("versao sem ':'"), "fora do formato esperado"),
    ],
)
@pytest.mark.parametrize("metodo", ["read_resource", "call_tool"])
async def test_falha_na_continuacao_nunca_deixa_a_task_em_working(ponte, mcp, metodo, erro, trecho):
    # Regressao: so McpError/httpx.HTTPError eram capturados; qualquer outra excecao prendia a Task em WORKING.
    async def boom(*a, **k):
        raise erro

    pausada = await ponte.iniciar(PEDIDO, None)
    setattr(mcp, metodo, boom)

    t = await ponte.continuar(pausada.id, "escolha=sala-fusca", None)

    assert t.state == TaskState.FAILED and trecho in texto_status(t)
    assert t.request_state is None


async def test_resultado_sem_campos_da_reserva_vira_failed(ponte, mcp):
    async def incompleto(*a, **k):
        return McpResult("complete", structured={"reservado": True})

    mcp.call_tool = incompleto

    t = await ponte.iniciar(PEDIDO, None)

    assert t.state == TaskState.FAILED and t.artifacts == []


async def test_politica_vazia_vira_failed(ponte, mcp):
    async def vazia(*a, **k):
        return ""

    mcp.read_resource = vazia

    t = await ponte.iniciar(PEDIDO, None)

    assert t.state == TaskState.FAILED


async def test_tool_ausente_na_descoberta_vira_failed_sem_chamar(ponte, mcp):
    async def outras(*a, **k):
        return [{"name": "listar_salas"}]

    mcp.list_tools = outras

    t = await ponte.iniciar(PEDIDO, None)

    assert t.state == TaskState.FAILED and "reservar_sala nao encontrada" in texto_status(t)
    assert mcp.calls == []
