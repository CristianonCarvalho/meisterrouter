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


def test_subtask_node_normalizes_backslash_and_dot_paths():
    """Caminhos com `\\` ou `./` se normalizam para a forma com `/` e colidem entre si."""
    node1 = SubtaskNode(id="t1", target_files=["src\\user.py"])
    node2 = SubtaskNode(id="t2", target_files=["./src/user.py"])

    assert node1.normalized_target_files() == {"src/user.py"}
    assert node2.normalized_target_files() == {"src/user.py"}
    assert node1.conflicts_with(node2) is True

    dag = TaskDAG([node1, node2])
    conflicts = dag.detect_file_conflicts()
    assert conflicts == [{"task_ids": ["t1", "t2"], "overlapping_files": ["src/user.py"]}]


def test_dag_two_independent_unscoped_tasks_serialized():
    """(a) duas tarefas independentes sem escopo → 2 lotes (uma por lote)."""
    steps = [
        {"id": "t1", "description": "Freeform 1", "target_files": [], "depends_on": []},
        {"id": "t2", "description": "Freeform 2", "target_files": [], "depends_on": []},
    ]
    dag = build_subtask_dag(steps)
    batches = dag.get_execution_batches()
    assert len(batches) == 2
    assert [n.id for n in batches[0]] == ["t1"]
    assert [n.id for n in batches[1]] == ["t2"]


def test_dag_scoped_and_unscoped_independent_both_orders():
    """(b) uma com escopo e uma sem, independentes, nas duas ordens do plano → lotes separados."""
    # Ordem 1: scoped primeiro, unscoped depois
    steps1 = [
        {"id": "t1", "description": "Scoped", "target_files": ["src/app.py"], "depends_on": []},
        {"id": "t2", "description": "Unscoped", "target_files": [], "depends_on": []},
    ]
    dag1 = build_subtask_dag(steps1)
    batches1 = dag1.get_execution_batches()
    assert len(batches1) == 2
    assert [n.id for n in batches1[0]] == ["t1"]
    assert [n.id for n in batches1[1]] == ["t2"]

    # Ordem 2: unscoped primeiro, scoped depois
    steps2 = [
        {"id": "t1", "description": "Unscoped", "target_files": [], "depends_on": []},
        {"id": "t2", "description": "Scoped", "target_files": ["src/app.py"], "depends_on": []},
    ]
    dag2 = build_subtask_dag(steps2)
    batches2 = dag2.get_execution_batches()
    assert len(batches2) == 2
    assert [n.id for n in batches2[0]] == ["t1"]
    assert [n.id for n in batches2[1]] == ["t2"]


def test_dag_disjoint_scoped_and_unscoped():
    """(c) duas com escopos disjuntos + uma sem escopo → as duas com escopo continuam no mesmo lote e a sem escopo fica sozinha em outro."""
    # Se scoped vem antes: [t1, t2] no lote 0, t3 no lote 1
    steps1 = [
        {"id": "t1", "description": "Backend", "target_files": ["backend.py"], "depends_on": []},
        {"id": "t2", "description": "Frontend", "target_files": ["frontend.py"], "depends_on": []},
        {"id": "t3", "description": "Unscoped", "target_files": [], "depends_on": []},
    ]
    dag1 = build_subtask_dag(steps1)
    batches1 = dag1.get_execution_batches()
    assert len(batches1) == 2
    assert {n.id for n in batches1[0]} == {"t1", "t2"}
    assert [n.id for n in batches1[1]] == ["t3"]

    # Se unscoped vem antes: t3 no lote 0, [t1, t2] no lote 1
    steps2 = [
        {"id": "t3", "description": "Unscoped", "target_files": [], "depends_on": []},
        {"id": "t1", "description": "Backend", "target_files": ["backend.py"], "depends_on": []},
        {"id": "t2", "description": "Frontend", "target_files": ["frontend.py"], "depends_on": []},
    ]
    dag2 = build_subtask_dag(steps2)
    batches2 = dag2.get_execution_batches()
    assert len(batches2) == 2
    assert [n.id for n in batches2[0]] == ["t3"]
    assert {n.id for n in batches2[1]} == {"t1", "t2"}


