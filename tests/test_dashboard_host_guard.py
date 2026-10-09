from click.testing import CliRunner

import meister.dashboard.server as dashboard_server
from meister.cli import main


def _fake_start_server(calls):
    def fake(**kwargs):
        calls.append(kwargs)

    return fake


def test_loopback_host_starts_without_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard_server, "start_server", _fake_start_server(calls))

    res = CliRunner().invoke(main, ["dashboard", "--host", "127.0.0.1"])

    assert res.exit_code == 0, res.output
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"


def test_localhost_and_ipv6_loopback_are_allowed(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard_server, "start_server", _fake_start_server(calls))

    for host in ("localhost", "::1", "127.0.0.2"):
        res = CliRunner().invoke(main, ["dashboard", "--host", host])
        assert res.exit_code == 0, res.output

    assert [call["host"] for call in calls] == ["localhost", "::1", "127.0.0.2"]


def test_remote_host_without_flag_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard_server, "start_server", _fake_start_server(calls))

    res = CliRunner().invoke(main, ["dashboard", "--host", "0.0.0.0"])

    assert res.exit_code != 0
    assert "--allow-remote" in res.output
    assert calls == []


def test_remote_host_with_flag_warns_and_starts(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard_server, "start_server", _fake_start_server(calls))

    res = CliRunner().invoke(main, ["dashboard", "--host", "0.0.0.0", "--allow-remote"])

    assert res.exit_code == 0, res.output
    assert "0.0.0.0" in res.output
    assert len(calls) == 1
    assert calls[0]["host"] == "0.0.0.0"


def test_tui_mode_is_not_affected_by_host_guard(monkeypatch):
    called = []
    monkeypatch.setattr("meister.herdr.tui.run_tui_loop", lambda: called.append(True))
    monkeypatch.setattr(dashboard_server, "start_server", _fake_start_server([]))

    res = CliRunner().invoke(main, ["dashboard", "--tui", "--host", "0.0.0.0"])

    assert res.exit_code == 0, res.output
    assert called == [True]
