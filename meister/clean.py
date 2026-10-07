"""Safe cleanup of obsolete MeisterRouter branches."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from meister.i18n import t


class CleanError(RuntimeError):
    """Raised when cleanup cannot safely inspect or modify a repository."""


class CleanBusyError(CleanError):
    """Raised when an active MeisterRouter process prevents cleanup."""


SITUATION_MERGED = "merged"
SITUATION_EQUIVALENT = "equivalent"
SITUATION_ARCHIVED = "archived"
SITUATION_UNMERGED = "unmerged"

ACTION_IN_USE = "in_use"
ACTION_KEEP_REQUESTED = "keep_requested"
ACTION_KEEP_UNMERGED = "keep_unmerged"
ACTION_ARCHIVE_DELETE = "archive_delete"
ACTION_DELETE = "delete"
ACTION_IN_USE_OR_KEEP = "in_use_or_keep"
ACTION_CHANGED = "changed"
ACTION_ARCHIVE_FAILED = "archive_failed"
ACTION_DELETE_FAILED = "delete_failed"
ACTION_DELETED = "deleted"

_SITUATION_MESSAGES = {
    SITUATION_MERGED: "commands.clean.situation_merged",
    SITUATION_EQUIVALENT: "commands.clean.situation_equivalent",
    SITUATION_ARCHIVED: "commands.clean.situation_archived",
    SITUATION_UNMERGED: "commands.clean.situation_unmerged",
}
_ACTION_MESSAGES = {
    ACTION_IN_USE: "commands.clean.action_in_use",
    ACTION_KEEP_REQUESTED: "commands.clean.action_keep",
    ACTION_KEEP_UNMERGED: "commands.clean.action_unmerged",
    ACTION_ARCHIVE_DELETE: "commands.clean.action_archive_delete",
    ACTION_DELETE: "commands.clean.action_delete",
    ACTION_IN_USE_OR_KEEP: "commands.clean.action_in_use_or_keep",
    ACTION_CHANGED: "commands.clean.action_changed",
    ACTION_ARCHIVE_FAILED: "commands.clean.action_archive_failed",
    ACTION_DELETE_FAILED: "commands.clean.action_delete_failed",
    ACTION_DELETED: "commands.clean.action_deleted",
}


@dataclass
class BranchPlan:
    branch: str
    ahead: int
    situation: str
    action: str
    commits: List[str] = field(default_factory=list)
    tip: str = ""
    situation_code: str = ""
    action_code: str = ""


@dataclass
class CleanupPlan:
    repo: str
    base: str
    apply: bool
    branches: List[BranchPlan]
    archive_refs_preserved: int


@dataclass
class ProcessInfo:
    pid: int
    argv: List[str]


def _git(repo: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CleanError(t("commands.clean.git_failed", args=" ".join(args), detail=detail))
    return result.stdout.strip()


def resolve_repository(path: str) -> str:
    """Resolve a repository root or raises a user-readable error."""
    result = subprocess.run(
        ["git", "-C", os.path.abspath(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise CleanError(t("commands.clean.not_git_repo", path=os.path.abspath(path)))
    return os.path.realpath(result.stdout.strip())


def resolve_base(repo: str, requested: Optional[str]) -> str:
    """Select and verify the requested/default base branch."""
    if requested:
        candidates = [requested]
    else:
        candidates = ["main", "master"]
    for candidate in candidates:
        result = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{candidate}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return candidate
    if requested:
        raise CleanError(t("commands.clean.base_missing", branch=requested))
    raise CleanError(t("commands.clean.base_missing_default"))


def list_processes() -> List[ProcessInfo]:
    """Read process argv without matching command text embedded in another argument."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise CleanError(t("commands.clean.process_inspection_failed", error=result.stderr.strip()))
    processes = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
            argv = shlex.split(fields[1])
        except ValueError:
            continue
        if argv:
            processes.append(ProcessInfo(pid, argv))
    return processes


def _active_meister_processes(processes: Iterable[ProcessInfo]) -> List[ProcessInfo]:
    active = []
    commands = {"orchestrate", "run-task", "worker"}
    executables = {"python", "python3", "bash", "sh", "meister"}
    for process in processes:
        argv = process.argv
        executable = os.path.basename(argv[0]).lower()
        if executable not in executables and not executable.startswith("python3."):
            continue
        if any(arg in commands for arg in argv[1:]):
            active.append(process)
    return active


