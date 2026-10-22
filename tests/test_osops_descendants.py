"""Testes para a função descendant_commands do pacote meister.osops."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
import types
import uuid
from pathlib import Path

import pytest

from meister import osops

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PATH = ROOT / "meister" / "osops" / "windows.py"


def _wait_popen(child: subprocess.Popen, timeout: float) -> bool:
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX implementation only")
def test_posix_real_process_descendants() -> None:
    father_marker = f"osops-father-{uuid.uuid4().hex[:8]}"
    child_marker = f"osops-child-{uuid.uuid4().hex[:8]}"
    neto_marker = f"osops-neto-{uuid.uuid4().hex[:8]}"

    father_script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([\n"
        "    sys.executable, '-c',\n"
        "    'import subprocess, sys, time; '\n"
        f"    'neto = subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(10)\", \"{neto_marker}\"]); '\n"
        "    'time.sleep(10)',\n"
        f"    '{child_marker}',\n"
        "])\n"
        "time.sleep(10)\n"
    )

    father = subprocess.Popen(
        [sys.executable, "-c", father_script, father_marker],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **osops.popen_session_kwargs(),
    )
    try:
        deadline = time.monotonic() + 10.0
        cmds: list[list[str]] = []
        while time.monotonic() < deadline:
            cmds = osops.descendant_commands(father.pid)
            if any(neto_marker in cmd for cmd in cmds):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"Neto process with marker {neto_marker} not found in descendants: {cmds}")

        assert any(neto_marker in cmd for cmd in cmds)
        assert any(child_marker in cmd for cmd in cmds)
        assert not any(father_marker in cmd for cmd in cmds)
    finally:
        osops.terminate_tree(father.pid, wait=lambda t: _wait_popen(father, t))
        father.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX implementation only")
def test_posix_real_process_child_appears_and_parent_not() -> None:
    father_marker = f"osops-pai-{uuid.uuid4().hex[:8]}"
    child_marker = f"osops-filho-{uuid.uuid4().hex[:8]}"

    father_script = (
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)', '{child_marker}'])\n"
        "time.sleep(10)\n"
    )

    father = subprocess.Popen(
        [sys.executable, "-c", father_script, father_marker],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **osops.popen_session_kwargs(),
    )
    try:
        deadline = time.monotonic() + 10.0
        cmds: list[list[str]] = []
        while time.monotonic() < deadline:
            cmds = osops.descendant_commands(father.pid)
            if any(child_marker in cmd for cmd in cmds):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"Process with marker {child_marker} not found in descendants: {cmds}")

        assert any(child_marker in cmd for cmd in cmds)
        assert not any(father_marker in cmd for cmd in cmds)
    finally:
        osops.terminate_tree(father.pid, wait=lambda t: _wait_popen(father, t))
        father.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX implementation only")
def test_posix_simulated_tree_with_cycles_and_malformed_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    from meister.osops import posix

    fake_ps_output = (
        "  100    1 /usr/bin/python3 parent.py --parent\n"
        "  200  100 /usr/bin/python3 child1.py --child1\n"
        "  300  200 /usr/bin/python3 grandchild1.py --grandchild1\n"
        "  400  300 /usr/bin/python3 greatgrandchild.py --cycle-to-200\n"
        "  200  400 /usr/bin/python3 cycle_back.py\n"
        "  100  300 /usr/bin/python3 cycle_to_parent.py\n"
        "  500  500 /usr/bin/python3 self_cycle.py\n"
        "  not a valid line\n"
        "  999\n"
        "  abc def invalid pids here\n"
        "  201  100 /usr/bin/python3 \"unclosed quote arg\n"
        "  202  100 /usr/bin/python3 child2.py --child2\n"
        "  900    1 /usr/bin/python3 other.py\n"
    )

    real_run = subprocess.run

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv and argv[0] == "ps":
            return subprocess.CompletedProcess(argv, 0, stdout=fake_ps_output, stderr="")
        return real_run(argv, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(posix.subprocess, "run", fake_run)

    descendants = posix.descendant_commands(100)

    cmd_strings = [" ".join(cmd) for cmd in descendants]
    assert any("child1.py" in s for s in cmd_strings)
    assert any("grandchild1.py" in s for s in cmd_strings)
    assert any("greatgrandchild.py" in s for s in cmd_strings)
    assert any("child2.py" in s for s in cmd_strings)

    assert not any("parent.py" in s for s in cmd_strings)
    assert not any("cycle_to_parent.py" in s for s in cmd_strings)
    assert not any("other.py" in s for s in cmd_strings)
    assert not any("self_cycle.py" in s for s in cmd_strings)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX implementation only")
def test_posix_pid_without_children_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from meister.osops import posix

    fake_ps_output = (
        "  100    1 /usr/bin/python3 parent.py --parent\n"
        "  200  100 /usr/bin/python3 child.py\n"
    )

    real_run = subprocess.run

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv and argv[0] == "ps":
            return subprocess.CompletedProcess(argv, 0, stdout=fake_ps_output, stderr="")
        return real_run(argv, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(posix.subprocess, "run", fake_run)

    assert posix.descendant_commands(200) == []
    assert posix.descendant_commands(999) == []
    assert posix.descendant_commands(0) == []
    assert posix.descendant_commands(-1) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX implementation only")
def test_posix_ps_failure_raises_process_inspection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from meister.osops import posix

    real_run = subprocess.run

    def fake_run_error(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv and argv[0] == "ps":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="ps: failed")
        return real_run(argv, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(posix.subprocess, "run", fake_run_error)

    with pytest.raises(posix.ProcessInspectionError, match="ps: failed"):
        posix.descendant_commands(100)

    def fake_run_os_error(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv and argv[0] == "ps":
            raise OSError("ps not found")
        return real_run(argv, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(posix.subprocess, "run", fake_run_os_error)

    with pytest.raises(posix.ProcessInspectionError, match="ps not found"):
        posix.descendant_commands(100)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX implementation only")
def test_posix_reexports() -> None:
    from meister.osops import posix

    assert osops.descendant_commands is posix.descendant_commands
    assert "descendant_commands" in osops.__all__


class FakeError(Exception):
    pass


class FakeNoSuchProcess(FakeError):
    pass


class FakeChildProcess:
    def __init__(
        self,
        pid: int,
        cmdline: list[str] | None = None,
        error_on_cmdline: Exception | None = None,
        children: list[FakeChildProcess] | None = None,
    ) -> None:
        self.pid = pid
        self._cmdline = cmdline
        self._error_on_cmdline = error_on_cmdline
        self._children = children or []

    def cmdline(self) -> list[str] | None:
        if self._error_on_cmdline:
            raise self._error_on_cmdline
        return self._cmdline

    def children(self, recursive: bool = False) -> list[FakeChildProcess]:
        out: list[FakeChildProcess] = []
        for child in self._children:
            out.append(child)
            if recursive:
                out.extend(child.children(recursive=True))
        return out


def _load_windows_module(monkeypatch: pytest.MonkeyPatch, psutil_mod: types.ModuleType) -> types.ModuleType:
    if "msvcrt" not in sys.modules:
        mock_msvcrt = types.ModuleType("msvcrt")
        mock_msvcrt.LK_NBLCK = 2  # type: ignore[attr-defined]
        mock_msvcrt.LK_UNLCK = 0  # type: ignore[attr-defined]
        mock_msvcrt.locking = lambda *args: None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "msvcrt", mock_msvcrt)
    monkeypatch.setitem(sys.modules, "psutil", psutil_mod)
    spec = importlib.util.spec_from_file_location("osops_windows_under_test_descendants", WINDOWS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_descendant_commands_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_psutil = types.ModuleType("psutil")
    fake_psutil.Error = FakeError  # type: ignore[attr-defined]
    fake_psutil.NoSuchProcess = FakeNoSuchProcess  # type: ignore[attr-defined]

    c1 = FakeChildProcess(201, cmdline=["cmd.exe", "/c", "worker.bat"])
    c2_no_cmd = FakeChildProcess(202, cmdline=[])
    c3_disappeared = FakeChildProcess(203, error_on_cmdline=FakeNoSuchProcess(203))
    c4_grandchild = FakeChildProcess(301, cmdline=["python.exe", "nested.py"])
    c1._children = [c4_grandchild]

    root = FakeChildProcess(100, cmdline=["root.exe"], children=[c1, c2_no_cmd, c3_disappeared])

    def fake_process_fn(pid: int) -> FakeChildProcess:
        if pid == 100:
            return root
        if pid == 200:
            return FakeChildProcess(200, children=[])
        if pid == 500:
            raise FakeError("access denied or other psutil error")
        raise FakeNoSuchProcess(pid)

    fake_psutil.Process = fake_process_fn  # type: ignore[attr-defined]

    win = _load_windows_module(monkeypatch, fake_psutil)

    # Normal traversal: includes c1 and grandchild c4, ignores c2 (no cmd) and c3 (NoSuchProcess)
    cmds = win.descendant_commands(100)
    assert cmds == [["cmd.exe", "/c", "worker.bat"], ["python.exe", "nested.py"]]

    # Nonexistent PID returns empty
    assert win.descendant_commands(999) == []

    # PID <= 0 returns empty
    assert win.descendant_commands(0) == []
    assert win.descendant_commands(-1) == []

    # PID without children returns empty
    assert win.descendant_commands(200) == []

    # Other psutil error raises ProcessInspectionError
    with pytest.raises(win.ProcessInspectionError):
        win.descendant_commands(500)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows implementation only")
def test_windows_real_process_descendants() -> None:
    from meister.osops import windows

    father_marker = f"osops-win-father-{uuid.uuid4().hex[:8]}"
    child_marker = f"osops-win-child-{uuid.uuid4().hex[:8]}"

    father_script = (
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)', '{child_marker}'])\n"
        "time.sleep(10)\n"
    )

    father = subprocess.Popen(
        [sys.executable, "-c", father_script, father_marker],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **windows.popen_session_kwargs(),
    )
    try:
        deadline = time.monotonic() + 10.0
        cmds: list[list[str]] = []
        while time.monotonic() < deadline:
            cmds = windows.descendant_commands(father.pid)
            if any(child_marker in cmd for cmd in cmds):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"Child process with marker {child_marker} not found in descendants: {cmds}")

        assert any(child_marker in cmd for cmd in cmds)
        assert not any(father_marker in cmd for cmd in cmds)
    finally:
        windows.terminate_tree(father.pid)
        father.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows implementation only")
def test_windows_reexports() -> None:
    from meister.osops import windows

    assert osops.descendant_commands is windows.descendant_commands
    assert "descendant_commands" in osops.__all__
