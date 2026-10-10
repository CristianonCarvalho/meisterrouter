"""
meister.worktree — Gerenciador determinístico de Git Worktrees para isolamento de workers.

Responsável por:
- Criação de worktrees dedicados por tarefa/subtarefa fora da árvore do repositório principal.
- Inspeção determinística de diff e arquivos modificados (git diff base..HEAD + untracked).
- Validação de escopo estrito (impedir que workers alterem arquivos fora de target_files).
- Limpeza pós-merge e pós-falha (git worktree remove, remoção de branch, git worktree prune).
- Varredura e descarte automático de worktrees órfãos na inicialização.

Segurança e Isolamento:
- Por padrão, os worktrees são criados FORA da árvore do repositório principal
  (em ~/.meister/worktrees/<hash_do_repo>/ ou via MEISTER_WORKTREES_DIR),
  impedindo que workers acessem arquivos como .env na raiz do repo via path traversal (../../../.env).
- Nota de Isolamento: O isolamento via worktrees externos protege arquivos e segredos do repositório,
  mas um isolamento total de processos e sistema operacional requer sandboxing no nível de container/OS.
  Para ferramentas CLI como Codex, Claude e Antigravity, utilize apenas flags formais documentadas por seus
  respectivos --help.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, asdict, field
from typing import Any, List, Optional, Set, Tuple

from meister import osops
from meister.faults import crash_point
from meister.config import load_config
from meister.env_setup import prepare_environment
from meister.i18n import t

logger = logging.getLogger(__name__)
GATE_INFRASTRUCTURE_PREFIX = "ERRO DE INFRAESTRUTURA no portão:"


class CodedMessage(str):
    """String with an associated semantic reason code."""

    code: str

    def __new__(cls, content: str, code: str = "integration") -> "CodedMessage":
        obj = str.__new__(cls, content)
        obj.code = code
        return obj

    def __repr__(self) -> str:
        return f"CodedMessage({super().__repr__()}, code={self.code!r})"


class ReplayError(RuntimeError):
    """Raised when strict integration replay cannot reconstruct a completed task."""

    code = "replay_failed"


def _log_cleanup_failure(task_id: str, path: str, reason: str) -> None:
    """Emite telemetria de uma falha de limpeza que foi engolida (sem alterar o retorno)."""
    from meister.logger import log_event

    log_event(event_type="cleanup_failure", task_id=task_id, path=path, reason=reason)



def _normalize_scope_pattern(value: str) -> str:
    raw = value.replace("\\", "/")
    trailing_slash = raw.endswith("/")
    normalized = os.path.normpath(raw).replace("\\", "/")
    if trailing_slash and normalized != ".":
        normalized += "/"
    return normalized


def _scope_matches(pattern: str, path: str, basename_glob: bool = False) -> bool:
    if path == pattern:
        return True
    if not any(char in pattern for char in "*?["):
        return (
            (basename_glob and "/" not in pattern and path.rsplit("/", 1)[-1] == pattern)
            or (pattern.endswith("/") and path.startswith(pattern))
        )
    if basename_glob and "/" not in pattern:
        pattern = f"**/{pattern}"
    if pattern.endswith("/"):
        pattern += "**"

    regex_parts: List[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*" and index + 1 < len(pattern) and pattern[index + 1] == "*":
            index += 2
            if index < len(pattern) and pattern[index] == "/":
                regex_parts.append("(?:.*/)?")
                index += 1
            else:
                regex_parts.append(".*")
            continue
        if char == "*":
            regex_parts.append("[^/]*")
        elif char == "?":
            regex_parts.append("[^/]")
        elif char == "[":
            depth = 0
            closing = -1
            for candidate in range(index + 1, len(pattern)):
                if pattern[candidate] == "[":
                    depth += 1
                elif pattern[candidate] == "]":
                    if depth:
                        depth -= 1
                    else:
                        closing = candidate
                        break
            if closing == -1:
                return False
            content = pattern[index + 1 : closing]
            if not content or content in ("!", "^"):
                return False
            literal = re.escape(pattern[index : closing + 1])
            if "[" in content or "]" in content:
                regex_parts.append(f"(?:{literal}|(?!)")
                regex_parts.append(")")
            else:
                character_class = "^" + content[1:] if content.startswith("!") else content
                regex_parts.append(f"(?:[{character_class}]|{literal})")
            index = closing
        else:
            regex_parts.append(re.escape(char))
        index += 1
    try:
        return re.fullmatch("".join(regex_parts), path) is not None
    except re.error:
        return False


def scope_violations(
    files: List[str],
    target_files: Optional[List[str]],
    tolerated_files: Optional[List[str]] = None,
    ignored_files: Optional[Set[str]] = None,
) -> List[str]:
    """Retorna arquivos fora do escopo usando as mesmas regras do portão de worktrees."""
    if not target_files:
        return []
    targets = [_normalize_scope_pattern(path) for path in target_files]
    tolerated = [_normalize_scope_pattern(path) for path in tolerated_files or []]
    ignored = ignored_files or set()
    return [
        path for path in files
        if path not in ignored
        and not any(_scope_matches(pattern, path) for pattern in targets)
        and not any(_scope_matches(pattern, path, basename_glob=True) for pattern in tolerated)
    ]


def tolerated_touched(
    files: List[str],
    target_files: Optional[List[str]],
    tolerated_files: Optional[List[str]],
    ignored_files: Optional[Set[str]] = None,
) -> List[str]:
    """Return tolerated changed files that are outside the declared target scope."""
    if not target_files:
        return []
    targets = [_normalize_scope_pattern(path) for path in target_files]
    tolerated = [_normalize_scope_pattern(path) for path in tolerated_files or []]
    ignored = ignored_files or set()
    return [
        path for path in files
        if path not in ignored
        and not any(_scope_matches(pattern, path) for pattern in targets)
        and any(_scope_matches(pattern, path, basename_glob=True) for pattern in tolerated)
    ]


def compute_repo_hash(repo_root: str) -> str:
    """Calcula um hash determinístico curto para isolar worktrees de diferentes repositórios."""
    real_path = os.path.realpath(os.path.abspath(repo_root))
    return hashlib.sha256(real_path.encode("utf-8")).hexdigest()[:16]


@dataclass
class WorktreeInfo:
    task_id: str
    worktree_path: str
    branch_name: str
    base_ref: str
    base_commit: str
    created_at: float
    pid: int
    status: str = "active"


@dataclass
class PreparedSubtask:
    subtask_wt: WorktreeInfo
    target_files: Optional[List[str]]
    commit_message: Optional[str]
    commit_sha: Optional[str]
    phase_task_id: str
    attempt: int
    tier: Optional[str]
    early: Optional[Tuple[bool, str]] = None
    integrated_sha: Optional[str] = None
    tolerated_touched: List[str] = field(default_factory=list)


class WorktreeManager:
    """Gerencia o ciclo de vida completo de worktrees isolados no Git."""

    def __init__(
        self,
        repo_root: Optional[str] = None,
        worktrees_dir: Optional[str] = None,
    ):
        self.repo_root = os.path.abspath(repo_root or self._find_repo_root())
        repo_hash = compute_repo_hash(self.repo_root)

        if worktrees_dir:
            self.worktrees_dir = os.path.abspath(worktrees_dir)
        else:
            base_dir = os.environ.get("MEISTER_WORKTREES_DIR") or os.path.expanduser("~/.meister/worktrees")
            self.worktrees_dir = os.path.abspath(os.path.join(base_dir, repo_hash))

        self.metadata_dir = os.path.join(self.worktrees_dir, ".metadata")
        os.makedirs(self.worktrees_dir, exist_ok=True)
        os.makedirs(self.metadata_dir, exist_ok=True)
        self._worktree_lock = threading.RLock()
        meister_dir = os.path.join(self.repo_root, ".meister")
        os.makedirs(meister_dir, exist_ok=True)
        meister_gitignore = os.path.join(meister_dir, ".gitignore")
        if not os.path.exists(meister_gitignore):
            try:
                with open(meister_gitignore, "w", encoding="utf-8") as f:
                    f.write("*\n")
            except Exception:
                pass

    def _prepare_node_worktree(self, info: WorktreeInfo) -> None:
        config = load_config(cwd=self.repo_root)
        if (
            not config.gate.install
            and not os.path.isfile(os.path.join(info.worktree_path, "package.json"))
        ):
            return
        if not config.environment.install_dependencies:
            return
        started = time.monotonic()
        ok, message = prepare_environment(info.worktree_path, config)
        duration_ms = (time.monotonic() - started) * 1000
        from meister.logger import log_event

        if ok:
            log_event(
                event_type="worktree_setup_ok",
                task_id=info.task_id,
                duration_ms=duration_ms,
                message=message,
            )
        else:
            logger.warning(t("engine.worktree.setup_failed", task_id=info.task_id, message=message))
            log_event(
                event_type="worktree_setup_failed",
                task_id=info.task_id,
                duration_ms=duration_ms,
                error=message,
            )

    def _find_repo_root(self) -> str:
        """Localiza a raiz do repositório Git atual."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except Exception:
            return os.getcwd()

    def _run_git(
        self,
        args: List[str],
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> str:
        """Executa comando git capturando stdout com tratamento defensivo."""
        target_cwd = cwd or self.repo_root
        res = subprocess.run(
            ["git"] + args,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            env=env,
        )
        if res.returncode != 0:
            raise RuntimeError(
                t(
                    "engine.worktree.git_command_failed",
                    return_code=res.returncode,
                    command=" ".join(args),
                    stderr=res.stderr.strip(),
                )
            )
        return res.stdout.strip()

    def create_worktree(
        self,
        task_id: str,
        base_ref: str = "HEAD",
        branch_name: Optional[str] = None,
    ) -> WorktreeInfo:
        """Cria um novo worktree isolado com branch dedicado para a tarefa."""
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        target_branch = branch_name or f"meister/worktree/{safe_id}"
        worktree_path = os.path.join(self.worktrees_dir, safe_id)

        _transient_patterns = ("commondir", "index.lock", ".lock")

        with self._worktree_lock:
            # Se já existir worktree ou branch anterior com esse nome, limpa defensivamente
            if os.path.exists(worktree_path):
                self.cleanup_worktree(safe_id, force=True)
            elif not self._delete_branch_archiving(target_branch, task_id, base_ref=base_ref):
                raise RuntimeError(t("engine.worktree.branch_archive_blocked", branch=target_branch))

            # Obtém o hash exato do commit base
            base_commit = self._run_git(["rev-parse", base_ref])

            # Cria o worktree com o branch novo apontando para base_ref
            # Retry limitado para falhas transitórias do git (corrida entre worktrees paralelos)
            _retry_waits = (0.2, 0.5, 1.0)
            _last_exc: Optional[Exception] = None
            for _attempt, _wait in enumerate((*_retry_waits, None), start=1):
                try:
                    self._run_git([
                        "worktree", "add", "-b", target_branch, worktree_path, base_ref
                    ])
                    _last_exc = None
                    break
                except Exception as exc:
                    err_str = str(exc)
                    is_transient = any(pat in err_str for pat in _transient_patterns)
                    if not is_transient or _wait is None:
                        raise
                    logger.warning(
                        t(
                            "engine.worktree.create_retry",
                            task_id=task_id,
                            attempt=_attempt,
                            error=err_str,
                            wait=f"{_wait:.1f}",
                        ),
                    )
                    _last_exc = exc
                    try:
                        self._run_git(["worktree", "prune"])
                    except Exception:
                        pass
                    if not self._delete_branch_archiving(target_branch, task_id, base_ref=base_ref):
                        raise exc
                    try:
                        if os.path.exists(worktree_path):
                            shutil.rmtree(worktree_path, ignore_errors=True)
                    except Exception:
                        pass
                    time.sleep(_wait)
            if _last_exc is not None:
                raise _last_exc

            info = WorktreeInfo(
                task_id=task_id,
                worktree_path=worktree_path,
                branch_name=target_branch,
                base_ref=base_ref,
                base_commit=base_commit,
                created_at=time.time(),
                pid=os.getpid(),
                status="active",
            )

            # Persiste metadados
            meta_file = os.path.join(self.metadata_dir, f"{safe_id}.json")
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(asdict(info), f, indent=2)

        logger.info(t("engine.worktree.created", task_id=task_id, path=worktree_path, branch=target_branch))
        self._prepare_node_worktree(info)
        return info

    def commit_worktree(
        self,
        worktree_path: str,
        message: str,
    ) -> Optional[str]:
        """Adiciona todos os arquivos e realiza commit no worktree.

        Retorna o hash do commit ou None se não houver modificações.
        """
        if not os.path.exists(worktree_path):
            return None

        # Adiciona modificações e untracked
        self._run_git(["add", "-A"], cwd=worktree_path)

        # Verifica se há algo staged para commitar
        res = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=worktree_path,
        )
        if res.returncode == 0:
            # Nenhuma modificação staged
            return None

        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "MeisterRouter")
        env.setdefault("GIT_AUTHOR_EMAIL", "bot@meisterrouter.dev")
        env.setdefault("GIT_COMMITTER_NAME", "MeisterRouter")
        env.setdefault("GIT_COMMITTER_EMAIL", "bot@meisterrouter.dev")

        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )

        return self._run_git(["rev-parse", "HEAD"], cwd=worktree_path)

    def merge_branch_into(
        self,
        source_branch: str,
        target_worktree_path: str,
        message: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Realiza merge sequencial da branch source_branch no target_worktree_path.

        Retorna (sucesso: bool, rollback_sha_ou_erro: str).
        Se sucesso, rollback_sha_ou_erro armazena o commit HEAD anterior ao merge.
        Se houver conflito ou erro, aborta o merge automaticamente e retorna False.
        """
        if not os.path.exists(target_worktree_path):
            return False, CodedMessage(t("engine.worktree.target_missing", path=target_worktree_path), code="merge")

        rollback_sha = self._run_git(["rev-parse", "HEAD"], cwd=target_worktree_path)
        commit_msg = message or f"Merge branch '{source_branch}'"

        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "MeisterRouter")
        env.setdefault("GIT_AUTHOR_EMAIL", "bot@meisterrouter.dev")
        env.setdefault("GIT_COMMITTER_NAME", "MeisterRouter")
        env.setdefault("GIT_COMMITTER_EMAIL", "bot@meisterrouter.dev")

        res = subprocess.run(
            ["git", "merge", "--no-ff", source_branch, "-m", commit_msg],
            cwd=target_worktree_path,
            capture_output=True,
            text=True,
            env=env,
        )

        if res.returncode != 0:
            try:
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=target_worktree_path,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                pass
            return False, CodedMessage(
                t("engine.worktree.merge_failed", detail=res.stderr.strip() or res.stdout.strip()),
                code="merge",
            )

        return True, rollback_sha

    def rollback_merge(
        self,
        target_worktree_path: str,
        target_sha: str,
        task_id: Optional[str] = None,
    ) -> bool:
        """Executa rollback atômico do merge via git reset --hard e git clean.

        Retorna False, sem resetar, se as alterações soltas não puderem ser arquivadas.
        """
        archive_ok, _ = self._archive_uncommitted(
            target_worktree_path,
            task_id or os.path.basename(target_worktree_path),
        )
        if not archive_ok:
            logger.error(t("engine.worktree.rollback_archive_blocked", path=target_worktree_path))
            return False
        # Archived above: loose changes are under refs/meister/archive/* before the reset/clean.
        self._run_git(["reset", "--hard", target_sha], cwd=target_worktree_path)
        self._run_git(["clean", "-fd"], cwd=target_worktree_path)
        return True

    def fast_forward_repo(
        self,
        source_branch: str,
        target_branch: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Aplica fast-forward da branch de integração no repositório principal."""
        try:
            status_res = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
            )
            if status_res.stdout.strip():
                return False, t("engine.worktree.repo_dirty")

            if target_branch:
                curr_branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo_root)
                if curr_branch != target_branch:
                    self._run_git(["checkout", target_branch], cwd=self.repo_root)

            self._run_git(["merge", "--ff-only", source_branch], cwd=self.repo_root)
            return True, t("engine.worktree.fast_forward_ok")
        except Exception as e:
            return False, t("engine.worktree.fast_forward_failed", error=e)

    def get_modified_files(
        self,
        worktree_path: str,
        base_ref: Optional[str] = None,
    ) -> List[str]:
        """Obtém deterministicamente todos os arquivos modificados e adicionados no worktree.

        Usa git diff contra o base_commit + git ls-files para arquivos untracked,
        sem perder arquivos já sujos no repositório principal (Achado #21).
        """
        if not os.path.exists(worktree_path):
            return []

        ref = base_ref or "HEAD"
        modified: Set[str] = set()

        # 1. Arquivos modificados com relação à referência base
        try:
            diff_out = self._run_git(["diff", "--name-only", ref], cwd=worktree_path)
            for line in diff_out.splitlines():
                f = line.strip()
                if f:
                    modified.add(f)
        except Exception as e:
            logger.debug(t("engine.worktree.diff_failed", path=worktree_path, error=e))

        # 2. Arquivos novos não rastreados (untracked)
        try:
            untracked_out = self._run_git(
                ["ls-files", "--others", "--exclude-standard"],
                cwd=worktree_path,
            )
            for line in untracked_out.splitlines():
                f = line.strip()
                if f:
                    modified.add(f)
        except Exception as e:
            logger.debug(t("engine.worktree.ls_files_failed", path=worktree_path, error=e))

        # Ignora arquivos internos do Meister (.meister)
        clean_modified = {f for f in modified if not f.startswith(".meister/") and f != ".meister"}
        return sorted(list(clean_modified))

    def verify_scope(
        self,
        worktree_path: str,
        target_files: Optional[List[str]],
        base_ref: Optional[str] = None,
        tolerated: Optional[List[str]] = None,
    ) -> Tuple[bool, List[str]]:
        """Verifica se as modificações no worktree respeitaram target_files (Achado #2).

        Retorna: (is_valid, out_of_scope_files)
        """
        if not target_files:
            return True, []

        modified = self.get_modified_files(worktree_path, base_ref=base_ref)
        tolerated_patterns = tolerated if tolerated is not None else load_config(cwd=self.repo_root).scope.tolerated_files
        ignored = self._get_ignored_files(worktree_path, modified)

        out_of_scope = scope_violations(modified, target_files, tolerated_patterns, ignored)
        return len(out_of_scope) == 0, out_of_scope

    @staticmethod
    def _get_ignored_files(worktree_path: str, files: List[str]) -> Set[str]:
        if not files:
            return set()
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "-z", "--stdin"],
                cwd=worktree_path,
                input="\0".join(files) + "\0",
                capture_output=True,
                text=True,
            )
            return {name for name in result.stdout.split("\0") if name}
        except OSError as exc:
            logger.warning(t("engine.worktree.check_ignore_failed", error=exc))
            return set()

    def _archive_branch_if_unmerged(
        self,
        branch: str,
        task_id: str,
        base_ref: Optional[str] = None,
        base_commit: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Cria ref de arquivo refs/meister/archive/<task_id>-<timestamp> se houver commits não integrados.

        Retorna:
            (True, ref_name) se arquivado com sucesso.
            (True, None) se não havia commits não integrados para arquivar.
            (False, None) se havia commits não integrados mas a criação do ref falhou.
        """
        try:
            branch_sha = self._run_git(["rev-parse", branch]).strip()
        except Exception:
            return (True, None)

        # Se temos o base_commit exato do qual a branch nasceu e o SHA coincide,
        # a branch não possui nenhum commit próprio.
        if base_commit and branch_sha == base_commit.strip():
            logger.debug(
                t(
                    "engine.worktree.branch_same_base_commit",
                    branch=branch,
                    base_commit=base_commit[:8],
                ),
            )
            return (True, None)

        # Se base_ref foi fornecido, verifica se o SHA coincide diretamente com base_ref
        base_sha = None
        if base_ref:
            try:
                base_sha = self._run_git(["rev-parse", base_ref]).strip()
            except Exception:
                base_sha = None

        if base_sha and branch_sha == base_sha:
            logger.debug(
                t(
                    "engine.worktree.branch_same_base_ref",
                    branch=branch,
                    sha=branch_sha[:8],
                    base_ref=base_ref,
                ),
            )
            return (True, None)

        # Se a branch já é ancestral de base_ref, todas as alterações já foram integradas
        if base_ref:
            try:
                res = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", branch, base_ref],
                    cwd=self.repo_root,
                    capture_output=True,
                )
                if res.returncode == 0:
                    logger.debug(
                        t("engine.worktree.branch_already_integrated_ref", branch=branch, base_ref=base_ref)
                    )
                    return (True, None)
            except Exception:
                pass

        # Se a branch já é ancestral de HEAD (main), já está integrada no repositório principal
        try:
            res = subprocess.run(
                ["git", "merge-base", "--is-ancestor", branch, "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
            )
            if res.returncode == 0:
                logger.debug(t("engine.worktree.branch_already_integrated_head", branch=branch))
                return (True, None)
        except Exception:
            pass

        # Determina a referência base correta para contagem de commits não integrados
        base = None
        if base_ref and base_sha:
            base = base_ref
        elif base_commit:
            base = base_commit
        else:
            base = "HEAD"

        try:
            count_str = self._run_git(["rev-list", "--count", f"{base}..{branch}"]).strip()
            count = int(count_str)
        except Exception:
            try:
                count_str = self._run_git(["rev-list", "--count", f"HEAD..{branch}"]).strip()
                count = int(count_str)
            except Exception:
                count = 0

        if count > 0:
            ts = int(time.time())
            safe_task = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
            ref_name = f"refs/meister/archive/{safe_task}-{ts}"
            try:
                self._run_git(["update-ref", ref_name, branch_sha])
                logger.warning(
                    t(
                        "engine.worktree.branch_archived",
                        branch=branch,
                        count=count,
                        ref=ref_name,
                        sha=branch_sha[:8],
                    )
                )
                try:
                    from meister.logger import log_event
                    log_event(
                        event_type="worktree_archived",
                        task_id=task_id,
                        branch=branch,
                        archive_ref=ref_name,
                        sha=branch_sha,
                        unmerged_commits=count,
                    )
                except Exception:
                    pass
                return (True, ref_name)
            except Exception as e:
                logger.error(t("engine.worktree.archive_branch_failed", branch=branch, error=e))
                try:
                    from meister.logger import log_event
                    log_event(
                        event_type="worktree_archive_failed",
                        task_id=task_id,
                        branch=branch,
                        sha=branch_sha,
                        unmerged_commits=count,
                        error=str(e),
                    )
                except Exception:
                    pass
                return (False, None)
        return (True, None)

    def _delete_branch_archiving(
        self,
        branch: str,
        task_id: str,
        base_ref: Optional[str] = None,
    ) -> bool:
        """Arquiva commits não integrados e só então remove a branch. Retorna False se o arquivamento falhar."""
        archived_ok, _ = self._archive_branch_if_unmerged(branch, task_id, base_ref=base_ref)
        if not archived_ok:
            logger.error(t("engine.worktree.branch_archive_blocked", branch=branch))
            return False
        # Archived above (or the branch has no unique commits), so deleting it loses nothing.
        try:
            self._run_git(["branch", "-D", branch])
        except Exception:
            pass
        return True

    def _archive_uncommitted(
        self,
        wt_path: str,
        task_id: str,
    ) -> Tuple[bool, Optional[str]]:
        """Arquiva alterações soltas sem alterar o índice ou a branch do worktree."""
        if not os.path.exists(wt_path):
            return (True, None)

        try:
            status = self._run_git(
                ["status", "--porcelain", "--untracked-files=all"],
                cwd=wt_path,
            )
        except Exception as exc:
            logger.error(t("engine.worktree.uncommitted_check_failed", path=wt_path, error=exc))
            return (False, None)
        if not status:
            return (True, None)

        safe_task = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        branch = "HEAD"
        snapshot_sha: Optional[str] = None
        index_path: Optional[str] = None
        try:
            branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt_path)
            fd, index_path = tempfile.mkstemp(prefix="meister-archive-index-")
            os.close(fd)
            os.remove(index_path)

            env = os.environ.copy()
            env.update({
                "GIT_INDEX_FILE": index_path,
                "GIT_AUTHOR_NAME": "MeisterRouter",
                "GIT_AUTHOR_EMAIL": "meister@localhost",
                "GIT_COMMITTER_NAME": "MeisterRouter",
                "GIT_COMMITTER_EMAIL": "meister@localhost",
            })
            self._run_git(["read-tree", "HEAD"], cwd=wt_path, env=env)
            self._run_git(
                ["add", "-A", "--", ".", ":(exclude).venv"],
                cwd=wt_path,
                env=env,
            )
            tree_sha = self._run_git(["write-tree"], cwd=wt_path, env=env)
            head_tree = self._run_git(["rev-parse", "HEAD^{tree}"], cwd=wt_path)
            if tree_sha == head_tree:
                return (True, None)

            snapshot_sha = self._run_git(
                [
                    "commit-tree",
                    tree_sha,
                    "-p",
                    "HEAD",
                    "-m",
                    t("engine.worktree.uncommitted_commit", task_id=task_id),
                ],
                cwd=wt_path,
                env=env,
            )
            timestamp = int(time.time())
            while True:
                ref_name = (
                    f"refs/meister/archive/{safe_task}-{timestamp}-uncommitted"
                )
                existing_refs = self._run_git(
                    ["for-each-ref", "--format=%(refname)", ref_name]
                ).splitlines()
                if not existing_refs:
                    break
                timestamp += 1
            self._run_git(["update-ref", ref_name, snapshot_sha, ""])
            logger.warning(
                t(
                    "engine.worktree.uncommitted_archived",
                    branch=branch,
                    ref=ref_name,
                    sha=snapshot_sha[:8],
                )
            )
            try:
                from meister.logger import log_event

                log_event(
                    event_type="worktree_archived",
                    task_id=task_id,
                    branch=branch,
                    archive_ref=ref_name,
                    sha=snapshot_sha,
                    unmerged_commits=0,
                    uncommitted=True,
                )
            except Exception:
                pass
            return (True, ref_name)
        except Exception as exc:
            logger.error(t("engine.worktree.archive_uncommitted_failed", task_id=task_id, error=exc))
            try:
                from meister.logger import log_event

                log_event(
                    event_type="worktree_archive_failed",
                    task_id=task_id,
                    branch=branch,
                    sha=snapshot_sha,
                    unmerged_commits=0,
                    uncommitted=True,
                    error=str(exc),
                )
            except Exception:
                pass
            return (False, None)
        finally:
            if index_path and os.path.exists(index_path):
                os.remove(index_path)

    def cleanup_worktree(
        self,
        task_id: str,
        delete_branch: bool = True,
        force: bool = False,
        archive_unmerged: bool = True,
    ) -> bool:
        """Remove o worktree, deleta o branch e executa git worktree prune."""
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        worktree_path = os.path.join(self.worktrees_dir, safe_id)
        meta_file = os.path.join(self.metadata_dir, f"{safe_id}.json")
        branch_name = f"meister/worktree/{safe_id}"
        meta_data: dict[str, Any] = {}
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                    branch_name = meta_data.get("branch_name", branch_name)
            except Exception:
                pass

        success = True

        with self._worktree_lock:
            # Preserve loose worker changes before deleting the worktree.
            if archive_unmerged:
                archive_ok, _ = self._archive_uncommitted(worktree_path, task_id)
                if not archive_ok:
                    logger.error(
                        t("engine.worktree.archive_remove_blocked", path=worktree_path)
                    )
                    return False

            # 1. Remove worktree via Git
            cmd = ["worktree", "remove"]
            if force:
                cmd.append("--force")
            cmd.append(worktree_path)

            try:
                self._run_git(cmd)
            except Exception as e:
                logger.warning(
                    t(
                        "engine.worktree.remove_worktree_notice",
                        path=worktree_path,
                        error=e,
                    )
                )
                _log_cleanup_failure(task_id=task_id, path=worktree_path, reason=str(e))
                if os.path.exists(worktree_path):
                    try:
                        shutil.rmtree(worktree_path, ignore_errors=True)
                    except Exception:
                        success = False

            # 2. Deleta o branch do worktree se solicitado (arquivando commits não integrados se solicitado)
            if delete_branch:
                can_delete = True
                if archive_unmerged:
                    base_ref = meta_data.get("base_ref")
                    base_commit = meta_data.get("base_commit")
                    archive_ok, _ = self._archive_branch_if_unmerged(
                        branch_name, task_id, base_ref=base_ref, base_commit=base_commit
                    )
                    if not archive_ok:
                        can_delete = False
                        success = False
                        logger.error(
                            t("engine.worktree.archive_delete_blocked", branch=branch_name)
                        )
                if can_delete:
                    # Archived above (or no unique commits), so the branch deletion loses nothing.
                    try:
                        self._run_git(["branch", "-D", branch_name])
                    except Exception as e:
                        logger.debug(t("engine.worktree.delete_branch_notice", branch=branch_name, error=e))

            # 3. Executa git worktree prune
            try:
                self._run_git(["worktree", "prune"])
            except Exception as e:
                logger.debug(t("engine.worktree.prune_notice", error=e))

        # 4. Remove arquivo de metadados
        if os.path.exists(meta_file):
            try:
                os.remove(meta_file)
            except Exception:
                pass

        logger.info(t("engine.worktree.cleaned", task_id=task_id))
        return success

    def list_active_worktrees(self) -> List[WorktreeInfo]:
        """Lista todos os worktrees ativos registrados no MeisterRouter."""
        result: List[WorktreeInfo] = []
        if not os.path.exists(self.metadata_dir):
            return result

        for fname in os.listdir(self.metadata_dir):
            if not fname.endswith(".json"):
                continue
            meta_path = os.path.join(self.metadata_dir, fname)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    result.append(WorktreeInfo(**data))
            except Exception:
                continue
        return result

    def cleanup_orphans(self, exclude_run_id: Optional[str] = None) -> List[str]:
        """Varredura na inicialização: detecta e limpa worktrees e branches órfãos."""
        cleaned_ids: List[str] = []

        git_worktree_paths = set()
        try:
            out = self._run_git(["worktree", "list", "--porcelain"])
            for line in out.splitlines():
                if line.startswith("worktree "):
                    git_worktree_paths.add(os.path.abspath(line.split(" ", 1)[1].strip()))
        except Exception as e:
            logger.warning(t("engine.worktree.list_failed", error=e))

        safe_exclude = None
        if exclude_run_id:
            safe_exclude = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in exclude_run_id)

        if os.path.exists(self.worktrees_dir):
            for entry in os.listdir(self.worktrees_dir):
                if entry.startswith("."):
                    continue
                if safe_exclude and entry == f"int_{safe_exclude}":
                    continue
                wt_path = os.path.abspath(os.path.join(self.worktrees_dir, entry))
                meta_file = os.path.join(self.metadata_dir, f"{entry}.json")

                is_orphan = False
                branch_name = ""
                if not os.path.exists(meta_file):
                    is_orphan = True
                else:
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        branch_name = data.get("branch_name", "")
                        pid = data.get("pid")
                        if pid and not self._is_pid_alive(pid):
                            is_orphan = True
                    except Exception:
                        is_orphan = True

                if is_orphan:
                    logger.warning(t("engine.worktree.orphan_found", path=wt_path))
                    is_int_branch = branch_name.startswith("meister/integration/") or entry.startswith("int_")
                    wt_cleaned = self.cleanup_worktree(
                        entry,
                        delete_branch=not is_int_branch,
                        force=True,
                        archive_unmerged=True,
                    )
                    if wt_cleaned:
                        cleaned_ids.append(entry)

        try:
            branches_out = self._run_git(["branch", "--list", "meister/worktree/*"])
            for line in branches_out.splitlines():
                branch = line.strip().lstrip("* ").strip()
                if branch:
                    task_id = branch.replace("meister/worktree/", "")
                    wt_path = os.path.abspath(os.path.join(self.worktrees_dir, task_id))
                    if wt_path not in git_worktree_paths:
                        meta_file = os.path.join(self.metadata_dir, f"{task_id}.json")
                        base_ref = None
                        base_commit = None
                        if os.path.exists(meta_file):
                            try:
                                with open(meta_file, "r", encoding="utf-8") as f:
                                    m_data = json.load(f)
                                base_ref = m_data.get("base_ref")
                                base_commit = m_data.get("base_commit")
                            except Exception:
                                pass
                        archive_ok, _ = self._archive_branch_if_unmerged(
                            branch, task_id, base_ref=base_ref, base_commit=base_commit
                        )
                        if archive_ok:
                            # Archived above (or no unique commits), so the branch deletion loses nothing.
                            try:
                                self._run_git(["branch", "-D", branch])
                                if task_id not in cleaned_ids:
                                    cleaned_ids.append(task_id)
                            except Exception:
                                pass
                        else:
                            logger.error(
                                t("engine.worktree.archive_orphan_blocked", branch=branch)
                            )
        except Exception:
            pass

        try:
            self._run_git(["worktree", "prune"])
        except Exception:
            pass

        # Política de retenção simples: limpa refs de arquivo expiradas
        try:
            self.prune_archive_refs()
        except Exception as e:
            logger.debug(t("engine.worktree.archive_prune_notice", error=e))

        return cleaned_ids

    def prune_archive_refs(self, max_age_days: int = 7) -> List[str]:
        """Remove refs de arquivo em refs/meister/archive/* mais antigas que max_age_days.

        Retorna a lista de referências removidas.
        """
        pruned: List[str] = []
        if max_age_days < 0:
            return pruned

        now = time.time()
        max_age_seconds = max_age_days * 86400

        try:
            refs_out = self._run_git(["for-each-ref", "--format=%(refname)", "refs/meister/archive"]).strip()
            if not refs_out:
                return pruned

            for ref in refs_out.splitlines():
                ref = ref.strip()
                if not ref:
                    continue

                ref_ts: Optional[float] = None
                suffix = ref.rsplit("-", 1)[-1]
                if suffix.isdigit():
                    try:
                        ref_ts = float(suffix)
                    except ValueError:
                        ref_ts = None

                if ref_ts is None:
                    try:
                        date_str = self._run_git(["log", "-1", "--format=%ct", ref]).strip()
                        if date_str.isdigit():
                            ref_ts = float(date_str)
                    except Exception:
                        ref_ts = None

                if ref_ts is not None and (now - ref_ts) > max_age_seconds:
                    try:
                        self._run_git(["update-ref", "-d", ref])
                        pruned.append(ref)
                        logger.info(t("engine.worktree.archive_ref_expired", ref=ref))
                    except Exception as e:
                        logger.warning(t("engine.worktree.archive_ref_remove_failed", ref=ref, error=e))
        except Exception as e:
            logger.debug(t("engine.worktree.archive_prune_failed", error=e))

        return pruned

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        return osops.pid_alive(pid)


class IntegrationPipeline:
    """Gerencia o pipeline completo de integração determinística:
    - Cria branch e worktree de integração para o run (`meister/integration/{run_id}`).
    - Valida escopo e executa portão determinístico por script no worktree do worker antes de commitar.
    - Executa merge sequencial topológico na branch de integração.
    - Valida portão determinístico na branch de integração após o merge.
    - Executa rollback atômico com git reset --hard em caso de falha de teste ou conflito.
    - Aplica fast-forward no repositório base se todas as subtarefas forem integradas.
    """

    def __init__(
        self,
        worktree_manager: WorktreeManager,
        gate: Optional[Any] = None,
        state_manager: Optional[Any] = None,
    ):
        self.wt_mgr = worktree_manager
        if gate is None:
            from meister.gate import DeterministicGate
            gate = DeterministicGate(self.wt_mgr.repo_root)
        self.gate = gate
        self.config = load_config(cwd=self.wt_mgr.repo_root)
        self.state_manager = state_manager
        self.integration_info: Optional[WorktreeInfo] = None
        self.base_ref: str = "HEAD"
        self.run_id: Optional[str] = None
        self.last_integrated_sha: Optional[str] = None

    def start_integration(
        self,
        run_id: str,
        base_ref: str = "HEAD",
        state_manager: Optional[Any] = None,
        strict_replay: bool = False,
    ) -> WorktreeInfo:
        """Inicializa ou recupera de forma idempotente o worktree e branch de integração para o run."""
        self.run_id = run_id
        self.base_ref = base_ref
        if state_manager is not None:
            self.state_manager = state_manager

        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in run_id)
        branch_name = f"meister/integration/{safe_id}"
        wt_task_id = f"int_{safe_id}"
        worktree_path = os.path.join(self.wt_mgr.worktrees_dir, wt_task_id)

        # 1. Verifica se a branch de integração já existe
        branch_exists = False
        try:
            self.wt_mgr._run_git(["rev-parse", "--verify", f"refs/heads/{branch_name}"])
            branch_exists = True
        except Exception:
            branch_exists = False

        has_history = False
        if branch_exists:
            try:
                count_str = self.wt_mgr._run_git(["rev-list", "--count", f"{base_ref}..{branch_name}"]).strip()
                has_history = int(count_str) > 0
            except Exception:
                has_history = False

        # Se já existe e tem histórico do run: REUSAR
        if branch_exists and has_history:
            logger.info(t("engine.worktree.integration_branch_reused", branch=branch_name, run_id=run_id))
            is_valid_worktree = False
            if os.path.exists(worktree_path):
                try:
                    wt_list = self.wt_mgr._run_git(["worktree", "list", "--porcelain"])
                    existing_paths = [
                        os.path.abspath(line.split(" ", 1)[1].strip())
                        for line in wt_list.splitlines()
                        if line.startswith("worktree ")
                    ]
                    if os.path.abspath(worktree_path) in existing_paths:
                        is_valid_worktree = True
                except Exception:
                    is_valid_worktree = False

            if is_valid_worktree:
                archived_ok, _ = self.wt_mgr._archive_uncommitted(worktree_path, wt_task_id)
                if not archived_ok:
                    logger.error(t("engine.worktree.reuse_archive_blocked", path=worktree_path))
                    raise RuntimeError(t("engine.worktree.reuse_archive_blocked", path=worktree_path))
                try:
                    # Archived above: loose changes are preserved under refs/meister/archive/*.
                    self.wt_mgr._run_git(["reset", "--hard", "HEAD"], cwd=worktree_path)
                    self.wt_mgr._run_git(["clean", "-fd"], cwd=worktree_path)
                except Exception as e:
                    logger.warning(t("engine.worktree.integration_cleanup_notice", error=e))
                    _log_cleanup_failure(task_id=wt_task_id, path=worktree_path, reason=str(e))
            else:
                stray_path = None
                if os.path.exists(worktree_path):
                    stray_path = os.path.join(
                        self.wt_mgr.metadata_dir,
                        f"stray-{safe_id}-{int(time.time() * 1000)}",
                    )
                    os.rename(worktree_path, stray_path)
                try:
                    self.wt_mgr._run_git(["worktree", "prune"])
                except Exception:
                    pass
                self.wt_mgr._run_git(["worktree", "add", worktree_path, branch_name])
                if stray_path is not None:
                    shutil.copytree(
                        stray_path,
                        worktree_path,
                        ignore=shutil.ignore_patterns(".git"),
                        dirs_exist_ok=True,
                    )
                    archived_ok, _ = self.wt_mgr._archive_uncommitted(worktree_path, wt_task_id)
                    if not archived_ok:
                        logger.error(t("engine.worktree.reuse_archive_blocked", path=stray_path))
                        raise RuntimeError(t("engine.worktree.reuse_archive_blocked", path=stray_path))
                    # Archived above; the stray copy is removed only after the reset succeeds.
                    self.wt_mgr._run_git(["reset", "--hard", "HEAD"], cwd=worktree_path)
                    self.wt_mgr._run_git(["clean", "-fd"], cwd=worktree_path)
                    shutil.rmtree(stray_path, ignore_errors=True)

            base_commit = self.wt_mgr._run_git(["rev-parse", base_ref])
            info = WorktreeInfo(
                task_id=wt_task_id,
                worktree_path=worktree_path,
                branch_name=branch_name,
                base_ref=base_ref,
                base_commit=base_commit,
                created_at=time.time(),
                pid=os.getpid(),
                status="active",
            )
            meta_file = os.path.join(self.wt_mgr.metadata_dir, f"{wt_task_id}.json")
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(asdict(info), f, indent=2)

            self.integration_info = info
            logger.info(t("engine.worktree.integration_reused", branch=branch_name, path=info.worktree_path))
            self.wt_mgr._prepare_node_worktree(info)
            return info

        # Se a branch não existe ou não tem histórico, mas o SQLite possui subtarefas COMPLETED com SHAs:
        # Reconstruir reaplicando os commits/SHAs
        completed_entries: List[Tuple[str, str]] = []
        if self.state_manager is not None:
            try:
                subtasks = self.state_manager.get_subtasks(run_id)
                for st in subtasks:
                    if st.get("status") == "COMPLETED" and st.get("integrated_sha"):
                        label = st.get("step_id") or st.get("subtask_id", "")
                        completed_entries.append((label, st["integrated_sha"]))
            except Exception as e:
                logger.debug(t("engine.worktree.subtasks_load_failed", error=e))

        info = self.wt_mgr.create_worktree(
            task_id=wt_task_id,
            base_ref=base_ref,
            branch_name=branch_name,
        )
        self.integration_info = info

        if completed_entries:
            logger.info(
                t(
                    "engine.worktree.integration_rebuilding",
                    branch=branch_name,
                    count=len(completed_entries),
                )
            )
            env = os.environ.copy()
            env.setdefault("GIT_AUTHOR_NAME", "MeisterRouter")
            env.setdefault("GIT_AUTHOR_EMAIL", "bot@meisterrouter.dev")
            env.setdefault("GIT_COMMITTER_NAME", "MeisterRouter")
            env.setdefault("GIT_COMMITTER_EMAIL", "bot@meisterrouter.dev")

            for label, sha in completed_entries:
                is_anc = False
                try:
                    self.wt_mgr._run_git(["merge-base", "--is-ancestor", sha, "HEAD"], cwd=info.worktree_path)
                    is_anc = True
                except Exception:
                    is_anc = False
                if not is_anc:
                    msg = f"Merge subtask {label} ({sha[:8]})" if label else f"Merge subtask ({sha[:8]})"
                    try:
                        res = subprocess.run(
                            ["git", "merge", "--no-ff", "--no-edit", "-m", msg, sha],
                            cwd=info.worktree_path,
                            capture_output=True,
                            text=True,
                            env=env,
                        )
                        if res.returncode != 0:
                            err_msg = res.stderr.strip() or res.stdout.strip()
                            try:
                                subprocess.run(
                                    ["git", "merge", "--abort"],
                                    cwd=info.worktree_path,
                                    capture_output=True,
                                    text=True,
                                )
                            except Exception:
                                pass
                            logger.warning(
                                t("engine.worktree.replay_commit_failed", sha=sha, error=err_msg)
                            )
                            if strict_replay:
                                raise ReplayError(
                                    t("engine.worktree.replay_strict_failed", label=label, error=err_msg)
                                )
                    except Exception as e:
                        try:
                            subprocess.run(
                                ["git", "merge", "--abort"],
                                cwd=info.worktree_path,
                                capture_output=True,
                                text=True,
                            )
                        except Exception:
                            pass
                        logger.warning(t("engine.worktree.replay_failed", sha=sha, error=e))
                        if strict_replay:
                            if isinstance(e, ReplayError):
                                raise
                            raise ReplayError(
                                t("engine.worktree.replay_strict_failed", label=label, error=e)
                            ) from e

        logger.info(t("engine.worktree.integration_started", branch=branch_name, path=info.worktree_path))
        return info

    def integrate_subtask(
        self,
        subtask_wt: WorktreeInfo,
        target_files: Optional[List[str]] = None,
        commit_message: Optional[str] = None,
        task_id: Optional[str] = None,
        attempt: int = 1,
        tier: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Prepara e integra uma subtarefa sequencialmente para callers legados."""
        return self.merge_prepared(
            self.prepare_subtask(
                subtask_wt=subtask_wt,
                target_files=target_files,
                commit_message=commit_message,
                task_id=task_id,
                attempt=attempt,
                tier=tier,
            )
        )

    def prepare_subtask(
        self,
        subtask_wt: WorktreeInfo,
        target_files: Optional[List[str]] = None,
        commit_message: Optional[str] = None,
        task_id: Optional[str] = None,
        attempt: int = 1,
        tier: Optional[str] = None,
    ) -> PreparedSubtask:
        """Valida escopo, executa o gate do worker e commita no worktree da tarefa."""
        phase_task_id = task_id or subtask_wt.task_id
        prepared = PreparedSubtask(
            subtask_wt=subtask_wt,
            target_files=target_files,
            commit_message=commit_message,
            commit_sha=None,
            phase_task_id=phase_task_id,
            attempt=attempt,
            tier=tier,
        )
        if self.integration_info is None:
            prepared.early = (
                False,
                CodedMessage(t("engine.worktree.integration_not_started"), code="integration"),
            )
            return prepared

        # 1. Validação estrita de escopo
        valid_scope, out_of_scope = self.wt_mgr.verify_scope(
            subtask_wt.worktree_path,
            target_files=target_files,
            base_ref=subtask_wt.base_commit,
            tolerated=self.config.scope.tolerated_files,
        )
        if not valid_scope:
            prepared.early = (False, CodedMessage((
                t("engine.worktree.scope_violation", files=out_of_scope)
            ), code="scope"))
            return prepared
        crash_point("before_worker_gate", task_id=subtask_wt.task_id)

        # 2. Gate determinístico por script no worktree do worker
        changed_files = self.wt_mgr.get_modified_files(
            subtask_wt.worktree_path,
            base_ref=subtask_wt.base_commit,
        )
        ignored_files = self.wt_mgr._get_ignored_files(subtask_wt.worktree_path, changed_files)
        prepared.tolerated_touched = tolerated_touched(
            changed_files,
            target_files,
            self.config.scope.tolerated_files,
            ignored_files,
        )
        worker_result = self._run_gate(
            subtask_wt.worktree_path,
            task_id=phase_task_id,
            attempt=attempt,
            tier=tier,
            changed_files=changed_files,
            allow_docs_only=True,
        )
        if worker_result.infrastructure_error:
            preserve_message = commit_message or f"subtask({subtask_wt.task_id}): automated changes"
            preserved_sha = self.wt_mgr.commit_worktree(subtask_wt.worktree_path, preserve_message)
            if preserved_sha:
                prepared.integrated_sha = preserved_sha
            self._log_gate_infrastructure_error(subtask_wt.task_id, worker_result.output)
            prepared.early = (False, self._gate_infrastructure_message(worker_result.output))
            return prepared
        if not worker_result.passed:
            out = worker_result.output
            prepared.early = (
                False,
                CodedMessage(t("engine.worktree.worker_gate_failed", output=out), code="gate"),
            )
            return prepared

        # 3. Commit das alterações no worktree do worker
        msg = commit_message or f"subtask({subtask_wt.task_id}): automated changes"
        commit_sha = self.wt_mgr.commit_worktree(subtask_wt.worktree_path, msg)
        if not commit_sha and getattr(subtask_wt, "base_commit", None):
            try:
                count_str = self.wt_mgr._run_git(
                    ["rev-list", "--count", f"{subtask_wt.base_commit}..HEAD"],
                    cwd=subtask_wt.worktree_path,
                )
                if int(count_str.strip()) > 0:
                    commit_sha = self.wt_mgr._run_git(["rev-parse", "HEAD"], cwd=subtask_wt.worktree_path)
            except Exception as e:
                logger.debug(
                    t(
                        "engine.worktree.commit_lookup_failed",
                        path=subtask_wt.worktree_path,
                        error=e,
                    )
                )

        if not commit_sha:
            if target_files:
                base_run = getattr(self.integration_info, "base_commit", None)
                if not base_run:
                    try:
                        base_run = self.wt_mgr._run_git(
                            ["merge-base", self.base_ref, self.integration_info.branch_name],
                            cwd=self.integration_info.worktree_path,
                        ).strip()
                    except Exception:
                        base_run = None

                existing_sha = None
                matched_id = subtask_wt.task_id
                if base_run:
                    try:
                        log_out = self.wt_mgr._run_git(
                            ["log", "--format=%H%x00%s", f"{base_run}..{self.integration_info.branch_name}"],
                            cwd=self.integration_info.worktree_path,
                        )
                        candidate_ids = [subtask_wt.task_id]
                        if commit_message and commit_message.startswith("subtask("):
                            idx = commit_message.find("):")
                            if idx != -1:
                                extracted = commit_message[len("subtask("):idx].strip()
                                if extracted and extracted not in candidate_ids:
                                    candidate_ids.append(extracted)

                        subtask_sha = None
                        merge_sha = None
                        for line in log_out.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            parts = line.split("\x00", 1)
                            if len(parts) != 2:
                                continue
                            sha, subject = parts
                            for tid in candidate_ids:
                                if subject.startswith(f"subtask({tid}):"):
                                    if not subtask_sha:
                                        subtask_sha = sha
                                        matched_id = tid
                                elif subject.startswith(f"Merge subtask {tid} ("):
                                    if not merge_sha:
                                        merge_sha = sha
                                        if not subtask_sha:
                                            matched_id = tid
                        existing_sha = subtask_sha or merge_sha
                    except Exception as e:
                        logger.debug(t("engine.worktree.prior_commit_lookup_failed", error=e))

                if existing_sha:
                    prepared.integrated_sha = existing_sha
                    prepared.early = (
                        True,
                        t(
                            "engine.worktree.subtask_already_integrated",
                            task_id=matched_id,
                            sha=existing_sha[:8],
                        ),
                    )
                    return prepared

                prepared.early = (
                    False,
                    CodedMessage(
                        t("engine.worktree.subtask_no_changes", target_files=target_files),
                        code="no_changes",
                    ),
                )
                return prepared
            prepared.early = (True, t("engine.worktree.no_changes_to_integrate"))
            return prepared

        prepared.commit_sha = commit_sha
        prepared.integrated_sha = commit_sha
        crash_point("after_worker_commit", task_id=subtask_wt.task_id)
        return prepared

    def merge_prepared(self, prepared: PreparedSubtask) -> Tuple[bool, str]:
        """Executa em série o merge, gate de integração e eventual rollback."""
        self.last_integrated_sha = prepared.integrated_sha
        if prepared.early is not None:
            return prepared.early
        if prepared.commit_sha is None:
            raise ValueError(t("engine.worktree.prepared_missing_result"))
        if self.integration_info is None:
            return False, CodedMessage(t("engine.worktree.integration_not_started"), code="integration")

        subtask_wt = prepared.subtask_wt
        commit_sha = prepared.commit_sha
        # 4. Merge sequencial na branch de integração
        merged, rollback_sha_or_err = self.wt_mgr.merge_branch_into(
            source_branch=subtask_wt.branch_name,
            target_worktree_path=self.integration_info.worktree_path,
            message=f"Merge subtask {subtask_wt.task_id} ({commit_sha[:8]})",
        )
        if not merged:
            return False, CodedMessage(
                t("engine.worktree.integration_merge_failed", error=rollback_sha_or_err),
                code="merge",
            )

        rollback_sha = rollback_sha_or_err
        crash_point("after_merge_before_gate", task_id=subtask_wt.task_id)

        # 5. Gate determinístico no worktree de integração após o merge
        changed_files = self.wt_mgr._run_git(
            ["diff", "--name-only", rollback_sha, "HEAD"],
            cwd=self.integration_info.worktree_path,
        ).splitlines()
        integration_result = self._run_gate(
            self.integration_info.worktree_path,
            task_id=prepared.phase_task_id,
            attempt=prepared.attempt,
            tier=prepared.tier,
            changed_files=changed_files,
            allow_docs_only=True,
        )
        if integration_result.infrastructure_error:
            self._log_gate_infrastructure_error(subtask_wt.task_id, integration_result.output)
            return False, self._gate_infrastructure_message(integration_result.output)
        if not integration_result.passed:
            int_out = integration_result.output
            logger.warning(
                t(
                    "engine.worktree.integration_gate_rollback",
                    task_id=subtask_wt.task_id,
                    sha=rollback_sha,
                )
            )
            # 6. Rollback atômico
            rolled_back = self.wt_mgr.rollback_merge(
                self.integration_info.worktree_path,
                rollback_sha,
                task_id=self.integration_info.task_id,
            )
            if not rolled_back:
                blocked_msg = t("engine.worktree.rollback_blocked", task_id=subtask_wt.task_id)
                logger.error(blocked_msg)
                return False, CodedMessage(blocked_msg, code="integration")
            return False, CodedMessage(
                t("engine.worktree.integration_gate_failed", output=int_out),
                code="gate",
            )

        return True, t(
            "engine.worktree.subtask_integrated",
            task_id=subtask_wt.task_id,
            sha=commit_sha[:8],
        )

    def validate_final_integration(self) -> Tuple[bool, str]:
        """Valida o portão de qualidade determinístico na branch de integração antes de qualquer alteração na main."""
        if self.integration_info is None:
            return False, CodedMessage(t("engine.worktree.integration_none_active"), code="integration")
        result = self._run_gate(self.integration_info.worktree_path)
        if result.infrastructure_error:
            self._log_gate_infrastructure_error("final-integration", result.output)
            return False, self._gate_infrastructure_message(result.output)
        if not result.passed:
            return False, CodedMessage(result.output, code="gate")
        return result.passed, result.output

    @staticmethod
    def _gate_infrastructure_message(detail: str) -> CodedMessage:
        return CodedMessage(
            t(
                "engine.worktree.infrastructure_message",
                prefix=t("engine.worktree.infrastructure_prefix"),
                detail=detail,
            ),
            code="gate_infrastructure",
        )

    def _log_gate_infrastructure_error(self, task_id: str, detail: str) -> None:
        from meister.logger import log_event

        log_event(event_type="gate_infrastructure_error", run_id=self.run_id, task_id=task_id, error=detail)

    def _run_gate(
        self,
        repo_path: str,
        task_id: Optional[str] = None,
        attempt: int = 1,
        tier: Optional[str] = None,
        changed_files: Optional[List[str]] = None,
        allow_docs_only: bool = False,
    ):
        import inspect

        from meister.gate import VerificationResult
        from meister.gate import is_docs_only_change

        started = time.monotonic()
        result = None
        gate_mode = "full"
        try:
            run_ex = getattr(self.gate, "run_verification_ex", None)
            if callable(run_ex):
                docs_only = (
                    allow_docs_only
                    and self.config.gate.docs_only.enabled
                    and changed_files is not None
                    and is_docs_only_change(changed_files, self.config.gate.docs_only.paths)
                )
                if docs_only:
                    try:
                        parameters = inspect.signature(run_ex).parameters.values()
                        supports_docs_only = any(
                            parameter.name == "docs_only"
                            or parameter.kind == inspect.Parameter.VAR_KEYWORD
                            for parameter in parameters
                        )
                    except (TypeError, ValueError):
                        supports_docs_only = False
                    docs_only = docs_only and supports_docs_only
                gate_mode = "docs_only" if docs_only else "full"
                result = run_ex(repo_path=repo_path, docs_only=True) if docs_only else run_ex(repo_path=repo_path)
                if isinstance(result, VerificationResult):
                    return result
            legacy = self.gate.run_verification(repo_path=repo_path)
            if isinstance(legacy, tuple) and len(legacy) == 2 and isinstance(legacy[0], bool):
                return VerificationResult(legacy[0], str(legacy[1]))
            raise TypeError("gate must return VerificationResult or a (bool, str) tuple")
        finally:
            from meister.logger import log_event

            # When callers lack a logical id, use the worktree id (or final-integration id).
            log_event(
                event_type="worker_phase",
                run_id=self.run_id,
                task_id=task_id or "final-integration",
                attempt=attempt,
                tier=tier or "integration",
                phase="gate",
                duration_ms=(time.monotonic() - started) * 1000.0,
                cached=bool(getattr(result, "cached", False)),
                saved_seconds=float(getattr(result, "saved_seconds", 0.0)),
                gate_mode=gate_mode,
            )
            flaky_tests = getattr(result, "flaky_tests", None)
            if flaky_tests:
                log_event(
                    event_type="gate_flaky",
                    run_id=self.run_id,
                    task_id=task_id or "final-integration",
                    attempt=attempt,
                    tier=tier or "integration",
                    failed_tests=list(flaky_tests),
                )

    def get_integration_diff_summary(self) -> str:
        """Obtém resumo de diff entre a branch de integração e a base para julgamento de conclusão."""
        if self.integration_info is None:
            return ""
        try:
            res = subprocess.run(
                ["git", "diff", "--stat", f"{self.base_ref}..HEAD"],
                cwd=self.integration_info.worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            return res.stdout.strip()
        except Exception:
            return ""

    def verify_completed_subtasks_ancestry(
        self,
        completed_shas: Optional[List[str]] = None,
        state_manager: Optional[Any] = None,
        run_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Invariante final determinística: verifica se o commit integrado de CADA subtarefa

        COMPLETED é ancestral da branch de integração antes de permitir fast-forward.
        """
        if self.integration_info is None:
            return False, t("engine.worktree.ancestry_none_active")

        shas_to_check: List[Tuple[str, str]] = []

        sm = state_manager or self.state_manager
        rid = run_id or self.run_id
        if sm is not None and rid is not None:
            try:
                subtasks = sm.get_subtasks(rid)
                for sub in subtasks:
                    if sub.get("status") == "COMPLETED" and sub.get("integrated_sha"):
                        label = sub.get("step_id") or sub.get("subtask_id", "")
                        shas_to_check.append((label, sub["integrated_sha"]))
            except Exception as e:
                logger.warning(t("engine.worktree.ancestry_lookup_failed", error=e))

        if completed_shas:
            for idx, sha in enumerate(completed_shas):
                shas_to_check.append((f"sha_{idx}", sha))

        for label, sha in shas_to_check:
            try:
                self.wt_mgr._run_git(
                    ["merge-base", "--is-ancestor", sha, "HEAD"],
                    cwd=self.integration_info.worktree_path,
                )
            except Exception:
                err = (
                    t(
                        "engine.worktree.ancestry_violation",
                        sha=sha[:8],
                        task_id=label,
                    )
                )
                logger.error(err)
                return False, err

        return True, t("engine.worktree.ancestry_valid")

    def apply_fast_forward(self) -> Tuple[bool, str]:
        """Aplica fast-forward no repositório principal somente após aprovação de todos os gates.

        Se o fast-forward falhar (ex.: repo dirty ou invariante violada), retorna False e mantém a branch de integração
        intacta para inspeção manual.
        """
        if self.integration_info is None:
            return False, t("engine.worktree.integration_none_active")

        # Invariante final, sempre: verificar ancestralidade de subtasks COMPLETED
        ok_anc, msg_anc = self.verify_completed_subtasks_ancestry()
        if not ok_anc:
            logger.critical(t("engine.worktree.fast_forward_blocked_log", message=msg_anc))
            return False, msg_anc

        ok, ff_msg = self.wt_mgr.fast_forward_repo(self.integration_info.branch_name)
        if not ok:
            # Mantém a branch e o worktree de integração para inspeção manual (Achado Item 1)
            logger.error(t("engine.worktree.fast_forward_failed_main", error=ff_msg))
            return False, t("engine.worktree.fast_forward_blocked", message=ff_msg)

        # Fast-forward com sucesso: limpa o worktree e remove a branch de integração
        self.wt_mgr.cleanup_worktree(self.integration_info.task_id, delete_branch=True, force=True)
        self.integration_info = None
        return True, ff_msg

    def finish_integration(self, fast_forward: bool = True) -> Tuple[bool, str]:
        """Finaliza a integração: valida portão final, aplica fast-forward se configurado e limpa worktree."""
        if self.integration_info is None:
            return False, t("engine.worktree.integration_none_active")

        # Portão final
        passed, out = self.validate_final_integration()
        if not passed:
            return False, t("engine.worktree.final_gate_failed", output=out)

        if fast_forward:
            return self.apply_fast_forward()

        # Limpa o worktree de integração, mantendo a branch se não fez ff
        self.wt_mgr.cleanup_worktree(self.integration_info.task_id, delete_branch=False, force=True)
        self.integration_info = None
        return True, t("engine.worktree.integration_validated")

    def abort_integration(self) -> None:
        """Aborta a integração e limpa o worktree e a branch de integração."""
        if self.integration_info is not None:
            self.wt_mgr.cleanup_worktree(self.integration_info.task_id, delete_branch=True, force=True)
            self.integration_info = None
