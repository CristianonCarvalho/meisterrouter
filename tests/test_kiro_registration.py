from __future__ import annotations

import pytest

from meister import config as config_module
from meister.config import KNOWN_HARNESSES, _parse_config_dict, validate_config
from meister.harness_effort import HARNESS_EFFORT_VALUES
from meister.i18n import t

KIRO_HARNESSES = ("kiro-cli", "kiro")


def _config(harness, effort=None):
    tier = {"name": "lane-kiro", "harness": harness, "model": "auto"}
    if effort is not None:
        tier["effort"] = effort
    return _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {"tier_order": [tier]},
        }
    )


def _harness_warnings(issues):
    return [
        issue
        for issue in issues
        if issue.level == "warning" and issue.path == "workers.tier_order[0].harness"
    ]


def _fake_which(available):
    return lambda name, *args, **kwargs: f"/usr/bin/{name}" if name in available else None


@pytest.mark.parametrize("harness", KIRO_HARNESSES)
def test_kiro_is_known_harness(harness, monkeypatch):
    assert harness in KNOWN_HARNESSES
    monkeypatch.setattr(config_module.shutil, "which", _fake_which({"kiro-cli"}))

    issues = validate_config(_config(harness))

    unknown = t("reports.config.unknown_harness", harness=harness)
    assert not [issue for issue in issues if issue.message == unknown]
    assert not _harness_warnings(issues)


@pytest.mark.parametrize("harness", KIRO_HARNESSES)
def test_missing_kiro_executable_warns(harness, monkeypatch):
    monkeypatch.setattr(config_module.shutil, "which", _fake_which(set()))

    warnings = _harness_warnings(validate_config(_config(harness)))

    assert [issue.message for issue in warnings] == [
        t("reports.config.executable_not_found", executable="kiro-cli")
    ]


@pytest.mark.parametrize("binary", ["kiro-cli", "kiro"])
def test_kiro_executable_in_path_does_not_warn(binary, monkeypatch):
    monkeypatch.setattr(config_module.shutil, "which", _fake_which({binary}))

    assert not _harness_warnings(validate_config(_config("kiro-cli")))


def _effort_errors(config):
    return [
        issue
        for issue in validate_config(config)
        if issue.level == "error" and issue.path.endswith(".effort")
    ]


@pytest.mark.parametrize("harness", KIRO_HARNESSES)
def test_kiro_effort_values_are_accepted(harness):
    assert HARNESS_EFFORT_VALUES[harness] == ("low", "medium", "high", "xhigh", "max")
    for effort in HARNESS_EFFORT_VALUES[harness]:
        config = _config(harness, effort)
        assert config.workers.tier_order[0].effort == effort
        assert not _effort_errors(config)


@pytest.mark.parametrize("harness", KIRO_HARNESSES)
@pytest.mark.parametrize("effort", ["none", "minimal", "ultra"])
def test_kiro_invalid_effort_is_rejected(harness, effort):
    issues = _effort_errors(_config(harness, effort))

    assert len(issues) == 1
    assert issues[0].path == "workers.tier_order[0].effort"
    assert "lane-kiro" in issues[0].message
    assert harness in issues[0].message
    assert ", ".join(HARNESS_EFFORT_VALUES[harness]) in issues[0].message
