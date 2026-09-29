"""
meister.faults — Fault injection hook for crash-consistency testing.

Provides ``crash_point(name, **ctx)`` that reads environment variables
``MEISTER_CRASH_AT``, ``MEISTER_CRASH_TASK``, and ``MEISTER_CRASH_NTH``
to decide whether to kill the current process with SIGKILL.

When ``MEISTER_CRASH_AT`` is not set the function returns immediately
with zero overhead.  Production behaviour is never affected.
"""

from __future__ import annotations

import json
import os
import signal
import time
from typing import Any

# Global counter per crash-point name for NTH matching
_hit_counts: dict[str, int] = {}


def crash_point(name: str, **ctx: Any) -> None:
    """Conditionally kill the process at a named crash point.

    Environment variables
    ---------------------
    MEISTER_CRASH_AT   : str
        Name of the crash point to trigger.  If unset or empty, no-op.
    MEISTER_CRASH_TASK : str, optional
        If set, only match when ``ctx["task_id"]`` equals this value.
    MEISTER_CRASH_NTH  : str, optional
        1-based occurrence number (default ``"1"``).  Only the Nth
        invocation of the matching crash point fires.
    """
    target = os.environ.get("MEISTER_CRASH_AT")
    if not target:
        return  # fast path — zero cost in production

    if target != name:
        return

    # Per-subtask filtering
    target_task = os.environ.get("MEISTER_CRASH_TASK")
    if target_task:
        task_id = ctx.get("task_id")
        if task_id is None or str(task_id) != target_task:
            return

    # NTH occurrence filtering
    nth = int(os.environ.get("MEISTER_CRASH_NTH", "1"))
    _hit_counts[name] = _hit_counts.get(name, 0) + 1
    if _hit_counts[name] != nth:
        return

    # Log the fault injection event (JSONL, flushed)
    log_dir = os.environ.get("MEISTER_LOG_DIR")
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "faults.jsonl")
            record = {
                "event": "fault_injected",
                "crash_point": name,
                "timestamp": time.time(),
                "pid": os.getpid(),
                **{k: str(v) for k, v in ctx.items()},
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass  # best-effort logging

    # SIGKILL — works from any thread, including asyncio.to_thread
    os.kill(os.getpid(), signal.SIGKILL)
