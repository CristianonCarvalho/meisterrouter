"""
meister.worker — Implementador nativo autônomo do MeisterRouter.

Executa tarefas de código via OpenRouter (GPT-6 Luna, Gemini Flash, etc.),
lê arquivos de contexto, aplica as edições recebidas e reporta o resultado.
"""

from __future__ import annotations

import os
import re
import sys
import logging
from typing import Optional, List, Dict, Any
import requests

from meister.jev import get_api_key

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = os.environ.get(
    "OPENROUTER_CHAT_URL",
    "https://openrouter.ai/api/v1/chat/completions",
)

MODEL_ALIASES: Dict[str, str] = {
    "luna": "openai/gpt-6-luna",
    "gpt-6-luna": "openai/gpt-6-luna",
    "gemini_flash": "google/gemini-2.5-flash",
    "gemini-flash": "google/gemini-2.5-flash",
    "gemini-3.8-flash": "google/gemini-2.5-flash",
    "haiku": "anthropic/claude-3-5-haiku-20241022",
    "haiku-4.5": "anthropic/claude-3-5-haiku-20241022",
    "sonnet": "anthropic/claude-3-7-sonnet",
}


def resolve_worker_model(model_name: str) -> str:
    """Resolve aliases or tier names into the canonical OpenRouter model identifier."""
    cleaned = (model_name or "luna").strip().lower()
    return MODEL_ALIASES.get(cleaned, model_name)


def parse_and_apply_file_edits(response_text: str, base_dir: str = ".") -> List[str]:
    """Parse file code blocks from model response and write them to disk.

    Supports formats:
    ```file:path/to/file.ext
    <content>
    ```
    or
    ```filepath:path/to/file.ext
    <content>
    ```
    """
    pattern = re.compile(r"```(?:file|filepath):([^\n\r]+)[\r\n](.*?)```", re.DOTALL)
    modified_files: List[str] = []

    for match in pattern.finditer(response_text):
        rel_path = match.group(1).strip()
        # Clean potential markdown or syntax markers
        rel_path = rel_path.split()[0].strip("`'\"")
        file_content = match.group(2)

        # Normalize line endings
        if not file_content.endswith("\n"):
            file_content += "\n"

        target_path = os.path.normpath(os.path.join(base_dir, rel_path))

        # Security check: prevent directory traversal outside base_dir
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


class NativeWorker:
    """Worker autônomo nativo para execução de código com modelos de baixo custo e escalonamento."""

    def __init__(self, model: str = "luna", cwd: Optional[str] = None):
        self.model_tier = model
        self.resolved_model = resolve_worker_model(model)
        self.cwd = os.path.abspath(cwd or os.getcwd())

    def _build_context(self, target_files: Optional[List[str]] = None) -> str:
        """Read target files from disk to include as context for the model."""
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
        """Execute task via OpenRouter chat completion and write changes to disk."""
        api_key = get_api_key()

        file_context = self._build_context(target_files)

        system_prompt = (
            "You are an autonomous expert software engineering agent in the MeisterRouter framework.\n"
            "Your job is to implement code changes requested in the task accurately and cleanly.\n"
            "Format every file you create or update using this exact markdown block format:\n\n"
            "```file:relative/path/to/file.ext\n"
            "<complete updated file contents here>\n"
            "```\n\n"
            "Do NOT omit parts of the file with 'rest of code unchanged'. Output the full updated file.\n"
            "Be precise, follow best practices, and introduce no regressions."
        )

        user_content_parts = [f"Task: {task}"]
        if target_files:
            user_content_parts.append(f"Target files: {', '.join(target_files)}")
        if file_context:
            user_content_parts.append(f"Current File Context:\n{file_context}")
        if extra_instructions:
            user_content_parts.append(f"Additional Instructions: {extra_instructions}")

        user_prompt = "\n\n".join(user_content_parts)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/CristianonCarvalho/meisterrouter",
            "X-Title": "MeisterRouter Native Worker",
        }

        payload = {
            "model": self.resolved_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }

        logger.info("Dispatching task to %s in %s", self.resolved_model, self.cwd)

        try:
            resp = requests.post(OPENROUTER_CHAT_URL, headers=headers, json=payload, timeout=120)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"OpenRouter connection error: {e}") from e

        if resp.status_code != 200:
            err_msg = f"HTTP {resp.status_code}: {resp.text}"
            logger.error("OpenRouter worker request failed: %s", err_msg)
            try:
                resp.raise_for_status()
            except requests.exceptions.HTTPError:
                raise RuntimeError(err_msg)

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"OpenRouter returned empty choices: {data}")

        response_content = choices[0].get("message", {}).get("content", "")
        modified_files = parse_and_apply_file_edits(response_content, base_dir=self.cwd)

        return {
            "status": "done",
            "model": self.resolved_model,
            "modified_files": modified_files,
            "output": response_content,
            "usage": data.get("usage", {}),
        }


def execute_worker_task(
    model: str = "luna",
    task: str = "",
    target_files: Optional[List[str]] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper function to run a single task via NativeWorker."""
    worker = NativeWorker(model=model, cwd=cwd)
    return worker.run_task(task=task, target_files=target_files)


def run_worker_interactive_loop(
    model: str = "luna",
    cwd: Optional[str] = None,
) -> None:
    """Run interactive worker loop reading tasks from terminal/pty stdin."""
    worker = NativeWorker(model=model, cwd=cwd)
    print(f"MeisterRouter Worker active ({model} -> {worker.resolved_model}) in {worker.cwd}")
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

            print(f"⚙️ Executing task with {worker.resolved_model}...")
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
