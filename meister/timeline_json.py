"""JSON serialization for the read-only orchestration timeline."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from meister.timeline import Timeline, build_timeline
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


def timeline_to_dict(
    timeline: Timeline,
    graph: Graph,
    *,
    project: str,
    now: datetime,
) -> dict[str, Any]:
    """Convert a Timeline and its graph into the stable, JSON-compatible schema."""
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
                    }
                    for segment in row.segments
                ],
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
    return timeline_to_dict(timeline, graph, project=project, now=now)
