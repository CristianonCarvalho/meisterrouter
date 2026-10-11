from meister.worktree import WorktreeManager, scope_violations


def test_dependency_file_is_not_a_violation():
    files = ["meister/a.py", "meister/dep.py"]
    assert scope_violations(files, ["meister/a.py"], dependency_files=["meister/dep.py"]) == []


def test_unrelated_file_is_still_a_violation():
    files = ["meister/a.py", "meister/dep.py", "meister/other.py"]
    result = scope_violations(files, ["meister/a.py"], dependency_files=["meister/dep.py"])
    assert result == ["meister/other.py"]


def test_dependency_glob_patterns():
    files = ["meister/a.py", "tests/test_x.py", "tests/sub/test_y.py", "docs/b.md"]
    result = scope_violations(
        files,
        ["meister/a.py"],
        dependency_files=["tests/*.py", "./docs/"],
    )
    assert result == ["tests/sub/test_y.py"]


def test_none_and_empty_keep_old_behavior():
    files = ["meister/a.py", "meister/dep.py"]
    old = scope_violations(files, ["meister/a.py"])
    assert old == ["meister/dep.py"]
    assert scope_violations(files, ["meister/a.py"], dependency_files=None) == old
    assert scope_violations(files, ["meister/a.py"], dependency_files=[]) == old


def test_empty_target_files_still_returns_no_violations():
    assert scope_violations(["x.py"], [], dependency_files=["y.py"]) == []
    assert scope_violations(["x.py"], None, dependency_files=["y.py"]) == []


def test_verify_scope_passes_dependency_files(tmp_path, monkeypatch):
    manager = WorktreeManager.__new__(WorktreeManager)
    manager.repo_root = str(tmp_path)
    monkeypatch.setattr(
        manager, "get_modified_files", lambda path, base_ref=None: ["meister/a.py", "meister/dep.py"]
    )
    monkeypatch.setattr(WorktreeManager, "_get_ignored_files", staticmethod(lambda path, files: set()))

    assert manager.verify_scope(str(tmp_path), ["meister/a.py"], tolerated=[]) == (
        False,
        ["meister/dep.py"],
    )
    assert manager.verify_scope(
        str(tmp_path), ["meister/a.py"], tolerated=[], dependency_files=["meister/dep.py"]
    ) == (True, [])
