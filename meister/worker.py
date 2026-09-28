"""
meister.worker — Implementador autônomo baseado em harnesses locais (Codex, Antigravity, Claude).

Executa tarefas de código utilizando os harnesses instalados localmente (codex, agy, claude),
sem consumir tokens da API de decisões (OpenRouter, reservada exclusivamente ao JEV).
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
import shutil
import asyncio
import logging
import subprocess
import shlex
import signal
from typing import Optional, List, Dict, Any, Tuple
from meister.logger import log_event

logger = logging.getLogger(__name__)

# Harness identifiers
HARNESS_CODEX = "codex"
HARNESS_ANTIGRAVITY = "antigravity"
HARNESS_CLAUDE = "claude"
HARNESS_COPILOT = "copilot"

# Variáveis seguras permitidas para workers locais (Achado #29)
DEFAULT_SAFE_ENV_VARS = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SSH_AUTH_SOCK",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "CI",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    # Chaves de API autorizadas para harnesses de execução de código
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "COPILOT_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    # Configurações do MeisterRouter para os workers
    "MEISTER_LUNA_MODEL",
    "MEISTER_GEMINI_FLASH_MODEL",
    "MEISTER_HAIKU_MODEL",
    "MEISTER_SONNET_MODEL",
    "MEISTER_COPILOT_MODEL",
    "MEISTER_IN_PANE",
}

# Variáveis estritamente proibidas para workers de código (Achado #29: OpenRouter Boundary)
FORBIDDEN_WORKER_ENV_VARS = {
    "OPENROUTER_API_KEY",
}


def build_safe_worker_env(extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Constrói um ambiente seguro para workers isolados, filtrando estritamente OPENROUTER_API_KEY (Achado #29)."""
    safe_env: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key in DEFAULT_SAFE_ENV_VARS and "OPENROUTER" not in key:
            safe_env[key] = value

    if extra_env:
        for k, v in extra_env.items():
            if k not in FORBIDDEN_WORKER_ENV_VARS and "OPENROUTER" not in k:
                safe_env[k] = v

    for forbidden in FORBIDDEN_WORKER_ENV_VARS:
        safe_env.pop(forbidden, None)

    return safe_env


def write_atomic_json(filepath: str, data: Any) -> None:
    """Grava dados em formato JSON de forma atômica usando arquivo temporário + fsync + os.replace (Achado #7)."""
    resolved_path = os.path.abspath(filepath)
    target_dir = os.path.dirname(resolved_path)
    os.makedirs(target_dir, exist_ok=True)

    temp_path = f"{resolved_path}.tmp.{uuid.uuid4().hex}"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, resolved_path)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


