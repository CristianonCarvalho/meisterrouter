from __future__ import annotations

import subprocess
from typing import Any

import pytest

from meister.config import MeisterConfig, WorkerTier, WorkersConfig, validate_config
from meister.engine.workers import WorkerSpawner
from meister.harness_effort import HARNESS_EFFORT_VALUES
from meister.i18n import t


def _spawner(harness: str = "opencode", model: str | None = None) -> WorkerSpawner:
    tier = WorkerTier(name="opencode_tier", harness=harness, model=model or "")
    return WorkerSpawner(MeisterConfig(workers=WorkersConfig(tier_order=[tier])))


@pytest.fixture(autouse=True)
def no_real_opencode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name == "opencode" else None)
    orig_run = subprocess.run

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        if cmd and "opencode" in str(cmd):
            raise AssertionError("opencode should not be called in tests")
        return orig_run(*args, **kwargs)

    monkeypatch.setattr("subprocess.run", fake_run)


def test_opencode_background_command_with_model() -> None:
    cmd = _spawner(model="opencode/big-pickle").resolve_command("opencode_tier", {"description": "faça X"})
    assert cmd == [
        "opencode", "run", "--auto",
        "-m", "opencode/big-pickle", "faça X",
    ]


def test_opencode_background_command_without_model() -> None:
    cmd = _spawner().resolve_command("opencode_tier", {"description": "faça X"})
    assert cmd == ["opencode", "run", "--auto", "faça X"]


def test_opencode_default_model_is_omitted() -> None:
    cmd = _spawner(model="default").resolve_command("opencode_tier", {"description": "faça X"})
    assert "-m" not in cmd
    assert cmd == ["opencode", "run", "--auto", "faça X"]
    assert cmd[-1] == "faça X"


def test_opencode_prompt_is_last_argument() -> None:
    cmd = _spawner(model="auto").resolve_command("opencode_tier", {"description": "-m custom --auto"})
    assert cmd[-1] == "-m custom --auto"
    assert cmd.index("-m") < len(cmd) - 2


def test_opencode_interactive_command() -> None:
    assert _spawner().resolve_command("opencode_tier", None) == ["opencode"]
    assert _spawner().resolve_command("opencode_tier", {}) == ["opencode"]
    assert _spawner(model="opencode/big-pickle").resolve_command("opencode_tier", None) == [
        "opencode", "-m", "opencode/big-pickle",
    ]
    assert _spawner(model="opencode/big-pickle").resolve_command("opencode_tier", {}) == [
        "opencode", "-m", "opencode/big-pickle",
    ]


def test_opencode_interactive_default_model_is_omitted() -> None:
    assert _spawner(model="default").resolve_command("opencode_tier", None) == ["opencode"]
    assert _spawner(model="default").resolve_command("opencode_tier", {}) == ["opencode"]


def test_opencode_task_context_command_priority() -> None:
    cmd = _spawner(model="opencode/big-pickle").resolve_command(
        "opencode_tier",
        {"command": ["custom", "worker", "bin"], "description": "ignored"},
    )
    assert cmd == ["custom", "worker", "bin"]


def test_opencode_extra_args_appended() -> None:
    cmd = _spawner(model="opencode/big-pickle").resolve_command(
        "opencode_tier",
        {"description": "faça X", "args": ["--extra", "arg"]},
    )
    assert cmd == [
        "opencode", "run", "--auto",
        "-m", "opencode/big-pickle", "faça X",
        "--extra", "arg",
    ]


def test_opencode_rejects_effort() -> None:
    assert "opencode" not in HARNESS_EFFORT_VALUES
    tier = WorkerTier(name="lane-opencode", harness="opencode", model="default", effort="high")
    config = MeisterConfig(workers=WorkersConfig(tier_order=[tier]))
    issues = [
        issue
        for issue in validate_config(config)
        if issue.level == "error" and issue.path.endswith(".effort")
    ]
    assert len(issues) == 1
    assert issues[0].path == "workers.tier_order[0].effort"
    assert issues[0].message == t(
        "tiers.unsupported_effort",
        lane="lane-opencode",
        harness="opencode",
    )
