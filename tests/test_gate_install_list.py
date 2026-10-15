import subprocess
import types

import pytest

from meister import env_setup
from meister.config import MeisterConfig, _parse_config_dict


@pytest.fixture
def runs(monkeypatch):
    calls = []
    results = []

    class Result:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, **kwargs):
        calls.append(command)
        if results:
            return results.pop(0)
        return Result(0, f"out:{' '.join(command)}", "")

    monkeypatch.setattr(
        env_setup,
        "subprocess",
        types.SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    return calls, results, Result


def _config(install):
    config = MeisterConfig()
    config.gate.install = install
    return config


def test_single_string_still_runs_as_one_command(tmp_path, runs):
    calls, _, _ = runs
    ok, output = env_setup.prepare_environment(str(tmp_path), _config("make deps"))
    assert ok and calls == [["make", "deps"]]
    assert output == "out:make deps"


def test_list_runs_each_command_in_order_without_shell(tmp_path, runs):
    calls, _, _ = runs
    config = _config(["npm ci", "npm --prefix backend ci"])
    ok, output = env_setup.prepare_environment(str(tmp_path), config)
    assert ok
    assert calls == [["npm", "ci"], ["npm", "--prefix", "backend", "ci"]]
    assert output == "out:npm ci\nout:npm --prefix backend ci"


def test_failing_command_stops_the_list_and_reports(tmp_path, runs):
    calls, results, Result = runs
    results.extend([Result(0, "first", ""), Result(2, "", "boom")])
    config = _config(["npm ci", "npm --prefix backend ci", "make never"])
    ok, message = env_setup.prepare_environment(str(tmp_path), config)
    assert ok is False
    assert calls == [["npm", "ci"], ["npm", "--prefix", "backend", "ci"]]
    assert "npm --prefix backend ci" in message
    assert "rc=2" in message
    assert "boom" in message


def _parse(install):
    return _parse_config_dict({"gate": {"install": install}})


def test_config_accepts_string_list_and_null():
    assert _parse("npm ci")._parse_issues == []
    assert _parse(["npm ci", "npm --prefix backend ci"])._parse_issues == []
    assert _parse(None)._parse_issues == []


def test_config_keeps_the_list_value():
    assert _parse(["npm ci", "make"]).gate.install == ["npm ci", "make"]


@pytest.mark.parametrize("bad", [[], [""], ["npm ci", 3], ["   "], 42, ""])
def test_config_rejects_invalid_install(bad):
    issues = [issue for issue in _parse(bad)._parse_issues if issue.path == "gate.install"]
    assert len(issues) == 1 and issues[0].level == "error"
