import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.plan_analysis import analyze_plan, serial_plan_warning


def task(task_id, files=None, dependencies=None):
    return {
        "id": task_id,
        "description": f"Task {task_id}",
        "target_files": list(files or []),
        "depends_on": list(dependencies or []),
    }


def test_analyze_serial_chain_and_critical_path():
    tasks = [
        task("a", ["a.py"]),
        task("b", ["b.py"], ["a"]),
        task("c", ["c.py"], ["b"]),
    ]

    result = analyze_plan(tasks, max_workers=4)

    assert result["batch_count"] == 3
    assert result["serial_ratio"] == 1.0
    assert [reason["kind"] for reason in result["why_not_parallel"]] == [
        "dependency",
        "dependency",
    ]
    assert result["critical_path"] == {"tasks": ["a", "b", "c"], "length": 3}
    assert "totalmente serial" in result["warnings"][0]


def test_analyze_fanout_has_parallel_middle_batch():
    tasks = [
        task("base", ["base.py"]),
        task("a", ["a.py"], ["base"]),
        task("b", ["b.py"], ["base"]),
        task("c", ["c.py"], ["base"]),
        task("close", ["close.py"], ["a", "b", "c"]),
    ]

    result = analyze_plan(tasks, max_workers=4)

    assert [len(batch) for batch in result["batches"]] == [1, 3, 1]
    assert result["max_width"] == 3
    assert not result["warnings"]


def test_analyze_reports_file_conflicts_and_unscoped_tasks():
    conflict = analyze_plan(
        [task("first", ["shared.py"]), task("second", ["shared.py"])],
        max_workers=4,
    )
    assert conflict["batch_count"] == 2
    assert conflict["why_not_parallel"] == [
        {
            "task": "second",
            "kind": "file_conflict",
            "with": ["first"],
            "files": ["shared.py"],
        }
    ]

    unscoped = analyze_plan([task("scoped", ["a.py"]), task("unscoped")], max_workers=4)
    assert unscoped["why_not_parallel"] == [
        {"task": "unscoped", "kind": "unscoped", "with": ["scoped"]}
    ]
    assert unscoped["unscoped_tasks"] == ["unscoped"]


def test_analyze_respects_worker_cap():
    tasks = [task(f"t{index}", [f"{index}.py"]) for index in range(6)]

    assert analyze_plan(tasks, max_workers=4)["rounds"] == 2
    assert analyze_plan(tasks, max_workers=8)["rounds"] == 1


def test_file_estimate_ignores_explicit_chain_and_warns_about_difference():
    disjoint_chain = [
        task("a", ["a.py"]),
        task("b", ["b.py"], ["a"]),
        task("c", ["c.py"], ["b"]),
    ]
    result = analyze_plan(disjoint_chain, max_workers=4)
    assert result["by_file_estimate"] == {"batch_count": 1, "rounds": 1}
    assert "a análise por arquivo daria 1 passos" in result["warnings"][0]

    shared_chain = [
        task("a", ["shared.py"]),
        task("b", ["shared.py"], ["a"]),
        task("c", ["shared.py"], ["b"]),
    ]
    assert analyze_plan(shared_chain, max_workers=4)["by_file_estimate"]["batch_count"] == 3


def test_hot_files_are_sorted_by_usage_then_path():
    tasks = [
        task("a", ["z.py", "b.py", "a.py"]),
        task("b", ["z.py", "b.py", "a.py"]),
        task("c", ["z.py"]),
    ]

    assert analyze_plan(tasks, max_workers=4)["hot_files"] == [
        {"file": "z.py", "tasks": ["a", "b", "c"]},
        {"file": "a.py", "tasks": ["a", "b"]},
        {"file": "b.py", "tasks": ["a", "b"]},
    ]


def test_plan_analyze_json_invalid_plan_and_no_files_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps([task("a", ["a.py"])]), encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(main, ["plan", "analyze", str(plan_path), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert list(payload) == [
        "tasks",
        "edges",
        "batches",
        "batch_count",
        "max_width",
        "rounds",
        "serial_ratio",
        "critical_path",
        "why_not_parallel",
        "hot_files",
        "unscoped_tasks",
        "by_file_estimate",
        "warnings",
    ]
    assert not (tmp_path / ".meister").exists()

    plan_path.write_text("not a plan", encoding="utf-8")
    invalid = runner.invoke(main, ["plan", "analyze", str(plan_path)])
    assert invalid.exit_code == 2
    assert "Plano inválido" in invalid.stderr
    assert not (tmp_path / ".meister").exists()


def test_plan_analyze_table_and_max_workers_override(tmp_path):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps([task(f"t{index}", [f"{index}.py"]) for index in range(6)]),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main, ["plan", "analyze", str(plan_path), "--max-workers", "4"]
    )

    assert result.exit_code == 0
    assert "Passos sequenciais (até 4 workers): 2" in result.output
    assert "Estimativa por arquivo (ignora dependência semântica)" in result.output


def test_serial_plan_warning_handles_small_serial_parallel_and_invalid_plans():
    assert serial_plan_warning([task("a"), task("b", dependencies=["a"])]) is None

    fanout = [task("base", ["base.py"])] + [
        task(f"fan{index}", [f"{index}.py"], ["base"]) for index in range(3)
    ] + [task("close", ["close.py"], ["fan0", "fan1", "fan2"])]
    assert serial_plan_warning(fanout) is None
    assert serial_plan_warning([task("a", dependencies=["missing"]) for _ in range(3)]) is None
    assert "3 tarefas em 3 lotes" in serial_plan_warning(
        [task("a"), task("b", dependencies=["a"]), task("c", dependencies=["b"])]
    )


@pytest.mark.parametrize("plan_option", ["--plan-file", "--task"])
def test_orchestrate_warns_for_serial_plan_without_changing_result(
    tmp_path, monkeypatch, plan_option
):
    monkeypatch.setenv("MEISTER_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    plan = [task("a", ["a.py"]), task("b", ["b.py"], ["a"]), task("c", ["c.py"], ["b"])]
    serial_path = tmp_path / "serial.json"
    serial_path.write_text(json.dumps(plan), encoding="utf-8")
    option_value = str(serial_path) if plan_option == "--plan-file" else json.dumps(plan)
    args = ["orchestrate", plan_option, option_value, "-q"]
    with patch("meister.cli.HerdrEventBridge") as bridge_cls:
        bridge_cls.return_value.run_orchestration_cycle = AsyncMock(return_value=True)
        serial_result = CliRunner().invoke(main, args)
        serial_call = bridge_cls.return_value.run_orchestration_cycle.await_args.kwargs
        with patch(
            "meister.plan_analysis.serial_plan_warning",
            side_effect=RuntimeError("warning failure"),
        ):
            without_warning = CliRunner().invoke(main, args)
        no_warning_call = bridge_cls.return_value.run_orchestration_cycle.await_args.kwargs

    assert serial_result.exit_code == without_warning.exit_code == 0
    assert serial_result.stdout == without_warning.stdout
    assert "Aviso: plano totalmente serial" in serial_result.stderr
    assert "meister plan analyze" in serial_result.stderr
    assert "Aviso: plano totalmente serial" not in without_warning.stderr
    assert serial_call["task"] == no_warning_call["task"]
