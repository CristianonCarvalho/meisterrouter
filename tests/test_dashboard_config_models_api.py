from __future__ import annotations

from typing import Any, Dict, List

import pytest

import meister.dashboard.server as dashboard_server
from meister.dashboard.server import app


def _fake_catalog(harness: str) -> Dict[str, Any]:
    models: List[Dict[str, Any]] = [
        {
            "id": "gpt-test",
            "label": "GPT Test",
            "cost_per_m_tokens": 0.2,
            "source": "codex cache",
        },
    ]
    return {
        "harness": harness,
        "models": models,
        "sources": [{"name": "codex cache", "ok": True}],
    }


@pytest.fixture
def models_client(monkeypatch: pytest.MonkeyPatch):
    calls: List[str] = []

    def fake_list_models(harness: str) -> Dict[str, Any]:
        calls.append(harness)
        return _fake_catalog(harness)

    monkeypatch.setattr(dashboard_server, "list_models", fake_list_models)
    return app.test_client(), calls


def test_models_api_returns_catalog_for_harness(models_client):
    client, calls = models_client
    resp = client.get("/api/config/models?harness=codex")
    assert resp.status_code == 200
    assert calls == ["codex"]
    data = resp.get_json()
    assert data["harness"] == "codex"
    assert data["models"][0]["id"] == "gpt-test"
    assert data["models"][0]["source"] == "codex cache"
    assert data["sources"] == [{"name": "codex cache", "ok": True}]


def test_models_api_missing_harness_is_400(models_client):
    client, calls = models_client
    resp = client.get("/api/config/models")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["reason"] == "missing_harness"
    assert data["error"]
    assert calls == []


def test_models_api_blank_harness_is_400(models_client):
    client, calls = models_client
    resp = client.get("/api/config/models?harness=%20%20")
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "missing_harness"
    assert calls == []


def test_models_api_readable_via_tailscale_header(models_client):
    client, calls = models_client
    resp = client.get(
        "/api/config/models?harness=codex",
        headers={"Tailscale-User-Login": "dono@example.com"},
    )
    assert resp.status_code == 200
    assert calls == ["codex"]
    assert resp.get_json()["models"][0]["id"] == "gpt-test"


def test_models_api_readable_via_forwarded_tailscale(models_client):
    client, _ = models_client
    resp = client.get(
        "/api/config/models?harness=codex",
        headers={"X-Forwarded-For": "100.64.0.2", "Tailscale-User-Name": "Dono"},
    )
    assert resp.status_code == 200


@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_models_api_does_not_accept_writes(models_client, method):
    client, calls = models_client
    resp = client.open(
        "/api/config/models?harness=codex",
        method=method,
        json={"harness": "codex"},
        headers={"X-Meister-Config": "1"},
    )
    assert resp.status_code == 405
    assert calls == []
