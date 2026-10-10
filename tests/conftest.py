import math
import os
import pathlib
import shlex
import signal
import subprocess
import tempfile
import time
import warnings
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from tests.parallel_default import default_numprocesses


REAL_MEISTER_WORKTREES_DIR = pathlib.Path(
    os.environ.get("MEISTER_WORKTREES_DIR") or os.path.expanduser("~/.meister/worktrees")
).resolve()


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    """Liga o pytest-xdist sozinho na rodada da suíte inteira, se ele estiver instalado (ver parallel_default)."""
    chosen = default_numprocesses(
        config.args,
        getattr(config.option, "numprocesses", None),
        config.pluginmanager.has_plugin("xdist"),
        os.environ,
    )
    if chosen is not None:
        config.option.numprocesses = int(chosen) if chosen.isdigit() else chosen


# Estes módulos gravam e leem arquivos de tarefa em <diretório de trabalho>/.meister/runs. Com o diretório
# compartilhado (a raiz do repositório), o `auto_write_result` de um teste respondia às tarefas dos outros
# testes rodando ao mesmo tempo (pytest-xdist): resultado errado ou travado até o watchdog de 180 s.
CWD_ISOLATED_MODULES = frozenset({"test_cli", "test_herdr_bridge", "test_herdr_tabs", "test_task_runner", "test_orchestrate_interrupt", "test_resume_attempt_numbering"})


@pytest.fixture(autouse=True)
def isolate_cwd_for_task_files(request, tmp_path, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] in CWD_ISOLATED_MODULES:
        monkeypatch.chdir(tmp_path)


@dataclass(frozen=True)
class RepoSnapshot:
    refs: frozenset
    worktrees: frozenset
    # pares (caminho do worktree, ref da branch aberta nele), para atribuir branches a worktrees
    worktree_branches: frozenset = frozenset()


@dataclass(frozen=True)
class RepoLeaks:
    refs: frozenset
    worktrees: frozenset


