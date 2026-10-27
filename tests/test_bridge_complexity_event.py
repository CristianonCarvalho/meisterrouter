"""Tests for complexity_check event emission on subtask integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meister.config import MeisterConfig, WorkerTier
from meister.engine.bridge import HerdrEventBridge
from meister.logger import add_event_observer, remove_event_observer
from meister.state import RunState, StateManager, SubtaskState
from meister.worker import write_atomic_json


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


async def _setup_and_execute(
    tmp_path: Path,
    integrated_sha: str | None = "deadbeef1234",
    result_data: dict | None = None,
):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    integration_repo = tmp_path / "integration_wt"
    integration_repo.mkdir(parents=True, exist_ok=True)

    sm = StateManager(str(tmp_path / "state.db"))
    run_id = str(sm.create_or_get_run(task_prompt="test plan", cwd=str(repo_dir))["run_id"])
    sm.transition_run(run_id, to_state=RunState.RUNNING)

    subtask = {
        "id": "task_complexity",
        "description": "Test complexity check",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }
    sm.add_subtasks(run_id, [subtask])

    subtask_wt = SimpleNamespace(
        task_id=f"{run_id}_task_complexity_abc",
        worktree_path=str(tmp_path / "subtask_wt"),
        base_commit="initsha",
    )

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        write_atomic_json(
            task_context["result_file"],
            result_data or {"status": "done", "modified_files": ["app.py"]},
        )
        return ("t1", "p1", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

    wt_mgr = MagicMock()
    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)
    wt_mgr.cleanup_worktree = MagicMock()

    integration_pipeline = MagicMock()
    integration_pipeline.integration_info = SimpleNamespace(
        branch_name="integration",
        worktree_path=str(integration_repo),
    )
    integration_pipeline.wt_mgr = wt_mgr
    integration_pipeline.prepare_subtask = MagicMock(return_value=SimpleNamespace(subtask_wt=subtask_wt))
    integration_pipeline.merge_prepared = MagicMock(return_value=(True, "merged successfully"))
    integration_pipeline.last_integrated_sha = integrated_sha

    mock_client = AsyncMock()
    mock_client.close_tab = AsyncMock()
    mock_client.close_pane = AsyncMock()

    bridge = HerdrEventBridge(
        config=MeisterConfig(),
        client=mock_client,
        spawner=mock_spawner,
        state_manager=sm,
    )
    bridge._integration_pipeline = integration_pipeline
    bridge.current_run_id = run_id

    success = await bridge.execute_subtask(subtask, run_id=run_id)
    return success, bridge, integration_repo, run_id, sm


@pytest.mark.asyncio
async def test_complexity_event_recorded_with_correct_fields(tmp_path: Path, event_collector):
    mock_report_data = {
        "functions_changed": 3,
        "over_threshold": 1,
        "worsened": 2,
        "max_complexity": 15,
        "worst": "app.py::complex_calc",
    }
    with patch("meister.engine.bridge.changed_function_report", return_value=mock_report_data) as mock_report:
        success, bridge, int_repo, run_id, sm = await _setup_and_execute(tmp_path, integrated_sha="deadbeef1234")

    assert success is True
    mock_report.assert_called_once_with(str(int_repo), "deadbeef1234^1", "deadbeef1234")

    complexity_events = [e for e in event_collector if e.get("event_type") == "complexity_check"]
    assert len(complexity_events) == 1
    ev = complexity_events[0]
    assert ev["run_id"] == run_id
    assert ev["task_id"] == "task_complexity"
    assert ev["functions_changed"] == 3
    assert ev["over_threshold"] == 1
    assert ev["worsened"] == 2
    assert ev["max_complexity"] == 15
    assert ev["worst"] == "app.py::complex_calc"

    completed_events = [e for e in event_collector if e.get("event_type") == "subtask_completed"]
    assert len(completed_events) == 1
    assert completed_events[0]["task_id"] == "task_complexity"

    # Verify event order: complexity_check precedes subtask_completed
    event_types = [e.get("event_type") for e in event_collector]
    assert event_types.index("complexity_check") < event_types.index("subtask_completed")


@pytest.mark.asyncio
async def test_complexity_event_not_recorded_when_report_returns_none(tmp_path: Path, event_collector):
    with patch("meister.engine.bridge.changed_function_report", return_value=None) as mock_report:
        success, bridge, int_repo, run_id, sm = await _setup_and_execute(tmp_path, integrated_sha="deadbeef1234")

    assert success is True
    mock_report.assert_called_once_with(str(int_repo), "deadbeef1234^1", "deadbeef1234")

    complexity_events = [e for e in event_collector if e.get("event_type") == "complexity_check"]
    assert len(complexity_events) == 0

    completed_events = [e for e in event_collector if e.get("event_type") == "subtask_completed"]
    assert len(completed_events) == 1
    assert completed_events[0]["task_id"] == "task_complexity"


@pytest.mark.asyncio
async def test_subtask_still_completes_when_complexity_report_raises_exception(tmp_path: Path, event_collector):
    with patch(
        "meister.engine.bridge.changed_function_report",
        side_effect=RuntimeError("simulated git or diff failure"),
    ) as mock_report, patch("meister.engine.bridge.logger.debug") as mock_debug:
        success, bridge, int_repo, run_id, sm = await _setup_and_execute(tmp_path, integrated_sha="deadbeef1234")

    assert success is True
    mock_report.assert_called_once_with(str(int_repo), "deadbeef1234^1", "deadbeef1234")
    assert mock_debug.called

    complexity_events = [e for e in event_collector if e.get("event_type") == "complexity_check"]
    assert len(complexity_events) == 0

    completed_events = [e for e in event_collector if e.get("event_type") == "subtask_completed"]
    assert len(completed_events) == 1
    assert completed_events[0]["task_id"] == "task_complexity"

    # Verify SQLite subtask state is COMPLETED
    subtasks = sm.get_subtasks(run_id)
    assert len(subtasks) == 1
    assert subtasks[0]["status"] == SubtaskState.COMPLETED.value


@pytest.mark.asyncio
async def test_complexity_event_not_called_when_no_integrated_sha(tmp_path: Path, event_collector):
    with patch("meister.engine.bridge.changed_function_report") as mock_report:
        success, bridge, int_repo, run_id, sm = await _setup_and_execute(tmp_path, integrated_sha=None)

    assert success is True
    mock_report.assert_not_called()

    complexity_events = [e for e in event_collector if e.get("event_type") == "complexity_check"]
    assert len(complexity_events) == 0

    completed_events = [e for e in event_collector if e.get("event_type") == "subtask_completed"]
    assert len(completed_events) == 1
