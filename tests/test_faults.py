"""Unit tests for meister.faults crash-point injection."""

import os
import signal
import subprocess
import sys
import textwrap


def test_crash_point_noop_without_env(monkeypatch):
    """crash_point returns immediately when MEISTER_CRASH_AT is unset."""
    monkeypatch.delenv("MEISTER_CRASH_AT", raising=False)
    from meister.faults import crash_point

    # Must not raise or kill
    crash_point("after_run_created")
    crash_point("after_fast_forward_before_state", task_id="t1")


def test_crash_point_noop_wrong_name(monkeypatch):
    """crash_point is a no-op when MEISTER_CRASH_AT doesn't match."""
    monkeypatch.setenv("MEISTER_CRASH_AT", "after_run_completed")
    from meister.faults import crash_point

    crash_point("after_run_created")  # different name — must not kill


def test_crash_point_kills_subprocess(tmp_path):
    """When MEISTER_CRASH_AT matches, the process is killed with SIGKILL."""
    script = tmp_path / "victim.py"
    script.write_text(textwrap.dedent("""\
        import os, sys
        sys.path.insert(0, os.environ["MEISTER_PROJECT_ROOT"])
        from meister.faults import crash_point
        crash_point("test_kill_point")
        print("SHOULD NOT REACH HERE")
    """))

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["MEISTER_CRASH_AT"] = "test_kill_point"
    env["MEISTER_PROJECT_ROOT"] = project_root
    env.pop("MEISTER_LOG_DIR", None)

    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # SIGKILL → returncode -9
    assert result.returncode == -signal.SIGKILL, (
        f"Expected rc={-signal.SIGKILL}, got {result.returncode}; "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "SHOULD NOT REACH HERE" not in result.stdout


def test_crash_point_nth_occurrence(tmp_path):
    """MEISTER_CRASH_NTH=2 skips the first hit and kills on the second."""
    script = tmp_path / "victim_nth.py"
    script.write_text(textwrap.dedent("""\
        import os, sys
        sys.path.insert(0, os.environ["MEISTER_PROJECT_ROOT"])
        from meister.faults import crash_point
        crash_point("nth_point")  # 1st — should NOT kill
        sys.stdout.write("SURVIVED_FIRST\\n")
        sys.stdout.flush()
        crash_point("nth_point")  # 2nd — should kill
        print("SHOULD NOT REACH HERE")
    """))

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["MEISTER_CRASH_AT"] = "nth_point"
    env["MEISTER_CRASH_NTH"] = "2"
    env["MEISTER_PROJECT_ROOT"] = project_root
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("MEISTER_LOG_DIR", None)

    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == -signal.SIGKILL
    assert "SURVIVED_FIRST" in result.stdout
    assert "SHOULD NOT REACH HERE" not in result.stdout


def test_crash_point_task_filter(tmp_path):
    """MEISTER_CRASH_TASK filters by task_id kwarg."""
    script = tmp_path / "victim_task.py"
    script.write_text(textwrap.dedent("""\
        import os, sys
        sys.path.insert(0, os.environ["MEISTER_PROJECT_ROOT"])
        from meister.faults import crash_point
        crash_point("task_point", task_id="t1")  # no match — should NOT kill
        sys.stdout.write("SURVIVED_T1\\n")
        sys.stdout.flush()
        crash_point("task_point", task_id="t2")  # match — should kill
        print("SHOULD NOT REACH HERE")
    """))

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["MEISTER_CRASH_AT"] = "task_point"
    env["MEISTER_CRASH_TASK"] = "t2"
    env["MEISTER_PROJECT_ROOT"] = project_root
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("MEISTER_LOG_DIR", None)

    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == -signal.SIGKILL
    assert "SURVIVED_T1" in result.stdout
    assert "SHOULD NOT REACH HERE" not in result.stdout


def test_crash_point_logs_event(tmp_path):
    """Fault injection writes a JSONL event before killing."""
    script = tmp_path / "victim_log.py"
    log_dir = tmp_path / "logs"
    script.write_text(textwrap.dedent(f"""\
        import os, sys
        sys.path.insert(0, os.environ["MEISTER_PROJECT_ROOT"])
        os.environ["MEISTER_LOG_DIR"] = {str(log_dir)!r}
        from meister.faults import crash_point
        crash_point("log_point", task_id="t_log")
    """))

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["MEISTER_CRASH_AT"] = "log_point"
    env["MEISTER_PROJECT_ROOT"] = project_root
    env["MEISTER_LOG_DIR"] = str(log_dir)

    subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        timeout=10,
    )

    import json
    log_file = log_dir / "faults.jsonl"
    assert log_file.exists(), "faults.jsonl should have been created"
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["event"] == "fault_injected"
    assert record["crash_point"] == "log_point"
    assert record["task_id"] == "t_log"
