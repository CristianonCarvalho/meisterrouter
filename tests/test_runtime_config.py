from __future__ import annotations

import pytest

from meister.config import MeisterConfig, _parse_config_dict, load_config, validate_config


def test_runtime_host_defaults_to_auto():
    assert MeisterConfig().runtime.host == "auto"
    assert _parse_config_dict({}).runtime.host == "auto"


@pytest.mark.parametrize("host", ["auto", "process", "herdr", "tmux"])
def test_runtime_host_accepts_valid_values(host):
    config = _parse_config_dict({"runtime": {"host": host}})

    assert config.runtime.host == host
    assert not [issue for issue in validate_config(config) if issue.path == "runtime.host"]


@pytest.mark.parametrize("host", ["screen", "", "HERDR", 3])
def test_runtime_host_rejects_invalid_values(host):
    config = _parse_config_dict({"runtime": {"host": host}})

    issues = [issue for issue in validate_config(config) if issue.path == "runtime.host"]
    assert len(issues) == 1
    assert issues[0].level == "error"
    assert "runtime.host" in issues[0].message
    assert config.runtime.host == "auto"


def test_runtime_host_validation_catches_directly_built_config():
    config = MeisterConfig()
    config.runtime.host = "screen"

    issues = [issue for issue in validate_config(config) if issue.path == "runtime.host"]
    assert len(issues) == 1
    assert issues[0].level == "error"


def test_project_config_overrides_runtime_host(tmp_path):
    config_path = tmp_path / "meister.config.yaml"
    config_path.write_text("runtime:\n  host: tmux\n", encoding="utf-8")

    config = load_config(str(config_path))

    assert config.runtime.host == "tmux"
    assert config.concurrency.max_parallel_workers == 4
    assert not [issue for issue in validate_config(config) if issue.level == "error"]
