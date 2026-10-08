from __future__ import annotations

import atexit
import os
import socket
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request
from werkzeug.serving import make_server

from meister.config import load_config
from meister.dashboard.metrics import (
    compute_summary,
    event_type,
    iter_events,
    list_runs,
    query_events,
)
from meister.dashboard.state import (
    ServerState,
    format_iso_utc,
    read_state,
    remove_state,
    write_state,
)
from meister.i18n import get_language, t
from meister.logger import find_project_root, get_log_file
from meister.report import compute_run_report
from meister.timeline_cli import stale_after_from_config
from meister.timeline_json import (
    _project_details,
    empty_timeline_dict,
    timeline_json_for_log,
)


app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

_last_call_monotonic: float = time.monotonic()
_last_call_lock = threading.Lock()


def _touch_call_time() -> None:
    global _last_call_monotonic
    with _last_call_lock:
        _last_call_monotonic = time.monotonic()


@app.before_request
def _on_before_request():
    _touch_call_time()


def _current_project_root() -> Optional[str]:
    root = find_project_root()
    if root:
        return root
    log_file = get_log_file()
    meta = project_from_log(log_file)
    path = meta.get("path")
    if path and os.path.isdir(path):
        return path
    return None


def _touch_project_state(now: datetime) -> None:
    project_root = _current_project_root()
    if not project_root:
        return
    state = read_state(project_root)
    if state is not None:
        state.last_seen = format_iso_utc(now)
        write_state(project_root, state)


def project_from_log(log_file: str, project_root: Optional[str] = None) -> Dict[str, str]:
    """Projeto dono do log: `<projeto>/.meister/logs/orchestration_log.jsonl` -> nome e pasta de `<projeto>`.

    Sem esse layout, cai para a pasta do projeto onde o comando rodou.
    """
    logs_dir = os.path.dirname(os.path.abspath(log_file))
    meister_dir = os.path.dirname(logs_dir)
    if os.path.basename(logs_dir) == "logs" and os.path.basename(meister_dir) == ".meister":
        owner = os.path.dirname(meister_dir)
        if owner == os.path.expanduser("~"):
            return {"name": "global (~/.meister)", "path": meister_dir}
        return {"name": os.path.basename(owner) or owner, "path": owner}
    if project_root:
        return {"name": os.path.basename(project_root.rstrip(os.sep)) or project_root, "path": project_root}
    return {"name": os.path.basename(logs_dir) or logs_dir, "path": logs_dir}


def _read_events() -> List[Dict[str, Any]]:
    return list(iter_events(get_log_file()))


def _dashboard_messages() -> Dict[str, str]:
    keys = (
        "project", "telemetry", "select_run", "view_mode", "grouped", "analytical",
        "selected_run", "tasks", "task", "status", "lane", "attempts", "duration",
        "failure", "classification", "jev_calls", "events", "jev_calls_heading",
        "route_results", "classification_by_task", "classification_by_call",
        "jev_latency_cost", "by_lane", "time_by_phase", "event_filters",
        "all_tasks", "all_events", "all_lanes", "search", "filter", "sort_newest",
        "sort_oldest", "date_time", "event_type", "previous", "next", "page_status",
        "not_measured", "known_cost_comment", "task_word", "task_word_plural",
        "in_progress", "running", "not_reported", "all_runs", "all_runs_sum", "no_run_found", "run_id",
        "started", "classifications", "completed", "failures", "reused",
        "average_duration", "completed_only", "jev_cost", "worker_cost",
        "select_a_run", "unknown_cost_tasks", "reported_estimated_cost",
        "orchestrator_overhead", "wall_clock", "worker", "worker_peak",
        "worker_sum", "classify_without_task", "average_latency", "cost_usd",
        "task_completed", "task_completed_one", "task_completed_many", "average",
        "tokens", "credits", "empty_data", "view_events", "no_tasks",
        "no_matching_events", "page", "log_read", "log_size", "log_not_created",
        "other_logs_not_read", "full_json", "attempt", "cost", "elapsed",
        "payload_size", "unknown_status", "stalled_status", "status_completed",
        "status_failed", "status_reused", "status_running",
    )
    return {key: t(f"reports.dashboard.{key}") for key in keys}


