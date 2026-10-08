from __future__ import annotations

from meister.config import _parse_config_dict, validate_config


def test_enabled_overrides_tier_states_without_reordering():
    config = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "tier_order": [
                    {"name": "first", "harness": "codex", "model": "placeholder"},
                    {
                        "name": "second",
                        "harness": "codex",
                        "model": "placeholder",
                        "enabled": False,
                    },
                    {"name": "third", "harness": "codex", "model": "placeholder"},
                ],
                "enabled": {"first": False, "second": True},
            },
        }
    )

    assert [tier.name for tier in config.workers.tier_order] == ["second", "third"]
    assert [tier.name for tier in config.workers.disabled] == ["first"]
    assert config.workers.tier_order[0].enabled is True
    assert config.workers.disabled[0].enabled is False


def test_enabled_unknown_lane_reports_warning_and_is_ignored():
    config = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "tier_order": [
                    {"name": "known", "harness": "codex", "model": "placeholder"},
                ],
                "enabled": {"missing": False},
            },
        }
    )

    issues = [issue for issue in config._parse_issues if issue.path == "workers.enabled.missing"]
    assert len(issues) == 1
    assert issues[0].level == "warning"
    assert "missing" in issues[0].message
    assert [tier.name for tier in config.workers.tier_order] == ["known"]


def test_enabled_non_boolean_value_reports_error():
    config = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "tier_order": [
                    {"name": "known", "harness": "codex", "model": "placeholder"},
                ],
                "enabled": {"known": "false"},
            },
        }
    )

    issues = [
        issue
        for issue in config._parse_issues
        if issue.level == "error" and issue.path == "workers.enabled.known"
    ]
    assert len(issues) == 1
    assert [tier.name for tier in config.workers.tier_order] == ["known"]


def test_enabled_cannot_disable_every_lane():
    config = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "tier_order": [
                    {"name": "first", "harness": "codex", "model": "placeholder"},
                    {"name": "second", "harness": "codex", "model": "placeholder"},
                ],
                "enabled": {"first": False, "second": False},
            },
        }
    )

    assert config.workers.tier_order == []
    assert any(
        issue.level == "error" and issue.path == "workers.enabled"
        for issue in config._parse_issues
    )
    assert any(
        issue.level == "error" and issue.path == "workers.enabled"
        for issue in validate_config(config)
    )


def test_missing_enabled_map_preserves_tier_order_configuration():
    config = _parse_config_dict(
        {
            "router": {"mode": "first"},
            "workers": {
                "tier_order": [
                    {"name": "first", "harness": "codex", "model": "placeholder"},
                    {
                        "name": "second",
                        "harness": "codex",
                        "model": "placeholder",
                        "enabled": False,
                    },
                    {"name": "third", "harness": "codex", "model": "placeholder"},
                ],
            },
        }
    )

    assert [tier.name for tier in config.workers.tier_order] == ["first", "third"]
    assert [tier.name for tier in config.workers.disabled] == ["second"]
