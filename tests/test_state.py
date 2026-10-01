import pytest
from unittest.mock import AsyncMock, MagicMock

from meister.state import (
    StateManager,
    RunState,
    SubtaskState,
    InvalidStateTransitionError,
    compute_run_id,
    compute_subtask_id,
)
from meister.herdr.bridge import HerdrEventBridge


def test_compute_ids_deterministic():
    # Achado #28: Determinismo por hash SHA-256
    id1 = compute_run_id("Build backend authentication", "/workspace/proj")
    id2 = compute_run_id("Build backend authentication", "/workspace/proj")
    assert id1 == id2
    assert len(id1) == 16

    id_diff = compute_run_id("Build frontend UI", "/workspace/proj")
    assert id1 != id_diff

    sub1 = compute_subtask_id(id1, "step_1", "Create user model")
    sub2 = compute_subtask_id(id1, "step_1", "Create user model")
    assert sub1 == sub2
    assert len(sub1) == 16

    sub_diff = compute_subtask_id(id1, "step_2", "Create auth controller")
    assert sub1 != sub_diff


def test_state_manager_init_and_run_lifecycle(tmp_path):
    db_file = tmp_path / "test_meister.db"
    sm = StateManager(str(db_file))

    # Create run
    run = sm.create_or_get_run("Refactor CSS", cwd=str(tmp_path))
    assert run["state"] == RunState.PENDING.value
    run_id = run["run_id"]

    # Getting same run is idempotent
    run_again = sm.create_or_get_run("Refactor CSS", cwd=str(tmp_path))
    assert run_again["run_id"] == run_id

    # Valid transitions: PENDING -> RUNNING -> COMPLETED
    running = sm.transition_run(run_id, RunState.RUNNING)
    assert running["state"] == RunState.RUNNING.value

    completed = sm.transition_run(run_id, RunState.COMPLETED)
    assert completed["state"] == RunState.COMPLETED.value


def test_state_manager_invalid_transitions():
    sm = StateManager(":memory:")
    run = sm.create_or_get_run("Task A")
    run_id = run["run_id"]

    # PENDING -> COMPLETED is invalid (must go through RUNNING)
    with pytest.raises(InvalidStateTransitionError):
        sm.transition_run(run_id, RunState.COMPLETED)

    # Transition to RUNNING, then COMPLETED
    sm.transition_run(run_id, RunState.RUNNING)
    sm.transition_run(run_id, RunState.COMPLETED)

    # COMPLETED cannot transition to CANCELLED
    with pytest.raises(InvalidStateTransitionError):
        sm.transition_run(run_id, RunState.CANCELLED)


def test_subtask_lifecycle_and_idempotency(tmp_path):
    sm = StateManager(str(tmp_path / "test.db"))
    run = sm.create_or_get_run("Parent task", cwd=str(tmp_path))
    run_id = run["run_id"]

    subtasks = [
        {"id": "s1", "description": "Step 1", "target_files": ["a.py"]},
        {"id": "s2", "description": "Step 2", "target_files": ["b.py"]},
    ]

    added = sm.add_subtasks(run_id, subtasks)
    assert len(added) == 2
    s1_id = added[0]["subtask_id"]
    assert added[0]["status"] == SubtaskState.PENDING.value

    # Re-adding existing subtasks preserves their current state
    added_again = sm.add_subtasks(run_id, subtasks)
    assert len(added_again) == 2
    assert added_again[0]["subtask_id"] == s1_id

    # Transition s1: PENDING -> RUNNING -> RETRYING -> RUNNING -> COMPLETED
    sm.transition_subtask(s1_id, SubtaskState.RUNNING, assigned_tier="luna")
    s1 = sm.get_subtask(s1_id)
    assert s1["status"] == SubtaskState.RUNNING.value
    assert s1["attempts"] == 1

    sm.transition_subtask(s1_id, SubtaskState.RETRYING, error="Quota limit")
    s1 = sm.get_subtask(s1_id)
    assert s1["status"] == SubtaskState.RETRYING.value
    assert s1["error_message"] == "Quota limit"

    sm.transition_subtask(s1_id, SubtaskState.RUNNING, assigned_tier="gemini_flash")
    s1 = sm.get_subtask(s1_id)
    assert s1["status"] == SubtaskState.RUNNING.value
    assert s1["attempts"] == 2

    sm.transition_subtask(s1_id, SubtaskState.COMPLETED, result={"status": "done"})
    s1 = sm.get_subtask(s1_id)
    assert s1["status"] == SubtaskState.COMPLETED.value

    # Invalid transition: COMPLETED -> RETRYING
    with pytest.raises(InvalidStateTransitionError):
        sm.transition_subtask(s1_id, SubtaskState.RETRYING)


