from dataclasses import asdict

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
      harness: "codex"
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
    assert config.architect.model == "claude-sonnet-5-5"
    assert config.architect.effort == "high"
    assert len(config.workers.tier_order) >= 1
    assert config.concurrency.max_parallel_workers == 4
    assert config.retry.pane_lost_attempts == 1
    assert config.retry.pane_lost_backoff_seconds == 5
    assert config.workers.idle_timeout_seconds == 600
    assert config.workers.max_runtime_seconds == 3600
    assert config.router.context_max_chars == 4000
    assert config.gate.cache is True


@pytest.mark.parametrize("value", ["499", "-1", "1.5", "500.0", '"five"', "true"])
def test_router_context_max_chars_invalid_values_report_validation_error(tmp_path, value):
    from meister.config import validate_config

    config_file = tmp_path / "invalid_context_max_chars.yaml"
    config_file.write_text(f"router:\n  context_max_chars: {value}\n")
    issues = validate_config(load_config(str(config_file)))

    assert any(
        issue.level == "error" and issue.path == "router.context_max_chars"
        for issue in issues
    )


def test_router_context_max_chars_accepts_minimum(tmp_path):
    from meister.config import validate_config

    config_file = tmp_path / "context_max_chars.yaml"
    config_file.write_text("router:\n  context_max_chars: 500\n")
    config = load_config(str(config_file))

    assert config.router.context_max_chars == 500
    assert not [
        issue for issue in validate_config(config)
        if issue.level == "error" and issue.path == "router.context_max_chars"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pane_lost_attempts", "-1"),
        ("pane_lost_attempts", "1.5"),
        ("pane_lost_attempts", '"one"'),
        ("pane_lost_backoff_seconds", "-0.1"),
        ("pane_lost_backoff_seconds", '"five"'),
        ("pane_lost_backoff_seconds", "true"),
    ],
)
def test_invalid_pane_lost_retry_config_reports_validation_error(tmp_path, field, value):
    from meister.config import validate_config

    config_file = tmp_path / "invalid_retry.yaml"
    config_file.write_text(f"retry:\n  {field}: {value}\n")

    errors = [
        issue for issue in validate_config(load_config(str(config_file)))
        if issue.level == "error"
    ]

    assert any(issue.path == f"retry.{field}" for issue in errors)


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
    assert config.architect.model == "claude-sonnet-5-5"
    assert config.architect.effort == "high"
    assert len(config.workers.tier_order) >= 1


def test_load_config_worker_tier_null_values_use_defaults(tmp_path):
    config_yaml = tmp_path / "null_worker_values.yaml"
    config_yaml.write_text("""
workers:
  tier_order:
    - name: null
      harness: null
      model: null
""")

    config = load_config(str(config_yaml))
    tier = config.workers.tier_order[0]

    assert tier.name == ""
    assert tier.harness == ""
    assert tier.model == ""
    assert tier.credit_usd is None


@pytest.mark.parametrize("value", ["0", "-1", '"x"', "true", ".inf"])
def test_worker_credit_price_invalid_values_report_validation_error(tmp_path, value):
    from meister.config import validate_config

    config_yaml = tmp_path / "invalid_credit_price.yaml"
    config_yaml.write_text(
        f"workers:\n  tier_order:\n    - name: copilot\n      credit_usd: {value}\n"
    )
    config = load_config(str(config_yaml))
    issues = validate_config(config)

    assert config.workers.tier_order[0].credit_usd is None
    assert any(
        issue.level == "error"
        and issue.path == "workers.tier_order[0].credit_usd"
        for issue in issues
    )


def test_default_worker_credit_prices():
    config = load_config()
    tiers = {tier.name: tier for tier in [*config.workers.tier_order, *config.workers.disabled]}

    assert tiers["copilot_luna"].credit_usd == 0.01
    assert tiers["codex_luna"].credit_usd is None


