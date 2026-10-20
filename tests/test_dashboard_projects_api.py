from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meister.dashboard.server import app
from meister.projects import list_projects, project_id, register_project

JSON_HEADERS = {"X-Meister-Config": "1"}


def _project(root: Path, name: str, marker: str = ".meister") -> Path:
    project = root / name
    project.mkdir(parents=True)
    if marker == ".meister":
        (project / marker).mkdir()
    else:
        (project / marker).write_text("workers: {}\n", encoding="utf-8")
    return project


def _write_log(project: Path, events: list) -> None:
    logs = project / ".meister" / "logs"
    logs.mkdir(parents=True)
    lines = [json.dumps(event) for event in events]
    (logs / "orchestration_log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture
def projects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    current = _project(tmp_path, "current")
    monkeypatch.chdir(current)
    register_project(str(current))
    return app.test_client(), current


def _post(client, url, body, headers=None):
    return client.post(url, json=body, headers=JSON_HEADERS if headers is None else headers)


def test_list_includes_registered_current_project(projects_env):
    client, current = projects_env
    data = client.get("/api/projects").get_json()
    current_id = project_id(str(current))
    assert data["current"] == current_id
    entry = next(item for item in data["projects"] if item["id"] == current_id)
    assert entry["name"] == "current"
    assert entry["exists"] is True
    assert entry["active_run"] is False


def test_register_adds_project_to_list(projects_env, tmp_path):
    client, _ = projects_env
    app_dir = _project(tmp_path, "app", marker=".git")
    resp = _post(client, "/api/projects", {"path": str(app_dir)})
    assert resp.status_code == 200
    assert resp.get_json()["project"]["id"] == project_id(str(app_dir))
    ids = [item["id"] for item in client.get("/api/projects").get_json()["projects"]]
    assert project_id(str(app_dir)) in ids


def test_discover_returns_candidates_without_registering(projects_env, tmp_path):
    client, _ = projects_env
    base = tmp_path / "workspace"
    base.mkdir()
    found = _project(base, "found")
    resp = _post(client, "/api/projects/discover", {"base": str(base)})
    assert resp.status_code == 200
    candidates = resp.get_json()["candidates"]
    assert [item["id"] for item in candidates] == [project_id(str(found))]
    assert candidates[0]["registered"] is False
    assert project_id(str(found)) not in [item["id"] for item in list_projects()]


def test_discover_then_register_chosen_paths(projects_env, tmp_path):
    client, _ = projects_env
    base = tmp_path / "workspace"
    base.mkdir()
    chosen = _project(base, "chosen")
    _project(base, "skipped")
    resp = _post(client, "/api/projects/register", {"paths": [str(chosen)]})
    assert resp.status_code == 200
    assert [item["id"] for item in resp.get_json()["projects"]] == [project_id(str(chosen))]
    registered = [item["id"] for item in list_projects()]
    assert project_id(str(chosen)) in registered
    assert len(registered) == 2


def test_discover_relative_base_is_422(projects_env):
    client, _ = projects_env
    resp = _post(client, "/api/projects/discover", {"base": "relative/dir"})
    assert resp.status_code == 422
    assert resp.get_json()["reason"] == "not_absolute"


def test_discover_missing_base_is_422(projects_env, tmp_path):
    client, _ = projects_env
    resp = _post(client, "/api/projects/discover", {"base": str(tmp_path / "missing")})
    assert resp.status_code == 422
    assert resp.get_json()["reason"] == "not_a_directory"


@pytest.mark.parametrize(
    "make_path, reason",
    [
        (lambda _tmp: "relative/app", "not_absolute"),
        (lambda tmp: str(tmp / "missing"), "not_a_directory"),
        (lambda tmp: str(_project(tmp, "plain", marker="README")), "not_a_project"),
        (lambda tmp: str(tmp / "home"), "forbidden"),
    ],
)
def test_register_refused_path_is_422_with_code(projects_env, tmp_path, make_path, reason):
    client, _ = projects_env
    resp = _post(client, "/api/projects", {"path": make_path(tmp_path)})
    assert resp.status_code == 422
    assert resp.get_json()["reason"] == reason
    assert resp.get_json()["error"]


def test_register_batch_with_refused_path_writes_nothing(projects_env, tmp_path):
    client, _ = projects_env
    good = _project(tmp_path, "good")
    bad = _project(tmp_path, "bad", marker="README")
    resp = _post(client, "/api/projects/register", {"paths": [str(good), str(bad)]})
    assert resp.status_code == 422
    assert resp.get_json()["reason"] == "not_a_project"
    assert project_id(str(good)) not in [item["id"] for item in list_projects()]


def test_register_rejects_body_without_path_list(projects_env):
    client, _ = projects_env
    resp = _post(client, "/api/projects/register", {"paths": "not-a-list"})
    assert resp.status_code == 422
    assert resp.get_json()["reason"] == "invalid"


@pytest.mark.parametrize(
    "url, body",
    [
        ("/api/projects", {"path": "/tmp"}),
        ("/api/projects/discover", {"base": "/tmp"}),
        ("/api/projects/register", {"paths": ["/tmp"]}),
    ],
)
def test_write_routes_without_config_header_are_400(projects_env, url, body):
    client, _ = projects_env
    resp = client.post(url, json=body)
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "csrf"


def test_write_route_with_wrong_content_type_is_400(projects_env, tmp_path):
    client, _ = projects_env
    app_dir = _project(tmp_path, "app", marker=".git")
    resp = client.post(
        "/api/projects",
        data=f"path={app_dir}",
        content_type="application/x-www-form-urlencoded",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "csrf"
    assert project_id(str(app_dir)) not in [item["id"] for item in list_projects()]


def test_write_route_via_tailscale_is_403(projects_env, tmp_path):
    client, _ = projects_env
    app_dir = _project(tmp_path, "app", marker=".git")
    resp = _post(
        client,
        "/api/projects",
        {"path": str(app_dir)},
        headers={**JSON_HEADERS, "Tailscale-User-Login": "dono@example.com"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["reason"] == "remote"
    assert project_id(str(app_dir)) not in [item["id"] for item in list_projects()]


def test_active_run_true_for_project_with_open_run(projects_env, tmp_path):
    client, _ = projects_env
    busy = _project(tmp_path, "busy")
    _write_log(busy, [
        {"event": "orchestration_start", "run_id": "run-open", "task": "plano", "ts": _now()},
        {"event": "worker_spawn", "run_id": "run-open", "task_id": "t1", "ts": _now()},
    ])
    idle = _project(tmp_path, "idle")
    _write_log(idle, [
        {"event": "orchestration_start", "run_id": "run-done", "task": "plano", "ts": _now()},
        {"event": "orchestration_end", "run_id": "run-done", "status": "completed", "ts": _now()},
    ])
    _post(client, "/api/projects/register", {"paths": [str(busy), str(idle)]})
    by_id = {item["id"]: item for item in client.get("/api/projects").get_json()["projects"]}
    assert by_id[project_id(str(busy))]["active_run"] is True
    assert by_id[project_id(str(idle))]["active_run"] is False


def test_unreadable_log_counts_as_no_run(projects_env, tmp_path):
    client, _ = projects_env
    broken = _project(tmp_path, "broken")
    (broken / ".meister" / "logs" / "orchestration_log.jsonl").mkdir(parents=True)
    _post(client, "/api/projects", {"path": str(broken)})
    by_id = {item["id"]: item for item in client.get("/api/projects").get_json()["projects"]}
    assert by_id[project_id(str(broken))]["active_run"] is False
