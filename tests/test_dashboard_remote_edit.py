from __future__ import annotations

from pathlib import Path

import pytest

import meister.dashboard.server as dashboard_server
from meister.dashboard.server import app
from meister.projects import list_projects, project_id

JSON_HEADERS = {"X-Meister-Config": "1"}
TAILSCALE_LOGIN = {"Tailscale-User-Login": "dono@example.com"}

PROJECT_CONFIG = """\
workers:
  tier_order:
    - name: tier_a
      harness: codex
      model: gpt-test
      enabled: true
"""


def _write_project_config(project: Path, logins: list[str]) -> None:
    items = "".join(f"    - {login!r}\n" for login in logins)
    dashboard = f"dashboard:\n  remote_edit_logins:\n{items}" if logins else "dashboard:\n  remote_edit_logins: []\n"
    (project / "meister.config.yaml").write_text(PROJECT_CONFIG + dashboard, encoding="utf-8")


@pytest.fixture
def remote_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.setattr(dashboard_server, "_has_active_run", lambda: False)
    return app.test_client(), project


def _put_lanes(client, model: str, headers: dict):
    body = {
        "stamp": client.get("/api/config", headers=headers).get_json()["stamp"],
        "lanes": [{"name": "tier_a", "harness": "codex", "model": model, "enabled": True}],
    }
    return client.put("/api/config/lanes", json=body, headers={**JSON_HEADERS, **headers})


def _new_app(tmp_path: Path, name: str) -> Path:
    app_dir = tmp_path / name
    app_dir.mkdir()
    (app_dir / ".git").write_text("gitdir: x\n", encoding="utf-8")
    return app_dir


def test_empty_list_keeps_remote_read_only(remote_env):
    client, project = remote_env
    _write_project_config(project, [])
    resp = client.get("/api/config", headers=TAILSCALE_LOGIN)
    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "remote"
    assert resp.get_json()["editable"] is False

    before = (project / "meister.config.yaml").read_bytes()
    resp = _put_lanes(client, "gpt-novo", TAILSCALE_LOGIN)
    assert resp.status_code == 403
    assert resp.get_json()["reason"] == "remote"
    assert (project / "meister.config.yaml").read_bytes() == before


def test_listed_login_can_save_lanes(remote_env):
    client, project = remote_env
    _write_project_config(project, ["dono@example.com"])
    resp = client.get("/api/config", headers=TAILSCALE_LOGIN)
    assert resp.get_json()["reason"] is None
    assert resp.get_json()["editable"] is True

    resp = _put_lanes(client, "gpt-novo", TAILSCALE_LOGIN)
    assert resp.status_code == 200
    assert "gpt-novo" in (project / "meister.config.yaml").read_text(encoding="utf-8")


def test_listed_login_matches_ignoring_case_and_spaces(remote_env):
    client, project = remote_env
    _write_project_config(project, ["  Dono@EXAMPLE.com "])
    headers = {"Tailscale-User-Login": "  dono@example.COM "}
    assert client.get("/api/config", headers=headers).get_json()["reason"] is None

    resp = _put_lanes(client, "gpt-novo", headers)
    assert resp.status_code == 200
    assert "gpt-novo" in (project / "meister.config.yaml").read_text(encoding="utf-8")


def test_login_outside_list_is_refused(remote_env):
    client, project = remote_env
    _write_project_config(project, ["outra@example.com"])
    resp = client.get("/api/config", headers=TAILSCALE_LOGIN)
    assert resp.get_json()["reason"] == "remote"

    before = (project / "meister.config.yaml").read_bytes()
    resp = _put_lanes(client, "gpt-novo", TAILSCALE_LOGIN)
    assert resp.status_code == 403
    assert (project / "meister.config.yaml").read_bytes() == before


def test_remote_without_login_header_is_refused_even_if_listed(remote_env):
    client, project = remote_env
    _write_project_config(project, ["dono@example.com"])
    headers = {"X-Forwarded-For": "100.64.0.2", "Tailscale-User-Name": "Dono"}
    resp = client.get("/api/config", headers=headers)
    assert resp.get_json()["reason"] == "remote"

    before = (project / "meister.config.yaml").read_bytes()
    resp = _put_lanes(client, "gpt-novo", headers)
    assert resp.status_code == 403
    assert resp.get_json()["reason"] == "remote"
    assert (project / "meister.config.yaml").read_bytes() == before


def test_local_request_unchanged_with_and_without_list(remote_env):
    client, project = remote_env
    _write_project_config(project, [])
    assert client.get("/api/config").get_json()["reason"] is None
    assert _put_lanes(client, "gpt-local", {}).status_code == 200

    _write_project_config(project, ["dono@example.com"])
    assert client.get("/api/config").get_json()["editable"] is True
    assert _put_lanes(client, "gpt-local-2", {}).status_code == 200


def test_unreadable_config_counts_as_empty_list(remote_env, tmp_path):
    client, project = remote_env
    (project / "meister.config.yaml").write_text("workers: [\n", encoding="utf-8")
    target = _new_app(tmp_path, "app-broken")
    resp = client.post("/api/projects", json={"path": str(target)}, headers={**JSON_HEADERS, **TAILSCALE_LOGIN})
    assert resp.status_code == 403
    assert resp.get_json()["reason"] == "remote"
    assert project_id(str(target)) not in [item["id"] for item in list_projects()]


def test_projects_write_follows_same_rule(remote_env, tmp_path):
    client, project = remote_env
    target = _new_app(tmp_path, "app-remote")

    _write_project_config(project, [])
    resp = client.post("/api/projects", json={"path": str(target)}, headers={**JSON_HEADERS, **TAILSCALE_LOGIN})
    assert resp.status_code == 403
    assert resp.get_json()["reason"] == "remote"
    assert project_id(str(target)) not in [item["id"] for item in list_projects()]

    _write_project_config(project, ["outra@example.com"])
    resp = client.post("/api/projects", json={"path": str(target)}, headers={**JSON_HEADERS, **TAILSCALE_LOGIN})
    assert resp.status_code == 403
    assert project_id(str(target)) not in [item["id"] for item in list_projects()]

    _write_project_config(project, ["DONO@example.com"])
    resp = client.post("/api/projects", json={"path": str(target)}, headers={**JSON_HEADERS, **TAILSCALE_LOGIN})
    assert resp.status_code == 200
    assert project_id(str(target)) in [item["id"] for item in list_projects()]
