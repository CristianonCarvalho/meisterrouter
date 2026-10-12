import pytest
import yaml

from meister import setup_cmd
from meister.config import load_config, validate_config
from meister.i18n import reset_language_cache


@pytest.fixture(params=["en", "pt-BR"])
def language(request, monkeypatch):
    monkeypatch.setenv("MEISTER_LANG", request.param)
    reset_language_cache()
    yield request.param
    reset_language_cache()


def _kiro_data():
    return yaml.safe_load(setup_cmd.generate_kiro_config_yaml_content())


def test_kiro_template_is_valid_yaml(language):
    data = _kiro_data()
    assert isinstance(data, dict)
    assert "gate" not in data


def test_kiro_template_uses_first_router(language):
    assert _kiro_data()["router"]["mode"] == "first"


def test_kiro_template_only_kiro_lanes(language):
    tiers = _kiro_data()["workers"]["tier_order"]
    assert [tier["name"] for tier in tiers] == ["tier_1", "tier_1b", "tier_2"]
    assert all(tier["harness"] == "kiro-cli" for tier in tiers)
    assert all("eligible_classes" not in tier for tier in tiers)


def test_kiro_template_passes_validation(language, tmp_path):
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text(setup_cmd.generate_kiro_config_yaml_content(), encoding="utf-8")

    cfg = load_config(str(config_file))
    errors = [issue for issue in validate_config(cfg) if issue.level == "error"]

    assert errors == []
    assert cfg.router.mode == "first"
    assert [tier.harness for tier in cfg.workers.tier_order] == ["kiro-cli"] * 3


def test_default_template_unchanged(language):
    assert "copilot" in setup_cmd.generate_config_yaml_content()
    assert "copilot" not in setup_cmd.generate_kiro_config_yaml_content()
