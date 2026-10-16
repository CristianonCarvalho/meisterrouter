from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from meister.cli import main, run_name_from_plan_file
from meister.engine.bridge import derive_run_name
from meister.engine.workers import WorkerSpawner
from meister.logger import get_log_file
from meister.timeline import build_timeline
from tests.platform_marks import posix_only

_PLAN = json.dumps(
    [
        {
            "id": "task-1",
            "description": "Add the login form\nDetails that should not be in the name.",
            "target_files": ["app.py"],
            "depends_on": [],
        }
    ]
)

_SIMULATED_WORKER = (
    "import json, sys\n"
    "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
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


def _run_orchestrate(tmp_path: Path, monkeypatch, extra_args: list[str]) -> dict:
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    monkeypatch.chdir(repo_dir)
    worker_script = tmp_path / "simulated_worker.py"
    worker_script.write_text(_SIMULATED_WORKER, encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        'version: "1.0"\nruntime:\n  host: process\nconcurrency:\n  isolation_mode: "none"\n',
        encoding="utf-8",
    )

    def simulated_command(self, tier, task_context=None):
        return [sys.executable, str(worker_script), task_context["result_file"]]

    gate = MagicMock()
    gate.run_verification.return_value = (True, "ok")
    gate.get_diff_summary.return_value = "no changes"
    gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    with (
        patch.object(WorkerSpawner, "resolve_command", simulated_command),
        patch("meister.gate.DeterministicGate", return_value=gate),
    ):
        result = CliRunner().invoke(
            main,
            ["orchestrate", "-c", str(cfg_file), "--task", _PLAN, "--quiet", "--no-open", *extra_args],
        )
    assert result.exit_code == 0, result.output

    with open(get_log_file(), encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    return next(
        event
        for event in events
        if (event.get("event_type") or event.get("event")) == "orchestration_start"
    )


@posix_only
def test_orchestrate_name_goes_to_run_name(tmp_path, monkeypatch):
    start = _run_orchestrate(tmp_path, monkeypatch, ["--name", "Login sprint"])

    assert start["run_name"] == "Login sprint"
    assert "run_name_derived" not in start


@posix_only
def test_orchestrate_without_name_derives_readable_title(tmp_path, monkeypatch):
    start = _run_orchestrate(tmp_path, monkeypatch, [])

    assert "run_name" not in start
    assert start["run_name_derived"] == "Add the login form"


def test_derive_run_name_uses_first_task_title_clipped_to_80():
    steps = [{"id": "a", "description": "x" * 200}, {"id": "b", "description": "second"}]

    name = derive_run_name(steps)

    assert len(name) <= 80
    assert name.startswith("xxx")


def test_derive_run_name_never_returns_raw_json():
    raw = '[{"id": "task_1", "description": "do it", "target_files": [], "depends_on": []}]'

    assert derive_run_name([{"id": "task_1", "description": raw}]) == ""
    assert derive_run_name([{"id": "task_1", "description": '{"a": 1}'}]) == ""
    assert derive_run_name([]) == ""
    assert derive_run_name([{"id": "task_1", "description": "  \n"}]) == ""


def test_old_orchestration_start_without_new_fields_still_builds_timeline():
    events = [
        {
            "ts": "2026-10-09T10:00:00+00:00",
            "event_type": "orchestration_start",
            "run_id": "oldrun000001",
            "task_id": "orchestrator",
            "task": "Migrate auth system",
        },
        {
            "ts": "2026-10-09T10:05:00+00:00",
            "event_type": "orchestration_end",
            "run_id": "oldrun000001",
            "task_id": "orchestrator",
            "status": "completed",
            "exit_code": 0,
        },
    ]

    timeline = build_timeline(
        events, "oldrun000001", now=datetime(2026, 10, 9, 11, tzinfo=timezone.utc)
    )

    assert timeline.title == "Migrate auth system"


def test_run_name_from_plan_file_strips_extension_and_date_prefix():
    assert run_name_from_plan_file("checkout-sem-conta.json") == "Checkout sem conta"
    assert run_name_from_plan_file("docs/plans/2026-10-15-gate-vitest.json") == "Gate vitest"
    assert run_name_from_plan_file("my__plan--  name.json") == "My plan name"


def test_run_name_from_plan_file_short_name_is_empty():
    assert run_name_from_plan_file("ab.json") == ""
    assert run_name_from_plan_file("2026-10-15-ab.json") == ""


def _orchestrate_plan_file(tmp_path: Path, plan_name: str, description: str, extra_args: list[str]) -> dict:
    plan_path = tmp_path / plan_name
    plan_path.write_text(
        json.dumps(
            [{"id": "task-1", "description": description, "target_files": ["app.py"], "depends_on": []}]
        ),
        encoding="utf-8",
    )
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        'version: "1.0"\nruntime:\n  host: process\nconcurrency:\n  isolation_mode: "none"\n',
        encoding="utf-8",
    )
    bridge = MagicMock()
    bridge.run_orchestration_cycle = AsyncMock(return_value=True)
    with (
        patch("meister.cli.HerdrEventBridge", return_value=bridge),
        patch("meister.cli.select_host", return_value=MagicMock()),
    ):
        result = CliRunner().invoke(
            main,
            ["orchestrate", "-c", str(cfg_file), "--plan-file", str(plan_path), "--quiet", "--no-open", *extra_args],
        )
    assert result.exit_code == 0, result.output
    return bridge.run_orchestration_cycle.await_args.kwargs


def test_plan_file_with_generic_task_title_uses_file_name(tmp_path):
    kwargs = _orchestrate_plan_file(tmp_path, "checkout-sem-conta.json", "Task 1: Add the form", [])

    assert kwargs["run_name"] == "Checkout sem conta"


def test_plan_file_generic_title_with_explicit_name_keeps_name(tmp_path):
    kwargs = _orchestrate_plan_file(
        tmp_path, "checkout-sem-conta.json", "Task 1: Add the form", ["--name", "Login sprint"]
    )

    assert kwargs["run_name"] == "Login sprint"


def test_plan_file_without_task_n_title_does_not_use_file_name(tmp_path):
    kwargs = _orchestrate_plan_file(tmp_path, "checkout-sem-conta.json", "Add the login form", [])

    assert "run_name" not in kwargs


def test_plan_file_generic_title_with_short_file_name_keeps_default(tmp_path):
    kwargs = _orchestrate_plan_file(tmp_path, "ab.json", "Task 1: Add the form", [])

    assert "run_name" not in kwargs

