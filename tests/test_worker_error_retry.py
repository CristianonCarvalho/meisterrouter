"""Erro do worker (status error): retenta na mesma via e depois cai para a próxima.

Os testes usam um host de mentira em memória; nenhum processo real é iniciado.
"""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import patch

from tests.platform_marks import posix_only
from meister.config import load_config
from meister.herdr.bridge import HerdrEventBridge
from meister.hosts.base import CAP_VISIBLE
from meister.state import StateManager, SubtaskState, compute_subtask_id
from meister.worker import write_atomic_json

ERROR = {"status": "error", "error": "harness crashed"}
DONE = {"status": "done", "modified_files": []}


def _config(tmp_path, worker_error_attempts):
    cfg_file = tmp_path / "worker_error_config.yaml"
    cfg_file.write_text(
        "router:\n  mode: first\n"
        "retry:\n  pane_lost_attempts: 0\n  pane_lost_backoff_seconds: 0\n"
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


def _subtask(repo):
    return {
        "id": "werr",
        "description": "Worker error retry",
        "target_files": ["app.py"],
        "cwd": str(repo),
    }


class _FakeHost:
    """Host de mentira: só responde por `tail` e `alive`."""

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


def _run(tmp_path, monkeypatch, worker_error_attempts, results):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEISTER_RETRY_BACKOFF", "0")
    repo = _repo(tmp_path)
    subtask = _subtask(repo)
    sm = StateManager(str(tmp_path / "state.db"))
    run_id = sm.create_or_get_run(task_prompt="plan", cwd=str(repo))["run_id"]
    sm.add_subtasks(run_id, [subtask])
    bridge = HerdrEventBridge(
        config=_config(tmp_path, worker_error_attempts),
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
    with patch("meister.herdr.bridge.log_event", side_effect=lambda **kw: events.append(kw)):
        ok = asyncio.run(
            asyncio.wait_for(bridge.execute_subtask(subtask, run_id=run_id), timeout=20)
        )
    row = sm.get_subtask(compute_subtask_id(run_id, subtask["id"], subtask["description"]))
    return ok, spawns, events, row


def _of(events, event_type):
    return [e for e in events if e.get("event_type") == event_type]


@posix_only
def test_error_then_success_completes_on_same_tier(tmp_path, monkeypatch):
    ok, spawns, events, row = _run(tmp_path, monkeypatch, 1, [ERROR, DONE])
    assert ok is True
    assert spawns == ["A", "A"]
    retries = _of(events, "worker_retry")
    assert len(retries) == 1
    assert retries[0]["reason"] == "worker_error"
    assert retries[0]["tier"] == "A"
    assert "harness crashed" in retries[0]["error"]
    assert _of(events, "subtask_rejected") == []
    assert row["status"] == SubtaskState.COMPLETED.value


@posix_only
def test_repeated_errors_fall_back_to_next_tier(tmp_path, monkeypatch):
    ok, spawns, events, row = _run(tmp_path, monkeypatch, 1, [ERROR, ERROR, DONE])
    assert ok is True
    assert spawns == ["A", "A", "B"]
    assert _of(events, "subtask_rejected") == []
    assert row["status"] == SubtaskState.COMPLETED.value


@posix_only
def test_all_tiers_failing_ends_failed(tmp_path, monkeypatch):
    ok, spawns, events, row = _run(tmp_path, monkeypatch, 1, [ERROR] * 10)
    assert ok is False
    assert spawns == ["A", "A", "B", "B"]
    rejected = _of(events, "subtask_rejected")
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "worker_error"
    assert rejected[0]["tier"] == "B"
    assert row["status"] == SubtaskState.FAILED.value


@posix_only
def test_zero_attempts_keeps_old_behavior(tmp_path, monkeypatch):
    ok, spawns, events, row = _run(tmp_path, monkeypatch, 0, [ERROR, DONE])
    assert ok is False
    assert spawns == ["A"]
    assert _of(events, "worker_retry") == []
    assert len(_of(events, "subtask_rejected")) == 1
    assert row["status"] == SubtaskState.FAILED.value
