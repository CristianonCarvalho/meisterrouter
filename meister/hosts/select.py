"""Seleção do host de workers a partir de ``runtime.host``.

``auto`` usa o Herdr quando o socket está acessível e cai para o host de
processos caso contrário. ``process`` sempre usa processos. ``herdr`` exige o
socket. ``tmux`` ainda não tem adaptador e falha com mensagem explícita.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Optional

from meister.hosts.base import HostError, WorkerHost
from meister.hosts.herdr import HerdrHost
from meister.hosts.process import ProcessHost
from meister.i18n import t
from meister.worker import is_herdr_available

if TYPE_CHECKING:
    from meister.config import MeisterConfig

_DEFAULT_SOCKET_DISPLAY = "~/.config/herdr/herdr.sock"


def _herdr_host(socket_path: Optional[str], client: Any) -> HerdrHost:
    if client is None:
        from meister.herdr.client import HerdrSocketClient

        client = HerdrSocketClient(socket_path)
    return HerdrHost(client)


def _socket_display(socket_path: Optional[str]) -> str:
    return socket_path or os.environ.get("HERDR_SOCKET_PATH") or _DEFAULT_SOCKET_DISPLAY


def select_host(
    config: MeisterConfig,
    *,
    socket_path: Optional[str] = None,
    client: Any = None,
) -> WorkerHost:
    """Devolve o ``WorkerHost`` correspondente a ``config.runtime.host``."""
    mode = config.runtime.host
    if mode == "process":
        return ProcessHost()
    if mode == "auto":
        if is_herdr_available(socket_path):
            return _herdr_host(socket_path, client)
        return ProcessHost()
    if mode == "herdr":
        if not is_herdr_available(socket_path):
            raise HostError(t("engine.hosts.herdr_unavailable", socket_path=_socket_display(socket_path)))
        return _herdr_host(socket_path, client)
    if mode == "tmux":
        raise HostError(t("engine.hosts.tmux_unavailable"))
    raise HostError(t("engine.hosts.unknown_mode", value=repr(mode)))
