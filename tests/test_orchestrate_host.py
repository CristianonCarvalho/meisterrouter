from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from meister.cli import main
from meister.herdr.workers import WorkerSpawner
from tests.platform_marks import posix_only

pytestmark = posix_only

_ONE_TASK_PLAN = (
    '[{"id": "task-1", "description": "Do the thing", '
    '"target_files": ["app.py"], "depends_on": []}]'
)

_SIMULATED_WORKER = (
    "import json, sys\n"
    "result_file = sys.argv[1]\n"
    "with open(result_file, 'w', encoding='utf-8') as handle:\n"
    "    json.dump({'status': 'done', 'modified_files': []}, handle)\n"
)


def _init_repo(repo_dir: Path) -> None:
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("APP = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)


def _write_config(tmp_path: Path, host: str) -> Path:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f'version: "1.0"\nruntime:\n  host: {host}\n'
        'concurrency:\n  isolation_mode: "none"\n',
        encoding="utf-8",
    )
    return cfg_file


def test_orchestrate_process_host_runs_cycle_without_herdr(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    monkeypatch.chdir(repo_dir)
    worker_script = tmp_path / "simulated_worker.py"
    worker_script.write_text(_SIMULATED_WORKER, encoding="utf-8")
    cfg_file = _write_config(tmp_path, "process")

    def simulated_command(self, tier, task_context=None):
        return [sys.executable, str(worker_script), task_context["result_file"]]

    gate = MagicMock()
    gate.run_verification.return_value = (True, "ok")
    gate.get_diff_summary.return_value = "no changes"
    gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    with (
        patch.object(WorkerSpawner, "resolve_command", simulated_command),
        patch("meister.gate.DeterministicGate", return_value=gate),
        patch("meister.herdr.bridge.HerdrSocketClient", side_effect=AssertionError("Herdr touched")),
    ):
        result = CliRunner().invoke(
            main,
            ["orchestrate", "-c", str(cfg_file), "--task", _ONE_TASK_PLAN, "--quiet", "--no-open"],
        )

    assert result.exit_code == 0, result.output
    assert gate.evaluate_completion.called


def test_orchestrate_herdr_host_without_socket_fails_clearly(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    monkeypatch.chdir(repo_dir)
    cfg_file = _write_config(tmp_path, "herdr")
    missing_socket = tmp_path / "missing.sock"

    result = CliRunner().invoke(
        main,
        [
            "orchestrate",
            "-c",
            str(cfg_file),
            "--socket-path",
            str(missing_socket),
            "--task",
            _ONE_TASK_PLAN,
            "--quiet",
            "--no-open",
        ],
    )

    assert result.exit_code != 0
    assert "herdr" in result.output.lower()
    assert "Traceback" not in result.output
