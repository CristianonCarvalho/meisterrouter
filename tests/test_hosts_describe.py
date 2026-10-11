from __future__ import annotations

import pytest

from meister.config import MeisterConfig, RuntimeConfig
from meister.hosts import HostError, describe_host


def _config(mode: str) -> MeisterConfig:
    return MeisterConfig(runtime=RuntimeConfig(host=mode))


def _env(monkeypatch, *, socket: bool, tmux: bool) -> list[object]:
    seen: list[object] = []

    def fake_available(socket_path=None):
        seen.append(socket_path)
        return socket

    monkeypatch.setattr("meister.hosts.select.is_herdr_available", fake_available)
    monkeypatch.setattr(
        "meister.hosts.select.shutil.which", lambda name: "/usr/bin/tmux" if tmux else None
    )
    return seen


@pytest.mark.parametrize(
    ("mode", "socket", "tmux", "expected"),
    [
        ("auto", True, False, ("herdr", "auto_socket")),
        ("auto", True, True, ("herdr", "auto_socket")),
        ("auto", False, True, ("process", "auto_no_socket")),
        ("auto", False, False, ("process", "auto_no_socket")),
        ("process", True, True, ("process", "explicit")),
        ("process", False, False, ("process", "explicit")),
        ("herdr", True, False, ("herdr", "explicit")),
        ("herdr", False, True, ("herdr", "explicit_unavailable")),
        ("tmux", False, True, ("tmux", "explicit")),
        ("tmux", True, False, ("tmux", "explicit_unavailable")),
    ],
)
def test_describe_host(monkeypatch, mode, socket, tmux, expected):
    _env(monkeypatch, socket=socket, tmux=tmux)
    assert describe_host(_config(mode)) == expected


def test_socket_path_is_forwarded(monkeypatch):
    seen = _env(monkeypatch, socket=True, tmux=False)
    describe_host(_config("auto"), socket_path="/tmp/x.sock")
    assert seen == ["/tmp/x.sock"]


def test_unknown_mode_raises(monkeypatch):
    _env(monkeypatch, socket=False, tmux=False)
    with pytest.raises(HostError):
        describe_host(_config("bogus"))


def test_describe_does_not_instantiate_adapters(monkeypatch):
    _env(monkeypatch, socket=True, tmux=True)

    def boom(*args, **kwargs):
        raise AssertionError("adaptador instanciado")

    monkeypatch.setattr("meister.hosts.select._herdr_host", boom)
    monkeypatch.setattr("meister.hosts.select.TmuxHost", boom)
    monkeypatch.setattr("meister.hosts.select.ProcessHost", boom)
    for mode in ("auto", "herdr", "tmux", "process"):
        describe_host(_config(mode))