def test_worker_eligible_classes_parse_normalize_and_default(tmp_path):
    config_file = tmp_path / "eligible_classes.yaml"
    config_file.write_text("""
workers:
  tier_order:
    - name: multiple
      eligible_classes: [small, HIGH]
    - name: unrestricted
""")
    config = load_config(str(config_file))

    assert config.workers.tier_order[0].eligible_classes == ["SMALL", "HIGH"]
    assert config.workers.tier_order[1].eligible_classes == []

    defaults = load_config()
    default_tiers = {
        tier.name: tier for tier in [*defaults.workers.tier_order, *defaults.workers.disabled]
    }
    assert default_tiers["claude_sonnet"].eligible_classes == ["ESCALATE"]
    assert all(
        tier.eligible_classes == []
        for name, tier in default_tiers.items()
        if name != "claude_sonnet"
    )


@pytest.mark.parametrize("value", ['["HUGE"]', '"ESCALATE"', "[1]", "true"])
def test_invalid_worker_eligible_classes_report_validation_error(tmp_path, value):
    from meister.config import validate_config

    config_file = tmp_path / "invalid_eligible_classes.yaml"
    config_file.write_text(
        f"workers:\n  tier_order:\n    - name: restricted\n      eligible_classes: {value}\n"
    )
    issues = validate_config(load_config(str(config_file)))

    assert any(
        issue.level == "error"
        and issue.path == "workers.tier_order[0].eligible_classes"
        for issue in issues
    )

def test_worker_max_parallel_parsing_and_validation(tmp_path):
    from meister.config import validate_config

    config_yaml = tmp_path / "tier_limits.yaml"
    config_yaml.write_text("""
workers:
  tier_order:
    - name: limited
      max_parallel: 2
    - name: unlimited
      max_parallel: null
    - name: missing
    - name: disabled
      enabled: false
      max_parallel: 1
""")
    config = load_config(str(config_yaml))

    assert [tier.max_parallel for tier in config.workers.tier_order] == [2, None, None]
    assert config.workers.disabled[0].max_parallel == 1
    assert not [issue for issue in validate_config(config) if issue.level == "error"]


@pytest.mark.parametrize("value", ["0", "-1", '"2"', "true", "1.5"])
def test_worker_max_parallel_invalid_values_report_validation_error(tmp_path, value):
    from meister.config import validate_config

    config_yaml = tmp_path / "invalid_tier_limit.yaml"
    config_yaml.write_text(
        f"workers:\n  tier_order:\n    - name: limited\n      max_parallel: {value}\n"
    )
    issues = validate_config(load_config(str(config_yaml)))
    assert any(issue.level == "error" and "max_parallel" in issue.path for issue in issues)


def test_load_config_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_path_meister.yaml")


def test_removed_worker_environment_overrides_are_ignored(tmp_path, monkeypatch):
    from meister.config import _default_worker_tiers

    monkeypatch.chdir(tmp_path)
    baseline = _default_worker_tiers()
    monkeypatch.setenv("MEISTER_DISABLE_LUNA", "true")
    monkeypatch.setenv("MEISTER_PRIMARY_WORKER", "agy_gemini_flash")
    monkeypatch.setenv("MEISTER_LUNA_MODEL", "custom-model")
    monkeypatch.setenv("MEISTER_ENABLE_COPILOT", "false")
    assert _default_worker_tiers() == baseline


def test_enabled_removes_disabled_tier_and_preserves_order(tmp_path):
    config_yaml = tmp_path / "meister.config.yaml"
    config_yaml.write_text("""
workers:
  tier_order:
    - name: "luna"
      enabled: false
    - name: "gemini_flash"
      enabled: true
    - name: "haiku"
    - name: "sonnet"
      enabled: false
""")
    cfg = load_config(str(config_yaml))
    active_names = [t.name for t in cfg.workers.tier_order]
    disabled_names = [t.name for t in cfg.workers.disabled]

    assert active_names == ["gemini_flash", "haiku"]
    assert disabled_names == ["luna", "sonnet"]
    assert all(t.enabled is False for t in cfg.workers.disabled)
    assert all(t.enabled is True for t in cfg.workers.tier_order)


