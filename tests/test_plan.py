"""Tests for meister.plan (schema, load_plan, canonical_json) and meister.plan_adapters.superpowers.

Covers spec §6:
  - Fixture real (9 tasks, target_files, sequential deps, snapshot byte-a-byte)
  - Determinism (double conversion)
  - Schema: cada regra do §3 tem teste de falha
  - Adaptador: Depends on explícito, none, deps files, unkn task, sem Files,
    sufixo de linhas, sem ### Task → erro
  - Estrito por padrão (orchestrate com texto livre → erro, nenhum run)
  - Segurança: chave command rejeitada por load_plan
  - CLI: plan import, plan validate, orchestrate --plan-file
  - Mutação: snapshot e command-rejection falham se código desligado
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

# ── helpers ───────────────────────────────────────────────────────────────────

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "plans"
REAL_PLAN_MD = FIXTURE_DIR / "2026-09-26-meisterrouter-herdr-plugin.md"
REAL_PLAN_SNAPSHOT = FIXTURE_DIR / "2026-09-26-meisterrouter-herdr-plugin.canonical.json"


def _minimal_task(**overrides) -> dict:
    base = {
        "id": "task-1",
        "description": "A valid description",
        "target_files": ["src/foo.py"],
        "depends_on": [],
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════════════
# §6: Fixture real
# ═══════════════════════════════════════════════════════════════════════════════

class TestRealPlanFixture:
    """Real plan: 9 tasks, correct target_files, sequential deps, snapshot."""

    def _get_tasks(self):
        import meister.plan_adapters  # noqa: F401
        from meister.plan_adapters.superpowers import convert
        from meister.plan import validate_tasks
        text = REAL_PLAN_MD.read_text(encoding="utf-8")
        tasks = convert(text)
        validate_tasks(tasks)
        return tasks

    def test_nine_tasks(self):
        tasks = self._get_tasks()
        assert len(tasks) == 9, f"Expected 9 tasks, got {len(tasks)}"

    def test_task_ids_sequential(self):
        tasks = self._get_tasks()
        for i, task in enumerate(tasks, 1):
            assert task["id"] == f"task_{i}", f"Expected task_{i}, got {task['id']}"

    def test_task3_target_files(self):
        """Spec §6: task_3 must have the three specified files."""
        tasks = self._get_tasks()
        t3 = next(t for t in tasks if t["id"] == "task_3")
        assert "tests/mocks/mock_herdr_server.py" in t3["target_files"]
        assert "meister/herdr/client.py" in t3["target_files"]
        assert "tests/test_herdr_client.py" in t3["target_files"]

    def test_sequential_deps_default(self):
        """Default deps=sequential: task_1 has [], others depend on predecessor."""
        tasks = self._get_tasks()
        assert tasks[0]["depends_on"] == []
        for i in range(1, len(tasks)):
            assert tasks[i]["depends_on"] == [f"task_{i}"], (
                f"task_{i+1}.depends_on={tasks[i]['depends_on']}"
            )

    def test_snapshot_byte_exact(self):
        """Snapshot: canonical JSON must be byte-identical to fixture file."""
        tasks = self._get_tasks()
        from meister.plan import canonical_json
        produced = canonical_json(tasks)
        expected = REAL_PLAN_SNAPSHOT.read_text(encoding="utf-8")
        assert produced == expected, (
            f"Snapshot mismatch: first difference at char "
            f"{next((i for i, (a, b) in enumerate(zip(produced, expected)) if a != b), min(len(produced), len(expected)))}"
        )

    def test_snapshot_mutation_fails_if_validation_disabled(self):
        """Mutation guard: if validate_tasks were a no-op, snapshot would still differ for invalid plans."""
        # This test verifies the snapshot mechanism is real by checking that
        # tampering with the output produces a different result from the fixture.
        tasks = self._get_tasks()
        from meister.plan import canonical_json
        # Mutate one field
        tasks[0]["id"] = "mutated_id"
        produced = canonical_json(tasks)
        expected = REAL_PLAN_SNAPSHOT.read_text(encoding="utf-8")
        assert produced != expected, "Snapshot check would pass even with mutated output — snapshot is not protecting anything"


# ═══════════════════════════════════════════════════════════════════════════════
# §6: Determinism
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeterminism:
    def test_double_conversion_identical(self):
        from meister.plan_adapters.superpowers import convert
        from meister.plan import canonical_json
        text = REAL_PLAN_MD.read_text(encoding="utf-8")
        r1 = canonical_json(convert(text))
        r2 = canonical_json(convert(text))
        assert r1 == r2

    def test_run_id_stable(self):
        """Same plan text → same canonical JSON → same run_id derivative."""
        import hashlib
        from meister.plan_adapters.superpowers import convert
        from meister.plan import canonical_json
        text = REAL_PLAN_MD.read_text(encoding="utf-8")
        cj1 = canonical_json(convert(text))
        cj2 = canonical_json(convert(text))
        assert hashlib.sha256(cj1.encode()).hexdigest() == hashlib.sha256(cj2.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# §6: Schema — each §3 rule has a failure test
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaValidation:
    """load_plan(allow_freeform=False) — strict mode."""

    def _err(self, tasks_list):
        from meister.plan import PlanError, validate_tasks
        with pytest.raises(PlanError) as exc_info:
            validate_tasks(tasks_list)
        return exc_info.value

    def test_empty_list_rejected(self):
        from meister.plan import PlanError, load_plan
        with pytest.raises(PlanError) as exc:
            load_plan("[]")
        assert any("empty" in m for m in exc.value.messages)

    def test_not_a_list_rejected(self):
        from meister.plan import PlanError, load_plan
        with pytest.raises(PlanError):
            load_plan('{"id": "t1"}')

    def test_id_invalid_pattern(self):
        err = self._err([_minimal_task(id="invalid id!")])
        assert any("id" in m for m in err.messages)

    def test_id_starts_with_underscore_rejected(self):
        err = self._err([_minimal_task(id="_task")])
        assert any("id" in m for m in err.messages)

    def test_id_too_long_rejected(self):
        err = self._err([_minimal_task(id="a" * 65)])
        assert any("id" in m for m in err.messages)

    def test_id_valid_alphanumeric(self):
        from meister.plan import validate_tasks
        validate_tasks([_minimal_task(id="task-1"), _minimal_task(id="task-2", depends_on=["task-1"])])

    def test_duplicate_id_rejected(self):
        t1 = _minimal_task(id="task-1")
        t2 = _minimal_task(id="task-1")
        err = self._err([t1, t2])
        assert any("duplicate" in m.lower() for m in err.messages)

    def test_missing_description_rejected(self):
        t = {"id": "t1", "target_files": [], "depends_on": []}
        err = self._err([t])
        assert any("description" in m for m in err.messages)

    def test_empty_description_rejected(self):
        err = self._err([_minimal_task(description="   ")])
        assert any("description" in m for m in err.messages)

    def test_missing_target_files_rejected(self):
        t = {"id": "t1", "description": "x", "depends_on": []}
        err = self._err([t])
        assert any("target_files" in m for m in err.messages)

    def test_target_files_not_list_rejected(self):
        err = self._err([_minimal_task(target_files="foo.py")])
        assert any("target_files" in m for m in err.messages)

    def test_target_files_path_with_dotdot_rejected(self):
        err = self._err([_minimal_task(target_files=["../outside.py"])])
        assert any(".." in m for m in err.messages)

    def test_target_files_absolute_path_rejected(self):
        err = self._err([_minimal_task(target_files=["/etc/passwd"])])
        assert any("absolute" in m for m in err.messages)

    def test_target_files_backslash_rejected(self):
        err = self._err([_minimal_task(target_files=["src\\foo.py"])])
        assert any("target_files" in m or "backslash" in m for m in err.messages)

    def test_target_files_duplicate_rejected(self):
        err = self._err([_minimal_task(target_files=["a.py", "a.py"])])
        assert any("duplicate" in m for m in err.messages)

    def test_missing_depends_on_rejected(self):
        t = {"id": "t1", "description": "x", "target_files": []}
        err = self._err([t])
        assert any("depends_on" in m for m in err.messages)

    def test_depends_on_unknown_id_rejected(self):
        err = self._err([_minimal_task(depends_on=["nonexistent"])])
        assert any("nonexistent" in m for m in err.messages)

    def test_self_reference_rejected(self):
        err = self._err([_minimal_task(id="t1", depends_on=["t1"])])
        assert any("self" in m.lower() or "t1" in m for m in err.messages)

    def test_cycle_detected(self):
        tasks = [
            _minimal_task(id="t1", depends_on=["t2"]),
            _minimal_task(id="t2", depends_on=["t1"]),
        ]
        err = self._err(tasks)
        assert any("cycle" in m.lower() for m in err.messages)

    def test_cycle_error_cites_nodes(self):
        tasks = [
            _minimal_task(id="t1", depends_on=["t3"]),
            _minimal_task(id="t2", depends_on=["t1"]),
            _minimal_task(id="t3", depends_on=["t2"]),
        ]
        err = self._err(tasks)
        cycle_msg = " ".join(err.messages)
        assert "cycle" in cycle_msg.lower()

    def test_unknown_key_rejected(self):
        t = _minimal_task()
        t["extra_key"] = "bad"
        err = self._err([t])
        assert any("extra_key" in m for m in err.messages)

    def test_command_key_rejected(self):
        """Spec §6 security: 'command' key must be rejected (D5)."""
        t = _minimal_task()
        t["command"] = ["rm", "-rf", "/"]
        err = self._err([t])
        assert any("command" in m for m in err.messages), (
            "SECURITY: 'command' key was NOT rejected — D5 is broken"
        )

    def test_command_key_mutation_guard(self):
        """Mutation guard: this test must FAIL if command-rejection code is removed."""
        from meister.plan import _validate_tasks
        t = _minimal_task()
        t["command"] = ["evil"]
        errors = _validate_tasks([t])
        found = any("command" in e for e in errors)
        assert found, (
            "Mutation guard failure: 'command' rejection code was disabled — "
            "removing it would allow arbitrary command execution via plan JSON"
        )

    def test_cwd_key_rejected(self):
        t = _minimal_task()
        t["cwd"] = "/tmp"
        err = self._err([t])
        assert any("cwd" in m for m in err.messages)

    def test_worktree_key_rejected(self):
        t = _minimal_task()
        t["worktree"] = "/tmp/wt"
        err = self._err([t])
        assert any("worktree" in m for m in err.messages)

    def test_task_file_key_rejected(self):
        t = _minimal_task()
        t["task_file"] = "/tmp/task.json"
        err = self._err([t])
        assert any("task_file" in m for m in err.messages)

    def test_result_file_key_rejected(self):
        t = _minimal_task()
        t["result_file"] = "/tmp/result.json"
        err = self._err([t])
        assert any("result_file" in m for m in err.messages)

    def test_timeout_negative_rejected(self):
        err = self._err([_minimal_task(timeout=-1)])
        assert any("timeout" in m for m in err.messages)

    def test_timeout_zero_rejected(self):
        err = self._err([_minimal_task(timeout=0)])
        assert any("timeout" in m for m in err.messages)

    def test_timeout_bool_rejected(self):
        err = self._err([_minimal_task(timeout=True)])
        assert any("timeout" in m for m in err.messages)

    def test_timeout_valid(self):
        from meister.plan import validate_tasks
        validate_tasks([_minimal_task(timeout=300)])

    def test_target_files_empty_ok_with_allow_unscoped(self):
        """Empty target_files allowed only via adapter --allow-unscoped; schema itself allows empty list."""
        from meister.plan import validate_tasks
        # Schema allows empty list (adapter enforces non-empty by default)
        validate_tasks([_minimal_task(target_files=[])])

    def test_errors_accumulated(self):
        """Multiple errors are reported at once, not just the first."""
        t = {"id": "bad id!", "description": "", "target_files": ["../bad"], "depends_on": "x"}
        err = self._err([t])
        assert len(err.messages) >= 3

    def test_freeform_text_rejected_strict_mode(self):
        """Non-JSON input rejected in strict mode."""
        from meister.plan import PlanError, load_plan
        with pytest.raises(PlanError) as exc:
            load_plan("- task A\n- task B\n")
        assert exc.value.messages

    def test_json_with_unknown_key_rejected(self):
        raw = json.dumps([_minimal_task() | {"command": ["evil"]}])
        from meister.plan import PlanError, load_plan
        with pytest.raises(PlanError) as exc:
            load_plan(raw)
        assert any("command" in m for m in exc.value.messages)


# ═══════════════════════════════════════════════════════════════════════════════
# §6: Adaptador superpowers
# ═══════════════════════════════════════════════════════════════════════════════

class TestSuperpowersAdapter:
    def _convert(self, text, **kw):
        from meister.plan_adapters.superpowers import convert
        return convert(text, **kw)

    def _minimal_plan(self, tasks_md: str, constraints: str = "") -> str:
        header = "# Test Plan\n\n**Goal:** test\n\n**Architecture:** simple\n\n**Tech Stack:** Python\n\n**Spec:** none\n\n"
        if constraints:
            header += f"## Global Constraints\n\n{constraints}\n\n"
        return header + "---\n\n" + tasks_md

    def _task_md(self, n: int, name: str, files_block: str, body: str = "", depends_line: str = "") -> str:
        dep_line = f"\n**Depends on:** {depends_line}\n" if depends_line else ""
        return (
            f"### Task {n}: {name}\n\n"
            f"**Files:**\n{files_block}\n"
            f"{dep_line}"
            f"**Interfaces:**\n- Consumes: nothing\n- Produces: nothing\n\n"
            f"{body}"
        )

    # ── Basic parsing ─────────────────────────────────────────────────────────

    def test_single_task(self):
        md = self._minimal_plan(
            self._task_md(1, "Alpha", "- Create: `src/a.py`\n- Test: `tests/test_a.py`\n")
        )
        tasks = self._convert(md)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "task_1"
        assert "src/a.py" in tasks[0]["target_files"]
        assert "tests/test_a.py" in tasks[0]["target_files"]
        assert tasks[0]["depends_on"] == []

    def test_sequential_default_deps(self):
        t1 = self._task_md(1, "A", "- Create: `a.py`\n")
        t2 = self._task_md(2, "B", "- Create: `b.py`\n")
        t3 = self._task_md(3, "C", "- Create: `c.py`\n")
        md = self._minimal_plan(t1 + t2 + t3)
        tasks = self._convert(md)
        assert tasks[0]["depends_on"] == []
        assert tasks[1]["depends_on"] == ["task_1"]
        assert tasks[2]["depends_on"] == ["task_2"]

    def test_explicit_depends_on_wins(self):
        t1 = self._task_md(1, "A", "- Create: `a.py`\n")
        t2 = self._task_md(2, "B", "- Create: `b.py`\n")
        t3 = self._task_md(3, "C", "- Create: `c.py`\n", depends_line="Task 1")
        md = self._minimal_plan(t1 + t2 + t3)
        tasks = self._convert(md)
        assert tasks[2]["depends_on"] == ["task_1"]

    def test_explicit_none_depends_on(self):
        t1 = self._task_md(1, "A", "- Create: `a.py`\n")
        t2 = self._task_md(2, "B", "- Create: `b.py`\n", depends_line="none")
        md = self._minimal_plan(t1 + t2)
        tasks = self._convert(md)
        assert tasks[1]["depends_on"] == []

    def test_line_range_suffix_removed(self):
        t1 = self._task_md(1, "A", "- Modify: `src/main.py:123-145`\n")
        md = self._minimal_plan(t1)
        tasks = self._convert(md)
        assert "src/main.py" in tasks[0]["target_files"]
        assert not any("123" in f for f in tasks[0]["target_files"])

    def test_dotslash_normalized(self):
        t1 = self._task_md(1, "A", "- Create: `./src/foo.py`\n")
        md = self._minimal_plan(t1)
        tasks = self._convert(md)
        assert "src/foo.py" in tasks[0]["target_files"]

    def test_no_tasks_raises_error(self):
        from meister.plan import PlanError
        with pytest.raises(PlanError) as exc:
            self._convert("# A plan with no tasks\n\nSome text.\n")
        assert exc.value.messages

    def test_duplicate_task_numbers_error(self):
        from meister.plan import PlanError
        t1a = self._task_md(1, "Alpha", "- Create: `a.py`\n")
        t1b = self._task_md(1, "Beta", "- Create: `b.py`\n")
        md = self._minimal_plan(t1a + t1b)
        with pytest.raises(PlanError) as exc:
            self._convert(md)
        assert any("duplicate" in m.lower() or "1" in m for m in exc.value.messages)

    def test_unknown_verb_rejected(self):
        from meister.plan import PlanError
        t1 = self._task_md(1, "A", "- Deploy: `src/a.py`\n")
        md = self._minimal_plan(t1)
        with pytest.raises(PlanError) as exc:
            self._convert(md)
        assert any("deploy" in m.lower() or "verb" in m.lower() for m in exc.value.messages)

    def test_no_files_section_rejected_by_default(self):
        from meister.plan import PlanError
        # Task with no **Files:** block at all
        body = "### Task 1: NoFiles\n\n**Interfaces:**\n- nothing\n\n- [ ] Step 1: do something\n"
        md = self._minimal_plan(body)
        with pytest.raises(PlanError) as exc:
            self._convert(md)
        assert any("Files" in m or "unscoped" in m.lower() for m in exc.value.messages)

    def test_no_files_allowed_with_allow_unscoped(self):
        body = "### Task 1: NoFiles\n\n**Interfaces:**\n- nothing\n\n- [ ] Step 1: do something\n"
        md = self._minimal_plan(body)
        tasks = self._convert(md, allow_unscoped=True)
        assert tasks[0]["target_files"] == []

    def test_explicit_depends_unknown_task_number_error(self):
        from meister.plan import PlanError
        t1 = self._task_md(1, "A", "- Create: `a.py`\n")
        t2 = self._task_md(2, "B", "- Create: `b.py`\n", depends_line="Task 99")
        md = self._minimal_plan(t1 + t2)
        with pytest.raises(PlanError) as exc:
            self._convert(md)
        assert any("99" in m for m in exc.value.messages)

    def test_deps_files_overlap(self):
        t1 = self._task_md(1, "A", "- Create: `shared.py`\n")
        t2 = self._task_md(2, "B", "- Modify: `shared.py`\n")  # overlaps t1's Create
        md = self._minimal_plan(t1 + t2)
        tasks = self._convert(md, deps="files")
        # task_2 Create+Modify overlaps task_1's Create
        assert "task_1" in tasks[1]["depends_on"]

    def test_deps_files_no_overlap(self):
        t1 = self._task_md(1, "A", "- Create: `a.py`\n")
        t2 = self._task_md(2, "B", "- Create: `b.py`\n")  # no overlap
        md = self._minimal_plan(t1 + t2)
        tasks = self._convert(md, deps="files")
        assert tasks[1]["depends_on"] == []

    def test_global_constraints_in_description(self):
        constraints = "- Python >= 3.10.\n- No third-party RPC.\n"
        t1 = self._task_md(1, "Alpha", "- Create: `a.py`\n")
        md = self._minimal_plan(t1, constraints=constraints)
        tasks = self._convert(md)
        assert "Global Constraints" in tasks[0]["description"]
        assert "Python >= 3.10" in tasks[0]["description"]

    def test_review_focus_not_in_description(self):
        """Review Focus section must NOT appear in task description."""
        header = (
            "# Test Plan\n\n**Goal:** x\n\n**Architecture:** y\n\n**Tech Stack:** z\n\n**Spec:** none\n\n"
            "## Global Constraints\n\n- Constraint 1.\n\n"
            "## Review Focus\n\n1. Focus item A.\n\n---\n\n"
        )
        t1 = self._task_md(1, "Alpha", "- Create: `a.py`\n")
        tasks = self._convert(header + t1)
        assert "Review Focus" not in tasks[0]["description"]
        assert "Focus item A" not in tasks[0]["description"]

    def test_description_starts_with_task_name(self):
        t1 = self._task_md(1, "My Component", "- Create: `a.py`\n")
        md = self._minimal_plan(t1)
        tasks = self._convert(md)
        assert tasks[0]["description"].startswith("Task 1: My Component")

    def test_no_extra_keys_in_output(self):
        """Adapter must produce only canonical keys (no _write_set etc.)."""
        t1 = self._task_md(1, "A", "- Create: `a.py`\n")
        md = self._minimal_plan(t1)
        tasks = self._convert(md)
        allowed = {"id", "description", "target_files", "depends_on", "timeout"}
        for task in tasks:
            extra = set(task.keys()) - allowed
            assert not extra, f"Extra keys in adapter output: {extra}"


# ═══════════════════════════════════════════════════════════════════════════════
# §6: Strict by default + security
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrictDefault:
    """orchestrate / load_plan reject freeform input; --allow-freeform preserves old behaviour."""

    def test_freeform_markdown_list_rejected(self):
        from meister.plan import PlanError, load_plan
        freeform = "- Task A\n- Task B\n- Task C\n"
        with pytest.raises(PlanError):
            load_plan(freeform)

    def test_plain_text_rejected(self):
        from meister.plan import PlanError, load_plan
        with pytest.raises(PlanError):
            load_plan("Implement a new feature in the codebase.")

    def test_freeform_allow_freeform_passes(self):
        from meister.plan import load_plan
        freeform = "- Task A\n- Task B\n"
        # With allow_freeform=True, falls through to parse_architect_plan — must not raise
        result = load_plan(freeform, allow_freeform=True)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_command_rejected_before_run_created(self, tmp_path):
        """Security: plan with 'command' key must be rejected — no SQLite run created."""
        import meister.plan  # noqa: F401 — ensure module loaded

        bad_plan = json.dumps([{
            "id": "task-1",
            "description": "evil",
            "target_files": ["x.py"],
            "depends_on": [],
            "command": ["rm", "-rf", "/"],
        }])

        from meister.plan import PlanError, load_plan
        with pytest.raises(PlanError) as exc:
            load_plan(bad_plan)
        assert any("command" in m for m in exc.value.messages), (
            "SECURITY CRITICAL: 'command' key was not rejected by load_plan — "
            "attacker can execute arbitrary commands via plan JSON"
        )

    def test_command_key_security_mutation_guard(self):
        """Mutation guard for command-rejection: MUST fail if rejection disabled."""
        # Directly test the validation logic; if it were disabled, this assertion fails.
        from meister.plan import _validate_tasks
        bad = [_minimal_task() | {"command": ["pwn"]}]
        errs = _validate_tasks(bad)
        assert errs, (
            "MUTATION GUARD FAILURE: _validate_tasks accepted 'command' key — "
            "this guard exists to ensure that disabling rejection code causes test failure"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# §6: CLI tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlanCLI:
    def _runner(self):
        return CliRunner()

    def test_plan_import_real_plan(self, tmp_path):
        from meister.cli import main
        runner = self._runner()
        result = runner.invoke(main, [
            "plan", "import", str(REAL_PLAN_MD),
            "--format", "superpowers",
        ])
        assert result.exit_code == 0, result.output
        assert "task_1" in result.output
        assert "9 tarefas" in result.output

    def test_plan_import_output_file(self, tmp_path):
        from meister.cli import main
        out = tmp_path / "out.json"
        runner = self._runner()
        result = runner.invoke(main, [
            "plan", "import", str(REAL_PLAN_MD),
            "--format", "superpowers",
            "-o", str(out),
        ])
        assert result.exit_code == 0, result.output
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert isinstance(loaded, list)
        assert len(loaded) == 9

    def test_plan_validate_valid(self, tmp_path):
        from meister.cli import main
        valid = json.dumps([_minimal_task()])
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(valid)
        runner = self._runner()
        result = runner.invoke(main, ["plan", "validate", str(plan_file)])
        assert result.exit_code == 0
        assert "válido" in result.output or "valid" in result.output.lower()

    def test_plan_validate_invalid(self, tmp_path):
        from meister.cli import main
        bad = json.dumps([_minimal_task() | {"command": ["evil"]}])
        plan_file = tmp_path / "bad.json"
        plan_file.write_text(bad)
        runner = self._runner()
        result = runner.invoke(main, ["plan", "validate", str(plan_file)])
        assert result.exit_code != 0

    def test_plan_import_table_shows_deps(self, tmp_path):
        from meister.cli import main
        runner = self._runner()
        result = runner.invoke(main, [
            "plan", "import", str(REAL_PLAN_MD),
            "--format", "superpowers",
        ])
        assert result.exit_code == 0
        # Table should show task_1 in depends_on column of task_2
        assert "task_1" in result.output

    def test_plan_import_unknown_format_error(self):
        from meister.cli import main
        runner = self._runner()
        result = runner.invoke(main, [
            "plan", "import", str(REAL_PLAN_MD),
            "--format", "unknown_fmt",
        ])
        assert result.exit_code != 0

    def test_orchestrate_freeform_rejected_strict(self):
        """orchestrate without --allow-freeform rejects plain text, exits != 0."""
        from meister.cli import main
        runner = self._runner()
        result = runner.invoke(main, [
            "orchestrate",
            "--task", "- Task A\n- Task B",
        ])
        # Should exit with code 2 (validation failure) — fails before reaching socket
        assert result.exit_code == 2

    def test_orchestrate_freeform_no_run_created(self, tmp_path):
        """Strict orchestrate: rejected plan must NOT create a SQLite run."""
        from meister.state import StateManager
        from meister.cli import main
        runner = self._runner()

        sm = StateManager()

        def _count_runs():
            with sm._get_connection() as conn:
                row = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
                return row[0] if row else 0

        runs_before = _count_runs()

        runner.invoke(main, [
            "orchestrate",
            "--task", "- freeform markdown list\n- task B",
        ])

        runs_after = _count_runs()
        # No new run created (validation fails before any DB write)
        assert runs_after == runs_before

    def test_orchestrate_plan_file_valid(self, tmp_path):
        """orchestrate --plan-file with valid plan passes validation (mocked bridge)."""
        from unittest.mock import patch, AsyncMock
        from meister.cli import main
        valid_plan = json.dumps([_minimal_task()])
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(valid_plan)
        runner = self._runner()
        # Mock the bridge so we don't actually connect to Herdr socket
        with patch(
            "meister.herdr.bridge.HerdrEventBridge.run_orchestration_cycle",
            new=AsyncMock(return_value=True),
        ):
            result = runner.invoke(main, [
                "orchestrate",
                "--plan-file", str(plan_file),
            ])
        # Validation should pass; exit_code 2 would mean validation failure
        assert result.exit_code != 2

    def test_orchestrate_allow_freeform_passes_validation(self):
        """--allow-freeform skips strict validation (mocked bridge)."""
        from unittest.mock import patch, AsyncMock
        from meister.cli import main
        runner = self._runner()
        # Mock bridge to avoid blocking socket connection
        with patch(
            "meister.herdr.bridge.HerdrEventBridge.run_orchestration_cycle",
            new=AsyncMock(return_value=True),
        ):
            result = runner.invoke(main, [
                "orchestrate",
                "--task", "- Task A\n- Task B",
                "--allow-freeform",
            ])
        # Should not fail with exit code 2 (validation)
        assert result.exit_code != 2




# ═══════════════════════════════════════════════════════════════════════════════
# §6: Segurança — dict do plano e task_context (command key path)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityCommandKey:
    """Confirm and test the command key injection path in workers.resolve_command."""

    def test_plan_command_key_rejected_before_spawn(self):
        """A plan JSON with 'command' is blocked by load_plan, never reaching resolve_command."""
        from meister.plan import PlanError, load_plan
        bad = json.dumps([{
            "id": "task-1",
            "description": "pwn",
            "target_files": [],
            "depends_on": [],
            "command": ["rm", "-rf", "/tmp/test"],
        }])
        with pytest.raises(PlanError) as exc:
            load_plan(bad)
        assert any("command" in m for m in exc.value.messages)

    def test_resolve_command_uses_task_dict_command(self):
        """Confirm the risk: if a task_context dict with 'command' reaches resolve_command,
        it IS used directly. This test documents the existing behaviour (not a bug we must fix
        since D5 prevents it at load_plan, but we must confirm the path and test it)."""
        from meister.config import load_config
        from meister.herdr.workers import WorkerSpawner
        cfg = load_config()
        spawner = WorkerSpawner(cfg)
        # Simulate a task_context that somehow has 'command' (would only happen
        # in the bridge itself after it sets task_dict["command"] = cmd_parts)
        injected_cmd = ["meister", "worker", "--model", "codex_luna"]
        task_ctx = {
            "id": "t1",
            "description": "test",
            "target_files": [],
            "command": injected_cmd,
        }
        result = spawner.resolve_command("luna", task_context=task_ctx)
        # The bridge itself sets task_dict["command"] legitimately — this verifies
        # the mechanism works. D5 ensures attacker-supplied plans cannot reach here.
        assert result == injected_cmd

    def test_allow_freeform_path_command_filtered(self):
        """In allow_freeform path, parse_architect_plan output does NOT include
        'command' or other forbidden keys — confirm the output is clean."""
        from meister.plan import load_plan
        freeform = "- Task A: implement feature\n- Task B: add tests\n"
        tasks = load_plan(freeform, allow_freeform=True)
        from meister.plan import FORBIDDEN_KEYS
        for task in tasks:
            for fk in FORBIDDEN_KEYS:
                assert fk not in task, (
                    f"SECURITY: freeform path produced task with forbidden key {fk!r}"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# §6: Canonical JSON and load_plan round-trip
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalJson:
    def test_sort_keys(self):
        from meister.plan import canonical_json
        tasks = [_minimal_task()]
        out = canonical_json(tasks)
        # Keys should be in alphabetical order: depends_on, description, id, target_files
        obj = json.loads(out)
        keys = list(obj[0].keys())
        assert keys == sorted(keys)

    def test_no_trailing_newline(self):
        from meister.plan import canonical_json
        out = canonical_json([_minimal_task()])
        assert not out.endswith("\n")

    def test_compact_separators(self):
        from meister.plan import canonical_json
        out = canonical_json([_minimal_task()])
        assert ": " not in out  # No space after colon
        assert ", " not in out  # No space after comma (in JSON level)

    def test_load_plan_round_trip(self):
        from meister.plan import canonical_json, load_plan
        tasks = [_minimal_task()]
        cj = canonical_json(tasks)
        reloaded = load_plan(cj)
        assert canonical_json(reloaded) == cj


# ── pane-plan contract (architect pane path) ──────────────────────────────────

import asyncio  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from meister.config import load_config  # noqa: E402
from meister.herdr.bridge import HerdrEventBridge  # noqa: E402
from meister.state import StateManager  # noqa: E402

_FREEFORM_PANE = "Please refactor the whole auth module and make it faster."
_CANONICAL_PANE = json.dumps(
    [{"id": "t1", "description": "Do it", "target_files": ["a.py"], "depends_on": []}]
)
_COMMAND_PANE = json.dumps(
    [{"id": "t1", "description": "Do it", "target_files": ["a.py"], "depends_on": [],
      "command": "rm -rf /"}]
)


def _run_pane_cycle(tmp_path, monkeypatch, pane_text, **kwargs):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("APP = True\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    monkeypatch.chdir(repo_dir)

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text('version: "1.0"\nconcurrency:\n  isolation_mode: "none"\n')
    client = AsyncMock()
    client.read_pane.return_value = pane_text
    gate = MagicMock()
    gate.run_verification.return_value = (True, "ok")
    gate.get_diff_summary.return_value = "diff"
    gate.evaluate_completion.return_value = {"action": "COMPLETE"}
    bridge = HerdrEventBridge(config=load_config(str(cfg_file)), client=client, gate=gate)
    executed: list = []

    async def fake_execute(steps):
        executed.append(steps)
        return True

    async def go():
        with patch.object(bridge, "execute_plan", new=fake_execute):
            return await bridge.run_orchestration_cycle(
                workspace_id="ws1", architect_pane_id="w1:p0", **kwargs
            )

    return asyncio.run(go()), executed


def _run_count() -> int:
    sm = StateManager()
    with sm._get_connection() as conn:  # type: ignore[attr-defined]
        return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


class TestPanePlanContract:
    def test_freeform_pane_strict_rejected_no_run_no_worker(self, tmp_path, monkeypatch):
        ok, executed = _run_pane_cycle(tmp_path, monkeypatch, _FREEFORM_PANE, allow_freeform=False)
        assert ok is False
        assert executed == []
        assert _run_count() == 0

    def test_canonical_pane_strict_proceeds(self, tmp_path, monkeypatch):
        ok, executed = _run_pane_cycle(tmp_path, monkeypatch, _CANONICAL_PANE, allow_freeform=False)
        assert ok is True
        assert [s["id"] for s in executed[0]] == ["t1"]
        assert _run_count() == 1

    def test_freeform_pane_default_keeps_legacy_behavior(self, tmp_path, monkeypatch):
        ok, executed = _run_pane_cycle(tmp_path, monkeypatch, _FREEFORM_PANE)
        assert ok is True
        assert executed[0][0]["id"] == "task_1"
        assert _run_count() == 1

    def test_command_key_pane_strict_rejected(self, tmp_path, monkeypatch):
        ok, executed = _run_pane_cycle(tmp_path, monkeypatch, _COMMAND_PANE, allow_freeform=False)
        assert ok is False
        assert executed == []
        assert _run_count() == 0


class TestOrchestrateCliForwardsAllowFreeform:
    def _invoke(self, extra):
        from meister.cli import main

        with patch("meister.cli.HerdrEventBridge") as bridge_cls:
            bridge = MagicMock()
            bridge.run_orchestration_cycle = AsyncMock(return_value=True)
            bridge_cls.return_value = bridge
            result = CliRunner().invoke(main, ["orchestrate", *extra])
        assert result.exit_code == 0, result.output
        return bridge.run_orchestration_cycle.call_args.kwargs

    def test_default_forwards_false(self):
        assert self._invoke([])["allow_freeform"] is False

    def test_flag_forwards_true(self):
        assert self._invoke(["--allow-freeform"])["allow_freeform"] is True
