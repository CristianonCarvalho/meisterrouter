"""Read-only analysis of canonical execution plans."""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence, Set

from meister.herdr.dag import SubtaskNode, build_subtask_dag
from meister.i18n import t


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
        warning = t(
            "commands.plan.serial_warning",
            tasks=task_count,
            batches=batch_count,
        )
        if by_file_estimate["rounds"] < rounds:
            warning += t(
                "commands.plan.file_estimate_warning",
                rounds=by_file_estimate["rounds"],
            )
        warnings.append(warning)
    if unscoped_tasks:
        warnings.append(
            t("commands.plan.unscoped_warning", tasks=", ".join(unscoped_tasks))
        )
    for item in hot_files:
        if len(item["tasks"]) >= 3:
            warnings.append(
                t(
                    "commands.plan.hot_file_warning",
                    file=item["file"],
                    count=len(item["tasks"]),
                )
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
            return t(
                "commands.plan.serial_warning",
                tasks=len(tasks),
                batches=len(batches),
            )
    except Exception:
        return None
    return None


def format_analysis_table(analysis: Dict[str, Any], max_workers: int) -> str:
    """Render an analysis as a plain-text Portuguese report."""
    lines = [
        t("commands.plan.title"),
        (
            t(
                "commands.plan.metrics",
                tasks=analysis["tasks"],
                batches=analysis["batch_count"],
                width=analysis["max_width"],
            )
        ),
        t("commands.plan.rounds", workers=max_workers, rounds=analysis["rounds"]),
        f"Serial ratio: {analysis['serial_ratio']:.2f}",
        t("commands.plan.batches"),
    ]
    if analysis["batches"]:
        lines.extend(
            t("commands.plan.batch", index=index, tasks=", ".join(batch))
            for index, batch in enumerate(analysis["batches"], start=1)
        )
    else:
        lines.append(t("commands.plan.none"))

    path = analysis["critical_path"]
    lines.extend(
        [
            t(
                "commands.plan.critical_path",
                length=path["length"],
                tasks=" → ".join(path["tasks"]) if path["tasks"] else "(nenhum)",
            ),
            t("commands.plan.why_not_parallel"),
        ]
    )
    if not analysis["why_not_parallel"]:
        lines.append(t("commands.plan.none"))
    for item in analysis["why_not_parallel"]:
        detail = t(
            "commands.plan.reason",
            kind=item["kind"],
            tasks=", ".join(item["with"]) or "(nenhum)",
        )
        if item["kind"] == "file_conflict":
            detail = t(
                "commands.plan.file_conflict",
                kind=item["kind"],
                tasks=", ".join(item["with"]) or "(nenhum)",
                files=", ".join(item["files"]),
            )
        lines.append(f"  {item['task']}: {detail}")

    lines.append(t("commands.plan.hot_files"))
    if analysis["hot_files"]:
        lines.extend(
            f"  {item['file']}: {', '.join(item['tasks'])}"
            for item in analysis["hot_files"]
        )
    else:
        lines.append(t("commands.plan.none"))

    lines.append(t("commands.plan.unscoped_tasks"))
    lines.append("  " + (", ".join(analysis["unscoped_tasks"]) or t("commands.plan.none_feminine").strip()))
    estimate = analysis["by_file_estimate"]
    lines.append(
        t(
            "commands.plan.file_estimate",
            batches=estimate["batch_count"],
            rounds=estimate["rounds"],
        )
    )
    lines.append(t("commands.plan.warnings"))
    if analysis["warnings"]:
        lines.extend(f"  - {warning}" for warning in analysis["warnings"])
    else:
        lines.append(t("commands.plan.none"))
    return "\n".join(lines)