def test_enabled_non_boolean_reports_validation_error(tmp_path):
    from meister.config import validate_config

    config_yaml = tmp_path / "invalid_enabled.yaml"
    config_yaml.write_text("""
workers:
  tier_order:
    - name: "luna"
      enabled: "sim"
""")
    cfg = load_config(str(config_yaml))
    issues = validate_config(cfg)
    errors = [i for i in issues if i.level == "error"]
    assert any("enabled" in e.path for e in errors)
    assert any("booleano" in e.message.lower() for e in errors)


def test_effective_worker_timeouts_resolve_workers_tier_and_task_overrides(tmp_path):
    from meister.config import effective_worker_timeouts

    config_file = tmp_path / "worker_timeouts.yaml"
    config_file.write_text("""
workers:
  idle_timeout_seconds: 25
  max_runtime_seconds: 100
  tier_order:
    - name: inherited
    - name: override
      idle_timeout_seconds: 12.5
      max_runtime_seconds: 50
""")
    cfg = load_config(str(config_file))

    assert effective_worker_timeouts(cfg, "inherited") == {
        "idle_timeout_seconds": 25.0,
        "max_runtime_seconds": 100.0,
    }
    assert effective_worker_timeouts(cfg, "override") == {
        "idle_timeout_seconds": 12.5,
        "max_runtime_seconds": 50.0,
    }
    assert effective_worker_timeouts(
        cfg,
        "override",
        {"idle_timeout_seconds": 2, "max_runtime_seconds": 3},
    ) == {"idle_timeout_seconds": 2.0, "max_runtime_seconds": 3.0}
    assert effective_worker_timeouts(cfg, "override", {"timeout": 4}) == {
        "idle_timeout_seconds": 12.5,
        "max_runtime_seconds": 4.0,
    }
    assert effective_worker_timeouts(
        cfg, "override", {"timeout": 4, "max_runtime_seconds": 6}
    )["max_runtime_seconds"] == 6


@pytest.mark.parametrize("value", ["-1", "true", '"2"', ".nan", ".inf"])
@pytest.mark.parametrize("field", ["idle_timeout_seconds", "max_runtime_seconds"])
def test_worker_timeout_invalid_config_is_error(tmp_path, field, value):
    from meister.config import validate_config

    config_file = tmp_path / "invalid_worker_timeout.yaml"
    config_file.write_text(f"workers:\n  {field}: {value}\n")

    issues = validate_config(load_config(str(config_file)))
    assert any(
        issue.level == "error" and issue.path == f"workers.{field}"
        for issue in issues
    )


def test_invalid_tier_timeout_is_validation_error(tmp_path):
    from meister.config import validate_config

    config_file = tmp_path / "invalid_tier_timeout.yaml"
    config_file.write_text("""
workers:
  tier_order:
    - name: custom
      idle_timeout_seconds: true
      max_runtime_seconds: -1
""")

    errors = [
        issue for issue in validate_config(load_config(str(config_file)))
        if issue.level == "error"
    ]
    assert any("tier_order[0].idle_timeout_seconds" in issue.path for issue in errors)
    assert any("tier_order[0].max_runtime_seconds" in issue.path for issue in errors)


def test_worker_timeout_both_disabled_warns(tmp_path):
    from meister.config import validate_config

    config_file = tmp_path / "timeouts_disabled.yaml"
    config_file.write_text("""
workers:
  idle_timeout_seconds: 0
  max_runtime_seconds: 0
""")

    issues = validate_config(load_config(str(config_file)))
    assert any(
        issue.level == "warning" and "sem proteção contra worker travado" in issue.message
        for issue in issues
    )


