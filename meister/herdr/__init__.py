"""MeisterRouter Herdr Integration Module."""

from meister.herdr.client import HerdrSocketClient, HerdrRPCError, HerdrConnectionError
from meister.herdr.workers import (
    WorkerSpawner,
    detect_quota_or_rate_limit,
    spawn_worker_pane,
    get_next_tier,
)
from meister.herdr.dag import (
    SubtaskNode,
    TaskDAG,
    build_subtask_dag,
    get_independent_batches,
    CycleDetectedError,
)
from meister.herdr.bridge import (
    HerdrEventBridge,
    parse_architect_plan,
)

__all__ = [
    "HerdrSocketClient",
    "HerdrRPCError",
    "HerdrConnectionError",
    "WorkerSpawner",
    "detect_quota_or_rate_limit",
    "spawn_worker_pane",
    "get_next_tier",
    "SubtaskNode",
    "TaskDAG",
    "build_subtask_dag",
    "get_independent_batches",
    "CycleDetectedError",
    "HerdrEventBridge",
    "parse_architect_plan",
]

