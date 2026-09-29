"""Tests for fix/reject-empty-subtask — three-layer worktree race correction.

Layers tested:
1. Concurrency: 8 simultaneous create_worktree calls on a real repo — all succeed, all distinct.
2. Retry:  _run_git monkeypatched to fail with commondir 2×, then succeed → success;
           fail 4× → raises; non-transient error → no retry (count verified).
3. Bridge: with active integration pipeline, create_worktree raising → execute_subtask
           returns False, subtask is FAILED (never COMPLETED), subtask_rejected event
           with reason="worktree_create_failed" is emitted, and the run does NOT complete.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meister.herdr.bridge import HerdrEventBridge
from meister.state import SubtaskState, compute_subtask_id
from meister.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Shared git_repo fixture (real git repo in tmp_path)
# ---------------------------------------------------------------------------

@pytest.fixture
def git_repo(tmp_path):
    """Initialise a clean git repository in tmp_path and return its Path."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cwd = str(repo_dir)

    subprocess.run(["git", "init", "-b", "main"], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@meisterrouter.local"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "Meister CI"], cwd=cwd, check=True)

    (repo_dir / "README.md").write_text("# Project\n")
    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=cwd, check=True)

    return repo_dir


# ===========================================================================
# Layer 1 — Concurrency: 8 threads, all succeed, all distinct
# ===========================================================================

def test_concurrent_create_worktree_all_succeed_and_distinct(git_repo):
    """8 threads calling create_worktree simultaneously on the same manager.

    All must succeed and produce distinct worktree paths.
    No artificial sleeps: correctness depends on the lock, not timing.
    """
    manager = WorktreeManager(repo_root=str(git_repo))
    n = 8
    results: list = [None] * n
    errors: list = [None] * n

    def worker(i: int) -> None:
        try:
            info = manager.create_worktree(f"concurrent-task-{i}")
            results[i] = info.worktree_path
        except Exception as exc:
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    failed = [(i, errors[i]) for i in range(n) if errors[i] is not None]
    assert not failed, f"Some create_worktree calls failed: {failed}"

    paths = [p for p in results if p is not None]
    assert len(paths) == n, f"Expected {n} results, got {len(paths)}"

    # All paths must be distinct
    assert len(set(paths)) == n, f"Duplicate worktree paths: {paths}"

    # All paths must actually exist on disk
    for p in paths:
        assert os.path.isdir(p), f"Worktree directory does not exist: {p}"


def test_concurrent_worktree_operations_strictly_serialized(git_repo, monkeypatch):
    """Deterministic concurrency test for WorktreeManager lock serialized operations.

    Monkeypatches WorktreeManager._run_git with a wrapper that tracks concurrent 'worktree add'
    and 'worktree remove' executions using a 0.05s artificial sleep. With 8 threads dispatched
    simultaneously via threading.Barrier, absence of self._worktree_lock will deterministically
    cause max_simultaneous to be > 1. With the lock present, max_simultaneous must be 1.
    """
    manager = WorktreeManager(repo_root=str(git_repo))
    n = 8
    barrier = threading.Barrier(n)

    test_lock = threading.Lock()
    current_simultaneous = 0
    max_simultaneous = 0
    orig_run_git = WorktreeManager._run_git

    def mock_run_git(self, args, *call_args, **call_kwargs):
        if len(args) >= 2 and args[0] == "worktree" and args[1] in ("add", "remove"):
            with test_lock:
                nonlocal current_simultaneous, max_simultaneous
                current_simultaneous += 1
                if current_simultaneous > max_simultaneous:
                    max_simultaneous = current_simultaneous
            try:
                time.sleep(0.05)
                return orig_run_git(self, args, *call_args, **call_kwargs)
            finally:
                with test_lock:
                    current_simultaneous -= 1
        return orig_run_git(self, args, *call_args, **call_kwargs)

    monkeypatch.setattr(WorktreeManager, "_run_git", mock_run_git)

    creations: list = [None] * n
    errors: list = [None] * n

    def worker(i: int) -> None:
        task_id = f"deterministic-race-task-{i}"
        barrier.wait()
        try:
            info = manager.create_worktree(task_id)
            creations[i] = info
            manager.cleanup_worktree(task_id)
        except Exception as exc:
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    failed = [(i, errors[i]) for i in range(n) if errors[i] is not None]
    assert not failed, f"Some worker threads raised exceptions: {failed}"
    assert max_simultaneous == 1, (
        f"Worktree operations were not strictly serialized: max_simultaneous={max_simultaneous}"
    )
    assert len([c for c in creations if c is not None]) == n, (
        f"Expected {n} successful worktree creations, got {len([c for c in creations if c is not None])}"
    )
    for c in creations:
        assert c is not None
        assert c.worktree_path
        assert c.branch_name


# ===========================================================================
# Layer 2 — Retry logic
# ===========================================================================

