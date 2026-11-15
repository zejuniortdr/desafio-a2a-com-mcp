"""Estresse da maquina de estados: Tasks concorrentes nao se misturam e nenhuma fica em WORKING depois de erro, timeout ou cancelamento."""

import asyncio

import httpx
import pytest
from agente_salas.mcp_client import McpError
from agente_salas.pedido import PedidoInvalido
from agente_salas.ponte import _CANCELADA, BridgeService
from agente_salas.tasks import TaskState, TaskStore
from test_ponte import PEDIDO, PEDIDO_LIVRE, TP, FakeMcp

N = 40


class McpLento(FakeMcp):
    """Cede o controle do event loop em cada chamada, para intercalar as Tasks."""

    async def list_tools(self, trace_id):
        await asyncio.sleep(0)
        return await super().list_tools(trace_id)

    async def read_resource(self, uri, trace_id):
        await asyncio.sleep(0)
        return await super().read_resource(uri, trace_id)

    async def call_tool(self, *a, **kw):
        await asyncio.sleep(0.001)
        return await super().call_tool(*a, **kw)


def pedido_de(i: int, sala: str = "sala-garagem") -> str:
    return PEDIDO.replace("sala-garagem", sala).replace("Marty", f"cli{i}")


async def test_n_pausas_simultaneas_cada_uma_conclui_com_o_seu_state():
    # Arrange
    mcp = McpLento()
    store = TaskStore()
    ponte = BridgeService(mcp, store)

    # Act
    pausadas = await asyncio.gather(*[ponte.iniciar(pedido_de(i), TP) for i in range(N)])
    estados = {t.id: t.request_state for t in pausadas}
    todas_pausadas = all(t.state == TaskState.INPUT_REQUIRED for t in pausadas)
    finais = await asyncio.gather(*[ponte.continuar(t.id, "escolha=sala-mirante", None) for t in pausadas])

    # Assert
    assert len(set(estados.values())) == N and todas_pausadas
    retries = {c[4]: c[2]["responsavel"] for c in mcp.calls if c[0] == "tools/call" and c[4]}
    for t, original in zip(finais, pausadas):
        assert t.state == TaskState.COMPLETED
        assert retries[estados[original.id]] == f"cli{pausadas.index(original)}"  # state devolvido ao pedido de origem


async def test_continuacoes_duplicadas_da_mesma_task_so_uma_chega_ao_mcp():
    # Arrange
    mcp = McpLento()
    ponte = BridgeService(mcp, TaskStore())
    t = await ponte.iniciar(PEDIDO, TP)

    # Act
    resultados = await asyncio.gather(
        *[ponte.continuar(t.id, "escolha=sala-mirante", None) for _ in range(N)], return_exceptions=True
    )

    # Assert
    assert sum(1 for r in resultados if not isinstance(r, Exception)) == 1
    assert all(isinstance(r, PedidoInvalido) for r in resultados if isinstance(r, Exception))
    assert len([c for c in mcp.calls if c[0] == "tools/call" and c[4]]) == 1


FALHAS = [
    McpError(-32603, "boom"),
    httpx.ReadTimeout("lento"),
    httpx.ConnectTimeout("nao conectou"),
    httpx.ConnectError("caiu"),
    httpx.RemoteProtocolError("meio de resposta"),
    KeyError("contents"),
    RuntimeError("coroutine"),
]


class McpQueFalha(McpLento):
    def __init__(self, falha, onde):
        super().__init__()
        self.falha, self.onde = falha, onde

    async def list_tools(self, trace_id):
        if self.onde == "list":
            raise self.falha
        return await super().list_tools(trace_id)

    async def read_resource(self, uri, trace_id):
        if self.onde == "resource":
            raise self.falha
        return await super().read_resource(uri, trace_id)

    async def call_tool(self, name, arguments, trace_id, input_responses=None, request_state=None):
        if self.onde == "call":
            raise self.falha
        return await super().call_tool(name, arguments, trace_id, input_responses, request_state)


@pytest.mark.parametrize("onde", ["list", "resource", "call"])
@pytest.mark.parametrize("falha", FALHAS, ids=lambda f: type(f).__name__)
async def test_falha_do_mcp_no_pedido_inicial_nunca_deixa_task_em_working(falha, onde):
    # Arrange
    store = TaskStore()
    ponte = BridgeService(McpQueFalha(falha, onde), store)

    # Act
    tasks = await asyncio.gather(*[ponte.iniciar(PEDIDO_LIVRE, TP) for _ in range(5)])

    # Assert
    assert all(t.state == TaskState.FAILED and t.request_state is None for t in tasks)
    assert not [t for t in store._tasks.values() if t.state == TaskState.WORKING]


@pytest.mark.parametrize(
    "falha",
    [f for f in FALHAS if not isinstance(f, (httpx.ConnectError, httpx.ConnectTimeout))],
    ids=lambda f: type(f).__name__,
)
async def test_falha_do_mcp_na_continuacao_termina_failed_e_libera_o_state(falha):
    # Arrange
    mcp = McpLento()
    store = TaskStore()
    ponte = BridgeService(mcp, store)
    pausadas = [await ponte.iniciar(PEDIDO, TP) for _ in range(5)]
    mcp.__class__ = type("Quebra", (McpQueFalha,), {})
    mcp.falha, mcp.onde = falha, "call"

    # Act
    finais = await asyncio.gather(*[ponte.continuar(t.id, "escolha=sala-mirante", None) for t in pausadas])

    # Assert
    assert all(t.state == TaskState.FAILED and t.request_state is None and t.input_key is None for t in finais)
    assert not [t for t in store._tasks.values() if t.state == TaskState.WORKING]


