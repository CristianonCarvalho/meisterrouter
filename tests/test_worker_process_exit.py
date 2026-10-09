"""Morte do processo `run-task` do worker sem resultado: detecção imediata pelo bridge.

Os testes usam dublês do Herdr (nunca o real) e um repositório git temporário.
"""

import asyncio
import os
import re
import stat
import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest

from meister.config import load_config
from meister.herdr.bridge import HerdrEventBridge
from meister.i18n import reset_language_cache
from meister.progress import format_event_line
from meister.worker import write_atomic_json

_ACCENTS = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")
RESULT_PAYLOAD = {"status": "done", "modified_files": []}


def _config(tmp_path, tiers=("A",), idle=600, retries=1):
    tier_yaml = "".join(
        f"    - name: {name}\n      harness: codex\n      model: {name}\n" for name in tiers
    )
    cfg_file = tmp_path / "process_exit_config.yaml"
    cfg_file.write_text(
        "router:\n  mode: first\n"
        f"retry:\n  pane_lost_attempts: {retries}\n  pane_lost_backoff_seconds: 0\n"
        f"workers:\n  idle_timeout_seconds: {idle}\n  max_runtime_seconds: 0\n"
        f"  tier_order:\n{tier_yaml}"
        "concurrency:\n  parallel_tasks: false\n  max_parallel_workers: 1\n"
        "  layout_strategy: tiled\n  isolation_mode: none\n"
    )
    return load_config(str(cfg_file))


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    (repo / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    return repo


def _subtask(repo, task_id="proc"):
    return {
        "id": task_id,
        "description": f"Process exit {task_id}",
        "target_files": ["app.py"],
        "cwd": str(repo),
    }


def _bridge(config, behavior):
    """Bridge com spawn falso; `behavior(attempt, task_context)` simula o que o pane faz."""
    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.read_pane.return_value = "building\nImportError: no module named meister_missing"
    bridge = HerdrEventBridge(config=config, client=mock_client)
    spawns = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawns.append(tier_name)
        behavior(len(spawns), task_context)
        return f"w1:p{len(spawns)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    return bridge, spawns


def _events(log_mock, event_type):
    return [c.kwargs for c in log_mock.call_args_list if c.kwargs.get("event_type") == event_type]


def _write_exit(task_context, code):
    with open(task_context["exit_file"], "w", encoding="utf-8") as handle:
        handle.write(f"{code}\n")


def _write_result_later(loop, task_context, delay):
    loop.call_later(delay, write_atomic_json, task_context["result_file"], dict(RESULT_PAYLOAD))


@pytest.mark.parametrize("code", [3, 0])
def test_command_str_records_worker_exit_code_in_exit_file(tmp_path, monkeypatch, code):
    """Roda o command_str real com um `sys.executable` que sai com `code`: o exit_file guarda o código."""
    monkeypatch.chdir(tmp_path)
    script = tmp_path / f"fake_run_task_{code}.sh"
    script.write_text(f"#!/bin/sh\nexit {code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(sys, "executable", str(script))
    repo = _repo(tmp_path)
    seen = {}

    def behavior(attempt, task_context):
        seen["exit_file"] = task_context["exit_file"]
        seen["result_file"] = task_context["result_file"]
        subprocess.run(task_context["command_str"], shell=True, check=False)

    bridge, spawns = _bridge(_config(tmp_path, retries=0), behavior)
    with patch("meister.herdr.bridge.log_event") as log_mock:
        result = asyncio.run(asyncio.wait_for(bridge.execute_subtask(_subtask(repo)), timeout=20))

    assert result is False
    assert spawns == ["A"]
    assert seen["exit_file"] == os.path.splitext(seen["result_file"])[0] + ".exit"
    assert seen["exit_file"].endswith("_1.exit")
    with open(seen["exit_file"], encoding="utf-8") as handle:
        assert handle.read().strip() == str(code)
    exited = _events(log_mock, "worker_process_exited")
    assert len(exited) == 1
    assert exited[0]["exit_code"] == code


@pytest.mark.parametrize("code", [1, 0])
def test_process_death_without_result_retries_same_tier_and_fails_fast(tmp_path, monkeypatch, code):
    monkeypatch.chdir(tmp_path)
    repo = _repo(tmp_path)

    def behavior(attempt, task_context):
        _write_exit(task_context, code)

    bridge, spawns = _bridge(_config(tmp_path, tiers=("A", "B"), idle=600, retries=1), behavior)
    started = time.monotonic()
    with patch("meister.herdr.bridge.log_event") as log_mock, patch.object(
        bridge.get_state_manager(), "record_harness_failure"
    ) as record_failure:
        result = asyncio.run(asyncio.wait_for(bridge.execute_subtask(_subtask(repo)), timeout=20))
    elapsed = time.monotonic() - started

    assert result is False
    assert elapsed < 10, "devia perceber a morte do processo sem esperar o timeout de inatividade"
    assert spawns == ["A", "A"], "deve tentar de novo na mesma via e nunca escalar"
    record_failure.assert_not_called()

    exited = _events(log_mock, "worker_process_exited")
    assert len(exited) == 2
    assert all(event["exit_code"] == code for event in exited)

    retries = _events(log_mock, "worker_retry")
    assert len(retries) == 1
    assert retries[0]["reason"] == "process_exit"
    assert retries[0]["exit_code"] == code
    assert retries[0]["tier"] == "A"
    assert retries[0]["retry"] == 1

    errors = _events(log_mock, "worker_error")
    assert len(errors) == 1
    assert "retentativas esgotadas: 1" in errors[0]["error"]


def test_result_landing_during_grace_window_wins_over_process_exit(tmp_path, monkeypatch):
    """exit_file aparece antes do resultado: o recheck depois da espera de 0,5 s segue o caminho normal."""
    monkeypatch.chdir(tmp_path)
    repo = _repo(tmp_path)

    def behavior(attempt, task_context):
        _write_exit(task_context, 0)
        _write_result_later(asyncio.get_running_loop(), task_context, 0.1)

    bridge, spawns = _bridge(_config(tmp_path, retries=1), behavior)
    with patch("meister.herdr.bridge.log_event") as log_mock:
        asyncio.run(asyncio.wait_for(bridge.execute_subtask(_subtask(repo)), timeout=20))

    assert spawns == ["A"]
    assert _events(log_mock, "worker_process_exited") == []
    assert _events(log_mock, "worker_retry") == []


def test_stale_exit_file_from_previous_attempt_is_not_reused(tmp_path, monkeypatch):
    """A retentativa na mesma via remove o exit_file velho antes de lançar o pane novo."""
    monkeypatch.chdir(tmp_path)
    repo = _repo(tmp_path)

    def behavior(attempt, task_context):
        if attempt == 1:
            _write_exit(task_context, 1)
        else:
            _write_result_later(asyncio.get_running_loop(), task_context, 1.0)

    bridge, spawns = _bridge(_config(tmp_path, retries=1), behavior)
    with patch("meister.herdr.bridge.log_event") as log_mock:
        result = asyncio.run(asyncio.wait_for(bridge.execute_subtask(_subtask(repo)), timeout=20))

    assert spawns == ["A", "A"]
    assert result is True
    assert len(_events(log_mock, "worker_process_exited")) == 1


def test_leftover_exit_file_at_spawn_path_is_removed_before_spawn(tmp_path, monkeypatch):
    """Um exit_file deixado no caminho do próximo lançamento não pode disparar detecção falsa."""
    monkeypatch.chdir(tmp_path)
    repo = _repo(tmp_path)
    runs_dir = os.path.join(str(repo), ".meister", "runs")
    os.makedirs(runs_dir, exist_ok=True)
    stale = os.path.join(runs_dir, "None_proc_1.exit")
    with open(stale, "w", encoding="utf-8") as handle:
        handle.write("1\n")

    def behavior(attempt, task_context):
        assert task_context["exit_file"] == stale
        _write_result_later(asyncio.get_running_loop(), task_context, 1.0)

    bridge, spawns = _bridge(_config(tmp_path, retries=0), behavior)
    with patch("meister.herdr.bridge.log_event") as log_mock:
        result = asyncio.run(asyncio.wait_for(bridge.execute_subtask(_subtask(repo)), timeout=20))

    assert spawns == ["A"]
    assert result is True
    assert _events(log_mock, "worker_process_exited") == []


def test_progress_lines_for_process_exit_and_gate_repair_in_both_languages(monkeypatch):
    record_exit = {
        "event_type": "worker_retry",
        "task_id": "task",
        "tier": "copilot",
        "retry": 1,
        "max_retries": 2,
        "reason": "process_exit",
        "exit_code": 1,
    }
    record_gate = {
        "event_type": "worker_retry",
        "task_id": "task",
        "tier": "copilot",
        "retry": 2,
        "max_retries": 3,
        "reason": "gate_repair",
    }
    expected = {
        "en": (
            "[1/3] task worker process exited with code 1; retry 1/2 on copilot",
            "[1/3] task gate repair 2/3 on copilot",
        ),
        "pt-BR": (
            "[1/3] task processo do worker encerrou com código 1; retentativa 1/2 em copilot",
            "[1/3] task reparo do gate 2/3 em copilot",
        ),
    }
    try:
        for language, (exit_line, gate_line) in expected.items():
            monkeypatch.setenv("MEISTER_LANG", language)
            reset_language_cache()
            assert format_event_line(record_exit, 1, 3) == exit_line
            assert format_event_line(record_gate, 1, 3) == gate_line
            if language == "en":
                assert not _ACCENTS.search(exit_line + gate_line)
    finally:
        monkeypatch.undo()
        reset_language_cache()


def test_existing_retry_lines_are_unchanged(monkeypatch):
    lines = {}
    try:
        for language in ("en", "pt-BR"):
            monkeypatch.setenv("MEISTER_LANG", language)
            reset_language_cache()
            lines[language] = (
                format_event_line(
                    {"event_type": "worker_retry", "task_id": "task", "tier": "copilot",
                     "retry": 2, "max_retries": 3, "reason": "pane_lost"}, 1, 3),
                format_event_line(
                    {"event_type": "worker_retry", "task_id": "task", "tier": "copilot",
                     "retry": 2, "max_retries": 3, "reason": "timeout"}, 1, 3),
            )
    finally:
        monkeypatch.undo()
        reset_language_cache()
    assert lines["en"] == (
        "[1/3] task pane lost; retry 2/3 on copilot",
        "[1/3] task timeout; retry 2/3 on copilot",
    )
    assert lines["pt-BR"] == (
        "[1/3] task pane perdido; retentativa 2/3 em copilot",
        "[1/3] task timeout; retentativa 2/3 em copilot",
    )
