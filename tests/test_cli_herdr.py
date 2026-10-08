import os
import json
import signal
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from click.testing import CliRunner
from click import echo
from meister.cli import main
from meister.logger import log_event
from meister.state import RunState, StateManager, SubtaskState


def test_cli_has_herdr_commands():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "daemon" in result.output
    assert "herdr-action" in result.output
    assert "orchestrate" in result.output


def test_cli_daemon_help():
    runner = CliRunner()
    result = runner.invoke(main, ["daemon", "--help"])
    assert result.exit_code == 0
    assert "--start" in result.output
    assert "--stop" in result.output
    assert "--status" in result.output


def test_cli_daemon_no_flags(tmp_path):
    runner = CliRunner()
    pid_file = str(tmp_path / "daemon.pid")
    result = runner.invoke(main, ["daemon", "--pid-file", pid_file])
    assert result.exit_code == 0
    assert "Specify --start, --stop, or --status" in result.output


def test_cli_daemon_status_when_stopped(tmp_path):
    runner = CliRunner()
    pid_file = str(tmp_path / "daemon.pid")
    result = runner.invoke(main, ["daemon", "--status", "--pid-file", pid_file])
    assert result.exit_code == 0
    assert "Meister daemon is not running." in result.output


def test_cli_daemon_status_and_stop_lifecycle(tmp_path):
    runner = CliRunner()
    pid_file = tmp_path / "daemon.pid"

    # Write current process PID to simulate active daemon
    pid_file.write_text(str(os.getpid()))

    # Status check
    res_status = runner.invoke(main, ["daemon", "--status", "--pid-file", str(pid_file)])
    assert res_status.exit_code == 0
    assert f"Meister daemon is running (PID: {os.getpid()})." in res_status.output

    # Start when already running should notify
    res_start = runner.invoke(main, ["daemon", "--start", "--pid-file", str(pid_file)])
    assert res_start.exit_code == 0
    assert "already running" in res_start.output

    # Mock os.kill for stopping daemon without killing our test runner process
    with patch("os.kill") as mock_kill:
        res_stop = runner.invoke(main, ["daemon", "--stop", "--pid-file", str(pid_file)])
        assert res_stop.exit_code == 0
        assert "Sent SIGTERM to Meister daemon" in res_stop.output
        mock_kill.assert_any_call(os.getpid(), signal.SIGTERM)
        assert not pid_file.exists()


def test_cli_daemon_stop_when_not_running(tmp_path):
    runner = CliRunner()
    pid_file = str(tmp_path / "daemon.pid")
    result = runner.invoke(main, ["daemon", "--stop", "--pid-file", pid_file])
    assert result.exit_code == 0
    assert "Meister daemon is not running." in result.output