def _price_tables() -> Tuple[Dict[str, float], Dict[str, float]]:
    """Preços do catálogo atual: (US$ por 1M de tokens, US$ por crédito) por via."""
    try:
        config = load_config()
    except Exception:
        return {}, {}
    tiers = [*config.workers.tier_order, *config.workers.disabled]
    return (
        {tier.name: tier.cost_per_m_tokens for tier in tiers},
        {tier.name: tier.credit_usd for tier in tiers if tier.credit_usd is not None},
    )


def _all_runs_cost(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Soma o custo dos workers de todos os runs, com o mesmo cálculo do relatório."""
    tier_prices, credit_prices = _price_tables()
    by_tier: Dict[str, Dict[str, Any]] = {}
    for run in list_runs(events):
        report = compute_run_report(
            events, run["run_id"], tier_prices=tier_prices, credit_prices=credit_prices
        )
        for tier, row in report["by_tier"].items():
            total = by_tier.setdefault(tier, {"cost_known_usd": None, "events_unknown": 0})
            if row["cost_known_usd"] is not None:
                total["cost_known_usd"] = round(
                    (total["cost_known_usd"] or 0.0) + row["cost_known_usd"], 6
                )
            total["events_unknown"] += row["events_unknown"]
    return {"by_tier": by_tier}


def compute_metrics(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compatibility metrics for the Herdr TUI, using grouped event data."""
    summary = compute_summary(events, "all")
    jev = summary["jev"]
    workers = summary["workers"]
    by_action = Counter(
        str(event.get("action") or "UNKNOWN")
        for event in events if event_type(event) == "control"
    )
    return {
        "total_cost": round(jev["cost_usd"] + (workers["cost_usd"] or 0.0), 6),
        "tasks_count": summary["totals"]["tasks_total"],
        "completed_tasks": summary["totals"]["completed"],
        "avg_duration_sec": summary["totals"]["avg_duration_s"] or 0.0,
        "by_model_cost": {},
        "by_model_count": {
            tier: row["completed"] for tier, row in workers["by_tier"].items()
        },
        "by_classification": jev["classify_by_result"],
        "by_action": dict(by_action),
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        messages=_dashboard_messages(),
        language=get_language(),
    )


@app.route("/api/meta")
def api_meta():
    log_file = get_log_file()
    exists = os.path.isfile(log_file)
    lines = 0
    size_bytes = 0
    if exists:
        size_bytes = os.path.getsize(log_file)
        with open(log_file, "r", encoding="utf-8", errors="replace") as source:
            lines = sum(1 for _ in source)

    project_root = find_project_root()
    known_logs = [
        os.path.join(os.path.expanduser("~"), ".meister", "logs", "orchestration_log.jsonl"),
    ]
    if project_root:
        known_logs.append(
            os.path.join(project_root, ".meister", "logs", "orchestration_log.jsonl")
        )
    current = os.path.abspath(log_file)
    other_logs = sorted({
        os.path.abspath(path)
        for path in known_logs
        if os.path.abspath(path) != current and os.path.isfile(path)
    })
    return jsonify({
        "project": project_from_log(log_file, project_root),
        "log_file": log_file,
        "exists": exists,
        "size_bytes": size_bytes,
        "lines": lines,
        "project_root": project_root,
        "other_logs": other_logs,
    })


@app.route("/api/runs")
def api_runs():
    return jsonify({
        "runs": list_runs(
            _read_events(),
            now=datetime.now(timezone.utc),
            stale_after_s=stale_after_from_config().total_seconds(),
        )
    })


@app.route("/api/summary")
def api_summary():
    run_id = request.args.get("run_id")
    events = _read_events()
    summary = compute_summary(
        events,
        run_id,
        log_file=get_log_file(),
        now=datetime.now(timezone.utc),
        stale_after_s=stale_after_from_config().total_seconds(),
    )
    # Custo, tokens, créditos e tempo por fase vêm do mesmo cálculo do `meister report`.
    selected = summary["meta"]["run_id"]
    tier_prices, credit_prices = _price_tables()
    summary["report"] = (
        compute_run_report(
            events, selected, tier_prices=tier_prices, credit_prices=credit_prices
        )
        if selected and selected != "all" else None
    )
    summary["all_runs_cost"] = _all_runs_cost(events) if selected == "all" else None
    return jsonify(summary)


def _query_int(name: str, default: int) -> int:
    value = request.args.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(t("reports.events_integer", name=name)) from exc


@app.route("/api/events")
def api_events():
    try:
        limit = _query_int("limit", 50)
        offset = _query_int("offset", 0)
        result = query_events(
            _read_events(),
            request.args.get("run_id"),
            task_id=request.args.get("task_id"),
            event_type_filter=request.args.get("event_type"),
            tier=request.args.get("tier"),
            q=request.args.get("q"),
            limit=limit,
            offset=offset,
            order=request.args.get("order", "desc"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/timeline")
def api_timeline():
    now = datetime.now(timezone.utc)
    _touch_call_time()
    _touch_project_state(now)

    raw_run_id = request.args.get("run_id")
    run_id = raw_run_id.strip() if raw_run_id is not None and raw_run_id.strip() else None
    log_file = get_log_file()
    project_root = _current_project_root() or os.path.dirname(os.path.abspath(log_file))

    if not os.path.isfile(log_file):
        if run_id is not None:
            return jsonify({
                "error": t(
                    "commands.timeline.runs_available",
                    status=t("commands.timeline.id_missing"),
                    id=run_id,
                    runs=t("commands.timeline.no_runs"),
                )
            }), 404
        return jsonify(empty_timeline_dict(project=project_root))

    events = _read_events()
    runs = list_runs(events)
    if not runs:
        if run_id is not None:
            return jsonify({
                "error": t(
                    "commands.timeline.runs_available",
                    status=t("commands.timeline.id_missing"),
                    id=run_id,
                    runs=t("commands.timeline.no_runs"),
                )
            }), 404
        return jsonify(empty_timeline_dict(project=project_root))

    try:
        data = timeline_json_for_log(log_file, run_id=run_id, now=now)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    if project_root:
        data["project"] = _project_details(project_root)

    return jsonify(data)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _has_active_run() -> bool:
    events = _read_events()
    if not events:
        return False
    runs = list_runs(
        events,
        now=datetime.now(timezone.utc),
        stale_after_s=stale_after_from_config().total_seconds(),
    )
    if not runs:
        return False
    for run in runs:
        status = run.get("status")
        if status in (None, "running"):
            return True
    return False


def _idle_watchdog(
    server: Any,
    idle_exit_minutes: float,
    stop_event: threading.Event,
) -> None:
    idle_timeout_s = idle_exit_minutes * 60.0
    while not stop_event.wait(timeout=0.2):
        with _last_call_lock:
            idle_duration = time.monotonic() - _last_call_monotonic
        if idle_duration >= idle_timeout_s:
            if not _has_active_run():
                server.shutdown()
                break


def start_server(
    host: str = "127.0.0.1",
    port: int = 5050,
    log_dir: Optional[str] = None,
    idle_exit_minutes: Optional[float] = None,
) -> None:
    """Start the dashboard and report the exact log file it will read."""
    if log_dir:
        os.environ["MEISTER_LOG_DIR"] = os.path.abspath(log_dir)

    project_root = _current_project_root() or os.getcwd()

    if getattr(app.run, "__func__", None) is not Flask.run:
        actual_port = port if port != 0 else _find_free_port()
        now_str = format_iso_utc(datetime.now(timezone.utc))
        state = ServerState(
            pid=os.getpid(),
            port=actual_port,
            url=f"http://{host}:{actual_port}",
            started_at=now_str,
            last_seen=now_str,
        )
        write_state(project_root, state)
        atexit.register(remove_state, project_root)
        try:
            print(t("reports.dashboard.server_started", host=host, port=actual_port))
            print(t("reports.dashboard.reading_events", path=get_log_file()))
            app.run(host=host, port=actual_port, debug=False)
        finally:
            remove_state(project_root)
        return

    server = make_server(host, port, app)
    actual_port = int(server.port)
    now_str = format_iso_utc(datetime.now(timezone.utc))
    state = ServerState(
        pid=os.getpid(),
        port=actual_port,
        url=f"http://{host}:{actual_port}",
        started_at=now_str,
        last_seen=now_str,
    )
    write_state(project_root, state)
    atexit.register(remove_state, project_root)

    stop_event = threading.Event()
    if idle_exit_minutes is not None and idle_exit_minutes > 0:
        watchdog = threading.Thread(
            target=_idle_watchdog,
            args=(server, idle_exit_minutes, stop_event),
            daemon=True,
        )
        watchdog.start()

    print(t("reports.dashboard.server_started", host=host, port=actual_port))
    print(t("reports.dashboard.reading_events", path=get_log_file()))
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        remove_state(project_root)


if __name__ == "__main__":
    start_server()

