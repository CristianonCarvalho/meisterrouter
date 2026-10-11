"""Leitura da saída do harness sem ``select`` (portável, inclusive Windows)."""

import select
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from meister import osops
from meister import worker as worker_mod
from meister.worker import HarnessWorker


def _make_worker(tmp_path: Path) -> HarnessWorker:
    worker = object.__new__(HarnessWorker)
    worker.model_tier = "pipe-test"
    worker.cwd = str(tmp_path)
    worker.config_path = None
    worker.config = None
    worker.harness = worker_mod.HARNESS_CODEX
    worker.resolved_model = None
    worker.tier_key = "pipe-test"
    worker.effort = None
    worker.cli_binary = sys.executable
    return worker


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: str,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    monkeypatch.setattr(
        worker_mod,
        "build_harness_command",
        lambda *args, **kwargs: [sys.executable, "-u", "-c", script],
    )
    return _make_worker(tmp_path).run_task("pipe", timeout=timeout)


def test_multiline_output_arrives_complete_and_in_order(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, "for i in range(50): print(f'linha {i}')")

    assert result["exit_code"] == 0
    assert result["output"].splitlines() == [f"linha {i}" for i in range(50)]


def test_slow_writer_is_read(tmp_path, monkeypatch):
    script = (
        "import time\n"
        "for i in range(3):\n"
        "    print(f'devagar {i}', flush=True)\n"
        "    time.sleep(0.4)\n"
    )
    result = _run(tmp_path, monkeypatch, script)

    assert result["output"].splitlines() == ["devagar 0", "devagar 1", "devagar 2"]


def test_silent_process_finishes_without_hanging(tmp_path, monkeypatch):
    start = time.monotonic()
    result = _run(tmp_path, monkeypatch, "pass", timeout=20.0)

    assert result["exit_code"] == 0
    assert result["output"] == ""
    assert time.monotonic() - start < 10.0


def test_nonzero_exit_code_is_preserved(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="exit code 3"):
        _run(tmp_path, monkeypatch, "import sys; print('x'); sys.exit(3)")


def test_timeout_raises_quickly_and_kills_process(tmp_path, monkeypatch):
    pid_file = tmp_path / "child.pid"
    script = (
        "import os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "print('inicio', flush=True)\n"
        "time.sleep(60)\n"
    )
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        _run(tmp_path, monkeypatch, script, timeout=1.5)
    elapsed = time.monotonic() - start

    assert elapsed < 15.0
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5.0
    while osops.pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not osops.pid_alive(pid)


def test_large_output_does_not_hang(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        "import sys\nfor i in range(20000): sys.stdout.write(f'{i}\\n')\n",
        timeout=60.0,
    )

    lines = result["output"].splitlines()
    assert len(lines) == 20000
    assert lines[0] == "0" and lines[-1] == "19999"


def test_reading_does_not_depend_on_select(tmp_path, monkeypatch):
    """No Windows, select só aceita sockets (WinError 10038 com um PIPE)."""

    def fail_on_pipe(*args, **kwargs):
        raise OSError(10038, "An operation was attempted on something that is not a socket")

    monkeypatch.setattr(select, "select", fail_on_pipe)
    result = _run(tmp_path, monkeypatch, "print('ok')")

    assert result["output"].splitlines() == ["ok"]
