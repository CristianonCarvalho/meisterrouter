import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from meister.dashboard.metrics import clip_title, compute_summary, iter_events, list_runs, query_events
from meister.dashboard.server import app, project_from_log, start_server
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


def test_api_summary_prices_copilot_credits_and_survives_bad_config(dashboard_client, monkeypatch):
    from meister import config as config_module
    from meister.dashboard import server

    client, log_dir = dashboard_client
    cfg = config_module.MeisterConfig()
    cfg.workers.tier_order[0].name = "copilot_luna"
    cfg.workers.tier_order[0].credit_usd = 0.01
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    write_log(log_dir, [
        event("orchestration_start", task="orchestrator"),
        event(
            "subtask_completed",
            tier="copilot_luna",
            task="copilot",
            cost=0.46,
            cost_source="estimated",
            credits=7.16,
        ),
        event("orchestration_end", task="orchestrator", status="completed"),
    ])

    summary = client.get("/api/summary").get_json()
    assert summary["report"]["by_tier"]["copilot_luna"]["cost_known_usd"] == 0.0716

    monkeypatch.setattr(server, "load_config", lambda: (_ for _ in ()).throw(ValueError("bad config")))
    broken_config_summary = client.get("/api/summary").get_json()
    assert broken_config_summary["totals"]["completed"] == 1
    assert broken_config_summary["report"]["by_tier"]["copilot_luna"]["cost_known_usd"] == 0.46


def test_api_summary_reprices_with_current_catalog_and_sums_all_runs(dashboard_client, monkeypatch):
    from meister import config as config_module
    from meister.dashboard import server

    client, log_dir = dashboard_client
    cfg = config_module.MeisterConfig()
    gemini = next(t for t in cfg.workers.tier_order if t.name == "agy_gemini_flash")
    gemini.cost_per_m_tokens = 1.5
    cfg.workers.tier_order[0].name = "copilot_luna"
    cfg.workers.tier_order[0].credit_usd = 0.01
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    events = []
    for run, credits in (("run-a", 7.16), ("run-b", 3.42)):
        events += [
            event("orchestration_start", run=run, task="orchestrator"),
            event(
                "subtask_completed", run=run, tier="agy_gemini_flash", task="gem",
                tokens_in=288094, tokens_out=20571, tokens_total=308665,
                cost=0.1781, cost_source="estimated",
            ),
            event(
                "subtask_completed", run=run, tier="copilot_luna", task="cop",
                cost=0.46, cost_source="estimated", credits=credits,
            ),
            event("orchestration_end", run=run, task="orchestrator", status="completed"),
        ]
    write_log(log_dir, events)

    one = client.get("/api/summary?run_id=run-a").get_json()
    assert one["report"]["by_tier"]["agy_gemini_flash"]["cost_known_usd"] == 0.462998
    assert one["all_runs_cost"] is None

    everything = client.get("/api/summary?run_id=all").get_json()
    assert everything["report"] is None
    by_tier = everything["all_runs_cost"]["by_tier"]
    assert by_tier["agy_gemini_flash"]["cost_known_usd"] == 0.925996
    assert by_tier["copilot_luna"]["cost_known_usd"] == 0.1058
    assert by_tier["copilot_luna"]["events_unknown"] == 0


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


def _real_style_run(classify_ids):
    """Run no formato REAL de um log: workers com id logico (task_N); `classify_ids` define o id dos eventos do Jev."""
    events = [event("orchestration_start", task="orchestrator")]
    for number in (1, 2, 3):
        logical = f"task_{number}"
        jev_id = classify_ids(number)
        events.append(event("classify", task=jev_id, classification="MEDIUM", duration_ms=10, cost_usd=0.001))
        events.append(event("route_decision", task=jev_id, tier="copilot_luna", fallback_rule_applied=False))
        events.append(event("worker_spawn", task=logical, tier="copilot_luna", ts="2026-01-01T00:00:00+00:00"))
        events.append(event("subtask_completed", task=logical, tier="copilot_luna", ts="2026-01-01T00:00:10+00:00"))
    # julgamento final do Jev: `control` com id em hash (nao e uma tarefa do plano)
    events.append(event("control", task="8e871068ba4faee4", action="COMPLETE", duration_ms=5, cost_usd=0.002))
    events.append(event("orchestration_end", task="orchestrator", status="completed"))
    return events


