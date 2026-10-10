"""Teste ponta a ponta do orchestrate com ``runtime.host: process`` e harness de mentira, sem Herdr."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from meister.cli import main

FAKE_HARNESS = Path(__file__).resolve().parent / "fixtures" / "fake_harness.py"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_BIN_DIRS = ("/usr/bin", "/bin")
_BINARY_BY_HARNESS = {"codex": "codex", "copilot": "copilot"}


def _task(task_id: str, name: str) -> dict:
    return {
        "id": task_id,
        "description": f"Create {name} FAKE_WRITE={name}.txt",
        "target_files": [f"{name}.txt"],
        "depends_on": [],
    }


TWO_TASK_PLAN = json.dumps([_task("task-alpha", "alpha"), _task("task-beta", "beta")])


@dataclass(frozen=True)
class Tier:
    name: str
    harness: str
    model: str
    mode: str


@dataclass
class E2EEnv:
    repo: Path
    bin_dir: Path
    log_dir: Path
    tmp_path: Path

    def install_cli(self, harness: str, mode: str) -> None:
        name = _BINARY_BY_HARNESS[harness]
        if sys.platform == "win32":
            script = self.bin_dir / f"{name}.cmd"
            body = f'@echo off\n"{sys.executable}" "{FAKE_HARNESS}" {mode} %*\n'
        else:
            script = self.bin_dir / name
            body = (
                "#!/bin/sh\n"
                f"exec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_HARNESS))} "
                f'{mode} "$@"\n'
            )
        script.write_text(body, encoding="utf-8")
        if sys.platform != "win32":
            script.chmod(0o755)

    def orchestrate(self, tiers: list[Tier], plan: str):
        lines = [
            'version: "1.0"',
            "router:",
            "  mode: first",
            "runtime:",
            "  host: process",
            "concurrency:",
            '  isolation_mode: "git_worktree"',
            "gate:",
            "  allow_unverified: true",
            "workers:",
            "  tier_order:",
        ]
        for tier in tiers:
            self.install_cli(tier.harness, tier.mode)
            lines.append(
                f"    - {{name: {tier.name}, harness: {tier.harness}, "
                f"model: {tier.model}, max_retries: 0}}"
            )
        config = self.tmp_path / "config.yaml"
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return CliRunner().invoke(
            main,
            ["orchestrate", "-c", str(config), "--task", plan, "--quiet", "--no-open"],
        )

    def worker_logs(self) -> list[Path]:
        return sorted((self.log_dir / "workers").glob("*.log"))


def _journal(env: E2EEnv) -> list[dict]:
    lines = (env.log_dir / "orchestration_log.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout


@pytest.fixture
def e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> E2EEnv:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_dir = tmp_path / "logs"
    # PATH sem os CLIs reais de IA: só os wrappers falsos e o sistema.
    # No Windows o PATH real fica (o git precisa dele); o wrapper vem primeiro.
    system_path = os.pathsep.join(SYSTEM_BIN_DIRS) if sys.platform != "win32" else os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), system_path]))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MEISTER_WORKTREES_DIR", str(tmp_path / "worktrees"))
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "meister.db"))
    monkeypatch.setenv("PYTHONPATH", str(PROJECT_ROOT))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@test.com")
    (repo / "app.py").write_text("APP = True\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial")
    monkeypatch.chdir(repo)
    env = E2EEnv(repo, bin_dir, log_dir, tmp_path)
    monkeypatch.setenv("MEISTER_CONFIG_PATH", str(tmp_path / "config.yaml"))
    return env


def _worktrees(repo: Path) -> list[Path]:
    return [
        Path(line.removeprefix("worktree ")).resolve()
        for line in _git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]


def test_two_tasks_integrate_and_keep_worker_logs(e2e: E2EEnv):
    result = e2e.orchestrate([Tier("tier_1", "codex", "gpt-6-luna", "write")], TWO_TASK_PLAN)

    assert result.exit_code == 0, result.output
    tracked = _git(e2e.repo, "ls-tree", "--name-only", "main").split()
    assert {"alpha.txt", "beta.txt"} <= set(tracked)

    logs = e2e.worker_logs()
    assert len(logs) == 2
    contents = {log.name: log.read_text(encoding="utf-8") for log in logs}
    assert any("fake harness wrote alpha.txt" in text for text in contents.values())
    assert any("fake harness wrote beta.txt" in text for text in contents.values())

    # Worktrees dos workers já foram removidos; os logs continuam existindo.
    assert _worktrees(e2e.repo) == [e2e.repo.resolve()]
    assert all(log.is_file() for log in logs)


def test_worker_exit_without_result_is_reported(e2e: E2EEnv):
    result = e2e.orchestrate(
        [Tier("tier_1", "codex", "gpt-6-luna", "silent")], json.dumps([_task("task-alpha", "alpha")])
    )

    assert result.exit_code != 0
    assert e2e.worker_logs()
    events = _journal(e2e)
    exit_codes = [e["exit_code"] for e in events if e.get("task_id") == "task-alpha" and "exit_code" in e]
    assert 1 in exit_codes


def test_quota_message_falls_back_to_next_tier(e2e: E2EEnv):
    result = e2e.orchestrate(
        [
            Tier("tier_1", "codex", "gpt-6-luna", "quota"),
            Tier("tier_1b", "copilot", "claude-haiku-5.5", "write"),
        ],
        json.dumps([_task("task-alpha", "alpha")]),
    )

    assert result.exit_code == 0, result.output
    assert "alpha.txt" in _git(e2e.repo, "ls-tree", "--name-only", "main").split()
    texts = {log.name: log.read_text(encoding="utf-8") for log in e2e.worker_logs()}
    quota_logs = [name for name, text in texts.items() if "exceeded your current quota" in text]
    write_logs = [name for name, text in texts.items() if "fake harness wrote alpha.txt" in text]
    assert quota_logs and write_logs
    assert quota_logs != write_logs
