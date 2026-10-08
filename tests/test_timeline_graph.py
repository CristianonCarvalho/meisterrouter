from __future__ import annotations

import dataclasses
import json

import pytest

from meister.timeline import build_timeline
from meister.timeline_graph import (
    CostPoint,
    Edge,
    Escalation,
    Graph,
    Lane,
    build_graph,
)
from tests.timeline_fixtures import at, ev, parallel_events, phase


def test_two_lanes_three_tasks_with_dependencies() -> None:
    events = parallel_events()
    timeline = build_timeline(events, "r1", at(30))
    via_index = {"tier_1b": 0, "tier_2": 1}

    graph = build_graph(timeline, via_index, at(30), events=events)

    # Lanes
    assert len(graph.lanes) == 2
    assert graph.lanes[0].via == "tier_1b"
    assert graph.lanes[0].tasks == ["task_1", "task_3"]
    assert graph.lanes[1].via == "tier_2"
    assert graph.lanes[1].tasks == ["task_2"]
    assert graph.lanes[0].busy_s > 0.0
    assert 0.0 < graph.lanes[0].utilization <= 1.0
    assert graph.lanes[1].busy_s > 0.0
    assert 0.0 < graph.lanes[1].utilization <= 1.0

    # Lane catalog order respecting via_index
    reversed_index = {"tier_2": 0, "tier_1b": 1}
    rev_graph = build_graph(timeline, reversed_index, at(30), events=events)
    assert [lane.via for lane in rev_graph.lanes] == ["tier_2", "tier_1b"]

    # Edges
    assert len(graph.edges) == 2
    edge_map = {(edge.src, edge.dst): edge.critical for edge in graph.edges}
    assert ("task_1", "task_3") in edge_map
    assert ("task_2", "task_3") in edge_map
    assert edge_map[("task_1", "task_3")] is False
    assert edge_map[("task_2", "task_3")] is True

    # Critical path: task_2 (22s) + task_3 (6.5s) > task_1 (12s) + task_3 (6.5s)
    assert graph.critical_path == ["task_2", "task_3"]


def test_task_escalates_lane() -> None:
    events = [
        ev("orchestration_start", "orchestrator", 0, run="r_esc"),
        ev("plan_parsed", "orchestrator", 0.1, run="r_esc", total=1, task_ids=["t1"], task_titles={"t1": "Task 1"}),
        ev("worker_spawn", "t1", 1, run="r_esc", tier="via_a"),
        phase("t1", 5, "worker", 4, run="r_esc"),
        ev("worker_retry", "t1", 5.5, run="r_esc"),
        ev("worker_spawn", "t1", 6, run="r_esc", tier="via_b"),
        phase("t1", 10, "worker", 4, run="r_esc"),
        ev("subtask_completed", "t1", 10, run="r_esc", tier="via_b", cost=0.1, cost_source="reported"),
        ev("orchestration_end", "orchestrator", 10, run="r_esc", status="completed"),
    ]
    timeline = build_timeline(events, "r_esc", at(12))
    via_index = {"via_a": 0, "via_b": 1}

    graph = build_graph(timeline, via_index, at(12), events=events)

    # One escalation recorded
    assert len(graph.escalations) == 1
    assert graph.escalations[0] == Escalation(task_id="t1", from_via="via_a", to_via="via_b", at=at(6))

    # Both lanes appeared and both contain t1
    lane_names = [lane.via for lane in graph.lanes]
    assert lane_names == ["via_a", "via_b"]
    assert "t1" in graph.lanes[0].tasks
    assert "t1" in graph.lanes[1].tasks


def test_run_without_dependencies_critical_path_is_longest_task() -> None:
    events = [
        ev("orchestration_start", "orchestrator", 0, run="r_nodep"),
        ev("plan_parsed", "orchestrator", 0.1, run="r_nodep", total=3, task_ids=["t1", "t2", "t3"], task_titles={"t1": "T1", "t2": "T2", "t3": "T3"}),
        # t1: 5s duration
        ev("worker_spawn", "t1", 1, run="r_nodep", tier="via_a"),
        phase("t1", 6, "worker", 5, run="r_nodep"),
        ev("subtask_completed", "t1", 6, run="r_nodep", tier="via_a"),
        # t2: 15s duration (longest)
        ev("worker_spawn", "t2", 1, run="r_nodep", tier="via_a"),
        phase("t2", 16, "worker", 15, run="r_nodep"),
        ev("subtask_completed", "t2", 16, run="r_nodep", tier="via_a"),
        # t3: 8s duration
        ev("worker_spawn", "t3", 1, run="r_nodep", tier="via_a"),
        phase("t3", 9, "worker", 8, run="r_nodep"),
        ev("subtask_completed", "t3", 9, run="r_nodep", tier="via_a"),
        ev("orchestration_end", "orchestrator", 16, run="r_nodep", status="completed"),
    ]
    timeline = build_timeline(events, "r_nodep", at(20))
    via_index = {"via_a": 0}

    graph = build_graph(timeline, via_index, at(20), events=events)

    assert graph.edges == []
    assert graph.critical_path == ["t2"]


def test_empty_run_produces_empty_graph() -> None:
    timeline = build_timeline([], "empty_run", at(0))
    graph = build_graph(timeline, {}, at(0))

    assert graph.lanes == []
    assert graph.edges == []
    assert graph.escalations == []
    assert graph.critical_path == []
    assert graph.cost_series == []


