import asyncio
import signal
import subprocess
import time

import pytest

from tests.conftest import (
    RepoLeaks,
    RepoSnapshot,
    cleanup_repo_leaks,
    configured_test_timeout,
    find_repo_leaks,
    snapshot_project_repo,
    split_owned_leaks,
    test_watchdog as watchdog,
)


def _init_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    (path / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


def test_repo_leak_comparison_detects_new_refs_and_worktrees():
    before = RepoSnapshot(
        refs=frozenset({"refs/heads/meister/existing", "refs/meister/existing"}),
        worktrees=frozenset({"/repo", "/repo/old"}),
    )
    after = RepoSnapshot(
        refs=frozenset({
            "refs/heads/meister/existing",
            "refs/heads/meister/new-branch",
            "refs/meister/existing",
            "refs/meister/new-ref",
        }),
        worktrees=frozenset({"/repo", "/repo/old", "/repo/new"}),
    )

    assert find_repo_leaks(before, after) == RepoLeaks(
        refs=frozenset({"refs/heads/meister/new-branch", "refs/meister/new-ref"}),
        worktrees=frozenset({"/repo/new"}),
    )
    assert find_repo_leaks(after, after) == RepoLeaks(frozenset(), frozenset())


def test_repo_leak_cleanup_removes_only_what_is_provably_the_tests_own(tmp_path):
    """Worktree dentro da pasta do teste (e a branch dele) e removido; o resto e so reportado, nunca apagado."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "refs/heads/meister/existing", "HEAD"],
        check=True, capture_output=True,
    )
    before = snapshot_project_repo(repo)
    assert before is not None

    owned_dir = tmp_path / "owned"
    owned_dir.mkdir()
    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir()
    subprocess.run(["git", "-C", str(repo), "branch", "meister/owned-branch", "HEAD"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", str(owned_dir / "wt"), "meister/owned-branch"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(repo), "branch", "meister/foreign-branch", "HEAD"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", str(foreign_dir / "wt"), "meister/foreign-branch"],
        check=True, capture_output=True,
    )
    # uma ref de arquivo criada por outra execucao (ex.: `meister clean --archive-and-delete`)
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/meister/archive/someone-else", "HEAD"], check=True, capture_output=True)

    after = snapshot_project_repo(repo)
    assert after is not None
    leaks = find_repo_leaks(before, after)
    owned, foreign = split_owned_leaks(leaks, after, owned_dir)
    assert owned.worktrees == frozenset({str((owned_dir / "wt").resolve())})
    assert owned.refs == frozenset({"refs/heads/meister/owned-branch"})
    assert foreign.refs == frozenset({"refs/heads/meister/foreign-branch", "refs/meister/archive/someone-else"})
    assert foreign.worktrees == frozenset({str((foreign_dir / "wt").resolve())})

    assert cleanup_repo_leaks(repo, owned) == []

    cleaned = snapshot_project_repo(repo)
    assert cleaned is not None
    assert "refs/heads/meister/owned-branch" not in cleaned.refs
    assert str((owned_dir / "wt").resolve()) not in cleaned.worktrees
    # o que nao e atribuivel ao teste continua la, intacto
    assert "refs/heads/meister/foreign-branch" in cleaned.refs
    assert "refs/meister/archive/someone-else" in cleaned.refs
    assert str((foreign_dir / "wt").resolve()) in cleaned.worktrees
    assert (foreign_dir / "wt" / "base.txt").exists()
    assert "refs/heads/meister/existing" in cleaned.refs


def test_without_an_owned_dir_nothing_is_attributed_to_the_test():
    leaks = RepoLeaks(refs=frozenset({"refs/meister/archive/x"}), worktrees=frozenset({"/somewhere/wt"}))
    owned, foreign = split_owned_leaks(leaks, RepoSnapshot(refs=frozenset(), worktrees=frozenset()), None)
    assert owned == RepoLeaks(frozenset(), frozenset())
    assert foreign == leaks


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="interval timers are unavailable")
def test_watchdog_interrupts_sync_test_and_restores_timer_and_handler():
    original_handler = signal.getsignal(signal.SIGALRM)
    with pytest.raises(TimeoutError, match=r"teste excedeu .*sync-node"):
        with watchdog(0.2, "sync-node"):
            time.sleep(3)

    assert signal.getsignal(signal.SIGALRM) is original_handler
    assert signal.getitimer(signal.ITIMER_REAL)[0] > 0


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="interval timers are unavailable")
def test_watchdog_leaves_no_timer_armed_when_there_was_none_before():
    """Sem timer externo, o watchdog tem de zerar o seu: um alarme esquecido dispararia dentro de outro teste."""
    signal.setitimer(signal.ITIMER_REAL, 0)  # desarma o timer da fixture autouse so para este teste
    with watchdog(60, "clear-node"):
        pass
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="interval timers are unavailable")
def test_watchdog_interrupts_async_wait():
    with pytest.raises(TimeoutError, match=r"teste excedeu .*async-node"):
        with watchdog(0.2, "async-node"):
            asyncio.run(asyncio.sleep(3))


def test_zero_timeout_disables_watchdog(monkeypatch):
    monkeypatch.setenv("MEISTER_TEST_TIMEOUT", "0")
    assert configured_test_timeout() == 0
    original_handler = signal.getsignal(signal.SIGALRM) if hasattr(signal, "SIGALRM") else None
    timer_calls = []
    if hasattr(signal, "setitimer"):
        monkeypatch.setattr(
            signal,
            "setitimer",
            lambda *args: timer_calls.append(args),
        )
    with watchdog(configured_test_timeout(), "disabled-node"):
        assert (
            signal.getsignal(signal.SIGALRM) is original_handler
            if hasattr(signal, "SIGALRM")
            else True
        )
    assert not timer_calls