def test_dag_five_unscoped_tasks_five_batches_no_deadlock():
    """(d) cinco tarefas sem escopo → 5 lotes, sem deadlock (o teste termina)."""
    steps = [
        {"id": f"t{i}", "description": f"Unscoped {i}", "target_files": [], "depends_on": []}
        for i in range(1, 6)
    ]
    dag = build_subtask_dag(steps)
    batches = dag.get_execution_batches()
    assert len(batches) == 5
    for i, batch in enumerate(batches, 1):
        assert [n.id for n in batch] == [f"t{i}"]


def test_dag_whitespace_empty_strings_target_files_count_as_unscoped():
    """(e) entradas só com espaços/strings vazias em target_files contam como sem escopo."""
    steps = [
        {"id": "t1", "description": "Task 1", "target_files": ["  ", ""], "depends_on": []},
        {"id": "t2", "description": "Task 2", "target_files": ["\t\n"], "depends_on": []},
    ]
    dag = build_subtask_dag(steps)
    batches = dag.get_execution_batches()
    assert len(batches) == 2
    assert [n.id for n in batches[0]] == ["t1"]
    assert [n.id for n in batches[1]] == ["t2"]


def test_dag_depends_on_respected_with_unscoped_tasks():
    """(f) depends_on continua respeitado com tarefas sem escopo."""
    steps = [
        {"id": "t1", "description": "Setup unscoped", "target_files": [], "depends_on": []},
        {"id": "t2", "description": "Scoped child", "target_files": ["core.py"], "depends_on": ["t1"]},
        {"id": "t3", "description": "Unscoped grandchild", "target_files": [], "depends_on": ["t2"]},
    ]
    dag = build_subtask_dag(steps)
    batches = dag.get_execution_batches()
    assert len(batches) == 3
    assert [n.id for n in batches[0]] == ["t1"]
    assert [n.id for n in batches[1]] == ["t2"]
    assert [n.id for n in batches[2]] == ["t3"]


def test_subtask_node_conflicts_with_truth_table():
    """(g) tabela-verdade de conflicts_with.

    vazio×vazio, vazio×preenchido, preenchido×vazio, disjuntos, sobrepostos,
    com ./ normalizado como no teste existente.
    """
    empty1 = SubtaskNode(id="e1", target_files=[])
    empty2 = SubtaskNode(id="e2", target_files=["  ", ""])
    scoped_a = SubtaskNode(id="sa", target_files=["src/auth.py", "./src/utils.py"])
    scoped_b = SubtaskNode(id="sb", target_files=["src/utils.py"])
    scoped_c = SubtaskNode(id="sc", target_files=["tests/test_auth.py"])

    # vazio x vazio -> True
    assert empty1.conflicts_with(empty2) is True
    assert empty2.conflicts_with(empty1) is True

    # vazio x preenchido -> True
    assert empty1.conflicts_with(scoped_a) is True
    assert empty2.conflicts_with(scoped_a) is True

    # preenchido x vazio -> True
    assert scoped_a.conflicts_with(empty1) is True
    assert scoped_a.conflicts_with(empty2) is True

    # disjuntos -> False
    assert scoped_b.conflicts_with(scoped_c) is False
    assert scoped_c.conflicts_with(scoped_b) is False

    # sobrepostos (com ./ normalizado) -> True
    assert scoped_a.conflicts_with(scoped_b) is True
    assert scoped_b.conflicts_with(scoped_a) is True


def test_dag_unscoped_deferred_logging(caplog):
    """(4) Log info emitido uma vez por tarefa sem escopo adiada."""
    import logging
    steps = [
        {"id": "t1", "description": "Task 1", "target_files": ["a.py"], "depends_on": []},
        {"id": "t2", "description": "Task 2 unscoped", "target_files": [], "depends_on": []},
    ]
    dag = build_subtask_dag(steps)
    with caplog.at_level(logging.INFO):
        batches = dag.get_execution_batches()

    assert len(batches) == 2
    # Confirma que t2 gerou log informativo sobre ausência de target_files
    unscoped_logs = [rec for rec in caplog.records if "t2" in rec.message and "target_files" in rec.message]
    assert len(unscoped_logs) == 1

