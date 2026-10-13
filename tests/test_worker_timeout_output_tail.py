from __future__ import annotations

import os
from typing import Optional
from unittest.mock import MagicMock

import pytest

from meister.engine.bridge import HerdrEventBridge
from meister.hosts import WorkerHandle


class FakeHost:
    def __init__(self, output: str = "", error: Optional[BaseException] = None) -> None:
        self.output = output
        self.error = error
        self.calls: list[tuple[WorkerHandle, int]] = []

    async def tail(self, handle: WorkerHandle, lines: int = 200) -> str:
        self.calls.append((handle, lines))
        if self.error is not None:
            raise self.error
        return self.output


def _bridge(host: FakeHost) -> HerdrEventBridge:
    return HerdrEventBridge(
        config=MagicMock(),
        spawner=MagicMock(),
        host=host,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_timeout_tail_returns_last_lines_and_writes_missing_log(tmp_path) -> None:
    lines = [f"linha {i}" for i in range(1, 61)]
    host = FakeHost(output="\n".join(lines[-40:]) + "\n")
    bridge = _bridge(host)
    log_file = os.path.join(str(tmp_path), "logs", "workers", "run_task_1.log")

    tail = await bridge._read_timeout_output_tail(WorkerHandle(id="p1", aux="tab1"), log_file)

    assert host.calls == [(WorkerHandle(id="p1", aux="tab1"), 40)]
    assert tail is not None
    assert "linha 21" in tail and "linha 60" in tail and "linha 20" not in tail
    assert os.path.isfile(log_file)
    with open(log_file, encoding="utf-8") as fh:
        assert fh.read() == tail


@pytest.mark.asyncio
async def test_timeout_tail_does_not_overwrite_existing_log(tmp_path) -> None:
    log_file = os.path.join(str(tmp_path), "run_task_1.log")
    with open(log_file, "w", encoding="utf-8") as fh:
        fh.write("original\n")
    bridge = _bridge(FakeHost(output="novo trecho\n"))

    tail = await bridge._read_timeout_output_tail(WorkerHandle(id="p1"), log_file)

    assert tail == "novo trecho\n"
    with open(log_file, encoding="utf-8") as fh:
        assert fh.read() == "original\n"


@pytest.mark.asyncio
async def test_timeout_tail_swallows_read_error(tmp_path) -> None:
    log_file = os.path.join(str(tmp_path), "run_task_1.log")
    bridge = _bridge(FakeHost(error=RuntimeError("socket caiu")))

    tail = await bridge._read_timeout_output_tail(WorkerHandle(id="p1"), log_file)

    assert tail is None
    assert not os.path.exists(log_file)
