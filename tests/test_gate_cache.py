import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from meister.config import GateCommand, MeisterConfig
from meister.gate import DeterministicGate
from meister.logger import add_event_observer, remove_event_observer
from meister.worktree import IntegrationPipeline, WorktreeManager


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "cache-test@meisterrouter.local")
    _git(repo, "config", "user.name", "Meister Cache Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")


def _configured_gate(repo, counter, *, exit_code=0, timeout=10, cache=True):
    config = MeisterConfig()
    config.environment.install_dependencies = False
    config.gate.cache = cache
    code = (
        "import sys; "
        "open(sys.argv[1], 'a', encoding='utf-8').write('run\\n'); "
        f"sys.exit({exit_code})"
    )
    config.gate.commands = [
        GateCommand(
            name="counter",
            run=[sys.executable, "-c", code, str(counter)],
            timeout_seconds=timeout,
            required=True,
        )
    ]
    return DeterministicGate(str(repo), config=config)


def _runs(counter):
    return len(counter.read_text(encoding="utf-8").splitlines()) if counter.exists() else 0


def test_same_tree_uses_cached_success_and_reports_saved_time(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    counter = tmp_path / "counter.txt"
    gate = _configured_gate(repo, counter)

    first = gate.run_verification_ex()
    second = gate.run_verification_ex()

    assert first.passed and not first.cached
    assert second.passed and second.cached
    assert second.saved_seconds > 0
    assert _runs(counter) == 1


@pytest.mark.parametrize("change", ["tracked", "untracked"])
def test_file_changes_invalidate_cache(tmp_path, change):
    repo = tmp_path / "repo"
    _init_repo(repo)
    counter = tmp_path / "counter.txt"
    gate = _configured_gate(repo, counter)
    assert gate.run_verification_ex().passed

    if change == "tracked":
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    else:
        (repo / "new-file.txt").write_text("new\n", encoding="utf-8")

    changed = gate.run_verification_ex()
    assert changed.passed and not changed.cached
    assert _runs(counter) == 2


def test_ignored_file_change_does_not_invalidate_cache(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore generated file")
    counter = tmp_path / "counter.txt"
    gate = _configured_gate(repo, counter)
    assert gate.run_verification_ex().passed

    (repo / "ignored.txt").write_text("ignored one\n", encoding="utf-8")
    assert gate.run_verification_ex().cached
    (repo / "ignored.txt").write_text("ignored two\n", encoding="utf-8")
    assert gate.run_verification_ex().cached
    assert _runs(counter) == 1


def test_failures_and_infrastructure_errors_are_never_cached(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    fail_counter = tmp_path / "fail-counter.txt"
    failing_gate = _configured_gate(repo, fail_counter, exit_code=1)

    failed_first = failing_gate.run_verification_ex()
    failed_second = failing_gate.run_verification_ex()
    assert not failed_first.passed and not failed_second.passed
    assert not failed_first.cached and not failed_second.cached
    assert _runs(fail_counter) == 2

    timeout_counter = tmp_path / "timeout-counter.txt"
    # 1 s (e não 0,1 s): o comando precisa iniciar o Python e gravar a linha antes do corte; com a máquina
    # carregada (gates em paralelo) 0,1 s não bastava e o teste contava 1 execução em vez de 2
    timeout_gate = _configured_gate(repo, timeout_counter, timeout=1.0)
    timeout_gate.config.gate.commands[0].run = [
        sys.executable,
        "-c",
        "import sys, time; open(sys.argv[1], 'a').write('run\\n'); time.sleep(10)",
        str(timeout_counter),
    ]
    infra_first = timeout_gate.run_verification_ex()
    infra_second = timeout_gate.run_verification_ex()
    assert infra_first.infrastructure_error and infra_second.infrastructure_error
    assert not infra_first.cached and not infra_second.cached
    assert _runs(timeout_counter) == 2


def test_config_changes_and_disabled_cache_force_execution(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    counter = tmp_path / "counter.txt"
    gate = _configured_gate(repo, counter)

    assert gate.run_verification_ex().passed
    gate.config.gate.commands[0].name = "renamed"
    assert not gate.run_verification_ex().cached
    gate.config.gate.python = sys.executable
    assert not gate.run_verification_ex().cached
    gate.config.gate.cache = False
    assert not gate.run_verification_ex().cached
    assert not gate.run_verification_ex().cached
    assert _runs(counter) == 5


def test_cache_key_is_path_independent_and_same_for_loose_or_committed_tree(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    second_worktree = tmp_path / "second-worktree"
    _git(repo, "worktree", "add", "--detach", str(second_worktree), "HEAD")
    counter = tmp_path / "counter.txt"
    gate = _configured_gate(repo, counter)

    new_content = "same loose content\n"
    (repo / "tracked.txt").write_text(new_content, encoding="utf-8")
    (second_worktree / "tracked.txt").write_text(new_content, encoding="utf-8")
    first = gate.run_verification_ex()
    second = gate.run_verification_ex(repo_path=str(second_worktree))
    assert first.passed and second.cached

    _git(second_worktree, "add", "tracked.txt")
    _git(second_worktree, "commit", "-m", "commit same content")
    committed = gate.run_verification_ex()
    assert committed.cached
    assert _runs(counter) == 1


def test_parallel_cache_lookups_return_success_without_exceptions(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    counter = tmp_path / "counter.txt"
    gate = _configured_gate(repo, counter)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: gate.run_verification_ex(), range(4)))

    assert all(result.passed for result in results)
    assert all(not result.infrastructure_error for result in results)
    assert 1 <= _runs(counter) <= 4


def test_pipeline_reuses_worker_gate_result_after_merge_and_logs_cache_hit(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    counter = tmp_path / "counter.txt"
    gate = _configured_gate(repo, counter)
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    pipeline = IntegrationPipeline(manager, gate=gate)
    integration = pipeline.start_integration("cache-run")
    worker = manager.create_worktree("cache-task", base_ref=integration.branch_name)
    with open(os.path.join(worker.worktree_path, "tracked.txt"), "a", encoding="utf-8") as stream:
        stream.write("worker change\n")

    events = []
    observer = events.append
    add_event_observer(observer)
    try:
        passed, _ = pipeline.integrate_subtask(
            worker, target_files=["tracked.txt"], task_id="cache-task"
        )
    finally:
        remove_event_observer(observer)
        pipeline.abort_integration()
        manager.cleanup_worktree(worker.task_id, force=True)

    gate_phases = [
        event for event in events
        if event.get("event") == "worker_phase" and event.get("phase") == "gate"
    ]
    assert passed
    assert _runs(counter) == 1
    assert len(gate_phases) == 2
    assert gate_phases[0]["cached"] is False
    assert gate_phases[1]["cached"] is True
    assert gate_phases[1]["saved_seconds"] > 0
