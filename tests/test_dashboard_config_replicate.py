from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from ruamel.yaml import YAML

from meister.dashboard.server import app
from meister.projects import project_id, register_projects

JSON_HEADERS = {"X-Meister-Config": "1"}

SOURCE_CONFIG = """\
# comentario da origem
workers:
  tier_order:
    - name: tier_1
      harness: codex
      model: gpt-origem
      enabled: true
      cost_per_m_tokens: 1.0
    - name: tier_2
      harness: codex
      model: gpt-dois
      enabled: false
      cost_per_m_tokens: 2.0
"""

DEST_A_CONFIG = """\
workers:
  tier_order:
    - name: tier_1
      harness: codex
      model: gpt-a-antigo
      enabled: true
      cost_per_m_tokens: 1.0
      notes: dono da via
    - name: tier_3
      harness: codex
      model: gpt-a-sobra
      enabled: true
      cost_per_m_tokens: 3.0
"""

DEST_B_CONFIG = """\
workers:
  tier_order:
    - name: tier_2
      harness: codex
      model: gpt-b-antigo
      enabled: true
      cost_per_m_tokens: 2.0
"""

LANES = [
    {"name": "tier_1", "harness": "codex", "model": "gpt-novo", "enabled": True, "cost_per_m_tokens": 1.5,
     "original_name": "tier_1"},
    {"name": "tier_2", "harness": "codex", "model": "gpt-dois-novo", "enabled": False, "cost_per_m_tokens": 2.0,
     "original_name": "tier_2"},
]


def _project(root: Path, name: str, config: str | None = None) -> Path:
    project = root / name
    (project / ".meister").mkdir(parents=True)
    if config is not None:
        (project / "meister.config.yaml").write_text(config, encoding="utf-8")
    return project


