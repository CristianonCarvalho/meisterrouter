import json
from datetime import datetime, timedelta, timezone

import pytest

from meister import timeline_cli, timeline_json
from meister.config import WorkerTier
from meister.timeline import build_timeline
from meister.timeline_demo import RUN_ID, build_scenario, materialize
from meister.timeline_graph import build_graph
from meister.timeline_json import timeline_json_for_log
from tests.timeline_fixtures import BASE

NOW = datetime(2026, 10, 10, 11, 22, 48, tzinfo=timezone.utc)


def _task_events(scenario, task_id):
    return [item for item in scenario if item.event.get("task_id") == task_id]


def _kinds(scenario, task_id):
    return [item.event["event_type"] for item in _task_events(scenario, task_id)]


def _depends_on(scenario, task_id):
    plan = next(item.event for item in scenario if item.event["event_type"] == "orchestration_start")
    entries = json.loads(plan["task"])
    return next(entry["depends_on"] for entry in entries if entry["id"] == task_id)


def _task_ids(scenario):
    return sorted({item.event["task_id"] for item in scenario if item.event["task_id"] != "orchestrator"})


def _first_offset(scenario, task_id, kind):
    return min(item.offset_s for item in _task_events(scenario, task_id) if item.event["event_type"] == kind)


def test_scenario_is_sorted_by_offset():
    scenario = build_scenario()
    offsets = [item.offset_s for item in scenario]
    assert offsets == sorted(offsets)
    assert all(item.offset_s >= 0 for item in scenario)


def test_scenario_events_have_no_ts_and_use_log_format():
    for item in build_scenario():
        assert "ts" not in item.event
        assert item.event["event_type"]
        assert item.event["task_id"]


def test_scenario_has_single_fixed_run_id():
    scenario = build_scenario()
    assert RUN_ID == "demo_timeline"
    assert {item.event["run_id"] for item in scenario} == {"demo_timeline"}


def test_scenario_has_about_twelve_tasks_and_forty_minutes():
    scenario = build_scenario()
    assert 11 <= len(_task_ids(scenario)) <= 13
    last = max(item.offset_s for item in scenario)
    assert 35 * 60 <= last <= 45 * 60


def test_scenario_uses_default_tiers_and_legacy_lane():
    tiers = {item.event.get("tier") for item in build_scenario()}
    assert {"tier_1", "tier_1b", "tier_2", "tier_3", "legacy_lane"} <= tiers


def test_parallel_tasks_without_dependency_overlap_in_tier_1():
    scenario = build_scenario()
    parallel = [
        task_id
        for task_id in _task_ids(scenario)
        if _depends_on(scenario, task_id) == [] and "subtask_completed" in _kinds(scenario, task_id)
        and any(item.event.get("tier") == "tier_1" for item in _task_events(scenario, task_id))
    ]
    assert len(parallel) >= 2
    first, second = parallel[0], parallel[1]
    assert _first_offset(scenario, second, "worker_spawn") < _first_offset(scenario, first, "subtask_completed")
    assert _first_offset(scenario, first, "worker_spawn") < _first_offset(scenario, second, "subtask_completed")


def test_fan_in_task_depends_on_two_parallel_tasks():
    scenario = build_scenario()
    fan_in = [task_id for task_id in _task_ids(scenario) if len(_depends_on(scenario, task_id)) == 2]
    assert len(fan_in) == 1
    assert len(_depends_on(scenario, fan_in[0])) == 2


def test_fan_out_three_tasks_depend_on_fan_in():
    scenario = build_scenario()
    fan_in = next(task_id for task_id in _task_ids(scenario) if len(_depends_on(scenario, task_id)) == 2)
    dependents = [task_id for task_id in _task_ids(scenario) if fan_in in _depends_on(scenario, task_id)]
    assert len(dependents) == 3


def test_retry_after_lost_pane_then_completes():
    scenario = build_scenario()
    retried = [
        task_id
        for task_id in _task_ids(scenario)
        if any(
            item.event["event_type"] == "worker_retry" and item.event.get("reason") == "pane_lost"
            for item in _task_events(scenario, task_id)
        )
    ]
    assert len(retried) == 1
    assert "subtask_completed" in _kinds(scenario, retried[0])


