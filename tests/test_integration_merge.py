import os
import subprocess
import pytest

from meister.gate import DeterministicGate
from meister.worktree import WorktreeManager, IntegrationPipeline


@pytest.fixture
def git_test_repo(tmp_path):
    """Fixture that initializes a git repository configured with a working pytest suite."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cwd = str(repo_dir)

    subprocess.run(["git", "init", "-b", "main"], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@meisterrouter.local"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "Meister CI"], cwd=cwd, check=True)

    # Base application code
    app_file = repo_dir / "app.py"
    app_file.write_text("def add(a, b):\n    return a + b\n")

    # Tests directory with pytest suite
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_app.py"
    test_file.write_text("from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")

    # pytest config
    pytest_ini = repo_dir / "pytest.ini"
    pytest_ini.write_text("[pytest]\npythonpath = .\n")

    gitignore = repo_dir / ".gitignore"
    gitignore.write_text("__pycache__/\n*.pyc\n*.pyo\n.pytest_cache/\n")

    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=cwd, check=True)

    return repo_dir


def test_worktree_commit_merge_and_rollback(git_test_repo):
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)

    # 1. Create a worktree for task A
    wt_a = wt_mgr.create_worktree("task-a")
    assert os.path.exists(wt_a.worktree_path)

    # 2. Modify app.py in task A
    app_in_a = os.path.join(wt_a.worktree_path, "app.py")
    with open(app_in_a, "w", encoding="utf-8") as f:
        f.write("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")

    # 3. Commit changes in worktree
    sha = wt_mgr.commit_worktree(wt_a.worktree_path, "feat: add sub function")
    assert sha is not None
    assert len(sha) >= 7

    # 4. Create an integration worktree
    int_wt = wt_mgr.create_worktree("integration-test", branch_name="meister/integration/test-run")
    assert os.path.exists(int_wt.worktree_path)

    # 5. Merge task A into integration worktree
    merged, rollback_sha = wt_mgr.merge_branch_into(
        source_branch=wt_a.branch_name,
        target_worktree_path=int_wt.worktree_path,
        message="Merge task A",
    )
    assert merged is True
    assert rollback_sha is not None

    # Check that app.py in integration worktree now contains sub function
    app_in_int = os.path.join(int_wt.worktree_path, "app.py")
    with open(app_in_int, "r", encoding="utf-8") as f:
        content = f.read()
    assert "def sub(a, b):" in content

    # 6. Test rollback restores previous commit
    wt_mgr.rollback_merge(int_wt.worktree_path, rollback_sha)
    with open(app_in_int, "r", encoding="utf-8") as f:
        rolled_back_content = f.read()
    assert "def sub(a, b):" not in rolled_back_content

    # Cleanup
    wt_mgr.cleanup_worktree(wt_a.task_id, force=True)
    wt_mgr.cleanup_worktree(int_wt.task_id, force=True)


def test_integration_pipeline_successful_flow(git_test_repo):
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-success")
    assert int_info is not None
    assert int_info.branch_name == "meister/integration/run-success"

    # Create subtask worktree based on integration branch
    subtask_wt = wt_mgr.create_worktree("subtask-1", base_ref=int_info.branch_name)

    # Worker modifies app.py and adds test
    app_file = os.path.join(subtask_wt.worktree_path, "app.py")
    with open(app_file, "a", encoding="utf-8") as f:
        f.write("\ndef mul(a, b):\n    return a * b\n")

    test_file = os.path.join(subtask_wt.worktree_path, "tests", "test_app.py")
    with open(test_file, "a", encoding="utf-8") as f:
        f.write("\ndef test_mul():\n    from app import mul\n    assert mul(2, 3) == 6\n")

    # Integrate subtask with scope check
    ok, msg = pipeline.integrate_subtask(
        subtask_wt=subtask_wt,
        target_files=["app.py", "tests/test_app.py"],
        commit_message="feat: add multiply function",
    )
    assert ok is True
    assert "integrada com sucesso" in msg

    # Finish integration with fast-forward
    ok_finish, finish_msg = pipeline.finish_integration(fast_forward=True)
    assert ok_finish is True
    assert "sucesso" in finish_msg

    # Verify app.py in main repo received changes via fast-forward
    with open(os.path.join(repo_path, "app.py"), "r", encoding="utf-8") as f:
        main_content = f.read()
    assert "def mul(a, b):" in main_content


def test_integration_pipeline_rejects_scope_violation(git_test_repo):
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-scope-violation")

    subtask_wt = wt_mgr.create_worktree("subtask-rogue", base_ref=int_info.branch_name)

    # Worker modifies allowed file and an unauthorized file
    with open(os.path.join(subtask_wt.worktree_path, "app.py"), "a", encoding="utf-8") as f:
        f.write("\ndef rogue(): pass\n")

    with open(os.path.join(subtask_wt.worktree_path, "unauthorized.py"), "w", encoding="utf-8") as f:
        f.write("secret = 123\n")

    # Subtask only allows app.py
    ok, err = pipeline.integrate_subtask(
        subtask_wt=subtask_wt,
        target_files=["app.py"],
    )
    assert ok is False
    assert "Violação de escopo" in err
    assert "unauthorized.py" in err

    # Abort integration
    pipeline.abort_integration()
    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)


def test_integration_pipeline_gate_failure_triggers_rollback(git_test_repo):
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-gate-failure")

    subtask_wt = wt_mgr.create_worktree("subtask-broken", base_ref=int_info.branch_name)

    # Worker breaks existing test
    app_file = os.path.join(subtask_wt.worktree_path, "app.py")
    with open(app_file, "w", encoding="utf-8") as f:
        f.write("def add(a, b):\n    return 0  # Broken logic\n")

    # Integrate subtask: worktree gate must catch broken test and reject before merge
    ok, err = pipeline.integrate_subtask(
        subtask_wt=subtask_wt,
        target_files=["app.py"],
    )
    assert ok is False
    assert "Portão determinístico falhou no worktree do worker" in err

    # Integration worktree remained unaffected
    with open(os.path.join(int_info.worktree_path, "app.py"), "r", encoding="utf-8") as f:
        int_content = f.read()
    assert "return a + b" in int_content

    pipeline.abort_integration()
    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)


def test_sequential_merge_of_two_subtasks(git_test_repo):
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-two-subtasks")

    # Subtask 1: adds module_x.py
    wt1 = wt_mgr.create_worktree("task-1", base_ref=int_info.branch_name)
    with open(os.path.join(wt1.worktree_path, "module_x.py"), "w", encoding="utf-8") as f:
        f.write("def func_x():\n    return 'x'\n")
    with open(os.path.join(wt1.worktree_path, "tests", "test_x.py"), "w", encoding="utf-8") as f:
        f.write("from module_x import func_x\ndef test_x():\n    assert func_x() == 'x'\n")

    ok1, msg1 = pipeline.integrate_subtask(wt1, target_files=["module_x.py", "tests/test_x.py"])
    assert ok1 is True
    wt_mgr.cleanup_worktree(wt1.task_id, force=True)

    # Subtask 2: adds module_y.py
    wt2 = wt_mgr.create_worktree("task-2", base_ref=int_info.branch_name)
    with open(os.path.join(wt2.worktree_path, "module_y.py"), "w", encoding="utf-8") as f:
        f.write("def func_y():\n    return 'y'\n")
    with open(os.path.join(wt2.worktree_path, "tests", "test_y.py"), "w", encoding="utf-8") as f:
        f.write("from module_y import func_y\ndef test_y():\n    assert func_y() == 'y'\n")

    ok2, msg2 = pipeline.integrate_subtask(wt2, target_files=["module_y.py", "tests/test_y.py"])
    assert ok2 is True, f"integrate_subtask 2 failed: {msg2}"
    wt_mgr.cleanup_worktree(wt2.task_id, force=True)

    # Finish integration
    ok_finish, finish_msg = pipeline.finish_integration(fast_forward=True)
    assert ok_finish is True

    # Both modules should exist in main repo
    assert os.path.exists(os.path.join(repo_path, "module_x.py"))
    assert os.path.exists(os.path.join(repo_path, "module_y.py"))


def test_finish_integration_fails_closed_when_repo_is_dirty(git_test_repo):
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-dirty-repo")

    # Integrate a valid subtask
    wt1 = wt_mgr.create_worktree("task-dirty-subtask", base_ref=int_info.branch_name)
    with open(os.path.join(wt1.worktree_path, "new_file.py"), "w", encoding="utf-8") as f:
        f.write("# valid new file\n")
    ok, msg = pipeline.integrate_subtask(wt1, target_files=["new_file.py"])
    assert ok is True
    wt_mgr.cleanup_worktree(wt1.task_id, force=True)

    # Dirty the main repo with unstaged tracked modification
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True).stdout.strip()
    with open(os.path.join(repo_path, "app.py"), "a", encoding="utf-8") as f:
        f.write("\n# uncommitted change in main repo\n")

    # Attempting to finish integration with fast_forward=True must FAIL CLOSED
    ok_finish, err_finish = pipeline.finish_integration(fast_forward=True)
    assert ok_finish is False
    assert "dirty" in err_finish.lower() or "fast-forward" in err_finish.lower()

    # Main HEAD must NOT have moved
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True).stdout.strip()
    assert head_before == head_after

    # Integration worktree/branch must be preserved for inspection
    assert os.path.exists(int_info.worktree_path)

    # Cleanup test resources
    pipeline.abort_integration()


def test_resumed_integration_preserves_completed_subtasks(git_test_repo):
    repo_path = str(git_test_repo)
    db_path = os.path.join(repo_path, ".meister", "meister.db")
    from meister.state import StateManager, SubtaskState
    sm1 = StateManager(db_path=db_path)
    run_id = "test-resume-run"
    sm1.create_or_get_run("task prompt", cwd=repo_path, force_run_id=run_id)
    subs = sm1.add_subtasks(run_id, [
        {"id": "t1", "description": "task 1", "target_files": ["calc.py", "tests/test_calc.py"]},
        {"id": "t2", "description": "task 2", "target_files": ["text.py", "tests/test_text.py"]},
    ])
    sub1_id = subs[0]["subtask_id"]
    sub2_id = subs[1]["subtask_id"]

    wt_mgr1 = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)
    pipeline1 = IntegrationPipeline(wt_mgr1, gate=gate)
    int_info1 = pipeline1.start_integration(run_id, state_manager=sm1)

    # Subtask 1: adds calc.py and tests/test_calc.py
    wt1 = wt_mgr1.create_worktree("t1", base_ref=int_info1.branch_name)
    with open(os.path.join(wt1.worktree_path, "calc.py"), "w", encoding="utf-8") as f:
        f.write("def mul(a, b):\n    return a * b\n")
    with open(os.path.join(wt1.worktree_path, "tests", "test_calc.py"), "w", encoding="utf-8") as f:
        f.write("from calc import mul\ndef test_mul():\n    assert mul(2, 3) == 6\n")

    ok1, msg1 = pipeline1.integrate_subtask(wt1, target_files=["calc.py", "tests/test_calc.py"])
    assert ok1 is True
    # Record integrated SHA in SQLite
    t1_sha = getattr(pipeline1, "last_integrated_sha", None)
    sm1.transition_subtask(sub1_id, to_state=SubtaskState.RUNNING)
    sm1.transition_subtask(sub1_id, to_state=SubtaskState.COMPLETED, integrated_sha=t1_sha)
    wt_mgr1.cleanup_worktree(wt1.task_id, force=True)

    # Simulate crash / new process restart:
    # A new StateManager, new WorktreeManager, and new IntegrationPipeline
    sm2 = StateManager(db_path=db_path)
    wt_mgr2 = WorktreeManager(repo_root=repo_path)
    wt_mgr2.cleanup_orphans(exclude_run_id=run_id)

    pipeline2 = IntegrationPipeline(wt_mgr2, gate=gate)
    int_info2 = pipeline2.start_integration(run_id, state_manager=sm2)

    # Subtask 2: adds text.py and tests/test_text.py
    wt2 = wt_mgr2.create_worktree("t2", base_ref=int_info2.branch_name)
    with open(os.path.join(wt2.worktree_path, "text.py"), "w", encoding="utf-8") as f:
        f.write("def shout(name):\n    return name.upper()\n")
    with open(os.path.join(wt2.worktree_path, "tests", "test_text.py"), "w", encoding="utf-8") as f:
        f.write("from text import shout\ndef test_shout():\n    assert shout('hi') == 'HI'\n")

    ok2, msg2 = pipeline2.integrate_subtask(wt2, target_files=["text.py", "tests/test_text.py"])
    assert ok2 is True, f"Subtask 2 integration failed: {msg2}"
    t2_sha = getattr(pipeline2, "last_integrated_sha", None)
    sm2.transition_subtask(sub2_id, to_state=SubtaskState.RUNNING)
    sm2.transition_subtask(sub2_id, to_state=SubtaskState.COMPLETED, integrated_sha=t2_sha)
    wt_mgr2.cleanup_worktree(wt2.task_id, force=True)

    # Finish integration with fast_forward=True
    ok_ff, ff_msg = pipeline2.finish_integration(fast_forward=True)
    assert ok_ff is True, f"Finish integration failed: {ff_msg}"

    # Main repository must contain BOTH t1 (calc.py) and t2 (text.py)
    assert os.path.exists(os.path.join(repo_path, "calc.py")), "calc.py from t1 missing on main!"
    assert os.path.exists(os.path.join(repo_path, "text.py")), "text.py from t2 missing on main!"
    with open(os.path.join(repo_path, "calc.py"), "r", encoding="utf-8") as f:
        assert "def mul" in f.read()
    with open(os.path.join(repo_path, "text.py"), "r", encoding="utf-8") as f:
        assert "def shout" in f.read()


def test_integration_ancestry_invariant_blocks_lost_subtask(git_test_repo):
    repo_path = str(git_test_repo)
    db_path = os.path.join(repo_path, ".meister", "meister.db")
    from meister.state import StateManager, SubtaskState
    sm = StateManager(db_path=db_path)
    run_id = "test-ancestry-run"
    sm.create_or_get_run("task prompt", cwd=repo_path, force_run_id=run_id)
    subs = sm.add_subtasks(run_id, [
        {"id": "t1", "description": "task 1", "target_files": ["calc.py"]},
    ])
    sub1_id = subs[0]["subtask_id"]

    # Mark t1 as COMPLETED with a commit SHA that is NOT in integration branch
    # Create an orphan commit in a separate branch
    wt_mgr = WorktreeManager(repo_root=repo_path)
    fake_wt = wt_mgr.create_worktree("fake_orphan")
    with open(os.path.join(fake_wt.worktree_path, "orphan.py"), "w", encoding="utf-8") as f:
        f.write("# orphan commit\n")
    orphan_sha = wt_mgr.commit_worktree(fake_wt.worktree_path, "orphan commit")
    wt_mgr.cleanup_worktree("fake_orphan", delete_branch=True, force=True)

    sm.transition_subtask(sub1_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub1_id, to_state=SubtaskState.COMPLETED, integrated_sha=orphan_sha)

    # Start a fresh integration pipeline from HEAD without orphan_sha
    gate = DeterministicGate(repo_path)
    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    pipeline.start_integration(run_id)
    pipeline.state_manager = sm

    # Invariant must block fast_forward because orphan_sha is not in integration branch
    ok_ff, ff_err = pipeline.finish_integration(fast_forward=True)
    assert ok_ff is False
    assert "invariante" in ff_err.lower() or "ancestral" in ff_err.lower()


def test_integration_subtask_noop_with_target_files_rejected(git_test_repo):
    """(a) diff vazio + target_files sem histórico -> False, mensagem contém 'sem alterações'."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-noop-rejected")

    subtask_wt = wt_mgr.create_worktree("noop-subtask", base_ref=int_info.branch_name)
    ok, msg = pipeline.integrate_subtask(subtask_wt, target_files=["app.py"])

    assert ok is False
    assert "sem alterações" in msg.lower()
    assert pipeline.last_integrated_sha is None
    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
    pipeline.abort_integration()


