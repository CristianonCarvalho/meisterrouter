"""Testes do host ``tmux`` (``TmuxHost``) contra um servidor tmux isolado (``-L``)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

from meister.hosts import CAP_VISIBLE, HostError, TmuxHost, WorkerCommand, WorkerHandle, WorkerHost
from meister.hosts import tmux as tmux_module
from tests.hosts_contract import HostContract, make_command, wait_command

_NO_TMUX = sys.platform == "win32" or shutil.which("tmux") is None
needs_tmux = pytest.mark.skipif(_NO_TMUX, reason="requer o binário tmux (fora do Windows)")


def _socket_name() -> str:
    return f"meister-test-{os.getpid()}"


def _raw_tmux(socket_name: str, *args: str) -> str:
    result = subprocess.run(
        ["tmux", "-L", socket_name, *args], capture_output=True, text=True, check=False, env={**os.environ, "TMUX": ""}
    )
    return result.stdout if result.returncode == 0 else ""


@pytest.fixture
def host() -> Iterator[TmuxHost]:
    socket_name = _socket_name()
    try:
        yield TmuxHost(socket_name=socket_name)
    finally:
        _raw_tmux(socket_name, "kill-server")


async def _eventually(check, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if await check():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.02)


@needs_tmux
class TestTmuxHostContract(HostContract):
    @pytest.fixture(autouse=True)
    def _signal_dir(self, tmp_path: Path) -> None:
        self.signal_dir = tmp_path

    async def spawn_printing(self, host: WorkerHost, text: str) -> WorkerHandle:
        return await host.spawn(make_command(["sh", "-c", f"echo {text}; sleep 60"], "contract-print"))

    async def spawn_waiting(self, host: WorkerHost) -> WorkerHandle:
        sentinel = self.signal_dir / "done"
        script = f"while [ ! -e {sentinel} ]; do sleep 0.05; done"
        return await host.spawn(make_command(["sh", "-c", script], "contract-wait"))

    async def finish(self, host: WorkerHost, handle: WorkerHandle) -> None:
        (self.signal_dir / "done").touch()


@needs_tmux
@pytest.mark.asyncio
async def test_start_announces_detached_session(host: TmuxHost, capsys: pytest.CaptureFixture[str]) -> None:
    await host.start()
    session = f"meister-{os.getpid()}"
    assert (await host.current_context()) == {"session": session}
    assert f"attach -t {session}" in capsys.readouterr().err
    assert session in _raw_tmux(_socket_name(), "list-sessions", "-F", "#{session_name}")


@needs_tmux
def test_capabilities_and_protocol() -> None:
    tmux_host = TmuxHost()
    assert isinstance(tmux_host, WorkerHost)
    assert tmux_host.name == "tmux"
    assert tmux_host.capabilities == frozenset({CAP_VISIBLE})


@needs_tmux
@pytest.mark.asyncio
async def test_window_is_created_and_closed(host: TmuxHost) -> None:
    handle = await host.spawn(make_command(["sleep", "60"], "janela-teste"))
    names = _raw_tmux(_socket_name(), "list-windows", "-a", "-F", "#{window_id} #{window_name}")
    assert f"{handle.aux} janela-teste" in names

    await host.close(handle)
    assert str(handle.aux) not in _raw_tmux(_socket_name(), "list-windows", "-a", "-F", "#{window_id}")
    assert await host.alive(handle) is False


@needs_tmux
@pytest.mark.asyncio
async def test_close_is_idempotent_and_session_is_recreated(host: TmuxHost) -> None:
    first = await host.spawn(make_command(["sleep", "60"], "primeira"))
    await host.close(first)
    await host.close(first)
    second = await host.spawn(make_command(["sleep", "60"], "segunda"))
    assert await host.alive(second) is True
    await host.close(second)


@needs_tmux
@pytest.mark.asyncio
async def test_close_keeps_session_while_other_windows_remain(host: TmuxHost) -> None:
    first = await host.spawn(make_command(["sleep", "60"], "a"))
    second = await host.spawn(make_command(["sleep", "60"], "b"))
    await host.close(first)
    assert await host.alive(second) is True
    await host.close(second)
    assert _raw_tmux(_socket_name(), "list-sessions") == ""


@needs_tmux
@pytest.mark.asyncio
async def test_tail_returns_printed_text_and_limits_lines(host: TmuxHost) -> None:
    handle = await host.spawn(make_command(["sh", "-c", "printf 'um\\ndois\\ntres\\n'; sleep 60"]))

    async def printed() -> bool:
        return "tres" in await host.tail(handle)

    assert await _eventually(printed)
    assert await host.tail(handle) == "um\ndois\ntres\n"
    assert await host.tail(handle, lines=2) == "dois\ntres\n"
    assert await host.tail(handle, lines=0) == ""
    await host.close(handle)
    assert await host.tail(handle) == ""


@needs_tmux
@pytest.mark.asyncio
async def test_interrupt_stops_sleep(host: TmuxHost) -> None:
    handle = await host.spawn(make_command(["sleep", "60"]))
    assert await host.alive(handle) is True
    await host.interrupt(handle)

    async def stopped() -> bool:
        return (await host.alive(handle)) is False

    assert await _eventually(stopped)
    await host.close(handle)
    await host.interrupt(handle)


@needs_tmux
@pytest.mark.asyncio
async def test_process_info_reports_pid_and_unknown_pane_is_none(host: TmuxHost) -> None:
    handle = await host.spawn(make_command(["sleep", "60"]))
    info = await host.process_info(handle)
    assert info is not None
    assert info["pid"] > 0
    await host.close(handle)
    assert await host.process_info(handle) is None


@needs_tmux
@pytest.mark.asyncio
async def test_terminal_line_runs_in_cwd_with_shell_syntax(host: TmuxHost, tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    command = WorkerCommand(
        argv=[], env={}, cwd=str(tmp_path), label="cwd", terminal_line=f"FOO=bar; pwd > {out}; echo $FOO >> {out}"
    )
    handle = await host.spawn(command)

    async def written() -> bool:
        return out.is_file() and out.read_text().count("\n") == 2

    assert await _eventually(written)
    assert out.read_text().splitlines()[1] == "bar"
    assert Path(out.read_text().splitlines()[0]).resolve() == tmp_path.resolve()
    await host.close(handle)


@needs_tmux
@pytest.mark.asyncio
async def test_notify_without_client_writes_to_stderr(host: TmuxHost, capsys: pytest.CaptureFixture[str]) -> None:
    await host.notify("terminou", title="worker")
    assert "worker: terminou" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_spawn_requires_terminal_line() -> None:
    with pytest.raises(HostError):
        await TmuxHost().spawn(WorkerCommand(argv=["ls"], env={}, cwd=".", label="sem-linha"))


@pytest.mark.asyncio
async def test_missing_tmux_binary_is_a_clear_host_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name, *args, **kwargs: None)
    with pytest.raises(HostError, match="tmux"):
        await TmuxHost().start()
    with pytest.raises(HostError, match="tmux"):
        await TmuxHost().spawn(wait_command())


@pytest.mark.parametrize(
    ("output", "expected"),
    [("tmux 3.4", (3, 4)), ("tmux 3.3a", (3, 3)), ("tmux next-3.5", (3, 5)), ("tmux 2.9", (2, 9))],
)
def test_parse_version(output: str, expected: tuple[int, int]) -> None:
    assert tmux_module._parse_version(output) == expected


def test_parse_version_rejects_garbage() -> None:
    with pytest.raises(HostError):
        tmux_module._parse_version("not tmux")


@pytest.mark.asyncio
async def test_start_rejects_tmux_older_than_3(monkeypatch: pytest.MonkeyPatch) -> None:
    tmux_host = TmuxHost()

    async def fake_tmux(*args: str) -> str:
        return "tmux 2.9a"

    monkeypatch.setattr(tmux_host, "_tmux", fake_tmux)
    with pytest.raises(HostError, match=r"2\.9"):
        await tmux_host.start()
