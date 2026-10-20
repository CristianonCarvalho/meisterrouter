"""Testes de leitura de configuração por projeto na timeline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meister.config import MeisterConfig
from meister.timeline import DEFAULT_STALE_AFTER
from meister.timeline_cli import (
    _tiers,
    price_tables_from_config,
    stale_after_from_config,
    via_index_from_config,
)
from meister.timeline_json import (
    _catalog_tiers,
    timeline_json_for_log,
    timeline_to_dict,
)
from tests.timeline_fixtures import at, parallel_events


def _write_config(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _write_log(log_path: Path, events: list[dict]) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return str(log_path)


def test_two_projects_with_different_configs_return_different_tables(tmp_path: Path) -> None:
    proj_a = tmp_path / "project_a"
    proj_a.mkdir()
    _write_config(
        proj_a / "meister.config.yaml",
        """
version: "2.0"
workers:
  tier_order:
    - name: tier_alpha
      harness: codex
      model: gpt-6-luna
      cost_per_m_tokens: 0.25
      credit_usd: 0.05
      max_runtime_seconds: 600
""",
    )

    proj_b = tmp_path / "project_b"
    proj_b.mkdir()
    _write_config(
        proj_b / "meister.config.yaml",
        """
version: "2.0"
workers:
  tier_order:
    - name: tier_beta
      harness: claude
      model: sonnet
      cost_per_m_tokens: 3.5
      credit_usd: 0.50
      max_runtime_seconds: 1800
""",
    )

    prices_a, credits_a = price_tables_from_config(str(proj_a))
    prices_b, credits_b = price_tables_from_config(str(proj_b))

    assert prices_a == {"tier_alpha": 0.25}
    assert credits_a == {"tier_alpha": 0.05}
    assert prices_b == {"tier_beta": 3.5}
    assert credits_b == {"tier_beta": 0.50}
    assert prices_a != prices_b
    assert credits_a != credits_b

    stale_a = stale_after_from_config(str(proj_a))
    stale_b = stale_after_from_config(str(proj_b))

    assert stale_a == timedelta(seconds=600 + 300)
    assert stale_b == timedelta(seconds=1800 + 300)
    assert stale_a != stale_b

    vias_a = via_index_from_config(str(proj_a))
    vias_b = via_index_from_config(str(proj_b))
    assert vias_a == {"tier_alpha": 0}
    assert vias_b == {"tier_beta": 0}
    assert vias_a != vias_b

    tiers_a = _catalog_tiers(str(proj_a))
    tiers_b = _catalog_tiers(str(proj_b))
    assert [t.name for t in tiers_a] == ["tier_alpha"]
    assert [t.name for t in tiers_b] == ["tier_beta"]

    # Leitura pura: nenhum arquivo ou pasta novo deve ter sido criado em proj_a ou proj_b
    assert sorted(p.name for p in proj_a.iterdir()) == ["meister.config.yaml"]
    assert sorted(p.name for p in proj_b.iterdir()) == ["meister.config.yaml"]


def test_project_without_config_returns_defaults_not_cwd_config(tmp_path: Path) -> None:
    empty_proj = tmp_path / "empty_project"
    empty_proj.mkdir()

    default_cfg = MeisterConfig()
    expected_names = [tier.name for tier in default_cfg.workers.tier_order]

    catalog = _catalog_tiers(str(empty_proj))
    assert [t.name for t in catalog] == expected_names

    all_tiers = _tiers(str(empty_proj))
    expected_all_names = [
        tier.name for tier in [*default_cfg.workers.tier_order, *default_cfg.workers.disabled]
    ]
    assert [t.name for t in all_tiers] == expected_all_names

    prices, credits_map = price_tables_from_config(str(empty_proj))
    expected_prices = {tier.name: tier.cost_per_m_tokens for tier in all_tiers}
    expected_credits = {
        tier.name: tier.credit_usd for tier in all_tiers if tier.credit_usd is not None
    }
    assert prices == expected_prices
    assert credits_map == expected_credits

    # Nenhum arquivo criado no diretório vazio
    assert list(empty_proj.iterdir()) == []


def test_readers_without_argument_behave_identically_to_none() -> None:
    assert _tiers() == _tiers(None)
    assert via_index_from_config() == via_index_from_config(None)
    assert price_tables_from_config() == price_tables_from_config(None)
    assert stale_after_from_config() == stale_after_from_config(None)
    assert [t.name for t in _catalog_tiers()] == [t.name for t in _catalog_tiers(None)]


def test_project_with_yml_extension(tmp_path: Path) -> None:
    proj = tmp_path / "project_yml"
    proj.mkdir()
    _write_config(
        proj / "meister.config.yml",
        """
version: "2.0"
workers:
  tier_order:
    - name: tier_from_yml
      harness: codex
      model: gpt-6-luna
      cost_per_m_tokens: 1.23
