import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meister.config import MeisterConfig
from meister.herdr.bridge import HerdrEventBridge
from meister.state import RunState, StateManager, SubtaskState, compute_subtask_id
from meister.worker import write_atomic_json

RUN_ID = "resume-attempts-run"
TASK_ID = "resume-task"
DESCRIPTION = "Resume attempt numbering"


def _init_repo(repo_dir: Path) -> None:
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)


def _make_state(tmp_path: Path, with_row: bool = True) -> tuple[StateManager, str]:
    state = StateManager(str(tmp_path / "state.db"))
    state.create_or_get_run(DESCRIPTION, force_run_id=RUN_ID)
    state.transition_run(RUN_ID, RunState.RUNNING)
    if with_row:
        state.add_subtasks(RUN_ID, [
            {"id": TASK_ID, "description": DESCRIPTION, "target_files": ["app.py"], "depends_on": []}
        ])
    return state, compute_subtask_id(RUN_ID, TASK_ID, DESCRIPTION)


def _set_attempts(state: StateManager, subtask_id: str, value) -> None:
    with state._get_connection() as conn:
        conn.execute("UPDATE subtasks SET attempts = ? WHERE subtask_id = ?", (value, subtask_id))


def _make_bridge(tmp_path: Path, state: StateManager, retries: int = 0, pane_lost=False):
    config = MeisterConfig()
    config.concurrency.layout_strategy = "tiled"
    config.concurrency.isolation_mode = "none"
    config.workers.tier_order[0].max_parallel = None
    config.retry.pane_lost_attempts = retries
    config.retry.pane_lost_backoff_seconds = 0
    client = AsyncMock()
    client.is_connected = True
    client.read_pane.return_value = ""
    client.pane_exists.return_value = not pane_lost
    bridge = HerdrEventBridge(config=config, client=client, state_manager=state)
    bridge.current_run_id = RUN_ID

    worktree_path = tmp_path / "worker-worktree"
    worktree_path.mkdir()
    worktree_manager = MagicMock()
    worktree_manager.create_worktree.return_value = SimpleNamespace(
        task_id="resume-worktree", worktree_path=str(worktree_path)
    )
    bridge._integration_pipeline = SimpleNamespace(
        integration_info=SimpleNamespace(branch_name="main"),
        wt_mgr=worktree_manager,
        prepare_subtask=MagicMock(return_value=object()),
        merge_prepared=MagicMock(return_value=(True, "")),
        last_integrated_sha=None,
    )
    return bridge, worktree_path


def _subtask_payload(repo_dir: Path) -> dict:
    return {
        "id": TASK_ID,
        "description": DESCRIPTION,
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
        "timeout": 3,
    }


def _spawn_events(log_event_mock) -> list[dict]:
    return [
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_spawn"
    ]


@pytest.mark.asyncio
async def test_resumed_subtask_continues_attempt_numbering_from_stored_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    state, subtask_id = _make_state(tmp_path)
    _set_attempts(state, subtask_id, 4)
    bridge, _ = _make_bridge(tmp_path, state)
    runs_dir = repo_dir / ".meister" / "runs"
    runs_dir.mkdir(parents=True)
    old_task_file = runs_dir / f"{RUN_ID}_{TASK_ID}_1_task.json"
    old_result_file = runs_dir / f"{RUN_ID}_{TASK_ID}_1.json"
    old_task_file.write_text('{"old": true}')
    old_result_file.write_text('{"old": true}')

    spawn_contexts = []
    spawn_task_files_exist = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawn_contexts.append(task_context)
        spawn_task_files_exist.append(Path(task_context["task_file"]).exists())
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return "w1:p1", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask(_subtask_payload(repo_dir)), timeout=5
        )

    assert result is True
    spawn_events = _spawn_events(log_event_mock)
    assert [event["attempt"] for event in spawn_events] == [5]
    context = spawn_contexts[0]
    assert context["task_file"].endswith(f"{RUN_ID}_{TASK_ID}_5_task.json")
    assert context["result_file"].endswith(f"{RUN_ID}_{TASK_ID}_5.json")
    assert context["exit_file"].endswith(f"{RUN_ID}_{TASK_ID}_5.exit")
    assert spawn_task_files_exist == [True]
    assert old_task_file.read_text() == '{"old": true}'
    assert old_result_file.read_text() == '{"old": true}'
    row = state.get_subtask(subtask_id)
    assert row["status"] == SubtaskState.COMPLETED.value
    assert row["attempts"] == 5


