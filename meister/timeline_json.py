"""JSON serialization for the read-only orchestration timeline."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from meister.config import WorkerTier
from meister.dashboard.metrics import _event_time, _timestamp, event_type
from meister.timeline import Timeline, TaskRow, build_timeline
from meister.timeline_graph import Graph, build_graph


def _iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _project_details(project: str) -> dict[str, Any]:
    root = Path(project).expanduser().resolve()
    digest = hashlib.sha256(os.fsencode(str(root))).digest()
    hue = int.from_bytes(digest[:8], "big") % 360
    return {"name": root.name or str(root), "hue": hue}


_LANE_FIELDS = ("harness", "model", "effort")
_SPAWN_EVENTS = ("worker_spawn", "worker_start")


def _latest_spawns(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Most recent spawn event of each via (log order breaks timestamp ties)."""
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for event in events:
        if event_type(event) not in _SPAWN_EVENTS or not event.get("tier"):
            continue
        ts = _timestamp(_event_time(event)) or datetime.min.replace(tzinfo=timezone.utc)
        via = str(event["tier"])
        if via not in latest or ts >= latest[via][0]:
            latest[via] = (ts, event)
    return {via: event for via, (_, event) in latest.items()}


def lane_catalog_details(
    names: Iterable[str],
    tiers: Sequence[WorkerTier],
    events: Optional[Iterable[dict[str, Any]]] = None,
) -> dict[str, dict[str, Optional[str]]]:
    """Map each via name to its harness, model and effort (all None when unknown).

    Each field comes from the via's most recent spawn event; the catalog is the fallback for
    old logs whose events do not carry it.
    """
    by_name = {tier.name: tier for tier in tiers}
    spawns = _latest_spawns(events or [])
    details: dict[str, dict[str, Optional[str]]] = {}
    for name in names:
        tier = by_name.get(name)
        spawn = spawns.get(name, {})
        details[name] = {
            field: (
                str(spawn[field]) or None
                if spawn.get(field) is not None
                else (getattr(tier, field) or None if tier is not None else None)
            )
            for field in _LANE_FIELDS
        }
    return details


def _catalog_tiers() -> list[WorkerTier]:
    """Return workers.tier_order from the project config; a broken config yields no tiers."""
    from meister.config import load_config

    try:
        return list(load_config().workers.tier_order)
    except Exception:
        return []


