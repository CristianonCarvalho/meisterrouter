import json
from datetime import timedelta

from meister.timeline import build_timeline
from meister.timeline_demo import RUN_ID, build_scenario
from tests.timeline_fixtures import BASE


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
