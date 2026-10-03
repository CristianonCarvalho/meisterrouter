import json
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.config import load_config
from meister.herdr.bridge import HerdrEventBridge, ResumeRequestError
from meister.state import RunState, StateManager, SubtaskState, compute_run_id, task_fingerprint
from meister.worktree import IntegrationPipeline


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Resume Tests")
    _git(repo, "config", "user.email", "resume-tests@example.com")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    commits = {}
    for step_id, filename in (("t1", "one.txt"), ("t2", "two.txt"), ("t3", "three.txt")):
        (repo / filename).write_text(f"{step_id}\n")
        _git(repo, "add", filename)
        _git(repo, "commit", "-m", step_id)
        commits[step_id] = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "retained-commits", commits["t3"])
    _git(repo, "reset", "--hard", base_sha)
    return repo, commits


def _tasks(t3_targets=None, t1_description="First task", t2_description="Second task"):
    return [
        {"id": "t1", "description": t1_description, "target_files": ["one.txt"], "depends_on": []},
        {"id": "t2", "description": t2_description, "target_files": ["two.txt"], "depends_on": ["t1"]},
        {
            "id": "t3",
            "description": "Third task",
            "target_files": t3_targets or ["three.txt"],
            "depends_on": ["t2"],
        },
    ]


def _seed_source(sm, repo, tasks, commits, failed=True, run_id="source-run"):
    run = sm.create_or_get_run("old plan", cwd=os.path.realpath(repo), force_run_id=run_id)
    sm.transition_run(run_id, RunState.RUNNING)
    rows = sm.add_subtasks(run_id, tasks)
    for task, row in zip(tasks, rows):
        if task["id"] in commits:
            sm.transition_subtask(row["subtask_id"], SubtaskState.RUNNING, assigned_tier="codex")
            sm.transition_subtask(
                row["subtask_id"],
                SubtaskState.COMPLETED,
                integrated_sha=commits[task["id"]],
            )
        else:
            sm.transition_subtask(row["subtask_id"], SubtaskState.RUNNING)
            sm.transition_subtask(row["subtask_id"], SubtaskState.FAILED, error="worker failed")
    if failed:
        sm.transition_run(run_id, RunState.FAILED)
    return run


def _seed_same_run(sm, repo, task):
    run_id = compute_run_id(task, os.path.realpath(repo))
    sm.create_or_get_run(task, cwd=os.path.realpath(repo), force_run_id=run_id)
    sm.transition_run(run_id, RunState.RUNNING)
    rows = sm.add_subtasks(run_id, _tasks())
    for row in rows[:2]:
        sm.transition_subtask(row["subtask_id"], SubtaskState.RUNNING)
        sm.transition_subtask(
            row["subtask_id"],
            SubtaskState.COMPLETED,
            integrated_sha="a" * 40,
        )
    sm.transition_run(run_id, RunState.FAILED)
    return run_id


def _bridge(sm, repo, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("MEISTER_WORKTREES_DIR", str(repo.parent / "worktrees"))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(repo.parent / "logs"))
    client = AsyncMock()
    client.is_connected = True
    client.get_current_pane.return_value = None
    return HerdrEventBridge(config=load_config(), client=client, state_manager=sm)


def test_task_fingerprint_uses_description_and_recursive_dependencies():
    first = {"id": "t1", "description": "First", "target_files": ["one.txt"], "depends_on": []}
    second = {"id": "t2", "description": "Second", "target_files": ["two.txt"], "depends_on": ["t1"]}
    third = {"id": "t3", "description": "Third", "target_files": ["three.txt"], "depends_on": ["t2"]}
    original = {step["id"]: step for step in (first, second, third)}

    same_description = {**first, "description": "  First  ", "target_files": ["different.txt"]}
    changed = {**second, "description": "Changed"}
    changed_steps = {**original, "t1": same_description, "t2": changed}
    changed_fingerprints = {
        step_id: task_fingerprint(step, changed_steps)
        for step_id, step in changed_steps.items()
    }
    original_fingerprints = {
        step_id: task_fingerprint(step, original)
        for step_id, step in original.items()
    }

    assert changed_fingerprints["t1"] == original_fingerprints["t1"]
    assert changed_fingerprints["t2"] != original_fingerprints["t2"]
    assert changed_fingerprints["t3"] != original_fingerprints["t3"]


