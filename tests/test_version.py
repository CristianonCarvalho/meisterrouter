import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

from meister import __version__
from meister.cli import main


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_version_metadata_is_synchronized():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)

    package_data = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    plugin_data = tomllib.loads((REPO_ROOT / "herdr-plugin.toml").read_text(encoding="utf-8"))
    project_data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setup_source = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert package_data["version"] == __version__
    assert plugin_data["version"] == __version__
    assert "version" not in project_data["project"]
    assert "version" in project_data["project"]["dynamic"]
    assert not re.search(r"\bversion\s*=", setup_source)
    assert any(
        line.startswith(f"## [{__version__}]")
        for line in changelog.splitlines()
    )


def test_version_option_prints_installed_version():
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith(f"meister {__version__}")


def test_version_option_uses_package_version(monkeypatch):
    def fail_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("meister.cli.__version__", "9.8.7")
    monkeypatch.setattr("importlib.metadata.version", lambda *args, **kwargs: "0.1.2")
    monkeypatch.setattr("meister.cli.subprocess", SimpleNamespace(run=fail_git))
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "meister 9.8.7"


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(cmd="git", timeout=2),
        FileNotFoundError("git"),
    ],
    ids=["git-timeout", "git-unavailable"],
)
def test_version_option_omits_commit_when_git_fails(monkeypatch, failure):
    def fail_git(*args, **kwargs):
        raise failure

    monkeypatch.setattr("meister.cli.subprocess", SimpleNamespace(run=fail_git))
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"meister {__version__}"


def test_version_option_includes_short_commit(monkeypatch):
    def fake_git(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="abc1234\n", stderr="")

    monkeypatch.setattr("meister.cli.subprocess", SimpleNamespace(run=fake_git))
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"meister {__version__} (commit abc1234)"


def test_build_metadata_uses_package_version(tmp_path):
    if importlib.util.find_spec("pip") is None:
        pytest.skip("pip is not installed")
    if importlib.util.find_spec("setuptools") is None:
        pytest.skip("setuptools is not installed")

    target = tmp_path / "install"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "--quiet",
            "--target",
            str(target),
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and (
        "No module named pip" in completed.stderr
        or "No module named setuptools" in completed.stderr
    ):
        pytest.skip(f"pip/setuptools unavailable: {completed.stderr.strip()}")
    assert completed.returncode == 0, completed.stderr

    metadata_files = list(target.glob("*.dist-info/METADATA"))
    assert len(metadata_files) == 1
    metadata = metadata_files[0].read_text(encoding="utf-8")
    assert f"Version: {__version__}" in metadata.splitlines()
