from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

import meister.dashboard.server as dashboard_server
from meister.dashboard.server import app
from meister.dashboard.state import ServerState, read_state, write_state
from meister.i18n import t
from meister.projects import get_project, project_id, register_project


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _write_log(log_path: Path, events: list[dict]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event) for event in events]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def two_projects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    proj_a = tmp_path / "project_a"
    (proj_a / ".meister" / "logs").mkdir(parents=True)
    (proj_a / "meister.config.yaml").write_text(
        """version: "2.0"
workers:
  tier_order:
    - name: tier_alpha
      harness: codex
      model: gpt-6-luna
      cost_per_m_tokens: 0.2
""",
        encoding="utf-8",
    )

    proj_b = tmp_path / "project_b"
    (proj_b / ".meister" / "logs").mkdir(parents=True)
    (proj_b / "meister.config.yaml").write_text(
        """version: "2.0"
workers:
  tier_order:
    - name: tier_beta
      harness: claude
      model: sonnet
      cost_per_m_tokens: 3.0
""",
        encoding="utf-8",
    )

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    events_a = [
        {"event": "orchestration_start", "run_id": "run-a1", "task": "task_a", "ts": now_iso},
        {
            "event": "worker_spawn",
            "run_id": "run-a1",
            "task_id": "task_a",
            "tier": "tier_alpha",
            "ts": now_iso,
        },
        {
            "event": "worker_complete",
            "run_id": "run-a1",
            "task_id": "task_a",
            "tier": "tier_alpha",
            "status": "completed",
            "cost_usd": 0.05,
            "ts": now_iso,
        },
    ]
    events_b = [
        {"event": "orchestration_start", "run_id": "run-b1", "task": "task_b", "ts": now_iso},
        {
            "event": "worker_spawn",
            "run_id": "run-b1",
            "task_id": "task_b",
            "tier": "tier_beta",
            "ts": now_iso,
        },
        {
            "event": "worker_complete",
            "run_id": "run-b1",
            "task_id": "task_b",
            "tier": "tier_beta",
            "status": "completed",
            "cost_usd": 0.12,
            "ts": now_iso,
        },
    ]

    _write_log(proj_a / ".meister" / "logs" / "orchestration_log.jsonl", events_a)
    _write_log(proj_b / ".meister" / "logs" / "orchestration_log.jsonl", events_b)

    register_project(str(proj_a))
    register_project(str(proj_b))

    monkeypatch.chdir(proj_a)
    monkeypatch.setenv("MEISTER_LOG_DIR", str(proj_a / ".meister" / "logs"))
    monkeypatch.setattr(dashboard_server, "_has_active_run", lambda: False)

    return app.test_client(), proj_a, proj_b