@pytest.mark.asyncio
async def test_pane_lost_retry_after_resume_gets_next_attempt_number(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.001")
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    state, subtask_id = _make_state(tmp_path)
    _set_attempts(state, subtask_id, 4)
    bridge, worktree_path = _make_bridge(
        tmp_path, state, retries=1, pane_lost=True
    )

    spawn_attempts = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawn_attempts.append(task_context["result_file"])
        if len(spawn_attempts) == 1:
            (worktree_path / "partial.txt").write_text("partial worker output")
        else:
            write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
            bridge.client.pane_exists.return_value = True
        return f"w1:p{len(spawn_attempts)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask(_subtask_payload(repo_dir)), timeout=5
        )

    assert result is True
    assert [event["attempt"] for event in _spawn_events(log_event_mock)] == [5, 6]
    assert spawn_attempts[0].endswith(f"{RUN_ID}_{TASK_ID}_5.json")
    assert spawn_attempts[1].endswith(f"{RUN_ID}_{TASK_ID}_6.json")
    assert state.get_subtask(subtask_id)["attempts"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["fresh_row", "missing_row", "no_run"])
async def test_first_attempt_stays_one_without_prior_attempts(tmp_path, monkeypatch, scenario):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    state, _ = _make_state(tmp_path, with_row=scenario != "missing_row")
    bridge, _ = _make_bridge(tmp_path, state)
    if scenario == "no_run":
        bridge.current_run_id = None

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return "w1:p1", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask(_subtask_payload(repo_dir)), timeout=5
        )
    assert [event["attempt"] for event in _spawn_events(log_event_mock)] == [1]
    assert result is True


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, "garbage", -2])
async def test_invalid_stored_attempts_are_treated_as_zero(tmp_path, monkeypatch, stored):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    state, subtask_id = _make_state(tmp_path)
    real_get_subtask = state.get_subtask

    def get_subtask_with_bad_attempts(sid):
        row = real_get_subtask(sid)
        if row is not None and sid == subtask_id:
            row = dict(row)
            row["attempts"] = stored
        return row

    state.get_subtask = get_subtask_with_bad_attempts
    bridge, _ = _make_bridge(tmp_path, state)

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return "w1:p1", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask(_subtask_payload(repo_dir)), timeout=5
        )

    assert result is True
    assert [event["attempt"] for event in _spawn_events(log_event_mock)] == [1]


@pytest.mark.asyncio
async def test_stored_attempts_are_read_without_capping_small_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    state, subtask_id = _make_state(tmp_path)
    _set_attempts(state, subtask_id, 2)
    bridge, _ = _make_bridge(tmp_path, state)

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return "w1:p1", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        await asyncio.wait_for(bridge.execute_subtask(_subtask_payload(repo_dir)), timeout=5)

    assert [event["attempt"] for event in _spawn_events(log_event_mock)] == [3]


@pytest.mark.asyncio
async def test_recovered_stranded_run_resumes_with_higher_unique_attempts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    state, subtask_id = _make_state(tmp_path)
    state.transition_subtask(subtask_id, to_state=SubtaskState.RUNNING)
    state.transition_subtask(subtask_id, to_state=SubtaskState.RUNNING)
    assert state.get_subtask(subtask_id)["attempts"] == 2
    assert state.recover_stranded_tasks(RUN_ID) == 1
    assert state.get_subtask(subtask_id)["status"] == SubtaskState.RETRYING.value

    bridge, _ = _make_bridge(tmp_path, state)

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return "w1:p1", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask(_subtask_payload(repo_dir)), timeout=5
        )

    assert result is True
    attempts = [event["attempt"] for event in _spawn_events(log_event_mock)]
    assert attempts == [3]
    assert len(attempts) == len(set(attempts))
    assert state.get_subtask(subtask_id)["attempts"] == 3
