import sys
from pathlib import Path

import pytest

from meister.worker import find_cli_binary


def _make_fake_cli(directory: Path, name: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        path = directory / f"{name}.cmd"
        path.write_text("@echo off\r\n", encoding="utf-8")
    else:
        path = directory / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return str(path)


@pytest.fixture
def fake_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def test_kiro_alias_resolves_same_as_canonical_name(fake_path):
    expected = _make_fake_cli(fake_path, "kiro-cli")

    assert find_cli_binary("kiro") == expected
    assert find_cli_binary("kiro-cli") == expected
    assert find_cli_binary("  Kiro  ") == expected


def test_github_copilot_alias_resolves_same_as_copilot(fake_path):
    expected = _make_fake_cli(fake_path, "copilot")

    assert find_cli_binary("github-copilot") == expected
    assert find_cli_binary("copilot") == expected


def test_unknown_harness_searches_its_own_name(fake_path):
    expected = _make_fake_cli(fake_path, "foobar-cli")

    assert find_cli_binary("foobar-cli") == expected


def test_unknown_harness_missing_returns_none(fake_path):
    assert find_cli_binary("no-such-harness") is None
