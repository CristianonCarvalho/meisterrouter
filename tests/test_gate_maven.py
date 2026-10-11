import os
import stat
from pathlib import Path

from meister.config import load_config
from meister.gate import DeterministicGate
from meister.i18n import t

WINDOWS = os.name == "nt"


def _fake_executable(directory: Path, name: str, exit_code: int = 0) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        target = directory / (name + ".cmd")
        target.write_text(f"@echo off\r\nexit /b {exit_code}\r\n")
    else:
        target = directory / name
        target.write_text(f"#!/bin/sh\nexit {exit_code}\n")
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def _project(root: Path) -> DeterministicGate:
    (root / "pom.xml").write_text("<project/>\n")
    config_path = root / "meister.config.yaml"
    config_path.write_text("environment:\n  install_dependencies: false\n")
    return DeterministicGate(str(root), load_config(str(config_path)))


def _clean_env(monkeypatch, path: str) -> None:
    monkeypatch.delenv("MAVEN_HOME", raising=False)
    monkeypatch.delenv("M2_HOME", raising=False)
    monkeypatch.setenv("PATH", path)


def test_pom_xml_detects_maven(tmp_path):
    gate = _project(tmp_path)
    assert gate.detect_test_runner() == "maven"


def test_resolve_maven_prefers_project_wrapper(tmp_path):
    project = tmp_path / "project"
    wrapper = _fake_executable(project, "mvnw")
    path_bin = tmp_path / "path_bin"
    _fake_executable(path_bin, "mvn")
    found = DeterministicGate.resolve_maven(str(project), {"PATH": str(path_bin)})
    assert found == str(wrapper)


def test_resolve_maven_uses_path(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    path_bin = tmp_path / "path_bin"
    fake = _fake_executable(path_bin, "mvn")
    found = DeterministicGate.resolve_maven(str(project), {"PATH": str(path_bin)})
    assert found is not None
    assert os.path.normcase(os.path.abspath(found)) == os.path.normcase(str(fake))


def test_resolve_maven_uses_maven_home(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    home = tmp_path / "maven"
    fake = _fake_executable(home / "bin", "mvn")
    found = DeterministicGate.resolve_maven(
        str(project), {"PATH": str(empty), "MAVEN_HOME": str(home)}
    )
    assert found == str(fake)


def test_resolve_maven_uses_m2_home(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    home = tmp_path / "m2"
    fake = _fake_executable(home / "bin", "mvn")
    found = DeterministicGate.resolve_maven(
        str(project), {"PATH": str(empty), "M2_HOME": str(home)}
    )
    assert found == str(fake)


def test_resolve_maven_returns_none_without_maven(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert DeterministicGate.resolve_maven(str(project), {"PATH": str(empty)}) is None


def test_gate_passes_when_maven_succeeds(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    path_bin = tmp_path / "path_bin"
    _fake_executable(path_bin, "mvn", exit_code=0)
    _clean_env(monkeypatch, str(path_bin))
    result = _project(project).run_verification_ex()
    assert result.passed, result.output
    assert not result.infrastructure_error
    assert "[maven]" in result.output


def test_gate_fails_when_maven_fails(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    path_bin = tmp_path / "path_bin"
    _fake_executable(path_bin, "mvn", exit_code=1)
    _clean_env(monkeypatch, str(path_bin))
    result = _project(project).run_verification_ex()
    assert not result.passed
    assert not result.infrastructure_error


def test_gate_reports_missing_maven(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    _clean_env(monkeypatch, str(empty))
    result = _project(project).run_verification_ex()
    assert not result.passed
    assert result.infrastructure_error
    assert result.output == t("engine.gate.maven_missing")