def _worktree_branches(repo: str) -> Set[str]:
    """Branches abertas em worktrees que ainda existem.

    Worktrees `prunable` (a pasta ja foi apagada, tipico de teste/tmp) NAO protegem a branch: o git so
    guarda um registro velho, e `git worktree prune` o remove.
    """
    output = _git(repo, "worktree", "list", "--porcelain")
    branches: Set[str] = set()
    branch: Optional[str] = None
    prunable = False
    for line in [*output.splitlines(), ""]:
        if not line:
            if branch and not prunable:
                branches.add(branch)
            branch, prunable = None, False
        elif line.startswith("branch refs/heads/"):
            branch = line.removeprefix("branch refs/heads/")
        elif line.startswith("prunable"):
            prunable = True
    return branches


def _branch_names(repo: str) -> List[str]:
    output = _git(
        repo,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads/meister/integration",
        "refs/heads/meister/worktree",
    )
    return sorted(line for line in output.splitlines() if line)


def classify_branch(repo: str, base: str, branch: str) -> Tuple[int, str, List[str], str]:
    """Return ahead count, classification, displayed commits, and tip SHA."""
    ahead, situation_code, commits, tip = _classify_branch_code(repo, base, branch)
    return ahead, t(_SITUATION_MESSAGES[situation_code]), commits, tip


def _classify_branch_code(repo: str, base: str, branch: str) -> Tuple[int, str, List[str], str]:
    """Classify a branch with stable codes for internal decisions."""
    ahead = int(_git(repo, "rev-list", "--count", f"{base}..{branch}"))
    tip = _git(repo, "rev-parse", branch)
    if ahead == 0:
        return ahead, SITUATION_MERGED, [], tip

    cherry = _git(repo, "cherry", base, branch)
    if not any(line.startswith("+") for line in cherry.splitlines()):
        return ahead, SITUATION_EQUIVALENT, [], tip

    archived = _git(
        repo, "for-each-ref", "--contains", tip, "--format=%(refname)", "refs/meister/archive"
    )
    if archived:
        return ahead, SITUATION_ARCHIVED, [], tip

    commits = _git(
        repo, "log", "--oneline", "--format=%h %s", f"{base}..{branch}"
    ).splitlines()[:5]
    return ahead, SITUATION_UNMERGED, commits, tip


def plan_cleanup(
    repo: str,
    base: Optional[str] = None,
    keep: Sequence[str] = (),
    apply: bool = False,
    archive_and_delete: bool = False,
) -> CleanupPlan:
    """Inspect branches and produce a non-mutating cleanup plan."""
    root = resolve_repository(repo)
    base_branch = resolve_base(root, base)
    current = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    open_worktree_branches = _worktree_branches(root)
    protected = set(open_worktree_branches)
    if current:
        protected.add(current)

    plans = []
    for branch in _branch_names(root):
        ahead, situation_code, commits, tip = _classify_branch_code(root, base_branch, branch)
        run_suffix = branch.split("/", 2)[-1]
        if branch in protected:
            action_code = ACTION_IN_USE
        elif any(run_suffix.startswith(prefix) for prefix in keep):
            action_code = ACTION_KEEP_REQUESTED
        elif situation_code == SITUATION_UNMERGED and not archive_and_delete:
            action_code = ACTION_KEEP_UNMERGED
        elif situation_code == SITUATION_UNMERGED:
            action_code = ACTION_ARCHIVE_DELETE
        else:
            action_code = ACTION_DELETE
        plans.append(
            BranchPlan(
                branch,
                ahead,
                t(_SITUATION_MESSAGES[situation_code]),
                t(_ACTION_MESSAGES[action_code]),
                commits,
                tip,
                situation_code,
                action_code,
            )
        )

    archive_refs = _git(
        root, "for-each-ref", "--format=%(refname)", "refs/meister/archive"
    ).splitlines()
    return CleanupPlan(root, base_branch, apply, plans, len(archive_refs))


