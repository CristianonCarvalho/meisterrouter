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

    subprocess.run(["git", "init"], cwd=cwd, check=True, capture_output=True)
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
