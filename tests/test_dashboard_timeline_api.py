from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

import pytest

import meister.dashboard.server as dashboard_server
from meister.dashboard.server import _touch_call_time, app, start_server
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


def test_timelines_api_returns_runs_newest_first_in_timeline_schema(dashboard_client):
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

    response = client.get("/api/timelines")
    assert response.status_code == 200
    data = response.get_json()
    assert [timeline["run"]["id"] for timeline in data["runs"]] == [
        "run-222222",
        "run-111111",
    ]

    single_run = client.get("/api/timeline?run_id=run-222222").get_json()
    assert data["runs"][0]["schema"] == single_run["schema"] == 1
    assert data["runs"][0]["rows"] == single_run["rows"]
    assert set(data["runs"][0]) == set(single_run)


def test_timelines_api_empty_log_returns_empty_list(dashboard_client):
    client, _, _ = dashboard_client
    response = client.get("/api/timelines")
    assert response.status_code == 200
    assert response.get_json() == {"runs": []}


def test_timelines_api_respects_default_limit_and_caps_at_100(dashboard_client):
    client, log_dir, _ = dashboard_client
    events = [
        {
            "event_type": "orchestration_start",
            "run_id": f"run-{index:06d}",
            "ts": f"2026-10-{(index // 24) + 1:02d}T{index % 24:02d}:00:00Z",
        }
        for index in range(105)
    ]
    _write_log(log_dir, events)

    default_response = client.get("/api/timelines").get_json()
    limited_response = client.get("/api/timelines?limit=2").get_json()
    capped_response = client.get("/api/timelines?limit=101").get_json()
    assert len(default_response["runs"]) == 20
    assert len(limited_response["runs"]) == 2
    assert len(capped_response["runs"]) == 100
    assert capped_response["runs"][0]["run"]["id"] == "run-000104"


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


def _safe_call(fn: Callable[[], object]) -> str:
    try:
        return repr(fn())
    except BaseException as exc:
        return f"erro ao chamar: {type(exc).__name__}: {exc}"


def _find_dashboard_json_files(roots: list[Path], max_items: int = 40, max_depth: int = 4) -> list[str]:
    found: list[str] = []
    for root in roots:
        root_depth = str(root).rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.rstrip(os.sep).count(os.sep) - root_depth >= max_depth:
                dirnames[:] = []
            if "dashboard.json" in filenames:
                found.append(os.path.realpath(os.path.join(dirpath, "dashboard.json")))
                if len(found) >= max_items:
                    return found
    return found


def _server_thread_stack(server_thread: threading.Thread, max_lines: int = 25, max_chars: int = 2000) -> str:
    """Pilha atual da thread (onde ela está parada), ou `(sem pilha)` se não houver."""
    try:
        if not server_thread.is_alive() or server_thread.ident is None:
            return "(sem pilha)"
        frame = sys._current_frames().get(server_thread.ident)
        if frame is None:
            return "(sem pilha)"
        stack = "".join(traceback.format_stack(frame))
    except Exception:
        return "(sem pilha)"
    lines = stack.splitlines()[-max_lines:]
    return "\n".join(lines)[-max_chars:]


def _describe_dashboard_failure(
    *,
    tmp_path: Path,
    project: Path,
    server_thread: threading.Thread,
    thread_errors: list[str],
) -> str:
    state_file = project / ".meister" / "dashboard.json"
    meister_dir = project / ".meister"
    found = _find_dashboard_json_files([tmp_path, tmp_path.parent])
    lines = [
        "diagnostico do dashboard:",
        "excecao capturada na thread: " + (thread_errors[0] if thread_errors else "nenhuma exceção"),
        f"thread viva: {server_thread.is_alive()}",
        f"cwd: {_safe_call(os.getcwd)}",
        f"project (realpath): {os.path.realpath(project)}",
        f"state_file (realpath): {os.path.realpath(state_file)}",
        f"MEISTER_LOG_DIR: {os.environ.get('MEISTER_LOG_DIR')!r}",
        f"_current_project_root(): {_safe_call(dashboard_server._current_project_root)}",
        f"get_log_file(): {_safe_call(dashboard_server.get_log_file)}",
        f"conteudo de project/.meister: {_safe_call(lambda: sorted(os.listdir(meister_dir)))}",
        f"arquivos dashboard.json sob tmp_path e pai ({len(found)}):",
    ]
    lines.extend(f"  {path}" for path in found)
    if not found:
        lines.append("  (nenhum)")
    lines.append("pilha da thread do servidor:")
    lines.append(_server_thread_stack(server_thread))
    return "\n".join(lines)


def _blocked_in_fake_getfqdn(entered: threading.Event, release: threading.Event) -> None:
    entered.set()
    release.wait(10.0)


