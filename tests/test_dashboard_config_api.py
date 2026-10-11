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


JSON_HEADERS = {"X-Meister-Config": "1"}


def _lane_request(model: str = "gpt-novo", enabled: bool = True) -> dict:
    return {"name": "tier_a", "harness": "codex", "model": model, "enabled": enabled}


def _put_lanes(client, body, headers=None, **kwargs):
    return client.put(
        "/api/config/lanes",
        json=body,
        headers=JSON_HEADERS if headers is None else headers,
        **kwargs,
    )


def _snapshot(project: Path) -> bytes:
    return (project / "meister.config.yaml").read_bytes()


def _stamp(client) -> str:
    return client.get("/api/config").get_json()["stamp"]


def test_put_lanes_saves_and_returns_new_stamp(config_client):
    client, project = config_client
    stamp = _stamp(client)
    resp = _put_lanes(client, {"stamp": stamp, "lanes": [_lane_request()]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["lanes"][0]["model"] == "gpt-novo"
    assert data["stamp"] != stamp
    assert data["stamp"] == _stamp(client)
    text = _snapshot(project).decode("utf-8")
    assert "gpt-novo" in text
    assert "# comentario do dono" in text


def test_put_lanes_without_csrf_header_is_400(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = _put_lanes(client, {"stamp": _stamp(client), "lanes": [_lane_request()]}, headers={})
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "csrf"
    assert _snapshot(project) == before


def test_put_lanes_with_other_content_type_is_400(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = client.put(
        "/api/config/lanes",
        data='{"stamp": "x", "lanes": []}',
        headers={**JSON_HEADERS, "Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "csrf"
    assert _snapshot(project) == before


def test_put_lanes_with_invalid_json_is_400(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = client.put(
        "/api/config/lanes",
        data="{nao e json",
        headers={**JSON_HEADERS, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert _snapshot(project) == before


def test_put_lanes_via_tailscale_is_403(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = _put_lanes(
        client,
        {"stamp": _stamp(client), "lanes": [_lane_request()]},
        headers={**JSON_HEADERS, "Tailscale-User-Login": "dono@example.com"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["reason"] == "remote"
    assert _snapshot(project) == before


def test_put_lanes_csrf_checked_before_remote(config_client):
    client, _ = config_client
    resp = _put_lanes(
        client,
        {"stamp": _stamp(client), "lanes": [_lane_request()]},
        headers={"Tailscale-User-Login": "dono@example.com"},
    )
    assert resp.status_code == 400


def test_put_lanes_with_active_run_is_409(config_client, monkeypatch):
    client, project = config_client
    stamp = _stamp(client)
    monkeypatch.setattr(dashboard_server, "_has_active_run", lambda: True)
    before = _snapshot(project)
    resp = _put_lanes(client, {"stamp": stamp, "lanes": [_lane_request()]})
    assert resp.status_code == 409
    assert resp.get_json()["reason"] == "run_active"
    assert _snapshot(project) == before


def test_put_lanes_with_stale_stamp_is_409(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = _put_lanes(client, {"stamp": "0" * 64, "lanes": [_lane_request()]})
    assert resp.status_code == 409
    assert resp.get_json()["reason"] == "stale"
    assert _snapshot(project) == before


def test_put_lanes_invalid_is_422_with_field_messages(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = _put_lanes(client, {"stamp": _stamp(client), "lanes": [_lane_request(model=" ")]})
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["reason"] == "invalid"
    assert "lanes[0].model" in data["fields"]
    assert any(issue["path"] == "lanes[0].model" for issue in data["issues"])
    assert _snapshot(project) == before


def test_put_lanes_rejects_field_outside_editable_list(config_client):
    client, project = config_client
    before = _snapshot(project)
    lane = {**_lane_request(), "cost_per_m_tokens": 99}
    resp = _put_lanes(client, {"stamp": _stamp(client), "lanes": [lane]})
    assert resp.status_code == 422
    assert "lanes[0].cost_per_m_tokens" in resp.get_json()["fields"]
    assert _snapshot(project) == before


def test_put_lanes_no_enabled_lane_is_422(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = _put_lanes(client, {"stamp": _stamp(client), "lanes": [_lane_request(enabled=False)]})
    assert resp.status_code == 422
    assert "lanes" in resp.get_json()["fields"]
    assert _snapshot(project) == before


def test_put_lanes_body_must_be_object(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = _put_lanes(client, [1, 2])
    assert resp.status_code == 422
    assert _snapshot(project) == before


def test_restore_default_removes_tier_order(config_client):
    client, project = config_client
    stamp = _stamp(client)
    resp = client.post("/api/config/restore-default", json={"stamp": stamp}, headers=JSON_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["has_tier_order"] is False
    assert data["stamp"] != stamp
    text = _snapshot(project).decode("utf-8")
    assert "tier_order" not in text
    assert "# comentario do dono" in text


def test_restore_default_without_csrf_header_is_400(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = client.post("/api/config/restore-default", json={"stamp": _stamp(client)})
    assert resp.status_code == 400
    assert _snapshot(project) == before


def test_restore_default_via_tailscale_is_403(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = client.post(
        "/api/config/restore-default",
        json={"stamp": _stamp(client)},
        headers={**JSON_HEADERS, "X-Forwarded-For": "100.64.0.2", "Tailscale-User-Name": "Dono"},
    )
    assert resp.status_code == 403
    assert _snapshot(project) == before


def test_restore_default_with_active_run_is_409(config_client, monkeypatch):
    client, project = config_client
    stamp = _stamp(client)
    monkeypatch.setattr(dashboard_server, "_has_active_run", lambda: True)
    before = _snapshot(project)
    resp = client.post("/api/config/restore-default", json={"stamp": stamp}, headers=JSON_HEADERS)
    assert resp.status_code == 409
    assert resp.get_json()["reason"] == "run_active"
    assert _snapshot(project) == before


def test_restore_default_with_stale_stamp_is_409(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = client.post("/api/config/restore-default", json={"stamp": "0" * 64}, headers=JSON_HEADERS)
    assert resp.status_code == 409
    assert resp.get_json()["reason"] == "stale"
    assert _snapshot(project) == before


def test_restore_default_with_missing_stamp_is_422(config_client):
    client, project = config_client
    before = _snapshot(project)
    resp = client.post("/api/config/restore-default", json={}, headers=JSON_HEADERS)
    assert resp.status_code == 422
    assert "stamp" in resp.get_json()["fields"]
    assert _snapshot(project) == before


def test_read_stays_open_with_tailscale_header_after_write_endpoints(config_client):
    client, _ = config_client
    resp = client.get("/api/config", headers={"Tailscale-User-Login": "dono@example.com"})
    assert resp.status_code == 200
    assert resp.get_json()["lanes"]
