import os
import subprocess

import pytest

from meister.gate import _worktree_tree_hash


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(tmp_path, gitignore_line):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / ".gitignore").write_text(gitignore_line + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c", "user.name=test",
        "-c", "user.email=test@example.com",
        "commit", "-q", "-m", "init",
    )
    return repo


def _write_venv_dir(venv_dir, marker):
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text(marker, encoding="utf-8")


@pytest.mark.parametrize("gitignore_line", [".venv", ".venv/"])
@pytest.mark.parametrize("name", [".venv", "venv"])
def test_tree_hash_ignores_venv_directory(tmp_path, gitignore_line, name):
    repo = _make_repo(tmp_path, gitignore_line.replace(".venv", name))
    venv = repo / name
    _write_venv_dir(venv, "v1")

    first = _worktree_tree_hash(str(repo))
    assert first

    (venv / "bin" / "python").write_text("v2 changed", encoding="utf-8")
    (venv / "lib-extra.txt").write_text("new file", encoding="utf-8")

    assert _worktree_tree_hash(str(repo)) == first


@pytest.mark.parametrize("gitignore_line", [".venv", ".venv/"])
def test_tree_hash_ignores_venv_symlink(tmp_path, gitignore_line):
    repo = _make_repo(tmp_path, gitignore_line)
    external = tmp_path / "external-env"
    _write_venv_dir(external, "v1")
    os.symlink(external, repo / ".venv")

    first = _worktree_tree_hash(str(repo))
    assert first

    (external / "bin" / "python").write_text("v2 changed", encoding="utf-8")

    assert _worktree_tree_hash(str(repo)) == first


def test_tree_hash_still_changes_with_source_edits(tmp_path):
    repo = _make_repo(tmp_path, ".venv/")
    _write_venv_dir(repo / ".venv", "v1")

    first = _worktree_tree_hash(str(repo))
    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")

    second = _worktree_tree_hash(str(repo))
    assert first and second and first != second
