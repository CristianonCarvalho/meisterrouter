"""Metadados dos eventos: via (harness, model, effort) no worker_spawn e `detail` nas rejeições.

Os testes usam um host de mentira em memória; nenhum processo real é iniciado.
"""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from tests.platform_marks import posix_only
from meister.config import load_config
from meister.herdr.bridge import HerdrEventBridge, _event_detail
from meister.hosts.base import CAP_VISIBLE
from meister.i18n import t
from meister.state import StateManager
from meister.worker import write_atomic_json
from meister.worktree import CodedMessage

ERROR = {"status": "error", "error": "harness crashed"}
DONE = {"status": "done", "modified_files": []}


def _config(tmp_path, repair_attempts=1):
    cfg_file = tmp_path / "events_metadata_config.yaml"
    cfg_file.write_text(
        "router:\n  mode: first\n"
        "retry:\n  pane_lost_attempts: 0\n  pane_lost_backoff_seconds: 0\n"
        "  worker_error_attempts: 0\n"
        f"gate:\n  repair_attempts: {repair_attempts}\n"
        "workers:\n  idle_timeout_seconds: 600\n  max_runtime_seconds: 0\n"
        "  tier_order:\n"
        "    - name: A\n      harness: codex\n      model: gpt-luna\n      effort: medium\n"
        "    - name: B\n      harness: claude\n      model: sonnet\n"
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


class _FakePipeline:
    """Pipeline de integração de mentira: sempre rejeita por escopo."""

    def __init__(self, repo, message):
        self.integration_info = SimpleNamespace(branch_name="main")
        self.last_integrated_sha = None
        self._message = message
        wt = SimpleNamespace(worktree_path=str(repo), base_commit=None, task_id="wt")
        self.wt_mgr = SimpleNamespace(
            create_worktree=lambda **kwargs: wt,
            cleanup_worktree=lambda *args, **kwargs: None,
        )

    def prepare_subtask(self, **kwargs):
        return SimpleNamespace(tolerated_touched=[])

    def merge_prepared(self, prepared):
        return False, self._message


def _run(tmp_path, monkeypatch, results, pipeline_message=None, repair_attempts=1):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEISTER_RETRY_BACKOFF", "0")
    repo = _repo(tmp_path)
    subtask = {
        "id": "meta",
        "description": "Events metadata",
        "target_files": ["app.py"],
        "cwd": str(repo),
    }
    sm = StateManager(str(tmp_path / "state.db"))
    run_id = sm.create_or_get_run(task_prompt="plan", cwd=str(repo))["run_id"]
    sm.add_subtasks(run_id, [subtask])
    bridge = HerdrEventBridge(
        config=_config(tmp_path, repair_attempts),
        host=_FakeHost(),
        state_manager=sm,
    )
    if pipeline_message is not None:
        bridge._integration_pipeline = _FakePipeline(repo, pipeline_message)
    queue = list(results)
    spawns: list[str] = []

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
    return ok, events


def _of(events, event_type):
    return [e for e in events if e.get("event_type") == event_type]


@posix_only
def test_worker_spawn_carries_lane_harness_model_effort(tmp_path, monkeypatch):
    ok, events = _run(tmp_path, monkeypatch, [DONE])
    assert ok is True
    spawn = _of(events, "worker_spawn")[0]
    assert spawn["tier"] == "A"
    assert spawn["harness"] == "codex"
    assert spawn["model"] == "gpt-luna"
    assert spawn["effort"] == "medium"


@posix_only
def test_worker_error_rejection_has_error_detail(tmp_path, monkeypatch):
    ok, events = _run(tmp_path, monkeypatch, [ERROR])
    assert ok is False
    rejected = _of(events, "subtask_rejected")[0]
    assert rejected["reason"] == "worker_error"
    assert rejected["detail"] == "harness crashed"


@posix_only
def test_scope_rejection_lists_out_of_scope_files_in_detail(tmp_path, monkeypatch):
    message = CodedMessage(
        t("engine.worktree.scope_violation", files=["meister/cli.py", "docs/x.md"]),
        code="scope",
    )
    ok, events = _run(tmp_path, monkeypatch, [DONE, DONE], pipeline_message=message)
    assert ok is False
    repair = _of(events, "gate_repair")[0]
    assert repair["detail"] == "meister/cli.py, docs/x.md"
    rejected = _of(events, "subtask_rejected")[0]
    assert rejected["reason"] == "scope"
    assert rejected["detail"] == "meister/cli.py, docs/x.md"


def test_detail_is_cut_at_200_chars():
    files = [f"pkg/module_{i:03d}.py" for i in range(40)]
    message = t("engine.worktree.scope_violation", files=files)
    detail = _event_detail("scope", message)
    assert detail is not None
    assert len(detail) == 200
    assert detail.startswith("pkg/module_000.py, pkg/module_001.py")
    assert len(_event_detail("worker_error", "x" * 500) or "") == 200


def test_gate_detail_is_first_failure_line():
    output = (
        "running checks\n"
        "app.py:3:1: F401 [*] `os` imported but unused\n"
        "FAILED tests/test_app.py::test_x - AssertionError\n"
    )
    assert _event_detail("gate", output) == "app.py:3:1: F401 [*] `os` imported but unused"
    pytest_output = "collected 3 items\nFAILED tests/test_app.py::test_x - AssertionError\n"
    assert _event_detail("gate", pytest_output) == "FAILED tests/test_app.py::test_x - AssertionError"
    mypy_output = "meister/a.py:10: error: Incompatible types\nFound 1 error\n"
    assert _event_detail("gate", mypy_output) == "meister/a.py:10: error: Incompatible types"


def test_detail_is_none_for_empty_text():
    assert _event_detail("worker_error", "") is None
    assert _event_detail("gate", "   \n") is None
