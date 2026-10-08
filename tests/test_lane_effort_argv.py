from __future__ import annotations

import json
import sys

import pytest

from meister.config import _parse_config_dict
from meister.worker import (
    HARNESS_ANTIGRAVITY,
    HARNESS_CLAUDE,
    HARNESS_CODEX,
    HARNESS_COPILOT,
    HarnessWorker,
    build_harness_command,
)


@pytest.mark.parametrize(
    ("harness", "with_effort", "without_effort"),
    [
        (
            HARNESS_COPILOT,
            [
                "/cli",
                "-p",
                "prompt",
                "--allow-all",
                "--no-ask-user",
                "--model",
                "test-model",
                "--reasoning-effort",
                "high",
            ],
            [
                "/cli",
                "-p",
                "prompt",
                "--allow-all",
                "--no-ask-user",
                "--model",
                "test-model",
            ],
        ),
        (
            HARNESS_CLAUDE,
            [
                "/cli",
                "--dangerously-skip-permissions",
                "--model",
                "test-model",
                "--effort",
                "high",
                "--output-format",
                "json",
                "-p",
                "prompt",
            ],
            [
                "/cli",
                "--dangerously-skip-permissions",
                "--model",
                "test-model",
                "--output-format",
                "json",
                "-p",
                "prompt",
            ],
        ),
        (
            HARNESS_ANTIGRAVITY,
            [
                "/cli",
                "--dangerously-skip-permissions",
                "--model",
                "test-model",
                "--effort",
                "high",
                "--output-format",
                "json",
                "-p",
                "prompt",
            ],
            [
                "/cli",
                "--dangerously-skip-permissions",
                "--model",
                "test-model",
                "--output-format",
                "json",
                "-p",
                "prompt",
            ],
        ),
        (
            HARNESS_CODEX,
            [
                "/cli",
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "-m",
                "test-model",
                "-c",
                'model_reasoning_effort="high"',
                "-C",
                "/work",
                "prompt",
            ],
            [
                "/cli",
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "-m",
                "test-model",
                "-C",
                "/work",
                "prompt",
            ],
        ),
    ],
)
def test_build_harness_command_effort_and_unchanged_default(
    harness, with_effort, without_effort
):
    assert build_harness_command(
        harness, "/cli", "test-model", "prompt", "/work", effort="high"
    ) == with_effort
    assert build_harness_command(
        harness, "/cli", "test-model", "prompt", "/work"
    ) == without_effort


@pytest.mark.parametrize(
    ("effort", "expected_flag"),
    [("high", "--reasoning-effort"), (None, None)],
)
def test_worker_passes_lane_effort_to_fake_harness(
    tmp_path, monkeypatch, effort, expected_flag
):
    argv_file = tmp_path / "argv.json"
    executable = tmp_path / "fake-harness"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr("meister.worker.find_cli_binary", lambda _harness: str(executable))

    tier = {"name": "lane-one", "harness": "copilot", "model": "test-model"}
    if effort is not None:
        tier["effort"] = effort
    config = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {"tier_order": [tier]},
        }
    )

    result = HarnessWorker(model="lane-one", cwd=str(tmp_path), config=config).run_task(
        "test task"
    )

    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    if expected_flag:
        assert argv[argv.index(expected_flag) + 1] == "high"
    else:
        assert "--reasoning-effort" not in argv
    assert result["exit_code"] == 0


def test_worker_rejects_effort_unsupported_by_lane_harness(tmp_path, monkeypatch):
    monkeypatch.setattr("meister.worker.find_cli_binary", lambda _harness: "/unused")
    config = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "tier_order": [
                    {
                        "name": "lane-one",
                        "harness": "codex",
                        "model": "test-model",
                        "effort": "max",
                    }
                ]
            },
        }
    )

    with pytest.raises(ValueError) as error:
        HarnessWorker(model="lane-one", cwd=str(tmp_path), config=config)

    message = str(error.value)
    assert "lane-one" in message
    assert "codex" in message
    assert "max" in message
    assert "minimal, low, medium, high, xhigh" in message
