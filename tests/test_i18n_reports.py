import re
from datetime import datetime, timedelta, timezone

import pytest

from meister.config import MeisterConfig, validate_config
from meister.dashboard.metrics import list_runs
from meister.dashboard.server import app
from meister.i18n import reset_language_cache
from meister.herdr.tui import render_tui_dashboard
from meister.report import compute_group_report, compute_run_report, render_report
from meister.timeline import build_timeline
from meister.timeline_view import render_frame, strip_ansi
from tests.timeline_fixtures import at, parallel_events


_ACCENTED = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")


@pytest.fixture
def language(monkeypatch):
    def set_language(value):
        monkeypatch.setenv("MEISTER_LANG", value)
        reset_language_cache()

    yield set_language
    reset_language_cache()


def test_reports_area_renders_english_and_preserves_portuguese(language, monkeypatch):
    config = MeisterConfig()
    config.router.mode = "invalid"
    config.retry.pane_lost_attempts = -1
    config.workers.tier_order = []
    config.workers.disabled = []

    events = [
        {
            "event_type": "orchestration_start",
            "run_id": "report-run",
            "task_id": "orchestrator",
            "ts": "2026-10-05T12:00:00+00:00",
            "task": "Sample plan",
        },
        {
            "event_type": "worker_spawn",
            "run_id": "report-run",
            "task_id": "task-1",
            "ts": "2026-10-05T12:00:01+00:00",
            "tier": "codex",
        },
        {
            "event_type": "subtask_completed",
            "run_id": "report-run",
            "task_id": "task-1",
            "ts": "2026-10-05T12:00:02+00:00",
            "tier": "codex",
        },
    ]
    monkeypatch.setattr("meister.dashboard.server._read_events", lambda: events)
    monkeypatch.setattr(
        "meister.dashboard.server.stale_after_from_config",
        lambda: timedelta(seconds=1),
    )

    language("en")
    issues = validate_config(config)
    issue_messages = [issue.message for issue in issues]
    report = compute_run_report(events, "report-run")
    table = render_report({"groups": [], "runs": [report]}, "table")
    markdown = render_report(
        {"groups": [compute_group_report("sample", [report])], "runs": []},
        "markdown",
    )
    timeline = build_timeline(parallel_events(), "r1", at(30))
    frames = [
        strip_ansi(
            render_frame(
                timeline,
                width=width,
                height=30,
                now=at(30),
                tz=timezone.utc,
            )
        )
        for width in (80, 100, 120)
    ]
    tui = render_tui_dashboard({}, {})
    client = app.test_client()
    dashboard = client.get("/").get_data(as_text=True)
    api_runs = client.get("/api/runs").get_json()["runs"]
    api_summary = client.get("/api/summary").get_json()
    api_error = client.get("/api/events?limit=0").get_json()["error"]

    english_outputs = [
        *issue_messages,
        *report["notes"],
        table,
        markdown,
        *frames,
        tui,
        dashboard,
        str(api_runs[0]["status"]),
        str(api_summary["meta"]["run"]["status"]),
        str(api_summary["report"]["notes"]),
        api_error,
    ]
    english_text = "\n".join(english_outputs)
    assert not _ACCENTED.search(english_text)
    for expected in (
        "invalid router.mode: 'invalid'. Valid values: first, jev",
        "must be >= 0 (-1)",
        "effective tier_order is empty (no lanes configured)",
        "run has no worker_phase (pre-E1 log): phase times not measured",
        "not measured",
        "## Groups",
        "Warning",
        "LIVE",
        "Completed",
        "Running",
        "[Q] close overlay | [O] open web dashboard | [T] timeline",
        "Orchestration telemetry",
        "Tasks",
        "limit must be between 1 and 500",
        "stalled",
    ):
        assert expected in english_text
    assert all(max(map(len, frame.splitlines())) <= width for frame, width in zip(frames, (80, 100, 120)))

    language("pt-BR")
    issues_pt = validate_config(config)
    report_pt = compute_run_report(events, "report-run")
    table_pt = render_report({"groups": [], "runs": [report_pt]}, "table")
    markdown_pt = render_report(
        {"groups": [compute_group_report("sample", [report_pt])], "runs": []},
        "markdown",
    )
    frame_pt = strip_ansi(
        render_frame(
            timeline,
            width=120,
            height=30,
            now=at(30),
            tz=timezone.utc,
        )
    )
    tui_pt = render_tui_dashboard({}, {})
    client_pt = app.test_client()
    dashboard_pt = client_pt.get("/").get_data(as_text=True)
    api_runs_pt = client_pt.get("/api/runs").get_json()["runs"]
    api_summary_pt = client_pt.get("/api/summary").get_json()
    api_error_pt = client_pt.get("/api/events?limit=0").get_json()["error"]

    assert "router.mode inválido: 'invalid'. Valores válidos: first, jev" in [
        issue.message for issue in issues_pt
    ]
    assert "deve ser >= 0 (-1)" in [issue.message for issue in issues_pt]
    assert "tier_order efetivo está vazio (nenhuma via configurada)" in [
        issue.message for issue in issues_pt
    ]
    assert "run sem worker_phase (log anterior à E1): tempo por fase não medido" in report_pt["notes"]
    assert "não medido" in table_pt
    assert "## Grupos" in markdown_pt and "Aviso" in markdown_pt
    assert "AO VIVO" in frame_pt and "Concluídas" in frame_pt and "Rodando 1" in frame_pt
    assert "[Q] fechar overlay | [O] abrir dashboard web | [T] linha do tempo" in tui_pt
    assert "Telemetria de orquestração" in dashboard_pt
    assert "Tarefas" in dashboard_pt
    assert api_runs_pt[0]["status"] == "sem sinal"
    assert api_summary_pt["meta"]["run"]["status"] == "sem sinal"
    assert api_error_pt == "limit deve estar entre 1 e 500"


def test_stale_run_status_uses_active_language(language):
    events = [
        {
            "event_type": "orchestration_start",
            "run_id": "stale-run",
            "task_id": "orchestrator",
            "ts": "2026-10-01T00:00:00+00:00",
        }
    ]
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)

    language("en")
    assert list_runs(events, now=now, stale_after_s=3600)[0]["status"] == "stalled"
    language("pt-BR")
    assert list_runs(events, now=now, stale_after_s=3600)[0]["status"] == "sem sinal"