def test_new_subtask_tier_defaults_to_empty_and_missing_tier_stays_empty(tmp_path):
    sm = StateManager(str(tmp_path / "new-state.db"))
    with sm._get_connection() as conn:
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(subtasks)")}
    assert columns["assigned_tier"]["dflt_value"] == "''"

    run = sm.create_or_get_run("Task without tier", cwd=str(tmp_path))
    added = sm.add_subtasks(run["run_id"], [{"id": "step", "description": "No assigned via"}])
    assert added[0]["assigned_tier"] == ""


def test_recover_stranded_tasks():
    sm = StateManager(":memory:")
    run = sm.create_or_get_run("Interrupted Task")
    run_id = run["run_id"]

    added = sm.add_subtasks(run_id, [
        {"id": "t1", "description": "Task 1"},
        {"id": "t2", "description": "Task 2"},
    ])
    t1_id, _t2_id = added[0]["subtask_id"], added[1]["subtask_id"]

    # Simula crash: t1 estava RUNNING quando processo caiu
    sm.transition_subtask(t1_id, SubtaskState.RUNNING)
    assert sm.get_subtask(t1_id)["status"] == SubtaskState.RUNNING.value

    recovered = sm.recover_stranded_tasks(run_id)
    assert recovered == 1
    assert sm.get_subtask(t1_id)["status"] == SubtaskState.RETRYING.value


def test_active_panes_multislot():
    # Achado #7: Registro multi-slot em SQLite de panes ativos
    sm = StateManager(":memory:")
    sm.register_pane("w1:p1", run_id="run_a", subtask_id="s1")
    sm.register_pane("w1:p2", run_id="run_a", subtask_id="s2")
    sm.register_pane("w1:p3", run_id="run_b", subtask_id="s3")

    panes_a = sm.get_active_panes("run_a")
    assert sorted(panes_a) == ["w1:p1", "w1:p2"]

    all_panes = sm.get_active_panes()
    assert len(all_panes) == 3

    sm.unregister_pane("w1:p1")
    assert sm.get_active_panes("run_a") == ["w1:p2"]


@pytest.mark.asyncio
async def test_bridge_idempotent_resumption_skips_completed_subtasks(tmp_path):
    # Achado #7 / #28: Retomada idempotente em HerdrEventBridge
    db_file = tmp_path / "bridge_test.db"
    sm = StateManager(str(db_file))

    run = sm.create_or_get_run("Orchestration Plan", cwd=str(tmp_path))
    run_id = run["run_id"]
    sm.transition_run(run_id, RunState.RUNNING)

    subtask_data = {"id": "step_1", "description": "Already completed task", "target_files": ["f.py"]}
    added = sm.add_subtasks(run_id, [subtask_data])
    subtask_id = added[0]["subtask_id"]

    # Marca step_1 como COMPLETED em execução anterior
    sm.transition_subtask(subtask_id, SubtaskState.RUNNING)
    sm.transition_subtask(subtask_id, SubtaskState.COMPLETED, result={"status": "done"})

    mock_client = AsyncMock()
    mock_spawner = MagicMock()

    bridge = HerdrEventBridge(
        client=mock_client,
        spawner=mock_spawner,
        state_manager=sm,
    )
    bridge.current_run_id = run_id

    # Executa a subtask que já estava COMPLETED
    success = await bridge.execute_subtask(subtask_data, run_id=run_id)

    assert success is True
    # Spawner NÃO deve ter sido chamado porque a tarefa já estava concluída (idempotência!)
    mock_spawner.spawn_worker_pane.assert_not_called()
