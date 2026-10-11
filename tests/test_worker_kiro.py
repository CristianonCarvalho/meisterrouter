from __future__ import annotations

import json
import sys

import pytest

from tests.fake_cli import make_fake_cli
from meister.config import _parse_config_dict
from meister.harness_effort import HARNESS_EFFORT_VALUES
from meister.worker import (
    HARNESS_KIRO,
    HarnessWorker,
    build_harness_command,
    find_cli_binary,
    resolve_worker_harness_and_model,
)


def _kiro_config(harness: str = "kiro-cli", model: str = "claude-sonnet-4.5", effort=None):
    tier = {"name": "lane-kiro", "harness": harness, "model": model}
    if effort is not None:
        tier["effort"] = effort
    return _parse_config_dict({"router": {"mode": "first"}, "workers": {"tier_order": [tier]}})


@pytest.fixture
def kiro_effort(monkeypatch):
    # O mapa de esforços por harness é mantido fora do worker; aqui só o comando importa.
    for name in ("kiro", HARNESS_KIRO):
        monkeypatch.setitem(HARNESS_EFFORT_VALUES, name, ("low", "medium", "high"))


@pytest.mark.parametrize("alias", ["kiro", "kiro-cli", "KIRO"])
def test_kiro_aliases_resolve_to_same_harness(alias):
    config = _kiro_config(harness=alias)
    assert resolve_worker_harness_and_model("lane-kiro", config=config) == (
        HARNESS_KIRO,
        "claude-sonnet-4.5",
    )


def test_kiro_command_without_model_or_effort():
    assert build_harness_command(HARNESS_KIRO, "/cli", None, "prompt", "/work") == [
        "/cli",
        "chat",
        "--no-interactive",
        "--trust-all-tools",
        "prompt",
    ]
    assert build_harness_command(HARNESS_KIRO, "/cli", "default", "prompt", "/work") == [
        "/cli",
        "chat",
        "--no-interactive",
        "--trust-all-tools",
        "prompt",
    ]


def test_kiro_command_with_model_and_effort(kiro_effort):
    assert build_harness_command(
        HARNESS_KIRO, "/cli", "claude-sonnet-4.5", "prompt", "/work", effort="high"
    ) == [
        "/cli",
        "chat",
        "--no-interactive",
        "--trust-all-tools",
        "--model",
        "claude-sonnet-4.5",
        "--effort",
        "high",
        "prompt",
    ]
    assert build_harness_command(
        HARNESS_KIRO, "/cli", "claude-sonnet-4.5", "prompt", "/work"
    ) == [
        "/cli",
        "chat",
        "--no-interactive",
        "--trust-all-tools",
        "--model",
        "claude-sonnet-4.5",
        "prompt",
    ]


def test_find_cli_binary_finds_fake_kiro_cli_on_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    executable = make_fake_cli(bin_dir, "kiro-cli", "print('ok')\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    if sys.platform == "win32":
        assert executable.name == "kiro-cli.cmd"

    found = find_cli_binary(HARNESS_KIRO)

    assert found is not None
    assert found.casefold() == str(executable).casefold()


@pytest.mark.parametrize("effort", ["high", None])
def test_worker_runs_fake_kiro_cli(tmp_path, monkeypatch, kiro_effort, effort):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    argv_file = tmp_path / "argv.json"
    executable = make_fake_cli(
        tmp_path / "bin",
        "kiro-cli",
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "pathlib.Path('kiro_out.txt').write_text('feito', encoding='utf-8')\n"
        "print('resposta final')\n"
        "sys.stderr.write('[tool] status: Completed\\n')\n",
    )
    monkeypatch.setattr("meister.worker.find_cli_binary", lambda _harness: str(executable))

    result = HarnessWorker(
        model="lane-kiro", cwd=str(work_dir), config=_kiro_config(harness="kiro", effort=effort)
    ).run_task("test task")

    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv[:3] == ["chat", "--no-interactive", "--trust-all-tools"]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4.5"
    if effort:
        assert argv[argv.index("--effort") + 1] == "high"
    else:
        assert "--effort" not in argv
    assert argv[-1].startswith("Task: test task")
    assert (work_dir / "kiro_out.txt").read_text(encoding="utf-8") == "feito"
    assert result["exit_code"] == 0
    assert result["harness"] == HARNESS_KIRO
    assert "resposta final" in result["output"]