def test_integration_subtask_noop_without_target_files_accepted(git_test_repo):
    """(b) diff vazio sem target_files -> True."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-noop-accepted")

    subtask_wt = wt_mgr.create_worktree("noop-subtask-no-targets", base_ref=int_info.branch_name)
    ok, msg = pipeline.integrate_subtask(subtask_wt, target_files=None)

    assert ok is True
    assert "nenhuma alteração" in msg.lower()
    assert pipeline.last_integrated_sha is None
    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
    pipeline.abort_integration()


def test_integration_subtask_noop_with_target_files_already_integrated_accepted(git_test_repo):
    """(c) diff vazio + target_files, mas integração já tem subtask(<id>): ... -> True e last_integrated_sha."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-noop-resume")

    # Step 1: Subtask t1 initially makes changes and integrates successfully
    subtask_wt1 = wt_mgr.create_worktree("t1", base_ref=int_info.branch_name)
    app_file = os.path.join(subtask_wt1.worktree_path, "app.py")
    with open(app_file, "a", encoding="utf-8") as f:
        f.write("\ndef mul(a, b):\n    return a * b\n")
    test_file = os.path.join(subtask_wt1.worktree_path, "tests", "test_app.py")
    with open(test_file, "a", encoding="utf-8") as f:
        f.write("\ndef test_mul():\n    from app import mul\n    assert mul(2, 3) == 6\n")

    ok1, msg1 = pipeline.integrate_subtask(subtask_wt1, target_files=["app.py", "tests/test_app.py"])
    assert ok1 is True
    first_sha = pipeline.last_integrated_sha
    assert first_sha is not None
    wt_mgr.cleanup_worktree(subtask_wt1.task_id, force=True)

    # Step 2: On resume, t1 is re-executed on top of the integration branch (already has changes)
    subtask_wt1_resume = wt_mgr.create_worktree("t1", base_ref=int_info.branch_name)
    # Worker modifies nothing (code already present)
    ok_res, msg_res = pipeline.integrate_subtask(subtask_wt1_resume, target_files=["app.py", "tests/test_app.py"])
    assert ok_res is True
    assert "já integrada anteriormente" in msg_res.lower()
    assert pipeline.last_integrated_sha == first_sha

    wt_mgr.cleanup_worktree(subtask_wt1_resume.task_id, force=True)
    pipeline.abort_integration()


