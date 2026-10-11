"""Saída do harness lida em UTF-8 em qualquer SO (no Windows o padrão seria cp1252)."""

from pathlib import Path
from typing import Any, Dict

import pytest

from meister import worker as worker_mod
from meister.worker import HarnessWorker
from tests.fake_cli import make_fake_cli


def _make_worker(tmp_path: Path, cli: Path) -> HarnessWorker:
    worker = object.__new__(HarnessWorker)
    worker.model_tier = "utf8-test"
    worker.cwd = str(tmp_path)
    worker.config_path = None
    worker.config = None
    worker.harness = worker_mod.HARNESS_COPILOT
    worker.resolved_model = None
    worker.tier_key = "utf8-test"
    worker.effort = None
    worker.cli_binary = str(cli)
    return worker


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes) -> Dict[str, Any]:
    cli = make_fake_cli(
        tmp_path / "bin",
        "copilot",
        f"""
        import sys
        sys.stdout.buffer.write({payload!r})
        sys.stdout.buffer.flush()
        """,
    )
    monkeypatch.setattr(
        worker_mod, "build_harness_command", lambda *args, **kwargs: [str(cli)]
    )
    workdir = tmp_path / "work"
    workdir.mkdir()
    return _make_worker(workdir, cli).run_task("utf8")


def test_utf8_arrows_are_preserved(tmp_path, monkeypatch):
    payload = "Tokens ↑ 11700 • ↓ 5\n".encode("utf-8")

    result = _run(tmp_path, monkeypatch, payload)

    assert result["exit_code"] == 0
    assert "Tokens ↑ 11700 • ↓ 5" in result["output"]
    assert result["usage"]["tokens_in"] == 11700
    assert result["usage"]["tokens_out"] == 5


def test_invalid_byte_does_not_raise(tmp_path, monkeypatch):
    payload = b"antes \x81 depois\n" + "Tokens ↑ 11700 • ↓ 5\n".encode("utf-8")

    result = _run(tmp_path, monkeypatch, payload)

    assert result["exit_code"] == 0
    assert "antes � depois" in result["output"]
    assert "Tokens ↑ 11700 • ↓ 5" in result["output"]


def test_popen_is_opened_with_utf8(tmp_path, monkeypatch):
    """O decodificador não pode depender da localidade do SO (cp1252 no Windows)."""
    seen: Dict[str, Any] = {}
    real_popen = worker_mod.subprocess.Popen

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(worker_mod.subprocess, "Popen", spy)
    _run(tmp_path, monkeypatch, b"ok\n")

    assert seen["encoding"] == "utf-8"
    assert seen["errors"] == "replace"
