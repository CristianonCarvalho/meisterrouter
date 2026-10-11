"""Seleção do host de workers a partir de ``runtime.host``.

``auto`` usa o Herdr quando o socket está acessível e cai para o host de
processos caso contrário. ``process`` sempre usa processos. ``herdr`` exige o
socket. ``tmux`` exige o binário na versão 3.0 ou mais nova; ``auto`` nunca o escolhe,
nem dentro de uma sessão tmux.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, Optional

from meister.hosts.base import HostError, WorkerHost
from meister.hosts.herdr import HerdrHost
from meister.hosts.process import ProcessHost
from meister.hosts.tmux import _MIN_VERSION, TmuxHost, _parse_version
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


def _tmux_usable() -> bool:
    """Diz se há um ``tmux`` no PATH na versão mínima suportada."""
    binary = shutil.which("tmux")
    if binary is None:
        return False
    try:
        result = subprocess.run([binary, "-V"], capture_output=True, text=True, timeout=5, check=False)
        return result.returncode == 0 and _parse_version(result.stdout) >= _MIN_VERSION
    except (OSError, subprocess.SubprocessError, HostError):
        return False


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
        if not _tmux_usable():
            raise HostError(t("engine.hosts.tmux_not_found"))
        return TmuxHost()
    raise HostError(t("engine.hosts.unknown_mode", value=repr(mode)))
