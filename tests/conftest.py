import os
import pathlib
import tempfile
import pytest


@pytest.fixture
def tmp_path():
    """Override tmp_path fixture to use /tmp to avoid macOS AF_UNIX 104-char path limit."""
    with tempfile.TemporaryDirectory(dir="/tmp") as d:
        yield pathlib.Path(d)


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, monkeypatch):
    """Isolate DB, logs, and worktrees to tmp_path for all tests."""
    isolate_dir = tmp_path / "_meister_isolated"
    isolate_dir.mkdir(parents=True, exist_ok=True)
    test_db = str(isolate_dir / "test_meister.db")
    test_logs = str(isolate_dir / "logs")
    test_wt = str(isolate_dir / "wt")
    os.makedirs(test_logs, exist_ok=True)
    os.makedirs(test_wt, exist_ok=True)
    monkeypatch.setenv("MEISTER_DB_PATH", test_db)
    monkeypatch.setenv("MEISTER_LOG_DIR", test_logs)
    monkeypatch.setenv("MEISTER_WORKTREES_DIR", test_wt)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Meister CI")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "ci@meisterrouter.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Meister CI")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "ci@meisterrouter.local")
    yield
