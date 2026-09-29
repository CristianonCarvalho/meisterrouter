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
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict
from typing import Any, List, Optional, Set, Tuple

from meister.faults import crash_point

logger = logging.getLogger(__name__)


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
        meister_dir = os.path.join(self.repo_root, ".meister")
        os.makedirs(meister_dir, exist_ok=True)
        meister_gitignore = os.path.join(meister_dir, ".gitignore")
        if not os.path.exists(meister_gitignore):
            try:
                with open(meister_gitignore, "w", encoding="utf-8") as f:
                    f.write("*\n")
            except Exception:
                pass

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

    def _run_git(self, args: List[str], cwd: Optional[str] = None) -> str:
        """Executa comando git capturando stdout com tratamento defensivo."""
        target_cwd = cwd or self.repo_root
        res = subprocess.run(
            ["git"] + args,
            cwd=target_cwd,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"Comando git falhou (rc={res.returncode}): git {' '.join(args)}\n"
                f"stderr: {res.stderr.strip()}"
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

        # Se já existir worktree ou branch anterior com esse nome, limpa defensivamente
        if os.path.exists(worktree_path):
            self.cleanup_worktree(safe_id, force=True)
        else:
            try:
                self._run_git(["branch", "-D", target_branch])
            except Exception:
                pass

        # Obtém o hash exato do commit base
        base_commit = self._run_git(["rev-parse", base_ref])

        # Cria o worktree com o branch novo apontando para base_ref
        self._run_git([
            "worktree", "add", "-b", target_branch, worktree_path, base_ref
        ])

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

        logger.info("Criado worktree para task %s em %s (branch: %s)", task_id, worktree_path, target_branch)
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
            return False, f"Target worktree não existe: {target_worktree_path}"

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
            return False, f"Falha/conflito no merge: {res.stderr.strip() or res.stdout.strip()}"

        return True, rollback_sha

    def rollback_merge(
        self,
        target_worktree_path: str,
        target_sha: str,
    ) -> None:
        """Executa rollback atômico do merge via git reset --hard e git clean."""
        self._run_git(["reset", "--hard", target_sha], cwd=target_worktree_path)
        self._run_git(["clean", "-fd"], cwd=target_worktree_path)

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
                return False, "Repositório principal está dirty; fast-forward pulado"

            if target_branch:
                curr_branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo_root)
                if curr_branch != target_branch:
                    self._run_git(["checkout", target_branch], cwd=self.repo_root)

            self._run_git(["merge", "--ff-only", source_branch], cwd=self.repo_root)
            return True, "Fast-forward aplicado com sucesso"
        except Exception as e:
            return False, f"Falha no fast-forward: {e}"

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
            logger.debug("Falha ao rodar git diff em %s: %s", worktree_path, e)

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
            logger.debug("Falha ao rodar git ls-files em %s: %s", worktree_path, e)

        # Ignora arquivos internos do Meister (.meister)
        clean_modified = {f for f in modified if not f.startswith(".meister/") and f != ".meister"}
        return sorted(list(clean_modified))

    def verify_scope(
        self,
        worktree_path: str,
        target_files: Optional[List[str]],
        base_ref: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Verifica se as modificações no worktree respeitaram target_files (Achado #2).

        Retorna: (is_valid, out_of_scope_files)
        """
        if not target_files:
            return True, []

        modified = self.get_modified_files(worktree_path, base_ref=base_ref)
        allowed = set(os.path.normpath(f) for f in target_files)

        out_of_scope = [
            f for f in modified
            if os.path.normpath(f) not in allowed
        ]
        return len(out_of_scope) == 0, out_of_scope

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
                "Branch %s não possui commits próprios (SHA coincide com base_commit %s). Arquivamento ignorado.",
                branch, base_commit[:8],
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
                "Branch %s não possui commits próprios (SHA %s coincide com base_ref %s). Arquivamento ignorado.",
                branch, branch_sha[:8], base_ref,
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
                    logger.debug("Branch %s já está completamente integrada em %s. Arquivamento ignorado.", branch, base_ref)
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
                logger.debug("Branch %s já está completamente integrada em HEAD. Arquivamento ignorado.", branch)
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
                    "Branch %s possui %d commits não integrados. Arquivado em %s (%s).",
                    branch, count, ref_name, branch_sha[:8],
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
                logger.error("Falha ao criar ref de arquivo para branch %s: %s", branch, e)
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

        # 1. Remove worktree via Git
        cmd = ["worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(worktree_path)

        try:
            self._run_git(cmd)
        except Exception as e:
            logger.debug("Aviso ao remover worktree via git (%s): %s", worktree_path, e)
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
                        "Bloqueando deleção da branch %s: falha ao arquivar commits não integrados.",
                        branch_name,
                    )
            if can_delete:
                try:
                    self._run_git(["branch", "-D", branch_name])
                except Exception as e:
                    logger.debug("Aviso ao deletar branch %s: %s", branch_name, e)

        # 3. Executa git worktree prune
        try:
            self._run_git(["worktree", "prune"])
        except Exception as e:
            logger.debug("Aviso ao rodar git worktree prune: %s", e)

        # 4. Remove arquivo de metadados
        if os.path.exists(meta_file):
            try:
                os.remove(meta_file)
            except Exception:
                pass

        logger.info("Limpeza de worktree concluída para task %s", task_id)
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
            logger.warning("Falha ao listar worktrees pelo git: %s", e)

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
                    logger.warning("Worktree órfão detectado: %s. Limpando...", wt_path)
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
                            try:
                                self._run_git(["branch", "-D", branch])
                                if task_id not in cleaned_ids:
                                    cleaned_ids.append(task_id)
                            except Exception:
                                pass
                        else:
                            logger.error(
                                "Bloqueando deleção da branch órfã %s: falha ao arquivar commits não integrados.",
                                branch,
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
            logger.debug("Aviso ao executar prune de refs de arquivo em cleanup_orphans: %s", e)

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
                        logger.info("Ref de arquivo expirada removida: %s", ref)
                    except Exception as e:
                        logger.warning("Falha ao remover ref de arquivo %s: %s", ref, e)
        except Exception as e:
            logger.debug("Aviso durante prune_archive_refs: %s", e)

        return pruned

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


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
            logger.info("Reutilizando branch de integração existente %s para o run %s", branch_name, run_id)
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
                try:
                    self.wt_mgr._run_git(["reset", "--hard", "HEAD"], cwd=worktree_path)
                    self.wt_mgr._run_git(["clean", "-fd"], cwd=worktree_path)
                except Exception as e:
                    logger.debug("Aviso ao limpar worktree existente: %s", e)
            else:
                if os.path.exists(worktree_path):
                    shutil.rmtree(worktree_path, ignore_errors=True)
                try:
                    self.wt_mgr._run_git(["worktree", "prune"])
                except Exception:
                    pass
                self.wt_mgr._run_git(["worktree", "add", worktree_path, branch_name])

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
            logger.info("Pipeline de integração reutilizou branch %s (worktree: %s)", branch_name, info.worktree_path)
            return info

        # Se a branch não existe ou não tem histórico, mas o SQLite possui subtarefas COMPLETED com SHAs:
        # Reconstruir reaplicando os commits/SHAs
        completed_shas: List[str] = []
        if self.state_manager is not None:
            try:
                subtasks = self.state_manager.get_subtasks(run_id)
                for st in subtasks:
                    if st.get("status") == "COMPLETED" and st.get("integrated_sha"):
                        completed_shas.append(st["integrated_sha"])
            except Exception as e:
                logger.debug("Não foi possível obter subtarefas do SQLite: %s", e)

        info = self.wt_mgr.create_worktree(
            task_id=wt_task_id,
            base_ref=base_ref,
            branch_name=branch_name,
        )
        self.integration_info = info

        if completed_shas:
            logger.info("Reconstruindo branch de integração %s reaplicando %d commits do SQLite...", branch_name, len(completed_shas))
            for sha in completed_shas:
                is_anc = False
                try:
                    self.wt_mgr._run_git(["merge-base", "--is-ancestor", sha, "HEAD"], cwd=info.worktree_path)
                    is_anc = True
                except Exception:
                    is_anc = False
                if not is_anc:
                    try:
                        self.wt_mgr._run_git(["cherry-pick", sha], cwd=info.worktree_path)
                    except Exception as e:
                        logger.warning("Falha ao reaplicar commit %s na reconstrução: %s", sha, e)

        logger.info("Pipeline de integração iniciado no branch %s (worktree: %s)", branch_name, info.worktree_path)
        return info

    def integrate_subtask(
        self,
        subtask_wt: WorktreeInfo,
        target_files: Optional[List[str]] = None,
        commit_message: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Processa a integração determinística de uma subtarefa:

        1. Valida escopo de arquivos modificados no worktree do worker (Achado #2).
        2. Executa o portão de verificação de testes e linters no worktree do worker.
        3. Commita as mudanças na branch do worker.
        4. Efetua merge na branch de integração.
        5. Executa o portão de verificação no worktree de integração.
        6. Se o portão falhar, faz rollback (git reset --hard) para o commit anterior.
        """
        if self.integration_info is None:
            return False, "Pipeline de integração não foi inicializado."

        # 1. Validação estrita de escopo
        valid_scope, out_of_scope = self.wt_mgr.verify_scope(
            subtask_wt.worktree_path,
            target_files=target_files,
            base_ref=subtask_wt.base_commit,
        )
        if not valid_scope:
            return False, f"Violação de escopo detectada no worktree: {out_of_scope}"
        crash_point("before_worker_gate", task_id=subtask_wt.task_id)

        # 2. Gate determinístico por script no worktree do worker
        passed, out = self.gate.run_verification(repo_path=subtask_wt.worktree_path)
        if not passed:
            return False, f"Portão determinístico falhou no worktree do worker:\n{out}"

        # 3. Commit das alterações no worktree do worker
        msg = commit_message or f"subtask({subtask_wt.task_id}): automated changes"
        commit_sha = self.wt_mgr.commit_worktree(subtask_wt.worktree_path, msg)
        if not commit_sha:
            return True, "Nenhuma alteração para integrar."

        self.last_integrated_sha = commit_sha
        crash_point("after_worker_commit", task_id=subtask_wt.task_id)

        # 4. Merge sequencial na branch de integração
        merged, rollback_sha_or_err = self.wt_mgr.merge_branch_into(
            source_branch=subtask_wt.branch_name,
            target_worktree_path=self.integration_info.worktree_path,
            message=f"Merge subtask {subtask_wt.task_id} ({commit_sha[:8]})",
        )
        if not merged:
            return False, f"Falha no merge com a branch de integração: {rollback_sha_or_err}"

        rollback_sha = rollback_sha_or_err
        crash_point("after_merge_before_gate", task_id=subtask_wt.task_id)

        # 5. Gate determinístico no worktree de integração após o merge
        int_passed, int_out = self.gate.run_verification(repo_path=self.integration_info.worktree_path)
        if not int_passed:
            logger.warning("Portão falhou na integração após merge de %s. Executando rollback para %s...", subtask_wt.task_id, rollback_sha)
            # 6. Rollback atômico
            self.wt_mgr.rollback_merge(self.integration_info.worktree_path, rollback_sha)
            return False, f"Portão de integração falhou após merge:\n{int_out}. Rollback executado."

        return True, f"Subtarefa {subtask_wt.task_id} integrada com sucesso ({commit_sha[:8]})."

    def validate_final_integration(self) -> Tuple[bool, str]:
        """Valida o portão de qualidade determinístico na branch de integração antes de qualquer alteração na main."""
        if self.integration_info is None:
            return False, "Nenhuma integração ativa."
        passed, out = self.gate.run_verification(repo_path=self.integration_info.worktree_path)
        return passed, out

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
            return False, "Nenhuma integração ativa para validar ancestrais."

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
                logger.warning("Erro ao consultar subtarefas para invariante: %s", e)

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
                    f"Invariante de integridade violada: commit integrado {sha[:8]} "
                    f"da subtarefa '{label}' NÃO é ancestral da branch de integração."
                )
                logger.error(err)
                return False, err

        return True, "Ancestralidade de subtarefas concluídas validada com sucesso."

    def apply_fast_forward(self) -> Tuple[bool, str]:
        """Aplica fast-forward no repositório principal somente após aprovação de todos os gates.

        Se o fast-forward falhar (ex.: repo dirty ou invariante violada), retorna False e mantém a branch de integração
        intacta para inspeção manual.
        """
        if self.integration_info is None:
            return False, "Nenhuma integração ativa."

        # Invariante final, sempre: verificar ancestralidade de subtasks COMPLETED
        ok_anc, msg_anc = self.verify_completed_subtasks_ancestry()
        if not ok_anc:
            logger.critical("Bloqueando fast-forward: %s", msg_anc)
            return False, msg_anc

        ok, ff_msg = self.wt_mgr.fast_forward_repo(self.integration_info.branch_name)
        if not ok:
            # Mantém a branch e o worktree de integração para inspeção manual (Achado Item 1)
            logger.error("Falha no fast-forward do repositório principal: %s", ff_msg)
            return False, f"Falha no fast-forward: {ff_msg}"

        # Fast-forward com sucesso: limpa o worktree e remove a branch de integração
        self.wt_mgr.cleanup_worktree(self.integration_info.task_id, delete_branch=True, force=True)
        self.integration_info = None
        return True, ff_msg

    def finish_integration(self, fast_forward: bool = True) -> Tuple[bool, str]:
        """Finaliza a integração: valida portão final, aplica fast-forward se configurado e limpa worktree."""
        if self.integration_info is None:
            return False, "Nenhuma integração ativa."

        # Portão final
        passed, out = self.validate_final_integration()
        if not passed:
            return False, f"Portão final de integração falhou:\n{out}"

        if fast_forward:
            return self.apply_fast_forward()

        # Limpa o worktree de integração, mantendo a branch se não fez ff
        self.wt_mgr.cleanup_worktree(self.integration_info.task_id, delete_branch=False, force=True)
        self.integration_info = None
        return True, "Integração validada com sucesso (fast-forward desabilitado)."

    def abort_integration(self) -> None:
        """Aborta a integração e limpa o worktree e a branch de integração."""
        if self.integration_info is not None:
            self.wt_mgr.cleanup_worktree(self.integration_info.task_id, delete_branch=True, force=True)
            self.integration_info = None