def test_integration_last_integrated_sha_does_not_leak_to_noop(git_test_repo):
    """(d) last_integrated_sha de uma subtarefa anterior NÃO vaza para um no-op."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-sha-leak")

    # Step 1: Subtask t1 succeeds and sets last_integrated_sha
    subtask_wt1 = wt_mgr.create_worktree("t1", base_ref=int_info.branch_name)
    app_file = os.path.join(subtask_wt1.worktree_path, "app.py")
    with open(app_file, "a", encoding="utf-8") as f:
        f.write("\ndef mul(a, b):\n    return a * b\n")
    test_file = os.path.join(subtask_wt1.worktree_path, "tests", "test_app.py")
    with open(test_file, "a", encoding="utf-8") as f:
        f.write("\ndef test_mul():\n    from app import mul\n    assert mul(2, 3) == 6\n")

    ok1, _ = pipeline.integrate_subtask(subtask_wt1, target_files=["app.py", "tests/test_app.py"])
    assert ok1 is True
    t1_sha = pipeline.last_integrated_sha
    assert t1_sha is not None
    wt_mgr.cleanup_worktree(subtask_wt1.task_id, force=True)

    # Step 2: Subtask t2 is a no-op without target_files -> last_integrated_sha must become None, not t1_sha
    subtask_wt2 = wt_mgr.create_worktree("t2", base_ref=int_info.branch_name)
    ok2, _ = pipeline.integrate_subtask(subtask_wt2, target_files=None)
    assert ok2 is True
    assert pipeline.last_integrated_sha is None
    wt_mgr.cleanup_worktree(subtask_wt2.task_id, force=True)

    # Step 3: Subtask t3 is a no-op with target_files without history -> rejected, last_integrated_sha must be None
    subtask_wt3 = wt_mgr.create_worktree("t3", base_ref=int_info.branch_name)
    ok3, _ = pipeline.integrate_subtask(subtask_wt3, target_files=["new_module.py"])
    assert ok3 is False
    assert pipeline.last_integrated_sha is None
    wt_mgr.cleanup_worktree(subtask_wt3.task_id, force=True)

    pipeline.abort_integration()


def test_integration_worker_committed_all_changes_integrated(git_test_repo):
    """(B) worker edita app.py e commita tudo no worktree: ok is True, mergeado, last_integrated_sha = worker HEAD."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-worker-commits-b")

    subtask_wt = wt_mgr.create_worktree("worker-b", base_ref=int_info.branch_name)
    subprocess.run(["git", "config", "user.name", "Worker Dev"], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "config", "user.email", "worker@example.com"], cwd=subtask_wt.worktree_path, check=True)

    app_file = os.path.join(subtask_wt.worktree_path, "app.py")
    with open(app_file, "a", encoding="utf-8") as f:
        f.write("\ndef worker_func():\n    return 'from_worker'\n")

    subprocess.run(["git", "add", "app.py"], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", "worker: implement worker_func"], cwd=subtask_wt.worktree_path, check=True)
    worker_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=subtask_wt.worktree_path, capture_output=True, text=True, check=True
    ).stdout.strip()

    ok, msg = pipeline.integrate_subtask(subtask_wt, target_files=["app.py"])
    assert ok is True, f"integrate_subtask failed: {msg}"
    assert pipeline.last_integrated_sha == worker_head

    # Verify integration worktree contains the new function
    with open(os.path.join(int_info.worktree_path, "app.py"), "r", encoding="utf-8") as f:
        int_content = f.read()
    assert "def worker_func():" in int_content

    # Verify merge entered integration git log
    log_out = wt_mgr._run_git(["log", "--format=%H", "HEAD"], cwd=int_info.worktree_path)
    assert worker_head in log_out.splitlines()

    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
    pipeline.abort_integration()


