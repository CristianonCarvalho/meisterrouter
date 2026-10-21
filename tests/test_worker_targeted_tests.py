from __future__ import annotations

import types
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from meister.config import _parse_config_dict, load_config
from meister.worker import (
    HARNESS_ANTIGRAVITY,
    HARNESS_CLAUDE,
    HARNESS_CODEX,
    HARNESS_COPILOT,
    HARNESS_KIRO,
    TARGETED_TESTS_PROMPT_PHRASE,
    HarnessWorker,
)


class CommandRecorder:
    """Dublê de processo que grava o comando executado sem chamar nenhuma CLI real."""

    def __init__(self, real_popen: Any) -> None:
        self.real_popen = real_popen
        self.recorded_commands: List[List[str]] = []

    def popen(self, cmd: Any, *args: Any, **kwargs: Any) -> Any:
        command = [str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd])]
        if command and Path(command[0]).name == "git":
            return self.real_popen(cmd, *args, **kwargs)

        self.recorded_commands.append(command)
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = []
        mock_process.poll.return_value = 0
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")
        return mock_process

    @property
    def last_command(self) -> List[str]:
        assert self.recorded_commands, "Nenhum comando foi gravado pelo dublê de CLI"
        return self.recorded_commands[-1]

    def extract_prompt(self, harness: str) -> str:
        cmd = self.last_command
        if harness in (HARNESS_ANTIGRAVITY, HARNESS_CLAUDE, HARNESS_COPILOT):
            idx = cmd.index("-p")
            return cmd[idx + 1]
        return cmd[-1]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> CommandRecorder:
    import subprocess

    real_popen = subprocess.Popen
    rec = CommandRecorder(real_popen)
    monkeypatch.setattr("subprocess.Popen", rec.popen)
    monkeypatch.setattr(
        "meister.worker.find_cli_binary",
        lambda harness: f"/mock/bin/{harness}",
    )
    monkeypatch.setattr("meister.worker.get_git_status_files", lambda cwd: set())
    return rec


def _config_with_targeted_tests(enabled: bool) -> Any:
    return _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "targeted_tests": enabled,
                "tier_order": [
                    {
                        "name": "tier-codex",
                        "harness": "codex",
                        "model": "test-model",
                    }
                ],
            },
        }
    )


def test_targeted_tests_enabled_prompt_ends_with_phrase(
    tmp_path: Path, recorder: CommandRecorder
) -> None:
    config = _config_with_targeted_tests(True)
    worker = HarnessWorker(
        model="tier-codex", cwd=str(tmp_path), config=config
    )
    assert worker.targeted_tests is True

    result = worker.run_task(
        task="Implement login feature", target_files=["auth.py"]
    )
    assert result["status"] == "done"

    prompt = recorder.extract_prompt(worker.harness)
    assert prompt.endswith(TARGETED_TESTS_PROMPT_PHRASE)
    expected_suffix = (
        "Implement all necessary changes directly into the project files. "
        "Ensure the codebase remains valid, clean, and passing tests.\n\n"
        + TARGETED_TESTS_PROMPT_PHRASE
    )
    assert prompt.endswith(expected_suffix)


def test_targeted_tests_disabled_prompt_is_identical_byte_by_byte(
    tmp_path: Path, recorder: CommandRecorder
) -> None:
    config = _config_with_targeted_tests(False)
    worker = HarnessWorker(
        model="tier-codex", cwd=str(tmp_path), config=config
    )
    assert worker.targeted_tests is False

    result = worker.run_task(
        task="Implement login feature", target_files=["auth.py"]
    )
    assert result["status"] == "done"

    prompt = recorder.extract_prompt(worker.harness)
    assert TARGETED_TESTS_PROMPT_PHRASE not in prompt
    expected_prompt = (
        "Task: Implement login feature\n\n"
        "Target files: auth.py\n\n"
        "Implement all necessary changes directly into the project files. "
        "Ensure the codebase remains valid, clean, and passing tests."
    )
    assert prompt == expected_prompt


def test_targeted_tests_config_none_does_not_add_phrase(
    tmp_path: Path, recorder: CommandRecorder
) -> None:
    worker = HarnessWorker(model=None, cwd=str(tmp_path), config=None)
    assert worker.config is None
    assert worker.targeted_tests is False

    result = worker.run_task(task="Implement without config")
    assert result["status"] == "done"

    prompt = recorder.extract_prompt(worker.harness)
    assert TARGETED_TESTS_PROMPT_PHRASE not in prompt
    expected_prompt = (
        "Task: Implement without config\n\n"
        "Implement all necessary changes directly into the project files. "
        "Ensure the codebase remains valid, clean, and passing tests."
    )
    assert prompt == expected_prompt


