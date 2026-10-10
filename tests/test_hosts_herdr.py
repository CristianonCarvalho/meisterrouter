from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from meister.hosts import (
    CAP_PUSH_EVENTS,
    CAP_VISIBLE,
    WorkerCommand,
    WorkerHandle,
    WorkerHost,
)
from meister.hosts.herdr import HerdrHost


def _client(**overrides):
    client = MagicMock(name="HerdrSocketClient")
    client.is_connected = True
    client.connect = AsyncMock()
    client.subscribe_events = AsyncMock()
    client.create_tab = AsyncMock(return_value=("tab-1", "pane-1"))
    client.split_pane = AsyncMock(return_value="pane-2")
    client.wait_pane_ready = AsyncMock(return_value=True)
    client.send_text = AsyncMock()
    client.pane_exists = AsyncMock(return_value=True)
    client.read_pane = AsyncMock(return_value="out")
    client.send_interrupt = AsyncMock()
    client.close_tab = AsyncMock(return_value=True)
    client.close_pane = AsyncMock()
    client.show_notification = AsyncMock()
    client._call = AsyncMock(return_value={"foreground_process_group_id": 42})
    client.get_current_pane = AsyncMock(return_value={"pane_id": "p", "workspace_id": "w"})
    for name, value in overrides.items():
        setattr(client, name, value)
    return client


def _command(terminal_line="claude --model x"):
    return WorkerCommand(
        argv=["claude", "--model", "x"],
        env={"A": "1"},
        cwd="/work",
        label="worker:t1",
        terminal_line=terminal_line,
    )


def test_herdr_host_satisfies_protocol_and_capabilities():
    host = HerdrHost(_client())
    assert isinstance(host, WorkerHost)
    assert host.name == "herdr"
    assert host.capabilities == frozenset({CAP_VISIBLE, CAP_PUSH_EVENTS})


@pytest.mark.asyncio
async def test_spawn_tab_creates_tab_waits_and_types_command_in_order():
    client = _client()
    host = HerdrHost(client)
    manager = MagicMock()
    manager.attach_mock(client.create_tab, "create_tab")
    manager.attach_mock(client.wait_pane_ready, "wait_pane_ready")
    manager.attach_mock(client.send_text, "send_text")

    handle = await host.spawn(_command(), layout="tab")

    assert handle == WorkerHandle(id="pane-1", aux="tab-1")
    client.create_tab.assert_awaited_once_with(cwd="/work", label="worker:t1", focus=False)
    client.wait_pane_ready.assert_awaited_once_with("pane-1")
    client.send_text.assert_awaited_once_with("pane-1", "claude --model x\n")
    assert [c[0] for c in manager.mock_calls] == ["create_tab", "wait_pane_ready", "send_text"]


@pytest.mark.asyncio
async def test_spawn_tab_default_layout_is_tab_and_skips_typing_without_terminal_line():
    client = _client()
    host = HerdrHost(client)

    handle = await host.spawn(_command(terminal_line=None))

    assert handle.aux == "tab-1"
    client.send_text.assert_not_awaited()
    client.wait_pane_ready.assert_not_awaited()


@pytest.mark.asyncio
async def test_spawn_tab_without_wait_pane_ready_still_sends_text():
    client = _client()
    del client.wait_pane_ready
    host = HerdrHost(client)

    await host.spawn(_command(), layout="tab")

    client.send_text.assert_awaited_once_with("pane-1", "claude --model x\n")


@pytest.mark.asyncio
async def test_spawn_pane_uses_split_and_has_no_aux():
    client = _client()
    host = HerdrHost(client, config={"direction": "down", "split_ratio": 0.3})

    handle = await host.spawn(_command(), layout="pane")

    assert handle == WorkerHandle(id="pane-2", aux=None)
    client.split_pane.assert_awaited_once_with(
        direction="down", command="claude --model x", split_ratio=0.3, cwd="/work"
    )
    client.create_tab.assert_not_awaited()


@pytest.mark.asyncio
async def test_spawn_pane_defaults_match_worker_spawner():
    client = _client()
    host = HerdrHost(client)

    await host.spawn(_command(terminal_line=None), layout="pane")

    client.split_pane.assert_awaited_once_with(
        direction="right", command=None, split_ratio=0.5, cwd="/work"
    )


@pytest.mark.asyncio
async def test_alive_uses_pane_exists_or_none_when_missing():
    client = _client()
    host = HerdrHost(client)
    handle = WorkerHandle(id="pane-1", aux="tab-1")

    assert await host.alive(handle) is True
    client.pane_exists.assert_awaited_once_with("pane-1")

    client.pane_exists = AsyncMock(return_value=False)
    assert await host.alive(handle) is False

    del client.pane_exists
    assert await HerdrHost(client).alive(handle) is None


@pytest.mark.asyncio
async def test_tail_interrupt_and_notify_delegate_to_client():
    client = _client()
    host = HerdrHost(client)
    handle = WorkerHandle(id="pane-1")

    assert await host.tail(handle, lines=50) == "out"
    client.read_pane.assert_awaited_once_with("pane-1", lines=50)

    await host.interrupt(handle)
    client.send_interrupt.assert_awaited_once_with("pane-1")

    await host.notify("oi", title="MeisterRouter")
    client.show_notification.assert_awaited_once_with("oi", title="MeisterRouter")

    client.show_notification.reset_mock()
    await host.notify("sem titulo")
    client.show_notification.assert_awaited_once_with("sem titulo")


@pytest.mark.asyncio
async def test_close_prefers_tab_when_aux_present():
    client = _client()
    host = HerdrHost(client)

    await host.close(WorkerHandle(id="pane-1", aux="tab-1"))

    client.close_tab.assert_awaited_once_with("tab-1")
    client.close_pane.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_falls_back_to_pane_without_aux():
    client = _client()
    host = HerdrHost(client)

    await host.close(WorkerHandle(id="pane-2"))

    client.close_pane.assert_awaited_once_with("pane-2")
    client.close_tab.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_swallows_failures():
    client = _client()
    client.close_tab = AsyncMock(side_effect=RuntimeError("boom"))
    host = HerdrHost(client)

    await host.close(WorkerHandle(id="pane-1", aux="tab-1"))

    client.close_tab.assert_awaited_once_with("tab-1")


@pytest.mark.asyncio
async def test_start_connects_when_disconnected_and_subscribes_events():
    client = _client(is_connected=False)
    host = HerdrHost(client)

    async def on_event(event):
        return None

    await host.start(on_event)

    client.connect.assert_awaited_once()
    client.subscribe_events.assert_awaited_once_with(on_event)


@pytest.mark.asyncio
async def test_start_without_callback_does_not_subscribe():
    client = _client(is_connected=True)
    host = HerdrHost(client)

    await host.start()

    client.connect.assert_not_awaited()
    client.subscribe_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_info_and_current_context():
    client = _client()
    host = HerdrHost(client)

    info = await host.process_info(WorkerHandle(id="pane-1"))
    assert info == {"foreground_process_group_id": 42}
    client._call.assert_awaited_once_with("pane.process_info", {"pane_id": "pane-1"})

    assert await host.current_context() == {"pane_id": "p", "workspace_id": "w"}
    client.get_current_pane.assert_awaited_once()