def test_task_fingerprint_rejects_cycles_without_recursing_forever():
    tasks = {
        "t1": {"id": "t1", "description": "One", "depends_on": ["t2"]},
        "t2": {"id": "t2", "description": "Two", "depends_on": ["t1"]},
    }
    with pytest.raises(ValueError, match="Ciclo"):
        task_fingerprint(tasks["t1"], tasks)


@pytest.mark.asyncio
async def test_bridge_reuses_committed_tasks_after_plan_scope_edit(tmp_path, monkeypatch):
    repo, commits = _git_repo(tmp_path)
    sm = StateManager(str(tmp_path / "state.db"))
    original_tasks = _tasks()
    _seed_source(sm, repo, original_tasks, {"t1": commits["t1"], "t2": commits["t2"]})
    updated_tasks = _tasks(t3_targets=["three.txt", "extra.txt"])
    bridge = _bridge(sm, repo, monkeypatch)
    pending_execution = []

    async def execute_updated_plan(steps):
        rows = {row["step_id"]: row for row in sm.get_subtasks(bridge.current_run_id)}
        assert rows["t1"]["status"] == SubtaskState.COMPLETED.value
        assert rows["t2"]["status"] == SubtaskState.COMPLETED.value
        integration_path = bridge._integration_pipeline.integration_info.worktree_path
        assert os.path.exists(os.path.join(integration_path, "one.txt"))
        assert os.path.exists(os.path.join(integration_path, "two.txt"))
        pending_execution.extend(step["id"] for step in steps if rows[step["id"]]["status"] == "PENDING")
        t3 = rows["t3"]
        sm.transition_subtask(t3["subtask_id"], SubtaskState.RUNNING)
        sm.transition_subtask(t3["subtask_id"], SubtaskState.COMPLETED, result={"status": "done"})
        return True

    bridge.execute_plan = execute_updated_plan
    bridge.gate = MagicMock()
    bridge.gate.evaluate_completion.return_value = {"action": "COMPLETE"}
    monkeypatch.setattr(IntegrationPipeline, "validate_final_integration", lambda self: (True, "ok"))
    monkeypatch.setattr(IntegrationPipeline, "apply_fast_forward", lambda self: (True, "ok"))

    success = await bridge.run_orchestration_cycle(
        task=json.dumps(updated_tasks),
        allow_freeform=False,
        resume_run_id="auto",
    )

    new_run_id = bridge.current_run_id
    assert success is True
    assert pending_execution == ["t3"]
    assert sm.get_run(new_run_id)["state"] == RunState.COMPLETED.value
    assert sm.get_run(new_run_id)["metadata_json"]
    assert (repo / "base.txt").exists()
    assert "one.txt" in _git(repo, "ls-tree", "-r", "--name-only", f"meister/integration/{new_run_id}")
    assert "two.txt" in _git(repo, "ls-tree", "-r", "--name-only", f"meister/integration/{new_run_id}")
    assert _git(repo, "rev-parse", f"refs/meister/resume/{new_run_id}/t1") == commits["t1"]
    assert _git(repo, "rev-parse", f"refs/meister/resume/{new_run_id}/t2") == commits["t2"]
    source_run = sm.get_run("source-run")
    assert json.loads(source_run["metadata_json"])["superseded_by"] == new_run_id
    assert json.loads(sm.get_run(new_run_id)["metadata_json"])["resumed_from"] == "source-run"
    assert sm.find_resumable_run(os.path.realpath(repo), exclude_run_id="another-run") is None
    resumed_t1 = next(row for row in sm.get_subtasks(new_run_id) if row["step_id"] == "t1")
    assert json.loads(resumed_t1["result_json"])["resumed_from"] == {
        "run_id": "source-run",
        "subtask_id": next(row for row in sm.get_subtasks("source-run") if row["step_id"] == "t1")["subtask_id"],
    }


