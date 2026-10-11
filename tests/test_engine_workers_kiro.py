import pytest

from meister.config import MeisterConfig, WorkerTier, WorkersConfig
from meister.engine.workers import WorkerSpawner


def _spawner(harness="kiro-cli", model=None):
    tier = WorkerTier(name="kiro_tier", harness=harness, model=model)
    return WorkerSpawner(MeisterConfig(workers=WorkersConfig(tier_order=[tier])))


@pytest.fixture
def kiro_cli_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name == "kiro-cli" else None)


def test_kiro_background_command_with_model(kiro_cli_installed):
    cmd = _spawner(model="claude-sonnet-4.5").resolve_command("kiro_tier", {"description": "faça X"})
    assert cmd == [
        "kiro-cli", "chat", "--no-interactive", "--trust-all-tools",
        "--model", "claude-sonnet-4.5", "faça X",
    ]


def test_kiro_background_command_without_model(kiro_cli_installed):
    cmd = _spawner().resolve_command("kiro_tier", {"description": "faça X"})
    assert cmd == ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", "faça X"]


def test_kiro_default_model_is_omitted(kiro_cli_installed):
    cmd = _spawner(model="default").resolve_command("kiro_tier", {"description": "faça X"})
    assert "--model" not in cmd
    assert cmd[-1] == "faça X"


def test_kiro_prompt_is_last_argument(kiro_cli_installed):
    cmd = _spawner(model="auto").resolve_command("kiro_tier", {"description": "--model x"})
    assert cmd[-1] == "--model x"
    assert cmd.index("--model") < len(cmd) - 2


def test_kiro_interactive_command(kiro_cli_installed):
    assert _spawner().resolve_command("kiro_tier", None) == ["kiro-cli", "chat"]
    assert _spawner(model="glm-5").resolve_command("kiro_tier", {}) == ["kiro-cli", "chat", "--model", "glm-5"]


def test_kiro_alias_harness(kiro_cli_installed):
    cmd = _spawner(harness="kiro").resolve_command("kiro_tier", {"description": "p"})
    assert cmd[:2] == ["kiro-cli", "chat"]
    assert cmd[-1] == "p"


def test_kiro_falls_back_to_kiro_binary(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/kiro" if name == "kiro" else None)
    cmd = _spawner().resolve_command("kiro_tier", {"description": "p"})
    assert cmd[0] == "kiro"


def test_kiro_defaults_to_kiro_cli_when_none_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert _spawner().resolve_command("kiro_tier", None)[0] == "kiro-cli"
