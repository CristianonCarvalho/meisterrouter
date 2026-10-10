"""Operações de processo usadas pelos hosts de workers.

Implementação POSIX (grupos de processos e sinais). Este módulo é o ponto de
troca para uma implementação de Windows: quem chama deve depender apenas das
funções públicas aqui definidas.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Sequence

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
            start_new_session=True,
        )


def is_running(popen: subprocess.Popen) -> bool:
    """Indica se o processo líder ainda está em execução."""
    return popen.poll() is None


def signal_group(popen: subprocess.Popen, sig: int) -> None:
    """Envia ``sig`` ao grupo de processos iniciado por ``popen``; ignora se já encerrou."""
    try:
        os.killpg(popen.pid, sig)
    except ProcessLookupError:
        return


def terminate_tree(popen: subprocess.Popen, grace: float = 5.0) -> None:
    """Encerra o grupo: SIGTERM, espera até ``grace`` segundos e então SIGKILL."""
    signal_group(popen, signal.SIGTERM)
    try:
        popen.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    signal_group(popen, signal.SIGKILL)
    try:
        popen.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
