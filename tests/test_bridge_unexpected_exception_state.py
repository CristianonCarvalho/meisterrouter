"""Exceção inesperada numa subtask: o lote registra a causa e a subtask termina FAILED."""

from unittest.mock import AsyncMock, patch

import pytest

from meister.config import MeisterConfig
from meister.engine.bridge import HerdrEventBridge
from meister.state import RunState, StateManager, SubtaskState, compute_subtask_id


@pytest.mark.asyncio
async def test_unexpected_exception_marks_subtask_failed_in_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subtask = {
        "id": "boom",
        "description": "Subtask that raises an unexpected error",
        "target_files": ["app.py"],
        "cwd": str(tmp_path),
    }
    sm = StateManager(str(tmp_path / "state.db"))
    run_id = str(sm.create_or_get_run(task_prompt="plan", cwd=str(tmp_path))["run_id"])
    sm.transition_run(run_id, to_state=RunState.RUNNING)
    sm.add_subtasks(run_id, [subtask])

    bridge = HerdrEventBridge(config=MeisterConfig(), client=AsyncMock(), state_manager=sm)
    bridge.current_run_id = run_id

    async def raising_core(task, initial_tier, run_id_arg):
        subtask_id = compute_subtask_id(run_id, subtask["id"], subtask["description"])
        sm.transition_subtask(subtask_id, to_state=SubtaskState.RUNNING, assigned_tier="A")
        raise RuntimeError("boom")

    with patch.object(bridge, "_execute_subtask_core", side_effect=raising_core), \
            patch("meister.engine.bridge.log_event"), \
            patch("meister.engine.bridge.logger") as mock_logger:
        result = await bridge.execute_parallel_batch([subtask])

    assert result is False
    row = sm.get_subtask(compute_subtask_id(run_id, subtask["id"], subtask["description"]))
    assert row is not None
    assert row["status"] == SubtaskState.FAILED.value
    assert "boom" in (row.get("error_message") or "")
    statuses = [item["status"] for item in sm.get_subtasks(run_id)]
    assert SubtaskState.RUNNING.value not in statuses
    assert any(call.kwargs.get("exc_info") is not None for call in mock_logger.error.call_args_list)


@pytest.mark.asyncio
async def test_unexpected_exception_without_active_run_only_logs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge = HerdrEventBridge(config=MeisterConfig(), client=AsyncMock())
    bridge.current_run_id = None

    with patch.object(bridge, "execute_subtask", side_effect=RuntimeError("boom")), \
            patch.object(bridge, "get_state_manager") as get_sm:
        result = await bridge.execute_parallel_batch([{"id": "t1", "description": "task"}])

    assert result is False
    get_sm.assert_not_called()
