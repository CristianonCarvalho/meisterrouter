from __future__ import annotations

from pathlib import Path

import pytest

import meister.dashboard.server as dashboard_server
from meister.dashboard.server import app

PROJECT_CONFIG = """\
# comentario do dono
workers:
  tier_order:
    - name: tier_a
      harness: codex
      model: gpt-test
      enabled: true
"""


@pytest.fixture
def config_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    (project / "meister.config.yaml").write_text(PROJECT_CONFIG, encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.setattr(dashboard_server, "_has_active_run", lambda: False)
    return app.test_client(), project


def test_config_api_reads_lanes_and_is_editable(config_client):
    client, project = config_client
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert [lane["name"] for lane in data["lanes"]] == ["tier_a"]
    assert Path(data["source"]).resolve() == (project / "meister.config.yaml").resolve()
    assert data["has_tier_order"] is True
    assert data["editable"] is True
    assert data["reason"] is None


def test_config_api_returns_stamp(config_client):
    client, _ = config_client
    data = client.get("/api/config").get_json()
    assert isinstance(data["stamp"], str) and len(data["stamp"]) == 64


def test_config_api_blocks_edit_with_active_run(config_client, monkeypatch):
    client, _ = config_client
    monkeypatch.setattr(dashboard_server, "_has_active_run", lambda: True)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["editable"] is False
    assert data["reason"] == "run_active"
    assert data["lanes"]


def test_config_api_read_only_via_tailscale_header(config_client):
    client, _ = config_client
    resp = client.get("/api/config", headers={"Tailscale-User-Login": "dono@example.com"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["editable"] is False
    assert data["reason"] == "remote"
    assert data["lanes"]


def test_config_api_read_only_via_forwarded_tailscale(config_client):
    client, _ = config_client
    resp = client.get(
        "/api/config",
        headers={"X-Forwarded-For": "100.64.0.2", "Tailscale-User-Name": "Dono"},
    )
    data = resp.get_json()
    assert data["editable"] is False
    assert data["reason"] == "remote"


def test_forwarded_for_alone_is_not_remote(config_client):
    client, _ = config_client
    data = client.get("/api/config", headers={"X-Forwarded-For": "127.0.0.1"}).get_json()
    assert data["editable"] is True


def test_config_api_without_project_file_uses_defaults(config_client):
    client, project = config_client
    (project / "meister.config.yaml").unlink()
    data = client.get("/api/config").get_json()
    assert data["has_tier_order"] is False
    assert data["lanes"]
    assert data["editable"] is True
