from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from meister.config import MeisterConfig, RuntimeConfig
from meister.hosts import HostError, HerdrHost, ProcessHost, select_host
from meister.i18n import reset_language_cache


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


def test_tmux_mode_raises_not_available_en(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "en")
    with pytest.raises(HostError, match="not available yet"):
        select_host(_config("tmux"))


def test_tmux_mode_raises_not_available_pt_br(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "pt-BR")
    with pytest.raises(HostError, match="ainda não disponível"):
        select_host(_config("tmux"))


def test_unknown_mode_raises(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "en")
    with pytest.raises(HostError, match="Unknown runtime.host"):
        select_host(_config("bogus"))


def test_unknown_mode_raises_pt_br(monkeypatch, herdr_up):
    _set_lang(monkeypatch, "pt-BR")
    with pytest.raises(HostError, match="desconhecido"):
        select_host(_config("bogus"))
