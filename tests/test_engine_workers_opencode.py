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
    assert "opencode" in HARNESS_EFFORT_VALUES
    assert HARNESS_EFFORT_VALUES["opencode"] == ()
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


def test_opencode_resolve_command_with_cwd_and_description() -> None:
    cmd = _spawner(model="opencode/big-pickle").resolve_command(
        "opencode_tier", {"description": "faça X"}, cwd="/x"
    )
    assert cmd == [
        "opencode", "run", "--auto", "--dir", "/x",
        "-m", "opencode/big-pickle", "faça X",
    ]


def test_opencode_resolve_command_with_cwd_without_model() -> None:
    cmd = _spawner().resolve_command("opencode_tier", {"description": "faça X"}, cwd="/x")
    assert cmd == ["opencode", "run", "--auto", "--dir", "/x", "faça X"]


def test_opencode_resolve_command_without_cwd() -> None:
    cmd_none = _spawner().resolve_command("opencode_tier", {"description": "faça X"}, cwd=None)
    cmd_empty = _spawner().resolve_command("opencode_tier", {"description": "faça X"}, cwd="")
    cmd_omitted = _spawner().resolve_command("opencode_tier", {"description": "faça X"})
    for cmd in (cmd_none, cmd_empty, cmd_omitted):
        assert "--dir" not in cmd
        assert cmd == ["opencode", "run", "--auto", "faça X"]


def test_opencode_interactive_mode_ignores_cwd() -> None:
    assert _spawner().resolve_command("opencode_tier", None, cwd="/x") == ["opencode"]
    assert _spawner().resolve_command("opencode_tier", {}, cwd="/x") == ["opencode"]
    assert _spawner(model="opencode/big-pickle").resolve_command("opencode_tier", None, cwd="/x") == [
        "opencode", "-m", "opencode/big-pickle",
    ]
    assert _spawner(model="opencode/big-pickle").resolve_command("opencode_tier", {}, cwd="/x") == [
        "opencode", "-m", "opencode/big-pickle",
    ]


def test_other_harness_ignores_cwd() -> None:
    spawner_kiro = _spawner(harness="kiro-cli", model="default")
    cmd_without_cwd = spawner_kiro.resolve_command("opencode_tier", {"description": "faça X"})
    cmd_with_cwd = spawner_kiro.resolve_command("opencode_tier", {"description": "faça X"}, cwd="/x")
    assert cmd_with_cwd == cmd_without_cwd
    assert "--dir" not in cmd_with_cwd

    interactive_without = spawner_kiro.resolve_command("opencode_tier", None)
    interactive_with = spawner_kiro.resolve_command("opencode_tier", None, cwd="/x")
    assert interactive_with == interactive_without
    assert "--dir" not in interactive_with


@pytest.mark.asyncio
async def test_opencode_spawn_worker_pane_passes_cwd() -> None:
    from unittest.mock import AsyncMock, MagicMock
    spawner = _spawner(model="opencode/big-pickle")
    mock_client = MagicMock()
    mock_client.split_pane = AsyncMock(return_value="pane_1")
    spawner.herdr_client = mock_client

    pane_id, tier = await spawner.spawn_worker_pane(
        "opencode_tier",
        task_context={"description": "faça X"},
        cwd="/workdir",
    )
    mock_client.split_pane.assert_awaited_once_with(
        direction="right",
        command=["opencode", "run", "--auto", "--dir", "/workdir", "-m", "opencode/big-pickle", "faça X"],
        split_ratio=0.5,
        cwd="/workdir",
    )


@pytest.mark.asyncio
async def test_opencode_spawn_worker_tab_passes_cwd() -> None:
    from unittest.mock import AsyncMock, MagicMock
    spawner = _spawner(model="opencode/big-pickle")
    mock_client = MagicMock()
    mock_client.create_tab = AsyncMock(return_value=("tab_1", "pane_1"))
    mock_client.wait_pane_ready = AsyncMock()
    mock_client.send_text = AsyncMock()
    spawner.herdr_client = mock_client

    tab_id, pane_id, tier = await spawner.spawn_worker_tab(
        "opencode_tier",
        task_context={"description": "faça X"},
        cwd="/workdir",
    )
    mock_client.send_text.assert_awaited_once_with(
        "pane_1",
        "opencode run --auto --dir /workdir -m opencode/big-pickle faça X\n",
    )

