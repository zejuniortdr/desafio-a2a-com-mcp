from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum

from agente_salas.pedido import PedidoReserva


class TaskState(str, Enum):
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"


TERMINAIS = {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}
_PERMITIDAS: dict[TaskState, set[TaskState]] = {
    TaskState.SUBMITTED: {TaskState.WORKING},
    TaskState.WORKING: {TaskState.INPUT_REQUIRED, *TERMINAIS},
    TaskState.INPUT_REQUIRED: {TaskState.INPUT_REQUIRED, TaskState.WORKING, TaskState.CANCELED},
}


class TaskTerminal(Exception):
    """Operacao sobre Task em estado terminal."""


class TaskNaoEncontrada(Exception):
    pass


def novo_id(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex[:12]}"


@dataclass
class Task:
    id: str
    context_id: str
    state: TaskState = TaskState.SUBMITTED
    status_message: dict | None = None
    history: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    trace_id: str = ""
    # Estado da pausa (ponte): opaco para o agente, nunca serializado para o cliente A2A.
    pedido: PedidoReserva | None = None
    request_state: str | None = None
    input_key: str | None = None
    enum: list[str] = field(default_factory=list)

    def mensagem(self, role: str, texto: str, com_ids: bool = True, message_id: str | None = None) -> dict:
        m: dict = {"messageId": message_id or novo_id("msg"), "role": role, "parts": [{"text": texto}]}
        if com_ids:
            m.update(taskId=self.id, contextId=self.context_id)
        self.history.append(m)
        return m

    def transicionar(self, novo: TaskState, texto: str | None = None) -> None:
        if self.state in TERMINAIS:
            raise TaskTerminal(f"Task {self.id} ja esta em estado terminal ({self.state.value})")
        if novo not in _PERMITIDAS[self.state]:
            raise ValueError(f"transicao invalida {self.state.value} -> {novo.value}")
        self.state = novo
        self.status_message = self.mensagem("ROLE_AGENT", texto) if texto is not None else None

    def limpar_pausa(self) -> None:
        self.request_state = self.input_key = None
        self.enum = []


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, trace_id: str) -> Task:
        t = Task(id=novo_id("task"), context_id=novo_id("ctx"), trace_id=trace_id)
        with self._lock:
            self._tasks[t.id] = t
        return t

    def get(self, task_id: str) -> Task:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError:
                raise TaskNaoEncontrada(task_id) from None
