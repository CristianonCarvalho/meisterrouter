"""Tests for Dependency Analysis & Parallel Task DAG."""

import pytest
from meister.herdr.dag import (
    SubtaskNode,
    TaskDAG,
    build_subtask_dag,
    get_independent_batches,
    CycleDetectedError,
)


def test_dag_parallel_batching():
    """Verify basic parallel batching with dependency resolution."""
    steps = [
        {"id": "t1", "description": "Auth backend", "target_files": ["src/auth.py"], "depends_on": []},
        {"id": "t2", "description": "Login frontend", "target_files": ["src/login.tsx"], "depends_on": []},
        {"id": "t3", "description": "Integrate tests", "target_files": ["tests/test_auth.py"], "depends_on": ["t1", "t2"]},
    ]
    dag = build_subtask_dag(steps)
    batches = dag.get_execution_batches()

    # t1 and t2 should execute in parallel in the first batch
    assert len(batches) == 2
    batch1_ids = {n.id for n in batches[0]}
    assert batch1_ids == {"t1", "t2"}
    assert [n.id for n in batches[1]] == ["t3"]


def test_get_independent_batches_standalone():
    """Verify standalone get_independent_batches function matches dag method."""
    steps = [
        {"id": "t1", "description": "Core", "target_files": ["core.py"], "depends_on": []},
        {"id": "t2", "description": "UI", "target_files": ["ui.py"], "depends_on": ["t1"]},
    ]
    dag = build_subtask_dag(steps)
    batches = get_independent_batches(dag)
    assert len(batches) == 2
    assert [n.id for n in batches[0]] == ["t1"]
    assert [n.id for n in batches[1]] == ["t2"]


def test_dag_file_overlap_serialization():
    """Verify tasks targeting the same file are serialized into separate batches."""
    steps = [
        {"id": "t1", "description": "Add auth login endpoint", "target_files": ["src/auth.py"], "depends_on": []},
        {"id": "t2", "description": "Add auth logout endpoint", "target_files": ["src/auth.py"], "depends_on": []},
        {"id": "t3", "description": "Add frontend styles", "target_files": ["src/styles.css"], "depends_on": []},
    ]
    dag = build_subtask_dag(steps)
    batches = dag.get_execution_batches()

    # t1 and t3 can run in parallel in batch 0; t2 must be in batch 1 due to src/auth.py conflict with t1
    assert len(batches) == 2
    batch0_ids = {n.id for n in batches[0]}
    assert batch0_ids == {"t1", "t3"}
    batch1_ids = {n.id for n in batches[1]}
    assert batch1_ids == {"t2"}


def test_dag_detect_file_conflicts():
    """Verify detection of file overlap conflicts across tasks."""
    steps = [
        {"id": "t1", "description": "Task 1", "target_files": ["src/auth.py", "src/user.py"], "depends_on": []},
        {"id": "t2", "description": "Task 2", "target_files": ["src/user.py", "src/profile.py"], "depends_on": []},
        {"id": "t3", "description": "Task 3", "target_files": ["src/billing.py"], "depends_on": []},
    ]
    dag = build_subtask_dag(steps)
    conflicts = dag.detect_file_conflicts()

    # Should detect conflict between t1 and t2 on src/user.py
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert set(conflict["task_ids"]) == {"t1", "t2"}
    assert conflict["overlapping_files"] == ["src/user.py"]


def test_dag_cycle_detection():
    """Verify cyclic dependencies raise CycleDetectedError."""
    steps = [
        {"id": "t1", "description": "Task 1", "target_files": ["a.py"], "depends_on": ["t2"]},
        {"id": "t2", "description": "Task 2", "target_files": ["b.py"], "depends_on": ["t1"]},
    ]
    with pytest.raises(CycleDetectedError, match="Cycle detected"):
        dag = build_subtask_dag(steps)
        dag.get_execution_batches()


def test_dag_unknown_dependency():
    """Verify referencing a non-existent task raises ValueError."""
    steps = [
        {"id": "t1", "description": "Task 1", "target_files": ["a.py"], "depends_on": ["t99"]},
    ]
    with pytest.raises(ValueError, match="unknown task"):
        build_subtask_dag(steps)


def test_dag_empty_steps():
    """Verify empty task list produces empty DAG batches."""
    dag = build_subtask_dag([])
    assert dag.get_execution_batches() == []
    assert len(dag.nodes) == 0


def test_subtask_node_properties():
    """Verify SubtaskNode fields, defaults, and conflict check."""
    node1 = SubtaskNode(id="t1", description="Backend", target_files=["src/auth.py", "./src/utils.py"])
    node2 = SubtaskNode(id="t2", description="Auth util", target_files=["src/utils.py"])
    node3 = SubtaskNode(id="t3", description="Frontend", target_files=["src/login.tsx"])

    assert node1.conflicts_with(node2) is True
    assert node1.conflicts_with(node3) is False
    assert node1.status == "pending"
    assert node1.depends_on == []
