import json
import subprocess

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.clean import (
    CleanError,
    ProcessInfo,
    _active_meister_processes,
    _create_archive_ref,
    apply_cleanup,
    plan_cleanup,
)


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def make_repo(path):
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "base.txt").write_text("base\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial")
    return path


def create_branch_commit(repo, branch, filename, content="change\n"):
    git(repo, "checkout", "-b", branch)
    (repo / filename).write_text(content)
    git(repo, "add", filename)
    git(repo, "commit", "-m", f"commit {branch}")
    tip = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "main")
    return tip


def branch_exists(repo, branch):
    return git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


def test_classifies_six_cases_simulates_then_applies_only_safe_branches(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    create_branch_commit(repo, "meister/integration/equivalent", "same.txt", "same\n")
    equivalent_tip = git(repo, "rev-parse", "meister/integration/equivalent").stdout.strip()
    (repo / "intervening.txt").write_text("intervening\n")
    git(repo, "add", "intervening.txt")
    git(repo, "commit", "-m", "intervening")
    git(repo, "cherry-pick", equivalent_tip)

    archived_tip = create_branch_commit(repo, "meister/integration/archived", "archived.txt")
    git(repo, "update-ref", "refs/meister/archive/keep-existing", archived_tip)
    create_branch_commit(repo, "meister/integration/unmerged", "unmerged.txt")
    git(repo, "branch", "meister/integration/merged", "main")
    git(repo, "checkout", "-b", "meister/integration/current")
    (repo / "current.txt").write_text("current\n")
    git(repo, "add", "current.txt")
    git(repo, "commit", "-m", "current")
    git(repo, "checkout", "main")
    open_path = tmp_path / "open-worktree"
    git(repo, "worktree", "add", "-b", "meister/worktree/open", str(open_path), "main")

    branches_before = set(git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/").stdout.splitlines())
    refs_before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/meister/archive").stdout
    result = CliRunner().invoke(main, ["clean", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Nada foi alterado." in result.output
    assert "commit meister/integration/unmerged" in result.output
    assert set(git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/").stdout.splitlines()) == branches_before
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/meister/archive").stdout == refs_before

    attempted_deletions = []
    original_run = subprocess.run

    def record_deletions(args, *call_args, **call_kwargs):
        if args[:4] == ["git", "-C", str(repo), "branch"] and "-D" in args:
            attempted_deletions.append(args[-1])
        return original_run(args, *call_args, **call_kwargs)

    monkeypatch.setattr("meister.clean.subprocess.run", record_deletions)
    apply = CliRunner().invoke(main, ["clean", "--repo", str(repo), "--apply"])
    assert apply.exit_code == 0, apply.output
    assert not branch_exists(repo, "meister/integration/merged")
    assert not branch_exists(repo, "meister/integration/equivalent")
    assert not branch_exists(repo, "meister/integration/archived")
    assert branch_exists(repo, "meister/integration/unmerged")
    assert branch_exists(repo, "meister/integration/current")
    assert branch_exists(repo, "meister/worktree/open")
    assert "meister/integration/current" not in attempted_deletions
    assert "meister/worktree/open" not in attempted_deletions
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/meister/archive").stdout == refs_before


def test_archive_then_delete_and_retain_commit(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    tip = create_branch_commit(repo, "meister/integration/save-me", "saved.txt")
    result = CliRunner().invoke(
        main,
        ["clean", "--repo", str(repo), "--apply", "--archive-and-delete"],
    )
    assert result.exit_code == 0, result.output
    assert not branch_exists(repo, "meister/integration/save-me")
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/meister/archive").stdout.splitlines()
    cleanup_refs = [line for line in refs if "cleanup_" in line]
    assert len(cleanup_refs) == 1
    assert cleanup_refs[0].endswith(tip)
    assert git(repo, "cat-file", "-e", f"{tip}^{{commit}}", check=False).returncode == 0


def test_archive_creation_failure_never_deletes_branch(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    create_branch_commit(repo, "meister/integration/fail-archive", "preserve.txt")

    def fail_archive(*_args, **_kwargs):
        raise CleanError("injected archive failure")

    monkeypatch.setattr("meister.clean._create_archive_ref", fail_archive)
    result = CliRunner().invoke(
        main,
        ["clean", "--repo", str(repo), "--apply", "--archive-and-delete"],
    )
    assert result.exit_code == 1
    assert branch_exists(repo, "meister/integration/fail-archive")
    assert "injected archive failure" in result.output


def test_keep_base_validation_outside_repo_and_archive_refs(tmp_path):
    repo = make_repo(tmp_path / "repo")
    git(repo, "branch", "meister/integration/prefix-run", "main")
    git(repo, "update-ref", "refs/meister/archive/pinned", "HEAD")
    result = CliRunner().invoke(
        main,
        ["clean", "--repo", str(repo), "--apply", "--keep", "prefix"],
    )
    assert result.exit_code == 0, result.output
    assert branch_exists(repo, "meister/integration/prefix-run")
    assert git(repo, "show-ref", "--verify", "refs/meister/archive/pinned").returncode == 0

    invalid_base = CliRunner().invoke(main, ["clean", "--repo", str(repo), "--base", "missing"])
    assert invalid_base.exit_code != 0
    assert "Branch base inexistente: missing" in invalid_base.output
    outside = CliRunner().invoke(main, ["clean", "--repo", str(tmp_path)])
    assert outside.exit_code != 0
    assert "Não é um repositório Git" in outside.output
    git(repo, "branch", "-m", "main", "master")
    fallback = CliRunner().invoke(main, ["clean", "--repo", str(repo), "--json"])
    assert json.loads(fallback.output)["base"] == "master"


def test_process_gate_matches_execution_not_mentions_and_force_busy_applies(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    git(repo, "branch", "meister/integration/safe", "main")
    monkeypatch.setattr(
        "meister.clean.list_processes",
        lambda: [ProcessInfo(123, ["python", "meister", "orchestrate"])],
    )
    blocked = CliRunner().invoke(main, ["clean", "--repo", str(repo), "--apply"])
    assert blocked.exit_code == 3
    assert branch_exists(repo, "meister/integration/safe")

    monkeypatch.setattr(
        "meister.clean.list_processes",
        lambda: [ProcessInfo(123, ["sleep", "meister orchestrate"])],
    )
    allowed = CliRunner().invoke(main, ["clean", "--repo", str(repo), "--apply"])
    assert allowed.exit_code == 0, allowed.output
    assert not branch_exists(repo, "meister/integration/safe")

    git(repo, "branch", "meister/integration/force", "main")
    monkeypatch.setattr(
        "meister.clean.list_processes",
        lambda: [ProcessInfo(123, ["bash", "meister", "worker"])],
    )
    forced = CliRunner().invoke(main, ["clean", "--repo", str(repo), "--apply", "--force-busy"])
    assert forced.exit_code == 0, forced.output
    assert not branch_exists(repo, "meister/integration/force")


def test_close_stale_runs_only_closes_unowned_pending_or_running_runs(tmp_path, monkeypatch):
    from meister.state import RunState, StateManager

    repo = make_repo(tmp_path / "repo")
    db = tmp_path / "meister.db"
    monkeypatch.setenv("MEISTER_DB_PATH", str(db))
    state = StateManager(str(db))
    stale = state.create_or_get_run("stale", cwd=str(repo), force_run_id="stale")
    state.transition_run(stale["run_id"], RunState.RUNNING)
    pane = state.create_or_get_run("pane", cwd=str(repo), force_run_id="pane")
    state.transition_run(pane["run_id"], RunState.RUNNING)
    state.register_pane("pane-1", run_id="pane")
    state.create_or_get_run("pending", cwd=str(repo), force_run_id="pending")
    live = state.create_or_get_run("live", cwd=str(repo), force_run_id="live")
    state.transition_run(live["run_id"], RunState.RUNNING)
    deleted_branch = state.create_or_get_run("deleted", cwd=str(repo), force_run_id="deleted")
    state.transition_run(deleted_branch["run_id"], RunState.RUNNING)
    git(repo, "branch", "meister/integration/deleted", "main")
    completed = state.create_or_get_run("done", cwd=str(repo), force_run_id="done")
    state.transition_run(completed["run_id"], RunState.RUNNING)
    state.transition_run(completed["run_id"], RunState.COMPLETED)
    failed = state.create_or_get_run("failed", cwd=str(repo), force_run_id="failed")
    state.transition_run(failed["run_id"], RunState.RUNNING)
    state.transition_run(failed["run_id"], RunState.FAILED)

    before = db.read_bytes()
    no_close = CliRunner().invoke(main, ["clean", "--repo", str(repo), "--apply"])
    assert no_close.exit_code == 0, no_close.output
    assert db.read_bytes() == before
    monkeypatch.setattr(
        "meister.clean.list_processes",
        lambda: [ProcessInfo(123, ["python", "meister", "worker", "live"])],
    )
    closed = CliRunner().invoke(
        main,
        ["clean", "--repo", str(repo), "--apply", "--close-stale-runs", "--force-busy"],
    )
    assert closed.exit_code == 0, closed.output
    assert state.get_run("stale")["state"] == RunState.CANCELLED.value
    assert json.loads(state.get_run("stale")["metadata_json"])["cleaned_by"] == "meister clean"
    assert state.get_run("pane")["state"] == RunState.RUNNING.value
    assert state.get_run("pending")["state"] == RunState.CANCELLED.value
    assert state.get_run("live")["state"] == RunState.RUNNING.value
    assert state.get_run("deleted")["state"] == RunState.CANCELLED.value
    assert state.get_run("done")["state"] == RunState.COMPLETED.value
    assert state.get_run("failed")["state"] == RunState.FAILED.value


def test_json_is_stable_and_simulation_message_is_text_only(tmp_path):
    repo = make_repo(tmp_path / "repo")
    git(repo, "branch", "meister/integration/json", "main")
    runner = CliRunner()
    simulation = runner.invoke(main, ["clean", "--repo", str(repo), "--json"])
    assert simulation.exit_code == 0, simulation.output
    payload = json.loads(simulation.output)
    assert list(payload) == sorted(payload)
    assert payload["modo"] == "simulacao"
    assert payload["branches"][0]["branch"] == "meister/integration/json"
    assert "Nada foi alterado." in payload["mensagem_simulacao"]
    text = runner.invoke(main, ["clean", "--repo", str(repo)])
    assert "Nada foi alterado." in text.output


def test_close_stale_runs_requires_apply(tmp_path):
    repo = make_repo(tmp_path / "repo")
    result = CliRunner().invoke(main, ["clean", "--repo", str(repo), "--close-stale-runs"])
    assert result.exit_code != 0
    assert "--close-stale-runs exige --apply" in result.output


def test_archive_ref_creation_is_create_only(tmp_path):
    repo = make_repo(tmp_path / "repo")
    tip = git(repo, "rev-parse", "HEAD").stdout.strip()
    ref = _create_archive_ref(str(repo), "meister/integration/run", tip, now=123)
    assert ref.startswith("refs/meister/archive/cleanup_")
    with pytest.raises(CleanError, match="Falha ao criar ref"):
        _create_archive_ref(str(repo), "meister/integration/run", tip, now=123)


def test_active_process_detection_ignores_mere_mentions_of_the_command_names():
    mentions = [
        ProcessInfo(1, ["sleep", "orchestrate"]),
        ProcessInfo(2, ["node", "/opt/homebrew/bin/copilot", "-p", "orchestrate"]),
        ProcessInfo(3, ["vim", "worker"]),
    ]
    assert _active_meister_processes(mentions) == []
    running = [
        ProcessInfo(10, ["python", "-m", "meister.cli", "orchestrate"]),
        ProcessInfo(11, ["/usr/bin/python3.13", "/x/bin/meister", "run-task", "t.json"]),
        ProcessInfo(12, ["meister", "worker"]),
        ProcessInfo(13, ["bash", "bin/meister", "orchestrate"]),
    ]
    assert [process.pid for process in _active_meister_processes([*mentions, *running])] == [10, 11, 12, 13]


def test_apply_cleanup_refuses_a_simulation_plan(tmp_path):
    repo = make_repo(tmp_path / "repo")
    git(repo, "branch", "meister/integration/safe", "main")
    simulation = plan_cleanup(str(repo), apply=False)
    with pytest.raises(CleanError):
        apply_cleanup(simulation, processes=[])
    assert branch_exists(repo, "meister/integration/safe")


def test_branch_changed_between_planning_and_applying_is_kept(tmp_path):
    repo = make_repo(tmp_path / "repo")
    git(repo, "branch", "meister/integration/moving", "main")
    plan = plan_cleanup(str(repo), apply=True)
    assert plan.branches[0].situation == "merged"
    # depois do plano, a branch ganha trabalho novo: ja nao e segura para apagar
    git(repo, "checkout", "meister/integration/moving")
    (repo / "late.txt").write_text("late work\n")
    git(repo, "add", "late.txt")
    git(repo, "commit", "-m", "late work")
    git(repo, "checkout", "main")
    result = apply_cleanup(plan, processes=[])
    assert branch_exists(repo, "meister/integration/moving")
    assert result["deleted"] == []
    assert result["failures"]
    assert plan.branches[0].action == "mantida (branch alterada)"


def test_simulation_summary_counts_the_branches_that_would_be_deleted(tmp_path):
    repo = make_repo(tmp_path / "repo")
    git(repo, "branch", "meister/integration/gone-a", "main")
    git(repo, "branch", "meister/integration/gone-b", "main")
    create_branch_commit(repo, "meister/integration/kept", "kept.txt")
    runner = CliRunner()
    text = runner.invoke(main, ["clean", "--repo", str(repo)])
    assert "3 avaliadas, 2 seriam apagadas, 1 mantidas" in text.output
    payload = json.loads(runner.invoke(main, ["clean", "--repo", str(repo), "--json"]).output)
    assert payload["resumo"]["seriam_apagadas"] == 2
    assert payload["resumo"]["apagadas"] == 0
    assert payload["resumo"]["mantidas"] == 1
    applied = json.loads(runner.invoke(main, ["clean", "--repo", str(repo), "--apply", "--json"]).output)
    assert applied["resumo"]["apagadas"] == 2
    assert applied["resumo"]["seriam_apagadas"] == 0
    assert applied["resumo"]["mantidas"] == 1
