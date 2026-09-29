import pytest
from meister.config import (
    load_config,
    MeisterConfig,
)


def test_load_default_config_from_yaml(tmp_path):
    config_yaml = tmp_path / "meister.config.yaml"
    config_yaml.write_text("""
version: "1.0"
master:
  provider: "openrouter"
  model: "typesafe/jev-1.13"
architect:
  harness: "claude"
  model: "anthropic/claude-3-7-sonnet"
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "openai/gpt-6-luna"
      cost_per_m_tokens: 0.077
concurrency:
  parallel_tasks: true
  max_parallel_workers: 3
""")
    config = load_config(str(config_yaml))
    assert isinstance(config, MeisterConfig)
    assert config.master.model == "typesafe/jev-1.13"
    assert config.architect.model == "anthropic/claude-3-7-sonnet"
    assert len(config.workers.tier_order) == 1
    assert config.workers.tier_order[0].name == "luna"
    assert config.concurrency.max_parallel_workers == 3


def test_load_config_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert isinstance(config, MeisterConfig)
    assert config.version == "1.0"
    assert config.master.model == "typesafe/jev-1.13"
    assert config.architect.model == "anthropic/claude-sonnet-5"
    assert len(config.workers.tier_order) >= 1
    assert config.concurrency.max_parallel_workers == 4


def test_load_config_partial_yaml(tmp_path):
    config_yaml = tmp_path / "custom.yaml"
    config_yaml.write_text("""
concurrency:
  max_parallel_workers: 8
""")
    config = load_config(str(config_yaml))
    assert config.concurrency.max_parallel_workers == 8
    assert config.concurrency.parallel_tasks is True
    assert config.master.model == "typesafe/jev-1.13"
    assert config.architect.model == "anthropic/claude-sonnet-5"
    assert len(config.workers.tier_order) >= 1


def test_load_config_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_path_meister.yaml")


def test_default_worker_tiers_overrides(monkeypatch):
    from meister.config import _default_worker_tiers

    monkeypatch.setenv("MEISTER_DISABLE_LUNA", "true")
    tiers = _default_worker_tiers()
    tier_names = [t.name for t in tiers]
    assert "luna" not in tier_names
    assert tier_names[0] == "gemini_flash"

    monkeypatch.delenv("MEISTER_DISABLE_LUNA", raising=False)
    monkeypatch.setenv("MEISTER_PRIMARY_WORKER", "haiku")
    tiers_primary = _default_worker_tiers()
    assert tiers_primary[0].name == "haiku"