def test_integration_worker_committed_and_loose_edits_both_integrated(git_test_repo):
    """(C) commita e deixa uma segunda edição solta em app.py: ok is True e a integração contém as DUAS funções."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-worker-commits-c")

    subtask_wt = wt_mgr.create_worktree("worker-c", base_ref=int_info.branch_name)
    subprocess.run(["git", "config", "user.name", "Worker Dev"], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "config", "user.email", "worker@example.com"], cwd=subtask_wt.worktree_path, check=True)

    app_file = os.path.join(subtask_wt.worktree_path, "app.py")
    with open(app_file, "a", encoding="utf-8") as f:
        f.write("\ndef committed_func():\n    return 'committed'\n")

    subprocess.run(["git", "add", "app.py"], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", "worker: committed func"], cwd=subtask_wt.worktree_path, check=True)

    with open(app_file, "a", encoding="utf-8") as f:
        f.write("\ndef loose_func():\n    return 'loose'\n")

    ok, msg = pipeline.integrate_subtask(subtask_wt, target_files=["app.py"])
    assert ok is True, f"integrate_subtask failed: {msg}"

    with open(os.path.join(int_info.worktree_path, "app.py"), "r", encoding="utf-8") as f:
        int_content = f.read()
    assert "def committed_func():" in int_content
    assert "def loose_func():" in int_content

    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
    pipeline.abort_integration()


def test_integration_worker_commits_out_of_scope_file_rejected(git_test_repo):
    """(D) worker commita um arquivo FORA de target_files: ok is False com violação de escopo, integração intacta."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-worker-commits-d")

    subtask_wt = wt_mgr.create_worktree("worker-d", base_ref=int_info.branch_name)
    subprocess.run(["git", "config", "user.name", "Worker Dev"], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "config", "user.email", "worker@example.com"], cwd=subtask_wt.worktree_path, check=True)

    # Worker modifies app.py and also commits an unauthorized file
    with open(os.path.join(subtask_wt.worktree_path, "app.py"), "a", encoding="utf-8") as f:
        f.write("\ndef allowed_func():\n    return True\n")
    with open(os.path.join(subtask_wt.worktree_path, "secret_out_of_scope.py"), "w", encoding="utf-8") as f:
        f.write("SECRET = 42\n")

    subprocess.run(["git", "add", "."], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", "worker: rogue changes"], cwd=subtask_wt.worktree_path, check=True)

    ok, msg = pipeline.integrate_subtask(subtask_wt, target_files=["app.py"])
    assert ok is False
    assert "violação de escopo" in msg.lower()
    assert "secret_out_of_scope.py" in msg

    # Integration worktree remains intact: out of scope file does NOT exist
    assert not os.path.exists(os.path.join(int_info.worktree_path, "secret_out_of_scope.py"))
    with open(os.path.join(int_info.worktree_path, "app.py"), "r", encoding="utf-8") as f:
        assert "def allowed_func():" not in f.read()

    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
    pipeline.abort_integration()


def test_integration_worker_commits_failing_gate_rejected(git_test_repo):
    """(E) worker commita código que faz o gate falhar (teste quebrado): ok is False, integração intacta."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-worker-commits-e")

    subtask_wt = wt_mgr.create_worktree("worker-e", base_ref=int_info.branch_name)
    subprocess.run(["git", "config", "user.name", "Worker Dev"], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "config", "user.email", "worker@example.com"], cwd=subtask_wt.worktree_path, check=True)

    app_file = os.path.join(subtask_wt.worktree_path, "app.py")
    with open(app_file, "w", encoding="utf-8") as f:
        f.write("def add(a, b):\n    return 0  # breaks test_add\n")

    subprocess.run(["git", "add", "app.py"], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", "worker: broken test commit"], cwd=subtask_wt.worktree_path, check=True)

    ok, msg = pipeline.integrate_subtask(subtask_wt, target_files=["app.py"])
    assert ok is False
    assert "portão determinístico falhou" in msg.lower()

    # Integration worktree remains intact
    with open(os.path.join(int_info.worktree_path, "app.py"), "r", encoding="utf-8") as f:
        assert "return 0" not in f.read()

    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
    pipeline.abort_integration()


def test_integration_regression_worker_without_commit_or_change(git_test_repo):
    """(F) regressão: worker sem commit e sem mudança + target_files continua False com 'sem alterações'; sem target_files continua True."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-worker-commits-f")

    # F1: with target_files -> False with 'sem alterações'
    subtask_wt1 = wt_mgr.create_worktree("worker-f1", base_ref=int_info.branch_name)
    ok1, msg1 = pipeline.integrate_subtask(subtask_wt1, target_files=["app.py"])
    assert ok1 is False
    assert "sem alterações" in msg1.lower()
    assert pipeline.last_integrated_sha is None
    wt_mgr.cleanup_worktree(subtask_wt1.task_id, force=True)

    # F2: without target_files -> True with 'nenhuma alteração'
    subtask_wt2 = wt_mgr.create_worktree("worker-f2", base_ref=int_info.branch_name)
    ok2, msg2 = pipeline.integrate_subtask(subtask_wt2, target_files=None)
    assert ok2 is True
    assert "nenhuma alteração" in msg2.lower()
    assert pipeline.last_integrated_sha is None
    wt_mgr.cleanup_worktree(subtask_wt2.task_id, force=True)

    pipeline.abort_integration()


