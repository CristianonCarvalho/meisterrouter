"""Motor de orquestração do MeisterRouter (independente do host)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meister.engine.bridge import HerdrEventBridge as HerdrEventBridge
    from meister.engine.bridge import parse_architect_plan as parse_architect_plan
    from meister.engine.dag import (
        CycleDetectedError as CycleDetectedError,
        SubtaskNode as SubtaskNode,
        TaskDAG as TaskDAG,
        build_subtask_dag as build_subtask_dag,
        get_independent_batches as get_independent_batches,
    )
    from meister.engine.workers import (
        WorkerSpawner as WorkerSpawner,
        detect_quota_or_rate_limit as detect_quota_or_rate_limit,
        get_next_tier as get_next_tier,
        spawn_worker_pane as spawn_worker_pane,
    )

# Carga preguiçosa: o bridge e o workers importam meister.herdr.client, cujo pacote reexporta
# estes nomes; importar tudo aqui de uma vez criaria um ciclo de importação.
_EXPORTS = {
    "WorkerSpawner": "workers",
    "detect_quota_or_rate_limit": "workers",
    "spawn_worker_pane": "workers",
    "get_next_tier": "workers",
    "SubtaskNode": "dag",
    "TaskDAG": "dag",
    "build_subtask_dag": "dag",
    "get_independent_batches": "dag",
    "CycleDetectedError": "dag",
    "HerdrEventBridge": "bridge",
    "parse_architect_plan": "bridge",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
