"""Host ``process``: cria workers como processos locais, sem Herdr.

Cada worker roda em um grupo de processos próprio, com saída anexada a um
arquivo de log. Não há superfície visível nem eventos push.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from meister import osops
from meister.hosts import _proc
from meister.hosts.base import EventCallback, WorkerCommand, WorkerHandle
from meister.logger import get_log_dir

_DEFAULT_GRACE_S = 5.0


def _resolve_argv(argv: Sequence[str]) -> list[str]:
    """No Windows, resolve ``argv[0]`` pelo PATH com PATHEXT (shims ``.cmd`` de CLIs npm)."""
    if sys.platform != "win32" or not argv:
        return list(argv)
    return [shutil.which(argv[0]) or argv[0], *argv[1:]]


def _write_text_atomic(path: str, text: str) -> None:
    """Grava ``text`` em arquivo temporário no mesmo diretório e troca por ``path``."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".exit.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _shell_exit_code(returncode: int) -> int:
    """Converte o ``returncode`` do Popen no valor que o shell grava em ``$?``."""
    if returncode < 0:
        return 128 + (-returncode)
    return returncode


def _record_exit(popen: subprocess.Popen, exit_file: str) -> None:
    """Espera o término de ``popen`` e grava o código de saída em ``exit_file``."""
    code = _shell_exit_code(popen.wait())
    _write_text_atomic(exit_file, f"{code}\n")


def _terminate_quietly(popen: subprocess.Popen, grace: float) -> None:
    """Encerra o grupo de ``popen``; se o líder já saiu, não há grupo a sinalizar.

    No macOS, ``killpg`` sobre um grupo cujo líder é zumbi (saiu sem ``wait``)
    ou vazio responde ``EPERM`` em vez de ``ESRCH``.
    """
    if popen.poll() is not None:
        return
    try:
        _proc.terminate_tree(popen, grace)
    except PermissionError:
        return


class ProcessHost:
    """Implementação de ``WorkerHost`` sobre processos locais."""

    name = "process"
    capabilities: frozenset[str] = frozenset()

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        self._config: dict[str, Any] = dict(config or {})
        self._grace = float(self._config.get("grace_seconds", _DEFAULT_GRACE_S))
        self._procs: dict[str, subprocess.Popen] = {}

    async def start(self, on_event: Optional[EventCallback] = None) -> None:
        """Nada a preparar: processos são criados sob demanda."""

    async def spawn(self, command: WorkerCommand, *, layout: str = "tab") -> WorkerHandle:
        log_path = command.log_file or os.path.join(get_log_dir(), "workers", f"{command.label}.log")
        popen = _proc.start_detached(
            _resolve_argv(command.argv),
            cwd=command.cwd,
            env={**os.environ, **command.env},
            log_path=log_path,
        )
        handle_id = str(popen.pid)
        self._procs[handle_id] = popen
        if command.exit_file:
            threading.Thread(
                target=_record_exit,
                args=(popen, command.exit_file),
                name=f"exit-watch-{handle_id}",
                daemon=True,
            ).start()
        return WorkerHandle(id=handle_id, aux=log_path)

    async def alive(self, handle: WorkerHandle) -> bool | None:
        popen = self._procs.get(handle.id)
        if popen is None:
            return False
        return _proc.is_running(popen)

    async def tail(self, handle: WorkerHandle, lines: int = 200) -> str:
        if not handle.aux or lines <= 0 or not Path(handle.aux).is_file():
            return ""
        with open(handle.aux, encoding="utf-8", errors="replace") as fh:
            return "".join(deque(fh, maxlen=lines))

    async def interrupt(self, handle: WorkerHandle) -> None:
        popen = self._procs.get(handle.id)
        if popen is not None:
            osops.interrupt_group(popen.pid)

    async def close(self, handle: WorkerHandle) -> None:
        popen = self._procs.pop(handle.id, None)
        if popen is not None:
            await asyncio.to_thread(_terminate_quietly, popen, self._grace)

    async def notify(self, message: str, title: Optional[str] = None) -> None:
        text = f"{title}: {message}" if title else message
        print(text, file=sys.stderr, flush=True)

    async def process_info(self, handle: WorkerHandle) -> dict[str, Any] | None:
        popen = self._procs.get(handle.id)
        if popen is None:
            return None
        # Tanto no POSIX (setsid) quanto no Windows (CREATE_NEW_PROCESS_GROUP) o pid do líder é o id do grupo.
        return {"pid": popen.pid, "pgid": popen.pid}

    async def current_context(self) -> dict[str, Any] | None:
        return None