def test_integration_multiple_worker_commits_all_integrated(git_test_repo):
    """(G) múltiplos commits do worker: todos entram."""
    repo_path = str(git_test_repo)
    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)

    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration("run-worker-commits-g")

    subtask_wt = wt_mgr.create_worktree("worker-g", base_ref=int_info.branch_name)
    subprocess.run(["git", "config", "user.name", "Worker Dev"], cwd=subtask_wt.worktree_path, check=True)
    subprocess.run(["git", "config", "user.email", "worker@example.com"], cwd=subtask_wt.worktree_path, check=True)

    app_file = os.path.join(subtask_wt.worktree_path, "app.py")
    commits = []
    for i in range(1, 4):
        with open(app_file, "a", encoding="utf-8") as f:
            f.write(f"\ndef func_{i}():\n    return {i}\n")
        subprocess.run(["git", "add", "app.py"], cwd=subtask_wt.worktree_path, check=True)
        subprocess.run(["git", "commit", "-m", f"worker: add func_{i}"], cwd=subtask_wt.worktree_path, check=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=subtask_wt.worktree_path, capture_output=True, text=True, check=True
        ).stdout.strip()
        commits.append(sha)

    final_worker_head = commits[-1]

    ok, msg = pipeline.integrate_subtask(subtask_wt, target_files=["app.py"])
    assert ok is True, f"integrate_subtask failed: {msg}"
    assert pipeline.last_integrated_sha == final_worker_head

    # All functions in app.py in integration worktree
    with open(os.path.join(int_info.worktree_path, "app.py"), "r", encoding="utf-8") as f:
        content = f.read()
    for i in range(1, 4):
        assert f"def func_{i}():" in content

    # All commits are ancestors of integration HEAD
    for sha in commits:
        res = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=int_info.worktree_path,
        )
        assert res.returncode == 0, f"Commit {sha} is not an ancestor of integration HEAD"

    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
    pipeline.abort_integration()


