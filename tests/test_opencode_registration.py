from __future__ import annotations

import pytest

from meister import config as config_module
from meister.config import KNOWN_HARNESSES, _parse_config_dict, validate_config
from meister.harness_effort import HARNESS_EFFORT_VALUES
from meister.i18n import t
from meister.worker import _validate_harness_effort

OPENCODE_HARNESS = "opencode"


def _config(harness, effort=None):
    tier = {"name": "lane-opencode", "harness": harness, "model": "default"}
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


def _effort_errors(config):
    return [
        issue
        for issue in validate_config(config)
        if issue.level == "error" and issue.path.endswith(".effort")
    ]


def test_opencode_is_known_harness(monkeypatch):
    assert OPENCODE_HARNESS in KNOWN_HARNESSES
    monkeypatch.setattr(config_module.shutil, "which", _fake_which({"opencode"}))

    issues = validate_config(_config(OPENCODE_HARNESS))

    unknown = t("reports.config.unknown_harness", harness=OPENCODE_HARNESS)
    assert not [issue for issue in issues if issue.message == unknown]
    assert not _harness_warnings(issues)


def test_missing_opencode_executable_warns(monkeypatch):
    monkeypatch.setattr(config_module.shutil, "which", _fake_which(set()))

    warnings = _harness_warnings(validate_config(_config(OPENCODE_HARNESS)))

    assert [issue.message for issue in warnings] == [
        t("reports.config.executable_not_found", executable="opencode")
    ]


def test_opencode_executable_in_path_does_not_warn(monkeypatch):
    monkeypatch.setattr(config_module.shutil, "which", _fake_which({"opencode"}))

    assert not _harness_warnings(validate_config(_config(OPENCODE_HARNESS)))


def test_opencode_effort_values_is_empty_tuple():
    assert OPENCODE_HARNESS in HARNESS_EFFORT_VALUES
    assert HARNESS_EFFORT_VALUES[OPENCODE_HARNESS] == ()


def test_opencode_rejects_effort():
    assert OPENCODE_HARNESS in HARNESS_EFFORT_VALUES
    assert HARNESS_EFFORT_VALUES[OPENCODE_HARNESS] == ()

    config = _config(OPENCODE_HARNESS, effort="high")
    issues = _effort_errors(config)

    assert len(issues) == 1
    assert issues[0].path == "workers.tier_order[0].effort"
    assert issues[0].message == t(
        "tiers.unsupported_effort",
        lane="lane-opencode",
        harness=OPENCODE_HARNESS,
    )

    with pytest.raises(ValueError) as exc_info:
        _validate_harness_effort(OPENCODE_HARNESS, "high", "lane-opencode")
    assert str(exc_info.value) == t(
        "tiers.unsupported_effort",
        lane="lane-opencode",
        harness=OPENCODE_HARNESS,
    )


def test_opencode_without_effort_is_valid():
    config_absent = _config(OPENCODE_HARNESS)
    assert not _effort_errors(config_absent)

    config_explicit_none = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "tier_order": [
                    {
                        "name": "lane-opencode",
                        "harness": OPENCODE_HARNESS,
                        "model": "default",
                        "effort": None,
                    }
                ]
            },
        }
    )
    assert not _effort_errors(config_explicit_none)

    _validate_harness_effort(OPENCODE_HARNESS, None, "lane-opencode")


def test_unknown_harness_is_rejected(monkeypatch):
    monkeypatch.setattr(config_module.shutil, "which", _fake_which(set()))
    harness = "unknown-harness-xyz"
    assert harness not in KNOWN_HARNESSES

    warnings = _harness_warnings(validate_config(_config(harness)))

    assert [issue.message for issue in warnings] == [
        t("reports.config.unknown_harness", harness=harness)
    ]