def test_architect_effort_parsing_and_validation(tmp_path):
    from meister.config import validate_config

    # Default
    cfg_default = load_config()
    assert cfg_default.architect.model == "claude-sonnet-5-5"
    assert cfg_default.architect.effort == "high"

    # Custom valid effort
    valid_yaml = tmp_path / "valid_arch.yaml"
    valid_yaml.write_text("""
architect:
  effort: "medium"
""")
    cfg_valid = load_config(str(valid_yaml))
    assert cfg_valid.architect.effort == "medium"
    issues_valid = validate_config(cfg_valid)
    assert not any(i.path == "architect.effort" for i in issues_valid)

    # Invalid effort
    invalid_yaml = tmp_path / "invalid_arch.yaml"
    invalid_yaml.write_text("""
architect:
  effort: "ultra"
""")
    cfg_invalid = load_config(str(invalid_yaml))
    issues_invalid = validate_config(cfg_invalid)
    errors = [i for i in issues_invalid if i.level == "error"]
    assert any(i.path == "architect.effort" and "ultra" in i.message for i in errors)


def test_validator_cases(tmp_path, monkeypatch):
    from meister.config import validate_config

    # 1. Lista vazia
    y_empty = tmp_path / "empty.yaml"
    y_empty.write_text("""
workers:
  tier_order: []
""")
    cfg = load_config(str(y_empty))
    issues = validate_config(cfg)
    errors = [i for i in issues if i.level == "error"]
    assert any(i.path == "workers.tier_order" and "vazio" in i.message for i in errors)

    # 2. Todas desabilitadas
    y_all_dis = tmp_path / "all_disabled.yaml"
    y_all_dis.write_text("""
workers:
  tier_order:
    - name: luna
      enabled: false
    - name: haiku
      enabled: false
""")
    cfg = load_config(str(y_all_dis))
    issues = validate_config(cfg)
    errors = [i for i in issues if i.level == "error"]
    assert any(i.path == "workers.tier_order" and "desabilitadas" in i.message for i in errors)

    # 3. Nome repetido
    y_dup = tmp_path / "dup.yaml"
    y_dup.write_text("""
workers:
  tier_order:
    - name: luna
    - name: luna
""")
    cfg = load_config(str(y_dup))
    issues = validate_config(cfg)
    errors = [i for i in issues if i.level == "error"]
    assert any(i.path == "workers.tier_order[1].name" and "repetido" in i.message for i in errors)

    # 4. Nome vazio
    y_empty_name = tmp_path / "empty_name.yaml"
    y_empty_name.write_text("""
workers:
  tier_order:
    - name: ""
""")
    cfg = load_config(str(y_empty_name))
    issues = validate_config(cfg)
    errors = [i for i in issues if i.level == "error"]
    assert any(i.path == "workers.tier_order[0].name" and "vazio" in i.message for i in errors)

    # 5. max_retries negativo
    y_neg_retries = tmp_path / "neg_retries.yaml"
    y_neg_retries.write_text("""
workers:
  tier_order:
    - name: luna
      max_retries: -1
""")
    cfg = load_config(str(y_neg_retries))
    issues = validate_config(cfg)
    errors = [i for i in issues if i.level == "error"]
    assert any(i.path == "workers.tier_order[0].max_retries" and "-1" in i.message for i in errors)

    # 6. concurrency.max_parallel_workers < 1
    y_zero_workers = tmp_path / "zero_workers.yaml"
    y_zero_workers.write_text("""
concurrency:
  max_parallel_workers: 0
""")
    cfg = load_config(str(y_zero_workers))
    issues = validate_config(cfg)
    errors = [i for i in issues if i.level == "error"]
    assert any(i.path == "concurrency.max_parallel_workers" for i in errors)

    # 7. Harness desconhecido -> aviso
    y_unk_harness = tmp_path / "unk_harness.yaml"
    y_unk_harness.write_text("""
workers:
  tier_order:
    - name: custom_worker
      harness: "non_existent_harness_xyz_987"
      model: "test-model"
""")
    cfg = load_config(str(y_unk_harness))
    issues = validate_config(cfg)
    warnings = [i for i in issues if i.level == "warning"]
    assert any("non_existent_harness_xyz_987" in w.message for w in warnings)

    # 8. Harness claude sem executável no PATH -> aviso
    monkeypatch.setenv("PATH", "/dummy_path_without_executables")
    y_claude_no_bin = tmp_path / "claude_no_bin.yaml"
    y_claude_no_bin.write_text("""
workers:
  tier_order:
    - name: haiku
      harness: "claude"
      model: "haiku"
""")
    cfg = load_config(str(y_claude_no_bin))
    issues = validate_config(cfg)
    warnings = [i for i in issues if i.level == "warning"]
    assert any("claude" in w.message and "PATH" in w.message for w in warnings)

    # 9. Configuração padrão dá ZERO erros
    monkeypatch.undo()
    default_cfg = load_config()
    default_issues = validate_config(default_cfg)
    default_errors = [i for i in default_issues if i.level == "error"]
    assert len(default_errors) == 0


