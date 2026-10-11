from __future__ import annotations

import json

from meister.config import WorkerTier
from meister.timeline import build_timeline
from meister.timeline_graph import build_graph
from meister.timeline_json import timeline_to_dict
from tests.timeline_fixtures import at, ev, parallel_events, phase


def _data(events, tmp_path, *, run="r1", tiers=(), vias=None, now=60):
    timeline = build_timeline(events, run, at(now))
    graph = build_graph(timeline, vias or {}, at(now), events)
    return timeline_to_dict(
        timeline, graph, project=str(tmp_path), now=at(now), tiers=list(tiers), events=events
    )


def _lane(data, via):
    return next(lane for lane in data["lanes"] if lane["via"] == via)


def _row(data, task_id):
    return next(row for row in data["rows"] if row["task_id"] == task_id)


def _fallback_events():
    return [
        ev("orchestration_start", "orchestrator", 0, task="t"),
        ev("worker_spawn", "t", 1, tier="tier_1", harness="codex", model="luna", effort="medium"),
        phase("t", 5, "worker", 4),
        ev("quota_error", "t", 6, tier="tier_1", exit_code=429, error="x", matched_line="LINHA_X"),
        ev("worker_spawn", "t", 8, tier="tier_1b", harness="copilot", model="haiku"),
        phase("t", 15, "worker", 7),
        phase("t", 16, "gate", 1),
        phase("t", 17, "integrate", 1),
        ev("subtask_completed", "t", 17, tier="tier_1b"),
    ]


def test_lane_details_come_from_spawn_event_even_without_catalog(tmp_path):
    events = [
        ev("orchestration_start", "orchestrator", 0, task="t"),
        ev("worker_spawn", "t", 1, tier="tier_3b", harness="old", model="old-model"),
        ev("worker_retry", "t", 2, tier="tier_3b", reason="pane_lost"),
        ev("worker_spawn", "t", 3, tier="tier_3b", harness="claude", model="opus", effort="max"),
        ev("subtask_completed", "t", 9, tier="tier_3b"),
    ]

    lane = _lane(_data(events, tmp_path, tiers=[]), "tier_3b")

    assert (lane["harness"], lane["model"], lane["effort"]) == ("claude", "opus", "max")


def test_missing_spawn_field_falls_back_to_catalog(tmp_path):
    tiers = [WorkerTier(name="tier_1b", harness="copilot", model="cat-model", effort="low")]
    events = [
        ev("orchestration_start", "orchestrator", 0, task="t"),
        ev("worker_spawn", "t", 1, tier="tier_1b", model="event-model"),
        ev("subtask_completed", "t", 9, tier="tier_1b"),
    ]

    lane = _lane(_data(events, tmp_path, tiers=tiers), "tier_1b")

    assert (lane["harness"], lane["model"], lane["effort"]) == ("copilot", "event-model", "low")


def test_fallback_between_vias_lists_two_attempts_and_lanes(tmp_path):
    data = _data(_fallback_events(), tmp_path)

    row = _row(data, "t")
    assert row["attempts"] == 2
    assert [(item["via"], item["outcome"]) for item in row["attempt_details"]] == [
        ("tier_1", "quota"),
        ("tier_1b", "completed"),
    ]
    first = row["attempt_details"][0]
    assert first["detail"] == "LINHA_X"
    assert first["reason"]
    assert (first["start"], first["end"]) == ("2026-10-05T12:00:01Z", "2026-10-05T12:00:06Z")
    vias = {segment["via"] for segment in row["segments"] if segment["phase"] != "wait"}
    assert vias == {"tier_1", "tier_1b"}
    assert {lane["via"] for lane in data["lanes"]} == {"tier_1", "tier_1b"}
    assert _lane(data, "tier_1")["tasks"] == ["t"]
    assert _lane(data, "tier_1")["busy_s"] == 4.0
    assert _lane(data, "tier_1")["harness"] == "codex"
    json.dumps(data)


def test_old_log_keeps_todays_output_plus_null_new_fields(tmp_path):
    events = parallel_events()
    timeline = build_timeline(events, "r1", at(30))
    graph = build_graph(timeline, {"tier_1b": 0, "tier_2": 1}, at(30), events)
    tiers = [WorkerTier(name="tier_1b", harness="copilot", model="m")]

    old = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30), tiers=tiers)
    new = timeline_to_dict(
        timeline, graph, project=str(tmp_path), now=at(30), tiers=tiers, events=events
    )

    assert new == old
    for row in new["rows"]:
        assert row["reused_from"] is None
        assert row["reused_duration_s"] is None
        for item in row["attempt_details"]:
            assert item["detail"] is None
    assert _lane(new, "tier_1b")["harness"] == "copilot"


def test_reused_task_points_to_source_run_and_duration(tmp_path):
    events = [
        ev("orchestration_start", "orchestrator", 0, run="old", task="t"),
        ev("worker_spawn", "t", 1, run="old", tier="tier_1b"),
        ev("subtask_completed", "t", 41, run="old", tier="tier_1b"),
        ev("orchestration_start", "orchestrator", 100, run="new", task="t"),
        ev("subtask_reused", "t", 101, run="new", source_run_id="old", source_subtask_id="s1"),
        ev("subtask_reused", "u", 101, run="new", source_run_id="gone", duration_s=12.5),
        ev("subtask_reused", "v", 101, run="new", source_run_id="gone"),
    ]

    data = _data(events, tmp_path, run="new", now=200)

    assert _row(data, "t")["reused_from"] == "old"
    assert _row(data, "t")["reused_duration_s"] == 40.0
    assert _row(data, "u")["reused_duration_s"] == 12.5
    assert _row(data, "v")["reused_from"] == "gone"
    assert _row(data, "v")["reused_duration_s"] is None
