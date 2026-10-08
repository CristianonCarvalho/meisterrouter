from pathlib import Path

from meister.config import load_config, validate_config


def test_repository_gate_commands_are_configured():
    config_path = Path(__file__).resolve().parents[1] / "meister.config.yaml"
    config = load_config(str(config_path))

    assert config.router.mode == "first"

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


def test_repository_scope_tolerated_files_include_defaults_and_tests():
    repository_root = Path(__file__).resolve().parents[1]
    config = load_config(str(repository_root / "meister.config.yaml"))
    default_config = load_config(str(repository_root / "meister" / "default_config.yaml"))

    assert "tests/**" in config.scope.tolerated_files
    assert set(default_config.scope.tolerated_files) <= set(config.scope.tolerated_files)
