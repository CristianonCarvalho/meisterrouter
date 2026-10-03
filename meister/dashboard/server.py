"""Local Flask server for grouped and event-level orchestration telemetry."""

import os
from collections import Counter
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

from meister.dashboard.metrics import (
    compute_summary,
    event_type,
    iter_events,
    list_runs,
    query_events,
)
from meister.logger import find_project_root, get_log_file


app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


def _read_events() -> List[Dict[str, Any]]:
    return list(iter_events(get_log_file()))


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
    return render_template("index.html")


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
        "log_file": log_file,
        "exists": exists,
        "size_bytes": size_bytes,
        "lines": lines,
        "project_root": project_root,
        "other_logs": other_logs,
    })


@app.route("/api/runs")
def api_runs():
    return jsonify({"runs": list_runs(_read_events())})


@app.route("/api/summary")
def api_summary():
    run_id = request.args.get("run_id")
    return jsonify(compute_summary(_read_events(), run_id, log_file=get_log_file()))


def _query_int(name: str, default: int) -> int:
    value = request.args.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} deve ser um inteiro") from exc


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


def start_server(
    host: str = "127.0.0.1",
    port: int = 5050,
    log_dir: Optional[str] = None,
) -> None:
    """Start the dashboard and report the exact log file it will read."""
    if log_dir:
        os.environ["MEISTER_LOG_DIR"] = os.path.abspath(log_dir)
    print(f"🚀 [MeisterRouter] Dashboard iniciado em http://{host}:{port}")
    print(f"📁 Lendo eventos de: {get_log_file()}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    start_server()
