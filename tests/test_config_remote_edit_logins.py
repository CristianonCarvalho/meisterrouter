import pathlib

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.config import load_config, validate_config

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "meister.config.example.yaml"


def _write_config(tmp_path, dashboard_yaml):
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text(dashboard_yaml, encoding="utf-8")
    return load_config(str(config_file))


def _dashboard_errors(config):
    return [
        issue for issue in validate_config(config)
        if issue.level == "error" and issue.path.startswith("dashboard")
    ]


def test_remote_edit_logins_defaults_to_empty_list(tmp_path):
    config = _write_config(tmp_path, "version: '1.0'\n")

    assert config.dashboard.remote_edit_logins == []
    assert _dashboard_errors(config) == []


def test_remote_edit_logins_reads_valid_list(tmp_path):
    config = _write_config(
        tmp_path,
        """
dashboard:
  remote_edit_logins:
    - Ana@example.com
    - "  bruno@example.com  "
""",
    )

    assert config.dashboard.remote_edit_logins == ["Ana@example.com", "bruno@example.com"]
    assert _dashboard_errors(config) == []


@pytest.mark.parametrize("value", ['"ana@example.com"', "true", "1", "{a: b}"])
def test_remote_edit_logins_must_be_a_list(tmp_path, value):
    config = _write_config(tmp_path, f"dashboard:\n  remote_edit_logins: {value}\n")

    assert any(
        issue.level == "error" and issue.path == "dashboard.remote_edit_logins"
        for issue in validate_config(config)
    )
    assert config.dashboard.remote_edit_logins == []


@pytest.mark.parametrize("item", ["1", "true", "null", '""', '"   "', "[ana]"])
def test_remote_edit_logins_items_must_be_non_empty_text(tmp_path, item):
    config = _write_config(
        tmp_path,
        f"dashboard:\n  remote_edit_logins:\n    - ana@example.com\n    - {item}\n",
    )

    assert any(
        issue.level == "error" and issue.path == "dashboard.remote_edit_logins"
        for issue in validate_config(config)
    )
    assert config.dashboard.remote_edit_logins == []


def test_cli_config_validate_accepts_example(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    result = CliRunner().invoke(main, ["config", "validate", "--config-path", str(EXAMPLE)])

    assert result.exit_code == 0
    assert "ERROR" not in result.output