def _write_log(project: Path, events: List[Dict[str, Any]]) -> None:
    logs = project / ".meister" / "logs"
    logs.mkdir(parents=True)
    lines = [json.dumps(event) for event in events]
    (logs / "orchestration_log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture
def replicate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    current = _project(tmp_path, "current")
    monkeypatch.chdir(current)
    source = _project(tmp_path, "source", SOURCE_CONFIG)
    dest_a = _project(tmp_path, "dest_a", DEST_A_CONFIG)
    dest_b = _project(tmp_path, "dest_b", DEST_B_CONFIG)
    register_projects([str(source), str(dest_a), str(dest_b)])
    return app.test_client(), {"source": source, "dest_a": dest_a, "dest_b": dest_b}


def _stamp(client, project: Path) -> str:
    return client.get("/api/config", query_string={"project": project_id(str(project))}).get_json()["stamp"]


def _body(client, project: Path, lanes: List[Dict[str, Any]] = LANES, stamp: str | None = None) -> Dict[str, Any]:
    return {
        "project": project_id(str(project)),
        "stamp": _stamp(client, project) if stamp is None else stamp,
        "lanes": lanes,
    }


def _post(client, body: Dict[str, Any], headers: Dict[str, str] | None = None):
    return client.post(
        "/api/config/replicate",
        json=body,
        headers=JSON_HEADERS if headers is None else headers,
    )


def _tier_order(project: Path) -> List[Dict[str, Any]]:
    data = YAML(typ="safe").load((project / "meister.config.yaml").read_text(encoding="utf-8"))
    return data["workers"]["tier_order"]


def _snapshot(*projects: Path) -> List[bytes | None]:
    return [
        (project / "meister.config.yaml").read_bytes()
        if (project / "meister.config.yaml").exists() else None
        for project in projects
    ]


def _statuses(resp) -> Dict[str, str]:
    return {item["name"]: item["status"] for item in resp.get_json()["results"]}


def test_replicate_writes_same_lanes_to_source_and_destinations(replicate_env):
    client, paths = replicate_env
    resp = _post(client, _body(client, paths["source"]))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["source"]["lanes"][0]["model"] == "gpt-novo"
    assert [item["name"] for item in data["results"]] == ["dest_a", "dest_b"]
    assert all(item["status"] == "saved" and item["message"] == "" for item in data["results"])
    expected = [
        {"name": "tier_1", "harness": "codex", "model": "gpt-novo", "enabled": True, "cost_per_m_tokens": 1.5},
        {"name": "tier_2", "harness": "codex", "model": "gpt-dois-novo", "enabled": False, "cost_per_m_tokens": 2.0},
    ]
    for project in paths.values():
        lanes = _tier_order(project)
        assert [
            {
                "name": lane["name"],
                "harness": lane["harness"],
                "model": lane["model"],
                "enabled": lane.get("enabled", True),
                "cost_per_m_tokens": lane["cost_per_m_tokens"],
            }
            for lane in lanes
        ] == expected
        assert all("original_name" not in lane for lane in lanes)
    assert "# comentario da origem" in (paths["source"] / "meister.config.yaml").read_text(encoding="utf-8")


def test_replicate_with_stale_source_stamp_writes_nothing(replicate_env):
    client, paths = replicate_env
    before = _snapshot(*paths.values())
    resp = _post(client, _body(client, paths["source"], stamp="0" * 64))
    assert resp.status_code == 409
    assert resp.get_json()["reason"] == "stale"
    assert _snapshot(*paths.values()) == before


def test_run_active_destination_is_skipped_and_others_saved(replicate_env):
    client, paths = replicate_env
    _write_log(paths["dest_a"], [
        {"event": "orchestration_start", "run_id": "run-open", "task": "plano", "ts": _now()},
        {"event": "worker_spawn", "run_id": "run-open", "task_id": "t1", "ts": _now()},
    ])
    before_a = (paths["dest_a"] / "meister.config.yaml").read_bytes()
    resp = _post(client, _body(client, paths["source"]))
    assert resp.status_code == 200
    assert _statuses(resp) == {"dest_a": "skipped_run_active", "dest_b": "saved"}
    skipped = resp.get_json()["results"][0]
    assert skipped["message"]
    assert (paths["dest_a"] / "meister.config.yaml").read_bytes() == before_a
    assert [lane["model"] for lane in _tier_order(paths["dest_b"])] == ["gpt-novo", "gpt-dois-novo"]


def test_missing_destination_folder_is_skipped_missing(replicate_env):
    client, paths = replicate_env
    shutil.rmtree(paths["dest_b"])
    resp = _post(client, _body(client, paths["source"]))
    assert resp.status_code == 200
    assert _statuses(resp) == {"dest_a": "saved", "dest_b": "skipped_missing"}
    assert resp.get_json()["results"][1]["message"]
    assert [lane["model"] for lane in _tier_order(paths["dest_a"])] == ["gpt-novo", "gpt-dois-novo"]


def test_destination_with_invalid_yaml_fails_and_others_saved(replicate_env):
    client, paths = replicate_env
    (paths["dest_a"] / "meister.config.yaml").write_text("workers: [oops\n", encoding="utf-8")
    resp = _post(client, _body(client, paths["source"]))
    assert resp.status_code == 200
    results = {item["name"]: item for item in resp.get_json()["results"]}
    assert results["dest_a"]["status"] == "failed"
    assert results["dest_a"]["message"]
    assert results["dest_b"]["status"] == "saved"
    assert [lane["model"] for lane in _tier_order(paths["dest_b"])] == ["gpt-novo", "gpt-dois-novo"]


def test_destination_without_config_file_gets_created(replicate_env):
    client, paths = replicate_env
    (paths["dest_b"] / "meister.config.yaml").unlink()
    resp = _post(client, _body(client, paths["source"]))
    assert _statuses(resp)["dest_b"] == "saved"
    assert [lane["model"] for lane in _tier_order(paths["dest_b"])] == ["gpt-novo", "gpt-dois-novo"]


def test_destination_lane_keeps_fields_the_screen_does_not_edit(replicate_env):
    client, paths = replicate_env
    resp = _post(client, _body(client, paths["source"]))
    assert _statuses(resp)["dest_a"] == "saved"
    tier_1 = next(lane for lane in _tier_order(paths["dest_a"]) if lane["name"] == "tier_1")
    assert tier_1["notes"] == "dono da via"
    assert tier_1["model"] == "gpt-novo"
    assert all(lane["name"] != "tier_3" for lane in _tier_order(paths["dest_a"]))


def test_source_run_active_blocks_replication_and_writes_nothing(replicate_env):
    client, paths = replicate_env
    _write_log(paths["source"], [
        {"event": "orchestration_start", "run_id": "run-open", "task": "plano", "ts": _now()},
        {"event": "worker_spawn", "run_id": "run-open", "task_id": "t1", "ts": _now()},
    ])
    before = _snapshot(*paths.values())
    resp = _post(client, _body(client, paths["source"]))
    assert resp.status_code == 409
    assert resp.get_json()["reason"] == "run_active"
    assert _snapshot(*paths.values()) == before


def test_missing_csrf_header_is_400_and_writes_nothing(replicate_env):
    client, paths = replicate_env
    before = _snapshot(*paths.values())
    resp = _post(client, _body(client, paths["source"]), headers={})
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "csrf"
    assert _snapshot(*paths.values()) == before


def test_tailscale_request_is_403_and_writes_nothing(replicate_env):
    client, paths = replicate_env
    before = _snapshot(*paths.values())
    resp = _post(
        client,
        _body(client, paths["source"]),
        headers={**JSON_HEADERS, "Tailscale-User-Login": "dono@example.com"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["reason"] == "remote"
    assert _snapshot(*paths.values()) == before


def test_invalid_lanes_is_422_and_writes_nothing(replicate_env):
    client, paths = replicate_env
    before = _snapshot(*paths.values())
    lanes = [{"name": "tier_1", "harness": "inexistente", "model": "x", "enabled": True, "original_name": "tier_1"}]
    resp = _post(client, _body(client, paths["source"], lanes=lanes))
    assert resp.status_code == 422
    assert resp.get_json()["reason"] == "invalid"
    assert resp.get_json()["fields"]
    assert _snapshot(*paths.values()) == before


def test_unknown_source_project_is_404(replicate_env):
    client, _ = replicate_env
    resp = _post(client, {"project": "000000000000", "stamp": "x", "lanes": LANES})
    assert resp.status_code == 404
    assert resp.get_json()["reason"] == "unknown_project"
