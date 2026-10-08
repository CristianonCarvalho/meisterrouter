"""Log sintético compartilhado pelos testes da linha do tempo."""

import json
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def ev(kind, task_id, t, run="r1", **fields):
    return {"event_type": kind, "run_id": run, "task_id": task_id, "ts": at(t).isoformat(), **fields}


def phase(task, end_t, name, duration_s, run="r1"):
    return ev("worker_phase", task, end_t, run=run, phase=name, duration_ms=duration_s * 1000.0)


PLAN = [
    {"id": "task_1", "depends_on": []},
    {"id": "task_2", "depends_on": []},
    {"id": "task_3", "depends_on": ["task_1", "task_2"]},
]


def parallel_events(run="r1"):
    """task_1 e task_2 em paralelo; task_3 depende das duas e está rodando em t=30."""
    return [
        ev("orchestration_start", "orchestrator", 0, run=run, task=json.dumps(PLAN)),
        ev(
            "plan_parsed",
            "orchestrator",
            0.1,
            run=run,
            total=3,
            batches=2,
            task_ids=["task_1", "task_2", "task_3"],
            task_titles={
                "task_1": "Task 1: Base",
                "task_2": "Task 2: API",
                "task_3": "Task 3: Docs",
            },
        ),
        ev("worker_spawn", "task_1", 1, run=run, tier="tier_1b"),
        ev("worker_spawn", "task_2", 1, run=run, tier="tier_1b"),
        phase("task_1", 11, "worker", 10, run=run),
        phase("task_1", 12, "gate", 1, run=run),
        phase("task_1", 12, "lock_wait", 0, run=run),
        phase("task_1", 13, "integrate", 1, run=run),
        ev(
            "subtask_completed",
            "task_1",
            13,
            run=run,
            tier="tier_1b",
            cost=0.5,
            cost_source="reported",
        ),
        phase("task_2", 21, "worker", 20, run=run),
        phase("task_2", 22, "gate", 1, run=run),
        phase("task_2", 23, "integrate", 1, run=run),
        ev(
            "subtask_completed",
            "task_2",
            23,
            run=run,
            tier="tier_2",
            cost=0.25,
            cost_source="reported",
        ),
        ev("worker_spawn", "task_3", 23.5, run=run, tier="tier_1b"),
    ]


def worker_cli_events(run="worker_cli", task_id="5b936c6b64348070"):
    """Formato real de `meister worker` sem eventos de orquestração."""
    return [
        ev("classify", task_id, 0.2, run=run, tier="jev", duration_ms=100),
        ev("classify", task_id, 0.5, run=run, tier="jev", duration_ms=100),
        ev("worker_start", task_id, 1, run=run, tier="tier_1b"),
        ev("worktree_setup_ok", "int_run_" + run, 1.5, run=run, tier="unknown"),
        ev("worktree_setup_ok", "worker_run_" + run, 2, run=run, tier="unknown"),
        phase(task_id, 8, "worker", 7, run=run),
        ev(
            "worker_end",
            task_id,
            10,
            run=run,
            status="timeout",
            exit_code=1,
            error="Worker excedeu o timeout de 180.0s",
        ),
    ]