def test_cli_daemon_start_lifecycle(tmp_path):
    runner = CliRunner()
    pid_file = tmp_path / "daemon.pid"

    mock_client = AsyncMock()
    mock_client.connect.return_value = None
    mock_client.subscribe_events.return_value = None
    mock_client.disconnect.return_value = None

    with patch("meister.cli.HerdrSocketClient", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        result = runner.invoke(main, ["daemon", "--start", "--pid-file", str(pid_file)])
        assert "Starting MeisterRouter daemon" in result.output
        assert "MeisterRouter daemon stopped." in result.output
        assert not pid_file.exists()


def test_cli_herdr_action_classify():
    runner = CliRunner()
    mock_classification = {
        "complexity": "low",
        "recommended_model": "luna",
        "reasoning": "Simple task",
    }
    with patch("meister.cli.get_herdr_client", return_value=None), \
         patch("meister.cli.classify_task", return_value=mock_classification) as mock_cls:
        result = runner.invoke(main, ["herdr-action", "classify", "--workspace-id", "ws-1"])
        assert result.exit_code == 0
        mock_cls.assert_called_once()
        data = json.loads(result.output)
        assert data["complexity"] == "low"
        assert data["recommended_model"] == "luna"


def test_cli_herdr_action_classify_with_pane():
    runner = CliRunner()
    mock_classification = {
        "complexity": "high",
        "recommended_model": "gemini-3.8-flash",
    }
    mock_client = AsyncMock()
    mock_client.read_pane.return_value = "Complex architecture refactor"

    with patch("meister.cli.HerdrSocketClient", return_value=mock_client), \
         patch("meister.cli.classify_task", return_value=mock_classification) as mock_cls:
        result = runner.invoke(main, ["herdr-action", "classify", "--pane-id", "pane-123"])
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(context="Complex architecture refactor")
        data = json.loads(result.output)
        assert data["recommended_model"] == "gemini-3.8-flash"


def test_cli_herdr_action_verify():
    runner = CliRunner()
    mock_gate_eval = {
        "action": "COMPLETE",
        "commit_ready": True,
        "reasoning": "Deterministic tests passed",
    }

    with patch("meister.gate.DeterministicGate") as mock_gate_class:
        mock_gate = MagicMock()
        mock_gate.run_verification.return_value = (True, "All 42 tests passed")
        mock_gate.evaluate_completion.return_value = mock_gate_eval
        mock_gate_class.return_value = mock_gate

        result = runner.invoke(main, ["herdr-action", "verify"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["action"] == "COMPLETE"
        assert data["commit_ready"] is True


def test_cli_herdr_action_orchestrate_success():
    runner = CliRunner()

    with patch("meister.cli.HerdrEventBridge") as mock_bridge_cls:
        mock_bridge = MagicMock()
        mock_bridge.run_orchestration_cycle = AsyncMock(return_value=True)
        mock_bridge_cls.return_value = mock_bridge

        result = runner.invoke(main, ["herdr-action", "orchestrate", "--workspace-id", "ws-1"])
        assert result.exit_code == 0
        assert "Orchestration cycle completed successfully." in result.output
        mock_bridge.run_orchestration_cycle.assert_called_once_with(
            workspace_id="ws-1",
            architect_pane_id=None,
            task=None,
        )


def test_cli_herdr_action_orchestrate_failure():
    runner = CliRunner()

    with patch("meister.cli.HerdrEventBridge") as mock_bridge_cls:
        mock_bridge = MagicMock()
        mock_bridge.run_orchestration_cycle = AsyncMock(return_value=False)
        mock_bridge_cls.return_value = mock_bridge

        result = runner.invoke(main, ["herdr-action", "orchestrate", "--workspace-id", "ws-1"])
        assert result.exit_code != 0
        assert "Orchestration cycle failed or incomplete." in result.output


def test_cli_herdr_action_unknown():
    runner = CliRunner()
    result = runner.invoke(main, ["herdr-action", "invalid-action-xyz"])
    assert result.exit_code != 0
    assert "Unknown Herdr action 'invalid-action-xyz'" in result.output


def test_cli_orchestrate_command_success():
    runner = CliRunner()

    with patch("meister.cli.HerdrEventBridge") as mock_bridge_cls:
        mock_bridge = MagicMock()
        mock_bridge.run_orchestration_cycle = AsyncMock(return_value=True)
        mock_bridge_cls.return_value = mock_bridge

        result = runner.invoke(
            main,
            ["orchestrate", "--workspace-id", "ws-main", "--architect-pane-id", "pane-arch"],
        )
        assert result.exit_code == 0
        assert "Orchestration cycle completed successfully." in result.output
        mock_bridge.run_orchestration_cycle.assert_called_once_with(
            workspace_id="ws-main",
            architect_pane_id="pane-arch",
            task=None,
            allow_freeform=False,
            resume_hint_callback=echo,
        )


def test_cli_orchestrate_command_failure():
    runner = CliRunner()

    with patch("meister.cli.HerdrEventBridge") as mock_bridge_cls:
        mock_bridge = MagicMock()
        mock_bridge.run_orchestration_cycle = AsyncMock(return_value=False)
        mock_bridge_cls.return_value = mock_bridge

        result = runner.invoke(main, ["orchestrate"])
        assert result.exit_code != 0
        assert "Orchestration cycle failed or incomplete." in result.output


def _seed_cli_run(run_state=RunState.COMPLETED, subtask_state=SubtaskState.COMPLETED):
    state = StateManager()
    run_id = "cli-progress-run"
    state.create_or_get_run("CLI progress test", force_run_id=run_id)
    state.transition_run(run_id, RunState.RUNNING)
    state.add_subtasks(run_id, [{"id": "step-a", "description": "A task"}])
    subtask = state.get_subtasks(run_id)[0]
    if subtask_state == SubtaskState.COMPLETED:
        state.transition_subtask(subtask["subtask_id"], SubtaskState.RUNNING, assigned_tier="tier_1b")
    state.transition_subtask(subtask["subtask_id"], subtask_state, assigned_tier="tier_1b")
    if run_state != RunState.RUNNING:
        state.transition_run(run_id, run_state)
    return run_id


def _emit_cli_progress(run_id):
    from datetime import datetime, timedelta, timezone

    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    log_event("orchestration_start", run_id=run_id, task_id="orchestrator")
    log_event(
        "plan_parsed",
        run_id=run_id,
        task_id="orchestrator",
        total=1,
        batches=1,
        task_ids=["step-a"],
    )
    log_event("worker_spawn", run_id=run_id, task_id="step-a", tier="tier_1b", ts=started.isoformat())
    log_event(
        "subtask_completed",
        run_id=run_id,
        task_id="step-a",
        tier="tier_1b",
        ts=(started + timedelta(seconds=28)).isoformat(),
    )


def test_cli_orchestrate_progress_stderr_and_stdout_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    run_id = _seed_cli_run()

    async def execute(**_kwargs):
        _emit_cli_progress(run_id)
        return True

    with patch("meister.cli.HerdrEventBridge") as bridge_cls:
        bridge = bridge_cls.return_value
        bridge.run_orchestration_cycle = AsyncMock(side_effect=execute)
        result = CliRunner().invoke(main, ["orchestrate", "--workspace-id", "ws-main"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "Orchestration cycle completed successfully."
    assert "Plano: 1 tarefas em 1 lotes (run cli-prog)" in result.stderr
    assert "[1/1] step-a iniciada em tier_1b" in result.stderr
    assert "[1/1] step-a concluida em tier_1b (28 s)" in result.stderr
    assert "Resumo do run cli-prog: 1 tarefas | 1 concluidas | 0 falhou | 0 reaproveitadas" in result.stderr
    assert "Concluido: a main foi atualizada." in result.stderr


def test_cli_orchestrate_resume_passes_hint_callback_so_the_message_is_printed(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    seen = {}

    async def execute(**kwargs):
        seen.update(kwargs)
        kwargs["resume_hint_callback"]("Plano identico ao run abc12345 (FAILED): retomando o mesmo run")
        return True

    for args, expected in ((["orchestrate", "--resume", "-q"], "auto"), (["orchestrate", "--resume", "-q", "run-x"], "run-x")):
        seen.clear()
        with patch("meister.cli.HerdrEventBridge") as bridge_cls:
            bridge_cls.return_value.run_orchestration_cycle = AsyncMock(side_effect=execute)
            result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, result.output
        assert seen["resume_run_id"] == expected
        assert "Plano identico ao run abc12345 (FAILED): retomando o mesmo run" in result.stdout


def test_cli_orchestrate_quiet_suppresses_progress_and_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    run_id = _seed_cli_run()

    async def execute(**_kwargs):
        _emit_cli_progress(run_id)
        return True

    with patch("meister.cli.HerdrEventBridge") as bridge_cls:
        bridge_cls.return_value.run_orchestration_cycle = AsyncMock(side_effect=execute)
        result = CliRunner().invoke(main, ["orchestrate", "-q"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "Orchestration cycle completed successfully."
    # Avisos de configuracao (chave do OpenRouter, CLIs dos workers no PATH) dependem da maquina;
    # o que --quiet deve garantir e a ausencia de progresso e resumo.
    for marker in ("Plano:", "[1/1]", "Resumo do run", "Proximo passo", "Concluido:"):
        assert marker not in result.stderr

    help_result = CliRunner().invoke(main, ["orchestrate", "--help"])
    assert help_result.exit_code == 0
    assert "-q, --quiet" in help_result.output


def test_cli_orchestrate_failure_status_and_observer_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    run_id = _seed_cli_run(run_state=RunState.FAILED, subtask_state=SubtaskState.FAILED)
    remove_observer = __import__("meister.cli", fromlist=["remove_event_observer"]).remove_event_observer
    removed = []

    def tracked_remove(callback):
        removed.append(callback)
        remove_observer(callback)

    monkeypatch.setattr("meister.cli.remove_event_observer", tracked_remove)

    async def execute(**_kwargs):
        log_event("orchestration_start", run_id=run_id, task_id="orchestrator")
        log_event(
            "plan_parsed",
            run_id=run_id,
            task_id="orchestrator",
            total=1,
            batches=1,
            task_ids=["step-a"],
        )
        log_event(
            "subtask_rejected",
            run_id=run_id,
            task_id="step-a",
            error="violacao de escopo",
        )
        return False

    with patch("meister.cli.HerdrEventBridge") as bridge_cls:
        bridge_cls.return_value.run_orchestration_cycle = AsyncMock(side_effect=execute)
        result = CliRunner().invoke(main, ["orchestrate"])

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Orchestration cycle failed or incomplete." in result.stderr
    assert "[1/1] step-a FALHOU: violacao de escopo" in result.stderr
    assert "1 tarefas | 0 concluidas | 1 falhou | 0 reaproveitadas" in result.stderr
    assert len(removed) == 1


def test_cli_orchestrate_removes_observer_after_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    run_id = _seed_cli_run(run_state=RunState.RUNNING, subtask_state=SubtaskState.PENDING)
    remove_observer = __import__("meister.cli", fromlist=["remove_event_observer"]).remove_event_observer
    removed = []

    def tracked_remove(callback):
        removed.append(callback)
        remove_observer(callback)

    monkeypatch.setattr("meister.cli.remove_event_observer", tracked_remove)

    async def execute(**_kwargs):
        log_event("orchestration_start", run_id=run_id, task_id="orchestrator")
        raise RuntimeError("bridge failed")

    with patch("meister.cli.HerdrEventBridge") as bridge_cls:
        bridge_cls.return_value.run_orchestration_cycle = AsyncMock(side_effect=execute)
        result = CliRunner().invoke(main, ["orchestrate"])

    assert result.exit_code != 0
    assert "Orchestration cycle encountered error: bridge failed" in result.stderr
    assert "Resumo do run cli-prog" in result.stderr
    assert len(removed) == 1


def test_cli_worker_command():
    runner = CliRunner()
    result = runner.invoke(main, ["worker", "--model", "tier_1c"])
    assert result.exit_code == 0
    assert "MeisterRouter worker starting with tier/model: tier_1c" in result.output
