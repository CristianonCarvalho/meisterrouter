"""Falha imediata do worker (via inexistente, timeout invalido) chega ao Meister na hora.

Antes, a construcao do HarnessWorker e a leitura do timeout ficavam fora do try de
_execute_task_file: o erro escapava sem gravar o result.json de erro nem o evento
worker_task_error, e o bridge esperava ate o timeout por inatividade (600 s).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from meister.worker import (
    UnknownTierError,
    _early_error_result,
    execute_task_file,
    read_atomic_json,
    write_atomic_json,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEISTER_LANG", "en")
    monkeypatch.delenv("MEISTER_RUN_ID", raising=False)


def _config(tmp_path: Path, tiers_yaml: str) -> str:
    path = tmp_path / "meister.config.yaml"
    path.write_text("workers:\n" + tiers_yaml, encoding="utf-8")
    return str(path)


ENABLED_TIER_1 = (
    "  tier_order:\n"
    "    - name: tier_1\n"
    "      harness: codex\n"
    "      model: default\n"
)


def _task_file(tmp_path: Path, *, model, config_path, result_file=None, **extra) -> str:
    payload = {
        "task_id": "early-1",
        "model": model,
        "task": "do something",
        "cwd": str(tmp_path),
        "config_path": config_path,
        "timeout": 30,
        **extra,
    }
    if result_file is not None:
        payload["result_file"] = str(result_file)
    path = tmp_path / "task.json"
    write_atomic_json(str(path), payload)
    return str(path)


def _log_events(tmp_path: Path) -> list[dict]:
    log = tmp_path / "logs" / "orchestration_log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fake_cli(tmp_path: Path) -> str:
    cli = tmp_path / "bin" / "codex"
    cli.parent.mkdir(exist_ok=True)
    cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli.chmod(0o755)
    return str(cli)


def test_unknown_lane_writes_error_result_and_logs_event(tmp_path):
    cfg = _config(tmp_path, ENABLED_TIER_1)
    result_file = tmp_path / "result.json"
    task_file = _task_file(tmp_path, model="via_inexistente", config_path=cfg, result_file=result_file)

    with pytest.raises(UnknownTierError):
        execute_task_file(task_file)

    assert result_file.exists()
    saved = read_atomic_json(str(result_file))
    assert saved is not None
    assert saved["status"] == "error"
    assert saved["exit_code"] == 1
    assert saved["task_id"] == "early-1"
    assert "Unknown lane" in saved["error"]
    assert "via_inexistente" in saved["error"]
    assert "tier_1" in saved["error"]
    assert saved["harness"] is None
    assert saved["cli"] is None
    assert saved["model"] == "via_inexistente"
    assert saved["modified_files"] == []
    assert saved["output"] == ""

    errors = [e for e in _log_events(tmp_path) if e.get("event_type") == "worker_task_error"]
    assert len(errors) == 1
    assert "Unknown lane" in errors[0]["error"]


def test_run_task_cli_unknown_lane_exits_1_and_writes_result(tmp_path):
    cfg = _config(tmp_path, ENABLED_TIER_1)
    result_file = tmp_path / "result.json"
    task_file = _task_file(tmp_path, model="via_inexistente", config_path=cfg, result_file=result_file)

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(PROJECT_ROOT), env.get("PYTHONPATH", "")) if item
    )
    env["MEISTER_LOG_DIR"] = str(tmp_path / "logs")
    completed = subprocess.run(
        [sys.executable, "-m", "meister.cli", "run-task", task_file],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 1
    assert "Task failed:" in completed.stderr
    assert "Unknown lane" in completed.stderr
    assert result_file.exists()
    saved = read_atomic_json(str(result_file))
    assert saved["status"] == "error"
    assert "tier_1" in saved["error"]


def test_invalid_timeout_writes_error_result(tmp_path):
    cfg = _config(tmp_path, ENABLED_TIER_1)
    result_file = tmp_path / "result.json"
    task_file = _task_file(
        tmp_path, model="tier_1", config_path=cfg, result_file=result_file, timeout="abc"
    )

    with pytest.raises(ValueError):
        execute_task_file(task_file)

    saved = read_atomic_json(str(result_file))
    assert saved is not None
    assert saved["status"] == "error"
    assert saved["exit_code"] == 1
    assert saved["task_id"] == "early-1"
    assert "abc" in saved["error"] or "float" in saved["error"]
    errors = [e for e in _log_events(tmp_path) if e.get("event_type") == "worker_task_error"]
    assert len(errors) == 1


def test_disabled_lane_in_error_message_writes_error_result(tmp_path):
    cfg = _config(
        tmp_path,
        ENABLED_TIER_1
        + "    - name: tier_2\n"
        + "      harness: agy\n"
        + "      model: gemini\n"
        + "      enabled: false\n",
    )
    result_file = tmp_path / "result.json"
    task_file = _task_file(tmp_path, model="nao_existe", config_path=cfg, result_file=result_file)

    with pytest.raises(UnknownTierError):
        execute_task_file(task_file)

    saved = read_atomic_json(str(result_file))
    assert saved["status"] == "error"
    assert "nao_existe" in saved["error"]
    assert "tier_2" in saved["error"]


def test_happy_path_still_writes_done_and_no_error(tmp_path):
    cfg = _config(tmp_path, ENABLED_TIER_1)
    result_file = tmp_path / "result.json"
    task_file = _task_file(tmp_path, model="tier_1", config_path=cfg, result_file=result_file)

    with patch("meister.worker.find_cli_binary", return_value=_fake_cli(tmp_path)):
        res = execute_task_file(task_file)

    assert res["status"] == "done"
    saved = read_atomic_json(str(result_file))
    assert saved["status"] == "done"
    assert saved["task_id"] == "early-1"
    events = [e.get("event_type") for e in _log_events(tmp_path)]
    assert "worker_task_end" in events
    assert "worker_task_error" not in events


def test_early_failure_without_result_path_raises_and_creates_nothing(tmp_path):
    cfg = _config(tmp_path, ENABLED_TIER_1)
    task_file = _task_file(tmp_path, model="via_inexistente", config_path=cfg)
    (tmp_path / "logs").mkdir()
    before = sorted(p.name for p in tmp_path.iterdir())

    with pytest.raises(UnknownTierError):
        execute_task_file(task_file)

    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not list(tmp_path.rglob("*result*"))


def test_early_error_result_without_worker_uses_fallbacks():
    res = _early_error_result("t-1", "boom", model="tier_9")
    assert res == {
        "task_id": "t-1",
        "status": "error",
        "harness": None,
        "model": "tier_9",
        "cli": None,
        "modified_files": [],
        "output": "",
        "exit_code": 1,
        "error": "boom",
    }


def test_early_error_result_without_worker_or_model_uses_default():
    res = _early_error_result("t-2", "boom")
    assert res["model"] == "default"
    assert res["harness"] is None
    assert res["cli"] is None


def test_early_error_result_with_worker_uses_worker_fields():
    class _Worker:
        harness = "codex"
        resolved_model = "gpt-x"
        cli_binary = "/usr/bin/codex"

    res = _early_error_result("t-3", "boom", worker=_Worker(), model="tier_1")
    assert res["harness"] == "codex"
    assert res["model"] == "gpt-x"
    assert res["cli"] == "/usr/bin/codex"
    assert res["status"] == "error"
    assert res["exit_code"] == 1
    assert res["error"] == "boom"


def test_early_error_result_with_worker_without_resolved_model_uses_default():
    class _Worker:
        harness = "codex"
        resolved_model = None
        cli_binary = None

    res = _early_error_result("t-5", "boom", worker=_Worker(), model="tier_1")
    assert res["model"] == "default"


def test_early_error_result_keeps_full_message():
    long_error = "x" * 300
    assert _early_error_result("t-4", long_error)["error"] == long_error
