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

from meister.code_id import current_code_id
from meister.config import load_config
from meister.config_editor import (
    ConfigEditorError,
    ConfigStampError,
    ConfigValidationError,
    LaneDocument,
    apply_lanes,
    load_effective_lanes,
    restore_default_lanes,
    validate_lanes,
)
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
from meister.config import ConfigIssue
from meister.i18n import CATALOGS, get_language, t
from meister.logger import find_project_root, get_log_file
from meister.model_catalog import list_models
from meister.projects import (
    ProjectError,
    discover_projects,
    get_project,
    list_projects,
    project_id,
    register_project,
    register_projects,
)
from meister.report import compute_run_report
from meister.timeline import build_timeline
from meister.timeline_cli import (
    price_tables_from_config,
    stale_after_from_config,
    via_index_from_config,
)
from meister.timeline_graph import build_graph
from meister.timeline_json import (
    _project_details,
    empty_timeline_dict,
    timeline_to_dict,
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


def _timeline_messages() -> Dict[str, str]:
    keys = (
        "waiting_first_run",
        "live",
        "live_newest",
        "run_not_found",
        "tasks",
        "completed",
        "failed",
        "running",
        "stalled",
        "peak_parallel",
        "avg_parallel",
        "cost",
        "baseline",
        "duration",
        "lanes",
        "phase_worker",
        "phase_gate",
        "phase_integrate",
        "phase_wait",
        "phase_failed_attempt",
        "phase_lock_wait",
        "phase_gate_task",
        "phase_gate_integrated",
        "lateral_fallback",
        "escalation",
        "critical_path",
        "legend_reused",
        "now",
        "cost_river",
        "dashboard",
        "config",
        "select_run",
        "unassigned",
        "mode_live",
        "mode_selected",
        "mode_paused",
        "mode_all",
        "keyboard_shortcuts",
        "shortcut_previous",
        "shortcut_next",
        "shortcut_live",
        "shortcut_all",
        "shortcut_pause",
        "shortcut_zoom",
        "shortcut_pan",
        "run_started",
        "run_duration",
        "run_cost",
        "run_title",
        "run_status",
        "mode_resume",
        "duration_seconds",
        "duration_minutes",
        "duration_hours",
        "no_runs",
        "tooltip_phase_duration",
        "tooltip_total_duration",
        "tooltip_wait_duration",
        "tooltip_attempt",
        "tooltip_reused_from",
        "tooltip_original_duration",
        "outcome_completed",
        "outcome_error",
        "outcome_quota",
        "outcome_timeout",
        "outcome_gate_rejected",
        "outcome_scope",
        "shortcut_sound",
        "sound_on",
        "sound_off",
        "sound_title",
    )
    return {key: t(f"cli.timeline.web.{key}") for key in keys}


def _config_messages() -> Dict[str, str]:
    keys = (
        "title",
        "subtitle",
        "back_timeline",
        "dashboard",
        "loading",
        "load_failed",
        "source",
        "source_default",
        "position",
        "name",
        "harness",
        "model",
        "effort",
        "max_parallel",
        "enabled",
        "yes",
        "no",
        "no_lanes",
        "blocked_run_active",
        "read_only_remote",
        "read_only_notice",
        "actions",
        "effort_default",
        "save",
        "revert",
        "restore_default",
        "restore_confirm",
        "confirm_yes",
        "confirm_no",
        "saving",
        "saved",
        "restored",
        "unsaved",
        "invalid",
        "stale",
        "reload",
        "save_failed",
        "project_list_notice",
        "path_title",
        "path_help",
        "path_class",
        "path_start",
        "path_clear",
        "path_disabled",
        "path_before_start",
        "path_failed",
        "path_runs_here",
        "result_no_eligible",
        "result_all_failed",
        "result_ok",
        "result_failed_before",
        "result_escalated",
        "level_title",
        "level_other",
        "level_order_hint",
        "escalation",
        "escalation_cost",
        "chip_enabled",
        "chip_disabled",
        "chip_retries",
        "chip_all_classes",
        "chip_only_classes",
        "chip_parallel",
        "content_effort",
        "cost_value",
        "no_model",
        "legend_lateral",
        "legend_escalation",
        "edit",
        "drawer_title",
        "close",
        "group_content",
        "group_policy",
        "rename_warning",
        "field_cost_per_m_tokens",
        "field_credit_usd",
        "field_max_retries",
        "field_idle_timeout",
        "field_max_runtime",
        "field_eligible_classes",
        "eligible_classes_all",
        "field_best_for",
        "remove_lane",
        "remove_lane_confirm",
        "remove_lane_yes",
        "err_invalid_name",
        "err_duplicate_name",
        "err_level_order",
        "err_empty_model",
        "err_invalid_number",
        "err_required",
        "err_no_enabled_lane",
        "add_lane",
        "add_level",
        "changes_title",
        "changes_none",
        "change_added",
        "change_removed",
        "change_renamed",
        "change_fields",
        "change_reordered",
        "model_unknown",
        "tree_title",
        "tree_loading",
        "tree_unavailable",
        "tree_empty",
        "project_label",
        "project_strip",
        "project_missing",
        "project_switch_confirm",
        "project_switch_yes",
        "discover_projects",
        "add_project",
        "discover_title",
        "discover_help",
        "discover_base",
        "discover_scan",
        "discover_scanning",
        "discover_found",
        "discover_none",
        "discover_registered",
        "discover_register",
        "discover_registered_count",
        "add_project_title",
        "add_project_path",
        "add_project_submit",
        "add_project_done",
        "project_request_failed",
        "save_all",
        "save_all_confirm",
        "save_all_result_title",
        "save_all_summary",
        "save_all_state_saved",
        "save_all_state_run_active",
        "save_all_state_missing",
        "save_all_state_failed",
        "save_all_yes",
    )
    return {key: t(f"cli.config.web.{key}") for key in keys}


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


@app.route("/timeline")
def timeline():
    now = datetime.now(timezone.utc)
    _touch_call_time()
    _touch_project_state(now)
    return render_template(
        "timeline.html",
        messages=_timeline_messages(),
        language=get_language(),
    )


@app.route("/config")
def config_page():
    return render_template(
        "config.html",
        messages=_config_messages(),
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


@app.route("/api/timelines")
def api_timelines():
    now = datetime.now(timezone.utc)
    _touch_call_time()
    _touch_project_state(now)
    try:
        limit = min(_query_int("limit", 20), 100)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if limit < 0:
        return jsonify({"error": t("reports.events_integer", name="limit")}), 400

    log_file = get_log_file()
    project_root = _current_project_root() or os.path.dirname(os.path.abspath(log_file))
    events = _read_events()
    stale_after = stale_after_from_config()
    runs = list_runs(
        events,
        now=now,
        stale_after_s=stale_after.total_seconds(),
    )
    if not os.path.isfile(log_file) or not runs or limit == 0:
        return jsonify({"runs": []})

    tier_prices, credit_prices = price_tables_from_config()
    via_index = via_index_from_config()
    timelines = []
    for run in runs[:limit]:
        timeline = build_timeline(
            events,
            str(run["run_id"]),
            now,
            tier_prices=tier_prices,
            credit_prices=credit_prices,
            stale_after=stale_after,
        )
        graph = build_graph(timeline, via_index, now, events=events)
        timelines.append(
            timeline_to_dict(timeline, graph, project=project_root, now=now)
        )
    return jsonify({"runs": timelines})


def _current_project_path() -> str:
    """Pasta do projeto servido pelo dashboard; e o projeto que a API usa sem identificador."""
    return _current_project_root() or os.getcwd()


def _project_config_path(project: Dict[str, Any]) -> str:
    """Caminho do meister.config.yaml do projeto escolhido (sempre um do registro)."""
    return os.path.join(project["path"], "meister.config.yaml")


def _is_remote_request() -> bool:
    """Pedido chegou pelo Tailscale (o `tailscale serve` entrega de 127.0.0.1, entao o IP nao basta)."""
    headers = request.headers
    if headers.get("Tailscale-User-Login"):
        return True
    has_tailscale = any(name.lower().startswith("tailscale-") for name in headers.keys())
    return bool(headers.get("X-Forwarded-For")) and has_tailscale


def _remote_edit_allowed() -> bool:
    """Pedido remoto com `Tailscale-User-Login` na lista `dashboard.remote_edit_logins` (sem diferenciar maiusculas)."""
    if not _is_remote_request():
        return False
    login = (request.headers.get("Tailscale-User-Login") or "").strip().casefold()
    if not login:
        return False
    try:
        logins = load_config().dashboard.remote_edit_logins
    except Exception:
        return False
    return any(entry.strip().casefold() == login for entry in logins)


def _remote_refused() -> bool:
    """Pedido remoto que nao esta autorizado a gravar: fica so leitura e a escrita e recusada."""
    return _is_remote_request() and not _remote_edit_allowed()


# Mensagens da escrita do /config; registradas nos catalogos do i18n ao importar este modulo.
_CONFIG_API_MESSAGES: Dict[str, Dict[str, str]] = {
    "en": {
        "reports.dashboard.config_csrf": (
            "Send the request as application/json with the header X-Meister-Config: 1."
        ),
        "reports.dashboard.config_invalid_json": "The request body is not valid JSON.",
        "reports.dashboard.config_remote": "Editing is not allowed through remote access.",
        "reports.dashboard.config_run_active": "A run is in progress; editing is blocked until it ends.",
        "reports.dashboard.config_body_object": "The request body must be an object.",
        "reports.dashboard.config_stamp_required": "stamp must be the text returned by GET /api/config.",
        "reports.dashboard.models_missing_harness": "Pass the harness name in the harness query parameter.",
        "reports.dashboard.config_unknown_project": "The project is not registered in the dashboard.",
        "reports.dashboard.config_project_missing": "The project folder no longer exists.",
        "reports.dashboard.replicate_skipped_run_active": "A run is in progress in this project; its lanes were not changed.",
        "reports.dashboard.replicate_skipped_missing": "The project folder no longer exists; nothing was written.",
    },
    "pt-BR": {
        "reports.dashboard.config_csrf": (
            "Envie o pedido como application/json com o cabe\u00e7alho X-Meister-Config: 1."
        ),
        "reports.dashboard.config_invalid_json": "O corpo do pedido n\u00e3o \u00e9 um JSON v\u00e1lido.",
        "reports.dashboard.config_remote": "A edi\u00e7\u00e3o n\u00e3o \u00e9 permitida pelo acesso remoto.",
        "reports.dashboard.config_run_active": (
            "H\u00e1 uma run em andamento; a edi\u00e7\u00e3o fica bloqueada at\u00e9 ela terminar."
        ),
        "reports.dashboard.config_body_object": "O corpo do pedido deve ser um objeto.",
        "reports.dashboard.config_stamp_required": "stamp deve ser o texto devolvido por GET /api/config.",
        "reports.dashboard.models_missing_harness": "Informe o nome do harness no par\u00e2metro harness.",
        "reports.dashboard.config_unknown_project": (
            "O projeto n\u00e3o est\u00e1 registrado no dashboard."
        ),
        "reports.dashboard.config_project_missing": "A pasta do projeto n\u00e3o existe mais.",
        "reports.dashboard.replicate_skipped_run_active": (
            "H\u00e1 uma run em andamento neste projeto; as vias n\u00e3o foram alteradas."
        ),
        "reports.dashboard.replicate_skipped_missing": (
            "A pasta do projeto n\u00e3o existe mais; nada foi gravado."
        ),
    },
}
for _language, _messages in _CONFIG_API_MESSAGES.items():
    for _key, _value in _messages.items():
        CATALOGS[_language].setdefault(_key, _value)


def _config_payload(document: LaneDocument, project: Dict[str, Any]) -> Dict[str, Any]:
    reason: Optional[str] = None
    if _project_has_active_run(project):
        reason = "run_active"
    elif _remote_refused():
        reason = "remote"
    return {
        "project": {"id": project["id"], "name": project["name"], "path": project["path"]},
        "lanes": document.lanes,
        "source": document.path,
        "has_tier_order": document.has_tier_order,
        "stamp": document.stamp,
        "editable": reason is None,
        "reason": reason,
    }


@app.route("/api/config")
def api_config():
    project, error = _resolve_config_project(request.args.get("project"))
    if error is not None or project is None:
        return error
    return jsonify(_config_payload(load_effective_lanes(_project_config_path(project)), project))


@app.route("/api/config/models")
def api_config_models():
    """Modelos de um harness para os seletores do /config; leitura, sem cabecalhos de escrita."""
    harness = (request.args.get("harness") or "").strip()
    if not harness:
        return _config_error(400, "missing_harness", t("reports.dashboard.models_missing_harness"))
    return jsonify(list_models(harness))


def _config_error(status: int, reason: str, message: str, issues: Optional[List[ConfigIssue]] = None):
    body: Dict[str, Any] = {"error": message, "reason": reason}
    if issues is not None:
        fields: Dict[str, List[str]] = {}
        for issue in issues:
            fields.setdefault(issue.path, []).append(issue.message)
        body["issues"] = [
            {"level": issue.level, "path": issue.path, "message": issue.message} for issue in issues
        ]
        body["fields"] = fields
    return jsonify(body), status


def _config_write_guard() -> Tuple[Any, Dict[str, Any], LaneDocument, Optional[Dict[str, Any]]]:
    """Checagens comuns da escrita, na ordem do spec: CSRF, remoto, projeto, run ativa, corpo, carimbo.

    Devolve (resposta de erro ou None, corpo do pedido, documento atual, projeto escolhido).
    O projeto vem do campo `project` do corpo; sem ele, e o projeto atual.
    """
    document = LaneDocument()
    if request.mimetype != "application/json" or request.headers.get("X-Meister-Config") != "1":
        return _config_error(400, "csrf", t("reports.dashboard.config_csrf")), {}, document, None
    if _remote_refused():
        return _config_error(403, "remote", t("reports.dashboard.config_remote")), {}, document, None
    raw = request.get_json(silent=True)
    project, error = _resolve_config_project(raw.get("project") if isinstance(raw, dict) else None)
    if error is not None or project is None:
        return error, {}, document, None
    if _project_has_active_run(project):
        return _config_error(409, "run_active", t("reports.dashboard.config_run_active")), {}, document, project
    body = raw
    if body is None:
        return _config_error(400, "invalid_json", t("reports.dashboard.config_invalid_json")), {}, document, project
    if not isinstance(body, dict):
        message = t("reports.dashboard.config_body_object")
        issue = ConfigIssue("error", "body", message)
        return _config_error(422, "invalid", message, [issue]), {}, document, project
    stamp = body.get("stamp")
    if not isinstance(stamp, str) or not stamp:
        message = t("reports.dashboard.config_stamp_required")
        issue = ConfigIssue("error", "stamp", message)
        return _config_error(422, "invalid", message, [issue]), body, document, project
    document = load_effective_lanes(_project_config_path(project))
    if document.stamp != stamp:
        return _config_error(409, "stale", t("config_editor.stamp_mismatch")), body, document, project
    return None, body, document, project


def _config_write_failure(exc: ConfigEditorError):
    if isinstance(exc, ConfigStampError):
        return _config_error(409, "stale", str(exc))
    if isinstance(exc, ConfigValidationError):
        return _config_error(422, "invalid", str(exc), exc.issues)
    return _config_error(500, "write_failed", str(exc))


@app.route("/api/config/lanes", methods=["PUT"])
def api_config_lanes():
    error, body, document, project = _config_write_guard()
    if error is not None or project is None:
        return error
    lanes = body.get("lanes")
    issues = [issue for issue in validate_lanes(document, lanes) if issue.level == "error"]
    if issues:
        return _config_error(422, "invalid", ConfigValidationError(issues).args[0], issues)
    try:
        updated = apply_lanes(document, lanes, body["stamp"])
    except ConfigEditorError as exc:
        return _config_write_failure(exc)
    return jsonify(_config_payload(updated, project))


@app.route("/api/config/restore-default", methods=["POST"])
def api_config_restore_default():
    error, body, document, project = _config_write_guard()
    if error is not None or project is None:
        return error
    try:
        updated = restore_default_lanes(document, body["stamp"])
    except ConfigEditorError as exc:
        return _config_write_failure(exc)
    return jsonify(_config_payload(updated, project))


def _replicate_to(target: Dict[str, Any], lanes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Grava a lista de vias num projeto do registro; falha de um destino nao levanta para o laco."""
    result = {"id": target["id"], "name": target["name"], "path": target["path"], "status": "saved", "message": ""}
    if not target["exists"]:
        return {**result, "status": "skipped_missing", "message": t("reports.dashboard.replicate_skipped_missing")}
    if _project_has_active_run(target):
        return {**result, "status": "skipped_run_active", "message": t("reports.dashboard.replicate_skipped_run_active")}
    try:
        document = load_effective_lanes(_project_config_path(target))
        issues = [issue for issue in validate_lanes(document, lanes) if issue.level == "error"]
        if issues:
            return {**result, "status": "failed", "message": issues[0].message}
        apply_lanes(document, lanes, document.stamp)
    except Exception as exc:  # ruamel.yaml levanta YAMLError fora de ConfigEditorError ao ler YAML invalido
        return {**result, "status": "failed", "message": str(exc)}
    return result


@app.route("/api/config/replicate", methods=["POST"])
def api_config_replicate():
    error, body, document, project = _config_write_guard()
    if error is not None or project is None:
        return error
    lanes = body.get("lanes")
    issues = [issue for issue in validate_lanes(document, lanes) if issue.level == "error"]
    if issues:
        return _config_error(422, "invalid", ConfigValidationError(issues).args[0], issues)
    try:
        updated = apply_lanes(document, lanes, body["stamp"])
    except ConfigEditorError as exc:
        return _config_write_failure(exc)
    # As vias casam pelo nome no destino; original_name so existe no pedido da origem.
    copies = [{key: value for key, value in lane.items() if key != "original_name"} for lane in body["lanes"]]
    results = [
        _replicate_to(target, copies)
        for target in list_projects()
        if target["id"] != project["id"]
    ]
    return jsonify({"source": _config_payload(updated, project), "results": results})


# Mensagens da API de projetos; registradas nos catalogos do i18n ao importar este modulo.
_PROJECT_API_MESSAGES: Dict[str, Dict[str, str]] = {
    "en": {
        "reports.dashboard.project_not_absolute": "The path must be an absolute path.",
        "reports.dashboard.project_not_a_directory": "The path is not an existing folder.",
        "reports.dashboard.project_not_a_project": "The folder has no .meister/, meister.config.yaml or .git.",
        "reports.dashboard.project_forbidden": "Your personal folder and the filesystem root cannot be projects.",
        "reports.dashboard.projects_body_paths": "paths must be a non-empty list of path strings.",
    },
    "pt-BR": {
        "reports.dashboard.project_not_absolute": "O caminho deve ser absoluto.",
        "reports.dashboard.project_not_a_directory": "O caminho n\u00e3o \u00e9 uma pasta existente.",
        "reports.dashboard.project_not_a_project": (
            "A pasta n\u00e3o tem .meister/, meister.config.yaml nem .git."
        ),
        "reports.dashboard.project_forbidden": (
            "A pasta pessoal e a raiz do sistema n\u00e3o podem ser projetos."
        ),
        "reports.dashboard.projects_body_paths": (
            "paths deve ser uma lista n\u00e3o vazia de caminhos."
        ),
    },
}
for _language, _messages in _PROJECT_API_MESSAGES.items():
    for _key, _value in _messages.items():
        CATALOGS[_language].setdefault(_key, _value)


def _current_project_id() -> str:
    return project_id(_current_project_path())


def _project_log_has_active_run(project_path: str) -> bool:
    """Run ativa lida do log do projeto; log ausente ou ilegível conta como sem run."""
    log_file = os.path.join(project_path, ".meister", "logs", "orchestration_log.jsonl")
    try:
        events = list(iter_events(log_file))
    except OSError:
        return False
    return _events_have_active_run(events)


def _project_has_active_run(project: Dict[str, Any]) -> bool:
    """Run ativa do projeto escolhido; o projeto atual continua pelo log do dashboard."""
    if project["id"] == _current_project_id():
        return _has_active_run()
    return _project_log_has_active_run(project["path"])


def _resolve_config_project(identifier: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Any]:
    """Projeto do pedido: sem identificador e o atual; com ele, so um projeto do registro.

    Devolve (projeto ou None, resposta de erro ou None).
    """
    if identifier is None:
        root = _current_project_path()
        return {"id": project_id(root), "name": os.path.basename(root.rstrip(os.sep)) or root, "path": root}, None
    project = get_project(identifier)
    if project is None:
        return None, _config_error(404, "unknown_project", t("reports.dashboard.config_unknown_project"))
    if not project["exists"]:
        return None, _config_error(409, "project_missing", t("reports.dashboard.config_project_missing"))
    return project, None


def _project_payload(project: Dict[str, Any]) -> Dict[str, Any]:
    active = False
    if project["exists"]:
        active = _project_has_active_run(project)
    return {
        "id": project["id"],
        "name": project["name"],
        "path": project["path"],
        "exists": project["exists"],
        "active_run": active,
    }


def _projects_write_guard() -> Tuple[Optional[Any], Dict[str, Any]]:
    """Checagens da escrita de projetos: CSRF, remoto e corpo JSON.

    Nao bloqueia por run ativa: o registro nao grava dentro do projeto.
    Devolve (resposta de erro ou None, corpo do pedido).
    """
    if request.mimetype != "application/json" or request.headers.get("X-Meister-Config") != "1":
        return _config_error(400, "csrf", t("reports.dashboard.config_csrf")), {}
    if _remote_refused():
        return _config_error(403, "remote", t("reports.dashboard.config_remote")), {}
    body = request.get_json(silent=True)
    if body is None:
        return _config_error(400, "invalid_json", t("reports.dashboard.config_invalid_json")), {}
    if not isinstance(body, dict):
        return _config_error(422, "invalid", t("reports.dashboard.config_body_object")), {}
    return None, body


def _project_error(exc: ProjectError):
    return _config_error(422, exc.code, t(f"reports.dashboard.project_{exc.code}"))


@app.route("/api/projects")
def api_projects():
    return jsonify({
        "projects": [_project_payload(project) for project in list_projects()],
        "current": _current_project_id(),
    })


@app.route("/api/projects", methods=["POST"])
def api_projects_add():
    error, body = _projects_write_guard()
    if error is not None:
        return error
    try:
        entry = register_project(body.get("path", ""))
    except ProjectError as exc:
        return _project_error(exc)
    return jsonify({"project": _project_payload(entry)})


@app.route("/api/projects/discover", methods=["POST"])
def api_projects_discover():
    error, body = _projects_write_guard()
    if error is not None:
        return error
    try:
        candidates = discover_projects(body.get("base", ""))
    except ProjectError as exc:
        return _project_error(exc)
    return jsonify({"candidates": candidates})


@app.route("/api/projects/register", methods=["POST"])
def api_projects_register():
    error, body = _projects_write_guard()
    if error is not None:
        return error
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        return _config_error(422, "invalid", t("reports.dashboard.projects_body_paths"))
    try:
        entries = register_projects(paths)
    except ProjectError as exc:
        return _project_error(exc)
    return jsonify({"projects": [_project_payload(entry) for entry in entries]})


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _events_have_active_run(events: List[Dict[str, Any]]) -> bool:
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


def _has_active_run() -> bool:
    return _events_have_active_run(_read_events())


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
    try:
        register_project(project_root)
    except Exception:
        pass

    if getattr(app.run, "__func__", None) is not Flask.run:
        actual_port = port if port != 0 else _find_free_port()
        now_str = format_iso_utc(datetime.now(timezone.utc))
        state = ServerState(
            pid=os.getpid(),
            port=actual_port,
            url=f"http://{host}:{actual_port}",
            started_at=now_str,
            last_seen=now_str,
            code_id=current_code_id(),
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
        code_id=current_code_id(),
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