def _number_or_none(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0.0 else None


def _source_duration(events: list[dict[str, Any]], run_id: str, task_id: str) -> Optional[float]:
    """Duration of a task in its source run: first spawn to completion (None when unknown)."""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    for event in events:
        if str(event.get("run_id") or "") != run_id or str(event.get("task_id") or "") != task_id:
            continue
        ts = _timestamp(_event_time(event))
        if ts is None:
            continue
        kind = event_type(event)
        if kind in _SPAWN_EVENTS and (start is None or ts < start):
            start = ts
        elif kind == "subtask_completed":
            end = ts
    if start is None or end is None or end < start:
        return None
    return round((end - start).total_seconds(), 3)


def _reuse_by_task(
    events: list[dict[str, Any]], run_id: str
) -> dict[str, tuple[Optional[str], Optional[float]]]:
    """`subtask_reused` of the run: task -> (source run, original duration)."""
    reused: dict[str, tuple[Optional[str], Optional[float]]] = {}
    for event in events:
        if event_type(event) != "subtask_reused" or str(event.get("run_id") or "") != run_id:
            continue
        task_id = str(event.get("task_id") or "")
        source = str(event.get("source_run_id") or "") or None
        duration = _number_or_none(event.get("source_duration_s"))
        if duration is None:
            duration = _number_or_none(event.get("duration_s"))
        if duration is None and source is not None:
            duration = _source_duration(events, source, task_id)
        reused[task_id] = (source, duration)
    return reused


def _attempts_to_list(row: TaskRow) -> list[dict[str, Any]]:
    return [
        {
            "via": attempt.via,
            "outcome": attempt.outcome,
            "reason": attempt.reason,
            "detail": attempt.detail,
            "start": _iso_utc(attempt.start),
            "end": _iso_utc(attempt.end),
        }
        for attempt in row.attempt_details
    ]


def timeline_to_dict(
    timeline: Timeline,
    graph: Graph,
    *,
    project: str,
    now: datetime,
    tiers: Optional[Sequence[WorkerTier]] = None,
    events: Optional[Iterable[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Convert a Timeline and its graph into the stable, JSON-compatible schema."""
    catalog = _catalog_tiers() if tiers is None else tiers
    all_events = list(events or [])
    run_events = [e for e in all_events if str(e.get("run_id") or "") == timeline.run_id]
    details = lane_catalog_details((lane.via for lane in graph.lanes), catalog, run_events)
    reused = _reuse_by_task(all_events, timeline.run_id)
    return {
        "schema": 1,
        "project": _project_details(project),
        "run": {
            "id": timeline.run_id,
            "title": timeline.title,
            "status": timeline.status,
            "started_at": _iso_utc(timeline.started_at),
            "ended_at": _iso_utc(timeline.ended_at),
            "stalled_since": _iso_utc(timeline.stalled_since),
        },
        "summary": {
            "total": timeline.summary.total,
            "completed": timeline.summary.completed,
            "failed": timeline.summary.failed,
            "running": timeline.summary.running,
            "stalled": timeline.summary.stalled,
            "peak_parallel": timeline.summary.peak_parallel,
            "avg_parallel": timeline.summary.avg_parallel,
            "cost_usd": timeline.summary.cost_usd,
        },
        "lanes": [
            {
                "via": lane.via,
                "tasks": lane.tasks,
                "busy_s": lane.busy_s,
                "utilization": lane.utilization,
                **details[lane.via],
            }
            for lane in graph.lanes
        ],
        "rows": [
            {
                "task_id": row.task_id,
                "title": row.title,
                "tier": row.tier,
                "attempts": row.attempts,
                "status": row.status,
                "depends_on": row.depends_on,
                "start": _iso_utc(row.start),
                "end": _iso_utc(row.end),
                "duration_s": row.duration_s,
                "failure": row.failure,
                "segments": [
                    {
                        "phase": segment.phase,
                        "start": _iso_utc(segment.start),
                        "end": _iso_utc(segment.end if segment.end is not None else now),
                        "inferred": segment.inferred,
                        "via": segment.via,
                    }
                    for segment in row.segments
                ],
                # `attempts` stays the count (the page reads it); the list goes beside it
                "attempt_details": _attempts_to_list(row),
                "reused_from": reused.get(row.task_id, (None, None))[0],
                "reused_duration_s": reused.get(row.task_id, (None, None))[1],
            }
            for row in timeline.rows
        ],
        "edges": [
            {"src": edge.src, "dst": edge.dst, "critical": edge.critical}
            for edge in graph.edges
        ],
        "escalations": [
            {
                "task_id": escalation.task_id,
                "from_via": escalation.from_via,
                "to_via": escalation.to_via,
                "at": _iso_utc(escalation.at),
            }
            for escalation in graph.escalations
        ],
        "critical_path": graph.critical_path,
        "cost_series": [
            {
                "t": _iso_utc(point.t),
                "actual_usd": point.actual_usd,
                "baseline_usd": point.baseline_usd,
            }
            for point in graph.cost_series
        ],
        "jev": {
            "calls": [
                {
                    "kind": call.kind,
                    "start": _iso_utc(call.start),
                    "end": _iso_utc(call.end),
                    "task_id": call.task_id,
                }
                for call in timeline.jev.calls
            ],
            "classify_count": timeline.jev.classify_count,
            "control_count": timeline.jev.control_count,
            "cost_usd": timeline.jev.cost_usd,
        },
    }


def empty_timeline_dict(*, project: str) -> dict[str, Any]:
    """Return a valid empty response when a log is missing or has no runs."""
    return {
        "schema": 1,
        "project": _project_details(project),
        "run": None,
        "summary": {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "running": 0,
            "stalled": 0,
            "peak_parallel": 0,
            "avg_parallel": 0.0,
            "cost_usd": None,
        },
        "lanes": [],
        "rows": [],
        "edges": [],
        "escalations": [],
        "critical_path": [],
        "cost_series": [],
        "jev": {"calls": [], "classify_count": 0, "control_count": 0, "cost_usd": 0.0},
    }


def _project_root_for_log(log_file: str) -> str:
    path = Path(log_file).expanduser().resolve()
    if path.parent.name == "logs" and path.parent.parent.name == ".meister":
        return str(path.parent.parent.parent)
    return str(path.parent)


def timeline_json_for_log(
    log_file: str,
    *,
    run_id: Optional[str],
    now: datetime,
) -> dict[str, Any]:
    """Read a log and construct its JSON timeline without modifying the log."""
    from meister.dashboard.metrics import iter_events, list_runs
    from meister.timeline_cli import (
        pick_run,
        price_tables_from_config,
        stale_after_from_config,
        via_index_from_config,
    )

    project = _project_root_for_log(log_file)
    if not os.path.isfile(log_file):
        return empty_timeline_dict(project=project)

    events = list(iter_events(log_file))
    runs = list_runs(events)
    if not runs:
        return empty_timeline_dict(project=project)

    selected_run = pick_run(runs, run_id)
    tier_prices, credit_prices = price_tables_from_config()
    timeline = build_timeline(
        events,
        selected_run,
        now,
        tier_prices=tier_prices,
        credit_prices=credit_prices,
        stale_after=stale_after_from_config(),
    )
    graph = build_graph(
        timeline,
        via_index_from_config(),
        now,
        events=events,
    )
    return timeline_to_dict(timeline, graph, project=project, now=now, events=events)
