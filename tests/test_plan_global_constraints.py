"""Regression tests for the superpowers Global Constraints section boundary."""
from __future__ import annotations

import meister.plan_adapters  # noqa: F401 — registers the superpowers adapter
from meister.plan import ADAPTERS, validate_tasks


def _convert(text: str) -> list[dict]:
    tasks = ADAPTERS["superpowers"](text)
    validate_tasks(tasks)
    return tasks


def _task(number: int, name: str, marker: str, path: str) -> str:
    return (
        f"### Task {number}: {name}\n\n"
        f"**Files:**\n- Create: `{path}`\n\n"
        f"Task-specific body: {marker}\n"
    )


def test_global_constraints_stop_at_first_task_and_tasks_stay_separate() -> None:
    plan = (
        "# Regression Plan\n\n"
        "## Global Constraints\n\n"
        "- Keep all changes backwards compatible.\n\n"
        + _task(1, "Alpha", "alpha-only detail", "src/alpha.py")
        + _task(2, "Beta", "beta-only detail", "src/beta.py")
        + _task(3, "Gamma", "gamma-only detail", "src/gamma.py")
    )

    tasks = _convert(plan)
    descriptions = [task["description"] for task in tasks]

    assert len(tasks) == 3
    task_names = ("Alpha", "Beta", "Gamma")
    task_markers = ("alpha-only detail", "beta-only detail", "gamma-only detail")
    for index, marker in enumerate(
        task_markers
    ):
        assert "Keep all changes backwards compatible." in descriptions[index]
        assert marker in descriptions[index]
        for other_index, (other_name, other_marker) in enumerate(
            zip(task_names, task_markers)
        ):
            if other_index != index:
                assert other_name not in descriptions[index]
                assert other_marker not in descriptions[index]
                assert f"Task {other_index + 1}: " not in descriptions[index]


def test_global_constraints_still_stop_at_next_level_two_heading() -> None:
    plan = (
        "# Regression Plan\n\n"
        "## Global Constraints\n\n"
        "- Preserve public APIs.\n\n"
        "## Outro\n\n"
        "This section is not a global constraint.\n\n"
        + _task(1, "Alpha", "alpha-only detail", "src/alpha.py")
    )

    task = _convert(plan)[0]

    assert "Preserve public APIs." in task["description"]
    assert "Outro" not in task["description"]
    assert "This section is not a global constraint." not in task["description"]


def test_plan_without_global_constraints_keeps_task_description() -> None:
    plan = (
        "# Regression Plan\n\n"
        + _task(1, "Alpha", "alpha-only detail", "src/alpha.py")
    )

    task = _convert(plan)[0]

    assert "Global Constraints" not in task["description"]
    assert "Task-specific body: alpha-only detail" in task["description"]
