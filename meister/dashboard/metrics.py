"""Pure event aggregation helpers for the web telemetry dashboard."""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional


# Eventos do ciclo de vida de um worker/tarefa. So ids que tem pelo menos um deles contam como TAREFA:
# ids so de `classify`/`route_decision`/`control` (hash de logs antigos, julgamento final) nao sao tarefas.
LIFECYCLE_EVENTS = {
    "worker_spawn",
    "worker_retry",
    "worker_timeout",
    "worker_error",
    "worker_task_start",
    "worker_task_end",
    "worker_task_error",
    "subtask_completed",
    "subtask_reused",
    "subtask_rejected",
    "gate_infrastructure_error",
}
FAILURE_EVENTS = {
    "subtask_rejected",
    "worker_error",
    "worker_timeout",
    "gate_infrastructure_error",
}
TERMINAL_EVENTS = FAILURE_EVENTS | {"subtask_completed", "subtask_reused"}


def event_type(event: Dict[str, Any]) -> str:
    return str(event.get("event_type") or event.get("event") or "")


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def _event_time(event: Dict[str, Any]) -> str:
    value = event.get("ts") or event.get("timestamp") or ""
    return str(value) if value is not None else ""


def _event_sort_key(event: Dict[str, Any]) -> datetime:
    return _timestamp(_event_time(event)) or datetime.min.replace(tzinfo=timezone.utc)


_JSON_DESCRIPTION = re.compile(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)')


def clip_title(value: Any, size: int = 100) -> str:
    """Primeira linha com texto de um plano/descrição, cortada em `size` caracteres."""
    text = str(value or "")
    if text.lstrip().startswith(("[", "{")):
        # plano JSON (cortado em 200 caracteres no log): usa a primeira "description" em vez do JSON cru
        match = _JSON_DESCRIPTION.search(text)
        if match:
            try:
                text = json.loads(f'"{match.group(1)}"')
            except ValueError:
                text = match.group(1)
        else:
            return ""
    for line in text.splitlines():
        line = line.strip()
        if not any(char.isalnum() for char in line) or line.rstrip(":").lower() in {"plan", "plano"}:
            continue
        return line if len(line) <= size else line[: size - 1] + "…"
    return ""


def _plan_titles(events: Iterable[Dict[str, Any]]) -> Dict[tuple, str]:
    """Títulos por (run_id, task_id), gravados pelo evento `plan_parsed` (runs novos)."""
    titles: Dict[tuple, str] = {}
    for event in events:
        if event_type(event) != "plan_parsed":
            continue
        run_id = str(event.get("run_id") or "")
        raw = event.get("task_titles")
        if run_id and isinstance(raw, dict):
            for task_id, title in raw.items():
                if title:
                    titles[(run_id, str(task_id))] = str(title)
    return titles


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cost(event: Dict[str, Any]) -> float:
    return _number(event.get("cost") if event.get("cost") is not None else event.get("cost_usd"))


def _task_key(event: Dict[str, Any]) -> Optional[tuple]:
    run_id = str(event.get("run_id") or "")
    task_id = str(event.get("task_id") or "")
    if not run_id or run_id == "global" or not task_id or task_id == "orchestrator":
        return None
    return run_id, task_id


def _task_events(events: Iterable[Dict[str, Any]]) -> Dict[tuple, List[Dict[str, Any]]]:
    """Eventos agrupados por tarefa (run_id, task_id); so ids com ciclo de vida de worker sao tarefas."""
    grouped: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        key = _task_key(event)
        if key:
            grouped[key].append(event)
    return {
        key: task_events for key, task_events in grouped.items()
        if any(event_type(event) in LIFECYCLE_EVENTS for event in task_events)
    }


