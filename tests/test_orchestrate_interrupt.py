"""Interrupção do `meister orchestrate` (Ctrl+C / SIGTERM / SIGHUP) deve registrar o término da run."""

import asyncio
import os
import signal
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.config import MeisterConfig
from meister.herdr import bridge as bridge_module
from meister.herdr.bridge import HerdrEventBridge
from meister.i18n import t
from meister.logger import get_events_by_run_id
from meister.state import RunState, StateManager
from meister.worktree import IntegrationPipeline
from tests.platform_marks import posix_only


PLAN = "1. Add feature"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEISTER_WORKTREES_DIR", str(tmp_path / "worktrees"))


def _bridge(tmp_path, isolation="none"):
    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = isolation
    client = AsyncMock()
    client.is_connected = True
    client.close_pane = AsyncMock()
    client.show_notification = AsyncMock()
    sm = StateManager(str(tmp_path / "state.db"))
    return HerdrEventBridge(config=cfg, client=client, state_manager=sm), client, sm


def _cycle(bridge):
    return bridge.run_orchestration_cycle(workspace_id="w1", architect_pane_id="w1:p0", task=PLAN)


def _run_state(sm, run_id):
    return sm.get_run(run_id)["state"]


def _end_events(run_id):
    return [e for e in get_events_by_run_id(run_id) if e.get("event_type") == "orchestration_end"]


def _assert_interrupted_record(sm, run_id):
    assert _run_state(sm, run_id) == RunState.FAILED.value
    ends = _end_events(run_id)
    assert ends, "orchestration_end ausente após a interrupção"
    last = ends[-1]
    assert last.get("status") == "interrupted"
    assert last.get("exit_code") == 130


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.mark.asyncio
async def test_cancelled_cycle_marks_run_failed_and_reraises_cancelled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge, client, sm = _bridge(tmp_path)
    started = asyncio.Event()

    async def hang(steps):
        started.set()
        await asyncio.Event().wait()
        return True

    bridge.execute_plan = hang
    task = asyncio.ensure_future(_cycle(bridge))
    await asyncio.wait_for(started.wait(), timeout=5)
    run_id = bridge.current_run_id
    assert _run_state(sm, run_id) == RunState.RUNNING.value

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    _assert_interrupted_record(sm, run_id)


@pytest.mark.asyncio
async def test_keyboard_interrupt_inside_cycle_marks_run_failed_and_reraises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge, _client, sm = _bridge(tmp_path)

    async def interrupt(steps):
        raise KeyboardInterrupt

    bridge.execute_plan = interrupt
    with pytest.raises(KeyboardInterrupt):
        await _cycle(bridge)

    _assert_interrupted_record(sm, bridge.current_run_id)


@pytest.mark.asyncio
async def test_interrupt_still_reraises_and_sweeps_panes_when_event_write_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge, client, sm = _bridge(tmp_path)
    real_log_event = bridge_module.log_event

    def failing_log_event(*args, **kwargs):
        if kwargs.get("status") == "interrupted":
            raise RuntimeError("disk full")
        return real_log_event(*args, **kwargs)

    async def interrupt(steps):
        sm.register_pane("w1:p_dangling", run_id=bridge.current_run_id)
        raise KeyboardInterrupt

    bridge.execute_plan = interrupt
    monkeypatch.setattr("meister.herdr.bridge.log_event", failing_log_event)
    with pytest.raises(KeyboardInterrupt):
        await _cycle(bridge)

    assert _run_state(sm, bridge.current_run_id) == RunState.FAILED.value
    closed = [c.args[0] for c in client.close_pane.call_args_list]
    assert "w1:p_dangling" in closed


@pytest.mark.asyncio
async def test_interrupt_still_reraises_and_sweeps_panes_when_state_write_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge, client, sm = _bridge(tmp_path)

    async def interrupt(steps):
        sm.register_pane("w1:p_dangling", run_id=bridge.current_run_id)
        raise KeyboardInterrupt

    bridge.execute_plan = interrupt
    real_transition = StateManager.transition_run

    def failing_transition(self, run_id, to_state, metadata=None):
        if to_state == RunState.FAILED:
            raise RuntimeError("sqlite locked")
        return real_transition(self, run_id, to_state, metadata)

    monkeypatch.setattr(StateManager, "transition_run", failing_transition)
    with pytest.raises(KeyboardInterrupt):
        await _cycle(bridge)

    closed = [c.args[0] for c in client.close_pane.call_args_list]
    assert "w1:p_dangling" in closed
    assert _run_state(sm, bridge.current_run_id) == RunState.RUNNING.value