def test_gate_repair_with_failed_tests_then_completes():
    scenario = build_scenario()
    repaired = [
        task_id
        for task_id in _task_ids(scenario)
        if any(
            item.event["event_type"] == "gate_repair" and item.event.get("failed_tests")
            for item in _task_events(scenario, task_id)
        )
    ]
    assert len(repaired) == 1
    assert "subtask_completed" in _kinds(scenario, repaired[0])


def test_escalation_tier_1_to_2_to_3_with_idle_timeouts():
    scenario = build_scenario()
    escalated = [
        task_id
        for task_id in _task_ids(scenario)
        if any(
            item.event["event_type"] == "worker_timeout" and item.event.get("kind") == "idle"
            for item in _task_events(scenario, task_id)
        )
    ]
    assert len(escalated) == 1
    events = _task_events(scenario, escalated[0])
    spawn_tiers = [item.event["tier"] for item in events if item.event["event_type"] == "worker_spawn"]
    assert spawn_tiers == ["tier_1", "tier_2", "tier_3"]
    assert [item.event["tier"] for item in events if item.event["event_type"] == "subtask_completed"] == ["tier_3"]


def test_failing_task_is_rejected_by_gate_with_failed_tests():
    scenario = build_scenario()
    rejected = [
        item for item in scenario
        if item.event["event_type"] == "subtask_rejected" and item.event.get("reason") == "gate"
    ]
    assert len(rejected) == 1
    task_id = rejected[0].event["task_id"]
    assert rejected[0].event.get("failed_tests")
    assert "subtask_completed" not in _kinds(scenario, task_id)


def test_interrupted_attempt_resumes_same_run_and_completes():
    scenario = build_scenario()
    resume = next(item.offset_s for item in scenario if item.event["event_type"] == "resume_same_run")
    interrupted = [
        task_id
        for task_id in _task_ids(scenario)
        if _first_offset(scenario, task_id, "worker_spawn") < resume
        and _kinds(scenario, task_id).count("worker_spawn") == 2
        and "subtask_completed" in _kinds(scenario, task_id)
        and "worker_retry" not in _kinds(scenario, task_id)
        and "worker_end" not in _kinds(scenario, task_id)
    ]
    assert len(interrupted) == 1
    task_id = interrupted[0]
    spawns = sorted(
        item.offset_s for item in _task_events(scenario, task_id) if item.event["event_type"] == "worker_spawn"
    )
    assert len(spawns) == 2
    assert spawns[0] < resume < spawns[1]


def test_legacy_lane_task_completes():
    scenario = build_scenario()
    legacy = [
        item for item in scenario
        if item.event["event_type"] == "subtask_completed" and item.event.get("tier") == "legacy_lane"
    ]
    assert len(legacy) == 1


def test_one_task_is_still_running_at_end():
    scenario = build_scenario()
    running = [
        task_id
        for task_id in _task_ids(scenario)
        if "worker_spawn" in _kinds(scenario, task_id)
        and not {"subtask_completed", "subtask_rejected", "worker_error", "worker_timeout"}
        & set(_kinds(scenario, task_id))
    ]
    assert len(running) == 1


def test_completed_subtasks_carry_cost_and_cost_source():
    completed = [item for item in build_scenario() if item.event["event_type"] == "subtask_completed"]
    assert len(completed) >= 8
    for item in completed:
        assert isinstance(item.event["cost"], float)
        assert item.event["cost_source"]


def test_scenario_builds_a_valid_timeline_with_expected_statuses():
    scenario = build_scenario()
    events = []
    for item in scenario:
        stamped = dict(item.event)
        stamped["ts"] = (BASE + timedelta(seconds=item.offset_s)).isoformat()
        events.append(stamped)
    now = BASE + timedelta(seconds=max(item.offset_s for item in scenario))
    timeline = build_timeline(events, RUN_ID, now)
    statuses = [row.status for row in timeline.rows]
    assert len(timeline.rows) == len(_task_ids(scenario))
    assert statuses.count("running") == 1
    assert statuses.count("failed") == 1
    assert timeline.summary.peak_parallel >= 2


def _parse(stamp):
    return datetime.fromisoformat(stamp)


def _static_events():
    return materialize(build_scenario(), "static", NOW)


def test_live_ts_is_non_decreasing_and_offsets_are_divided_by_speed():
    scenario = build_scenario()
    events = materialize(scenario, "live", NOW, speed=4.0)
    stamps = [_parse(event["ts"]) for event in events]
    assert stamps == sorted(stamps)
    for item, event in zip(scenario, events):
        assert _parse(event["ts"]) == NOW + timedelta(seconds=item.offset_s / 4.0)


