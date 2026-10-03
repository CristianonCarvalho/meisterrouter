import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from meister.dashboard.metrics import compute_summary, iter_events, list_runs, query_events
from meister.dashboard.server import app, start_server
from meister.logger import find_project_root, get_log_dir


def event(kind, run="run-a", task="task-1", ts="2026-01-01T00:00:00+00:00", **fields):
    return {
        "event_type": kind,
        "run_id": run,
        "task_id": task,
        "ts": ts,
        **fields,
    }


def test_summary_groups_retries_and_uses_subtask_events():
    events = []
    for index in range(15):
        task_id = f"task-{index:02}"
        for attempt in range(2 if index < 14 else 1):
            events.append(event(
                "classify", task=task_id, classification="SMALL" if attempt == 0 else "MEDIUM",
                cost=0.001, duration_ms=10, tokens_in=2, tokens_out=3,
            ))
        events.append(event("worker_spawn", task=task_id, tier="copilot", ts="2026-01-01T00:00:00+00:00"))
        if index < 7:
            events.append(event("worker_retry", task=task_id, tier="copilot"))
            events.append(event("worker_spawn", task=task_id, tier="codex", ts="2026-01-01T00:00:01+00:00"))
        terminal_type = "subtask_completed" if index < 12 else (
            "subtask_reused" if index == 12 else "subtask_rejected" if index == 13 else "worker_timeout"
        )
        fields = {"cost": 0.004} if terminal_type == "subtask_completed" else {"reason": "synthetic failure"}
        events.append(event(
            terminal_type,
            task=task_id,
            tier="codex",
            ts="2026-01-01T00:00:05+00:00",
            **fields,
        ))
    # Fourteen retries produce 29 classification calls for 15 distinct tasks.
    events.extend([
        event("control", task="orchestrator", cost=0.002, duration_ms=30, tokens_in=1, tokens_out=1),
        event("route_decision", task="task-00", status="decided", fallback_rule_applied=False),
        event("route_decision", task="task-01", status="fallback", fallback_rule_applied=True),
        event("route_decision", task="task-02", status="skipped_unavailable", fallback_rule_applied=True),
        event("route_decision", task="task-03", status="unavailable", fallback_rule_applied=True),
    ])
    summary = compute_summary(events, "run-a")

    assert summary["totals"] == {
        "tasks_total": 15, "completed": 12, "failed": 2, "reused": 1, "running": 0,
        "avg_duration_s": 5.0,
    }
    assert summary["jev"]["classify_calls"] == 29
    assert summary["jev"]["control_calls"] == 1
    assert summary["jev"]["classification_by_task"]["MEDIUM"] == 14
    assert summary["jev"]["classification_by_task"]["SMALL"] == 1
    assert summary["jev"]["classification_by_call"]["SMALL"] == 15
    assert summary["jev"]["route_by_status"] == {
        "decided": 1, "fallback": 1, "cooldown": 1, "unavailable": 1,
    }
    assert summary["jev"]["avg_latency_ms"] == 10.67
    assert summary["jev"]["cost_usd"] == pytest.approx(0.031)
    assert summary["workers"]["cost_usd"] == pytest.approx(0.048)
    task_zero = next(task for task in summary["tasks"] if task["task_id"] == "task-00")
    assert task_zero["attempts"] == 2
    assert task_zero["retries"] == 1
    assert task_zero["duration_s"] == 5
    assert task_zero["classification"] == "MEDIUM"
    assert next(task for task in summary["tasks"] if task["task_id"] == "task-13")["failure"] == "synthetic failure"
    assert summary["savings"] is None
    assert "savings_usd" not in summary and "savings_pct" not in summary


