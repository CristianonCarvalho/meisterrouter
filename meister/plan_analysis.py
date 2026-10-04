"""Read-only analysis of canonical execution plans."""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence, Set

from meister.herdr.dag import SubtaskNode, build_subtask_dag


def _normalized_files(task: Dict[str, Any]) -> Set[str]:
    return {
        os.path.normpath(path.strip())
        for path in task.get("target_files", [])
        if path and path.strip()
    }


def _batch_metrics(batches: List[List[SubtaskNode]], max_workers: int) -> Dict[str, Any]:
    return {
        "batch_count": len(batches),
        "rounds": sum(math.ceil(len(batch) / max_workers) for batch in batches),
    }


def analyze_plan(tasks: Sequence[Dict[str, Any]], max_workers: int) -> Dict[str, Any]:
    """Analyze execution batches, dependencies, and file-based parallelism."""
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")

    dag = build_subtask_dag(tasks)
    nodes = list(dag.nodes.values())
    scheduled_batches = dag.get_execution_batches()
    batches = [[node.id for node in batch] for batch in scheduled_batches]
    batch_count = len(batches)
    rounds = sum(math.ceil(len(batch) / max_workers) for batch in batches)
    task_count = len(tasks)
    serial_ratio = rounds / task_count if task_count else 0.0

    files_by_id = {task["id"]: _normalized_files(task) for task in tasks}
    dependencies = {node.id: list(node.depends_on) for node in nodes}

    depth_cache: Dict[str, int] = {}
    path_cache: Dict[str, List[str]] = {}

    def dependency_path(task_id: str) -> List[str]:
        if task_id in path_cache:
            return path_cache[task_id]
        parents = dependencies[task_id]
        best_parent_path: List[str] = []
        for parent_id in parents:
            candidate = dependency_path(parent_id)
            if len(candidate) > len(best_parent_path):
                best_parent_path = candidate
        path = best_parent_path + [task_id]
        path_cache[task_id] = path
        depth_cache[task_id] = len(path)
        return path

    all_paths = [dependency_path(node.id) for node in nodes]
    critical_path = max(all_paths, key=len) if all_paths else []

    # Mirror the scheduler's greedy pass to capture the ready tasks that
    # actually prevented a task from joining an earlier batch.
    deferred_by: Dict[str, List[str]] = {}
    completed: Set[str] = set()
    remaining = [node.id for node in nodes]
    for scheduled_batch in scheduled_batches:
        ready = [
            task_id
            for task_id in remaining
            if all(parent in completed for parent in dependencies[task_id])
        ]
        selected: List[str] = []
        for candidate_id in ready:
            candidate = dag.nodes[candidate_id]
            blockers = [
                selected_id
                for selected_id in selected
                if candidate.conflicts_with(dag.nodes[selected_id])
            ]
            if blockers:
                deferred_by[candidate_id] = blockers
            else:
                selected.append(candidate_id)
        for task_id in (node.id for node in scheduled_batch):
            completed.add(task_id)
            remaining.remove(task_id)

    why_not_parallel: List[Dict[str, Any]] = []
    for batch_index, batch_ids in enumerate(batches, start=1):
        if batch_index == 1:
            continue
        for task_id in batch_ids:
            if depth_cache[task_id] == batch_index:
                why_not_parallel.append(
                    {"task": task_id, "kind": "dependency", "with": dependencies[task_id]}
                )
                continue

            blockers = deferred_by.get(task_id, [])
            common_files = sorted(
                {
                    path
                    for blocker in blockers
                    for path in files_by_id[task_id] & files_by_id[blocker]
                }
            )
            is_unscoped_conflict = not files_by_id[task_id] or any(
                not files_by_id[blocker] for blocker in blockers
            )
            reason: Dict[str, Any] = {
                "task": task_id,
                "kind": "unscoped" if is_unscoped_conflict else "file_conflict",
                "with": blockers,
            }
            if not is_unscoped_conflict:
                reason["files"] = common_files
            why_not_parallel.append(reason)

    file_users: Dict[str, List[str]] = {}
    for task in tasks:
        for path in files_by_id[task["id"]]:
            file_users.setdefault(path, []).append(task["id"])
    hot_files = [
        {"file": path, "tasks": users}
        for path, users in file_users.items()
        if len(users) >= 2
    ]
    hot_files.sort(key=lambda item: (-len(item["tasks"]), item["file"]))

    unscoped_tasks = [task["id"] for task in tasks if not files_by_id[task["id"]]]

    file_only_tasks = []
    prior_tasks: List[Dict[str, Any]] = []
    for task in tasks:
        task_files = files_by_id[task["id"]]
        if task_files:
            task_dependencies = [
                previous["id"]
                for previous in prior_tasks
                if task_files & files_by_id[previous["id"]]
            ]
        else:
            task_dependencies = [previous["id"] for previous in prior_tasks]
        file_only_tasks.append({**task, "depends_on": task_dependencies})
        prior_tasks.append(task)
    file_only_dag = build_subtask_dag(file_only_tasks)
    by_file_batches = file_only_dag.get_execution_batches()
    by_file_estimate = _batch_metrics(by_file_batches, max_workers)

    warnings: List[str] = []
    if task_count >= 3 and batch_count == task_count:
        warning = f"plano totalmente serial ({task_count} tarefas em {batch_count} lotes)"
        if by_file_estimate["rounds"] < rounds:
            warning += f"; a análise por arquivo daria {by_file_estimate['rounds']} passos"
        warnings.append(warning)
    if unscoped_tasks:
        warnings.append(
            "tarefas sem target_files rodam isoladas: " + ", ".join(unscoped_tasks)
        )
    for item in hot_files:
        if len(item["tasks"]) >= 3:
            warnings.append(
                f"arquivo quente {item['file']} declarado por {len(item['tasks'])} tarefas"
            )

    return {
        "tasks": task_count,
        "edges": sum(len(node.depends_on) for node in nodes),
        "batches": batches,
        "batch_count": batch_count,
        "max_width": max((len(batch) for batch in batches), default=0),
        "rounds": rounds,
        "serial_ratio": serial_ratio,
        "critical_path": {"tasks": critical_path, "length": len(critical_path)},
        "why_not_parallel": why_not_parallel,
        "hot_files": hot_files,
        "unscoped_tasks": unscoped_tasks,
        "by_file_estimate": by_file_estimate,
        "warnings": warnings,
    }