def test_jev_events_with_hash_ids_do_not_create_phantom_tasks():
    """Logs antigos: classify/route com o HASH da subtarefa. Nao viram tarefas 'unknown'; sao contados como chamadas sem tarefa."""
    events = _real_style_run(lambda n: f"{n:016x}")
    summary = compute_summary(events, "run-a")
    assert summary["totals"]["tasks_total"] == 3
    assert summary["totals"]["completed"] == 3
    assert {task["task_id"] for task in summary["tasks"]} == {"task_1", "task_2", "task_3"}
    assert all(task["status"] == "completed" and task["jev_calls"] == 0 for task in summary["tasks"])
    assert summary["jev"]["classify_calls"] == 3
    assert summary["jev"]["unlinked_classify_calls"] == 3
    assert summary["jev"]["control_calls"] == 1
    assert list_runs(events)[0]["tasks"] == 3


def test_jev_events_with_logical_ids_link_to_their_task():
    """Logs novos: classify/route com o id logico: ligam-se a tarefa (chamadas por tarefa e classificacao por tarefa)."""
    events = _real_style_run(lambda n: f"task_{n}")
    summary = compute_summary(events, "run-a")
    assert summary["totals"]["tasks_total"] == 3
    assert all(task["jev_calls"] == 1 and task["classification"] == "MEDIUM" for task in summary["tasks"])
    assert summary["jev"]["unlinked_classify_calls"] == 0
    assert summary["jev"]["classification_by_task"] == {"MEDIUM": 3}
    assert list_runs(events)[0]["tasks"] == 3


