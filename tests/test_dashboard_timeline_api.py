from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from meister.dashboard.server import app, start_server
from meister.dashboard.state import ServerState, read_state, write_state
from tests.timeline_fixtures import parallel_events


@pytest.fixture
def dashboard_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    client = app.test_client()
    return client, log_dir, project


def _write_log(log_dir: Path, events: list[dict]):
    log_path = log_dir / "orchestration_log.jsonl"
    with open(log_path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return log_path


def test_timeline_api_empty_log_returns_empty_skeleton(dashboard_client):
    client, log_dir, _ = dashboard_client
    resp = client.get("/api/timeline")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schema"] == 1
    assert data["run"] is None
    assert data["rows"] == []
    assert data["lanes"] == []


def test_timeline_api_two_runs_default_newest(dashboard_client):
    client, log_dir, _ = dashboard_client
    events1 = parallel_events(run="run-111111")
    # run-222222 has newer timestamps
    events2 = [
        {
            **ev,
            "run_id": "run-222222",
            "ts": ev["ts"].replace("2026-10-05T12:", "2026-10-05T13:"),
        }
        for ev in parallel_events(run="run-222222")
    ]
    _write_log(log_dir, [*events1, *events2])

    resp = client.get("/api/timeline")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schema"] == 1
    assert data["run"]["id"] == "run-222222"
    assert len(data["rows"]) == 3


def test_timeline_api_specific_run_id(dashboard_client):
    client, log_dir, _ = dashboard_client
    events1 = parallel_events(run="run-111111")
    events2 = [
        {
            **ev,
            "run_id": "run-222222",
            "ts": ev["ts"].replace("2026-10-05T12:", "2026-10-05T13:"),
        }
        for ev in parallel_events(run="run-222222")
    ]
    _write_log(log_dir, [*events1, *events2])

    resp = client.get("/api/timeline?run_id=run-111111")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["run"]["id"] == "run-111111"


def test_timeline_api_unknown_run_id_returns_404_json(dashboard_client):
    client, log_dir, _ = dashboard_client
    events = parallel_events(run="run-111111")
    _write_log(log_dir, events)

    resp = client.get("/api/timeline?run_id=run-999999")
    assert resp.status_code == 404
    data = resp.get_json()
    assert "error" in data


def test_timeline_api_unknown_run_id_on_empty_log_returns_404_json(dashboard_client):
    client, log_dir, _ = dashboard_client
    resp = client.get("/api/timeline?run_id=run-999999")
    assert resp.status_code == 404
    data = resp.get_json()
    assert "error" in data


def test_timeline_api_advances_last_seen(dashboard_client):
    client, log_dir, project = dashboard_client
    old_ts = "2026-01-01T00:00:00Z"
    state = ServerState(
        pid=os.getpid(),
        port=5050,
        url="http://127.0.0.1:5050",
        started_at=old_ts,
        last_seen=old_ts,
    )
    write_state(project, state)

    resp = client.get("/api/timeline")
    assert resp.status_code == 200

    updated = read_state(project)
    assert updated is not None
    assert updated.last_seen > old_ts
    assert updated.started_at == old_ts
    assert updated.port == 5050


def test_start_server_port_0_allocates_port_and_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    monkeypatch.chdir(project)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))

    # Run start_server in a thread with idle_exit_minutes to exit quickly
    # Or stop it via thread
    server_thread = threading.Thread(
        target=lambda: start_server(port=0, idle_exit_minutes=0.01),
        daemon=True,
    )
    server_thread.start()

    # Wait for dashboard.json to appear
    state_file = project / ".meister" / "dashboard.json"
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3.0 and not state_file.is_file():
        time.sleep(0.05)

    assert state_file.is_file()
    state = read_state(project)
    assert state is not None
    assert state.port > 0
    assert state.port != 5050
    assert f":{state.port}" in state.url
    assert state.pid == os.getpid()

    # Server should exit due to idle_exit_minutes
    server_thread.join(timeout=5.0)
    assert not server_thread.is_alive()
    # dashboard.json should be removed on exit
    assert not state_file.exists()


def test_timeline_api_run_without_plan_parsed(dashboard_client):
    client, log_dir, _ = dashboard_client
    _write_log(log_dir, [
        {"event_type": "orchestration_start", "run_id": "run-000001", "ts": "2026-10-08T00:00:00Z"},
    ])
    resp = client.get("/api/timeline")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schema"] == 1
    assert data["run"]["id"] == "run-000001"
    assert data["rows"] == []


def test_timeline_api_truncated_log(dashboard_client):
    client, log_dir, _ = dashboard_client
    log_path = log_dir / "orchestration_log.jsonl"
    log_path.write_text('{"event_type": "orchestration_start", "run_id": "r1"\n', encoding="utf-8")
    resp = client.get("/api/timeline")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schema"] == 1
    assert data["rows"] == []


def test_timeline_page_returns_200_and_localized_html(dashboard_client, monkeypatch):
    client, log_dir, _ = dashboard_client
    from meister.i18n import reset_language_cache

    monkeypatch.setenv("MEISTER_LANG", "en")
    reset_language_cache()
    resp_en = client.get("/timeline")
    assert resp_en.status_code == 200
    assert "text/html" in resp_en.content_type
    html_en = resp_en.get_data(as_text=True)
    assert "waiting for the first run" in html_en
    assert "Meister" in html_en

    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    reset_language_cache()
    resp_pt = client.get("/timeline")
    assert resp_pt.status_code == 200
    assert "text/html" in resp_pt.content_type
    html_pt = resp_pt.get_data(as_text=True)
    assert "aguardando a primeira run" in html_pt


def test_timeline_html_no_inner_html_and_no_cdn(dashboard_client):
    client, _, _ = dashboard_client
    resp = client.get("/timeline")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "innerHTML" not in html
    assert "outerHTML" not in html
    assert "document.write" not in html
    assert "cdn." not in html.lower()
    assert "unpkg.com" not in html.lower()
    assert "jsdelivr.net" not in html.lower()
    assert "cdnjs" not in html.lower()
    assert "https://" not in html


def test_timeline_html_uses_create_element_ns_and_text_content_for_xss_prevention(dashboard_client):
    client, log_dir, _ = dashboard_client
    malicious_title = '<img src=x onerror=alert("xss")>'
    _write_log(log_dir, [
        {"event_type": "orchestration_start", "run_id": "run-xss", "ts": "2026-10-08T00:00:00Z"},
        {
            "event_type": "plan_parsed",
            "run_id": "run-xss",
            "task_id": "orchestrator",
            "ts": "2026-10-08T00:00:01Z",
            "task_ids": ["t1"],
            "task_titles": {"t1": malicious_title},
        },
        {"event_type": "worker_spawn", "run_id": "run-xss", "task_id": "t1", "ts": "2026-10-08T00:00:02Z", "tier": "worker1"},
    ])
    api_resp = client.get("/api/timeline")
    assert api_resp.status_code == 200
    assert api_resp.get_json()["rows"][0]["title"] == malicious_title

    resp = client.get("/timeline")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "<img src=x onerror=" not in html
    assert "createElementNS" in html
    assert "textContent" in html
    assert "innerHTML" not in html


def test_timeline_page_with_real_run_log(dashboard_client):
    client, log_dir, project = dashboard_client
    events = parallel_events(run="run-sample")
    _write_log(log_dir, events)
    resp = client.get("/timeline?run_id=run-sample")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Meister" in html
    assert "/api/timeline" in html



