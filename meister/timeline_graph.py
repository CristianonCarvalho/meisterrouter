"""Pure graph model derived from Timeline: lanes, edges, critical path, escalations, cost series."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from meister.dashboard.metrics import _event_time, _timestamp, event_type
from meister.timeline import TaskRow, Timeline

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Lane:
    via: str
    tasks: list[str]
    busy_s: float
    utilization: float


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    critical: bool


@dataclass(frozen=True)
class Escalation:
    task_id: str
    from_via: str
    to_via: str
    at: datetime


@dataclass(frozen=True)
class CostPoint:
    t: datetime
    actual_usd: float
    baseline_usd: Optional[float] = None


@dataclass(frozen=True)
class Graph:
    lanes: list[Lane]
    edges: list[Edge]
    escalations: list[Escalation]
    critical_path: list[str]
    cost_series: list[CostPoint]


def _ts(event: Dict[str, Any]) -> Optional[datetime]:
    return _timestamp(_event_time(event))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _union_duration(intervals: List[Tuple[datetime, datetime]]) -> float:
    if not intervals:
        return 0.0
    sorted_spans = sorted(
        ((start.timestamp(), end.timestamp()) for start, end in intervals if end >= start)
    )
    if not sorted_spans:
        return 0.0
    union = 0.0
    cur_start, cur_end = sorted_spans[0]
    for nxt_start, nxt_end in sorted_spans[1:]:
        if nxt_start > cur_end:
            union += cur_end - cur_start
            cur_start, cur_end = nxt_start, nxt_end
        else:
            cur_end = max(cur_end, nxt_end)
    return round(union + (cur_end - cur_start), 3)


def _task_duration(row: TaskRow, now_eff: datetime) -> float:
    if row.duration_s is not None:
        return row.duration_s
    if row.start is not None:
        return max(0.0, (now_eff - row.start).total_seconds())
    return 0.0


def _build_critical_path(
    rows: List[TaskRow], now_eff: datetime
) -> Tuple[List[str], Set[Tuple[str, str]]]:
    if not rows:
        return [], set()

    durations = {row.task_id: _task_duration(row, now_eff) for row in rows}
    preds: Dict[str, List[str]] = {
        row.task_id: [dep for dep in row.depends_on if dep in durations]
        for row in rows
    }

    memo: Dict[str, Tuple[float, List[str]]] = {}
    visiting: Set[str] = set()

    def get_best_path(task_id: str) -> Tuple[float, List[str]]:
        if task_id in memo:
            return memo[task_id]
        if task_id in visiting:
            return (durations.get(task_id, 0.0), [task_id])
        visiting.add(task_id)

        task_dur = durations.get(task_id, 0.0)
        task_preds = preds.get(task_id, [])

        best_pred_dur = -1.0
        best_pred_path: List[str] = []

        for pred_id in task_preds:
            pred_dur, pred_path = get_best_path(pred_id)
            if pred_dur > best_pred_dur:
                best_pred_dur = pred_dur
                best_pred_path = pred_path
            elif abs(pred_dur - best_pred_dur) < 1e-9:
                if pred_path < best_pred_path:
                    best_pred_path = pred_path

        visiting.remove(task_id)
        if best_pred_dur >= 0.0:
            res = (round(best_pred_dur + task_dur, 6), best_pred_path + [task_id])
        else:
            res = (round(task_dur, 6), [task_id])
        memo[task_id] = res
        return res

    best_overall_dur = -1.0
    best_overall_path: List[str] = []

    for row in rows:
        path_dur, path = get_best_path(row.task_id)
        if path_dur > best_overall_dur:
            best_overall_dur = path_dur
            best_overall_path = path
        elif abs(path_dur - best_overall_dur) < 1e-9:
            if path < best_overall_path:
                best_overall_path = path

    crit_edges: Set[Tuple[str, str]] = set()
    if len(best_overall_path) > 1:
        for u, v in zip(best_overall_path[:-1], best_overall_path[1:]):
            crit_edges.add((u, v))

    return best_overall_path, crit_edges


def _build_cost_series(
    timeline: Timeline,
    run_events: List[Dict[str, Any]],
    now_eff: datetime,
) -> List[CostPoint]:
    target_cost = timeline.summary.cost_usd
    if target_cost is None or target_cost <= 0.0:
        return []

    points: List[CostPoint] = []
    if timeline.started_at is not None:
        points.append(CostPoint(t=timeline.started_at, actual_usd=0.0))

    completion_events: List[Tuple[datetime, float]] = []
    for event in run_events:
        if event_type(event) != "subtask_completed":
            continue
        ts = _ts(event)
        if ts is None:
            continue
        cost_val = _number(
            event.get("cost") if event.get("cost") is not None else event.get("cost_usd")
        )
        if cost_val > 0.0:
            completion_events.append((ts, cost_val))

    completion_events.sort(key=lambda item: item[0])

    running_cost = 0.0
    by_timestamp: Dict[datetime, float] = {}
    if points:
        by_timestamp[points[0].t] = 0.0

    for ts, cost_val in completion_events:
        running_cost += cost_val
        by_timestamp[ts] = round(running_cost, 6)

    # Finalize series with target_cost at end if not already reached
    if not by_timestamp or max(by_timestamp.values(), default=0.0) < target_cost:
        end_t = timeline.ended_at or now_eff
        by_timestamp[end_t] = target_cost
    else:
        # Ensure the final point matches target_cost exactly
        latest_ts = max(by_timestamp.keys())
        by_timestamp[latest_ts] = target_cost

    series = [CostPoint(t=ts, actual_usd=cost) for ts, cost in sorted(by_timestamp.items())]

    # Ensure monotonic non-decreasing
    monotonic_series: List[CostPoint] = []
    current_max = 0.0
    for pt in series:
        if pt.actual_usd < current_max:
            monotonic_series.append(CostPoint(t=pt.t, actual_usd=current_max, baseline_usd=pt.baseline_usd))
        else:
            monotonic_series.append(pt)
            current_max = pt.actual_usd

    if monotonic_series:
        last = monotonic_series[-1]
        if last.actual_usd != target_cost:
            monotonic_series[-1] = CostPoint(t=last.t, actual_usd=target_cost, baseline_usd=last.baseline_usd)

    return monotonic_series


def build_graph(
    timeline: Timeline,
    via_index: Mapping[str, int] | Dict[str, int],
    now: datetime,
    events: Optional[Iterable[Dict[str, Any]]] = None,
) -> Graph:
    """Derive lanes, edges, critical path, escalations, and cost series from a Timeline."""
    now_eff = timeline.ended_at or timeline.stalled_since or now
    run_events = (
        [e for e in events if str(e.get("run_id") or "") == timeline.run_id]
        if events is not None
        else []
    )
    run_events.sort(key=lambda e: _ts(e) or _EPOCH)

    # 1. Parse spawns and escalations per task
    spawns_by_task: Dict[str, List[Tuple[str, datetime]]] = defaultdict(list)
    for event in run_events:
        kind = event_type(event)
        if kind in ("worker_spawn", "worker_start"):
            task_id = str(event.get("task_id") or "")
            tier = event.get("tier")
            ts = _ts(event)
            if task_id and tier and str(tier) != "unknown" and ts is not None:
                spawns_by_task[task_id].append((str(tier), ts))

    escalations: List[Escalation] = []
    for task_id, spawns in spawns_by_task.items():
        for i in range(1, len(spawns)):
            prev_via, _ = spawns[i - 1]
            curr_via, at_t = spawns[i]
            if curr_via != prev_via:
                escalations.append(
                    Escalation(task_id=task_id, from_via=prev_via, to_via=curr_via, at=at_t)
                )
    escalations.sort(key=lambda esc: (esc.at, esc.task_id))

    # 2. Identify all vias that appeared in this run
    task_vias: Dict[str, Set[str]] = defaultdict(set)
    escalated_task_ids = {esc.task_id for esc in escalations}

    for row in timeline.rows:
        if row.task_id in escalated_task_ids and row.task_id in spawns_by_task:
            for via_name, _ in spawns_by_task[row.task_id]:
                task_vias[row.task_id].add(via_name)
        elif row.tier and row.tier != "unknown":
            task_vias[row.task_id].add(row.tier)
        elif row.task_id in spawns_by_task and spawns_by_task[row.task_id]:
            task_vias[row.task_id].add(spawns_by_task[row.task_id][-1][0])

    all_vias = {via for vias in task_vias.values() for via in vias}
    sorted_vias = sorted(all_vias, key=lambda v: (via_index.get(v, 999999), v))

    # 3. Compute run duration
    if timeline.started_at is not None:
        run_duration = max(0.0, (now_eff - timeline.started_at).total_seconds())
    else:
        run_duration = 0.0

    # 4. Build lanes
    lanes: List[Lane] = []
    for via in sorted_vias:
        via_tasks = [
            row.task_id
            for row in timeline.rows
            if via in task_vias.get(row.task_id, set())
        ]
        # Intervals where tasks on this via were active (non-wait)
        intervals: List[Tuple[datetime, datetime]] = []
        for row in timeline.rows:
            if row.task_id not in via_tasks:
                continue
            task_spawns = spawns_by_task.get(row.task_id, [])
            for seg in row.segments:
                if seg.phase == "wait":
                    continue
                seg_end = seg.end or now_eff
                # If task spawned across multiple vias, match segment to attempt's via
                if len(task_spawns) > 1:
                    seg_via = task_spawns[0][0]
                    for spawn_via, spawn_t in task_spawns:
                        if seg.start >= spawn_t:
                            seg_via = spawn_via
                    if seg_via != via:
                        continue
                intervals.append((seg.start, seg_end))

        busy_s = _union_duration(intervals)
        utilization = round(busy_s / run_duration, 4) if run_duration > 0.0 else 0.0
        lanes.append(
            Lane(via=via, tasks=via_tasks, busy_s=busy_s, utilization=utilization)
        )

    # 5. Critical path and edges
    critical_path, crit_edges = _build_critical_path(timeline.rows, now_eff)
    edges: List[Edge] = []
    known_ids = {row.task_id for row in timeline.rows}
    for row in timeline.rows:
        for dep in row.depends_on:
            if dep in known_ids:
                is_crit = (dep, row.task_id) in crit_edges
                edges.append(Edge(src=dep, dst=row.task_id, critical=is_crit))

    # 6. Cost series
    cost_series = _build_cost_series(timeline, run_events, now_eff)

    return Graph(
        lanes=lanes,
        edges=edges,
        escalations=escalations,
        critical_path=critical_path,
        cost_series=cost_series,
    )