@pytest.mark.parametrize(
    "falha", [httpx.ConnectError("caiu"), httpx.ConnectTimeout("x")], ids=lambda f: type(f).__name__
)
async def test_conexao_recusada_na_continuacao_devolve_a_pausa_e_permite_novo_envio(falha):
    # Arrange
    mcp = McpLento()
    ponte = BridgeService(mcp, TaskStore())
    t = await ponte.iniciar(PEDIDO, TP)
    estado = t.request_state
    original = mcp.call_tool

    async def cai(*a, **kw):
        raise falha

    mcp.call_tool = cai

    # Act
    pausa = await ponte.continuar(t.id, "escolha=sala-mirante", None)
    estado_da_pausa, state_da_pausa = pausa.state, pausa.request_state
    mcp.call_tool = original
    final = await ponte.continuar(t.id, "escolha=sala-mirante", None)

    # Assert
    assert estado_da_pausa == TaskState.INPUT_REQUIRED and state_da_pausa == estado
    assert final.state == TaskState.COMPLETED


class McpTravado(McpLento):
    """O tools/call da fase escolhida (inicial ou retry) nunca responde."""

    def __init__(self, travar_em):
        super().__init__()
        self.travar_em, self.preso = travar_em, asyncio.Event()

    async def call_tool(self, name, arguments, trace_id, input_responses=None, request_state=None):
        if self.travar_em == ("retry" if request_state is not None else "inicial"):
            self.preso.set()
            await asyncio.Event().wait()
        return await super().call_tool(name, arguments, trace_id, input_responses, request_state)


async def test_timeout_externo_no_pedido_inicial_nao_deixa_a_task_em_working():
    # Arrange: o MCP nunca responde e o chamador desiste (wait_for cancela a coroutine)
    store = TaskStore()
    ponte = BridgeService(McpTravado("inicial"), store)

    # Act
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ponte.iniciar(PEDIDO_LIVRE, TP), timeout=0.05)

    # Assert
    (t,) = store._tasks.values()
    assert t.state == TaskState.FAILED, f"Task ficou em {t.state.value}"
    assert t.status_message["parts"][0]["text"] == _CANCELADA


async def test_timeout_externo_na_continuacao_nao_deixa_a_task_em_working():
    # Arrange
    mcp = McpTravado(None)
    store = TaskStore()
    ponte = BridgeService(mcp, store)
    t = await ponte.iniciar(PEDIDO, TP)
    mcp.travar_em = "retry"

    # Act
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ponte.continuar(t.id, "escolha=sala-mirante", None), timeout=0.05)

    # Assert
    assert t.state == TaskState.FAILED, f"Task ficou em {t.state.value}"
    assert t.status_message["parts"][0]["text"] == _CANCELADA
    assert t.request_state is None


async def test_get_task_reflete_working_enquanto_o_mcp_trabalha():
    # Arrange
    mcp = McpTravado("inicial")
    store = TaskStore()
    ponte = BridgeService(mcp, store)

    # Act
    tarefa = asyncio.create_task(ponte.iniciar(PEDIDO_LIVRE, TP))
    await asyncio.wait_for(mcp.preso.wait(), 1)
    (t,) = store._tasks.values()
    visto = store.get(t.id).state
    tarefa.cancel()
    await asyncio.gather(tarefa, return_exceptions=True)

    # Assert
    assert visto == TaskState.WORKING


@pytest.mark.parametrize("onde", ["list", "resource", "call"])
@pytest.mark.parametrize("falha", [ZeroDivisionError("bug"), OSError("disco")], ids=lambda f: type(f).__name__)
async def test_excecao_inesperada_falha_a_task_e_propaga_sem_deixar_working(falha, onde):
    # Arrange
    store = TaskStore()
    ponte = BridgeService(McpQueFalha(falha, onde), store)

    # Act
    with pytest.raises(type(falha)):
        await ponte.iniciar(PEDIDO_LIVRE, TP)

    # Assert
    (t,) = store._tasks.values()
    assert t.state == TaskState.FAILED and t.request_state is None
    assert t.status_message["parts"][0]["text"] == f"Erro interno do agente: {type(falha).__name__}"


async def test_excecao_inesperada_na_continuacao_falha_a_task_e_libera_o_state():
    # Arrange
    mcp = McpQueFalha(ZeroDivisionError("bug"), None)
    store = TaskStore()
    ponte = BridgeService(mcp, store)
    t = await ponte.iniciar(PEDIDO, TP)
    mcp.onde = "call"

    # Act
    with pytest.raises(ZeroDivisionError):
        await ponte.continuar(t.id, "escolha=sala-mirante", None)

    # Assert
    assert t.state == TaskState.FAILED and t.request_state is None
    assert t.status_message["parts"][0]["text"] == "Erro interno do agente: ZeroDivisionError"
