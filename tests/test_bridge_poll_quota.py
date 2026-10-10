"""Cota detectada por polling do `tail` em hosts sem eventos empurrados (sem CAP_PUSH_EVENTS).

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
from meister.worker import write_atomic_json

QUOTA_TEXT = '{"type":"error","error":{"type":"rate_limit_error","message":"Rate limit reached"}}'
TEST_CODE_TEXT = "def test_quota():\n    assert 'HTTP 429: Insufficient quota balance' in out"


def _config(tmp_path):
    cfg_file = tmp_path / "poll_quota_config.yaml"
    cfg_file.write_text(
        "router:\n  mode: first\n"
        "retry:\n  pane_lost_attempts: 0\n  pane_lost_backoff_seconds: 0\n"
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
        "id": "poll",
        "description": "Poll quota",
        "target_files": ["app.py"],
        "cwd": str(repo),
    }


class _NoEventsHost:
    """Host de mentira sem CAP_PUSH_EVENTS: só responde por `tail` e `alive`."""

    name = "noevents"
    capabilities = frozenset({CAP_VISIBLE})

    def __init__(self, pane_text: str):
        self.pane_text = pane_text
        self.interrupted: list[str] = []

    async def tail(self, handle, lines=None):
        return self.pane_text

    async def alive(self, handle):
        return True

    async def interrupt(self, handle):
        self.interrupted.append(handle.id)

    async def close(self, handle):
        return None


def _bridge(config, host, finish_first=False):
    bridge = HerdrEventBridge(config=config, host=host)
    spawns: list[str] = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawns.append(tier_name)
        if finish_first or len(spawns) > 1:
            write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return f"w1:p{len(spawns)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    return bridge, spawns


def _run(bridge, repo, timeout=20):
    with patch("meister.herdr.bridge.log_event"):
        return asyncio.run(asyncio.wait_for(bridge.execute_subtask(_subtask(repo)), timeout=timeout))


@posix_only
def test_no_event_host_quota_in_tail_escalates_to_next_tier(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    repo = _repo(tmp_path)
    host = _NoEventsHost(QUOTA_TEXT)
    bridge, spawns = _bridge(_config(tmp_path), host)
    try:
        _run(bridge, repo)
        assert spawns[:2] == ["A", "B"]
        assert host.interrupted
    finally:
        for info in bridge.active_workers.values():
            info["status"] = "done"


@posix_only
def test_no_event_host_test_code_text_does_not_escalate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    repo = _repo(tmp_path)
    host = _NoEventsHost(TEST_CODE_TEXT)
    bridge, spawns = _bridge(_config(tmp_path), host, finish_first=True)
    _run(bridge, repo)
    assert spawns == ["A"]
    assert host.interrupted == []

