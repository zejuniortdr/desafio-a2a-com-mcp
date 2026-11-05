import pytest

from agente_salas.tasks import TERMINAIS, TaskNaoEncontrada, TaskState, TaskStore, TaskTerminal


def nova():
    return TaskStore().create("a" * 32)


def test_fluxo_normal_e_ids_proprios():
    s = TaskStore()
    a, b = s.create("t"), s.create("t")
    assert a.id != b.id and a.context_id != b.context_id and a.state == TaskState.SUBMITTED
    a.transicionar(TaskState.WORKING)
    a.transicionar(TaskState.INPUT_REQUIRED, "alternativas: x")
    assert a.status_message["parts"][0]["text"] == "alternativas: x"
    assert s.get(a.id) is a


@pytest.mark.parametrize("terminal", sorted(TERMINAIS, key=lambda s: s.value))
def test_terminal_e_definitivo(terminal):
    t = nova()
    t.transicionar(TaskState.WORKING)
    t.transicionar(terminal)
    with pytest.raises(TaskTerminal):
        t.transicionar(TaskState.WORKING)


def test_transicao_invalida():
    with pytest.raises(ValueError):
        nova().transicionar(TaskState.COMPLETED)


def test_task_inexistente():
    with pytest.raises(TaskNaoEncontrada):
        TaskStore().get("nada")
