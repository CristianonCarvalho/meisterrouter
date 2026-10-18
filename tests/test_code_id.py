from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import meister
from meister.code_id import current_code_id


@pytest.fixture(autouse=True)
def _clear_cache():
    if hasattr(current_code_id, "cache_clear"):
        current_code_id.cache_clear()
    yield
    if hasattr(current_code_id, "cache_clear"):
        current_code_id.cache_clear()


def test_current_code_id_with_git():
    mock_proc = MagicMock(returncode=0, stdout="f4361aa\n", stderr="")
    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        code_id = current_code_id()
        assert code_id == f"{meister.__version__}+f4361aa"
        assert mock_run.called
        args, kwargs = mock_run.call_args
        assert args[0] == ["git", "rev-parse", "--short", "HEAD"]
        assert "timeout" in kwargs


def test_current_code_id_without_git_nonzero_exit():
    mock_proc = MagicMock(returncode=128, stdout="", stderr="fatal: not a git repository")
    with patch("subprocess.run", return_value=mock_proc):
        assert current_code_id() == meister.__version__


def test_current_code_id_without_git_file_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError("git")):
        assert current_code_id() == meister.__version__


def test_current_code_id_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=1.0)):
        assert current_code_id() == meister.__version__


def test_current_code_id_caches_result():
    mock_proc = MagicMock(returncode=0, stdout="f4361aa\n", stderr="")
    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        first = current_code_id()
        second = current_code_id()
        assert first == second == f"{meister.__version__}+f4361aa"
        assert mock_run.call_count == 1