def test_live_divides_worker_phase_duration_ms_by_speed():
    scenario = build_scenario()
    events = materialize(scenario, "live", NOW, speed=4.0)
    phases = [(item, event) for item, event in zip(scenario, events) if event["event_type"] == "worker_phase"]
    assert phases
    for item, event in phases:
        assert event["duration_ms"] == pytest.approx(item.event["duration_ms"] / 4.0)


def test_live_speed_one_keeps_original_duration_ms():
    scenario = build_scenario()
    events = materialize(scenario, "live", NOW)
    for item, event in zip(scenario, events):
        if event["event_type"] == "worker_phase":
            assert event["duration_ms"] == item.event["duration_ms"]


def test_static_has_no_future_ts_and_ends_a_few_seconds_before_now():
    stamps = [_parse(event["ts"]) for event in _static_events()]
    assert max(stamps) <= NOW
    assert timedelta(0) < NOW - max(stamps) <= timedelta(seconds=10)


def test_static_keeps_original_durations_and_offsets_in_real_time():
    scenario = build_scenario()
    events = _static_events()
    origin = _parse(events[0]["ts"]) - timedelta(seconds=scenario[0].offset_s)
    for item, event in zip(scenario, events):
        assert event.get("duration_ms") == item.event.get("duration_ms")
        assert _parse(event["ts"]) - origin == timedelta(seconds=item.offset_s)


def test_materialize_rejects_unknown_mode_and_non_positive_speed():
    with pytest.raises(ValueError):
        materialize(build_scenario(), "replay", NOW)
    with pytest.raises(ValueError):
        materialize(build_scenario(), "live", NOW, speed=0)


def _static_timeline():
    events = _static_events()
    return build_timeline(events, RUN_ID, NOW), events


def test_static_timeline_has_all_tasks_and_expected_statuses():
    timeline, _ = _static_timeline()
    statuses = {row.task_id: row.status for row in timeline.rows}
    assert len(timeline.rows) == 12
    assert statuses["task_07"] == "failed"
    assert statuses["task_10"] == "running"
    assert [task for task, status in statuses.items() if status == "running"] == ["task_10"]
    assert all(status == "completed" for task, status in statuses.items() if task not in {"task_07", "task_10"})


def test_static_timeline_shows_retry_and_escalation_attempts():
    timeline, _ = _static_timeline()
    rows = {row.task_id: row for row in timeline.rows}
    assert rows["task_04"].attempts == 2
    assert rows["task_06"].attempts == 3
    assert rows["task_06"].tier == "tier_3"


def test_static_timeline_interrupted_task_has_no_inferred_segments():
    timeline, _ = _static_timeline()
    rows = {row.task_id: row for row in timeline.rows}
    assert rows["task_08"].attempts == 2
    assert not any(segment.inferred for segment in rows["task_08"].segments)


def test_static_timeline_has_legacy_lane_and_fan_in_fan_out_edges():
    timeline, events = _static_timeline()
    rows = {row.task_id: row for row in timeline.rows}
    assert rows["task_09"].tier == "legacy_lane"
    graph = build_graph(timeline, {}, NOW, events=events)
    assert "legacy_lane" in {lane.via for lane in graph.lanes}
    assert sorted(edge.dst for edge in graph.edges if edge.src == "task_03") == ["task_04", "task_05", "task_06"]
    assert sorted(edge.src for edge in graph.edges if edge.dst == "task_03") == ["task_01", "task_02"]


def test_timeline_json_for_log_reports_harness_and_model_per_lane(tmp_path, monkeypatch):
    log = tmp_path / "orchestration_log.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in _static_events()), encoding="utf-8")
    tiers = [WorkerTier(name="tier_1", harness="codex", model="luna-model")]
    monkeypatch.setattr(timeline_cli, "_tiers", lambda: tiers)
    monkeypatch.setattr(timeline_json, "_catalog_tiers", lambda: tiers)

    data = timeline_json_for_log(str(log), run_id=None, now=NOW)

    lanes = {lane["via"]: lane for lane in data["lanes"]}
    assert lanes["tier_1"]["harness"] == "codex"
    assert lanes["tier_1"]["model"] == "luna-model"
    assert lanes["legacy_lane"]["harness"] is None
    assert lanes["legacy_lane"]["model"] is None