def test_dashboard_template_keeps_the_events_table_readable_and_safe():
    """Regressao visual: colunas curtas fixas e em uma linha (o `overflow-wrap: anywhere` global as reduzia a 1 caractere)."""
    html = (Path(__file__).resolve().parents[1] / "meister" / "dashboard" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'class="events-table"' in html
    assert "table-layout: fixed" in html
    assert "white-space: nowrap" in html
    assert "event-details" in html and "JSON completo" in html
    assert "innerHTML" not in html and "outerHTML" not in html
    assert "classify sem tarefa associada" in html


def test_dashboard_template_shows_run_column_only_in_all_runs_view():
    """Em "Todos os runs" os task_ids se repetem entre runs: a coluna Run diferencia as linhas."""
    html = (Path(__file__).resolve().parents[1] / "meister" / "dashboard" / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<th id="run-col" hidden>Run</th>' in html
    assert 'renderTasks(summary.tasks, summary.meta.run_id === "all")' in html
    assert "runCell(task.run_id)" in html


def test_query_events_breaks_timestamp_ties_by_log_position():
    stamp = "2026-10-03T17:00:00+00:00"
    events = [
        {"ts": stamp, "event": "first", "run_id": "r"},
        {"ts": stamp, "event": "second", "run_id": "r"},
        {"ts": "2026-10-03T16:00:00+00:00", "event": "older", "run_id": "r"},
        {"ts": "", "event": "no_ts", "run_id": "r"},
    ]

    newest_first = query_events(events, "r", order="desc")["events"]
    oldest_first = query_events(events, "r", order="asc")["events"]

    assert [e["event"] for e in newest_first] == ["second", "first", "older", "no_ts"]
    assert [e["event"] for e in oldest_first] == ["no_ts", "older", "first", "second"]


def test_clip_title_skips_plan_header_and_punctuation_lines():
    assert clip_title("\nPlan:\n{\n  Migrar auth para OAuth2\n") == "Migrar auth para OAuth2"
    assert clip_title("a" * 150, 20) == "a" * 19 + "…"
    assert clip_title("") == ""
    assert clip_title(None) == ""


def test_runs_and_tasks_expose_human_titles_from_the_log():
    events = [
        {**event("orchestration_start", task="orchestrator", ts="2026-01-01T00:00:00+00:00"),
         "task": "\nPlan:\nMigrar auth"},
        event("plan_parsed", task="orchestrator", ts="2026-01-01T00:00:01+00:00",
              task_ids=["t1", "t2"], task_titles={"t1": "Criar rota", "t2": ""}),
        event("worker_spawn", task="t1", ts="2026-01-01T00:00:02+00:00"),
        event("worker_spawn", task="t2", ts="2026-01-01T00:00:03+00:00"),
        event("worker_spawn", task="t1", run="outro", ts="2026-01-01T00:00:04+00:00"),
    ]

    runs = {row["run_id"]: row for row in list_runs(events)}
    assert runs["run-a"]["title"] == "Migrar auth"
    assert runs["outro"]["title"] == ""

    summary = compute_summary(events, "run-a")
    titles = {task["task_id"]: task["title"] for task in summary["tasks"]}
    assert titles == {"t1": "Criar rota", "t2": ""}
    assert summary["meta"]["run"]["title"] == "Migrar auth"

    # o título é por run: o mesmo id em outro run não herda
    other = compute_summary(events, "outro")
    assert [task["title"] for task in other["tasks"]] == [""]

    assert query_events(events, "run-a")["task_titles"] == {"t1": "Criar rota"}
    assert query_events(events, "outro")["task_titles"] == {}
    assert query_events(events, "all")["task_titles"] == {"t1": "Criar rota"}


def test_project_from_log_uses_the_log_owner_not_the_cwd(tmp_path, monkeypatch):
    log = tmp_path / "CRM_Base" / ".meister" / "logs" / "orchestration_log.jsonl"
    assert project_from_log(str(log), "/outro/projeto") == {
        "name": "CRM_Base", "path": str(tmp_path / "CRM_Base"),
    }
    # layout fora do padrão: cai para o projeto do cwd, e sem ele para a pasta do log
    odd = tmp_path / "logs-soltos" / "orchestration_log.jsonl"
    assert project_from_log(str(odd), "/x/meu_projeto") == {"name": "meu_projeto", "path": "/x/meu_projeto"}
    assert project_from_log(str(odd))["name"] == "logs-soltos"
    # log global em ~/.meister
    monkeypatch.setenv("HOME", str(tmp_path))
    home_log = tmp_path / ".meister" / "logs" / "orchestration_log.jsonl"
    assert project_from_log(str(home_log))["name"] == "global (~/.meister)"


def test_clip_title_reads_description_from_truncated_json_plan():
    raw = (
        '[{"depends_on":[],"description":"Task fix_13: Parse \\"real\\" headers (bio, followers) '
        'and more text that keeps going and going and going and going and going'
    )
    title = clip_title(raw)
    assert title.startswith('Task fix_13: Parse "real" headers')
    assert title.endswith("…") and len(title) == 100
    # JSON sem description legível: sem título (o dashboard cai para o id), nunca o JSON cru
    assert clip_title('[{"depends_on":[],"desc') == ""
    assert clip_title('{"a": 1}') == ""


def test_api_summary_exposes_the_run_report_with_unknown_costs_and_phases(dashboard_client):
    client, log_dir = dashboard_client
    write_log(log_dir, [
        event("orchestration_start", task="orchestrator", ts="2026-01-01T00:00:00+00:00"),
        event("worker_spawn", task="a", tier="claude_sonnet", ts="2026-01-01T00:00:01+00:00"),
        event("worker_spawn", task="b", tier="copilot_luna", ts="2026-01-01T00:00:01+00:00"),
        event("worker_phase", task="a", tier="claude_sonnet", phase="worker", duration_ms=6000,
              ts="2026-01-01T00:00:07+00:00"),
        event("worker_phase", task="b", tier="copilot_luna", phase="worker", duration_ms=6000,
              ts="2026-01-01T00:00:07+00:00"),
        event("subtask_completed", task="a", tier="claude_sonnet", cost=0.05, cost_source="reported",
              tokens_in=100, tokens_out=4, ts="2026-01-01T00:00:08+00:00"),
        event("subtask_completed", task="b", tier="copilot_luna", cost=0.0, cost_source="unknown",
              credits=0.19, ts="2026-01-01T00:00:08+00:00"),
        event("orchestration_end", task="orchestrator", status="completed", ts="2026-01-01T00:00:10+00:00"),
    ])

    report = client.get("/api/summary").get_json()["report"]
    assert report["peak_parallel_workers"] == 2
    assert report["by_tier"]["claude_sonnet"]["cost_known_usd"] == 0.05
    assert report["by_tier"]["copilot_luna"]["cost_known_usd"] == 0.0019
    assert report["by_tier"]["copilot_luna"]["cost_reported_usd"] == 0.0019
    assert report["by_tier"]["copilot_luna"]["credits_usd"] == 0.0019
    assert report["by_tier"]["copilot_luna"]["events_unknown"] == 0
    assert report["by_tier"]["copilot_luna"]["credits"] == 0.19
    assert client.get("/api/summary?run_id=all").get_json()["report"] is None

    html = client.get("/").get_data(as_text=True)
    for element_id in ("phases", "report-notes", "summary-cards"):
        assert f'id="{element_id}"' in html
    assert "Overhead do orquestrador" in html and "Pico de workers" in html


def test_api_summary_report_for_an_old_log_reports_unmeasured(dashboard_client):
    client, log_dir = dashboard_client
    write_log(log_dir, [
        event("orchestration_start", task="orchestrator"),
        event("worker_spawn", task="a", tier="copilot_luna"),
        event("subtask_completed", task="a", tier="copilot_luna", cost=0.0),
        event("orchestration_end", task="orchestrator", status="completed", ts="2026-01-01T00:00:09+00:00"),
    ])
    report = client.get("/api/summary").get_json()["report"]
    assert report["overhead_ratio"] is None and report["peak_parallel_workers"] is None
    assert report["phase_seconds"]["worker"] is None
    assert report["by_tier"]["copilot_luna"]["events_unknown"] == 1
    assert any("worker_phase" in note for note in report["notes"])
