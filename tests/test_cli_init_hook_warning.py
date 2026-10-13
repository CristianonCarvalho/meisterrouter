"""Aviso do CLI quando o hook de pre-commit vai reprovar commits (sem testes e sem gate.commands)."""

from __future__ import annotations

import subprocess

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.i18n import EN_MESSAGES, PT_BR_MESSAGES, reset_language_cache

KEY = "commands.hook.no_test_runner_warning"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("MEISTER_LANG", raising=False)
    reset_language_cache()
    yield
    reset_language_cache()


def _git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.fixture
def _lang(monkeypatch):
    def set_lang(value: str) -> None:
        monkeypatch.setenv("MEISTER_LANG", value)
        reset_language_cache()

    return set_lang


def test_init_hooks_warns_in_english_only(tmp_path, _lang):
    _lang("en")
    project = _git_repo(tmp_path / "proj")
    result = CliRunner().invoke(main, ["init", "--target", str(project), "--hooks"])
    assert result.exit_code == 0, result.output
    assert EN_MESSAGES[KEY] in result.output
    assert PT_BR_MESSAGES[KEY] not in result.output


def test_init_hooks_warns_in_portuguese_only(tmp_path, _lang):
    _lang("pt-BR")
    project = _git_repo(tmp_path / "proj")
    result = CliRunner().invoke(main, ["init", "--target", str(project), "--hooks"])
    assert result.exit_code == 0, result.output
    assert PT_BR_MESSAGES[KEY] in result.output
    assert EN_MESSAGES[KEY] not in result.output


def test_install_hooks_warns_in_active_language_only(tmp_path, _lang):
    _lang("en")
    project = _git_repo(tmp_path / "proj")
    result = CliRunner().invoke(main, ["install-hooks", "--target", str(project), "--git"])
    assert result.exit_code == 0, result.output
    assert EN_MESSAGES[KEY] in result.output
    assert PT_BR_MESSAGES[KEY] not in result.output


def test_init_hooks_does_not_warn_when_pytest_ini_present(tmp_path):
    project = _git_repo(tmp_path / "proj")
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["init", "--target", str(project), "--hooks"])
    assert result.exit_code == 0, result.output
    assert EN_MESSAGES[KEY] not in result.output
    assert PT_BR_MESSAGES[KEY] not in result.output


def test_init_hooks_does_not_warn_when_gate_commands_configured(tmp_path):
    project = _git_repo(tmp_path / "proj")
    (project / "meister.config.yaml").write_text(
        "gate:\n  commands:\n    - name: check\n      run: [\"true\"]\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(main, ["init", "--target", str(project), "--hooks"])
    assert result.exit_code == 0, result.output
    assert EN_MESSAGES[KEY] not in result.output
    assert PT_BR_MESSAGES[KEY] not in result.output
