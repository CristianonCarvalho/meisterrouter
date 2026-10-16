import os
import subprocess

from meister import worktree as worktree_mod
from meister.worktree import IntegrationPipeline, WorktreeInfo, WorktreeManager


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "app.py").write_text("base\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "base")
    return path


def _info(path):
    os.makedirs(path, exist_ok=True)
    (path / "package.json").write_text("{}")
    return WorktreeInfo(
        task_id="setup-task",
        worktree_path=str(path),
        branch_name="meister/worktree/setup-task",
        base_ref="HEAD",
        base_commit="0" * 40,
        created_at=0.0,
        pid=os.getpid(),
    )


def _capture(monkeypatch):
    events = []
    monkeypatch.setattr("meister.logger.log_event", lambda **kwargs: events.append(kwargs))
    return events


def test_setup_ok_event_carries_given_run_id(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts"))
    events = _capture(monkeypatch)
    monkeypatch.setattr(worktree_mod, "prepare_environment", lambda path, config: (True, "ok"))

    manager._prepare_node_worktree(_info(tmp_path / "wt-ok"), run_id="abc")

    assert events[-1]["event_type"] == "worktree_setup_ok"
    assert events[-1]["run_id"] == "abc"


def test_setup_failed_event_carries_given_run_id(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts"))
    events = _capture(monkeypatch)
    monkeypatch.setattr(worktree_mod, "prepare_environment", lambda path, config: (False, "boom"))

    manager._prepare_node_worktree(_info(tmp_path / "wt-fail"), run_id="abc")

    assert events[-1]["event_type"] == "worktree_setup_failed"
    assert events[-1]["run_id"] == "abc"


def test_setup_without_run_id_leaves_event_without_explicit_run_id(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts"))
    events = _capture(monkeypatch)
    monkeypatch.setattr(worktree_mod, "prepare_environment", lambda path, config: (True, "ok"))

    manager._prepare_node_worktree(_info(tmp_path / "wt-none"))

    assert events[-1]["event_type"] == "worktree_setup_ok"
    assert events[-1].get("run_id") is None


def test_integration_pipeline_reuse_passes_its_run_id(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts"))
    first = IntegrationPipeline(manager, gate=object())
    integration = first.start_integration("run-x")
    with open(os.path.join(integration.worktree_path, "app.py"), "a") as output:
        output.write("change\n")
    assert manager.commit_worktree(integration.worktree_path, "subtask(x): change")

    calls = []
    monkeypatch.setattr(
        manager, "_prepare_node_worktree", lambda info, **kwargs: calls.append(kwargs)
    )
    second = IntegrationPipeline(manager, gate=object())
    second.start_integration("run-x")

    assert calls[-1].get("run_id") == "run-x"
