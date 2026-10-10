"""Contrato de host de workers (Herdr, Orca, ou outro terminal/runtime).

Um ``WorkerHost`` abstrai como o MeisterRouter cria, observa e encerra
processos de worker. Os tipos aqui não dependem de nenhum host concreto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol, runtime_checkable

# Host mostra o worker em uma superfície visível ao usuário (pane, aba).
CAP_VISIBLE = "visible"
# Host envia eventos de ciclo de vida por push (sem precisar consultar).
CAP_PUSH_EVENTS = "push_events"

EventCallback = Callable[[Mapping[str, Any]], Any]


class HostError(RuntimeError):
    """Falha de operação em um host de workers."""


@dataclass(frozen=True)
class WorkerHandle:
    """Referência opaca a um worker criado por um host.

    ``id`` identifica o worker dentro do host. ``aux`` guarda dados extras
    específicos do host; use valores hasheáveis se o handle for usado em
    conjuntos ou como chave de dicionário.
    """

    id: str
    aux: Any = None


@dataclass
class WorkerCommand:
    """Comando a executar dentro de um worker.

    ``terminal_line`` é uma linha opcional a ser digitada no terminal do
    worker após o start; ``None`` significa que nada é digitado.
    ``log_file`` e ``exit_file`` são caminhos opcionais para a saída do worker
    e para o código de saída gravado pelo shell; hosts que não os usam ignoram-nos.
    """

    argv: list[str]
    env: dict[str, str]
    cwd: str
    label: str
    terminal_line: Optional[str] = field(default=None)
    log_file: Optional[str] = field(default=None)
    exit_file: Optional[str] = field(default=None)


@runtime_checkable
class WorkerHost(Protocol):
    """Operações que um host de workers precisa oferecer.

    ``process_info`` e ``current_context`` podem retornar ``None``, que
    significa "não suportado pelo host".
    """

    name: str
    capabilities: frozenset[str]

    async def start(self, on_event: Optional[EventCallback] = None) -> None:
        """Prepara o host; ``on_event`` recebe eventos push, se suportados."""
        ...

    async def spawn(self, command: WorkerCommand, *, layout: str = "tab") -> WorkerHandle:
        """Cria um worker executando ``command`` no layout pedido."""
        ...

    async def alive(self, handle: WorkerHandle) -> bool | None:
        """``True``/``False`` conforme o worker esteja vivo; ``None`` se desconhecido."""
        ...

    async def tail(self, handle: WorkerHandle, lines: int = 200) -> str:
        """Últimas ``lines`` linhas de saída do worker."""
        ...

    async def interrupt(self, handle: WorkerHandle) -> None:
        """Interrompe a execução atual do worker (equivalente a Ctrl+C)."""
        ...

    async def close(self, handle: WorkerHandle) -> None:
        """Encerra e libera o worker."""
        ...

    async def notify(self, message: str, title: Optional[str] = None) -> None:
        """Mostra uma notificação ao usuário pelo host."""
        ...

    async def process_info(self, handle: WorkerHandle) -> dict[str, Any] | None:
        """Metadados do processo do worker; ``None`` se não suportado."""
        ...

    async def current_context(self) -> dict[str, Any] | None:
        """Contexto atual do host (workspace, projeto); ``None`` se não suportado."""
        ...
