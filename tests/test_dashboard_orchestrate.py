from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from click.testing import CliRunner

import meister.cli as cli
from meister.cli import main


class _ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


def _prepare_orchestrate(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(dashboard=object(), retry=SimpleNamespace(pane_lost_attempts=0))
    monkeypatch.setattr(cli, "_load_cli_config", lambda config_path: config)

    import meister.config

    monkeypatch.setattr(meister.config, "validate_config", lambda cfg: [])
    monkeypatch.setattr(cli, "select_host", lambda cfg, socket_path=None: None)
    bridge = Mock()
    bridge.run_orchestration_cycle = AsyncMock(return_value=True)
    monkeypatch.setattr(cli, "HerdrEventBridge", lambda config, host: bridge)
    monkeypatch.setattr(cli.threading, "Thread", _ImmediateThread, raising=False)
    return config


def test_orchestrate_starts_dashboard_once_and_prints_timeline_url(
    monkeypatch, tmp_path
):
    config = _prepare_orchestrate(monkeypatch, tmp_path)
    ensure = Mock(return_value=SimpleNamespace(
        kind="url_only",
        url="http://127.0.0.1:51423/timeline",
    ))
    monkeypatch.setattr(cli, "ensure_dashboard", ensure, raising=False)

    result = CliRunner().invoke(main, ["orchestrate", "--quiet"])

    assert result.exit_code == 0
    ensure.assert_called_once_with(tmp_path.resolve(), config.dashboard)
    assert "Timeline: http://127.0.0.1:51423/timeline" in result.output


def test_orchestrate_no_open_skips_dashboard_launch(monkeypatch, tmp_path):
    _prepare_orchestrate(monkeypatch, tmp_path)
    ensure = Mock()
    monkeypatch.setattr(cli, "ensure_dashboard", ensure, raising=False)

    result = CliRunner().invoke(main, ["orchestrate", "--quiet", "--no-open"])

    assert result.exit_code == 0
    ensure.assert_not_called()


def test_orchestrate_dashboard_failure_does_not_change_exit_code(
    monkeypatch, tmp_path
):
    _prepare_orchestrate(monkeypatch, tmp_path)
    ensure = Mock(side_effect=RuntimeError("launcher failed"))
    monkeypatch.setattr(cli, "ensure_dashboard", ensure, raising=False)

    result = CliRunner().invoke(main, ["orchestrate", "--quiet"])

    assert result.exit_code == 0
    ensure.assert_called_once()


def test_dashboard_idle_exit_minutes_is_passed_to_server(monkeypatch):
    from meister.dashboard import server

    start_server = Mock()
    monkeypatch.setattr(server, "start_server", start_server)

    result = CliRunner().invoke(main, ["dashboard", "--idle-exit-minutes", "1"])

    assert result.exit_code == 0
    start_server.assert_called_once_with(
        host="127.0.0.1",
        port=5050,
        log_dir=None,
        idle_exit_minutes=1,
    )


def test_config_show_includes_dashboard_settings(monkeypatch, tmp_path):
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text("dashboard:\n  open: never\n  idle_exit_minutes: 7\n")

    result = CliRunner().invoke(
        main, ["config", "show", "--json", "--config-path", str(config_file)]
    )

    assert result.exit_code == 0
    assert '"dashboard"' in result.output
    assert '"open": "never"' in result.output
    assert '"idle_exit_minutes": 7' in result.output