def test_timeline_routes_return_requested_project_runs(two_projects_env):
    client, proj_a, proj_b = two_projects_env
    id_a = project_id(str(proj_a))
    id_b = project_id(str(proj_b))

    # 1. Sem parâmetro: projeto servido (proj_a)
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    runs = resp.get_json()["runs"]
    assert [r["run_id"] for r in runs] == ["run-a1"]

    resp = client.get("/api/timelines")
    assert resp.status_code == 200
    timelines = resp.get_json()["runs"]
    assert len(timelines) == 1
    assert timelines[0]["run"]["id"] == "run-a1"
    assert _norm(proj_a) == _norm(proj_a)
    assert timelines[0]["project"]["name"] == "project_a"

    resp = client.get("/api/timeline")
    assert resp.status_code == 200
    timeline = resp.get_json()
    assert timeline["run"]["id"] == "run-a1"
    assert timeline["project"]["name"] == "project_a"

    # 2. Com id do projeto servido: comportamento intacto (proj_a)
    resp = client.get(f"/api/runs?project={id_a}")
    assert resp.status_code == 200
    assert [r["run_id"] for r in resp.get_json()["runs"]] == ["run-a1"]

    resp = client.get(f"/api/timelines?project={id_a}")
    assert resp.status_code == 200
    assert [t["run"]["id"] for t in resp.get_json()["runs"]] == ["run-a1"]

    resp = client.get(f"/api/timeline?project={id_a}")
    assert resp.status_code == 200
    assert resp.get_json()["run"]["id"] == "run-a1"

    # 3. Com id do outro projeto (proj_b): devolve runs de B e NÃO de A
    resp = client.get(f"/api/runs?project={id_b}")
    assert resp.status_code == 200
    assert [r["run_id"] for r in resp.get_json()["runs"]] == ["run-b1"]

    resp = client.get(f"/api/timelines?project={id_b}")
    assert resp.status_code == 200
    timelines_b = resp.get_json()["runs"]
    assert len(timelines_b) == 1
    assert timelines_b[0]["run"]["id"] == "run-b1"
    assert timelines_b[0]["project"]["name"] == "project_b"

    resp = client.get(f"/api/timeline?project={id_b}")
    assert resp.status_code == 200
    timeline_b = resp.get_json()
    assert timeline_b["run"]["id"] == "run-b1"
    assert timeline_b["project"]["name"] == "project_b"

    # Buscar run específico de B em B dá 200; buscar run de A em B dá 404
    resp = client.get(f"/api/timeline?project={id_b}&run_id=run-b1")
    assert resp.status_code == 200
    assert resp.get_json()["run"]["id"] == "run-b1"

    resp = client.get(f"/api/timeline?project={id_b}&run_id=run-a1")
    assert resp.status_code == 404


def test_unknown_project_id_returns_404_for_all_three_routes(two_projects_env):
    client, _, _ = two_projects_env
    unknown_id = "0123456789ab"
    expected_error = t("reports.dashboard.config_unknown_project")

    for endpoint in ("/api/runs", "/api/timelines", "/api/timeline"):
        resp = client.get(f"{endpoint}?project={unknown_id}")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["reason"] == "unknown_project"
        assert data["error"] == expected_error


def test_missing_project_on_disk_returns_409_for_all_three_routes(two_projects_env, tmp_path):
    client, _, _ = two_projects_env
    proj_c = tmp_path / "project_c"
    (proj_c / ".meister").mkdir(parents=True)
    register_project(str(proj_c))
    id_c = project_id(str(proj_c))

    shutil.rmtree(proj_c)
    expected_error = t("reports.dashboard.config_project_missing")

    for endpoint in ("/api/runs", "/api/timelines", "/api/timeline"):
        resp = client.get(f"{endpoint}?project={id_c}")
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["reason"] == "project_missing"
        assert data["error"] == expected_error


def test_registered_project_without_log_returns_empty_and_does_not_create_meister(
    two_projects_env, tmp_path
):
    client, _, _ = two_projects_env
    proj_d = tmp_path / "project_d"
    proj_d.mkdir()
    (proj_d / "meister.config.yaml").write_text("workers: {}\n", encoding="utf-8")
    register_project(str(proj_d))
    id_d = project_id(str(proj_d))

    meister_dir = proj_d / ".meister"
    assert not meister_dir.exists()

    resp_runs = client.get(f"/api/runs?project={id_d}")
    assert resp_runs.status_code == 200
    assert resp_runs.get_json() == {"runs": []}

    resp_timelines = client.get(f"/api/timelines?project={id_d}")
    assert resp_timelines.status_code == 200
    assert resp_timelines.get_json() == {"runs": []}

    resp_timeline = client.get(f"/api/timeline?project={id_d}")
    assert resp_timeline.status_code == 200
    data = resp_timeline.get_json()
    assert data["schema"] == 1
    assert data["run"] is None
    assert data["rows"] == []
    assert data["project"]["name"] == "project_d"

    # Não pode ter criado pasta nem arquivo dentro de proj_d
    assert not meister_dir.exists()


