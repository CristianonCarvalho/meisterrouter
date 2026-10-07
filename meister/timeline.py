"""Modelo puro da linha do tempo (Gantt) de um run: eventos do log -> `Timeline`. Sem I/O."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from meister.i18n import t
from meister.dashboard.metrics import (
    FAILURE_EVENTS,
    LIFECYCLE_EVENTS,
    _event_time,
    _natural_key,
    _timestamp,
    clip_title,
    event_type,
)

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
DEFAULT_STALE_AFTER = timedelta(seconds=3900)
_TERMINAL = FAILURE_EVENTS | {"subtask_completed", "subtask_reused"}
_NEXT_PHASE = {
    None: "worker",
    "worker": "gate",
    "gate": "integrate",
    "lock_wait": "integrate",
    "integrate": "integrate",
}


@dataclass(frozen=True)
class Segment:
    phase: str
    start: datetime
    end: Optional[datetime]
    inferred: bool = False


@dataclass
class TaskRow:
    task_id: str
    title: str
    tier: Optional[str]
    attempts: int
    status: str
    depends_on: List[str]
    segments: List[Segment]
    start: Optional[datetime]
    end: Optional[datetime]
    duration_s: Optional[float]
    failure: Optional[str]


@dataclass
class Summary:
    total: int
    completed: int
    failed: int
    running: int
    stalled: int
    peak_parallel: int
    avg_parallel: float
    cost_usd: Optional[float]


@dataclass(frozen=True)
class JevCall:
    kind: str
    start: datetime
    end: datetime
    task_id: str


@dataclass
class JevLane:
    calls: List[JevCall]
    classify_count: int
    control_count: int
    cost_usd: float


@dataclass
class Timeline:
    run_id: str
    title: str
    status: str
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    stalled_since: Optional[datetime]
    rows: List[TaskRow]
    summary: Summary
    jev: JevLane


def _ts(event: Dict[str, Any]) -> Optional[datetime]:
    return _timestamp(_event_time(event))


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _plan_dependencies(run_events: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    for event in run_events:
        if event_type(event) != "orchestration_start":
            continue
        raw = event.get("task")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return {}
        if isinstance(raw, list):
            return {
                str(item["id"]): [str(dep) for dep in item.get("depends_on") or []]
                for item in raw
                if isinstance(item, dict) and item.get("id")
            }
        return {}
    return {}


def _integrate_remainder(
    start: datetime, end: datetime, phases: List[Tuple[str, datetime, datetime]]
) -> List[Segment]:
    """Janela final menos gate e lock_wait: o que sobra é a integração (merge)."""
    covered = sorted(
        (max(phase_start, start), min(phase_end, end))
        for name, phase_start, phase_end in phases
        if name in ("gate", "lock_wait")
    )
    out: List[Segment] = []
    cursor = start
    for phase_start, phase_end in covered:
        if phase_start > cursor:
            out.append(Segment("integrate", cursor, phase_start))
        cursor = max(cursor, phase_end)
    if end > cursor:
        out.append(Segment("integrate", cursor, end))
    return out


def _build_row(
    task_id: str,
    events: List[Dict[str, Any]],
    title: str,
    depends_on: List[str],
    run_start: Optional[datetime],
    run_end: Optional[datetime],
) -> TaskRow:
    cli_worker = any(event_type(event) == "worker_start" for event in events) and not any(
        event_type(event) == "worker_spawn" for event in events
    )
    terminal_events = _TERMINAL | ({"worker_end"} if cli_worker else set())
    attempts: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    tier: Optional[str] = None
    for event in events:
        kind = event_type(event)
        timestamp = _ts(event)
        if kind not in {"classify", "control"} and event.get("tier") and event.get("tier") != "unknown":
            tier = str(event["tier"])
        if timestamp is None:
            continue
        if kind == "worker_spawn" or (cli_worker and kind == "worker_start"):
            current = {
                "spawn": timestamp,
                "worker_end": None,
                "phases": [],
                "last_phase": None,
                "last_end": timestamp,
                "terminal": None,
                "kind": None,
                "reason": None,
                "worker_status": None,
            }
            attempts.append(current)
            continue
        if current is None and kind in terminal_events:
            current = {
                "spawn": run_start or timestamp,
                "worker_end": None,
                "phases": [],
                "last_phase": None,
                "last_end": timestamp,
                "terminal": None,
                "kind": None,
                "reason": None,
                "worker_status": None,
            }
            attempts.append(current)
        if current is None:
            continue
        if kind == "worker_phase":
            name = str(event.get("phase") or "")
            phase_duration = timedelta(milliseconds=_num(event.get("duration_ms")))
            if name == "worker":
                current["worker_end"] = timestamp
                current["phases"].append(("worker", current["spawn"], timestamp))
            elif name in ("gate", "lock_wait"):
                current["phases"].append((name, timestamp - phase_duration, timestamp))
            if name in ("worker", "gate", "lock_wait", "integrate"):
                current["last_phase"] = name
                current["last_end"] = timestamp
        elif kind in terminal_events:
            current["terminal"] = timestamp
            current["kind"] = kind
            if kind == "worker_end":
                current["worker_status"] = str(event.get("status") or "")
                current["reason"] = str(event.get("error") or current["worker_status"])
            elif kind in FAILURE_EVENTS:
                current["reason"] = str(event.get("reason") or event.get("error") or kind)
        elif kind == "worker_retry" and current["terminal"] is None:
            # tentativa abandonada (ex.: pane_lost): fecha aqui, senão uma fase inferida ficaria
            # aberta por cima da tentativa seguinte
            current["terminal"] = timestamp
            current["kind"] = "worker_retry"

    segments: List[Segment] = []
    previous_end: Optional[datetime] = None
    for attempt in attempts:
        if previous_end is not None and attempt["spawn"] > previous_end:
            segments.append(Segment("wait", previous_end, attempt["spawn"]))
        elif previous_end is None and depends_on and run_start and attempt["spawn"] > run_start:
            segments.append(Segment("wait", run_start, attempt["spawn"]))
        segments.extend(Segment(name, start, end) for name, start, end in attempt["phases"])
        if attempt["terminal"] is not None:
            if attempt["kind"] == "worker_retry":
                if attempt["worker_end"] is None:
                    segments.append(Segment("worker", attempt["spawn"], attempt["terminal"]))
                if attempt is attempts[-1]:  # retry anunciado e a nova tentativa ainda não começou
                    segments.append(
                        Segment("wait", attempt["terminal"], run_end, inferred=run_end is None)
                    )
            elif attempt["kind"] == "worker_end":
                if attempt["worker_end"] is None:
                    segments.append(Segment("worker", attempt["spawn"], attempt["terminal"]))
            elif attempt["worker_end"] is None:
                segments.append(Segment("worker", attempt["spawn"], attempt["terminal"]))
            else:
                segments.extend(
                    _integrate_remainder(
                        attempt["worker_end"], attempt["terminal"], attempt["phases"]
                    )
                )
            previous_end = attempt["terminal"]
        else:
            segments.append(
                Segment(
                    _NEXT_PHASE[attempt["last_phase"]],
                    attempt["last_end"],
                    run_end,
                    inferred=True,
                )
            )
            previous_end = None

    if not attempts:
        if run_start is not None:
            segments.append(Segment("wait", run_start, run_end, inferred=run_end is None))
        return TaskRow(
            task_id, title, tier, 0, "waiting", depends_on, segments, None, None, None, None
        )

    last = attempts[-1]
    failure: Optional[str] = None
    if last["terminal"] is not None and last["kind"] != "worker_retry":
        if last["kind"] == "worker_end":
            if last["worker_status"] == "done":
                status = "completed"
            else:
                status, failure = "failed", last["reason"]
        elif last["kind"] == "subtask_completed":
            status = "completed"
        elif last["kind"] == "subtask_reused":
            status = "reused"
        else:
            status, failure = "failed", last["reason"]
        end: Optional[datetime] = last["terminal"]
    elif run_end is not None:
        status, failure, end = "failed", t("misc.timeline.no_conclusion"), run_end
    else:
        status, end = "running", None
    start = attempts[0]["spawn"]
    duration = max(0.0, (end - start).total_seconds()) if end is not None else None
    return TaskRow(
        task_id,
        title,
        tier,
        len(attempts),
        status,
        depends_on,
        segments,
        start,
        end,
        duration,
        failure,
    )


def _parallelism(rows: List[TaskRow], now_eff: datetime) -> Tuple[int, float]:
    marks: List[Tuple[datetime, int]] = []
    for row in rows:
        for segment in row.segments:
            if segment.phase == "wait":
                continue
            marks.append((segment.start, 1))
            marks.append((segment.end or now_eff, -1))
    if not marks:
        return 0, 0.0
    marks.sort(key=lambda mark: (mark[0], mark[1]))
    active = peak = 0
    union = total = 0.0
    previous = marks[0][0]
    for moment, delta in marks:
        span = (moment - previous).total_seconds()
        if active > 0:
            union += span
            total += span * active
        active += delta
        peak = max(peak, active)
        previous = moment
    return peak, (total / union if union > 0 else 0.0)


def build_timeline(
    events: Iterable[Dict[str, Any]],
    run_id: str,
    now: datetime,
    tier_prices: Optional[Dict[str, float]] = None,
    credit_prices: Optional[Dict[str, float]] = None,
    stale_after: Optional[timedelta] = None,
) -> Timeline:
    stale_after = stale_after if stale_after is not None else DEFAULT_STALE_AFTER
    all_events = list(events)
    indexed = [
        (index, event)
        for index, event in enumerate(all_events)
        if str(event.get("run_id") or "") == run_id
    ]
    indexed.sort(key=lambda pair: (_ts(pair[1]) or _EPOCH, pair[0]))
    run_events = [event for _, event in indexed]

    start_event = next(
        (event for event in run_events if event_type(event) == "orchestration_start"), None
    )
    end_event = next(
        (event for event in reversed(run_events) if event_type(event) == "orchestration_end"), None
    )
    plan_event = next(
        (event for event in reversed(run_events) if event_type(event) == "plan_parsed"), None
    )
    worker_cli_run = start_event is None and plan_event is None
    started_at = _ts(start_event) if start_event else (_ts(run_events[0]) if run_events else None)
    ended_at = _ts(end_event) if end_event else None
    event_times: List[datetime] = []
    for event in run_events:
        event_time = _ts(event)
        if event_time is not None:
            event_times.append(event_time)
    last_event_at = max(event_times, default=None)
    title = clip_title(start_event.get("task")) if start_event else ""
    stalled_since = (
        last_event_at
        if end_event is None
        and last_event_at is not None
        and now - last_event_at > stale_after
        else None
    )
    if end_event is not None:
        status = (
            "completed"
            if str(end_event.get("status") or "completed") == "completed"
            else "failed"
        )
    elif stalled_since is not None:
        status = "stalled"
    else:
        status = "running"

    plan_ids = [str(task_id) for task_id in (plan_event.get("task_ids") or [])] if plan_event else []
    raw_titles = plan_event.get("task_titles") if plan_event else None
    titles = {str(key): str(value) for key, value in raw_titles.items()} if isinstance(raw_titles, dict) else {}
    dependencies = _plan_dependencies(run_events)

    by_task: Dict[str, List[Dict[str, Any]]] = {}
    for event in run_events:
        task_id = str(event.get("task_id") or "")
        if task_id and task_id != "orchestrator":
            by_task.setdefault(task_id, []).append(event)
    worker_cli_task_ids = {
        task_id
        for task_id, task_events in by_task.items()
        if worker_cli_run
        and any(event_type(event) == "worker_start" for event in task_events)
        and not any(event_type(event) == "worker_spawn" for event in task_events)
    }
    extras = sorted(
        (
            task_id
            for task_id, task_events in by_task.items()
            if task_id not in plan_ids
            and (
                task_id in worker_cli_task_ids
                or any(event_type(event) in LIFECYCLE_EVENTS for event in task_events)
            )
        ),
        key=_natural_key,
    )
    rows = [
        _build_row(
            task_id,
            by_task.get(task_id, []),
            titles.get(task_id, ""),
            dependencies.get(task_id, []),
            started_at,
            ended_at,
        )
        for task_id in plan_ids + extras
    ]
    if worker_cli_task_ids and all(
        any(
            event_type(event) == "worker_end" and _ts(event) is not None
            for event in by_task[task_id]
        )
        for task_id in worker_cli_task_ids
    ):
        terminal_times = [
            _ts(event)
            for task_id in worker_cli_task_ids
            for event in by_task[task_id]
            if event_type(event) == "worker_end"
        ]
        ended_at = max((moment for moment in terminal_times if moment is not None), default=None)
        if ended_at is not None:
            stalled_since = None
            cli_rows = [row for row in rows if row.task_id in worker_cli_task_ids]
            status = (
                "completed" if all(row.status == "completed" for row in cli_rows) else "failed"
            )
    if stalled_since is not None:
        for row in rows:
            if row.status == "running":
                row.status = "stalled"
            row.segments = [
                Segment(segment.phase, segment.start, stalled_since, segment.inferred)
                if segment.end is None
                else segment
                for segment in row.segments
            ]

    jev_events = [
        event for event in run_events if event_type(event) in {"classify", "control"}
    ]
    jev_calls = []
    for event in jev_events:
        end = _ts(event)
        if end is None:
            continue
        jev_calls.append(
            JevCall(
                kind=event_type(event),
                start=end - timedelta(milliseconds=_num(event.get("duration_ms"))),
                end=end,
                task_id=str(event.get("task_id") or ""),
            )
        )
    jev_calls.sort(key=lambda call: call.end)
    jev_cost = sum(
        _num(event.get("cost") if event.get("cost") is not None else event.get("cost_usd"))
        for event in jev_events
    )
    jev = JevLane(
        calls=jev_calls,
        classify_count=sum(event_type(event) == "classify" for event in jev_events),
        control_count=sum(event_type(event) == "control" for event in jev_events),
        cost_usd=jev_cost,
    )

    peak, average = _parallelism(rows, ended_at or stalled_since or now)
    cost: Optional[float] = None
    if run_events:
        from meister.report import compute_run_report

        report = compute_run_report(
            all_events, run_id, tier_prices=tier_prices, credit_prices=credit_prices
        )
        known = [
            row["cost_known_usd"]
            for row in report["by_tier"].values()
            if row.get("cost_known_usd") is not None
        ]
        cost = round(sum(known), 6) if known else None
    summary = Summary(
        total=len(rows),
        completed=sum(row.status in ("completed", "reused") for row in rows),
        failed=sum(row.status == "failed" for row in rows),
        running=sum(row.status == "running" for row in rows),
        stalled=sum(row.status == "stalled" for row in rows),
        peak_parallel=peak,
        avg_parallel=average,
        cost_usd=cost,
    )
    return Timeline(
        run_id, title, status, started_at, ended_at, stalled_since, rows, summary, jev
    )