def test_rebuild_ancestry_after_clean_abort_single_subtask(git_test_repo):
    """(1) Reconstrução após falha limpa: 1 subtarefa COMPLETED preserva ancestralidade."""
    repo_path = str(git_test_repo)
    db_path = os.path.join(repo_path, ".meister", "meister.db")
    from meister.state import StateManager, SubtaskState

    sm = StateManager(db_path=db_path)
    run_id = "test-rebuild-single"
    sm.create_or_get_run("task prompt", cwd=repo_path, force_run_id=run_id)
    subs = sm.add_subtasks(
        run_id,
        [{"id": "t1", "description": "task 1", "target_files": ["calc.py", "tests/test_calc.py"]}],
    )
    sub1_id = subs[0]["subtask_id"]

    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)
    pipeline1 = IntegrationPipeline(wt_mgr, gate=gate)
    int_info1 = pipeline1.start_integration(run_id, state_manager=sm)

    # Subtask 1: adds calc.py and tests/test_calc.py
    wt1 = wt_mgr.create_worktree("t1", base_ref=int_info1.branch_name)
    with open(os.path.join(wt1.worktree_path, "calc.py"), "w", encoding="utf-8") as f:
        f.write("def mul(a, b):\n    return a * b\n")
    with open(os.path.join(wt1.worktree_path, "tests", "test_calc.py"), "w", encoding="utf-8") as f:
        f.write("from calc import mul\ndef test_mul():\n    assert mul(2, 3) == 6\n")

    ok1, msg1 = pipeline1.integrate_subtask(wt1, target_files=["calc.py", "tests/test_calc.py"])
    assert ok1 is True, f"Integrate t1 failed: {msg1}"
    t1_sha = pipeline1.last_integrated_sha
    assert t1_sha is not None

    sm.transition_subtask(sub1_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub1_id, to_state=SubtaskState.COMPLETED, integrated_sha=t1_sha)
    wt_mgr.cleanup_worktree(wt1.task_id, force=True)

    # Simula falha limpa: abort_integration arquiva e apaga a branch de integração
    pipeline1.abort_integration()

    # Confirma que a branch de integração não existe mais
    res_b = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/meister/integration/{run_id}"],
        cwd=repo_path,
        capture_output=True,
    )
    assert res_b.returncode != 0

    import time
    time.sleep(1.05)

    # Novo pipeline para o mesmo run_id: dispara reconstrução a partir do SQLite
    pipeline2 = IntegrationPipeline(wt_mgr, gate=gate)
    int_info2 = pipeline2.start_integration(run_id, state_manager=sm)

    # Assert: o arquivo existe na branch reconstruída
    calc_path = os.path.join(int_info2.worktree_path, "calc.py")
    assert os.path.exists(calc_path), "calc.py ausente na branch de integração reconstruída"
    with open(calc_path, "r", encoding="utf-8") as f:
        assert "def mul" in f.read()

    # Assert: commit original é ancestral do HEAD (invariante passa)
    anc_ok, anc_msg = pipeline2.verify_completed_subtasks_ancestry()
    assert anc_ok is True, f"verify_completed_subtasks_ancestry falhou: {anc_msg}"

    pipeline2.abort_integration()


def test_rebuild_ancestry_two_completed_subtasks(git_test_repo):
    """(2) Duas subtarefas COMPLETED preservam ancestralidade e passam na validação final."""
    repo_path = str(git_test_repo)
    db_path = os.path.join(repo_path, ".meister", "meister.db")
    from meister.state import StateManager, SubtaskState

    sm = StateManager(db_path=db_path)
    run_id = "test-rebuild-two"
    sm.create_or_get_run("task prompt", cwd=repo_path, force_run_id=run_id)
    subs = sm.add_subtasks(
        run_id,
        [
            {"id": "t1", "description": "task 1", "target_files": ["calc.py", "tests/test_calc.py"]},
            {"id": "t2", "description": "task 2", "target_files": ["text.py", "tests/test_text.py"]},
        ],
    )
    sub1_id = subs[0]["subtask_id"]
    sub2_id = subs[1]["subtask_id"]

    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)
    pipeline1 = IntegrationPipeline(wt_mgr, gate=gate)
    int_info1 = pipeline1.start_integration(run_id, state_manager=sm)

    # Subtask 1
    wt1 = wt_mgr.create_worktree("t1", base_ref=int_info1.branch_name)
    with open(os.path.join(wt1.worktree_path, "calc.py"), "w", encoding="utf-8") as f:
        f.write("def mul(a, b):\n    return a * b\n")
    with open(os.path.join(wt1.worktree_path, "tests", "test_calc.py"), "w", encoding="utf-8") as f:
        f.write("from calc import mul\ndef test_mul():\n    assert mul(2, 3) == 6\n")

    ok1, msg1 = pipeline1.integrate_subtask(wt1, target_files=["calc.py", "tests/test_calc.py"])
    assert ok1 is True, msg1
    t1_sha = pipeline1.last_integrated_sha
    sm.transition_subtask(sub1_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub1_id, to_state=SubtaskState.COMPLETED, integrated_sha=t1_sha)
    wt_mgr.cleanup_worktree(wt1.task_id, force=True)

    # Subtask 2
    wt2 = wt_mgr.create_worktree("t2", base_ref=int_info1.branch_name)
    with open(os.path.join(wt2.worktree_path, "text.py"), "w", encoding="utf-8") as f:
        f.write("def shout(msg):\n    return msg.upper()\n")
    with open(os.path.join(wt2.worktree_path, "tests", "test_text.py"), "w", encoding="utf-8") as f:
        f.write("from text import shout\ndef test_shout():\n    assert shout('hi') == 'HI'\n")

    ok2, msg2 = pipeline1.integrate_subtask(wt2, target_files=["text.py", "tests/test_text.py"])
    assert ok2 is True, msg2
    t2_sha = pipeline1.last_integrated_sha
    sm.transition_subtask(sub2_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub2_id, to_state=SubtaskState.COMPLETED, integrated_sha=t2_sha)
    wt_mgr.cleanup_worktree(wt2.task_id, force=True)

    # Falha limpa
    pipeline1.abort_integration()

    # Reconstrução
    pipeline2 = IntegrationPipeline(wt_mgr, gate=gate)
    int_info2 = pipeline2.start_integration(run_id, state_manager=sm)

    # Ambos os arquivos existem na branch de integração reconstruída
    assert os.path.exists(os.path.join(int_info2.worktree_path, "calc.py"))
    assert os.path.exists(os.path.join(int_info2.worktree_path, "text.py"))

    # Ambas ancestrais
    anc_ok, anc_msg = pipeline2.verify_completed_subtasks_ancestry()
    assert anc_ok is True, f"verify_completed_subtasks_ancestry falhou: {anc_msg}"

    val_ok, val_msg = pipeline2.validate_final_integration()
    assert val_ok is True, f"validate_final_integration falhou: {val_msg}"

    pipeline2.abort_integration()