def serial_plan_warning(tasks: Sequence[Dict[str, Any]]) -> Optional[str]:
    """Return the serial-plan warning, or None if the plan is not fully serial."""
    try:
        if len(tasks) < 3:
            return None
        batches = build_subtask_dag(tasks).get_execution_batches()
        if len(batches) == len(tasks):
            return f"plano totalmente serial ({len(tasks)} tarefas em {len(batches)} lotes)"
    except Exception:
        return None
    return None


def format_analysis_table(analysis: Dict[str, Any], max_workers: int) -> str:
    """Render an analysis as a plain-text Portuguese report."""
    lines = [
        "Análise do plano",
        (
            f"Tarefas: {analysis['tasks']} | Lotes: {analysis['batch_count']} | "
            f"Largura máxima: {analysis['max_width']}"
        ),
        f"Passos sequenciais (até {max_workers} workers): {analysis['rounds']}",
        f"Serial ratio: {analysis['serial_ratio']:.2f}",
        "Lotes:",
    ]
    if analysis["batches"]:
        lines.extend(
            f"  Lote {index}: {', '.join(batch)}"
            for index, batch in enumerate(analysis["batches"], start=1)
        )
    else:
        lines.append("  (nenhum)")

    path = analysis["critical_path"]
    lines.extend(
        [
            f"Caminho crítico ({path['length']} tarefas): "
            + (" → ".join(path["tasks"]) if path["tasks"] else "(nenhum)"),
            "Por que não paralelo:",
        ]
    )
    if not analysis["why_not_parallel"]:
        lines.append("  (nenhum)")
    for item in analysis["why_not_parallel"]:
        detail = f"{item['kind']}: {', '.join(item['with']) or '(nenhum)'}"
        if item["kind"] == "file_conflict":
            detail += f" (arquivos: {', '.join(item['files'])})"
        lines.append(f"  {item['task']}: {detail}")

    lines.append("Arquivos quentes:")
    if analysis["hot_files"]:
        lines.extend(
            f"  {item['file']}: {', '.join(item['tasks'])}"
            for item in analysis["hot_files"]
        )
    else:
        lines.append("  (nenhum)")

    lines.append("Tarefas sem escopo:")
    lines.append("  " + (", ".join(analysis["unscoped_tasks"]) or "(nenhuma)"))
    estimate = analysis["by_file_estimate"]
    lines.append(
        "Estimativa por arquivo (ignora dependência semântica): "
        f"{estimate['batch_count']} lotes; {estimate['rounds']} passos"
    )
    lines.append("Avisos:")
    if analysis["warnings"]:
        lines.extend(f"  - {warning}" for warning in analysis["warnings"])
    else:
        lines.append("  (nenhum)")
    return "\n".join(lines)
