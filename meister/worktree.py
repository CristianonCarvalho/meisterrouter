"""
meister.worktree — Gerenciador determinístico de Git Worktrees para isolamento de workers.

Responsável por:
- Criação de worktrees dedicados por tarefa/subtarefa.
- Inspeção determinística de diff e arquivos modificados (git diff base..HEAD + untracked).
- Validação de escopo estrito (impedir que workers alterem arquivos fora de target_files).
- Limpeza pós-merge e pós-falha (git worktree remove, remoção de branch, git worktree prune).
- Varredura e descarte automático de worktrees órfãos na inicialização.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict
from typing import Any, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


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

    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = os.path.abspath(repo_root or self._find_repo_root())
        self.worktrees_dir = os.path.join(self.repo_root, ".meister", "worktrees")
        self.metadata_dir = os.path.join(self.repo_root, ".meister", "worktree_meta")
        os.makedirs(self.worktrees_dir, exist_ok=True)
        os.makedirs(self.metadata_dir, exist_ok=True)
        meister_gitignore = os.path.join(self.repo_root, ".meister", ".gitignore")
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

        return sorted(list(modified))

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

    def cleanup_worktree(
        self,
        task_id: str,
        delete_branch: bool = True,
        force: bool = False,
    ) -> bool:
        """Remove o worktree, deleta o branch e executa git worktree prune."""
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        worktree_path = os.path.join(self.worktrees_dir, safe_id)
        meta_file = os.path.join(self.metadata_dir, f"{safe_id}.json")
        branch_name = f"meister/worktree/{safe_id}"
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

        # 2. Deleta o branch do worktree se solicitado
        if delete_branch:
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

    def cleanup_orphans(self) -> List[str]:
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

        if os.path.exists(self.worktrees_dir):
            for entry in os.listdir(self.worktrees_dir):
                wt_path = os.path.abspath(os.path.join(self.worktrees_dir, entry))
                meta_file = os.path.join(self.metadata_dir, f"{entry}.json")

                is_orphan = False
                if not os.path.exists(meta_file):
                    is_orphan = True
                else:
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        pid = data.get("pid")
                        if pid and not self._is_pid_alive(pid):
                            is_orphan = True
                    except Exception:
                        is_orphan = True

                if is_orphan:
                    logger.warning("Worktree órfão detectado: %s. Limpando...", wt_path)
                    self.cleanup_worktree(entry, force=True)
                    cleaned_ids.append(entry)

        try:
            branches_out = self._run_git(["branch", "--list", "meister/worktree/*"])
            for line in branches_out.splitlines():
                branch = line.strip().lstrip("* ").strip()
                if branch:
                    task_id = branch.replace("meister/worktree/", "")
                    wt_path = os.path.abspath(os.path.join(self.worktrees_dir, task_id))
                    if wt_path not in git_worktree_paths:
                        try:
                            self._run_git(["branch", "-D", branch])
                            if task_id not in cleaned_ids:
                                cleaned_ids.append(task_id)
                        except Exception:
                            pass
        except Exception:
            pass

        try:
            self._run_git(["worktree", "prune"])
        except Exception:
            pass

        return cleaned_ids

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
    ):
        self.wt_mgr = worktree_manager
        if gate is None:
            from meister.gate import DeterministicGate
            gate = DeterministicGate(self.wt_mgr.repo_root)
        self.gate = gate
        self.integration_info: Optional[WorktreeInfo] = None
        self.base_ref: str = "HEAD"
        self.run_id: Optional[str] = None

    def start_integration(self, run_id: str, base_ref: str = "HEAD") -> WorktreeInfo:
        """Inicializa o worktree e branch de integração para o run."""
        self.run_id = run_id
        self.base_ref = base_ref
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in run_id)
        branch_name = f"meister/integration/{safe_id}"
        info = self.wt_mgr.create_worktree(
            task_id=f"int_{safe_id}",
            base_ref=base_ref,
            branch_name=branch_name,
        )
        self.integration_info = info
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

        # 2. Gate determinístico por script no worktree do worker
        passed, out = self.gate.run_verification(repo_path=subtask_wt.worktree_path)
        if not passed:
            return False, f"Portão determinístico falhou no worktree do worker:\n{out}"

        # 3. Commit das alterações no worktree do worker
        msg = commit_message or f"subtask({subtask_wt.task_id}): automated changes"
        commit_sha = self.wt_mgr.commit_worktree(subtask_wt.worktree_path, msg)
        if not commit_sha:
            return True, "Nenhuma alteração para integrar."

        # 4. Merge sequencial na branch de integração
        merged, rollback_sha_or_err = self.wt_mgr.merge_branch_into(
            source_branch=subtask_wt.branch_name,
            target_worktree_path=self.integration_info.worktree_path,
            message=f"Merge subtask {subtask_wt.task_id} ({commit_sha[:8]})",
        )
        if not merged:
            return False, f"Falha no merge com a branch de integração: {rollback_sha_or_err}"

        rollback_sha = rollback_sha_or_err

        # 5. Gate determinístico no worktree de integração após o merge
        int_passed, int_out = self.gate.run_verification(repo_path=self.integration_info.worktree_path)
        if not int_passed:
            logger.warning("Portão falhou na integração após merge de %s. Executando rollback para %s...", subtask_wt.task_id, rollback_sha)
            # 6. Rollback atômico
            self.wt_mgr.rollback_merge(self.integration_info.worktree_path, rollback_sha)
            return False, f"Portão de integração falhou após merge:\n{int_out}. Rollback executado."

        return True, f"Subtarefa {subtask_wt.task_id} integrada com sucesso ({commit_sha[:8]})."

    def finish_integration(self, fast_forward: bool = True) -> Tuple[bool, str]:
        """Finaliza a integração: valida portão final, aplica fast-forward se configurado e limpa worktree."""
        if self.integration_info is None:
            return False, "Nenhuma integração ativa."

        # Portão final
        passed, out = self.gate.run_verification(repo_path=self.integration_info.worktree_path)
        if not passed:
            return False, f"Portão final de integração falhou:\n{out}"

        ff_msg = "Fast-forward desabilitado"
        if fast_forward:
            ok, ff_msg = self.wt_mgr.fast_forward_repo(self.integration_info.branch_name)
            if not ok:
                logger.warning("Aviso no fast-forward do repositório principal: %s", ff_msg)

        # Limpa o worktree de integração, mantendo a branch se não fez ff
        self.wt_mgr.cleanup_worktree(self.integration_info.task_id, delete_branch=False, force=True)
        self.integration_info = None
        return True, f"Integração concluída com sucesso. {ff_msg}"

    def abort_integration(self) -> None:
        """Aborta a integração e limpa o worktree e a branch de integração."""
        if self.integration_info is not None:
            self.wt_mgr.cleanup_worktree(self.integration_info.task_id, delete_branch=True, force=True)
            self.integration_info = None