@pytest.mark.parametrize(
    ("change", "expected_status"),
    [
        ("missing_commit", "PENDING"),
        ("scope", "PENDING"),
        ("description", "PENDING"),
        ("dependency", "PENDING"),
    ],
)
def test_bridge_reuse_rejects_invalid_origin_task(tmp_path, monkeypatch, change, expected_status):
    repo, commits = _git_repo(tmp_path)
    sm = StateManager(str(tmp_path / f"{change}.db"))
    original_tasks = _tasks()
    source_shas = {"t1": commits["t1"], "t2": commits["t2"]}
    if change == "missing_commit":
        source_shas["t1"] = "0" * 40
    _seed_source(sm, repo, original_tasks, source_shas)
    updated_tasks = _tasks()
    if change == "scope":
        updated_tasks[0]["target_files"] = ["elsewhere.txt"]
    elif change == "description":
        updated_tasks[0]["description"] = "Changed description"
    elif change == "dependency":
        updated_tasks[0]["target_files"] = ["elsewhere.txt"]
    bridge = _bridge(sm, repo, monkeypatch)
    reuse_events = []
    monkeypatch.setattr("meister.herdr.bridge.log_event", lambda **event: reuse_events.append(event))
    new_run = sm.create_or_get_run(json.dumps(updated_tasks), cwd=str(repo), force_run_id=f"new-{change}")
    new_rows = sm.add_subtasks(new_run["run_id"], updated_tasks)

    bridge._reuse_completed_subtasks(sm, "source-run", new_run["run_id"], updated_tasks, str(repo))

    status_by_id = {row["step_id"]: row["status"] for row in sm.get_subtasks(new_run["run_id"])}
    if change == "missing_commit":
        assert status_by_id["t1"] == expected_status
        assert status_by_id["t2"] == "PENDING"
    elif change == "scope":
        assert status_by_id["t1"] == expected_status
        assert status_by_id["t2"] == "PENDING"
    elif change == "description":
        assert status_by_id["t1"] == expected_status
        assert status_by_id["t2"] == "PENDING"
    else:
        assert status_by_id["t1"] == "PENDING"
        assert status_by_id["t2"] == "PENDING"
    event_reasons = {event["task_id"]: event["reason"] for event in reuse_events}
    assert event_reasons["t1"] == ("missing_commit" if change == "missing_commit" else
                                   "changed" if change == "description" else "scope")
    if change in {"missing_commit", "scope", "description", "dependency"}:
        assert event_reasons["t2"] == "dependency_not_reused"
    assert len(new_rows) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_state", "exists", "source_cwd", "should_raise"),
    [
        ("FAILED", True, "same", False),
        ("RUNNING", True, "same", False),
        ("COMPLETED", True, "same", True),
        ("FAILED", False, "same", True),
        ("FAILED", True, "other", True),
    ],
)
async def test_explicit_resume_source_validation(
    tmp_path,
    monkeypatch,
    source_state,
    exists,
    source_cwd,
    should_raise,
):
    repo, _ = _git_repo(tmp_path)
    sm = StateManager(str(tmp_path / f"explicit-{source_state}-{exists}-{source_cwd}.db"))
    source_id = "source-explicit"
    if exists:
        source_repo = repo if source_cwd == "same" else tmp_path / "other"
        source_repo.mkdir(exist_ok=True)
        sm.create_or_get_run("source", cwd=os.path.realpath(source_repo), force_run_id=source_id)
        sm.transition_run(source_id, RunState.RUNNING)
        if source_state != "RUNNING":
            sm.transition_run(source_id, RunState(source_state))
    bridge = _bridge(sm, repo, monkeypatch)
    task = json.dumps(_tasks())

    if should_raise:
        with pytest.raises(ResumeRequestError):
            await bridge.run_orchestration_cycle(
                task=task,
                allow_freeform=False,
                resume_run_id=source_id,
            )
    else:
        bridge.execute_plan = AsyncMock(return_value=False)
        await bridge.run_orchestration_cycle(
            task=task,
            allow_freeform=False,
            resume_run_id=source_id,
        )