def iter_events(log_file: str, run_id: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Yield valid JSON object events from a JSONL log without buffering it."""
    try:
        source = open(log_file, "r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return
    with source:
        for line in source:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if run_id is None or run_id == "all" or str(event.get("run_id") or "") == run_id:
                yield event


def list_runs(
    events: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
    stale_after_s: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Summarize non-global orchestration runs, newest first."""
    runs: Dict[str, Dict[str, Any]] = {}
    tasks: Dict[str, set] = defaultdict(set)
    for event in events:
        run_id = str(event.get("run_id") or "")
        if not run_id or run_id == "global":
            continue
        row = runs.setdefault(
            run_id,
            {
                "run_id": run_id,
                "title": "",
                "started_at": None,
                "ended_at": None,
                "status": None,
                "events": 0,
                "tasks": 0,
                "_sort_at": "",
                "_last_event_at": None,
                "_has_end": False,
            },
        )
        row["events"] += 1
        timestamp = _event_time(event)
        parsed_timestamp = _timestamp(timestamp)
        current_timestamp = _timestamp(row["_sort_at"])
        if parsed_timestamp and (current_timestamp is None or parsed_timestamp > current_timestamp):
            row["_sort_at"] = timestamp
        last_event_at = row["_last_event_at"]
        if parsed_timestamp and (last_event_at is None or parsed_timestamp > last_event_at):
            row["_last_event_at"] = parsed_timestamp
        kind = event_type(event)
        if kind == "orchestration_start":
            row["started_at"] = timestamp or row["started_at"]
            row["title"] = clip_title(event.get("task")) or row["title"]
        elif kind == "orchestration_end":
            row["ended_at"] = timestamp or row["ended_at"]
            row["status"] = event.get("status") or row["status"]
            row["_has_end"] = True
        task_key = _task_key(event)
        if task_key and kind in LIFECYCLE_EVENTS:
            tasks[run_id].add(task_key[1])
    for run_id, row in runs.items():
        row["tasks"] = len(tasks[run_id])
    ordered_runs = sorted(
        runs.values(),
        key=lambda row: (
            _timestamp(row["started_at"] or row["_sort_at"])
            or datetime.min.replace(tzinfo=timezone.utc),
            row["run_id"],
        ),
        reverse=True,
    )
    for row in ordered_runs:
        if (
            now is not None
            and stale_after_s is not None
            and not row["_has_end"]
            and row["_last_event_at"] is not None
            and now - row["_last_event_at"] > timedelta(seconds=stale_after_s)
        ):
            row["status"] = "sem sinal"
        row.pop("_sort_at")
        row.pop("_last_event_at")
        row.pop("_has_end")
    return ordered_runs


def _failure_reason(event: Dict[str, Any]) -> str:
    reason = event.get("reason") or event.get("error") or event.get("message") or event_type(event)
    return str(reason).replace("\n", " ").strip()[:180]


def _build_task(
    run_id: str,
    task_id: str,
    events: List[Dict[str, Any]],
    title: str = "",
) -> Dict[str, Any]:
    spawns = [event for event in events if event_type(event) == "worker_spawn"]
    retries = [event for event in events if event_type(event) == "worker_retry"]
    completions = [event for event in events if event_type(event) == "subtask_completed"]
    failures = [event for event in events if event_type(event) in FAILURE_EVENTS]
    terminals = [event for event in events if event_type(event) in TERMINAL_EVENTS]
    latest_terminal = terminals[-1] if terminals else None
    latest_type = event_type(latest_terminal) if latest_terminal else ""
    last_activity_index = max(
        (
            index for index, event in enumerate(events)
            if event_type(event) in {"worker_spawn", "worker_retry"}
        ),
        default=-1,
    )
    last_terminal_index = max(
        (index for index, event in enumerate(events) if event_type(event) in TERMINAL_EVENTS),
        default=-1,
    )
    if last_activity_index > last_terminal_index:
        latest_terminal = None
        latest_type = ""

    if latest_type == "subtask_completed":
        status = "completed"
    elif latest_type == "subtask_reused":
        status = "reused"
    elif latest_type in FAILURE_EVENTS:
        status = "failed"
    elif spawns or retries:
        status = "running"
    else:
        status = "unknown"

    duration = None
    if spawns and latest_terminal and latest_type != "subtask_reused":
        started = _timestamp(_event_time(spawns[0]))
        ended = _timestamp(_event_time(latest_terminal))
        if started and ended:
            duration = max(0.0, (ended - started).total_seconds())

    classification_events = [
        event for event in events
        if event_type(event) == "classify" and event.get("classification") is not None
    ]
    latest_classification = classification_events[-1] if classification_events else None
    latest_tier = next(
        (
            event.get("tier")
            for event in reversed(events)
            if event_type(event) not in {"classify", "control"} and event.get("tier")
        ),
        None,
    )
    cost = sum(_cost(event) for event in completions)
    return {
        "run_id": run_id,
        "task_id": task_id,
        "title": title,
        "status": status,
        "tier": latest_tier,
        "attempts": len(spawns),
        "retries": len(retries),
        "duration_s": round(duration, 3) if duration is not None else None,
        "failure": _failure_reason(failures[-1]) if status == "failed" and failures else None,
        "cost_usd": round(cost, 6),
        "jev_calls": len([event for event in events if event_type(event) == "classify"]),
        "classification": latest_classification.get("classification") if latest_classification else None,
    }


def _natural_key(value: str) -> List[Any]:
    """Chave de ordenação natural: números dentro do id comparam como números."""
    return [
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", value) if part
    ]


def compute_summary(
    events: Iterable[Dict[str, Any]],
    run_id: Optional[str],
    log_file: Optional[str] = None,
    now: Optional[datetime] = None,
    stale_after_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute grouped analytics for the selected run (or all runs)."""
    event_list = list(events)
    runs = list_runs(event_list, now=now, stale_after_s=stale_after_s)
    selected_run = run_id
    if selected_run is None:
        selected_run = runs[0]["run_id"] if runs else None
    if selected_run != "all":
        selected_events = [
            event for event in event_list
            if selected_run is not None and str(event.get("run_id") or "") == selected_run
        ]
    else:
        selected_events = event_list

    grouped = _task_events(selected_events)
    titles = _plan_titles(selected_events)
    tasks = [
        _build_task(task_run_id, task_id, task_events, titles.get((task_run_id, task_id), ""))
        for (task_run_id, task_id), task_events in grouped.items()
    ]
    # Runs do mais recente para o mais antigo (mesma ordem de `list_runs`); dentro do run, ordem natural
    # dos ids (task_2 antes de task_10).
    run_position = {run["run_id"]: index for index, run in enumerate(runs)}
    tasks.sort(key=lambda task: (
        run_position.get(task["run_id"], len(run_position)), _natural_key(task["task_id"]),
    ))
    counts = Counter(task["status"] for task in tasks)
    completed_durations = [
        task["duration_s"] for task in tasks
        if task["status"] == "completed" and task["duration_s"] is not None
    ]

    classify_events = [event for event in selected_events if event_type(event) == "classify"]
    control_events = [event for event in selected_events if event_type(event) == "control"]
    jev_events = classify_events + control_events
    classifications_by_call = Counter(
        str(event.get("classification") or "UNKNOWN") for event in classify_events
    )
    last_classifications = {
        key: next(
            (event.get("classification") for event in reversed(task_events)
             if event_type(event) == "classify" and event.get("classification") is not None),
            None,
        )
        for key, task_events in _task_events(selected_events).items()
    }
    classifications_by_task = Counter(
        str(value) for value in last_classifications.values() if value is not None
    )
    route_status: Counter[str] = Counter()
    for event in selected_events:
        if event_type(event) != "route_decision":
            continue
        status = str(event.get("status") or "").lower()
        if status == "resumed":
            # tarefa retomada (--resume): a via ja estava escolhida, o Jev nao foi consultado
            route_status["resumed"] += 1
        elif status == "skipped_unavailable":
            route_status["cooldown"] += 1
        elif status in {"unavailable", "indisponivel", "api_unavailable"}:
            route_status["unavailable"] += 1
        elif status == "fallback" or event.get("fallback_rule_applied"):
            route_status["fallback"] += 1
        else:
            route_status["decided"] += 1

    linked_keys = set(grouped)
    unlinked_calls = sum(
        1 for event in classify_events
        if _task_key(event) not in linked_keys
    )
    by_tier: Dict[str, Dict[str, Any]] = {}
    for task in tasks:
        tier = str(task["tier"] or "unknown")
        row = by_tier.setdefault(tier, {"tasks": 0, "completed": 0, "_durations": []})
        row["tasks"] += 1
        if task["status"] == "completed":
            row["completed"] += 1
            if task["duration_s"] is not None:
                row["_durations"].append(task["duration_s"])
    for row in by_tier.values():
        durations = row.pop("_durations")
        row["avg_duration_s"] = round(sum(durations) / len(durations), 3) if durations else None

    worker_costs = [_cost(event) for event in selected_events if event_type(event) == "subtask_completed"]
    jev_duration_values = [_number(event.get("duration_ms")) for event in jev_events]
    return {
        "meta": {
            "log_file": log_file,
            "run_id": selected_run,
            "run": next((row for row in runs if row["run_id"] == selected_run), None),
            "runs_available": len(runs),
            "events_total": len(selected_events),
        },
        "tasks": tasks,
        "totals": {
            "tasks_total": len(tasks),
            "completed": counts["completed"],
            "failed": counts["failed"],
            "reused": counts["reused"],
            "running": counts["running"],
            "avg_duration_s": round(sum(completed_durations) / len(completed_durations), 3)
            if completed_durations else None,
        },
        "jev": {
            "classify_calls": len(classify_events),
            "unlinked_classify_calls": unlinked_calls,
            "control_calls": len(control_events),
            "classify_by_result": dict(classifications_by_call),
            "route_by_status": dict(route_status),
            "classification_by_task": dict(classifications_by_task),
            "classification_by_call": dict(classifications_by_call),
            "avg_latency_ms": round(sum(jev_duration_values) / len(jev_duration_values), 2)
            if jev_duration_values else None,
            "tokens_in": sum(int(_number(event.get("tokens_in"))) for event in jev_events),
            "tokens_out": sum(int(_number(event.get("tokens_out"))) for event in jev_events),
            "cost_usd": round(sum(_cost(event) for event in jev_events), 6),
        },
        "workers": {
            "cost_usd": round(sum(worker_costs), 6) if any(worker_costs) else None,
            "by_tier": by_tier,
        },
        "savings": None,
    }


def query_events(
    events: Iterable[Dict[str, Any]],
    run_id: Optional[str],
    task_id: Optional[str] = None,
    event_type_filter: Optional[str] = None,
    tier: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
) -> Dict[str, Any]:
    """Filter, sort and paginate event dictionaries."""
    if not 1 <= limit <= 500:
        raise ValueError("limit deve estar entre 1 e 500")
    if offset < 0:
        raise ValueError("offset deve ser maior ou igual a zero")
    if order not in {"asc", "desc"}:
        raise ValueError("order deve ser 'asc' ou 'desc'")
    event_list = list(events)
    if run_id is None:
        available_runs = list_runs(event_list)
        run_id = available_runs[0]["run_id"] if available_runs else None
    matching = []
    needle = (q or "").casefold()
    for event in event_list:
        if run_id != "all" and (
            run_id is None or str(event.get("run_id") or "") != run_id
        ):
            continue
        if task_id and str(event.get("task_id") or "") != task_id:
            continue
        if event_type_filter and event_type(event) != event_type_filter:
            continue
        if tier and str(event.get("tier") or "") != tier:
            continue
        if needle and needle not in json.dumps(event, ensure_ascii=False, sort_keys=True).casefold():
            continue
        matching.append(event)
    # Desempate pela posição no log: sort(reverse=True) preserva a ordem original dos
    # empates, o que deixaria o evento mais antigo antes do mais recente.
    indexed = sorted(
        enumerate(matching),
        key=lambda pair: (_event_sort_key(pair[1]), pair[0]),
        reverse=(order == "desc"),
    )
    matching = [event for _, event in indexed]
    return {
        "total": len(matching),
        "limit": limit,
        "offset": offset,
        "events": matching[offset:offset + limit],
        "task_titles": {
            task: title for (title_run, task), title in _plan_titles(event_list).items()
            if run_id == "all" or title_run == run_id
        },
    }
