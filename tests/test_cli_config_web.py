from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from click.testing import CliRunner
import pytest

import meister.cli
from meister.cli import main
from meister.dashboard.launcher import Outcome, resolve_project_root
from meister.i18n import reset_language_cache, t


@pytest.fixture(autouse=True)
def restore_cli(monkeypatch: pytest.MonkeyPatch):
    yield
    monkeypatch.delenv("MEISTER_LANG", raising=False)
    reset_language_cache()
    importlib.reload(meister.cli)


def test_config_web_arguments_forwarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    recorded: list[dict[str, Any]] = []

    def fake_ensure_dashboard(project_root: Any, config: Any = None, **kwargs: Any) -> Outcome:
        recorded.append({"project_root": project_root, "config": config, "kwargs": kwargs})
        return Outcome(kind="window", url="http://127.0.0.1:5050/config")

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web"])
    assert result.exit_code == 0

    assert len(recorded) == 1
    call = recorded[0]
    expected_root = resolve_project_root(Path.cwd())
    assert call["project_root"] == expected_root
    assert call["kwargs"]["view"] == "config"
    assert call["kwargs"]["require_current_code"] is True
    assert call["kwargs"]["url_only"] is False


def test_config_web_custom_config_path_forwarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    custom_cfg = tmp_path / "custom.yaml"
    custom_cfg.write_text(
        """
dashboard:
  open: never
  idle_exit_minutes: 45
""",
        encoding="utf-8",
    )

    recorded: list[dict[str, Any]] = []

    def fake_ensure_dashboard(project_root: Any, config: Any = None, **kwargs: Any) -> Outcome:
        recorded.append({"project_root": project_root, "config": config, "kwargs": kwargs})
        return Outcome(kind="url_only", url="http://127.0.0.1:5050/config")

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web", "-c", str(custom_cfg)])
    assert result.exit_code == 0

    assert len(recorded) == 1
    dashboard_cfg = recorded[0]["config"]
    assert getattr(dashboard_cfg, "open", None) == "never"
    assert getattr(dashboard_cfg, "idle_exit_minutes", None) == 45


def test_config_web_no_open_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    recorded: list[dict[str, Any]] = []

    def fake_ensure_dashboard(project_root: Any, config: Any = None, **kwargs: Any) -> Outcome:
        recorded.append({"project_root": project_root, "config": config, "kwargs": kwargs})
        return Outcome(kind="url_only", url="http://127.0.0.1:5050/config")

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web", "--no-open"])
    assert result.exit_code == 0

    assert len(recorded) == 1
    assert recorded[0]["kwargs"]["url_only"] is True
    assert "http://127.0.0.1:5050/config" in result.output


def test_config_web_outcome_window(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ensure_dashboard(*args: Any, **kwargs: Any) -> Outcome:
        return Outcome(kind="window", url="http://127.0.0.1:5050/config")

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web"])
    assert result.exit_code == 0
    assert "http://127.0.0.1:5050/config" in result.output
    assert result.output.strip() == t("cli.config.web.window", url="http://127.0.0.1:5050/config")


def test_config_web_outcome_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ensure_dashboard(*args: Any, **kwargs: Any) -> Outcome:
        return Outcome(kind="tab", url="http://127.0.0.1:5050/config")

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web"])
    assert result.exit_code == 0
    assert "http://127.0.0.1:5050/config" in result.output
    assert result.output.strip() == t("cli.config.web.tab", url="http://127.0.0.1:5050/config")


def test_config_web_outcome_url_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ensure_dashboard(*args: Any, **kwargs: Any) -> Outcome:
        return Outcome(kind="url_only", url="http://127.0.0.1:5050/config")

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web", "--no-open"])
    assert result.exit_code == 0
    assert result.output.strip() == "http://127.0.0.1:5050/config"


def test_config_web_outcome_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ensure_dashboard(*args: Any, **kwargs: Any) -> Outcome:
        return Outcome(kind="failed", url=None)

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web"])
    assert result.exit_code == 1
    assert t("cli.config.web.failed") in result.stderr


def test_config_web_fresh_server_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ensure_dashboard(*args: Any, **kwargs: Any) -> Outcome:
        return Outcome(kind="window", url="http://127.0.0.1:5050/config", fresh=True)

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web"])
    assert result.exit_code == 0
    assert t("cli.config.web.fresh_server") in result.output
    assert "http://127.0.0.1:5050/config" in result.output


def test_config_web_no_fresh_notice_when_not_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ensure_dashboard(*args: Any, **kwargs: Any) -> Outcome:
        return Outcome(kind="window", url="http://127.0.0.1:5050/config", fresh=False)

    monkeypatch.setattr(meister.cli, "ensure_dashboard", fake_ensure_dashboard)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "web"])
    assert result.exit_code == 0
    assert t("cli.config.web.fresh_server") not in result.output


def test_config_web_appears_in_help_both_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()

    # 1. English
    monkeypatch.setenv("MEISTER_LANG", "en")
    reset_language_cache()
    cli_en = importlib.reload(meister.cli)
    res_en = runner.invoke(cli_en.main, ["config", "--help"])
    assert res_en.exit_code == 0
    assert "web" in res_en.output
    assert "Open or print the URL of the web configuration editor." in res_en.output

    # 2. Portuguese
    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    reset_language_cache()
    cli_pt = importlib.reload(meister.cli)
    res_pt = runner.invoke(cli_pt.main, ["config", "--help"])
    assert res_pt.exit_code == 0
    assert "web" in res_pt.output
    assert "Abre ou imprime a URL do editor web de configuração." in res_pt.output


def test_config_web_help_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "web", "--help"])
    assert result.exit_code == 0
    assert "--config-path" in result.output or "-c" in result.output
    assert "--no-open" in result.output


def test_config_web_config_path_not_found(tmp_path: Path) -> None:
    runner = CliRunner()
    missing_file = tmp_path / "does_not_exist.yaml"
    result = runner.invoke(main, ["config", "web", "-c", str(missing_file)])
    assert result.exit_code == 2
