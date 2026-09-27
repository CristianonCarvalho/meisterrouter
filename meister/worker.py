"""
meister.worker — Implementador autônomo baseado em harnesses locais (Codex, Antigravity, Claude).

Executa tarefas de código utilizando os harnesses instalados localmente (codex, agy, claude),
sem consumir tokens da API de decisões (OpenRouter, reservada exclusivamente ao JEV).
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import uuid
import shutil
import asyncio
import logging
import subprocess
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Harness identifiers
HARNESS_CODEX = "codex"
HARNESS_ANTIGRAVITY = "antigravity"
HARNESS_CLAUDE = "claude"

MODEL_ALIASES: Dict[str, str] = {
    "luna": "gpt-6-luna",
    "gpt-6-luna": "gpt-6-luna",
    "codex": "gpt-4o",
    "gemini_flash": "gemini-3.8-flash-high",
    "gemini-flash": "gemini-3.8-flash-high",
    "gemini-3.8-flash": "gemini-3.8-flash-high",
    "gemini_antigravity": "gemini-3.8-flash-high",
    "antigravity": "gemini-3.8-flash-high",
    "agy": "gemini-3.8-flash-high",
    "haiku": "claude-3-5-haiku-20241022",
    "haiku-4.5": "claude-3-5-haiku-20241022",
    "sonnet": "claude-3-7-sonnet",
    "sonnet-5": "claude-3-7-sonnet",
    "claude": "claude-3-7-sonnet",
}


def resolve_worker_model(model_name: str) -> str:
    """Resolve aliases or tier names into the canonical harness model identifier."""
    cleaned = (model_name or "luna").strip().lower()
    return MODEL_ALIASES.get(cleaned, model_name)


def resolve_worker_harness_and_model(model_name: str) -> Tuple[str, Optional[str]]:
    """Resolve a model name or alias to (harness_name, resolved_model).

    Harnesses:
      - 'codex': runs OpenAI Codex CLI (native default: gpt-6-luna)
      - 'antigravity': runs Google Antigravity CLI ('agy', native: gemini-3.8-flash-high)
      - 'claude': runs Anthropic Claude Code CLI ('claude')
    """
    cleaned = (model_name or "luna").strip().lower()

    if cleaned in ("luna", "gpt-6-luna"):
        return (HARNESS_CODEX, "gpt-6-luna")
    if cleaned in ("codex", "openai/gpt-4o", "gpt-4o"):
        return (HARNESS_CODEX, "gpt-4o" if "4o" in cleaned else None)
    if cleaned in (
        "gemini_flash", "gemini-flash", "gemini-3.8-flash",
        "gemini_antigravity", "antigravity", "agy", "google/gemini-2.5-flash",
        "gemini-3.8-flash-high", "gemini-3.8-flash-medium",
    ):
        return (HARNESS_ANTIGRAVITY, "gemini-3.8-flash-high")
    if cleaned in (
        "haiku", "haiku-4.5", "claude-3-5-haiku-20241022", "anthropic/claude-3-5-haiku-20241022"
    ):
        return (HARNESS_CLAUDE, "claude-3-5-haiku-20241022")
    if cleaned in (
        "sonnet", "sonnet-5", "claude-3-7-sonnet", "anthropic/claude-3-7-sonnet"
    ):
        return (HARNESS_CLAUDE, "claude-3-7-sonnet")
    if cleaned == "claude":
        return (HARNESS_CLAUDE, None)

    # Heuristic matching
    if "gemini" in cleaned or "antigravity" in cleaned or "agy" in cleaned:
        return (HARNESS_ANTIGRAVITY, "gemini-3.8-flash-high")
    if "claude" in cleaned or "haiku" in cleaned or "sonnet" in cleaned or "opus" in cleaned:
        return (HARNESS_CLAUDE, None)
    if "gpt" in cleaned or "o1" in cleaned or "o3" in cleaned or "codex" in cleaned or "luna" in cleaned:
        return (HARNESS_CODEX, None)

    return (HARNESS_CODEX, None)


def find_cli_binary(harness: str) -> Optional[str]:
    """Locate the executable path for the requested harness."""
    names = []
    if harness == HARNESS_CODEX:
        names = ["codex"]
    elif harness == HARNESS_ANTIGRAVITY:
        names = ["agy", "antigravity"]
    elif harness == HARNESS_CLAUDE:
        names = ["claude"]
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


def parse_and_apply_file_edits(response_text: str, base_dir: str = ".") -> List[str]:
    """Parse file code blocks from model response and write them to disk if present."""
    pattern = re.compile(r"```(?:file|filepath):([^\n\r]+)[\r\n](.*?)```", re.DOTALL)
    modified_files: List[str] = []

    for match in pattern.finditer(response_text):
        rel_path = match.group(1).strip()
        rel_path = rel_path.split()[0].strip("`'\"")
        file_content = match.group(2)

        if not file_content.endswith("\n"):
            file_content += "\n"

        target_path = os.path.normpath(os.path.join(base_dir, rel_path))

        abs_base = os.path.abspath(base_dir)
        abs_target = os.path.abspath(target_path)
        if not abs_target.startswith(abs_base):
            logger.warning("Prevented unsafe path traversal: %s outside %s", abs_target, abs_base)
            continue

        os.makedirs(os.path.dirname(abs_target), exist_ok=True)
        with open(abs_target, "w", encoding="utf-8") as f:
            f.write(file_content)

        modified_files.append(rel_path)
        logger.info("Worker applied edits to file: %s", rel_path)

    return modified_files


class HarnessWorker:
    """Worker autônomo baseado em harnesses locais (Codex, Antigravity, Claude).

    Executa tarefas de código utilizando os agentes instalados no sistema,
    sem consumir tokens da API de decisões do OpenRouter (exclusiva do JEV).
    """

    def __init__(self, model: str = "luna", cwd: Optional[str] = None):
        self.model_tier = model
        self.harness, self.resolved_model = resolve_worker_harness_and_model(model)
        self.cwd = os.path.abspath(cwd or os.getcwd())
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

        output_lines: List[str] = []
        try:
            process = subprocess.Popen(
                cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if process.stdout:
                for line in process.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    output_lines.append(line)

            exit_code = process.wait()
        except Exception as e:
            raise RuntimeError(f"Erro ao executar harness {self.harness}: {e}") from e

        captured_output = "".join(output_lines)

        # Check git status for modified files
        status_after = get_git_status_files(self.cwd)
        diff_files = status_after - status_before
        if not diff_files and status_after and not status_before:
            diff_files = status_after

        # Also support markdown code block extraction if harness generated formatted text
        extra_mods = parse_and_apply_file_edits(captured_output, base_dir=self.cwd)
        modified_files = sorted(list(diff_files | set(extra_mods)))

        print(f"\n\033[1;36m{'='*68}\033[0m")
        if exit_code == 0:
            print(f"\033[1;32m✅ Concluído com sucesso pelo harness {self.harness}!\033[0m")
            if modified_files:
                print(f"📂 \033[1mArquivos alterados:\033[0m")
                for f in modified_files:
                    print(f"   \033[1;32m✓\033[0m {f}")
            else:
                print(f"ℹ️ Nenhum arquivo alterado.")
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
) -> Dict[str, Any]:
    """Abre um terminal lateral visível no Herdr (split pane) e aguarda conclusão."""
    from meister.herdr.client import HerdrSocketClient

    resolved_cwd = os.path.abspath(cwd or os.getcwd())
    runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
    os.makedirs(runs_dir, exist_ok=True)

    run_id = uuid.uuid4().hex[:8]
    result_file = os.path.join(runs_dir, f"{run_id}.json")

    meister_bin = shutil.which("meister") or os.path.expanduser("~/.local/bin/meister")
    cmd_parts = [
        "MEISTER_IN_PANE=1",
        meister_bin,
        "worker",
        "--model",
        model,
        "--task",
        f"\"{task}\"",
        "--run-id",
        run_id,
    ]
    if target_files:
        cmd_parts.extend(["--files", f"\"{','.join(target_files)}\""])
    if cwd:
        cmd_parts.extend(["--cwd", f"\"{resolved_cwd}\""])

    command_str = " ".join(cmd_parts)

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

    # Aguarda o worker terminar no pane lendo o arquivo de resultado
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if os.path.exists(result_file):
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    result = json.load(f)
                try:
                    os.remove(result_file)
                except Exception:
                    pass
                return result
            except Exception:
                pass
        await asyncio.sleep(0.5)

    raise TimeoutError(f"Worker no pane {pane_id} excedeu o timeout de {timeout}s aguardando conclusão")


def run_worker_in_herdr_pane(
    model: str,
    task: str,
    target_files: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    socket_path: Optional[str] = None,
    timeout: float = 180.0,
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
        )
    )


def execute_worker_task(
    model: str = "luna",
    task: str = "",
    target_files: Optional[List[str]] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper function to run a single task via HarnessWorker."""
    worker = HarnessWorker(model=model, cwd=cwd)
    return worker.run_task(task=task, target_files=target_files)


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