def test_describe_dashboard_failure_includes_server_thread_stack(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    entered = threading.Event()
    release = threading.Event()
    blocked = threading.Thread(
        target=_blocked_in_fake_getfqdn, args=(entered, release), daemon=True
    )
    blocked.start()
    try:
        assert entered.wait(5.0), "a thread de teste não entrou na função bloqueante"
        text = _describe_dashboard_failure(
            tmp_path=tmp_path,
            project=project,
            server_thread=blocked,
            thread_errors=[],
        )
    finally:
        release.set()
        blocked.join(5.0)

    assert "pilha da thread do servidor:" in text
    assert "_blocked_in_fake_getfqdn" in text


def test_server_thread_stack_keeps_innermost_frame_when_truncated():
    entered = threading.Event()
    release = threading.Event()

    def _deep(depth: int) -> None:
        if depth == 0:
            _blocked_in_fake_getfqdn(entered, release)
        else:
            _deep(depth - 1)

    blocked = threading.Thread(target=_deep, args=(40,), daemon=True)
    blocked.start()
    try:
        assert entered.wait(5.0)
        full = _server_thread_stack(blocked, max_lines=500, max_chars=100_000)
        text = _server_thread_stack(blocked, max_lines=500, max_chars=300)
    finally:
        release.set()
        blocked.join(5.0)

    assert len(full) > 300
    assert len(text) <= 300
    # truncating keeps the END of the stack, where the thread is actually parked
    assert full.endswith(text)


def test_describe_dashboard_failure_reports_no_stack_for_finished_thread(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    finished = threading.Thread(target=lambda: None, daemon=True)
    finished.start()
    finished.join(5.0)

    text = _describe_dashboard_failure(
        tmp_path=tmp_path,
        project=project,
        server_thread=finished,
        thread_errors=[],
    )

    assert "pilha da thread do servidor:" in text
    assert "(sem pilha)" in text


def test_start_server_port_0_allocates_port_and_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    monkeypatch.chdir(project)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))

    # On macOS, HTTPServer.server_bind calls socket.getfqdn(host), a reverse DNS lookup
    # that can hang on runners without DNS. Replace it with a local stub that records calls.
    getfqdn_calls: list[str] = []

    def _fake_getfqdn(name: str = "") -> str:
        getfqdn_calls.append(name)
        return name or "localhost"

    monkeypatch.setattr(socket, "getfqdn", _fake_getfqdn)

    # The idle watchdog compares against the last request time, which can be stale
    # from earlier tests in the session; reset it so the idle window starts now.
    _touch_call_time()
    thread_errors: list[str] = []

    def _run_server() -> None:
        try:
            start_server(port=0, idle_exit_minutes=0.05)
        except BaseException:
            thread_errors.append(traceback.format_exc())

    # Run start_server in a thread with idle_exit_minutes to exit quickly
    # Or stop it via thread
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    def diag() -> str:
        return _describe_dashboard_failure(
            tmp_path=tmp_path,
            project=project,
            server_thread=server_thread,
            thread_errors=thread_errors,
        )

    # Wait for dashboard.json to appear (generous deadline for slow CI runners)
    state_file = project / ".meister" / "dashboard.json"
    t0 = time.monotonic()
    while time.monotonic() - t0 < 20.0 and not state_file.is_file():
        time.sleep(0.05)

    assert state_file.is_file(), diag()
    assert getfqdn_calls, "server_bind não chamou socket.getfqdn (o dublê não foi usado)"
    state = read_state(project)
    assert state is not None, diag()
    assert state.port > 0, diag()
    assert state.port != 5050, diag()
    assert f":{state.port}" in state.url, diag()
    assert state.pid == os.getpid(), diag()

    # Server should exit due to idle_exit_minutes
    server_thread.join(timeout=5.0)
    assert not server_thread.is_alive(), diag()
    # dashboard.json should be removed on exit
    assert not state_file.exists(), diag()



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


def test_timeline_html_has_run_controls_and_localized_keyboard_help(
    dashboard_client, monkeypatch
):
    client, _, _ = dashboard_client
    from meister.i18n import reset_language_cache

    monkeypatch.setenv("MEISTER_LANG", "en")
    reset_language_cache()
    html_en = client.get("/timeline").get_data(as_text=True)
    assert 'id="run-list"' in html_en
    assert 'id="mode-live"' in html_en
    assert 'id="mode-paused"' in html_en
    assert 'id="mode-all"' in html_en
    assert "Keyboard shortcuts" in html_en
    assert "Selected run" in html_en
    assert "All runs" in html_en
    assert "Pause" in html_en
    assert "Previous run" in html_en
    assert "Next run" in html_en

    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    reset_language_cache()
    html_pt = client.get("/timeline").get_data(as_text=True)
    assert "Teclas de atalho" in html_pt
    assert "Run selecionada" in html_pt
    assert "Todas as runs" in html_pt
    assert "Pausar" in html_pt


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




def test_timeline_html_hides_notice_banner_and_draws_wait_distinctly(dashboard_client):
    """O atributo hidden precisa vencer o display:flex do banner e a espera não pode usar a cor do worker."""
    client, _, _ = dashboard_client
    response = client.get("/timeline")
    html = response.get_data(as_text=True)

    assert ".notice[hidden] { display: none; }" in html
    assert 'seg.phase === "wait"' in html
    assert 'segFill = "#475569"' in html


def test_timeline_task_tooltips_are_visible_and_task_titles_have_tooltips(dashboard_client):
    client, _, _ = dashboard_client
    response = client.get("/timeline")
    html = response.get_data(as_text=True)

    assert ".tooltip {\n    position: fixed;" in html
    assert "event.clientX" in html
    assert "event.clientY" in html
    assert "event.pageX" not in html
    assert "event.pageY" not in html
    assert "window.innerWidth" in html
    assert "window.innerHeight" in html
    assert 'taskText.addEventListener("mouseenter"' in html
    assert 'taskText.addEventListener("mouseleave"' in html
    assert "rawTitle.length > 25" not in html
    assert "innerHTML" not in html