def _is_protected(plan: CleanupPlan, branch: BranchPlan, keep: Sequence[str]) -> bool:
    current = _git(plan.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.branch == current or branch.branch in _worktree_branches(plan.repo):
        return True
    suffix = branch.branch.split("/", 2)[-1]
    return any(suffix.startswith(prefix) for prefix in keep)


def _create_archive_ref(repo: str, branch: str, tip: str, now: Optional[int] = None) -> str:
    safe_branch = "".join(char if char.isalnum() or char in "-_" else "_" for char in branch)
    epoch = int(time.time()) if now is None else now
    ref = f"refs/meister/archive/cleanup_{safe_branch}-{epoch}"
    zero_oid = "0" * len(tip)
    result = subprocess.run(
        ["git", "-C", repo, "update-ref", ref, tip, zero_oid],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise CleanError(t("commands.clean.archive_ref_failed", ref=ref, error=result.stderr.strip()))
    return ref


def _run_has_live_process(processes: Sequence[ProcessInfo], run_id: str) -> bool:
    return any(run_id in process.argv[1:] for process in processes)


def _close_stale_runs(repo: str, processes: Sequence[ProcessInfo], surviving_branches: Set[str]) -> int:
    from meister.state import RunState, StateManager

    db_path = os.environ.get("MEISTER_DB_PATH") or os.path.join(repo, ".meister", "meister.db")
    if not os.path.isfile(db_path):
        return 0
    manager = StateManager(db_path=db_path)
    with manager._get_connection() as conn:
        runs = conn.execute(
            "SELECT run_id, state, cwd FROM runs WHERE state IN (?, ?)",
            (RunState.RUNNING.value, RunState.PENDING.value),
        ).fetchall()
        panes = {
            row["run_id"]
            for row in conn.execute("SELECT DISTINCT run_id FROM active_panes WHERE run_id IS NOT NULL")
        }
    closed = 0
    for row in runs:
        if os.path.realpath(row["cwd"]) != repo:
            continue
        run_id = row["run_id"]
        branch = f"meister/integration/{run_id}"
        if branch in surviving_branches or run_id in panes or _run_has_live_process(processes, run_id):
            continue
        manager.transition_run(run_id, RunState.CANCELLED, {"cleaned_by": "meister clean"})
        closed += 1
    return closed


def apply_cleanup(
    plan: CleanupPlan,
    keep: Sequence[str] = (),
    archive_and_delete: bool = False,
    processes: Optional[Sequence[ProcessInfo]] = None,
    close_stale_runs: bool = False,
    force_busy: bool = False,
) -> Dict[str, Any]:
    """Apply safe deletions, creating archive refs before deleting unmerged branches."""
    if not plan.apply:
        raise CleanError(t("commands.clean.simulation_apply_error"))
    process_list = list_processes() if processes is None else list(processes)
    active = _active_meister_processes(process_list)
    if active and not force_busy:
        raise CleanBusyError(t("commands.clean.busy"))

    # Registros de worktrees cuja pasta nao existe mais: o git recusa apagar a branch enquanto eles existirem.
    _git(plan.repo, "worktree", "prune")
    deleted: List[str] = []
    failures: List[str] = []
    for branch in plan.branches:
        if _is_protected(plan, branch, keep):
            branch.action_code = ACTION_IN_USE_OR_KEEP
            branch.action = t(_ACTION_MESSAGES[branch.action_code])
            continue
        try:
            current_ahead, current_situation_code, _, current_tip = _classify_branch_code(
                plan.repo, plan.base, branch.branch
            )
        except CleanError as exc:
            branch.action_code = ACTION_CHANGED
            branch.action = t(_ACTION_MESSAGES[branch.action_code])
            failures.append(str(exc))
            continue
        if (
            current_tip != branch.tip
            or current_situation_code != branch.situation_code
            or current_ahead != branch.ahead
        ):
            branch.action_code = ACTION_CHANGED
            branch.action = t(_ACTION_MESSAGES[branch.action_code])
            failures.append(t("commands.clean.branch_changed", branch=branch.branch))
            continue
        if branch.situation_code == SITUATION_UNMERGED:
            if not archive_and_delete:
                branch.action_code = ACTION_KEEP_UNMERGED
                branch.action = t(_ACTION_MESSAGES[branch.action_code])
                continue
            try:
                _create_archive_ref(plan.repo, branch.branch, branch.tip)
            except CleanError as exc:
                branch.action_code = ACTION_ARCHIVE_FAILED
                branch.action = t(_ACTION_MESSAGES[branch.action_code])
                failures.append(str(exc))
                continue
        result = subprocess.run(
            ["git", "-C", plan.repo, "branch", "-D", "--", branch.branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            branch.action_code = ACTION_DELETE_FAILED
            branch.action = t(_ACTION_MESSAGES[branch.action_code])
            failures.append(
                t(
                    "commands.clean.branch_delete_failed",
                    branch=branch.branch,
                    error=result.stderr.strip(),
                )
            )
            continue
        branch.action_code = ACTION_DELETED
        branch.action = t(_ACTION_MESSAGES[branch.action_code])
        deleted.append(branch.branch)

    _git(plan.repo, "worktree", "prune")
    closed_runs = 0
    if close_stale_runs:
        surviving = set(_branch_names(plan.repo))
        closed_runs = _close_stale_runs(plan.repo, process_list, surviving)
    return {
        "deleted": deleted,
        "failures": failures,
        "closed_runs": closed_runs,
        "archive_refs_preserved": plan.archive_refs_preserved,
    }


def render_result(plan: CleanupPlan, result: Optional[Dict[str, Any]] = None, json_format: bool = False) -> str:
    """Format the stable JSON or human-readable result."""
    applied = result or {
        "deleted": [],
        "failures": [],
        "closed_runs": 0,
        "archive_refs_preserved": plan.archive_refs_preserved,
    }
    deleted = set(applied["deleted"])
    rows = [asdict(branch) for branch in plan.branches]
    for row in rows:
        if row["branch"] in deleted:
            row["action_code"] = ACTION_DELETED
            row["action"] = t(_ACTION_MESSAGES[ACTION_DELETED])
    # Na simulacao nada e apagado: contar as que SERIAM apagadas para o resumo nao dar a entender o contrario.
    would_delete = 0 if plan.apply else sum(
        1 for row in rows if row["action_code"] in (ACTION_DELETE, ACTION_ARCHIVE_DELETE)
    )
    summary = {
        "avaliadas": len(rows),
        "apagadas": len(deleted),
        "seriam_apagadas": would_delete,
        "mantidas": sum(1 for row in rows if row["branch"] not in deleted) - would_delete,
        "runs_fechados": applied["closed_runs"],
        "refs_arquivo_preservadas": applied["archive_refs_preserved"],
    }
    payload: Dict[str, Any] = {
        "base": plan.base,
        "branches": rows,
        "modo": "apply" if plan.apply else "simulacao",
        "repo": plan.repo,
        "resumo": summary,
    }
    if applied["failures"]:
        payload["falhas"] = applied["failures"]
    if not plan.apply:
        payload["mensagem_simulacao"] = t(
            "commands.clean.simulation_message",
            repo=shlex.quote(plan.repo),
        )
    if json_format:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)

    for row in rows:
        row["situation"] = t(_SITUATION_MESSAGES[row.pop("situation_code")])
        row["action"] = t(_ACTION_MESSAGES[row.pop("action_code")])

    lines = [
        f"{t('commands.clean.branch')}  {t('commands.clean.ahead')}  "
        f"{t('commands.clean.situation')}  {t('commands.clean.action')}"
    ]
    for row in rows:
        lines.append(
            f"{row['branch']}  {row['ahead']}  {row['situation']}  {row['action']}"
        )
        for commit in row["commits"]:
            lines.append(f"  {commit}")
    deleted_label = (
        t("commands.clean.would_delete", count=summary["seriam_apagadas"])
        if not plan.apply
        else t("commands.clean.deleted", count=summary["apagadas"])
    )
    lines.append(
        t(
            "commands.clean.summary",
            evaluated=summary["avaliadas"],
            deleted_label=deleted_label,
            kept=summary["mantidas"],
            runs=summary["runs_fechados"],
            refs=summary["refs_arquivo_preservadas"],
        )
    )
    if applied["failures"]:
        lines.extend(t("commands.clean.error", error=failure) for failure in applied["failures"])
    if not plan.apply:
        lines.append(
            t("commands.clean.simulation_message", repo=shlex.quote(plan.repo))
        )
    return "\n".join(lines)
