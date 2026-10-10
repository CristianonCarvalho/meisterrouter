"""Testes da implementação Windows do pacote meister.osops.

A maior parte roda em qualquer plataforma: ``psutil`` e ``msvcrt`` são simulados em
``sys.modules`` e o módulo é carregado a partir do arquivo. Os testes com processos
reais rodam só no Windows.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import types
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PATH = ROOT / "meister" / "osops" / "windows.py"


class FakeError(Exception):
    """Substituto de ``psutil.Error``."""


class FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        zombie: bool = False,
        children: Optional[list[FakeProcess]] = None,
        create_time: float = 123.5,
        calls: Optional[list] = None,
    ) -> None:
        self.pid = pid
        self._zombie = zombie
        self._children = children or []
        self._create_time = create_time
        self.calls = calls if calls is not None else []

    def status(self) -> str:
        return "zombie" if self._zombie else "running"

    def children(self, recursive: bool = False) -> list[FakeProcess]:
        out: list[FakeProcess] = []
        for child in self._children:
            out.append(child)
            if recursive:
                out.extend(child.children(recursive=True))
        return out

    def create_time(self) -> float:
        return self._create_time

    def terminate(self) -> None:
        self.calls.append(("terminate", self.pid))

    def kill(self) -> None:
        self.calls.append(("kill", self.pid))


def _make_fake_psutil(processes: dict[int, FakeProcess], alive_after_wait: set[int]) -> types.ModuleType:
    module = types.ModuleType("psutil")
    module.Error = FakeError  # type: ignore[attr-defined]
    module.STATUS_ZOMBIE = "zombie"  # type: ignore[attr-defined]

    def pid_exists(pid: int) -> bool:
        return pid in processes

    def Process(pid: int) -> FakeProcess:  # noqa: N802 - nome da API do psutil
        if pid not in processes:
            raise FakeError(pid)
        return processes[pid]

    def wait_procs(procs: list[FakeProcess], timeout: float) -> tuple[list, list]:
        alive = [p for p in procs if p.pid in alive_after_wait]
        gone = [p for p in procs if p.pid not in alive_after_wait]
        return gone, alive

    module.pid_exists = pid_exists  # type: ignore[attr-defined]
    module.Process = Process  # type: ignore[attr-defined]
    module.wait_procs = wait_procs  # type: ignore[attr-defined]
    return module


def _make_fake_msvcrt(locking) -> types.ModuleType:
    module = types.ModuleType("msvcrt")
    module.LK_NBLCK = 2  # type: ignore[attr-defined]
    module.LK_UNLCK = 0  # type: ignore[attr-defined]
    module.locking = locking  # type: ignore[attr-defined]
    return module


@pytest.fixture
def load_windows(monkeypatch):
    """Carrega ``windows.py`` com ``psutil`` e ``msvcrt`` simulados e devolve o módulo."""

    def _load(psutil_module: types.ModuleType, msvcrt_module: types.ModuleType) -> types.ModuleType:
        monkeypatch.setitem(sys.modules, "psutil", psutil_module)
        monkeypatch.setitem(sys.modules, "msvcrt", msvcrt_module)
        spec = importlib.util.spec_from_file_location("osops_windows_under_test", WINDOWS_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load


def _no_locking(*_args) -> None:
    raise AssertionError("msvcrt.locking não deveria ser chamado")


def test_pid_alive_reports_running_zombie_and_missing(load_windows):
    procs = {10: FakeProcess(10), 11: FakeProcess(11, zombie=True)}
    win = load_windows(_make_fake_psutil(procs, set()), _make_fake_msvcrt(_no_locking))

    assert win.pid_alive(10) is True
    assert win.pid_alive(11) is False
    assert win.pid_alive(99) is False
    assert win.pid_alive(0) is False
    assert win.pid_alive(-1) is False


def test_process_signature_uses_create_time_or_none(load_windows):
    procs = {10: FakeProcess(10, create_time=42.25)}
    win = load_windows(_make_fake_psutil(procs, set()), _make_fake_msvcrt(_no_locking))

    assert win.process_signature(10) == "42.25"
    assert win.process_signature(99) is None


def test_popen_session_kwargs_uses_new_process_group(load_windows, monkeypatch):
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    win = load_windows(_make_fake_psutil({}, set()), _make_fake_msvcrt(_no_locking))

    assert win.popen_session_kwargs() == {"creationflags": 512}


def test_interrupt_group_sends_ctrl_break_and_tolerates_failure(load_windows, monkeypatch):
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    win = load_windows(_make_fake_psutil({}, set()), _make_fake_msvcrt(_no_locking))

    win.interrupt_group(77)
    assert sent == [(77, 1)]

    def boom(pid, sig):
        raise OSError("já encerrou")

    monkeypatch.setattr(os, "kill", boom)
    win.interrupt_group(77)


def test_terminate_tree_terminates_descendants_then_kills_survivors(load_windows):
    calls: list = []
    grandchild = FakeProcess(3, calls=calls)
    child = FakeProcess(2, children=[grandchild], calls=calls)
    root = FakeProcess(1, children=[child], calls=calls)
    procs = {1: root, 2: child, 3: grandchild}
    win = load_windows(_make_fake_psutil(procs, alive_after_wait={2}), _make_fake_msvcrt(_no_locking))

    win.terminate_tree(1, grace=0.01)

    assert calls[:3] == [("terminate", 1), ("terminate", 2), ("terminate", 3)]
    assert calls[3:] == [("kill", 2)]


def test_kill_tree_terminates_then_kills_and_ignores_missing_pid(load_windows):
    calls: list = []
    proc = FakeProcess(5, calls=calls)
    win = load_windows(_make_fake_psutil({5: proc}, alive_after_wait={5}), _make_fake_msvcrt(_no_locking))

    win.kill_tree(5)
    win.kill_tree(999)

    assert calls == [("terminate", 5), ("kill", 5)]


def test_signal_group_maps_sigint_to_ctrl_break(load_windows, monkeypatch):
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    win = load_windows(_make_fake_psutil({}, set()), _make_fake_msvcrt(_no_locking))

    win.signal_group(8, signal.SIGINT)

    assert sent == [(8, 1)]


def test_lock_file_returns_false_when_busy_and_true_when_free(load_windows, tmp_path):
    busy = {"value": True}
    calls: list = []

    def locking(fd, mode, nbytes):
        calls.append((mode, nbytes))
        if busy["value"]:
            raise OSError("ocupado")

    win = load_windows(_make_fake_psutil({}, set()), _make_fake_msvcrt(locking))
    fd = os.open(tmp_path / "lock.bin", os.O_RDWR | os.O_CREAT)
    try:
        assert win.lock_file(fd) is False
        busy["value"] = False
        assert win.lock_file(fd) is True
        assert calls == [(2, 1), (2, 1)]
        win.unlock_file(fd)
    finally:
        os.close(fd)


def test_list_processes_returns_pid_and_argv_and_skips_empty(load_windows, monkeypatch):
    class Info:
        def __init__(self, info):
            self.info = info

    entries = [
        Info({"pid": 4, "cmdline": ["python", "-m", "meister"]}),
        Info({"pid": 5, "cmdline": None}),
        Info({"pid": 6, "cmdline": []}),
    ]
    psutil_module = _make_fake_psutil({}, set())
    psutil_module.process_iter = lambda attrs: iter(entries)  # type: ignore[attr-defined]
    win = load_windows(psutil_module, _make_fake_msvcrt(_no_locking))

    assert win.list_processes() == [(4, ["python", "-m", "meister"])]


def test_list_processes_wraps_psutil_error(load_windows):
    psutil_module = _make_fake_psutil({}, set())

    def failing(attrs):
        raise FakeError("sem acesso")

    psutil_module.process_iter = failing  # type: ignore[attr-defined]
    win = load_windows(psutil_module, _make_fake_msvcrt(_no_locking))

    with pytest.raises(win.ProcessInspectionError, match="sem acesso"):
        win.list_processes()


def test_kill_self_exits_with_137(load_windows, monkeypatch):
    codes: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code: codes.append(code))
    win = load_windows(_make_fake_psutil({}, set()), _make_fake_msvcrt(_no_locking))

    win.kill_self()

    assert codes == [137]


@pytest.mark.skipif(sys.platform != "win32", reason="processos reais só no Windows")
def test_real_process_is_alive_and_terminated_by_tree():
    from meister.osops import windows

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **windows.popen_session_kwargs(),
    )
    try:
        assert windows.pid_alive(child.pid) is True
        assert windows.process_signature(child.pid) is not None
        windows.terminate_tree(child.pid, grace=5.0, wait=lambda t: _wait_popen(child, t))
        assert child.wait(timeout=10) is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="msvcrt só existe no Windows")
def test_real_lock_file_is_exclusive(tmp_path):
    from meister.osops import windows

    fd = os.open(tmp_path / "lock.bin", os.O_RDWR | os.O_CREAT)
    other = os.open(tmp_path / "lock.bin", os.O_RDWR)
    try:
        assert windows.lock_file(fd) is True
        assert windows.lock_file(other) is False
        windows.unlock_file(fd)
        assert windows.lock_file(other) is True
        windows.unlock_file(other)
    finally:
        os.close(other)
        os.close(fd)


def _wait_popen(child: subprocess.Popen, timeout: float) -> bool:
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True
