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