def read_atomic_json(filepath: str) -> Optional[Dict[str, Any]]:
    """Lê um arquivo JSON de forma segura, retornando None se o arquivo estiver incompleto ou inexistente (Achado #7)."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return None
            return json.loads(content)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def kill_process_tree(pgid_or_pid: int, is_pgid: bool = True) -> None:
    """Finaliza de forma determinística um grupo de processos ou PID com SIGTERM e SIGKILL (Achado #8)."""
    try:
        if is_pgid and hasattr(os, "killpg"):
            os.killpg(pgid_or_pid, signal.SIGTERM)
        else:
            os.kill(pgid_or_pid, signal.SIGTERM)
    except (OSError, ProcessLookupError, PermissionError):
        return

    time.sleep(0.15)

    try:
        if is_pgid and hasattr(os, "killpg"):
            os.killpg(pgid_or_pid, signal.SIGKILL)
        else:
            os.kill(pgid_or_pid, signal.SIGKILL)
    except (OSError, ProcessLookupError, PermissionError):
        pass


def get_configured_model(tier: str, default: str) -> str:
    """Obtém o ID de modelo configurado externamente via variável de ambiente."""
    env_key = f"MEISTER_{tier.upper()}_MODEL"
    return os.environ.get(env_key, default)


def get_model_aliases() -> Dict[str, str]:
    """Retorna o mapeamento atualizado de aliases para identificadores de modelo."""
    return {
        "luna": get_configured_model("luna", "gpt-6-luna"),
        "gpt-6-luna": "gpt-6-luna",
        "codex": "gpt-4o",
        "gemini_flash": get_configured_model("gemini_flash", "gemini-3.8-flash-high"),
        "gemini-flash": get_configured_model("gemini_flash", "gemini-3.8-flash-high"),
        "gemini-3.8-flash": get_configured_model("gemini_flash", "gemini-3.8-flash-high"),
        "gemini_antigravity": get_configured_model("gemini_flash", "gemini-3.8-flash-high"),
        "antigravity": get_configured_model("gemini_flash", "gemini-3.8-flash-high"),
        "agy": get_configured_model("gemini_flash", "gemini-3.8-flash-high"),
        "haiku": get_configured_model("haiku", "haiku"),
        "haiku-4.5": get_configured_model("haiku", "haiku"),
        "sonnet": get_configured_model("sonnet", "sonnet"),
        "sonnet-5": get_configured_model("sonnet", "sonnet"),
        "claude": get_configured_model("sonnet", "sonnet"),
        "copilot": get_configured_model("copilot", "auto"),
        "github-copilot": get_configured_model("copilot", "auto"),
    }


def resolve_worker_model(model_name: str) -> str:
    """Resolve aliases or tier names into the canonical harness model identifier."""
    cleaned = (model_name or "luna").strip().lower()
    return get_model_aliases().get(cleaned, model_name)


def resolve_worker_harness_and_model(
    model_name: str,
    config: Optional[Any] = None,
    cwd: Optional[str] = None,
    config_path: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Resolve a model name or alias to (harness_name, resolved_model).

    Harnesses:
      - 'codex': runs OpenAI Codex CLI (native default: gpt-6-luna)
      - 'antigravity': runs Google Antigravity CLI ('agy', native: gemini-3.8-flash-high)
      - 'claude': runs Anthropic Claude Code CLI ('claude')
      - 'copilot': runs GitHub Copilot CLI ('copilot')
    """
    cleaned = (model_name or "luna").strip().lower()

    if config is None and (config_path or cwd or os.environ.get("MEISTER_CONFIG_PATH")):
        try:
            from meister.config import load_config
            config = load_config(config_path=config_path, cwd=cwd)
        except Exception:
            config = None

    if config and getattr(config, "workers", None) and getattr(config.workers, "tier_order", None):
        for tier in config.workers.tier_order:
            if getattr(tier, "name", "").lower() == cleaned:
                h = (getattr(tier, "harness", "native") or "native").lower().strip()
                t_model = getattr(tier, "model", "") or ""
                if h == "native":
                    if cleaned in ("luna", "gpt-6-luna") or "gpt" in t_model.lower() or "luna" in t_model.lower():
                        harness = HARNESS_CODEX
                    elif "gemini" in cleaned or "gemini" in t_model.lower() or "antigravity" in cleaned or "agy" in cleaned:
                        harness = HARNESS_ANTIGRAVITY
                    elif "claude" in cleaned or "haiku" in cleaned or "sonnet" in cleaned or "opus" in cleaned:
                        harness = HARNESS_CLAUDE
                    elif "copilot" in cleaned:
                        harness = HARNESS_COPILOT
                    else:
                        harness = HARNESS_CODEX
                elif h in ("agy", "antigravity"):
                    harness = HARNESS_ANTIGRAVITY
                elif h == "claude":
                    harness = HARNESS_CLAUDE
                elif h == "codex":
                    harness = HARNESS_CODEX
                elif h in ("copilot", "github-copilot"):
                    harness = HARNESS_COPILOT
                else:
                    harness = h

                tier_model = t_model if t_model else None
                return (harness, tier_model)

    if cleaned in ("luna", "gpt-6-luna"):
        return (HARNESS_CODEX, get_configured_model("luna", "gpt-6-luna"))
    if cleaned in ("codex", "openai/gpt-4o", "gpt-4o"):
        return (HARNESS_CODEX, "gpt-4o" if "4o" in cleaned else None)
    if cleaned in (
        "gemini_flash", "gemini-flash", "gemini-3.8-flash",
        "gemini_antigravity", "antigravity", "agy", "google/gemini-2.5-flash",
        "gemini-3.8-flash-high", "gemini-3.8-flash-medium",
    ):
        return (HARNESS_ANTIGRAVITY, get_configured_model("gemini_flash", "gemini-3.8-flash-high"))
    if cleaned in (
        "haiku", "haiku-4.5", "claude-3-5-haiku-20241022", "anthropic/claude-3-5-haiku-20241022"
    ):
        return (HARNESS_CLAUDE, get_configured_model("haiku", "haiku"))
    if cleaned in (
        "sonnet", "sonnet-5", "claude-sonnet-5", "anthropic/claude-sonnet-5", "claude-3-7-sonnet", "anthropic/claude-3-7-sonnet"
    ):
        return (HARNESS_CLAUDE, get_configured_model("sonnet", "sonnet"))
    if cleaned == "claude":
        return (HARNESS_CLAUDE, None)
    if cleaned in ("copilot", "github-copilot", "gh-copilot"):
        return (HARNESS_COPILOT, get_configured_model("copilot", "auto"))

    # Heuristic matching
    if "gemini" in cleaned or "antigravity" in cleaned or "agy" in cleaned:
        return (HARNESS_ANTIGRAVITY, get_configured_model("gemini_flash", "gemini-3.8-flash-high"))
    if "claude" in cleaned or "haiku" in cleaned or "sonnet" in cleaned or "opus" in cleaned:
        return (HARNESS_CLAUDE, None)
    if "copilot" in cleaned:
        return (HARNESS_COPILOT, get_configured_model("copilot", "auto"))
    if "gpt" in cleaned or "o1" in cleaned or "o3" in cleaned or "codex" in cleaned or "luna" in cleaned:
        return (HARNESS_CODEX, None)

    return (HARNESS_CODEX, None)


def smoke_test_tier(tier: str) -> Dict[str, Any]:
    """Smoke test determinístico de um tier de worker, validando resolução e CLI local."""
    harness, model = resolve_worker_harness_and_model(tier)
    cli_bin = find_cli_binary(harness)
    available = cli_bin is not None and os.path.exists(cli_bin)
    return {
        "tier": tier,
        "harness": harness,
        "resolved_model": model,
        "cli_binary": cli_bin,
        "available": available,
    }


def find_cli_binary(harness: str) -> Optional[str]:
    """Locate the executable path for the requested harness."""
    names = []
    if harness == HARNESS_CODEX:
        names = ["codex"]
    elif harness == HARNESS_ANTIGRAVITY:
        names = ["agy", "antigravity"]
    elif harness == HARNESS_CLAUDE:
        names = ["claude"]
    elif harness == HARNESS_COPILOT:
        names = ["copilot", "github-copilot-cli"]
    else:
        names = [harness]

    for name in names:
        p = shutil.which(name)
        if p and os.path.exists(p):
            return p
        user_local = os.path.expanduser(f"~/.local/bin/{name}")
        if os.path.exists(user_local):
            return user_local
        brew = f"/opt/homebrew/bin/{name}"
        if os.path.exists(brew):
            return brew
        usr_local = f"/usr/local/bin/{name}"
        if os.path.exists(usr_local):
            return usr_local

    return None


def build_harness_command(
    harness: str,
    cli_bin: str,
    model: Optional[str],
    prompt: str,
    cwd: str,
) -> List[str]:
    """Construct the command line invocation for the specified harness."""
    if harness == HARNESS_CODEX:
        cmd = [cli_bin, "exec", "--dangerously-bypass-approvals-and-sandbox"]
        if model and model not in ("default", "luna", "codex"):
            cmd.extend(["-m", model])
        cmd.extend(["-C", cwd, prompt])
        return cmd
    elif harness == HARNESS_ANTIGRAVITY:
        cmd = [cli_bin, "--dangerously-skip-permissions"]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["-p", prompt])
        return cmd
    elif harness == HARNESS_CLAUDE:
        cmd = [cli_bin, "--dangerously-skip-permissions"]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["-p", prompt])
        return cmd
    elif harness == HARNESS_COPILOT:
        # Adaptador GitHub Copilot CLI (verificado contra GitHub Copilot CLI 1.0.88).
        # Flags: -p (modo não-interativo), --allow-all (permissões completas), --no-ask-user (autônomo).
        cmd = [cli_bin, "-p", prompt, "--allow-all", "--no-ask-user"]
        if model and model not in ("default", "copilot", "auto"):
            cmd.extend(["--model", model])
        return cmd
    else:
        return [cli_bin, prompt]


def get_git_status_files(cwd: str) -> set[str]:
    """Return the set of modified or untracked file paths from git status."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        files = set()
        for line in res.stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            parts = line_str.split(maxsplit=1)
            if len(parts) == 2:
                # Remove quotes if git quoted paths with special characters
                fpath = parts[1].strip('"\'')
                files.add(fpath)
        return files
    except Exception:
        return set()





class HarnessWorker:
    """Worker autônomo baseado em harnesses locais (Codex, Antigravity, Claude).

    Executa tarefas de código utilizando os agentes instalados no sistema,
    sem consumir tokens da API de decisões do OpenRouter (exclusiva do JEV).
    """

    def __init__(
        self,
        model: str = "luna",
        cwd: Optional[str] = None,
        config_path: Optional[str] = None,
        config: Optional[Any] = None,
    ):
        self.model_tier = model
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.config_path = config_path or os.environ.get("MEISTER_CONFIG_PATH")
        self.config = config
        if self.config is None:
            try:
                from meister.config import load_config
                self.config = load_config(config_path=self.config_path, cwd=self.cwd)
            except Exception:
                self.config = None
        self.harness, self.resolved_model = resolve_worker_harness_and_model(
            model, config=self.config, cwd=self.cwd, config_path=self.config_path
        )
        self.cli_binary = find_cli_binary(self.harness)

    def _build_context(self, target_files: Optional[List[str]] = None) -> str:
        """Read target files from disk to include as context for the prompt."""
        if not target_files:
            return ""

        context_parts = []
        for rel_path in target_files:
            full_path = os.path.join(self.cwd, rel_path)
            if os.path.exists(full_path) and os.path.isfile(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    context_parts.append(f"--- File: {rel_path} ---\n{content}\n")
                except Exception as e:
                    logger.debug("Could not read context file %s: %s", rel_path, e)
        return "\n".join(context_parts)

    def run_task(
        self,
        task: str,
        target_files: Optional[List[str]] = None,
        extra_instructions: Optional[str] = None,
        timeout: float = 300.0,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute task via local agent harness and track changes on disk."""
        if not self.cli_binary:
            raise RuntimeError(
                f"Harness CLI '{self.harness}' não encontrado no sistema. "
                f"Instale o executável correspondente ou selecione outro modelo de worker."
            )

        file_context = self._build_context(target_files)

        prompt_parts = [f"Task: {task}"]
        if target_files:
            prompt_parts.append(f"Target files: {', '.join(target_files)}")
        if file_context:
            prompt_parts.append(f"Context from files:\n{file_context}")
        if extra_instructions:
            prompt_parts.append(f"Instructions: {extra_instructions}")
        prompt_parts.append(
            "Implement all necessary changes directly into the project files. "
            "Ensure the codebase remains valid, clean, and passing tests."
        )

        full_prompt = "\n\n".join(prompt_parts)

        # Visual progress banner in terminal / pane
        print(f"\n\033[1;36m{'='*68}\033[0m")
        print(
            f"\033[1;35m🔮 [MeisterRouter Worker]\033[0m "
            f"Harness: \033[1;32m{self.harness}\033[0m | "
            f"Modelo: \033[1;33m{self.resolved_model or 'default'}\033[0m"
        )
        print(f"🛠️ \033[1mCLI Executável:\033[0m {self.cli_binary}")
        print(f"📋 \033[1mTarefa:\033[0m {task}")
        if target_files:
            print(f"📂 \033[1mArquivos Alvo:\033[0m {', '.join(target_files)}")
        print(f"\033[1;36m{'='*68}\033[0m\n")
        sys.stdout.flush()

        cmd = build_harness_command(
            self.harness,
            self.cli_binary,
            self.resolved_model,
            full_prompt,
            self.cwd,
        )

        status_before = get_git_status_files(self.cwd)

        logger.info("Executing harness command: %s in %s", " ".join(cmd), self.cwd)

        safe_env = build_safe_worker_env(extra_env=env)
        output_lines: List[str] = []
        process = None
        try:
            process = subprocess.Popen(
                cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=safe_env,
                start_new_session=True,
                text=True,
                bufsize=1,
            )

            if isinstance(process.stdout, list):
                for line in process.stdout:
                    sys.stdout.write(str(line))
                    sys.stdout.flush()
                    output_lines.append(str(line))
                exit_code = process.wait(timeout=timeout) if hasattr(process, "wait") else 0
            elif process.stdout is not None and hasattr(process.stdout, "fileno"):
                import select
                start_time = time.monotonic()
                while True:
                    remaining = timeout - (time.monotonic() - start_time)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(cmd, timeout)

                    rlist, _, _ = select.select([process.stdout], [], [], min(remaining, 0.2))
                    if rlist:
                        line = process.stdout.readline()
                        if not line:
                            break
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        output_lines.append(line)
                    else:
                        if process.poll() is not None:
                            for rem_line in process.stdout:
                                sys.stdout.write(rem_line)
                                sys.stdout.flush()
                                output_lines.append(rem_line)
                            break
                exit_code = process.wait(timeout=max(1.0, timeout - (time.monotonic() - start_time)))
            else:
                if process.stdout:
                    for line in process.stdout:
                        sys.stdout.write(str(line))
                        sys.stdout.flush()
                        output_lines.append(str(line))
                exit_code = process.wait(timeout=timeout) if hasattr(process, "wait") else 0
        except (subprocess.TimeoutExpired, TimeoutError) as te:
            if process is not None and hasattr(process, "pid") and isinstance(process.pid, int):
                kill_process_tree(process.pid, is_pgid=True)
            raise TimeoutError(f"Harness {self.harness} excedeu o timeout duro de {timeout}s") from te
        except Exception as e:
            raise RuntimeError(f"Erro ao executar harness {self.harness}: {e}") from e

        captured_output = "".join(output_lines)

        # Check git status for modified files
        status_after = get_git_status_files(self.cwd)
        diff_files = status_after - status_before
        if not diff_files and status_after and not status_before:
            diff_files = status_after

        # If inside a git worktree/repo, also verify with WorktreeManager (Achado #21)
        if not diff_files and os.path.exists(os.path.join(self.cwd, ".git")):
            try:
                from meister.worktree import WorktreeManager
                manager = WorktreeManager(self.cwd)
                diff_files = set(manager.get_modified_files(self.cwd))
            except Exception:
                pass

        modified_files = sorted(list(diff_files))

        print(f"\n\033[1;36m{'='*68}\033[0m")
        if exit_code == 0:
            print(f"\033[1;32m✅ Concluído com sucesso pelo harness {self.harness}!\033[0m")
            if modified_files:
                print("📂 \033[1mArquivos alterados:\033[0m")
                for f in modified_files:
                    print(f"   \033[1;32m✓\033[0m {f}")
            else:
                print("ℹ️ Nenhum arquivo alterado.")
        else:
            print(f"\033[1;31m❌ Falha na execução do harness {self.harness} (exit code: {exit_code})\033[0m")
        print(f"\033[1;36m{'='*68}\033[0m\n")
        sys.stdout.flush()

        if exit_code != 0:
            raise RuntimeError(f"Harness {self.harness} failed with exit code {exit_code}")

        return {
            "status": "done",
            "harness": self.harness,
            "model": self.resolved_model or "default",
            "cli": self.cli_binary,
            "modified_files": modified_files,
            "output": captured_output,
            "exit_code": exit_code,
        }


# Alias for backward compatibility
NativeWorker = HarnessWorker


def is_herdr_available(socket_path: Optional[str] = None) -> bool:
    """Verifica se o UNIX domain socket do Herdr está acessível."""
    resolved = socket_path or os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")
    return os.path.exists(resolved)


async def run_worker_in_herdr_pane_async(
    model: str,
    task: str,
    target_files: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    socket_path: Optional[str] = None,
    timeout: float = 180.0,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Abre um terminal lateral visível no Herdr (split pane) e aguarda conclusão."""
    from meister.herdr.client import HerdrSocketClient

    resolved_cwd = os.path.abspath(cwd or os.getcwd())
    resolved_config_path = config_path or os.environ.get("MEISTER_CONFIG_PATH")
    if not resolved_config_path:
        for fname in ("meister.config.yaml", "meister.config.yml"):
            cand = os.path.join(resolved_cwd, fname)
            if os.path.exists(cand):
                resolved_config_path = os.path.abspath(cand)
                break

    runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
    os.makedirs(runs_dir, exist_ok=True)

    run_id = uuid.uuid4().hex[:8]
    task_file = os.path.join(runs_dir, f"{run_id}_task.json")
    result_file = os.path.join(runs_dir, f"{run_id}.json")

    # Contrato task.json gravado de forma atômica (Achados #7, #11)
    task_payload = {
        "task_id": run_id,
        "model": model,
        "task": task,
        "target_files": target_files or [],
        "cwd": resolved_cwd,
        "config_path": resolved_config_path,
        "timeout": timeout,
        "result_file": result_file,
    }
    write_atomic_json(task_file, task_payload)

    cmd_parts = [sys.executable, "-m", "meister.cli", "run-task", task_file]
    command_str = f"MEISTER_IN_PANE=1 {' '.join(shlex.quote(p) for p in cmd_parts)}"

    client = HerdrSocketClient(socket_path=socket_path)
    pane_id = await client.split_pane(
        direction="right",
        split_ratio=0.5,
        command=command_str,
    )
    if not pane_id:
        raise RuntimeError("Herdr did not return a valid pane_id on split_pane")

    # Registra o pane ativo para permitir fechamento automático após aprovação no control
    active_pane_file = os.path.join(resolved_cwd, ".meister", "active_worker_pane.txt")
    try:
        with open(active_pane_file, "w", encoding="utf-8") as f:
            f.write(pane_id)
    except Exception:
        pass

    try:
        from meister.state import StateManager
        state_mgr = StateManager(os.path.join(resolved_cwd, ".meister", "meister.db"))
        state_mgr.register_pane(pane_id, run_id=run_id)
    except Exception:
        pass

    # Registra listener para pane.exited (Achado #10)
    pane_exited_event = asyncio.Event()

    async def _on_pane_event(event: dict):
        params = event.get("params", event)
        ev_type = params.get("type") or event.get("type")
        p_id = params.get("pane_id") or event.get("pane_id")
        if ev_type == "pane_exited" and p_id == pane_id:
            pane_exited_event.set()

    try:
        await client.subscribe_events(_on_pane_event)
    except Exception:
        pass

    # Aguarda o worker terminar no pane lendo o arquivo de resultado de forma atômica ou evento pane.exited
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if os.path.exists(result_file):
            result = read_atomic_json(result_file)
            if result is not None:
                try:
                    os.remove(result_file)
                except Exception:
                    pass
                try:
                    if os.path.exists(task_file):
                        os.remove(task_file)
                except Exception:
                    pass
                return result

        if pane_exited_event.is_set():
            if os.path.exists(result_file):
                result = read_atomic_json(result_file)
                if result is not None:
                    try:
                        os.remove(result_file)
                        if os.path.exists(task_file):
                            os.remove(task_file)
                    except Exception:
                        pass
                    return result

        await asyncio.sleep(0.3)

    try:
        await client.send_interrupt(pane_id)
    except Exception as e:
        logger.debug("Failed to send interrupt to timed out pane %s: %s", pane_id, e)
    try:
        await client.close_pane(pane_id)
    except Exception as e:
        logger.debug("Failed to close timed out pane %s: %s", pane_id, e)

    raise TimeoutError(f"Worker no pane {pane_id} excedeu o timeout de {timeout}s aguardando conclusão")


def run_worker_in_herdr_pane(
    model: str,
    task: str,
    target_files: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    socket_path: Optional[str] = None,
    timeout: float = 180.0,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrapper síncrono para execução do worker em pane lateral do Herdr."""
    return asyncio.run(
        run_worker_in_herdr_pane_async(
            model=model,
            task=task,
            target_files=target_files,
            cwd=cwd,
            socket_path=socket_path,
            timeout=timeout,
            config_path=config_path,
        )
    )


async def run_worker_in_herdr_tab_async(
    model: str,
    task: str,
    target_files: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    socket_path: Optional[str] = None,
    timeout: float = 180.0,
    label: Optional[str] = None,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Abre uma tab dedicada no Herdr (focus=False) e aguarda conclusão via result.json ou pane.exited (Achados #10, #13)."""
    from meister.herdr.client import HerdrSocketClient

    resolved_cwd = os.path.abspath(cwd or os.getcwd())
    resolved_config_path = config_path or os.environ.get("MEISTER_CONFIG_PATH")
    if not resolved_config_path:
        for fname in ("meister.config.yaml", "meister.config.yml"):
            cand = os.path.join(resolved_cwd, fname)
            if os.path.exists(cand):
                resolved_config_path = os.path.abspath(cand)
                break

    runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
    os.makedirs(runs_dir, exist_ok=True)

    run_id = uuid.uuid4().hex[:8]
    task_file = os.path.join(runs_dir, f"{run_id}_task.json")
    result_file = os.path.join(runs_dir, f"{run_id}.json")

    task_payload = {
        "task_id": run_id,
        "model": model,
        "task": task,
        "target_files": target_files or [],
        "cwd": resolved_cwd,
        "config_path": resolved_config_path,
        "timeout": timeout,
        "result_file": result_file,
    }
    write_atomic_json(task_file, task_payload)

    cmd_parts = [sys.executable, "-m", "meister.cli", "run-task", task_file]
    command_str = f"MEISTER_IN_PANE=1 {' '.join(shlex.quote(p) for p in cmd_parts)}"

    client = HerdrSocketClient(socket_path=socket_path)
    tab_label = label or f"worker:{model}:{run_id}"
    tab_id, pane_id = await client.create_tab(
        cwd=resolved_cwd,
        label=tab_label,
        focus=False,
    )
    if not pane_id:
        raise RuntimeError("Herdr did not return a valid pane_id on tab.create")

    # Inicia o comando no terminal da tab recém-criada após aguardar prontidão do shell
    try:
        await client.wait_pane_ready(pane_id)
        await client.send_text(pane_id, command_str + "\n")
    except Exception as e:
        logger.debug("Failed sending command to tab pane %s: %s", pane_id, e)

    # Registra o pane ativo em SQLite
    try:
        from meister.state import StateManager
        state_mgr = StateManager(os.path.join(resolved_cwd, ".meister", "meister.db"))
        state_mgr.register_pane(pane_id, run_id=run_id)
    except Exception:
        pass

    # Registra listener para pane.exited (Achado #10)
    pane_exited_event = asyncio.Event()

    async def _on_tab_event(event: dict):
        params = event.get("params", event)
        ev_type = params.get("type") or event.get("type")
        p_id = params.get("pane_id") or event.get("pane_id")
        if ev_type == "pane_exited" and p_id == pane_id:
            pane_exited_event.set()

    try:
        await client.subscribe_events(_on_tab_event)
    except Exception:
        pass

    # Aguarda conclusão via result.json ou evento pane.exited
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if os.path.exists(result_file):
            result = read_atomic_json(result_file)
            if result is not None:
                try:
                    os.remove(result_file)
                except Exception:
                    pass
                try:
                    if os.path.exists(task_file):
                        os.remove(task_file)
                except Exception:
                    pass
                try:
                    await client.close_tab(tab_id)
                except Exception:
                    pass
                return result

        if pane_exited_event.is_set():
            if os.path.exists(result_file):
                result = read_atomic_json(result_file)
                if result is not None:
                    try:
                        os.remove(result_file)
                        if os.path.exists(task_file):
                            os.remove(task_file)
                        await client.close_tab(tab_id)
                    except Exception:
                        pass
                    return result

        await asyncio.sleep(0.3)

    # Timeout duro
    try:
        await client.send_interrupt(pane_id)
    except Exception:
        pass
    try:
        await client.close_tab(tab_id)
    except Exception:
        pass

    raise TimeoutError(f"Worker na tab {tab_id} (pane {pane_id}) excedeu o timeout de {timeout}s aguardando conclusão")


def run_worker_in_herdr_tab(
    model: str,
    task: str,
    target_files: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    socket_path: Optional[str] = None,
    timeout: float = 180.0,
    label: Optional[str] = None,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrapper síncrono para execução do worker em tab dedicada do Herdr."""
    return asyncio.run(
        run_worker_in_herdr_tab_async(
            model=model,
            task=task,
            target_files=target_files,
            cwd=cwd,
            socket_path=socket_path,
            timeout=timeout,
            label=label,
            config_path=config_path,
        )
    )


def execute_worker_task(
    model: str = "luna",
    task: str = "",
    target_files: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    timeout: float = 300.0,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper function to run a single task via HarnessWorker."""
    worker = HarnessWorker(model=model, cwd=cwd, config_path=config_path)
    return worker.run_task(task=task, target_files=target_files, timeout=timeout)


def execute_task_file(
    task_file: str,
    result_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Lê contrato task.json, executa worker isolado e grava result.json atômico (Achados #7, #8, #11, #29)."""
    task_data = read_atomic_json(task_file)
    if not task_data:
        raise ValueError(f"Arquivo de tarefa inválido ou inacessível: {task_file}")

    task_id = task_data.get("task_id", uuid.uuid4().hex[:8])
    run_id = task_data.get("run_id") or os.environ.get("MEISTER_RUN_ID")
    model = task_data.get("model", "luna")
    task = task_data.get("task", "")
    target_files = task_data.get("target_files")
    cwd = task_data.get("cwd")
    config_path = task_data.get("config_path") or os.environ.get("MEISTER_CONFIG_PATH")
    if not config_path and cwd:
        for fname in ("meister.config.yaml", "meister.config.yml"):
            cand = os.path.join(cwd, fname)
            if os.path.exists(cand):
                config_path = os.path.abspath(cand)
                break
    timeout = float(task_data.get("timeout", 300.0))
    resolved_res_file = result_file or task_data.get("result_file")

    log_event(
        event_type="worker_task_start",
        run_id=run_id,
        task_id=task_id,
        tier=model,
        cwd=cwd,
    )

    worker = HarnessWorker(model=model, cwd=cwd, config_path=config_path)

    try:
        res = worker.run_task(task=task, target_files=target_files, timeout=timeout)
        res["task_id"] = task_id
        if resolved_res_file:
            write_atomic_json(resolved_res_file, res)
        log_event(
            event_type="worker_task_end",
            run_id=run_id,
            task_id=task_id,
            tier=model,
            exit_code=res.get("exit_code", 0),
            status=res.get("status", "done"),
        )
        return res
    except Exception as e:
        log_event(
            event_type="worker_task_error",
            run_id=run_id,
            task_id=task_id,
            tier=model,
            exit_code=1,
            error=str(e),
        )
        err_res = {
            "task_id": task_id,
            "status": "error",
            "harness": worker.harness,
            "model": worker.resolved_model or "default",
            "cli": worker.cli_binary,
            "modified_files": [],
            "output": "",
            "exit_code": 1,
            "error": str(e),
        }
        if resolved_res_file:
            write_atomic_json(resolved_res_file, err_res)
        raise


def run_worker_interactive_loop(
    model: str = "luna",
    cwd: Optional[str] = None,
) -> None:
    """Run interactive worker loop reading tasks from terminal/pty stdin."""
    worker = HarnessWorker(model=model, cwd=cwd)
    print(f"MeisterRouter Worker active ({model} -> harness: {worker.harness}, cli: {worker.cli_binary}) in {worker.cwd}")
    print("Ready to receive tasks. Enter task description (or 'exit' to quit):")
    sys.stdout.flush()

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            task_str = line.strip()
            if not task_str:
                continue
            if task_str.lower() in ("exit", "quit"):
                break

            print(f"⚙️ Executing task with {worker.harness}...")
            sys.stdout.flush()

            result = worker.run_task(task_str)
            modified = result.get("modified_files", [])
            print(f"✅ Status: done. Modified files: {modified}")
            print(f"Summary: {result.get('output', '')[:300]}")
            sys.stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ Error during worker execution: {e}")
            sys.stdout.flush()
