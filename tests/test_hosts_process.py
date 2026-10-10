"""Testes do host ``process`` (``ProcessHost``) com processos Python reais."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
import time
from pathlib import Path

import pytest

from meister.hosts import ProcessHost, WorkerCommand, WorkerHandle, WorkerHost
from meister.hosts import _proc
from tests.hosts_contract import HostContract, wait_command
from tests.platform_marks import posix_only

pytestmark = posix_only

_WAITER_SOURCE = """\
import os
import pathlib
import sys
import time

if len(sys.argv) > 1:
    print(sys.argv[1], flush=True)
done = pathlib.Path(os.environ["CONTRACT_SIGNAL_DIR"]) / f"done-{os.getpid()}"
while not done.exists():
    time.sleep(0.02)
"""


def _wait_for_file(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().endswith("\n"):
            return path.read_text()
        time.sleep(0.02)
    raise AssertionError(f"{path} não foi escrito a tempo")


@pytest.fixture
def host_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isola logs, sinais e PATH; instala ``contract-wait`` (espera o sentinela)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    waiter = tmp_path / "waiter.py"
    waiter.write_text(_WAITER_SOURCE, encoding="utf-8")
    contract_wait = bin_dir / "contract-wait"
    contract_wait.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(waiter))} \"$@\"\n",
        encoding="utf-8",
    )
    contract_wait.chmod(0o755)
    signal_dir = tmp_path / "signals"
    signal_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("CONTRACT_SIGNAL_DIR", str(signal_dir))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path


@pytest.fixture
def host(host_env: Path):
    process_host = ProcessHost(config={"grace_seconds": 0.5})
    try:
        yield process_host
    finally:
        for popen in list(process_host._procs.values()):
            if _proc.is_running(popen):
                _proc.terminate_tree(popen, grace=0.5)


def _waiter_command(host_env: Path, label: str = "waiter") -> WorkerCommand:
    argv = [sys.executable, str(host_env / "waiter.py")]
    return WorkerCommand(argv=argv, env={}, cwd=str(host_env), label=label)


class TestProcessHostContract(HostContract):
    async def spawn_printing(self, host: WorkerHost, text: str) -> WorkerHandle:
        assert isinstance(host, ProcessHost)
        return await host.spawn(WorkerCommand(argv=["contract-wait", text], env={}, cwd=".", label="contract-print"))

    async def spawn_waiting(self, host: WorkerHost) -> WorkerHandle:
        return await host.spawn(wait_command())

    async def finish(self, host: WorkerHost, handle: WorkerHandle) -> None:
        (Path(os.environ["CONTRACT_SIGNAL_DIR"]) / f"done-{handle.id}").touch()


def test_capabilities_and_protocol() -> None:
    process_host = ProcessHost()
    assert isinstance(process_host, WorkerHost)
    assert process_host.capabilities == frozenset()
    assert process_host.name == "process"


@pytest.mark.asyncio
async def test_start_and_current_context_are_noops(host: ProcessHost) -> None:
    await host.start()
    assert await host.current_context() is None


@pytest.mark.asyncio
async def test_exit_file_records_zero(host: ProcessHost, host_env: Path) -> None:
    exit_file = host_env / "ok.exit"
    handle = await host.spawn(
        WorkerCommand(
            argv=[sys.executable, "-c", "print('ok')"],
            env={},
            cwd=str(host_env),
            label="ok",
            exit_file=str(exit_file),
        )
    )
    assert _wait_for_file(exit_file) == "0\n"
    assert await host.alive(handle) is False


@pytest.mark.asyncio
async def test_exit_file_records_nonzero(host: ProcessHost, host_env: Path) -> None:
    exit_file = host_env / "fail.exit"
    await host.spawn(
        WorkerCommand(
            argv=[sys.executable, "-c", "raise SystemExit(3)"],
            env={},
            cwd=str(host_env),
            label="fail",
            exit_file=str(exit_file),
        )
    )
    assert _wait_for_file(exit_file) == "3\n"


@pytest.mark.asyncio
async def test_exit_file_records_shell_code_when_closed_by_signal(host: ProcessHost, host_env: Path) -> None:
    exit_file = host_env / "closed.exit"
    handle = await host.spawn(
        WorkerCommand(
            argv=[sys.executable, "-c", "import time; time.sleep(60)"],
            env={},
            cwd=str(host_env),
            label="closed",
            exit_file=str(exit_file),
        )
    )
    await host.close(handle)
    assert _wait_for_file(exit_file) == f"{128 + 15}\n"


@pytest.mark.asyncio
async def test_log_receives_output_and_default_path_is_under_log_dir(host: ProcessHost, host_env: Path) -> None:
    handle = await host.spawn(
        WorkerCommand(
            argv=[sys.executable, "-c", "import os, sys; print('log-marker', os.environ['MH_VAR'])"],
            env={"MH_VAR": "valor-do-env"},
            cwd=str(host_env),
            label="logger",
        )
    )
    expected = host_env / "logs" / "workers" / "logger.log"
    assert handle.aux == str(expected)

    async def has_marker() -> bool:
        return "log-marker valor-do-env" in await host.tail(handle)

    deadline = time.monotonic() + 10
    while not await has_marker():
        assert time.monotonic() < deadline, "saída não chegou ao log"
        await asyncio.sleep(0.02)
    assert "log-marker valor-do-env" in expected.read_text()


@pytest.mark.asyncio
async def test_explicit_log_file_creates_parent_dirs(host: ProcessHost, host_env: Path) -> None:
    log_file = host_env / "nested" / "dir" / "custom.log"
    handle = await host.spawn(
        WorkerCommand(
            argv=[sys.executable, "-c", "print('custom-log')"],
            env={},
            cwd=str(host_env),
            label="custom",
            log_file=str(log_file),
        )
    )
    assert handle.aux == str(log_file)
    deadline = time.monotonic() + 10
    while not (log_file.exists() and "custom-log" in log_file.read_text()):
        assert time.monotonic() < deadline, "saída não chegou ao log"
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_tail_returns_last_lines_and_empty_when_missing(host: ProcessHost, host_env: Path) -> None:
    log_file = host_env / "many.log"
    log_file.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    handle = WorkerHandle(id="0", aux=str(log_file))
    assert await host.tail(handle, lines=2) == "d\ne\n"
    assert await host.tail(WorkerHandle(id="0", aux=str(host_env / "missing.log"))) == ""


@pytest.mark.asyncio
async def test_close_kills_process_that_ignores_sigterm(host_env: Path) -> None:
    host = ProcessHost(config={"grace_seconds": 0.3})
    script = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('pronto', flush=True)\n"
        "time.sleep(60)\n"
    )
    handle = await host.spawn(
        WorkerCommand(argv=[sys.executable, "-c", script], env={}, cwd=str(host_env), label="stubborn")
    )
    try:
        deadline = time.monotonic() + 10
        while "pronto" not in await host.tail(handle):
            assert time.monotonic() < deadline, "processo não ficou pronto"
            await asyncio.sleep(0.02)
        assert await host.alive(handle) is True
        await host.close(handle)
        assert await host.alive(handle) is False
        await host.close(handle)
    finally:
        for popen in list(host._procs.values()):
            if _proc.is_running(popen):
                _proc.terminate_tree(popen, grace=0.3)


@pytest.mark.asyncio
async def test_concurrent_spawns_get_distinct_handles(host: ProcessHost, host_env: Path) -> None:
    first = await host.spawn(_waiter_command(host_env, label="first"))
    second = await host.spawn(_waiter_command(host_env, label="second"))
    assert first.id != second.id
    assert await host.alive(first) is True
    assert await host.alive(second) is True
    await host.close(first)
    await host.close(second)


@pytest.mark.asyncio
async def test_unknown_handle_is_inert(host: ProcessHost) -> None:
    unknown = WorkerHandle(id="999999999", aux=None)
    assert await host.alive(unknown) is False
    assert await host.process_info(unknown) is None
    await host.interrupt(unknown)
    await host.close(unknown)


@pytest.mark.asyncio
async def test_process_info_reports_pid_and_group(host: ProcessHost, host_env: Path) -> None:
    handle = await host.spawn(_waiter_command(host_env))
    try:
        info = await host.process_info(handle)
        assert info is not None
        assert info["pid"] == int(handle.id)
        assert info["pgid"] == int(handle.id)
    finally:
        await host.close(handle)


@pytest.mark.asyncio
async def test_notify_writes_title_and_message_to_stderr(
    host: ProcessHost, capsys: pytest.CaptureFixture[str]
) -> None:
    await host.notify("terminou", title="worker")
    await host.notify("sem título")
    err = capsys.readouterr().err
    assert "worker: terminou" in err
    assert "sem título" in err