def test_targeted_tests_with_extra_instructions(
    tmp_path: Path, recorder: CommandRecorder
) -> None:
    config = _config_with_targeted_tests(True)
    worker = HarnessWorker(
        model="tier-codex", cwd=str(tmp_path), config=config
    )

    worker.run_task(
        task="Refactor auth",
        extra_instructions="Preserve backward compatibility with v1 API.",
    )

    prompt = recorder.extract_prompt(worker.harness)
    assert prompt.endswith(TARGETED_TESTS_PROMPT_PHRASE)

    instructions_idx = prompt.find(
        "Instructions: Preserve backward compatibility with v1 API."
    )
    closing_idx = prompt.find(
        "Implement all necessary changes directly into the project files. "
        "Ensure the codebase remains valid, clean, and passing tests."
    )
    verification_idx = prompt.find(TARGETED_TESTS_PROMPT_PHRASE)

    assert instructions_idx != -1
    assert closing_idx != -1
    assert verification_idx != -1
    assert instructions_idx < closing_idx < verification_idx


def test_targeted_tests_with_repair_in_description(
    tmp_path: Path, recorder: CommandRecorder
) -> None:
    config = _config_with_targeted_tests(True)
    worker = HarnessWorker(
        model="tier-codex", cwd=str(tmp_path), config=config
    )

    worker.run_task(
        task="## REPAIR: Fix AttributeError in meister/worker.py\nTraceback: ..."
    )

    prompt = recorder.extract_prompt(worker.harness)
    assert "Task: ## REPAIR: Fix AttributeError in meister/worker.py\nTraceback: ..." in prompt
    assert prompt.endswith(TARGETED_TESTS_PROMPT_PHRASE)


def test_targeted_tests_dummy_config_without_key_does_not_add_phrase(
    tmp_path: Path, recorder: CommandRecorder
) -> None:
    tier = types.SimpleNamespace(
        name="tier-dummy", harness="codex", model="test-model", effort=None
    )
    dummy_config = types.SimpleNamespace(
        workers=types.SimpleNamespace(tier_order=[tier], disabled=[])
    )
    worker = HarnessWorker(
        model="tier-dummy", cwd=str(tmp_path), config=dummy_config
    )
    assert worker.targeted_tests is False

    worker.run_task("Task with dummy config")
    prompt = recorder.extract_prompt(worker.harness)
    assert TARGETED_TESTS_PROMPT_PHRASE not in prompt


@pytest.mark.parametrize(
    ("harness_name", "tier_name"),
    [
        (HARNESS_CODEX, "tier-codex"),
        (HARNESS_ANTIGRAVITY, "tier-agy"),
        (HARNESS_CLAUDE, "tier-claude"),
        (HARNESS_COPILOT, "tier-copilot"),
        (HARNESS_KIRO, "tier-kiro"),
    ],
)
def test_targeted_tests_phrase_included_across_all_harnesses(
    tmp_path: Path, recorder: CommandRecorder, harness_name: str, tier_name: str
) -> None:
    config = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "targeted_tests": True,
                "tier_order": [
                    {
                        "name": tier_name,
                        "harness": harness_name,
                        "model": "test-model",
                    }
                ],
            },
        }
    )
    worker = HarnessWorker(model=tier_name, cwd=str(tmp_path), config=config)
    assert worker.targeted_tests is True

    worker.run_task("Generic harness task")
    prompt = recorder.extract_prompt(worker.harness)
    assert prompt.endswith(TARGETED_TESTS_PROMPT_PHRASE)


def test_targeted_tests_default_config_enables_targeted_tests(
    tmp_path: Path, recorder: CommandRecorder
) -> None:
    config = load_config()
    assert config.workers.targeted_tests is True
    worker = HarnessWorker(cwd=str(tmp_path), config=config)
    assert worker.targeted_tests is True

    worker.run_task("Default config task")
    prompt = recorder.extract_prompt(worker.harness)
    assert prompt.endswith(TARGETED_TESTS_PROMPT_PHRASE)