def test_removed_copilot_and_primary_environment_overrides_are_ignored(monkeypatch):
    from meister.config import _default_worker_tiers

    baseline_tiers = _default_worker_tiers()
    # codex_luna vem desligada por padrao: fora das vias ativas
    assert [t.name for t in baseline_tiers] == [
        "copilot_luna", "agy_gemini_flash", "claude_sonnet"
    ]
    monkeypatch.setenv("MEISTER_ENABLE_COPILOT", "true")
    monkeypatch.setenv("MEISTER_PRIMARY_WORKER", "codex_luna")
    monkeypatch.setenv("MEISTER_LUNA_MODEL", "custom-model")
    assert _default_worker_tiers() == baseline_tiers


def test_example_yaml_loads_and_validates():
    from meister.config import validate_config

    cfg = load_config("meister.config.example.yaml")
    assert isinstance(cfg, MeisterConfig)
    issues = validate_config(cfg)
    errors = [i for i in issues if i.level == "error"]
    assert len(errors) == 0

    # As quatro vias padrão permanecem habilitadas.
    active_names = [t.name for t in cfg.workers.tier_order]
    disabled_names = [t.name for t in cfg.workers.disabled]
    assert active_names == ["copilot_luna", "codex_luna", "agy_gemini_flash", "claude_sonnet"]
    assert disabled_names == []


@pytest.mark.parametrize("value", ['"yes"', "1", "null"])
def test_gate_cache_must_be_boolean(tmp_path, value):
    from meister.config import validate_config

    config_yaml = tmp_path / "invalid_gate_cache.yaml"
    config_yaml.write_text(f"gate:\n  cache: {value}\n")
    config = load_config(str(config_yaml))

    assert config.gate.cache is True
    assert any(
        issue.level == "error" and issue.path == "gate.cache"
        for issue in validate_config(config)
    )


def test_default_config_has_no_errors_or_warnings_and_metadata_is_info(tmp_path, monkeypatch):
    from meister.config import validate_config

    monkeypatch.chdir(tmp_path)
    for env_var in (
        "MEISTER_CONFIG_PATH",
    ):
        monkeypatch.delenv(env_var, raising=False)

    fake_path = tmp_path / "bin"
    fake_path.mkdir()
    for name in ("claude", "copilot"):
        executable = fake_path / name
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_path))
    monkeypatch.setattr("meister.jev.get_api_key", lambda: "test-key")

    issues = validate_config(load_config())
    errors = [issue for issue in issues if issue.level == "error"]
    warnings = [issue for issue in issues if issue.level == "warning"]
    info = [issue for issue in issues if issue.level == "info"]

    assert not errors
    assert not warnings
    assert not info


def test_validate_config_normalizes_harness_before_checking(tmp_path, monkeypatch):
    from meister.config import validate_config

    config_yaml = tmp_path / "uppercase_harness.yaml"
    config_yaml.write_text("""
workers:
  tier_order:
    - name: claude
      harness: Claude
      model: haiku
""")
    monkeypatch.setattr("meister.config.shutil.which", lambda _: "/usr/bin/claude")

    issues = validate_config(load_config(str(config_yaml)))

    assert not any("desconhecido" in issue.message for issue in issues)


