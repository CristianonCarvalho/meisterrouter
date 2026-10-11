"""Teste ponta a ponta do orchestrate com ``runtime.host: tmux``, harness de mentira e servidor tmux isolado."""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.hosts import TmuxHost
from tests.test_e2e_process_host import (
    PROJECT_ROOT,
    SYSTEM_BIN_DIRS,
    TWO_TASK_PLAN,
    E2EEnv,
    Tier,
    _git,
    _journal,
    _task,
    _worktrees,
)

_NO_TMUX = sys.platform == "win32" or shutil.which("tmux") is None
pytestmark = pytest.mark.skipif(_NO_TMUX, reason="requer o binário tmux (fora do Windows)")

# Janela criada junto com a sessão pelo host; não é de worker.
_SESSION_WINDOW = "meister"


def _socket_name() -> str:
    return f"meister-test-{os.getpid()}"


def _raw_tmux(*args: str) -> str:
    result = subprocess.run(
        ["tmux", "-L", _socket_name(), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TMUX": ""},
    )
    return result.stdout if result.returncode == 0 else ""


class TmuxE2EEnv(E2EEnv):
    def orchestrate(self, tiers: list[Tier], plan: str):
        lines = [
            'version: "1.0"',
            "router:",
            "  mode: first",
            "runtime:",
            "  host: tmux",
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

    def leftover_windows(self) -> list[str]:
        """Nomes das janelas de worker ainda abertas no servidor isolado (vazio se o servidor já saiu)."""
        names = _raw_tmux("list-windows", "-a", "-F", "#{window_name}").split()
        return [name for name in names if name != _SESSION_WINDOW]


@pytest.fixture
def e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TmuxE2EEnv]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # O PATH do teste não tem os CLIs reais de IA; o tmux entra por um link no diretório de wrappers.
    real_tmux = shutil.which("tmux")
    assert real_tmux is not None
    (bin_dir / "tmux").symlink_to(real_tmux)
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), *SYSTEM_BIN_DIRS]))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MEISTER_WORKTREES_DIR", str(tmp_path / "worktrees"))
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "meister.db"))
    monkeypatch.setenv("PYTHONPATH", str(PROJECT_ROOT))
    monkeypatch.setenv("MEISTER_CONFIG_PATH", str(tmp_path / "config.yaml"))
    # O select_host cria o TmuxHost sem socket; aqui ele passa a usar o servidor isolado do teste.
    monkeypatch.setattr(
        "meister.hosts.select.TmuxHost", functools.partial(TmuxHost, socket_name=_socket_name())
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@test.com")
    (repo / "app.py").write_text("APP = True\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial")
    monkeypatch.chdir(repo)
    try:
        yield TmuxE2EEnv(repo, bin_dir, log_dir, tmp_path)
    finally:
        _raw_tmux("kill-server")


def test_two_tasks_integrate_and_leave_no_windows(e2e: TmuxE2EEnv):
    result = e2e.orchestrate([Tier("tier_1", "codex", "gpt-6-luna", "write")], TWO_TASK_PLAN)

    assert result.exit_code == 0, result.output
    tracked = _git(e2e.repo, "ls-tree", "--name-only", "main").split()
    assert {"alpha.txt", "beta.txt"} <= set(tracked)
    assert _git(e2e.repo, "show", "main:alpha.txt") == "written by fake harness: alpha.txt\n"

    assert _worktrees(e2e.repo) == [e2e.repo.resolve()]
    assert e2e.leftover_windows() == []


def test_worker_exit_code_is_recorded_by_shell_line(e2e: TmuxE2EEnv):
    result = e2e.orchestrate(
        [Tier("tier_1", "codex", "gpt-6-luna", "silent")], json.dumps([_task("task-alpha", "alpha")])
    )

    assert result.exit_code != 0
    events = _journal(e2e)
    exit_codes = [e["exit_code"] for e in events if e.get("task_id") == "task-alpha" and "exit_code" in e]
    assert 1 in exit_codes
    assert e2e.leftover_windows() == []


def test_quota_message_falls_back_to_next_tier(e2e: TmuxE2EEnv):
    result = e2e.orchestrate(
        [
            Tier("tier_1", "codex", "gpt-6-luna", "quota"),
            Tier("tier_1b", "copilot", "claude-haiku-5.5", "write"),
        ],
        json.dumps([_task("task-alpha", "alpha")]),
    )

    assert result.exit_code == 0, result.output
    assert "alpha.txt" in _git(e2e.repo, "ls-tree", "--name-only", "main").split()
    assert any(event.get("event_type") == "quota_error" for event in _journal(e2e))
    assert e2e.leftover_windows() == []
