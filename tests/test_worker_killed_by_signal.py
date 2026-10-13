"""Harness morto por sinal (ex.: -9 SIGKILL) é erro de infraestrutura, não erro do worker.

Worker: exit code negativo levanta HarnessKilledError e grava error_kind="signal".
Bridge: resultados com error_kind="signal" usam o orçamento de pane perdido, sem escalar de via.
"""

from __future__ import annotations

import asyncio
import stat
import subprocess
import sys
from unittest.mock import patch

import pytest

from tests.platform_marks import posix_only
from meister.config import load_config
from meister.engine.bridge import HerdrEventBridge
from meister.hosts.base import CAP_VISIBLE
from meister.state import StateManager, SubtaskState, compute_subtask_id
from meister.worker import (
    HarnessKilledError,
    HarnessWorker,
    _early_error_result,
    execute_task_file,
    read_atomic_json,
    write_atomic_json,
)

SIGNAL = {"status": "error", "error": "killed", "error_kind": "signal"}
DONE = {"status": "done", "modified_files": []}


def _fake_cli(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@posix_only
def test_worker_sigkill_raises_harness_killed_and_marks_signal(tmp_path):
    cli = _fake_cli(
        tmp_path,
        "killed_cli",
        "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)",
    )
    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    write_atomic_json(
        str(task_file),
        {
            "task_id": "killed-task",
            "model": "tier_1c",
            "task": "Killed task",
            "cwd": str(tmp_path),
            "result_file": str(result_file),
        },
    )

    with patch("meister.worker.find_cli_binary", return_value=cli):
        with pytest.raises(HarnessKilledError) as info:
            execute_task_file(str(task_file))

    assert info.value.signal_number == 9
    assert "SIGKILL" in str(info.value)
    saved = read_atomic_json(str(result_file))
    assert saved["status"] == "error"
    assert saved["error_kind"] == "signal"


@posix_only
def test_worker_plain_failure_stays_runtime_error_without_signal_kind(tmp_path):
    cli = _fake_cli(tmp_path, "failing_cli", "import sys\nsys.exit(1)")
    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    write_atomic_json(
        str(task_file),
        {
            "task_id": "failing-task",
            "model": "tier_1c",
            "task": "Failing task",
            "cwd": str(tmp_path),
            "result_file": str(result_file),
        },
    )

    with patch("meister.worker.find_cli_binary", return_value=cli):
        with pytest.raises(RuntimeError) as info:
            execute_task_file(str(task_file))

    assert not isinstance(info.value, HarnessKilledError)
    saved = read_atomic_json(str(result_file))
    assert "error_kind" not in saved


def test_run_task_negative_exit_code_raises_harness_killed(tmp_path):
    worker = HarnessWorker(model=None, cwd=str(tmp_path), config=None)
    worker.cli_binary = "/bin/echo"

    class _Proc:
        pid = 0
        stdout = []

        def wait(self, timeout=None):
            return -9

    with patch("meister.worker.subprocess.Popen", return_value=_Proc()), patch(
        "meister.worker.kill_process_tree"
    ):
        with pytest.raises(HarnessKilledError) as info:
            worker.run_task("task")
    assert info.value.signal_number == 9


def test_run_task_positive_exit_code_stays_runtime_error(tmp_path):
    worker = HarnessWorker(model=None, cwd=str(tmp_path), config=None)
    worker.cli_binary = "/bin/echo"

    class _Proc:
        pid = 0
        stdout = []

        def wait(self, timeout=None):
            return 1

    with patch("meister.worker.subprocess.Popen", return_value=_Proc()), patch(
        "meister.worker.kill_process_tree"
    ):
        with pytest.raises(RuntimeError) as info:
            worker.run_task("task")
    assert not isinstance(info.value, HarnessKilledError)


def test_early_error_result_carries_error_kind_only_when_given():
    assert "error_kind" not in _early_error_result("t", "boom", model="tier_1")
    res = _early_error_result("t", "boom", model="tier_1", error_kind="signal")
    assert res["error_kind"] == "signal"


# ---------------------------------------------------------------- bridge


def _config(tmp_path, pane_lost_attempts, worker_error_attempts=1):
    cfg_file = tmp_path / "killed_config.yaml"
    cfg_file.write_text(
        "router:\n  mode: first\n"
        f"retry:\n  pane_lost_attempts: {pane_lost_attempts}\n  pane_lost_backoff_seconds: 0\n"
        f"  worker_error_attempts: {worker_error_attempts}\n"
        "workers:\n  idle_timeout_seconds: 600\n  max_runtime_seconds: 0\n"
        "  tier_order:\n"
        "    - name: A\n      harness: codex\n      model: A\n"
        "    - name: B\n      harness: codex\n      model: B\n"
        "concurrency:\n  parallel_tasks: false\n  max_parallel_workers: 1\n"
        "  layout_strategy: tiled\n  isolation_mode: none\n"
    )
    return load_config(str(cfg_file))


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    (repo / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    return repo


class _FakeHost:
    name = "fake"
    capabilities = frozenset({CAP_VISIBLE})

    async def tail(self, handle, lines=None):
        return ""

    async def alive(self, handle):
        return True

    async def interrupt(self, handle):
        return None

    async def close(self, handle):
        return None


def _run(tmp_path, monkeypatch, pane_lost_attempts, results):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEISTER_RETRY_BACKOFF", "0")
    repo = _repo(tmp_path)
    subtask = {
        "id": "killed",
        "description": "Killed by signal",
        "target_files": ["app.py"],
        "cwd": str(repo),
    }
    sm = StateManager(str(tmp_path / "state.db"))
    run_id = sm.create_or_get_run(task_prompt="plan", cwd=str(repo))["run_id"]
    sm.add_subtasks(run_id, [subtask])
    bridge = HerdrEventBridge(
        config=_config(tmp_path, pane_lost_attempts),
        host=_FakeHost(),
        state_manager=sm,
    )
    spawns: list[str] = []
    queue = list(results)

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawns.append(tier_name)
        write_atomic_json(task_context["result_file"], queue.pop(0) if queue else DONE)
        return f"w1:p{len(spawns)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    events: list[dict] = []
    with patch("meister.engine.bridge.log_event", side_effect=lambda **kw: events.append(kw)):
        ok = asyncio.run(asyncio.wait_for(bridge.execute_subtask(subtask, run_id=run_id), timeout=20))
    row = sm.get_subtask(compute_subtask_id(run_id, subtask["id"], subtask["description"]))
    return ok, spawns, events, row


def _of(events, event_type):
    return [e for e in events if e.get("event_type") == event_type]


@posix_only
def test_two_signal_results_fail_without_tier_switch(tmp_path, monkeypatch):
    ok, spawns, events, row = _run(tmp_path, monkeypatch, 1, [SIGNAL, SIGNAL, DONE])
    assert ok is False
    assert spawns == ["A", "A"]
    assert row["status"] == SubtaskState.FAILED.value
    retries = _of(events, "worker_retry")
    assert len(retries) == 1
    assert retries[0]["reason"] == "signal_killed"
    assert _of(events, "subtask_rejected") == []
    assert not [e for e in events if e.get("reason") == "worker_error"]
    errors = _of(events, "worker_error")
    assert len(errors) == 1
    assert errors[0]["status"] == "infrastructure_error"


@posix_only
def test_signal_retry_on_same_tier_then_success(tmp_path, monkeypatch):
    ok, spawns, events, row = _run(tmp_path, monkeypatch, 1, [SIGNAL, DONE])
    assert ok is True
    assert spawns == ["A", "A"]
    assert row["status"] == SubtaskState.COMPLETED.value
    assert _of(events, "worker_error") == []