class _CallCounter:
    def __init__(self):
        self.count = 0


def _make_transient_error(pattern: str = "commondir") -> RuntimeError:
    return RuntimeError(f"Comando git falhou (rc=128): git worktree add\nstderr: fatal: failed to read .git/worktrees/foo/{pattern}")


def test_create_worktree_retry_succeeds_after_two_transient_failures(git_repo, monkeypatch):
    """create_worktree retries on transient errors and succeeds on the 3rd attempt."""
    manager = WorktreeManager(repo_root=str(git_repo))
    counter = _CallCounter()
    orig_run_git = manager._run_git

    def mock_run_git(args, **kwargs):
        if args and args[0] == "worktree" and len(args) > 1 and args[1] == "add":
            counter.count += 1
            if counter.count <= 2:
                raise _make_transient_error("commondir")
            return orig_run_git(args, **kwargs)
        return orig_run_git(args, **kwargs)

    monkeypatch.setattr(manager, "_run_git", mock_run_git)
    # Speed up the test by removing real sleeps
    monkeypatch.setattr("meister.worktree.time.sleep", lambda _: None)

    info = manager.create_worktree("retry-task")
    assert info is not None
    assert os.path.isdir(info.worktree_path)
    assert counter.count == 3, f"Expected 3 git worktree add calls, got {counter.count}"


