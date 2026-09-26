import os
import json
import signal
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from click.testing import CliRunner
from meister.cli import main, is_pid_alive


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
    with patch("meister.cli.classify_task", return_value=mock_classification) as mock_cls:
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
            architect_pane_id="architect",
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


def test_cli_worker_command():
    runner = CliRunner()
    result = runner.invoke(main, ["worker", "--model", "luna"])
    assert result.exit_code == 0
    assert "MeisterRouter worker starting with tier/model: luna" in result.output
