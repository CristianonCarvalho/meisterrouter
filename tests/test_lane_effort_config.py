from __future__ import annotations

import pytest

from meister.config import _parse_config_dict, validate_config
from meister.harness_effort import HARNESS_EFFORT_VALUES


@pytest.mark.parametrize(
    ("harness", "efforts"),
    [
        ("copilot", ("none", "minimal", "low", "medium", "high", "xhigh", "max")),
        ("claude", ("low", "medium", "high", "xhigh", "max")),
        ("agy", ("low", "medium", "high", "xhigh", "max")),
        ("codex", ("minimal", "low", "medium", "high", "xhigh")),
    ],
)
def test_effort_values_are_valid_for_each_harness(harness, efforts):
    assert HARNESS_EFFORT_VALUES[harness] == efforts
    for effort in efforts:
        config = _config_with_effort(harness, effort)
        assert config.workers.tier_order[0].effort == effort
        assert not _effort_issues(config)


@pytest.mark.parametrize(
    ("harness", "effort"),
    [
        ("copilot", "ultra"),
        ("claude", "minimal"),
        ("agy", "none"),
        ("codex", "max"),
    ],
)
def test_effort_outside_harness_values_reports_lane_error(harness, effort):
    issues = _effort_issues(_config_with_effort(harness, effort))

    assert len(issues) == 1
    assert issues[0].path == "workers.tier_order[0].effort"
    assert "lane-one" in issues[0].message
    assert harness in issues[0].message
    assert ", ".join(HARNESS_EFFORT_VALUES[harness]) in issues[0].message


def test_effort_is_rejected_for_harness_without_effort_table():
    issues = _effort_issues(_config_with_effort("custom", "high"))

    assert len(issues) == 1
    assert issues[0].path == "workers.tier_order[0].effort"
    assert "lane-one" in issues[0].message
    assert "custom" in issues[0].message
    assert "none" in issues[0].message or "nenhum" in issues[0].message


@pytest.mark.parametrize("effort", [None, "", "   "])
def test_missing_or_empty_effort_is_none(effort):
    config = _config_with_effort("codex", effort)

    assert config.workers.tier_order[0].effort is None
    assert not _effort_issues(config)


def _config_with_effort(harness, effort):
    tier = {"name": "lane-one", "harness": harness, "model": "placeholder"}
    if effort is not None:
        tier["effort"] = effort
    return _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {"tier_order": [tier]},
        }
    )


def _effort_issues(config):
    return [
        issue
        for issue in validate_config(config)
        if issue.level == "error" and issue.path.endswith(".effort")
    ]