def test_summary_run_selection_unknown_and_zero_worker_costs():
    events = [
        event("orchestration_start", task="orchestrator", ts="2026-01-01T00:00:00+00:00"),
        event("worker_spawn", task="older", ts="2026-01-01T00:00:01+00:00"),
        event("subtask_completed", task="older", ts="2026-01-01T00:00:02+00:00", cost=0),
        event("orchestration_start", run="run-b", task="orchestrator", ts="2026-01-02T00:00:00+00:00"),
        event("subtask_reused", run="run-b", task="newer", ts="2026-01-02T00:00:01+00:00"),
        event("orchestration_end", run="run-b", task="orchestrator", status="completed"),
    ]
    assert [row["run_id"] for row in list_runs(events)] == ["run-b", "run-a"]
    assert compute_summary(events, None)["meta"]["run_id"] == "run-b"
    assert compute_summary(events, "run-b")["totals"]["reused"] == 1
    all_summary = compute_summary(events, "all")
    assert all_summary["totals"]["tasks_total"] == 2
    assert all_summary["workers"]["cost_usd"] is None
    assert compute_summary(events, "missing")["totals"]["tasks_total"] == 0


def test_query_events_filters_sorts_and_paginates():
    events = [
        event("worker_spawn", task="one", tier="codex", ts="2026-01-01T00:00:00+00:00", text="first"),
        event("worker_error", task="two", tier="copilot", ts="2026-01-02T00:00:00+00:00", text="bad <script>"),
        event("worker_spawn", run="run-b", task="one", tier="codex", ts="2026-01-03T00:00:00+00:00"),
    ]
    page = query_events(events, "run-a", task_id="two", event_type_filter="worker_error", tier="copilot", q="SCRIPT")
    assert page["total"] == 1
    assert page["events"][0]["text"] == "bad <script>"
    assert query_events(events, "run-a", task_id="one")["total"] == 1
    assert query_events(events, "run-a", limit=1, offset=1, order="asc")["events"][0]["task_id"] == "two"
    assert query_events(events, "run-a", limit=500)["limit"] == 500
    with pytest.raises(ValueError):
        query_events(events, "all", limit=501)
    with pytest.raises(ValueError):
        query_events(events, "all", offset=-1)
    with pytest.raises(ValueError):
        query_events(events, "all", order="sideways")


def test_iter_events_streams_all_valid_lines_and_skips_invalid(tmp_path, monkeypatch):
    path = tmp_path / "orchestration_log.jsonl"
    path.write_text("not json\n" + "".join(json.dumps({"n": n}) + "\n" for n in range(20_000)), encoding="utf-8")
    real_open = open
    read_count = {"lines": 0}

    class CountedFile:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.source.close()

        def __iter__(self):
            return self

        def __next__(self):
            line = next(self.source)
            read_count["lines"] += 1
            return line

    def counted_open(*args, **kwargs):
        return CountedFile(real_open(*args, **kwargs))

    monkeypatch.setattr("meister.dashboard.metrics.open", counted_open, raising=False)
    assert sum(1 for _ in iter_events(str(path))) == 20_000
    assert read_count["lines"] == 20_001


@pytest.fixture
def dashboard_client(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    client = app.test_client()
    return client, log_dir


def write_log(log_dir: Path, events):
    path = log_dir / "orchestration_log.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in events),
        encoding="utf-8",
    )
    return path


def test_api_meta_runs_summary_events_and_html(dashboard_client):
    client, log_dir = dashboard_client
    other_log = Path.cwd() / ".meister" / "logs" / "orchestration_log.jsonl"
    other_log.parent.mkdir()
    other_log.write_text('{"private":"synthetic only"}\n', encoding="utf-8")
    path = write_log(log_dir, [
        event("orchestration_start", task="orchestrator"),
        event("classify", task="task-1", classification="SMALL", context="<script>alert(1)</script>"),
        event("worker_spawn", task="task-1", tier="codex"),
        event("subtask_completed", task="task-1", tier="codex", ts="2026-01-01T00:00:05+00:00", cost=0.01),
        event("orchestration_end", task="orchestrator", status="completed"),
    ])
    meta = client.get("/api/meta").get_json()
    assert meta["exists"] is True
    assert meta["size_bytes"] == path.stat().st_size
    assert meta["lines"] == 5
    assert meta["other_logs"] == [str(other_log)]
    assert "synthetic only" not in json.dumps(meta)
    assert meta["project_root"] == str(Path.cwd())
    assert client.get("/api/runs").get_json()["runs"][0]["run_id"] == "run-a"
    summary = client.get("/api/summary").get_json()
    assert summary["totals"]["completed"] == 1
    assert summary["workers"]["cost_usd"] == 0.01
    event_result = client.get("/api/events?run_id=run-a&task_id=task-1&event_type=classify").get_json()
    assert event_result["total"] == 1
    assert event_result["events"][0]["context"] == "<script>alert(1)</script>"
    assert client.get("/").status_code == 200
    html = client.get("/").get_data(as_text=True)
    assert "Agrupado" in html and "Analítico" in html and "run-select" in html
    assert "innerHTML" not in html


