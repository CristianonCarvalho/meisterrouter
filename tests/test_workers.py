import pytest
from unittest.mock import AsyncMock
from meister.config import load_config, MeisterConfig, WorkerTier, WorkersConfig
from meister.herdr.workers import (
    WorkerSpawner,
    detect_quota_or_rate_limit,
    spawn_worker_pane,
    get_next_tier,
)


def test_detect_quota_and_escalate_tier(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "openai/gpt-6-luna"
      cost_per_m_tokens: 0.077
    - name: "gemini_flash"
      harness: "native"
      model: "google/gemini-2.5-flash"
      cost_per_m_tokens: 0.577
""")
    config = load_config(str(cfg_file))
    spawner = WorkerSpawner(config, herdr_client=None)

    assert detect_quota_or_rate_limit("Error 429: Rate limit exceeded") is True
    assert detect_quota_or_rate_limit("Credit balance too low (402)") is True
    assert detect_quota_or_rate_limit("Successfully compiled") is False

    next_tier = spawner.get_next_tier("luna")
    assert next_tier is not None
    assert next_tier.name == "gemini_flash"
    assert spawner.get_next_tier("gemini_flash") is None


def test_detect_quota_various_error_formats():
    assert detect_quota_or_rate_limit("HTTP status 429: Too Many Requests") is True
    assert detect_quota_or_rate_limit("{\"error\": {\"code\": 429, \"message\": \"Rate limit reached\"}}") is True
    assert detect_quota_or_rate_limit("402 Payment Required: Insufficient credits") is True
    assert detect_quota_or_rate_limit("Error: RESOURCE_EXHAUSTED quota exceeded") is True
    assert detect_quota_or_rate_limit("anthropic.OverloadedError: Provider is overloaded") is True
    assert detect_quota_or_rate_limit("You exceeded your current quota, please check your plan") is True

    # Non-quota outputs
    assert detect_quota_or_rate_limit("All 42 tests passed") is False
    assert detect_quota_or_rate_limit("Building project... done") is False
    assert detect_quota_or_rate_limit("") is False
    assert detect_quota_or_rate_limit(None) is False


def test_resolve_command():
    config = MeisterConfig(
        workers=WorkersConfig(
            tier_order=[
                WorkerTier(name="luna", harness="native", model="openai/gpt-6-luna"),
                WorkerTier(name="haiku", harness="claude", model="anthropic/claude-3-5-haiku-20241022"),
                WorkerTier(name="codex_tier", harness="codex", model="openai/codex-1"),
                WorkerTier(name="custom_cli", harness="custom_agent", model="custom/model"),
            ]
        )
    )
    spawner = WorkerSpawner(config, herdr_client=None)

    # Native tier resolves to meister worker --model <tier_name>
    cmd_native = spawner.resolve_command("luna")
    assert cmd_native == ["meister", "worker", "--model", "luna"]

    # Claude harness resolves to claude --model <model>
    cmd_claude = spawner.resolve_command("haiku")
    assert cmd_claude == ["claude", "--model", "anthropic/claude-3-5-haiku-20241022"]

    # Codex harness resolves to codex --model <model>
    cmd_codex = spawner.resolve_command("codex_tier")
    assert cmd_codex == ["codex", "--model", "openai/codex-1"]

    # Custom harness resolves to harness --model <model>
    cmd_custom = spawner.resolve_command("custom_cli")
    assert cmd_custom == ["custom_agent", "--model", "custom/model"]

    # Task context command override
    cmd_override = spawner.resolve_command("luna", task_context={"command": ["my-agent", "--run"]})
    assert cmd_override == ["my-agent", "--run"]


@pytest.mark.asyncio
async def test_spawn_worker_pane_success():
    config = MeisterConfig(
        workers=WorkersConfig(
            tier_order=[
                WorkerTier(name="luna", harness="native", model="openai/gpt-6-luna"),
                WorkerTier(name="gemini_flash", harness="native", model="google/gemini-2.5-flash"),
            ]
        )
    )
    mock_client = AsyncMock()
    mock_client.split_pane.return_value = "pane_123"

    spawner = WorkerSpawner(config, herdr_client=mock_client)
    pane_id, tier = await spawner.spawn_worker_pane("luna", direction="right", split_ratio=0.6)

    assert pane_id == "pane_123"
    assert tier.name == "luna"
    mock_client.split_pane.assert_awaited_once_with(
        direction="right",
        command=["meister", "worker", "--model", "luna"],
        split_ratio=0.6,
    )


@pytest.mark.asyncio
async def test_spawn_worker_pane_errors():
    config = MeisterConfig(
        workers=WorkersConfig(
            tier_order=[
                WorkerTier(name="luna", harness="native", model="openai/gpt-6-luna"),
            ]
        )
    )
    spawner_no_client = WorkerSpawner(config, herdr_client=None)

    # Missing client raises RuntimeError
    with pytest.raises(RuntimeError, match="Herdr client is required"):
        await spawner_no_client.spawn_worker_pane("luna")

    mock_client = AsyncMock()
    spawner = WorkerSpawner(config, herdr_client=mock_client)

    # Unknown tier raises ValueError
    with pytest.raises(ValueError, match="Worker tier 'unknown' not found"):
        await spawner.spawn_worker_pane("unknown")


@pytest.mark.asyncio
async def test_escalate_worker():
    config = MeisterConfig(
        workers=WorkersConfig(
            tier_order=[
                WorkerTier(name="luna", harness="native", model="openai/gpt-6-luna"),
                WorkerTier(name="gemini_flash", harness="native", model="google/gemini-2.5-flash"),
            ]
        )
    )
    mock_client = AsyncMock()
    mock_client.split_pane.return_value = "pane_456"

    spawner = WorkerSpawner(config, herdr_client=mock_client)

    # Escalate from luna -> gemini_flash
    result = await spawner.escalate_worker(current_pane_id="pane_old", current_tier_name="luna")
    assert result is not None
    new_pane_id, new_tier = result
    assert new_pane_id == "pane_456"
    assert new_tier.name == "gemini_flash"
    mock_client.send_interrupt.assert_awaited_once_with("pane_old")

    # Escalate from highest tier returns None
    result_none = await spawner.escalate_worker(current_pane_id="pane_456", current_tier_name="gemini_flash")
    assert result_none is None


def test_module_level_helpers(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "native"
      model: "google/gemini-2.5-flash"
""")
    config = load_config(str(cfg_file))
    spawner = WorkerSpawner(config, herdr_client=None)

    next_tier = get_next_tier("luna", spawner=spawner)
    assert next_tier is not None
    assert next_tier.name == "gemini_flash"