@pytest.mark.asyncio
async def test_explicit_resume_of_same_run_keeps_completed_tasks(tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    task = json.dumps(_tasks())
    sm = StateManager(str(tmp_path / "same-run.db"))
    run_id = _seed_same_run(sm, repo, task)
    bridge = _bridge(sm, repo, monkeypatch)
    bridge.config.concurrency.isolation_mode = "none"
    events = []
    monkeypatch.setattr("meister.herdr.bridge.log_event", lambda **event: events.append(event))
    dispatched = []
    bridge.gate = MagicMock()
    bridge.gate.run_verification.return_value = (True, "ok")
    bridge.gate.get_diff_summary.return_value = ""
    bridge.gate.evaluate_completion.return_value = {"action": "COMPLETE"}
    execute_subtask = bridge.execute_subtask

    async def dispatch_only_pending(step):
        step_id = step["id"] if isinstance(step, dict) else step.id
        row = next(row for row in sm.get_subtasks(run_id) if row["step_id"] == step_id)
        if row["status"] == SubtaskState.COMPLETED.value:
            return await execute_subtask(step)
        dispatched.append(step_id)
        sm.transition_subtask(row["subtask_id"], SubtaskState.RUNNING)
        sm.transition_subtask(row["subtask_id"], SubtaskState.COMPLETED)
        return True

    bridge.execute_subtask = dispatch_only_pending
    assert await bridge.run_orchestration_cycle(
        task=task,
        allow_freeform=False,
        resume_run_id=run_id,
    ) is True

    assert dispatched == ["t3"]
    run = sm.get_run(run_id)
    assert run["state"] == RunState.COMPLETED.value
    metadata = json.loads(run["metadata_json"] or "{}")
    assert "superseded_by" not in metadata
    assert "resumed_from" not in metadata
    assert any(event["event_type"] == "resume_same_run" for event in events)
    notifications = [call.args[0] for call in bridge.client.show_notification.await_args_list]
    assert any(run_id[:8] in message and "tarefas concluidas sao puladas" in message for message in notifications)


@pytest.mark.asyncio
async def test_auto_resume_uses_same_run_when_no_other_run_is_eligible(tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    task = json.dumps(_tasks())
    sm = StateManager(str(tmp_path / "auto-same-run.db"))
    run_id = _seed_same_run(sm, repo, task)
    bridge = _bridge(sm, repo, monkeypatch)
    bridge.config.concurrency.isolation_mode = "none"
    messages = []
    events = []
    monkeypatch.setattr("meister.herdr.bridge.log_event", lambda **event: events.append(event))
    bridge.execute_plan = AsyncMock(return_value=False)

    assert await bridge.run_orchestration_cycle(
        task=task,
        allow_freeform=False,
        resume_run_id="auto",
        resume_hint_callback=messages.append,
    ) is False

    assert messages == [
        f"Plano identico ao run {run_id[:8]} (FAILED): retomando o mesmo run; tarefas concluidas sao puladas."
    ]
    assert any(event["event_type"] == "resume_same_run" for event in events)
    assert messages[0] in [call.args[0] for call in bridge.client.show_notification.await_args_list]


@pytest.mark.asyncio
async def test_explicit_resume_rejects_completed_same_run(tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    task = json.dumps(_tasks())
    sm = StateManager(str(tmp_path / "completed-same-run.db"))
    run_id = _seed_same_run(sm, repo, task)
    sm.transition_run(run_id, RunState.RUNNING)
    sm.transition_run(run_id, RunState.COMPLETED)
    bridge = _bridge(sm, repo, monkeypatch)

    with pytest.raises(ResumeRequestError, match="está COMPLETED"):
        await bridge.run_orchestration_cycle(
            task=task,
            allow_freeform=False,
            resume_run_id=run_id,
        )


@pytest.mark.asyncio
async def test_rejected_resume_names_automatic_source_candidate(tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    sm = StateManager(str(tmp_path / "candidate.db"))
    _seed_source(sm, repo, _tasks(), {}, run_id="automatic-candidate")
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    sm.create_or_get_run("other cwd", cwd=os.path.realpath(other_repo), force_run_id="wrong-cwd")
    sm.transition_run("wrong-cwd", RunState.RUNNING)
    sm.transition_run("wrong-cwd", RunState.FAILED)
    bridge = _bridge(sm, repo, monkeypatch)

    with pytest.raises(ResumeRequestError, match="Use --resume sem id para usar o run automati"):
        await bridge.run_orchestration_cycle(
            task=json.dumps(_tasks()),
            allow_freeform=False,
            resume_run_id="wrong-cwd",
        )


@pytest.mark.asyncio
async def test_resume_replay_failure_fails_run_with_clear_error(tmp_path, monkeypatch):
    repo, commits = _git_repo(tmp_path)
    # Make HEAD diverge from the source commit while changing the same path.
    _git(repo, "checkout", "main")
    (repo / "one.txt").write_text("conflicting main edit\n")
    _git(repo, "add", "one.txt")
    _git(repo, "commit", "-m", "conflicting edit")

    sm = StateManager(str(tmp_path / "replay.db"))
    source_tasks = _tasks()
    _seed_source(sm, repo, source_tasks, {"t1": commits["t1"]})
    bridge = _bridge(sm, repo, monkeypatch)
    bridge.execute_plan = AsyncMock(return_value=True)

    success = await bridge.run_orchestration_cycle(
        task=json.dumps(_tasks(t3_targets=["three.txt", "extra.txt"])),
        allow_freeform=False,
        resume_run_id="auto",
    )

    assert success is False
    assert sm.get_run(bridge.current_run_id)["state"] == RunState.FAILED.value
    assert "falha ao reaplicar t1" in bridge.client.show_notification.await_args.args[0].lower()
    assert "rode sem --resume" in bridge.client.show_notification.await_args.args[0].lower()
    bridge.execute_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_resume_reexecutes_tasks_and_does_not_pin_commits(tmp_path, monkeypatch):
    repo, commits = _git_repo(tmp_path)
    sm = StateManager(str(tmp_path / "no-resume.db"))
    _seed_source(sm, repo, _tasks(), {"t1": commits["t1"], "t2": commits["t2"]})
    updated_tasks = _tasks(t3_targets=["three.txt", "extra.txt"])
    bridge = _bridge(sm, repo, monkeypatch)

    async def assert_all_steps_pending(steps):
        assert all(row["status"] == "PENDING" for row in sm.get_subtasks(bridge.current_run_id))
        return False

    bridge.execute_plan = assert_all_steps_pending
    assert await bridge.run_orchestration_cycle(task=json.dumps(updated_tasks), allow_freeform=False) is False
    assert _git(repo, "for-each-ref", "--format=%(refname)", "refs/meister/resume") == ""


def test_cli_prints_resume_hint_without_resume_option(tmp_path, monkeypatch):
    repo, commits = _git_repo(tmp_path)
    db_path = tmp_path / "cli-state.db"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("MEISTER_DB_PATH", str(db_path))
    sm = StateManager(str(db_path))
    _seed_source(sm, repo, _tasks(), {"t1": commits["t1"], "t2": commits["t2"]})
    config = load_config()
    monkeypatch.setattr("meister.cli.load_config", lambda _path=None: config)
    monkeypatch.setattr("meister.cli.get_herdr_client", lambda **_kwargs: AsyncMock())
    mock_cycle = AsyncMock(return_value=True)
    monkeypatch.setattr(HerdrEventBridge, "run_orchestration_cycle", mock_cycle)

    result = CliRunner().invoke(
        main,
        ["orchestrate", "--task", json.dumps(_tasks(t3_targets=["three.txt", "extra.txt"]))],
    )

    assert result.exit_code == 0, result.output
    assert "Run source-run (FAILED) tem 2 tarefas concluidas reaproveitaveis; use --resume" in result.output
    assert "resume_run_id" not in mock_cycle.await_args.kwargs


def test_cli_explicit_resume_errors_exit_with_code_two(tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    monkeypatch.chdir(repo)
    config = load_config()
    monkeypatch.setattr("meister.cli.load_config", lambda _path=None: config)
    monkeypatch.setattr("meister.cli.get_herdr_client", lambda **_kwargs: AsyncMock())
    mock_cycle = AsyncMock(side_effect=ResumeRequestError("Run inexistente"))
    monkeypatch.setattr(HerdrEventBridge, "run_orchestration_cycle", mock_cycle)
    tasks = json.dumps(_tasks())

    result = CliRunner().invoke(
        main,
        ["orchestrate", "--task", tasks, "--resume", "missing-run"],
    )

    assert result.exit_code == 2
    assert "Run inexistente" in result.output
    assert mock_cycle.await_args.kwargs["resume_run_id"] == "missing-run"