@pytest.mark.parametrize("query", [
    "?limit=nope", "?offset=-1", "?limit=501", "?order=invalid",
])
def test_api_rejects_invalid_event_query(dashboard_client, query):
    client, _ = dashboard_client
    response = client.get(f"/api/events{query}")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_empty_and_missing_logs_are_safe(dashboard_client):
    client, log_dir = dashboard_client
    meta = client.get("/api/meta").get_json()
    assert meta["exists"] is False
    assert meta["size_bytes"] == 0 and meta["lines"] == 0
    assert client.get("/api/summary").get_json()["totals"]["tasks_total"] == 0
    assert client.get("/api/events").get_json()["events"] == []
    path = log_dir / "orchestration_log.jsonl"
    path.write_text("{bad json\n{}\n[]\n", encoding="utf-8")
    assert client.get("/api/runs").get_json()["runs"] == []
    assert client.get("/api/summary").status_code == 200


def test_cli_log_dir_and_missing_cwd_fallback(tmp_path, monkeypatch):
    from meister.cli import main
    from meister.dashboard import server

    captured = {}

    def start_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(server, "start_server", start_server)
    selected_dir = tmp_path / "custom-logs"
    result = CliRunner().invoke(main, ["dashboard", "--log-dir", str(selected_dir)])
    assert result.exit_code == 0, result.output
    assert captured["log_dir"] == str(selected_dir)

    with patch("meister.logger.os.getcwd", side_effect=FileNotFoundError):
        assert find_project_root() is None
        monkeypatch.delenv("MEISTER_LOG_DIR", raising=False)
        fallback_dir = tmp_path / "fallback-home" / ".meister" / "logs"
        monkeypatch.setattr("meister.logger.DEFAULT_LOG_DIR", str(fallback_dir))
        assert get_log_dir() == str(fallback_dir)


def test_server_uses_and_prints_selected_log_dir(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "selected-logs"
    monkeypatch.setattr(app, "run", lambda **kwargs: None)
    start_server(log_dir=str(log_dir))
    output = capsys.readouterr().out
    assert f"Lendo eventos de: {log_dir}/orchestration_log.jsonl" in output


def test_event_pagination_endpoint_and_invalid_integer(dashboard_client):
    client, log_dir = dashboard_client
    write_log(log_dir, [
        event("worker_spawn", task=f"task-{index}", ts=f"2026-01-01T00:00:{index:02d}+00:00")
        for index in range(55)
    ])
    first_page = client.get("/api/events?run_id=run-a&limit=50&offset=0&order=asc").get_json()
    second_page = client.get("/api/events?run_id=run-a&limit=50&offset=50&order=asc").get_json()
    assert first_page["total"] == 55 and len(first_page["events"]) == 50
    assert second_page["offset"] == 50 and len(second_page["events"]) == 5
    assert client.get("/api/events?limit=abc").status_code == 400


def test_route_decisions_use_the_statuses_the_bridge_really_emits():
    """A decisao normal do Jev nao tem `status`; `resumed` (retomada) NAO e uma decisao do Jev."""
    def route(task, **fields):
        return {"event_type": "route_decision", "run_id": "run-r", "task_id": task, "ts": "2026-01-01T00:00:00+00:00", **fields}

    events = [
        route("t1", fallback_rule_applied=False),
        route("t2", fallback_rule_applied=False),
        route("t3", status="resumed", fallback_rule_applied=False),
        route("t4", status="resumed", fallback_rule_applied=False),
        route("t5", status="fallback", fallback_rule_applied=True),
        route("t6", status="skipped_unavailable", fallback_rule_applied=True),
    ]
    assert compute_summary(events, "run-r")["jev"]["route_by_status"] == {
        "decided": 2, "resumed": 2, "fallback": 1, "cooldown": 1,
    }
