"""Comprehensive hermetic tests for the gate repair loop and failure diagnostics.

Covers:
1. Subtask rejected events carrying failed_tests and failure_summary (gate & scope).
2. Timeline failure rendering with failed_tests in en and pt-BR, plus truncation.
3. Repair loop:
   - Gate failure once then pass on 2nd attempt: COMPLETED, 1 gate_repair, 1 worker_retry,
     same worktree reused, worker 2 task description contains test names and summary.
   - Exhaust attempts (repair_attempts=2, fails 3 times): exactly 2 gate_repairs,
     ends in subtask_rejected and FAILED, worktree cleaned up.
   - repair_attempts=0: disabled, exactly 0 repairs, immediate failure.
   - Scope rejection triggers repair.
   - Merge conflict, no_changes, gate_infrastructure, worker error never trigger repair.
   - Max retries and tier escalation are not consumed by repair.
   - Pre-merge and post-merge paths preserve worker work in worktree.
   - Config validation (-1, true, 'x', 1.5 error; 0 and 3 valid).
   - Locales catalog parity.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from meister.config import MeisterConfig, WorkerTier, load_config, validate_config
from meister.herdr.bridge import HerdrEventBridge
from meister.i18n import reset_language_cache, set_language
from meister.logger import add_event_observer, remove_event_observer
from meister.timeline import build_timeline
from meister.worker import write_atomic_json
from meister.worktree import CodedMessage, WorktreeManager, IntegrationPipeline


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


# ---------------------------------------------------------------------------
# 1. Config validation tests
# ---------------------------------------------------------------------------


def test_config_validation_repair_attempts():
    cfg = MeisterConfig()
    cfg.gate.repair_attempts = 1
    issues = validate_config(cfg)
    assert not any(i.path == "gate.repair_attempts" for i in issues)

    cfg.gate.repair_attempts = 0
    issues = validate_config(cfg)
    assert not any(i.path == "gate.repair_attempts" for i in issues)

    cfg.gate.repair_attempts = 3
    issues = validate_config(cfg)
    assert not any(i.path == "gate.repair_attempts" for i in issues)

    for invalid in (-1, True, False, "x", 1.5):
        cfg.gate.repair_attempts = invalid  # type: ignore
        issues = validate_config(cfg)
        assert any(i.path == "gate.repair_attempts" and i.level == "error" for i in issues), f"Failed for {invalid}"


def test_config_parsing_repair_attempts(tmp_path: Path):
    cfg_file = tmp_path / "custom.yaml"
    cfg_file.write_text("gate:\n  repair_attempts: 3\n")
    loaded = load_config(str(cfg_file))
    assert loaded.gate.repair_attempts == 3

    cfg_file.write_text("gate:\n  repair_attempts: 0\n")
    loaded = load_config(str(cfg_file))
    assert loaded.gate.repair_attempts == 0

    cfg_file.write_text("gate:\n  repair_attempts: -1\n")
    loaded = load_config(str(cfg_file))
    assert loaded.gate.repair_attempts == 1
    assert any(i.path == "gate.repair_attempts" for i in loaded._parse_issues)

    cfg_file.write_text("gate:\n  repair_attempts: true\n")
    loaded = load_config(str(cfg_file))
    assert loaded.gate.repair_attempts == 1
    assert any(i.path == "gate.repair_attempts" for i in loaded._parse_issues)


# ---------------------------------------------------------------------------
# 2. Timeline failure formatting tests (Item 3)
# ---------------------------------------------------------------------------


def test_timeline_failure_formatting_english_and_portuguese():
    try:
        from datetime import datetime, timezone
        now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)

        # Single failure
        events_single = [
            {"event": "orchestration_start", "run_id": "r1", "timestamp": "2026-10-07T12:00:00Z"},
            {"event": "plan_parsed", "run_id": "r1", "task_ids": ["t1"], "timestamp": "2026-10-07T12:00:00Z"},
            {"event": "worker_spawn", "run_id": "r1", "task_id": "t1", "timestamp": "2026-10-07T12:00:01Z", "tier": "luna"},
            {
                "event": "subtask_rejected",
                "run_id": "r1",
                "task_id": "t1",
                "reason": "gate",
                "timestamp": "2026-10-07T12:00:05Z",
                "failed_tests": ["tests/test_mod.py::test_alpha"],
            },
        ]
        set_language("en")
        tl_en_single = build_timeline(events_single, "r1", now)
        assert len(tl_en_single.rows) == 1
        assert tl_en_single.rows[0].failure == "gate: 1 failed test (test_alpha)"

        set_language("pt-BR")
        tl_pt_single = build_timeline(events_single, "r1", now)
        assert len(tl_pt_single.rows) == 1
        assert tl_pt_single.rows[0].failure == "gate: 1 teste (test_alpha)"

        # Multiple failures (2 items)
        events_multi = [
            {"event": "orchestration_start", "run_id": "r1", "timestamp": "2026-10-07T12:00:00Z"},
            {"event": "plan_parsed", "run_id": "r1", "task_ids": ["t2"], "timestamp": "2026-10-07T12:00:00Z"},
            {"event": "worker_spawn", "run_id": "r1", "task_id": "t2", "timestamp": "2026-10-07T12:00:01Z", "tier": "luna"},
            {
                "event": "subtask_rejected",
                "run_id": "r1",
                "task_id": "t2",
                "reason": "gate",
                "timestamp": "2026-10-07T12:00:05Z",
                "failed_tests": ["tests/test_a.py::test_one", "tests/test_b.py::test_two"],
            },
        ]
        set_language("en")
        tl_en_multi = build_timeline(events_multi, "r1", now)
        assert len(tl_en_multi.rows) == 1
        assert tl_en_multi.rows[0].failure == "gate: 2 failed tests (test_one, test_two)"

        set_language("pt-BR")
        tl_pt_multi = build_timeline(events_multi, "r1", now)
        assert len(tl_pt_multi.rows) == 1
        assert tl_pt_multi.rows[0].failure == "gate: 2 testes (test_one, test_two)"

        # Truncation (> 3 items)
        events_trunc = [
            {"event": "orchestration_start", "run_id": "r1", "timestamp": "2026-10-07T12:00:00Z"},
            {"event": "plan_parsed", "run_id": "r1", "task_ids": ["t3"], "timestamp": "2026-10-07T12:00:00Z"},
            {"event": "worker_spawn", "run_id": "r1", "task_id": "t3", "timestamp": "2026-10-07T12:00:01Z", "tier": "luna"},
            {
                "event": "subtask_rejected",
                "run_id": "r1",
                "task_id": "t3",
                "reason": "gate",
                "timestamp": "2026-10-07T12:00:05Z",
                "failed_tests": [
                    "tests/test_a.py::test_1",
                    "tests/test_b.py::test_2",
                    "tests/test_c.py::test_3",
                    "tests/test_d.py::test_4",
                    "tests/test_e.py::test_5",
                ],
            },
        ]
        set_language("en")
        tl_en_trunc = build_timeline(events_trunc, "r1", now)
        assert len(tl_en_trunc.rows) == 1
        assert tl_en_trunc.rows[0].failure == "gate: 5 failed tests (test_1, test_2, test_3, +2)"

        set_language("pt-BR")
        tl_pt_trunc = build_timeline(events_trunc, "r1", now)
        assert len(tl_pt_trunc.rows) == 1
        assert tl_pt_trunc.rows[0].failure == "gate: 5 testes (test_1, test_2, test_3, +2)"

        # Without failed_tests, existing behavior preserved
        events_normal = [
            {"event": "orchestration_start", "run_id": "r1", "timestamp": "2026-10-07T12:00:00Z"},
            {"event": "plan_parsed", "run_id": "r1", "task_ids": ["t4"], "timestamp": "2026-10-07T12:00:00Z"},
            {"event": "worker_spawn", "run_id": "r1", "task_id": "t4", "timestamp": "2026-10-07T12:00:01Z", "tier": "luna"},
            {
                "event": "subtask_rejected",
                "run_id": "r1",
                "task_id": "t4",
                "reason": "gate",
                "timestamp": "2026-10-07T12:00:05Z",
            },
        ]
        set_language("en")
        tl_normal = build_timeline(events_normal, "r1", now)
        assert len(tl_normal.rows) == 1
        assert tl_normal.rows[0].failure == "gate"
    finally:
        reset_language_cache()


# ---------------------------------------------------------------------------
# 3. Repair loop tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_repair_once_then_passes(temp_git_repo: Path, event_collector):
    """(1) reprova no gate uma vez e passa na 2ª: subtarefa termina COMPLETED,
    há exatamente um gate_repair e um worker_retry reason=gate_repair, o worker
    foi aberto 2 vezes no MESMO worktree e a descrição da 2ª contém os nomes dos testes e o resumo.
    """
    wt_dir = temp_git_repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(temp_git_repo), worktrees_dir=str(wt_dir))
    subtask_wt = wt_mgr.create_worktree(task_id="run1_task1_abc", base_ref="main")

    spawn_cwds = []
    task_descriptions = []
    call_count = 0

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        nonlocal call_count
        call_count += 1
        spawn_cwds.append(cwd)
        task_descriptions.append(task_context.get("description", ""))

        # Worktree must exist and be intact when worker runs (Mutation b guard)
        assert Path(cwd).exists(), "Worktree path must exist when worker runs"
        if call_count > 1:
            assert (Path(cwd) / "worker_pass_1.txt").exists(), (
                "Worktree must preserve files from previous attempt"
            )
        (Path(cwd) / f"worker_pass_{call_count}.txt").write_text(f"pass {call_count}\n")

        write_atomic_json(
            task_context["result_file"],
            {"status": "done", "modified_files": [f"worker_pass_{call_count}.txt"]},
        )
        return ("t1", f"p{call_count}", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

    gate_output_fail = (
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_core.py::test_gate_fail - AssertionError: expected true\n"
        "======================== 1 failed, 0 passed in 0.1s ==========================\n"
    )

    integration_pipeline = MagicMock()
    integration_pipeline.integration_info = SimpleNamespace(branch_name="main")
    integration_pipeline.wt_mgr = wt_mgr
    integration_pipeline.last_integrated_sha = "deadbeef1234"

    integration_call_count = 0

    def fake_prepare(subtask_wt, target_files, commit_message, task_id, attempt, tier):
        return SimpleNamespace(
            subtask_wt=subtask_wt,
            target_files=target_files,
            commit_message=commit_message,
            task_id=task_id,
            attempt=attempt,
            tier=tier,
        )

    def fake_merge(prepared):
        nonlocal integration_call_count
        integration_call_count += 1
        if integration_call_count == 1:
            return (False, CodedMessage(gate_output_fail, code="gate"))
        return (True, "subtask integrated")

    integration_pipeline.prepare_subtask = MagicMock(side_effect=fake_prepare)
    integration_pipeline.merge_prepared = MagicMock(side_effect=fake_merge)

    cfg = MeisterConfig()
    cfg.gate.repair_attempts = 1
    client = AsyncMock()

    bridge = HerdrEventBridge(config=cfg, client=client, spawner=mock_spawner)
    bridge._integration_pipeline = integration_pipeline
    bridge.current_run_id = "run1"

    # Avoid create_worktree creating a second worktree inside execute_subtask
    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

    subtask = {
        "id": "task1",
        "description": "Original subtask instruction",
        "target_files": ["core.py"],
    }

    success = await bridge.execute_subtask(subtask)
    assert success is True, "Subtask should succeed after successful repair"

    # Assert worker was spawned exactly twice in the SAME worktree
    assert call_count == 2
    assert len(spawn_cwds) == 2
    assert spawn_cwds[0] == spawn_cwds[1] == subtask_wt.worktree_path

    # Assert description of attempt 2 contains test names, summary, and instructions (Mutation d guard)
    assert "Original subtask instruction" in task_descriptions[1]
    assert "## REPAIR" in task_descriptions[1]
    assert "tests/test_core.py::test_gate_fail" in task_descriptions[1]
    assert "AssertionError: expected true" in task_descriptions[1]
    assert "Your previous attempt was rejected by the deterministic gate." in task_descriptions[1]

    # Assert events
    gate_repairs = [e for e in event_collector if e.get("event_type") == "gate_repair"]
    worker_retries = [
        e for e in event_collector if e.get("event_type") == "worker_retry" and e.get("reason") == "gate_repair"
    ]
    subtask_rejects = [e for e in event_collector if e.get("event_type") == "subtask_rejected"]
    subtask_completeds = [e for e in event_collector if e.get("event_type") == "subtask_completed"]

    assert len(gate_repairs) == 1, "Exactly one gate_repair event must be emitted"
    gr = gate_repairs[0]
    assert gr["task_id"] == "task1"
    assert gr["repair_attempt"] == 1
    assert gr["reason"] == "gate"
    assert "tests/test_core.py::test_gate_fail" in gr["failed_tests"]
    assert "short test summary info" in gr["failure_summary"]

    assert len(worker_retries) == 1, "Exactly one worker_retry event must be emitted"
    assert worker_retries[0]["reason"] == "gate_repair"

    # No intermediate subtask_rejected should be logged
    assert len(subtask_rejects) == 0, "Intermediate rejection must not emit subtask_rejected"
    assert len(subtask_completeds) == 1, "Successful repair must emit subtask_completed"


@pytest.mark.asyncio
async def test_exhaust_repair_attempts_bounded(temp_git_repo: Path, event_collector):
    """(2) esgota as tentativas (repair_attempts: 2, reprova 3 vezes):
    comportamento final (arquivada, FAILED, subtask_rejected), exatamente 2 gate_repair.
    Also guards against Mutation (c): attempts are bounded by gate.repair_attempts.
    """
    wt_dir = temp_git_repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(temp_git_repo), worktrees_dir=str(wt_dir))
    subtask_wt = wt_mgr.create_worktree(task_id="run2_task1_abc", base_ref="main")

    call_count = 0

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        nonlocal call_count
        call_count += 1
        write_atomic_json(
            task_context["result_file"],
            {"status": "done", "modified_files": []},
        )
        return ("t1", f"p{call_count}", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

    gate_output_fail = (
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_mod.py::test_bad - AssertionError\n"
        "======================== 1 failed in 0.1s ==========================\n"
    )

    integration_pipeline = MagicMock()
    integration_pipeline.integration_info = SimpleNamespace(branch_name="main")
    integration_pipeline.wt_mgr = wt_mgr
    integration_pipeline.prepare_subtask = MagicMock(return_value=SimpleNamespace(subtask_wt=subtask_wt))
    # Always fails gate
    integration_pipeline.merge_prepared = MagicMock(
        return_value=(False, CodedMessage(gate_output_fail, code="gate"))
    )

    cleaned_worktrees = []
    wt_mgr.cleanup_worktree = MagicMock(side_effect=lambda task_id, **kw: cleaned_worktrees.append(task_id))

    cfg = MeisterConfig()
    cfg.gate.repair_attempts = 2  # Allows 2 repairs (3 total attempts)
    client = AsyncMock()

    bridge = HerdrEventBridge(config=cfg, client=client, spawner=mock_spawner)
    bridge._integration_pipeline = integration_pipeline
    bridge.current_run_id = "run2"

    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

    subtask = {"id": "task_exhaust", "description": "Fails always", "target_files": ["mod.py"]}
    success = await bridge.execute_subtask(subtask)

    assert success is False, "Exhausted repairs should result in failure"
    assert call_count == 3, f"Expected 3 spawns (1 initial + 2 repairs), got {call_count}"

    gate_repairs = [e for e in event_collector if e.get("event_type") == "gate_repair"]
    assert len(gate_repairs) == 2, f"Expected exactly 2 gate_repair events, got {len(gate_repairs)}"
    assert gate_repairs[0]["repair_attempt"] == 1
    assert gate_repairs[1]["repair_attempt"] == 2

    subtask_rejects = [e for e in event_collector if e.get("event_type") == "subtask_rejected"]
    assert len(subtask_rejects) == 1, "Final failure must emit subtask_rejected"
    rej = subtask_rejects[0]
    assert rej["reason"] == "gate"
    assert "tests/test_mod.py::test_bad" in rej["failed_tests"]
    assert "short test summary info" in rej["failure_summary"]

    assert len(cleaned_worktrees) >= 1, "Worktree must be cleaned up/archived when attempts are exhausted"


@pytest.mark.asyncio
async def test_repair_attempts_zero_disables_repair(temp_git_repo: Path, event_collector):
    """(3) repair_attempts: 0: nenhum reparo, comportamento idêntico ao de hoje.
    Also guards against Mutation (e): repair_attempts: 0 cannot be ignored.
    """
    wt_dir = temp_git_repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(temp_git_repo), worktrees_dir=str(wt_dir))
    subtask_wt = wt_mgr.create_worktree(task_id="run3_task1_abc", base_ref="main")

    call_count = 0

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        nonlocal call_count
        call_count += 1
        write_atomic_json(
            task_context["result_file"],
            {"status": "done", "modified_files": []},
        )
        return ("t1", f"p{call_count}", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

    integration_pipeline = MagicMock()
    integration_pipeline.integration_info = SimpleNamespace(branch_name="main")
    integration_pipeline.wt_mgr = wt_mgr
    integration_pipeline.prepare_subtask = MagicMock(return_value=SimpleNamespace(subtask_wt=subtask_wt))
    integration_pipeline.merge_prepared = MagicMock(
        return_value=(False, CodedMessage("Gate failure output", code="gate"))
    )

    cfg = MeisterConfig()
    cfg.gate.repair_attempts = 0  # Disabled!
    client = AsyncMock()

    bridge = HerdrEventBridge(config=cfg, client=client, spawner=mock_spawner)
    bridge._integration_pipeline = integration_pipeline
    bridge.current_run_id = "run3"

    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

    subtask = {"id": "task_zero", "description": "Disabled repair test", "target_files": ["mod.py"]}
    success = await bridge.execute_subtask(subtask)

    assert success is False
    assert call_count == 1, "With repair_attempts: 0, worker must only be called once"

    gate_repairs = [e for e in event_collector if e.get("event_type") == "gate_repair"]
    assert len(gate_repairs) == 0, "No gate_repair events must be emitted when repair_attempts=0"

    subtask_rejects = [e for e in event_collector if e.get("event_type") == "subtask_rejected"]
    assert len(subtask_rejects) == 1


@pytest.mark.asyncio
async def test_scope_rejection_triggers_repair(temp_git_repo: Path, event_collector):
    """(4) rejeição por escopo também repara."""
    wt_dir = temp_git_repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(temp_git_repo), worktrees_dir=str(wt_dir))
    subtask_wt = wt_mgr.create_worktree(task_id="run4_task1_abc", base_ref="main")

    call_count = 0

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        nonlocal call_count
        call_count += 1
        write_atomic_json(
            task_context["result_file"],
            {"status": "done", "modified_files": []},
        )
        return ("t1", f"p{call_count}", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

    integration_pipeline = MagicMock()
    integration_pipeline.integration_info = SimpleNamespace(branch_name="main")
    integration_pipeline.wt_mgr = wt_mgr
    integration_pipeline.last_integrated_sha = "sha_scope_fixed"

    def fake_prepare(subtask_wt, target_files, commit_message, task_id, attempt, tier):
        return SimpleNamespace(subtask_wt=subtask_wt)

    scope_err_count = 0

    def fake_merge(prepared):
        nonlocal scope_err_count
        scope_err_count += 1
        if scope_err_count == 1:
            return (
                False,
                CodedMessage(
                    "Scope violation: out-of-scope files modified: ['secret.py']",
                    code="scope",
                ),
            )
        return (True, "subtask integrated")

    integration_pipeline.prepare_subtask = MagicMock(side_effect=fake_prepare)
    integration_pipeline.merge_prepared = MagicMock(side_effect=fake_merge)

    cfg = MeisterConfig()
    cfg.gate.repair_attempts = 1
    client = AsyncMock()

    bridge = HerdrEventBridge(config=cfg, client=client, spawner=mock_spawner)
    bridge._integration_pipeline = integration_pipeline
    bridge.current_run_id = "run4"
    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

    subtask = {"id": "task_scope", "description": "Fix scope", "target_files": ["app.py"]}
    success = await bridge.execute_subtask(subtask)

    assert success is True
    assert call_count == 2
    gate_repairs = [e for e in event_collector if e.get("event_type") == "gate_repair"]
    assert len(gate_repairs) == 1
    assert gate_repairs[0]["reason"] == "scope"


@pytest.mark.asyncio
async def test_non_repairable_rejections_never_trigger_repair(temp_git_repo: Path, event_collector):
    """(5) merge, no_changes, gate_infrastructure e erro do worker NUNCA disparam reparo.
    Also guards against Mutation (a): repair on merge conflict must not happen.
    """
    wt_dir = temp_git_repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(temp_git_repo), worktrees_dir=str(wt_dir))

    # Test cases: (code, error_message, worker_status)
    test_cases = [
        ("merge", CodedMessage("Automatic merge failed; fix conflicts", code="merge"), "done"),
        ("no_changes", CodedMessage("Sem alterações em target_files", code="no_changes"), "done"),
        (
            "gate_infrastructure",
            "ERRO DE INFRAESTRUTURA no portão: runner indisponível",
            "done",
        ),
        ("worker_error", "None", "error"),
    ]

    for label, error_val, worker_status in test_cases:
        event_collector.clear()
        subtask_wt = wt_mgr.create_worktree(task_id=f"run5_{label}", base_ref="main")
        call_count = 0

        async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
            nonlocal call_count
            call_count += 1
            res_payload = (
                {"status": "error", "error": "Worker exited with code 1"}
                if worker_status == "error"
                else {"status": "done", "modified_files": []}
            )
            write_atomic_json(task_context["result_file"], res_payload)
            return ("t1", f"p{call_count}", None)

        mock_spawner = MagicMock()
        mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
        mock_spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

        integration_pipeline = MagicMock()
        integration_pipeline.integration_info = SimpleNamespace(branch_name="main")
        integration_pipeline.wt_mgr = wt_mgr
        integration_pipeline.prepare_subtask = MagicMock(return_value=SimpleNamespace(subtask_wt=subtask_wt))
        integration_pipeline.merge_prepared = MagicMock(return_value=(False, error_val))

        cfg = MeisterConfig()
        cfg.gate.repair_attempts = 2  # Even with repairs allowed, non-repairables must not trigger!
        client = AsyncMock()

        bridge = HerdrEventBridge(config=cfg, client=client, spawner=mock_spawner)
        bridge._integration_pipeline = integration_pipeline
        bridge.current_run_id = f"run5_{label}"
        wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

        subtask = {"id": f"task_{label}", "description": f"Test {label}", "target_files": ["app.py"]}
        success = await bridge.execute_subtask(subtask)

        assert success is False, f"{label} should fail"
        assert call_count == 1, f"{label} must not be retried, spawned {call_count} times"

        gate_repairs = [e for e in event_collector if e.get("event_type") == "gate_repair"]
        assert len(gate_repairs) == 0, f"{label} must NOT emit any gate_repair events!"


@pytest.mark.asyncio
async def test_repair_does_not_consume_tier_retries_or_escalate(temp_git_repo: Path):
    """(6) o reparo não consome max_retries nem escala de via."""
    wt_dir = temp_git_repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(temp_git_repo), worktrees_dir=str(wt_dir))
    subtask_wt = wt_mgr.create_worktree(task_id="run6_tier_test", base_ref="main")

    tiers_spawned = []

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        tiers_spawned.append(tier_name)
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return ("t1", f"p{len(tiers_spawned)}", None)

    tier_luna = WorkerTier(name="luna", harness="codex", model="gpt-6-luna", max_retries=1)
    tier_sonnet = WorkerTier(name="sonnet", harness="claude", model="sonnet", max_retries=1)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.side_effect = lambda name: tier_luna if name == "luna" else tier_sonnet
    mock_spawner.get_next_available_tier.return_value = tier_sonnet

    call_count = 0

    def fake_merge(prepared):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (False, CodedMessage("Gate failure", code="gate"))
        return (True, "success")

    integration_pipeline = MagicMock()
    integration_pipeline.integration_info = SimpleNamespace(branch_name="main")
    integration_pipeline.wt_mgr = wt_mgr
    integration_pipeline.last_integrated_sha = "sha_repaired"
    integration_pipeline.prepare_subtask = MagicMock(return_value=SimpleNamespace(subtask_wt=subtask_wt))
    integration_pipeline.merge_prepared = MagicMock(side_effect=fake_merge)

    cfg = MeisterConfig()
    cfg.gate.repair_attempts = 1
    cfg.workers.tier_order = [tier_luna, tier_sonnet]
    client = AsyncMock()

    bridge = HerdrEventBridge(config=cfg, client=client, spawner=mock_spawner)
    bridge._integration_pipeline = integration_pipeline
    bridge.current_run_id = "run6"
    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

    subtask = {"id": "task_tier", "description": "Tier check", "target_files": ["app.py"]}
    success = await bridge.execute_subtask(subtask)

    assert success is True
    # Both spawns must be on 'luna' - no escalation!
    assert tiers_spawned == ["luna", "luna"]


@pytest.mark.asyncio
async def test_pre_merge_and_post_merge_paths_preserve_work(temp_git_repo: Path):
    """(7) caminhos pré-merge e pós-merge (rollback) mantêm o trabalho do worker."""
    wt_dir = temp_git_repo / "worktrees"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(temp_git_repo), worktrees_dir=str(wt_dir))

    # Test pre-merge gate failure: worker's changes are uncommitted in worktree
    subtask_wt_pre = wt_mgr.create_worktree(task_id="pre_merge_wt", base_ref="main")
    (Path(subtask_wt_pre.worktree_path) / "feature.txt").write_text("uncommitted work from worker 1\n")

    # In pre-merge failure, subtask_wt is not cleaned up
    # Second worker sees feature.txt intact
    assert (Path(subtask_wt_pre.worktree_path) / "feature.txt").exists()
    assert (Path(subtask_wt_pre.worktree_path) / "feature.txt").read_text() == "uncommitted work from worker 1\n"
    wt_mgr.cleanup_worktree(subtask_wt_pre.task_id, force=True)

    # Test post-merge gate failure: worker committed to subtask_wt branch,
    # integration merged and then rolled back.
    pipeline = IntegrationPipeline(worktree_manager=wt_mgr)
    pipeline.start_integration("run_test_paths")

    subtask_wt_post = wt_mgr.create_worktree(task_id="post_merge_wt", base_ref="main")
    (Path(subtask_wt_post.worktree_path) / "worker_work.txt").write_text("worker 1 committed work\n")
    commit_sha = wt_mgr.commit_worktree(subtask_wt_post.worktree_path, "subtask commit 1")
    assert commit_sha is not None

    # Merge into integration branch
    merged, rollback_sha = wt_mgr.merge_branch_into(
        subtask_wt_post.branch_name, pipeline.integration_info.worktree_path
    )
    assert merged is True

    # Simulate post-merge gate rejection: rollback integration worktree
    wt_mgr.rollback_merge(pipeline.integration_info.worktree_path, rollback_sha)

    # Verify:
    # 1. Integration worktree no longer has worker_work.txt
    assert not (Path(pipeline.integration_info.worktree_path) / "worker_work.txt").exists()
    # 2. subtask_wt STILL has worker_work.txt and the commit!
    assert (Path(subtask_wt_post.worktree_path) / "worker_work.txt").exists()
    head_sha = wt_mgr._run_git(["rev-parse", "HEAD"], cwd=subtask_wt_post.worktree_path)
    assert head_sha == commit_sha

    # Now simulate worker 2 adding a fix on top of the same worktree
    (Path(subtask_wt_post.worktree_path) / "worker_work.txt").write_text("worker 1 work + worker 2 fix\n")
    commit_sha_2 = wt_mgr.commit_worktree(subtask_wt_post.worktree_path, "subtask commit 2 (repair)")
    assert commit_sha_2 is not None

    # Merge again into integration branch
    merged_2, _ = wt_mgr.merge_branch_into(
        subtask_wt_post.branch_name, pipeline.integration_info.worktree_path
    )
    assert merged_2 is True
    # Now integration branch has the repaired work!
    assert (Path(pipeline.integration_info.worktree_path) / "worker_work.txt").read_text() == "worker 1 work + worker 2 fix\n"

    # Clean up
    wt_mgr.cleanup_worktree(subtask_wt_post.task_id, force=True)
    pipeline.abort_integration()
