from meister.config import MeisterConfig, WorkerTier, WorkersConfig, load_config
from meister.models import estimate_cost


def _config(tier_order=None, disabled=None):
    config = MeisterConfig()
    config.workers = WorkersConfig(
        tier_order=tier_order or [],
        disabled=disabled or [],
    )
    return config


def _tier(name, model, cost, enabled=True):
    return WorkerTier(
        name=name,
        harness="test",
        model=model,
        cost_per_m_tokens=cost,
        enabled=enabled,
    )


def test_estimate_cost_matches_tier_name_case_insensitively():
    config = _config(tier_order=[_tier("Fast-Path", "model-a", 0.25)])

    assert estimate_cost(" FAST-PATH ", tokens_in=1_000_000, config=config) == 0.25


def test_estimate_cost_matches_model_field_when_name_does_not_match():
    config = _config(tier_order=[_tier("custom-route", "vendor/model-b", 0.75)])

    assert estimate_cost("VENDOR/MODEL-B", tokens_out=1_000_000, config=config) == 0.75


def test_estimate_cost_uses_first_tier_for_repeated_models():
    config = _config(tier_order=[
        _tier("first-route", "same-model", 0.2),
        _tier("second-route", "same-model", 0.9),
    ])

    assert estimate_cost("same-model", tokens_in=1_000_000, config=config) == 0.2


def test_estimate_cost_includes_disabled_tiers():
    config = _config(disabled=[_tier("off-route", "disabled-model", 0.4, enabled=False)])

    assert estimate_cost("off-route", tokens_out=1_000_000, config=config) == 0.4


def test_estimate_cost_uses_configured_yaml_price(tmp_path):
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text(
        "workers:\n"
        "  tier_order:\n"
        "    - name: custom-yaml-route\n"
        "      harness: codex\n"
        "      model: custom-yaml-model\n"
        "      cost_per_m_tokens: 0.123456\n",
        encoding="utf-8",
    )
    config = load_config(str(config_file))

    assert estimate_cost(
        "custom-yaml-route",
        tokens_in=1_000_000,
        tokens_out=1_000_000,
        config=config,
    ) == 0.246912


def test_estimate_cost_ignores_cache_read_tokens():
    config = _config(tier_order=[_tier("route", "model", 0.5)])

    assert estimate_cost(
        "route",
        tokens_in=10_000,
        tokens_out=2_000,
        cache_read_tokens=9_000_000,
        config=config,
    ) == 0.006


def test_estimate_cost_rounds_combined_tokens_to_six_places():
    config = _config(tier_order=[_tier("route", "model", 0.123456)])

    assert estimate_cost(
        "route",
        tokens_in=1_000_000,
        tokens_out=1_000_000,
        config=config,
    ) == round((1_000_000 + 1_000_000) / 1_000_000 * 0.123456, 6)


def test_estimate_cost_returns_zero_for_unknown_or_empty_key():
    config = _config(tier_order=[_tier("known-route", "known-model", 0.5)])

    assert estimate_cost("unknown-route", tokens_in=1_000_000, config=config) == 0.0
    assert estimate_cost("  ", tokens_in=1_000_000, config=config) == 0.0
