from __future__ import annotations

import dataclasses

import pytest

from meister.hosts import (
    CAP_PUSH_EVENTS,
    CAP_VISIBLE,
    HostError,
    WorkerCommand,
    WorkerHandle,
    WorkerHost,
)


def test_worker_handle_is_immutable_and_comparable():
    a = WorkerHandle(id="w1")
    b = WorkerHandle(id="w1")
    assert a == b
    assert a != WorkerHandle(id="w2")
    assert a != WorkerHandle(id="w1", aux="x")
    assert a.aux is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.id = "other"  # type: ignore[misc]
    assert hash(a) == hash(b)


def test_worker_command_terminal_line_is_optional():
    cmd = WorkerCommand(argv=["echo", "hi"], env={"A": "1"}, cwd="/tmp", label="w")
    assert cmd.terminal_line is None
    assert cmd.argv == ["echo", "hi"]
    cmd2 = WorkerCommand(argv=[], env={}, cwd="/tmp", label="w", terminal_line="ls")
    assert cmd2.terminal_line == "ls"


def test_worker_command_log_and_exit_files_default_to_none():
    cmd = WorkerCommand(argv=["echo"], env={}, cwd="/tmp", label="w")
    assert cmd.log_file is None
    assert cmd.exit_file is None
    cmd2 = WorkerCommand(
        argv=["echo"], env={}, cwd="/tmp", label="w", log_file="/l.log", exit_file="/e.exit"
    )
    assert cmd2.log_file == "/l.log"
    assert cmd2.exit_file == "/e.exit"


def test_capability_constants_and_host_error():
    assert CAP_VISIBLE == "visible"
    assert CAP_PUSH_EVENTS == "push_events"
    assert issubclass(HostError, RuntimeError)


class _MinimalHost:
    name = "minimal"
    capabilities = frozenset({CAP_VISIBLE})

    async def start(self, on_event=None) -> None:
        return None

    async def spawn(self, command, *, layout="tab"):
        return WorkerHandle(id="w")

    async def alive(self, handle):
        return None

    async def tail(self, handle, lines=200) -> str:
        return ""

    async def interrupt(self, handle) -> None:
        return None

    async def close(self, handle) -> None:
        return None

    async def notify(self, message, title=None) -> None:
        return None

    async def process_info(self, handle):
        return None

    async def current_context(self):
        return None


class _Incomplete:
    name = "incomplete"
    capabilities = frozenset()

    async def start(self, on_event=None) -> None:
        return None


def test_minimal_class_satisfies_worker_host_protocol():
    assert isinstance(_MinimalHost(), WorkerHost)


def test_incomplete_class_does_not_satisfy_worker_host_protocol():
    assert not isinstance(_Incomplete(), WorkerHost)