@pytest.mark.asyncio
@pytest.mark.parametrize("final_state", [RunState.FAILED, RunState.COMPLETED])
async def test_interrupt_keeps_already_terminal_run_state(tmp_path, monkeypatch, final_state):
    monkeypatch.chdir(tmp_path)
    bridge, _client, sm = _bridge(tmp_path)

    async def finish_then_interrupt(steps):
        sm.transition_run(bridge.current_run_id, to_state=final_state)
        raise KeyboardInterrupt

    bridge.execute_plan = finish_then_interrupt
    with pytest.raises(KeyboardInterrupt):
        await _cycle(bridge)

    assert _run_state(sm, bridge.current_run_id) == final_state.value


@pytest.mark.asyncio
async def test_interrupt_keeps_worktrees_branches_and_run_resumable(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "ci@meister.local")
    _git(repo, "config", "user.name", "Meister CI")
    (repo / "app.py").write_text("print('hi')\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    bridge, _client, sm = _bridge(tmp_path, isolation="git_worktree")
    snapshot = {}
    abort_calls = []

    real_abort = IntegrationPipeline.abort_integration

    def spy_abort(self, *args, **kwargs):
        abort_calls.append(True)
        return real_abort(self, *args, **kwargs)

    monkeypatch.setattr(IntegrationPipeline, "abort_integration", spy_abort)

    async def interrupt(steps):
        snapshot["worktrees"] = _git(repo, "worktree", "list", "--porcelain")
        snapshot["branches"] = _git(repo, "branch", "--format=%(refname:short)")
        raise KeyboardInterrupt

    bridge.execute_plan = interrupt
    with pytest.raises(KeyboardInterrupt):
        await _cycle(bridge)

    run_id = bridge.current_run_id
    assert abort_calls == []
    assert snapshot["worktrees"].count("worktree ") > 1
    assert _git(repo, "worktree", "list", "--porcelain") == snapshot["worktrees"]
    assert _git(repo, "branch", "--format=%(refname:short)") == snapshot["branches"]
    assert _run_state(sm, run_id) == RunState.FAILED.value
    assert sm.find_resumable_run(os.getcwd(), exclude_run_id="other-run")["run_id"] == run_id


def _invoke_orchestrate(bridge_cls_side_effect, current_run_id=None):
    with patch("meister.cli.HerdrEventBridge") as bridge_cls:
        bridge = bridge_cls.return_value
        bridge.current_run_id = current_run_id
        bridge.run_orchestration_cycle = AsyncMock(side_effect=bridge_cls_side_effect)
        return CliRunner().invoke(main, ["orchestrate", "--workspace-id", "ws-main", "-q"])


def test_cli_keyboard_interrupt_exits_130_with_run_id_and_no_traceback():
    async def interrupted(**_kwargs):
        raise KeyboardInterrupt

    result = _invoke_orchestrate(interrupted, current_run_id="run-int-1234")

    assert result.exit_code == 130
    assert "run-int-1234" in result.output
    assert "meister orchestrate --resume" in result.output
    assert "Traceback" not in result.output


def test_cli_interrupt_without_run_prints_short_message_and_exits_130():
    async def interrupted(**_kwargs):
        raise KeyboardInterrupt

    result = _invoke_orchestrate(interrupted, current_run_id=None)

    assert result.exit_code == 130
    assert t("interrupt.cli_message_no_run") in result.output
    assert "Traceback" not in result.output


@posix_only
def test_cli_sigterm_during_orchestrate_exits_130_with_run_id():
    async def terminated(**_kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(5)

    result = _invoke_orchestrate(terminated, current_run_id="run-term-5678")

    assert result.exit_code == 130
    assert "run-term-5678" in result.output
    assert "Traceback" not in result.output


@posix_only
def test_cli_sighup_during_orchestrate_exits_130_with_run_id():
    async def hung_up(**_kwargs):
        os.kill(os.getpid(), signal.SIGHUP)
        await asyncio.sleep(5)

    result = _invoke_orchestrate(hung_up, current_run_id="run-hup-9999")

    assert result.exit_code == 130
    assert "run-hup-9999" in result.output

