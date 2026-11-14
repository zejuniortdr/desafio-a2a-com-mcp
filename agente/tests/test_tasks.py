import pytest

from agente_salas.tasks import TERMINAIS, TaskNaoEncontrada, TaskState, TaskStore, TaskTerminal


def nova():
    return TaskStore().create("a" * 32)


def test_create_gera_ids_proprios_em_submitted():
    s = TaskStore()

    a, b = s.create("t"), s.create("t")

    assert a.id != b.id and a.context_id != b.context_id
    assert a.state == TaskState.SUBMITTED


def test_input_required_guarda_a_mensagem():
    t = nova()
    t.transicionar(TaskState.WORKING)

    t.transicionar(TaskState.INPUT_REQUIRED, "alternativas: x")

    assert t.status_message["parts"][0]["text"] == "alternativas: x"


def test_get_devolve_a_mesma_task():
    s = TaskStore()
    t = s.create("t")

    achada = s.get(t.id)

    assert achada is t


@pytest.mark.parametrize("terminal", sorted(TERMINAIS, key=lambda s: s.value))
def test_terminal_e_definitivo(terminal):
    t = nova()
    t.transicionar(TaskState.WORKING)
    t.transicionar(terminal)

    with pytest.raises(TaskTerminal):
        t.transicionar(TaskState.WORKING)


def test_transicao_invalida():
    t = nova()

    with pytest.raises(ValueError):
        t.transicionar(TaskState.COMPLETED)


def test_task_inexistente():
    s = TaskStore()

    with pytest.raises(TaskNaoEncontrada):
        s.get("nada")
