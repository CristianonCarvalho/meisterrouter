"""Operações de processo para POSIX (Linux e macOS): sinais, grupos de processos e flock."""

from __future__ import annotations

import fcntl
import os
import shlex
import signal
import subprocess
import time
from typing import Callable, NoReturn, Optional

WaitFn = Callable[[float], bool]


class ProcessInspectionError(RuntimeError):
    """Falha ao listar os processos do sistema."""


def pid_alive(pid: int) -> bool:
    """Indica se existe um processo com ``pid``."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_signature(pid: int) -> Optional[str]:
    """Return the OS-reported start time for a PID, or None when it cannot be verified."""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    signature = result.stdout.strip()
    return signature if result.returncode == 0 and signature else None


def popen_session_kwargs() -> dict:
    """Argumentos de ``Popen`` que colocam o filho em um novo grupo de processos."""
    return {"start_new_session": True}


def signal_group(pid: int, sig: int) -> None:
    """Envia ``sig`` ao grupo de processos ``pid``; ignora se já encerrou.

    No macOS, ``killpg`` devolve EPERM quando o líder do grupo acabou de sair e ainda
    é zumbi. O grupo foi criado com ``start_new_session``, então ``PermissionError``
    aqui significa que o grupo já está encerrando, não falta de permissão.
    """
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        return


def interrupt_group(pid: int) -> None:
    """Envia SIGINT ao grupo de processos ``pid``."""
    signal_group(pid, signal.SIGINT)


def _wait_exit(pid: int, grace: float, wait: Optional[WaitFn]) -> bool:
    if wait is not None:
        return wait(grace)
    # Sem um ``wait`` do chamador, um líder zumbi conta como vivo até ser colhido.
    deadline = time.monotonic() + grace
    while pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def terminate_tree(pid: int, grace: float = 5.0, *, wait: Optional[WaitFn] = None) -> None:
    """Encerra o grupo ``pid``: SIGTERM, espera até ``grace`` segundos e então SIGKILL.

    ``wait(timeout) -> bool``, quando dado, espera a saída do líder (por exemplo
    ``Popen.wait``) e colhe o zumbi; devolve False se o tempo estourar.
    """
    signal_group(pid, signal.SIGTERM)
    _wait_exit(pid, grace, wait)
    signal_group(pid, signal.SIGKILL)
    _wait_exit(pid, grace, wait)


def terminate_pid(pid: int) -> None:
    """Envia SIGTERM somente ao processo ``pid``; ignora se não existe mais."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def terminate_group_of(pid: int) -> None:
    """Envia SIGTERM uma vez ao grupo de ``pid``, sem esperar nem SIGKILL.

    Se ``pid`` não tiver grupo verificável, ou o grupo for o do próprio processo
    chamador (o que mataria quem chama), envia o SIGTERM só ao ``pid``.
    """
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        terminate_pid(pid)
        return
    if pgid == os.getpgrp():
        terminate_pid(pid)
        return
    signal_group(pgid, signal.SIGTERM)


def kill_tree(pgid_or_pid: int, *, is_pgid: bool = True) -> None:
    """Finaliza de forma determinística um grupo de processos ou PID com SIGTERM e SIGKILL (Achado #8)."""
    try:
        if is_pgid and hasattr(os, "killpg"):
            os.killpg(pgid_or_pid, signal.SIGTERM)
        else:
            os.kill(pgid_or_pid, signal.SIGTERM)
    except (OSError, ProcessLookupError, PermissionError):
        return

    time.sleep(0.15)

    try:
        if is_pgid and hasattr(os, "killpg"):
            os.killpg(pgid_or_pid, signal.SIGKILL)
        else:
            os.kill(pgid_or_pid, signal.SIGKILL)
    except (OSError, ProcessLookupError, PermissionError):
        pass


def lock_file(fd: int, *, blocking: bool = False) -> bool:
    """Aplica ``flock`` exclusivo em ``fd``; devolve False se o lock não foi obtido."""
    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fd, flags)
    except (BlockingIOError, OSError):
        return False
    return True


def unlock_file(fd: int) -> None:
    """Libera o ``flock`` de ``fd``."""
    fcntl.flock(fd, fcntl.LOCK_UN)


def list_processes() -> list[tuple[int, list[str]]]:
    """Lista ``(pid, argv)`` lendo o comando de cada processo sem casar texto dentro de outro argumento."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ProcessInspectionError(result.stderr.strip())
    processes: list[tuple[int, list[str]]] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
            argv = shlex.split(fields[1])
        except ValueError:
            continue
        if argv:
            processes.append((pid, argv))
    return processes


def kill_self() -> NoReturn:
    """Encerra o processo atual com SIGKILL (não pode ser capturado)."""
    os.kill(os.getpid(), signal.SIGKILL)
    # O kernel pode devolver o controle à thread antes de encerrar o processo;
    # bloquear garante que só o SIGKILL termine a execução.
    while True:
        time.sleep(3600)
