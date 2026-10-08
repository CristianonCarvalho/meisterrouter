from pathlib import Path

from meister.config import load_config, validate_config


def test_repository_gate_commands_are_configured():
    config_path = Path(__file__).resolve().parents[1] / "meister.config.yaml"
    config = load_config(str(config_path))

    assert [command.name for command in config.gate.commands] == [
        "ruff",
        "mypy",
        "pytest",
    ]
    assert [command.run for command in config.gate.commands] == [
        "{python} -m ruff check .",
        "{python} -m mypy meister",
        "{python} -m pytest -q -p no:cacheprovider",
    ]
    assert all(command.required for command in config.gate.commands)
    assert [command.timeout_seconds for command in config.gate.commands] == [
        120,
        300,
        900,
    ]
    assert not [
        issue for issue in validate_config(config) if issue.level == "error"
    ]
