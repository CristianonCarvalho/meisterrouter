import json
import os
from pathlib import Path

import pytest

from meister.config import MeisterConfig, WorkerTier
from meister.usage import (
    WorkerUsage,
    catalog_cost,
    finalize_usage,
    parse_agy_json,
    parse_claude_json,
    parse_copilot_text,
    parse_codex_text,
    parse_for_harness,
)


FIXTURES = Path(__file__).parent / "fixtures" / "usage"


def test_real_usage_fixtures():
    claude_text, claude = parse_claude_json((FIXTURES / "claude_json.out").read_text())
    assert claude_text == "OK"
    assert claude.cost_usd == 0.0026178
    assert claude.tokens_out == 94
    assert claude.cost_source == "reported"

    agy_text, agy = parse_agy_json((FIXTURES / "agy2_json.out").read_text())
    assert agy_text.strip() == "OK"
    assert agy.tokens_total == 30478
    assert agy.cost_source == "unknown"

    _, codex = parse_codex_text((FIXTURES / "codex.out").read_text())
    assert codex.tokens_total == 3884

    _, copilot = parse_copilot_text((FIXTURES / "copilot.out").read_text())
    assert copilot.credits == 0.15
    assert copilot.tokens_in == 11700
    assert copilot.tokens_out == 5
    assert copilot.approx is True


@pytest.mark.parametrize(
    ("parser", "raw"),
    [
        (parse_claude_json, ""),
        (parse_claude_json, '{"result":'),
        (parse_agy_json, '{"response":'),
        (parse_codex_text, "some output with no usage"),
        (parse_codex_text, "tokens used\n3,884\n\n"),
        (parse_copilot_text, "AI Credits nope\nTokens ↑ nope"),
    ],
)
def test_malformed_or_missing_usage_never_invents_cost(parser, raw):
    text, usage = parser(raw)
    assert text == raw
    assert usage.cost_source == "unknown"
    assert usage.cost_usd is None


def test_parsers_handle_noise_decimal_k_and_last_codex_occurrence():
    _, claude = parse_claude_json(
        'warning before json\n{"result":"OK","total_cost_usd":0.2,"usage":{"output_tokens":3}}\nrc=0'
    )
    assert claude.cost_usd == 0.2
    _, codex = parse_codex_text("tokens used\n1,111\ntokens used\n3,884\nrc=0")
    assert codex.tokens_total == 3884
    _, copilot = parse_copilot_text(
        "AI Credits 0.15\nTokens ↑ 11.7k (4.7k cached) • ↓ 5\n"
    )
    assert (copilot.tokens_in, copilot.cache_read_tokens, copilot.tokens_out) == (11700, 4700, 5)
    assert copilot.approx
    _, unknown = parse_for_harness("not-a-harness", "output")
    assert unknown.cost_source == "unknown"
    assert unknown.cost_usd is None


def _catalog_config():
    config = MeisterConfig()
    config.workers.tier_order = [
        WorkerTier(name="route", harness="agy", model="flash-id", cost_per_m_tokens=2.0),
    ]
    config.workers.disabled = [
        WorkerTier(name="disabled-route", harness="codex", model="codex-id", cost_per_m_tokens=4.0),
    ]
    return config


def test_catalog_estimates_only_configured_routes_and_finalizes_reported_usage():
    config = _catalog_config()
    assert catalog_cost("route", 1_000_000, 2_000_000, config) == 6.0
    assert catalog_cost("flash-id", 1_000_000, 0, config) == 2.0
    assert catalog_cost("disabled-route", 1_000_000, 0, config) == 4.0

    estimated = finalize_usage(WorkerUsage(tokens_total=100), "route", config)
    assert estimated.cost_source == "estimated"
    assert estimated.cost_usd == 0.0002
    missing = finalize_usage(WorkerUsage(tokens_total=100), "outside", config)
    assert missing.cost_source == "unknown"
    assert missing.cost_usd is None
    no_tokens = finalize_usage(WorkerUsage(), "route", config)
    assert no_tokens.cost_source == "unknown"
    assert no_tokens.cost_usd is None

    reported = finalize_usage(
        WorkerUsage(cost_usd=0.00001, cost_source="reported", tokens_in=1),
        "route",
        config,
    )
    assert reported.cost_usd == 0.00001
    assert reported.cost_source == "reported"


