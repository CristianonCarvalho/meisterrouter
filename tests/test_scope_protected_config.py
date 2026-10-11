import subprocess

from meister.worktree import PROTECTED_FILES, WorktreeManager, scope_violations, tolerated_touched


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_protected_files_constant():
    assert PROTECTED_FILES == ("meister.config.yaml",)


def test_root_config_is_violation_even_when_declared_in_target_files():
    files = ["meister.config.yaml", "src/app.py"]
    assert scope_violations(files, ["meister.config.yaml", "src/app.py"]) == ["meister.config.yaml"]


def test_root_config_is_violation_even_with_tolerate_everything():
    files = ["meister.config.yaml", "src/app.py"]
    assert scope_violations(files, ["src/app.py"], ["*"]) == ["meister.config.yaml"]
    assert scope_violations(files, ["src/app.py"], ["*.yaml", "**"]) == ["meister.config.yaml"]
    assert tolerated_touched(files, ["src/app.py"], ["*"]) == []


def test_root_config_is_violation_even_in_dependency_files():
    files = ["meister.config.yaml", "src/app.py"]
    violations = scope_violations(
        files, ["src/app.py"], dependency_files=["meister.config.yaml", "**"]
    )
    assert violations == ["meister.config.yaml"]


def test_config_in_subfolder_is_not_protected():
    files = ["docs/meister.config.yaml"]
    assert scope_violations(files, ["docs/meister.config.yaml"]) == []


def test_without_target_files_there_is_no_violation():
    assert scope_violations(["meister.config.yaml"], None) == []
    assert scope_violations(["meister.config.yaml"], []) == []
    assert tolerated_touched(["meister.config.yaml"], None, ["*"]) == []


def test_verify_scope_rejects_worktree_that_changes_root_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    (repo / "meister.config.yaml").write_text("router:\n  mode: first\n")
    (repo / "app.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    wt = manager.create_worktree("protected-config")
    (tmp_path / "worktrees" / "protected-config" / "meister.config.yaml").write_text(
        "router:\n  mode: jev\n"
    )
    (tmp_path / "worktrees" / "protected-config" / "app.py").write_text("x = 2\n")

    valid, invalid = manager.verify_scope(
        wt.worktree_path,
        ["app.py", "meister.config.yaml"],
        base_ref=wt.base_commit,
        tolerated=["*"],
    )
    assert not valid
    assert invalid == ["meister.config.yaml"]


def test_root_config_is_violation_even_when_gitignored():
    assert scope_violations(["meister.config.yaml"], ["app.py"], ignored_files={"meister.config.yaml"}) == [
        "meister.config.yaml"
    ]
