import pytest

from meister.config import load_config, validate_config


def _write_config(tmp_path, dashboard_yaml):
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text(dashboard_yaml, encoding="utf-8")
    return load_config(str(config_file))


def test_dashboard_defaults_when_section_is_absent(tmp_path):
    config = _write_config(tmp_path, "version: '1.0'\n")

    assert config.dashboard.open == "auto"
    assert config.dashboard.idle_exit_minutes == 30
    assert config.dashboard.window.width == 1280
    assert config.dashboard.window.height == 800
    assert not [
        issue for issue in validate_config(config)
        if issue.level == "error" and issue.path.startswith("dashboard")
    ]


def test_dashboard_accepts_custom_values(tmp_path):
    config = _write_config(
        tmp_path,
        """
dashboard:
  open: app
  idle_exit_minutes: 45
  window:
    width: 1440
    height: 900
""",
    )

    assert config.dashboard.open == "app"
    assert config.dashboard.idle_exit_minutes == 45
    assert config.dashboard.window.width == 1440
    assert config.dashboard.window.height == 900
    assert not [
        issue for issue in validate_config(config)
        if issue.level == "error" and issue.path.startswith("dashboard")
    ]


@pytest.mark.parametrize("value", ["invalid", "null", "1", "true"])
def test_invalid_dashboard_open_reports_config_issue(tmp_path, value):
    config = _write_config(tmp_path, f"dashboard:\n  open: {value}\n")

    assert any(
        issue.level == "error" and issue.path == "dashboard.open"
        for issue in validate_config(config)
    )
    assert config.dashboard.open == "auto"


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '"thirty"', "null"])
def test_invalid_dashboard_idle_exit_minutes_reports_config_issue(tmp_path, value):
    config = _write_config(tmp_path, f"dashboard:\n  idle_exit_minutes: {value}\n")

    assert any(
        issue.level == "error" and issue.path == "dashboard.idle_exit_minutes"
        for issue in validate_config(config)
    )
    assert config.dashboard.idle_exit_minutes == 30


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("width", "0"),
        ("width", "-1"),
        ("width", "true"),
        ("width", "1.5"),
        ("width", '"wide"'),
        ("width", "null"),
        ("height", "0"),
        ("height", "-1"),
        ("height", "false"),
        ("height", "1.5"),
        ("height", '"tall"'),
        ("height", "null"),
    ],
)
def test_invalid_dashboard_window_dimensions_report_config_issue(tmp_path, field, value):
    config = _write_config(
        tmp_path,
        f"dashboard:\n  window:\n    {field}: {value}\n",
    )

    assert any(
        issue.level == "error" and issue.path == f"dashboard.window.{field}"
        for issue in validate_config(config)
    )
    default_value = 1280 if field == "width" else 800
    assert getattr(config.dashboard.window, field) == default_value


def test_invalid_dashboard_objects_report_config_issues(tmp_path):
    config = _write_config(
        tmp_path,
        "dashboard: invalid\n",
    )

    assert any(
        issue.level == "error" and issue.path == "dashboard"
        for issue in validate_config(config)
    )
    assert config.dashboard.open == "auto"
