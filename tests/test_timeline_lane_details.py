from __future__ import annotations

import json

import meister.config as config_module
from meister.config import WorkerTier, load_config
from meister.timeline import build_timeline
from meister.timeline_graph import build_graph
from meister.timeline_json import lane_catalog_details, timeline_to_dict
from tests.timeline_fixtures import at, parallel_events


def _graph_for(vias: dict[str, int]):
    events = parallel_events()
    timeline = build_timeline(events, "r1", at(30))
    return timeline, build_graph(timeline, vias, at(30), events)


def _lane_by_via(data: dict, via: str) -> dict:
    return next(lane for lane in data["lanes"] if lane["via"] == via)


def test_catalog_via_maps_to_its_harness_and_model_with_null_effort(tmp_path):
    tiers = [WorkerTier(name="tier_1b", harness="copilot", model="claude-haiku-5.5")]
    timeline, graph = _graph_for({"tier_1b": 0})

    data = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30), tiers=tiers)

    lane = _lane_by_via(data, "tier_1b")
    assert lane["harness"] == "copilot"
    assert lane["model"] == "claude-haiku-5.5"
    assert lane["effort"] is None


def test_default_catalog_is_read_from_project_config(tmp_path):
    catalog = {tier.name: tier for tier in load_config().workers.tier_order}
    timeline, graph = _graph_for({"tier_1b": 0})

    data = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30))

    lane = _lane_by_via(data, "tier_1b")
    assert lane["harness"] == catalog["tier_1b"].harness == "copilot"
    assert lane["model"] == catalog["tier_1b"].model
    assert lane["effort"] == catalog["tier_1b"].effort


def test_configured_effort_is_exposed():
    tiers = [WorkerTier(name="tier_2", harness="agy", model="gemini", effort="high")]

    details = lane_catalog_details(["tier_2"], tiers)

    assert details == {"tier_2": {"harness": "agy", "model": "gemini", "effort": "high"}}


def test_unknown_via_has_null_details():
    tiers = [WorkerTier(name="tier_1b", harness="copilot", model="m")]

    details = lane_catalog_details(["ghost_via"], tiers)

    assert details == {"ghost_via": {"harness": None, "model": None, "effort": None}}


def test_empty_catalog_nulls_every_lane(tmp_path):
    timeline, graph = _graph_for({})

    data = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30), tiers=[])

    assert data["lanes"]
    for lane in data["lanes"]:
        assert lane["harness"] is None
        assert lane["model"] is None
        assert lane["effort"] is None


def test_broken_config_does_not_raise(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("configuração inválida")

    monkeypatch.setattr(config_module, "load_config", boom)
    timeline, graph = _graph_for({"tier_1b": 0})

    data = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30))

    assert _lane_by_via(data, "tier_1b")["harness"] is None
    json.dumps(data)


def test_existing_lane_keys_are_preserved(tmp_path):
    timeline, graph = _graph_for({"tier_1b": 0, "tier_2": 1})

    data = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30), tiers=[])

    assert data["lanes"]
    for lane in data["lanes"]:
        assert {"via", "tasks", "busy_s", "utilization", "harness", "model", "effort"} == set(lane)