def test_critical_path_tie_broken_by_task_id() -> None:
    # Two independent tasks with exact same duration: 10s
    events = [
        ev("orchestration_start", "orchestrator", 0, run="r_tie"),
        ev("plan_parsed", "orchestrator", 0.1, run="r_tie", total=2, task_ids=["task_b", "task_a"], task_titles={"task_b": "B", "task_a": "A"}),
        ev("worker_spawn", "task_b", 1, run="r_tie", tier="via_a"),
        phase("task_b", 11, "worker", 10, run="r_tie"),
        ev("subtask_completed", "task_b", 11, run="r_tie", tier="via_a"),
        ev("worker_spawn", "task_a", 1, run="r_tie", tier="via_a"),
        phase("task_a", 11, "worker", 10, run="r_tie"),
        ev("subtask_completed", "task_a", 11, run="r_tie", tier="via_a"),
        ev("orchestration_end", "orchestrator", 11, run="r_tie", status="completed"),
    ]
    timeline = build_timeline(events, "r_tie", at(15))
    via_index = {"via_a": 0}

    graph = build_graph(timeline, via_index, at(15), events=events)

    # Ties broken deterministically by task_id -> task_a < task_b
    assert graph.critical_path == ["task_a"]


def test_critical_path_tie_broken_by_task_id_with_dependencies() -> None:
    plan = [
        {"id": "task_a", "depends_on": []},
        {"id": "task_b", "depends_on": []},
        {"id": "task_c", "depends_on": ["task_a", "task_b"]},
    ]
    events = [
        ev("orchestration_start", "orchestrator", 0, run="r_dep_tie", task=json.dumps(plan)),
        ev("plan_parsed", "orchestrator", 0.1, run="r_dep_tie", total=3, task_ids=["task_a", "task_b", "task_c"], task_titles={}),
        ev("worker_spawn", "task_a", 1, run="r_dep_tie", tier="via_a"),
        phase("task_a", 6, "worker", 5, run="r_dep_tie"),
        ev("subtask_completed", "task_a", 6, run="r_dep_tie", tier="via_a"),
        ev("worker_spawn", "task_b", 1, run="r_dep_tie", tier="via_a"),
        phase("task_b", 6, "worker", 5, run="r_dep_tie"),
        ev("subtask_completed", "task_b", 6, run="r_dep_tie", tier="via_a"),
        ev("worker_spawn", "task_c", 6.1, run="r_dep_tie", tier="via_a"),
        phase("task_c", 11.1, "worker", 5, run="r_dep_tie"),
        ev("subtask_completed", "task_c", 11.1, run="r_dep_tie", tier="via_a"),
        ev("orchestration_end", "orchestrator", 12, run="r_dep_tie", status="completed"),
    ]
    timeline = build_timeline(events, "r_dep_tie", at(15))
    graph = build_graph(timeline, {"via_a": 0}, at(15), events=events)

    # Both chains [task_a, task_c] and [task_b, task_c] have duration 5s + 5s = 10s.
    # Deterministic tie-break chooses ["task_a", "task_c"]
    assert graph.critical_path == ["task_a", "task_c"]
    assert [edge for edge in graph.edges if edge.critical] == [Edge("task_a", "task_c", critical=True)]


def test_cost_series_monotonic_and_ends_at_summary_cost() -> None:
    events = parallel_events()
    timeline = build_timeline(events, "r1", at(30))
    via_index = {"tier_1b": 0, "tier_2": 1}

    graph = build_graph(timeline, via_index, at(30), events=events)

    assert timeline.summary.cost_usd is not None
    assert timeline.summary.cost_usd > 0.0
    assert len(graph.cost_series) > 0

    # Non-decreasing in time and actual_usd
    for i in range(len(graph.cost_series) - 1):
        assert graph.cost_series[i].t <= graph.cost_series[i + 1].t
        assert graph.cost_series[i].actual_usd <= graph.cost_series[i + 1].actual_usd

    # Ends at Summary.cost_usd
    assert graph.cost_series[-1].actual_usd == timeline.summary.cost_usd


def test_dataclasses_are_immutable() -> None:
    lane = Lane(via="via_a", tasks=["t1"], busy_s=5.0, utilization=0.5)
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        lane.busy_s = 10.0  # type: ignore[misc]

    edge = Edge(src="t1", dst="t2", critical=True)
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        edge.critical = False  # type: ignore[misc]

    esc = Escalation(task_id="t1", from_via="a", to_via="b", at=at(5))
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        esc.to_via = "c"  # type: ignore[misc]

    cp = CostPoint(t=at(5), actual_usd=1.0, baseline_usd=2.0)
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        cp.actual_usd = 2.0  # type: ignore[misc]

    graph = Graph(lanes=[lane], edges=[edge], escalations=[esc], critical_path=["t1"], cost_series=[cp])
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        graph.critical_path = []  # type: ignore[misc]


def test_build_graph_without_events() -> None:
    events = parallel_events()
    timeline = build_timeline(events, "r1", at(30))
    via_index = {"tier_1b": 0, "tier_2": 1}

    # Calling purely with (timeline, via_index, now)
    graph = build_graph(timeline, via_index, at(30))

    assert len(graph.lanes) == 2
    assert len(graph.edges) == 2
    assert graph.critical_path == ["task_2", "task_3"]
    assert len(graph.cost_series) > 0
    assert graph.cost_series[-1].actual_usd == timeline.summary.cost_usd
