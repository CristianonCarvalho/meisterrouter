"""Dependency Analysis & Parallel Task DAG.

Decomposes execution plans into a Directed Acyclic Graph (DAG) of subtasks,
detects dependency cycles, analyzes file overlaps to prevent concurrent write
conflicts, and computes optimal parallel execution batches for Herdr split panes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class CycleDetectedError(ValueError):
    """Raised when circular dependencies are detected in the subtask DAG."""


@dataclass
class SubtaskNode:
    """Represents an individual unit of work in the task DAG."""

    id: str
    description: str = ""
    target_files: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def normalized_target_files(self) -> Set[str]:
        """Return the normalized set of target file paths."""
        return {os.path.normpath(f.strip()) for f in self.target_files if f and f.strip()}

    def conflicts_with(self, other: SubtaskNode) -> bool:
        """Check if this subtask shares any target files with another subtask."""
        if not self.target_files or not other.target_files:
            return False
        return bool(self.normalized_target_files() & other.normalized_target_files())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SubtaskNode:
        """Construct a SubtaskNode from a dictionary specification."""
        return cls(
            id=str(data.get("id", "")),
            description=str(data.get("description", "")),
            target_files=list(data.get("target_files", [])),
            depends_on=list(data.get("depends_on", [])),
            status=str(data.get("status", "pending")),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert the SubtaskNode to a dictionary representation."""
        return {
            "id": self.id,
            "description": self.description,
            "target_files": list(self.target_files),
            "depends_on": list(self.depends_on),
            "status": self.status,
            "metadata": dict(self.metadata),
        }


class TaskDAG:
    """Directed Acyclic Graph (DAG) of subtasks with concurrency analysis."""

    def __init__(self, nodes: Optional[List[SubtaskNode] | Dict[str, SubtaskNode]] = None) -> None:
        self.nodes: Dict[str, SubtaskNode] = {}
        if nodes:
            if isinstance(nodes, dict):
                for k, v in nodes.items():
                    self.add_node(v)
            else:
                for node in nodes:
                    self.add_node(node)

    def add_node(self, node: SubtaskNode) -> None:
        """Add a subtask node to the DAG."""
        self.nodes[node.id] = node

    def get_node(self, task_id: str) -> Optional[SubtaskNode]:
        """Retrieve a subtask node by its ID."""
        return self.nodes.get(task_id)

    def validate(self) -> None:
        """Validate the DAG integrity.

        Checks:
        1. All referenced dependencies exist in the DAG.
        2. No circular dependencies exist (topological cycle check).

        Raises:
            ValueError: If a task depends on an unknown task ID.
            CycleDetectedError: If a cycle is detected among dependencies.
        """
        # 1. Dependency existence check
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"Task '{node.id}' depends on unknown task '{dep}'")

        # 2. Cycle detection via Kahn's algorithm
        in_degree = {nid: 0 for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on:
                # dep must execute before node, so node depends on dep
                pass
            in_degree[node.id] = len(node.depends_on)

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited_count = 0

        # Adjacency list: dep -> list of nodes that depend on dep
        dependents: Dict[str, List[str]] = {nid: [] for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on:
                dependents[dep].append(node.id)

        while queue:
            curr = queue.pop(0)
            visited_count += 1
            for dependent_id in dependents.get(curr, []):
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    queue.append(dependent_id)

        if visited_count < len(self.nodes):
            cyclic_nodes = [nid for nid, deg in in_degree.items() if deg > 0]
            raise CycleDetectedError(
                f"Cycle detected in task dependencies involving: {sorted(cyclic_nodes)}"
            )

    def detect_file_conflicts(self) -> List[Dict[str, Any]]:
        """Identify pairs of subtasks that share target files.

        Returns a list of conflict summaries:
            [{"task_ids": [t1, t2], "overlapping_files": [file1, ...]}]
        """
        conflicts: List[Dict[str, Any]] = []
        node_list = list(self.nodes.values())
        seen_pairs: Set[tuple[str, str]] = set()

        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                n1 = node_list[i]
                n2 = node_list[j]
                pair_key = tuple(sorted([n1.id, n2.id]))
                if pair_key in seen_pairs:
                    continue

                common_files = sorted(
                    list(n1.normalized_target_files() & n2.normalized_target_files())
                )
                if common_files:
                    seen_pairs.add(pair_key)
                    conflicts.append(
                        {
                            "task_ids": [n1.id, n2.id],
                            "overlapping_files": common_files,
                        }
                    )
        return conflicts

    def get_execution_batches(self) -> List[List[SubtaskNode]]:
        """Compute parallel execution batches.

        Tasks within a single batch:
        1. Have all their dependencies satisfied by preceding batches.
        2. Do not share any target files with other tasks in the same batch
           (avoiding concurrent modification conflicts).

        Returns:
            List of batches, where each batch is a list of independent SubtaskNodes.
        """
        self.validate()

        if not self.nodes:
            return []

        completed_ids: Set[str] = set()
        remaining_ids: List[str] = list(self.nodes.keys())
        batches: List[List[SubtaskNode]] = []

        while remaining_ids:
            # Candidate tasks whose dependencies are already completely met
            ready_candidates = [
                self.nodes[tid]
                for tid in remaining_ids
                if all(dep in completed_ids for dep in self.nodes[tid].depends_on)
            ]

            if not ready_candidates:
                # Should have been caught by validate(), but safeguard against deadlock
                raise CycleDetectedError(
                    f"Deadlock or unresolvable dependencies among remaining tasks: {remaining_ids}"
                )

            current_batch: List[SubtaskNode] = []
            current_batch_files: Set[str] = set()

            for candidate in ready_candidates:
                candidate_files = candidate.normalized_target_files()
                # Check for file conflict with any node already added to current batch
                if candidate_files and (candidate_files & current_batch_files):
                    # Conflicts with a task already scheduled in this batch; defer to next batch
                    continue

                current_batch.append(candidate)
                current_batch_files.update(candidate_files)

            # If all ready candidates conflicted with one another, at least the first one runs
            if not current_batch and ready_candidates:
                single_node = ready_candidates[0]
                current_batch.append(single_node)

            for node in current_batch:
                completed_ids.add(node.id)
                remaining_ids.remove(node.id)

            batches.append(current_batch)

        return batches


def build_subtask_dag(actionable_steps: List[Dict[str, Any] | SubtaskNode]) -> TaskDAG:
    """Build and validate a TaskDAG from a list of action steps or SubtaskNodes."""
    dag = TaskDAG()
    for step in actionable_steps:
        if isinstance(step, SubtaskNode):
            dag.add_node(step)
        elif isinstance(step, dict):
            dag.add_node(SubtaskNode.from_dict(step))
        else:
            raise TypeError(f"Expected dict or SubtaskNode, got {type(step).__name__}")

    # Validate immediately to catch missing dependencies early
    for node in dag.nodes.values():
        for dep in node.depends_on:
            if dep not in dag.nodes:
                raise ValueError(f"Task '{node.id}' depends on unknown task '{dep}'")

    return dag


def get_independent_batches(dag: TaskDAG) -> List[List[SubtaskNode]]:
    """Compute independent parallel batches for a given TaskDAG."""
    return dag.get_execution_batches()
