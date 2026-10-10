"""Host de mentira em memória para testar o contrato ``WorkerHost``.

Não cria processos nem chama nenhuma CLI: guarda o texto impresso de cada
worker, simula término (``finish``) e registra interrupções e notificações.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from meister.hosts import CAP_VISIBLE, EventCallback, HostError, WorkerCommand, WorkerHandle

_RUNNING = "running"
_EXITED = "exited"
_INTERRUPTED = "interrupted"
_CLOSED = "closed"


@dataclass
class _FakeWorker:
    command: WorkerCommand
    layout: str
    state: str = _RUNNING
    lines: list[str] = field(default_factory=list)
    interrupts: int = 0


class FakeHost:
    name = "fake"
    capabilities = frozenset({CAP_VISIBLE})

    def __init__(self) -> None:
        self._workers: dict[str, _FakeWorker] = {}
        self._counter = 0
        self.notifications: list[tuple[str, Optional[str]]] = []
        self.on_event: Optional[EventCallback] = None

    async def start(self, on_event: Optional[EventCallback] = None) -> None:
        self.on_event = on_event

    async def spawn(self, command: WorkerCommand, *, layout: str = "tab") -> WorkerHandle:
        self._counter += 1
        wid = f"fake-{self._counter}"
        self._workers[wid] = _FakeWorker(command=command, layout=layout)
        return WorkerHandle(id=wid)

    async def alive(self, handle: WorkerHandle) -> bool | None:
        return self._worker(handle).state == _RUNNING

    async def tail(self, handle: WorkerHandle, lines: int = 200) -> str:
        return "\n".join(self._worker(handle).lines[-lines:])

    async def interrupt(self, handle: WorkerHandle) -> None:
        worker = self._worker(handle)
        if worker.state == _RUNNING:
            worker.state = _INTERRUPTED
        worker.interrupts += 1

    async def close(self, handle: WorkerHandle) -> None:
        self._worker(handle).state = _CLOSED

    async def notify(self, message: str, title: Optional[str] = None) -> None:
        self.notifications.append((message, title))

    async def process_info(self, handle: WorkerHandle) -> dict[str, Any] | None:
        return None

    async def current_context(self) -> dict[str, Any] | None:
        return None

    # Controles de teste: simulam o que um worker real faria.

    def print_line(self, handle: WorkerHandle, text: str) -> None:
        worker = self._worker(handle)
        if worker.state != _RUNNING:
            raise HostError(f"worker {handle.id} não está em execução")
        worker.lines.append(text)

    def finish(self, handle: WorkerHandle) -> None:
        worker = self._worker(handle)
        if worker.state == _RUNNING:
            worker.state = _EXITED

    def command_of(self, handle: WorkerHandle) -> WorkerCommand:
        return self._worker(handle).command

    def interrupt_count(self, handle: WorkerHandle) -> int:
        return self._worker(handle).interrupts

    def _worker(self, handle: WorkerHandle) -> _FakeWorker:
        try:
            return self._workers[handle.id]
        except KeyError:
            raise HostError(f"worker desconhecido: {handle.id}") from None
