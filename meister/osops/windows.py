"""Operações de processo para Windows: psutil para árvores de processos e msvcrt para locks."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Callable, NoReturn, Optional

import msvcrt
import psutil

WaitFn = Callable[[float], bool]

_SIGKILL = getattr(signal, "SIGKILL", None)


class ProcessInspectionError(RuntimeError):
    """Falha ao listar os processos do sistema."""


def pid_alive(pid: int) -> bool:
    """Indica se existe um processo com ``pid`` que não é zumbi."""
    if pid <= 0:
        return False
    try:
        if not psutil.pid_exists(pid):
            return False
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def process_signature(pid: int) -> Optional[str]:
    """Devolve o horário de criação do processo, ou None se não puder ser verificado."""
    try:
        return str(psutil.Process(pid).create_time())
    except psutil.Error:
        return None


def popen_session_kwargs() -> dict:
    """Argumentos de ``Popen`` que colocam o filho em um novo grupo de processos."""
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]


def signal_group(pid: int, sig: int) -> None:
    """Aproxima ``sig`` no Windows: SIGINT vira Ctrl+Break, SIGKILL mata a árvore e o resto a encerra."""
    if sig == signal.SIGINT:
        interrupt_group(pid)
        return
    procs = _tree(pid)
    _signal_all(procs, "kill" if sig == _SIGKILL else "terminate")


def interrupt_group(pid: int) -> None:
    """Envia Ctrl+Break ao grupo de processos ``pid``; ignora se já encerrou ou falhar."""
    try:
        os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    except (OSError, ValueError):
        return


def _tree(pid: int) -> list[psutil.Process]:
    """Processo ``pid`` seguido de todos os descendentes atuais."""
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return []
    try:
        return [root, *root.children(recursive=True)]
    except psutil.Error:
        return [root]


def _signal_all(procs: list[psutil.Process], method: str) -> None:
    for proc in procs:
        try:
            getattr(proc, method)()
        except psutil.Error:
            continue


def _settle(procs: list[psutil.Process], grace: float, wait: Optional[WaitFn]) -> list[psutil.Process]:
    """Espera até ``grace`` segundos a saída de ``procs``; devolve os que continuam vivos."""
    deadline = time.monotonic() + grace
    if wait is not None:
        wait(grace)
    remaining = max(0.0, deadline - time.monotonic())
    _, alive = psutil.wait_procs(procs, timeout=remaining)
    return alive


def terminate_tree(pid: int, grace: float = 5.0, *, wait: Optional[WaitFn] = None) -> None:
    """Encerra a árvore ``pid``: terminate, espera até ``grace`` segundos e então kill.

    ``wait(timeout) -> bool``, quando dado, espera a saída do líder (por exemplo
    ``Popen.wait``); devolve False se o tempo estourar.
    """
    procs = _tree(pid)
    _signal_all(procs, "terminate")
    alive = _settle(procs, grace, wait)
    _signal_all(alive, "kill")
    _settle(alive, grace, None)


def terminate_pid(pid: int) -> None:
    """Chama ``terminate`` somente no processo ``pid``; ignora se não existe mais."""
    try:
        psutil.Process(pid).terminate()
    except psutil.Error:
        return


def terminate_group_of(pid: int) -> None:
    """Chama ``terminate`` uma vez em ``pid`` e nos descendentes atuais, sem esperar nem kill."""
    _signal_all(_tree(pid), "terminate")


def kill_tree(pgid_or_pid: int, *, is_pgid: bool = True) -> None:
    """Finaliza a árvore de processos com terminate e kill; no Windows ``pgid == pid``."""
    procs = _tree(pgid_or_pid)
    _signal_all(procs, "terminate")
    alive = _settle(procs, 0.15, None)
    _signal_all(alive, "kill")


def lock_file(fd: int, *, blocking: bool = False) -> bool:
    """Trava o byte 0 de ``fd`` exclusivamente; devolve False se o lock não foi obtido."""
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return True
        except OSError:
            if not blocking:
                return False
            time.sleep(0.05)


def unlock_file(fd: int) -> None:
    """Libera o lock do byte 0 de ``fd``."""
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]


def list_processes() -> list[tuple[int, list[str]]]:
    """Lista ``(pid, argv)`` de todos os processos, ignorando os sem linha de comando."""
    try:
        infos = psutil.process_iter(["pid", "cmdline"])
        processes: list[tuple[int, list[str]]] = []
        for proc in infos:
            argv = proc.info.get("cmdline") or []
            if argv:
                processes.append((int(proc.info["pid"]), [str(part) for part in argv]))
    except psutil.Error as exc:
        raise ProcessInspectionError(str(exc)) from exc
    return processes


def kill_self() -> NoReturn:
    """Encerra o processo atual imediatamente, sem executar limpeza."""
    os._exit(137)
