"""Suíte de contrato reutilizável para implementações de ``WorkerHost``.

A subclasse fornece a fixture ``host`` e implementa os três ganchos de
driver (``spawn_printing``, ``spawn_waiting``, ``finish``), que sabem como
fazer um worker do host concreto imprimir texto, ficar esperando e terminar.
Os testes só usam a superfície pública do contrato.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

import pytest

from meister.hosts import WorkerCommand, WorkerHandle, WorkerHost

_TIMEOUT_S = 5.0
_STEP_S = 0.01


async def _eventually(check: Callable[[], Awaitable[bool]], timeout: float = _TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if await check():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_STEP_S)


def wait_command(label: str = "contract-worker") -> WorkerCommand:
    return WorkerCommand(argv=["contract-wait"], env={}, cwd=".", label=label)


class HostContract:
    """Testes de contrato; a subclasse herda e fornece ``host`` e os ganchos."""

    async def spawn_printing(self, host: WorkerHost, text: str) -> WorkerHandle:
        """Cria um worker que já imprimiu ``text`` e segue vivo."""
        raise NotImplementedError

    async def spawn_waiting(self, host: WorkerHost) -> WorkerHandle:
        """Cria um worker que segue vivo até ser interrompido ou terminar."""
        raise NotImplementedError

    async def finish(self, host: WorkerHost, handle: WorkerHandle) -> None:
        """Faz o worker terminar por conta própria."""
        raise NotImplementedError

    @pytest.mark.asyncio
    async def test_spawn_returns_handle_with_id(self, host: WorkerHost) -> None:
        handle = await host.spawn(wait_command())
        assert isinstance(handle, WorkerHandle)
        assert handle.id.strip()

    @pytest.mark.asyncio
    async def test_alive_is_true_right_after_spawn(self, host: WorkerHost) -> None:
        handle = await self.spawn_waiting(host)
        assert await host.alive(handle) is True

    @pytest.mark.asyncio
    async def test_alive_is_false_or_none_after_exit(self, host: WorkerHost) -> None:
        handle = await self.spawn_waiting(host)
        await self.finish(host, handle)

        async def not_alive() -> bool:
            return (await host.alive(handle)) is not True

        assert await _eventually(not_alive)
        assert (await host.alive(handle)) in (False, None)

    @pytest.mark.asyncio
    async def test_tail_returns_printed_text(self, host: WorkerHost) -> None:
        handle = await self.spawn_printing(host, "linha-de-contrato-42")

        async def has_text() -> bool:
            return "linha-de-contrato-42" in await host.tail(handle)

        assert await _eventually(has_text)

    @pytest.mark.asyncio
    async def test_interrupt_stops_waiting_worker(self, host: WorkerHost) -> None:
        handle = await self.spawn_waiting(host)
        assert await host.alive(handle) is True
        await host.interrupt(handle)

        async def stopped() -> bool:
            return (await host.alive(handle)) is not True

        assert await _eventually(stopped)

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, host: WorkerHost) -> None:
        handle = await self.spawn_waiting(host)
        await host.close(handle)
        await host.close(handle)

    @pytest.mark.asyncio
    async def test_notify_does_not_raise(self, host: WorkerHost) -> None:
        await host.notify("mensagem de contrato")
        await host.notify("mensagem de contrato", title="título")