def test_router_mode_defaults_and_parses(tmp_path):
    default = load_config()
    assert default.router.mode == "jev"
    assert default.router.timeout_seconds == 10
    assert default.router.max_attempts == 2
    assert default.router.unavailable_cooldown_seconds == 300

    config_yaml = tmp_path / "router.yaml"
    config_yaml.write_text("router:\n  mode: jev\n")
    assert load_config(str(config_yaml)).router.mode == "jev"

    first_yaml = tmp_path / "first_router.yaml"
    first_yaml.write_text("router: {mode: first}\n")
    assert load_config(str(first_yaml)).router.mode == "first"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", "0"),
        ("timeout_seconds", "-1"),
        ("max_attempts", "0"),
        ("max_attempts", "-1"),
        ("unavailable_cooldown_seconds", "-1"),
        ("timeout_seconds", '"ten"'),
        ("timeout_seconds", "true"),
        ("max_attempts", "1.5"),
        ("max_attempts", "true"),
        ("unavailable_cooldown_seconds", '"later"'),
        ("unavailable_cooldown_seconds", "false"),
    ],
)
def test_router_timing_validation_rejects_invalid_values(tmp_path, field, value):
    from meister.config import validate_config

    config_yaml = tmp_path / "invalid_router_timing.yaml"
    config_yaml.write_text(f"router:\n  {field}: {value}\n")
    issues = validate_config(load_config(str(config_yaml)))
    assert any(issue.level == "error" and issue.path == f"router.{field}" for issue in issues)

def test_router_mode_validation_and_missing_api_key_warning(tmp_path, monkeypatch):
    from meister.config import validate_config

    invalid_yaml = tmp_path / "invalid_router.yaml"
    invalid_yaml.write_text("router:\n  mode: random\n")
    issues = validate_config(load_config(str(invalid_yaml)))
    assert any(issue.level == "error" and issue.path == "router.mode" for issue in issues)

    jev_yaml = tmp_path / "jev_router.yaml"
    jev_yaml.write_text("router:\n  mode: jev\n")
    monkeypatch.setattr(
        "meister.jev.get_api_key",
        lambda: (_ for _ in ()).throw(ValueError("missing key")),
    )
    issues = validate_config(load_config(str(jev_yaml)))
    assert any(
        issue.level == "warning"
        and issue.path == "router.mode"
        and "sem chave, o roteamento cai na primeira via" in issue.message
        for issue in issues
    )


def test_router_jev_uses_metadata_without_informational_issues(tmp_path):
    from meister.config import validate_config

    config_yaml = tmp_path / "jev_metadata.yaml"
    config_yaml.write_text("""
router:
  mode: jev
workers:
  tier_order:
    - name: luna
      harness: codex
      model: gpt-6-luna
      best_for: [small_edits]
      cost_per_m_tokens: 0.077
""")
    issues = validate_config(load_config(str(config_yaml)))
    assert not any(issue.level == "info" for issue in issues)


