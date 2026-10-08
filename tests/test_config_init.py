"""
tests.test_config_init — Testes para meister config init.
"""

from click.testing import CliRunner

from meister.cli import main
from meister.config import load_config


def test_config_init_creates_file_and_matches_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target_yaml = tmp_path / "meister.config.yaml"
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init", "--path", str(target_yaml)])
    assert result.exit_code == 0
    assert target_yaml.is_file()
    assert f"Arquivo de configuração gerado em: {target_yaml}" in result.output

    # Valida equivalência estrita com o catálogo padrão
    cfg_init = load_config(str(target_yaml))
    cfg_default = load_config()

    # Vias ativas e ordem
    assert [t.name for t in cfg_init.workers.tier_order] == [t.name for t in cfg_default.workers.tier_order]
    assert [t.name for t in cfg_init.workers.tier_order] == [
        "tier_1", "tier_1b", "tier_2", "tier_3"
    ]
    assert [t.harness for t in cfg_init.workers.tier_order] == [t.harness for t in cfg_default.workers.tier_order]
    assert [t.model for t in cfg_init.workers.tier_order] == [t.model for t in cfg_default.workers.tier_order]
    assert [t.cost_per_m_tokens for t in cfg_init.workers.tier_order] == [t.cost_per_m_tokens for t in cfg_default.workers.tier_order]
    assert [t.credit_usd for t in cfg_init.workers.tier_order] == [t.credit_usd for t in cfg_default.workers.tier_order]
    assert [t.eligible_classes for t in cfg_init.workers.tier_order] == [t.eligible_classes for t in cfg_default.workers.tier_order]

    # Vias desabilitadas
    assert [t.name for t in cfg_init.workers.disabled] == [t.name for t in cfg_default.workers.disabled]
    assert [t.name for t in cfg_init.workers.disabled] == ["tier_1c", "tier_3b"]
    assert [t.enabled for t in cfg_init.workers.disabled] == [False, False]
    assert [t.cost_per_m_tokens for t in cfg_init.workers.disabled] == [t.cost_per_m_tokens for t in cfg_default.workers.disabled]
    assert [t.eligible_classes for t in cfg_init.workers.disabled] == [
        t.eligible_classes for t in cfg_default.workers.disabled
    ]

    # Router
    assert cfg_init.router.mode == cfg_default.router.mode
    assert cfg_init.router.timeout_seconds == cfg_default.router.timeout_seconds


def test_config_init_locale_catalogs_match_default_tiers():
    from pathlib import Path

    import yaml

    from meister.locales.en_commands import MESSAGES as en_messages
    from meister.locales.pt_br_commands import MESSAGES as pt_messages

    repo_root = Path(__file__).resolve().parents[1]
    default = yaml.safe_load(
        (repo_root / "meister" / "default_config.yaml").read_text(encoding="utf-8")
    )
    expected_tiers = default["workers"]["tier_order"]

    for messages in (en_messages, pt_messages):
        template = yaml.safe_load(messages["commands.setup.config_template"])
        assert template["workers"]["tier_order"] == expected_tiers


def test_config_init_refuses_overwrite_without_force(tmp_path):
    target_yaml = tmp_path / "meister.config.yaml"
    target_yaml.write_text("custom: 1\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["config", "init", "--path", str(target_yaml)])
    assert result.exit_code == 1
    assert "já existe" in result.output
    assert "--force" in result.output
    assert target_yaml.read_text(encoding="utf-8") == "custom: 1\n"


def test_config_init_overwrites_with_force(tmp_path):
    target_yaml = tmp_path / "meister.config.yaml"
    target_yaml.write_text("custom: 1\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["config", "init", "--path", str(target_yaml), "--force"])
    assert result.exit_code == 0
    assert target_yaml.read_text(encoding="utf-8") != "custom: 1\n"
    assert "router:" in target_yaml.read_text(encoding="utf-8")


def test_config_init_contains_key_explanatory_comments(tmp_path):
    target_yaml = tmp_path / "meister.config.yaml"
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init", "--path", str(target_yaml)])
    assert result.exit_code == 0

    content = target_yaml.read_text(encoding="utf-8")

    # Ordem define a cadeia de fallback (sempre para a frente)
    assert "ordem define a cadeia de fallback" in content.lower()
    # enabled: false desliga uma via
    assert "enabled: false" in content
    # eligible_classes restringe as classes
    assert "eligible_classes" in content
    # router.mode: first ignora o Jev
    assert "router.mode: first" in content
    # tier_order SUBSTITUI a padrão inteira
    assert "SUBSTITUI" in content
    # meister models e meister config validate conferem
    assert "meister models" in content
    assert "meister config validate" in content
