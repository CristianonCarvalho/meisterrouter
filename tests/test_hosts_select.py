from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from meister.config import MeisterConfig, RuntimeConfig
from meister.hosts import HostError, HerdrHost, ProcessHost, TmuxHost, select_host
from meister.i18n import CATALOGS, reset_language_cache


def _config(mode: str) -> MeisterConfig:
    return MeisterConfig(runtime=RuntimeConfig(host=mode))


def _set_lang(monkeypatch, lang: str) -> None:
    monkeypatch.setenv("MEISTER_LANG", lang)
    reset_language_cache()


@pytest.fixture
def herdr_up(monkeypatch):
    calls: list[object] = []

    def fake(socket_path=None):
        calls.append(socket_path)
        return True

    monkeypatch.setattr("meister.hosts.select.is_herdr_available", fake)
    return calls


@pytest.fixture
def herdr_down(monkeypatch):
    monkeypatch.setattr("meister.hosts.select.is_herdr_available", lambda socket_path=None: False)


def test_process_mode_always_process_host(herdr_up):
    host = select_host(_config("process"))
    assert isinstance(host, ProcessHost)
    assert herdr_up == []


def test_process_mode_ignores_missing_herdr(herdr_down):
    assert isinstance(select_host(_config("process")), ProcessHost)


def test_auto_with_herdr_socket_uses_herdr_host(herdr_up):
    client = MagicMock(name="client")
    host = select_host(_config("auto"), socket_path="/tmp/x.sock", client=client)
    assert isinstance(host, HerdrHost)
    assert host.client is client
    assert herdr_up == ["/tmp/x.sock"]


def test_auto_without_herdr_socket_falls_back_to_process(herdr_down):
    assert isinstance(select_host(_config("auto")), ProcessHost)


def test_auto_with_real_socket_path_builds_socket_client(tmp_path):
    sock = tmp_path / "herdr.sock"
    sock.write_text("")
    host = select_host(_config("auto"), socket_path=str(sock))
    assert isinstance(host, HerdrHost)
    assert host.client.socket_path == str(sock)


def test_auto_without_real_socket_path_is_process(tmp_path):
    host = select_host(_config("auto"), socket_path=str(tmp_path / "missing.sock"))
    assert isinstance(host, ProcessHost)


def test_herdr_mode_with_socket_uses_herdr_host(herdr_up):
    client = MagicMock(name="client")
    host = select_host(_config("herdr"), socket_path="/tmp/x.sock", client=client)
    assert isinstance(host, HerdrHost)
    assert host.client is client


def test_herdr_mode_without_socket_raises(monkeypatch, herdr_down):
    _set_lang(monkeypatch, "en")
    with pytest.raises(HostError) as exc:
        select_host(_config("herdr"), socket_path="/tmp/none.sock")
    assert "/tmp/none.sock" in str(exc.value)
    assert "Herdr" in str(exc.value)


def test_herdr_unavailable_message_pt_br(monkeypatch, herdr_down):
    _set_lang(monkeypatch, "pt-BR")
    with pytest.raises(HostError) as exc:
        select_host(_config("herdr"), socket_path="/tmp/none.sock")
    assert "inacessível" in str(exc.value)
    assert "/tmp/none.sock" in str(exc.value)


def _fake_tmux(monkeypatch, version_output: str | None, returncode: int = 0) -> None:
    """Simula o binário ``tmux`` (``None`` = ausente do PATH)."""
    # Substitui os módulos só no namespace de ``select``: patchar ``shutil.which`` /
    # ``subprocess.run`` globalmente quebraria chamadas ao git feitas por outros fixtures.
    monkeypatch.setattr(
        "meister.hosts.select.shutil",
        SimpleNamespace(which=lambda name: None if version_output is None else "/fake/tmux"),
    )

    def fake_run(argv, **kwargs):
        assert argv == ["/fake/tmux", "-V"]
        return subprocess.CompletedProcess(argv, returncode, stdout=version_output or "", stderr="")

    monkeypatch.setattr(
        "meister.hosts.select.subprocess",
        SimpleNamespace(run=fake_run, SubprocessError=subprocess.SubprocessError),
    )


@pytest.mark.parametrize("output", ["tmux 3.0\n", "tmux 3.4\n", "tmux 3.3a\n", "tmux next-3.5\n"])
def test_tmux_mode_returns_tmux_host(monkeypatch, herdr_down, output):
    _fake_tmux(monkeypatch, output)
    assert isinstance(select_host(_config("tmux")), TmuxHost)


def test_tmux_mode_without_binary_raises_en(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "en")
    _fake_tmux(monkeypatch, None)
    with pytest.raises(HostError, match="tmux 3.0 or newer not found"):
        select_host(_config("tmux"))


def test_tmux_mode_without_binary_raises_pt_br(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "pt-BR")
    _fake_tmux(monkeypatch, None)
    with pytest.raises(HostError, match="não encontrado"):
        select_host(_config("tmux"))


@pytest.mark.parametrize("output", ["tmux 2.9\n", "tmux 1.8\n", "garbage\n", ""])
def test_tmux_mode_too_old_or_unreadable_raises(monkeypatch, herdr_up, output):
    _set_lang(monkeypatch, "en")
    _fake_tmux(monkeypatch, output)
    with pytest.raises(HostError, match="not found"):
        select_host(_config("tmux"))


def test_tmux_mode_failing_version_command_raises(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "en")
    _fake_tmux(monkeypatch, "tmux 3.4\n", returncode=1)
    with pytest.raises(HostError, match="not found"):
        select_host(_config("tmux"))


def test_tmux_unavailable_key_removed():
    assert "engine.hosts.tmux_unavailable" not in CATALOGS["en"]
    assert "engine.hosts.tmux_unavailable" not in CATALOGS["pt-BR"]


@pytest.mark.parametrize("herdr_ok", [True, False])
def test_auto_never_picks_tmux_inside_tmux_session(monkeypatch, herdr_ok):
    monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,123,0")
    monkeypatch.setattr("meister.hosts.select.is_herdr_available", lambda socket_path=None: herdr_ok)
    _fake_tmux(monkeypatch, "tmux 3.4\n")
    host = select_host(_config("auto"), client=MagicMock(name="client"))
    assert not isinstance(host, TmuxHost)
    assert isinstance(host, HerdrHost if herdr_ok else ProcessHost)


def test_unknown_mode_raises(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "en")
    with pytest.raises(HostError, match="Unknown runtime.host"):
        select_host(_config("bogus"))


def test_unknown_mode_raises_pt_br(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "pt-BR")
    with pytest.raises(HostError, match="desconhecido"):
        select_host(_config("bogus"))