def test_packaged_default_config_values_and_deep_merge(tmp_path, monkeypatch):
    import importlib.resources
    from meister.config import validate_config

    resource = importlib.resources.files("meister").joinpath("default_config.yaml")
    assert resource.is_file()
    assert 'package_data={"meister": ["default_config.yaml"]}' in open(
        "setup.py", encoding="utf-8"
    ).read()

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)
    default = load_config()
    # codex_luna vem desligada por padrao (creditos do Codex limitados): fica fora da ordem ativa
    assert [tier.name for tier in default.workers.tier_order] == [
        "copilot_luna", "agy_gemini_flash", "claude_sonnet"
    ]
    assert [tier.name for tier in default.workers.disabled] == ["codex_luna"]
    assert default.master.model == "typesafe/jev-1.13"
    assert default.architect.effort == "high"
    assert not [issue for issue in validate_config(default) if issue.level == "error"]

    partial = tmp_path / "partial.yaml"
    partial.write_text("router: {mode: jev}\nconcurrency: {max_parallel_workers: 2}\n")
    merged = load_config(str(partial))
    assert merged.router.mode == "jev"
    assert [tier.name for tier in merged.workers.tier_order] == [
        "copilot_luna", "agy_gemini_flash", "claude_sonnet"
    ]
    assert [tier.name for tier in merged.workers.disabled] == ["codex_luna"]
    assert merged.concurrency.max_parallel_workers == 2
    assert merged.concurrency.parallel_tasks is True

    replacement = tmp_path / "replacement.yaml"
    replacement.write_text("""
workers:
  tier_order:
    - {name: custom, harness: codex, model: local-model}
""")
    replaced = load_config(str(replacement))
    assert [tier.name for tier in replaced.workers.tier_order] == ["custom"]


def test_removed_environment_variables_do_not_change_default_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)
    baseline = asdict(load_config())
    for key, value in {
        "MEISTER_LUNA_MODEL": "changed",
        "MEISTER_PRIMARY_WORKER": "codex_luna",
        "MEISTER_DISABLE_LUNA": "1",
        "MEISTER_ENABLE_COPILOT": "0",
    }.items():
        monkeypatch.setenv(key, value)
    assert asdict(load_config()) == baseline


def test_removed_environment_variables_do_not_override_user_tier_models(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)
    for key, value in {
        "MEISTER_LUNA_MODEL": "env-luna",
        "MEISTER_GEMINI_MODEL": "env-gemini",
        "MEISTER_GEMINI_FLASH_MODEL": "env-gemini-flash",
        "MEISTER_HAIKU_MODEL": "env-haiku",
        "MEISTER_SONNET_MODEL": "env-sonnet",
        "MEISTER_COPILOT_MODEL": "env-copilot",
        "MEISTER_PRIMARY_WORKER": "env-primary",
        "MEISTER_DISABLE_LUNA": "true",
        "MEISTER_ENABLE_COPILOT": "true",
        "JEV_MODEL": "env-jev",
    }.items():
        monkeypatch.setenv(key, value)

    config_yaml = tmp_path / "meister.config.yaml"
    config_yaml.write_text("""
workers:
  tier_order:
    - {name: a, harness: codex, model: m1}
    - {name: b, harness: copilot, model: m2}
""")

    config = load_config()
    tiers = config.workers.tier_order

    assert [tier.model for tier in tiers] == ["m1", "m2"]
    assert [tier.name for tier in tiers] == ["a", "b"]


def test_native_harness_reports_migration_error_and_package_default_is_copied(tmp_path):
    from meister.config import _default_config_data, validate_config

    custom = tmp_path / "native.yaml"
    custom.write_text("""
workers:
  tier_order:
    - {name: legacy, harness: native, model: local}
""")
    errors = [
        issue for issue in validate_config(load_config(str(custom)))
        if issue.level == "error"
    ]
    assert any(
        issue.path == "workers.tier_order[0].harness"
        and issue.message == "harness 'native' foi removido; declare codex, agy, claude ou copilot"
        for issue in errors
    )

    disabled = tmp_path / "disabled-native.yaml"
    disabled.write_text("""
workers:
  tier_order:
    - {name: legacy, harness: native, model: local, enabled: false}
""")
    disabled_errors = [
        issue for issue in validate_config(load_config(str(disabled)))
        if issue.level == "error"
    ]
    assert any(
        issue.path == "workers.disabled[0].harness"
        and issue.message == "harness 'native' foi removido; declare codex, agy, claude ou copilot"
        for issue in disabled_errors
    )

    first_copy = _default_config_data()
    first_copy["workers"]["tier_order"].clear()
    assert len(_default_config_data()["workers"]["tier_order"]) == 4