@pytest.mark.parametrize(
    ("route", "harness", "fixture", "response", "expected"),
    [
        ("claude-route", "claude", "claude_json.out", "OK", "reported"),
        ("agy-route", "agy", "agy2_json.out", "OK", "estimated"),
        ("codex-route", "codex", "codex.out", None, "estimated"),
        ("copilot-route", "copilot", "copilot.out", None, "estimated"),
    ],
)
def test_harness_worker_uses_fake_cli_and_emits_readable_output(
    tmp_path, monkeypatch, capsys, route, harness, fixture, response, expected
):
    from meister.worker import HarnessWorker

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli_name = "agy" if harness == "agy" else harness
    output_file = FIXTURES / fixture
    script = bin_dir / cli_name
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        f"sys.stdout.write(pathlib.Path({str(output_file)!r}).read_text())\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    config = _catalog_config()
    config.workers.tier_order = [
        WorkerTier(name=route, harness=harness, model=f"{route}-model", cost_per_m_tokens=1.0),
    ]
    worker = HarnessWorker(model=route, cwd=str(tmp_path), config=config)
    result = worker.run_task("test fake CLI")

    assert result["status"] == "done"
    assert result["usage"]["cost_source"] == expected
    assert result["usage"].get("cost") is not None
    if response is not None:
        assert result["output"].strip() == response
        assert '"total_cost_usd"' not in capsys.readouterr().out
    elif harness == "codex":
        assert result["output"] == output_file.read_text()
        assert result["usage"]["tokens_total"] == 3884
    else:
        assert result["usage"]["credits"] == 0.15
        assert result["usage"]["approx"] is True


def test_old_structured_cli_output_is_printed_raw_and_usage_unknown(tmp_path, monkeypatch, capsys):
    from meister.worker import HarnessWorker

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text("#!/bin/sh\nprintf 'old CLI response\\n'\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    config = _catalog_config()
    config.workers.tier_order = [
        WorkerTier(name="claude-route", harness="claude", model="claude-model", cost_per_m_tokens=1.0),
    ]
    result = HarnessWorker(model="claude-route", cwd=str(tmp_path), config=config).run_task("test")
    captured = capsys.readouterr().out
    assert "old CLI response" in captured
    assert result["output"] == "old CLI response\n"
    assert result["usage"]["cost_source"] == "unknown"
    assert "cost" not in result["usage"]


def test_usage_serialization_uses_bridge_keys():
    data = WorkerUsage(tokens_in=5, tokens_out=2, cost_usd=0.3, cost_source="reported").to_dict()
    assert data == {
        "tokens_in": 5,
        "tokens_out": 2,
        "cost": 0.3,
        "cost_source": "reported",
        "approx": False,
    }
    assert json.loads(json.dumps(data)) == data


def test_copilot_real_output_variants():
    """Saídas reais do Copilot 1.0.91: `written`, `cached, written`, sufixo `m` e `reasoning`."""
    _, written = parse_copilot_text((FIXTURES / "copilot_written.out").read_text())
    assert (written.credits, written.tokens_in, written.tokens_out) == (0.19, 14900, 42)
    assert written.thinking_tokens == 35
    assert written.cache_read_tokens is None
    assert written.approx is True

    _, long_run = parse_copilot_text((FIXTURES / "copilot_long.out").read_text())
    assert (long_run.credits, long_run.tokens_in, long_run.tokens_out) == (11.5, 7_300_000, 50_000)
    assert long_run.cache_read_tokens == 7_100_000
    assert long_run.thinking_tokens == 28_800

    # número exato (sem sufixo) não é aproximado; só a última linha de uso vale
    _, exact = parse_copilot_text(
        "Tokens     ↑ 100 (40 cached) • ↓ 7\nAI Credits 0.5 (1s)\nTokens     ↑ 1,200 • ↓ 9\n"
    )
    assert (exact.tokens_in, exact.tokens_out, exact.cache_read_tokens, exact.approx) == (1200, 9, None, False)
