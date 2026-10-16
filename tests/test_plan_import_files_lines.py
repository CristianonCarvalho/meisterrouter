"""Tests for the **Files:** line forms accepted by the superpowers plan adapter."""
from __future__ import annotations

import pytest

from meister.plan import PlanError
from meister.plan_adapters.superpowers import convert


def _plan(files_block: str, name: str = "Alpha") -> str:
    return (
        "# Test Plan\n\n**Goal:** test\n\n---\n\n"
        f"### Task 1: {name}\n\n"
        f"**Files:**\n{files_block}\n"
        "**Interfaces:**\n- Consumes: nothing\n- Produces: nothing\n\n"
        "- [ ] Step 1: do something\n"
    )


def test_create_with_multiple_backtick_paths():
    tasks = convert(_plan("- Create: `a.ts`, `b.ts`\n"))
    assert tasks[0]["target_files"] == ["a.ts", "b.ts"]


def test_text_outside_backticks_is_ignored():
    tasks = convert(_plan("- Modify: `docker/init.sql` (append ao final)\n"))
    assert tasks[0]["target_files"] == ["docker/init.sql"]


def test_rewrite_counts_as_modify_for_deps_files():
    text = (
        "# Plan\n\n---\n\n"
        "### Task 1: A\n\n**Files:**\n- Create: `shared.py`\n\n**Interfaces:** none\n\n"
        "### Task 2: B\n\n**Files:**\n- Rewrite: `shared.py`\n\n**Interfaces:** none\n"
    )
    tasks = convert(text, deps="files")
    assert tasks[1]["target_files"] == ["shared.py"]
    assert "task_1" in tasks[1]["depends_on"]


def test_rewrite_and_delete_enter_target_files():
    tasks = convert(_plan("- Rewrite: `r.py`\n- Delete: `old.py`\n"))
    assert tasks[0]["target_files"] == ["r.py", "old.py"]


def test_delete_counts_as_write_for_deps_files():
    text = (
        "# Plan\n\n---\n\n"
        "### Task 1: A\n\n**Files:**\n- Create: `x.py`\n\n**Interfaces:** none\n\n"
        "### Task 2: B\n\n**Files:**\n- Delete: `x.py`\n\n**Interfaces:** none\n"
    )
    tasks = convert(text, deps="files")
    assert "task_1" in tasks[1]["depends_on"]


def test_line_range_suffix_still_stripped_in_multi_path_line():
    tasks = convert(_plan("- Modify: `src/a.py:10-20`, `src/b.py:5`\n"))
    assert tasks[0]["target_files"] == ["src/a.py", "src/b.py"]


def test_line_without_backticks_warns_and_does_not_fail(capsys):
    text = _plan("- Create: `a.py`\n- Modify: src/b.py\n", name="Beta")
    tasks = convert(text)
    assert tasks[0]["target_files"] == ["a.py"]
    err = capsys.readouterr().err
    assert "task_1" in err
    assert "Modify: src/b.py" in err


def test_only_backtickless_lines_is_empty_section_error():
    with pytest.raises(PlanError) as exc:
        convert(_plan("- Modify: src/b.py\n"))
    assert any("empty" in m for m in exc.value.messages)


def test_unknown_verb_still_errors():
    with pytest.raises(PlanError) as exc:
        convert(_plan("- Deploy: `a.py`\n"))
    assert any("deploy" in m.lower() for m in exc.value.messages)