def test_specific_run_id_in_project_without_log_returns_404(two_projects_env, tmp_path):
    client, _, _ = two_projects_env
    proj_e = tmp_path / "project_e"
    proj_e.mkdir()
    (proj_e / "meister.config.yaml").write_text("workers: {}\n", encoding="utf-8")
    register_project(str(proj_e))
    id_e = project_id(str(proj_e))

    resp = client.get(f"/api/timeline?project={id_e}&run_id=any-run")
    assert resp.status_code == 404
    data = resp.get_json()
    assert "error" in data
    assert "any-run" in data["error"]
    assert not (proj_e / ".meister").exists()


def test_querying_other_project_does_not_alter_state_of_either_project(two_projects_env):
    client, proj_a, proj_b = two_projects_env
    id_a = project_id(str(proj_a))
    id_b = project_id(str(proj_b))

    initial_time = "2026-10-20T00:00:00Z"
    state_a = ServerState(
        pid=9999,
        port=5050,
        url="http://127.0.0.1:5050",
        started_at=initial_time,
        last_seen=initial_time,
        code_id="test_code",
    )
    write_state(str(proj_a), state_a)

    reg_a_before = get_project(id_a)
    reg_b_before = get_project(id_b)
    assert reg_a_before is not None
    assert reg_b_before is not None

    # Consultas a proj_b não podem alterar last_seen em nenhum lugar
    client.get(f"/api/runs?project={id_b}")
    client.get(f"/api/timelines?project={id_b}")
    client.get(f"/api/timeline?project={id_b}")
    client.get(f"/api/timeline?project={id_b}&run_id=run-b1")

    # Estado em disco de proj_a não mudou
    current_state_a = read_state(str(proj_a))
    assert current_state_a is not None
    assert current_state_a.last_seen == initial_time

    # Estado em disco de proj_b continua sem existir
    assert read_state(str(proj_b)) is None

    # last_seen do registro de projetos continua idêntico
    reg_a_after = get_project(id_a)
    reg_b_after = get_project(id_b)
    assert reg_a_after is not None and reg_b_after is not None
    assert reg_a_after["last_seen"] == reg_a_before["last_seen"]
    assert reg_b_after["last_seen"] == reg_b_before["last_seen"]

    # Em contraste, consultar a rota da timeline do próprio projeto servido atualiza o last_seen
    resp = client.get("/api/timeline")
    assert resp.status_code == 200
    touched_state_a = read_state(str(proj_a))
    assert touched_state_a is not None
    assert touched_state_a.last_seen != initial_time


def test_unreadable_log_returns_empty_and_never_500(two_projects_env, tmp_path):
    client, _, _ = two_projects_env
    proj_f = tmp_path / "project_f"
    proj_f.mkdir()
    (proj_f / "meister.config.yaml").write_text("workers: {}\n", encoding="utf-8")
    # Torna orchestration_log.jsonl um diretório para causar erro de leitura (IsADirectoryError / OSError)
    log_as_dir = proj_f / ".meister" / "logs" / "orchestration_log.jsonl"
    log_as_dir.mkdir(parents=True)
    register_project(str(proj_f))
    id_f = project_id(str(proj_f))

    resp_runs = client.get(f"/api/runs?project={id_f}")
    assert resp_runs.status_code == 200
    assert resp_runs.get_json() == {"runs": []}

    resp_timelines = client.get(f"/api/timelines?project={id_f}")
    assert resp_timelines.status_code == 200
    assert resp_timelines.get_json() == {"runs": []}

    resp_timeline = client.get(f"/api/timeline?project={id_f}")
    assert resp_timeline.status_code == 200
    data = resp_timeline.get_json()
    assert data["schema"] == 1
    assert data["run"] is None

    resp_run_id = client.get(f"/api/timeline?project={id_f}&run_id=some-run")
    assert resp_run_id.status_code == 404

