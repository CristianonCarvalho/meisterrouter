"""Operações de processo usadas pelos hosts de workers.

Os sinais e a identidade de processos vêm de ``meister.osops``, que escolhe a
implementação da plataforma. Este módulo mantém apenas a orquestração sobre
``subprocess.Popen``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from meister import osops
from meister.worker import kill_process_tree as kill_tree
from meister.worker import process_start_signature as process_signature

__all__ = [
    "is_running",
    "kill_tree",
    "process_signature",
    "signal_group",
    "start_detached",
    "terminate_tree",
]


def start_detached(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    env: dict[str, str] | None,
    log_path: str | os.PathLike[str],
) -> subprocess.Popen:
    """Inicia ``argv`` em um novo grupo de processos, anexando saída e erro a ``log_path``."""
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("ab") as log:
        return subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            **osops.popen_session_kwargs(),
        )


def is_running(popen: subprocess.Popen) -> bool:
    """Indica se o processo líder ainda está em execução."""
    return popen.poll() is None


def signal_group(popen: subprocess.Popen, sig: int) -> None:
    """Envia ``sig`` ao grupo de processos iniciado por ``popen``; ignora se já encerrou."""
    osops.signal_group(popen.pid, sig)


def _wait_popen(popen: subprocess.Popen, timeout: float) -> bool:
    try:
        popen.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def terminate_tree(popen: subprocess.Popen, grace: float = 5.0) -> None:
    """Encerra o grupo: SIGTERM, espera até ``grace`` segundos e então SIGKILL."""
    osops.terminate_tree(popen.pid, grace, wait=lambda timeout: _wait_popen(popen, timeout))
