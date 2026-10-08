from __future__ import annotations

import subprocess
import sys
import webbrowser
from typing import Any
from unittest.mock import MagicMock

import pytest


def test_blocks_dashboard_server() -> None:
    with pytest.raises(AssertionError, match="test tried to launch a real dashboard server or browser"):
        subprocess.Popen(["python", "-m", "meister.cli", "dashboard"])


def test_blocks_dashboard_server_string_command() -> None:
    with pytest.raises(AssertionError, match="test tried to launch a real dashboard server or browser"):
        subprocess.Popen("python -m meister.cli dashboard")


def test_blocks_browser_app_argument() -> None:
    with pytest.raises(AssertionError, match="test tried to launch a real dashboard server or browser"):
        subprocess.Popen(["python", "--app=http://x"])


def test_blocks_browser_executable() -> None:
    with pytest.raises(AssertionError, match="test tried to launch a real dashboard server or browser"):
        subprocess.Popen(
            ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        )


def test_blocks_webbrowser_open() -> None:
    with pytest.raises(AssertionError, match="test tried to launch a real dashboard server or browser"):
        webbrowser.open("http://x")


def test_allows_ordinary_subprocess() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "print(1)"],
        stdout=subprocess.PIPE,
        text=True,
    )
    stdout, _ = process.communicate(timeout=5)

    assert process.returncode == 0
    assert stdout.strip() == "1"


def test_respects_test_local_popen_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    real_popen = subprocess.Popen
    fake_process = MagicMock()
    popen_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (list, tuple)) and command and command[0] == "git":
            return real_popen(*args, **kwargs)
        popen_calls.append((args, kwargs))
        return fake_process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = subprocess.Popen(["python", "-m", "meister.cli", "dashboard"])

    assert result is fake_process
    assert len(popen_calls) == 1
