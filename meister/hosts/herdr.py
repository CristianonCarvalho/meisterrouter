"""Adaptador do Herdr para o contrato ``WorkerHost``.

Embrulha um ``HerdrSocketClient`` e replica as chamadas que hoje são feitas
diretamente por ``WorkerSpawner`` e pelo bridge, sem alterar sequência nem
argumentos.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from meister.hosts.base import (
    CAP_PUSH_EVENTS,
    CAP_VISIBLE,
    EventCallback,
    WorkerCommand,
    WorkerHandle,
)

logger = logging.getLogger(__name__)


class HerdrHost:
    """Implementação de ``WorkerHost`` sobre o cliente de socket do Herdr."""

    name = "herdr"
    capabilities = frozenset({CAP_VISIBLE, CAP_PUSH_EVENTS})

    def __init__(self, client: Any, config: Optional[Mapping[str, Any]] = None) -> None:
        self.client = client
        self._config: dict[str, Any] = dict(config or {})

    async def start(self, on_event: Optional[EventCallback] = None) -> None:
        if not self.client.is_connected:
            await self.client.connect()
        if on_event is not None:
            await self.client.subscribe_events(on_event)

    async def spawn(self, command: WorkerCommand, *, layout: str = "tab") -> WorkerHandle:
        if layout == "pane":
            return await self._spawn_pane(command)
        return await self._spawn_tab(command)

    async def _spawn_tab(self, command: WorkerCommand) -> WorkerHandle:
        tab_id, pane_id = await self.client.create_tab(
            cwd=command.cwd,
            label=command.label,
            focus=False,
        )
        if command.terminal_line and pane_id:
            if hasattr(self.client, "wait_pane_ready"):
                await self.client.wait_pane_ready(pane_id)
            await self.client.send_text(pane_id, f"{command.terminal_line}\n")
        return WorkerHandle(id=pane_id, aux=tab_id)

    async def _spawn_pane(self, command: WorkerCommand) -> WorkerHandle:
        pane_id = await self.client.split_pane(
            direction=str(self._config.get("direction", "right")),
            command=command.terminal_line or None,
            split_ratio=float(self._config.get("split_ratio", 0.5)),
            cwd=command.cwd or None,
        )
        return WorkerHandle(id=pane_id, aux=None)

    async def alive(self, handle: WorkerHandle) -> bool | None:
        if not hasattr(self.client, "pane_exists"):
            return None
        return bool(await self.client.pane_exists(handle.id))

    async def tail(self, handle: WorkerHandle, lines: int = 200) -> str:
        return await self.client.read_pane(handle.id, lines=lines)

    async def interrupt(self, handle: WorkerHandle) -> None:
        await self.client.send_interrupt(handle.id)

    async def close(self, handle: WorkerHandle) -> None:
        try:
            if handle.aux:
                await self.client.close_tab(handle.aux)
            else:
                await self.client.close_pane(handle.id)
        except Exception as e:
            logger.debug("herdr close ignored failure for %s: %s", handle.id, e)

    async def notify(self, message: str, title: Optional[str] = None) -> None:
        if title is None:
            await self.client.show_notification(message)
        else:
            await self.client.show_notification(message, title=title)

    async def process_info(self, handle: WorkerHandle) -> dict[str, Any] | None:
        result = await self.client._call("pane.process_info", {"pane_id": handle.id})
        return result if isinstance(result, dict) else None

    async def current_context(self) -> dict[str, Any] | None:
        return await self.client.get_current_pane()