def test_rebuild_ancestry_resume_complete_and_finish(git_test_repo):
    """(3) Retomada completa: integra 3a subtarefa após reconstrução e finish_integration(fast_forward=True) sucede."""
    repo_path = str(git_test_repo)
    db_path = os.path.join(repo_path, ".meister", "meister.db")
    from meister.state import StateManager, SubtaskState

    sm = StateManager(db_path=db_path)
    run_id = "test-rebuild-resume"
    sm.create_or_get_run("task prompt", cwd=repo_path, force_run_id=run_id)
    subs = sm.add_subtasks(
        run_id,
        [
            {"id": "t1", "description": "task 1", "target_files": ["calc.py", "tests/test_calc.py"]},
            {"id": "t2", "description": "task 2", "target_files": ["text.py", "tests/test_text.py"]},
            {"id": "t3", "description": "task 3", "target_files": ["math_ops.py", "tests/test_math.py"]},
        ],
    )
    sub1_id = subs[0]["subtask_id"]
    sub2_id = subs[1]["subtask_id"]
    sub3_id = subs[2]["subtask_id"]

    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)
    pipeline1 = IntegrationPipeline(wt_mgr, gate=gate)
    int_info1 = pipeline1.start_integration(run_id, state_manager=sm)

    # Integra t1 e t2
    wt1 = wt_mgr.create_worktree("t1", base_ref=int_info1.branch_name)
    with open(os.path.join(wt1.worktree_path, "calc.py"), "w", encoding="utf-8") as f:
        f.write("def mul(a, b):\n    return a * b\n")
    with open(os.path.join(wt1.worktree_path, "tests", "test_calc.py"), "w", encoding="utf-8") as f:
        f.write("from calc import mul\ndef test_mul():\n    assert mul(2, 3) == 6\n")
    ok1, _ = pipeline1.integrate_subtask(wt1, target_files=["calc.py", "tests/test_calc.py"])
    assert ok1 is True
    sm.transition_subtask(sub1_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub1_id, to_state=SubtaskState.COMPLETED, integrated_sha=pipeline1.last_integrated_sha)
    wt_mgr.cleanup_worktree(wt1.task_id, force=True)

    wt2 = wt_mgr.create_worktree("t2", base_ref=int_info1.branch_name)
    with open(os.path.join(wt2.worktree_path, "text.py"), "w", encoding="utf-8") as f:
        f.write("def shout(msg):\n    return msg.upper()\n")
    with open(os.path.join(wt2.worktree_path, "tests", "test_text.py"), "w", encoding="utf-8") as f:
        f.write("from text import shout\ndef test_shout():\n    assert shout('hi') == 'HI'\n")
    ok2, _ = pipeline1.integrate_subtask(wt2, target_files=["text.py", "tests/test_text.py"])
    assert ok2 is True
    sm.transition_subtask(sub2_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub2_id, to_state=SubtaskState.COMPLETED, integrated_sha=pipeline1.last_integrated_sha)
    wt_mgr.cleanup_worktree(wt2.task_id, force=True)

    # Aborta pipeline1 (limpa a branch de integração)
    pipeline1.abort_integration()

    # Pipeline2 reconstrói do SQLite
    pipeline2 = IntegrationPipeline(wt_mgr, gate=gate)
    int_info2 = pipeline2.start_integration(run_id, state_manager=sm)

    # Integra t3 na branch reconstruída
    wt3 = wt_mgr.create_worktree("t3", base_ref=int_info2.branch_name)
    with open(os.path.join(wt3.worktree_path, "math_ops.py"), "w", encoding="utf-8") as f:
        f.write("def div(a, b):\n    return a // b\n")
    with open(os.path.join(wt3.worktree_path, "tests", "test_math.py"), "w", encoding="utf-8") as f:
        f.write("from math_ops import div\ndef test_div():\n    assert div(10, 2) == 5\n")
    ok3, msg3 = pipeline2.integrate_subtask(wt3, target_files=["math_ops.py", "tests/test_math.py"])
    assert ok3 is True, msg3
    sm.transition_subtask(sub3_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub3_id, to_state=SubtaskState.COMPLETED, integrated_sha=pipeline2.last_integrated_sha)
    wt_mgr.cleanup_worktree(wt3.task_id, force=True)

    # finish_integration com fast-forward
    ok_ff, msg_ff = pipeline2.finish_integration(fast_forward=True)
    assert ok_ff is True, f"finish_integration falhou: {msg_ff}"

    # Repositório principal contém todas as 3 subtarefas
    assert os.path.exists(os.path.join(repo_path, "calc.py"))
    assert os.path.exists(os.path.join(repo_path, "text.py"))
    assert os.path.exists(os.path.join(repo_path, "math_ops.py"))


