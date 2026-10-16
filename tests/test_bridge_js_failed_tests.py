"""Ponte: `js_failed_tests` aparece nos eventos gate_repair e subtask_rejected só quando há testes JS.

- Saída de gate do vitest gera a lista com os nomes dos testes nos dois eventos.
- Saída de pytest não traz a chave em nenhum dos dois eventos (eventos idênticos aos de hoje).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from meister.config import MeisterConfig, WorkerTier
from meister.engine.bridge import HerdrEventBridge
from meister.logger import add_event_observer, remove_event_observer
from meister.worker import write_atomic_json
from meister.worktree import CodedMessage, WorktreeManager


VITEST_OUTPUT = (
    " FAIL  src/app.test.ts > App > renders the button\n"
    "AssertionError: expected 'a' to be 'b'\n"
    " FAIL  src/app.test.ts > App > saves the form\n"
    "Error: boom\n"
    "Test Files  1 failed (1)\n"
    "Tests  2 failed (2)\n"
)

PYTEST_OUTPUT = (
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_core.py::test_gate_fail - AssertionError: expected true\n"
    "======================== 1 failed, 0 passed in 0.1s ==========================\n"
)


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tester@test.local"], cwd=repo, check=True)
    (repo / "base.txt").write_text("initial\n")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def event_collector():
    events: List[Dict[str, Any]] = []

    def handler(ev: Dict[str, Any]):
        events.append(dict(ev))

    add_event_observer(handler)
    try:
        yield events
    finally:
        remove_event_observer(handler)


async def _run_gate_rejection(repo: Path, gate_output: str) -> None:
    """Executa uma subtarefa cujo merge reprova no gate com `gate_output` (1 reparo, depois rejeição)."""
    wt_dir = repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(repo), worktrees_dir=str(wt_dir))
    subtask_wt = wt_mgr.create_worktree(task_id="run_js_task1_abc", base_ref="main")

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        (Path(cwd) / "work.txt").write_text("work\n")
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": ["work.txt"]})
        return ("t1", "p1", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

    def fake_prepare(subtask_wt, target_files, commit_message, task_id, attempt, tier):
        return SimpleNamespace(
            subtask_wt=subtask_wt,
            target_files=target_files,
            commit_message=commit_message,
            task_id=task_id,
            attempt=attempt,
            tier=tier,
        )

    integration_pipeline = MagicMock()
    integration_pipeline.integration_info = SimpleNamespace(branch_name="main")
    integration_pipeline.wt_mgr = wt_mgr
    integration_pipeline.last_integrated_sha = "deadbeef1234"
    integration_pipeline.prepare_subtask = MagicMock(side_effect=fake_prepare)
    integration_pipeline.merge_prepared = MagicMock(return_value=(False, CodedMessage(gate_output, code="gate")))

    cfg = MeisterConfig()
    cfg.gate.repair_attempts = 1

    bridge = HerdrEventBridge(config=cfg, client=AsyncMock(), spawner=mock_spawner)
    bridge._integration_pipeline = integration_pipeline
    bridge.current_run_id = "run_js"
    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

    subtask = {"id": "task1", "description": "Subtask", "target_files": ["app.ts", "tests/test_core.py"]}
    success = await bridge.execute_subtask(subtask)
    assert success is False


@pytest.mark.asyncio
async def test_vitest_gate_failure_carries_js_failed_tests(temp_git_repo: Path, event_collector):
    await _run_gate_rejection(temp_git_repo, VITEST_OUTPUT)

    gate_repairs = [e for e in event_collector if e.get("event_type") == "gate_repair"]
    rejects = [e for e in event_collector if e.get("event_type") == "subtask_rejected"]
    assert len(gate_repairs) == 1
    assert len(rejects) == 1

    expected = [
        "src/app.test.ts > App > renders the button",
        "src/app.test.ts > App > saves the form",
    ]
    assert gate_repairs[0]["js_failed_tests"] == expected
    assert rejects[0]["js_failed_tests"] == expected


@pytest.mark.asyncio
async def test_pytest_gate_failure_has_no_js_failed_tests_key(temp_git_repo: Path, event_collector):
    await _run_gate_rejection(temp_git_repo, PYTEST_OUTPUT)

    gate_repairs = [e for e in event_collector if e.get("event_type") == "gate_repair"]
    rejects = [e for e in event_collector if e.get("event_type") == "subtask_rejected"]
    assert len(gate_repairs) == 1
    assert len(rejects) == 1

    assert "tests/test_core.py::test_gate_fail" in gate_repairs[0]["failed_tests"]
    assert "js_failed_tests" not in gate_repairs[0]
    assert "js_failed_tests" not in rejects[0]