def snapshot_project_repo(repo_root):
    """Return the MeisterRouter refs and worktree paths, or None if Git is unavailable."""
    try:
        refs_result = subprocess.run(
            [
                "git", "-C", str(repo_root), "for-each-ref",
                "--format=%(refname)", "refs/heads/meister", "refs/meister",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        worktrees_result = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    refs = frozenset(line for line in refs_result.stdout.splitlines() if line)
    worktrees = set()
    pairs = set()
    current_path = None
    for line in worktrees_result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = str(pathlib.Path(line.removeprefix("worktree ")).resolve())
            worktrees.add(current_path)
        elif line.startswith("branch ") and current_path:
            pairs.add((current_path, line.removeprefix("branch ")))
    return RepoSnapshot(
        refs=refs,
        worktrees=frozenset(worktrees),
        worktree_branches=frozenset(pairs),
    )


def find_repo_leaks(before, after):
    """Compare two snapshots without consulting or mutating repository state."""
    if before is None or after is None:
        return RepoLeaks(refs=frozenset(), worktrees=frozenset())
    return RepoLeaks(
        refs=after.refs - before.refs,
        worktrees=after.worktrees - before.worktrees,
    )


def split_owned_leaks(leaks, after, owned_dir):
    """Separate leaks attributable to the test from those that are not.

    Owned = worktrees living inside the test's own isolated directory, plus the branches checked out in
    them. Everything else that appeared (e.g. archive refs or worktrees from a real MeisterRouter run in
    the same checkout) is "foreign": it is reported but NEVER deleted.
    """
    if after is None or owned_dir is None:
        return RepoLeaks(frozenset(), frozenset()), leaks
    base = pathlib.Path(owned_dir).resolve()
    owned_worktrees = frozenset(
        worktree for worktree in leaks.worktrees
        if pathlib.Path(worktree).is_relative_to(base)
    )
    owned_refs = frozenset(
        ref for worktree, ref in after.worktree_branches
        if worktree in owned_worktrees and ref in leaks.refs
    )
    owned = RepoLeaks(refs=owned_refs, worktrees=owned_worktrees)
    foreign = RepoLeaks(
        refs=leaks.refs - owned.refs,
        worktrees=leaks.worktrees - owned.worktrees,
    )
    return owned, foreign


def split_external_meister_leaks(foreign, real_worktrees_dir, live_meister):
    """Separate external MeisterRouter activity from leaks that should still fail the test."""
    base = pathlib.Path(real_worktrees_dir).resolve()
    external_worktrees = frozenset(
        worktree for worktree in foreign.worktrees
        if pathlib.Path(worktree).resolve().is_relative_to(base)
    )
    has_meister_refs = any(
        ref.startswith(("refs/heads/meister/", "refs/meister/"))
        for ref in foreign.refs
    )
    external_refs = frozenset()
    if has_meister_refs and (external_worktrees or live_meister):
        external_refs = frozenset(
            ref for ref in foreign.refs
            if ref.startswith(("refs/heads/meister/", "refs/meister/"))
        )
    external = RepoLeaks(refs=external_refs, worktrees=external_worktrees)
    remaining = RepoLeaks(
        refs=foreign.refs - external.refs,
        worktrees=foreign.worktrees - external.worktrees,
    )
    return external, remaining


def meister_is_running():
    """Return whether an active MeisterRouter process is visible."""
    try:
        from meister.clean import _active_meister_processes, list_processes

        return bool(_active_meister_processes(list_processes()))
    except Exception:
        return False


def cleanup_repo_leaks(repo_root, leaks):
    """Remove the given refs and worktrees (callers pass only leaks attributable to the test)."""
    failures = []
    for worktree in sorted(leaks.worktrees):
        result = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "remove", "--force", worktree],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode:
            failures.append(f"{worktree}: {result.stderr.strip()}")

    for ref in sorted(leaks.refs):
        result = subprocess.run(
            ["git", "-C", str(repo_root), "update-ref", "-d", ref],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode:
            failures.append(f"{ref}: {result.stderr.strip()}")
    return failures


@contextmanager
def test_watchdog(timeout_seconds, nodeid):
    """Interrupt a hung test with SIGALRM where interval timers are supported."""
    alarm_signal = getattr(signal, "SIGALRM", None)
    setitimer = getattr(signal, "setitimer", None)
    if timeout_seconds == 0 or alarm_signal is None or setitimer is None:
        yield
        return

    old_handler = signal.getsignal(alarm_signal)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started_at = time.monotonic()

    def timeout_handler(_signum, _frame):
        raise TimeoutError(
            f"teste excedeu {timeout_seconds:g} s (provavel travamento): {nodeid}"
        )

    signal.signal(alarm_signal, timeout_handler)
    setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    finally:
        setitimer(signal.ITIMER_REAL, 0)
        signal.signal(alarm_signal, old_handler)
        old_delay, old_interval = old_timer
        if old_delay:
            remaining = max(0.001, old_delay - (time.monotonic() - started_at))
            setitimer(signal.ITIMER_REAL, remaining, old_interval)


def configured_test_timeout():
    """Parse and validate the per-test watchdog setting."""
    try:
        timeout_seconds = float(os.environ.get("MEISTER_TEST_TIMEOUT", "180"))
    except ValueError as exc:
        raise pytest.UsageError("MEISTER_TEST_TIMEOUT deve ser um numero >= 0") from exc
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise pytest.UsageError("MEISTER_TEST_TIMEOUT deve ser um numero >= 0")
    return timeout_seconds


@pytest.fixture
def tmp_path():
    """Override tmp_path fixture to use /tmp to avoid macOS AF_UNIX 104-char path limit.

    Onde /tmp não existe (Windows) usa o diretório temporário padrão do sistema.
    """
    base = "/tmp" if os.path.isdir("/tmp") else None
    with tempfile.TemporaryDirectory(dir=base) as d:
        yield pathlib.Path(d)


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, monkeypatch):
    """Isolate DB, logs, and worktrees to tmp_path for all tests."""
    isolate_dir = tmp_path / "_meister_isolated"
    isolate_dir.mkdir(parents=True, exist_ok=True)
    test_db = str(isolate_dir / "test_meister.db")
    test_logs = str(isolate_dir / "logs")
    test_wt = str(isolate_dir / "wt")
    os.makedirs(test_logs, exist_ok=True)
    os.makedirs(test_wt, exist_ok=True)
    monkeypatch.setenv("MEISTER_DB_PATH", test_db)
    monkeypatch.setenv("MEISTER_LOG_DIR", test_logs)
    monkeypatch.setenv("MEISTER_WORKTREES_DIR", test_wt)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Meister CI")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "ci@meisterrouter.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Meister CI")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "ci@meisterrouter.local")
    yield


@pytest.fixture(autouse=True)
def prevent_project_repo_leaks(request, tmp_path):
    """Fail if a test pollutes the real checkout's refs or worktrees; clean only what is provably the test's.

    Refs/worktrees that appear but cannot be attributed to the test (not inside its own isolated directory)
    are reported and left untouched: they may come from a legitimate concurrent MeisterRouter run.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    before = snapshot_project_repo(repo_root)
    yield
    after = snapshot_project_repo(repo_root)
    leaks = find_repo_leaks(before, after)
    if not leaks.refs and not leaks.worktrees:
        return

    owned, foreign = split_owned_leaks(leaks, after, tmp_path)
    failures = cleanup_repo_leaks(repo_root, owned)
    has_meister_refs = any(
        ref.startswith(("refs/heads/meister/", "refs/meister/"))
        for ref in foreign.refs
    )
    external, remaining = split_external_meister_leaks(
        foreign,
        REAL_MEISTER_WORKTREES_DIR,
        meister_is_running() if has_meister_refs else False,
    )

    def describe(group):
        return [
            *(f"branch/ref {ref}" for ref in sorted(group.refs)),
            *(f"worktree {worktree}" for worktree in sorted(group.worktrees)),
        ]

    if external.refs or external.worktrees:
        warnings.warn(
            "atividade de um MeisterRouter externo durante o teste, ignorada: "
            + ", ".join(describe(external)),
            UserWarning,
        )

    if not owned.refs and not owned.worktrees and not remaining.refs and not remaining.worktrees and not failures:
        return

    reported = RepoLeaks(
        refs=owned.refs | remaining.refs,
        worktrees=owned.worktrees | remaining.worktrees,
    )
    message = (
        f"o teste {request.node.nodeid} deixou {', '.join(describe(reported))} "
        "no repositorio real: rode-o num repo temporario"
    )
    if owned.refs or owned.worktrees:
        message += f"; removido (era do proprio teste): {', '.join(describe(owned))}"
    if remaining.refs or remaining.worktrees:
        message += (
            f"; NAO removido (nao atribuivel ao teste): {', '.join(describe(remaining))}"
        )
    if failures:
        message += f"; falha na limpeza: {'; '.join(failures)}"
    pytest.fail(message, pytrace=False)


@pytest.fixture(autouse=True)
def timeout_each_test(request):
    """Interrupt a stuck test after MEISTER_TEST_TIMEOUT seconds (default 180)."""
    with test_watchdog(configured_test_timeout(), request.node.nodeid):
        yield


@pytest.fixture(autouse=True)
def block_network_and_stub_jev(monkeypatch):
    """Prevent real network access and make bridge routing deterministic."""
    def blocked_post(*_args, **_kwargs):
        raise RuntimeError("rede bloqueada em testes")

    def deterministic_classify_task(*, implementers, **_kwargs):
        tiers = list(implementers)
        names = [tier.name for tier in tiers]
        return {
            "recommended_implementer": names[0],
            "classification": "SMALL",
            "classification_confidence": 1.0,
            "fallback_rule_applied": False,
            "api_unavailable": False,
            "fallback_chain": names[1:],
        }

    monkeypatch.setattr("requests.post", blocked_post)
    monkeypatch.setattr("meister.herdr.bridge.classify_task", deterministic_classify_task)


@pytest.fixture(autouse=True)
def forbid_real_dashboard_and_browser(monkeypatch):
    """Prevent tests from starting a real dashboard server or browser."""
    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        def __init__(self, args, *popenargs, **kwargs):
            command = os.fsdecode(args) if isinstance(args, (str, bytes, os.PathLike)) else list(args)
            if isinstance(command, str):
                try:
                    parts = shlex.split(command)
                except ValueError:
                    parts = command.split()
            else:
                parts = [os.fsdecode(part) for part in command]

            has_dashboard_command = any(
                parts[index:index + 2] == ["meister.cli", "dashboard"]
                for index in range(len(parts) - 1)
            )
            has_app_argument = any(part.startswith("--app=") for part in parts)
            executable = pathlib.Path(parts[0]).name.lower() if parts else ""
            is_browser = any(
                browser in executable
                for browser in ("chrome", "chromium", "brave", "edge", "msedge", "firefox")
            )
            if has_dashboard_command or has_app_argument or is_browser:
                raise AssertionError(
                    "test tried to launch a real dashboard server or browser: "
                    f"{args!r}"
                )
            super().__init__(args, *popenargs, **kwargs)

    def block_webbrowser(*args, **kwargs):
        raise AssertionError(
            "test tried to launch a real dashboard server or browser: "
            f"{args or kwargs!r}"
        )

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    monkeypatch.setattr(webbrowser, "open", block_webbrowser)
    monkeypatch.setattr(webbrowser, "open_new_tab", block_webbrowser)


@pytest.fixture(autouse=True)
def default_test_language(monkeypatch):
    """Keep test suite in Portuguese during transition, with clean cache."""
    from meister.i18n import reset_language_cache

    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    reset_language_cache()
    yield
    reset_language_cache()