def test_rebuild_ancestry_conflict_handling_aborts_and_reports(git_test_repo):
    """(4) Conflito na reconstrução: não levanta exceção, remove MERGE_HEAD e invariante falha citando o SHA."""
    repo_path = str(git_test_repo)
    db_path = os.path.join(repo_path, ".meister", "meister.db")
    from meister.state import StateManager, SubtaskState

    sm = StateManager(db_path=db_path)
    run_id = "test-rebuild-conflict"
    sm.create_or_get_run("task prompt", cwd=repo_path, force_run_id=run_id)
    subs = sm.add_subtasks(
        run_id,
        [
            {"id": "t1", "description": "task 1", "target_files": ["conflict.py"]},
            {"id": "t2", "description": "task 2", "target_files": ["conflict.py"]},
        ],
    )
    sub1_id = subs[0]["subtask_id"]
    sub2_id = subs[1]["subtask_id"]

    wt_mgr = WorktreeManager(repo_root=repo_path)

    # Cria dois commits conflitantes a partir da mesma base HEAD
    wt1 = wt_mgr.create_worktree("conf-1", base_ref="HEAD")
    with open(os.path.join(wt1.worktree_path, "conflict.py"), "w", encoding="utf-8") as f:
        f.write("def val(): return 'worker1'\n")
    sha1 = wt_mgr.commit_worktree(wt1.worktree_path, "worker1 commit")
    assert sha1 is not None
    wt_mgr.cleanup_worktree("conf-1", delete_branch=True, force=True)

    wt2 = wt_mgr.create_worktree("conf-2", base_ref="HEAD")
    with open(os.path.join(wt2.worktree_path, "conflict.py"), "w", encoding="utf-8") as f:
        f.write("def val(): return 'worker2'\n")
    sha2 = wt_mgr.commit_worktree(wt2.worktree_path, "worker2 commit")
    assert sha2 is not None
    wt_mgr.cleanup_worktree("conf-2", delete_branch=True, force=True)

    # Registra ambos no SQLite como COMPLETED
    sm.transition_subtask(sub1_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub1_id, to_state=SubtaskState.COMPLETED, integrated_sha=sha1)
    sm.transition_subtask(sub2_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub2_id, to_state=SubtaskState.COMPLETED, integrated_sha=sha2)

    # Inicia integração: reconstrução não deve levantar exceção mesmo com conflito
    gate = DeterministicGate(repo_path)
    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    int_info = pipeline.start_integration(run_id, state_manager=sm)

    # Confirma que MERGE_HEAD não ficou ativo no worktree
    mh_rel = subprocess.run(
        ["git", "rev-parse", "--git-path", "MERGE_HEAD"],
        cwd=int_info.worktree_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    mh_full = mh_rel if os.path.isabs(mh_rel) else os.path.join(int_info.worktree_path, mh_rel)
    assert not os.path.exists(mh_full), f"MERGE_HEAD ainda existe em {mh_full}"

    # Invariante deve barrar e citar o SHA conflitante (sha2)
    anc_ok, anc_msg = pipeline.verify_completed_subtasks_ancestry()
    assert anc_ok is False
    assert sha2[:8] in anc_msg, f"Mensagem deveria citar sha {sha2[:8]}: {anc_msg}"

    pipeline.abort_integration()


def test_rebuild_ancestry_reuse_path_regression(git_test_repo):
    """(5) Regressão: o caminho de REUSO (branch de integração ainda existe) continua preservado."""
    repo_path = str(git_test_repo)
    db_path = os.path.join(repo_path, ".meister", "meister.db")
    from meister.state import StateManager, SubtaskState

    sm = StateManager(db_path=db_path)
    run_id = "test-rebuild-reuse"
    sm.create_or_get_run("task prompt", cwd=repo_path, force_run_id=run_id)
    subs = sm.add_subtasks(
        run_id,
        [{"id": "t1", "description": "task 1", "target_files": ["calc.py", "tests/test_calc.py"]}],
    )
    sub1_id = subs[0]["subtask_id"]

    wt_mgr = WorktreeManager(repo_root=repo_path)
    gate = DeterministicGate(repo_path)
    pipeline1 = IntegrationPipeline(wt_mgr, gate=gate)
    int_info1 = pipeline1.start_integration(run_id, state_manager=sm)

    wt1 = wt_mgr.create_worktree("t1", base_ref=int_info1.branch_name)
    with open(os.path.join(wt1.worktree_path, "calc.py"), "w", encoding="utf-8") as f:
        f.write("def mul(a, b):\n    return a * b\n")
    with open(os.path.join(wt1.worktree_path, "tests", "test_calc.py"), "w", encoding="utf-8") as f:
        f.write("from calc import mul\ndef test_mul():\n    assert mul(2, 3) == 6\n")

    ok1, msg1 = pipeline1.integrate_subtask(wt1, target_files=["calc.py", "tests/test_calc.py"])
    assert ok1 is True
    t1_sha = pipeline1.last_integrated_sha
    sm.transition_subtask(sub1_id, to_state=SubtaskState.RUNNING)
    sm.transition_subtask(sub1_id, to_state=SubtaskState.COMPLETED, integrated_sha=t1_sha)
    wt_mgr.cleanup_worktree(wt1.task_id, force=True)

    # NÃO chama abort_integration: a branch de integração permanece viva no git
    # Novo pipeline para o mesmo run_id deve REUTILIZAR a branch existente sem reconstruir
    pipeline2 = IntegrationPipeline(wt_mgr, gate=gate)
    int_info2 = pipeline2.start_integration(run_id, state_manager=sm)

    # Confirma que a branch foi reutilizada
    assert int_info2.branch_name == int_info1.branch_name
    anc_ok, anc_msg = pipeline2.verify_completed_subtasks_ancestry()
    assert anc_ok is True, anc_msg

    pipeline2.abort_integration()


