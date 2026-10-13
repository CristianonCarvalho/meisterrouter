"""Comando `meister gate` pela CLI (CliRunner)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.i18n import reset_language_cache


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("MEISTER_LANG", raising=False)
    reset_language_cache()
    yield
    reset_language_cache()


def _project(tmp_path: Path, run: str) -> Path:
    (tmp_path / "meister.config.yaml").write_text(
        "gate:\n  commands:\n    - name: check\n      run: [" + run + "]\n",
        encoding="utf-8",
    )
    return tmp_path


def _invoke(path: Path):
    return CliRunner().invoke(main, ["gate", "--path", str(path)])


def test_gate_passes_with_exit_0(tmp_path):
    result = _invoke(_project(tmp_path, '"true"'))
    assert result.exit_code == 0, result.output
    assert "Gate aprovado" in result.output or "Gate passed" in result.output


def test_gate_fails_with_exit_1(tmp_path):
    result = _invoke(_project(tmp_path, '"false"'))
    assert result.exit_code == 1, result.output


def test_gate_infrastructure_error_exits_2(tmp_path, monkeypatch):
    from meister import gate as gate_module

    def _infra(self, repo_path=None, docs_only=False):
        return gate_module.VerificationResult(False, "no checks", infrastructure_error=True)

    monkeypatch.setattr(gate_module.DeterministicGate, "run_verification_ex", _infra)
    result = _invoke(_project(tmp_path, '"true"'))
    assert result.exit_code == 2, result.output


def test_gate_help_is_available():
    result = CliRunner().invoke(main, ["gate", "--help"])
    assert result.exit_code == 0
    assert "--path" in result.output
