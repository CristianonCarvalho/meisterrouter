"""Gate failures on tests outside target_files end the subtask as scope_conflict (no repair)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from meister.config import MeisterConfig, WorkerTier
from meister.engine.bridge import HerdrEventBridge
from meister.logger import add_event_observer, remove_event_observer
from meister.worker import write_atomic_json
from meister.worktree import CodedMessage, WorktreeManager


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tester@test.local"], cwd=repo, check=True)
    (repo / "base.txt").write_text("initial\n")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def event_collector():
    events: List[Dict[str, Any]] = []

    def handler(ev: Dict[str, Any]):
        events.append(dict(ev))

    add_event_observer(handler)
    try:
        yield events
    finally:
        remove_event_observer(handler)


def _gate_output(test_id: str) -> str:
    return (
        "=========================== short test summary info ============================\n"
        f"FAILED {test_id} - AssertionError\n"
        "======================== 1 failed in 0.1s ==========================\n"
    )


async def _run(
    repo: Path,
    event_collector: List[Dict[str, Any]],
    *,
    target_files: List[str],
    gate_results: List[Any],
    task_id: str,
):
    wt_dir = repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(repo), worktrees_dir=str(wt_dir))
    subtask_wt = wt_mgr.create_worktree(task_id=f"run_{task_id}_abc", base_ref="main")

    call_count = 0

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        nonlocal call_count
        call_count += 1
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return ("t1", f"p{call_count}", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

    integration_pipeline = MagicMock()
    integration_pipeline.integration_info = SimpleNamespace(branch_name="main")
    integration_pipeline.wt_mgr = wt_mgr
    integration_pipeline.prepare_subtask = MagicMock(return_value=SimpleNamespace(subtask_wt=subtask_wt))
    integration_pipeline.merge_prepared = MagicMock(side_effect=gate_results)

    cleaned: List[str] = []
    wt_mgr.cleanup_worktree = MagicMock(side_effect=lambda tid, **kw: cleaned.append(tid))

    cfg = MeisterConfig()
    client = AsyncMock()
    bridge = HerdrEventBridge(config=cfg, client=client, spawner=mock_spawner)
    bridge._integration_pipeline = integration_pipeline
    bridge.current_run_id = "run_scope"
    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

    subtask = {"id": task_id, "description": "Scope check", "target_files": target_files}
    success = await bridge.execute_subtask(subtask)
    return success, bridge, call_count, cleaned


@pytest.mark.asyncio
async def test_gate_failure_outside_target_files_is_scope_conflict(temp_git_repo: Path, event_collector):
    gate = (False, CodedMessage(_gate_output("tests/test_x.py::test_a"), code="gate"))
    success, bridge, call_count, cleaned = await _run(
        temp_git_repo,
        event_collector,
        target_files=["app.py"],
        gate_results=[gate],
        task_id="task_conflict",
    )

    assert success is False
    assert call_count == 1, "scope_conflict must not spend a repair spawn"
    assert not [e for e in event_collector if e.get("event_type") == "gate_repair"]

    rejects = [e for e in event_collector if e.get("event_type") == "subtask_rejected"]
    assert len(rejects) == 1
    assert rejects[0]["reason"] == "scope_conflict"
    assert "tests/test_x.py::test_a" in bridge.last_failure_reason
    assert "tests/test_x.py" in bridge.last_failure_reason
    assert "refs/meister/archive/" in bridge.last_failure_reason
    assert cleaned, "Worktree must be archived on scope_conflict"


@pytest.mark.asyncio
async def test_gate_failure_inside_target_files_still_repairs(temp_git_repo: Path, event_collector):
    gate_fail = (False, CodedMessage(_gate_output("tests/test_x.py::test_a"), code="gate"))
    success, _bridge, call_count, _cleaned = await _run(
        temp_git_repo,
        event_collector,
        target_files=["app.py", "tests/test_x.py"],
        gate_results=[gate_fail, (True, "ok")],
        task_id="task_inscope",
    )

    assert success is True
    assert call_count == 2
    repairs = [e for e in event_collector if e.get("event_type") == "gate_repair"]
    assert len(repairs) == 1
    assert not [e for e in event_collector if e.get("reason") == "scope_conflict"]


@pytest.mark.asyncio
async def test_gate_failure_without_target_files_never_scope_conflict(temp_git_repo: Path, event_collector):
    gate_fail = (False, CodedMessage(_gate_output("tests/test_x.py::test_a"), code="gate"))
    _success, _bridge, call_count, _cleaned = await _run(
        temp_git_repo,
        event_collector,
        target_files=[],
        gate_results=[gate_fail, (True, "ok")],
        task_id="task_noscope",
    )

    assert call_count == 2
    assert not [e for e in event_collector if e.get("reason") == "scope_conflict"]