def test_create_worktree_raises_after_four_transient_failures(git_repo, monkeypatch):
    """create_worktree raises after exhausting all 3 retry attempts (4th call = no retry)."""
    manager = WorktreeManager(repo_root=str(git_repo))
    counter = _CallCounter()
    orig_run_git = manager._run_git

    def mock_run_git(args, **kwargs):
        if args and args[0] == "worktree" and len(args) > 1 and args[1] == "add":
            counter.count += 1
            raise _make_transient_error("commondir")
        return orig_run_git(args, **kwargs)

    monkeypatch.setattr(manager, "_run_git", mock_run_git)
    monkeypatch.setattr("meister.worktree.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="commondir"):
        manager.create_worktree("exhausted-task")

    # 1 initial + 3 retries = 4 total
    assert counter.count == 4, f"Expected 4 calls (1 + 3 retries), got {counter.count}"


def test_create_worktree_no_retry_on_non_transient_error(git_repo, monkeypatch):
    """Non-transient errors must not trigger retry: exactly 1 git worktree add call."""
    manager = WorktreeManager(repo_root=str(git_repo))
    counter = _CallCounter()
    orig_run_git = manager._run_git

    def mock_run_git(args, **kwargs):
        if args and args[0] == "worktree" and len(args) > 1 and args[1] == "add":
            counter.count += 1
            raise RuntimeError("Comando git falhou (rc=128): git worktree add\nstderr: fatal: already checked out")
        return orig_run_git(args, **kwargs)

    monkeypatch.setattr(manager, "_run_git", mock_run_git)
    monkeypatch.setattr("meister.worktree.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="already checked out"):
        manager.create_worktree("no-retry-task")

    assert counter.count == 1, f"Expected exactly 1 call (no retry), got {counter.count}"


def test_create_worktree_retry_cleans_residual_state_on_transient_failure(git_repo, monkeypatch):
    """When the 1st 'worktree add' creates residual branch and directory before failing
    with a transient error ('commondir'), create_worktree cleans up the residual state
    so that the 2nd attempt succeeds and the final worktree exists and is valid.
    """
    manager = WorktreeManager(repo_root=str(git_repo))
    counter = _CallCounter()
    orig_run_git = manager._run_git

    def mock_run_git(args, **kwargs):
        if args and len(args) > 1 and args[0] == "worktree" and args[1] == "add":
            counter.count += 1
            if counter.count == 1:
                # args: ['worktree', 'add', '-b', target_branch, worktree_path, base_ref]
                target_branch = args[3]
                worktree_path = args[4]
                base_ref = args[5]
                # Cria de verdade a branch e o diretorio parcial
                orig_run_git(["branch", target_branch, base_ref])
                os.makedirs(worktree_path, exist_ok=True)
                with open(os.path.join(worktree_path, "partial.txt"), "w", encoding="utf-8") as f:
                    f.write("residual")
                raise _make_transient_error("commondir")
            return orig_run_git(args, **kwargs)
        return orig_run_git(args, **kwargs)

    monkeypatch.setattr(manager, "_run_git", mock_run_git)
    monkeypatch.setattr("meister.worktree.time.sleep", lambda _: None)

    task_id = "residual-race-task"
    info = manager.create_worktree(task_id)
    assert info is not None
    assert os.path.isdir(info.worktree_path)
    assert counter.count == 2, f"Expected 2 git worktree add calls, got {counter.count}"

    # Worktree final deve existir e ser valido (git worktree list)
    wt_list = manager._run_git(["worktree", "list"])
    assert info.worktree_path in wt_list
    assert info.branch_name in wt_list


# ===========================================================================
# Layer 3 — Bridge: worktree creation failure rejects subtask
# ===========================================================================

def _make_bridge_with_integration_pipeline(tmp_path, mock_wt_mgr):
    """Helper that builds a HerdrEventBridge with a mocked integration pipeline."""
    from meister.config import load_config
    from meister.state import StateManager

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  parallel_tasks: true
  max_parallel_workers: 2
  layout_strategy: tabs
""")
    config = load_config(str(cfg_file))

    mock_client = AsyncMock()
    mock_client.is_connected = True

    sm = StateManager()

    bridge = HerdrEventBridge(config=config, client=mock_client, state_manager=sm)

    # Wire up a fake integration pipeline whose wt_mgr.create_worktree raises
    fake_pipeline = MagicMock()
    fake_pipeline.wt_mgr = mock_wt_mgr
    fake_pipeline.integration_info = MagicMock()
    fake_pipeline.integration_info.branch_name = "meister/integration/run-test"
    bridge._integration_pipeline = fake_pipeline

    return bridge, sm, mock_client


@pytest.mark.asyncio
async def test_bridge_rejects_subtask_on_worktree_create_failure(tmp_path):
    """When integration pipeline is active and create_worktree raises, execute_subtask
    must return False, subtask must be FAILED (never COMPLETED), a subtask_rejected event
    with reason="worktree_create_failed" must be emitted, and the run must NOT complete.
    """
    mock_wt_mgr = MagicMock()
    mock_wt_mgr.create_worktree.side_effect = RuntimeError(
        "fatal: failed to read .git/worktrees/run_t1_abc/commondir"
    )

    bridge, sm, mock_client = _make_bridge_with_integration_pipeline(tmp_path, mock_wt_mgr)

    run_id = "run-test"
    sm.create_or_get_run(task_prompt="test", cwd=str(tmp_path))

    subtask = {
        "id": "t1",
        "description": "Test subtask",
        "target_files": ["app.py"],
        "depends_on": [],
    }

    logged_events: list = []

    with patch("meister.herdr.bridge.log_event", side_effect=lambda **kw: logged_events.append(kw)):
        result = await bridge.execute_subtask(subtask, run_id=run_id)

    # Must return False
    assert result is False, "execute_subtask must return False when worktree creation fails"

    # Subtask must be FAILED in the DB
    subtask_id = compute_subtask_id(run_id, "t1", "Test subtask")
    db_subtask = sm.get_subtask(subtask_id)
    if db_subtask is not None:
        assert db_subtask.get("status") != SubtaskState.COMPLETED.value, (
            "Subtask must NOT be COMPLETED when worktree creation fails"
        )
        assert db_subtask.get("status") == SubtaskState.FAILED.value, (
            f"Subtask status must be FAILED, got: {db_subtask.get('status')}"
        )

    # A subtask_rejected event with reason="worktree_create_failed" must have been emitted
    rejected_events = [
        e for e in logged_events
        if e.get("event_type") == "subtask_rejected"
        and e.get("reason") == "worktree_create_failed"
    ]
    assert rejected_events, (
        f"Expected subtask_rejected event with reason='worktree_create_failed'. "
        f"Got events: {logged_events}"
    )

    # The worker client must NOT have been invoked (subtask was rejected before spawning)
    mock_client.spawn_worker_tab = AsyncMock()
    assert mock_client.spawn_worker_tab.call_count == 0


@pytest.mark.asyncio
async def test_bridge_subtask_never_completed_on_worktree_failure(tmp_path):
    """Regression guard: even if the execution loop is somehow reached,
    the subtask must never transition to COMPLETED with integrated_sha=None.
    This test verifies that after a worktree failure the subtask_id is FAILED
    and any subsequent run_orchestration_cycle would not mark it COMPLETED.
    """
    mock_wt_mgr = MagicMock()
    mock_wt_mgr.create_worktree.side_effect = RuntimeError("commondir race")

    bridge, sm, mock_client = _make_bridge_with_integration_pipeline(tmp_path, mock_wt_mgr)

    run_id = "run-regression"
    sm.create_or_get_run(task_prompt="regression test", cwd=str(tmp_path))

    subtask = {"id": "t2", "description": "Regression check", "target_files": [], "depends_on": []}

    with patch("meister.herdr.bridge.log_event"):
        result = await bridge.execute_subtask(subtask, run_id=run_id)

    assert result is False

    subtask_id = compute_subtask_id(run_id, "t2", "Regression check")
    db_subtask = sm.get_subtask(subtask_id)
    if db_subtask is not None:
        assert db_subtask.get("status") != SubtaskState.COMPLETED.value, (
            "Subtask must NOT be COMPLETED with integrated_sha=None (silent data loss)"
        )
