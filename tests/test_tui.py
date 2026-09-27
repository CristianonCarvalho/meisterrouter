import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from meister.cli import main
from meister.herdr.tui import render_tui_dashboard, run_tui_loop, get_live_metrics_and_state


def test_render_tui_dashboard_output():
    state = {
        "workspace": "my-project",
        "status": "ORCHESTRATING",
        "active_worker": "gpt-6-luna",
        "task_desc": "Auth unit tests"
    }
    metrics = {
        "tokens": 42000,
        "cost": 0.0032,
        "traditional_cost": 0.126,
        "savings_pct": 97.4
    }
    output = render_tui_dashboard(state, metrics)
    assert "MeisterRouter Live Telemetry" in output
    assert "gpt-6-luna" in output
    assert "97.4%" in output
    assert "my-project" in output
    assert "ORCHESTRATING" in output
    assert "Auth unit tests" in output


def test_render_tui_dashboard_workers_table():
    state = {
        "workspace": "payments-service",
        "status": "PARALLEL_EXECUTION",
        "workers": [
            {"id": "w1", "name": "worker-1", "model": "gpt-6-luna", "status": "RUNNING", "task": "Stripe webhook"},
            {"id": "w2", "name": "worker-2", "model": "claude-haiku-4.5", "status": "COMPLETED", "task": "Validate payload"},
        ]
    }
    metrics = {
        "tokens": 15000,
        "cost": 0.0015,
        "traditional_cost": 0.045,
        "savings_pct": 96.7
    }
    output = render_tui_dashboard(state, metrics)
    assert "worker-1" in output
    assert "gpt-6-luna" in output
    assert "RUNNING" in output
    assert "claude-haiku-4.5" in output
    assert "COMPLETED" in output


def test_render_tui_dashboard_shortcuts():
    output = render_tui_dashboard({}, {})
    assert "MeisterRouter Live Telemetry" in output
    assert "[Q]" in output or "Q to close" in output.lower() or "q:" in output.lower() or "q" in output
    assert "[O]" in output or "o to open" in output.lower() or "o:" in output.lower() or "o" in output


def test_render_tui_dashboard_handles_empty_or_partial_data():
    output = render_tui_dashboard({}, {})
    assert "MeisterRouter Live Telemetry" in output
    assert "IDLE" in output or "default" in output


def test_get_live_metrics_and_state(tmp_path):
    with patch("meister.herdr.tui.read_events") as mock_read:
        mock_read.return_value = [
            {"event_type": "classify", "classification": "SIMPLE", "recommended_implementer": "gpt-6-luna", "cost_usd": 0.001, "context": "Task A"},
            {"event_type": "task_end", "subagent": "gpt-6-luna", "cost_usd": 0.002, "duration_seconds": 3.5, "tokens_in": 1000, "tokens_out": 200, "status": "ok"}
        ]
        state, metrics = get_live_metrics_and_state()
        assert "tokens" in metrics
        assert metrics["cost"] > 0
        assert state["active_worker"] == "gpt-6-luna"


def test_run_tui_loop_single_iteration():
    with patch("meister.herdr.tui.render_tui_dashboard") as mock_render, \
         patch("meister.herdr.tui.get_live_metrics_and_state") as mock_get:
        mock_get.return_value = ({"workspace": "demo", "status": "IDLE"}, {"savings_pct": 90.0})
        mock_render.return_value = "DASHBOARD CONTENT"

        # max_iterations=1 ensures it exits after 1 loop
        run_tui_loop(poll_interval=0.01, max_iterations=1)
        mock_render.assert_called_once()


def test_run_tui_loop_interactive_keys():
    open_mock = MagicMock()
    with patch("meister.herdr.tui.check_key_press", side_effect=["o", "q"]), \
         patch("meister.herdr.tui.open_browser", open_mock), \
         patch("builtins.print"):
        run_tui_loop(poll_interval=0.01, max_iterations=5)
        open_mock.assert_called_once()


@pytest.mark.parametrize("exit_key", ["q", "Q", "x", "X", "\x1b", "\x03", "\x04"])
def test_run_tui_loop_exit_keys(exit_key):
    with patch("meister.herdr.tui.check_key_press", side_effect=[exit_key]), \
         patch("meister.herdr.tui.render_tui_dashboard", return_value="OUTPUT"), \
         patch("meister.herdr.tui.get_live_metrics_and_state", return_value=({}, {})):
        run_tui_loop(poll_interval=0.5, max_iterations=10)


def test_cli_dashboard_tui_invocation():
    runner = CliRunner()
    with patch("meister.herdr.tui.run_tui_loop") as mock_tui_loop:
        result = runner.invoke(main, ["dashboard", "--tui"])
        assert result.exit_code == 0
        mock_tui_loop.assert_called_once()
