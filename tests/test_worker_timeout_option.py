from __future__ import annotations

from click.testing import CliRunner

import meister.worker as worker_module
from meister.cli import main
from meister.i18n import t


def _patch_herdr_tab(monkeypatch, behavior):
    calls = []

    def fake_tab(**kwargs):
        calls.append(kwargs)
        return behavior(kwargs)

    monkeypatch.setattr(worker_module, "run_worker_in_herdr_tab", fake_tab)
    monkeypatch.setattr(worker_module, "is_herdr_available", lambda *a, **k: True)
    monkeypatch.setattr("meister.cli.is_worker_env", lambda: False)
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    return calls


def _invoke(tmp_path, *extra):
    args = ["worker", "--task", "faz algo", "--cwd", str(tmp_path), *extra]
    return CliRunner().invoke(main, args)


def test_timeout_option_reaches_herdr_tab(monkeypatch, tmp_path):
    calls = _patch_herdr_tab(monkeypatch, lambda kwargs: {"status": "done"})

    result = _invoke(tmp_path, "--timeout", "600")

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["timeout"] == 600


def test_timeout_defaults_to_180(monkeypatch, tmp_path):
    calls = _patch_herdr_tab(monkeypatch, lambda kwargs: {"status": "done"})

    result = _invoke(tmp_path)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["timeout"] == 180


def test_timeout_message_mentions_option_and_archive(monkeypatch, tmp_path):
    def raise_timeout(kwargs):
        raise TimeoutError("limite de 600s")

    _patch_herdr_tab(monkeypatch, raise_timeout)

    result = _invoke(tmp_path, "--timeout", "600")

    expected = t("cli.worker.timeout", error="limite de 600s")
    assert "--timeout" in expected
    assert "refs/meister/archive" in expected
    assert "--timeout" in result.output
    assert "refs/meister/archive" in result.output
