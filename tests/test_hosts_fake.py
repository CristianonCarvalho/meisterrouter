from __future__ import annotations

import pytest

from meister.hosts import CAP_VISIBLE, HostError, WorkerCommand, WorkerHandle, WorkerHost
from tests.hosts_contract import HostContract, wait_command
from tests.hosts_fake import FakeHost


class TestFakeHostContract(HostContract):
    @pytest.fixture
    def host(self) -> FakeHost:
        return FakeHost()

    async def spawn_printing(self, host: WorkerHost, text: str) -> WorkerHandle:
        assert isinstance(host, FakeHost)
        handle = await host.spawn(wait_command())
        host.print_line(handle, text)
        return handle

    async def spawn_waiting(self, host: WorkerHost) -> WorkerHandle:
        return await host.spawn(wait_command())

    async def finish(self, host: WorkerHost, handle: WorkerHandle) -> None:
        assert isinstance(host, FakeHost)
        host.finish(handle)


def test_fake_host_satisfies_worker_host_protocol() -> None:
    assert isinstance(FakeHost(), WorkerHost)
    assert FakeHost.capabilities == frozenset({CAP_VISIBLE})


@pytest.mark.asyncio
async def test_spawn_records_command_and_layout() -> None:
    host = FakeHost()
    cmd = WorkerCommand(argv=["x"], env={"A": "1"}, cwd="/tmp", label="w")
    handle = await host.spawn(cmd, layout="split")
    assert host.command_of(handle) is cmd
    assert host._worker(handle).layout == "split"


@pytest.mark.asyncio
async def test_spawn_ids_are_unique() -> None:
    host = FakeHost()
    a = await host.spawn(wait_command("a"))
    b = await host.spawn(wait_command("b"))
    assert a.id != b.id


@pytest.mark.asyncio
async def test_tail_respects_line_limit() -> None:
    host = FakeHost()
    handle = await host.spawn(wait_command())
    for i in range(5):
        host.print_line(handle, f"l{i}")
    assert await host.tail(handle, lines=2) == "l3\nl4"
    assert await host.tail(handle) == "l0\nl1\nl2\nl3\nl4"


@pytest.mark.asyncio
async def test_interrupt_counts_and_stops_worker() -> None:
    host = FakeHost()
    handle = await host.spawn(wait_command())
    await host.interrupt(handle)
    assert host.interrupt_count(handle) == 1
    assert await host.alive(handle) is False


@pytest.mark.asyncio
async def test_print_after_exit_is_rejected() -> None:
    host = FakeHost()
    handle = await host.spawn(wait_command())
    host.finish(handle)
    with pytest.raises(HostError):
        host.print_line(handle, "tarde demais")


@pytest.mark.asyncio
async def test_unknown_handle_raises_host_error() -> None:
    host = FakeHost()
    ghost = WorkerHandle(id="nao-existe")
    with pytest.raises(HostError):
        await host.alive(ghost)
    with pytest.raises(HostError):
        await host.tail(ghost)


@pytest.mark.asyncio
async def test_notify_is_recorded() -> None:
    host = FakeHost()
    await host.notify("oi", title="t")
    await host.notify("sem título")
    assert host.notifications == [("oi", "t"), ("sem título", None)]


@pytest.mark.asyncio
async def test_optional_introspection_returns_none() -> None:
    host = FakeHost()
    handle = await host.spawn(wait_command())
    assert await host.process_info(handle) is None
    assert await host.current_context() is None