""",
    )

    prices, _ = price_tables_from_config(str(proj))
    assert prices == {"tier_from_yml": 1.23}
    catalog = _catalog_tiers(str(proj))
    assert [t.name for t in catalog] == ["tier_from_yml"]


def test_broken_config_falls_back_gracefully(tmp_path: Path) -> None:
    broken = tmp_path / "broken_project"
    broken.mkdir()
    _write_config(broken / "meister.config.yaml", ": : this is invalid yaml : :")

    assert _catalog_tiers(str(broken)) == []
    assert via_index_from_config(str(broken)) == {}
    assert price_tables_from_config(str(broken)) == ({}, {})
    assert stale_after_from_config(str(broken)) == DEFAULT_STALE_AFTER


def test_timeline_json_for_log_with_project_path(tmp_path: Path) -> None:
    proj = tmp_path / "my_project"
    proj.mkdir()
    _write_config(
        proj / "meister.config.yaml",
        """
version: "2.0"
workers:
  tier_order:
    - name: tier_1b
      harness: custom_harness
      model: custom_model
      cost_per_m_tokens: 99.0
      max_runtime_seconds: 400
""",
    )

    log_file = _write_log(proj / ".meister" / "logs" / "orchestration_log.jsonl", parallel_events())

    data = timeline_json_for_log(
        log_file,
        run_id=None,
        now=at(30),
        project_path=str(proj),
    )

    assert data["project"]["name"] == "my_project"
    assert data["run"]["id"] == "r1"

    # Lanes must have resolved harness and model from the project config
    lane_1b = next((lane for lane in data["lanes"] if lane["via"] == "tier_1b"), None)
    assert lane_1b is not None
    assert lane_1b["harness"] == "custom_harness"
    assert lane_1b["model"] == "custom_model"

    # Verificar que o projeto não foi alterado na leitura (leitura pura)
    assert (proj / ".meister" / "logs" / "orchestration_log.jsonl").is_file()
    assert sorted(p.name for p in proj.iterdir()) == [".meister", "meister.config.yaml"]


def test_timeline_to_dict_respects_project_path_catalog(tmp_path: Path) -> None:
    from meister.timeline import build_timeline
    from meister.timeline_graph import build_graph

    proj = tmp_path / "catalog_proj"
    proj.mkdir()
    _write_config(
        proj / "meister.config.yaml",
        """
version: "2.0"
workers:
  tier_order:
    - name: tier_1b
      harness: kiro-cli
      model: kiro-special
      cost_per_m_tokens: 5.0
""",
    )

    events = parallel_events()
    timeline = build_timeline(events, "r1", at(30))
    graph = build_graph(timeline, {"tier_1b": 0}, at(30), events)

    data = timeline_to_dict(
        timeline,
        graph,
        project=str(proj),
        now=at(30),
        project_path=str(proj),
    )

    lane = next((target_lane for target_lane in data["lanes"] if target_lane["via"] == "tier_1b"), None)
    assert lane is not None
    assert lane["harness"] == "kiro-cli"
    assert lane["model"] == "kiro-special"


def test_cwd_with_custom_config_does_not_leak_into_empty_project(tmp_path: Path, monkeypatch) -> None:
    cwd_dir = tmp_path / "cwd_dir"
    cwd_dir.mkdir()
    _write_config(
        cwd_dir / "meister.config.yaml",
        """
version: "2.0"
workers:
  tier_order:
    - name: cwd_only_via
      harness: copilot
      model: test-model
      cost_per_m_tokens: 77.0
""",
    )
    monkeypatch.chdir(cwd_dir)

    # In cwd_dir, the cwd config has cwd_only_via
    assert [t.name for t in _catalog_tiers()] == ["cwd_only_via"]

    # In empty project, it must NOT use cwd_dir's config; it must return default MeisterConfig tiers
    empty_proj = tmp_path / "empty_dir"
    empty_proj.mkdir()

    empty_catalog = _catalog_tiers(str(empty_proj))
    assert "cwd_only_via" not in [t.name for t in empty_catalog]
    default_names = [t.name for t in MeisterConfig().workers.tier_order]
    assert [t.name for t in empty_catalog] == default_names

    prices, _ = price_tables_from_config(str(empty_proj))
    assert "cwd_only_via" not in prices


def test_timeline_json_for_log_missing_log_with_project_path(tmp_path: Path) -> None:
    proj = tmp_path / "another_project"
    proj.mkdir()
    missing_log = str(proj / ".meister" / "logs" / "non_existent.jsonl")

    data = timeline_json_for_log(
        missing_log,
        run_id=None,
        now=datetime.now(timezone.utc),
        project_path=str(proj),
    )

    assert data["project"]["name"] == "another_project"
    assert data["run"] is None
    assert data["lanes"] == []
    assert data["rows"] == []
