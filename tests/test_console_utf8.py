"""ensure_utf8_streams: a CLI não cai por causa do texto que ela mesma imprime."""

import io
import sys

import pytest
from click.testing import CliRunner

from meister.console import ensure_utf8_streams

TEXT = "🔮 ❌"


def _cp1252_stream() -> tuple[io.BytesIO, io.TextIOWrapper]:
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding="cp1252", errors="strict")


def test_cp1252_stream_fails_without_fix():
    _, stream = _cp1252_stream()
    with pytest.raises(UnicodeEncodeError):
        stream.write(TEXT)
        stream.flush()


def test_reconfigures_stdout_and_stderr_to_utf8(monkeypatch):
    out_raw, out = _cp1252_stream()
    err_raw, err = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    ensure_utf8_streams()
    out.write(TEXT)
    out.flush()
    err.write(TEXT)
    err.flush()

    assert out_raw.getvalue().decode("utf-8") == TEXT
    assert err_raw.getvalue().decode("utf-8") == TEXT


def test_streams_without_reconfigure_do_not_raise(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", object())
    ensure_utf8_streams()


def test_idempotent(monkeypatch):
    raw, stream = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    ensure_utf8_streams()
    ensure_utf8_streams()
    stream.write(TEXT)
    stream.flush()
    assert stream.encoding == "utf-8"
    assert raw.getvalue().decode("utf-8") == TEXT


def test_cli_entry_point_calls_ensure_utf8_streams(monkeypatch):
    from meister import cli

    calls = []
    monkeypatch.setattr(cli, "ensure_utf8_streams", lambda: calls.append(True))
    result = CliRunner().invoke(cli.main, ["--version"])
    assert result.exit_code == 0
    assert calls == [True]
