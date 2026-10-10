"""Testes das operações de processo de meister.hosts._proc."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from meister.hosts import _proc
from tests.platform_marks import posix_only

pytestmark = posix_only


def _wait_for_file(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return
        time.sleep(0.02)
    raise AssertionError(f"{path} não foi escrito a tempo")


def _write_script(tmp_path: Path, name: str, body: str) -> Path:
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return script


def test_reexports_worker_helpers():
    from meister.worker import kill_process_tree, process_start_signature

    assert _proc.kill_tree is kill_process_tree
    assert _proc.process_signature is process_start_signature


def test_process_that_finishes_logs_output_and_closes_stdin(tmp_path):
    script = _write_script(
        tmp_path,
        "finish.py",
        "import sys\n"
        "data = sys.stdin.read()\n"
        "print('out-line', flush=True)\n"
        "print('err-line', file=sys.stderr, flush=True)\n"
        "print('stdin-bytes', len(data), flush=True)\n",
    )
    log_path = tmp_path / "logs" / "nested" / "worker.log"
    popen = _proc.start_detached(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        env=dict(os.environ),
        log_path=log_path,
    )
    try:
        assert popen.wait(timeout=20) == 0
        assert _proc.is_running(popen) is False
    finally:
        _proc.terminate_tree(popen, grace=0.5)

    content = log_path.read_text()
    assert "out-line" in content
    assert "err-line" in content
    assert "stdin-bytes 0" in content


def test_terminate_tree_sigterm_is_enough_for_well_behaved_process(tmp_path):
    ready = tmp_path / "ready"
    script = _write_script(
        tmp_path,
        "sleeper.py",
        "import pathlib, sys, time\n"
        f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(60)\n",
    )
    popen = _proc.start_detached(
        [sys.executable, str(script)],
        cwd=None,
        env=dict(os.environ),
        log_path=tmp_path / "sleeper.log",
    )
    try:
        _wait_for_file(ready)
        assert _proc.is_running(popen) is True
        _proc.terminate_tree(popen, grace=10.0)
        assert _proc.is_running(popen) is False
        assert popen.returncode == -signal.SIGTERM
    finally:
        _proc.terminate_tree(popen, grace=0.5)


def test_terminate_tree_escalates_to_sigkill_when_sigterm_ignored(tmp_path):
    ready = tmp_path / "ready"
    script = _write_script(
        tmp_path,
        "stubborn.py",
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(60)\n",
    )
    popen = _proc.start_detached(
        [sys.executable, str(script)],
        cwd=None,
        env=dict(os.environ),
        log_path=tmp_path / "stubborn.log",
    )
    try:
        _wait_for_file(ready)
        started = time.monotonic()
        _proc.terminate_tree(popen, grace=0.3)
        elapsed = time.monotonic() - started
        assert _proc.is_running(popen) is False
        assert popen.returncode == -signal.SIGKILL
        assert elapsed < 10
    finally:
        _proc.terminate_tree(popen, grace=0.5)


def test_signals_and_terminate_tolerate_already_dead_process(tmp_path):
    script = _write_script(tmp_path, "quick.py", "print('bye', flush=True)\n")
    popen = _proc.start_detached(
        [sys.executable, str(script)],
        cwd=None,
        env=dict(os.environ),
        log_path=tmp_path / "quick.log",
    )
    popen.wait(timeout=20)
    assert _proc.is_running(popen) is False

    _proc.signal_group(popen, signal.SIGTERM)
    _proc.signal_group(popen, signal.SIGKILL)
    _proc.terminate_tree(popen, grace=0.5)

    assert _proc.is_running(popen) is False
    assert (tmp_path / "quick.log").read_text().strip() == "bye"


def test_signal_group_and_terminate_tolerate_permission_error(tmp_path, monkeypatch):
    script = _write_script(tmp_path, "perm.py", "print('bye', flush=True)\n")
    popen = _proc.start_detached(
        [sys.executable, str(script)],
        cwd=None,
        env=dict(os.environ),
        log_path=tmp_path / "perm.log",
    )
    popen.wait(timeout=20)

    def _eperm(pid: int, sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(_proc.os, "killpg", _eperm)

    _proc.signal_group(popen, signal.SIGTERM)
    _proc.terminate_tree(popen, grace=0.5)


def test_signal_group_stops_running_process(tmp_path):
    ready = tmp_path / "ready"
    script = _write_script(
        tmp_path,
        "group.py",
        "import pathlib, time\n"
        f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(60)\n",
    )
    popen = _proc.start_detached(
        [sys.executable, str(script)],
        cwd=None,
        env=dict(os.environ),
        log_path=tmp_path / "group.log",
    )
    try:
        _wait_for_file(ready)
        _proc.signal_group(popen, signal.SIGTERM)
        assert popen.wait(timeout=10) == -signal.SIGTERM
    finally:
        _proc.terminate_tree(popen, grace=0.5)
