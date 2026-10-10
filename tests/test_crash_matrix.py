"""
tests/test_crash_matrix.py — Systematic crash-consistency matrix test.

For each of the 13 crash points:
  (a) Run with MEISTER_CRASH_AT=<point> → expect rc == -9
  (b) Run the SAME command without crash → expect success
  (c) Check invariants I1-I7

Also tests per-subtask variations, double crash, and idempotent resume.

Runs in CI without LLM, without real Herdr, without network.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from tests.platform_marks import posix_only

pytestmark = posix_only


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRASH_POINTS = [
    "after_run_created",
    "after_integration_started",
    "after_worker_spawned",
    "after_worker_result",
    "before_worker_gate",
    "after_worker_commit",
    "after_merge_before_gate",
    "after_merge_before_state",
    "after_subtask_completed",
    "before_final_gate",
    "before_fast_forward",
    "after_fast_forward_before_state",
    "after_run_completed",
]

# Points that are per-subtask (fire once per task_id)
PER_SUBTASK_POINTS = [
    "after_worker_spawned",
    "after_worker_result",
    "before_worker_gate",
    "after_worker_commit",
    "after_merge_before_gate",
    "after_merge_before_state",
    "after_subtask_completed",
]

# Points chosen for double-crash testing (justification in docstrings)
DOUBLE_CRASH_POINTS = [
    # after_merge_before_state: most critical — git says integrated but SQLite disagrees
    "after_merge_before_state",
    # after_fast_forward_before_state: main has work, SQLite says RUNNING
    "after_fast_forward_before_state",
    # after_worker_spawned: worker running, crash, resume, crash again
    "after_worker_spawned",
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER_PATH = os.path.join(PROJECT_ROOT, "tests", "crash_driver.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_env(
    repo_dir: str,
    state_dir: str,
    crash_at: Optional[str] = None,
    crash_task: Optional[str] = None,
    crash_nth: Optional[str] = None,
) -> Dict[str, str]:
    """Build a clean environment for the driver subprocess."""
    isolated_home = os.path.join(state_dir, "home")
    os.makedirs(isolated_home, exist_ok=True)
    env = {
        "HOME": isolated_home,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": PROJECT_ROOT,
        "PYTHONUNBUFFERED": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "OPENROUTER_API_KEY": "crash-matrix-sentinel",
        "OPENROUTER_DECISIONS_URL": "http://127.0.0.1:9/",
        "MEISTER_DB_PATH": os.path.join(state_dir, "meister.db"),
        "MEISTER_LOG_DIR": os.path.join(state_dir, "logs"),
        "MEISTER_WORKTREES_DIR": os.path.join(state_dir, "wt"),
        "GIT_AUTHOR_NAME": "CrashMatrix",
        "GIT_AUTHOR_EMAIL": "crash@matrix.test",
        "GIT_COMMITTER_NAME": "CrashMatrix",
        "GIT_COMMITTER_EMAIL": "crash@matrix.test",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if crash_at:
        env["MEISTER_CRASH_AT"] = crash_at
    if crash_task:
        env["MEISTER_CRASH_TASK"] = crash_task
    if crash_nth:
        env["MEISTER_CRASH_NTH"] = crash_nth
    return env


def run_driver(
    repo_dir: str,
    state_dir: str,
    crash_at: Optional[str] = None,
    crash_task: Optional[str] = None,
    crash_nth: Optional[str] = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess:
    """Run the crash driver in a subprocess."""
    env = make_env(repo_dir, state_dir, crash_at, crash_task, crash_nth)
    return subprocess.run(
        [sys.executable, DRIVER_PATH, repo_dir, state_dir],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def setup_repo(tmp_path: Path) -> Tuple[str, str]:
    """Create temp repo and state dir, return (repo_dir, state_dir)."""
    repo_dir = str(tmp_path / "repo")
    state_dir = str(tmp_path / "state")
    os.makedirs(state_dir, exist_ok=True)
    os.makedirs(os.path.join(state_dir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(state_dir, "wt"), exist_ok=True)

    # Use the driver's setup function
    sys.path.insert(0, os.path.dirname(DRIVER_PATH))
    from crash_driver import setup_temp_repo
    setup_temp_repo(repo_dir)

    return repo_dir, state_dir


# ---------------------------------------------------------------------------
# Invariant Checks
# ---------------------------------------------------------------------------

def assert_invariants(
    repo_dir: str,
    state_dir: str,
    point: str,
    expect_completed: bool = True,
) -> Dict[str, str]:
    """Check all 7 invariants. Returns dict of {invariant: PASS/FAIL}."""
    results = {}

    def check(name: str, condition: bool, detail: str = ""):
        results[name] = "PASS" if condition else f"FAIL: {detail}"

    db_path = os.path.join(state_dir, "meister.db")
    main_content = {}
    for fname in ["calc.py", "text.py", "utils.py",
                   "tests/test_calc.py", "tests/test_text.py", "tests/test_utils.py"]:
        try:
            res = subprocess.run(
                ["git", "show", f"main:{fname}"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
            main_content[fname] = res.stdout if res.returncode == 0 else ""
        except Exception:
            main_content[fname] = ""

    # I1: nothing lost — main has ALL subtask effects
    has_mul = "def mul" in main_content.get("calc.py", "")
    has_shout = "def shout" in main_content.get("text.py", "")
    has_reverse = "def reverse" in main_content.get("utils.py", "")
    has_test_mul = "test_mul" in main_content.get("tests/test_calc.py", "")
    has_test_shout = "test_shout" in main_content.get("tests/test_text.py", "")
    has_test_reverse = "test_reverse" in main_content.get("tests/test_utils.py", "")

    if expect_completed:
        i1_ok = all([has_mul, has_shout, has_reverse, has_test_mul, has_test_shout, has_test_reverse])
        flags = {"mul": has_mul, "shout": has_shout, "reverse": has_reverse,
                 "test_mul": has_test_mul, "test_shout": has_test_shout,
                 "test_reverse": has_test_reverse}
        missing = [name for name, ok in flags.items() if not ok]
        check("I1", i1_ok, f"missing: {missing}")
    else:
        check("I1", True, "skipped: not expected to complete")

    # I2: nothing duplicated — each function/test appears exactly once
    i2_ok = True
    i2_detail = []
    for func, content in [("def mul", main_content.get("calc.py", "")),
                          ("def shout", main_content.get("text.py", "")),
                          ("def reverse", main_content.get("utils.py", ""))]:
        count = content.count(func)
        if count > 1:
            i2_ok = False
            i2_detail.append(f"{func} appears {count} times")
    check("I2", i2_ok, "; ".join(i2_detail))

    # I3: COMPLETED ⇒ I1 and I2
    run_state = None
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT state FROM runs ORDER BY updated_at DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                run_state = row["state"]
            conn.close()
        except Exception:
            pass

    if run_state == "COMPLETED":
        check("I3", results.get("I1") == "PASS" and results.get("I2") == "PASS",
              f"run_state=COMPLETED but I1={results.get('I1')}, I2={results.get('I2')}")
    else:
        check("I3", True, f"run_state={run_state}, not COMPLETED")

    # I4: main never has partial/failing work
    if expect_completed and has_mul:
        try:
            res = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=no"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=15,
            )
            check("I4", res.returncode == 0, f"pytest failed on main: {res.stdout[-200:]}")
        except Exception as e:
            check("I4", False, f"pytest error: {e}")
    else:
        check("I4", True, "main not advanced or not expected to complete")

    # I5: no leftovers after final run
    if expect_completed and run_state == "COMPLETED":
        # worktrees
        try:
            res = subprocess.run(
                ["git", "worktree", "list"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
            wt_count = len(res.stdout.strip().splitlines())
            check_wt = wt_count == 1
        except Exception:
            check_wt = False

        # branches
        try:
            res = subprocess.run(
                ["git", "branch", "--list", "meister/worktree/*"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
            leftover_wt_branches = res.stdout.strip()
        except Exception:
            leftover_wt_branches = "error"

        # Herdr state
        herdr_file = os.path.join(state_dir, "herdr_state.json")
        open_panes = 0
        if os.path.exists(herdr_file):
            try:
                with open(herdr_file) as f:
                    hstate = json.load(f)
                open_panes = len([p for p in hstate.get("panes", {}).values() if not p.get("closed")])
            except Exception:
                pass

        i5_ok = check_wt and not leftover_wt_branches
        check("I5", i5_ok, f"worktrees={wt_count}, branches='{leftover_wt_branches}', open_panes={open_panes}")
    else:
        check("I5", True, "skipped: run not COMPLETED")

    # I6: already-integrated subtask not re-executed
    spawn_file = os.path.join(state_dir, "spawn_counts.json")
    if os.path.exists(spawn_file):
        try:
            with open(spawn_file) as f:
                spawns = json.load(f)
            # Each task should be spawned at most twice (crash + resume)
            over_spawned = {k: v for k, v in spawns.items() if v > 2}
            check("I6", len(over_spawned) == 0, f"over-spawned: {over_spawned}")
        except Exception as e:
            check("I6", False, f"spawn_counts error: {e}")
    else:
        check("I6", True, "no spawn_counts.json")

    # I7: idempotency (checked separately in test)
    check("I7", True, "checked separately")

    return results


# ---------------------------------------------------------------------------
# Test parametrization
# ---------------------------------------------------------------------------

def test_crash_driver_no_network(tmp_path):
    """The driver completes without contacting a local Jev endpoint, even with dotenv files."""
    requests: List[str] = []
    requests_lock = threading.Lock()
    driver_pid: List[Optional[int]] = [None]

    class RequestCounter(BaseHTTPRequestHandler):
        def do_POST(self):
            with requests_lock:
                requests.append(self.path)
                pid = driver_pid[0]
            if pid is not None:
                os.kill(pid, signal.SIGKILL)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RequestCounter)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        repo_dir, state_dir = setup_repo(tmp_path)
        home_dir = Path(state_dir) / "home"
        meister_home = home_dir / ".meister"
        meister_home.mkdir(parents=True)
        dotenv_content = (
            "OPENROUTER_API_KEY=dotenv-sentinel\n"
            f"OPENROUTER_DECISIONS_URL=http://127.0.0.1:{server.server_port}/decisions\n"
        )
        (Path(repo_dir) / ".env").write_text(dotenv_content)
        (meister_home / ".env").write_text(dotenv_content)

        env = make_env(repo_dir, state_dir)
        env["OPENROUTER_DECISIONS_URL"] = (
            f"http://127.0.0.1:{server.server_port}/decisions"
        )
        assert env["OPENROUTER_API_KEY"] == "crash-matrix-sentinel"
        assert env["PYTHON_DOTENV_DISABLED"] == "1"
        assert env["HOME"] == str(home_dir)

        driver = subprocess.Popen(
            [sys.executable, DRIVER_PATH, repo_dir, state_dir],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with requests_lock:
            driver_pid[0] = driver.pid
        try:
            stdout, stderr = driver.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            driver.kill()
            stdout, stderr = driver.communicate()
            pytest.fail(f"Driver timed out. stdout: {stdout[-500:]}; stderr: {stderr[-500:]}")
        assert driver.returncode == 0, (
            f"Driver did not complete (rc={driver.returncode}). "
            f"stdout: {stdout[-500:]}; stderr: {stderr[-500:]}"
        )
        with requests_lock:
            assert requests == [], f"Driver contacted the Jev endpoint: {requests}"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


@pytest.mark.parametrize("crash_point", CRASH_POINTS, ids=CRASH_POINTS)
def test_crash_matrix_basic(tmp_path, crash_point):
    """For each crash point: crash → resume → check invariants."""
    repo_dir, state_dir = setup_repo(tmp_path)

    # (a) Run with crash injection — expect SIGKILL (rc == -9)
    result_crash = run_driver(repo_dir, state_dir, crash_at=crash_point)
    assert result_crash.returncode == -signal.SIGKILL, (
        f"Point '{crash_point}' was NOT reached (rc={result_crash.returncode}). "
        f"stderr: {result_crash.stderr[-500:]}"
    )

    # (b) Resume without crash — expect success
    result_resume = run_driver(repo_dir, state_dir)
    assert result_resume.returncode == 0, (
        f"Resume after '{crash_point}' failed (rc={result_resume.returncode}). "
        f"stderr: {result_resume.stderr[-500:]}"
    )

    # (c) Check invariants
    inv = assert_invariants(repo_dir, state_dir, crash_point)
    for name, status in inv.items():
        assert status == "PASS" or status.startswith("PASS"), (
            f"[{crash_point}] {name}: {status}"
        )


@pytest.mark.parametrize(
    "crash_point,task_id",
    [
        (p, t) for p in ["after_worker_result", "after_merge_before_state", "after_subtask_completed"]
        for t in ["t1", "t2"]
    ],
    ids=[
        f"{p}@{t}" for p in ["after_worker_result", "after_merge_before_state", "after_subtask_completed"]
        for t in ["t1", "t2"]
    ],
)
def test_crash_matrix_per_subtask(tmp_path, crash_point, task_id):
    """Per-subtask crash: crash at a specific task, resume, check invariants."""
    repo_dir, state_dir = setup_repo(tmp_path)

    result_crash = run_driver(repo_dir, state_dir, crash_at=crash_point, crash_task=task_id)
    assert result_crash.returncode == -signal.SIGKILL, (
        f"Point '{crash_point}@{task_id}' NOT reached (rc={result_crash.returncode})"
    )

    result_resume = run_driver(repo_dir, state_dir)
    assert result_resume.returncode == 0, (
        f"Resume after '{crash_point}@{task_id}' failed (rc={result_resume.returncode}). "
        f"stderr: {result_resume.stderr[-500:]}"
    )

    inv = assert_invariants(repo_dir, state_dir, f"{crash_point}@{task_id}")
    for name, status in inv.items():
        assert status == "PASS" or status.startswith("PASS"), (
            f"[{crash_point}@{task_id}] {name}: {status}"
        )


@pytest.mark.parametrize("crash_point", DOUBLE_CRASH_POINTS, ids=[f"double_{p}" for p in DOUBLE_CRASH_POINTS])
def test_crash_matrix_double(tmp_path, crash_point):
    """Double crash: crash → resume with crash → resume clean → check invariants.

    Justification for chosen points:
    - after_merge_before_state: most critical inconsistency (git vs SQLite)
    - after_fast_forward_before_state: main advanced but run not COMPLETED
    - after_worker_spawned: worker started, crash, resume starts worker again, crash again
    """
    repo_dir, state_dir = setup_repo(tmp_path)

    # First crash
    r1 = run_driver(repo_dir, state_dir, crash_at=crash_point)
    assert r1.returncode == -signal.SIGKILL, f"1st crash NOT reached (rc={r1.returncode})"

    # Second crash (on resume)
    run_driver(repo_dir, state_dir, crash_at=crash_point)
    # May or may not hit the crash point again depending on state
    # (if the point is after completion and we resumed, it might succeed)

    # Final clean resume
    r3 = run_driver(repo_dir, state_dir)
    assert r3.returncode == 0, (
        f"Final resume after double crash at '{crash_point}' failed (rc={r3.returncode}). "
        f"stderr: {r3.stderr[-500:]}"
    )

    inv = assert_invariants(repo_dir, state_dir, f"double_{crash_point}")
    for name, status in inv.items():
        assert status == "PASS" or status.startswith("PASS"), (
            f"[double_{crash_point}] {name}: {status}"
        )


def test_crash_matrix_idempotent_after_success(tmp_path):
    """I7: Resume after COMPLETED is a no-op."""
    repo_dir, state_dir = setup_repo(tmp_path)

    # Run to completion
    r1 = run_driver(repo_dir, state_dir)
    assert r1.returncode == 0, f"Initial run failed (rc={r1.returncode})"

    # Capture main HEAD
    head_before = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Resume again — should be no-op
    r2 = run_driver(repo_dir, state_dir)
    assert r2.returncode == 0, f"Idempotent resume failed (rc={r2.returncode})"

    head_after = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert head_before == head_after, (
        f"I7 violated: main moved from {head_before} to {head_after} on idempotent resume"
    )


def test_crash_matrix_report(tmp_path, capsys):
    """Print the full crash matrix table and write report file."""
    table_rows: List[Tuple[str, Dict[str, str]]] = []

    for point in CRASH_POINTS:
        repo_dir = str(tmp_path / f"report_{point}" / "repo")
        state_dir = str(tmp_path / f"report_{point}" / "state")
        os.makedirs(state_dir, exist_ok=True)
        os.makedirs(os.path.join(state_dir, "logs"), exist_ok=True)
        os.makedirs(os.path.join(state_dir, "wt"), exist_ok=True)

        sys.path.insert(0, os.path.dirname(DRIVER_PATH))
        from crash_driver import setup_temp_repo
        setup_temp_repo(repo_dir)

        # Crash
        r_crash = run_driver(repo_dir, state_dir, crash_at=point)
        crashed = r_crash.returncode == -signal.SIGKILL

        # Resume
        if crashed:
            r_resume = run_driver(repo_dir, state_dir)
            resumed = r_resume.returncode == 0
        else:
            resumed = False

        # Invariants
        inv = assert_invariants(repo_dir, state_dir, point, expect_completed=resumed)
        inv["CRASH"] = "PASS" if crashed else "FAIL"
        inv["RESUME"] = "PASS" if resumed else "FAIL"
        table_rows.append((point, inv))

    # Print table
    headers = ["CRASH_POINT", "CRASH", "RESUME", "I1", "I2", "I3", "I4", "I5", "I6", "I7"]
    col_widths = [max(len(h), 35) for h in headers]

    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    sep_line = "-+-".join("-" * w for w in col_widths)

    lines = [header_line, sep_line]
    for point, inv in table_rows:
        row = [point] + [inv.get(h, "N/A")[:35] for h in headers[1:]]
        lines.append(" | ".join(v.ljust(w) for v, w in zip(row, col_widths)))

    table = "\n".join(lines)
    print("\n" + table)

    # Write report file
    report_path = tmp_path / "crash_matrix_report.txt"
    report_path.write_text(table + "\n")
    print(f"\nReport written to: {report_path}")

    # Assert all passed
    for point, inv in table_rows:
        for key in ["CRASH", "RESUME", "I1", "I2", "I3"]:
            val = inv.get(key, "N/A")
            assert val == "PASS" or val.startswith("PASS"), f"[{point}] {key}: {val}"
