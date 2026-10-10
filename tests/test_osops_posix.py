"""Testes da implementação POSIX do pacote meister.osops."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from meister import osops
from meister.osops import posix
from tests.platform_marks import posix_only

pytestmark = posix_only

ROOT = Path(__file__).resolve().parents[1]
SLEEPER = "import time\ntime.sleep(60)\n"


def _spawn(code: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", code, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **osops.popen_session_kwargs(),
    )


def _reap(popen: subprocess.Popen) -> None:
    if popen.poll() is None:
        popen.kill()
    popen.wait(timeout=10)


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=20)
    return child.pid


def _wait_ready(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return
        time.sleep(0.02)
    raise AssertionError(f"{path} não foi escrito a tempo")


def test_package_reexports_posix_implementation():
    assert osops.pid_alive is posix.pid_alive
    assert osops.process_signature is posix.process_signature
    assert osops.popen_session_kwargs is posix.popen_session_kwargs
    assert osops.interrupt_group is posix.interrupt_group
    assert osops.terminate_tree is posix.terminate_tree
    assert osops.kill_tree is posix.kill_tree
    assert osops.lock_file is posix.lock_file
    assert osops.unlock_file is posix.unlock_file
    assert osops.list_processes is posix.list_processes
    assert osops.kill_self is posix.kill_self


def test_import_does_not_require_psutil():
    code = (
        "import sys\n"
        "sys.modules['psutil'] = None\n"
        "from meister import osops\n"
        "import os\n"
        "assert osops.pid_alive(os.getpid())\n"
        "print(osops.pid_alive.__module__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "meister.osops.posix"


def test_pid_alive_for_self_and_reaped_child():
    assert posix.pid_alive(os.getpid()) is True
    assert posix.pid_alive(0) is False
    assert posix.pid_alive(-1) is False
    assert posix.pid_alive(_dead_pid()) is False


def test_process_signature_is_stable_for_live_process_and_none_when_gone():
    child = _spawn(SLEEPER)
    try:
        first = posix.process_signature(child.pid)
        assert first
        assert posix.process_signature(child.pid) == first
    finally:
        _reap(child)
    assert posix.process_signature(child.pid) is None


def test_popen_session_kwargs_creates_new_group():
    assert posix.popen_session_kwargs() == {"start_new_session": True}
    child = _spawn(SLEEPER)
    try:
        assert os.getpgid(child.pid) == child.pid
    finally:
        _reap(child)


def test_signal_group_stops_group_with_sigterm():
    child = _spawn(SLEEPER)
    try:
        posix.signal_group(child.pid, signal.SIGTERM)
        assert child.wait(timeout=10) == -signal.SIGTERM
    finally:
        _reap(child)


def test_signal_group_ignores_group_that_already_exited():
    child = _spawn("pass")
    child.wait(timeout=20)
    posix.signal_group(child.pid, signal.SIGTERM)
    posix.signal_group(child.pid, signal.SIGKILL)


def test_interrupt_group_sends_sigint():
    child = _spawn(SLEEPER)
    try:
        posix.interrupt_group(child.pid)
        assert child.wait(timeout=10) != 0
    finally:
        _reap(child)


def test_terminate_tree_with_default_wait_for_well_behaved_group(tmp_path: Path):
    ready = tmp_path / "ready"
    child = _spawn(f"import pathlib, time\npathlib.Path({str(ready)!r}).write_text('ready')\ntime.sleep(60)\n")
    reaper = threading.Thread(target=child.wait, daemon=True)
    reaper.start()
    try:
        _wait_ready(ready)
        started = time.monotonic()
        posix.terminate_tree(child.pid, grace=10.0)
        reaper.join(timeout=10)
        assert time.monotonic() - started < 9
        assert child.returncode == -signal.SIGTERM
    finally:
        _reap(child)


def test_terminate_tree_escalates_to_sigkill_with_default_wait(tmp_path: Path):
    ready = tmp_path / "ready"
    code = (
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(60)\n"
    )
    child = _spawn(code)
    reaper = threading.Thread(target=child.wait, daemon=True)
    reaper.start()
    try:
        _wait_ready(ready)
        posix.terminate_tree(child.pid, grace=0.3)
        reaper.join(timeout=10)
        assert child.returncode == -signal.SIGKILL
    finally:
        _reap(child)


def test_terminate_tree_with_wait_callback_reaps_leader():
    child = _spawn(SLEEPER)
    calls: list[float] = []

    def wait(timeout: float) -> bool:
        calls.append(timeout)
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    try:
        time.sleep(0.2)
        posix.terminate_tree(child.pid, grace=10.0, wait=wait)
        assert child.returncode == -signal.SIGTERM
        assert calls == [10.0, 10.0]
    finally:
        _reap(child)


def test_kill_tree_terminates_process_group():
    child = _spawn(SLEEPER)
    try:
        time.sleep(0.2)
        posix.kill_tree(child.pid, is_pgid=True)
        assert child.wait(timeout=10) in (-signal.SIGTERM, -signal.SIGKILL)
    finally:
        _reap(child)


def test_kill_tree_tolerates_missing_pid():
    posix.kill_tree(_dead_pid(), is_pgid=False)


def test_lock_file_is_exclusive_and_releasable(tmp_path: Path):
    path = str(tmp_path / "daemon.pid")
    first = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    second = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        assert posix.lock_file(first) is True
        assert posix.lock_file(second) is False
        posix.unlock_file(first)
        assert posix.lock_file(second) is True
        posix.unlock_file(second)
    finally:
        os.close(first)
        os.close(second)


def test_lock_file_blocking_acquires_free_lock(tmp_path: Path):
    fd = os.open(str(tmp_path / "free.pid"), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        assert posix.lock_file(fd, blocking=True) is True
        posix.unlock_file(fd)
    finally:
        os.close(fd)


def test_list_processes_reports_argv_of_live_process():
    marker = "osops-marker-7f3a"
    child = _spawn(SLEEPER, marker)
    try:
        time.sleep(0.2)
        processes = dict(posix.list_processes())
        assert child.pid in processes
        assert processes[child.pid][-1] == marker
        assert all(isinstance(argv, list) for argv in processes.values())
    finally:
        _reap(child)


def test_kill_self_terminates_with_sigkill():
    code = "from meister.osops import kill_self\nkill_self()\n"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL
